#!/usr/bin/env python3
"""WP-7 job dispatcher: one image, one entrypoint, STRIDE_JOB selects work.

Every job follows the self-healing contract:
  1. read this job's run-state row (DynamoDB stride_run_state)
  2. backfill missed days first, within what the source allows
  3. do today's work
  4. write last_success, rows_written, gaps_found, gaps_healed
  5. raise on failure so Lambda retries fire, the DLQ catches, the alarm mails

Secrets come from Secrets Manager (STRIDE_SECRET_ID, default stride/prod)
and are loaded into the environment before any repo import; nothing reads a
local env file in production.

Honesty rules baked in: tip_time snapshots are live-only and are never
"healed", only reported as permanent losses; racecard/results backfill is
bounded by the PF wall (~31 days) and older gaps are reported the same way.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

import boto3

REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
TABLE = os.environ.get("STRIDE_STATE_TABLE", "stride_run_state")
SYD = timezone(timedelta(hours=10))  # display only; schedules own DST
PF_WALL_DAYS = 31
# probe: throwaway edit so this PR touches a panel-staging file.


def _load_secrets() -> None:
    sid = os.environ.get("STRIDE_SECRET_ID", "stride/prod")
    sm = boto3.client("secretsmanager", region_name=REGION)
    blob = json.loads(sm.get_secret_value(SecretId=sid)["SecretString"])
    for k, v in blob.items():
        os.environ.setdefault(k, v)


def _state():
    return boto3.resource("dynamodb", region_name=REGION).Table(TABLE)


def _get_state(job: str) -> dict:
    try:
        return _state().get_item(Key={"job_name": job}).get("Item") or {}
    except Exception:
        return {}


def _put_state(job: str, **attrs) -> None:
    item = {"job_name": job,
            "updated_at": datetime.now(timezone.utc).isoformat(), **attrs}
    _state().put_item(Item={k: v for k, v in item.items() if v is not None})


def _today() -> str:
    return datetime.now(SYD).strftime("%Y-%m-%d")


def _root() -> str:
    return os.environ.get("LAMBDA_TASK_ROOT", "/var/task")


# --- cross-task plumbing ----------------------------------------------------
#
# Two facts of the runtime shape everything here: Lambda's filesystem is
# read-only (so any job that writes repo paths runs on Fargate), and Fargate
# tasks do NOT share a filesystem (so the 05:30 racecard -> 06:00
# intelligence -> 07:00 consensus -> 10:00 tips chain relays its file
# artifacts through S3). Models are proprietary IP in a PUBLIC repo: never
# in git or the image — staged from the private models bucket at startup.

def _s3():
    return boto3.client("s3", region_name=REGION)


def _stage_models() -> None:
    """Fargate: pull model artifacts into server/python/models/. No-op on
    Lambda (read-only fs, and no Lambda job loads the model). Raises when
    the bucket is configured but empty — a tips run without the ensemble
    must fail loudly at start, not score garbage at 10:01."""
    bucket = os.environ.get("STRIDE_MODELS_BUCKET", "").strip()
    if not bucket:
        return
    dest = f"{_root()}/server/python/models"
    try:
        os.makedirs(dest, exist_ok=True)
        probe = os.path.join(dest, ".write_probe")
        open(probe, "w").close()
        os.remove(probe)
    except OSError:
        return  # read-only fs = Lambda = model not needed here
    n = 0
    for page in _s3().get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            name = os.path.basename(o["Key"])
            if name:
                _s3().download_file(bucket, o["Key"], os.path.join(dest, name))
                n += 1
    print(f"[models] staged {n} artifact(s) from s3://{bucket}")
    if n == 0:
        raise RuntimeError(
            f"models bucket s3://{bucket} is EMPTY — run "
            "infra/09b_upload_models.sh from the box that has the artifacts")


ARTIFACTS_PREFIX = "artifacts"


def _artifact_bucket() -> str:
    return os.environ.get("STRIDE_EVIDENCE_BUCKET", "").strip()


def _sync_up(rel_dir: str, pattern: str = "*.json") -> int:
    b = _artifact_bucket()
    if not b:
        return 0
    import glob
    n = 0
    for p in glob.glob(f"{_root()}/{rel_dir}/{pattern}"):
        _s3().upload_file(p, b,
                          f"{ARTIFACTS_PREFIX}/{rel_dir}/{os.path.basename(p)}")
        n += 1
    print(f"[sync] up {n} from {rel_dir}/{pattern}")
    return n


def _sync_down(rel_dir: str) -> int:
    b = _artifact_bucket()
    if not b:
        return 0
    prefix = f"{ARTIFACTS_PREFIX}/{rel_dir}/"
    dest = f"{_root()}/{rel_dir}"
    os.makedirs(dest, exist_ok=True)
    n = 0
    for page in _s3().get_paginator("list_objects_v2").paginate(
            Bucket=b, Prefix=prefix):
        for o in page.get("Contents", []):
            name = o["Key"][len(prefix):]
            if name and "/" not in name:
                _s3().download_file(b, o["Key"], os.path.join(dest, name))
                n += 1
    print(f"[sync] down {n} into {rel_dir}")
    return n


# Subprocess bounds sized to what each job actually costs. The old single 840s
# constant was Lambda's 15-minute ceiling minus margin applied to every job; on
# Fargate there is no execution limit, and on 2026-08-05 it became the binding
# constraint — intelligence-build (269.1s for 53 runners on 2026-08-02) faced
# 404 runners and died at the cap, as did consensus-agent, while the day read
# green downstream.
#
# Prefer a bound no longer than the gap to the next dependent job where one
# exists: a bound past the gap converts a loud timeout into a silent
# stale-data run, when the next task syncs down what the timed-out job never
# produced. That constraint binds stride_build and consensus_agent, whose
# output the following job reads.
#
# It does NOT bind run_tips_pipeline, and the number below deliberately breaks
# the 10:00 -> 10:45 gap. Nothing downstream consumes tips_<date>.json:
# tip-time-snapshot shells out to betfair_odds_snapshot.py, which reads the
# Betfair markets for the date, not the tips. Bounding tips at the gap would
# buy no safety and would kill a card outright — first-race cost alone exceeds
# it.
#
# run_tips_pipeline is MEASURED, on the real image, against the 2026-08-05
# card (ECS task 02c99b61, Belmont Park, 18-runner fields):
#
#   race 1   532.2s   includes ~290s of once-per-process setup — the franking
#                     graph (130,155 result rows, 1,997,611 edges, PageRank
#                     over 28,750 horses), the par FSP table and the model
#                     build, none of which survives a Fargate task exit
#   race 2   243.4s   steady state
#
# so a 31-race metro Saturday is ~290 + 31*243 = ~7,800s. 10800 leaves room
# for larger fields without being unbounded.
#
# Raising this makes a full card COMPLETE; it does not make the 10:00 schedule
# sound. A card that costs ~2.2h from a 10:00 start publishes tips around
# 12:15, after the early races have jumped. The fix for that is the per-race
# cost — ~243s is spent in the quant/franking feature engines, not in the
# 2,000-iteration simulation itself — or an earlier start. Both are real work,
# neither belongs in a timeout constant, and a bigger card only widens the gap.
# The gap that binds a job is the gap to the job that READS ITS OUTPUT, which
# is not always the next one on the clock. consensus_agent was first bounded at
# 2700s against morning-odds at 08:00; morning-odds runs odds_movement.py,
# which does not reference consensus at all. The only reader of
# consensus_<date>.json is run_tips_pipeline (via consensus_blender
# .load_consensus_intelligence) at 10:00, so the real gap is three hours, not
# one, and the old bound was tight for no reason.
#
# Measured per-race cost, from the only two real cards the cloud chain has run:
#
#   stride_build      269.1s /  8 races (2026-08-02)   ~36s/race
#                    1132.0s / 31 races (2026-08-05)
#   consensus_agent  ~2280.0s / 31 races (2026-08-05)   ~74s/race
#
# Both bounds above were sized on a 31-race midweek card. A metro Saturday of
# 8-10 meetings is plausibly ~90 races, which costs stride_build ~3,250s and
# consensus_agent ~6,600s — each blowing 2700s, on the day of the week the
# system exists to serve. Sized below for ~90 races with headroom.
JOB_TIMEOUTS = {
    # Bounded by the 06:00 -> 07:00 gap: consensus syncs down this output, so
    # a bound past 3600s would let it start on yesterday's intelligence
    # instead of failing loudly. 3400 covers ~94 races and still dies inside
    # the gap. A Saturday bigger than that does not fit the hour at all, and
    # the fix then is an earlier start, not a larger number.
    "stride_build.py": 3400,
    # Bounded by 07:00 -> 10:00 (tips), NOT 08:00 (morning-odds, which does
    # not read consensus). 9000 finishes by 09:30 and covers ~120 races.
    "consensus_agent.py": 9000,
    "run_tips_pipeline.py": 10800,  # measured ~7,800s for 31 races; no consumer
}
DEFAULT_TIMEOUT = 840  # Lambda-era bound; unchanged for the remaining jobs


def _dump_captured(stdout, stderr, header: str) -> None:
    """Print what a child wrote before it was killed.

    capture_output buffers both streams in the parent, so nothing reaches
    CloudWatch until the parent chooses to print it — and on a timeout the
    parent raises instead. Tail-bounded to match _run_ok, for the same reason:
    a full card's output is megabytes and the end is the part that says where
    it got to.
    """
    for stream, label, sink in ((stdout, "stdout", sys.stdout),
                                (stderr, "stderr", sys.stderr)):
        if not stream:
            print(f"--- {header} {label}: (empty) ---", file=sink)
            continue
        if isinstance(stream, bytes):
            stream = stream.decode("utf-8", "replace")
        print(f"--- {header} {label} (last 4000 chars) ---", file=sink)
        print(stream[-4000:], file=sink)


def _run(script: str, *args: str, cwd: str = None) -> subprocess.CompletedProcess:
    root = os.environ.get("LAMBDA_TASK_ROOT", "/var/task")
    cmd = [sys.executable, f"{root}/server/python/{script}", *args]
    print("+", " ".join(cmd))
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=JOB_TIMEOUTS.get(script, DEFAULT_TIMEOUT),
                              cwd=cwd or f"{root}/server/python")
    except subprocess.TimeoutExpired as e:
        # The run that most needs explaining was the one that explained least.
        # On 2026-08-05 consensus-agent worked for 14 minutes and was killed at
        # the cap; its entire CloudWatch stream was 31 lines, all of them the
        # parent's own traceback. Everything the agent printed died in the
        # parent's buffer, so how far it got is unknowable and the new bound
        # could only be guessed at. TimeoutExpired carries what was read before
        # the kill — print it, then re-raise UNCHANGED so the job still
        # hard-fails and the alarm still fires. That part was always correct.
        _dump_captured(e.stdout, e.stderr,
                       f"{script} TIMED OUT after {e.timeout}s —")
        raise


def _run_ok(script: str, *args: str, ok_codes=(0,)) -> str:
    proc = _run(script, *args)
    print(proc.stdout[-4000:])
    # The pipeline's per-race diagnostics all go to stderr, and they are the
    # only record of WHY a run did what it did. Printing them only on failure
    # let the 2026-08-05 tips run drop all 31 races, exit 0, and leave a log
    # with no reason anywhere in it. Kept on success too, tail-bounded: with
    # 31 races of output the informative end is the last one.
    if proc.stderr and proc.stderr.strip():
        print(proc.stderr[-4000:], file=sys.stderr)
    if proc.returncode not in ok_codes:
        raise RuntimeError(f"{script} exited {proc.returncode}")
    return proc.stdout


def _missed_days(job: str, max_back: int) -> list:
    last = _get_state(job).get("last_success_date")
    if not last:
        return []
    start = datetime.strptime(str(last), "%Y-%m-%d").date() + timedelta(days=1)
    end = datetime.now(SYD).date()
    days = []
    d = start
    while d < end and len(days) < max_back:
        days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return days


# --- jobs -------------------------------------------------------------------

def job_racecard_collect() -> dict:
    healed = []
    for day in _missed_days("racecard-collect", PF_WALL_DAYS):
        try:
            _run_ok("download_racecards.py", "--date", day, ok_codes=(0, 3))
            _run_ok("seed_race_schedule.py", day)
            healed.append(day)
        except RuntimeError as e:
            print(f"backfill {day} failed: {e}", file=sys.stderr)
    out = _run_ok("download_racecards.py", "--date", _today(), ok_codes=(0,))
    _run_ok("seed_race_schedule.py", _today())
    _sync_up("server/python/racecards", "racecard_*.json")
    # The quiet-day sentinel has to travel too. Without it every downstream
    # task sees an absent card and cannot tell "no racing we bet on" from
    # "the 05:30 collect died" — which is the whole distinction.
    _sync_up("server/python/racecards", "quiet_*.json")
    quiet = os.path.exists(
        f"{_root()}/server/python/racecards/quiet_{_today()}.json")
    # Known limitation: a quiet day still advances last_success_date, so
    # _missed_days will never revisit it. That is correct while the target
    # list is fixed and wrong the day it widens — days that were quiet under
    # the old list would stay permanently uncollected. The sentinel records
    # the target_count it ran under and is synced to S3, so the affected days
    # are recoverable from the evidence rather than from memory; acting on
    # that is a separate change, and this note is here so it is a decision
    # rather than a discovery.
    return {"last_success_date": _today(), "gaps_healed": len(healed),
            "quiet_day": quiet, "detail": out[-500:]}


def _db_query(sql: str, params: tuple):
    """One-shot read for the job-contract layer.

    The handler owns no schema and holds no connection; it asks the
    database what the script it just ran actually left behind. URL checked
    before the driver import, or an environment missing psycopg2 masks the
    honest message with ModuleNotFoundError.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL unset — the post-condition cannot "
                           "tell a quiet day from a dead write")
    import psycopg2
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def _db_now():
    """The clock the rows are stamped with.

    snapshot_time defaults to NOW() and the upsert re-stamps it on
    conflict, so freshness must be measured DB-side. Timing a Neon-stamped
    row against this task's time.time() lets NTP drift decide whether the
    morning alarms.
    """
    return _db_query("SELECT NOW()", ())[0]


def job_morning_odds() -> dict:
    _sync_down("server/python/intelligence")
    # odds_movement reads the card for fallback odds
    if _require_racecard("morning-odds") == "quiet":
        # No card means no markets to map, so the MORNING_CHECK row assertion
        # below would fire. That assertion exists to catch the market pillar
        # going dark silently and must keep firing on every day that has races.
        return {"last_success_date": _today(), "morning_rows": 0,
                "signal_rows": 0, "quiet_day": True}
    t0 = _db_now()
    _run_ok("odds_movement.py", _today(), "--snapshot", "morning")

    # 1. The snapshot actually committed. odds_movement.py has no non-zero
    #    exit path, so without this the market pillar can go dark silently
    #    and every pick quietly degrades toward NO_BET.
    rows = _db_query(
        "SELECT COUNT(*) FROM betfair_odds_snapshots WHERE race_date = %s "
        "AND snapshot_type = 'MORNING_CHECK' AND snapshot_time >= %s",
        (_today(), t0))[0]
    if not rows:
        raise RuntimeError(
            "morning-odds: odds_movement.py exited 0 but committed no "
            "MORNING_CHECK rows — the market pillar has no data today.")

    # 2. Signals were derived, not just prices stored. On 2026-04-06 this
    #    job wrote 272 MORNING_CHECK rows against a 230-row baseline and
    #    produced ZERO market_signal_scores: compute_market_signals keys the
    #    baseline on the raw horse name (odds_movement.py:204-212, :231), so
    #    any name-format drift between the two snapshots misses every lookup
    #    and every runner silently falls back to neutral 50. Three of the
    #    five MORNING_CHECK days on record wrote no signals at all.
    #    Conditional on a baseline existing: with no baseline, zero signals
    #    is the documented and correct day-one behaviour, not a failure.
    baseline = _db_query(
        "SELECT COUNT(*) FROM betfair_odds_snapshots WHERE race_date = %s "
        "AND snapshot_type = 'BASELINE_NIGHT'", (_today(),))[0]
    signals = _db_query(
        "SELECT COUNT(*) FROM market_signal_scores WHERE race_date = %s",
        (_today(),))[0]
    if baseline and not signals:
        raise RuntimeError(
            f"morning-odds: {baseline} baseline rows and {rows} morning rows, "
            f"but ZERO market_signal_scores — the baseline join matched "
            f"nothing, so every runner defaulted to neutral. This is the "
            f"2026-04-06 failure, which passed green at the time.")

    _sync_up("server/python/intelligence")
    return {"last_success_date": _today(), "morning_rows": rows,
            "signal_rows": signals}


def job_tip_time_snapshot() -> dict:
    out = _run_ok("betfair_odds_snapshot.py", "--date", _today(),
                  "--kind", "tip_time", "--commit")
    rows = 0
    for line in out.splitlines():
        if line.startswith("Committed"):
            rows = int(line.split()[1])
    return {"last_success_date": _today(), "rows_written": rows}


def job_late_odds_watch() -> dict:
    # Self-gating: exits instantly outside [first_jump - 30min, last + grace].
    # --date, not positional: this script is one of the four that take a
    # flag. Passing it positionally is an argparse exit 2 on EVERY one of
    # the ~90 invocations a race day.
    _run_ok("capture_late_odds.py", "--date", _today())
    return {"last_success_date": _today()}


def job_results_collect() -> dict:
    """Collect TODAY and yesterday, matching the pf-evening-results workflow
    this replaces. Yesterday alone would leave the day's own races waiting
    for the 01:00 retry — and everything downstream (ledger settlement, the
    calibrator's settled-row evidence) waiting with them."""
    today = _today()
    yesterday = (datetime.now(SYD).date() - timedelta(days=1)).strftime("%Y-%m-%d")
    healed = []
    # _run's 840s cap raises subprocess.TimeoutExpired, which is a
    # SubprocessError and NOT a RuntimeError: catching only RuntimeError let
    # a slow run walk straight past both handlers below and hard-fail the
    # whole job, which is the exact opposite of what they promise.
    SOFT = (RuntimeError, subprocess.TimeoutExpired)
    for day in _missed_days("results-collect", PF_WALL_DAYS):
        try:
            _run_ok("auto_results_collector.py", "--date", day)
            _run_ok("fetch_and_import_date.py", "--date", day)
            healed.append(day)
        except SOFT as e:
            print(f"backfill {day} failed: {e}", file=sys.stderr)
    for day in (yesterday, today):
        # Today's late meetings may not have resulted yet at 22:30; the
        # 01:00 retry covers them, so today is non-fatal here.
        try:
            _run_ok("auto_results_collector.py", "--date", day)
            _run_ok("fetch_and_import_date.py", "--date", day)
            _run_ok("stride_results_collector.py", day)
        except SOFT as e:
            if day == yesterday:
                raise
            print(f"today ({day}) not fully resulted yet: {e}", file=sys.stderr)
    return {"last_success_date": today, "gaps_healed": len(healed)}


def job_gap_heal() -> dict:
    """The 03:00 self-healing pass plus the honesty report."""
    epoch = "2026-08-02"
    proc = _run("betfair_odds_snapshot.py", "--check-gaps",
                "--gaps-from", epoch)
    print(proc.stdout[-4000:])
    permanent = proc.stdout.count("results rows that day")
    healed = 0
    for job_fn, job_name in ((job_racecard_collect, "racecard-collect"),
                             (job_results_collect, "results-collect")):
        try:
            result = job_fn()
            healed += int(result.get("gaps_healed") or 0)
            _put_state(job_name, **result)
        except Exception as e:
            print(f"heal via {job_name} failed: {e}", file=sys.stderr)
    return {"last_success_date": _today(), "gaps_found": permanent,
            "gaps_healed": healed,
            "note": "tip_time gaps are live-only losses, never healable"}


def job_preflight() -> dict:
    # Exit 1 means "verdict RED", which is a finding to record, not a crash:
    # treating it as failure would alarm every morning until the last gate
    # passes — weeks of noise that teaches the operator to ignore the alarm.
    out = _run_ok("deploy_preflight.py", ok_codes=(0, 1))
    verdict = "GREEN"
    for line in out.splitlines():
        if line.startswith("VERDICT"):
            verdict = line.split(":", 1)[1].strip()
    # Gate status rides along daily so run-state (and the digest scan) always
    # carries a <24h-old readout; gate_status exits 1 while NOT READY, which
    # is a report, not a failure.
    gates = _run("gate_status.py")
    gate_line = next((l.strip() for l in reversed(gates.stdout.splitlines())
                      if l.strip()), "?")
    return {"last_success_date": _today(), "preflight": verdict[:200],
            "gates": gate_line[:200]}


def job_calibrator_coverage() -> dict:
    # Daily since the gate-3 fix: emits per-race-day calibrator_shadow_<date>
    # evidence to the durable store (what gate_status counts). Recomputed
    # from settled audit rows every run, so missed days backfill themselves.
    out = _run_ok("shadow_calibrator_compare.py", "--emit-evidence")
    return {"last_success_date": _today(), "detail": out[-300:] or "ran"}


def job_bsp_settle() -> dict:
    # Betfair BSP files publish on a lag; the sweep targets every settled
    # row still missing SP since day zero, so a late file self-heals. Exit 4
    # (a date past the grace window with no file) raises -> DLQ + alarm.
    out = _run_ok("bsp_settlement.py", "--since", "2026-08-02", "--commit")
    filled = 0
    for line in out.splitlines():
        if line.startswith("FILLED"):
            filled = int(line.split()[1])
    return {"last_success_date": _today(), "rows_written": filled}


def _require_racecard(job: str) -> str:
    """Every card-dependent job must FAIL when the card is missing.

    stride_build.py, odds_movement.py and run_tips_pipeline.py have no
    non-zero exit path, so without this a failed 05:30 collect would let
    06:00, 07:00 and 10:00 each run on nothing and report success — the
    whole morning silently producing no tips.

    A quiet day is the one case where no card is correct: the provider was
    healthy and listed meetings, none on the target-track list. Returns
    "card" or "quiet" and raises only on "missing", so the guard above keeps
    its teeth on the failure it was written for while a calendar fact stops
    taking four jobs red about three mornings a week.
    """
    state = _prepare_racecard()
    if state == "missing":
        raise RuntimeError(
            f"{job}: no racecard for {_today()} after relay. The 05:30 "
            f"racecard-collect either did not run or wrote nothing; every "
            f"downstream job today would otherwise no-op and report success.")
    if state == "quiet":
        print(f"[racecard] {job}: {_today()} is a QUIET DAY — the provider "
              f"listed meetings but none are on the target-track list. There "
              f"is no work to do and that is the correct outcome; see "
              f"racecards/quiet_{_today()}.json for what did race.")
    return state


def _fresh_files(dirpath: str, suffix: str, since: float) -> list:
    """Files written after `since` — proof a step did work, not just exited."""
    if not os.path.isdir(dirpath):
        return []
    return [f for f in os.listdir(dirpath) if f.endswith(suffix)
            and os.path.getmtime(os.path.join(dirpath, f)) >= since]


def job_intelligence_build() -> dict:
    if _require_racecard("intelligence-build") == "quiet":
        # Skipped rather than run-and-tolerated: stride_build.py has nothing
        # to build from, and the fresh-files post-condition below would fire
        # on the empty result. Weakening that assertion to accommodate a quiet
        # day would blind it on every other day.
        return {"last_success_date": _today(), "files_built": 0,
                "quiet_day": True}
    # Timed from just before the build, so a file relayed down earlier in
    # this same task can never be mistaken for one the build produced.
    t0 = time.time()
    _run_ok("stride_build.py", _today(), "--parallel")
    built = _fresh_files(f"{_root()}/server/python/intelligence", ".json", t0)
    if not built:
        raise RuntimeError(
            "intelligence-build: stride_build.py exited 0 but wrote no "
            "intelligence file; consensus and tips would run on stale data.")
    _sync_up("server/python/intelligence")
    return {"last_success_date": _today(), "files_built": len(built)}


def job_consensus_agent() -> dict:
    _sync_down("server/python/intelligence")
    if _require_racecard("consensus-agent") == "quiet":
        # consensus_agent.py exits 4 on zero yield by design — it treats an
        # empty panel as a failed run rather than a quiet one, which is right
        # when there are races to find mentions for. On a quiet day that would
        # be a RuntimeError and an SNS alarm for a day with nothing to do.
        # It also spends real LLM budget to reach that conclusion.
        return {"last_success_date": _today(), "quiet_day": True}
    _run_ok("consensus_agent.py", _today())
    path = f"{_root()}/server/python/intelligence/consensus_{_today()}.json"
    if not os.path.exists(path):
        raise RuntimeError(
            f"consensus-agent: {path} absent after a clean exit. Without "
            f"fresh consensus every pick degrades to NO_BET.")
    _sync_up("server/python/intelligence")
    return {"last_success_date": _today()}


def job_tips_pipeline() -> dict:
    _sync_down("server/python/intelligence")
    if _require_racecard("tips-pipeline") == "quiet":
        # No card means no races to tip, so tips_<date>.json is never written
        # and the existence check below would fail. Note this is a different
        # claim from the NO_BET case already documented there: that is "we
        # looked at every runner and bet none", this is "there was nothing to
        # look at". Both are legitimate; only the second has no output file.
        return {"last_success_date": _today(), "quiet_day": True}
    out = _run_ok("run_tips_pipeline.py", _today())
    _run_ok("backfill_tips_contract.py", _today())
    tips = f"{_root()}/racecards/tips_{_today()}.json"
    if not os.path.exists(tips):
        raise RuntimeError(
            f"tips-pipeline: {tips} absent after a clean exit — the "
            f"pipeline produced nothing and the frontend has no tips.")
    # Deliberately NOT asserting a bet count: a day on which every runner
    # gates to NO_BET is a legitimate outcome, not a failure.
    _sync_up("racecards", "tips_*.json")
    return {"last_success_date": _today(), "detail": out[-300:]}


def _prepare_racecard() -> str:
    """Relay the day's racecard down and place it in BOTH locations.

    Not just a tips concern: the intelligence agents, the consensus agent
    and odds_movement all resolve the card at the REPO-ROOT racecards/
    path, so every one of them needs this. intelligence-build ran without
    it and both agents died with "Racecard not found" — CLAUDE.md's
    recurring copy-path bug, reappearing in the cloud because each Fargate
    task starts with an empty filesystem.

    Returns "card", "quiet" or "missing". Three answers rather than two
    because "no card" has two causes that need opposite handling: the 05:30
    collect died (fail every downstream job, loudly), or the day is quiet and
    there was never a card to write (do nothing, and say so). Roughly a third
    of days are the second kind.
    """
    import shutil
    _sync_down("server/python/racecards")
    src = f"{_root()}/server/python/racecards/racecard_{_today()}.json"
    dst_dir = f"{_root()}/racecards"
    os.makedirs(dst_dir, exist_ok=True)
    if not os.path.exists(src):
        quiet_src = f"{_root()}/server/python/racecards/quiet_{_today()}.json"
        if os.path.exists(quiet_src):
            # Relay it alongside the card so anything reading the repo-root
            # directory sees the same evidence this handler did.
            shutil.copy(quiet_src, f"{dst_dir}/quiet_{_today()}.json")
            return "quiet"
        print(f"[racecard] {src} absent after relay — the 05:30 collect "
              f"either has not run or wrote nothing", file=sys.stderr)
        return "missing"
    shutil.copy(src, f"{dst_dir}/racecard_{_today()}.json")
    print(f"[racecard] staged into both locations for {_today()}")
    return "card"


def _tips_prepare() -> None:
    _sync_down("server/python/intelligence")
    _prepare_racecard()


def job_tips_proof() -> dict:
    """Exercise the whole 10:00 path in the real runtime, writing nothing.

    The tips job stops two gate clocks if it fails, and cannot be rehearsed
    by running it for real out-of-hours: an evening run would re-upsert
    day-one ledger rows at post-race prices. This proves the parts that
    have never executed — model staging from S3, the artifact relay, the
    card copy, MC scoring under the task's memory and time limits — with
    DB writes skipped, the ledger forced off, shadow evidence forced off
    (so the registered delta distribution gains no duplicate races), and
    output to a suffixed file that no consumer reads.
    """
    os.environ["STRIDE_LEDGER_WRITE"] = "false"
    os.environ["STRIDE_SERVE_LIVE_FEATURES_SHADOW"] = "false"
    _tips_prepare()
    out = _run_ok("run_tips_pipeline.py", _today(), "--skip-db-store",
                  "--output-suffix", "cloudproof")
    return {"last_success_date": _today(), "detail": out[-400:]}


def job_consensus_proof() -> dict:
    """Consensus in --dry-run: proves the container, secrets, DB and panel
    setup without LLM spend and without overwriting today's consensus file
    (which the tips hard-gate depends on)."""
    out = _run_ok("consensus_agent.py", _today(), "--dry-run")
    return {"last_success_date": _today(), "detail": out[-400:]}


def job_nightly_etl() -> dict:
    yesterday = (datetime.now(SYD).date() - timedelta(days=1)).strftime("%Y-%m-%d")
    for script, args in (("nsw_sectional_collector.py", ("--date", yesterday)),
                         ("sectional_times_collector.py", ("--date", yesterday)),
                         ("racing_com_sectionals_collector.py", ("--date", yesterday))):
        try:
            _run_ok(script, *args)
        except RuntimeError as e:
            print(f"{script}: {e}", file=sys.stderr)
    _sync_up("server/python/intelligence")
    return {"last_success_date": yesterday}


def job_weekly_digest() -> dict:
    rows = _state().scan().get("Items", [])
    lines = ["STRIDE weekly digest", "=" * 40]
    for r in sorted(rows, key=lambda x: x.get("job_name", "")):
        # quiet_day is carried so a week of green rows is readable. Without it
        # a quiet Monday and a working Monday look identical in the digest,
        # and "everything is green" stops meaning "everything ran".
        quiet = " QUIET" if r.get("quiet_day") else ""
        lines.append(f"{r.get('job_name')}: last_success="
                     f"{r.get('last_success_date')}{quiet} rows={r.get('rows_written', '-')} "
                     f"gaps_found={r.get('gaps_found', 0)} healed={r.get('gaps_healed', 0)} "
                     f"{('preflight=' + str(r.get('preflight'))) if r.get('preflight') else ''}")
    # Gate status comes from the run-state row the (Fargate) preflight job
    # writes daily — the digest Lambda cannot run gate_status itself: gate 5
    # shells retrain_preflight, which reads the model artifact, absent on a
    # Lambda by design.
    gates = next((r.get("gates") for r in rows
                  if r.get("job_name") == "preflight" and r.get("gates")), None)
    lines += ["", f"Retrain gates (preflight job, daily): {gates or 'no row yet'}"]
    try:
        snap = _run("betfair_odds_snapshot.py", "--check-gaps",
                    "--gaps-from", "2026-08-02")
        lines += ["", "tip_time capture:", snap.stdout[-800:]]
    except Exception:
        pass
    body = "\n".join(lines)
    print(body)
    # The ARN is in the environment; listing topics needs an SNS:ListTopics
    # grant the role deliberately does not have (least privilege) and cost
    # the digest its entire run before publishing anything.
    arn = os.environ.get("STRIDE_ALERT_TOPIC_ARN", "").strip()
    if not arn:
        raise RuntimeError("STRIDE_ALERT_TOPIC_ARN unset — digest has "
                           "nowhere to publish")
    boto3.client("sns", region_name=REGION).publish(
        TopicArn=arn, Subject="STRIDE weekly digest", Message=body)
    return {"last_success_date": _today()}


JOBS = {
    "racecard-collect": job_racecard_collect,
    "morning-odds": job_morning_odds,
    "tip-time-snapshot": job_tip_time_snapshot,
    "late-odds-watch": job_late_odds_watch,
    "results-collect": job_results_collect,
    "gap-heal": job_gap_heal,
    "preflight": job_preflight,
    "calibrator-coverage": job_calibrator_coverage,
    "bsp-settle": job_bsp_settle,
    "intelligence-build": job_intelligence_build,
    "consensus-agent": job_consensus_agent,
    "tips-pipeline": job_tips_pipeline,
    # Proof variants: same image and task definition, selected by the
    # STRIDE_JOB override. Never scheduled — dispatched by hand to verify
    # a path before it runs for real.
    "tips-proof": job_tips_proof,
    "consensus-proof": job_consensus_proof,
    "nightly-etl": job_nightly_etl,
    "weekly-digest": job_weekly_digest,
}


def dispatch(event=None, context=None):
    job = os.environ.get("STRIDE_JOB", "").strip()
    if job not in JOBS:
        raise RuntimeError(f"unknown STRIDE_JOB {job!r}; known: {sorted(JOBS)}")
    _load_secrets()
    _stage_models()
    try:
        result = JOBS[job]()
        _put_state(job, **result)
        return {"job": job, "ok": True, **{k: str(v) for k, v in result.items()}}
    except Exception as e:
        _put_state(job, last_error=f"{type(e).__name__}: {e}"[:400],
                   last_error_at=datetime.now(timezone.utc).isoformat())
        # One failure hook for BOTH runtimes: Lambda's DLQ/alarm path only
        # covers Lambdas; a failed Fargate task would otherwise die silent
        # until the digest noticed the stale run-state row.
        arn = os.environ.get("STRIDE_ALERT_TOPIC_ARN", "").strip()
        if arn:
            try:
                boto3.client("sns", region_name=REGION).publish(
                    TopicArn=arn, Subject=f"STRIDE job FAILED: {job}",
                    Message=f"{type(e).__name__}: {e}"[:1000])
            except Exception as sns_err:
                print(f"alert publish failed: {sns_err}", file=sys.stderr)
        raise


if __name__ == "__main__":
    print(json.dumps(dispatch(), indent=2, default=str))

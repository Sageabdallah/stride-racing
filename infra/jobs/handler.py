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
    """The race date every job keys its work off: the Sydney calendar date,
    or STRIDE_DATE when an operator sets it on a hand-dispatched task
    (verify-jobs.yml's `date` input).

    Every artifact in the chain is named by this date (racecard, intelligence,
    consensus_<date>.json, tips_<date>.json), so overriding it once here runs
    the whole chain for a chosen day: a Friday preview of Saturday's card, or
    a rebuild of a day the schedule missed. Never set it on a schedule, and
    never pair it with the real tips-pipeline job outside its slot: that job
    writes ledger rows the registered window depends on. tips-proof is the
    out-of-slot variant, and writes nothing to the database.
    """
    forced = os.environ.get("STRIDE_DATE", "").strip()
    if forced:
        # Anything that is not a calendar date is a typo, not a request.
        datetime.strptime(forced, "%Y-%m-%d")
        return forced
    return datetime.now(SYD).strftime("%Y-%m-%d")


def _root() -> str:
    return os.environ.get("LAMBDA_TASK_ROOT", "/var/task")


# --- cross-task plumbing ----------------------------------------------------
#
# Two facts of the runtime shape everything here: Lambda's filesystem is
# read-only (so any job that writes repo paths runs on Fargate), and Fargate
# tasks do NOT share a filesystem (so the 04:00 racecard -> 04:20
# intelligence -> 05:30 consensus -> 08:05 tips chain relays its file
# artifacts through S3). Models are proprietary IP in a PUBLIC repo: never
# in git or the image — staged from the private models bucket at startup.

def _s3():
    return boto3.client("s3", region_name=REGION)


# The artifact the scorer opens by name (ml_model.py:165). Listed rather than
# inferred: a count cannot tell "the ensemble is here" from "four JSONs are".
REQUIRED_MODEL_ARTIFACTS = ("racing_ensemble_v2.pkl",)


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
    manifest_key = os.environ.get("STRIDE_RELEASE_MANIFEST_KEY", "").strip()
    manifest_required = os.environ.get(
        "STRIDE_RELEASE_MANIFEST_REQUIRED", "false").strip().lower() in (
            "1", "true", "yes", "on")
    if manifest_key:
        from pathlib import Path
        from release_manifest import (
            artifact_downloads,
            load_manifest,
            sha256_file,
            validate_artifact_files,
        )

        manifest_path = Path(dest) / "release_manifest.json"
        _s3().download_file(bucket, manifest_key, str(manifest_path))
        manifest = load_manifest(manifest_path)
        ensemble_path = str(manifest["artifacts"]["ensemble"]["path"])
        if ensemble_path != REQUIRED_MODEL_ARTIFACTS[0]:
            raise RuntimeError(
                "release manifest ensemble path must be "
                f"{REQUIRED_MODEL_ARTIFACTS[0]!r}, got {ensemble_path!r}; "
                "ml_model.py loads the required artifact by that exact name"
            )
        for object_key, relative_path in artifact_downloads(manifest):
            local_path = (Path(dest) / relative_path).resolve()
            if Path(dest).resolve() not in local_path.parents:
                raise RuntimeError(
                    f"release manifest artifact escapes model directory: {relative_path}")
            local_path.parent.mkdir(parents=True, exist_ok=True)
            _s3().download_file(bucket, object_key, str(local_path))
        validate_artifact_files(manifest, Path(dest))
        missing = [f for f in REQUIRED_MODEL_ARTIFACTS
                   if not os.path.exists(os.path.join(dest, f))]
        if missing:
            raise RuntimeError(
                "validated release manifest did not stage required runtime "
                "artifact(s): " + ", ".join(missing)
            )
        os.environ["STRIDE_RELEASE_ID"] = str(manifest["release_id"])
        os.environ["STRIDE_RELEASE_MANIFEST_SHA256"] = sha256_file(manifest_path)
        print(f"[models] staged validated release {manifest['release_id']} "
              f"from s3://{bucket}/{manifest_key}")
        return
    if manifest_required:
        raise RuntimeError(
            "STRIDE_RELEASE_MANIFEST_REQUIRED is true but "
            "STRIDE_RELEASE_MANIFEST_KEY is unset")
    n = 0
    for page in _s3().get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            # config/ is not a model artifact. Without this skip it would
            # land a second copy at models/tipster_panel.json that nothing
            # reads, and a bucket holding only config would satisfy the
            # "models bucket is EMPTY" check below.
            if o["Key"].startswith("config/"):
                continue
            name = os.path.basename(o["Key"])
            if name:
                _s3().download_file(bucket, o["Key"], os.path.join(dest, name))
                n += 1
    print(f"[models] staged {n} artifact(s) from s3://{bucket}")
    if n == 0:
        raise RuntimeError(
            f"models bucket s3://{bucket} is EMPTY — run "
            "infra/09b_upload_models.sh from the box that has the artifacts")
    # Count was a PROXY for "the ensemble is here", and CLAUDE.md's own rule is
    # to verify content rather than proxies. A bucket holding the four
    # sectional_combiner JSONs and no .pkl staged 4 artifacts and satisfied the
    # check above, while the file the scorer actually opens — ml_model.py:165,
    # models/racing_ensemble_v2.pkl — was absent. That is the same failure this
    # whole pass keeps finding: a green check standing in front of a missing
    # thing. Name the artifact instead of counting its neighbours.
    missing = [f for f in REQUIRED_MODEL_ARTIFACTS
               if not os.path.exists(os.path.join(dest, f))]
    if missing:
        raise RuntimeError(
            f"models bucket s3://{bucket} staged {n} artifact(s) but not "
            f"{', '.join(missing)} — ml_model.py loads that by name, so the "
            f"scorer would fail at 08:05 with the day already half gone, or "
            f"worse, score on a fallback. Re-run infra/09b_upload_models.sh.")


PANEL_KEY = "config/tipster_panel.json"


def _stage_panel() -> bool:
    """Pull the tipster panel out of the private models bucket.

    Same reasoning as _stage_models: the repo is PUBLIC and this file is
    gitignored, so it is not in the build context and cannot be in the
    image. It carries which 16 of 37 sources are trusted, which bucket each
    is weighted into, and which get the proofed-results boost — the vetting,
    not a description of it. historical_accuracy is null today and will not
    stay null, and git is a one-way door: a file published while it is
    low-value stays published in every clone once it is not.

    Called from dispatch() rather than from the consensus jobs, so every
    path through this handler stages it — Fargate, Lambda, proof variants,
    and any job added later that nobody remembers to wire up.

    Returns rather than raising, and that is deliberate: results-collect
    must not go red because the panel is missing. The loud failure belongs
    to the CONSUMER — consensus_agent.load_tipster_panel raises
    PanelUnavailable (exit 6) — so a job that needs the panel fails and a
    job that does not carries on. A best-effort stage is only safe because
    something else is not best-effort.
    """
    bucket = os.environ.get("STRIDE_MODELS_BUCKET", "").strip()
    if not bucket:
        return False
    dest = f"{_root()}/server/python/tipster_panel.json"
    try:
        # Lambda's task root is read-only, so staging cannot work there at
        # all. No Lambda job runs consensus today; if one ever does, the
        # consumer's guard is what catches it rather than this returning a
        # quiet False that reads like "nothing to do".
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        _s3().download_file(bucket, PANEL_KEY, dest)
        print(f"[panel] staged from s3://{bucket}/{PANEL_KEY}")
        return True
    except Exception as e:
        print(f"[panel] NOT staged from s3://{bucket}/{PANEL_KEY}: "
              f"{type(e).__name__}: {e}. Jobs that read the panel will fail "
              f"loudly; run infra/09c_upload_panel.sh if it was never "
              f"uploaded.", file=sys.stderr)
        return False


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
# so a 31-race metro Saturday is ~290 + 31*243 = ~7,800s.
#
# The gap that binds a job is the gap to the job that READS ITS OUTPUT, which
# is not always the next one on the clock. consensus_agent was first bounded at
# 2700s against morning-odds at 08:00; morning-odds runs odds_movement.py,
# which does not reference consensus at all. The only reader of
# consensus_<date>.json is run_tips_pipeline (via consensus_blender
# .load_consensus_intelligence), so the real gap is the one to tips.
#
# --- 2026-08-06: the earlier start, and the one bound that had to move -------
#
# The note above said raising the tips bound "does not make the 10:00 schedule
# sound... the fix is the per-race cost, or an earlier start." The earlier
# start is now taken: the whole chain moved (infra/07b_fargate_schedules.sh),
# card 04:00 -> intelligence 04:20 -> consensus 05:30 -> tips 08:05.
#
# The chain was moved so the GAPS stayed wide enough for the bounds already
# here, not the other way round. stride_build keeps 3400 inside a 04:20->05:30
# gap of 4200s; consensus_agent keeps 9000 inside a 05:30->08:05 gap of 9300s.
# Neither loosens. If either schedule is edited again, check the gap first: a
# bound wider than its gap converts a loud timeout into the next task silently
# syncing down yesterday's file.
#
# Only the tips bound changes, and only because the card it must cover got
# measured properly. From the tips files' own timing block:
#
#   run_tips_pipeline  5875.1s / 31 races (2026-08-05)   ~190.0s/race
#                      1540.9s /  8 races (2026-08-06)   ~192.6s/race
#
# The two agree to 1.4%, and 190s is the LOW estimate — the per-race
# decomposition above, taken on 18-runner Belmont fields, says 243s, and
# Saturday fields are metro and big. At 90 races that is 290 + 90*243 =
# ~22,100s. The old 10,800s bound covered ~43 races: it would have killed
# every recent Saturday part-way through, on the day the system exists for.
#
# 21600 was then still 500s under its own worst case, and every figure above
# was measured on runs where the LLM contributed nothing — 2026-09-02 spent
# 1.1s in it across 30 races because every call was failing. A working provider
# is not free: run_tips_pipeline makes SIX blocking calls per race, strictly
# sequentially and with no pool or batching (analyse_race_field,
# score_race_horses, three generate_rich_insight, generate_brief_assessments).
# At ~20s each that is ~120s/race on top of MC — for the 55-race 2026-09-05
# card, ~13,400s of scoring plus ~6,600s of LLM, or 92% of the old bound.
#
# The failure that bound buys is the worst available: the tips JSON is written
# once, after the whole meet loop, so a subprocess killed at the cap produces
# NO file at all — six hours of Fargate and nothing to show. Widened to 8.5h,
# which still lands 08:05 + 8.5h = 16:35, far inside the 22:30 results gap the
# note above says to check.
JOB_TIMEOUTS = {
    "stride_build.py": 3400,
    "consensus_agent.py": 9000,
    "run_tips_pipeline.py": 30600,
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


def job_baseline_night() -> dict:
    """Captures the reference snapshot job_morning_odds diffs against.

    The only job in the daily chain never ported to Fargate. It ran on the
    Mac as scheduler.ts's `consensus_baseline_odds`, and every
    BASELINE_NIGHT row in the database was written by it; there has been
    none since 2026-05-04. compute_market_signals derives movement by
    diffing MORNING_CHECK against BASELINE_NIGHT, so with no baseline every
    runner falls back to neutral 50 however healthy odds capture itself is
    — which is why market_signal_scores has been empty since 2026-04-18.

    NOT at 00:30, which is when scheduler.ts ran it and what
    betfair_snapshot_coverage_audit.py still documented. That time is a
    Mac-era constant and does not survive the migration: the Mac downloaded
    a week of cards every Wednesday 4 PM (scheduler.ts
    `download_racecards_wednesday`), so a card for today existed at
    midnight. On Fargate racecard-collect writes it at 05:31 the same
    morning and seed_race_schedule.py seeds race_schedule in the same task
    — and betfair_prices maps the Exchange catalogue THROUGH race_schedule.
    At 00:30 there is neither, so this job would raise in
    _require_racecard every single day. Hence 04:15: after the 04:00
    collect has landed both, before the morning check. The price is a ~2h
    lever arm instead of an overnight one; widening it means seeding
    tomorrow's card tonight, which is a separate change.

    Carries the same "exited 0 but wrote no rows" postcondition
    morning-odds has for MORNING_CHECK, so a silent repeat of this gap
    fails loudly here rather than surfacing four months later as a
    different job's missing output.
    """
    _sync_down("server/python/intelligence")
    # odds_movement reads the card for its fallback odds, same as
    # morning-odds — and _prepare_racecard is what proves the 05:30 collect
    # actually landed before this runs.
    if _require_racecard("baseline-night") == "quiet":
        return {"last_success_date": _today(), "baseline_rows": 0,
                "quiet_day": True}
    t0 = _db_now()
    _run_ok("odds_movement.py", _today(), "--snapshot", "baseline_night")

    rows = _db_query(
        "SELECT COUNT(*) FROM betfair_odds_snapshots WHERE race_date = %s "
        "AND snapshot_type = 'BASELINE_NIGHT' AND snapshot_time >= %s",
        (_today(), t0))[0]
    if not rows:
        raise RuntimeError(
            "baseline-night: odds_movement.py exited 0 but committed no "
            "BASELINE_NIGHT rows — the morning check will have nothing to "
            "diff against and every runner will default to neutral, same as "
            "every day since 2026-05-04.")

    return {"last_success_date": _today(), "baseline_rows": rows}


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

    # 2. Relay the signals file BEFORE asserting on it. market_signals_
    #    <date>.json is what consensus_blender actually reads at tips time
    #    (consensus_blender.py:315) — the DB table is not that path — and
    #    Fargate tasks share no filesystem, so a file that is not synced up
    #    does not exist as far as 08:15 is concerned. Ordered before the
    #    assertions deliberately: every check below is about whether the
    #    market pillar is HEALTHY, and failing one must not also delete the
    #    degraded-but-usable artifact from the run. On 2026-08-06 the
    #    row assertion fired and market_signals_2026-08-06.json never left
    #    the container. The alarm still fires — the exception is raised, the
    #    job still goes red, _put_state still records it. Only the evidence
    #    survives the failure now.
    _sync_up("server/python/intelligence")

    # 3. Every scheduled venue has at least one MORNING_CHECK row. The row
    #    check above is all-or-nothing per DAY: on 2026-08-05 this job passed
    #    green with odds for 2 of 4 scheduled venues (Canterbury, Doomben)
    #    while Cranbourne and Belmont Park mapped zero — 16 races dark and
    #    invisible. Per-VENUE, never per-race: Betfair publishes provincial
    #    markets only a few hours out, so a venue with 3 of 8 races covered
    #    at 08:00 is healthy, and a per-race check would false-alarm on every
    #    provincial card. stride_norm_track is the DB's own normaliser
    #    (identity_normalization.py), applied to BOTH sides: race_schedule
    #    stores capitalised names and snapshots store lowercased ones, and
    #    before 41df6c8 the function's ELSE branch deleted uppercase letters,
    #    so the first cut of this check (#97) would have reported every venue
    #    missing on every day. scripts/verify_venue_coverage.py executes this
    #    comparison against the real schema; it was run against prod before
    #    this shipped (Aug 5/6 known answers match, recent days report none
    #    missing).
    venues_scheduled = _db_query(
        "SELECT COUNT(DISTINCT stride_norm_track(track)) FROM race_schedule "
        "WHERE race_date = %s", (_today(),))[0]
    missing = _db_query(
        "SELECT COALESCE(array_agg(sched.track ORDER BY sched.track), '{}') "
        "FROM ("
        "  SELECT MIN(track) AS track, stride_norm_track(track) AS v "
        "  FROM race_schedule WHERE race_date = %s GROUP BY v"
        ") sched LEFT JOIN ("
        "  SELECT DISTINCT stride_norm_track(track) AS v "
        "  FROM betfair_odds_snapshots "
        "  WHERE race_date = %s AND snapshot_type = 'MORNING_CHECK' "
        "  AND snapshot_time >= %s"
        ") cov ON cov.v = sched.v "
        "WHERE cov.v IS NULL",
        (_today(), _today(), t0))[0]
    if missing:
        raise RuntimeError(
            f"morning-odds: {len(missing)} of {venues_scheduled} scheduled "
            f"venues have NO MORNING_CHECK odds today: "
            f"{', '.join(missing)}. The day-level row check cannot see a "
            f"single dark venue — this is the 2026-08-05 failure, which "
            f"passed green at the time.")

    # 4. A baseline exists at all. baseline-night runs at 04:15 with its own
    #    "wrote zero rows" postcondition, so reaching a non-quiet morning
    #    with zero baseline rows means that alarm should already have fired.
    #    Checked and raised on before signals are queried: the old
    #    `if baseline and not signals` could only fire once baseline was
    #    already nonzero, which is exactly why it never fired once in the
    #    four months baseline-night went unscheduled.
    baseline = _db_query(
        "SELECT COUNT(*) FROM betfair_odds_snapshots WHERE race_date = %s "
        "AND snapshot_type = 'BASELINE_NIGHT'", (_today(),))[0]
    if not baseline:
        raise RuntimeError(
            f"morning-odds: ZERO BASELINE_NIGHT rows for {_today()} — "
            f"baseline-night should have raised on this at 04:15 if it did "
            f"not run clean today. Confirm it actually fired before treating "
            f"this as a first day with nothing to diff against.")

    # 5. Signals were derived, not just prices stored. On 2026-04-06 this
    #    job wrote 272 MORNING_CHECK rows against a 230-row baseline and
    #    produced ZERO market_signal_scores: compute_market_signals keys the
    #    baseline on the raw horse name (odds_movement.py:204-212, :231), so
    #    any name-format drift between the two snapshots misses every lookup
    #    and every runner silently falls back to neutral 50. Three of the
    #    five MORNING_CHECK days on record wrote no signals at all.
    signals = _db_query(
        "SELECT COUNT(*) FROM market_signal_scores WHERE race_date = %s",
        (_today(),))[0]
    if not signals:
        raise RuntimeError(
            f"morning-odds: {baseline} baseline rows and {rows} morning rows, "
            f"but ZERO market_signal_scores — the baseline join matched "
            f"nothing, so every runner defaulted to neutral. This is the "
            f"2026-04-06 failure, which passed green at the time.")

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


# The realised context-multiplier distribution (audit 2026-09-06 H3/H4) is
# the runtime proof docs/analysis SYSTEM_MAP §9 Q3 said only a run could
# give. One stderr line per race would not survive this handler's 4000-char
# tail, so with the flag on run_tips_pipeline also records it in the artifact
# — races[].context_multipliers and summary.context_multipliers in
# tips_<date>.json, which _sync_up relays. It changes no probability and
# costs nothing, so the cloud tips jobs default it on; an explicit "false"
# in the task environment or the stride/prod secret still wins (setdefault).
CTX_MULT_DIAG_FLAG = "STRIDE_CTX_MULT_DIAG"


def job_tips_pipeline() -> dict:
    os.environ.setdefault(CTX_MULT_DIAG_FLAG, "true")
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


def _tips_prepare() -> str:
    """Relay the inputs and return the card state, "card" or "quiet".

    Raises on "missing", like every other card-dependent job: a proof that
    scores nothing and exits 0 has proved nothing.
    """
    _sync_down("server/python/intelligence")
    return _require_racecard("tips-proof")


# Below this share of top picks carrying insight text, an LLM-enabled preview
# is a failure rather than a thin result. The 2026-09-02 shape (0 of 90) is the
# degenerate end of a continuum: a provider that starts rate-limiting at race 20
# leaves most picks blank and still exits 0, and a gate that only fires at
# exactly zero passes 40/330. Not 1.0 — a handful of per-pick errors is normal
# and losing the artifact over three blank insights helps nobody.
INSIGHT_COVERAGE_FLOOR = 0.80


def _llm_expected() -> bool:
    """run_tips_pipeline's own reading of LLM_ENABLED: unset means on.

    Deliberately character-for-character run_tips_pipeline.py's expression,
    .strip() included — which is to say, excluded. Adding one made the handler
    strictly more permissive: LLM_ENABLED=" true " disabled the LLM in the
    pipeline while the handler asserted insights were expected, failing a run
    that did exactly what its environment said.
    """
    return os.environ.get("LLM_ENABLED", "true").lower() in ("true", "1", "yes")


def _insight_coverage(path: str) -> tuple:
    """(top picks with non-blank ai_insight, top picks) for a tips file.

    Counts text, not timestamps: the 2026-09-02 file stamped
    ai_insight_generated_at on every pick while every ai_insight was "".

    Raises on a file that cannot be read or parsed. Returning (0, 0) for it
    made three different outcomes identical at the call site — corrupt file,
    empty races list, and every race an error stub — and all three then passed
    the caller's guard, which is the failure this job exists to catch.
    """
    with open(path) as fh:
        data = json.load(fh)
    picks = [p for race in (data.get("races") or [])
             for p in (race.get("top_picks") or [])]
    with_text = sum(1 for p in picks if str(p.get("ai_insight") or "").strip())
    return with_text, len(picks)


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

    Four switches, not two. The ledger and the shadow-evidence flag were the
    writes this proof knew about. Walking the path for the 2026-09-05 preview
    found two more that --skip-db-store never gated: odds_snapshots' tip_time
    capture (STRIDE_ODDS_SNAPSHOT_WRITE, default on) and mc_api's
    prediction_audit / feature_snapshots / race_schedule logging
    (STRIDE_MC_AUDIT_WRITE, added for this). An evening proof never noticed:
    it re-scored a date whose real rows already existed and lost every
    first-write-wins conflict, so it left no mark. A preview runs BEFORE the
    real run and would have won them, its Friday probabilities and Friday
    prices becoming Saturday's record in the tables training_view_v2 and the
    ledger price backfill read.

    The insight count at the end is the check the 2026-09-02 run needed: 90
    picks, every ai_insight empty, 1.1s of LLM time, exit 0. A proof that
    finishes with the LLM enabled and no insight text has not proved the
    thing it was dispatched for, so it relays the file (evidence first) and
    then fails.
    """
    os.environ["STRIDE_LEDGER_WRITE"] = "false"
    os.environ["STRIDE_SERVE_LIVE_FEATURES_SHADOW"] = "false"
    os.environ["STRIDE_ODDS_SNAPSHOT_WRITE"] = "false"
    os.environ["STRIDE_MC_AUDIT_WRITE"] = "false"
    os.environ.setdefault(CTX_MULT_DIAG_FLAG, "true")   # diagnostic only, see above
    if _tips_prepare() == "quiet":
        return {"last_success_date": _today(), "quiet_day": True}
    # run_tips_pipeline has always taken tracks positionally; this job simply
    # never passed them, so the only available preview was the whole card.
    # That matters because the run cannot be watched to completion — the OIDC
    # session dies at an hour and a Saturday card is four to five — so the
    # ability to ask for a two-hour subset instead is the difference between a
    # preview that lands when someone needs it and one that lands at 1am.
    # Comma-separated, because a container override is one string.
    tracks = [t.strip() for t in
              os.environ.get("STRIDE_TRACKS", "").split(",") if t.strip()]
    if tracks:
        print(f"[tips-proof] track filter: {', '.join(tracks)} "
              f"(the rest of the card is not scored)")
    out = _run_ok("run_tips_pipeline.py", _today(), *tracks,
                  "--skip-db-store", "--output-suffix", "cloudproof")
    tips = f"{_root()}/racecards/tips_{_today()}_cloudproof.json"
    if not os.path.exists(tips):
        raise RuntimeError(
            f"tips-proof: {tips} absent after a clean exit — the pipeline "
            f"produced nothing, so there is nothing to preview or to prove.")
    # No consumer reads the suffixed file, but for a preview run of a chosen
    # date (STRIDE_DATE) it is the whole point, and the task's filesystem is
    # gone the moment it stops. Relayed under its own name, so it can never
    # be mistaken for the real tips_<date>.json the frontend and the Saturday
    # wrap-up read. Relayed BEFORE the insight check, for the reason
    # morning-odds relays before it asserts: a failed check must not also
    # destroy the evidence of what the run produced.
    _sync_up("racecards", f"tips_{_today()}_cloudproof.json")
    try:
        with_text, picks = _insight_coverage(tips)
    except (OSError, ValueError) as e:
        raise RuntimeError(
            f"tips-proof: {tips} exists but could not be parsed ({e}). A "
            f"corrupt artifact is a failed preview, not a preview with no "
            f"insights. It was relayed first, so it can still be inspected.")
    if not picks:
        # run_tips_pipeline writes the file unconditionally and has no non-zero
        # exit path: every race erroring produces top_picks [] and exit 0. The
        # existence check above cannot see that, and this is the exact shape of
        # the 2026-08-05 run that dropped all 31 races and reported success.
        raise RuntimeError(
            f"tips-proof: {tips} carries no top picks at all — the pipeline "
            f"exited 0 having scored nothing. There is no preview here.")
    if _llm_expected():
        floor = max(1, int(picks * INSIGHT_COVERAGE_FLOOR))
        if with_text < floor:
            raise RuntimeError(
                f"tips-proof: LLM enabled but only {with_text} of {picks} top "
                f"picks carry insight text (floor {floor}). Either every call "
                f"failed — the 2026-09-02 shape — or the provider degraded "
                f"part-way and most picks are blank. The file was relayed "
                f"first so it can be read.")
    return {"last_success_date": _today(), "insights": f"{with_text}/{picks}",
            "detail": out[-400:]}


def job_llm_proof() -> dict:
    """Does the LLM this task is configured with produce insight text?

    Two synthetic picks through generate_rich_insight in the real runtime,
    with the real secrets, on the provider LLM_PROVIDER names: the value on
    the task if verify-jobs set one, otherwise the stride/prod secret's.
    Never scheduled. Dispatched by hand before any run whose insights are the
    point, and after a change to the LLM secrets, because the pipeline itself
    cannot tell "insights off" from "insights broken": both leave ai_insight
    empty and exit 0 (2026-09-02, 90 of 90 picks).

    Exit 1 from the script is a finding, not a crash; the markers say which
    step failed. It is accepted here and turned into the specific error.
    """
    out = _run_ok("llm_insight_proof.py", ok_codes=(0, 1))
    provider = model = None
    chars = []
    verdict = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("LLM_PROOF provider="):
            fields = dict(f.split("=", 1) for f in line.split()[1:] if "=" in f)
            provider = fields.get("provider")
            model = fields.get("model")
        elif line.startswith("LLM_PROOF pick="):
            fields = dict(f.split("=", 1) for f in line.split()[1:] if "=" in f)
            try:
                chars.append(int(fields.get("chars", "0")))
            except ValueError:
                chars.append(0)
        elif line.startswith("LLM_PROOF result="):
            verdict = line
    if provider is None:
        raise RuntimeError(
            "llm-proof: llm_insight_proof.py printed no LLM_PROOF provider "
            "marker — it exited before constructing a provider; the tail above "
            "carries the import or configuration error.")
    if not verdict.startswith("LLM_PROOF result=PASS"):
        raise RuntimeError(
            f"llm-proof: {provider} ({model}) did not produce insight text — "
            f"{verdict or 'no verdict line'}. A tips run on this configuration "
            f"would publish blank ai_insight for every pick.")
    return {"last_success_date": _today(), "provider": provider, "model": model,
            "insight_chars": ",".join(str(c) for c in chars)}


def job_consensus_proof() -> dict:
    """Consensus in --dry-run: proves the container, secrets and panel setup
    without LLM spend and without overwriting today's consensus file (which
    the tips hard-gate depends on).

    The racecard relay was missing and made this claim false. Fargate tasks
    start with an empty filesystem, so with no card staged run_consensus
    returns at its `load_racecard_meetings` check — which sits ABOVE
    load_tipster_panel — prints "No racecard found", writes an empty
    consensus file and exits 0. Verified 2026-08-06 (ECS task 417b4554):
    PASSED, and not one line of panel or secret setup ran. A proof that
    cannot reach what it proves is worse than no proof, because it is
    reported as evidence.
    """
    _sync_down("server/python/intelligence")
    if _require_racecard("consensus-proof") == "quiet":
        return {"last_success_date": _today(), "quiet_day": True}
    out = _run_ok("consensus_agent.py", _today(), "--dry-run")
    # The line run_consensus prints once the panel is loaded. Absent means
    # the run stopped short of it again, whatever the exit code said.
    if "[PANEL]" not in out:
        raise RuntimeError(
            "consensus-proof: exited 0 without reaching the tipster panel. "
            "Something returned before load_tipster_panel — check for 'No "
            "racecard found' or 'No runners' above.")
    return {"last_success_date": _today(), "detail": out[-400:]}


def job_panel_proof() -> dict:
    """Does the tipster panel actually fetch? --dry-run cannot answer that.

    dry_run returns before tavily_client.extract is called, so it proves the
    list parses and nothing more. This does the real extracts and nothing
    else: no races, no Perplexity, no Claude, no DB. Never scheduled —
    dispatched by hand, like the other proof variants.

    Exit 1 is "staged, but the sources have rotted" — a finding for the URL
    pass, not a staging failure, and the same shape as job_preflight's RED
    verdict. Accepting it is not leniency, it is the difference between two
    opposite instructions: the documented response to a red panel-proof is to
    set STRIDE_PANEL_OPTIONAL and run without the panel. At the measured 4 of
    16 usable sources, failing on exit 1 would report the panel as broken on
    the first day it was ever shipped working, and the operator would then
    correctly follow the runbook and disable it. A red that means the opposite
    of what its reader will do is worse than no check.

    Only PANEL_STAGED 0 — the panel never reached the container — is what this
    job exists to detect.
    """
    out = _run_ok("consensus_agent.py", _today(), "--panel-only",
                  ok_codes=(0, 1, 6))
    staged, usable, total = None, None, None
    for line in out.splitlines():
        if line.startswith("PANEL_STAGED"):
            staged = line.split()[1] == "1"
        elif line.startswith("PANEL_USABLE"):
            _, u, t = line.split()
            usable, total = int(u), int(t)
    if staged is None:
        raise RuntimeError(
            "panel-proof: consensus_agent.py --panel-only printed no "
            "PANEL_STAGED marker — it exited before reaching the panel at all, "
            "which is neither of the outcomes this job knows how to report.")
    if not staged:
        raise RuntimeError(
            "panel-proof: the panel is NOT in the container. _stage_panel "
            "could not fetch config/tipster_panel.json from the models "
            "bucket — run infra/09c_upload_panel.sh. Until it is there, "
            "consensus_agent exits 6 every morning.")
    return {"last_success_date": _today(), "panel_staged": True,
            "sources_usable": usable, "sources_total": total,
            "degraded": bool(usable is not None and total and usable < total)}


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
    "baseline-night": job_baseline_night,
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
    "llm-proof": job_llm_proof,
    "consensus-proof": job_consensus_proof,
    "panel-proof": job_panel_proof,
    "nightly-etl": job_nightly_etl,
    "weekly-digest": job_weekly_digest,
}


def dispatch(event=None, context=None):
    job = os.environ.get("STRIDE_JOB", "").strip()
    if job not in JOBS:
        raise RuntimeError(f"unknown STRIDE_JOB {job!r}; known: {sorted(JOBS)}")
    preview = bool(os.environ.get("STRIDE_DATE", "").strip())
    # A run for a chosen date keeps its own run-state row. missing-run-watch
    # falls back to `last_success_date == today` as proof a job ran, so a
    # Friday preview of Saturday's card stamped on the real row would vouch
    # for a Saturday task that never started. The preview row is still
    # written, under its own name, so the digest and a reader can see it.
    state_job = f"{job}~preview" if preview else job
    if preview:
        # Said once, up front, so a log that shows Saturday's card being built
        # on a Friday afternoon explains itself.
        print(f"[dispatch] STRIDE_DATE override: running {job} for {_today()} "
              f"(Sydney today is {datetime.now(SYD).strftime('%Y-%m-%d')}); "
              f"run-state row {state_job}")
    llm_override = os.environ.get("LLM_PROVIDER", "").strip()
    if llm_override:
        # _load_secrets uses setdefault, so a value already on the task wins
        # over the stride/prod secret. Said up front so a log whose insights
        # came from a different provider than the schedule's explains itself.
        print(f"[dispatch] LLM_PROVIDER={llm_override} set on the task; the "
              f"stride/prod value is not consulted for this run")
    _load_secrets()
    _stage_models()
    _stage_panel()
    try:
        result = JOBS[job]()
        _put_state(state_job, **result)
        return {"job": job, "ok": True, **{k: str(v) for k, v in result.items()}}
    except Exception as e:
        _put_state(state_job, last_error=f"{type(e).__name__}: {e}"[:400],
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

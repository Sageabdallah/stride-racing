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


def _run(script: str, *args: str, cwd: str = None) -> subprocess.CompletedProcess:
    root = os.environ.get("LAMBDA_TASK_ROOT", "/var/task")
    cmd = [sys.executable, f"{root}/server/python/{script}", *args]
    print("+", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=840,
                          cwd=cwd or f"{root}/server/python")


def _run_ok(script: str, *args: str, ok_codes=(0,)) -> str:
    proc = _run(script, *args)
    print(proc.stdout[-4000:])
    if proc.returncode not in ok_codes:
        print(proc.stderr[-4000:], file=sys.stderr)
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
    return {"last_success_date": _today(), "gaps_healed": len(healed),
            "detail": out[-500:]}


def job_morning_odds() -> dict:
    _sync_down("server/python/intelligence")
    _prepare_racecard()   # odds_movement reads the card for its fallback odds
    _run_ok("odds_movement.py", _today(), "--snapshot", "morning")
    _sync_up("server/python/intelligence")
    return {"last_success_date": _today()}


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
    yesterday = (datetime.now(SYD).date() - timedelta(days=1)).strftime("%Y-%m-%d")
    healed = []
    for day in _missed_days("results-collect", PF_WALL_DAYS):
        try:
            _run_ok("auto_results_collector.py", "--date", day)
            _run_ok("fetch_and_import_date.py", "--date", day)
            healed.append(day)
        except RuntimeError as e:
            print(f"backfill {day} failed: {e}", file=sys.stderr)
    _run_ok("auto_results_collector.py", "--date", yesterday)
    _run_ok("fetch_and_import_date.py", "--date", yesterday)
    _run_ok("stride_results_collector.py", yesterday)
    return {"last_success_date": yesterday, "gaps_healed": len(healed)}


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


def job_intelligence_build() -> dict:
    _prepare_racecard()
    _run_ok("stride_build.py", _today(), "--parallel")
    _sync_up("server/python/intelligence")
    return {"last_success_date": _today()}


def job_consensus_agent() -> dict:
    _sync_down("server/python/intelligence")
    _prepare_racecard()
    _run_ok("consensus_agent.py", _today())
    _sync_up("server/python/intelligence")
    return {"last_success_date": _today()}


def job_tips_pipeline() -> dict:
    _sync_down("server/python/intelligence")
    _sync_down("server/python/racecards")
    # The pipeline copy path (CLAUDE.md "recurring bug"): the card must
    # exist in BOTH locations before tips run.
    import shutil
    src = f"{_root()}/server/python/racecards/racecard_{_today()}.json"
    dst_dir = f"{_root()}/racecards"
    os.makedirs(dst_dir, exist_ok=True)
    if os.path.exists(src):
        shutil.copy(src, f"{dst_dir}/racecard_{_today()}.json")
    out = _run_ok("run_tips_pipeline.py", _today())
    _run_ok("backfill_tips_contract.py", _today())
    _sync_up("racecards", "tips_*.json")
    return {"last_success_date": _today(), "detail": out[-300:]}


def _prepare_racecard() -> bool:
    """Relay the day's racecard down and place it in BOTH locations.

    Not just a tips concern: the intelligence agents, the consensus agent
    and odds_movement all resolve the card at the REPO-ROOT racecards/
    path, so every one of them needs this. intelligence-build ran without
    it and both agents died with "Racecard not found" — CLAUDE.md's
    recurring copy-path bug, reappearing in the cloud because each Fargate
    task starts with an empty filesystem.
    """
    import shutil
    _sync_down("server/python/racecards")
    src = f"{_root()}/server/python/racecards/racecard_{_today()}.json"
    dst_dir = f"{_root()}/racecards"
    os.makedirs(dst_dir, exist_ok=True)
    if not os.path.exists(src):
        print(f"[racecard] {src} absent after relay — the 05:30 collect "
              f"either has not run or wrote nothing", file=sys.stderr)
        return False
    shutil.copy(src, f"{dst_dir}/racecard_{_today()}.json")
    print(f"[racecard] staged into both locations for {_today()}")
    return True


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
        lines.append(f"{r.get('job_name')}: last_success="
                     f"{r.get('last_success_date')} rows={r.get('rows_written', '-')} "
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

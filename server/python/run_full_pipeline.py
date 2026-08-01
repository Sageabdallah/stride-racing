#!/usr/bin/env python3
"""
Full pipeline orchestrator: download -> tips -> backfill -> validate.

Usage:
    py run_full_pipeline.py 2026-03-28
    py run_full_pipeline.py 2026-03-28 --tracks randwick flemington
    py run_full_pipeline.py 2026-03-28 --skip-download
    py run_full_pipeline.py 2026-03-28 --skip-backfill
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def run_step(name, cmd):
    print(f"\n{'='*60}")
    print(f"  STEP: {name}")
    print(f"  CMD:  {' '.join(cmd)}")
    print(f"{'='*60}\n")
    t0 = time.time()
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    elapsed = time.time() - t0
    status = "OK" if result.returncode == 0 else "FAILED"
    print(f"\n  [{name}] {status} ({elapsed:.1f}s)")
    return result.returncode


def run_step_nonfatal(name, cmd):
    """A step that must never abort or fail the pipeline (odds-snapshot monitoring)."""
    try:
        rc = run_step(name, cmd)
        if rc != 0:
            print(f"  [{name}] non-zero exit {rc} — ignored (non-fatal)", file=sys.stderr)
    except Exception as e:
        print(f"  [{name}] errored (non-fatal): {e}", file=sys.stderr)


def launch_background(name, cmd, log_path):
    """Fire-and-forget: start a detached process and return immediately.

    Used for the T-5min late-odds capture watcher, which runs through the race day.
    It is launched AFTER tipping and fully detached (start_new_session) so it can
    never delay or fail the tips pipeline — the ROI task 04 guardrail.
    """
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = open(log_path, "ab")
        proc = subprocess.Popen(cmd, cwd=str(SCRIPT_DIR), stdout=logf, stderr=logf,
                                start_new_session=True)
        print(f"  [{name}] launched fire-and-forget (pid {proc.pid}) -> {log_path}")
    except Exception as e:
        print(f"  [{name}] launch failed (non-fatal): {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Run the full race analytics pipeline.")
    parser.add_argument("date", help="Race date YYYY-MM-DD")
    parser.add_argument("--tracks", nargs="*", help="Optional track filter")
    parser.add_argument("--skip-download", action="store_true", help="Skip racecard download step")
    parser.add_argument("--skip-backfill", action="store_true", help="Skip contract backfill step")
    parser.add_argument("--skip-db-store", action="store_true", help="Do not write selections to database")
    parser.add_argument("--skip-odds-capture", action="store_true",
                        help="Do not launch the T-5min late-odds watcher / coverage monitor")
    args = parser.parse_args()

    py = sys.executable
    exit_codes = []

    if not args.skip_download:
        cmd = [py, "download_racecards.py", "--date", args.date]
        rc = run_step("DOWNLOAD", cmd)
        exit_codes.append(("download", rc))
        if rc != 0:
            print(f"\n  [ABORT] Download failed (exit {rc}). Fix and retry.", file=sys.stderr)
            sys.exit(rc)

    cmd = [py, "run_tips_pipeline.py", args.date]
    if args.tracks:
        cmd.extend(args.tracks)
    if args.skip_db_store:
        cmd.append("--skip-db-store")
    rc = run_step("TIPS", cmd)
    exit_codes.append(("tips", rc))
    if rc != 0:
        print(f"\n  [ABORT] Tips pipeline failed (exit {rc}).", file=sys.stderr)
        sys.exit(rc)

    # ROI task 04: start the day's T-5min late-odds capture. Fire-and-forget and
    # launched only after tipping, so it can never delay or fail the tips pipeline.
    if not args.skip_odds_capture and not args.skip_db_store:
        launch_background(
            "LATE_ODDS_WATCH",
            [py, "capture_late_odds.py", "--watch", "--date", args.date],
            SCRIPT_DIR / "logs" / f"late_odds_{args.date}.log",
        )

    if not args.skip_backfill:
        cmd = [py, "backfill_tips_contract.py", args.date]
        rc = run_step("BACKFILL", cmd)
        exit_codes.append(("backfill", rc))

    cmd = [py, "validate_tips.py", args.date]
    rc = run_step("VALIDATE", cmd)
    exit_codes.append(("validate", rc))

    # ROI task 04: daily odds-snapshot coverage report (tip_time / late_t5 %).
    # Non-fatal — a monitoring miss must never fail the pipeline.
    if not args.skip_odds_capture:
        run_step_nonfatal("ODDS_COVERAGE",
                          [py, "odds_snapshot_coverage.py", "--date", args.date])

    print(f"\n{'='*60}")
    print(f"  PIPELINE SUMMARY")
    print(f"{'='*60}")
    for name, rc in exit_codes:
        status = "OK" if rc == 0 else f"FAILED (exit {rc})"
        print(f"  {name:>12}: {status}")
    print(f"{'='*60}\n")

    any_failed = any(rc != 0 for _, rc in exit_codes)
    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()

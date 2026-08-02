#!/usr/bin/env python3
"""WP-8: the five retrain gates, live, with days remaining on each.

Reads operational state only (row counts, schema verification, shadow
artifact counts, preflight verdict). It never reads P&L or accuracy, so it
is safe to run any day without touching the pre-registration discipline.
Registered dates come from docs/project_retrain_gate.md and are hardcoded
here on purpose: the window does not move because a script re-derived it.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

DAY_ZERO = date(2026, 8, 2)
EARLIEST = date(2026, 8, 30)
RECOMMENDED = date(2026, 9, 13)
CAL_ROWS_REQUIRED = 500
SHADOW_DAYS_REQUIRED = 5

HERE = Path(__file__).resolve().parent


def _conn():
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"])


def gate1_snapshot_weeks(cur) -> dict:
    cur.execute(
        "SELECT COUNT(DISTINCT race_date) FROM runner_odds_snapshots "
        "WHERE snapshot_kind = 'tip_time' AND race_date::date >= %s", (DAY_ZERO,))
    days = cur.fetchone()[0]
    today = date.today()
    return {
        "name": "1. tip_time accrual",
        "ok": today >= EARLIEST and days >= 20,
        "detail": (f"{days} capture day(s) since {DAY_ZERO}; window opens "
                   f"{EARLIEST} ({max(0, (EARLIEST - today).days)}d away), "
                   f"recommended {RECOMMENDED} "
                   f"({max(0, (RECOMMENDED - today).days)}d away)"),
    }


def gate2_gseries(cur) -> dict:
    cur.execute("""
        SELECT COUNT(*) FROM (
          SELECT 1 FROM race_results_history
          GROUP BY race_date, race_number,
                   regexp_replace(lower(track), '[^a-z0-9]+', '', 'g')
          HAVING COUNT(DISTINCT track) > 1) x""")
    doubles = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*) FROM race_results_history r
        WHERE r.horse_id LIKE 'pf%%'
          AND regexp_replace(upper(r.horse_name),
              '\\s*\\((NZ|GB|IRE|USA|JPN|FR|GER|SAF|ARG|BRZ|HK|SIN|MAC|UAE)\\)$',
              '') <> upper(r.horse_name)
          AND EXISTS (SELECT 1 FROM race_results_history r2
                      WHERE r2.horse_id NOT LIKE 'pf%%'
                        AND upper(r2.horse_name) = regexp_replace(
                            upper(r.horse_name),
                            '\\s*\\((NZ|GB|IRE|USA|JPN|FR|GER|SAF|ARG|BRZ|HK|SIN|MAC|UAE)\\)$',
                            ''))""")
    forks = cur.fetchone()[0]
    return {
        "name": "2. G2 then G1 applies",
        "ok": doubles == 0 and forks == 0,
        "detail": f"alias-doubled groups={doubles}, suffix forks={forks} "
                  "(applied and verified 2026-08-02)",
    }


def gate3_shadow_flips() -> dict:
    live_days = len(glob.glob(str(HERE / "logs" / "serve_liveness_shadow_*.json")))
    cal_days = len(glob.glob(str(HERE / "logs" / "calibrator_shadow_*.json"))) \
        or len(glob.glob(str(HERE / "intelligence" / "calibrator_shadow_*.json")))
    flipped = (os.environ.get("STRIDE_SERVE_LIVE_FEATURES", "").lower()
               in ("true", "1", "yes"),
               os.environ.get("STRIDE_RENORMALISE_FIELD", "").lower()
               in ("true", "1", "yes"))
    return {
        "name": "3. shadow flips",
        "ok": all(flipped),
        "detail": (f"serve-liveness shadow days {live_days}/"
                   f"{SHADOW_DAYS_REQUIRED}, calibrator shadow days "
                   f"{cal_days}/{SHADOW_DAYS_REQUIRED}; flags flipped: "
                   f"serve={flipped[0]}, renorm={flipped[1]}"),
    }


def gate4_calibrator_coverage(cur) -> dict:
    cur.execute(
        "SELECT COUNT(*) FROM prediction_audit "
        "WHERE final_win_prob IS NOT NULL AND race_date::date >= %s", (DAY_ZERO,))
    rows = cur.fetchone()[0]
    per_day = 87
    remaining = max(0, CAL_ROWS_REQUIRED - rows)
    return {
        "name": "4. calibrator coverage",
        "ok": rows >= CAL_ROWS_REQUIRED,
        "detail": (f"{rows}/{CAL_ROWS_REQUIRED} audit rows since {DAY_ZERO} "
                   f"(~{-(-remaining // per_day)} race day(s) to go)"
                   if remaining else f"{rows}/{CAL_ROWS_REQUIRED} audit rows"),
    }


def gate5_preflight() -> dict:
    proc = subprocess.run(
        [sys.executable, str(HERE / "retrain_preflight.py")],
        capture_output=True, text=True, timeout=300)
    tail = (proc.stdout or proc.stderr).strip().splitlines()
    verdict = next((l for l in reversed(tail) if "VERDICT" in l.upper()),
                   f"exit={proc.returncode}")
    return {
        "name": "5. retrain_preflight",
        "ok": proc.returncode == 0 and "RED" not in verdict.upper(),
        "detail": verdict.strip()[:160],
    }


def main() -> int:
    conn = _conn()
    cur = conn.cursor()
    gates = [gate1_snapshot_weeks(cur), gate2_gseries(cur), gate3_shadow_flips(),
             gate4_calibrator_coverage(cur)]
    conn.close()
    try:
        gates.append(gate5_preflight())
    except Exception as e:
        gates.append({"name": "5. retrain_preflight", "ok": False,
                      "detail": f"could not run: {e}"})

    print(f"RETRAIN GATE STATUS  {datetime.now().isoformat(timespec='seconds')}")
    print(f"registered: day zero {DAY_ZERO}, earliest {EARLIEST}, "
          f"recommended {RECOMMENDED}")
    print("-" * 72)
    all_ok = True
    for g in gates:
        mark = "PASS" if g["ok"] else "WAIT"
        all_ok = all_ok and g["ok"]
        print(f"[{mark}] {g['name']}: {g['detail']}")
    print("-" * 72)
    print("ALL GATES PASS: retrain may be scheduled (never auto-started)"
          if all_ok else "NOT READY: no training job may start")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

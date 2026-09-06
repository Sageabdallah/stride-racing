#!/usr/bin/env python3
"""WP-8: the five retrain gates, live, with days remaining on each.

Reads operational state only (row counts, schema verification, shadow
artifact counts, preflight verdict). It never reads P&L or accuracy, so it
is safe to run any day without touching the pre-registration discipline.
Registered dates come from docs/project_retrain_gate.md and are hardcoded
here on purpose: the window does not move because a script re-derived it.
"""

from __future__ import annotations

import json
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


def _flag_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("true", "1", "yes")


def gate3_shadow_flips() -> dict:
    """Two flips, each made ON EVIDENCE: at least SHADOW_DAYS_REQUIRED clean
    shadow days in the store, a PASS review record from shadow_flip_review
    (the registered criteria, computed), and the flag actually on.

    Until 2026-09-05 `ok` was `all(flipped)`: the day counts were printed but
    never enforced, so two environment variables set on day one would have
    passed the gate with zero evidence. The review record is what turns
    "flipped" into "flipped on the registered criteria".

    Distinct race days come from the durable evidence store (S3 plus the
    local logs/ cache). An unreadable store is a loud WAIT, never a silent
    zero.
    """
    from evidence_store import EvidenceStoreError, describe, list_evidence_dates
    try:
        live_days = len(list_evidence_dates("serve_liveness_shadow"))
        cal_days = len(list_evidence_dates("calibrator_shadow"))
        from shadow_flip_review import latest_review
        reviews = {k: latest_review(k) for k in ("serve", "renorm")}
    except EvidenceStoreError as e:
        return {"name": "3. shadow flips", "ok": False,
                "detail": f"EVIDENCE STORE UNREADABLE: {e}"}
    verdicts = {k: (r or {}).get("auto_verdict", "NONE") for k, r in reviews.items()}
    flipped = {"serve": _flag_on("STRIDE_SERVE_LIVE_FEATURES"),
               "renorm": _flag_on("STRIDE_RENORMALISE_FIELD")}
    days_ok = live_days >= SHADOW_DAYS_REQUIRED and cal_days >= SHADOW_DAYS_REQUIRED
    reviews_ok = all(v == "PASS" for v in verdicts.values())
    missing = [k for k, v in verdicts.items() if v != "PASS"]
    hint = (" — run `shadow_flip_review.py --emit-evidence` and review"
            if missing else "")
    return {
        "name": "3. shadow flips",
        "ok": days_ok and reviews_ok and all(flipped.values()),
        "detail": (f"serve-liveness shadow days {live_days}/"
                   f"{SHADOW_DAYS_REQUIRED}, calibrator shadow days "
                   f"{cal_days}/{SHADOW_DAYS_REQUIRED}; review verdicts: "
                   f"serve={verdicts['serve']}, renorm={verdicts['renorm']}{hint}; "
                   f"flags on: serve={flipped['serve']}, renorm={flipped['renorm']}; "
                   f"store: {describe()}"),
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
    """The retrain INPUTS pass preflight: `retrain_preflight.py --inputs-only`.

    Until 2026-09-05 this ran retrain_preflight.py with no arguments. Its
    --staging flag was required, so argparse exited 2 every day; the parser
    then looked for a "VERDICT" line the script never prints and fell back
    to "exit=2". The gate could not pass. Candidate preflight (--staging)
    runs on the artifact after it exists — a gate on whether training may
    START cannot depend on the artifact training would produce.

    ok = every row GREEN (no RED, no PEND): the inputs-only board has no
    expected PENDs once the parity suites and the pre-registration are in
    place, so a PEND here is a real blocker, not a formality.
    """
    proc = subprocess.run(
        [sys.executable, str(HERE / "retrain_preflight.py"), "--inputs-only", "--json"],
        capture_output=True, text=True, timeout=300)
    try:
        boards = json.loads(proc.stdout)
        rows = boards["board1"] + boards["board2"]
    except (ValueError, KeyError, TypeError):
        tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:]
        return {"name": "5. retrain inputs preflight", "ok": False,
                "detail": f"unreadable preflight output (exit={proc.returncode}): "
                          f"{' '.join(tail)[:140]}"}
    not_green = [f"{r['name']}={r['status']}" for r in rows if r["status"] != "GREEN"]
    return {
        "name": "5. retrain inputs preflight",
        "ok": proc.returncode == 0 and not not_green,
        "detail": (f"{len(rows)} gate(s) all GREEN" if not not_green
                   else "; ".join(not_green)[:220]),
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
        gates.append({"name": "5. retrain inputs preflight", "ok": False,
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

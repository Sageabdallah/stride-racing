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
    """Two flips, each made ON EVIDENCE: a PASS review record from
    shadow_flip_review (the registered criteria, computed) that is BOUND to
    the evidence in the store, and the flag actually on.

    Until 2026-09-05 `ok` was `all(flipped)`: the day counts were printed and
    never enforced, so two environment variables set on day one would have
    passed the gate with zero evidence. Until 2026-09-06 the repair trusted
    whichever review record sorted last: its verdict alone, over a filename
    count that included empty and unreadable files, with no check of which
    flag it was about, how many clean days it was computed on, or whether
    evidence had arrived since. A PASS emitted once stayed authoritative
    forever. Bound means all of:

      - the record is about this flag (its `flag` field);
      - the reviewer computed it on at least SHADOW_DAYS_REQUIRED CLEAN days
        (`n_clean_days` — the reviewer's count, which excludes empty,
        unreadable and runner-less files; the store's filename count is
        printed for information only);
      - it is no older than the newest evidence file for its stream. A day
        written after the review can be the dirty day that restarts the
        count (shadow-flip-criteria.md #2), so later evidence invalidates an
        earlier PASS until the review is re-run.

    Distinct race days come from the durable evidence store (S3 plus the
    local logs/ cache). An unreadable store is a loud WAIT, never a silent
    zero.
    """
    from evidence_store import EvidenceStoreError, describe, list_evidence_dates
    try:
        stream_dates = {"serve": list_evidence_dates("serve_liveness_shadow"),
                        "renorm": list_evidence_dates("calibrator_shadow")}
        from shadow_flip_review import FLAG_NAMES, latest_review
        reviews = {k: latest_review(k) for k in ("serve", "renorm")}
    except EvidenceStoreError as e:
        return {"name": "3. shadow flips", "ok": False,
                "detail": f"EVIDENCE STORE UNREADABLE: {e}"}
    flipped = {"serve": _flag_on("STRIDE_SERVE_LIVE_FEATURES"),
               "renorm": _flag_on("STRIDE_RENORMALISE_FIELD")}
    bound, notes = {}, {}
    for k in ("serve", "renorm"):
        r = reviews[k] if isinstance(reviews[k], dict) else None
        newest = stream_dates[k][-1] if stream_dates[k] else None
        if r is None:
            bound[k], notes[k] = False, "NONE"
            continue
        verdict = str(r.get("auto_verdict", "NONE"))
        try:
            clean = int(r.get("n_clean_days") or 0)
        except (TypeError, ValueError):
            clean = 0   # a record that cannot say how many clean days it saw counts none
        when = str(r.get("record_date") or "?")
        problems = []
        if verdict != "PASS":
            problems.append(verdict)
        if r.get("flag") != FLAG_NAMES[k]:
            problems.append(f"record is about {r.get('flag')!r}, not {FLAG_NAMES[k]}")
        if clean < SHADOW_DAYS_REQUIRED:
            problems.append(f"computed on {clean} clean day(s) < {SHADOW_DAYS_REQUIRED}")
        if newest and when < newest:
            problems.append(f"STALE: record {when} predates evidence {newest}; re-run the review")
        bound[k] = not problems
        notes[k] = (f"PASS@{when} on {clean} clean days" if not problems
                    else f"{verdict}@{when} — " + "; ".join(problems))
    missing = [k for k in ("serve", "renorm") if not bound[k]]
    hint = (" — run `shadow_flip_review.py --emit-evidence` and review"
            if missing else "")
    return {
        "name": "3. shadow flips",
        "ok": all(bound.values()) and all(flipped.values()),
        "detail": (f"review records: serve={notes['serve']}, renorm={notes['renorm']}{hint}; "
                   f"store files: serve-liveness {len(stream_dates['serve'])}, "
                   f"calibrator {len(stream_dates['renorm'])}; "
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
        ok, detail = preflight_board_verdict(boards)
    except (ValueError, KeyError, TypeError):
        tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:]
        return {"name": "5. retrain inputs preflight", "ok": False,
                "detail": f"unreadable preflight output (exit={proc.returncode}): "
                          f"{' '.join(tail)[:140]}"}
    return {
        "name": "5. retrain inputs preflight",
        "ok": proc.returncode == 0 and ok,
        "detail": detail,
    }


def preflight_board_verdict(boards) -> tuple:
    """(ok, detail) for an inputs-only preflight board: ok only when every
    row is GREEN — AMBER included, which retrain_preflight's own exit code
    (1 on RED only) does not cover.

    One rule, two readers: gate 5 above and the retrain-model workflow's
    v3-candidate refusal (`gate_status.py --preflight-board FILE`) read the
    same JSON the same way, so "fully green" cannot mean two things. An
    empty board is not green: a preflight that evaluated nothing has proved
    nothing. A board without the two lists raises, and the caller names it
    unreadable.
    """
    rows = boards["board1"] + boards["board2"]
    if not rows:
        return False, "empty preflight board (no gate evaluated)"
    not_green = [f"{r['name']}={r['status']}" for r in rows if r.get("status") != "GREEN"]
    if not_green:
        return False, "; ".join(not_green)[:220]
    return True, f"{len(rows)} gate(s) all GREEN"


def _preflight_board_cli(path: str) -> int:
    """`gate_status.py --preflight-board FILE`: exit 0 only when FILE holds a
    fully green board. No database, no subprocess — the workflow that
    already ran the preflight hands over its JSON and gets gate 5's verdict
    on it."""
    try:
        with open(path, encoding="utf-8") as fh:
            boards = json.load(fh)
        ok, detail = preflight_board_verdict(boards)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"preflight board {path}: unreadable ({type(e).__name__}: {e})")
        return 1
    print(f"preflight board {path}: {'GREEN' if ok else 'NOT GREEN'} — {detail}")
    return 0 if ok else 1


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--preflight-board":
        return _preflight_board_cli(sys.argv[2])
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

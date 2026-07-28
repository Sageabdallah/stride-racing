#!/usr/bin/env python3
"""Betfair historical-odds coverage audit (12P-7) — READ-ONLY.

Question: can the consensus layer's EXISTING betfair_odds_snapshots rows serve
as a retrospective pre-race odds source for the SP-vs-pre-race ablation (12P-4)
over the backtest window 2026-03-04..2026-04-18?

Context: server/scheduler.ts runs odds_movement.py daily — `--snapshot
baseline_night` at ~00:30 (snapshot_type BASELINE_NIGHT) and `--snapshot
morning` at ~08:00 (snapshot_type MORNING_CHECK, written by
compute_market_signals -> capture_snapshot). A MORNING_CHECK row is an as-of
pre-race price for that race day.

This script runs SELECTs only. It introspects information_schema for the actual
columns FIRST and adapts — if a required column is absent it fails naming the
column rather than assuming a schema.

Usage:
  python server/python/betfair_snapshot_coverage_audit.py            # report
  python server/python/betfair_snapshot_coverage_audit.py --json     # machine-readable

Exit codes: 0 = audit ran (any verdict), 2 = could not run (no DB, table or
required column missing). The VERDICT is data, not an exit code.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from statistics import median

SNAPSHOT_TABLE = "betfair_odds_snapshots"
SCHEDULE_TABLE = "race_schedule"
SELECTIONS_TABLE = "selections"

# Columns the audit cannot work without (per the INSERT in odds_movement.py).
REQUIRED_COLUMNS = ("race_date", "track", "race_number", "snapshot_type", "back_price")
# Optional: runner identity for runner-level coverage, timestamp for timing.
RUNNER_COLUMN_CANDIDATES = ("horse_name_norm", "horse_name")
TIME_COLUMN = "snapshot_time"

# selections horse-name column candidates (schema-first: introspect, pick the
# first that exists). The intersection join normalises it with the SAME rule
# odds_movement.py's normalize_name applies to betfair_odds_snapshots
# (lower, strip non-alphanumerics).
SELECTIONS_NAME_CANDIDATES = ("horse_name_norm", "horse_name", "horse")


def sql_norm_name(column: str) -> str:
    """SQL equivalent of intelligence.common.normalize_name — must stay in
    sync: lower FIRST, then strip everything outside [a-z0-9]. Stripping
    before lowering would delete every uppercase letter ('Winx' -> 'inx')
    and the join would never match horse_name_norm."""
    return (f"regexp_replace(lower(coalesce({column}::text, '')), "
            f"'[^a-z0-9]+', '', 'g')")


def selections_join_sql(runner_col: str, sel_name_col: str):
    """LEFT JOIN clause + intersected-numerator column for counting priced
    snapshot runners that are selections horses. Both name sides go through
    sql_norm_name (idempotent on already-normalised *_norm columns; the
    snapshot fallback column horse_name is raw mixed case).
    betfair_odds_snapshots.race_date is DATE while selections.race_date is
    TEXT — without the cast Postgres has no text = date operator and the
    query errors."""
    join = (f"LEFT JOIN {SELECTIONS_TABLE} sel "
            f"ON sel.race_date = s.race_date::text AND sel.track = s.track "
            f"AND sel.race_number = s.race_number "
            f"AND {sql_norm_name('sel.' + sel_name_col)} = "
            f"{sql_norm_name('s.' + runner_col)}")
    num_col = (f"CASE WHEN sel.{sel_name_col} IS NOT NULL "
               f"THEN s.{runner_col} END")
    return join, num_col

MORNING_TYPE = "MORNING_CHECK"   # ~08:00 capture — the as-of pre-race price
BACKTEST_WINDOW = ("2026-03-04", "2026-04-18")  # the 12P-4 ablation window

# A race's morning price is "usable" if priced snapshots cover at least this
# fraction of its selections runners (falls back to >=1 priced row when the
# race has no selections denominator).
MIN_RUNNER_FRACTION = 0.5

FEASIBLE_AT = 70.0
PARTIAL_AT = 40.0


# ---------------------------------------------------------------------------
# Pure functions (unit-tested; no DB)
# ---------------------------------------------------------------------------

def missing_required_columns(available) -> list:
    """Required columns absent from the introspected set."""
    return [c for c in REQUIRED_COLUMNS if c not in set(available)]


def schema_failure_message(table: str, missing) -> str:
    return (f"SCHEMA MISMATCH: table {table} is missing required column(s): "
            f"{', '.join(missing)} — audit cannot assume the schema; aborting.")


def verdict_for_coverage(pct: float) -> str:
    """The overall verdict line for the windowed summary."""
    if pct >= FEASIBLE_AT:
        return f"RETRO-ABLATION: FEASIBLE ({pct:.1f}% coverage)"
    if pct >= PARTIAL_AT:
        return f"RETRO-ABLATION: PARTIAL ({pct:.1f}% coverage)"
    return f"RETRO-ABLATION: NOT_FEASIBLE ({pct:.1f}% coverage)"


def race_is_usable(priced_runners: int, selection_runners: int,
                   min_fraction: float = MIN_RUNNER_FRACTION) -> bool:
    """A race has a usable morning price when priced snapshot rows cover at
    least min_fraction of its selections runners; with no selections
    denominator, any priced row makes it usable."""
    if selection_runners and selection_runners > 0:
        return priced_runners >= math.ceil(selection_runners * min_fraction)
    return priced_runners >= 1


def compute_window_coverage(scheduled_races, priced_counts, selection_counts,
                            matched_counts=None,
                            min_fraction: float = MIN_RUNNER_FRACTION) -> dict:
    """Windowed coverage math on plain fixture-shaped data.

    scheduled_races:  set of race keys (date, track, race_number)
    priced_counts:    {race_key: runners with a morning row AND back_price > 1.0}
                      (ALL priced snapshot runners in the race)
    selection_counts: {race_key: selections runners for that race} (0/absent =
                      no denominator)
    matched_counts:   {race_key: priced runners whose horse identity matches a
                      selections row for the race}. When provided for a race
                      WITH a selections denominator, it is the numerator —
                      priced runners that aren't in selections don't count.
                      Races without a denominator keep the >=1-priced-row
                      fallback on priced_counts.
    Returns every count — nothing is truncated.
    """
    scheduled = set(scheduled_races)
    usable = []
    for r in sorted(scheduled):
        sel_n = selection_counts.get(r, 0)
        if matched_counts is not None and sel_n > 0:
            numerator = matched_counts.get(r, 0)
        else:
            numerator = priced_counts.get(r, 0)
        if race_is_usable(numerator, sel_n, min_fraction):
            usable.append(r)
    total = len(scheduled)
    pct = (len(usable) / total * 100.0) if total else 0.0
    return {
        "scheduled_races": total,
        "usable_races": len(usable),
        "coverage_pct": round(pct, 1),
        "usable_race_keys": [list(map(str, r)) for r in usable],
        "verdict": verdict_for_coverage(pct),
    }


def runner_coverage_pct(snapshot_runners: int, selection_runners: int):
    """Runner-level coverage % within a race/day; None when no denominator.
    When the numerator is intersected with selections it is structurally
    <= the denominator; the clamp pins that contract for any caller."""
    if not selection_runners:
        return None
    return min(100.0, round(snapshot_runners / selection_runners * 100.0, 1))


# ---------------------------------------------------------------------------
# DB access (SELECTs only)
# ---------------------------------------------------------------------------

def _connect():
    """Same pattern as deploy_preflight.py: DATABASE_URL from env or ../.env."""
    import psycopg2

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        env = Path(__file__).resolve().parents[2] / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
                    break
    if not url:
        raise RuntimeError("DATABASE_URL not set (env or project .env)")
    conn = psycopg2.connect(url)
    conn.autocommit = True  # read-only audit; never leave a tx open
    return conn


def _q(conn, sql, params=None):
    cur = conn.cursor()
    try:
        cur.execute(sql, params or ())
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        cur.close()


def introspect_columns(conn, table: str) -> set:
    rows = _q(conn, """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = %s AND table_schema = 'public'
    """, (table,))
    return {r["column_name"] for r in rows}


def collect_report(conn) -> dict:
    report = {"table": SNAPSHOT_TABLE, "window": list(BACKTEST_WINDOW)}

    # 1. Introspect FIRST; fail naming the missing column.
    cols = introspect_columns(conn, SNAPSHOT_TABLE)
    report["columns"] = sorted(cols)
    if not cols:
        raise RuntimeError(f"table {SNAPSHOT_TABLE} not found in public schema")
    missing = missing_required_columns(cols)
    if missing:
        raise RuntimeError(schema_failure_message(SNAPSHOT_TABLE, missing))
    runner_col = next((c for c in RUNNER_COLUMN_CANDIDATES if c in cols), None)
    has_time = TIME_COLUMN in cols
    report["runner_column"] = runner_col
    report["has_snapshot_time"] = has_time

    schedule_cols = introspect_columns(conn, SCHEDULE_TABLE)
    has_schedule = {"race_date", "track", "race_number"} <= schedule_cols
    selections_cols = introspect_columns(conn, SELECTIONS_TABLE)
    has_selections = {"race_date", "track", "race_number"} <= selections_cols
    sel_name_col = next((c for c in SELECTIONS_NAME_CANDIDATES
                         if c in selections_cols), None)
    report["schedule_table_usable"] = has_schedule
    report["selections_table_usable"] = has_selections
    report["selections_name_column"] = sel_name_col
    if has_selections and not sel_name_col:
        report["intersection_note"] = (
            f"selections has no usable horse-name column "
            f"{SELECTIONS_NAME_CANDIDATES} — numerator NOT intersected with "
            f"selections; counting all priced snapshot runners")

    # 2a. Date range per snapshot_type.
    report["per_snapshot_type"] = _q(conn, f"""
        SELECT snapshot_type,
               COUNT(*) AS rows,
               COUNT(DISTINCT race_date) AS days,
               COUNT(DISTINCT (race_date, track, race_number)) AS races,
               MIN(race_date) AS first_date,
               MAX(race_date) AS last_date
        FROM {SNAPSHOT_TABLE}
        GROUP BY snapshot_type
        ORDER BY snapshot_type
    """)

    # 2b. Races-with-snapshots per day vs races scheduled/selected that day.
    snap_per_day = {str(r["race_date"]): r for r in _q(conn, f"""
        SELECT race_date,
               COUNT(DISTINCT (track, race_number)) AS races_with_snapshots,
               COUNT(DISTINCT (track, race_number))
                   FILTER (WHERE snapshot_type = %s) AS races_with_morning,
               COUNT(*) FILTER (WHERE snapshot_type = %s
                                AND back_price > 1.0) AS morning_priced_rows
        FROM {SNAPSHOT_TABLE}
        GROUP BY race_date ORDER BY race_date
    """, (MORNING_TYPE, MORNING_TYPE))}

    sched_per_day = {}
    if has_schedule:
        sched_per_day = {str(r["race_date"]): int(r["races"]) for r in _q(conn, f"""
            SELECT race_date, COUNT(DISTINCT (track, race_number)) AS races
            FROM {SCHEDULE_TABLE} GROUP BY race_date
        """)}
    sel_per_day = {}
    sel_per_race = {}
    if has_selections:
        for r in _q(conn, f"""
            SELECT race_date, track, race_number, COUNT(*) AS runners
            FROM {SELECTIONS_TABLE} GROUP BY race_date, track, race_number
        """):
            key = (str(r["race_date"]), r["track"], int(r["race_number"]))
            sel_per_race[key] = int(r["runners"])
        for (d, _t, _n), n in sel_per_race.items():
            sel_per_day[d] = sel_per_day.get(d, 0) + 1

    days = []
    all_days = sorted(set(snap_per_day) | set(sched_per_day) | set(sel_per_day))
    for d in all_days:
        s = snap_per_day.get(d, {})
        days.append({
            "date": d,
            "races_scheduled": sched_per_day.get(d, 0),
            "races_selected": sel_per_day.get(d, 0),
            "races_with_snapshots": int(s.get("races_with_snapshots", 0)),
            "races_with_morning": int(s.get("races_with_morning", 0)),
            "morning_priced_rows": int(s.get("morning_priced_rows", 0)),
        })
    report["per_day"] = days

    # 2c. Runner-level coverage % within covered races (needs runner identity
    # + a selections denominator). When selections has a usable name column
    # the numerator is INTERSECTED: only priced snapshot runners whose horse
    # identity matches a selections row for that race count.
    if runner_col and has_selections:
        join = ""
        num_col = f"s.{runner_col}"
        if sel_name_col:
            join, num_col = selections_join_sql(runner_col, sel_name_col)
        rows = _q(conn, f"""
            SELECT s.race_date, s.track, s.race_number,
                   COUNT(DISTINCT {num_col}) AS snap_runners
            FROM {SNAPSHOT_TABLE} s
            {join}
            WHERE s.snapshot_type = %s AND s.back_price > 1.0
            GROUP BY s.race_date, s.track, s.race_number
        """, (MORNING_TYPE,))
        pcts, per_day_runners = [], {}
        for r in rows:
            key = (str(r["race_date"]), r["track"], int(r["race_number"]))
            pct = runner_coverage_pct(int(r["snap_runners"]),
                                      sel_per_race.get(key, 0))
            if pct is not None:
                pcts.append(pct)
                per_day_runners.setdefault(key[0], []).append(pct)
        report["runner_coverage_morning"] = {
            "races_measured": len(pcts),
            "median_pct": round(median(pcts), 1) if pcts else None,
            "per_day_median_pct": {d: round(median(v), 1)
                                   for d, v in sorted(per_day_runners.items())},
        }
    else:
        report["runner_coverage_morning"] = None
        report["runner_coverage_note"] = (
            f"skipped — needs a runner column {RUNNER_COLUMN_CANDIDATES} "
            f"and selections(race_date, track, race_number)")

    # 2d. Median capture time vs race off_time, if timestamps allow.
    if has_time and has_schedule and "off_time" in schedule_cols:
        try:
            rows = _q(conn, f"""
                SELECT s.snapshot_type,
                       EXTRACT(EPOCH FROM (s.snapshot_time -
                           (s.race_date + rs.off_time::time))) / 60.0 AS minutes_before_off
                FROM {SNAPSHOT_TABLE} s
                JOIN {SCHEDULE_TABLE} rs
                  ON rs.race_date = s.race_date::text AND rs.track = s.track
                 AND rs.race_number = s.race_number
                WHERE s.snapshot_time IS NOT NULL AND rs.off_time IS NOT NULL
            """)
            timing = {}
            for r in rows:
                timing.setdefault(r["snapshot_type"], []).append(
                    float(r["minutes_before_off"]))
            report["capture_timing"] = {
                t: {"pairs": len(v),
                    "median_minutes_before_off": round(median(v), 1)}
                for t, v in sorted(timing.items())}
        except Exception as e:
            report["capture_timing"] = None
            report["capture_timing_note"] = f"timing unavailable ({e})"
    else:
        report["capture_timing"] = None
        report["capture_timing_note"] = (
            "skipped — needs snapshot_time and race_schedule.off_time")

    # 3. Windowed summary for the backtest window.
    wf, wt = BACKTEST_WINDOW
    join_w = ""
    num_col_w = f"s.{runner_col or 'horse_name'}"
    if sel_name_col and runner_col:
        join_w, num_col_w = selections_join_sql(runner_col, sel_name_col)
    priced_rows = _q(conn, f"""
        SELECT s.race_date, s.track, s.race_number,
               COUNT(DISTINCT s.{runner_col or 'horse_name'}) AS priced,
               COUNT(DISTINCT {num_col_w}) AS priced_matched
        FROM {SNAPSHOT_TABLE} s
        {join_w}
        WHERE s.snapshot_type = %s AND s.back_price > 1.0
          AND s.race_date >= %s AND s.race_date <= %s
        GROUP BY s.race_date, s.track, s.race_number
    """, (MORNING_TYPE, wf, wt)) if runner_col else []
    priced = {(str(r["race_date"]), r["track"], int(r["race_number"])):
              int(r["priced"]) for r in priced_rows}
    priced_matched = ({(str(r["race_date"]), r["track"], int(r["race_number"])):
                       int(r["priced_matched"]) for r in priced_rows}
                      if sel_name_col else None)

    scheduled = set()
    n_sched = n_sel = 0
    if has_schedule:
        sched_races = {(str(r["race_date"]), r["track"], int(r["race_number"]))
                       for r in _q(conn, f"""
            SELECT DISTINCT race_date, track, race_number
            FROM {SCHEDULE_TABLE} WHERE race_date >= %s AND race_date <= %s
        """, (wf, wt))}
        n_sched = len(sched_races)
        scheduled |= sched_races
    if has_selections:
        sel_races = {k for k in sel_per_race if wf <= k[0] <= wt}
        n_sel = len(sel_races)
        scheduled |= sel_races
    # Denominator: union of race_schedule and selections — either table alone
    # can be sparse (race_schedule only logs days the collector saw; selections
    # only days the model ran), and a sparse denominator flatters coverage.
    denominator = f"race_schedule ({n_sched}) UNION selections ({n_sel})"
    if not scheduled:
        scheduled = set(priced)
        denominator = "snapshots_only (no schedule/selections denominator)"
    report["window_denominator"] = denominator
    report["window_summary"] = compute_window_coverage(
        scheduled, priced,
        {k: v for k, v in sel_per_race.items() if wf <= k[0] <= wt},
        matched_counts=priced_matched)
    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def print_report(rep: dict) -> None:
    print("BETFAIR ODDS SNAPSHOT COVERAGE AUDIT (read-only)")
    print(f"table: {rep['table']}  columns: {len(rep['columns'])}  "
          f"runner_col: {rep['runner_column']}  snapshot_time: {rep['has_snapshot_time']}")
    print(f"denominators: race_schedule usable={rep['schedule_table_usable']}  "
          f"selections usable={rep['selections_table_usable']}  "
          f"selections name col: {rep['selections_name_column']}")
    if rep.get("intersection_note"):
        print(f"  NOTE: {rep['intersection_note']}")

    print("\n=== Date range per snapshot_type ===")
    for r in rep["per_snapshot_type"]:
        print(f"  {r['snapshot_type']}: rows={r['rows']} days={r['days']} "
              f"races={r['races']} range={r['first_date']}..{r['last_date']}")

    print("\n=== Per-day coverage (every day with schedule, selections, or snapshots) ===")
    print("  date        scheduled  selected  with_snaps  with_morning  morning_priced_rows")
    for d in rep["per_day"]:
        print(f"  {d['date']}  {d['races_scheduled']:>9}  {d['races_selected']:>8}  "
              f"{d['races_with_snapshots']:>10}  {d['races_with_morning']:>12}  "
              f"{d['morning_priced_rows']:>19}")

    print("\n=== Runner-level coverage within covered races (morning, priced) ===")
    rc = rep.get("runner_coverage_morning")
    if rc:
        num_desc = ("intersected with selections"
                    if rep.get("selections_name_column") else "ALL priced runners")
        print(f"  numerator: {num_desc}")
        print(f"  races measured: {rc['races_measured']}  median: {rc['median_pct']}%")
        for d, pct in rc["per_day_median_pct"].items():
            print(f"  {d}: {pct}%")
    else:
        print(f"  {rep.get('runner_coverage_note', 'unavailable')}")

    print("\n=== Capture timing vs race off_time ===")
    ct = rep.get("capture_timing")
    if ct:
        for t, v in ct.items():
            print(f"  {t}: pairs={v['pairs']}  "
                  f"median {v['median_minutes_before_off']} min vs off "
                  f"(negative = captured pre-race)")
    else:
        print(f"  {rep.get('capture_timing_note', 'unavailable')}")

    wf, wt = rep["window"]
    ws = rep["window_summary"]
    print(f"\n=== Backtest window {wf}..{wt} (denominator: {rep['window_denominator']}) ===")
    print(f"  scheduled races: {ws['scheduled_races']}")
    print(f"  races with usable morning price "
          f"(>= {MIN_RUNNER_FRACTION:.0%} of runners priced, or >=1 if no denominator): "
          f"{ws['usable_races']}")
    print(f"  coverage: {ws['coverage_pct']}%")
    print(f"\n{ws['verdict']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    try:
        conn = _connect()
    except Exception as e:
        print(f"AUDIT: cannot connect — {e}", file=sys.stderr)
        return 2
    try:
        report = collect_report(conn)
    except RuntimeError as e:
        print(f"AUDIT: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

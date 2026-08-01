#!/usr/bin/env python3
"""Backfill barrier trials from Punting Form over a date range.

Was: The Racing API (dead — ceased Australian coverage, credentials return
401). Now: PF meetings flagged isBarrierTrial — and ONLY those; the results
importers exclude them, so trial form comes from exactly one place (the
partition is asserted on every date via pf_results_mapper).

Output contract is UNCHANGED: one ./barrier_trials/trials_<DATE>.json per
date with the same meet/race/runner field names, so
import_barrier_trials_to_db.py loads these files into barrier_trial_results
with its existing 19 target columns, untouched. Runner horse_ids go through
the mapper's bridge (normalised name -> existing id; unknown ->
pf<runnerId>) so trial rows join the same horse identity as
race_results_history. Fetched payloads are archived into pf_raw_payloads
(kind='trials') before parsing, on committing runs — never skipped.

HARD LIMIT — the Starter window slides at ~31 days back (DM-K1, measured
2026-08-01). The wall is probed at runtime; dates before it are listed and
skipped. Exit codes:

    0 = done (or nothing to do)
    1 = failure (Punting Form error, missing DATABASE_URL on a writing run)
    3 = partial due to the wall — the printed dates were NOT fetched

Usage:
    DATABASE_URL=... PUNTINGFORM_API_KEY=... \
        python3 server/python/backfill_barrier_trials.py \
            --start-date YYYY-MM-DD --end-date YYYY-MM-DD [--dry-run]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

_env_path = Path(SCRIPT_DIR).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

import pf_client
import pf_results_mapper as mapper
import pf_window

OUTPUT_DIR = Path("./barrier_trials")
EXIT_WALL_PARTIAL = 3


def output_path_for_date(date_str):
    return OUTPUT_DIR / f"trials_{date_str}.json"


def date_already_collected(date_str):
    return output_path_for_date(date_str).exists()


def _is_jump_out(race_name):
    rn = str(race_name or "").upper()
    return "JUMP OUT" in rn or "JUMPOUT" in rn


def fetch_trials_for_date(date_str, bridge=None, dry_run=False):
    """Fetch all trial races for a single date from PF. Returns the same
    trial-meet dict shape the Racing API version wrote. Strict: any Punting
    Form error propagates (exit 1) — no silent partial days."""
    meetings = pf_client.meetings_for_date(date_str)
    mapper.assert_trial_partition(meetings)
    trials = mapper.aus_trial_meetings(meetings)

    collected_at = datetime.utcnow().isoformat() + "Z"
    trial_meets = []
    raw_payloads = []

    for meet in trials:
        meet_id = meet.get("meetingId")
        course = (meet.get("track") or {}).get("name") or ""
        if not meet_id or not course:
            continue

        results = pf_client.results_for_meeting(meet_id)
        if not results:
            continue
        raw_payloads.append({"kind": "trials", "ref_date": date_str,
                             "meeting_id": meet_id, "payload": results})

        detail_races = {}
        try:
            detail = pf_client.meeting_detail(meet_id) or {}
            for r in detail.get("races") or []:
                rnum = mapper.safe_int(r.get("raceNumber") or r.get("number"))
                if rnum:
                    detail_races[rnum] = r
        except pf_client.PFError as e:
            print(f"    ! {course} ({meet_id}): meeting detail failed ({e})"
                  " — importing without race names/distances", file=sys.stderr)

        trial_races = []
        for block in results:
            for rr in block.get("raceResults") or []:
                race_num = mapper.safe_int(rr.get("raceNumber"))
                if not race_num:
                    continue
                detail = detail_races.get(race_num, {})
                race_name, _race_class = mapper.race_meta(detail)
                runners = []
                for r in rr.get("runners") or []:
                    name = r.get("runner")
                    if not name:
                        continue
                    runners.append({
                        "horse_id": (mapper.resolve_horse_id(bridge, name, r.get("runnerId"))
                                     if bridge is not None else f"pf{r.get('runnerId')}"),
                        "horse": name,
                        "trainer_id": (f"pf{r.get('trainerId')}" if r.get("trainerId") else None),
                        "trainer": r.get("trainer"),
                        "jockey_id": (f"pf{r.get('jockeyId')}" if r.get("jockeyId") else None),
                        "jockey": r.get("jockey"),
                        "position": r.get("position"),
                        "margin": r.get("margin"),
                        "barrier": r.get("barrier"),
                        "weight": r.get("weight"),
                    })
                if not runners:
                    continue
                race = {
                    "race_number": race_num,
                    "race_name": race_name,
                    "distance": detail.get("distance") or detail.get("raceDistance"),
                    "going": rr.get("trackConditionLabel"),
                    "winning_time": rr.get("officialRaceTime"),
                    "course": block.get("track") or course,
                    "date": str(block.get("meetingDate") or date_str)[:10],
                    "is_barrier_trial": True,
                    "is_jump_out": _is_jump_out(race_name),
                    "data_source": "backfill_barrier_trials",
                    "collected_at": collected_at,
                    "runners": runners,
                }
                trial_races.append(race)
                if dry_run:
                    print(f"    [DRY RUN] {race_name or f'Race {race_num}'} — {len(runners)} runners")

        if trial_races:
            trial_meets.append({
                "date": date_str,
                "meet_id": str(meet_id),
                "course": course,
                "is_barrier_trial": True,
                "data_source": "backfill_barrier_trials",
                "collected_at": collected_at,
                "races": trial_races,
            })
            total_runners = sum(len(r["runners"]) for r in trial_races)
            status = "[DRY RUN] " if dry_run else ""
            print(f"  {status}{course}: {len(trial_races)} trial races, {total_runners} runners")

    return trial_meets, raw_payloads


def backfill(start_date, end_date, dry_run=False):
    """Backfill barrier trials over a date range. Returns the exit code."""
    if not dry_run:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total_dates = (end_date - start_date).days + 1
    all_dates = [(start_date + timedelta(days=i)).strftime('%Y-%m-%d')
                 for i in range(total_dates)]

    print(f"\nBarrier Trial Backfill: {all_dates[0]} → {all_dates[-1]} ({total_dates} days)")
    if dry_run:
        print("  *** DRY RUN — nothing will be written ***\n")
    else:
        print()

    # Wall honesty first: probe before fetching anything else (anchored at
    # today so a fully-before-wall range resolves with zero extra fetches).
    today = pf_window.today_aest()
    try:
        wall, _lo, wall_calls = pf_window.find_wall(
            pf_window.probe_date,
            today - timedelta(days=pf_window.WALL_SEARCH_SPAN_DAYS), today)
    except RuntimeError as e:
        print(f"ERROR: cannot probe the PF wall: {e}", file=sys.stderr)
        return 1
    print(f"PF wall (runtime probe, {wall_calls} calls): {wall}")
    wall_skipped = [d for d in all_dates if d < wall.isoformat()]
    for d in wall_skipped:
        print(f"  SKIP {d}: before the PF wall ({wall}) — beyond the Starter "
              f"window; unrecoverable without the archive purchase")
    dates = [d for d in all_dates if d >= wall.isoformat()]
    if not dates:
        print("Nothing reachable in the requested range.")
        return EXIT_WALL_PARTIAL

    db_url = os.environ.get("DATABASE_URL")
    conn = None
    bridge = None
    if not dry_run:
        if not db_url:
            print("ERROR: DATABASE_URL not set (needed for the horse-ID bridge "
                  "and payload archival; or use --dry-run)", file=sys.stderr)
            return 1
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(mapper.RAW_TABLE_DDL)
        conn.commit()
        bridge = mapper.load_bridge(cur)
        print(f"ID bridge loaded: {len(bridge)} known horses")

    dates_processed = 0
    dates_skipped = 0
    total_meets = 0
    total_races = 0

    try:
        for date_str in dates:
            if date_already_collected(date_str):
                dates_skipped += 1
                continue

            print(f"  {date_str}:", end=" ", flush=True)
            trial_meets, raw_payloads = fetch_trials_for_date(
                date_str, bridge=bridge, dry_run=dry_run)

            if trial_meets:
                total_meets += len(trial_meets)
                total_races += sum(len(m['races']) for m in trial_meets)

                if not dry_run:
                    mapper.archive_payloads(conn.cursor(), raw_payloads)
                    path = output_path_for_date(date_str)
                    with open(path, 'w') as f:
                        json.dump(trial_meets, f, indent=2)
                    conn.commit()
            else:
                print("no trials")

            dates_processed += 1
    except pf_client.PFError as e:
        print(f"\nERROR: Punting Form fetch failed: {e}", file=sys.stderr)
        print("Dates above were committed (re-runs skip collected dates); "
              "the failing date wrote nothing.", file=sys.stderr)
        if conn:
            conn.close()
        return 1

    if conn:
        conn.close()

    print(f"\n{'─' * 50}")
    print(f"  Dates processed: {dates_processed}")
    if dates_skipped:
        print(f"  Dates skipped (already collected): {dates_skipped}")
    if wall_skipped:
        print(f"  Dates skipped (before PF wall): {len(wall_skipped)}")
    print(f"  Trial meetings found: {total_meets}")
    print(f"  Trial races found: {total_races}")
    if not dry_run and total_meets:
        print(f"  Output: {OUTPUT_DIR.resolve()}/")
    print()
    return EXIT_WALL_PARTIAL if wall_skipped else 0


def main():
    parser = argparse.ArgumentParser(description="Backfill barrier trial data from Punting Form")
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be collected without writing")
    args = parser.parse_args()

    start = datetime.strptime(args.start_date, '%Y-%m-%d')
    end = datetime.strptime(args.end_date, '%Y-%m-%d')

    if start > end:
        print("Error: --start-date must be before --end-date", file=sys.stderr)
        sys.exit(1)

    sys.exit(backfill(start, end, dry_run=args.dry_run))


if __name__ == "__main__":
    main()

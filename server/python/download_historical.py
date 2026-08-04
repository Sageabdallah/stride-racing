#!/usr/bin/env python3
"""
Bulk historical results import from Punting Form into race_results_history.

Was: download 365 days of race data from The Racing API into a training
JSON (dead — the API ceased Australian coverage; its credentials return
401, and the old JSON consumer import_historical_to_db.py belongs to that
era). Now: bulk version of the daily PF importer over the target-track
list below, mapping through pf_results_mapper (same 21 columns, same
dedup, same horse-ID bridge, payloads archived into pf_raw_payloads before
each day's insert, one transaction per day).

HARD LIMIT — the Starter window slides at ~31 days back (measured by
DM-K1 on 2026-08-01: meetingslist 400s before 2026-07-01; first assumed
~53). Anything before the wall is unrecoverable from this subscription.
The wall is probed at runtime (it slides daily); dates before it are
listed explicitly and skipped, and the exit code says so:

    0 = done (or nothing to do)
    1 = failure (Punting Form error, missing DATABASE_URL)
    3 = partial due to the wall — the printed dates were NOT fetched

Dedup makes re-runs idempotent, so re-running after the wall slides just
re-covers what is reachable. --dry-run fetches and maps but writes nothing.

Usage:
    DATABASE_URL=... PUNTINGFORM_API_KEY=... \
        python3 server/python/download_historical.py [--days 31]
            [--start-offset 1] [--dry-run]
"""

import argparse
import os
import sys
from datetime import timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import pf_client
import pf_results_mapper as mapper
import pf_window
import target_tracks

TARGET_TRACKS = [
    "flemington", "caulfield", "moonee valley", "sandown",
    "randwick", "royal randwick", "rosehill", "warwick farm", "canterbury",
    "eagle farm", "doomben", "ascot", "belmont", "pinjarra",
    "kensington", "randwick kensington", "gold coast", "aquis park gold coast",
    "geelong", "ladbrokes geelong", "ballarat", "ipswich",
    "morphettville", "murray bridge", "pakenham", "cranbourne",
    "newcastle",
]

EXIT_WALL_PARTIAL = 3


def is_target_track(name):
    # The list above stays this module's own — it is the ingestion universe,
    # not the card's, and they differ deliberately. Only the comparison is
    # shared. Measured over 516 distinct course spellings: identical results
    # to the bidirectional test this replaces, 77 matched either way, zero
    # disagreements.
    return target_tracks.matches(name, TARGET_TRACKS)


def fetch_day(iso_date):
    """One date from PF: resulted target-track non-trial meetings.

    Strict per the loud-failure rule: any Punting Form error propagates
    (exit 1) rather than silently importing a partial day."""
    meetings = pf_client.meetings_for_date(iso_date)
    mapper.assert_trial_partition(meetings)
    aus = [m for m in mapper.aus_race_meetings(meetings)
           if is_target_track((m.get("track") or {}).get("name", ""))]
    day = {"date": iso_date, "meetings": [], "raw": []}
    for m in aus:
        mid = m.get("meetingId")
        track_name = (m.get("track") or {}).get("name") or "Unknown"
        results = pf_client.results_for_meeting(mid)
        if not results:
            continue
        day["raw"].append({"kind": "results", "ref_date": iso_date,
                           "meeting_id": mid, "payload": results})
        detail_races = {}
        last10 = {}
        detail = pf_client.meeting_detail(mid)
        for r in (detail or {}).get("races") or []:
            rnum = mapper.safe_int(r.get("raceNumber") or r.get("number"))
            if rnum:
                detail_races[rnum] = r
            for runner in r.get("runners") or []:
                rid = str(runner.get("runnerId") or "")
                if rid:
                    last10[rid] = (runner.get("last10") or "").strip() or None
        day["meetings"].append({"meeting_id": mid, "track": track_name,
                                "results": results, "detail_races": detail_races,
                                "last10": last10})
    return day


def main():
    ap = argparse.ArgumentParser(
        description="Bulk PF results import within the Starter window")
    ap.add_argument("--days", type=int, default=365,
                    help="days back from --start-offset (dates before the PF "
                         "wall are skipped with exit 3)")
    ap.add_argument("--start-offset", type=int, default=1,
                    help="end date is today AEST minus this many days")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and map but write nothing")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not args.dry_run and not db_url:
        print("ERROR: DATABASE_URL not set (or use --dry-run)")
        return 1

    end = pf_window.today_aest() - timedelta(days=args.start_offset)
    dates = [(end - timedelta(days=i)).isoformat() for i in range(args.days)]

    # Wall honesty first: probe before fetching anything else. The search is
    # anchored at today (always served in practice) so a fully-before-wall
    # range still resolves — and costs zero fetches beyond the probe.
    today = pf_window.today_aest()
    try:
        wall, _lo, wall_calls = pf_window.find_wall(
            pf_window.probe_date,
            today - timedelta(days=pf_window.WALL_SEARCH_SPAN_DAYS), today)
    except RuntimeError as e:
        print(f"ERROR: cannot probe the PF wall: {e}")
        return 1
    print(f"PF wall (runtime probe, {wall_calls} calls): {wall}")
    skipped = [d for d in dates if d < wall.isoformat()]
    reachable = [d for d in dates if d >= wall.isoformat()]
    for d in sorted(skipped):
        print(f"  SKIP {d}: before the PF wall ({wall}) — beyond the Starter "
              f"window; unrecoverable without the archive purchase")
    if skipped:
        print(f"  {len(skipped)} date(s) skipped due to the wall; "
              f"{len(reachable)} reachable")
    if not reachable:
        print("Nothing reachable in the requested range.")
        return EXIT_WALL_PARTIAL

    conn = cur = None
    existing = set()
    bridge = {}
    if db_url:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(mapper.RAW_TABLE_DDL)
        conn.commit()
        existing = mapper.load_existing_keys(cur, reachable)
        bridge = mapper.load_bridge(cur)
        print(f"ID bridge loaded: {len(bridge)} known horses; "
              f"{len(existing)} existing race keys in range")
    else:
        print("DRY RUN: no DATABASE_URL use — fetch and map only")

    mode = "DRY RUN" if args.dry_run else "COMMIT"
    print(f"{mode}: {len(reachable)} days, {reachable[-1]} .. {reachable[0]} "
          f"(target tracks only)")

    unknown_keys = set()
    total_rows = total_skipped = 0
    try:
        for iso in reachable:
            day = fetch_day(iso)
            rows, races_seen = mapper.build_rows(day, bridge, unknown_keys)
            fresh = [r for r in rows if mapper.row_race_key(r) not in existing]
            print(f"  {iso}: {len(day['meetings'])} meetings -> {len(fresh)} rows "
                  f"({len(rows) - len(fresh)} already in DB)")
            total_rows += len(fresh)
            total_skipped += len(rows) - len(fresh)
            if not args.dry_run and cur:
                from psycopg2.extras import execute_values
                mapper.archive_payloads(cur, day["raw"])  # archive first
                if fresh:
                    execute_values(
                        cur,
                        f"INSERT INTO race_results_history ({', '.join(mapper.RRH_COLUMNS)}) VALUES %s",
                        fresh, page_size=500)
                conn.commit()
                existing.update(mapper.row_race_key(r) for r in fresh)
    except pf_client.PFError as e:
        print(f"\nERROR: Punting Form fetch failed: {e}")
        print("Partial days above were committed (dedup makes re-runs safe); "
              "the failing day wrote nothing.")
        if conn:
            conn.close()
        return 1

    print(f"\n{mode} COMPLETE: {total_rows} rows "
          f"{'inserted' if not args.dry_run else 'would insert'}, "
          f"{total_skipped} already present")
    if unknown_keys:
        print(f"race-detail keys seen without a mapping (refine race_meta): "
              f"{sorted(unknown_keys)[:30]}")
    if conn:
        conn.close()
    return EXIT_WALL_PARTIAL if skipped else 0


if __name__ == '__main__':
    sys.exit(main())

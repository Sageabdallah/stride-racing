#!/usr/bin/env python3
"""Backfill race results from Punting Form into race_results_history.

Phase B of PUNTINGFORM_MIGRATION.md. Mirrors the retired Racing API importer
(fetch_and_import_date.py): same 21 columns, same append-only contract, same
(race_date, lower(track), race_number) skip-if-existing dedup, trials and
scratched/no-position runners excluded.

Two deliberate additions over the old importer (both now living in
pf_results_mapper.py, shared with the daily importer and the racecard side):

  * horse-ID bridge — features join race_results_history by horse_id in SQL
    (442 usage sites), so PF rows must not fork a horse's identity. Each PF
    runner is matched to the existing horse_id by normalised name (most recent
    prior row wins); only genuinely new horses get a 'pf<runnerId>' id. The
    same rule must be used by the racecard side at serve time.
  * raw archive — every results payload is stored in pf_raw_payloads (jsonb)
    before parsing, so a permanent owned archive accrues while the Starter
    subscription only serves ~31 days back.

The mapping/dedup/bridge/archive logic itself was extracted to
pf_results_mapper.py and is imported back here unchanged — the fixture pin
in test_pf_results_mapper_pin.py proves byte-identical rows.

Usage (CI: pf-backfill workflow; local works too):
    DATABASE_URL=... PUNTINGFORM_API_KEY=... \
        python3 server/python/pf_backfill_results.py --days 60 [--end YYYY-MM-DD]
            [--commit] [--metro-only]

Default is a DRY RUN: fetches, maps, prints per-day/per-meeting row counts and
unmapped-field warnings, writes nothing. --commit performs the inserts.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pf_client
from pf_results_mapper import (
    RRH_COLUMNS, RAW_TABLE_DDL, aus_race_meetings, is_metro_track,
    load_existing_keys, load_bridge, row_race_key, build_rows,
    archive_payloads, norm_name, safe_int, safe_float, parse_class_level,
    parse_distance, race_meta, normalize_finish_position,
)

__all__ = [
    "norm_name", "safe_int", "safe_float", "parse_class_level",
    "parse_distance", "race_meta", "normalize_finish_position",
    "aus_race_meetings", "is_metro_track", "build_rows", "row_race_key",
    "load_existing_keys", "load_bridge", "load_horse_id_bridge",
    "archive_payloads", "RRH_COLUMNS", "RAW_TABLE_DDL", "collect_day", "main",
]

# The racecard serve path (providers/puntingform) imported the bridge under
# this name before the mapper extraction; both names must resolve to the SAME
# function or serve and settle drift apart.
load_horse_id_bridge = load_bridge


def collect_day(iso_date, metro_only=False, verbose=True):
    """Fetch one AEST date from PF → (rows-by-race dict, raw payloads list)."""
    meetings = pf_client.meetings_for_date(iso_date)
    aus = aus_race_meetings(meetings)
    if metro_only:
        aus = [m for m in aus
               if is_metro_track((m.get("track") or {}).get("name", ""))]
    day = {"date": iso_date, "meetings": [], "raw": []}
    for m in aus:
        mid = m.get("meetingId")
        track_name = (m.get("track") or {}).get("name") or "Unknown"
        try:
            results = pf_client.results_for_meeting(mid)
        except pf_client.PFError as e:
            print(f"    ! {track_name} ({mid}): results fetch failed: {e}")
            continue
        if not results:
            if verbose:
                print(f"    - {track_name} ({mid}): no results payload (not resulted?)")
            continue
        day["raw"].append({"kind": "results", "ref_date": iso_date, "meeting_id": mid,
                           "payload": results})
        # race-level metadata (name/distance/class + last10 per runner)
        detail_races = {}
        last10 = {}
        try:
            detail = pf_client.meeting_detail(mid)
            for r in (detail or {}).get("races") or []:
                rnum = safe_int(r.get("raceNumber") or r.get("number"))
                if rnum:
                    detail_races[rnum] = r
                for runner in r.get("runners") or []:
                    rid = str(runner.get("runnerId") or "")
                    if rid:
                        last10[rid] = (runner.get("last10") or "").strip() or None
        except pf_client.PFError as e:
            print(f"    ! {track_name} ({mid}): meeting detail failed ({e}) — importing without race meta")
        day["meetings"].append({"meeting_id": mid, "track": track_name,
                                "results": results, "detail_races": detail_races,
                                "last10": last10})
    return day


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--end", help="last date to fetch (ISO); default yesterday AEST")
    ap.add_argument("--commit", action="store_true", help="write to the database (default: dry run)")
    ap.add_argument("--metro-only", action="store_true")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if args.commit and not db_url:
        print("ERROR: --commit requires DATABASE_URL")
        return 1

    now_aest = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=10)
    end = datetime.date.fromisoformat(args.end) if args.end else (now_aest.date() - datetime.timedelta(days=1))
    dates = [(end - datetime.timedelta(days=i)).isoformat() for i in range(args.days)]

    conn = cur = None
    existing = set()
    bridge = {}
    if db_url:
        import psycopg2
        from psycopg2.extras import execute_values  # noqa: F401
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(RAW_TABLE_DDL)
        conn.commit()
        existing = load_existing_keys(cur, dates)
        bridge = load_bridge(cur)
        print(f"ID bridge loaded: {len(bridge)} known horses; "
              f"{len(existing)} existing race keys in window")
    else:
        print("NOTE: no DATABASE_URL — dedup/bridge skipped, pure fetch-and-map dry run")

    mode = "COMMIT" if args.commit else "DRY RUN"
    print(f"{mode}: {len(dates)} days, {dates[-1]} .. {dates[0]}")

    unknown_keys = set()
    total_rows = total_skipped = total_new_ids = 0
    for iso in dates:
        print(f"\n=== {iso} ===")
        try:
            day = collect_day(iso, metro_only=args.metro_only)
        except pf_client.PFError as e:
            print(f"  ! day fetch failed: {e}")
            continue
        rows, races_seen = build_rows(day, bridge, unknown_keys)
        fresh = [r for r in rows if row_race_key(r) not in existing]
        skipped = len(rows) - len(fresh)
        new_ids = sum(1 for r in fresh if str(r[0]).startswith("pf"))
        print(f"  {len(day['meetings'])} resulted meetings -> {len(rows)} runner rows "
              f"({skipped} already in DB, {new_ids} new horse ids)")
        total_rows += len(fresh)
        total_skipped += skipped
        total_new_ids += new_ids
        if args.commit and cur:
            from psycopg2.extras import execute_values
            if fresh:
                execute_values(
                    cur,
                    f"INSERT INTO race_results_history ({', '.join(RRH_COLUMNS)}) VALUES %s",
                    fresh, page_size=500)
            archive_payloads(cur, day["raw"])
            conn.commit()
            existing.update(row_race_key(r) for r in fresh)

    print("\n" + "=" * 60)
    print(f"{mode} COMPLETE: {total_rows} rows {'inserted' if args.commit else 'would insert'}, "
          f"{total_skipped} already present, {total_new_ids} new horse ids")
    if unknown_keys:
        print(f"race-detail keys seen without a mapping (refine race_meta): {sorted(unknown_keys)[:30]}")
    if conn:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

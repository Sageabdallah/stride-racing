#!/usr/bin/env python3
"""
Fetches race results for a specific date from Punting Form and
APPENDS them into race_results_history (no truncation).

Source history: this module fetched from The Racing API until it ceased
Australian coverage (credentials now return 401). The fetch layer is now
Punting Form Starter via pf_client, mapped through pf_results_mapper —
same 21 columns, same append-only contract, same
(race_date, lower(track), race_number) skip-if-existing dedup, trials and
scratched/no-position runners excluded, and the horse-ID bridge so PF
runners join existing horse_ids instead of forking them. Each meeting's
raw results payload is archived into pf_raw_payloads before its row
insert, in the same transaction. There is no racing.com fallback in this
module (there never was); racing.com remains the sectional-times source in
the collectors.

The public pair fetch_date/import_meetings keeps its signature for the
modules that call it (backfill_results.py, learn_from_results_v2.py,
ingest_target_track_results_and_sectionals.py, stride_results_collector.py);
meeting dicts now carry the PF payloads under "pf" plus "course"/"track"
display keys those callers filter on.

Loud failure: any Punting Form error aborts the date with a non-zero exit
and zero writes — a silent empty day is how the old pipeline's death went
unnoticed. A genuinely meeting-less day exits 0, exactly as before.
"""

import os, sys, json, argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.normpath(os.path.join(SCRIPT_DIR, os.pardir, os.pardir, ".env"))
EXPLICIT_ENV = r"c:\Users\sagea\OneDrive\Desktop\Race-Analytics\Race-Analytics\.env"
def _load_env(path):
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env

_env = _load_env(ENV_PATH)
if not _env.get("DATABASE_URL"):
    _env = _load_env(EXPLICIT_ENV)
DATABASE_URL = _env.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
for _key, _value in _env.items():
    os.environ.setdefault(_key, _value)

import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, SCRIPT_DIR)
import pf_client
import pf_results_mapper as mapper
from intelligence_common import KEEPALIVE_KWARGS


def fetch_date(date_str, metro_only=False):
    """Fetch all resulted AUS race meetings for a date from Punting Form.

    Returns a list of meeting dicts:
        {"date", "meet_id", "course", "track", "pf", "raw"}
    where "pf" is the mapper's day-meeting structure and "raw" is the
    pf_raw_payloads archival record. Raises pf_client.PFError on ANY API
    failure (loud failure — callers that preferred the old silent-empty
    behavior must catch it themselves).
    """
    print(f"\n  Fetching PF meetings for {date_str}...")
    meetings = mapper.aus_race_meetings(pf_client.meetings_for_date(date_str))
    if metro_only:
        meetings = [m for m in meetings
                    if mapper.is_metro_track((m.get("track") or {}).get("name", ""))]

    all_meetings = []
    for m in meetings:
        mid = m.get("meetingId")
        track_name = (m.get("track") or {}).get("name") or "Unknown"
        print(f"  {track_name} (meeting {mid})...")
        results = pf_client.results_for_meeting(mid)
        if not results:
            print("    not resulted yet — skipped")
            continue
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
        nraces = sum(1 for b in results for rr in b.get("raceResults") or []
                     if mapper.safe_int(rr.get("raceNumber")))
        print(f"    {nraces} races fetched")
        all_meetings.append({
            "date": date_str,
            "meet_id": str(mid),
            "course": track_name,
            "track": track_name,
            "pf": {"meeting_id": mid, "track": track_name, "results": results,
                   "detail_races": detail_races, "last10": last10},
            "raw": {"kind": "results", "ref_date": date_str,
                    "meeting_id": mid, "payload": results},
        })

    return all_meetings


def import_meetings(meetings, conn):
    """Insert PF-sourced meetings into race_results_history, skipping
    existing races. Each meeting's raw payload is archived BEFORE its row
    insert, in the same transaction. Returns
    (rows_inserted, skipped_races, skipped_runners) — the same tuple the
    Racing API version returned."""
    cur = conn.cursor()
    cur.execute(mapper.RAW_TABLE_DDL)
    conn.commit()

    dates = sorted({m["date"] for m in meetings})
    existing = mapper.load_existing_keys(cur, dates)
    bridge = mapper.load_bridge(cur)
    print(f"\n  Existing date/track/race combos: {len(existing)}; "
          f"ID bridge: {len(bridge)} known horses")

    insert_sql = f"INSERT INTO race_results_history ({', '.join(mapper.RRH_COLUMNS)}) VALUES %s"
    total_rows = 0
    total_skipped_races = 0
    total_skipped_runners = 0
    unknown_keys = set()

    for meeting in meetings:
        pf_meeting = dict(meeting["pf"])
        # honour a caller-renamed display course (select_target_meetings
        # rewrites "course" to its canonical target-track name)
        if meeting.get("course") and meeting["course"] != pf_meeting["track"]:
            pf_meeting["track"] = meeting["course"]
        day = {"date": meeting["date"], "meetings": [pf_meeting]}

        rows, races_seen = mapper.build_rows(day, bridge, unknown_keys)
        fresh = [r for r in rows if mapper.row_race_key(r) not in existing]
        total_skipped_races += sum(1 for k in races_seen if k in existing)
        total_skipped_runners += mapper.count_excluded_runners(pf_meeting["results"])

        mapper.archive_payloads(cur, [meeting["raw"]])  # archive first
        if fresh:
            execute_values(cur, insert_sql, fresh, page_size=500)
        conn.commit()
        existing.update(mapper.row_race_key(r) for r in fresh)
        total_rows += len(fresh)

    if unknown_keys:
        print(f"  race-detail keys seen without a mapping (refine race_meta): "
              f"{sorted(unknown_keys)[:30]}")

    cur.close()
    return total_rows, total_skipped_races, total_skipped_runners


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Date YYYY-MM-DD")
    parser.add_argument("--metro-only", action="store_true", help="Metro tracks only")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not found"); sys.exit(1)

    print("=" * 65)
    print(f"  FETCH & IMPORT RACE RESULTS — {args.date}")
    print("=" * 65)
    print(f"  Source: Punting Form (The Racing API is dead)")
    print(f"  Metro only: {args.metro_only}")

    try:
        meetings = fetch_date(args.date, metro_only=args.metro_only)
    except pf_client.PFError as e:
        print(f"\n  ERROR: Punting Form fetch failed: {e}")
        print("  Nothing written. A silent empty day is worse than a crash —")
        print("  fix the cause and re-run (dedup makes re-runs safe).")
        sys.exit(1)

    if not meetings:
        print("\n  No meetings found for this date.")
        sys.exit(0)

    total_races = sum(
        1 for m in meetings for b in m["pf"]["results"]
        for rr in b.get("raceResults") or []
        if mapper.safe_int(rr.get("raceNumber")))
    total_runners = sum(
        1 for m in meetings for b in m["pf"]["results"]
        for rr in b.get("raceResults") or []
        if mapper.safe_int(rr.get("raceNumber"))
        for r in rr.get("runners") or []
        if mapper.normalize_finish_position(r.get("position")) is not None)

    print(f"\n  Fetched: {len(meetings)} meetings, {total_races} races, {total_runners} runners")

    out_dir = os.path.join(SCRIPT_DIR, os.pardir, os.pardir, "historical_data", "track_imports")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f"{'metro' if args.metro_only else 'all'}_{args.date}.json")
    with open(out_file, "w") as f:
        json.dump(meetings, f, indent=2)
    print(f"  JSON saved: {out_file}")

    print(f"\n  Importing into race_results_history...")
    conn = psycopg2.connect(DATABASE_URL, **KEEPALIVE_KWARGS)
    rows, skipped_races, skipped_runners = import_meetings(meetings, conn)
    conn.close()

    print(f"\n  {'='*65}")
    print(f"  IMPORT COMPLETE")
    print(f"  {'='*65}")
    print(f"  Meetings:  {len(meetings)}")
    print(f"  Races:     {total_races} ({skipped_races} already existed)")
    print(f"  Runners imported:  {rows}")
    print(f"  Runners skipped:   {skipped_runners} (scratched/no position)")
    print()

    for m in meetings:
        nraces = sum(1 for b in m["pf"]["results"] for rr in b.get("raceResults") or []
                     if mapper.safe_int(rr.get("raceNumber")))
        nrunners = sum(1 for b in m["pf"]["results"] for rr in b.get("raceResults") or []
                       for r in rr.get("runners") or []
                       if mapper.normalize_finish_position(r.get("position")) is not None)
        print(f"    {m['course']}: {nraces} races, {nrunners} runners")


if __name__ == "__main__":
    main()

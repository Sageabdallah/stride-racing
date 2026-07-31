#!/usr/bin/env python3
"""Backfill race results from Punting Form into race_results_history.

Phase B of PUNTINGFORM_MIGRATION.md. Mirrors the retired Racing API importer
(fetch_and_import_date.py): same 21 columns, same append-only contract, same
(race_date, lower(track), race_number) skip-if-existing dedup, trials and
scratched/no-position runners excluded.

Two deliberate additions over the old importer:

  * horse-ID bridge — features join race_results_history by horse_id in SQL
    (442 usage sites), so PF rows must not fork a horse's identity. Each PF
    runner is matched to the existing horse_id by normalised name (most recent
    prior row wins); only genuinely new horses get a 'pf<runnerId>' id. The
    same rule must be used by the racecard side at serve time.
  * raw archive — every results payload is stored in pf_raw_payloads (jsonb)
    before parsing, so a permanent owned archive accrues while the Starter
    subscription only serves ~60 days back.

Usage (CI: pf-backfill workflow; local works too):
    DATABASE_URL=... PUNTINGFORM_API_KEY=... \
        python3 server/python/pf_backfill_results.py --days 60 [--end YYYY-MM-DD]
            [--commit] [--metro-only]

Default is a DRY RUN: fetches, maps, prints per-day/per-meeting row counts and
unmapped-field warnings, writes nothing. --commit performs the inserts.
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pf_client

METRO_TRACKS = [
    "flemington", "caulfield", "moonee valley", "sandown",
    "randwick", "royal randwick", "rosehill", "warwick farm", "canterbury",
    "eagle farm", "doomben", "gold coast", "morphettville",
    "newcastle", "kembla grange", "ascot", "belmont",
]

RRH_COLUMNS = (
    "horse_id", "horse_name", "race_id", "track", "race_date",
    "distance_m", "race_class", "class_level", "going", "position",
    "margin_lengths", "weight_kg", "jockey", "jockey_id", "barrier",
    "sp_odds", "field_size", "race_name", "race_number", "form_string",
    "opponents_json"
)

RAW_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS pf_raw_payloads (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    ref_date DATE,
    meeting_id BIGINT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL
)
"""


def norm_name(name):
    """Horse-name key for the ID bridge: lowercase, no country suffix like
    '(NZ)', letters+digits only."""
    s = re.sub(r"\([A-Z]{2,3}\)\s*$", "", str(name or "").strip())
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def parse_class_level(rc):
    if not rc:
        return 1
    rc = str(rc).upper()
    if any(x in rc for x in ("GROUP 1", "GROUP1", " G1")):
        return 10
    if any(x in rc for x in ("GROUP 2", "GROUP2", " G2")):
        return 9
    if any(x in rc for x in ("GROUP 3", "GROUP3", " G3")):
        return 8
    if "LISTED" in rc:
        return 7
    bm = re.search(r"BM\s*(\d+)", rc)
    if bm:
        n = int(bm.group(1))
        if n >= 88:
            return 6
        if n >= 70:
            return 5
        if n >= 58:
            return 4
        if n >= 50:
            return 3
        return 2
    if "MAIDEN" in rc:
        return 2
    return 1


def safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_distance(race_obj):
    for k in ("distance", "raceDistance", "distanceM", "length"):
        v = race_obj.get(k)
        if v:
            m = re.search(r"(\d+)", str(v))
            if m:
                return int(m.group(1))
    return None


def race_meta(race_obj):
    """(race_name, race_class) from a meeting-detail race object, tolerant of
    naming differences; unknown keys are reported once in dry-run output."""
    name = race_obj.get("name") or race_obj.get("raceName")
    rclass = (race_obj.get("raceClass") or race_obj.get("class")
              or race_obj.get("restrictions") or "")
    return name, str(rclass or "")


def normalize_finish_position(v, field_size=None):
    pos = safe_int(v)
    if pos is None or pos <= 0 or pos >= 100:
        return None
    if field_size and pos > field_size:
        return None
    return pos


def collect_day(iso_date, metro_only=False, verbose=True):
    """Fetch one AEST date from PF → (rows-by-race dict, raw payloads list)."""
    meetings = pf_client.meetings_for_date(iso_date)
    aus = [m for m in meetings
           if (m.get("track") or {}).get("country") == "AUS"
           and not m.get("isBarrierTrial")]
    if metro_only:
        aus = [m for m in aus
               if any(t in (m.get("track") or {}).get("name", "").lower()
                      or (m.get("track") or {}).get("name", "").lower() in t
                      for t in METRO_TRACKS)]
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


def build_rows(day, bridge, unknown_keys):
    """Map one collected day to RRH rows. bridge: norm_name -> horse_id."""
    rows, races_seen = [], []
    for m in day["meetings"]:
        for block in m["results"]:
            race_date = str(block.get("meetingDate") or day["date"])[:10]
            track = block.get("track") or m["track"]
            for rr in block.get("raceResults") or []:
                rnum = safe_int(rr.get("raceNumber"))
                if not rnum:
                    continue
                detail = m["detail_races"].get(rnum, {})
                race_name, race_class = race_meta(detail)
                if not race_name and detail:
                    unknown_keys.update(k for k in detail.keys() if k != "runners")
                distance_m = parse_distance(detail) or parse_distance(rr)
                going = rr.get("trackConditionLabel")
                runners = [r for r in (rr.get("runners") or [])
                           if normalize_finish_position(r.get("position")) is not None]
                field_size = len(runners)
                opponents = [{"horse_id": None, "horse_name": r.get("runner"),
                              "position": normalize_finish_position(r.get("position"), field_size),
                              "margin": safe_float(r.get("margin"))} for r in runners]
                races_seen.append((race_date, str(track).lower(), rnum))
                for r in runners:
                    nk = norm_name(r.get("runner"))
                    horse_id = bridge.get(nk) or f"pf{r.get('runnerId')}"
                    my_opponents = [o for o in opponents if norm_name(o["horse_name"]) != nk]
                    rows.append((
                        horse_id,
                        r.get("runner", ""),
                        f"pf{m['meeting_id']}_{rnum}",
                        track,
                        race_date,
                        distance_m,
                        race_class,
                        parse_class_level(race_class),
                        going,
                        normalize_finish_position(r.get("position"), field_size),
                        safe_float(r.get("margin")),
                        safe_float(r.get("weight")),
                        r.get("jockey"),
                        f"pf{r.get('jockeyId')}" if r.get("jockeyId") else None,
                        safe_int(r.get("barrier")),
                        safe_float(r.get("price")),
                        field_size,
                        race_name,
                        rnum,
                        m["last10"].get(str(r.get("runnerId") or "")),
                        json.dumps(my_opponents),
                    ))
    return rows, races_seen


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
        cur.execute("""
            SELECT race_date::text, LOWER(track), race_number
            FROM race_results_history WHERE race_date >= %s
        """, (dates[-1],))
        existing = {(r[0], r[1], r[2]) for r in cur.fetchall()}
        cur.execute("""
            SELECT DISTINCT ON (LOWER(horse_name)) LOWER(horse_name), horse_id
            FROM race_results_history ORDER BY LOWER(horse_name), race_date DESC
        """)
        bridge = {norm_name(name): hid for name, hid in cur.fetchall()}
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
        fresh = [r for r in rows if (r[4], str(r[3]).lower(), r[18]) not in existing]
        skipped = len(rows) - len(fresh)
        new_ids = sum(1 for r in fresh if str(r[0]).startswith("pf"))
        print(f"  {len(day['meetings'])} resulted meetings -> {len(rows)} runner rows "
              f"({skipped} already in DB, {new_ids} new horse ids)")
        total_rows += len(fresh)
        total_skipped += skipped
        total_new_ids += new_ids
        if args.commit and cur:
            from psycopg2.extras import execute_values, Json
            if fresh:
                execute_values(
                    cur,
                    f"INSERT INTO race_results_history ({', '.join(RRH_COLUMNS)}) VALUES %s",
                    fresh, page_size=500)
            for raw in day["raw"]:
                cur.execute(
                    "INSERT INTO pf_raw_payloads (kind, ref_date, meeting_id, payload) VALUES (%s,%s,%s,%s)",
                    (raw["kind"], raw["ref_date"], raw["meeting_id"], Json(raw["payload"])))
            conn.commit()
            existing.update((r[4], str(r[3]).lower(), r[18]) for r in fresh)

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

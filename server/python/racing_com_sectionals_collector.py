#!/usr/bin/env python3
"""
Collects sectional time data from the racing.com GraphQL API (VIC & SA tracks)
and imports into the sectional_times database table.

Endpoint: https://graphql.rmdprod.racing.com/
Auth: x-api-key header + referer header
"""

import os
import sys
import json
import argparse
import time
import re
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(__file__))

GRAPHQL_ENDPOINT = "https://graphql.rmdprod.racing.com/"
REFERER = "https://www.racing.com/"

# The key is the site's PUBLIC AppSync client key — the same one every browser
# visitor is served, not a personal credential. It rotates, so bootstrap it
# from the page each run rather than storing a copy that silently goes stale.
API_KEY_PAGE = "https://www.racing.com/layouts/app.aspx"
API_KEY_PATTERN = r'headerAPIKey:\s*"(da2-[a-z0-9]+)"'
_API_KEY_CACHE = None


def resolve_api_key(verbose=False):
    """RACING_COM_API_KEY if set, else extract from the page. Never returns
    empty: a keyless request would fail as an opaque 4xx much later."""
    global _API_KEY_CACHE
    if _API_KEY_CACHE:
        return _API_KEY_CACHE
    env_key = os.getenv("RACING_COM_API_KEY", "").strip()
    if env_key:
        _API_KEY_CACHE = env_key
        if verbose:
            print("  API key: from RACING_COM_API_KEY")
        return _API_KEY_CACHE
    req = urllib.request.Request(
        API_KEY_PAGE, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except Exception as e:
        raise RuntimeError(
            f"racing.com key bootstrap FAILED fetching {API_KEY_PAGE}: {e}. "
            "Set RACING_COM_API_KEY to override.") from e
    m = re.search(API_KEY_PATTERN, html)
    if not m:
        raise RuntimeError(
            f"racing.com key bootstrap FAILED: no {API_KEY_PATTERN!r} match in "
            f"{API_KEY_PAGE} ({len(html)} bytes) — the page or key format "
            "changed. Set RACING_COM_API_KEY to override while it is fixed.")
    _API_KEY_CACHE = m.group(1)
    if verbose:
        print(f"  API key: bootstrapped from page ({len(_API_KEY_CACHE)} chars)")
    return _API_KEY_CACHE

SPONSOR_PREFIXES = [
    r"Picklebet\s+Park\s+",
    r"Sportsbet[\s\-]+",
    r"Southside\s+",
    r"bet365\s+Park\s+",
    r"bet365\s+",
    r"TABtouch\s+Park\s+",
    r"TABtouch\s+",
    r"Ladbrokes\s+Park\s+",
    r"Ladbrokes\s+",
]

SPONSOR_REGEX = re.compile(
    r"^(?:" + "|".join(SPONSOR_PREFIXES) + r")",
    re.IGNORECASE,
)

TRACK_NAME_MAP = {
    "caulfield heath": "Caulfield",
    "sandown hillside": "Sandown",
    "sandown lakeside": "Sandown",
    "sportsbet sandown hillside": "Sandown",
    "sportsbet sandown lakeside": "Sandown",
    "picklebet park werribee": "Werribee",
    "southside pakenham": "Pakenham",
    "southside cranbourne": "Cranbourne",
    "sportsbet-ballarat": "Ballarat",
    "bet365 park kyneton": "Kyneton",
    "bet365 echuca": "Echuca",
    "bet365 geelong": "Geelong",
    "bet365 stadium": "Geelong",
}


def normalize_track_name(raw_name):
    if not raw_name:
        return raw_name
    name = raw_name.strip()
    lower = name.lower()
    if lower in TRACK_NAME_MAP:
        return TRACK_NAME_MAP[lower]
    cleaned = SPONSOR_REGEX.sub("", name).strip()
    if cleaned:
        return cleaned
    return name


def normalize_track_key(raw_name):
    normalized = normalize_track_name(raw_name) if raw_name else ""
    return re.sub(r"[^a-z0-9]+", "", str(normalized).lower())


def track_match_score(left, right):
    left_key = normalize_track_key(left)
    right_key = normalize_track_key(right)
    if not left_key or not right_key:
        return 0
    if left_key == right_key:
        return 3
    if left_key in right_key or right_key in left_key:
        return 2
    return 1 if SequenceMatcher(None, left_key, right_key).ratio() >= 0.85 else 0


def normalize_track_filters(tracks):
    if not tracks:
        return None

    normalized = set()
    for track in tracks:
        if not track:
            continue
        normalized.add(normalize_track_name(track).strip().lower())

    return normalized or None


from horse_names import normalize_horse_name, horse_name_match


def parse_time_string(time_str):
    if not time_str:
        return None
    time_str = str(time_str).strip()
    if not time_str or time_str in ("0", "0.0", "00:00:00.000"):
        return None
    try:
        return float(time_str)
    except ValueError:
        pass
    try:
        parts = time_str.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except (ValueError, IndexError):
        pass
    return None


def parse_distance(dist_str):
    if not dist_str:
        return None
    if isinstance(dist_str, (int, float)):
        return int(dist_str)
    m = re.search(r'(\d+)', str(dist_str))
    if m:
        return int(m.group(1))
    return None


def graphql_request(query, verbose=False):
    params = urllib.parse.urlencode({"query": query})
    url = f"{GRAPHQL_ENDPOINT}?{params}"
    headers = {
        "x-api-key": resolve_api_key(verbose),
        "referer": REFERER,
        "User-Agent": "Mozilla/5.0 (compatible; RacingAnalytics/1.0)",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "errors" in data:
                if verbose:
                    print(f"  GraphQL errors: {data['errors']}")
                return None
            return data.get("data")
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        if verbose:
            print(f"  API request error: {e}")
        return None
    except Exception as e:
        if verbose:
            print(f"  Unexpected error: {e}")
        return None


def get_meetings(days_back, states="VIC|SA", verbose=False):
    query = '{ GetRaceMeetingsByState(daysBack: %d, daysForward: 0, states: "%s") { id venue venueCode state status date racesCount meetUrl trackName } }' % (days_back, states)
    data = graphql_request(query, verbose)
    if not data:
        return []
    meetings = data.get("GetRaceMeetingsByState", [])
    return meetings or []


def get_races_with_sectionals(meet_code, verbose=False):
    query = '{ getNoCacheRacesForMeet(meetCode: "%s") { id raceNumber raceStatus distance name hasSectionals hasResults sectionalDistances splitDistances raceEntryTimes { horseName horseCode finishPosition finishTime finishTimeSeconds sixHundredMetresTime twoHundredMetresTime overallAvgSpeed overallPeakSpeed topSpeed avgSpeedEarly avgSpeedMid avgSpeedLate beatenMargin distanceTravelled distanceFromRail barrierNumber saddleNumber jockeyName trainerName toEightHundredMetresSeconds eightHundredToFourHundredMetresSeconds fourHundredToFinishMetresSeconds splitTimes { distance time avgSpeed position index } } } }' % meet_code
    data = graphql_request(query, verbose)
    if not data:
        return []
    races = data.get("getNoCacheRacesForMeet", [])
    return races or []


def extract_split_data(entry, race_distance):
    split_times_raw = entry.get("splitTimes") or []
    splits = []
    for st in split_times_raw:
        dist = st.get("distance")
        t = st.get("time")
        spd = st.get("avgSpeed")
        if dist is not None:
            split_entry = {"distance": dist}
            if t is not None:
                split_entry["time"] = t
            if spd is not None:
                split_entry["avgSpeed"] = spd
            if st.get("position") is not None:
                split_entry["position"] = st["position"]
            splits.append(split_entry)
    splits.sort(key=lambda x: x.get("distance", 0))

    last_200m_time = parse_time_string(entry.get("twoHundredMetresTime"))
    last_600m_time = parse_time_string(entry.get("sixHundredMetresTime"))
    avg_speed = entry.get("overallAvgSpeed")
    if avg_speed is not None:
        try:
            avg_speed = float(avg_speed)
        except (ValueError, TypeError):
            avg_speed = None

    last_200m_speed = None
    last_400m_speed = None
    last_400m_time = None
    last_600m_speed = None
    last_800m_speed = None
    last_800m_time = None

    if last_200m_time and last_200m_time > 0:
        last_200m_speed = 200.0 / last_200m_time

    if last_600m_time and last_600m_time > 0:
        last_600m_speed = 600.0 / last_600m_time

    four_to_finish = entry.get("fourHundredToFinishMetresSeconds")
    if four_to_finish:
        try:
            raw_val = float(four_to_finish)
            if raw_val > 100:
                last_400m_time = raw_val / 100.0
            else:
                last_400m_time = raw_val
            if last_400m_time > 0:
                last_400m_speed = 400.0 / last_400m_time
        except (ValueError, TypeError):
            pass

    four_to_finish_val = last_400m_time
    eight_to_four = entry.get("eightHundredToFourHundredMetresSeconds")
    if four_to_finish_val and eight_to_four:
        try:
            raw_8to4 = float(eight_to_four)
            if raw_8to4 > 100:
                eight_to_four_secs = raw_8to4 / 100.0
            else:
                eight_to_four_secs = raw_8to4
            last_800m_time = four_to_finish_val + eight_to_four_secs
            if last_800m_time > 0:
                last_800m_speed = 800.0 / last_800m_time
        except (ValueError, TypeError):
            pass

    if not last_400m_time and splits:
        for s in splits:
            d = s.get("distance", 0)
            if d and race_distance and abs(d - (race_distance - 400)) < 50:
                t_val = s.get("time")
                if t_val:
                    parsed = parse_time_string(t_val)
                    if parsed and parsed > 0:
                        last_400m_time = parsed
                        last_400m_speed = 400.0 / parsed

    if not last_800m_time and splits:
        for s in splits:
            d = s.get("distance", 0)
            if d and race_distance and abs(d - (race_distance - 800)) < 50:
                t_val = s.get("time")
                if t_val:
                    parsed = parse_time_string(t_val)
                    if parsed and parsed > 0:
                        last_800m_time = parsed
                        last_800m_speed = 800.0 / parsed

    finishing_burst = None
    if last_200m_speed and avg_speed and avg_speed > 0:
        finishing_burst = round(last_200m_speed / avg_speed, 4)

    return {
        "splits_json": splits,
        "last_200m_speed": round(last_200m_speed, 3) if last_200m_speed else None,
        "last_200m_time": round(last_200m_time, 3) if last_200m_time else None,
        "last_400m_speed": round(last_400m_speed, 3) if last_400m_speed else None,
        "last_400m_time": round(last_400m_time, 3) if last_400m_time else None,
        "last_600m_speed": round(last_600m_speed, 3) if last_600m_speed else None,
        "last_600m_time": round(last_600m_time, 3) if last_600m_time else None,
        "last_800m_speed": round(last_800m_speed, 3) if last_800m_speed else None,
        "last_800m_time": round(last_800m_time, 3) if last_800m_time else None,
        "finishing_burst": round(finishing_burst, 4) if finishing_burst else None,
        "avg_speed": round(avg_speed, 3) if avg_speed else None,
    }


def process_meeting(meeting, db_url, verbose=False):
    meet_id = meeting.get("id")
    venue = meeting.get("venue") or meeting.get("trackName") or ""
    state = meeting.get("state", "")
    meet_date = meeting.get("date", "")
    status = meeting.get("status", "")

    if not meet_id:
        return []

    track_name = normalize_track_name(venue)

    if meet_date:
        try:
            dt = datetime.strptime(meet_date[:10], "%Y-%m-%d")
            race_date = dt.strftime("%Y-%m-%d")
        except ValueError:
            race_date = meet_date[:10]
    else:
        race_date = ""

    races = get_races_with_sectionals(meet_id, verbose)
    time.sleep(0.75)

    if not races:
        if verbose:
            print(f"  No races returned for {venue}")
        return []

    sectional_races = [r for r in races if r.get("hasSectionals") and r.get("raceEntryTimes")]
    total_races = len(races)

    if verbose:
        print(f"[{race_date}] {track_name} ({state}) - {total_races} races, {len(sectional_races)} with sectionals")

    all_records = []

    for race in races:
        if not race.get("hasSectionals"):
            continue
        entries = race.get("raceEntryTimes") or []
        if not entries:
            continue

        race_number = race.get("raceNumber", 0)
        race_name = race.get("name", "")
        race_distance = parse_distance(race.get("distance"))

        runners_with_data = 0

        for entry in entries:
            horse_name = entry.get("horseName", "").strip()
            if not horse_name:
                continue

            split_data = extract_split_data(entry, race_distance)

            if not split_data["splits_json"] and not split_data["last_200m_time"] and not split_data["avg_speed"]:
                continue

            runners_with_data += 1

            saddle_number = entry.get("saddleNumber")
            if saddle_number is not None:
                try:
                    saddle_number = int(saddle_number)
                except (ValueError, TypeError):
                    saddle_number = None

            winning_time = entry.get("finishTime") or ""

            record = {
                "track": track_name,
                "race_date": race_date,
                "race_number": race_number,
                "race_name": race_name,
                "horse_name": horse_name,
                "saddle_number": saddle_number,
                "distance_m": race_distance,
                "winning_time": str(winning_time) if winning_time else None,
                "track_config": None,
                "splits_json": split_data["splits_json"],
                "last_200m_speed": split_data["last_200m_speed"],
                "last_200m_time": split_data["last_200m_time"],
                "last_400m_speed": split_data["last_400m_speed"],
                "last_400m_time": split_data["last_400m_time"],
                "last_600m_speed": split_data["last_600m_speed"],
                "last_600m_time": split_data["last_600m_time"],
                "last_800m_speed": split_data["last_800m_speed"],
                "last_800m_time": split_data["last_800m_time"],
                "finishing_burst": split_data["finishing_burst"],
                "avg_speed": split_data["avg_speed"],
                "race_results_history_id": None,
            }
            all_records.append(record)

        if verbose and runners_with_data > 0:
            dist_str = f"{race_distance}m" if race_distance else "?m"
            print(f"  R{race_number}: {race_name} ({dist_str}) - {runners_with_data} runners with sectionals")

    return all_records


def match_to_race_results(records, db_url):
    import psycopg2

    if not records:
        return 0, 0

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    matched = 0
    unmatched = 0

    dates_tracks = set()
    for r in records:
        dates_tracks.add((r["race_date"], r["track"]))

    results_cache = {}
    for race_date, track in dates_tracks:
        cur.execute("""
            SELECT id, horse_name, race_number, race_date, track
            FROM race_results_history
            WHERE race_date = %s AND (track = %s OR track ILIKE %s)
        """, (race_date, track, f"%{track}%"))

        rows = cur.fetchall()
        for row in rows:
            key = (row[3], row[2])
            if key not in results_cache:
                results_cache[key] = []
            results_cache[key].append({"id": row[0], "horse_name": row[1], "track": row[4]})

    for record in records:
        key = (record["race_date"], record["race_number"])
        candidates = results_cache.get(key, [])

        best_match = None
        best_score = -1
        for candidate in candidates:
            score = track_match_score(record["track"], candidate["track"])
            if score <= 0:
                continue
            if horse_name_match(record["horse_name"], candidate["horse_name"]) and score > best_score:
                best_match = candidate["id"]
                best_score = score

        if best_match:
            record["race_results_history_id"] = best_match
            matched += 1
        else:
            record["race_results_history_id"] = None
            unmatched += 1

    cur.close()
    conn.close()

    return matched, unmatched


def import_to_database(records, db_url, batch_size=500):
    """Insert a meeting's records in batched statements.

    The INSERT already carries ON CONFLICT DO NOTHING, so the old per-record
    existence SELECT only served to count skips — at two cross-region round
    trips per runner it dominated runtime (Neon is us-east-1; collectors run
    from Sydney). One execute_values call per batch does the same work:
    rowcount gives the rows actually written, and everything else was a
    duplicate. Records are de-duplicated on the conflict key first so a
    payload repeating a runner cannot inflate the batch.
    """
    import psycopg2
    from psycopg2.extras import execute_values

    if not records:
        return 0, 0

    deduped = {}
    for record in records:
        key = (record["race_date"], record["track"], record["race_number"],
               record["horse_name"])
        deduped.setdefault(key, record)
    batch_records = list(deduped.values())

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    sql = """
        INSERT INTO sectional_times (
            race_results_history_id, track, race_date, race_number, race_name,
            horse_name, saddle_number, distance_m, winning_time, track_config,
            splits_json, last_200m_speed, last_200m_time,
            last_400m_speed, last_400m_time,
            last_600m_speed, last_600m_time,
            last_800m_speed, last_800m_time,
            finishing_burst, avg_speed, source
        ) VALUES %s
        ON CONFLICT (race_date, track, race_number, horse_name) DO NOTHING
    """

    imported = 0
    try:
        for i in range(0, len(batch_records), batch_size):
            batch = batch_records[i:i + batch_size]
            values = [(
                record.get("race_results_history_id"),
                record["track"],
                record["race_date"],
                record["race_number"],
                record.get("race_name"),
                record["horse_name"],
                record.get("saddle_number"),
                record.get("distance_m"),
                record.get("winning_time"),
                record.get("track_config"),
                json.dumps(record.get("splits_json", [])),
                record.get("last_200m_speed"),
                record.get("last_200m_time"),
                record.get("last_400m_speed"),
                record.get("last_400m_time"),
                record.get("last_600m_speed"),
                record.get("last_600m_time"),
                record.get("last_800m_speed"),
                record.get("last_800m_time"),
                record.get("finishing_burst"),
                record.get("avg_speed"),
                "racing_com",
            ) for record in batch]
            execute_values(cur, sql, values, page_size=len(values))
            imported += max(cur.rowcount, 0)
            conn.commit()
    finally:
        cur.close()
        conn.close()

    return imported, len(records) - imported

def collect_for_date(date_str, states="VIC|SA", db_url=None, verbose=False, tracks=None):
    today = datetime.now()
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"  Invalid date format: {date_str}")
        return 0

    days_back = (today - target).days
    if days_back < 0:
        days_back = 0
    days_back = max(days_back + 1, 1)

    meetings = get_meetings(days_back, states, verbose)
    time.sleep(0.75)
    target_track_filters = normalize_track_filters(tracks)

    seen_ids = set()
    target_meetings = []
    for m in meetings:
        mid = m.get("id")
        venue = normalize_track_name(m.get("venue") or m.get("trackName") or "")
        if m.get("date", "")[:10] != date_str or mid in seen_ids:
            continue
        if target_track_filters and venue.strip().lower() not in target_track_filters:
            continue
        if m.get("date", "")[:10] == date_str and mid not in seen_ids:
            seen_ids.add(mid)
            target_meetings.append(m)

    if not target_meetings:
        if verbose:
            print(f"  No meetings found for {date_str}")
        return 0

    total_imported = 0

    for meeting in target_meetings:
        records = process_meeting(meeting, db_url, verbose)

        if not records:
            continue

        matched, unmatched = match_to_race_results(records, db_url)

        imported, skipped = import_to_database(records, db_url)

        venue = normalize_track_name(meeting.get("venue") or meeting.get("trackName") or "")
        if verbose:
            print(f"  Imported {imported} sectional records from {venue} (matched={matched}, skipped={skipped})")
        elif imported > 0:
            print(f"  Imported {imported} sectional records from {venue}")

        total_imported += imported
        time.sleep(0.75)

    return total_imported


def collect_from_date(from_date, states="VIC|SA", db_url=None, verbose=False,
                      max_windows=400):
    """Backfill every meeting from `from_date` to today.

    GetRaceMeetingsByState caps each response at roughly 100-150 meetings
    starting at the daysBack boundary, so a single large daysBack silently
    returns a sliver of the range. This walks a cursor forward instead: each
    call starts where the previous response ended, and a window that fails to
    advance the cursor is nudged a day so a dense date can never wedge the
    loop. Meetings already seen are skipped, so overlapping windows cost one
    dict lookup rather than a refetch.
    """
    states_pipe = states.replace(",", "|")
    today = datetime.now().date()
    cursor = datetime.strptime(from_date, "%Y-%m-%d").date()

    seen_ids = set()
    total_imported = total_records = total_meetings = 0
    windows = 0

    print(f"  Paging {from_date} -> {today} for {states}")

    while cursor <= today and windows < max_windows:
        windows += 1
        days_back = (today - cursor).days
        meetings = get_meetings(days_back, states_pipe, verbose) or []
        time.sleep(0.75)

        fresh = []
        max_date = cursor
        for m in meetings:
            mdate = (m.get("date") or "")[:10]
            if not mdate:
                continue
            try:
                d = datetime.strptime(mdate, "%Y-%m-%d").date()
            except ValueError:
                continue
            if d > max_date:
                max_date = d
            if d < cursor or d > today:
                continue
            mid = m.get("id")
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                fresh.append(m)

        if verbose or fresh:
            print(f"  [window {windows}] from {cursor} (daysBack={days_back}): "
                  f"{len(meetings)} returned, {len(fresh)} new")

        for meeting in fresh:
            try:
                records = process_meeting(meeting, db_url, verbose)
                if not records:
                    continue
                total_records += len(records)
                matched, _ = match_to_race_results(records, db_url)
                imported, skipped = import_to_database(records, db_url)
                total_imported += imported
                total_meetings += 1
                venue = normalize_track_name(
                    meeting.get("venue") or meeting.get("trackName") or "")
                meet_date = (meeting.get("date") or "")[:10]
                if imported > 0:
                    print(f"  [{meet_date}] {venue}: {imported} imported "
                          f"({matched} matched, {skipped} skipped)")
            except Exception as e:
                venue = meeting.get("venue") or meeting.get("trackName") or "Unknown"
                print(f"  Error processing {venue}: {e}")
            time.sleep(0.75)

        cursor = max_date + timedelta(days=1) if max_date > cursor \
            else cursor + timedelta(days=1)

    if windows >= max_windows:
        print(f"  STOPPED at the {max_windows}-window guard, cursor {cursor} — "
              "rerun with --from-date at that cursor to continue")
    print(f"  Done: {total_meetings} meetings with data, {total_records} records, "
          f"{total_imported} imported ({len(seen_ids)} meetings seen)")
    return total_imported


def collect_backfill(days, states="VIC|SA", db_url=None, verbose=False):
    states_pipe = states.replace(",", "|")

    meetings = get_meetings(days, states_pipe, verbose)
    time.sleep(0.75)

    if not meetings:
        print(f"  No meetings found in last {days} days")
        return 0

    seen_ids = set()
    unique_meetings = []
    for m in meetings:
        mid = m.get("id")
        if mid not in seen_ids:
            seen_ids.add(mid)
            unique_meetings.append(m)

    meetings_with_results = [
        m for m in unique_meetings
        if m.get("status", "").lower() in ("results", "completed", "final", "abandoned")
        or m.get("racesCount", 0) > 0
    ]

    if not meetings_with_results:
        meetings_with_results = unique_meetings

    print(f"  Found {len(meetings_with_results)} meetings in last {days} days for {states}")

    total_imported = 0
    total_records = 0

    for i, meeting in enumerate(meetings_with_results):
        try:
            records = process_meeting(meeting, db_url, verbose)

            if not records:
                continue

            total_records += len(records)

            matched, unmatched = match_to_race_results(records, db_url)

            imported, skipped = import_to_database(records, db_url)

            venue = normalize_track_name(meeting.get("venue") or meeting.get("trackName") or "")
            meet_date = meeting.get("date", "")[:10]

            if imported > 0:
                print(f"  [{meet_date}] {venue}: {imported} imported ({matched} matched, {skipped} skipped)")
            elif verbose:
                print(f"  [{meet_date}] {venue}: 0 imported ({skipped} skipped/dupes)")

            total_imported += imported

        except Exception as e:
            venue = meeting.get("venue") or meeting.get("trackName") or "Unknown"
            print(f"  Error processing {venue}: {e}")
            continue

        if (i + 1) % 5 == 0:
            print(f"  Progress: {i+1}/{len(meetings_with_results)} meetings processed")

        time.sleep(0.75)

    return total_imported


def main():
    parser = argparse.ArgumentParser(description="Collect sectional times from racing.com (VIC & SA)")
    parser.add_argument("--date", type=str, help="Date to fetch (YYYY-MM-DD)")
    parser.add_argument("--backfill", action="store_true", help="Backfill last N days")
    parser.add_argument("--days", type=int, default=30, help="Number of days to backfill (default: 30)")
    parser.add_argument("--states", type=str, default="VIC,SA", help="Comma-separated states (default: VIC,SA)")
    parser.add_argument("--from-date", type=str, dest="from_date",
                        help="Backfill every meeting from this date (YYYY-MM-DD) "
                             "to today, paging past the API's per-call cap")
    parser.add_argument("--tracks", type=str, help="Comma-separated track filter for single-date mode")
    parser.add_argument("--verbose", action="store_true", help="Enable detailed logging")
    args = parser.parse_args()

    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL not set")
        sys.exit(1)

    states_pipe = args.states.replace(",", "|")

    print("\n" + "=" * 60)
    print("  RACING.COM SECTIONAL TIMES COLLECTOR")
    print("  States: " + args.states)
    print("=" * 60)

    if args.from_date:
        print(f"\n  Mode: Paged backfill from {args.from_date}")
        total = collect_from_date(args.from_date, states_pipe, db_url, args.verbose)

    elif args.backfill:
        print(f"\n  Mode: Backfill last {args.days} days")
        total = collect_backfill(args.days, states_pipe, db_url, args.verbose)
        print(f"\n  Total imported: {total}")
    elif args.date:
        print(f"\n  Mode: Single date ({args.date})")
        track_filters = [part.strip() for part in args.tracks.split(",")] if args.tracks else None
        if track_filters:
            print("  Track filter: " + ", ".join(track_filters))
        total = collect_for_date(args.date, states_pipe, db_url, args.verbose, tracks=track_filters)
        print(f"\n  Total imported: {total}")
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        print(f"\n  Mode: Today ({today})")
        total = collect_for_date(today, states_pipe, db_url, args.verbose)
        print(f"\n  Total imported: {total}")

    print()


if __name__ == "__main__":
    main()

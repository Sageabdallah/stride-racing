#!/usr/bin/env python3
"""
Fetches race results for a specific date from The Racing API and
APPENDS them into race_results_history (no truncation).

Skips races that already exist (by race_date + track + race_number).
"""

import os, sys, json, re, time, argparse, base64, ssl
import urllib.request

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

API_USERNAME = os.environ.get("RACING_API_USERNAME", "")
API_PASSWORD = os.environ.get("RACING_API_PASSWORD", "")
BASE_URL = os.environ.get("RACING_API_BASE", "https://api.theracingapi.com")

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

METRO_TRACKS = [
    "flemington", "caulfield", "moonee valley", "sandown",
    "randwick", "royal randwick", "rosehill", "warwick farm", "canterbury",
    "eagle farm", "doomben", "gold coast",
    "morphettville",
    "newcastle", "kembla grange",
    "ascot", "belmont",
]


def api_get(endpoint, params=None):
    url = f"{BASE_URL}{endpoint}"
    if params:
        url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    req = urllib.request.Request(url)
    creds = base64.b64encode(f"{API_USERNAME}:{API_PASSWORD}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("Accept", "application/json")
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  API error: {e}")
        return None


def is_trial(race):
    if race.get('is_trial') or race.get('is_jump_out'):
        return True
    rc = str(race.get('class') or '').upper()
    if any(x in rc for x in ('BT', 'TRIAL', 'TRL')):
        return True
    rn = str(race.get('race_name') or '').upper()
    if 'TRIAL' in rn or 'JUMP OUT' in rn:
        return True
    return False


def parse_distance(d):
    if not d: return None
    m = re.search(r'(\d+)', str(d))
    return int(m.group(1)) if m else None

def parse_class_level(rc):
    if not rc: return 1
    rc = rc.upper()
    if any(x in rc for x in ("GROUP 1", "GROUP1", " G1")): return 10
    if any(x in rc for x in ("GROUP 2", "GROUP2", " G2")): return 9
    if any(x in rc for x in ("GROUP 3", "GROUP3", " G3")): return 8
    if "LISTED" in rc: return 7
    bm = re.search(r'BM\s*(\d+)', rc)
    if bm:
        n = int(bm.group(1))
        if n >= 88: return 6
        if n >= 70: return 5
        if n >= 58: return 4
        if n >= 50: return 3
        return 2
    if "MAIDEN" in rc: return 2
    return 1

def safe_int(v):
    if v is None: return None
    try: return int(v)
    except: return None

def safe_float(v):
    if v is None: return None
    try: return float(v)
    except: return None

def normalize_finish_position(v, field_size=None):
    pos = safe_int(v)
    if pos is None or pos <= 0:
        return None
    # Racing API sometimes returns status-like codes (e.g. 100/109) instead of
    # a genuine finishing position. Keep those out of RRH.
    if pos >= 100:
        return None
    if field_size and pos > field_size:
        return None
    return pos

def parse_margin(m):
    if m is None: return None
    if isinstance(m, (int, float)): return float(m)
    s = str(m).replace("L","").replace("l","").strip()
    try: return float(s)
    except: return None

def parse_weight(w):
    if w is None: return None
    if isinstance(w, (int, float)): return float(w)
    m = re.search(r'([\d.]+)', str(w))
    return float(m.group(1)) if m else None


def fetch_date(date_str, metro_only=False):
    """Fetch all race results for a date from the API."""
    print(f"\n  Fetching meets for {date_str}...")
    meets = api_get("/v1/australia/meets", {"date": date_str})
    if not meets:
        print("  No meets returned")
        return []

    meets_list = meets.get("meets", meets) if isinstance(meets, dict) else meets
    if not isinstance(meets_list, list):
        print("  Invalid meets response")
        return []

    all_meetings = []
    for meet in meets_list:
        meet_id = meet.get("meet_id") or meet.get("id")
        course = meet.get("course") or meet.get("track") or ""
        if not meet_id or not course:
            continue

        if metro_only:
            cl = course.lower()
            if not any(t in cl or cl in t for t in METRO_TRACKS):
                continue

        print(f"  {course} (meet {meet_id})...")
        races_resp = api_get(f"/v1/australia/meets/{meet_id}/races")
        if not races_resp:
            continue

        races_list = races_resp.get("races", races_resp) if isinstance(races_resp, dict) else races_resp
        if not isinstance(races_list, list):
            continue

        proper_races = []
        for r in races_list:
            rnum = r.get("race_number") or r.get("number")
            if rnum is None:
                continue
            details = api_get(f"/v1/australia/meets/{meet_id}/races/{int(rnum)}")
            if not details or not isinstance(details, dict):
                continue
            if is_trial(details):
                continue
            details['course'] = details.get('course') or course
            details['date'] = details.get('date') or date_str
            proper_races.append(details)
            time.sleep(0.1)

        if proper_races:
            print(f"    {len(proper_races)} races fetched")
            all_meetings.append({
                "date": date_str,
                "meet_id": str(meet_id),
                "course": course,
                "races": proper_races,
            })
        time.sleep(0.1)

    return all_meetings


def import_meetings(meetings, conn):
    """Insert race results into race_results_history, skipping existing."""
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT race_date, LOWER(track), race_number
        FROM race_results_history
        WHERE race_date = ANY(%s)
    """, ([m["date"] for m in meetings],))
    existing = set((r[0], r[1], r[2]) for r in cur.fetchall())
    print(f"\n  Existing date/track/race combos: {len(existing)}")

    columns = (
        "horse_id", "horse_name", "race_id", "track", "race_date",
        "distance_m", "race_class", "class_level", "going", "position",
        "margin_lengths", "weight_kg", "jockey", "jockey_id", "barrier",
        "sp_odds", "field_size", "race_name", "race_number", "form_string",
        "opponents_json"
    )
    insert_sql = f"INSERT INTO race_results_history ({', '.join(columns)}) VALUES %s"

    total_rows = 0
    total_skipped_races = 0
    total_skipped_runners = 0
    batch = []

    for meeting in meetings:
        meet_id = meeting.get("meet_id", "unknown")
        for race in meeting.get("races", []):
            rnum_raw = race.get("race_number")
            rnum = safe_int(rnum_raw) if rnum_raw else None
            track = race.get("course", meeting.get("course", "Unknown"))
            race_date = race.get("date", meeting.get("date", ""))

            key = (race_date, track.lower(), rnum)
            if key in existing:
                total_skipped_races += 1
                continue

            race_id = f"{meet_id}_{rnum}"
            distance_m = parse_distance(race.get("distance"))
            race_class = race.get("class", "")
            class_level = parse_class_level(race_class)
            going = race.get("going")
            race_name = race.get("race_name")

            runners = race.get("runners", [])
            active = [r for r in runners if not r.get("scratched", False)]
            field_size = len(active)

            opponent_data = []
            for r in active:
                opponent_data.append({
                    "horse_id": r.get("horse_id"),
                    "horse_name": r.get("horse"),
                    "position": normalize_finish_position(r.get("position"), field_size),
                    "margin": parse_margin(r.get("margin"))
                })

            for runner in runners:
                if runner.get("scratched", False):
                    total_skipped_runners += 1
                    continue
                horse_id = runner.get("horse_id")
                if not horse_id:
                    total_skipped_runners += 1
                    continue

                opponents = [o for o in opponent_data if o["horse_id"] != horse_id]
                row = (
                    horse_id,
                    runner.get("horse", ""),
                    race_id,
                    track,
                    race_date,
                    distance_m,
                    race_class,
                    class_level,
                    going,
                    normalize_finish_position(runner.get("position"), field_size),
                    parse_margin(runner.get("margin")),
                    parse_weight(runner.get("weight")),
                    runner.get("jockey"),
                    runner.get("jockey_id"),
                    safe_int(runner.get("draw")),
                    safe_float(runner.get("sp")),
                    field_size,
                    race_name,
                    rnum,
                    runner.get("form"),
                    json.dumps(opponents),
                )
                batch.append(row)

            if len(batch) >= 500:
                execute_values(cur, insert_sql, batch, page_size=500)
                conn.commit()
                total_rows += len(batch)
                batch = []

    if batch:
        execute_values(cur, insert_sql, batch, page_size=500)
        conn.commit()
        total_rows += len(batch)

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
    print(f"  Metro only: {args.metro_only}")

    meetings = fetch_date(args.date, metro_only=args.metro_only)
    if not meetings:
        print("\n  No meetings found for this date.")
        sys.exit(0)

    total_races = sum(len(m.get("races", [])) for m in meetings)
    total_runners = sum(
        len([r for r in race.get("runners", []) if not r.get("scratched")])
        for m in meetings for race in m.get("races", [])
    )

    print(f"\n  Fetched: {len(meetings)} meetings, {total_races} races, {total_runners} runners")

    out_dir = os.path.join(SCRIPT_DIR, os.pardir, os.pardir, "historical_data", "track_imports")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f"{'metro' if args.metro_only else 'all'}_{args.date}.json")
    with open(out_file, "w") as f:
        json.dump(meetings, f, indent=2)
    print(f"  JSON saved: {out_file}")

    print(f"\n  Importing into race_results_history...")
    conn = psycopg2.connect(DATABASE_URL)
    rows, skipped_races, skipped_runners = import_meetings(meetings, conn)
    conn.close()

    print(f"\n  {'='*65}")
    print(f"  IMPORT COMPLETE")
    print(f"  {'='*65}")
    print(f"  Meetings:  {len(meetings)}")
    print(f"  Races:     {total_races} ({skipped_races} already existed)")
    print(f"  Runners imported:  {rows}")
    print(f"  Runners skipped:   {skipped_runners} (scratched/no ID)")
    print()

    for m in meetings:
        nraces = len(m.get("races", []))
        nrunners = sum(len([r for r in race.get("runners",[]) if not r.get("scratched")]) for race in m.get("races",[]))
        print(f"    {m['course']}: {nraces} races, {nrunners} runners")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Download racecards from TheRacingAPI (trials excluded)."""

import json
import sys
import argparse
import base64
import os
from datetime import datetime, timedelta
from pathlib import Path
import time
import warnings

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

API_USERNAME = os.environ.get("RACING_API_USERNAME", "")
API_PASSWORD = os.environ.get("RACING_API_PASSWORD", "")
BASE_URL = os.environ.get("RACING_API_BASE", "https://api.theracingapi.com")

TARGET_TRACKS = [
    "flemington", "caulfield", "caulfield heath", "moonee valley", "sandown",
    "randwick", "royal randwick", "rosehill", "warwick farm", "canterbury",
    "eagle farm", "doomben", "ascot", "belmont", "pinjarra",
    "kensington", "randwick kensington", "gold coast", "aquis park gold coast",
    "geelong", "ladbrokes geelong", "ballarat", "cranbourne", "ipswich", "newcastle",
    "morphettville",
]

OUTPUT_DIR = Path("./racecards")

_SESSION = None


def _get_session():
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        _SESSION.mount("https://", HTTPAdapter(max_retries=retries))
        creds = base64.b64encode(f"{API_USERNAME}:{API_PASSWORD}".encode()).decode()
        _SESSION.headers.update({
            "Authorization": f"Basic {creds}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        _SESSION.verify = False
    return _SESSION


def api_get(endpoint, params=None):
    url = f"{BASE_URL}{endpoint}"
    try:
        resp = _get_session().get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  [API] Request failed {endpoint}: {e}", file=sys.stderr)
        return None


def is_trial(race):
    """Check if race is trial using API flags."""
    if race.get('is_trial') == True:
        return True
    if race.get('is_jump_out') == True:
        return True
    
    # Class check (BT = Barrier Trial)
    race_class = str(race.get('class') or '').upper()
    if 'BT' in race_class or 'TRIAL' in race_class:
        return True
    
    # Name check
    race_name = str(race.get('race_name') or '').upper()
    if 'TRIAL' in race_name or 'JUMP OUT' in race_name or 'JUMPOUT' in race_name:
        return True
    
    return False


def fetch_racecards(date_str):
    """Fetch racecards for a date (proper races only)."""
    print(f"\n  {date_str}:", end=" ", flush=True)
    
    meets = api_get("/v1/australia/meets", {"date": date_str})
    if not meets:
        print("no data")
        return None
    
    meets_list = meets.get("meets", meets) if isinstance(meets, dict) else meets
    if not isinstance(meets_list, list):
        print("no meets")
        return None
    
    all_tracks = []
    for meet in meets_list:
        course = meet.get("course") or meet.get("track") or ""
        if course:
            all_tracks.append(course)
    
    if all_tracks:
        print(f"Available tracks: {', '.join(all_tracks[:10])}", end="")
        if len(all_tracks) > 10:
            print(f" ... and {len(all_tracks)-10} more")
        else:
            print()
    
    results = []
    tracks_info = []
    
    for meet in meets_list:
        meet_id = meet.get("meet_id") or meet.get("id")
        course = meet.get("course") or meet.get("track") or ""
        if not meet_id or not course:
            continue
        
        course_lower = course.lower()
        if not any(t in course_lower or course_lower in t for t in TARGET_TRACKS):
            continue
        
        races_resp = api_get(f"/v1/australia/meets/{meet_id}/races")
        if not races_resp:
            continue
        
        races_list = races_resp.get("races", races_resp) if isinstance(races_resp, dict) else races_resp
        if not isinstance(races_list, list):
            continue
        
        proper_races = []
        trials_count = 0
        
        for r in races_list:
            race_num = r.get("race_number") or r.get("number")
            if race_num is None:
                continue
            
            details = api_get(f"/v1/australia/meets/{meet_id}/races/{int(race_num)}")
            if not details or not isinstance(details, dict):
                continue
            
            # TAG TRIALS (collect but flag them)
            if is_trial(details):
                details['is_barrier_trial'] = True
                trials_count += 1
            
            details['course'] = details.get('course') or course
            details['date'] = details.get('date') or date_str
            
            proper_races.append(details)
            time.sleep(0.1)
        
        if proper_races:
            results.append({
                "date": date_str,
                "meet_id": str(meet_id),
                "course": course,
                "races": proper_races,
            })
            
            info = f"{course}({len(proper_races)}R)"
            if trials_count:
                info = f"{course}({len(proper_races)}R, {trials_count} trials tagged)"
            tracks_info.append(info)
    
    if tracks_info:
        print(", ".join(tracks_info))
    else:
        print("no target tracks")
    
    return results if results else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    
    print("\n" + "=" * 55)
    print("  DOWNLOAD RACECARDS (proper races only)")
    print("=" * 55)
    
    dates = [args.date] if args.date else [
        (datetime.now() + timedelta(days=i)).strftime('%Y-%m-%d') 
        for i in range(args.days)
    ]
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    
    for date_str in dates:
        data = fetch_racecards(date_str)
        if data:
            path = OUTPUT_DIR / f"racecard_{date_str}.json"
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            saved.append(date_str)
        time.sleep(0.3)
    
    print("\n" + "=" * 55)
    if saved:
        print(f"  Saved {len(saved)} day(s)")
        print("\n  Note: Odds not available from API until race day")
        print("  Next: python3 get_tips.py")
    else:
        print("  WARNING: No data downloaded")
    print()


if __name__ == '__main__':
    main()

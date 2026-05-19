#!/usr/bin/env python3
"""
Download historical race data (365 days back) from TheRacingAPI.
Ultra-slow mode with comprehensive error handling and auto-recovery.
"""

import json
import urllib.request
import urllib.error
import ssl
import os
from datetime import datetime, timedelta
from pathlib import Path
import time
import base64
import sys
import random

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

API_USERNAME = os.environ.get("RACING_API_USERNAME", "")
API_PASSWORD = os.environ.get("RACING_API_PASSWORD", "")
BASE_URL = os.environ.get("RACING_API_BASE", "https://api.theracingapi.com")

TARGET_TRACKS = [
    "flemington", "caulfield", "moonee valley", "sandown",
    "randwick", "royal randwick", "rosehill", "warwick farm", "canterbury",
    "eagle farm", "doomben", "ascot", "belmont", "pinjarra",
    "kensington", "randwick kensington", "gold coast", "aquis park gold coast",
    "geelong", "ladbrokes geelong", "ballarat", "ipswich",
    "morphettville", "murray bridge", "pakenham", "cranbourne",
    "newcastle",
]

OUTPUT_DIR = Path("./historical_data")

REQUEST_DELAY = 0.8
BETWEEN_RACES_DELAY = 0.4
BETWEEN_DAYS_DELAY = 1.0
MAX_RETRIES = 5
CONSECUTIVE_ERROR_LIMIT = 10
LONG_COOLDOWN = 60


def api_get(endpoint, params=None, attempt=1):
    url = f"{BASE_URL}{endpoint}"
    if params:
        url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    
    req = urllib.request.Request(url)
    creds = base64.b64encode(f"{API_USERNAME}:{API_PASSWORD}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            return json.loads(resp.read().decode()), 200
    except urllib.error.HTTPError as e:
        code = e.code
        if code == 403:
            if attempt <= MAX_RETRIES:
                wait = min(30 * attempt + random.uniform(5, 15), 120)
                print(f"\n    [403 Forbidden] Waiting {wait:.0f}s before retry {attempt}/{MAX_RETRIES}...", flush=True)
                time.sleep(wait)
                return api_get(endpoint, params, attempt + 1)
            return None, 403
        elif code == 429:
            wait = min(60 * attempt + random.uniform(10, 30), 180)
            if attempt <= MAX_RETRIES:
                print(f"\n    [429 Rate Limited] Waiting {wait:.0f}s before retry {attempt}/{MAX_RETRIES}...", flush=True)
                time.sleep(wait)
                return api_get(endpoint, params, attempt + 1)
            return None, 429
        elif code in (500, 502, 503, 504):
            if attempt <= MAX_RETRIES:
                wait = 15 * attempt + random.uniform(5, 10)
                print(f"\n    [{code} Server Error] Waiting {wait:.0f}s before retry {attempt}/{MAX_RETRIES}...", flush=True)
                time.sleep(wait)
                return api_get(endpoint, params, attempt + 1)
            return None, code
        elif code == 404:
            return None, 404
        elif code == 401:
            print(f"\n    [401 Unauthorized] Check API credentials", flush=True)
            return None, 401
        else:
            print(f"\n    [HTTP {code}] Unexpected error", flush=True)
            return None, code
    except urllib.error.URLError as e:
        if attempt <= MAX_RETRIES:
            wait = 10 * attempt + random.uniform(3, 8)
            print(f"\n    [Network Error] {e.reason} - Waiting {wait:.0f}s...", flush=True)
            time.sleep(wait)
            return api_get(endpoint, params, attempt + 1)
        return None, 0
    except Exception as e:
        if attempt <= MAX_RETRIES:
            wait = 10 * attempt
            print(f"\n    [Error] {e} - Waiting {wait:.0f}s...", flush=True)
            time.sleep(wait)
            return api_get(endpoint, params, attempt + 1)
        return None, 0


def is_trial(race):
    if race.get('is_trial') == True:
        return True
    if race.get('is_jump_out') == True:
        return True
    race_class = str(race.get('class') or '').upper()
    if 'BT' in race_class or 'TRIAL' in race_class:
        return True
    race_name = str(race.get('race_name') or '').upper()
    if 'TRIAL' in race_name or 'JUMP OUT' in race_name or 'JUMPOUT' in race_name:
        return True
    return False


def fetch_historical_day(date_str):
    meets_data, code = api_get("/v1/australia/meets", {"date": date_str})
    if not meets_data:
        return [], code
    
    meets_list = meets_data.get("meets", meets_data) if isinstance(meets_data, dict) else meets_data
    if not isinstance(meets_list, list):
        return [], 200
    
    day_meets = []
    
    for meet in meets_list:
        meet_id = meet.get("meet_id") or meet.get("id")
        course = meet.get("course") or meet.get("track") or ""
        if not meet_id or not course:
            continue
        
        course_lower = course.lower()
        if not any(t in course_lower or course_lower in t for t in TARGET_TRACKS):
            continue
        
        time.sleep(REQUEST_DELAY)
        races_resp, rcode = api_get(f"/v1/australia/meets/{meet_id}/races")
        if not races_resp:
            if rcode in (403, 429):
                return day_meets, rcode
            continue
        
        races_list = races_resp.get("races", races_resp) if isinstance(races_resp, dict) else races_resp
        if not isinstance(races_list, list):
            continue
        
        proper_races = []
        
        for r in races_list:
            race_num = r.get("race_number") or r.get("number")
            if race_num is None:
                continue
            
            time.sleep(BETWEEN_RACES_DELAY)
            details, dcode = api_get(f"/v1/australia/meets/{meet_id}/races/{int(race_num)}")
            if not details or not isinstance(details, dict):
                if dcode in (403, 429):
                    if proper_races:
                        day_meets.append({
                            "date": date_str,
                            "meet_id": str(meet_id),
                            "course": course,
                            "races": proper_races,
                        })
                    return day_meets, dcode
                continue
            
            if is_trial(details):
                continue
            
            details['course'] = details.get('course') or course
            details['date'] = details.get('date') or date_str
            proper_races.append(details)
        
        if proper_races:
            day_meets.append({
                "date": date_str,
                "meet_id": str(meet_id),
                "course": course,
                "races": proper_races,
            })
    
    return day_meets, 200


def save_checkpoint(checkpoint_file, output_file, all_meets, total_races, days_processed, next_offset, start_date, end_date, complete=False):
    checkpoint_data = {
        'meets': all_meets,
        'total_races': total_races,
        'days_processed': days_processed,
        'next_offset': next_offset,
    }
    with open(checkpoint_file, 'w') as f:
        json.dump(checkpoint_data, f)
    
    final_data = {
        'metadata': {
            'date_range_start': start_date,
            'date_range_end': end_date,
            'total_meets': len(all_meets),
            'total_races': total_races,
            'downloaded_at': datetime.now().isoformat(),
            'days_processed': days_processed,
            'complete': complete,
        },
        'meets': all_meets,
    }
    with open(output_file, 'w') as f:
        json.dump(final_data, f, indent=2)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Download historical race data going back N days")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--start-offset", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    output_file = OUTPUT_DIR / "historical_training_data.json"
    checkpoint_file = OUTPUT_DIR / "download_checkpoint.json"
    
    all_meets = []
    total_races = 0
    days_processed = 0
    start_offset = args.start_offset
    consecutive_errors = 0
    
    if args.resume and checkpoint_file.exists():
        with open(checkpoint_file, 'r') as f:
            checkpoint = json.load(f)
        all_meets = checkpoint.get('meets', [])
        total_races = checkpoint.get('total_races', 0)
        days_processed = checkpoint.get('days_processed', 0)
        start_offset = checkpoint.get('next_offset', args.start_offset)
        print(f"\n  Resuming from day {days_processed}, offset {start_offset}")
        print(f"  Already have {len(all_meets)} meets, {total_races} races")
    
    today = datetime.now()
    remaining_days = args.days - days_processed
    end_date = (today - timedelta(days=args.start_offset)).strftime('%Y-%m-%d')
    start_date = (today - timedelta(days=args.start_offset + args.days - 1)).strftime('%Y-%m-%d')
    
    print("\n" + "=" * 60)
    print("  DOWNLOADING HISTORICAL RACE DATA (SLOW MODE)")
    print("=" * 60)
    print(f"  Range: {start_date} to {end_date}")
    print(f"  Days remaining: {remaining_days}")
    print(f"  Request delay: {REQUEST_DELAY}s between API calls")
    print(f"  Between days: {BETWEEN_DAYS_DELAY}s pause")
    print(f"  Max retries per request: {MAX_RETRIES}")
    print(f"  Checkpoint every: {args.batch_size} days")
    print()
    
    for i in range(remaining_days):
        day_offset = start_offset + (remaining_days - 1 - i)
        date_str = (today - timedelta(days=day_offset)).strftime('%Y-%m-%d')
        days_processed += 1
        
        print(f"  [{days_processed}/{args.days}] {date_str}: ", end="", flush=True)
        
        try:
            day_meets, status_code = fetch_historical_day(date_str)
            
            if status_code in (403, 429):
                consecutive_errors += 1
                print(f"BLOCKED ({status_code})", flush=True)
                
                if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT:
                    print(f"\n  !!! {consecutive_errors} consecutive errors - taking {LONG_COOLDOWN}s cooldown !!!", flush=True)
                    next_off = start_offset + (remaining_days - 1 - i) - 1 if i < remaining_days - 1 else start_offset
                    save_checkpoint(checkpoint_file, output_file, all_meets, total_races, days_processed - 1, day_offset, start_date, end_date)
                    print(f"  Checkpoint saved. Cooling down...", flush=True)
                    time.sleep(LONG_COOLDOWN)
                    consecutive_errors = 0
                    days_processed -= 1
                    i -= 1
                else:
                    cooldown = 30 + random.uniform(10, 20)
                    print(f"  Cooling down {cooldown:.0f}s...", flush=True)
                    time.sleep(cooldown)
                    days_processed -= 1
                continue
            
            if day_meets:
                consecutive_errors = 0
                day_races = sum(len(m['races']) for m in day_meets)
                total_races += day_races
                all_meets.extend(day_meets)
                tracks = [m['course'] for m in day_meets]
                print(f"{day_races} races from {', '.join(tracks)}", flush=True)
            else:
                consecutive_errors = 0
                print("no target track races", flush=True)
            
            if days_processed % args.batch_size == 0 and days_processed > 0:
                next_off = start_offset + (remaining_days - 1 - i) - 1 if i < remaining_days - 1 else start_offset
                save_checkpoint(checkpoint_file, output_file, all_meets, total_races, days_processed, next_off, start_date, end_date)
                print(f"\n  --- Checkpoint: {len(all_meets)} meets, {total_races} races, {days_processed} days ---\n", flush=True)
            
        except KeyboardInterrupt:
            print("\n\n  Interrupted! Saving checkpoint...", flush=True)
            next_off = start_offset + (remaining_days - 1 - i) - 1 if i < remaining_days - 1 else start_offset
            save_checkpoint(checkpoint_file, output_file, all_meets, total_races, days_processed, next_off, start_date, end_date)
            print(f"  Saved. Resume with: python3 server/python/download_historical.py --resume")
            sys.exit(0)
        except Exception as e:
            print(f"ERROR: {e}", flush=True)
            consecutive_errors += 1
            continue
        
        delay = BETWEEN_DAYS_DELAY + random.uniform(1, 3)
        time.sleep(delay)
    
    save_checkpoint(checkpoint_file, output_file, all_meets, total_races, days_processed, start_offset, start_date, end_date, complete=True)
    
    if checkpoint_file.exists():
        checkpoint_file.unlink()
    
    print("\n" + "=" * 60)
    print("  DOWNLOAD COMPLETE")
    print("=" * 60)
    print(f"  Total meets: {len(all_meets)}")
    print(f"  Total races: {total_races}")
    print(f"  Days processed: {days_processed}")
    print(f"  Output: {output_file}")
    print(f"\n  Next: python3 server/python/import_historical_to_db.py")
    print()


if __name__ == '__main__':
    main()

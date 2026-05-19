#!/usr/bin/env python3
"""Downloads 365 days of historical race results from target tracks, capturing ALL available data fields for ML training."""

import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import time
import os
import sys
import requests

API_USERNAME = os.environ.get("RACING_API_USERNAME", "")
API_PASSWORD = os.environ.get("RACING_API_PASSWORD", "")
BASE_URL = "https://api.theracingapi.com"

if not API_USERNAME:
    print("Error: RACING_API_USERNAME environment variable not set")
    print("Please set RACING_API_USERNAME and RACING_API_PASSWORD in your shell environment or .env file")
    sys.exit(1)

# These are substring-matched against API track names (case-insensitive)
TARGET_TRACKS = [
    "randwick", "rosehill",
    "flemington", "caulfield",
    "doomben", "sunshine coast",
    "ascot", "pinjarra",
]

OUTPUT_DIR = Path("./historical_data")
OUTPUT_FILE = OUTPUT_DIR / "historical_training_data.json"
PROGRESS_FILE = OUTPUT_DIR / "download_progress.json"


def api_get(endpoint, params=None, retries=3):
    """Make API request with retries using requests library."""
    url = f"{BASE_URL}{endpoint}"
    
    for attempt in range(retries):
        try:
            resp = requests.get(
                url, params=params,
                auth=(API_USERNAME, API_PASSWORD),
                headers={"Accept": "application/json"},
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503):
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(1)
            else:
                return None
    return None


def is_trial(race):
    """Check if race is a trial (exclude from training data)."""
    if race.get('is_trial') == True or race.get('is_jump_out') == True:
        return True
    race_class = str(race.get('class') or '').upper()
    if 'BT' in race_class or 'TRIAL' in race_class or 'TRL' in race_class:
        return True
    race_name = str(race.get('race_name') or '').upper()
    if 'TRIAL' in race_name or 'JUMP OUT' in race_name:
        return True
    return False


def is_target_track(course):
    """Check if course matches target tracks."""
    course_lower = course.lower().strip()
    for target in TARGET_TRACKS:
        if target in course_lower or course_lower in target:
            return True
    return False


def extract_comprehensive_runner_data(runner):
    """Extract ALL available data from runner for ML training."""
    return {
        "horse": runner.get("horse") or runner.get("name") or "",
        "horse_id": runner.get("horse_id") or "",
        "number": runner.get("number") or runner.get("tab_no") or 0,
        "barrier": runner.get("barrier") or runner.get("draw") or 0,
        
        "finishing_position": runner.get("position") or runner.get("finishing_position") or 0,
        "margin": runner.get("margin") or 0,
        "win_margin": runner.get("win_margin") or runner.get("winning_margin") or 0,
        "beaten_margin": runner.get("beaten_margin") or runner.get("lengths_behind") or 0,
        
        "starting_price": runner.get("sp") or runner.get("starting_price") or 0,
        "win_odds": runner.get("win_odds") or 0,
        "place_odds": runner.get("place_odds") or 0,
        "odds_history": runner.get("odds") or [],
        
        "weight": runner.get("weight") or 0,
        "weight_carried": runner.get("weight_carried") or runner.get("weight") or 0,
        "handicap_weight": runner.get("handicap_weight") or 0,
        "weight_for_age": runner.get("weight_for_age") or runner.get("wfa") or 0,
        "penalty": runner.get("penalty") or 0,
        "allowance": runner.get("allowance") or 0,
        
        "form": runner.get("form") or "",
        "last_5_runs": runner.get("last_5_runs") or runner.get("form") or "",
        "career_record": runner.get("career") or "",
        "last_raced": runner.get("last_raced") or "",
        "days_since_last_run": runner.get("days_since_last_run") or 0,
        
        "age": runner.get("age") or 0,
        "sex": runner.get("sex") or "",
        "colour": runner.get("colour") or "",
        "sire": runner.get("sire") or "",
        "dam": runner.get("dam") or "",
        "dam_sire": runner.get("dam_sire") or "",
        "breeder": runner.get("breeder") or "",
        
        "jockey": runner.get("jockey") or "",
        "jockey_id": runner.get("jockey_id") or "",
        "trainer": runner.get("trainer") or "",
        "trainer_id": runner.get("trainer_id") or "",
        "owner": runner.get("owner") or "",
        "silks": runner.get("silks") or "",
        
        "gear": runner.get("gear") or "",
        "gear_changes": runner.get("gear_changes") or "",
        "blinkers": runner.get("blinkers") or False,
        "tongue_tie": runner.get("tongue_tie") or False,
        "winkers": runner.get("winkers") or False,
        "cheek_pieces": runner.get("cheek_pieces") or False,
        "first_time_gear": runner.get("first_time_gear") or "",
        
        "speed_rating": runner.get("speed_rating") or runner.get("rating") or 0,
        "official_rating": runner.get("official_rating") or runner.get("or") or 0,
        "topspeed": runner.get("topspeed") or 0,
        "rpr": runner.get("rpr") or 0,
        
        "sectionals": runner.get("sectionals") or [],
        "final_600m": runner.get("final_600m") or runner.get("last_600") or 0,
        "final_400m": runner.get("final_400m") or runner.get("last_400") or 0,
        "final_200m": runner.get("final_200m") or runner.get("last_200") or 0,
        
        "distance_stats": runner.get("distance_stats") or {},
        "course_stats": runner.get("course_stats") or {},
        "course_distance_stats": runner.get("course_distance_stats") or {},
        "ground_firm_stats": runner.get("ground_firm_stats") or {},
        "ground_good_stats": runner.get("ground_good_stats") or {},
        "ground_soft_stats": runner.get("ground_soft_stats") or {},
        "ground_heavy_stats": runner.get("ground_heavy_stats") or {},
        "jockey_stats": runner.get("jockey_stats") or {},
        "trainer_stats": runner.get("trainer_stats") or {},
        "jockey_trainer_stats": runner.get("jockey_trainer_stats") or {},
        
        "first_up_stats": runner.get("first_up_stats") or {},
        "second_up_stats": runner.get("second_up_stats") or {},
        
        "class_stats": runner.get("class_stats") or {},
        "distance_range": runner.get("distance_range") or "",
        
        "scratched": runner.get("scratched") or runner.get("is_scratched") or False,
        "late_scratching": runner.get("late_scratching") or False,
        "emergency": runner.get("emergency") or False,
        
        "in_running_800m": runner.get("position_800m") or runner.get("in_running_800m") or 0,
        "in_running_400m": runner.get("position_400m") or runner.get("in_running_400m") or 0,
        "settling_position": runner.get("settling_position") or 0,
        
        "prize_money_won": runner.get("prize_money_won") or runner.get("prizemoney") or 0,
        "career_earnings": runner.get("career_earnings") or 0,
    }


def extract_comprehensive_race_data(race):
    """Extract ALL available race data for ML training."""
    runners = []
    for r in race.get("runners") or race.get("horses") or []:
        if not r.get("scratched") and not r.get("is_scratched"):
            runners.append(extract_comprehensive_runner_data(r))
    
    return {
        "race_id": race.get("race_id") or race.get("id") or "",
        "race_number": race.get("race_number") or race.get("number") or 0,
        "race_name": race.get("race_name") or race.get("name") or "",
        
        "course": race.get("course") or race.get("track") or "",
        "track_type": race.get("track_type") or "turf",
        "rail_position": race.get("rail_position") or "",
        
        "date": race.get("date") or "",
        "off_time": race.get("off_time") or race.get("time") or "",
        "local_time": race.get("local_time") or "",
        
        "distance": race.get("distance") or 0,
        "distance_metres": race.get("distance_metres") or race.get("distance") or 0,
        "distance_yards": race.get("distance_yards") or 0,
        
        "going": race.get("going") or race.get("track_condition") or "",
        "going_description": race.get("going_description") or "",
        "weather": race.get("weather") or "",
        "penetrometer": race.get("penetrometer") or 0,
        
        "class": race.get("class") or "",
        "class_level": race.get("class_level") or "",
        "race_type": race.get("race_type") or race.get("type") or "",
        "age_restriction": race.get("age_restriction") or race.get("ages") or "",
        "sex_restriction": race.get("sex_restriction") or "",
        "weight_type": race.get("weight_type") or "",
        
        "prize_money": race.get("prize_money") or race.get("prizemoney") or 0,
        "first_prize": race.get("first_prize") or 0,
        "total_prize": race.get("total_prize") or race.get("prize_money") or 0,
        
        "field_size": len(runners),
        "declared_runners": race.get("declared_runners") or len(runners),
        
        "winning_time": race.get("winning_time") or race.get("time") or "",
        "standard_time": race.get("standard_time") or "",
        
        "track_bias": race.get("track_bias") or "",
        "pace_scenario": race.get("pace_scenario") or "",
        
        "runners": runners,
    }


def fetch_results_for_date(date_str):
    """Fetch all race results for target tracks on a specific date."""
    meets = api_get("/v1/australia/meets", {"date": date_str})
    if not meets:
        return []
    
    meets_list = meets.get("meets", meets) if isinstance(meets, dict) else meets
    if not isinstance(meets_list, list):
        return []
    
    results = []
    
    for meet in meets_list:
        meet_id = meet.get("meet_id") or meet.get("id")
        course = meet.get("course") or meet.get("track") or ""
        
        if not meet_id or not course:
            continue
        
        if not is_target_track(course):
            continue
        
        races_resp = api_get(f"/v1/australia/meets/{meet_id}/races")
        if not races_resp:
            continue
        
        races_list = races_resp.get("races", races_resp) if isinstance(races_resp, dict) else races_resp
        if not isinstance(races_list, list):
            continue
        
        meet_races = []
        
        for r in races_list:
            if is_trial(r):
                continue
            
            r['course'] = r.get('course') or course
            r['date'] = r.get('date') or date_str
            
            race_data = extract_comprehensive_race_data(r)
            
            def get_position(runner):
                pos = runner.get("finishing_position", 0)
                try:
                    return int(pos) if pos else 0
                except (ValueError, TypeError):
                    return 0
            
            has_results = any(get_position(runner) > 0 for runner in race_data.get("runners", []))
            if has_results or race_data.get("winning_time"):
                meet_races.append(race_data)
        
        if meet_races:
            results.append({
                "date": date_str,
                "meet_id": str(meet_id),
                "course": course,
                "races": meet_races,
            })
        
        time.sleep(0.5)
    
    return results


def load_progress():
    """Load download progress for resume capability."""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"completed_dates": [], "last_updated": None}


def save_progress(progress):
    """Save download progress."""
    progress["last_updated"] = datetime.now().isoformat()
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)


def load_existing_data():
    """Load existing downloaded data for append mode."""
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"metadata": {}, "meets": []}


def save_data(data):
    """Save downloaded data to JSON."""
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Download comprehensive historical race data for ML training")
    parser.add_argument("--days", type=int, default=365, help="Days to go back (default: 365)")
    parser.add_argument("--resume", action="store_true", help="Resume from last progress")
    parser.add_argument("--force", action="store_true", help="Force re-download all dates")
    parser.add_argument("--track", type=str, default=None, help="Download only for specific track (e.g., 'geelong')")
    args = parser.parse_args()
    
    global TARGET_TRACKS
    if args.track:
        TARGET_TRACKS = [args.track.lower()]
        print(f"\n  Filtering to single track: {args.track}")
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    print("\n" + "=" * 70)
    print("  COMPREHENSIVE HISTORICAL DATA DOWNLOAD FOR ML TRAINING")
    print("=" * 70)
    print(f"\n  Target Tracks:")
    print("    - NSW: Randwick, Warwick Farm, Rosehill")
    print("    - VIC: Flemington, Caulfield, Moonee Valley, Sandown")
    print("    - QLD: Doomben, Eagle Farm")
    print("    - WA: Ascot, Pinjarra")
    print(f"\n  Days: {args.days}")
    print(f"  Output: {OUTPUT_FILE}")
    
    progress = load_progress() if args.resume and not args.force else {"completed_dates": []}
    data = load_existing_data() if args.resume and not args.force else {"metadata": {}, "meets": []}
    
    if args.force:
        progress = {"completed_dates": []}
        data = {"metadata": {}, "meets": []}
    
    today = datetime.now()
    all_dates = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(1, args.days + 1)]
    
    dates_to_process = [d for d in all_dates if d not in progress.get("completed_dates", [])]
    
    print(f"\n  Date range: {all_dates[-1]} to {all_dates[0]}")
    print(f"  Already completed: {len(progress.get('completed_dates', []))} dates")
    print(f"  Remaining: {len(dates_to_process)} dates")
    print()
    
    if not dates_to_process:
        print("  All dates already downloaded. Use --force to re-download.")
        return
    
    races_count = sum(len(m.get('races', [])) for m in data.get('meets', []))
    meets_count = len(data.get('meets', []))
    
    start_time = time.time()
    
    for i, date_str in enumerate(dates_to_process):
        elapsed = time.time() - start_time
        rate = (i + 1) / elapsed if elapsed > 0 else 0
        remaining = (len(dates_to_process) - i - 1) / rate if rate > 0 else 0
        
        print(f"\r  Processing: {date_str} ({i+1}/{len(dates_to_process)}) "
              f"| Meets: {meets_count} | Races: {races_count} "
              f"| ETA: {int(remaining/60)}m", end="", flush=True)
        
        try:
            results = fetch_results_for_date(date_str)
            
            if results:
                for meet in results:
                    data["meets"].append(meet)
                    meets_count += 1
                    races_count += len(meet.get('races', []))
            
            progress["completed_dates"].append(date_str)
            
            if (i + 1) % 5 == 0:
                save_data(data)
                save_progress(progress)
        
        except KeyboardInterrupt:
            print("\n\n  Download interrupted. Progress saved. Run with --resume to continue.")
            save_data(data)
            save_progress(progress)
            return
        except Exception as e:
            print(f"\n  Error on {date_str}: {e}")
        
        time.sleep(0.25)
    
    data["metadata"] = {
        "download_date": datetime.now().isoformat(),
        "date_range_start": all_dates[-1],
        "date_range_end": all_dates[0],
        "days_covered": args.days,
        "target_tracks": TARGET_TRACKS,
        "total_meets": meets_count,
        "total_races": races_count,
    }
    
    save_data(data)
    save_progress(progress)
    
    print("\n\n" + "=" * 70)
    print("  DOWNLOAD COMPLETE")
    print("=" * 70)
    print(f"\n  Meets downloaded: {meets_count}")
    print(f"  Races downloaded: {races_count}")
    print(f"  Saved to: {OUTPUT_FILE}")
    print()
    print("  Next step: Run ML training with this data")
    print("    python3 server/python/train_ml.py --historical historical_data/historical_training_data.json")
    print()


if __name__ == '__main__':
    main()

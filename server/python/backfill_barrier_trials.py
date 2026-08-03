#!/usr/bin/env python3
"""Backfill barrier trials from TheRacingAPI over a date range, tagging them explicitly and storing to the database."""

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

OUTPUT_DIR = Path("./barrier_trials")

REQUEST_DELAY = 0.8
BETWEEN_RACES_DELAY = 0.4
BETWEEN_DAYS_DELAY = 1.0


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
    """Check if race is a trial using API flags."""
    if race.get('is_trial') is True:
        return True
    if race.get('is_jump_out') is True:
        return True
    race_class = str(race.get('class') or '').upper()
    if 'BT' in race_class or 'TRIAL' in race_class:
        return True
    race_name = str(race.get('race_name') or '').upper()
    if 'TRIAL' in race_name or 'JUMP OUT' in race_name or 'JUMPOUT' in race_name:
        return True
    return False



def output_path_for_date(date_str):
    return OUTPUT_DIR / f"trials_{date_str}.json"


def date_already_collected(date_str):
    return output_path_for_date(date_str).exists()


def fetch_trials_for_date(date_str, dry_run=False):
    """Fetch all trial races for a single date. Returns list of trial meets."""
    meets = api_get("/v1/australia/meets", {"date": date_str})
    if not meets:
        return []

    meets_list = meets.get("meets", meets) if isinstance(meets, dict) else meets
    if not isinstance(meets_list, list):
        return []

    collected_at = datetime.utcnow().isoformat() + "Z"
    trial_meets = []

    for meet in meets_list:
        meet_id = meet.get("meet_id") or meet.get("id")
        course = meet.get("course") or meet.get("track") or ""
        if not meet_id or not course:
            continue

        time.sleep(REQUEST_DELAY)
        races_resp = api_get(f"/v1/australia/meets/{meet_id}/races")
        if not races_resp:
            continue

        races_list = races_resp.get("races", races_resp) if isinstance(races_resp, dict) else races_resp
        if not isinstance(races_list, list):
            continue

        trial_races = []
        for r in races_list:
            race_num = r.get("race_number") or r.get("number")
            if race_num is None:
                continue

            time.sleep(BETWEEN_RACES_DELAY)
            details = api_get(f"/v1/australia/meets/{meet_id}/races/{int(race_num)}")
            if not details or not isinstance(details, dict):
                continue

            if not is_trial(details):
                continue

            details['is_barrier_trial'] = True
            details['data_source'] = "backfill_barrier_trials"
            details['collected_at'] = collected_at
            details['course'] = details.get('course') or course
            details['date'] = details.get('date') or date_str

            runners = details.get('runners') or details.get('horses') or []
            trial_races.append(details)

            if dry_run:
                race_name = details.get('race_name', f'Race {race_num}')
                print(f"    [DRY RUN] {race_name} — {len(runners)} runners")

        if trial_races:
            trial_meet = {
                "date": date_str,
                "meet_id": str(meet_id),
                "course": course,
                "is_barrier_trial": True,
                "data_source": "backfill_barrier_trials",
                "collected_at": collected_at,
                "races": trial_races,
            }
            trial_meets.append(trial_meet)

            total_runners = sum(
                len(r.get('runners') or r.get('horses') or [])
                for r in trial_races
            )
            status = "[DRY RUN] " if dry_run else ""
            print(f"  {status}{course}: {len(trial_races)} trial races, {total_runners} runners")

    return trial_meets


def backfill(start_date, end_date, dry_run=False):
    """Backfill barrier trials over a date range."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    current = start_date
    total_dates = (end_date - start_date).days + 1
    dates_processed = 0
    dates_skipped = 0
    total_meets = 0
    total_races = 0

    print(f"\nBarrier Trial Backfill: {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')} ({total_dates} days)")
    if dry_run:
        print("  *** DRY RUN — nothing will be written ***\n")
    else:
        print()

    while current <= end_date:
        date_str = current.strftime('%Y-%m-%d')

        if date_already_collected(date_str):
            dates_skipped += 1
            current += timedelta(days=1)
            continue

        print(f"  {date_str}:", end=" ", flush=True)
        trial_meets = fetch_trials_for_date(date_str, dry_run=dry_run)

        if trial_meets:
            total_meets += len(trial_meets)
            total_races += sum(len(m['races']) for m in trial_meets)

            if not dry_run:
                path = output_path_for_date(date_str)
                with open(path, 'w') as f:
                    json.dump(trial_meets, f, indent=2)
        else:
            print("no trials")

        dates_processed += 1
        current += timedelta(days=1)
        time.sleep(BETWEEN_DAYS_DELAY)

    print(f"\n{'─' * 50}")
    print(f"  Dates processed: {dates_processed}")
    if dates_skipped:
        print(f"  Dates skipped (already collected): {dates_skipped}")
    print(f"  Trial meetings found: {total_meets}")
    print(f"  Trial races found: {total_races}")
    if not dry_run and total_meets:
        print(f"  Output: {OUTPUT_DIR.resolve()}/")
    print()



def main():
    parser = argparse.ArgumentParser(description="Backfill barrier trial data from TheRacingAPI")
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be collected without writing")
    args = parser.parse_args()

    start = datetime.strptime(args.start_date, '%Y-%m-%d')
    end = datetime.strptime(args.end_date, '%Y-%m-%d')

    if start > end:
        print("Error: --start-date must be before --end-date", file=sys.stderr)
        sys.exit(1)

    backfill(start, end, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

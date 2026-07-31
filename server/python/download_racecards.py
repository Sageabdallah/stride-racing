#!/usr/bin/env python3
"""Download racecards via the configured racing data provider (trials tagged, not excluded)."""

import json
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import time

from providers import get_provider, validate_meet_cards

TARGET_TRACKS = [
    "flemington", "caulfield", "caulfield heath", "moonee valley", "sandown",
    "randwick", "royal randwick", "rosehill", "warwick farm", "canterbury",
    "eagle farm", "doomben", "ascot", "belmont", "pinjarra",
    "kensington", "randwick kensington", "gold coast", "aquis park gold coast",
    "geelong", "ladbrokes geelong", "ballarat", "cranbourne", "ipswich", "newcastle",
    "morphettville",
]

OUTPUT_DIR = Path("./racecards")


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


def fetch_racecards(provider, date_str):
    """Fetch racecards for a date (proper races only)."""
    print(f"\n  {date_str}:", end=" ", flush=True)

    meets = provider.fetch_meets(date_str)
    if not meets:
        print("no data")
        return None

    all_tracks = [m["course"] for m in meets]
    print(f"Available tracks: {', '.join(all_tracks[:10])}", end="")
    if len(all_tracks) > 10:
        print(f" ... and {len(all_tracks)-10} more")
    else:
        print()

    results = []
    tracks_info = []

    for meet in meets:
        course = meet["course"]
        course_lower = course.lower()
        if not any(t in course_lower or course_lower in t for t in TARGET_TRACKS):
            continue

        proper_races = provider.fetch_detailed_races(meet["meet_id"], date_str, course)
        if not proper_races:
            continue

        trials_count = 0
        for details in proper_races:
            # TAG TRIALS (collect but flag them)
            if is_trial(details):
                details['is_barrier_trial'] = True
                trials_count += 1

        results.append({
            "date": date_str,
            "meet_id": meet["meet_id"],
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

    provider = get_provider()

    print("\n" + "=" * 55)
    print(f"  DOWNLOAD RACECARDS (proper races only, source: {provider.name})")
    print("=" * 55)

    if not provider.has_credentials():
        print("  ERROR: provider credentials missing from environment")
        sys.exit(1)

    dates = [args.date] if args.date else [
        (datetime.now() + timedelta(days=i)).strftime('%Y-%m-%d')
        for i in range(args.days)
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    rejected = []

    for date_str in dates:
        data = fetch_racecards(provider, date_str)
        if data:
            # A card that fails the ingest contract must never reach disk —
            # downstream reads it blind, so a bad save poisons the pipeline.
            report = validate_meet_cards(data, date_str)
            report.print_report()
            if not report.ok:
                rejected.append(date_str)
            else:
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
    if rejected:
        print(f"  REJECTED {len(rejected)} day(s) on ingest validation: {', '.join(rejected)}")
    if not saved:
        print("  WARNING: No data downloaded")
        sys.exit(1)
    print()


if __name__ == '__main__':
    main()

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

# Exit codes. Everything but 0 is read by infra/jobs/handler.py, so these are
# a contract between the two files, not an implementation detail.
EXIT_NOTHING = 1            # nothing reached disk and something is wrong
EXIT_PARTIAL = 3            # saved, but the day is incomplete
EXIT_NO_TARGET_RACING = 4   # provider answered; no meeting at a covered track


def classify_exit(saved, rejected, partial, quiet, blank):
    """Turn the per-day tallies into the process exit code.

    The distinction 4 draws is the whole point of this function. A day on
    which the provider answered, named its meetings, and none of them was
    ours is a Monday — Australian Monday racing is country and provincial,
    and TARGET_TRACKS is metro. Folding that into 1 is what made the 05:30
    collect alarm on 2026-08-03 and take the 06:00, 07:00, 08:00 and 10:00
    jobs down with it, on a day that had nothing to collect.

    Only an all-quiet run earns 4. A rejected card, a listed target meeting
    that delivered no races, or an empty answer from the provider are real
    faults and keep the code they had.
    """
    if saved:
        return EXIT_PARTIAL if (rejected or partial) else 0
    if quiet and not blank and not rejected and not partial:
        return EXIT_NO_TARGET_RACING
    return EXIT_NOTHING


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
    """Fetch racecards for a date (proper races only).

    Returns (data, expected, missing, listed). `listed` is every meeting the
    provider named for the date, ours or not: main() needs it to tell "the
    feed said nothing" from "the feed said Lismore, Goulburn, Fannie Bay and
    Pakenham Synthetic", which are the same empty result and very different
    events.
    """
    print(f"\n  {date_str}:", end=" ", flush=True)

    meets = provider.fetch_meets(date_str)
    if not meets:
        print("no data")
        return None, [], [], []

    all_tracks = [m["course"] for m in meets]
    print(f"Available tracks: {', '.join(all_tracks[:10])}", end="")
    if len(all_tracks) > 10:
        print(f" ... and {len(all_tracks)-10} more")
    else:
        print()

    results = []
    tracks_info = []
    expected = []   # target meetings PF listed for the date
    missing = []    # target meetings that produced no races

    for meet in meets:
        course = meet["course"]
        course_lower = course.lower()
        if not any(t in course_lower or course_lower in t for t in TARGET_TRACKS):
            continue
        expected.append(course)

        proper_races = provider.fetch_detailed_races(meet["meet_id"], date_str, course)
        if not proper_races:
            # PF listed the meeting but delivered no races: that is partial
            # data, and partial must never be silent.
            missing.append(course)
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

    got = [m["course"] for m in results]
    print(f"  MEETINGS GOT ({len(got)}): {', '.join(got) or '-'}")
    if missing:
        print(f"  MEETINGS MISSING ({len(missing)}): {', '.join(missing)} "
              "- PF listed these but delivered no races", file=sys.stderr)

    return (results if results else None), expected, missing, all_tracks


def _self_test():
    """Assert the exit-code contract. handler.py branches on these numbers,
    so a change here that nobody notices takes the morning chain with it."""
    D, E = ["2026-08-03"], []
    cases = [
        # (saved, rejected, partial, quiet, blank), expected, label
        ((D, E, E, E, E), 0, "clean day"),
        ((D, E, D, E, E), EXIT_PARTIAL, "saved, but a meeting delivered nothing"),
        ((D, D, E, E, E), EXIT_PARTIAL, "saved one day, rejected another"),
        ((E, E, E, D, E), EXIT_NO_TARGET_RACING, "quiet day: no covered track"),
        ((E, E, E, E, D), EXIT_NOTHING, "provider named no meeting at all"),
        ((E, D, E, E, E), EXIT_NOTHING, "card rejected on validation"),
        ((E, E, D, E, E), EXIT_NOTHING, "target meeting delivered no races"),
        ((E, E, E, D, D), EXIT_NOTHING, "one quiet, one blank: not all quiet"),
        ((E, D, E, D, E), EXIT_NOTHING, "a rejection is never a quiet day"),
        ((E, E, D, D, E), EXIT_NOTHING, "a partial is never a quiet day"),
    ]
    for tallies, expected, label in cases:
        got = classify_exit(*tallies)
        assert got == expected, f"{label}: expected {expected}, got {got}"
    print(f"download_racecards self-test: {len(cases)} exit-code cases OK")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--self-test", action="store_true",
                        help="assert the exit-code contract and exit")
    args = parser.parse_args()

    if args.self_test:
        _self_test()
        return

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

    partial = []
    quiet = []    # provider listed meetings, none of them at a covered track
    blank = []    # provider named no meeting at all for the date
    for date_str in dates:
        data, expected, missing, listed = fetch_racecards(provider, date_str)
        if missing:
            partial.append(date_str)
        if not data:
            # Three different empty results. `partial` is already recorded
            # above via `missing` — a target meeting that delivered no races.
            if not listed:
                blank.append(date_str)
            elif not expected:
                quiet.append(date_str)
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
    if partial:
        print(f"  PARTIAL {len(partial)} day(s): a listed target meeting "
              f"delivered no races: {', '.join(partial)}", file=sys.stderr)
    if quiet:
        print(f"  NO TARGET RACING {len(quiet)} day(s): the provider listed "
              f"meetings, none at a covered track: {', '.join(quiet)}")
    if blank:
        print(f"  NO DATA {len(blank)} day(s): the provider named no meeting "
              f"at all: {', '.join(blank)}", file=sys.stderr)

    # Saved days stay on disk and are reported above; the non-zero exit is
    # what stops a scheduler from treating incomplete output as a green day.
    code = classify_exit(saved, rejected, partial, quiet, blank)
    if code == EXIT_NOTHING:
        print("  WARNING: No data downloaded")
    if code:
        sys.exit(code)
    print()


if __name__ == '__main__':
    main()

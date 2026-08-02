#!/usr/bin/env python3
"""Download racecards via the configured racing data provider (trials tagged, not excluded).

Exit codes
----------
0  a card was written, or the day was genuinely quiet (see below)
1  nothing usable: the provider errored, returned an empty calendar, or
   listed meetings that none of them survived the AUS country filter
2  config error — the resolved target-track list is empty
3  partial or rejected: a listed target meeting delivered no races, or a
   built card failed the ingest contract

On a quiet day
--------------
A quiet day is one where the provider answered normally and listed meetings,
but none of them are on TARGET_TRACKS. Australian racing is country- and
provincial-only on roughly a third of days: measured against
race_results_history, 28 of the 75 days to 2026-08-03 had zero target-track
racing, concentrated on Mondays, Tuesdays, Thursdays and Sundays. Treating
that as a failure meant the morning job went red about three times a week,
and every one of those reds looked exactly like a real outage.

So a quiet day exits 0 — and writes racecards/quiet_<date>.json naming every
meeting the provider DID list. The file is the point. A zero exit with nothing
on disk is the silent no-op class this repository already has a rule about;
a zero exit with a file saying "Punting Form listed Lismore, Goulburn, Fannie
Bay and Pakenham Synthetic, none of which are ours" is a claim you can check.
It is also the corpus that answers whether the target list should widen.

Quiet is not the same as empty, and the difference is not visible in the meets
list: an empty list means both "the upstream is down" and "there is no racing".
provider.discovery_stats() carries the pre-filter counts that separate them,
and a provider that does not report them is treated as an outage — the
conservative reading, because the other mistake is the silent one.
"""

import json
import os
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import time

import target_tracks as tt
from providers import get_provider, validate_meet_cards

# Resolved once per run in main(), from target_tracks.py — see that module for
# why the list moved and why STRIDE_TARGET_TRACKS exists. Bound here at import
# so the default holds for anything importing this module directly.
TARGET_TRACKS = list(tt.DEFAULT_TARGET_TRACKS)

OUTPUT_DIR = Path("./racecards")

# One day's outcome. Kept as flat constants rather than an enum so the value
# that reaches a log line is the word a reader wants to see.
SAVED = "saved"          # card built and written
QUIET = "quiet"          # provider healthy, meetings listed, none of them ours
NO_DATA = "no_data"      # provider returned nothing at all
DRIFT = "drift"          # provider listed meetings, none survived its own filters


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
    """One day's outcome as a dict: {"date", "state", ...}.

    Deliberately does not decide what an empty day means — that judgement
    needs the pre-filter counts, and lives in main() where the exit code is."""
    print(f"\n  {date_str}:", end=" ", flush=True)

    meets = provider.fetch_meets(date_str)
    stats = provider.discovery_stats() or {}

    if not meets:
        # A provider that reports no counts leaves us unable to tell an outage
        # from an empty calendar, so it gets the loud reading.
        if stats.get("raw"):
            print(f"none of {stats['raw']} listed meetings usable")
            return {"date": date_str, "state": DRIFT, "stats": stats}
        print("no data")
        return {"date": date_str, "state": NO_DATA, "stats": stats}

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
        if not (tt.collect_all()
                or any(t in course_lower or course_lower in t
                       for t in TARGET_TRACKS)):
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

    if not expected:
        # Meetings exist, none of them are ours. Not a failure — see the
        # module docstring. `expected` is the right test rather than `results`:
        # a target meeting that delivered no races is a partial day, which is
        # a different (and genuinely bad) thing.
        return {"date": date_str, "state": QUIET, "stats": stats}

    return {"date": date_str, "state": SAVED, "stats": stats,
            "data": results, "expected": expected, "missing": missing}


def write_quiet_sentinel(date_str, stats, provider_name):
    """Evidence for a zero.

    A quiet day exits 0, so without a file on disk it is indistinguishable from
    a job that ran and did nothing — the silent no-op class. This records every
    meeting the provider listed and why none were taken, which makes the zero
    checkable and doubles as the corpus for deciding whether TARGET_TRACKS
    should widen.

    The filename is quiet_<date>.json and must not become racecard_quiet_*:
    blackbook_candidates.py globs racecard_*.json and would try to read this
    as a list of meet cards.
    """
    payload = {
        "date": date_str,
        "status": "quiet",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": provider_name,
        "meetings_listed": stats.get("raw"),
        "meetings_after_country_filter": stats.get("country_kept"),
        "meetings_matched": 0,
        "target_count": len(TARGET_TRACKS),
        "target_source": tt.target_source(),
        "listed": stats.get("listed") or [],
    }
    path = OUTPUT_DIR / f"quiet_{date_str}.json"
    try:
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    except OSError as e:
        print(f"  FATAL: could not write the quiet-day sentinel {path}: {e}\n"
              "  A quiet day with no evidence on disk cannot be told apart "
              "from a silent no-op, so this exits 1 rather than 0.",
              file=sys.stderr)
        sys.exit(1)
    return path


def main():
    global TARGET_TRACKS
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    # Resolved here rather than at import so an operator can widen coverage by
    # editing the AWS secret alone — no code change, no deploy, no rebuild.
    TARGET_TRACKS = tt.target_tracks()

    provider = get_provider()

    print("\n" + "=" * 55)
    print(f"  DOWNLOAD RACECARDS (proper races only, source: {provider.name})")
    if tt.collect_all():
        print(f"  TARGET TRACKS: ALL ({tt.ENV_VAR}) — no track filter")
    else:
        print(f"  TARGET TRACKS: {len(TARGET_TRACKS)} from {tt.target_source()}")
    print("=" * 55)

    if not provider.has_credentials():
        print("  ERROR: provider credentials missing from environment")
        sys.exit(1)

    if not TARGET_TRACKS and not tt.collect_all():
        # An empty target list makes every single day look quiet, which would
        # take the whole morning green while producing nothing at all. Never a
        # quiet day; always a config error. Only reachable via a malformed
        # override, since the built-in list is never empty.
        print(f"  CONFIG ERROR: {tt.ENV_VAR} is set but parsed to zero tracks "
              f"({os.environ.get(tt.ENV_VAR)!r}). Every day would look quiet.",
              file=sys.stderr)
        sys.exit(2)

    dates = [args.date] if args.date else [
        (datetime.now() + timedelta(days=i)).strftime('%Y-%m-%d')
        for i in range(args.days)
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    rejected = []
    quiet = []
    hard = []

    partial = []
    for date_str in dates:
        outcome = fetch_racecards(provider, date_str)
        state = outcome["state"]

        if state in (NO_DATA, DRIFT):
            hard.append(outcome)
        elif state == QUIET:
            write_quiet_sentinel(date_str, outcome["stats"], provider.name)
            quiet.append(date_str)
        else:
            if outcome["missing"]:
                partial.append(date_str)
            data = outcome["data"]
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
    if quiet:
        print(f"  QUIET {len(quiet)} day(s): the provider listed meetings but "
              f"none are on the target-track list: {', '.join(quiet)}")
        print(f"  Evidence: {OUTPUT_DIR}/quiet_<date>.json names what did run")
    if rejected:
        print(f"  REJECTED {len(rejected)} day(s) on ingest validation: {', '.join(rejected)}")
    if partial:
        print(f"  PARTIAL {len(partial)} day(s): a listed target meeting "
              f"delivered no races: {', '.join(partial)}", file=sys.stderr)

    # Precedence: any hard failure outranks any partial, which outranks
    # success. Previously the exit was decided by `saved` alone, so a run
    # covering several days reported green whenever one of them worked — a
    # dead provider on day one was invisible if day two happened to save.
    if hard:
        for o in hard:
            s = o["stats"]
            if o["state"] == DRIFT and s.get("country_kept") == 0:
                print(f"  FAILED {o['date']}: the provider listed "
                      f"{s.get('raw')} meetings and none carried "
                      f"country=='AUS' ({s.get('country_missing')} had no "
                      "country field at all). That is the feed's country "
                      "coding changing shape, not a day without racing.",
                      file=sys.stderr)
            elif o["state"] == DRIFT:
                print(f"  FAILED {o['date']}: {s.get('country_kept')} AUS "
                      f"meetings listed but none carried both an id and a "
                      "track name, so none could be fetched.", file=sys.stderr)
            else:
                print(f"  FAILED {o['date']}: the provider returned nothing "
                      f"({s.get('error') or 'empty calendar'}). There is "
                      "racing in Australia every day, so an empty calendar is "
                      "an outage, not a quiet day.", file=sys.stderr)
        print("  WARNING: No data downloaded")
        sys.exit(1)
    if rejected or partial:
        # Saved days stay on disk and are reported above; the non-zero exit
        # is what stops a scheduler from treating partial output as a green
        # day. 3 = partial/rejected, distinct from 1 = nothing at all.
        sys.exit(3)
    if not saved and not quiet:
        # Defensive: every state above is accounted for, so reaching here
        # means a new one was added without an exit rule.
        print("  WARNING: No data downloaded")
        sys.exit(1)
    print()


if __name__ == '__main__':
    main()

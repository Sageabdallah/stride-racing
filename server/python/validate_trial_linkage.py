#!/usr/bin/env python3
"""Read-only audit — queries race_results_history to measure what % of trial runners can be joined to actual race records, and reports field coverage."""

import json
import os
import sys
import csv
import argparse
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"

def _load_env(path):
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env

_env = _load_env(ENV_PATH)
DATABASE_URL = _env.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
for _key, _value in _env.items():
    os.environ.setdefault(_key, _value)

import psycopg2

TRIALS_DIR = Path(__file__).resolve().parent.parent.parent / "barrier_trials"


def load_trial_runners(min_date=None):
    """
    Read all trials_*.json files and return a flat list of runner records.
    Each record: {horse_id, horse_name, trial_date, course, position, race_name, winning_time, margin, distance, going}
    Also returns field coverage counts.
    """
    if not TRIALS_DIR.exists():
        print(f"ERROR: barrier_trials/ directory not found at {TRIALS_DIR}", file=sys.stderr)
        sys.exit(1)

    files = sorted(TRIALS_DIR.glob("trials_*.json"))

    if min_date:
        min_dt = datetime.strptime(min_date, "%Y-%m-%d").date()
        files = [
            f for f in files
            if datetime.strptime(f.stem.replace("trials_", ""), "%Y-%m-%d").date() >= min_dt
        ]

    if not files:
        print("No trial files found matching criteria.", file=sys.stderr)
        sys.exit(1)

    runners = []
    total_races = 0
    total_meets = 0

    coverage = defaultdict(int)
    coverage_fields = ["winning_time", "margin", "distance", "going",
                       "horse_id", "horse", "position", "trainer",
                       "trainer_id", "jockey", "jockey_id", "form",
                       "draw", "weight", "sp"]

    for path in files:
        try:
            with open(path) as f:
                meets = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        total_meets += len(meets)
        for meet in meets:
            for race in meet.get("races", []):
                total_races += 1
                winning_time = race.get("winning_time")
                distance = race.get("distance")
                going = race.get("going")
                race_name = race.get("race_name", "")
                trial_date_str = race.get("date") or meet.get("date")
                course = race.get("course") or meet.get("course", "")

                for runner in race.get("runners", []):
                    if runner.get("scratched"):
                        continue

                    horse_id = runner.get("horse_id")
                    horse_name = runner.get("horse") or runner.get("horse_name")
                    if not horse_id or not horse_name:
                        continue

                    for field in coverage_fields:
                        val = runner.get(field)
                        if val is not None and val != [] and val != "":
                            coverage[field] += 1
                    if winning_time:
                        coverage["winning_time"] += 1
                    if distance:
                        coverage["distance"] += 1
                    if going:
                        coverage["going"] += 1

                    runners.append({
                        "horse_id": horse_id,
                        "horse_name": horse_name,
                        "trial_date": trial_date_str,
                        "course": course,
                        "position": runner.get("position"),
                        "race_name": race_name,
                        "winning_time": winning_time,
                        "margin": runner.get("margin"),
                        "distance": distance,
                        "going": going,
                    })

    return runners, total_meets, total_races, coverage, len(files)



def query_linked_horses(horse_ids):
    """
    Batch query: return all (horse_id, race_date) rows from race_results_history for the given set of horse_ids.
    Per-horse 90-day window filtering happens in Python after this.
    """
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT horse_id, race_date FROM race_results_history WHERE horse_id = ANY(%s)",
            (list(horse_ids),)
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    result = defaultdict(list)
    for horse_id, race_date in rows:
        result[horse_id].append(race_date)
    return result



def compute_linkage(runners, rrh_map):
    """
    For each runner, check if a race exists in race_results_history within 0-90 days after the trial date.
    Returns: linked count, gap_buckets {label: count}, per-horse linkage set
    """
    linked = 0
    unlinked = 0
    gap_buckets = {"0-14": 0, "15-30": 0, "31-60": 0, "61-90": 0}
    linked_horse_ids = set()

    for r in runners:
        horse_id = r["horse_id"]
        trial_date_str = r["trial_date"]
        if not trial_date_str:
            unlinked += 1
            continue

        try:
            trial_date = datetime.strptime(trial_date_str, "%Y-%m-%d").date()
        except ValueError:
            unlinked += 1
            continue

        race_dates = rrh_map.get(horse_id, [])
        matched = False
        for race_date in race_dates:
            # race_date may be a str or date
            if isinstance(race_date, str):
                try:
                    race_date = datetime.strptime(race_date, "%Y-%m-%d").date()
                except ValueError:
                    continue

            gap = (race_date - trial_date).days
            if 0 < gap <= 90:
                matched = True
                linked_horse_ids.add(horse_id)
                if gap <= 14:
                    gap_buckets["0-14"] += 1
                elif gap <= 30:
                    gap_buckets["15-30"] += 1
                elif gap <= 60:
                    gap_buckets["31-60"] += 1
                else:
                    gap_buckets["61-90"] += 1
                break

        if matched:
            linked += 1
        else:
            unlinked += 1

    return linked, unlinked, gap_buckets, linked_horse_ids



def print_report(num_files, total_meets, total_races, total_runners,
                 linked, unlinked, gap_buckets, coverage, output_csv=None):
    total = linked + unlinked
    link_pct = (linked / total * 100) if total else 0

    print()
    print("Trial Linkage Audit")
    print("=" * 50)
    print(f"  Trial files loaded:     {num_files}")
    print(f"  Trial meetings:         {total_meets}")
    print(f"  Trial races:            {total_races}")
    print(f"  Non-scratched runners:  {total_runners}")
    print()
    print("Linkage (horse in race_results_history within 90 days of trial):")
    print(f"  Linked:    {linked:,}  ({link_pct:.1f}%)")
    print(f"  Unlinked:  {unlinked:,}  ({100-link_pct:.1f}%)")
    if link_pct < 85:
        print()
        print("  WARNING: Linkage < 85% — check race_results_history coverage")
        print("  for the same date range before proceeding to feature engineering.")
    print()
    print("Gap distribution (trial → next race):")
    gap_total = sum(gap_buckets.values())
    for label, count in gap_buckets.items():
        pct = (count / gap_total * 100) if gap_total else 0
        bar = "█" * int(pct / 2)
        print(f"  {label} days:  {count:,}  ({pct:.1f}%)  {bar}")
    print()
    print("Trial field coverage (across non-scratched runners):")
    coverage_order = [
        ("horse_id",    "← join key, must be 100%"),
        ("horse",       "← join fallback"),
        ("position",    "← primary feature"),
        ("trainer",     "← trainer_trial_pattern"),
        ("trainer_id",  ""),
        ("jockey",      ""),
        ("jockey_id",   ""),
        ("form",        "← prior race form string"),
        ("winning_time","← speed signal"),
        ("margin",      "← ~50-80% expected"),
        ("distance",    ""),
        ("going",       ""),
        ("draw",        "← confirmed sparse, skip"),
        ("weight",      "← confirmed sparse, skip"),
        ("sp",          "← confirmed sparse, skip"),
    ]
    for field, note in coverage_order:
        count = coverage.get(field, 0)
        pct = (count / total_runners * 100) if total_runners else 0
        note_str = f"  {note}" if note else ""
        print(f"  {field:<16}  {pct:5.1f}%{note_str}")
    print()

    if output_csv:
        rows = [
            ["metric", "value"],
            ["trial_files", num_files],
            ["trial_meets", total_meets],
            ["trial_races", total_races],
            ["trial_runners", total_runners],
            ["linked", linked],
            ["unlinked", unlinked],
            ["linkage_pct", f"{link_pct:.1f}"],
        ]
        for label, count in gap_buckets.items():
            rows.append([f"gap_{label.replace('-','_')}_days", count])
        for field, _ in coverage_order:
            rows.append([f"coverage_{field}", f"{(coverage.get(field,0)/total_runners*100):.1f}" if total_runners else "0"])
        with open(output_csv, "w", newline="") as f:
            csv.writer(f).writerows(rows)
        print(f"  CSV written: {output_csv}")
        print()



def main():
    parser = argparse.ArgumentParser(description="Validate barrier trial → race_results_history linkage")
    parser.add_argument("--min-date", help="Only load trial files on or after this date (YYYY-MM-DD)")
    parser.add_argument("--output-csv", help="Write summary report to CSV file")
    args = parser.parse_args()

    print("Loading trial files...", end=" ", flush=True)
    runners, total_meets, total_races, coverage, num_files = load_trial_runners(args.min_date)
    print(f"{len(runners):,} non-scratched runners from {num_files} files")

    horse_ids = {r["horse_id"] for r in runners}
    print(f"Querying race_results_history for {len(horse_ids):,} unique horses...", end=" ", flush=True)
    rrh_map = query_linked_horses(horse_ids)
    print(f"{len(rrh_map):,} found in DB")

    linked, unlinked, gap_buckets, _ = compute_linkage(runners, rrh_map)

    print_report(
        num_files, total_meets, total_races, len(runners),
        linked, unlinked, gap_buckets, coverage,
        output_csv=args.output_csv
    )


if __name__ == "__main__":
    main()

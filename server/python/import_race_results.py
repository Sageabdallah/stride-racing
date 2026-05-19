#!/usr/bin/env python3
import os
import sys
import json
import glob
import re
import psycopg2
from psycopg2.extras import execute_values

DATABASE_URL = os.environ.get("DATABASE_URL")

def parse_distance(distance_str):
    if not distance_str:
        return None
    match = re.search(r'(\d+)', distance_str)
    return int(match.group(1)) if match else None

def parse_class_level(race_class):
    if not race_class:
        return 1
    rc = race_class.upper()
    if "GROUP 1" in rc or "GROUP1" in rc or rc == "G1" or " G1" in rc:
        return 10
    if "GROUP 2" in rc or "GROUP2" in rc or rc == "G2" or " G2" in rc:
        return 9
    if "GROUP 3" in rc or "GROUP3" in rc or rc == "G3" or " G3" in rc:
        return 8
    if "LISTED" in rc:
        return 7
    bm_match = re.search(r'BM\s*(\d+)', rc)
    if bm_match:
        bm_num = int(bm_match.group(1))
        if bm_num >= 88:
            return 6
        if bm_num >= 70:
            return 5
        if bm_num >= 58:
            return 4
        if bm_num >= 50:
            return 3
        return 2
    if "MAIDEN" in rc:
        return 2
    return 1

def parse_position(pos):
    if pos is None:
        return None
    try:
        return int(pos)
    except (ValueError, TypeError):
        return None

def parse_margin(margin):
    if margin is None:
        return None
    if isinstance(margin, (int, float)):
        return float(margin)
    m = str(margin).replace("L", "").replace("l", "").strip()
    try:
        return float(m)
    except (ValueError, TypeError):
        return None

def parse_weight(weight):
    if weight is None:
        return None
    if isinstance(weight, (int, float)):
        return float(weight)
    m = re.search(r'([\d.]+)', str(weight))
    return float(m.group(1)) if m else None

def parse_int_safe(val):
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None

def parse_float_safe(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL environment variable not set")
        sys.exit(1)

    json_files = sorted(glob.glob("historical_data/track_imports/*.json"))
    if not json_files:
        print("No JSON files found in historical_data/track_imports/")
        sys.exit(1)

    print(f"Found {len(json_files)} JSON files to import")
    for f in json_files:
        print(f"  - {f}")

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    print("\nTRUNCATING race_results_history table...")
    cur.execute("TRUNCATE TABLE race_results_history")
    conn.commit()

    print("Dropping existing indexes (if any)...")
    for idx in ["idx_rrh_horse_id", "idx_rrh_race_id", "idx_rrh_race_date", "idx_rrh_track"]:
        cur.execute(f"DROP INDEX IF EXISTS {idx}")
    conn.commit()

    total_rows = 0
    total_skipped = 0
    total_meetings = 0
    total_races = 0
    batch = []
    BATCH_SIZE = 500

    columns = (
        "horse_id", "horse_name", "race_id", "track", "race_date",
        "distance_m", "race_class", "class_level", "going", "position",
        "margin_lengths", "weight_kg", "jockey", "jockey_id", "barrier",
        "sp_odds", "field_size", "race_name", "race_number", "form_string",
        "opponents_json"
    )

    insert_sql = f"""
        INSERT INTO race_results_history ({', '.join(columns)})
        VALUES %s
    """

    def flush_batch():
        nonlocal batch, total_rows
        if not batch:
            return
        execute_values(cur, insert_sql, batch, page_size=BATCH_SIZE)
        conn.commit()
        total_rows += len(batch)
        batch = []

    for filepath in json_files:
        filename = os.path.basename(filepath)
        print(f"\nProcessing {filename}...")

        with open(filepath, "r") as f:
            meetings = json.load(f)

        file_rows = 0
        for meeting in meetings:
            total_meetings += 1
            meet_id = meeting.get("meet_id", "unknown")
            races = meeting.get("races", [])

            for race_idx, race in enumerate(races):
                total_races += 1
                race_number_raw = race.get("race_number")
                race_number = parse_int_safe(race_number_raw) if race_number_raw else (race_idx + 1)
                race_id = f"{meet_id}_{race_number}"
                track = race.get("course", meeting.get("course", "Unknown"))
                race_date = race.get("date", meeting.get("date", ""))
                distance_m = parse_distance(race.get("distance"))
                race_class = race.get("class", "")
                class_level = parse_class_level(race_class)
                going = race.get("going")
                race_name = race.get("race_name")

                runners = race.get("runners", [])
                active_runners = [r for r in runners if not r.get("scratched", False)]
                field_size = len(active_runners)

                opponent_data = []
                for r in active_runners:
                    opponent_data.append({
                        "horse_id": r.get("horse_id"),
                        "horse_name": r.get("horse"),
                        "position": parse_position(r.get("position")),
                        "margin": parse_margin(r.get("margin"))
                    })

                for runner in runners:
                    if runner.get("scratched", False):
                        total_skipped += 1
                        continue

                    horse_id = runner.get("horse_id")
                    if not horse_id:
                        total_skipped += 1
                        continue

                    horse_name = runner.get("horse", "")
                    opponents = [o for o in opponent_data if o["horse_id"] != horse_id]

                    row = (
                        horse_id,
                        horse_name,
                        race_id,
                        track,
                        race_date,
                        distance_m,
                        race_class,
                        class_level,
                        going,
                        parse_position(runner.get("position")),
                        parse_margin(runner.get("margin")),
                        parse_weight(runner.get("weight")),
                        runner.get("jockey"),
                        runner.get("jockey_id"),
                        parse_int_safe(runner.get("draw")),
                        parse_float_safe(runner.get("sp")),
                        field_size,
                        race_name,
                        race_number,
                        runner.get("form"),
                        json.dumps(opponents)
                    )

                    batch.append(row)
                    file_rows += 1

                    if len(batch) >= BATCH_SIZE:
                        flush_batch()
                        print(f"  Inserted {total_rows} rows so far...")

        flush_batch()
        print(f"  {filename}: {file_rows} runners imported")

    flush_batch()

    print("\nCreating indexes...")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rrh_horse_id ON race_results_history (horse_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rrh_race_id ON race_results_history (race_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rrh_race_date ON race_results_history (race_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rrh_track ON race_results_history (track)")
    conn.commit()

    print("\n" + "=" * 50)
    print("IMPORT COMPLETE")
    print("=" * 50)
    print(f"Files processed:    {len(json_files)}")
    print(f"Total meetings:     {total_meetings}")
    print(f"Total races:        {total_races}")
    print(f"Total rows inserted:{total_rows}")
    print(f"Skipped (scratched/no ID): {total_skipped}")
    print("=" * 50)

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()

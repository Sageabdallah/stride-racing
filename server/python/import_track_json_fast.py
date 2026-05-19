#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from import_track_json import process_file  # noqa: E402


INSERT_SQL = """
INSERT INTO training_data (
    race_id, track, race_number, race_date, distance, going, race_class,
    horse_name, horse_number, barrier, jockey, trainer, weight,
    predicted_win_prob, predicted_place_prob, predicted_position,
    confidence, expected_value, market_odds,
    actual_position, won, placed, starting_price
) VALUES %s
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fast bulk import of track JSON files into training_data.")
    parser.add_argument("--dir", type=str, default="historical_data/track_imports")
    parser.add_argument("--batch-size", type=int, default=1000)
    return parser.parse_args()


def record_key(record: dict) -> tuple[str, int, str, str]:
    return (
        str(record["race_date"]),
        int(record["race_number"]),
        str(record["track"]).strip().lower(),
        str(record["horse_name"]).strip().lower(),
    )


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL not set")

    files = sorted(glob.glob(os.path.join(args.dir, "*.json")))
    if not files:
        raise SystemExit(f"No JSON files found in {args.dir}")

    all_records: list[dict] = []
    for file in files:
        print(f"[FastImport] Processing {os.path.basename(file)}")
        all_records.extend(process_file(file))

    print(f"[FastImport] Parsed records: {len(all_records)}")

    deduped: dict[tuple[str, int, str, str], dict] = {}
    for record in all_records:
        deduped[record_key(record)] = record
    deduped_records = list(deduped.values())
    print(f"[FastImport] Deduped records: {len(deduped_records)}")

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT race_date, race_number, lower(track), lower(horse_name)
                FROM training_data
                """
            )
            existing = {(row[0], int(row[1]), row[2], row[3]) for row in cur.fetchall()}

        new_records = [record for record in deduped_records if record_key(record) not in existing]
        print(f"[FastImport] New records to insert: {len(new_records)}")
        if not new_records:
            print("[FastImport] Nothing to insert.")
            return 0

        rows = []
        for record in new_records:
            rows.append(
                (
                    record.get("race_id", ""),
                    record["track"],
                    record["race_number"],
                    record["race_date"],
                    record.get("distance", ""),
                    record.get("going", ""),
                    record.get("race_class", ""),
                    record["horse_name"],
                    record.get("horse_number", ""),
                    record.get("barrier"),
                    record.get("jockey", ""),
                    record.get("trainer", ""),
                    record.get("weight"),
                    record.get("predicted_win_prob"),
                    record.get("predicted_place_prob"),
                    record.get("predicted_position"),
                    record.get("confidence", "low"),
                    record.get("expected_value"),
                    record.get("market_odds"),
                    record.get("actual_position"),
                    record.get("won", 0),
                    record.get("placed", 0),
                    record.get("starting_price"),
                )
            )

        inserted = 0
        with conn.cursor() as cur:
            for start in range(0, len(rows), args.batch_size):
                chunk = rows[start : start + args.batch_size]
                execute_values(cur, INSERT_SQL, chunk, page_size=args.batch_size)
                inserted += len(chunk)
                print(f"[FastImport] Inserted {inserted}/{len(rows)}")
            conn.commit()
        print(f"[FastImport] Complete. Inserted {inserted} rows.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

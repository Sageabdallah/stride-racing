#!/usr/bin/env python3
"""Reads all barrier_trials/trials_*.json files and inserts non-scratched runners into the barrier_trial_results table. Idempotent — safe to re-run."""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.normpath(os.path.join(SCRIPT_DIR, os.pardir, os.pardir, ".env"))
# A .env outside the repo, for a checkout whose root has none. This used to be
# a hardcoded path to one dev machine's Windows checkout, which resolved on no
# other machine — including the Mac it was meant for.
EXPLICIT_ENV = os.environ.get("STRIDE_ENV_FILE", "")

def _load_env(path):
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env

_env = _load_env(ENV_PATH)
if EXPLICIT_ENV and not _env.get("DATABASE_URL"):
    _env = _load_env(EXPLICIT_ENV)
DATABASE_URL = _env.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
for _key, _value in _env.items():
    os.environ.setdefault(_key, _value)

import psycopg2
from psycopg2.extras import execute_values

TRIALS_DIR = Path(SCRIPT_DIR).parent.parent / "barrier_trials"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS barrier_trial_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    horse_id        TEXT NOT NULL,
    horse_name      TEXT NOT NULL,
    trainer_id      TEXT,
    trainer         TEXT,
    jockey_id       TEXT,
    jockey          TEXT,
    trial_date      TEXT NOT NULL,
    course          TEXT NOT NULL,
    race_name       TEXT,
    race_number     INTEGER,
    distance        TEXT,
    going           TEXT,
    position        INTEGER,
    field_size      INTEGER,
    margin          REAL,
    winning_time    TEXT,
    is_jump_out     BOOLEAN NOT NULL DEFAULT FALSE,
    meet_id         TEXT,
    collected_at    TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (horse_id, trial_date, course, race_name)
);
CREATE INDEX IF NOT EXISTS idx_btr_horse_id   ON barrier_trial_results (horse_id);
CREATE INDEX IF NOT EXISTS idx_btr_trial_date ON barrier_trial_results (trial_date);
CREATE INDEX IF NOT EXISTS idx_btr_trainer_id ON barrier_trial_results (trainer_id);
"""

INSERT_SQL = """
INSERT INTO barrier_trial_results (
    horse_id, horse_name, trainer_id, trainer, jockey_id, jockey,
    trial_date, course, race_name, race_number, distance, going,
    position, field_size, margin, winning_time, is_jump_out, meet_id, collected_at
) VALUES %s
ON CONFLICT (horse_id, trial_date, course, race_name) DO NOTHING
"""


def parse_margin(val):
    """Parse margin values like '0.1L', '1.5', None → float or None."""
    if val is None:
        return None
    try:
        return float(str(val).rstrip("LlNnHhSs").strip())
    except (ValueError, TypeError):
        return None


def parse_position(val):
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def parse_race_number(val):
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def load_rows(min_date=None):
    files = sorted(TRIALS_DIR.glob("trials_*.json"))

    if min_date:
        min_dt = datetime.strptime(min_date, "%Y-%m-%d").date()
        files = [
            f for f in files
            if datetime.strptime(f.stem.replace("trials_", ""), "%Y-%m-%d").date() >= min_dt
        ]

    rows = []
    files_processed = 0
    scratched_skipped = 0

    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                meets = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [WARN] Could not read {path.name}: {e}", file=sys.stderr)
            continue

        files_processed += 1

        for meet in meets:
            meet_id = meet.get("meet_id", "")
            meet_course = meet.get("course", "")
            meet_date = meet.get("date", "")

            for race in meet.get("races", []):
                trial_date = race.get("date") or meet_date
                course = race.get("course") or meet_course
                race_name = race.get("race_name")
                race_number = parse_race_number(race.get("race_number"))
                distance = race.get("distance")
                going = race.get("going")
                winning_time = race.get("winning_time")
                is_jump_out = bool(race.get("is_jump_out", False))
                collected_at = race.get("collected_at")

                active_runners = [r for r in race.get("runners", []) if not r.get("scratched")]
                field_size = len(active_runners)

                for runner in active_runners:
                    horse_id = runner.get("horse_id")
                    horse_name = runner.get("horse")  # field is "horse", not "horse_name"
                    if not horse_id or not horse_name:
                        scratched_skipped += 1
                        continue

                    rows.append((
                        horse_id,
                        horse_name,
                        runner.get("trainer_id"),
                        runner.get("trainer"),
                        runner.get("jockey_id"),
                        runner.get("jockey"),
                        trial_date,
                        course,
                        race_name,
                        race_number,
                        distance,
                        going,
                        parse_position(runner.get("position")),
                        field_size,
                        parse_margin(runner.get("margin")),
                        winning_time,
                        is_jump_out,
                        meet_id,
                        collected_at,
                    ))

    return rows, files_processed, scratched_skipped


def main():
    parser = argparse.ArgumentParser(description="Import barrier trial results into DB")
    parser.add_argument("--min-date", help="Only import files on or after this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Count rows without writing to DB")
    args = parser.parse_args()

    if not TRIALS_DIR.exists():
        print(f"ERROR: barrier_trials/ not found at {TRIALS_DIR}", file=sys.stderr)
        sys.exit(1)

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    print("Loading trial JSON files...", end=" ", flush=True)
    rows, files_processed, scratched_skipped = load_rows(args.min_date)
    print(f"{len(rows):,} rows from {files_processed} files")

    if args.dry_run:
        print()
        print("DRY RUN — nothing written.")
        print(f"  Files processed:  {files_processed}")
        print(f"  Rows to insert:   {len(rows):,}")
        print(f"  Skipped (no id):  {scratched_skipped:,}")
        return

    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()

        cur.execute(CREATE_TABLE_SQL)
        conn.commit()

        BATCH = 5000
        total_inserted = 0
        total_skipped = 0

        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            execute_values(cur, INSERT_SQL, batch, page_size=BATCH)
            inserted = cur.rowcount if cur.rowcount >= 0 else 0
            skipped = len(batch) - inserted
            total_inserted += inserted
            total_skipped += skipped
            conn.commit()

        conn.commit()
    finally:
        conn.close()

    print()
    print(f"  Files processed:  {files_processed}")
    print(f"  Rows inserted:    {total_inserted:,}")
    print(f"  Rows skipped:     {total_skipped + scratched_skipped:,}  (conflicts or missing id)")
    print()


if __name__ == "__main__":
    main()

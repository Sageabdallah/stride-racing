#!/usr/bin/env python3
"""
Backfills Phase 2 biomechanical columns on all existing rows in the
sectional_times table by iterating over every distinct (track, race_date) pair.

Phase 2 columns populated:
  - lambda_decay          (Ward-Smith 1985 velocity decay constant)
  - svi                   (Sustained Velocity Index)
  - rsi                   (Race Shape Index)
  - trip_cost_seconds     (Thorograph wide-barrier time cost)
  - variant_adjusted_200m (track-variant-adjusted last-200m speed)
  - variant_adjusted_400m
  - variant_adjusted_600m
  - variant_adjusted_800m

This script is idempotent: every UPDATE overwrites existing values, so it is
safe to re-run without duplicating data.
"""

import os
import sys
import time
import psycopg2
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from sectional_times_collector import (
    compute_phase2_primitives,
    compute_and_write_daily_variant,
)

ENV_PATH = os.path.join(
    os.path.dirname(SCRIPT_DIR),
    os.pardir,
    ".env",
)
ENV_PATH = os.path.normpath(ENV_PATH)

# A .env outside the repo, for a checkout whose root has none. This used to be
# a hardcoded path to one dev machine's Windows checkout, which resolved on no
# other machine — including the Mac it was meant for.
EXPLICIT_ENV = os.environ.get("STRIDE_ENV_FILE", "")


def load_env(path):
    """Parse a simple KEY=VALUE .env file and return a dict."""
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


env_vars = load_env(ENV_PATH)
if EXPLICIT_ENV and not env_vars.get("DATABASE_URL"):
    env_vars = load_env(EXPLICIT_ENV)

DATABASE_URL = env_vars.get("DATABASE_URL") or os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    print(
        "ERROR: DATABASE_URL not found. Checked "
        f"{ENV_PATH}{' and ' + EXPLICIT_ENV if EXPLICIT_ENV else ''} "
        "(set STRIDE_ENV_FILE to point at a .env elsewhere).",
        file=sys.stderr,
    )
    sys.exit(1)


LOG_EVERY = 50


def get_distinct_pairs(db_url):
    """Return all distinct (track, race_date) pairs from sectional_times, newest first."""
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT track, race_date
        FROM sectional_times
        WHERE track IS NOT NULL
          AND race_date IS NOT NULL
        GROUP BY track, race_date
        ORDER BY race_date DESC, track
        """
    )
    pairs = cur.fetchall()
    cur.close()
    conn.close()
    return pairs


def get_most_common_going(db_url, track, race_date):
    """
    Return the most common track_config value for the given (track, race_date).
    Falls back to 'Good' when no non-null track_config exists.
    """
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT track_config, COUNT(*) AS cnt
        FROM sectional_times
        WHERE track = %s
          AND race_date = %s
          AND track_config IS NOT NULL
          AND track_config <> ''
        GROUP BY track_config
        ORDER BY cnt DESC
        LIMIT 1
        """,
        (track, race_date),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else "Good"


def count_phase2_populated(db_url):
    """Return the number of rows that have lambda_decay populated (as a proxy for Phase 2 being done)."""
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM sectional_times WHERE lambda_decay IS NOT NULL"
    )
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count



def main():
    start_time = datetime.utcnow()
    print("=" * 65)
    print("  PHASE 2 BIOMECHANICAL BACKFILL")
    print("=" * 65)
    print(f"  Started : {start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  DB      : {DATABASE_URL[:50]}...")
    print()

    pre_count = count_phase2_populated(DATABASE_URL)
    print(f"  Rows with lambda_decay before backfill : {pre_count}")

    pairs = get_distinct_pairs(DATABASE_URL)
    total_pairs = len(pairs)
    print(f"  Distinct (track, race_date) pairs found: {total_pairs}")
    print()

    if total_pairs == 0:
        print("  Nothing to process — sectional_times table appears empty.")
        return

    processed = 0
    errors = 0

    for idx, (track, race_date) in enumerate(pairs, start=1):
        if hasattr(race_date, "strftime"):
            race_date_str = race_date.strftime("%Y-%m-%d")
        else:
            race_date_str = str(race_date)

        try:
            compute_phase2_primitives(DATABASE_URL, track, race_date_str)

            going = get_most_common_going(DATABASE_URL, track, race_date_str)
            compute_and_write_daily_variant(DATABASE_URL, track, race_date_str, going)

            processed += 1

        except Exception as exc:
            errors += 1
            print(
                f"  [ERROR] ({idx}/{total_pairs}) {track} | {race_date_str} : {exc}",
                file=sys.stderr,
            )

        if idx % LOG_EVERY == 0:
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            rate = idx / elapsed if elapsed > 0 else 0
            eta_secs = (total_pairs - idx) / rate if rate > 0 else 0
            print(
                f"  Progress: {idx:>5}/{total_pairs}  "
                f"processed={processed}  errors={errors}  "
                f"elapsed={elapsed:.0f}s  ETA={eta_secs:.0f}s"
            )

    end_time = datetime.utcnow()
    elapsed_total = (end_time - start_time).total_seconds()
    post_count = count_phase2_populated(DATABASE_URL)

    print()
    print("=" * 65)
    print("  BACKFILL COMPLETE")
    print("=" * 65)
    print(f"  Finished  : {end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  Duration  : {elapsed_total:.1f}s")
    print()
    print(f"  (track, date) pairs total     : {total_pairs}")
    print(f"  (track, date) pairs processed : {processed}")
    print(f"  (track, date) pairs errored   : {errors}")
    print()
    print(f"  Rows with lambda_decay BEFORE : {pre_count}")
    print(f"  Rows with lambda_decay AFTER  : {post_count}")
    print(f"  Net rows updated (approx)     : {post_count - pre_count}")
    print()


if __name__ == "__main__":
    main()

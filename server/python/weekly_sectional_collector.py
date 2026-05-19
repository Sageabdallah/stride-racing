#!/usr/bin/env python3
"""
Weekly sectional times collector: runs all 3 state sectional collectors in sequence.
Designed to run every Sunday evening.
"""

import os
import sys
import subprocess
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run_collector(name, cmd):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    try:
        result = subprocess.run(
            cmd,
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = result.stdout + result.stderr
        lines = output.strip().split('\n')
        for line in lines[-20:]:
            print(f"  {line}")
        if result.returncode != 0:
            print(f"  WARNING: {name} exited with code {result.returncode}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  ERROR: {name} timed out after 10 minutes")
        return False
    except Exception as e:
        print(f"  ERROR: {name} failed: {e}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Weekly sectional times collector - all states")
    parser.add_argument("--days", type=int, default=7, help="Number of days to backfill (default: 7)")
    args = parser.parse_args()

    days = args.days
    start_time = time.time()

    print(f"Weekly Sectional Collection - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Collecting last {days} days of sectional data from NSW, QLD, VIC/SA")

    results = {}

    results['NSW'] = run_collector(
        f"NSW (pidata API - 16 tracks, last {days} days)",
        [sys.executable, os.path.join(SCRIPT_DIR, "nsw_sectional_collector.py"), "--days", str(days)]
    )

    results['QLD'] = run_collector(
        f"QLD (Racing Queensland - {days} days)",
        [sys.executable, os.path.join(SCRIPT_DIR, "sectional_times_collector.py"), "--backfill", "--days", str(days)]
    )

    results['VIC_SA'] = run_collector(
        f"VIC/SA (racing.com - {days} days)",
        [sys.executable, os.path.join(SCRIPT_DIR, "racing_com_sectionals_collector.py"), "--backfill", "--days", str(days)]
    )

    elapsed = time.time() - start_time

    print(f"\n{'='*60}")
    print(f"  WEEKLY COLLECTION COMPLETE")
    print(f"  Time: {elapsed:.0f}s")
    print(f"{'='*60}")
    for state, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  {state}: {status}")

    try:
        import psycopg2
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        cur = conn.cursor()
        cur.execute("SELECT source, COUNT(*) FROM sectional_times GROUP BY source ORDER BY COUNT(*) DESC")
        print(f"\n  Database totals:")
        total = 0
        for row in cur.fetchall():
            print(f"    {row[0]}: {row[1]}")
            total += row[1]
        print(f"    TOTAL: {total}")
        conn.close()
    except Exception as e:
        print(f"  Could not query totals: {e}")

    all_ok = all(results.values())
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Daily odds-snapshot coverage audit — ROI roadmap task 04.

Reports, for a race date:
  * % of the day's tipped runners with at least one `tip_time` snapshot row
  * % with at least one `late_t5` snapshot row

Alerts (status AMBER + `alert: true`) when tip_time coverage falls below 95%:
the honest retrain (task 12) excludes runners without tip_time snapshots, so
low coverage directly shrinks the future training set.

Follows the results_health_check.py pattern: a compute function taking a
connection, a status rollup, and a JSON report on stdout. Without a database
the report degrades to {"overall_status": "NO_DB"} and exit code 0 — the
monitor must never page anyone for a missing DB.

    python odds_snapshot_coverage.py --date 2026-03-28
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]

TIP_TIME_ALERT_THRESHOLD = 95.0


def coverage_pct(covered: int, total: int) -> float:
    """Percentage covered, 0-100 rounded to 2dp. 0.0 when there is no field
    (the NO_DATA status carries that meaning, not the number)."""
    if not total:
        return 0.0
    return round((int(covered) / int(total)) * 100.0, 2)


def status_for_coverage(field_runners: int, tip_time_pct: float,
                        threshold: float = TIP_TIME_ALERT_THRESHOLD) -> str:
    """GREEN at/above threshold, AMBER (alert) below, NO_DATA with no field."""
    if not field_runners:
        return "NO_DATA"
    return "GREEN" if tip_time_pct >= threshold else "AMBER"


def load_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        return database_url
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                database_url = line.split("=", 1)[1].strip().strip('"')
                break
    if not database_url:
        raise RuntimeError("DATABASE_URL not found in environment or project .env")
    return database_url


def get_connection():
    import psycopg2  # lazy: monitor must import cleanly without a DB driver
    return psycopg2.connect(load_database_url(), connect_timeout=5)


# Denominator: distinct tipped runners (selections) for the date. Numerators:
# those runners with >= 1 runner_odds_snapshots row of each kind. Normalisation
# is inlined rather than relying on stride_norm_* functions existing.
COVERAGE_SQL = """
WITH field AS (
    SELECT DISTINCT
        lower(regexp_replace(coalesce(s.track, ''), '[^a-z0-9]+', '', 'g')) AS track_norm,
        s.race_number,
        lower(regexp_replace(coalesce(s.horse_name, ''), '[^a-z0-9]+', '', 'g')) AS horse_name_norm
    FROM selections s
    WHERE NULLIF(s.race_date, '')::date = %s::date
      AND s.race_number IS NOT NULL
      AND s.horse_name IS NOT NULL
),
snap AS (
    SELECT DISTINCT
        lower(regexp_replace(coalesce(o.track, ''), '[^a-z0-9]+', '', 'g')) AS track_norm,
        o.race_number,
        o.horse_name_norm,
        o.snapshot_kind
    FROM runner_odds_snapshots o
    WHERE o.race_date = %s::date
      AND o.horse_name_norm IS NOT NULL
)
SELECT
    COUNT(*) AS field_runners,
    COUNT(*) FILTER (WHERE tip.track_norm IS NOT NULL) AS tip_time_covered,
    COUNT(*) FILTER (WHERE late.track_norm IS NOT NULL) AS late_t5_covered
FROM field f
LEFT JOIN snap tip
  ON tip.snapshot_kind = 'tip_time'
 AND tip.track_norm = f.track_norm
 AND tip.race_number = f.race_number
 AND tip.horse_name_norm = f.horse_name_norm
LEFT JOIN snap late
  ON late.snapshot_kind = 'late_t5'
 AND late.track_norm = f.track_norm
 AND late.race_number = f.race_number
 AND late.horse_name_norm = f.horse_name_norm
"""


def compute_coverage(conn, date_str: str,
                     threshold: float = TIP_TIME_ALERT_THRESHOLD) -> Dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(COVERAGE_SQL, (date_str, date_str))
        row = cur.fetchone()

    field_runners = int(row[0] or 0)
    tip_covered = int(row[1] or 0)
    late_covered = int(row[2] or 0)
    tip_pct = coverage_pct(tip_covered, field_runners)
    late_pct = coverage_pct(late_covered, field_runners)
    status = status_for_coverage(field_runners, tip_pct, threshold)

    notes = []
    alert = False
    if status == "NO_DATA":
        notes.append("No tipped runners (selections rows) found for this date.")
    elif status == "AMBER":
        alert = True
        notes.append(
            f"tip_time snapshot coverage {tip_pct}% is below the {threshold}% "
            "threshold — the task-12 retrain set is losing runners; check the "
            "tips pipeline capture path and STRIDE_ODDS_SNAPSHOT_WRITE.")
    if field_runners and late_pct < tip_pct:
        notes.append(
            f"late_t5 coverage {late_pct}% — low early coverage is expected "
            "while the capture job beds in; honesty over coverage.")

    return {
        "status": status,
        "alert": alert,
        "field_runners": field_runners,
        "tip_time_covered": tip_covered,
        "tip_time_pct": tip_pct,
        "late_t5_covered": late_covered,
        "late_t5_pct": late_pct,
        "threshold_pct": threshold,
        "notes": notes,
    }


def build_report(date_str: str, conn=None) -> Dict[str, Any]:
    if conn is None:
        return {
            "date": date_str,
            "overall_status": "NO_DB",
            "coverage": None,
            "notes": ["No database available — coverage audit skipped."],
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }
    coverage = compute_coverage(conn, date_str)
    return {
        "date": date_str,
        "overall_status": coverage["status"],
        "coverage": coverage,
        "notes": coverage["notes"],
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Odds snapshot coverage audit")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                        help="Race date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    conn: Optional[Any] = None
    try:
        conn = get_connection()
    except Exception as e:
        print(f"  [COVERAGE] DB unavailable (non-fatal): {e}", file=sys.stderr)

    try:
        report = build_report(args.date, conn=conn)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if report["overall_status"] == "AMBER":
        cov = report["coverage"] or {}
        print(f"  [COVERAGE][ALERT] tip_time snapshot coverage "
              f"{cov.get('tip_time_pct')}% < {cov.get('threshold_pct')}% "
              f"for {args.date}", file=sys.stderr)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

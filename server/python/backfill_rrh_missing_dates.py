#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg2


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
FETCH_SCRIPT = SCRIPT_DIR / "fetch_and_import_date.py"


def load_env() -> None:
    if os.environ.get("DATABASE_URL"):
        return
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def get_conn():
    load_env()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not configured")
    return psycopg2.connect(database_url)


def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def fetch_missing_dates(conn, start_date: str, end_date: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH d AS (
              SELECT generate_series(%s::date, %s::date, interval '1 day')::date AS dt
            ), rr AS (
              SELECT race_date::date AS dt, COUNT(*) AS row_count
              FROM race_results_history
              WHERE race_date::date BETWEEN %s::date AND %s::date
              GROUP BY 1
            )
            SELECT d.dt::text
            FROM d
            LEFT JOIN rr USING (dt)
            WHERE COALESCE(rr.row_count, 0) = 0
            ORDER BY d.dt
            """,
            (start_date, end_date, start_date, end_date),
        )
        return [row[0] for row in cur.fetchall()]


def row_count_for_date(conn, race_date: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM race_results_history WHERE race_date::date = %s::date",
            (race_date,),
        )
        return int(cur.fetchone()[0])


def rrh_window_stats(conn, start_date: str, end_date: str) -> dict[str, int | str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              COUNT(*) AS rows_total,
              COUNT(DISTINCT race_date::date) AS present_dates,
              MIN(race_date::date)::text AS min_date,
              MAX(race_date::date)::text AS max_date
            FROM race_results_history
            WHERE race_date::date BETWEEN %s::date AND %s::date
            """,
            (start_date, end_date),
        )
        rows_total, present_dates, min_date, max_date = cur.fetchone()
    return {
        "rows_total": int(rows_total or 0),
        "present_dates": int(present_dates or 0),
        "min_date": min_date,
        "max_date": max_date,
    }


@dataclass
class DateRunResult:
    race_date: str
    before_rows: int
    after_rows: int
    status: str
    return_code: int
    stdout_tail: str
    stderr_tail: str


def run_import_for_date(race_date: str) -> DateRunResult:
    with get_conn() as conn:
        before_rows = row_count_for_date(conn, race_date)

    proc = subprocess.run(
        [sys.executable, str(FETCH_SCRIPT), "--date", race_date],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=1800,
    )

    with get_conn() as conn:
        after_rows = row_count_for_date(conn, race_date)

    stdout_tail = proc.stdout[-4000:]
    stderr_tail = proc.stderr[-4000:]

    if after_rows > before_rows:
        status = "imported"
    elif "No meetings found for this date." in proc.stdout:
        status = "no_meetings"
    elif proc.returncode == 0 and after_rows == 0:
        status = "still_missing"
    else:
        status = "failed"

    return DateRunResult(
        race_date=race_date,
        before_rows=before_rows,
        after_rows=after_rows,
        status=status,
        return_code=proc.returncode,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
    )


def sample_imported_dates(conn, dates: list[str], limit: int = 5) -> list[dict[str, object]]:
    samples = []
    for race_date in dates[:limit]:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  COUNT(*) AS rows_total,
                  COUNT(DISTINCT race_number) AS race_count,
                  COUNT(*) FILTER (
                    WHERE horse_id IS NOT NULL
                      AND race_id IS NOT NULL
                      AND track IS NOT NULL
                      AND race_number IS NOT NULL
                  ) AS keyed_rows
                FROM race_results_history
                WHERE race_date::date = %s::date
                """,
                (race_date,),
            )
            rows_total, race_count, keyed_rows = cur.fetchone()
        samples.append(
            {
                "race_date": race_date,
                "rows_total": int(rows_total or 0),
                "race_count": int(race_count or 0),
                "keyed_rows": int(keyed_rows or 0),
            }
        )
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill missing RRH dates and report status")
    parser.add_argument("--start-date", default="2025-11-01")
    parser.add_argument("--end-date", default="2026-03-20")
    parser.add_argument("--summary-json")
    parser.add_argument("--limit", type=int, default=0, help="Optional limit for missing dates to process")
    args = parser.parse_args()

    with get_conn() as conn:
        before_stats = rrh_window_stats(conn, args.start_date, args.end_date)
        missing_dates = fetch_missing_dates(conn, args.start_date, args.end_date)

    if args.limit > 0:
        missing_dates = missing_dates[: args.limit]

    print(
        f"[RRH BACKFILL] Missing dates in window {args.start_date}..{args.end_date}: {len(missing_dates)}",
        file=sys.stderr,
    )

    results: list[DateRunResult] = []
    for idx, race_date in enumerate(missing_dates, start=1):
        print(f"[RRH BACKFILL] {idx}/{len(missing_dates)} -> {race_date}", file=sys.stderr)
        result = run_import_for_date(race_date)
        results.append(result)
        print(
            f"[RRH BACKFILL] {race_date}: {result.status} ({result.before_rows} -> {result.after_rows})",
            file=sys.stderr,
        )

    with get_conn() as conn:
        after_stats = rrh_window_stats(conn, args.start_date, args.end_date)
        remaining_missing_dates = fetch_missing_dates(conn, args.start_date, args.end_date)
        sample_checks = sample_imported_dates(conn, [r.race_date for r in results if r.status == "imported"])
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(race_date::date)::text FROM race_results_history")
            global_latest_rrh_date = cur.fetchone()[0]

    summary = {
        "generated_at": datetime.now().isoformat(),
        "window": {"start_date": args.start_date, "end_date": args.end_date},
        "before": before_stats,
        "after": after_stats,
        "attempted_dates": len(results),
        "imported_dates": [r.race_date for r in results if r.status == "imported"],
        "no_meetings_dates": [r.race_date for r in results if r.status == "no_meetings"],
        "still_missing_dates": [r.race_date for r in results if r.status == "still_missing"],
        "failed_dates": [
            {
                "race_date": r.race_date,
                "return_code": r.return_code,
                "stdout_tail": r.stdout_tail,
                "stderr_tail": r.stderr_tail,
            }
            for r in results
            if r.status == "failed"
        ],
        "remaining_missing_dates": remaining_missing_dates,
        "global_latest_rrh_date": global_latest_rrh_date,
        "sample_checks": sample_checks,
    }

    if args.summary_json:
        path = Path(args.summary_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

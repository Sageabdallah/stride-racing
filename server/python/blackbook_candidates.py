#!/usr/bin/env python3
"""
Finds horses that finished 2nd-4th with the fastest or second-fastest closing 200m split in the field, and were NOT tipped by STRIDE.

These are horses the model missed that showed genuine latent ability.
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RACECARDS_DIR = PROJECT_ROOT / "racecards"


def _load_env():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"')
                if key not in os.environ:
                    os.environ[key] = val


def _get_connection():
    import psycopg2
    _load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    return conn


def _normalize_name(name):
    """Lowercase, alphanumeric only — matches the codebase convention."""
    if not name:
        return ""
    return re.sub(r'[^a-z0-9]', '', name.lower())


def find_candidates(conn, days):
    """Find luckless runners: 2nd-4th with top-2 closing 200m, not tipped by STRIDE."""
    cur = conn.cursor()
    cur.execute("""
        WITH fast_closers AS (
            SELECT
                rrh.horse_name,
                rrh.horse_id,
                rrh.track,
                rrh.race_date,
                rrh.race_number,
                rrh.position,
                rrh.distance_m,
                rrh.race_class,
                rrh.going,
                rrh.sp_odds,
                rrh.field_size,
                rrh.margin_lengths,
                st.last_200m_speed,
                st.last_200m_time,
                st.finishing_burst
            FROM race_results_history rrh
            JOIN sectional_times st
                ON LOWER(rrh.horse_name) = LOWER(st.horse_name)
                AND rrh.race_date = st.race_date
                AND rrh.race_number::int = st.race_number
                AND LOWER(rrh.track) = LOWER(st.track)
            WHERE rrh.position IN (2, 3, 4)
              AND rrh.race_date::date >= CURRENT_DATE - interval '%s days'
              AND st.last_200m_speed IS NOT NULL
        ),
        speed_ranked AS (
            SELECT *,
                RANK() OVER (
                    PARTITION BY track, race_date, race_number
                    ORDER BY last_200m_speed DESC
                ) AS speed_rank
            FROM fast_closers
        ),
        not_tipped AS (
            SELECT sr.*
            FROM speed_ranked sr
            LEFT JOIN stride_tip_results str
                ON LOWER(sr.horse_name) = LOWER(str.horse_name)
                AND sr.race_date = str.race_date
                AND sr.track = str.track
                AND sr.race_number = str.race_number::int
            WHERE sr.speed_rank <= 2
              AND str.horse_name IS NULL
        )
        SELECT * FROM not_tipped
        ORDER BY race_date DESC, track, race_number
    """ % days)

    columns = [desc[0] for desc in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    return rows


def find_next_runs(candidates, today_str):
    """Scan upcoming racecards for next scheduled runs of candidate horses."""
    upcoming_by_name = defaultdict(list)
    upcoming_by_id = defaultdict(list)

    for rc_file in sorted(RACECARDS_DIR.glob("racecard_*.json")):
        try:
            rc_date = rc_file.stem.replace("racecard_", "")
            if rc_date < today_str:
                continue
        except Exception:
            continue

        try:
            data = json.loads(rc_file.read_text())
        except Exception:
            continue

        meets = data if isinstance(data, list) else [data]
        for meet in meets:
            races = meet.get("races", [])
            for race in races:
                track = race.get("course", meet.get("course", ""))
                race_num = race.get("race_number", "")
                distance = race.get("distance", "")
                race_class = race.get("race_class", "")
                race_date = race.get("date", rc_date)

                for runner in race.get("runners", []):
                    horse_name = runner.get("horse", "")
                    horse_id = runner.get("horse_id", "")
                    entry = {
                        "date": race_date,
                        "track": track,
                        "race_number": int(race_num) if str(race_num).isdigit() else race_num,
                        "distance": distance,
                        "race_class": race_class,
                    }
                    norm = _normalize_name(horse_name)
                    if norm:
                        upcoming_by_name[norm].append(entry)
                    if horse_id:
                        upcoming_by_id[horse_id].append(entry)

    next_runs = {}
    for cand in candidates:
        horse_id = cand.get("horse_id")
        horse_name = cand.get("horse_name", "")
        key = _normalize_name(horse_name)

        entries = []
        if horse_id and horse_id in upcoming_by_id:
            entries = upcoming_by_id[horse_id]
        elif key in upcoming_by_name:
            entries = upcoming_by_name[key]

        if entries:
            entries_sorted = sorted(entries, key=lambda e: (e["date"], e.get("race_number", 0)))
            next_runs[key] = entries_sorted[0]

    return next_runs


def build_output(rows, next_runs):
    """Group qualifying runs by horse and attach next-run info."""
    horses = defaultdict(lambda: {"qualifying_runs": []})

    for row in rows:
        key = _normalize_name(row["horse_name"])
        horse = horses[key]
        horse["horse_name"] = row["horse_name"]
        horse["horse_id"] = row.get("horse_id")

        run = {
            "race_date": str(row["race_date"]),
            "track": row["track"],
            "race_number": row["race_number"],
            "position": row["position"],
            "margin_lengths": float(row["margin_lengths"]) if row.get("margin_lengths") else None,
            "distance_m": row.get("distance_m"),
            "race_class": row.get("race_class"),
            "going": row.get("going"),
            "sp_odds": float(row["sp_odds"]) if row.get("sp_odds") else None,
            "last_200m_speed": float(row["last_200m_speed"]) if row.get("last_200m_speed") else None,
            "last_200m_time": float(row["last_200m_time"]) if row.get("last_200m_time") else None,
            "finishing_burst": float(row["finishing_burst"]) if row.get("finishing_burst") else None,
            "speed_rank": row.get("speed_rank"),
            "field_size": row.get("field_size"),
        }
        horse["qualifying_runs"].append(run)

        if key in next_runs:
            horse["next_run"] = next_runs[key]
        elif "next_run" not in horse:
            horse["next_run"] = None

    candidates = sorted(
        horses.values(),
        key=lambda h: (h["next_run"] is None, -len(h["qualifying_runs"]), h["horse_name"]),
    )
    return candidates


def main():
    parser = argparse.ArgumentParser(description="Find blackbook candidates — luckless runners with fast closing sectionals")
    parser.add_argument("--days", type=int, default=30, help="Lookback period in days (default: 30)")
    parser.add_argument("--date", type=str, default=None, help="Output file date (default: today)")
    args = parser.parse_args()

    target_date = args.date or date.today().isoformat()

    conn = _get_connection()
    try:
        rows = find_candidates(conn, args.days)
        print(f"[Blackbook] Found {len(rows)} qualifying runs", file=sys.stderr)

        next_runs = find_next_runs(rows, target_date)
        print(f"[Blackbook] Matched {len(next_runs)} horses to upcoming racecards", file=sys.stderr)

        candidates = build_output(rows, next_runs)

        payload = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "lookback_days": args.days,
            "total_candidates": len(candidates),
            "candidates_with_next_run": sum(1 for c in candidates if c.get("next_run")),
            "candidates": candidates,
        }

        output_path = RACECARDS_DIR / f"blackbook_candidates_{target_date}.json"
        output_path.write_text(json.dumps(payload, indent=2, default=str))
        print(f"[Blackbook] Written to {output_path}", file=sys.stderr)

        print(json.dumps(payload, indent=2, default=str))

    finally:
        conn.close()


if __name__ == "__main__":
    main()

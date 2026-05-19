#!/usr/bin/env python3
"""
Targeted lambda/svi/rsi/trip_cost backfill across ALL sectional sources.

The original compute_phase2_primitives in sectional_times_collector.py only
understands racing_com's splits_json shape (list of {distance, avgSpeed, time}).
NSW pidata uses a dict keyed by mark ("0m", "200m", ...) and racing_qld uses
list of {mark, speed_ms, split_time}. Result: ~33k rows had splits but no
lambda/svi/rsi.

This script normalises all three formats to a common shape, then computes
the same Ward-Smith lambda, sustained velocity index, race shape index,
and Thorograph trip cost — writing back to the row by id.
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:
    pass

import psycopg2
from psycopg2.extras import execute_batch


def _parse_mark_str(s):
    """Parse '200m-FINISH' or '1000m-800m' or '200m' into the END mark in metres."""
    if s is None:
        return None
    s = str(s).strip()
    if "-" in s:
        end = s.split("-", 1)[1].strip()
    else:
        end = s
    if end.upper() in ("FINISH", "FIN"):
        return 0
    m = re.match(r"(\d+)", end)
    return int(m.group(1)) if m else None


def normalise_splits(splits_json):
    """
    Return list of dicts {mark:int, speed:float, time:float} sorted by mark DESC.

    Handles three formats produced by collectors:
      racing_com:  [{"distance": "1000m-800m", "avgSpeed": 19.46, "time": "10.28"}]
      racing_qld:  [{"mark": 200, "speed_ms": 18.42, "split_time": 13.57}]
      nsw_pidata:  {"0m": {"time": 11.03, "speed_ms": 18.077}, "200m": {...}}
    """
    if not splits_json:
        return []
    if isinstance(splits_json, str):
        try:
            splits_json = json.loads(splits_json)
        except Exception:
            return []

    out = []
    if isinstance(splits_json, dict):
        for k, v in splits_json.items():
            if not isinstance(v, dict):
                continue
            mark = _parse_mark_str(k)
            if mark is None:
                continue
            spd = v.get("speed_ms") or v.get("avgSpeed")
            tm = v.get("time") or v.get("split_time")
            if spd is None or tm is None:
                continue
            try:
                out.append({"mark": int(mark), "speed": float(spd), "time": float(tm)})
            except (TypeError, ValueError):
                continue
    elif isinstance(splits_json, list):
        for s in splits_json:
            if not isinstance(s, dict):
                continue
            if "mark" in s and s["mark"] is not None:
                mark = s["mark"]
                if isinstance(mark, str):
                    mark = _parse_mark_str(mark)
            elif "distance" in s:
                mark = _parse_mark_str(s["distance"])
            else:
                mark = None
            if mark is None:
                continue
            spd = s.get("speed_ms") or s.get("avgSpeed")
            tm = s.get("split_time") or s.get("time")
            if spd is None or tm is None:
                continue
            try:
                out.append({"mark": int(mark), "speed": float(spd), "time": float(tm)})
            except (TypeError, ValueError):
                continue

    out.sort(key=lambda r: r["mark"], reverse=True)
    return out


def compute_lambda(norm_splits):
    """Ward-Smith decay constant. norm_splits sorted mark DESC (race order)."""
    if len(norm_splits) < 3:
        return None
    speeds = [s["speed"] for s in norm_splits if s["speed"] > 0]
    if len(speeds) < 3:
        return None
    try:
        v_peak = max(speeds)
        peak_idx = speeds.index(v_peak)
        v_fin = speeds[-1]
        decay_secs = len(speeds) - 1 - peak_idx
        if decay_secs <= 0 or v_peak <= 0 or v_fin <= 0:
            return None
        return round(-math.log(v_fin / v_peak) / (decay_secs * 200), 6)
    except (ValueError, ZeroDivisionError):
        return None


def compute_svi(norm_splits):
    """Sustained velocity index: mean(last 3 sections) / mean(all sections)."""
    if len(norm_splits) < 3:
        return None
    # last 3 sections to finish = lowest marks
    finishing = sorted(norm_splits, key=lambda s: s["mark"])[:3]
    speeds_all = [s["speed"] for s in norm_splits if s["speed"] > 0]
    speeds_fin = [s["speed"] for s in finishing if s["speed"] > 0]
    if len(speeds_all) < 3 or len(speeds_fin) < 3:
        return None
    try:
        return round(statistics.mean(speeds_fin) / statistics.mean(speeds_all), 4)
    except statistics.StatisticsError:
        return None


def compute_rsi(norm_splits, distance_m):
    """Race shape index: first-half time / second-half time."""
    if not norm_splits or not distance_m:
        return None
    try:
        half = float(distance_m) / 2.0
    except (TypeError, ValueError):
        return None
    fh = sum(s["time"] for s in norm_splits if s["mark"] >= half and s["time"] > 0)
    sh = sum(s["time"] for s in norm_splits if s["mark"] < half and s["time"] > 0)
    if fh <= 0 or sh <= 0:
        return None
    return round(fh / sh, 4)


def compute_trip_cost(barrier, distance_m, avg_speed):
    """Thorograph wide-barrier time cost (Brohamer 2000)."""
    try:
        b = int(barrier) if barrier else 1
    except (TypeError, ValueError):
        b = 1
    if b <= 1:
        return 0.0
    try:
        v = float(avg_speed) if avg_speed and float(avg_speed) > 0 else 14.5
    except (TypeError, ValueError):
        v = 14.5
    try:
        d = int(distance_m) if distance_m else 1400
    except (TypeError, ValueError):
        d = 1400
    turns = 1 if d < 1200 else 2
    return round((b - 1) * 1.8 * turns * 0.65 / v, 3)


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    conn.autocommit = False  # batch commits keep round trips short

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, splits_json, distance_m, avg_speed, saddle_number
            FROM sectional_times
            WHERE splits_json IS NOT NULL
              AND splits_json::text NOT IN ('null', '[]', '{}')
              AND lambda_decay IS NULL
        """)
        rows = cur.fetchall()

    total = len(rows)
    print(f"Rows needing lambda/svi/rsi: {total}", flush=True)

    start = time.time()
    updated = 0
    skipped_empty = 0
    skipped_invalid = 0

    pending = []
    BATCH = 500

    cur = conn.cursor()
    update_sql = """
        UPDATE sectional_times
        SET lambda_decay = %s, svi = %s, rsi = %s, trip_cost_seconds = %s
        WHERE id = %s
    """

    def flush(batch):
        nonlocal updated
        if not batch:
            return
        execute_batch(cur, update_sql, batch, page_size=200)
        conn.commit()
        updated += len(batch)

    for idx, (rid, splits_json, dist_m, avg_spd, saddle) in enumerate(rows, 1):
        norm = normalise_splits(splits_json)
        if not norm:
            skipped_empty += 1
        else:
            lam = compute_lambda(norm)
            svi = compute_svi(norm)
            rsi = compute_rsi(norm, dist_m)
            tc = compute_trip_cost(saddle, dist_m, avg_spd)

            if lam is None and svi is None and rsi is None and tc == 0.0:
                skipped_invalid += 1
            else:
                pending.append((lam, svi, rsi, tc, rid))

        if len(pending) >= BATCH:
            flush(pending)
            pending = []

        if idx % 2000 == 0 or idx == total:
            elapsed = time.time() - start
            rate = idx / elapsed if elapsed else 0
            eta = (total - idx) / rate if rate else 0
            print(f"  [{idx}/{total}] updated={updated} empty={skipped_empty} invalid={skipped_invalid} elapsed={elapsed:.0f}s ETA={eta:.0f}s", flush=True)

    flush(pending)
    cur.close()
    conn.close()
    print(f"\nDone. Total processed: {total}, updated: {updated}, empty splits: {skipped_empty}, invalid: {skipped_invalid}")


if __name__ == "__main__":
    main()

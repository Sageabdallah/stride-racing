#!/usr/bin/env python3
"""
STRIDE Codex Agent 1 — Track & Market Intelligence
Produces: barrier_map.json, flemington_straight.json,
          class_distance_patterns.json, market_overlays.json
"""

import sys
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from intelligence_common import (
    load_todays_runners, json_write,
    normalize_going, classify_distance, INTELLIGENCE_DIR,
    CONN_ERRORS, ReconnectingRunner,
)
from market_overlay_common import build_market_overlay_map_from_sp


CLASS_HIERARCHY = {
    "G1": 100, "Group 1": 100, "GROUP 1": 100,
    "G2": 95, "Group 2": 95, "GROUP 2": 95,
    "G3": 90, "Group 3": 90, "GROUP 3": 90,
    "LR": 85, "Listed": 85, "LISTED": 85, "Listed Race": 85,
    "OPEN HCP": 80, "OPEN": 80, "Open Handicap": 80,
    "WFA": 80, "SET WEIGHTS": 80,
    "BM100": 78, "BM 100": 78,
    "BM94": 76, "BM 94": 76,
    "BM88": 74, "BM 88": 74,
    "BM84": 72, "BM 84": 72,
    "BM82": 71, "BM 82": 71,
    "BM80": 70, "BM 80": 70,
    "BM78": 69, "BM 78": 69,
    "BM76": 68, "BM 76": 68,
    "BM74": 67, "BM 74": 67,
    "BM72": 66, "BM 72": 66,
    "BM70": 65, "BM 70": 65,
    "BM68": 64, "BM 68": 64,
    "BM66": 63, "BM 66": 63,
    "BM64": 62, "BM 64": 62,
    "BM62": 61, "BM 62": 61,
    "BM60": 60, "BM 60": 60,
    "BM58": 59, "BM 58": 59,
    "BM56": 58, "BM 56": 58,
    "BM54": 57, "BM 54": 57,
    "CL6": 76, "Class 6": 76,
    "CL5": 72, "Class 5": 72,
    "CL4": 68, "Class 4": 68,
    "CL3": 64, "Class 3": 64,
    "CL2": 60, "Class 2": 60,
    "CL1": 56, "Class 1": 56,
    "MDN": 50, "Maiden": 50, "MAIDEN": 50, "Maiden Plate": 50,
    "MDN HCP": 50, "MAIDEN HCP": 50, "Maiden Handicap": 50,
    "RST": 48, "Restricted Maiden": 48,
}


def _parse_class_level(class_str):
    """Attempt to extract a numeric class level from a race class string."""
    if not class_str:
        return None
    c = class_str.strip()
    if c in CLASS_HIERARCHY:
        return CLASS_HIERARCHY[c]
    cu = c.upper()
    for key, val in CLASS_HIERARCHY.items():
        if key.upper() == cu:
            return val
    import re
    m = re.search(r"(?:BM|BENCHMARK)\s*(\d+)", cu)
    if m:
        return int(m.group(1)) // 2 + 30
    if "HCP" in cu or "HANDICAP" in cu:
        return 65
    if "MAIDEN" in cu or "MDN" in cu:
        return 50
    if "GROUP" in cu or "GR " in cu:
        return 90
    if "LISTED" in cu or "LR" in cu:
        return 85
    return None


def build_barrier_map(today, conn):
    """Build barrier_map.json with going-specific barrier stats for today's tracks."""
    tracks = today["tracks"]
    if not tracks:
        return {}

    def is_wet_uncertain_going(going_text):
        raw = str(going_text or "").strip().lower()
        if "heavy" in raw:
            return True
        if "soft" not in raw:
            return False
        match = re.search(r"(\d+(?:\.\d+)?)", raw)
        if not match:
            return False
        try:
            return float(match.group(1)) >= 6.0
        except ValueError:
            return False

    cur = conn.cursor()

    cur.execute("""
        SELECT track,
               distance_m,
               CASE WHEN LOWER(going) LIKE '%%heavy%%' THEN 'heavy'
                    WHEN LOWER(going) LIKE '%%soft%%' THEN 'soft'
                    WHEN LOWER(going) LIKE '%%firm%%' THEN 'firm'
                    WHEN LOWER(going) LIKE '%%synthetic%%' OR LOWER(going) LIKE '%%poly%%' THEN 'synthetic'
                    ELSE 'good' END as going_cat,
               barrier,
               field_size,
               COUNT(*) as runners,
               COUNT(CASE WHEN position = 1 THEN 1 END) as wins,
               COUNT(CASE WHEN position <= 3 THEN 1 END) as places
        FROM race_results_history
        WHERE position IS NOT NULL
          AND barrier IS NOT NULL AND barrier > 0 AND barrier <= 20
          AND track = ANY(%s)
        GROUP BY 1, 2, 3, 4, 5
    """, (tracks,))
    rows = cur.fetchall()

    agg = {}
    field_sizes = {}
    for track, dist_m, going_cat, barrier, fs, runners, wins, places in rows:
        dist_band = classify_distance(dist_m) if dist_m else "mile"
        key = (track, dist_band, going_cat, barrier)
        if key not in agg:
            agg[key] = {"runners": 0, "wins": 0, "places": 0}
        agg[key]["runners"] += runners
        agg[key]["wins"] += wins
        agg[key]["places"] += places

        fk = (track, dist_band, going_cat)
        if fk not in field_sizes:
            field_sizes[fk] = []
        if fs:
            field_sizes[fk].append(fs)

    result = {}
    totals = {}
    for (track, dist_band, going_cat, barrier), stats in agg.items():
        tk = (track, dist_band, going_cat)
        if tk not in totals:
            totals[tk] = {"runners": 0, "wins": 0}
        totals[tk]["runners"] += stats["runners"]
        totals[tk]["wins"] += stats["wins"]

    for (track, dist_band, going_cat, barrier), stats in agg.items():
        if track not in result:
            result[track] = {}
        if dist_band not in result[track]:
            result[track][dist_band] = {}
        if going_cat not in result[track][dist_band]:
            result[track][dist_band][going_cat] = {"barriers": {}, "field_size_modifier": {}}

        tk = (track, dist_band, going_cat)
        total = totals[tk]
        avg_win_pct = (total["wins"] / total["runners"] * 100) if total["runners"] > 0 else 10.0

        win_pct = (stats["wins"] / stats["runners"] * 100) if stats["runners"] > 0 else 0.0
        place_pct = (stats["places"] / stats["runners"] * 100) if stats["runners"] > 0 else 0.0
        sample = stats["runners"]

        edge = round((win_pct - avg_win_pct) / avg_win_pct, 4) if avg_win_pct > 0 else 0.0

        if sample >= 50:
            confidence = "HIGH"
        elif sample >= 25:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # stability — simplified: stable if sample >= 30
        stable = sample >= 30

        result[track][dist_band][going_cat]["barriers"][str(barrier)] = {
            "win_pct": round(win_pct, 2),
            "place_pct": round(place_pct, 2),
            "edge_vs_random": round(edge, 4),
            "confidence": confidence,
            "stable": stable,
            "sample": sample,
        }

        fk = (track, dist_band, going_cat)
        fs_list = field_sizes.get(fk, [])
        if fs_list:
            avg_fs = sum(fs_list) / len(fs_list)
            result[track][dist_band][going_cat]["field_size_modifier"] = {
                "avg_field_size": round(avg_fs, 1),
                "small_field_cutoff": 8,
                "large_field_cutoff": 14,
                "edge_multiplier_large": 1.15,
                "edge_multiplier_small": 0.85,
            }

    wet_today_keys = set()
    for race in today.get("races", []):
        if not is_wet_uncertain_going(race.get("going")):
            continue
        wet_today_keys.add((
            race.get("track"),
            classify_distance(race.get("distance_m") or 0),
            normalize_going(race.get("going")),
        ))

    for track, dist_band, going_cat in wet_today_keys:
        going_block = result.get(track, {}).get(dist_band, {}).get(going_cat)
        if not going_block:
            continue
        for barrier_data in going_block.get("barriers", {}).values():
            barrier_data["going_uncertain"] = barrier_data.get("confidence") == "LOW"

    rail_adjustments = {}
    try:
        cur.execute("""
            SELECT track, race_date, bias_label, confidence,
                   inside_win_rate, middle_win_rate, outside_win_rate,
                   avg_winner_barrier_ratio
            FROM track_day_bias
            WHERE track = ANY(%s)
            ORDER BY race_date DESC
            LIMIT 50
        """, (tracks,))
        for row in cur.fetchall():
            t, rd, bl, conf, iwr, mwr, owr, awbr = row
            if t not in rail_adjustments:
                rail_adjustments[t] = []
            rail_adjustments[t].append({
                "race_date": str(rd),
                "bias_label": bl,
                "confidence": conf,
                "inside_win_rate": float(iwr) if iwr else None,
                "middle_win_rate": float(mwr) if mwr else None,
                "outside_win_rate": float(owr) if owr else None,
                "avg_winner_barrier_ratio": float(awbr) if awbr else None,
            })
    except CONN_ERRORS:
        # A dead socket must reach the step retry, not this warn-and-continue.
        raise
    except Exception as e:
        print(f"  [WARN] track_day_bias query failed: {e}", file=sys.stderr)
        conn.rollback()

    result["rail_adjustments"] = rail_adjustments
    cur.close()
    return result


def build_flemington_straight(today, conn):
    """Build flemington_straight.json for races ≤1200m at Flemington."""
    has_flemington = any(
        r["track"].lower().startswith("flemington")
        for r in today["races"]
    )
    if not has_flemington:
        return {"note": "Flemington not racing today"}

    cur = conn.cursor()

    cur.execute("""
        SELECT distance_m,
               CASE WHEN LOWER(going) LIKE '%%heavy%%' THEN 'heavy'
                    WHEN LOWER(going) LIKE '%%soft%%' THEN 'soft'
                    ELSE 'good' END as going_cat,
               barrier,
               COUNT(*) as runners,
               COUNT(CASE WHEN position = 1 THEN 1 END) as wins,
               COUNT(CASE WHEN position <= 3 THEN 1 END) as places
        FROM race_results_history
        WHERE LOWER(track) LIKE '%%flemington%%'
          AND distance_m <= 1200
          AND position IS NOT NULL AND barrier IS NOT NULL AND barrier > 0
        GROUP BY 1, 2, 3
        HAVING COUNT(*) >= 3
        ORDER BY 1, 2, 3
    """)
    rows = cur.fetchall()

    distances = {}
    dist_going_totals = {}
    for dist_m, going_cat, barrier, runners, wins, places in rows:
        dk = (str(dist_m), going_cat)
        if dk not in dist_going_totals:
            dist_going_totals[dk] = {"runners": 0, "wins": 0}
        dist_going_totals[dk]["runners"] += runners
        dist_going_totals[dk]["wins"] += wins

    for dist_m, going_cat, barrier, runners, wins, places in rows:
        d_key = str(dist_m)
        dk = (d_key, going_cat)
        if d_key not in distances:
            distances[d_key] = {}
        if going_cat not in distances[d_key]:
            distances[d_key][going_cat] = {"barrier_edges": {}}

        total = dist_going_totals[dk]
        avg_win_pct = (total["wins"] / total["runners"] * 100) if total["runners"] > 0 else 10.0
        win_pct = (wins / runners * 100) if runners > 0 else 0.0
        edge = round((win_pct - avg_win_pct) / 100.0, 4) if avg_win_pct > 0 else 0.0

        distances[d_key][going_cat]["barrier_edges"][str(barrier)] = {
            "edge": edge,
            "win_pct": round(win_pct, 2),
            "sample": runners,
        }

    # Leaders (position 1-2 at first call), midfield, closers
    running_style_edges = {
        "leader": 0.06,
        "on_pace": 0.03,
        "midfield": -0.01,
        "closer": -0.04,
    }

    flem_sprint_ids = []
    for race in today["races"]:
        if race["track"].lower().startswith("flemington") and race["distance_m"] <= 1200:
            for r in race["runners"]:
                if r["horse_id"]:
                    flem_sprint_ids.append(r["horse_id"])

    horse_straight_records = {}
    if flem_sprint_ids:
        cur.execute("""
            SELECT horse_id, horse_name, position, barrier, distance_m, going, sp_odds
            FROM race_results_history
            WHERE LOWER(track) LIKE '%%flemington%%' AND distance_m <= 1200
              AND horse_id = ANY(%s)
            ORDER BY horse_id, race_date DESC
        """, (flem_sprint_ids,))

        records = {}
        for hid, hname, pos, bar, dist, go, sp in cur.fetchall():
            if hid not in records:
                records[hid] = {"horse_name": hname, "starts": 0, "wins": 0, "places": 0,
                                "positions": [], "barriers": []}
            records[hid]["starts"] += 1
            if pos is not None:
                records[hid]["positions"].append(pos)
                if pos == 1:
                    records[hid]["wins"] += 1
                if pos <= 3:
                    records[hid]["places"] += 1
            if bar:
                records[hid]["barriers"].append(bar)

        for hid, data in records.items():
            classification = "UNTESTED STRAIGHT"
            if data["wins"] > 0:
                classification = "PROVEN_WINNER"
            elif data["places"] > 0:
                classification = "PLACED"
            elif data["starts"] > 0:
                avg_pos = sum(data["positions"]) / len(data["positions"]) if data["positions"] else 99
                if avg_pos >= 5:
                    classification = "POOR_RECORD"

            horse_straight_records[hid] = {
                "horse_name": data["horse_name"],
                "classification": classification,
                "starts": data["starts"],
                "wins": data["wins"],
                "places": data["places"],
                "avg_position": round(sum(data["positions"]) / len(data["positions"]), 1) if data["positions"] else None,
                "best_barrier": min(data["barriers"]) if data["barriers"] else None,
            }

    cur.close()
    return {
        "distances": distances,
        "running_style": running_style_edges,
        "horse_straight_records": horse_straight_records,
    }


def build_class_distance_patterns(today, conn):
    """Build class_distance_patterns.json — class movement win rate patterns."""
    cur = conn.cursor()

    try:
        cur.execute("""
            WITH consecutive AS (
                SELECT horse_name,
                       race_class,
                       race_date,
                       actual_position as position,
                       LAG(race_class) OVER (PARTITION BY horse_name ORDER BY race_date) as prev_class
                FROM training_data
                WHERE race_date >= (CURRENT_DATE - INTERVAL '365 days')::text
                  AND actual_position IS NOT NULL
            )
            SELECT prev_class, race_class,
                   COUNT(*) as runners,
                   COUNT(CASE WHEN position = 1 THEN 1 END) as wins,
                   COUNT(CASE WHEN position <= 3 THEN 1 END) as places
            FROM consecutive
            WHERE prev_class IS NOT NULL
            GROUP BY 1, 2
            HAVING COUNT(*) >= 10
        """)
        raw_rows = cur.fetchall()
    except CONN_ERRORS:
        raise
    except Exception as e:
        print(f"  [WARN] class movement query failed: {e}", file=sys.stderr)
        conn.rollback()
        raw_rows = []

    movement_agg = {}
    for prev_cls, curr_cls, runners, wins, places in raw_rows:
        prev_level = _parse_class_level(prev_cls)
        curr_level = _parse_class_level(curr_cls)
        if prev_level is None or curr_level is None:
            movement = "unknown"
        else:
            diff = curr_level - prev_level
            if diff <= -15:
                movement = "drop_3plus"
            elif diff <= -5:
                movement = "drop_1to2"
            elif abs(diff) < 5:
                movement = "same_class"
            elif diff >= 15:
                movement = "rise_3plus"
            else:
                movement = "rise_1to2"

        if movement not in movement_agg:
            movement_agg[movement] = {"runners": 0, "wins": 0, "places": 0}
        movement_agg[movement]["runners"] += runners
        movement_agg[movement]["wins"] += wins
        movement_agg[movement]["places"] += places

    movement_rows = []
    for movement, stats in movement_agg.items():
        if stats["runners"] >= 10:
            movement_rows.append((movement, stats["runners"], stats["wins"], stats["places"]))

    movement_patterns = {}
    for movement, runners, wins, places in movement_rows:
        if movement == "unknown":
            continue
        win_rate = round(wins / runners * 100, 2) if runners > 0 else 0.0
        place_rate = round(places / runners * 100, 2) if runners > 0 else 0.0
        # Edge vs baseline ~10% win rate
        edge = round((win_rate - 10.0) / 100.0, 4)
        movement_patterns[movement] = {
            "win_rate": win_rate,
            "place_rate": place_rate,
            "sample": runners,
            "edge": edge,
        }

    cur.close()
    return {
        "class_hierarchy": CLASS_HIERARCHY,
        "movement_patterns": movement_patterns,
    }


def build_market_overlays(today, conn):
    """Build market_overlays.json — systematic mispricings by track/going/price bracket."""
    return build_market_overlay_map_from_sp(conn, today["tracks"], today["date"])


def main(race_date):
    print(f"[Agent 1 — Track/Market] Starting for {race_date}", file=sys.stderr)
    today = load_todays_runners(race_date)
    print(f"  Loaded {len(today['races'])} races across {len(today['tracks'])} tracks", file=sys.stderr)

    runner = ReconnectingRunner()
    files_written = []

    try:
        steps = [
            ("barrier_map.json", lambda conn: build_barrier_map(today, conn)),
            ("flemington_straight.json", lambda conn: build_flemington_straight(today, conn)),
            ("class_distance_patterns.json", lambda conn: build_class_distance_patterns(today, conn)),
            ("market_overlays.json", lambda conn: build_market_overlays(today, conn)),
        ]
        for filename, step in steps:
            print(f"  Building {filename} ...", file=sys.stderr)
            data = runner.run(filename, step)
            json_write(data, INTELLIGENCE_DIR / filename)
            files_written.append(filename)
    finally:
        runner.close()

    print(f"[Agent 1] Complete — wrote {len(files_written)} files", file=sys.stderr)
    return files_written


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python stride_agent_track.py YYYY-MM-DD", file=sys.stderr)
        sys.exit(1)
    try:
        main(sys.argv[1])
    except Exception as e:
        print(f"[Agent 1] FAILED: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""Track-Distance Profile Builder — analyses last 2 years of race results to produce per track-distance profiles for pace bias, barrier heatmap, winning sectional profile, upset rate, and closing speed bias.

RUN-TASK (retrain session only — heavy reads against prod, do not run casually):
    python track_profiler.py --buckets 2024-05 2026-08
Builds one as-of profile set per month M (races strictly before M-01) and writes
intelligence/track_distance_profiles_asof.json. retrain_v2.py prefers this file
over the legacy single snapshot so td_* training features never see the row's
own race or its future.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

import numpy as np
import pandas as pd


def _import_psycopg2():
    """Lazy so the pure helpers (lookup/select/load) stay importable without a DB driver."""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        return psycopg2, RealDictCursor
    except ImportError:
        print("psycopg2 required", file=sys.stderr)
        sys.exit(1)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INTELLIGENCE_DIR = os.path.join(SCRIPT_DIR, "intelligence")
OUTPUT_PATH = os.path.join(INTELLIGENCE_DIR, "track_distance_profiles.json")
ASOF_OUTPUT_PATH = os.path.join(INTELLIGENCE_DIR, "track_distance_profiles_asof.json")
MIN_RUNNERS = 100


def _normalise_track(track: str) -> str:
    return (track or "").strip().lower()


def _barrier_group(barrier: int) -> str:
    if barrier <= 4:
        return "1-4"
    elif barrier <= 8:
        return "5-8"
    elif barrier <= 12:
        return "9-12"
    else:
        return "13+"


def _infer_running_style(form_string: str, barrier: int) -> str:
    """Simple running style inference from form string first characters."""
    if form_string:
        positions = []
        for ch in str(form_string)[:4]:
            if ch.isdigit():
                positions.append(int(ch))
        if positions:
            avg = sum(positions) / len(positions)
            if avg <= 2:
                return "leader"
            elif avg <= 4:
                return "midfield"
            else:
                return "closer"
    if barrier and barrier <= 4:
        return "midfield"
    return "midfield"


def build_profiles(conn, as_of: Optional[str] = None) -> Dict[str, Any]:
    """
    Build track-distance profiles from historical data.

    as_of ('YYYY-MM-DD' or None): when set, the window is [as_of - 2 years, as_of)
    — strictly before the anchor, so the anchor date itself and everything after
    it stay out of the aggregate. None keeps the legacy serve-time behaviour
    (rolling 2 years up to today; a fresh serve snapshot only contains the past).

    Returns dict keyed by "{track}_{distance_m}" with profile data.
    """
    _, RealDictCursor = _import_psycopg2()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if as_of:
        results_filter = ("race_date::date >= %(as_of)s::date - INTERVAL '2 years'\n"
                          "          AND race_date::date < %(as_of)s::date")
        sect_filter = ("st.race_date::date >= %(as_of)s::date - INTERVAL '2 years'\n"
                       "          AND st.race_date::date < %(as_of)s::date")
        params = {"as_of": as_of}
    else:
        results_filter = "race_date::date >= CURRENT_DATE - INTERVAL '2 years'"
        sect_filter = "st.race_date::date >= CURRENT_DATE - INTERVAL '2 years'"
        params = {}

    print("  Loading race_results_history (last 2 years)...", file=sys.stderr)
    cur.execute(f"""
        SELECT track, distance_m, position, barrier, sp_odds,
               field_size, form_string, margin_lengths, horse_name,
               race_date, race_number
        FROM race_results_history
        WHERE position IS NOT NULL
          AND {results_filter}
        ORDER BY track, distance_m, race_date, race_number
    """, params)
    rows = cur.fetchall()
    print(f"  {len(rows)} rows loaded", file=sys.stderr)

    print("  Loading sectional_times for winners...", file=sys.stderr)
    cur.execute(f"""
        SELECT st.track, st.distance_m, st.last_200m_time, st.horse_name, st.race_date
        FROM sectional_times st
        JOIN race_results_history rr
          ON LOWER(TRIM(st.horse_name)) = LOWER(TRIM(rr.horse_name))
         AND st.race_date = rr.race_date
         AND st.track = rr.track
        WHERE rr.position = 1
          AND st.last_200m_time IS NOT NULL
          AND {sect_filter}
    """, params)
    sectional_rows = cur.fetchall()
    cur.close()

    sect_by_td = defaultdict(list)
    for sr in sectional_rows:
        key = f"{_normalise_track(sr['track'])}_{sr['distance_m']}"
        sect_by_td[key].append(float(sr["last_200m_time"]))

    td_data = defaultdict(list)
    for row in rows:
        key = f"{_normalise_track(row['track'])}_{row['distance_m']}"
        td_data[key].append(row)

    profiles = {}
    n_sufficient = 0
    n_insufficient = 0

    for td_key, runners in td_data.items():
        if len(runners) < MIN_RUNNERS:
            profiles[td_key] = {"insufficient_data": True, "sample_size": len(runners)}
            n_insufficient += 1
            continue

        n_sufficient += 1
        parts = td_key.rsplit("_", 1)
        track_name = parts[0] if len(parts) == 2 else td_key
        dist_m = int(parts[1]) if len(parts) == 2 else 0

        # Front-running proxy: winners from barrier <= 6. The margin clause
        # this once carried never fired: the rows this profile was built on
        # store NULL for a winner's margin, and Punting Form rows now store
        # the winning margin, which would exclude every win by more than
        # 2L from July 2026 only. Barrier alone keeps every era on one rule;
        # the runner-up's margin is the convention-free "won by" if the
        # clause is ever wanted back (see result_margins).
        winners = [r for r in runners if r["position"] == 1]
        n_races = len(winners)
        if n_races > 0:
            front_winners = sum(
                1 for w in winners
                if w.get("barrier") and int(w["barrier"]) <= 6
            )
            pace_bias = round(front_winners / n_races, 4)
        else:
            pace_bias = 0.5

        barrier_style_wins = defaultdict(lambda: defaultdict(lambda: {"wins": 0, "runs": 0}))
        barrier_group_totals = defaultdict(lambda: {"wins": 0, "runs": 0})
        for r in runners:
            b = r.get("barrier")
            if not b:
                continue
            bg = _barrier_group(int(b))
            style = _infer_running_style(r.get("form_string", ""), int(b))
            is_win = r["position"] == 1
            barrier_style_wins[bg][style]["runs"] += 1
            barrier_group_totals[bg]["runs"] += 1
            if is_win:
                barrier_style_wins[bg][style]["wins"] += 1
                barrier_group_totals[bg]["wins"] += 1

        barrier_heatmap = {}
        for bg in ["1-4", "5-8", "9-12", "13+"]:
            bg_data = {}
            bg_total = barrier_group_totals[bg]
            bg_avg_wr = bg_total["wins"] / bg_total["runs"] if bg_total["runs"] > 0 else 0
            bg_data["avg_win_rate"] = round(bg_avg_wr, 4)
            for style in ["leader", "midfield", "closer"]:
                sd = barrier_style_wins[bg][style]
                wr = sd["wins"] / sd["runs"] if sd["runs"] >= 10 else None
                bg_data[style] = {
                    "win_rate": round(wr, 4) if wr is not None else None,
                    "runs": sd["runs"],
                }
            barrier_heatmap[bg] = bg_data

        winning_l200 = sect_by_td.get(td_key, [])
        if winning_l200:
            winning_sectional_profile = {
                "last_200m_median": round(float(np.median(winning_l200)), 3),
                "last_200m_mean": round(float(np.mean(winning_l200)), 3),
                "sample_size": len(winning_l200),
            }
        else:
            winning_sectional_profile = None

        # For each race, rank by sp_odds ascending. Winner with rank > 3 = upset
        race_groups = defaultdict(list)
        for r in runners:
            rk = f"{r['race_date']}_{r['race_number']}"
            race_groups[rk].append(r)

        n_upsets = 0
        n_decided = 0
        for rk, race_runners in race_groups.items():
            winner = None
            for rr in race_runners:
                if rr["position"] == 1:
                    winner = rr
                    break
            if not winner or not winner.get("sp_odds"):
                continue

            priced = [rr for rr in race_runners if rr.get("sp_odds") and float(rr["sp_odds"]) > 1]
            priced.sort(key=lambda x: float(x["sp_odds"]))
            winner_rank = None
            for i, rr in enumerate(priced):
                if (rr.get("horse_name", "").lower().strip() ==
                        winner.get("horse_name", "").lower().strip()):
                    winner_rank = i + 1
                    break
            if winner_rank is not None:
                n_decided += 1
                if winner_rank > 3:
                    n_upsets += 1

        upset_rate = round(n_upsets / n_decided, 4) if n_decided > 0 else 0.2

        # --- Closing speed bias (normalised) ---
        # Will be z-scored across all track-distances after the loop
        closing_speed_raw = None
        if winning_sectional_profile:
            closing_speed_raw = winning_sectional_profile["last_200m_median"]

        profiles[td_key] = {
            "insufficient_data": False,
            "sample_size": len(runners),
            "n_races": n_races,
            "track": track_name,
            "distance_m": dist_m,
            "pace_bias": pace_bias,
            "barrier_heatmap": barrier_heatmap,
            "winning_sectional_profile": winning_sectional_profile,
            "upset_rate": upset_rate,
            "_closing_speed_raw": closing_speed_raw,
        }

    closing_speeds = [
        (k, p["_closing_speed_raw"])
        for k, p in profiles.items()
        if not p.get("insufficient_data") and p.get("_closing_speed_raw") is not None
    ]
    if closing_speeds:
        values = [cs for _, cs in closing_speeds]
        mean_cs = float(np.mean(values))
        std_cs = float(np.std(values)) if len(values) > 1 else 1.0
        for k, raw in closing_speeds:
            if std_cs > 0:
                profiles[k]["closing_speed_bias"] = round((raw - mean_cs) / std_cs, 4)
            else:
                profiles[k]["closing_speed_bias"] = 0.0

    for k, p in profiles.items():
        p.pop("_closing_speed_raw", None)

    for k, p in profiles.items():
        if not p.get("insufficient_data") and "closing_speed_bias" not in p:
            p["closing_speed_bias"] = 0.0

    print(f"  Profiles built: {n_sufficient} sufficient, {n_insufficient} insufficient (<{MIN_RUNNERS} runners)",
          file=sys.stderr)
    return profiles


def save_profiles(profiles: Dict[str, Any]):
    """Save profiles to intelligence directory."""
    os.makedirs(INTELLIGENCE_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(profiles, f, indent=2, default=str)
    print(f"  Saved to {OUTPUT_PATH}", file=sys.stderr)


def _month_range(start_month: str, end_month: str):
    """Inclusive 'YYYY-MM' iteration, stdlib-only."""
    y, m = int(start_month[:4]), int(start_month[5:7])
    ey, em = int(end_month[:4]), int(end_month[5:7])
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m > 12:
            m, y = 1, y + 1


def build_profiles_buckets(conn, start_month: str, end_month: str) -> Dict[str, Any]:
    """One as-of profile set per month in [start_month, end_month] (YYYY-MM).

    Bucket M is built with as_of = M-01, i.e. contains only races with
    race_date < M-01, so any training row in month M is scored against data
    strictly before its own race.
    """
    combined: Dict[str, Any] = {}
    for month in _month_range(start_month, end_month):
        print(f"  Building as-of bucket {month} (races < {month}-01)...", file=sys.stderr)
        combined[month] = build_profiles(conn, as_of=f"{month}-01")
    combined["_meta"] = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "as_of_rule": "bucket M contains only races with race_date < M-01",
    }
    return combined


def save_profiles_asof(combined: Dict[str, Any]):
    """Save the monthly-bucketed as-of profiles to the intelligence directory."""
    os.makedirs(INTELLIGENCE_DIR, exist_ok=True)
    with open(ASOF_OUTPUT_PATH, "w") as f:
        json.dump(combined, f, indent=2, default=str)
    print(f"  Saved to {ASOF_OUTPUT_PATH}", file=sys.stderr)


def load_profiles_asof(path: str = ASOF_OUTPUT_PATH) -> Dict[str, Any]:
    """Load monthly-bucketed as-of profiles; {} when absent or invalid.

    Strips "_meta" so the result maps "YYYY-MM" -> profiles dict only.
    """
    try:
        with open(path) as f:
            combined = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(combined, dict):
        return {}
    combined.pop("_meta", None)
    return combined


def select_bucket(asof_buckets: Dict[str, Any], race_date) -> Dict[str, Any]:
    """Profile dict strictly as-of a training row's race date.

    Bucket "YYYY-MM" holds only races before that month's 1st, so the row's own
    race (and its future) can never be inside the aggregate. Missing bucket or
    unparseable date returns {} -> lookup_profile yields the no-data defaults.
    """
    rd = pd.to_datetime(race_date, errors="coerce")
    if pd.isna(rd):
        return {}
    return asof_buckets.get(rd.strftime("%Y-%m"), {}) or {}


def load_td_profiles_for_training(script_dir: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load td profiles for retraining: (asof_buckets, legacy_profiles).

    Exactly one of the two is non-empty. Prefers the monthly as-of buckets;
    falls back to the legacy single snapshot with a loud warning because that
    snapshot leaks (each row's own result is inside the aggregate). Both absent
    -> ({}, {}) and the caller fills the no-data defaults.
    """
    asof = load_profiles_asof(os.path.join(
        script_dir, "intelligence", "track_distance_profiles_asof.json"))
    if asof:
        print(f"  As-of track-distance profiles loaded: {len(asof)} monthly buckets")
        return asof, {}

    legacy_path = os.path.join(script_dir, "intelligence", "track_distance_profiles.json")
    if os.path.exists(legacy_path):
        try:
            with open(legacy_path) as f:
                legacy = json.load(f)
            print(f"  Track-distance profiles loaded: {len(legacy)} entries")
            print("  WARNING: td profiles are NOT as-of (legacy snapshot) — known leak")
            return {}, legacy
        except Exception as e:
            print(f"  WARNING: Track-distance profiles load failed: {e}")
    return {}, {}


def lookup_profile(
    profiles: Dict[str, Any],
    track: str,
    distance_m: int,
    barrier: Optional[int] = None,
    running_style: Optional[str] = None,
) -> Dict[str, float]:
    """
    Look up track-distance profile features for a single horse.

    Returns dict with ML-ready features:
      - td_pace_bias
      - td_upset_rate
      - td_barrier_style_edge (per-horse, needs barrier + style)
      - td_closing_speed_bias
    """
    key = f"{_normalise_track(track)}_{distance_m}"
    profile = profiles.get(key, {})

    if profile.get("insufficient_data", True):
        return {
            "td_pace_bias": 0.5,
            "td_upset_rate": 0.2,
            "td_barrier_style_edge": 0,
            "td_closing_speed_bias": 0,
        }

    result = {
        "td_pace_bias": profile.get("pace_bias", 0.5),
        "td_upset_rate": profile.get("upset_rate", 0.2),
        "td_closing_speed_bias": profile.get("closing_speed_bias", 0),
        "td_barrier_style_edge": 0,
    }

    if barrier and running_style:
        bg = _barrier_group(int(barrier))
        heatmap = profile.get("barrier_heatmap", {})
        bg_data = heatmap.get(bg, {})
        avg_wr = bg_data.get("avg_win_rate", 0)
        style_key = running_style.lower()
        if style_key in ("leader", "front"):
            style_key = "leader"
        elif style_key in ("closer", "backmarker", "off_pace", "off pace"):
            style_key = "closer"
        else:
            style_key = "midfield"
        style_data = bg_data.get(style_key, {})
        style_wr = style_data.get("win_rate")
        if style_wr is not None and avg_wr > 0:
            result["td_barrier_style_edge"] = round(style_wr - avg_wr, 4)

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build track-distance profiles.")
    parser.add_argument("--buckets", nargs=2, metavar=("START_MONTH", "END_MONTH"),
                        help="Build monthly as-of buckets (YYYY-MM YYYY-MM) into "
                             "intelligence/track_distance_profiles_asof.json "
                             "(retrain-session run-task; heavy prod reads)")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    psycopg2, _ = _import_psycopg2()
    conn = psycopg2.connect(database_url)
    try:
        if args.buckets:
            combined = build_profiles_buckets(conn, args.buckets[0], args.buckets[1])
            save_profiles_asof(combined)
            sys.exit(0)

        profiles = build_profiles(conn)
        save_profiles(profiles)

        sufficient = {k: v for k, v in profiles.items() if not v.get("insufficient_data")}
        print(f"\n  Track-distance profiles summary:", file=sys.stderr)
        for k in sorted(sufficient.keys()):
            p = sufficient[k]
            print(f"    {k}: {p['sample_size']} runners, pace_bias={p['pace_bias']}, "
                  f"upset_rate={p['upset_rate']}, closing_bias={p.get('closing_speed_bias', 'N/A')}",
                  file=sys.stderr)
    finally:
        conn.close()

#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from .common import (
        barrier_segment,
        derive_split_position,
        ensure_output_path,
        flatten_races,
        fetch_df,
        load_racecard_meetings,
        merge_results_with_sectionals,
        normalize_going_group,
        now_iso,
        safe_int,
        write_json,
    )
except ImportError:
    from common import (
        barrier_segment,
        derive_split_position,
        ensure_output_path,
        flatten_races,
        fetch_df,
        load_racecard_meetings,
        merge_results_with_sectionals,
        normalize_going_group,
        now_iso,
        safe_int,
        write_json,
    )


STRAIGHT_DISTANCES = {1000, 1100, 1200}

RESULTS_QUERY = """
SELECT
  id,
  track,
  race_date,
  race_number,
  race_name,
  distance_m,
  race_class,
  class_level,
  going,
  horse_id,
  horse_name,
  barrier,
  position,
  field_size,
  sp_odds
FROM race_results_history
WHERE race_date::date >= %(from_date)s::date
  AND race_date::date < %(to_date)s::date
  AND LOWER(track) = LOWER('Flemington')
  AND distance_m = ANY(%(distances)s)
  AND position IS NOT NULL
  AND barrier IS NOT NULL
"""

SECTIONALS_QUERY = """
SELECT
  race_results_history_id,
  track,
  race_date,
  race_number,
  race_name,
  horse_name,
  distance_m,
  track_config,
  splits_json,
  last_200m_time,
  last_400m_time,
  last_600m_time,
  last_800m_time,
  created_at
FROM sectional_times
WHERE race_date::date >= %(from_date)s::date
  AND race_date::date < %(to_date)s::date
  AND LOWER(track) = LOWER('Flemington')
  AND distance_m = ANY(%(distances)s)
"""

TRACK_BIAS_QUERY = """
SELECT track, race_date, bias_label, confidence, races_sampled, inside_win_rate, middle_win_rate, outside_win_rate
FROM track_day_bias
WHERE LOWER(track) = LOWER('Flemington')
  AND race_date::date >= %(from_date)s::date
  AND race_date::date < %(to_date)s::date
"""


def prepare_history(target_date: str, lookback_months: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    from_date = pd.Timestamp(target_date) - pd.DateOffset(months=lookback_months)
    results = fetch_df(
        RESULTS_QUERY,
        {"from_date": from_date.date().isoformat(), "to_date": target_date, "distances": list(STRAIGHT_DISTANCES)},
    )
    sectionals = fetch_df(
        SECTIONALS_QUERY,
        {"from_date": from_date.date().isoformat(), "to_date": target_date, "distances": list(STRAIGHT_DISTANCES)},
    )
    merged = merge_results_with_sectionals(results, sectionals)
    if merged.empty:
        return merged, pd.DataFrame()

    merged["going_group"] = merged.apply(
        lambda row: normalize_going_group(row.get("going") or row.get("track_config")),
        axis=1,
    )
    merged["barrier_segment"] = merged["barrier"].map(barrier_segment)
    merged["win"] = merged["position"].astype(int).eq(1)
    merged["pos_400m"] = merged["splits_json"].apply(lambda value: derive_split_position(value, "400m"))
    merged["pos_600m"] = merged["splits_json"].apply(lambda value: derive_split_position(value, "600m"))
    merged["pos_800m"] = merged["splits_json"].apply(lambda value: derive_split_position(value, "800m"))
    merged["pace_role"] = merged.apply(classify_pace_role, axis=1)

    bias = fetch_df(
        TRACK_BIAS_QUERY,
        {"from_date": from_date.date().isoformat(), "to_date": target_date},
    )
    return merged, bias


def classify_pace_role(row: pd.Series) -> str:
    pos_600 = safe_int(row.get("pos_600m"))
    field_size = safe_int(row.get("field_size")) or 0
    if pos_600 is None or field_size <= 0:
        return "Unknown"
    percentile = pos_600 / max(field_size, 1)
    if pos_600 <= 2 or percentile <= 0.25:
        return "Front"
    if percentile <= 0.55:
        return "Midfield"
    return "Back"


def choose_cohort(history: pd.DataFrame, race: dict[str, Any]) -> tuple[pd.DataFrame, str]:
    rules = [
        (
            "exact distance + going",
            (history["distance_m"] == race["distance_m"]) & (history["going_group"] == race["going_group"]),
            72,
        ),
        (
            "exact distance",
            history["distance_m"] == race["distance_m"],
            48,
        ),
        (
            "all straight races",
            history["distance_m"].isin(sorted(STRAIGHT_DISTANCES)),
            144,
        ),
    ]
    for label, mask, minimum in rules:
        cohort = history.loc[mask].copy()
        if len(cohort.index) >= minimum:
            return cohort, label
    return history.copy(), "all Flemington straight fallback"


def lane_stats(cohort: pd.DataFrame) -> list[dict[str, Any]]:
    base_win_rate = cohort["win"].mean() * 100.0 if not cohort.empty else 0.0
    rows = []
    for segment, group in cohort.groupby("barrier_segment", dropna=False):
        win_rate = group["win"].mean() * 100.0 if len(group.index) else 0.0
        rows.append(
            {
                "segment": segment,
                "runs": int(len(group.index)),
                "wins": int(group["win"].sum()),
                "win_rate": round(win_rate, 2),
                "iv": round(win_rate / base_win_rate, 2) if base_win_rate > 0 else None,
            }
        )
    return rows


def pace_stats(cohort: pd.DataFrame) -> dict[str, Any]:
    rows = []
    for role, group in cohort.groupby("pace_role", dropna=False):
        if role == "Unknown":
            continue
        rows.append(
            {
                "pace_role": role,
                "runs": int(len(group.index)),
                "wins": int(group["win"].sum()),
                "win_rate": round(group["win"].mean() * 100.0 if len(group.index) else 0.0, 2),
            }
        )
    last600 = cohort.dropna(subset=["last_600m_time"]).copy()
    fast_close = None
    if not last600.empty:
        threshold = float(last600["last_600m_time"].quantile(0.25))
        fast_group = last600.loc[last600["last_600m_time"] <= threshold]
        fast_close = {
            "threshold_last_600m_time": round(threshold, 3),
            "runs": int(len(fast_group.index)),
            "wins": int(fast_group["win"].sum()),
            "win_rate": round(fast_group["win"].mean() * 100.0 if len(fast_group.index) else 0.0, 2),
        }
    winner_600 = cohort.loc[cohort["win"] & cohort["pos_600m"].notna(), "pos_600m"]
    winner_400 = cohort.loc[cohort["win"] & cohort["pos_400m"].notna(), "pos_400m"]
    return {
        "pace_roles": rows,
        "winner_median_600m_position": round(float(winner_600.median()), 2) if not winner_600.empty else None,
        "winner_median_400m_position": round(float(winner_400.median()), 2) if not winner_400.empty else None,
        "fast_closer": fast_close,
    }


def bias_context(bias_df: pd.DataFrame) -> dict[str, Any] | None:
    if bias_df.empty:
        return None
    scoped = bias_df.sort_values("race_date", ascending=False).head(8)
    latest = scoped.iloc[0]
    return {
        "latest_bias_label": latest["bias_label"],
        "latest_confidence": latest["confidence"],
        "latest_race_date": latest["race_date"],
        "bias_label_counts": dict(Counter(scoped["bias_label"])),
    }


def build_race_payload(cohort: pd.DataFrame, race: dict[str, Any], bias_df: pd.DataFrame) -> dict[str, Any]:
    lanes = lane_stats(cohort)
    lanes_by_segment = {row["segment"]: row for row in lanes}
    runners = []
    for runner in race["runners"]:
        barrier = safe_int(runner.get("draw"))
        if barrier is None:
            continue
        segment = barrier_segment(barrier)
        segment_stats = lanes_by_segment.get(segment, {"segment": segment, "runs": 0, "wins": 0, "win_rate": 0.0, "iv": None})
        runners.append(
            {
                "horse": runner.get("horse"),
                "horse_id": runner.get("horse_id"),
                "barrier": barrier,
                "barrier_segment": segment,
                "segment_stats": segment_stats,
            }
        )
    return {
        "track": race["track"],
        "race_number": race["race_number"],
        "race_name": race["race_name"],
        "distance_m": race["distance_m"],
        "going_group": race["going_group"],
        "active_runners": len(race["runners"]),
        "cohort_rule": race["cohort_rule"],
        "cohort_sample_size": int(len(cohort.index)),
        "lane_stats": lanes,
        "pace_stats": pace_stats(cohort),
        "recent_bias_context": bias_context(bias_df),
        "runners": sorted(runners, key=lambda row: row["barrier"]),
    }


def build_payload(target_date: str, racecard_path: str | None, lookback_months: int) -> dict[str, Any]:
    meetings = load_racecard_meetings(target_date, racecard_path=racecard_path)
    all_races = flatten_races(meetings)
    straight_races = [
        race
        for race in all_races
        if race["track"] == "Flemington" and race["distance_m"] in STRAIGHT_DISTANCES
    ]
    history, bias_df = prepare_history(target_date=target_date, lookback_months=lookback_months)

    race_payloads = []
    for race in straight_races:
        cohort, rule = choose_cohort(history, race)
        race["cohort_rule"] = rule
        race_payloads.append(build_race_payload(cohort, race, bias_df))

    return {
        "report": "flemington_straight_intelligence",
        "generated_at": now_iso(),
        "target_date": target_date,
        "source_racecard": str(Path(racecard_path) if racecard_path else Path("racecards") / f"racecard_{target_date}.json"),
        "lookback_months": lookback_months,
        "straight_distances": sorted(STRAIGHT_DISTANCES),
        "race_count": len(race_payloads),
        "races": race_payloads,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Flemington straight-course intelligence.")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--racecard", default=None)
    parser.add_argument("--lookback-months", type=int, default=18)
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(
        target_date=args.date,
        racecard_path=args.racecard,
        lookback_months=args.lookback_months,
    )
    output_path = ensure_output_path(
        f"flemington_straight_intelligence_{args.date}.json",
        output_path=args.output,
    )
    write_json(payload, output_path)
    print(output_path)


if __name__ == "__main__":
    main()

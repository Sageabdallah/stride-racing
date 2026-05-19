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
        class_bucket,
        distance_bucket,
        ensure_output_path,
        field_size_band,
        flatten_races,
        fetch_df,
        load_racecard_meetings,
        now_iso,
        normalize_going_group,
        normalize_track,
        parse_class_score,
        safe_float,
        safe_int,
        write_json,
    )
except ImportError:
    from common import (
        barrier_segment,
        class_bucket,
        distance_bucket,
        ensure_output_path,
        field_size_band,
        flatten_races,
        fetch_df,
        load_racecard_meetings,
        now_iso,
        normalize_going_group,
        normalize_track,
        parse_class_score,
        safe_float,
        safe_int,
        write_json,
    )


RESULTS_QUERY = """
SELECT
  track,
  race_date,
  race_number,
  race_name,
  distance_m,
  race_class,
  class_level,
  going,
  position,
  barrier,
  field_size,
  horse_id,
  horse_name,
  sp_odds
FROM race_results_history
WHERE race_date::date >= %(from_date)s::date
  AND race_date::date < %(to_date)s::date
  AND barrier IS NOT NULL
  AND barrier > 0
  AND position IS NOT NULL
  AND field_size IS NOT NULL
"""

TRACK_BIAS_QUERY = """
SELECT
  track,
  race_date,
  bias_label,
  confidence,
  races_sampled,
  inside_win_rate,
  middle_win_rate,
  outside_win_rate,
  avg_winner_barrier_ratio
FROM track_day_bias
WHERE race_date::date >= %(from_date)s::date
  AND race_date::date < %(to_date)s::date
"""


def prepare_history(target_date: str, lookback_months: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    from_date = pd.Timestamp(target_date) - pd.DateOffset(months=lookback_months)
    results = fetch_df(
        RESULTS_QUERY,
        {"from_date": from_date.date().isoformat(), "to_date": target_date},
    )
    if results.empty:
        return results, pd.DataFrame()

    results["track_key"] = results["track"].map(normalize_track)
    results["distance_bucket"] = results["distance_m"].map(distance_bucket)
    results["field_size_band"] = results["field_size"].map(field_size_band)
    results["going_group"] = results["going"].map(normalize_going_group)
    results["class_score"] = results.apply(lambda row: parse_class_score(row.get("race_class"), row.get("class_level")), axis=1)
    results["class_bucket"] = results["class_score"].map(class_bucket)
    results["barrier_segment"] = results["barrier"].map(barrier_segment)
    results["win"] = results["position"].astype(int).eq(1)
    results["place"] = results["position"].astype(int).le(3)

    bias = fetch_df(
        TRACK_BIAS_QUERY,
        {"from_date": from_date.date().isoformat(), "to_date": target_date},
    )
    if not bias.empty:
        bias["track_key"] = bias["track"].map(normalize_track)
    return results, bias


def choose_cohort(history: pd.DataFrame, race: dict[str, Any]) -> tuple[pd.DataFrame, str]:
    field_band = field_size_band(len(race["runners"]))
    class_group = class_bucket(race["class_score"])
    filters = [
        (
            "track + exact distance + field band + going",
            (history["track_key"] == race["track_key"])
            & (history["distance_m"] == race["distance_m"])
            & (history["field_size_band"] == field_band)
            & (history["going_group"] == race["going_group"]),
            60,
        ),
        (
            "track + exact distance + field band",
            (history["track_key"] == race["track_key"])
            & (history["distance_m"] == race["distance_m"])
            & (history["field_size_band"] == field_band),
            48,
        ),
        (
            "track + exact distance",
            (history["track_key"] == race["track_key"]) & (history["distance_m"] == race["distance_m"]),
            36,
        ),
        (
            "track + distance bucket + class bucket",
            (history["track_key"] == race["track_key"])
            & (history["distance_bucket"] == distance_bucket(race["distance_m"]))
            & (history["class_bucket"] == class_group),
            72,
        ),
        (
            "track + distance bucket",
            (history["track_key"] == race["track_key"])
            & (history["distance_bucket"] == distance_bucket(race["distance_m"])),
            96,
        ),
        (
            "track only",
            history["track_key"] == race["track_key"],
            160,
        ),
    ]
    for label, mask, minimum in filters:
        cohort = history.loc[mask].copy()
        if len(cohort.index) >= minimum:
            return cohort, label
    return history.loc[history["track_key"] == race["track_key"]].copy(), "track only fallback"


def build_bias_context(bias_df: pd.DataFrame, race: dict[str, Any]) -> dict[str, Any] | None:
    if bias_df.empty:
        return None
    scoped = bias_df.loc[bias_df["track_key"] == race["track_key"]].sort_values("race_date", ascending=False).head(8)
    if scoped.empty:
        return None
    latest = scoped.iloc[0]
    return {
        "latest_bias_label": latest["bias_label"],
        "latest_confidence": latest["confidence"],
        "latest_race_date": latest["race_date"],
        "bias_label_counts": dict(Counter(scoped["bias_label"])),
    }


def barrier_note(barrier: int, barrier_stats: dict[str, Any], segment_stats: dict[str, Any]) -> str:
    source = barrier_stats if barrier_stats.get("runs", 0) >= 8 else segment_stats
    sample = source.get("runs", 0) or 0
    iv = source.get("iv")
    win_rate = source.get("win_rate", 0.0)
    if sample <= 0 or iv is None:
        return f"Barrier {barrier} has no meaningful local sample in the selected cohort."
    if iv >= 1.15:
        return f"Barrier {barrier} profiles as a positive draw in this cohort ({win_rate:.1f}% win rate, IV {iv:.2f}, n={sample})."
    if iv <= 0.85:
        return f"Barrier {barrier} has underperformed in this cohort ({win_rate:.1f}% win rate, IV {iv:.2f}, n={sample})."
    return f"Barrier {barrier} reads neutral in this cohort ({win_rate:.1f}% win rate, IV {iv:.2f}, n={sample})."


def summarize_cohort(cohort: pd.DataFrame, race: dict[str, Any], bias_df: pd.DataFrame) -> dict[str, Any]:
    base_win_rate = cohort["win"].mean() * 100.0 if not cohort.empty else 0.0
    segment_rows = []
    for segment, group in cohort.groupby("barrier_segment", dropna=False):
        wins = int(group["win"].sum())
        runs = int(len(group.index))
        win_rate = (wins / runs * 100.0) if runs else 0.0
        segment_rows.append(
            {
                "segment": segment,
                "runs": runs,
                "wins": wins,
                "places": int(group["place"].sum()),
                "win_rate": round(win_rate, 2),
                "place_rate": round(group["place"].mean() * 100.0 if runs else 0.0, 2),
                "iv": round(win_rate / base_win_rate, 2) if base_win_rate > 0 else None,
            }
        )
    segment_lookup = {row["segment"]: row for row in segment_rows}

    barrier_rows = []
    for barrier, group in cohort.groupby("barrier"):
        wins = int(group["win"].sum())
        runs = int(len(group.index))
        win_rate = (wins / runs * 100.0) if runs else 0.0
        barrier_rows.append(
            {
                "barrier": int(barrier),
                "runs": runs,
                "wins": wins,
                "win_rate": round(win_rate, 2),
                "iv": round(win_rate / base_win_rate, 2) if base_win_rate > 0 else None,
            }
        )
    barrier_lookup = {row["barrier"]: row for row in barrier_rows}

    runners = []
    for runner in race["runners"]:
        barrier = safe_int(runner.get("draw"))
        if barrier is None:
            continue
        segment = barrier_segment(barrier)
        barrier_stats = barrier_lookup.get(barrier, {"barrier": barrier, "runs": 0, "wins": 0, "win_rate": 0.0, "iv": None})
        segment_stats = segment_lookup.get(segment, {"segment": segment, "runs": 0, "wins": 0, "win_rate": 0.0, "iv": None})
        runners.append(
            {
                "horse": runner.get("horse"),
                "horse_id": runner.get("horse_id"),
                "barrier": barrier,
                "barrier_segment": segment,
                "barrier_stats": barrier_stats,
                "segment_stats": segment_stats,
                "note": barrier_note(barrier, barrier_stats, segment_stats),
            }
        )

    return {
        "track": race["track"],
        "race_number": race["race_number"],
        "race_name": race["race_name"],
        "distance_m": race["distance_m"],
        "race_class": race["race_class"],
        "going_group": race["going_group"],
        "active_runners": len(race["runners"]),
        "cohort_sample_size": int(len(cohort.index)),
        "cohort_rule": race["cohort_rule"],
        "cohort_base_win_rate": round(base_win_rate, 2),
        "barrier_segments": segment_rows,
        "top_barriers": barrier_rows[:8],
        "recent_bias_context": build_bias_context(bias_df, race),
        "runners": sorted(runners, key=lambda row: row["barrier"]),
    }


def build_payload(target_date: str, racecard_path: str | None, lookback_months: int) -> dict[str, Any]:
    meetings = load_racecard_meetings(target_date, racecard_path=racecard_path)
    today_races = flatten_races(meetings)
    history, bias_df = prepare_history(target_date=target_date, lookback_months=lookback_months)

    race_payloads = []
    for race in today_races:
        cohort, cohort_rule = choose_cohort(history, race)
        race["cohort_rule"] = cohort_rule
        race_payloads.append(summarize_cohort(cohort, race, bias_df))

    return {
        "report": "barrier_intelligence",
        "generated_at": now_iso(),
        "target_date": target_date,
        "source_racecard": str(Path(racecard_path) if racecard_path else Path("racecards") / f"racecard_{target_date}.json"),
        "lookback_months": lookback_months,
        "tracks": sorted({race["track"] for race in race_payloads}),
        "race_count": len(race_payloads),
        "races": race_payloads,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build barrier intelligence for today's racecard meetings.")
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
        f"barrier_intelligence_{args.date}.json",
        output_path=args.output,
    )
    write_json(payload, output_path)
    print(output_path)


if __name__ == "__main__":
    main()

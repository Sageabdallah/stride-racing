#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from .common import (
        class_bucket,
        distance_bucket,
        ensure_output_path,
        flatten_races,
        fetch_df,
        load_racecard_meetings,
        normalize_name,
        now_iso,
        parse_class_score,
        safe_float,
        write_json,
    )
except ImportError:
    from common import (
        class_bucket,
        distance_bucket,
        ensure_output_path,
        flatten_races,
        fetch_df,
        load_racecard_meetings,
        normalize_name,
        now_iso,
        parse_class_score,
        safe_float,
        write_json,
    )


RESULTS_QUERY = """
SELECT
  horse_id,
  horse_name,
  track,
  race_date,
  race_number,
  distance_m,
  race_class,
  class_level,
  position,
  sp_odds
FROM race_results_history
WHERE race_date::date >= %(from_date)s::date
  AND race_date::date < %(to_date)s::date
  AND position IS NOT NULL
"""


def prepare_sequences(target_date: str, lookback_months: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    from_date = pd.Timestamp(target_date) - pd.DateOffset(months=lookback_months)
    results = fetch_df(
        RESULTS_QUERY,
        {"from_date": from_date.date().isoformat(), "to_date": target_date},
    )
    if results.empty:
        return results, results

    results["horse_key"] = results["horse_id"].fillna("").astype(str)
    missing_id = results["horse_key"].eq("")
    results.loc[missing_id, "horse_key"] = results.loc[missing_id, "horse_name"].map(normalize_name)
    results["race_date_dt"] = pd.to_datetime(results["race_date"], errors="coerce")
    results["class_score"] = results.apply(lambda row: parse_class_score(row.get("race_class"), row.get("class_level")), axis=1)
    results["class_bucket"] = results["class_score"].map(class_bucket)
    results["distance_bucket"] = results["distance_m"].map(distance_bucket)
    results["win"] = results["position"].astype(int).eq(1)
    results["place"] = results["position"].astype(int).le(3)
    results = results.sort_values(["horse_key", "race_date_dt", "race_number"]).reset_index(drop=True)

    grouped = results.groupby("horse_key", group_keys=False)
    results["prev_distance_m"] = grouped["distance_m"].shift(1)
    results["prev_class_score"] = grouped["class_score"].shift(1)
    results["prev_race_date"] = grouped["race_date_dt"].shift(1)
    results["prev_track"] = grouped["track"].shift(1)
    results["prev_position"] = grouped["position"].shift(1)
    results["distance_delta_m"] = results["distance_m"] - results["prev_distance_m"]
    results["class_delta"] = results["class_score"] - results["prev_class_score"]
    results["distance_move_bucket"] = results["distance_delta_m"].map(distance_move_bucket)
    results["class_move_bucket"] = results["class_delta"].map(class_move_bucket)

    cohort_stats = (
        results.dropna(subset=["prev_distance_m", "prev_class_score"])
        .groupby(["class_bucket", "distance_bucket", "class_move_bucket", "distance_move_bucket"], dropna=False)
        .agg(
            starts=("horse_key", "count"),
            wins=("win", "sum"),
            places=("place", "sum"),
            avg_sp_odds=("sp_odds", "mean"),
        )
        .reset_index()
    )
    cohort_stats["win_rate"] = (cohort_stats["wins"] / cohort_stats["starts"] * 100.0).round(2)
    cohort_stats["place_rate"] = (cohort_stats["places"] / cohort_stats["starts"] * 100.0).round(2)
    cohort_stats["avg_sp_odds"] = cohort_stats["avg_sp_odds"].round(2)
    return results, cohort_stats


def distance_move_bucket(delta_m: Any) -> str:
    if pd.isna(delta_m):
        return "No prior run"
    value = float(delta_m)
    if value <= -250:
        return "Sharp drop"
    if value <= -100:
        return "Step back"
    if value < 100:
        return "Similar trip"
    if value < 250:
        return "Step up"
    return "Sharp rise"


def class_move_bucket(delta: Any) -> str:
    if pd.isna(delta):
        return "No prior run"
    value = float(delta)
    if value <= -8:
        return "Class drop"
    if value >= 8:
        return "Class rise"
    return "Same class"


def build_runner_rows(race: dict[str, Any], history: pd.DataFrame, cohort_stats: pd.DataFrame, target_date: str) -> list[dict[str, Any]]:
    rows = []
    race_class_score = parse_class_score(race["race_class"])
    race_class_bucket = class_bucket(race_class_score)
    race_distance_bucket = distance_bucket(race["distance_m"])
    for runner in race["runners"]:
        horse_id = runner.get("horse_id")
        horse_name = runner.get("horse")
        prior = history.loc[
            (history["horse_id"].eq(horse_id))
            | ((history["horse_id"].isna() | history["horse_id"].eq("")) & history["horse_name"].map(normalize_name).eq(normalize_name(horse_name)))
        ].sort_values(["race_date_dt", "race_number"], ascending=[False, False]).head(1)
        if prior.empty:
            rows.append(
                {
                    "horse": horse_name,
                    "horse_id": horse_id,
                    "history_found": False,
                    "note": "No prior run found in race_results_history inside the 18-month window.",
                }
            )
            continue
        last_start = prior.iloc[0]
        distance_delta = (race["distance_m"] or 0) - (last_start["distance_m"] or 0)
        class_delta = race_class_score - last_start["class_score"]
        runner_distance_bucket = distance_move_bucket(distance_delta)
        runner_class_bucket = class_move_bucket(class_delta)
        pattern = cohort_stats.loc[
            (cohort_stats["class_bucket"] == race_class_bucket)
            & (cohort_stats["distance_bucket"] == race_distance_bucket)
            & (cohort_stats["class_move_bucket"] == runner_class_bucket)
            & (cohort_stats["distance_move_bucket"] == runner_distance_bucket)
        ].head(1)
        pattern_row = pattern.iloc[0].to_dict() if not pattern.empty else {}
        starts = int(pattern_row.get("starts", 0) or 0)
        note = (
            f"Last start {last_start['race_date']} at {last_start['track']} over {int(last_start['distance_m'])}m "
            f"({last_start['race_class']}); today maps to {runner_class_bucket.lower()} + {runner_distance_bucket.lower()}."
        )
        if starts >= 20:
            note += f" Historical cohort strike rate: {pattern_row['win_rate']:.2f}% wins / {pattern_row['place_rate']:.2f}% places from {starts} starts."
        else:
            note += " Historical cohort is thin; treat the pattern as descriptive rather than strong evidence."
        rows.append(
            {
                "horse": horse_name,
                "horse_id": horse_id,
                "history_found": True,
                "last_start": {
                    "race_date": last_start["race_date"],
                    "track": last_start["track"],
                    "distance_m": int(last_start["distance_m"]),
                    "race_class": last_start["race_class"],
                    "position": int(last_start["position"]),
                    "sp_odds": safe_float(last_start["sp_odds"]),
                },
                "today_class_bucket": race_class_bucket,
                "today_distance_bucket": race_distance_bucket,
                "class_move_bucket": runner_class_bucket,
                "distance_move_bucket": runner_distance_bucket,
                "historical_pattern": {
                    "starts": starts,
                    "wins": int(pattern_row.get("wins", 0) or 0),
                    "places": int(pattern_row.get("places", 0) or 0),
                    "win_rate": pattern_row.get("win_rate"),
                    "place_rate": pattern_row.get("place_rate"),
                    "avg_sp_odds": pattern_row.get("avg_sp_odds"),
                },
                "note": note,
            }
        )
    return rows


def build_payload(target_date: str, racecard_path: str | None, lookback_months: int) -> dict[str, Any]:
    meetings = load_racecard_meetings(target_date, racecard_path=racecard_path)
    today_races = flatten_races(meetings)
    history, cohort_stats = prepare_sequences(target_date=target_date, lookback_months=lookback_months)
    races = []
    for race in today_races:
        race_class_score = parse_class_score(race["race_class"])
        races.append(
            {
                "track": race["track"],
                "race_number": race["race_number"],
                "race_name": race["race_name"],
                "distance_m": race["distance_m"],
                "race_class": race["race_class"],
                "today_class_bucket": class_bucket(race_class_score),
                "today_distance_bucket": distance_bucket(race["distance_m"]),
                "runners": build_runner_rows(race, history, cohort_stats, target_date),
            }
        )
    return {
        "report": "class_distance_patterns",
        "generated_at": now_iso(),
        "target_date": target_date,
        "source_racecard": str(Path(racecard_path) if racecard_path else Path("racecards") / f"racecard_{target_date}.json"),
        "lookback_months": lookback_months,
        "sequence_rows": int(len(history.index)),
        "pattern_rows": int(len(cohort_stats.index)),
        "race_count": len(races),
        "races": races,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build class-distance pattern intelligence for today's racecard meetings.")
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
        f"class_distance_patterns_{args.date}.json",
        output_path=args.output,
    )
    write_json(payload, output_path)
    print(output_path)


if __name__ == "__main__":
    main()

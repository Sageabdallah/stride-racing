#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    import sys

    sys.path.append(str(Path(__file__).resolve().parent))
    from common import build_output_path, build_parser, fetch_all_dicts, get_connection, horse_key, load_target_runners, parse_iso_date, write_json
else:
    from .common import build_output_path, build_parser, fetch_all_dicts, get_connection, horse_key, load_target_runners, parse_iso_date, write_json


def franking_score(wins: int, places: int, next_runs: int, avg_finish: float | None) -> float:
    if next_runs <= 0:
        return 0.0
    raw = (wins * 3.0) + (places * 1.5)
    if avg_finish is not None:
        raw += max(0.0, 6.0 - avg_finish) * 0.35
    return round(min(100.0, (raw / (next_runs * 3.0)) * 100.0), 1)


def build_payload(target_date: str, track_filter: str | None, statement_timeout_ms: int) -> dict[str, Any]:
    runners = load_target_runners(target_date, track_filter)
    target_dt = parse_iso_date(target_date)
    history_start = (target_dt - timedelta(days=540)).isoformat() if target_dt else "2024-10-01"

    horse_ids = sorted({runner["horse_id"] for runner in runners if runner.get("horse_id")})
    horse_name_norms = sorted({runner["horse_name_norm"] for runner in runners})

    conn = get_connection(statement_timeout_ms)
    try:
        history_rows = fetch_all_dicts(
            conn,
            """
            SELECT
                id,
                horse_id,
                horse_name,
                race_id,
                race_date,
                race_number,
                track,
                race_name,
                position,
                margin_lengths,
                class_level,
                field_size,
                sp_odds
            FROM race_results_history
            WHERE race_date::date < %(target_date)s::date
              AND race_date::date >= %(history_start)s::date
              AND (
                (horse_id IS NOT NULL AND horse_id = ANY(%(horse_ids)s))
                OR lower(regexp_replace(coalesce(horse_name, ''), '[^a-z0-9]+', '', 'g')) = ANY(%(horse_name_norms)s)
              )
            ORDER BY race_date::date DESC, race_number DESC, id DESC
            """,
            {
                "target_date": target_date,
                "history_start": history_start,
                "horse_ids": horse_ids or [""],
                "horse_name_norms": horse_name_norms or [""],
            },
        )
        history_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in history_rows:
            history_by_key[horse_key(row.get("horse_id"), row.get("horse_name"))].append(row)

        recent_runs: list[dict[str, Any]] = []
        race_ids: set[str] = set()
        for key, rows in history_by_key.items():
            for row in rows[:3]:
                row_copy = dict(row)
                row_copy["target_horse_key"] = key
                recent_runs.append(row_copy)
                if row.get("race_id"):
                    race_ids.add(str(row["race_id"]))

        opponent_rows = fetch_all_dicts(
            conn,
            """
            SELECT
                id,
                horse_id,
                horse_name,
                race_id,
                race_date,
                race_number,
                track,
                race_name,
                position,
                margin_lengths,
                class_level,
                sp_odds
            FROM race_results_history
            WHERE race_id = ANY(%(race_ids)s)
            ORDER BY race_date::date ASC, race_number ASC, id ASC
            """,
            {"race_ids": sorted(race_ids) or [""]},
        )

        opponents_by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
        opponent_keys: set[str] = set()
        min_source_date = target_date
        for row in opponent_rows:
            race_id = str(row.get("race_id") or "")
            opponents_by_race[race_id].append(row)
            key = horse_key(row.get("horse_id"), row.get("horse_name"))
            opponent_keys.add(key)
            race_date = str(row.get("race_date") or "")[:10]
            if race_date and race_date < min_source_date:
                min_source_date = race_date

        opponent_ids = sorted({row.get("horse_id") for row in opponent_rows if row.get("horse_id")})
        opponent_norms = sorted(
            {
                horse_key(row.get("horse_id"), row.get("horse_name"))
                for row in opponent_rows
                if not row.get("horse_id")
            }
        )
        future_rows = fetch_all_dicts(
            conn,
            """
            SELECT
                id,
                horse_id,
                horse_name,
                race_id,
                race_date,
                race_number,
                track,
                race_name,
                position,
                margin_lengths,
                class_level,
                sp_odds
            FROM race_results_history
            WHERE race_date::date > %(min_source_date)s::date
              AND race_date::date < %(target_date)s::date
              AND (
                (horse_id IS NOT NULL AND horse_id = ANY(%(horse_ids)s))
                OR lower(regexp_replace(coalesce(horse_name, ''), '[^a-z0-9]+', '', 'g')) = ANY(%(horse_norms)s)
              )
            ORDER BY race_date::date ASC, race_number ASC, id ASC
            """,
            {
                "min_source_date": min_source_date,
                "target_date": target_date,
                "horse_ids": opponent_ids or [""],
                "horse_norms": opponent_norms or [""],
            },
        )
    finally:
        conn.close()

    future_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in future_rows:
        future_by_key[horse_key(row.get("horse_id"), row.get("horse_name"))].append(row)

    horses_payload: list[dict[str, Any]] = []
    for runner in sorted(runners, key=lambda item: (item["track"], item["race_number"], item["horse_name"])):
        target_key = runner["horse_key"]
        recent = []
        aggregate_wins = 0
        aggregate_places = 0
        aggregate_next_runs = 0
        aggregate_finishes: list[int] = []

        for run in history_by_key.get(target_key, [])[:3]:
            source_date = str(run.get("race_date") or "")[:10]
            source_race_id = str(run.get("race_id") or "")
            opponents = [
                row for row in opponents_by_race.get(source_race_id, [])
                if horse_key(row.get("horse_id"), row.get("horse_name")) != target_key
            ]
            next_run_events = []
            next_positions: list[int] = []
            wins = 0
            places = 0
            for opponent in opponents:
                opp_key = horse_key(opponent.get("horse_id"), opponent.get("horse_name"))
                next_run = None
                for candidate in future_by_key.get(opp_key, []):
                    candidate_date = str(candidate.get("race_date") or "")[:10]
                    if candidate_date > source_date:
                        next_run = candidate
                        break
                if not next_run:
                    continue
                finish = int(next_run.get("position") or 0)
                if finish == 1:
                    wins += 1
                if finish and finish <= 3:
                    places += 1
                next_positions.append(finish)
                next_run_events.append(
                    {
                        "opponent_name": opponent.get("horse_name"),
                        "source_finish": opponent.get("position"),
                        "next_run_date": str(next_run.get("race_date") or "")[:10],
                        "next_track": next_run.get("track"),
                        "next_race_number": next_run.get("race_number"),
                        "next_finish": finish,
                        "next_sp_odds": next_run.get("sp_odds"),
                    }
                )
            avg_finish = round(sum(next_positions) / len(next_positions), 2) if next_positions else None
            recent_score = franking_score(wins, places, len(next_run_events), avg_finish)
            aggregate_wins += wins
            aggregate_places += places
            aggregate_next_runs += len(next_run_events)
            aggregate_finishes.extend(next_positions)
            recent.append(
                {
                    "source_race_date": source_date,
                    "source_track": run.get("track"),
                    "source_race_number": run.get("race_number"),
                    "source_finish": run.get("position"),
                    "source_margin_lengths": run.get("margin_lengths"),
                    "opponent_count": len(opponents),
                    "opponents_with_next_run": len(next_run_events),
                    "franking_wins": wins,
                    "franking_places": places,
                    "average_next_finish": avg_finish,
                    "franking_score": recent_score,
                    "top_events": next_run_events[:5],
                }
            )

        aggregate_avg_finish = round(sum(aggregate_finishes) / len(aggregate_finishes), 2) if aggregate_finishes else None
        horses_payload.append(
            {
                "track": runner["track"],
                "race_number": runner["race_number"],
                "horse_name": runner["horse_name"],
                "horse_id": runner["horse_id"],
                "recent_runs_analyzed": len(recent),
                "form_franking_score": franking_score(
                    aggregate_wins,
                    aggregate_places,
                    aggregate_next_runs,
                    aggregate_avg_finish,
                ),
                "opponents_with_next_run": aggregate_next_runs,
                "franking_wins": aggregate_wins,
                "franking_places": aggregate_places,
                "average_opponent_next_finish": aggregate_avg_finish,
                "recent_race_franking": recent,
            }
        )

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "target_date": target_date,
        "track_filter": track_filter,
        "source_racecard": f"racecards/racecard_{target_date}.json",
        "source_tables": ["race_results_history"],
        "history_lookback_days": 540,
        "horse_count": len(runners),
        "schema_caveats": [
            "race_results_history.race_date is stored as text and is cast with ::date in all queries",
            "franking is based on opponents' next recorded run before the target date",
        ],
        "horses": horses_payload,
    }


def main() -> None:
    parser = build_parser("Build horse-level form franking intelligence for today's racecard.")
    args = parser.parse_args()
    payload = build_payload(args.date, args.track, args.statement_timeout_ms)
    output_path = build_output_path("form_franking", args.date, args.track, args.output)
    write_json(output_path, payload)
    print(output_path)


if __name__ == "__main__":
    main()

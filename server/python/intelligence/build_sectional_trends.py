#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    import sys

    sys.path.append(str(Path(__file__).resolve().parent))
    from common import build_output_path, build_parser, fetch_all_dicts, get_connection, load_target_runners, median, norm_name, parse_iso_date, safe_float, write_json
else:
    from .common import build_output_path, build_parser, fetch_all_dicts, get_connection, load_target_runners, median, norm_name, parse_iso_date, safe_float, write_json


def average_metric(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [safe_float(row.get(field)) for row in rows]
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return None
    return round(sum(cleaned) / len(cleaned), 4)


def trend_label(delta_600: float | None, delta_burst: float | None) -> str:
    if delta_600 is not None and delta_600 >= 0.15:
        return "sharpening"
    if delta_600 is not None and delta_600 <= -0.15:
        return "regressing"
    if delta_burst is not None and delta_burst >= 2.0:
        return "sharpening"
    if delta_burst is not None and delta_burst <= -2.0:
        return "regressing"
    return "flat"


def build_payload(target_date: str, track_filter: str | None, statement_timeout_ms: int) -> dict[str, Any]:
    runners = load_target_runners(target_date, track_filter)
    target_dt = parse_iso_date(target_date)
    history_start = (target_dt - timedelta(days=540)).isoformat() if target_dt else "2024-10-01"
    horse_name_norms = sorted({runner["horse_name_norm"] for runner in runners})
    runner_by_key = {runner["horse_key"]: runner for runner in runners}

    conn = get_connection(statement_timeout_ms)
    try:
        rows = fetch_all_dicts(
            conn,
            """
            SELECT
                race_results_history_id,
                track,
                race_date,
                race_number,
                race_name,
                horse_name,
                distance_m,
                last_200m_time,
                last_400m_time,
                last_600m_time,
                last_800m_time,
                finishing_burst,
                lambda_decay,
                svi,
                rsi,
                z_200m,
                z_400m,
                z_600m,
                z_800m,
                trip_cost_seconds,
                variant_adjusted_200m,
                variant_adjusted_400m,
                variant_adjusted_600m,
                variant_adjusted_800m,
                source
            FROM sectional_times
            WHERE race_date::date < %(target_date)s::date
              AND race_date::date >= %(history_start)s::date
              AND lower(regexp_replace(coalesce(horse_name, ''), '[^a-z0-9]+', '', 'g')) = ANY(%(horse_name_norms)s)
            ORDER BY race_date::date DESC, race_number DESC, race_results_history_id DESC NULLS LAST
            """,
            {
                "target_date": target_date,
                "history_start": history_start,
                "horse_name_norms": horse_name_norms or [""],
            },
        )
    finally:
        conn.close()

    history_by_norm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_races: set[tuple[str, str, int]] = set()
    for row in rows:
        horse_norm = norm_name(row.get("horse_name"))
        race_key = (horse_norm, str(row.get("race_date") or "")[:10], int(row.get("race_number") or 0))
        if race_key in seen_races:
            continue
        seen_races.add(race_key)
        history_by_norm[horse_norm].append(row)

    horses_payload: list[dict[str, Any]] = []
    for runner in sorted(runners, key=lambda item: (item["track"], item["race_number"], item["horse_name"])):
        rows_for_horse = history_by_norm.get(runner["horse_name_norm"], [])[:6]
        recent = rows_for_horse[:3]
        prior = rows_for_horse[3:6]

        recent_last600 = average_metric(recent, "last_600m_time")
        prior_last600 = average_metric(prior, "last_600m_time")
        recent_burst = average_metric(recent, "finishing_burst")
        prior_burst = average_metric(prior, "finishing_burst")
        recent_trip = average_metric(recent, "trip_cost_seconds")
        prior_trip = average_metric(prior, "trip_cost_seconds")
        delta_600 = round((prior_last600 - recent_last600), 4) if recent_last600 is not None and prior_last600 is not None else None
        delta_burst = round((recent_burst - prior_burst), 4) if recent_burst is not None and prior_burst is not None else None
        delta_trip = round((prior_trip - recent_trip), 4) if recent_trip is not None and prior_trip is not None else None

        latest = rows_for_horse[0] if rows_for_horse else None
        horses_payload.append(
            {
                "track": runner["track"],
                "race_number": runner["race_number"],
                "horse_name": runner["horse_name"],
                "horse_id": runner["horse_id"],
                "sectional_sample_count": len(rows_for_horse),
                "latest_sectional_date": str(latest.get("race_date") or "")[:10] if latest else None,
                "latest_source": latest.get("source") if latest else None,
                "latest_metrics": (
                    {
                        "last_200m_time": safe_float(latest.get("last_200m_time")),
                        "last_400m_time": safe_float(latest.get("last_400m_time")),
                        "last_600m_time": safe_float(latest.get("last_600m_time")),
                        "finishing_burst": safe_float(latest.get("finishing_burst")),
                        "trip_cost_seconds": safe_float(latest.get("trip_cost_seconds")),
                        "lambda_decay": safe_float(latest.get("lambda_decay")),
                        "z_600m": safe_float(latest.get("z_600m")),
                    }
                    if latest
                    else None
                ),
                "recent_window": {
                    "runs": len(recent),
                    "avg_last_200m_time": average_metric(recent, "last_200m_time"),
                    "avg_last_400m_time": average_metric(recent, "last_400m_time"),
                    "avg_last_600m_time": recent_last600,
                    "avg_last_800m_time": average_metric(recent, "last_800m_time"),
                    "avg_finishing_burst": recent_burst,
                    "avg_trip_cost_seconds": recent_trip,
                    "avg_lambda_decay": average_metric(recent, "lambda_decay"),
                    "avg_z_600m": average_metric(recent, "z_600m"),
                },
                "prior_window": {
                    "runs": len(prior),
                    "avg_last_600m_time": prior_last600,
                    "avg_finishing_burst": prior_burst,
                    "avg_trip_cost_seconds": prior_trip,
                },
                "trend": {
                    "late_speed_seconds_improvement": delta_600,
                    "finishing_burst_change": delta_burst,
                    "trip_efficiency_change": delta_trip,
                    "trend_label": trend_label(delta_600, delta_burst),
                },
            }
        )

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "target_date": target_date,
        "track_filter": track_filter,
        "source_racecard": f"racecards/racecard_{target_date}.json",
        "source_tables": ["sectional_times"],
        "horse_count": len(runners),
        "schema_caveats": [
            "sectional_times.race_date is text and is cast with ::date in queries",
            "sectional coverage is uneven by source; NSW PIDATA is the dominant source in the historical window",
        ],
        "horses": horses_payload,
    }


def main() -> None:
    parser = build_parser("Build horse-level sectional trend intelligence for today's racecard.")
    args = parser.parse_args()
    payload = build_payload(args.date, args.track, args.statement_timeout_ms)
    output_path = build_output_path("sectional_trends", args.date, args.track, args.output)
    write_json(output_path, payload)
    print(output_path)


if __name__ == "__main__":
    main()

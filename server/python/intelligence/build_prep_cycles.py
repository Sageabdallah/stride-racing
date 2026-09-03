#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    import sys

    sys.path.append(str(Path(__file__).resolve().parent))
    from common import build_output_path, build_parser, fetch_all_dicts, get_connection, horse_key, load_target_runners, parse_iso_date, safe_float, safe_int, write_json
else:
    from .common import build_output_path, build_parser, fetch_all_dicts, get_connection, horse_key, load_target_runners, parse_iso_date, safe_float, safe_int, write_json

import sys as _sys

_PY_ROOT = str(Path(__file__).resolve().parents[1])
if _PY_ROOT not in _sys.path:
    _sys.path.append(_PY_ROOT)
from result_margins import beaten_margin  # noqa: E402


SPELL_THRESHOLD_DAYS = 60


def split_preparations(runs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    preps: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    prev_date = None
    for run in runs:
        run_date = parse_iso_date(run.get("race_date"))
        if not run_date:
            continue
        run_copy = dict(run)
        run_copy["_run_date"] = run_date
        if prev_date is not None and (run_date - prev_date).days >= SPELL_THRESHOLD_DAYS:
            if current:
                preps.append(current)
            current = []
        current.append(run_copy)
        prev_date = run_date
    if current:
        preps.append(current)
    return preps


def prep_trend(positions: list[int], margins: list[float]) -> str:
    if len(positions) < 2:
        return "insufficient_data"
    if positions[-1] < positions[-2]:
        return "improving"
    if positions[-1] > positions[-2]:
        return "flattening_or_regressing"
    if len(margins) >= 2 and margins[-1] < margins[-2]:
        return "improving"
    return "stable"


def build_payload(target_date: str, track_filter: str | None, statement_timeout_ms: int) -> dict[str, Any]:
    runners = load_target_runners(target_date, track_filter)
    target_dt = parse_iso_date(target_date)
    history_start = (target_dt - timedelta(days=720)).isoformat() if target_dt else "2024-04-01"
    horse_ids = sorted({runner["horse_id"] for runner in runners if runner.get("horse_id")})
    horse_name_norms = sorted({runner["horse_name_norm"] for runner in runners})

    conn = get_connection(statement_timeout_ms)
    try:
        rows = fetch_all_dicts(
            conn,
            """
            SELECT
                id,
                horse_id,
                horse_name,
                race_date,
                race_number,
                track,
                distance_m,
                race_class,
                class_level,
                going,
                position,
                margin_lengths,
                sp_odds,
                weight_kg
            FROM race_results_history
            WHERE race_date::date < %(target_date)s::date
              AND race_date::date >= %(history_start)s::date
              AND (
                (horse_id IS NOT NULL AND horse_id = ANY(%(horse_ids)s))
                OR lower(regexp_replace(coalesce(horse_name, ''), '[^a-z0-9]+', '', 'g')) = ANY(%(horse_name_norms)s)
              )
            ORDER BY race_date::date ASC, race_number ASC, id ASC
            """,
            {
                "target_date": target_date,
                "history_start": history_start,
                "horse_ids": horse_ids or [""],
                "horse_name_norms": horse_name_norms or [""],
            },
        )
    finally:
        conn.close()

    history_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        history_by_key[horse_key(row.get("horse_id"), row.get("horse_name"))].append(row)

    horses_payload: list[dict[str, Any]] = []
    for runner in sorted(runners, key=lambda item: (item["track"], item["race_number"], item["horse_name"])):
        target_key = runner["horse_key"]
        runs = history_by_key.get(target_key, [])
        preps = split_preparations(runs)
        last_run_date = parse_iso_date(runs[-1].get("race_date")) if runs else None
        days_since_last_run = (target_dt - last_run_date).days if target_dt and last_run_date else None

        current_prep = preps[-1] if preps else []
        prep_start_date = current_prep[0]["_run_date"] if current_prep else None
        last_gap = None
        if len(preps) >= 2 and current_prep:
            prev_prep_last_date = preps[-2][-1]["_run_date"]
            last_gap = (current_prep[0]["_run_date"] - prev_prep_last_date).days

        if days_since_last_run is not None and days_since_last_run >= SPELL_THRESHOLD_DAYS:
            projected_run_number = 1
            completed_runs_this_prep = 0
            active_prep_positions: list[int] = []
            active_prep_margins: list[float] = []
            active_prep_start = None
            active_prep_weights: list[float] = []
            days_between: list[int] = []
        else:
            projected_run_number = len(current_prep) + 1 if current_prep else 1
            completed_runs_this_prep = len(current_prep)
            active_prep_positions = [safe_int(run.get("position")) or 0 for run in current_prep][-5:]
            # Beaten margins, 0 for a win: a winning margin must not read as lost ground.
            active_prep_margins = [safe_float(beaten_margin(run.get("position"), run.get("margin_lengths"))) or 0.0
                                   for run in current_prep][-5:]
            active_prep_start = prep_start_date.isoformat() if prep_start_date else None
            active_prep_weights = [safe_float(run.get("weight_kg")) or 0.0 for run in current_prep if safe_float(run.get("weight_kg"))]
            # Compute days between runs in current prep
            days_between = []
            for i in range(1, len(current_prep)):
                prev_d = current_prep[i - 1].get("_run_date")
                curr_d = current_prep[i].get("_run_date")
                if prev_d and curr_d:
                    days_between.append((curr_d - prev_d).days)

        # Campaign tempo
        campaign_tempo = "STANDARD"
        if days_between:
            avg_gap = sum(days_between) / len(days_between)
            if len(days_between) >= 2:
                std = (sum((d - avg_gap) ** 2 for d in days_between) / len(days_between)) ** 0.5
                if std > 15:
                    campaign_tempo = "FRAGILE"
            if campaign_tempo != "FRAGILE":
                if avg_gap <= 18:
                    campaign_tempo = "TIGHT"
                elif avg_gap <= 28:
                    campaign_tempo = "STANDARD"
                elif avg_gap <= 42:
                    campaign_tempo = "PATIENT"
                else:
                    campaign_tempo = "FRAGILE"

        # Weight penalty
        weight_penalty = 0.0
        valid_weights = [w for w in active_prep_weights if w > 0]
        if len(valid_weights) >= 2:
            kg_above = valid_weights[-1] - valid_weights[0]
            if kg_above > 0:
                weight_penalty = round(-0.15 * kg_above, 2)

        peak_run_counter: dict[int, int] = defaultdict(int)
        place_run_counter: dict[int, int] = defaultdict(int)
        for prep in preps:
            for idx, run in enumerate(prep, start=1):
                finish = safe_int(run.get("position")) or 0
                if finish == 1:
                    peak_run_counter[idx] += 1
                if finish and finish <= 3:
                    place_run_counter[idx] += 1
        historical_peak_run = None
        if peak_run_counter:
            historical_peak_run = sorted(peak_run_counter.items(), key=lambda item: (-item[1], item[0]))[0][0]
        elif place_run_counter:
            historical_peak_run = sorted(place_run_counter.items(), key=lambda item: (-item[1], item[0]))[0][0]

        horses_payload.append(
            {
                "track": runner["track"],
                "race_number": runner["race_number"],
                "horse_name": runner["horse_name"],
                "horse_id": runner["horse_id"],
                "history_runs": len(runs),
                "completed_preparations": len(preps),
                "last_run_date": last_run_date.isoformat() if last_run_date else None,
                "days_since_last_run": days_since_last_run,
                "spell_threshold_days": SPELL_THRESHOLD_DAYS,
                "projected_run_number_today": projected_run_number,
                "completed_runs_this_prep": completed_runs_this_prep,
                "active_prep_start_date": active_prep_start,
                "spell_length_into_today": days_since_last_run if projected_run_number == 1 else last_gap,
                "first_up_today": projected_run_number == 1,
                "second_up_today": projected_run_number == 2,
                "quick_backup_today": days_since_last_run is not None and 1 <= days_since_last_run <= 6,
                "prep_positions": active_prep_positions,
                "prep_margins_lengths": [round(value, 2) for value in active_prep_margins],
                "prep_trend": prep_trend(active_prep_positions, active_prep_margins),
                "historical_peak_run_number": historical_peak_run,
                "campaign_tempo": campaign_tempo,
                "weight_penalty": weight_penalty,
                "weights_this_prep": [round(w, 1) for w in valid_weights] if valid_weights else [],
            }
        )

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "target_date": target_date,
        "track_filter": track_filter,
        "source_racecard": f"racecards/racecard_{target_date}.json",
        "source_tables": ["race_results_history"],
        "schema_caveats": [
            "trainer is not available in race_results_history, so prep cycles are horse-level only",
            "race_results_history.race_date is text and is cast with ::date in queries",
        ],
        "horse_count": len(runners),
        "horses": horses_payload,
    }


def main() -> None:
    parser = build_parser("Build horse-level preparation cycle intelligence for today's racecard.")
    args = parser.parse_args()
    payload = build_payload(args.date, args.track, args.statement_timeout_ms)
    output_path = build_output_path("prep_cycles", args.date, args.track, args.output)
    write_json(output_path, payload)
    print(output_path)


if __name__ == "__main__":
    main()

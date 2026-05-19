#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    import sys

    sys.path.append(str(Path(__file__).resolve().parent))
    from common import build_output_path, build_parser, load_target_runners, median, write_json
else:
    from .common import build_output_path, build_parser, load_target_runners, median, write_json


def build_payload(target_date: str, track_filter: str | None) -> dict[str, Any]:
    runners = load_target_runners(target_date, track_filter)
    trainer_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for runner in runners:
        trainer_groups[(str(runner.get("trainer_id") or ""), str(runner.get("trainer") or ""))].append(runner)

    trainer_rows = []
    horse_rows = []
    for (trainer_id, trainer_name), trainer_runners in sorted(trainer_groups.items(), key=lambda item: (-len(item[1]), item[0][1])):
        meeting_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        race_groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        for runner in trainer_runners:
            meeting_groups[runner["track"]].append(runner)
            race_groups[(runner["track"], runner["race_number"])].append(runner)
        trainer_rows.append(
            {
                "trainer_name": trainer_name,
                "trainer_id": trainer_id or None,
                "runners_today": len(trainer_runners),
                "tracks_today": sorted(meeting_groups.keys()),
                "same_race_multi_hand_count": sum(1 for runners_in_race in race_groups.values() if len(runners_in_race) > 1),
                "median_market_odds_today": median(runner.get("market_odds") for runner in trainer_runners),
                "horse_names": [runner["horse_name"] for runner in trainer_runners],
                "historical_pattern_available": False,
                "historical_pattern_caveat": "trainer is not stored in race_results_history or training_view_v2, so only current-card stable context is available from the racecard JSON",
            }
        )
        for runner in trainer_runners:
            stable_mates_same_race = [
                mate["horse_name"]
                for mate in race_groups[(runner["track"], runner["race_number"])]
                if mate["horse_name"] != runner["horse_name"]
            ]
            horse_rows.append(
                {
                    "track": runner["track"],
                    "race_number": runner["race_number"],
                    "horse_name": runner["horse_name"],
                    "horse_id": runner["horse_id"],
                    "trainer_name": trainer_name,
                    "trainer_id": trainer_id or None,
                    "stable_runners_today": len(trainer_runners),
                    "stable_runners_same_track_today": len(meeting_groups[runner["track"]]),
                    "stable_mates_same_race": stable_mates_same_race,
                    "historical_pattern_available": False,
                }
            )

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "target_date": target_date,
        "track_filter": track_filter,
        "source_racecard": f"racecards/racecard_{target_date}.json",
        "source_tables": ["racecard_json_only"],
        "schema_caveats": [
            "trainer is present on today's racecard runners",
            "trainer is not present in race_results_history or training_view_v2, so no truthful historical trainer pattern can be computed from the allowed sources",
        ],
        "horse_count": len(runners),
        "trainer_count": len(trainer_rows),
        "trainers": trainer_rows,
        "horses": sorted(horse_rows, key=lambda row: (row["track"], row["race_number"], row["horse_name"])),
    }


def main() -> None:
    parser = build_parser("Build trainer-context intelligence for today's racecard.")
    args = parser.parse_args()
    payload = build_payload(args.date, args.track)
    output_path = build_output_path("trainer_patterns", args.date, args.track, args.output)
    write_json(output_path, payload)
    print(output_path)


if __name__ == "__main__":
    main()

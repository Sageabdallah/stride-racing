#!/usr/bin/env python3
"""
Race context score computed once per race before individual horse scoring.

Produces 6 race-level features that modify every runner's score: pace_pressure, leader/closer advantage, barrier relevance, field size context, market efficiency flag, pace clarity.
"""

import sys
from math import log2
from typing import Dict, List

try:
    from pace_modeling import classify_running_style, RunningStyle, TIGHT_TURNING_TRACKS
    PACE_AVAILABLE = True
except ImportError:
    PACE_AVAILABLE = False
    TIGHT_TURNING_TRACKS = []


# Metro tracks in Australia (used for market efficiency classification)
METRO_TRACKS = [
    "flemington", "caulfield", "moonee valley", "sandown",
    "randwick", "rosehill", "canterbury", "warwick farm",
    "eagle farm", "doomben",
    "morphettville", "cheltenham",
    "ascot", "belmont",
]


def _parse_class_level(race_class: str) -> int:
    """Parse race class string to numeric level (1=maiden, 100=Group 1)."""
    rc = (race_class or "").upper().strip()
    if "GROUP 1" in rc or "G1" in rc:
        return 100
    if "GROUP 2" in rc or "G2" in rc:
        return 90
    if "GROUP 3" in rc or "G3" in rc:
        return 80
    if "LISTED" in rc:
        return 75
    if "OPEN" in rc:
        return 65
    for prefix in ("BM", "BENCHMARK"):
        if prefix in rc:
            try:
                num = int("".join(c for c in rc.split(prefix)[-1].strip() if c.isdigit())[:3])
                return min(70, max(20, num))
            except (ValueError, IndexError):
                return 50
    if "MAIDEN" in rc:
        return 10
    if "CLASS" in rc:
        return 40
    if "RESTRICT" in rc:
        return 30
    return 50


def compute_race_context(
    runners: List[Dict],
    track: str,
    distance_m: int,
    race_class: str,
    going: str,
) -> Dict[str, float]:
    """
    Compute race-level context features once per race.

    Returns dict with 6 features to be applied to every runner.
    """
    field_size = len(runners)
    track_lower = (track or "").lower().strip()

    n_leaders = 0
    n_on_pace = 0
    style_counts = [0] * 5
    style_confidences = []

    if PACE_AVAILABLE:
        for runner in runners:
            style, conf = classify_running_style(runner)
            if style == RunningStyle.LEADER:
                n_leaders += 1
            elif style == RunningStyle.ON_PACE:
                n_on_pace += 1
            style_counts[style.value] += 1
            style_confidences.append(conf)

    if field_size > 0:
        pace_pressure_score = min(1.0, (2 * n_leaders + n_on_pace) / field_size)
    else:
        pace_pressure_score = 0.5

    # Pace clarity: how unambiguous is the likely pace scenario?
    pace_clarity_score = 0.5  # neutral default
    if PACE_AVAILABLE and field_size > 0 and style_confidences:
        # Component 1: Style distribution entropy (0=all one style, 1=uniform)
        proportions = [c / field_size for c in style_counts if c > 0]
        entropy = -sum(p * log2(p) for p in proportions)
        max_entropy = log2(5)
        norm_entropy = entropy / max_entropy if max_entropy > 0 else 0

        # Component 2: Average style classification confidence
        avg_confidence = sum(style_confidences) / len(style_confidences)

        # Component 3: Scenario dominance — how lopsided is speed vs non-speed
        speed_ratio = (n_leaders + n_on_pace) / field_size
        dominance = abs(speed_ratio - 0.5) * 2  # 0 when 50/50, 1 when all one side

        pace_clarity_score = round(
            (1 - norm_entropy) * 0.4 + avg_confidence * 0.3 + dominance * 0.3, 4
        )

    leader_advantage = round(1.0 - pace_pressure_score, 4)
    closer_advantage = round(pace_pressure_score, 4)

    if distance_m <= 1200:
        barrier_base = 1.0
    elif distance_m <= 1600:
        barrier_base = 0.7
    elif distance_m <= 2000:
        barrier_base = 0.4
    else:
        barrier_base = 0.2

    # Tight-turning tracks amplify barrier importance
    if any(t in track_lower for t in TIGHT_TURNING_TRACKS):
        barrier_base = min(1.0, barrier_base * 1.3)

    barrier_relevance_score = round(barrier_base, 4)

    # --- 4. Field size context ---
    # Small fields (<8) produce class-dominant outcomes
    # Large fields (>14) produce chaos where pace and luck matter more
    field_size_context = round(min(1.0, field_size / 16.0), 4)

    class_level = _parse_class_level(race_class)
    is_metro = any(t in track_lower for t in METRO_TRACKS)

    if class_level >= 80 and is_metro:
        market_efficiency_flag = 1.0
    elif class_level >= 60 and is_metro:
        market_efficiency_flag = 0.7
    elif class_level >= 50:
        market_efficiency_flag = 0.5
    elif class_level <= 20 and not is_metro:
        market_efficiency_flag = 0.0
    elif class_level <= 30:
        market_efficiency_flag = 0.2
    else:
        market_efficiency_flag = 0.4

    return {
        "pace_pressure_score": round(pace_pressure_score, 4),
        "leader_advantage": leader_advantage,
        "closer_advantage": closer_advantage,
        "barrier_relevance_score": barrier_relevance_score,
        "field_size_context": field_size_context,
        "market_efficiency_flag": round(market_efficiency_flag, 4),
        "pace_clarity_score": pace_clarity_score,
    }


if __name__ == "__main__":
    test_runners = [
        {"horse": "Horse A", "form": "1123", "barrier": 2},
        {"horse": "Horse B", "form": "5467", "barrier": 8},
        {"horse": "Horse C", "form": "1211", "barrier": 1},
        {"horse": "Horse D", "form": "6789", "barrier": 12},
    ]
    ctx = compute_race_context(test_runners, "Flemington", 1200, "Group 1", "Good")
    for k, v in ctx.items():
        print(f"  {k}: {v}")

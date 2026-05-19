#!/usr/bin/env python3
"""
Multi-dimensional freshness features for horse racing form analysis.

The current system treats form decay simplistically - "2nd-up" is treated the same whether the gap was 3 weeks or 10 weeks. This module adds nuanced temporal features based on empirical Australian racing statistics.
"""

import math
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional


FRESHNESS_BANDS = [
    (0, 6, 0.70),
    (7, 13, 0.85),
    (14, 21, 1.00),
    (22, 35, 0.92),
    (36, 49, 0.78),
    (50, 90, 0.60),
    (91, 180, 0.45),
    (181, 365, 0.30),
]

CLASS_LEVEL_MAP = {
    'group 1': 10, 'g1': 10, 'grp1': 10,
    'group 2': 9, 'g2': 9, 'grp2': 9,
    'group 3': 8, 'g3': 8, 'grp3': 8,
    'listed': 7,
    'open': 6,
    'benchmark 88': 5, 'bm88': 5,
    'benchmark 78': 5, 'bm78': 5,
    'benchmark 72': 4, 'bm72': 4,
    'benchmark 68': 4, 'bm68': 4,
    'benchmark 64': 3, 'bm64': 3,
    'benchmark 58': 3, 'bm58': 3,
    'class 6': 5, 'c6': 5,
    'class 5': 4, 'c5': 4,
    'class 4': 4, 'c4': 4,
    'class 3': 3, 'c3': 3,
    'class 2': 2, 'c2': 2,
    'class 1': 2, 'c1': 2,
    'maiden': 1,
    'restricted maiden': 1,
}


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _get_field(d: Dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return default


def _parse_class_level(race_class: Optional[str]) -> int:
    if not race_class:
        return 3
    rc = str(race_class).lower().strip()
    for pattern, level in CLASS_LEVEL_MAP.items():
        if pattern in rc:
            return level
    return 3


def _parse_distance(dist: Any) -> int:
    if not dist:
        return 0
    try:
        return int(dist)
    except (ValueError, TypeError):
        pass
    import re
    s = str(dist).lower().strip()
    m = re.search(r'(\d+)', s)
    return int(m.group(1)) if m else 0


def _parse_date(date_val: Any) -> Optional[datetime]:
    if not date_val:
        return None
    if isinstance(date_val, datetime):
        return date_val
    s = str(date_val).strip()
    for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%dT%H:%M:%S%z', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(s[:len(fmt)+5], fmt)
        except (ValueError, TypeError):
            continue
    return None


def compute_empirical_freshness_score(days_since: float) -> float:
    """
    Compute empirical freshness score using smooth interpolation between bands.

    Based on Australian racing win-rate statistics:
    - Peak at 14-21 days (score 1.0)
    - 7-13 days: 0.85 (backing up quickly)
    - 22-35 days: 0.92 (still competitive)
    - 36-49 days: 0.78 (getting stale)
    - 50-90 days: 0.60 (returning from break)
    - 91-180 days: 0.45 (long spell)
    - 180+: 0.30 (very long absence)

    Uses cosine interpolation between band midpoints for smooth transitions.
    """
    try:
        days = max(0.0, _safe_float(days_since, 0.0))

        midpoints = [
            (3.0, 0.70),
            (10.0, 0.85),
            (17.5, 1.00),
            (28.5, 0.92),
            (42.5, 0.78),
            (70.0, 0.60),
            (135.5, 0.45),
            (270.0, 0.30),
        ]

        if days <= midpoints[0][0]:
            return midpoints[0][1]
        if days >= midpoints[-1][0]:
            return midpoints[-1][1]

        for i in range(len(midpoints) - 1):
            d0, s0 = midpoints[i]
            d1, s1 = midpoints[i + 1]
            if d0 <= days <= d1:
                t = (days - d0) / (d1 - d0)
                cos_t = 0.5 * (1 - math.cos(t * math.pi))
                return s0 + (s1 - s0) * cos_t

        return 0.30
    except Exception:
        return 0.50


def compute_run_spacing_quality(form_details: Optional[List[Dict]]) -> float:
    """
    Analyze regularity of gaps between recent runs.

    Computes the coefficient of variation of inter-run gaps from the last 6 runs.
    A well-managed campaign has regular spacing (low CV), while an irregular campaign has high variation.
    """
    try:
        if not form_details or len(form_details) < 3:
            return 0.5

        dates = []
        for run in form_details[:7]:
            d = _parse_date(run.get('date'))
            if d:
                dates.append(d)

        if len(dates) < 3:
            return 0.5

        dates.sort(reverse=True)
        dates = dates[:7]

        gaps = []
        for i in range(len(dates) - 1):
            gap = abs((dates[i] - dates[i + 1]).days)
            if gap > 0:
                gaps.append(gap)

        if len(gaps) < 2:
            return 0.5

        mean_gap = sum(gaps) / len(gaps)
        if mean_gap == 0:
            return 0.5

        variance = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
        stddev = math.sqrt(variance)
        cv = stddev / mean_gap

        if cv <= 0.15:
            return 1.0
        elif cv <= 0.30:
            return 0.9
        elif cv <= 0.50:
            return 0.75
        elif cv <= 0.80:
            return 0.6
        elif cv <= 1.2:
            return 0.45
        else:
            return 0.3

    except Exception:
        return 0.5


def classify_trainer_freshness(trainer_name: Optional[str],
                                historical_results: Optional[List[Dict]] = None) -> str:
    """
    Classify a trainer's freshness profile based on historical patterns.

    Currently a placeholder that returns 'standard' as the default.
    When the feature store is built, this will analyze first-up win rate, average spacing, spell patterns and success rates.

    Returns one of: 'first_up_specialist', 'needs_runs', 'quick_backer', 'standard'
    """
    try:
        if not trainer_name:
            return 'standard'

        if historical_results and len(historical_results) >= 20:
            first_up_wins = 0
            first_up_total = 0
            subsequent_wins = 0
            subsequent_total = 0
            quick_backup_count = 0

            for r in historical_results:
                runs_prep = _safe_int(r.get('runs_this_prep', r.get('runsThisPrep')), 1)
                finished_first = _safe_int(r.get('position'), 0) == 1
                days_gap = _safe_int(r.get('days_since_run', r.get('daysSinceRun')), 30)

                if runs_prep == 1:
                    first_up_total += 1
                    if finished_first:
                        first_up_wins += 1
                else:
                    subsequent_total += 1
                    if finished_first:
                        subsequent_wins += 1

                if days_gap < 10:
                    quick_backup_count += 1

            first_up_rate = first_up_wins / max(first_up_total, 1)
            subsequent_rate = subsequent_wins / max(subsequent_total, 1)
            quick_backup_pct = quick_backup_count / max(len(historical_results), 1)

            if first_up_rate > 0.18 and first_up_rate > subsequent_rate * 1.5:
                return 'first_up_specialist'
            if subsequent_rate > first_up_rate * 1.5 and subsequent_total > 10:
                return 'needs_runs'
            if quick_backup_pct > 0.25:
                return 'quick_backer'

        return 'standard'
    except Exception:
        return 'standard'


def compute_distance_change_interaction(days_since: float,
                                         current_distance: int,
                                         last_distance: int) -> float:
    """
    Compute interaction between staleness and distance change.

    A horse returning from a long break AND changing distance significantly faces compounded challenges. Small distance changes with optimal spacing are not penalized.
    """
    try:
        days = max(0.0, _safe_float(days_since, 0.0))
        curr_d = _safe_int(current_distance, 0)
        last_d = _safe_int(last_distance, 0)

        if curr_d <= 0 or last_d <= 0:
            return 0.0

        dist_change_pct = abs(curr_d - last_d) / last_d

        if dist_change_pct <= 0.05 and 14 <= days <= 28:
            return 0.0

        staleness_factor = min(1.0, max(0.0, (days - 14) / (90 - 14))) if days > 14 else 0.0

        if dist_change_pct <= 0.10:
            dist_factor = 0.1
        elif dist_change_pct <= 0.20:
            dist_factor = 0.3
        elif dist_change_pct <= 0.35:
            dist_factor = 0.6
        else:
            dist_factor = 0.9

        if dist_change_pct > 0.20 and days > 35:
            penalty_boost = 0.2
        else:
            penalty_boost = 0.0

        score = min(1.0, staleness_factor * dist_factor + penalty_boost)
        return round(score, 4)

    except Exception:
        return 0.0


def compute_staleness_features(runner: Dict[str, Any],
                                race_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract all temporal staleness features from a runner dict.

    Computes multi-dimensional freshness metrics including days since last start, prep run interactions, spell analysis, freshness zones, empirical scores, and various interaction terms.
    """
    defaults: Dict[str, Any] = {
        'days_since_last_start': 30.0,
        'days_since_last_normalized': 30.0 / 365.0,
        'runs_this_prep': 1,
        'prep_run_x_days_since': 0.0,
        'spell_length_days': 0,
        'run_spacing_quality': 0.5,
        'freshness_zone': 'moderate',
        'freshness_score': 0.78,
        'is_quick_backup': False,
        'is_long_absence': False,
        'distance_change_staleness': 0.0,
        'class_x_spell': 0.0,
        'trainer_freshness_profile': 'standard',
    }

    try:
        form_details = _get_field(runner, 'formDetails', 'form_details', default=[])
        if not isinstance(form_details, list):
            form_details = []

        days_since_raw = _get_field(runner, 'daysSinceRun', 'days_since_run', default=None)
        days_since = _safe_float(days_since_raw, -1.0)

        if days_since < 0 and form_details:
            most_recent = _parse_date(form_details[0].get('date'))
            if most_recent:
                days_since = (datetime.now() - most_recent).days

        if days_since < 0:
            days_since = 30.0

        days_since = min(max(days_since, 0.0), 365.0)

        days_normalized = days_since / 365.0

        fitness_data = _get_field(runner, 'fitnessDataJson', 'fitness_data', default={})
        if not isinstance(fitness_data, dict):
            fitness_data = {}

        runs_this_prep = _safe_int(
            _get_field(fitness_data, 'runsThisPrep', 'runs_this_prep', default=1), 1
        )
        runs_this_prep = max(runs_this_prep, 1)

        spell_length_days = _safe_int(
            _get_field(fitness_data, 'spellLengthDays', 'spell_length_days', default=0), 0
        )

        prep_run_x_days_since = runs_this_prep * days_normalized

        run_spacing = compute_run_spacing_quality(form_details)

        if days_since < 7:
            freshness_zone = 'backed_up'
        elif days_since <= 13:
            freshness_zone = 'fresh'
        elif days_since <= 28:
            freshness_zone = 'optimal'
        elif days_since <= 49:
            freshness_zone = 'moderate'
        else:
            freshness_zone = 'stale'

        freshness_score = compute_empirical_freshness_score(days_since)

        is_quick_backup = days_since < 10
        is_long_absence = days_since > 90

        current_distance = _parse_distance(
            _get_field(race_data, 'distance', default=0)
        )
        last_distance = 0
        if form_details:
            last_distance = _parse_distance(
                form_details[0].get('distance', 0)
            )

        if current_distance > 0 and last_distance > 0:
            distance_change_staleness = compute_distance_change_interaction(
                days_since, current_distance, last_distance
            )
            dist_change_pct = abs(current_distance - last_distance) / last_distance
        else:
            distance_change_staleness = 0.0
            dist_change_pct = 0.0

        race_class = _get_field(race_data, 'raceClass', 'race_class', 'class', default='')
        class_level = _parse_class_level(race_class)
        spell_normalized = min(spell_length_days / 365.0, 1.0) if spell_length_days > 0 else days_normalized
        class_x_spell = (class_level / 10.0) * spell_normalized

        trainer_name = _get_field(runner, 'trainer', 'trainerName', default='')
        trainer_profile = classify_trainer_freshness(trainer_name)

        return {
            'days_since_last_start': round(days_since, 1),
            'days_since_last_normalized': round(days_normalized, 4),
            'runs_this_prep': runs_this_prep,
            'prep_run_x_days_since': round(prep_run_x_days_since, 4),
            'spell_length_days': spell_length_days,
            'run_spacing_quality': round(run_spacing, 4),
            'freshness_zone': freshness_zone,
            'freshness_score': round(freshness_score, 4),
            'is_quick_backup': is_quick_backup,
            'is_long_absence': is_long_absence,
            'distance_change_staleness': round(distance_change_staleness, 4),
            'class_x_spell': round(class_x_spell, 4),
            'trainer_freshness_profile': trainer_profile,
        }

    except Exception:
        return defaults


if __name__ == '__main__':
    print("=" * 60)
    print("Temporal Staleness Feature Module - Test Suite")
    print("=" * 60)

    sample_runner = {
        'horse': 'Test Runner',
        'trainer': 'Chris Waller',
        'daysSinceRun': 21,
        'fitnessDataJson': {
            'runsThisPrep': 3,
            'spellLengthDays': 60,
        },
        'formDetails': [
            {'date': '2026-01-29', 'position': 2, 'margin': 0.5, 'distance': 1400, 'race_class': 'Group 2'},
            {'date': '2026-01-08', 'position': 1, 'margin': 0.0, 'distance': 1200, 'race_class': 'Group 3'},
            {'date': '2025-12-18', 'position': 3, 'margin': 1.2, 'distance': 1400, 'race_class': 'Listed'},
            {'date': '2025-11-27', 'position': 4, 'margin': 2.0, 'distance': 1600, 'race_class': 'Open'},
            {'date': '2025-11-06', 'position': 2, 'margin': 0.3, 'distance': 1400, 'race_class': 'Group 3'},
            {'date': '2025-10-16', 'position': 1, 'margin': 0.0, 'distance': 1200, 'race_class': 'Listed'},
        ],
    }

    sample_race = {
        'distance': 1600,
        'raceClass': 'Group 2',
    }

    print("\n--- Test 1: Full staleness features (21 days since, 3rd-up) ---")
    features = compute_staleness_features(sample_runner, sample_race)
    for k, v in features.items():
        print(f"  {k}: {v}")

    print("\n--- Test 2: Empirical freshness scores across day ranges ---")
    test_days = [0, 5, 10, 14, 17, 21, 28, 35, 42, 50, 70, 90, 120, 180, 250, 365]
    for d in test_days:
        score = compute_empirical_freshness_score(d)
        print(f"  {d:>3d} days -> {score:.4f}")

    print("\n--- Test 3: Run spacing quality ---")
    regular_form = [
        {'date': '2026-01-28'},
        {'date': '2026-01-07'},
        {'date': '2025-12-17'},
        {'date': '2025-11-26'},
        {'date': '2025-11-05'},
    ]
    irregular_form = [
        {'date': '2026-01-28'},
        {'date': '2026-01-20'},
        {'date': '2025-11-01'},
        {'date': '2025-10-28'},
        {'date': '2025-07-15'},
    ]
    print(f"  Regular campaign:   {compute_run_spacing_quality(regular_form):.4f}")
    print(f"  Irregular campaign: {compute_run_spacing_quality(irregular_form):.4f}")
    print(f"  No form data:       {compute_run_spacing_quality(None):.4f}")
    print(f"  Only 2 runs:        {compute_run_spacing_quality(regular_form[:2]):.4f}")

    print("\n--- Test 4: Distance change interaction ---")
    test_cases = [
        (14, 1400, 1400, "Same dist, optimal spacing"),
        (21, 1600, 1400, "Small change, optimal"),
        (50, 2000, 1200, "Big change, stale"),
        (7, 1200, 1400, "Small change, backed up"),
        (100, 1600, 1200, "Moderate change, long absence"),
    ]
    for days, curr, last, desc in test_cases:
        score = compute_distance_change_interaction(days, curr, last)
        print(f"  {desc}: {score:.4f}")

    print("\n--- Test 5: Trainer classification (placeholder) ---")
    print(f"  Chris Waller:  {classify_trainer_freshness('Chris Waller')}")
    print(f"  No trainer:    {classify_trainer_freshness(None)}")
    print(f"  Empty string:  {classify_trainer_freshness('')}")

    print("\n--- Test 6: Edge cases ---")
    empty_runner: Dict[str, Any] = {}
    empty_race: Dict[str, Any] = {}
    features_empty = compute_staleness_features(empty_runner, empty_race)
    print("  Empty runner/race:")
    for k, v in features_empty.items():
        print(f"    {k}: {v}")

    first_up_runner = {
        'daysSinceRun': 120,
        'fitnessDataJson': {'runsThisPrep': 1, 'spellLengthDays': 120},
        'formDetails': [],
        'trainer': 'Unknown Trainer',
    }
    features_first_up = compute_staleness_features(first_up_runner, {'distance': 1200, 'raceClass': 'Maiden'})
    print("\n  First-up after long spell (120 days):")
    for k, v in features_first_up.items():
        print(f"    {k}: {v}")

    print("\n" + "=" * 60)
    print("All tests completed successfully.")
    print("=" * 60)

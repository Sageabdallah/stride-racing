#!/usr/bin/env python3
"""Predicts race tempo and identifies which horses suit each pace scenario."""

import sys
import json
from typing import Dict, List, Tuple
from enum import Enum


class RunningStyle(Enum):
    LEADER = 4
    ON_PACE = 3
    MIDFIELD = 2
    OFF_PACE = 1
    BACKMARKER = 0


class PaceScenario(Enum):
    VERY_SLOW = 'very_slow'
    SLOW = 'slow'
    MODERATE = 'moderate'
    FAST = 'fast'
    VERY_FAST = 'very_fast'


def classify_running_style(runner: Dict) -> Tuple[RunningStyle, float]:
    """
    Classify a horse's running style from available data.
    Returns: (style, confidence)
    """
    explicit_style = runner.get('running_style', '').lower()
    if explicit_style:
        if 'leader' in explicit_style or 'front' in explicit_style:
            return RunningStyle.LEADER, 0.9
        elif 'on pace' in explicit_style or 'prominent' in explicit_style:
            return RunningStyle.ON_PACE, 0.9
        elif 'midfield' in explicit_style or 'mid' in explicit_style:
            return RunningStyle.MIDFIELD, 0.9
        elif 'off pace' in explicit_style or 'back' in explicit_style:
            return RunningStyle.OFF_PACE, 0.9
        elif 'tail' in explicit_style or 'rear' in explicit_style:
            return RunningStyle.BACKMARKER, 0.9
    
    form = runner.get('form') or runner.get('form_string', '')
    if form:
        positions = []
        for char in form[:4]:
            if char.isdigit():
                positions.append(int(char))
        
        if positions:
            avg_pos = sum(positions) / len(positions)
            wins_from_front = sum(1 for p in positions if p == 1)
            
            if wins_from_front >= 2:
                return RunningStyle.LEADER, 0.7
            elif avg_pos <= 2:
                return RunningStyle.ON_PACE, 0.6
            elif avg_pos <= 4:
                return RunningStyle.MIDFIELD, 0.5
            elif avg_pos <= 6:
                return RunningStyle.OFF_PACE, 0.5
            else:
                return RunningStyle.BACKMARKER, 0.5
    
    barrier = runner.get('barrier') or runner.get('draw')
    try:
        barrier = int(barrier)
        if barrier <= 4:
            return RunningStyle.ON_PACE, 0.4
        elif barrier >= 12:
            return RunningStyle.MIDFIELD, 0.3
    except (ValueError, TypeError):
        pass
    
    return RunningStyle.MIDFIELD, 0.2


def predict_race_tempo(runners: List[Dict], distance: int) -> Tuple[PaceScenario, Dict]:
    """
    Predict the pace scenario for a race based on field composition.
    Returns: (scenario, analysis_details)
    """
    speed_runners = 0
    confirmed_leaders = 0
    field_size = len(runners)
    
    for runner in runners:
        style, conf = classify_running_style(runner)
        if style == RunningStyle.LEADER:
            confirmed_leaders += 1
            speed_runners += 1
        elif style == RunningStyle.ON_PACE:
            speed_runners += 1
    
    speed_ratio = speed_runners / field_size if field_size > 0 else 0.3
    
    base_scenario = PaceScenario.MODERATE
    
    if confirmed_leaders >= 3:
        base_scenario = PaceScenario.VERY_FAST
    elif confirmed_leaders >= 2 or speed_ratio >= 0.4:
        base_scenario = PaceScenario.FAST
    elif confirmed_leaders == 0 and speed_ratio <= 0.15:
        base_scenario = PaceScenario.VERY_SLOW
    elif speed_ratio <= 0.2:
        base_scenario = PaceScenario.SLOW
    
    if distance <= 1100:
        if base_scenario == PaceScenario.MODERATE:
            base_scenario = PaceScenario.FAST
    elif distance >= 2000:
        if base_scenario == PaceScenario.MODERATE:
            base_scenario = PaceScenario.SLOW
    
    analysis = {
        'scenario': base_scenario.value,
        'confirmed_leaders': confirmed_leaders,
        'speed_runners': speed_runners,
        'speed_ratio': round(speed_ratio, 2),
        'field_size': field_size,
        'distance_factor': 'sprint' if distance <= 1200 else 'middle' if distance <= 1600 else 'staying'
    }
    
    return base_scenario, analysis


# Tight-turning tracks where front-runners and on-pace horses have extra advantage
TIGHT_TURNING_TRACKS = [
    'geelong', 'ladbrokes geelong',
    'moonee valley',
    'ballarat',
    'ipswich',
]

def calculate_pace_advantage(
    style: RunningStyle, 
    scenario: PaceScenario,
    barrier: int = 0,
    field_size: int = 10,
    track: str = ''
) -> float:
    """
    Calculate pace advantage/disadvantage based on style vs scenario.
    Returns: adjustment (-0.20 to +0.20)
    """
    advantage_matrix = {
        PaceScenario.VERY_FAST: {
            RunningStyle.LEADER: -0.15,
            RunningStyle.ON_PACE: -0.08,
            RunningStyle.MIDFIELD: 0.05,
            RunningStyle.OFF_PACE: 0.12,
            RunningStyle.BACKMARKER: 0.15,
        },
        PaceScenario.FAST: {
            RunningStyle.LEADER: -0.08,
            RunningStyle.ON_PACE: -0.03,
            RunningStyle.MIDFIELD: 0.03,
            RunningStyle.OFF_PACE: 0.08,
            RunningStyle.BACKMARKER: 0.10,
        },
        PaceScenario.MODERATE: {
            RunningStyle.LEADER: 0.02,
            RunningStyle.ON_PACE: 0.02,
            RunningStyle.MIDFIELD: 0.0,
            RunningStyle.OFF_PACE: 0.0,
            RunningStyle.BACKMARKER: -0.02,
        },
        PaceScenario.SLOW: {
            RunningStyle.LEADER: 0.12,
            RunningStyle.ON_PACE: 0.08,
            RunningStyle.MIDFIELD: 0.02,
            RunningStyle.OFF_PACE: -0.05,
            RunningStyle.BACKMARKER: -0.10,
        },
        PaceScenario.VERY_SLOW: {
            RunningStyle.LEADER: 0.18,
            RunningStyle.ON_PACE: 0.12,
            RunningStyle.MIDFIELD: 0.03,
            RunningStyle.OFF_PACE: -0.08,
            RunningStyle.BACKMARKER: -0.15,
        },
    }
    
    base_advantage = advantage_matrix.get(scenario, {}).get(style, 0.0)
    
    if barrier > 0:
        if style in [RunningStyle.LEADER, RunningStyle.ON_PACE]:
            if barrier >= 12:
                base_advantage -= 0.05
            elif barrier <= 3:
                base_advantage += 0.03
        elif style in [RunningStyle.OFF_PACE, RunningStyle.BACKMARKER]:
            pass
    
    # Tight-turning track bonus for front-runners and on-pace horses
    track_lower = track.lower()
    is_tight_track = any(t in track_lower for t in TIGHT_TURNING_TRACKS)
    if is_tight_track:
        if style == RunningStyle.LEADER:
            base_advantage += 0.06  # Strong advantage for leaders on tight tracks
        elif style == RunningStyle.ON_PACE:
            base_advantage += 0.04  # Moderate advantage for on-pace
        elif style == RunningStyle.BACKMARKER:
            base_advantage -= 0.04  # Harder for closers on tight tracks
    
    return round(max(-0.20, min(0.20, base_advantage)), 3)


def extract_pace_features(runner: Dict, all_runners: List[Dict], race_data: Dict) -> Dict:
    """Extract all pace-related features for a runner."""
    style, style_confidence = classify_running_style(runner)
    
    distance = race_data.get('distance_numeric', 1400)
    scenario, pace_analysis = predict_race_tempo(all_runners, distance)
    
    barrier = runner.get('barrier') or runner.get('draw') or 0
    try:
        barrier = int(barrier)
    except (ValueError, TypeError):
        barrier = 0
    
    track = race_data.get('track', race_data.get('course', ''))
    
    pace_advantage = calculate_pace_advantage(
        style, scenario, barrier, len(all_runners), track
    )
    
    is_lone_speed = (
        style in [RunningStyle.LEADER, RunningStyle.ON_PACE] and 
        pace_analysis['confirmed_leaders'] <= 1 and 
        pace_analysis['speed_ratio'] <= 0.25
    )
    
    is_pace_disadvantage = (
        style in [RunningStyle.LEADER, RunningStyle.ON_PACE] and
        scenario in [PaceScenario.VERY_FAST, PaceScenario.FAST]
    )
    
    suits_scenario = (
        (style in [RunningStyle.OFF_PACE, RunningStyle.BACKMARKER] and 
         scenario in [PaceScenario.FAST, PaceScenario.VERY_FAST]) or
        (style in [RunningStyle.LEADER, RunningStyle.ON_PACE] and
         scenario in [PaceScenario.SLOW, PaceScenario.VERY_SLOW])
    )
    
    form = runner.get('form') or runner.get('form_string', '')
    is_consistent_finisher = False
    if form:
        positions = [int(c) for c in form[:5] if c.isdigit()]
        if positions and max(positions) - min(positions) <= 3:
            is_consistent_finisher = True
    
    return {
        'running_style': style.name.lower(),
        'running_style_score': style.value,
        'style_confidence': style_confidence,
        'predicted_pace': scenario.value,
        'pace_advantage': pace_advantage,
        'expected_pace_advantage': pace_advantage,
        'is_lone_speed': is_lone_speed,
        'is_pace_disadvantage': is_pace_disadvantage,
        'suits_pace_scenario': suits_scenario,
        'is_consistent_finisher': is_consistent_finisher,
        'pace_analysis': pace_analysis
    }


def analyze_pace_map(runners: List[Dict], race_data: Dict) -> Dict:
    """Generate a pace map for the race."""
    distance = race_data.get('distance_numeric', 1400)
    scenario, analysis = predict_race_tempo(runners, distance)
    
    pace_groups = {
        'leaders': [],
        'on_pace': [],
        'midfield': [],
        'off_pace': [],
        'backmarkers': []
    }
    
    for runner in runners:
        name = runner.get('horse') or runner.get('name') or runner.get('horse_name', 'Unknown')
        barrier = runner.get('barrier') or runner.get('draw') or 0
        try:
            barrier = int(barrier)
        except (ValueError, TypeError):
            barrier = 0
            
        style, conf = classify_running_style(runner)
        adv = calculate_pace_advantage(style, scenario, barrier, len(runners))
        
        runner_info = {
            'name': name,
            'barrier': barrier,
            'pace_advantage': adv,
            'confidence': conf
        }
        
        if style == RunningStyle.LEADER:
            pace_groups['leaders'].append(runner_info)
        elif style == RunningStyle.ON_PACE:
            pace_groups['on_pace'].append(runner_info)
        elif style == RunningStyle.MIDFIELD:
            pace_groups['midfield'].append(runner_info)
        elif style == RunningStyle.OFF_PACE:
            pace_groups['off_pace'].append(runner_info)
        else:
            pace_groups['backmarkers'].append(runner_info)
    
    for group in pace_groups.values():
        group.sort(key=lambda x: x['barrier'])
    
    return {
        'predicted_tempo': scenario.value,
        'pace_analysis': analysis,
        'pace_map': pace_groups,
        'recommendation': get_pace_recommendation(scenario, pace_groups)
    }


def get_pace_recommendation(scenario: PaceScenario, pace_groups: Dict) -> str:
    """Generate human-readable pace recommendation."""
    leader_count = len(pace_groups['leaders'])
    on_pace_count = len(pace_groups['on_pace'])
    closer_count = len(pace_groups['off_pace']) + len(pace_groups['backmarkers'])
    
    if scenario in [PaceScenario.VERY_FAST, PaceScenario.FAST]:
        if closer_count > 0:
            return f"Hot pace expected with {leader_count} leaders. Look to closers and backmarkers."
        else:
            return f"Hot pace but no confirmed closers. Midfield runners may benefit."
    elif scenario in [PaceScenario.VERY_SLOW, PaceScenario.SLOW]:
        if leader_count == 1:
            return f"Soft pace with lone leader advantage. On-pace runners favored."
        else:
            return f"Slow pace expected. Leaders and on-pace runners have advantage."
    else:
        return f"Moderate pace expected. Drawn well matters more than running style."


if __name__ == '__main__':
    test_runners = [
        {'horse': 'Leader A', 'running_style': 'front runner', 'barrier': 2, 'form': '111x2'},
        {'horse': 'On Pace B', 'running_style': 'prominent', 'barrier': 5, 'form': '23121'},
        {'horse': 'Midfield C', 'form': '43524', 'barrier': 8},
        {'horse': 'Closer D', 'running_style': 'backmarker', 'barrier': 10, 'form': '65312'},
    ]
    
    test_race = {
        'distance_numeric': 1400,
        'track': 'Randwick'
    }
    
    result = analyze_pace_map(test_runners, test_race)
    print(json.dumps(result, indent=2, default=str))

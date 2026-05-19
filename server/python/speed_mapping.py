#!/usr/bin/env python3
"""
Advanced pace and position prediction for horse racing.

Features:
1. Running Style Classification - Leader/OnPace/Midfield/Backmarker from form analysis
2. Settling Position Prediction - Expected position at first turn based on style + barrier
3. Pace Scenario Analysis - Hot/Moderate/Soft tempo prediction
4. Pace Advantage Scoring - Lone leader bonus, covered run bonus, wide without cover penalty
5. Visual Speed Map Generation - Data structure for frontend visualization
"""

import sys
import json
import re
from typing import Dict, List, Tuple, Optional, Any
from enum import Enum
from dataclasses import dataclass, asdict


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


@dataclass
class RunnerPaceProfile:
    """Complete pace profile for a runner."""
    horse: str
    barrier: int
    running_style: str
    style_confidence: float
    settling_position: int
    expected_section: str
    pace_advantage: float
    is_lone_speed: bool
    has_covered_run: bool
    is_wide_no_cover: bool
    rail_position: str
    pace_flags: List[str]


@dataclass
class SpeedMap:
    """Complete speed map for a race."""
    race_id: str
    distance: int
    track: str
    pace_scenario: str
    pace_pressure: int
    leader_count: int
    speed_horse_count: int
    field_size: int
    pace_recommendation: str
    runners: List[Dict]
    settling_order: List[Dict]
    pace_analysis: Dict


TIGHT_TURNING_TRACKS = [
    'geelong', 'ladbrokes geelong',
    'moonee valley',
    'ballarat',
    'ipswich',
    'bendigo',
    'mornington'
]


def parse_form_positions(form_details: List[Dict]) -> List[Dict]:
    """
    Parse settling positions and margins from form details.
    Returns list of {settling_position, beaten_margin, class_level, field_size}
    """
    parsed = []
    for run in form_details[:6]:
        try:
            settling = run.get('settling_position', run.get('in_running', run.get('position_600m')))
            if settling:
                if isinstance(settling, str):
                    settling = int(re.search(r'\d+', settling).group()) if re.search(r'\d+', settling) else None
                else:
                    settling = int(settling)
            
            finish_pos = run.get('position', run.get('finish_position'))
            if finish_pos:
                if isinstance(finish_pos, str):
                    finish_pos = int(re.search(r'\d+', str(finish_pos)).group()) if re.search(r'\d+', str(finish_pos)) else None
                else:
                    finish_pos = int(finish_pos)
            
            field_size = run.get('field_size', run.get('runners', 10))
            if isinstance(field_size, str):
                field_size = int(field_size) if field_size.isdigit() else 10
            
            parsed.append({
                'settling': settling,
                'finish': finish_pos,
                'field_size': field_size,
                'class': run.get('race_class', run.get('class', '')),
                'margin': run.get('beaten_margin', run.get('margin', ''))
            })
        except (ValueError, TypeError, AttributeError):
            continue
    
    return parsed


def classify_running_style_enhanced(runner: Dict) -> Tuple[RunningStyle, float, Dict]:
    """
    Enhanced running style classification using multiple data sources.

    Priority:
    1. Explicit running_style field
    2. Form details with settling positions
    3. Recent form string pattern
    4. Barrier position (low confidence fallback)

    Returns: (style, confidence, analysis_data)
    """
    analysis = {
        'source': 'default',
        'avg_settling': None,
        'pattern': None,
        'barrier_influence': 0
    }
    
    explicit_style = (runner.get('running_style', '') or '').lower()
    if explicit_style:
        if 'leader' in explicit_style or 'front' in explicit_style or 'pace' in explicit_style.split()[0:1]:
            analysis['source'] = 'explicit'
            analysis['pattern'] = explicit_style
            return RunningStyle.LEADER, 0.95, analysis
        elif 'on pace' in explicit_style or 'on-pace' in explicit_style or 'prominent' in explicit_style or 'stalker' in explicit_style:
            analysis['source'] = 'explicit'
            return RunningStyle.ON_PACE, 0.95, analysis
        elif 'midfield' in explicit_style or 'mid' in explicit_style:
            analysis['source'] = 'explicit'
            return RunningStyle.MIDFIELD, 0.90, analysis
        elif 'off pace' in explicit_style or 'off-pace' in explicit_style or 'back' in explicit_style:
            analysis['source'] = 'explicit'
            return RunningStyle.OFF_PACE, 0.90, analysis
        elif 'tail' in explicit_style or 'rear' in explicit_style:
            analysis['source'] = 'explicit'
            return RunningStyle.BACKMARKER, 0.90, analysis
    
    form_details = runner.get('formDetails', runner.get('form_details', []))
    if form_details and isinstance(form_details, list):
        parsed = parse_form_positions(form_details)
        settling_positions = [p['settling'] for p in parsed if p['settling'] is not None]
        
        if settling_positions:
            avg_settling = sum(settling_positions) / len(settling_positions)
            analysis['source'] = 'form_details'
            analysis['avg_settling'] = round(avg_settling, 1)
            
            lead_count = sum(1 for s in settling_positions if s == 1)
            lead_ratio = lead_count / len(settling_positions)
            
            if lead_ratio >= 0.6 or avg_settling <= 1.5:
                return RunningStyle.LEADER, 0.85, analysis
            elif avg_settling <= 3:
                return RunningStyle.ON_PACE, 0.80, analysis
            elif avg_settling <= 5:
                return RunningStyle.MIDFIELD, 0.75, analysis
            elif avg_settling <= 8:
                return RunningStyle.OFF_PACE, 0.75, analysis
            else:
                return RunningStyle.BACKMARKER, 0.75, analysis
    
    form = runner.get('form', runner.get('form_string', ''))
    if form and isinstance(form, str):
        positions = []
        for char in form[:6]:
            if char.isdigit():
                positions.append(int(char))
        
        if positions:
            analysis['source'] = 'form_string'
            avg_pos = sum(positions) / len(positions)
            wins_from_front = sum(1 for p in positions if p == 1)
            analysis['avg_settling'] = round(avg_pos, 1)
            
            if wins_from_front >= 2 or (len(positions) >= 3 and avg_pos <= 1.5):
                return RunningStyle.LEADER, 0.70, analysis
            elif avg_pos <= 2.5:
                return RunningStyle.ON_PACE, 0.65, analysis
            elif avg_pos <= 4.5:
                return RunningStyle.MIDFIELD, 0.55, analysis
            elif avg_pos <= 6:
                return RunningStyle.OFF_PACE, 0.50, analysis
            else:
                return RunningStyle.BACKMARKER, 0.50, analysis
    
    barrier = runner.get('barrier', runner.get('draw', 0))
    try:
        barrier = int(barrier)
    except (ValueError, TypeError):
        barrier = 8
    
    analysis['source'] = 'barrier_fallback'
    analysis['barrier_influence'] = barrier
    
    if barrier <= 3:
        return RunningStyle.ON_PACE, 0.35, analysis
    elif barrier <= 6:
        return RunningStyle.MIDFIELD, 0.30, analysis
    else:
        return RunningStyle.OFF_PACE, 0.25, analysis


def predict_settling_position(
    runner: Dict,
    style: RunningStyle,
    barrier: int,
    field_size: int,
    track: str = ''
) -> Tuple[int, str, str]:
    """
    Predict where a horse will settle at the first turn.

    Returns: (position, section, rail_position)
    - position: 1 to field_size
    - section: 'lead', 'box_seat', 'midfield', 'back', 'tail'
    - rail_position: 'fence', 'one_off', 'three_wide', 'four_wide'
    """
    style_base = {
        RunningStyle.LEADER: 1,
        RunningStyle.ON_PACE: 3,
        RunningStyle.MIDFIELD: max(5, field_size // 2),
        RunningStyle.OFF_PACE: max(7, int(field_size * 0.7)),
        RunningStyle.BACKMARKER: max(9, field_size - 2)
    }
    
    base_pos = style_base.get(style, field_size // 2)
    
    if style in [RunningStyle.LEADER, RunningStyle.ON_PACE]:
        if barrier <= 3:
            base_pos = max(1, base_pos - 1)  # Easier to cross from inside
        elif barrier >= 10:
            base_pos = min(field_size, base_pos + 2)  # Hard to cross from wide
    
    settling_pos = max(1, min(field_size, base_pos))
    
    position_ratio = settling_pos / field_size
    if settling_pos == 1:
        section = 'lead'
    elif position_ratio <= 0.25:
        section = 'box_seat'
    elif position_ratio <= 0.55:
        section = 'midfield'
    elif position_ratio <= 0.8:
        section = 'back'
    else:
        section = 'tail'
    
    if barrier <= 3 and style in [RunningStyle.LEADER, RunningStyle.ON_PACE, RunningStyle.MIDFIELD]:
        rail_position = 'fence'
    elif barrier <= 6:
        rail_position = 'one_off'
    elif barrier <= 10:
        rail_position = 'three_wide'
    else:
        rail_position = 'four_wide'
    
    # Special case: backmarkers often slide in
    if style in [RunningStyle.OFF_PACE, RunningStyle.BACKMARKER] and settling_pos >= field_size - 3:
        rail_position = 'one_off'  # Typically slide back and save ground
    
    return settling_pos, section, rail_position


def predict_pace_scenario(
    runners: List[Dict],
    distance: int,
    track: str = ''
) -> Tuple[PaceScenario, Dict]:
    """
    Predict race pace scenario based on field composition.

    Returns: (scenario, analysis)
    """
    field_size = len(runners)
    leaders = []
    on_pace = []
    midfield = []
    closers = []
    
    for runner in runners:
        horse = runner.get('horse', runner.get('horseName', runner.get('horse_name', 'Unknown')))
        barrier = runner.get('barrier', runner.get('draw', 8))
        try:
            barrier = int(barrier)
        except (ValueError, TypeError):
            barrier = 8
        
        style, conf, _ = classify_running_style_enhanced(runner)
        
        runner_data = {
            'horse': horse,
            'barrier': barrier,
            'style': style.name,
            'confidence': conf
        }
        
        if style == RunningStyle.LEADER:
            leaders.append(runner_data)
        elif style == RunningStyle.ON_PACE:
            on_pace.append(runner_data)
        elif style == RunningStyle.MIDFIELD:
            midfield.append(runner_data)
        else:
            closers.append(runner_data)
    
    leader_count = len(leaders)
    speed_count = leader_count + len(on_pace)
    speed_ratio = speed_count / field_size if field_size > 0 else 0.3
    
    if leader_count >= 3:
        scenario = PaceScenario.VERY_FAST
        pressure = 9
    elif leader_count >= 2 and speed_ratio >= 0.35:
        scenario = PaceScenario.FAST
        pressure = 7
    elif leader_count >= 2:
        scenario = PaceScenario.FAST
        pressure = 6
    elif leader_count == 1 and speed_ratio <= 0.25:
        scenario = PaceScenario.SLOW
        pressure = 3
    elif leader_count == 0 and speed_ratio <= 0.2:
        scenario = PaceScenario.VERY_SLOW
        pressure = 2
    elif speed_ratio >= 0.4:
        scenario = PaceScenario.FAST
        pressure = 6
    else:
        scenario = PaceScenario.MODERATE
        pressure = 5
    
    if distance <= 1100:
        if scenario == PaceScenario.MODERATE:
            scenario = PaceScenario.FAST
            pressure = min(10, pressure + 1)
    elif distance >= 2000:
        if scenario == PaceScenario.MODERATE:
            scenario = PaceScenario.SLOW
            pressure = max(1, pressure - 1)
    
    is_tight = any(t in track.lower() for t in TIGHT_TURNING_TRACKS)
    
    analysis = {
        'leader_count': leader_count,
        'speed_count': speed_count,
        'speed_ratio': round(speed_ratio, 2),
        'field_size': field_size,
        'pressure': pressure,
        'is_tight_track': is_tight,
        'leaders': leaders,
        'on_pace': on_pace,
        'midfield_count': len(midfield),
        'closer_count': len(closers)
    }
    
    return scenario, analysis


def calculate_pace_advantage_score(
    style: RunningStyle,
    scenario: PaceScenario,
    barrier: int,
    field_size: int,
    is_lone_speed: bool,
    has_covered_run: bool,
    is_wide_no_cover: bool,
    track: str = ''
) -> Tuple[float, List[str]]:
    """
    Calculate pace advantage and generate flags.

    Returns: (advantage_score, flags)
    """
    flags = []
    advantage = 0.0
    
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
            RunningStyle.LEADER: 0.03,
            RunningStyle.ON_PACE: 0.02,
            RunningStyle.MIDFIELD: 0.0,
            RunningStyle.OFF_PACE: -0.02,
            RunningStyle.BACKMARKER: -0.03,
        },
        PaceScenario.SLOW: {
            RunningStyle.LEADER: 0.12,
            RunningStyle.ON_PACE: 0.08,
            RunningStyle.MIDFIELD: 0.02,
            RunningStyle.OFF_PACE: -0.06,
            RunningStyle.BACKMARKER: -0.12,
        },
        PaceScenario.VERY_SLOW: {
            RunningStyle.LEADER: 0.18,
            RunningStyle.ON_PACE: 0.12,
            RunningStyle.MIDFIELD: 0.03,
            RunningStyle.OFF_PACE: -0.08,
            RunningStyle.BACKMARKER: -0.15,
        },
    }
    
    base_adv = advantage_matrix.get(scenario, {}).get(style, 0.0)
    advantage += base_adv
    
    if is_lone_speed:
        advantage += 0.15
        flags.append('LONE LEADER')
    
    if has_covered_run:
        advantage += 0.05
        flags.append('COVERED RUN')
    
    if is_wide_no_cover:
        advantage -= 0.08
        flags.append('WIDE NO COVER')
    
    if style in [RunningStyle.LEADER, RunningStyle.ON_PACE]:
        if barrier <= 3:
            advantage += 0.03
            flags.append('INSIDE SPEED')
        elif barrier >= 12:
            advantage -= 0.06
            flags.append('WIDE SPEED')
    
    is_tight = any(t in track.lower() for t in TIGHT_TURNING_TRACKS)
    if is_tight:
        if style == RunningStyle.LEADER:
            advantage += 0.06
            flags.append('TIGHT TRACK LEADER')
        elif style == RunningStyle.ON_PACE:
            advantage += 0.04
        elif style == RunningStyle.BACKMARKER:
            advantage -= 0.04
            flags.append('HARD TO CLOSE')
    
    if style == RunningStyle.ON_PACE and barrier <= 6 and scenario in [PaceScenario.FAST, PaceScenario.VERY_FAST]:
        flags.append('BOX SEAT')
        advantage += 0.04
    
    return round(max(-0.25, min(0.25, advantage)), 3), flags


def generate_speed_map(runners: List[Dict], race_data: Dict) -> SpeedMap:
    """
    Generate complete speed map for a race.

    Args:
        runners: List of runner dicts with horse, barrier, form, running_style
        race_data: Dict with id, track, distance, etc.

    Returns: SpeedMap dataclass with all analysis
    """
    distance = race_data.get('distance_numeric', 1400)
    if isinstance(race_data.get('distance'), str):
        match = re.search(r'(\d+)', str(race_data.get('distance', '1400')))
        distance = int(match.group(1)) if match else 1400
    
    track = race_data.get('track', race_data.get('course', ''))
    race_id = race_data.get('id', race_data.get('race_id', 'unknown'))
    field_size = len(runners)
    
    scenario, pace_analysis = predict_pace_scenario(runners, distance, track)
    
    leader_count = pace_analysis['leader_count']
    is_race_has_lone_speed = leader_count == 1 and pace_analysis['speed_ratio'] <= 0.25
    
    runner_profiles = []
    settling_order = []
    
    for runner in runners:
        horse = runner.get('horse', runner.get('horseName', runner.get('horse_name', 'Unknown')))
        barrier = runner.get('barrier', runner.get('draw', 8))
        try:
            barrier = int(barrier)
        except (ValueError, TypeError):
            barrier = 8
        
        style, style_conf, style_analysis = classify_running_style_enhanced(runner)
        
        settling_pos, section, rail_position = predict_settling_position(
            runner, style, barrier, field_size, track
        )
        
        is_lone_speed = (style == RunningStyle.LEADER and is_race_has_lone_speed)
        has_covered_run = (section in ['box_seat', 'midfield'] and rail_position in ['fence', 'one_off'])
        is_wide_no_cover = (
            rail_position in ['three_wide', 'four_wide'] and 
            section in ['on_pace', 'midfield', 'box_seat'] and
            style in [RunningStyle.ON_PACE, RunningStyle.MIDFIELD]
        )
        
        pace_adv, pace_flags = calculate_pace_advantage_score(
            style, scenario, barrier, field_size,
            is_lone_speed, has_covered_run, is_wide_no_cover, track
        )
        
        profile = RunnerPaceProfile(
            horse=horse,
            barrier=barrier,
            running_style=style.name.lower(),
            style_confidence=style_conf,
            settling_position=settling_pos,
            expected_section=section,
            pace_advantage=pace_adv,
            is_lone_speed=is_lone_speed,
            has_covered_run=has_covered_run,
            is_wide_no_cover=is_wide_no_cover,
            rail_position=rail_position,
            pace_flags=pace_flags
        )
        
        runner_profiles.append(asdict(profile))
        settling_order.append({
            'horse': horse,
            'settling_position': settling_pos,
            'section': section,
            'rail': rail_position,
            'barrier': barrier,
            'style': style.name.lower(),
            'pace_advantage': pace_adv,
            'flags': pace_flags
        })
    
    settling_order.sort(key=lambda x: (x['settling_position'], x['barrier']))
    
    recommendation = generate_pace_recommendation(scenario, pace_analysis, is_race_has_lone_speed)
    
    return SpeedMap(
        race_id=str(race_id),
        distance=distance,
        track=track,
        pace_scenario=scenario.value,
        pace_pressure=pace_analysis['pressure'],
        leader_count=leader_count,
        speed_horse_count=pace_analysis['speed_count'],
        field_size=field_size,
        pace_recommendation=recommendation,
        runners=runner_profiles,
        settling_order=settling_order,
        pace_analysis=pace_analysis
    )


def generate_pace_recommendation(
    scenario: PaceScenario,
    analysis: Dict,
    has_lone_leader: bool
) -> str:
    """Generate human-readable pace recommendation."""
    leader_count = analysis['leader_count']
    closer_count = analysis['closer_count']
    is_tight = analysis.get('is_tight_track', False)
    
    if has_lone_leader:
        leader_name = analysis['leaders'][0]['horse'] if analysis['leaders'] else 'Lone leader'
        return f"LONE SPEED: {leader_name} should control tempo. On-pace runners to benefit in the run. Hard for closers."
    
    if scenario == PaceScenario.VERY_FAST:
        return f"HOT PACE: {leader_count} leaders will burn it up early. Look to patient closers for value."
    
    if scenario == PaceScenario.FAST:
        if closer_count >= 3:
            return f"FAST PACE: Genuine speed with {leader_count} leaders. Closers to benefit - look for value at odds."
        return f"FAST PACE: {leader_count} leaders pressing. Midfield runners with tactical speed can pounce."
    
    if scenario == PaceScenario.SLOW:
        if is_tight:
            return f"SOFT PACE on tight track: Leaders and on-pace will be tough to catch. Back for speed."
        return f"SOFT PACE: On-pace runners well placed. Closers need luck or muddling tempo."
    
    if scenario == PaceScenario.VERY_SLOW:
        return f"VERY SOFT PACE: No pressure up front. Leaders to control - back on-pace and look to form."
    
    return f"GENUINE PACE: Fair tempo expected. Barrier and form more important than running style."


def extract_pace_features_for_mc(runner: Dict, all_runners: List[Dict], race_data: Dict) -> Dict:
    """
    Extract pace features for Monte Carlo integration.

    Returns dict with features for probability adjustment.
    """
    speed_map = generate_speed_map(all_runners, race_data)
    
    horse = runner.get('horse', runner.get('horseName', runner.get('horse_name', 'Unknown')))
    
    runner_profile = None
    for r in speed_map.runners:
        if r['horse'] == horse:
            runner_profile = r
            break
    
    if not runner_profile:
        style, conf, _ = classify_running_style_enhanced(runner)
        return {
            'running_style': style.name.lower(),
            'style_confidence': conf,
            'pace_scenario': speed_map.pace_scenario,
            'pace_advantage': 0,
            'is_lone_speed': False,
            'has_covered_run': False,
            'is_wide_no_cover': False,
            'settling_position': len(all_runners) // 2,
            'expected_section': 'midfield',
            'rail_position': 'one_off',
            'pace_flags': [],
            'pace_pressure': speed_map.pace_pressure
        }
    
    return {
        'running_style': runner_profile['running_style'],
        'style_confidence': runner_profile['style_confidence'],
        'pace_scenario': speed_map.pace_scenario,
        'pace_advantage': runner_profile['pace_advantage'],
        'is_lone_speed': runner_profile['is_lone_speed'],
        'has_covered_run': runner_profile['has_covered_run'],
        'is_wide_no_cover': runner_profile['is_wide_no_cover'],
        'settling_position': runner_profile['settling_position'],
        'expected_section': runner_profile['expected_section'],
        'rail_position': runner_profile['rail_position'],
        'pace_flags': runner_profile['pace_flags'],
        'pace_pressure': speed_map.pace_pressure,
        'leader_count': speed_map.leader_count,
        'speed_map_recommendation': speed_map.pace_recommendation
    }


def generate_pace_insights(runner: Dict, pace_features: Dict) -> List[str]:
    """Generate human-readable pace insights for form analyst."""
    insights = []
    
    style = pace_features.get('running_style', 'midfield')
    scenario = pace_features.get('pace_scenario', 'moderate')
    pace_adv = pace_features.get('pace_advantage', 0)
    flags = pace_features.get('pace_flags', [])
    settling = pace_features.get('settling_position', 5)
    section = pace_features.get('expected_section', 'midfield')
    rail = pace_features.get('rail_position', 'one_off')
    
    if 'LONE LEADER' in flags:
        insights.append("Speed map: Only confirmed leader - should control the tempo and prove hard to catch")
    
    if 'BOX SEAT' in flags:
        insights.append("Maps to sit in the box seat with cover - ideal position to pounce")
    
    if 'COVERED RUN' in flags and 'BOX SEAT' not in flags:
        insights.append(f"Should get a covered run settling {section} on the fence")
    
    if 'WIDE NO COVER' in flags:
        insights.append("Likely to race wide without cover - will need to do extra work")
    
    if 'TIGHT TRACK LEADER' in flags:
        insights.append("Front-runner on tight track - significant tactical advantage")
    
    if 'INSIDE SPEED' in flags and 'LONE LEADER' not in flags:
        insights.append("Drawn inside with natural speed - should cross and find the rail")
    
    if 'WIDE SPEED' in flags:
        insights.append("Speed horse drawn wide - must use petrol early to find position")
    
    if pace_adv >= 0.15:
        insights.append(f"Strong pace advantage: {scenario.replace('_', ' ')} tempo suits perfectly")
    elif pace_adv >= 0.08:
        insights.append(f"Pace scenario favours: Expected {scenario.replace('_', ' ')} pace is ideal")
    elif pace_adv <= -0.10:
        insights.append(f"Pace concern: {scenario.replace('_', ' ')} tempo against running style")
    
    if style == 'backmarker' and scenario in ['fast', 'very_fast']:
        insights.append("Closer should get the fast tempo needed to finish over the top")
    elif style == 'backmarker' and scenario in ['slow', 'very_slow']:
        insights.append("Concerned for closers in a slow tempo - may need luck")
    
    return insights


def _get_recency_weighted_settling(runner: Dict) -> Optional[float]:
    """
    Compute recency-weighted average settling position from form history.
    Weights: [0.4, 0.3, 0.2, 0.1] for most recent to oldest runs.
    Returns None if no settling data available.
    """
    try:
        form_details = runner.get('formDetails', runner.get('form_details', []))
        if not form_details or not isinstance(form_details, list):
            return None

        parsed = parse_form_positions(form_details)
        settling_positions = [p['settling'] for p in parsed if p.get('settling') is not None]

        if not settling_positions:
            return None

        weights = [0.4, 0.3, 0.2, 0.1]
        total_weight = 0.0
        weighted_sum = 0.0
        for i, pos in enumerate(settling_positions[:len(weights)]):
            w = weights[i] if i < len(weights) else weights[-1]
            weighted_sum += pos * w
            total_weight += w

        if total_weight == 0:
            return None

        return weighted_sum / total_weight
    except Exception:
        return None


def predict_field_aware_settling(runners: List[Dict], race_data: Dict) -> List[Dict]:
    """
    Field-composition-aware settling prediction.

    Takes ALL runners in a race and predicts where EACH will settle relative
    to the others, handling congestion when multiple horses want the same
    position zone.

    Returns a list of dicts (one per runner) with:
        - horse: str
        - predicted_settling_position: int (1..field_size, unique)
        - settling_position_percentile: float (0-100, 0=front 100=back)
        - pace_pressure_index: float (0-100)
        - settling_difficulty: float (0-1)
    """
    try:
        field_size = len(runners)
        if field_size == 0:
            return []

        style_counts: Dict[str, int] = {
            'LEADER': 0, 'ON_PACE': 0, 'MIDFIELD': 0, 'OFF_PACE': 0, 'BACKMARKER': 0
        }

        runner_entries = []
        for runner in runners:
            try:
                horse = runner.get('horse', runner.get('horseName', runner.get('horse_name', 'Unknown')))
                barrier = runner.get('barrier', runner.get('draw', 8))
                try:
                    barrier = int(barrier)
                except (ValueError, TypeError):
                    barrier = 8

                style, conf, _ = classify_running_style_enhanced(runner)
                style_counts[style.name] = style_counts.get(style.name, 0) + 1

                weighted_settling = _get_recency_weighted_settling(runner)

                style_base = {
                    RunningStyle.LEADER: 1.0,
                    RunningStyle.ON_PACE: 3.0,
                    RunningStyle.MIDFIELD: max(5.0, field_size * 0.45),
                    RunningStyle.OFF_PACE: max(7.0, field_size * 0.7),
                    RunningStyle.BACKMARKER: max(9.0, field_size * 0.85),
                }
                base_desire = style_base.get(style, field_size / 2.0)

                if weighted_settling is not None:
                    scaled_settling = weighted_settling * (field_size / max(10, field_size))
                    desire = 0.6 * scaled_settling + 0.4 * base_desire
                else:
                    desire = base_desire

                barrier_adjustment = 0.0
                if style in [RunningStyle.LEADER, RunningStyle.ON_PACE]:
                    if barrier <= 3:
                        barrier_adjustment = -0.5
                    elif barrier >= 10:
                        barrier_adjustment = 1.5
                    elif barrier >= 7:
                        barrier_adjustment = 0.5
                elif style == RunningStyle.MIDFIELD:
                    if barrier <= 2:
                        barrier_adjustment = -0.3
                    elif barrier >= 12:
                        barrier_adjustment = 0.3

                natural_desire = desire
                desire += barrier_adjustment
                desire = max(1.0, min(float(field_size), desire))

                priority_score = (1.0 / max(1, barrier)) * 0.5 + conf * 0.5

                runner_entries.append({
                    'horse': horse,
                    'barrier': barrier,
                    'style': style,
                    'style_name': style.name,
                    'confidence': conf,
                    'desire': desire,
                    'natural_desire': natural_desire,
                    'barrier_adjustment': barrier_adjustment,
                    'priority': priority_score,
                })
            except Exception:
                runner_entries.append({
                    'horse': runner.get('horse', runner.get('horseName', runner.get('horse_name', 'Unknown'))),
                    'barrier': 8,
                    'style': RunningStyle.MIDFIELD,
                    'style_name': 'MIDFIELD',
                    'confidence': 0.3,
                    'desire': field_size / 2.0,
                    'natural_desire': field_size / 2.0,
                    'barrier_adjustment': 0.0,
                    'priority': 0.3,
                })

        runner_entries.sort(key=lambda x: (x['desire'], -x['priority']))

        assigned_positions: Dict[str, int] = {}
        used_positions: set = set()

        for entry in runner_entries:
            ideal = round(entry['desire'])
            ideal = max(1, min(field_size, ideal))

            if ideal not in used_positions:
                assigned_positions[entry['horse']] = ideal
                used_positions.add(ideal)
            else:
                best_pos = None
                best_dist = field_size + 1
                for p in range(1, field_size + 1):
                    if p not in used_positions:
                        dist = abs(p - ideal)
                        if dist < best_dist:
                            best_dist = dist
                            best_pos = p
                if best_pos is not None:
                    assigned_positions[entry['horse']] = best_pos
                    used_positions.add(best_pos)
                else:
                    for p in range(1, field_size + 1):
                        if p not in used_positions:
                            assigned_positions[entry['horse']] = p
                            used_positions.add(p)
                            break

        zone_counts: Dict[str, int] = {
            'front': 0, 'mid': 0, 'back': 0
        }
        for entry in runner_entries:
            desire_pct = (entry['desire'] - 1) / max(1, field_size - 1) * 100
            if desire_pct < 25:
                zone_counts['front'] += 1
            elif desire_pct <= 60:
                zone_counts['mid'] += 1
            else:
                zone_counts['back'] += 1

        results = []
        for entry in runner_entries:
            try:
                pos = assigned_positions.get(entry['horse'], field_size // 2)

                if field_size > 1:
                    percentile = (pos - 1) / (field_size - 1) * 100.0
                else:
                    percentile = 50.0

                desire_pct = (entry['desire'] - 1) / max(1, field_size - 1) * 100
                if desire_pct < 25:
                    zone_count = zone_counts['front']
                elif desire_pct <= 60:
                    zone_count = zone_counts['mid']
                else:
                    zone_count = zone_counts['back']

                zone_capacity = max(1, field_size / 3.0)
                pace_pressure = min(100.0, (zone_count / zone_capacity) * 50.0)

                displacement = abs(pos - round(entry['desire']))
                barrier_penalty = max(0, (entry['barrier'] - 5) / 10.0) if entry['style'] in [RunningStyle.LEADER, RunningStyle.ON_PACE] else 0
                congestion_factor = pace_pressure / 100.0
                settling_difficulty = min(1.0, (displacement / max(1, field_size) * 0.4) + (barrier_penalty * 0.3) + (congestion_factor * 0.3))

                results.append({
                    'horse': entry['horse'],
                    'predicted_settling_position': pos,
                    'settling_position_percentile': round(percentile, 1),
                    'pace_pressure_index': round(pace_pressure, 1),
                    'settling_difficulty': round(settling_difficulty, 3),
                })
            except Exception:
                results.append({
                    'horse': entry['horse'],
                    'predicted_settling_position': field_size // 2,
                    'settling_position_percentile': 50.0,
                    'pace_pressure_index': 30.0,
                    'settling_difficulty': 0.3,
                })

        results.sort(key=lambda x: x['predicted_settling_position'])
        return results

    except Exception:
        return [{
            'horse': r.get('horse', r.get('horseName', r.get('horse_name', 'Unknown'))),
            'predicted_settling_position': i + 1,
            'settling_position_percentile': 50.0,
            'pace_pressure_index': 30.0,
            'settling_difficulty': 0.3,
        } for i, r in enumerate(runners)]


def compute_settling_pace_interaction(settling_pct: float, pace_scenario: str) -> float:
    """
    Compute interaction score between settling percentile and pace scenario.

    Returns interaction score in range [-0.15, +0.15].
    - Front settlers (pct<25): benefit in slow pace (+0.12), hurt in hot pace (-0.10)
    - Mid settlers (25-60): neutral-to-slight benefit in hot pace
    - Back settlers (pct>60): benefit in hot pace (+0.12), hurt in slow pace (-0.10)
    """
    try:
        settling_pct = max(0.0, min(100.0, float(settling_pct)))
        pace_scenario = str(pace_scenario).lower().strip()

        interaction_table = {
            'very_fast': {'front': -0.10, 'mid': 0.04, 'back': 0.12},
            'fast':      {'front': -0.06, 'mid': 0.03, 'back': 0.08},
            'moderate':  {'front': 0.02,  'mid': 0.00, 'back': -0.02},
            'slow':      {'front': 0.10,  'mid': -0.02, 'back': -0.08},
            'very_slow': {'front': 0.12,  'mid': -0.04, 'back': -0.10},
        }

        if settling_pct < 25:
            zone = 'front'
        elif settling_pct <= 60:
            zone = 'mid'
        else:
            zone = 'back'

        scenario_scores = interaction_table.get(pace_scenario, interaction_table['moderate'])
        score = scenario_scores.get(zone, 0.0)

        return round(max(-0.15, min(0.15, score)), 3)
    except Exception:
        return 0.0


def extract_enhanced_speed_map_features(runner: Dict, all_runners: List[Dict], race_data: Dict) -> Dict:
    """
    Extract enhanced speed map features for ML pipeline integration.

    Returns a dict with all new field-aware settling features:
        predicted_settling_pos, settling_percentile, pace_pressure_index,
        settling_difficulty, settling_pace_interaction, leader_congestion,
        speed_horse_ratio, is_congested_speed, settling_vs_barrier
    """
    try:
        field_size = len(all_runners) if all_runners else 10
        horse_name = runner.get('horse', runner.get('horseName', runner.get('horse_name', 'Unknown')))
        barrier = runner.get('barrier', runner.get('draw', 8))
        try:
            barrier = int(barrier)
        except (ValueError, TypeError):
            barrier = 8

        settling_results = predict_field_aware_settling(all_runners, race_data)

        runner_settling = None
        for sr in settling_results:
            if sr.get('horse') == horse_name:
                runner_settling = sr
                break

        if runner_settling is None:
            runner_settling = {
                'predicted_settling_position': field_size // 2,
                'settling_position_percentile': 50.0,
                'pace_pressure_index': 30.0,
                'settling_difficulty': 0.3,
            }

        leader_count = 0
        on_pace_count = 0
        for r in all_runners:
            try:
                style, _, _ = classify_running_style_enhanced(r)
                if style == RunningStyle.LEADER:
                    leader_count += 1
                elif style == RunningStyle.ON_PACE:
                    on_pace_count += 1
            except Exception:
                pass

        speed_horse_ratio = (leader_count + on_pace_count) / max(1, field_size)

        distance = race_data.get('distance_numeric', 1400)
        if isinstance(race_data.get('distance'), str):
            match = re.search(r'(\d+)', str(race_data.get('distance', '1400')))
            distance = int(match.group(1)) if match else 1400
        track = race_data.get('track', race_data.get('course', ''))

        scenario, _ = predict_pace_scenario(all_runners, distance, track)

        settling_pct = runner_settling['settling_position_percentile']
        pace_interaction = compute_settling_pace_interaction(settling_pct, scenario.value)

        style, _, _ = classify_running_style_enhanced(runner)
        style_base = {
            RunningStyle.LEADER: 1.0,
            RunningStyle.ON_PACE: 3.0,
            RunningStyle.MIDFIELD: max(5.0, field_size * 0.45),
            RunningStyle.OFF_PACE: max(7.0, field_size * 0.7),
            RunningStyle.BACKMARKER: max(9.0, field_size * 0.85),
        }
        natural_pos = style_base.get(style, field_size / 2.0)
        settling_vs_barrier = runner_settling['predicted_settling_position'] - natural_pos

        return {
            'predicted_settling_pos': runner_settling['predicted_settling_position'],
            'settling_percentile': round(runner_settling['settling_position_percentile'], 2),
            'pace_pressure_index': round(runner_settling['pace_pressure_index'], 2),
            'settling_difficulty': round(runner_settling['settling_difficulty'], 3),
            'settling_pace_interaction': pace_interaction,
            'leader_congestion': leader_count,
            'speed_horse_ratio': round(speed_horse_ratio, 3),
            'is_congested_speed': leader_count >= 3,
            'settling_vs_barrier': round(settling_vs_barrier, 2),
        }

    except Exception:
        return {
            'predicted_settling_pos': len(all_runners) // 2 if all_runners else 5,
            'settling_percentile': 50.0,
            'pace_pressure_index': 30.0,
            'settling_difficulty': 0.3,
            'settling_pace_interaction': 0.0,
            'leader_congestion': 0,
            'speed_horse_ratio': 0.3,
            'is_congested_speed': False,
            'settling_vs_barrier': 0.0,
        }


if __name__ == '__main__':
    test_runners = [
        {'horse': 'Leader A', 'running_style': 'front runner', 'barrier': 2, 'form': '111x2'},
        {'horse': 'On Pace B', 'running_style': 'prominent', 'barrier': 5, 'form': '23121'},
        {'horse': 'Midfield C', 'form': '43524', 'barrier': 8},
        {'horse': 'Closer D', 'running_style': 'backmarker', 'barrier': 10, 'form': '65312'},
        {'horse': 'Wide Speed E', 'running_style': 'leader', 'barrier': 14, 'form': '12231'},
    ]
    
    test_race = {
        'id': 'test-race-1',
        'distance': '1400m',
        'distance_numeric': 1400,
        'track': 'Randwick'
    }
    
    speed_map = generate_speed_map(test_runners, test_race)
    print(json.dumps(asdict(speed_map), indent=2, default=str))

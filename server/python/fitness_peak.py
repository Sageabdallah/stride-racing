#!/usr/bin/env python3
"""Calculates how fit a horse is based on their preparation (campaign) pattern."""

import os
import sys
import json
from collections import defaultdict
from datetime import datetime, timedelta

try:
    from result_margins import beaten_margin
except ImportError:  # imported with a different cwd; the module sits beside this file
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from result_margins import beaten_margin

try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

SPELL_THRESHOLD_DAYS = 60

_fitness_profile_cache = {}


def get_db_connection():
    if not PSYCOPG2_AVAILABLE:
        return None
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        return None
    try:
        return psycopg2.connect(database_url)
    except Exception as e:
        print(f"[FitnessPeak] DB connection error: {e}", file=sys.stderr)
        return None


def _log(msg):
    print(f"[FitnessPeak] {msg}", file=sys.stderr)


def _fetch_race_history(horse_name, race_date, conn):
    """Fetch merged race history from both tables, deduplicated by race_date."""
    if not conn:
        return []

    seen_dates = {}

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT race_date, actual_position, NULL as margin, distance, race_class
            FROM training_data
            WHERE horse_name = %s AND race_date < %s
              AND (race_class IS NULL OR (
                  LOWER(race_class) NOT LIKE '%%jump%%'
                  AND LOWER(race_class) NOT LIKE '%%trial%%'
                  AND LOWER(race_class) NOT LIKE '%%jumpout%%'))
            ORDER BY race_date ASC
        """, (horse_name, race_date))
        for row in cur.fetchall():
            rd = row[0]
            if rd and rd not in seen_dates:
                pos = None
                if row[1] is not None:
                    try:
                        pos = int(row[1])
                    except (ValueError, TypeError):
                        pass
                seen_dates[rd] = {
                    'race_date': rd,
                    'position': pos,
                    'margin': None,
                    'distance': row[3],
                    'race_class': row[4],
                }
        cur.close()
    except Exception as e:
        _log(f"Error querying training_data for {horse_name}: {e}")

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT race_date, position, margin_lengths, distance_m, race_class
            FROM race_results_history
            WHERE LOWER(horse_name) = LOWER(%s) AND race_date < %s
              AND (race_class IS NULL OR (
                  LOWER(race_class) NOT LIKE '%%jump%%'
                  AND LOWER(race_class) NOT LIKE '%%trial%%'
                  AND LOWER(race_class) NOT LIKE '%%jumpout%%'))
            ORDER BY race_date ASC
        """, (horse_name, race_date))
        for row in cur.fetchall():
            rd = row[0]
            if rd and rd not in seen_dates:
                pos = None
                if row[1] is not None:
                    try:
                        pos = int(row[1])
                    except (ValueError, TypeError):
                        pass
                # Lengths behind the winner, None for the winner, whichever
                # importer wrote the row (result_margins). Read raw, a Punting
                # Form win by three lengths scored as a three-length defeat.
                margin = beaten_margin(pos, row[2])
                seen_dates[rd] = {
                    'race_date': rd,
                    'position': pos,
                    'margin': margin,
                    'distance': row[3],
                    'race_class': row[4],
                }
            elif rd and rd in seen_dates and seen_dates[rd]['margin'] is None and row[2] is not None:
                # A winner training_data left blank must stay blank: filling
                # it from the duplicate row would put the winning margin back.
                known = seen_dates[rd]
                position = known['position'] if known['position'] is not None else row[1]
                known['margin'] = beaten_margin(position, row[2])
        cur.close()
    except Exception as e:
        _log(f"Error querying race_results_history for {horse_name}: {e}")

    runs = sorted(seen_dates.values(), key=lambda x: x['race_date'])
    return runs


def _parse_date(d):
    try:
        return datetime.strptime(str(d)[:10], '%Y-%m-%d')
    except (ValueError, TypeError):
        return None


def _detect_preparations(runs):
    """Split a list of runs into preparations based on 60+ day gaps."""
    if not runs:
        return []

    preps = []
    current_prep = []

    for i, run in enumerate(runs):
        dt = _parse_date(run['race_date'])
        if dt is None:
            continue

        if i == 0:
            run_copy = dict(run)
            run_copy['gap_days'] = None
            run_copy['_dt'] = dt
            current_prep.append(run_copy)
            continue

        prev_dt = None
        for j in range(len(current_prep) - 1, -1, -1):
            if current_prep[j].get('_dt'):
                prev_dt = current_prep[j]['_dt']
                break

        if prev_dt is None:
            for k in range(i - 1, -1, -1):
                prev_dt = _parse_date(runs[k]['race_date'])
                if prev_dt:
                    break

        gap = (dt - prev_dt).days if prev_dt else 0

        if gap >= SPELL_THRESHOLD_DAYS:
            if current_prep:
                for idx, r in enumerate(current_prep):
                    r['run_number'] = idx + 1
                preps.append(current_prep)
            current_prep = []
            run_copy = dict(run)
            run_copy['gap_days'] = gap
            run_copy['_dt'] = dt
            current_prep.append(run_copy)
        else:
            run_copy = dict(run)
            run_copy['gap_days'] = gap
            run_copy['_dt'] = dt
            current_prep.append(run_copy)

    if current_prep:
        for idx, r in enumerate(current_prep):
            r['run_number'] = idx + 1
        preps.append(current_prep)

    return preps


def detect_current_prep(horse_name, race_date, conn):
    """Detect the current preparation state for a horse on a given date."""
    runs = _fetch_race_history(horse_name, race_date, conn)
    if not runs:
        return {
            'runs_this_prep': 1,
            'spell_length_days': 0,
            'days_between_runs': [],
            'avg_run_spacing': 0.0,
            'positions_this_prep': [],
            'margins_this_prep': [],
            'days_since_last_run': 999,
        }

    preps = _detect_preparations(runs)
    if not preps:
        return {
            'runs_this_prep': 1,
            'spell_length_days': 0,
            'days_between_runs': [],
            'avg_run_spacing': 0.0,
            'positions_this_prep': [],
            'margins_this_prep': [],
            'days_since_last_run': 999,
        }

    current_prep = preps[-1]

    race_dt = _parse_date(race_date)
    if race_dt and current_prep:
        last_run_dt = current_prep[-1].get('_dt')
        if last_run_dt:
            gap_to_today = (race_dt - last_run_dt).days
            if gap_to_today >= SPELL_THRESHOLD_DAYS:
                spell_length = gap_to_today
                return {
                    'runs_this_prep': 1,
                    'spell_length_days': spell_length,
                    'days_between_runs': [],
                    'avg_run_spacing': 0.0,
                    'positions_this_prep': [],
                    'margins_this_prep': [],
                    'days_since_last_run': gap_to_today,
                }

    runs_this_prep = len(current_prep)

    spell_length = 0
    if current_prep[0].get('gap_days') and current_prep[0]['gap_days'] >= SPELL_THRESHOLD_DAYS:
        spell_length = current_prep[0]['gap_days']
    elif len(preps) >= 2:
        prev_prep = preps[-2]
        last_of_prev = prev_prep[-1].get('_dt')
        first_of_curr = current_prep[0].get('_dt')
        if last_of_prev and first_of_curr:
            spell_length = (first_of_curr - last_of_prev).days

    days_between = []
    for r in current_prep:
        if r.get('gap_days') is not None and r['run_number'] > 1:
            days_between.append(r['gap_days'])

    avg_spacing = sum(days_between) / len(days_between) if days_between else 0.0

    positions = [r['position'] for r in current_prep if r.get('position') is not None]
    margins = [r['margin'] for r in current_prep if r.get('margin') is not None]

    days_since_last = 999
    if race_dt and current_prep:
        last_dt = current_prep[-1].get('_dt')
        if last_dt:
            days_since_last = (race_dt - last_dt).days

    return {
        'runs_this_prep': runs_this_prep,
        'spell_length_days': spell_length,
        'days_between_runs': days_between,
        'avg_run_spacing': round(avg_spacing, 1),
        'positions_this_prep': positions,
        'margins_this_prep': margins,
        'days_since_last_run': days_since_last,
    }


def build_fitness_profile(horse_name, race_date, conn):
    """Build individual horse fitness profile from ALL historical data."""
    global _fitness_profile_cache
    if horse_name in _fitness_profile_cache:
        return _fitness_profile_cache[horse_name]

    runs = _fetch_race_history(horse_name, race_date, conn)
    preps = _detect_preparations(runs)

    run_stats = defaultdict(lambda: {'wins': 0, 'places': 0, 'starts': 0})

    for prep in preps:
        for r in prep:
            rn = r['run_number']
            key = rn if rn <= 5 else 6
            run_stats[key]['starts'] += 1
            pos = r.get('position')
            if pos is not None:
                if pos == 1:
                    run_stats[key]['wins'] += 1
                    run_stats[key]['places'] += 1
                elif pos <= 3:
                    run_stats[key]['places'] += 1

    win_rates = {}
    place_rates = {}
    for rn in range(1, 7):
        s = run_stats[rn]
        if s['starts'] > 0:
            win_rates[rn] = round(s['wins'] / s['starts'] * 100, 1)
            place_rates[rn] = round(s['places'] / s['starts'] * 100, 1)
        else:
            win_rates[rn] = 0.0
            place_rates[rn] = 0.0

    peak_run = 2
    peak_winrate = 0.0
    for rn in range(1, 7):
        s = run_stats[rn]
        if s['starts'] >= 2 and s['starts'] > 0:
            wr = s['wins'] / s['starts'] * 100
            if wr > peak_winrate:
                peak_winrate = wr
                peak_run = rn

    prep_lengths = [len(p) for p in preps]
    career_avg_prep = sum(prep_lengths) / len(prep_lengths) if prep_lengths else 3.0

    first_up_wr = win_rates.get(1, 0)
    second_up_wr = win_rates.get(2, 0)
    third_up_wr = win_rates.get(3, 0)
    fourth_plus_wr = max(win_rates.get(4, 0), win_rates.get(5, 0), win_rates.get(6, 0))

    is_first_up_specialist = (first_up_wr > 15 and first_up_wr > second_up_wr and first_up_wr > third_up_wr)
    is_third_up_peaker = (third_up_wr > 0 and first_up_wr > 0 and third_up_wr > 2 * first_up_wr)
    is_campaign_horse = (fourth_plus_wr > first_up_wr and fourth_plus_wr > second_up_wr)
    is_improver = (second_up_wr > first_up_wr * 1.5 and run_stats[2]['starts'] >= 2)

    profile = {
        'win_rates_by_run': win_rates,
        'place_rates_by_run': place_rates,
        'peak_run_number': peak_run,
        'peak_run_winrate': round(peak_winrate, 1),
        'career_avg_prep_length': round(career_avg_prep, 1),
        'total_preps': len(preps),
        'pattern_flags': {
            'is_first_up_specialist': is_first_up_specialist,
            'is_third_up_peaker': is_third_up_peaker,
            'is_campaign_horse': is_campaign_horse,
            'is_improver': is_improver,
        },
    }

    _fitness_profile_cache[horse_name] = profile
    return profile


def _linear_slope(values):
    """Simple linear regression slope for a list of numeric values."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    if denominator == 0:
        return 0.0
    return numerator / denominator


def analyze_form_trajectory(positions, margins):
    """Analyze form trajectory within a preparation."""
    trajectory = 'steady'
    margin_trend = 0.0
    placed_last = False
    close_up = False
    improving_and_placed = False

    if positions:
        placed_last = positions[-1] <= 3
        close_up = 4 <= positions[-1] <= 6

        if len(positions) >= 2:
            slope = _linear_slope(positions)
            if slope < -0.5:
                trajectory = 'improving'
            elif slope > 0.5:
                trajectory = 'declining'
            else:
                trajectory = 'steady'

            if len(positions) >= 3:
                mid = len(positions) // 2
                first_half_avg = sum(positions[:mid]) / mid if mid > 0 else 10
                second_half = positions[mid:]
                second_half_avg = sum(second_half) / len(second_half) if second_half else 10
                last_val = positions[-1]
                if first_half_avg > second_half_avg and last_val > second_half_avg + 1:
                    if slope < -0.3:
                        pass
                    elif trajectory == 'improving':
                        pass
                    else:
                        if last_val > min(positions[:-1]):
                            trajectory = 'peaked'

    if margins and len(margins) >= 2:
        margin_trend = round(_linear_slope(margins), 3)

    improving_and_placed = placed_last and trajectory == 'improving'

    return {
        'trajectory': trajectory,
        'margin_trend': margin_trend,
        'placed_last_start': placed_last,
        'close_up_last_start': close_up,
        'improving_and_placed': improving_and_placed,
    }


def calculate_run_spacing_quality(days_since_last_run):
    """Score the quality of spacing based on days since last run."""
    if days_since_last_run is None or days_since_last_run >= 999:
        return 0.75
    d = days_since_last_run
    if 15 <= d <= 21:
        return 1.0
    elif 7 <= d <= 14:
        return 0.85
    elif 22 <= d <= 35:
        return 0.90
    elif 36 <= d <= 60:
        return 0.75
    elif d < 7:
        return 0.70
    else:
        return 0.75


def calculate_spell_length_score(spell_days):
    """Score the spell length for first-up readiness."""
    if spell_days <= 0:
        return 0.85
    if 91 <= spell_days <= 150:
        return 1.0
    elif 60 <= spell_days <= 90:
        return 0.85
    elif 151 <= spell_days <= 270:
        return 0.90
    else:
        return 0.75


def calculate_campaign_tempo(days_between_runs):
    """Classify campaign tempo from inter-run gaps."""
    if not days_between_runs:
        return 'STANDARD'
    avg = sum(days_between_runs) / len(days_between_runs)
    if len(days_between_runs) >= 2:
        std = (sum((d - avg) ** 2 for d in days_between_runs) / len(days_between_runs)) ** 0.5
        if std > 15:
            return 'FRAGILE'
    if avg <= 18:
        return 'TIGHT'
    if avg <= 28:
        return 'STANDARD'
    if avg <= 42:
        return 'PATIENT'
    return 'FRAGILE'


def calculate_weight_penalty(weights_this_prep):
    """Penalty for weight accumulation in current campaign."""
    if not weights_this_prep or len(weights_this_prep) < 2:
        return 0.0
    valid = [w for w in weights_this_prep if w and w > 0]
    if not valid or len(valid) < 2:
        return 0.0
    first_up_weight = valid[0]
    current_weight = valid[-1]
    kg_above = current_weight - first_up_weight
    if kg_above <= 0:
        return 0.0
    return round(-0.15 * kg_above, 2)


def classify_return_type(horse_name, race_date, spell_length_days, conn):
    """For 90+ day spells, classify return type using sectional improvement."""
    if spell_length_days is None or spell_length_days < 90:
        return None
    if not conn:
        return 'UNKNOWN'
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT race_date, last_600m_time, last_400m_time, last_200m_time
            FROM sectional_times
            WHERE LOWER(horse_name) = LOWER(%s)
              AND race_date::date <= %s::date
            ORDER BY race_date::date DESC
            LIMIT 2
        """, (horse_name, str(race_date)[:10]))
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        _log(f"Error querying sectionals for return type ({horse_name}): {e}")
        return 'UNKNOWN'

    if len(rows) < 2:
        return 'UNKNOWN'

    # rows[0] = most recent (first-up back), rows[1] = pre-spell run
    t1 = rows[0][1]
    t2 = rows[1][1]

    if t1 is None or t2 is None:
        t1 = rows[0][2]
        t2 = rows[1][2]

    if t1 is None or t2 is None:
        return 'UNKNOWN'

    try:
        t1_f = float(t1)
        t2_f = float(t2)
    except (ValueError, TypeError):
        return 'UNKNOWN'

    if t2_f == 0:
        return 'UNKNOWN'

    # Negative improvement_pct means first-up was slower (bad)
    improvement_pct = (t2_f - t1_f) / t2_f * 100

    if improvement_pct >= -2.0:  # within 2% or better
        return 'REST_RETURN'
    return 'INJURY_RETURN'


def calculate_fitness_readiness_score(prep_data, profile, form_analysis,
                                      horse_prep_profile=None, age_years=None,
                                      campaign_tempo=None, weight_penalty=0.0,
                                      return_type=None, trainer_first_up_pct=None,
                                      distance_band=None, race_month=None):
    """
    Composite fitness readiness score (0.0 to 1.0).
    Weights: peak_alignment=30%, trajectory=25%, spacing=15%, spell=10%, placed=20%.

    Enhanced with:
    - Individual horse prep profiles (replaces universal peak logic when available)
    - Campaign tempo modifiers (TIGHT third-up amplified, FRAGILE dampened)
    - Age-based modifiers (2yo trajectory boost, 5+ first-up trainer bonus, 3yo Jan-Apr)
    - Weight accumulation penalty (-0.15 per kg above first-up weight)
    - Return type adjustment (REST_RETURN gets first-up bonus, INJURY_RETURN dampened)
    """
    runs_this_prep = prep_data['runs_this_prep']
    peak_run = profile.get('peak_run_number', 2)
    has_individual_data = profile.get('total_preps', 0) >= 2
    run_key = min(runs_this_prep, 5)

    used_individual_profile = False
    if horse_prep_profile and distance_band:
        profile_key = (distance_band, run_key)
        hp = horse_prep_profile.get(profile_key)
        if hp and hp.get('starts', 0) >= 4:
            win_rate = hp['wins'] / hp['starts'] if hp['starts'] > 0 else 0
            # Scale: 20%+ win rate = 1.0, 10% = 0.8, 5% = 0.6, 0% = 0.4
            if win_rate >= 0.20:
                peak_score = 1.0
            elif win_rate >= 0.10:
                peak_score = 0.8
            elif win_rate >= 0.05:
                peak_score = 0.6
            else:
                peak_score = 0.4
            used_individual_profile = True
        else:
            used_individual_profile = False

    if not used_individual_profile:
        if has_individual_data:
            if runs_this_prep == peak_run:
                peak_score = 1.0
            elif abs(runs_this_prep - peak_run) == 1:
                peak_score = 0.8
            else:
                peak_score = 0.5
        else:
            if runs_this_prep in (2, 3):
                peak_score = 1.0
            elif runs_this_prep in (1, 4):
                peak_score = 0.8
            else:
                peak_score = 0.5

    traj = form_analysis.get('trajectory', 'steady')
    traj_map = {'improving': 1.0, 'steady': 0.7, 'declining': 0.3, 'peaked': 0.5}
    traj_score = traj_map.get(traj, 0.7)
    if form_analysis.get('improving_and_placed'):
        traj_score = min(1.0, traj_score + 0.3)
    elif form_analysis.get('placed_last_start'):
        traj_score = min(1.0, traj_score + 0.2)

    if age_years is not None and age_years <= 2 and traj == 'improving':
        traj_score = min(1.0, traj_score * 1.5)

    spacing_score = calculate_run_spacing_quality(prep_data.get('days_since_last_run', 999))

    spell_score = calculate_spell_length_score(prep_data.get('spell_length_days', 0))

    if runs_this_prep == 1 and return_type:
        if return_type == 'REST_RETURN':
            # Competitive immediately — treat as a positive first-up signal
            spell_score = min(1.0, spell_score + 0.15)
        elif return_type == 'INJURY_RETURN':
            # Needs another run — dampen
            spell_score *= 0.7

    if form_analysis.get('placed_last_start'):
        placed_score = 1.0
    elif form_analysis.get('close_up_last_start'):
        placed_score = 0.6
    else:
        placed_score = 0.3

    composite = (
        peak_score * 0.30 +
        traj_score * 0.25 +
        spacing_score * 0.15 +
        spell_score * 0.10 +
        placed_score * 0.20
    )

    if campaign_tempo:
        if campaign_tempo == 'TIGHT' and runs_this_prep == 3:
            composite *= 1.4
        elif campaign_tempo == 'FRAGILE':
            composite *= 0.6

    if age_years is not None:
        # 5+ year-olds first-up with above-average trainer first-up pct
        if age_years >= 5 and runs_this_prep == 1 and trainer_first_up_pct is not None:
            if trainer_first_up_pct > 12.0:  # above ~12% is above average for first-up
                composite *= 1.2

        # 3yo Jan-Apr: reduce first-up discount (carrying fitness from juvenile campaign)
        if age_years == 3 and runs_this_prep == 1 and race_month is not None:
            if 1 <= race_month <= 4:
                # Boost first-up score — they carry fitness from summer
                composite = min(1.0, composite * 1.15)

    if weight_penalty and weight_penalty < 0:
        # Scale penalty: weight_penalty is already in points (e.g., -0.75 for 5kg above)
        # Normalise to 0-1 scale: divide by 10 to get proportional impact
        composite += weight_penalty / 10.0

    return round(min(1.0, max(0.0, composite)), 3)


def _run_label(runs_this_prep):
    labels = {1: '1st-up', 2: '2nd-up', 3: '3rd-up', 4: '4th-up'}
    if runs_this_prep in labels:
        return labels[runs_this_prep]
    return f'{runs_this_prep}th-up'


def _build_description(run_label, is_at_peak, trajectory, improving_and_placed, placed_last):
    parts = [run_label]
    if is_at_peak:
        parts.append('at peak fitness')
    if improving_and_placed:
        parts.append('improving & placed (killer combo)')
    elif trajectory == 'improving':
        parts.append('improving form')
    elif trajectory == 'declining':
        parts.append('form declining')
    elif trajectory == 'peaked':
        parts.append('may have peaked')
    if placed_last and not improving_and_placed:
        parts.append('placed last start')
    return ', '.join(parts)


def get_fitness_peak_data(horse_name, race_date, conn=None):
    """Main entry point. Returns comprehensive fitness data for a horse."""
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    if not conn:
        return _default_result(horse_name)

    try:
        prep_data = detect_current_prep(horse_name, race_date, conn)
        profile = build_fitness_profile(horse_name, race_date, conn)
        form_analysis = analyze_form_trajectory(
            prep_data['positions_this_prep'],
            prep_data['margins_this_prep']
        )
        fitness_score = calculate_fitness_readiness_score(prep_data, profile, form_analysis)

        runs = prep_data['runs_this_prep']
        run_key = runs if runs <= 5 else 6
        peak_run = profile.get('peak_run_number', 2)
        is_at_peak = (runs == peak_run)

        this_run_wr = profile.get('win_rates_by_run', {}).get(run_key, 0.0)
        peak_wr = profile.get('peak_run_winrate', 0.0)

        label = _run_label(runs)
        desc = _build_description(
            label, is_at_peak,
            form_analysis['trajectory'],
            form_analysis['improving_and_placed'],
            form_analysis['placed_last_start']
        )

        result = {
            'runs_this_prep': runs,
            'run_label': label,
            'spell_length_days': prep_data['spell_length_days'],
            'is_at_peak_run': is_at_peak,
            'peak_run_number': peak_run,
            'peak_run_winrate': peak_wr,
            'this_run_winrate': this_run_wr,
            'fitness_readiness_score': fitness_score,
            'prep_form_trajectory': form_analysis['trajectory'],
            'margin_improvement_trend': form_analysis['margin_trend'],
            'placed_last_start': form_analysis['placed_last_start'],
            'improving_and_placed': form_analysis['improving_and_placed'],
            'run_spacing_quality': calculate_run_spacing_quality(prep_data.get('days_since_last_run', 999)),
            'career_avg_prep_length': profile.get('career_avg_prep_length', 3.0),
            'pattern_flags': profile.get('pattern_flags', {
                'is_first_up_specialist': False,
                'is_third_up_peaker': False,
                'is_campaign_horse': False,
                'is_improver': False,
            }),
            'positions_this_prep': prep_data['positions_this_prep'],
            'description': desc,
        }

        return result

    except Exception as e:
        _log(f"Error computing fitness data for {horse_name}: {e}")
        return _default_result(horse_name)
    finally:
        if should_close and conn:
            try:
                conn.close()
            except Exception:
                pass


def _default_result(horse_name):
    return {
        'runs_this_prep': 1,
        'run_label': '1st-up',
        'spell_length_days': 0,
        'is_at_peak_run': False,
        'peak_run_number': 2,
        'peak_run_winrate': 0.0,
        'this_run_winrate': 0.0,
        'fitness_readiness_score': 0.5,
        'prep_form_trajectory': 'steady',
        'margin_improvement_trend': 0.0,
        'placed_last_start': False,
        'improving_and_placed': False,
        'run_spacing_quality': 0.75,
        'career_avg_prep_length': 3.0,
        'pattern_flags': {
            'is_first_up_specialist': False,
            'is_third_up_peaker': False,
            'is_campaign_horse': False,
            'is_improver': False,
        },
        'positions_this_prep': [],
        'description': '1st-up, no history available',
    }


def clear_cache():
    global _fitness_profile_cache
    _fitness_profile_cache = {}


if __name__ == '__main__':
    test_horses = ['Ndola', 'Wanda Rox']
    test_date = '2026-01-24'

    conn = get_db_connection()
    if not conn:
        print("No database connection available", file=sys.stderr)
        sys.exit(1)

    results = {}
    for horse in test_horses:
        _log(f"Testing {horse}...")
        clear_cache()
        data = get_fitness_peak_data(horse, test_date, conn)
        results[horse] = data
        _log(f"  {horse}: {data['run_label']}, score={data['fitness_readiness_score']}, "
             f"trajectory={data['prep_form_trajectory']}, peak_run={data['peak_run_number']}")

    conn.close()
    print(json.dumps(results, indent=2, default=str))

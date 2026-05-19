#!/usr/bin/env python3
"""
Track Condition Performance Database & Pace Energy Model

Two algorithm enhancements:

1. TrackConditionDatabase - tracks horse performance by going/track condition
   using training_data table in PostgreSQL.

2. PaceEnergyModel - models energy expenditure curves for different running
   styles to enhance pace predictions.

Uses training_data table for historical analysis.
"""

import os
import sys
import re
import psycopg2
from typing import Dict, Optional, Any


def _get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        return None
    try:
        return psycopg2.connect(database_url)
    except Exception as e:
        print(f"[TrackConditionDB] DB connection error: {e}", file=sys.stderr)
        return None


GOING_KEYWORDS = {
    'firm': ['firm', 'hard', 'fast'],
    'good': ['good', 'dead', 'fair', 'standard'],
    'soft': ['soft', 'yielding', 'wet'],
    'heavy': ['heavy', 'boggy', 'slow', 'very soft'],
    'synthetic': ['synthetic', 'polytrack', 'all weather', 'tapeta', 'cushion', 'pro-ride'],
}


def normalize_going(going_str: str) -> str:
    """
    Converts various going descriptions to standardized categories.

    Examples:
        'Good to Firm' -> 'firm'
        'Soft 5'       -> 'soft'
        'Heavy 8'      -> 'heavy'
        'Yielding'     -> 'soft'
        'Synthetic'    -> 'synthetic'
        'Good 4'       -> 'good'
        'Dead 5'       -> 'good'
    """
    if not going_str:
        return 'good'

    text = going_str.lower().strip()
    text = re.sub(r'\d+', '', text).strip()

    for category in ['synthetic', 'heavy', 'soft', 'firm', 'good']:
        for keyword in GOING_KEYWORDS[category]:
            if keyword in text:
                return category

    return 'good'


class TrackConditionDatabase:
    """Tracks horse performance by going / track condition."""

    def __init__(self):
        self._cache: Dict[str, Any] = {}

    def _conn(self):
        return _get_db_connection()

    def _cache_key(self, *parts) -> str:
        return '|'.join(str(p).lower() for p in parts)

    def get_horse_going_record(self, horse_name: str, going: str) -> dict:
        """
        Return performance record for a horse on a specific going category.

        Keys: runs_on_going, wins_on_going, places_on_going, strike_rate,
              is_specialist, going_adjustment
        """
        norm = normalize_going(going)
        ck = self._cache_key('horse_going', horse_name, norm)
        if ck in self._cache:
            return self._cache[ck]

        neutral = {
            'runs_on_going': 0,
            'wins_on_going': 0,
            'places_on_going': 0,
            'strike_rate': 0.0,
            'is_specialist': False,
            'going_adjustment': 1.0,
        }

        conn = self._conn()
        if not conn:
            return neutral

        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT going, actual_position FROM training_data WHERE horse_name = %s AND going IS NOT NULL",
                (horse_name,),
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()

            runs = 0
            wins = 0
            places = 0
            total_runs_all = len(rows)

            for row_going, pos in rows:
                if normalize_going(row_going) == norm:
                    runs += 1
                    if pos is not None:
                        if pos == 1:
                            wins += 1
                        if pos <= 3:
                            places += 1

            if runs < 3:
                result = {
                    'runs_on_going': runs,
                    'wins_on_going': wins,
                    'places_on_going': places,
                    'strike_rate': wins / runs if runs else 0.0,
                    'is_specialist': False,
                    'going_adjustment': 1.0,
                }
                self._cache[ck] = result
                return result

            strike = wins / runs if runs else 0.0
            place_rate = places / runs if runs else 0.0
            is_specialist = strike > 0.25 and runs >= 3

            if is_specialist:
                adj = min(1.15, 1.0 + strike * 0.3)
            elif strike > 0.15:
                adj = 1.0 + (strike - 0.15) * 0.5
            elif runs >= 5 and strike < 0.05:
                adj = max(0.85, 1.0 - (0.05 - strike) * 3)
            else:
                adj = 1.0

            adj = round(max(0.85, min(1.15, adj)), 3)

            result = {
                'runs_on_going': runs,
                'wins_on_going': wins,
                'places_on_going': places,
                'strike_rate': round(strike, 3),
                'is_specialist': is_specialist,
                'going_adjustment': adj,
            }
            self._cache[ck] = result
            return result

        except Exception as e:
            print(f"[TrackConditionDB] Error fetching going record for {horse_name}: {e}", file=sys.stderr)
            if conn:
                conn.close()
            return neutral

    def batch_get_going_adjustments(self, horse_names: list, going: str) -> Dict[str, float]:
        """
        Batch fetch going adjustments for multiple horses in a single DB query.

        Returns dict mapping horse_name → going_adjustment (0.85-1.15).
        """
        norm = normalize_going(going)
        results: Dict[str, float] = {}
        uncached = []

        for name in horse_names:
            ck = self._cache_key('horse_going', name, norm)
            if ck in self._cache:
                results[name] = self._cache[ck].get('going_adjustment', 1.0)
            else:
                uncached.append(name)

        if not uncached:
            return results

        conn = self._conn()
        if not conn:
            for name in uncached:
                results[name] = 1.0
            return results

        try:
            cur = conn.cursor()
            placeholders = ','.join(['%s'] * len(uncached))
            cur.execute(
                f"SELECT horse_name, going, actual_position FROM training_data "
                f"WHERE horse_name IN ({placeholders}) AND going IS NOT NULL",
                uncached,
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()

            horse_data: Dict[str, list] = {n: [] for n in uncached}
            for horse_name, row_going, pos in rows:
                if horse_name in horse_data:
                    horse_data[horse_name].append((row_going, pos))

            for name, recs in horse_data.items():
                runs = 0
                wins = 0
                for row_going, pos in recs:
                    if normalize_going(row_going) == norm:
                        runs += 1
                        if pos is not None and pos == 1:
                            wins += 1

                if runs < 3:
                    adj = 1.0
                else:
                    strike = wins / runs
                    if strike > 0.25:
                        adj = min(1.15, 1.0 + strike * 0.3)
                    elif strike > 0.15:
                        adj = 1.0 + (strike - 0.15) * 0.5
                    elif runs >= 5 and strike < 0.05:
                        adj = max(0.85, 1.0 - (0.05 - strike) * 3)
                    else:
                        adj = 1.0
                    adj = round(max(0.85, min(1.15, adj)), 3)

                results[name] = adj
                ck = self._cache_key('horse_going', name, norm)
                self._cache[ck] = {'going_adjustment': adj, 'runs_on_going': runs, 'wins_on_going': wins}

        except Exception as e:
            print(f"[TrackConditionDB] Batch query error: {e}", file=sys.stderr)
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            for name in uncached:
                results.setdefault(name, 1.0)

        return results

    def get_track_condition_profile(self, track: str, going: str) -> dict:
        """
        Return track-level condition profile.

        Keys: leader_win_rate, avg_winning_barrier, favors_speed, condition_insight
        """
        norm = normalize_going(going)
        ck = self._cache_key('track_profile', track, norm)
        if ck in self._cache:
            return self._cache[ck]

        neutral = {
            'leader_win_rate': 0.0,
            'avg_winning_barrier': 0.0,
            'favors_speed': False,
            'condition_insight': 'Insufficient data for track condition profile.',
        }

        conn = self._conn()
        if not conn:
            return neutral

        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT going, actual_position, barrier
                   FROM training_data
                   WHERE LOWER(track) = LOWER(%s) AND going IS NOT NULL""",
                (track,),
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()

            filtered = [(pos, barrier) for row_going, pos, barrier in rows if normalize_going(row_going) == norm]

            if len(filtered) < 10:
                self._cache[ck] = neutral
                return neutral

            winners = [(pos, barrier) for pos, barrier in filtered if pos == 1]
            total_races = len(set(filtered))
            win_count = len(winners)

            barriers_of_winners = [b for _, b in winners if b is not None and b > 0]
            avg_barrier = sum(barriers_of_winners) / len(barriers_of_winners) if barriers_of_winners else 0.0

            inside_winners = sum(1 for b in barriers_of_winners if b <= 4)
            leader_proxy_rate = inside_winners / len(barriers_of_winners) if barriers_of_winners else 0.0

            favors_speed = leader_proxy_rate > 0.45 or avg_barrier < 5.0

            if favors_speed:
                insight = f"On {norm} ground at {track}, inside barriers and speed runners are favoured (avg winning barrier {avg_barrier:.1f})."
            elif avg_barrier > 8:
                insight = f"On {norm} ground at {track}, wider barriers have performed well (avg winning barrier {avg_barrier:.1f})."
            else:
                insight = f"On {norm} ground at {track}, no strong barrier bias detected (avg winning barrier {avg_barrier:.1f})."

            result = {
                'leader_win_rate': round(leader_proxy_rate, 3),
                'avg_winning_barrier': round(avg_barrier, 1),
                'favors_speed': favors_speed,
                'condition_insight': insight,
            }
            self._cache[ck] = result
            return result

        except Exception as e:
            print(f"[TrackConditionDB] Error fetching track profile for {track}/{norm}: {e}", file=sys.stderr)
            if conn:
                conn.close()
            return neutral

    def get_going_suitability_score(self, horse_name: str, going: str, track: str) -> dict:
        """
        Combined suitability score for a horse on a given going at a track.

        Keys: suitability_score (0-100), suitability_adjustment, is_going_specialist,
              going_insight
        """
        norm = normalize_going(going)
        ck = self._cache_key('suitability', horse_name, norm, track)
        if ck in self._cache:
            return self._cache[ck]

        horse_record = self.get_horse_going_record(horse_name, going)
        track_profile = self.get_track_condition_profile(track, going)

        if horse_record['runs_on_going'] < 3:
            result = {
                'suitability_score': 50,
                'suitability_adjustment': 1.0,
                'is_going_specialist': False,
                'going_insight': f"Insufficient data for {horse_name} on {norm} ground (only {horse_record['runs_on_going']} runs).",
            }
            self._cache[ck] = result
            return result

        strike = horse_record['strike_rate']
        place_rate = horse_record['places_on_going'] / horse_record['runs_on_going'] if horse_record['runs_on_going'] else 0.0

        score = 50.0
        score += strike * 80
        score += place_rate * 30
        if horse_record['is_specialist']:
            score += 10

        score = max(0, min(100, round(score)))

        adj = horse_record['going_adjustment']

        if score >= 70:
            insight = f"{horse_name} has an excellent record on {norm} ground ({horse_record['wins_on_going']}/{horse_record['runs_on_going']} wins)."
        elif score >= 55:
            insight = f"{horse_name} handles {norm} ground adequately ({horse_record['wins_on_going']}/{horse_record['runs_on_going']} wins)."
        else:
            insight = f"{horse_name} has a poor record on {norm} ground ({horse_record['wins_on_going']}/{horse_record['runs_on_going']} wins)."

        result = {
            'suitability_score': score,
            'suitability_adjustment': adj,
            'is_going_specialist': horse_record['is_specialist'],
            'going_insight': insight,
        }
        self._cache[ck] = result
        return result


ENERGY_RATES = {
    'leader':     {'early': 0.08, 'late': 0.12},
    'on-pace':    {'early': 0.06, 'late': 0.10},
    'midfield':   {'early': 0.05, 'late': 0.09},
    'off-pace':   {'early': 0.04, 'late': 0.08},
    'backmarker': {'early': 0.04, 'late': 0.08},
}


class PaceEnergyModel:
    """Models energy expenditure curves for different running styles."""

    @staticmethod
    def get_fitness_factor(days_since_run: int, age: int = 5) -> float:
        """
        Return fitness multiplier based on days since last run.

        Ranges:
            < 7  days  -> 0.90  (backing up)
            7-13 days  -> 0.93  (short break)
            14-20 days -> 0.97  (fresh)
            21-35 days -> 1.00  (optimal)
            36-60 days -> 0.95  (long break)
            60+  days  -> 0.88  (very long break)
        """
        if days_since_run < 7:
            return 0.90
        elif days_since_run <= 13:
            return 0.93
        elif days_since_run <= 20:
            return 0.97
        elif days_since_run <= 35:
            return 1.0
        elif days_since_run <= 60:
            return 0.95
        else:
            return 0.88

    @staticmethod
    def calculate_pace_pressure_score(leaders_count: int, field_size: int, distance: int) -> dict:
        """
        Evaluate pace pressure in a race.

        Keys: pressure_score (0-100), is_hot_pace, is_soft_pace, pressure_insight
        """
        if field_size <= 0:
            field_size = 1
        leader_ratio = leaders_count / field_size

        score = leader_ratio * 200

        if distance <= 1200:
            score *= 1.15
        elif distance >= 2000:
            score *= 0.85

        score = max(0, min(100, round(score)))

        is_hot = score >= 65
        is_soft = score <= 25

        if is_hot:
            insight = f"Hot pace expected with {leaders_count} leaders in a {field_size}-runner field over {distance}m. Closers advantaged."
        elif is_soft:
            insight = f"Soft pace likely with only {leaders_count} leader(s) in {field_size} runners over {distance}m. Leaders advantaged."
        else:
            insight = f"Moderate pace expected ({leaders_count} leaders, {field_size} runners, {distance}m). No strong bias."

        return {
            'pressure_score': score,
            'is_hot_pace': is_hot,
            'is_soft_pace': is_soft,
            'pressure_insight': insight,
        }

    @staticmethod
    def calculate_energy_curve(
        running_style: str,
        distance: int,
        pace_pressure: float,
        days_since_run: int,
    ) -> dict:
        """
        Calculate energy curve for a runner.

        Args:
            running_style: one of leader, on-pace, midfield, off-pace, backmarker
            distance: race distance in metres
            pace_pressure: 0-100 pressure score
            days_since_run: days since the horse last raced

        Returns dict with:
            energy_at_800m, energy_at_400m, energy_at_finish,
            fatigue_factor (0.8-1.0), collapse_probability (0-1)
        """
        style_key = running_style.lower().replace(' ', '-')
        if style_key not in ENERGY_RATES:
            style_key = 'midfield'

        rates = ENERGY_RATES[style_key]
        early_rate = rates['early']
        late_rate = rates['late']

        if pace_pressure >= 65:
            pressure_mult = 1.3
        elif pace_pressure <= 25:
            pressure_mult = 0.7
        else:
            pressure_mult = 0.7 + (pace_pressure - 25) * (0.6 / 40)

        early_rate *= pressure_mult
        late_rate *= pressure_mult

        fitness = PaceEnergyModel.get_fitness_factor(days_since_run)
        early_rate *= (2.0 - fitness)
        late_rate *= (2.0 - fitness)

        energy = 100.0

        dist_to_800 = max(0, distance - 800)
        dist_800_to_400 = min(400, max(0, distance - 400)) if distance > 800 else max(0, distance - 400)
        dist_last_400 = min(400, distance)

        if distance > 800:
            early_segment = dist_to_800
        else:
            early_segment = distance

        energy_after_early = energy - early_rate * early_segment
        energy_at_800m = max(0, energy_after_early)

        if distance > 800:
            remaining_to_400 = dist_800_to_400
            energy_after_mid = energy_at_800m - late_rate * remaining_to_400
        else:
            energy_after_mid = energy_at_800m

        energy_at_400m = max(0, energy_after_mid)

        if distance > 400:
            final_segment = dist_last_400
            energy_at_finish = max(0, energy_at_400m - late_rate * final_segment)
        else:
            energy_at_finish = max(0, energy_at_400m - late_rate * distance)

        energy_at_800m = round(max(0, min(100, energy_at_800m)), 1)
        energy_at_400m = round(max(0, min(100, energy_at_400m)), 1)
        energy_at_finish = round(max(0, min(100, energy_at_finish)), 1)

        fatigue_factor = round(max(0.8, min(1.0, energy_at_finish / 100)), 3)

        pace_pressure_factor = pressure_mult
        collapse_prob = max(0.0, (100 - energy_at_400m) / 100) * pace_pressure_factor
        collapse_prob = round(max(0.0, min(1.0, collapse_prob)), 3)

        return {
            'energy_at_800m': energy_at_800m,
            'energy_at_400m': energy_at_400m,
            'energy_at_finish': energy_at_finish,
            'fatigue_factor': fatigue_factor,
            'collapse_probability': collapse_prob,
        }


if __name__ == '__main__':
    print("=== Track Condition Database Tests ===", file=sys.stderr)
    assert normalize_going('Good to Firm') == 'firm'
    assert normalize_going('Soft 5') == 'soft'
    assert normalize_going('Heavy 8') == 'heavy'
    assert normalize_going('Yielding') == 'soft'
    assert normalize_going('Synthetic') == 'synthetic'
    assert normalize_going('Good 4') == 'good'
    assert normalize_going('Dead') == 'good'
    assert normalize_going('') == 'good'
    assert normalize_going('All Weather') == 'synthetic'
    print("normalize_going: all tests passed", file=sys.stderr)

    model = PaceEnergyModel()
    assert model.get_fitness_factor(5) == 0.90
    assert model.get_fitness_factor(10) == 0.93
    assert model.get_fitness_factor(17) == 0.97
    assert model.get_fitness_factor(28) == 1.0
    assert model.get_fitness_factor(45) == 0.95
    assert model.get_fitness_factor(90) == 0.88
    print("get_fitness_factor: all tests passed", file=sys.stderr)

    pp = model.calculate_pace_pressure_score(leaders_count=4, field_size=10, distance=1200)
    assert pp['is_hot_pace'] is True
    pp2 = model.calculate_pace_pressure_score(leaders_count=1, field_size=14, distance=2400)
    assert pp2['is_soft_pace'] is True
    print("calculate_pace_pressure_score: all tests passed", file=sys.stderr)

    ec = model.calculate_energy_curve('leader', 1600, 70, 28)
    assert 0 <= ec['energy_at_800m'] <= 100
    assert 0 <= ec['energy_at_400m'] <= 100
    assert 0 <= ec['energy_at_finish'] <= 100
    assert 0.8 <= ec['fatigue_factor'] <= 1.0
    assert 0 <= ec['collapse_probability'] <= 1.0

    ec2 = model.calculate_energy_curve('backmarker', 1600, 30, 28)
    assert ec2['energy_at_finish'] > ec['energy_at_finish'], "Backmarker should have more energy at finish than leader"
    print("calculate_energy_curve: all tests passed", file=sys.stderr)

    print("\nAll self-tests passed.", file=sys.stderr)

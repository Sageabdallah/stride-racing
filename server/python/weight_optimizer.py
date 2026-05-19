#!/usr/bin/env python3
"""Feature weight optimizer and enhanced barrier analyzer providing data-driven replacements for hardcoded probability adjustment multipliers."""

import os
import sys
import json
import re
import logging
from collections import defaultdict
from typing import Dict, Any, Optional, List, Tuple

import numpy as np

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

try:
    from scipy.optimize import minimize
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))
logger.setLevel(logging.INFO)

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
WEIGHTS_FILE = os.path.join(MODELS_DIR, 'optimized_weights.json')

DEFAULT_WEIGHTS = {
    'pace_advantage_weight': 1.0,
    'class_drop_weight': 0.08,
    'elite_connection_weight': 0.10,
    'barrier_advantage_weight': 1.0,
    'form_improvement_weight_strong': 0.12,
    'form_improvement_weight_moderate': 0.05,
    'market_steam_weight': 0.10,
    'sectional_speed_weight': 0.08,
    'jockey_streak_weight_combo': 0.12,
    'jockey_streak_weight_single': 0.08,
    'breeding_match_weight': 1.0,
}

WEIGHT_KEYS = list(DEFAULT_WEIGHTS.keys())


def _get_db_connection():
    """Get PostgreSQL connection from DATABASE_URL."""
    if not HAS_PSYCOPG2:
        logger.warning("psycopg2 not available")
        return None
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.warning("DATABASE_URL not set")
        return None
    try:
        return psycopg2.connect(database_url)
    except Exception as e:
        logger.error(f"DB connection error: {e}")
        return None


def _parse_distance_meters(distance_str) -> int:
    """Extract numeric meters from distance string."""
    if not distance_str:
        return 1400
    if isinstance(distance_str, (int, float)):
        return int(distance_str)
    try:
        match = re.search(r'(\d+)', str(distance_str))
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return 1400



class FeatureWeightOptimizer:
    """Optimizes the manual probability adjustment multipliers currently hardcoded in calculate_sophisticated_adjustments()."""

    def __init__(self, regularization_lambda: float = 0.01):
        self.regularization_lambda = regularization_lambda
        self.optimized_weights = dict(DEFAULT_WEIGHTS)
        self.training_data = []
        self.optimization_result = None

    def _fetch_training_data(self) -> List[Dict[str, Any]]:
        """Fetch historical selections with outcomes from training_data table."""
        conn = _get_db_connection()
        if not conn:
            logger.warning("No DB connection, cannot fetch training data")
            return []

        rows = []
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT horse_name, track, race_date, race_number,
                       predicted_win_prob, actual_position, won,
                       market_odds, confidence, expected_value,
                       race_class, distance, going, barrier,
                       jockey, trainer
                FROM training_data
                WHERE actual_position IS NOT NULL
                  AND market_odds IS NOT NULL
                  AND market_odds > 1.0
                ORDER BY race_date
            """)
            columns = [desc[0] for desc in cursor.description]
            for row in cursor.fetchall():
                rows.append(dict(zip(columns, row)))
            cursor.close()
            logger.info(f"Fetched {len(rows)} training records")
        except Exception as e:
            logger.error(f"Error fetching training data: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return rows

    def _extract_feature_signals(self, record: Dict[str, Any]) -> Dict[str, float]:
        """
        Extract feature signals from a training record.
        Maps each record to the adjustment categories being optimized.
        """
        signals = {}
        barrier = record.get('barrier')
        distance = _parse_distance_meters(record.get('distance'))
        race_class = str(record.get('race_class', '')).lower()
        market_odds = float(record.get('market_odds', 10.0))
        win_prob = float(record.get('predicted_win_prob') or 0) / 100.0 if record.get('predicted_win_prob') else 1.0 / max(1.01, market_odds)
        jockey = record.get('jockey', '')
        trainer = record.get('trainer', '')

        signals['pace_advantage'] = 0.0
        if distance <= 1200:
            if barrier and barrier <= 4:
                signals['pace_advantage'] = 0.05
            elif barrier and barrier >= 10:
                signals['pace_advantage'] = -0.03

        is_class_drop = 0
        if 'maiden' in race_class:
            is_class_drop = 0
        elif 'benchmark' in race_class or 'bm' in race_class:
            bm_match = re.search(r'(\d+)', race_class)
            if bm_match:
                bm = int(bm_match.group(1))
                if bm <= 65:
                    is_class_drop = 1
        signals['class_drop'] = float(is_class_drop)

        is_elite = 0
        elite_jockeys = ['mcdonald', 'bowman', 'lane', 'zahra', 'purton', 'moreira']
        elite_trainers = ['waller', 'waterhouse', 'cummings', 'maher', 'weir']
        j_lower = (jockey or '').lower()
        t_lower = (trainer or '').lower()
        if any(ej in j_lower for ej in elite_jockeys) and any(et in t_lower for et in elite_trainers):
            is_elite = 1
        signals['elite_connection'] = float(is_elite)

        barrier_adv = 0.0
        if barrier:
            if barrier <= 4:
                barrier_adv = 0.05
            elif barrier <= 8:
                barrier_adv = 0.0
            elif barrier <= 12:
                barrier_adv = -0.05
            else:
                barrier_adv = -0.12
        signals['barrier_advantage'] = barrier_adv

        signals['form_improvement_strong'] = 0.0
        signals['form_improvement_moderate'] = 0.0
        if win_prob > 0.20:
            signals['form_improvement_strong'] = 1.0
        elif win_prob > 0.12:
            signals['form_improvement_moderate'] = 1.0

        signals['market_steam'] = 0.0
        if market_odds < 5.0:
            signals['market_steam'] = 1.0

        signals['sectional_speed'] = 0.0
        if distance <= 1400 and market_odds < 8.0:
            signals['sectional_speed'] = 1.0

        signals['jockey_streak_combo'] = 0.0
        signals['jockey_streak_single'] = 0.0
        if any(ej in j_lower for ej in elite_jockeys):
            if any(et in t_lower for et in elite_trainers):
                signals['jockey_streak_combo'] = 1.0
            else:
                signals['jockey_streak_single'] = 1.0

        signals['breeding_match'] = 1.0
        if distance >= 2000 and 'staying' in race_class:
            signals['breeding_match'] = 1.05
        elif distance <= 1100:
            signals['breeding_match'] = 1.02

        return signals

    def _compute_adjusted_probability(self, base_prob: float, signals: Dict[str, float],
                                       weights: np.ndarray) -> float:
        """Compute adjusted probability given feature signals and weights."""
        adjustment = 1.0

        adjustment += signals['pace_advantage'] * weights[0]
        if signals['class_drop'] > 0:
            adjustment += weights[1]
        if signals['elite_connection'] > 0:
            adjustment += weights[2]
        adjustment += signals['barrier_advantage'] * weights[3]
        if signals['form_improvement_strong'] > 0:
            adjustment += weights[4]
        elif signals['form_improvement_moderate'] > 0:
            adjustment += weights[5]
        if signals['market_steam'] > 0:
            adjustment += weights[6]
        if signals['sectional_speed'] > 0:
            adjustment += weights[7]
        if signals['jockey_streak_combo'] > 0:
            adjustment += weights[8]
        elif signals['jockey_streak_single'] > 0:
            adjustment += weights[9]
        adjustment *= signals['breeding_match'] ** weights[10]

        adjusted = base_prob * max(0.5, min(2.0, adjustment))
        return max(0.01, min(0.95, adjusted))

    def _objective(self, weights: np.ndarray) -> float:
        """
        Negative log-likelihood objective with L2 regularization.
        Minimizing this finds weights that best predict historical outcomes.
        """
        import math

        neg_log_likelihood = 0.0
        n = len(self.training_data)

        if n == 0:
            return 0.0

        for record, signals in self.training_data:
            market_odds = float(record.get('market_odds', 10.0))
            base_prob = 1.0 / max(1.01, market_odds)
            won = int(record.get('won', 0))

            adj_prob = self._compute_adjusted_probability(base_prob, signals, weights)

            if won == 1:
                neg_log_likelihood -= math.log(max(adj_prob, 1e-10))
            else:
                neg_log_likelihood -= math.log(max(1.0 - adj_prob, 1e-10))

        neg_log_likelihood /= n

        l2_penalty = self.regularization_lambda * np.sum((weights - np.array(list(DEFAULT_WEIGHTS.values()))) ** 2)
        return neg_log_likelihood + l2_penalty

    def optimize(self) -> Dict[str, float]:
        """
        Run the optimization pipeline:
        1. Fetch training data
        2. Extract feature signals
        3. Minimize negative log-likelihood with L2 regularization
        4. Save optimized weights
        """
        if not HAS_SCIPY:
            logger.error("scipy not available, returning default weights")
            return dict(DEFAULT_WEIGHTS)

        raw_data = self._fetch_training_data()
        if len(raw_data) < 50:
            logger.warning(f"Insufficient training data ({len(raw_data)} records), using defaults")
            return dict(DEFAULT_WEIGHTS)

        self.training_data = []
        for record in raw_data:
            signals = self._extract_feature_signals(record)
            self.training_data.append((record, signals))

        logger.info(f"Optimizing weights with {len(self.training_data)} records...")

        initial_weights = np.array(list(DEFAULT_WEIGHTS.values()))

        bounds = [
            (0.1, 3.0),    # pace_advantage_weight
            (0.0, 0.25),   # class_drop_weight
            (0.0, 0.25),   # elite_connection_weight
            (0.1, 3.0),    # barrier_advantage_weight
            (0.0, 0.25),   # form_improvement_weight_strong
            (0.0, 0.15),   # form_improvement_weight_moderate
            (0.0, 0.25),   # market_steam_weight
            (0.0, 0.20),   # sectional_speed_weight
            (0.0, 0.25),   # jockey_streak_weight_combo
            (0.0, 0.20),   # jockey_streak_weight_single
            (0.5, 2.0),    # breeding_match_weight
        ]

        try:
            result = minimize(
                self._objective,
                initial_weights,
                method='L-BFGS-B',
                bounds=bounds,
                options={'maxiter': 500, 'ftol': 1e-8}
            )

            self.optimization_result = result

            if result.success:
                optimized = result.x
                self.optimized_weights = dict(zip(WEIGHT_KEYS, optimized.tolist()))
                logger.info(f"Optimization converged. Final loss: {result.fun:.6f}")
            else:
                logger.warning(f"Optimization did not converge: {result.message}")
                optimized = result.x
                self.optimized_weights = dict(zip(WEIGHT_KEYS, optimized.tolist()))

        except Exception as e:
            logger.error(f"Optimization failed: {e}")
            self.optimized_weights = dict(DEFAULT_WEIGHTS)

        self._save_weights()
        return dict(self.optimized_weights)

    def _save_weights(self):
        """Save optimized weights to JSON file."""
        try:
            os.makedirs(MODELS_DIR, exist_ok=True)
            output = {
                'weights': self.optimized_weights,
                'defaults': DEFAULT_WEIGHTS,
                'num_records': len(self.training_data),
                'converged': self.optimization_result.success if self.optimization_result else False,
                'final_loss': float(self.optimization_result.fun) if self.optimization_result else None,
            }
            with open(WEIGHTS_FILE, 'w') as f:
                json.dump(output, f, indent=2)
            logger.info(f"Saved optimized weights to {WEIGHTS_FILE}")
        except Exception as e:
            logger.error(f"Error saving weights: {e}")


def get_optimized_weights() -> Dict[str, float]:
    """
    Load optimized weights from file, falling back to defaults.

    Returns a dict of weight_key -> float value.
    """
    try:
        if os.path.exists(WEIGHTS_FILE):
            with open(WEIGHTS_FILE, 'r') as f:
                data = json.load(f)
            weights = data.get('weights', {})
            if weights:
                merged = dict(DEFAULT_WEIGHTS)
                merged.update(weights)
                return merged
    except Exception as e:
        logger.error(f"Error loading weights: {e}")

    return dict(DEFAULT_WEIGHTS)



DEFAULT_BARRIER_PROFILES = {
    'sprint': {
        1: 1.15, 2: 1.12, 3: 1.08, 4: 1.05,
        5: 1.02, 6: 1.00, 7: 0.98, 8: 0.95,
        9: 0.92, 10: 0.88, 11: 0.85, 12: 0.82,
        13: 0.78, 14: 0.75, 15: 0.72, 16: 0.70,
    },
    'short': {
        1: 1.10, 2: 1.08, 3: 1.06, 4: 1.04,
        5: 1.02, 6: 1.00, 7: 0.99, 8: 0.97,
        9: 0.95, 10: 0.92, 11: 0.90, 12: 0.87,
        13: 0.84, 14: 0.82, 15: 0.80, 16: 0.78,
    },
    'medium': {
        1: 1.06, 2: 1.05, 3: 1.04, 4: 1.03,
        5: 1.02, 6: 1.01, 7: 1.00, 8: 0.99,
        9: 0.98, 10: 0.96, 11: 0.94, 12: 0.92,
        13: 0.90, 14: 0.88, 15: 0.86, 16: 0.84,
    },
    'staying': {
        1: 1.03, 2: 1.02, 3: 1.02, 4: 1.01,
        5: 1.01, 6: 1.00, 7: 1.00, 8: 0.99,
        9: 0.99, 10: 0.98, 11: 0.97, 12: 0.96,
        13: 0.95, 14: 0.94, 15: 0.93, 16: 0.92,
    },
}

DEFAULT_LEADER_RATES = {
    'Randwick': 0.22,
    'Flemington': 0.18,
    'Caulfield': 0.25,
    'Moonee Valley': 0.30,
    'Rosehill': 0.24,
    'Eagle Farm': 0.20,
    'Doomben': 0.26,
    'Canterbury': 0.28,
    'Warwick Farm': 0.23,
    'Sandown': 0.21,
    'Morphettville': 0.22,
    'Ascot': 0.24,
    'Gold Coast': 0.25,
}


def _distance_band(distance_m: int) -> str:
    """Map distance in meters to a band category."""
    if distance_m <= 1100:
        return 'sprint'
    elif distance_m <= 1400:
        return 'short'
    elif distance_m <= 1800:
        return 'medium'
    else:
        return 'staying'


class BarrierAnalyzer:
    """
    Data-driven barrier analysis replacing hardcoded penalties/bonuses.
    Queries training_data for historical barrier win rates by track and distance.
    """

    def __init__(self):
        self._barrier_stats = {}
        self._leader_rates = {}
        self._loaded = False

    def _load_data(self):
        """Load barrier statistics from database."""
        if self._loaded:
            return

        conn = _get_db_connection()
        if not conn:
            self._loaded = True
            return

        try:
            self._load_barrier_stats(conn)
            self._load_leader_rates(conn)
        except Exception as e:
            logger.error(f"Error loading barrier data: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

        self._loaded = True

    def _load_barrier_stats(self, conn):
        """Load barrier win rates from training_data."""
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT track, distance, barrier,
                       COUNT(*) as runs,
                       SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) as wins,
                       COUNT(DISTINCT race_date || '-' || CAST(race_number AS TEXT)) as race_count
                FROM training_data
                WHERE barrier IS NOT NULL
                  AND barrier > 0
                  AND barrier <= 24
                  AND actual_position IS NOT NULL
                GROUP BY track, distance, barrier
                ORDER BY track, distance, barrier
            """)

            raw = defaultdict(lambda: defaultdict(lambda: defaultdict(
                lambda: {'wins': 0, 'runs': 0, 'races': 0}
            )))

            for track, distance, barrier, runs, wins, races in cursor.fetchall():
                dist_m = _parse_distance_meters(distance)
                band = _distance_band(dist_m)
                raw[track][band][barrier]['wins'] += (wins or 0)
                raw[track][band][barrier]['runs'] += (runs or 0)
                raw[track][band][barrier]['races'] += (races or 0)

            cursor.close()

            for track, bands in raw.items():
                self._barrier_stats[track] = {}
                for band, barriers in bands.items():
                    total_runs = sum(b['runs'] for b in barriers.values())
                    total_wins = sum(b['wins'] for b in barriers.values())

                    if total_runs < 50:
                        continue

                    expected_rate = total_wins / total_runs if total_runs > 0 else 0.10
                    self._barrier_stats[track][band] = {}

                    for barrier, data in barriers.items():
                        if data['runs'] < 5:
                            continue
                        actual_rate = data['wins'] / data['runs'] if data['runs'] > 0 else 0
                        self._barrier_stats[track][band][barrier] = {
                            'wins': data['wins'],
                            'runs': data['runs'],
                            'win_rate': actual_rate,
                            'expected_rate': expected_rate,
                        }

            logger.info(f"Loaded barrier stats for {len(self._barrier_stats)} tracks")

        except Exception as e:
            logger.error(f"Error loading barrier stats: {e}")

    def _load_leader_rates(self, conn):
        """Load track-specific leader rates (% of winners from barrier 1-3)."""
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT track,
                       COUNT(*) as total_wins,
                       SUM(CASE WHEN barrier <= 3 THEN 1 ELSE 0 END) as inside_wins
                FROM training_data
                WHERE won = 1
                  AND barrier IS NOT NULL
                  AND barrier > 0
                GROUP BY track
                HAVING COUNT(*) >= 20
            """)

            for track, total_wins, inside_wins in cursor.fetchall():
                self._leader_rates[track] = (inside_wins or 0) / total_wins if total_wins > 0 else 0.25

            cursor.close()
            logger.info(f"Loaded leader rates for {len(self._leader_rates)} tracks")

        except Exception as e:
            logger.error(f"Error loading leader rates: {e}")

    def get_barrier_score(self, barrier: int, field_size: int, distance: int, track: str) -> Dict[str, Any]:
        """Get data-driven barrier score for a specific position."""
        self._load_data()

        band = _distance_band(distance)
        has_data = False
        win_rate = 0.0
        expected_rate = 0.0

        track_data = self._barrier_stats.get(track, {})
        if not track_data:
            for cached_track in self._barrier_stats:
                if track.lower() in cached_track.lower() or cached_track.lower() in track.lower():
                    track_data = self._barrier_stats[cached_track]
                    break

        band_data = track_data.get(band, {})
        if not band_data:
            for alt_band in ['medium', 'short', 'sprint', 'staying']:
                if alt_band in track_data:
                    band_data = track_data[alt_band]
                    break

        if barrier in band_data:
            stats = band_data[barrier]
            win_rate = stats['win_rate']
            expected_rate = stats['expected_rate']
            has_data = True

        if not has_data:
            profile = DEFAULT_BARRIER_PROFILES.get(band, DEFAULT_BARRIER_PROFILES['medium'])
            capped_barrier = min(barrier, max(profile.keys()))
            default_adj = profile.get(capped_barrier, 1.0)

            field_factor = 1.0
            if field_size > 0:
                relative_pos = barrier / field_size
                if relative_pos > 0.75:
                    field_factor = 0.92
                elif relative_pos > 0.5:
                    field_factor = 0.97
                elif relative_pos < 0.25:
                    field_factor = 1.03

            adjustment = default_adj * field_factor
            base_win_rate = 1.0 / max(field_size, 1)
            win_rate = base_win_rate * adjustment
            expected_rate = base_win_rate

            raw_score = (adjustment - 0.70) / (1.15 - 0.70)
            barrier_score = max(0, min(100, raw_score * 100))

            return {
                'barrier_score': round(barrier_score, 1),
                'barrier_win_rate': round(win_rate, 4),
                'is_advantageous': adjustment > 1.02,
                'barrier_adjustment': round(adjustment, 4),
                'data_driven': False,
                'track': track,
                'distance_band': band,
                'barrier': barrier,
                'field_size': field_size,
            }

        if expected_rate > 0:
            bias = win_rate / expected_rate
        else:
            bias = 1.0

        field_factor = 1.0
        if field_size > 0:
            relative_pos = barrier / field_size
            if relative_pos > 0.75:
                field_factor = 0.95
            elif relative_pos < 0.25:
                field_factor = 1.02

        adjustment = max(0.5, min(1.8, bias * field_factor))

        raw_score = (adjustment - 0.5) / (1.8 - 0.5)
        barrier_score = max(0, min(100, raw_score * 100))

        return {
            'barrier_score': round(barrier_score, 1),
            'barrier_win_rate': round(win_rate, 4),
            'is_advantageous': adjustment > 1.05,
            'barrier_adjustment': round(adjustment, 4),
            'data_driven': True,
            'track': track,
            'distance_band': band,
            'barrier': barrier,
            'field_size': field_size,
            'sample_size': band_data[barrier]['runs'] if barrier in band_data else 0,
        }

    def get_track_leader_rate(self, track: str) -> float:
        """Get the percentage of winners that led or came from inside barriers at a given track."""
        self._load_data()

        if track in self._leader_rates:
            return round(self._leader_rates[track], 4)

        for cached_track in self._leader_rates:
            if track.lower() in cached_track.lower() or cached_track.lower() in track.lower():
                return round(self._leader_rates[cached_track], 4)

        if track in DEFAULT_LEADER_RATES:
            return DEFAULT_LEADER_RATES[track]

        return 0.22


_barrier_analyzer_instance = None


def get_barrier_analyzer() -> BarrierAnalyzer:
    """Get singleton BarrierAnalyzer instance."""
    global _barrier_analyzer_instance
    if _barrier_analyzer_instance is None:
        _barrier_analyzer_instance = BarrierAnalyzer()
    return _barrier_analyzer_instance


if __name__ == '__main__':
    print("=" * 60)
    print("Feature Weight Optimizer & Barrier Analyzer")
    print("=" * 60)

    print("\n--- Weight Optimizer ---")
    optimizer = FeatureWeightOptimizer()
    weights = optimizer.optimize()
    print(f"\nOptimized weights:")
    for k, v in weights.items():
        default = DEFAULT_WEIGHTS.get(k, 'N/A')
        print(f"  {k}: {v:.4f} (default: {default})")

    print("\n--- Barrier Analyzer ---")
    analyzer = BarrierAnalyzer()
    test_cases = [
        (1, 12, 1200, 'Randwick'),
        (5, 14, 1600, 'Flemington'),
        (10, 16, 2000, 'Caulfield'),
        (14, 16, 1100, 'Moonee Valley'),
    ]
    for barrier, field_size, distance, track in test_cases:
        result = analyzer.get_barrier_score(barrier, field_size, distance, track)
        print(f"\n  {track} B{barrier} ({distance}m, {field_size} runners):")
        print(f"    Score: {result['barrier_score']}/100")
        print(f"    Win Rate: {result['barrier_win_rate']:.4f}")
        print(f"    Advantageous: {result['is_advantageous']}")
        print(f"    Adjustment: {result['barrier_adjustment']:.4f}")
        print(f"    Data-driven: {result.get('data_driven', False)}")

    for track in ['Randwick', 'Flemington', 'Moonee Valley']:
        rate = analyzer.get_track_leader_rate(track)
        print(f"\n  {track} leader rate: {rate:.2%}")

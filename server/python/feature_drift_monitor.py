"""
Tracks feature importance changes over time and alerts on significant shifts.
Helps catch model degradation by monitoring how feature contributions evolve.
"""
import os
import json
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
from scipy import stats as scipy_stats


BARRIER_FEATURES = ['barrier_draw', 'barrier_advantage', 'empirical_barrier_advantage', 'barrier_bias']
MARKET_FEATURES = ['market_odds', 'steam_drift_pct', 'is_sharp_money', 'odds_movement_pct']
TEMPORAL_FEATURES = ['days_since_run', 'freshness_score', 'is_first_up', 'is_second_up']

BARRIER_THRESHOLD = 0.35
MARKET_THRESHOLD = 0.40
TEMPORAL_THRESHOLD = 0.25
CONCENTRATION_INCREASE_THRESHOLD = 0.05


def compute_js_divergence(p: Dict[str, float], q: Dict[str, float]) -> float:
    """
    Jensen-Shannon divergence between two probability distributions.
    JSD = (KL(P||M) + KL(Q||M)) / 2 where M = (P+Q)/2
    Returns value between 0 (identical) and 1 (completely different).
    """
    try:
        all_features = sorted(set(list(p.keys()) + list(q.keys())))
        if not all_features:
            return 0.0

        epsilon = 1e-10
        p_vec = np.array([p.get(f, 0.0) for f in all_features], dtype=np.float64)
        q_vec = np.array([q.get(f, 0.0) for f in all_features], dtype=np.float64)

        p_sum = p_vec.sum()
        q_sum = q_vec.sum()
        if p_sum > 0:
            p_vec = p_vec / p_sum
        if q_sum > 0:
            q_vec = q_vec / q_sum

        p_vec = p_vec + epsilon
        q_vec = q_vec + epsilon
        p_vec = p_vec / p_vec.sum()
        q_vec = q_vec / q_vec.sum()

        m_vec = (p_vec + q_vec) / 2.0

        kl_pm = np.sum(p_vec * np.log2(p_vec / m_vec))
        kl_qm = np.sum(q_vec * np.log2(q_vec / m_vec))

        jsd = (kl_pm + kl_qm) / 2.0
        return float(np.clip(jsd, 0.0, 1.0))
    except Exception as e:
        print(f"Error computing JS divergence: {e}")
        return 0.0


def compute_feature_concentration(importances: Dict[str, float]) -> float:
    """
    Herfindahl-Hirschman Index of feature importance.
    Returns 0-1 where 0=evenly spread, 1=single feature dominates.
    """
    try:
        if not importances:
            return 0.0

        values = np.array(list(importances.values()), dtype=np.float64)
        total = values.sum()
        if total <= 0:
            return 0.0

        shares = values / total
        hhi_raw = float(np.sum(shares ** 2))

        n = len(shares)
        if n <= 1:
            return 1.0

        min_hhi = 1.0 / n
        normalized = (hhi_raw - min_hhi) / (1.0 - min_hhi)
        return float(np.clip(normalized, 0.0, 1.0))
    except Exception as e:
        print(f"Error computing feature concentration: {e}")
        return 0.0


def detect_concerning_patterns(
    current_importances: Dict[str, float],
    historical_importances: List[Dict[str, float]]
) -> List[str]:
    """Check for specific red flags in feature importance patterns."""
    try:
        patterns = []
        if not current_importances:
            return patterns

        total = sum(current_importances.values()) or 1.0

        barrier_share = sum(current_importances.get(f, 0.0) for f in BARRIER_FEATURES) / total
        if barrier_share > BARRIER_THRESHOLD:
            patterns.append(
                "Barrier features dominating — possible overfitting to track preparation patterns"
            )

        market_share = sum(current_importances.get(f, 0.0) for f in MARKET_FEATURES) / total
        if market_share > MARKET_THRESHOLD:
            patterns.append(
                "Market features dominating — model may be echoing market (no independent edge)"
            )

        temporal_share = sum(current_importances.get(f, 0.0) for f in TEMPORAL_FEATURES) / total
        if temporal_share > TEMPORAL_THRESHOLD:
            patterns.append(
                "Temporal/freshness features spiking — possible data quality issue"
            )

        if historical_importances:
            prev = historical_importances[-1]
            current_top = max(current_importances, key=current_importances.get) if current_importances else None
            prev_top = max(prev, key=prev.get) if prev else None
            if current_top and prev_top and current_top != prev_top:
                patterns.append(
                    "Top feature changed — model behavior shifting fundamentally"
                )

            current_concentration = compute_feature_concentration(current_importances)
            prev_concentration = compute_feature_concentration(prev)
            if current_concentration - prev_concentration > CONCENTRATION_INCREASE_THRESHOLD:
                patterns.append(
                    "Feature concentration increasing — model becoming too dependent on few features"
                )

        return patterns
    except Exception as e:
        print(f"Error detecting concerning patterns: {e}")
        return []


class FeatureDriftMonitor:
    """Tracks feature importance changes over time and alerts on significant shifts."""

    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or os.path.join(
            os.path.dirname(__file__), 'models', 'drift_history.json'
        )
        self.history: List[Dict] = []
        self.load()

    def record_snapshot(
        self,
        feature_importances: Dict[str, float],
        metadata: Optional[Dict] = None
    ):
        """
        Record a feature importance snapshot with timestamp.
        feature_importances: {feature_name: importance_score} (should sum to ~1.0)
        metadata: optional dict with model version, training data size, etc.
        """
        try:
            total = sum(feature_importances.values()) or 1.0
            normalized = {k: v / total for k, v in feature_importances.items()}

            snapshot = {
                'timestamp': datetime.now().isoformat(),
                'importance_vector': normalized,
                'metadata': metadata or {},
                'concentration': compute_feature_concentration(normalized),
            }
            self.history.append(snapshot)
            self.save()
        except Exception as e:
            print(f"Error recording snapshot: {e}")

    def compute_drift(
        self,
        current: Optional[Dict[str, float]] = None,
        lookback: int = 1
    ) -> Dict:
        """Compare current (or latest) snapshot against previous one(s)."""
        try:
            if current is not None:
                current_vec = current
            elif self.history:
                current_vec = self.history[-1]['importance_vector']
            else:
                return self._empty_drift_result()

            compare_idx = max(0, len(self.history) - 1 - lookback)
            if len(self.history) < 2 and current is None:
                return self._empty_drift_result()

            if current is not None and self.history:
                previous_vec = self.history[-1]['importance_vector']
            elif len(self.history) >= 2:
                previous_vec = self.history[compare_idx]['importance_vector']
            else:
                return self._empty_drift_result()

            js_div = compute_js_divergence(current_vec, previous_vec)

            all_features = sorted(set(list(current_vec.keys()) + list(previous_vec.keys())))
            current_ranks = self._rank_features(current_vec, all_features)
            previous_ranks = self._rank_features(previous_vec, all_features)

            if len(all_features) >= 2:
                tau, _ = scipy_stats.kendalltau(current_ranks, previous_ranks)
                kendall_tau = float(tau) if not np.isnan(tau) else 1.0
            else:
                kendall_tau = 1.0

            per_feature_zscore = self._compute_zscores(current_vec)

            if js_div < 0.05:
                alert_level = 'green'
            elif js_div < 0.15:
                alert_level = 'amber'
            else:
                alert_level = 'red'

            changes = {}
            for f in all_features:
                changes[f] = current_vec.get(f, 0.0) - previous_vec.get(f, 0.0)

            sorted_changes = sorted(changes.items(), key=lambda x: x[1], reverse=True)
            top_gainers = [
                {'feature': f, 'change': round(c, 4), 'current': round(current_vec.get(f, 0.0), 4)}
                for f, c in sorted_changes[:5] if c > 0
            ]
            top_losers = [
                {'feature': f, 'change': round(c, 4), 'current': round(current_vec.get(f, 0.0), 4)}
                for f, c in sorted_changes[-5:] if c < 0
            ]
            top_losers.reverse()

            hist_vectors = [s['importance_vector'] for s in self.history]
            concerning = detect_concerning_patterns(current_vec, hist_vectors)

            return {
                'js_divergence': round(js_div, 6),
                'kendall_tau': round(kendall_tau, 4),
                'per_feature_zscore': per_feature_zscore,
                'alert_level': alert_level,
                'top_gainers': top_gainers,
                'top_losers': top_losers,
                'concerning_patterns': concerning,
            }
        except Exception as e:
            print(f"Error computing drift: {e}")
            return self._empty_drift_result()

    def get_trend(self, feature_name: str, periods: int = 6) -> Dict:
        """Get importance trend for a specific feature over last N snapshots."""
        try:
            if not self.history:
                return {'values': [], 'timestamps': [], 'trend_direction': 'stable'}

            recent = self.history[-periods:]
            values = [s['importance_vector'].get(feature_name, 0.0) for s in recent]
            timestamps = [s['timestamp'] for s in recent]

            if len(values) >= 2:
                x = np.arange(len(values))
                slope, _, _, _, _ = scipy_stats.linregress(x, values)
                if slope > 0.005:
                    trend_direction = 'increasing'
                elif slope < -0.005:
                    trend_direction = 'decreasing'
                else:
                    trend_direction = 'stable'
            else:
                trend_direction = 'stable'

            return {
                'values': [round(v, 4) for v in values],
                'timestamps': timestamps,
                'trend_direction': trend_direction,
            }
        except Exception as e:
            print(f"Error getting trend: {e}")
            return {'values': [], 'timestamps': [], 'trend_direction': 'stable'}

    def get_full_report(self) -> Dict:
        """Get comprehensive drift report with all metrics."""
        try:
            report = {
                'snapshot_count': len(self.history),
                'latest_timestamp': self.history[-1]['timestamp'] if self.history else None,
                'drift': {},
                'concentration_history': [],
                'feature_trends': {},
                'alerts': [],
            }

            if len(self.history) >= 2:
                report['drift'] = self.compute_drift()

            report['concentration_history'] = [
                {'timestamp': s['timestamp'], 'concentration': round(s.get('concentration', 0.0), 4)}
                for s in self.history
            ]

            if self.history:
                latest = self.history[-1]['importance_vector']
                top_features = sorted(latest.items(), key=lambda x: x[1], reverse=True)[:10]
                for feat, _ in top_features:
                    report['feature_trends'][feat] = self.get_trend(feat)

            if report['drift']:
                if report['drift'].get('alert_level') == 'red':
                    report['alerts'].append('HIGH DRIFT DETECTED — model may need retraining')
                elif report['drift'].get('alert_level') == 'amber':
                    report['alerts'].append('Moderate drift detected — monitor closely')
                report['alerts'].extend(report['drift'].get('concerning_patterns', []))

            return report
        except Exception as e:
            print(f"Error generating full report: {e}")
            return {'error': str(e)}

    def save(self):
        """Save history to disk."""
        try:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            with open(self.storage_path, 'w') as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"Error saving drift history: {e}")

    def load(self):
        """Load history from disk."""
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, 'r') as f:
                    self.history = json.load(f)
            else:
                self.history = []
        except Exception as e:
            print(f"Error loading drift history: {e}")
            self.history = []

    def _rank_features(self, importances: Dict[str, float], all_features: List[str]) -> List[float]:
        """Get rank positions for features (higher importance = lower rank number)."""
        try:
            values = [importances.get(f, 0.0) for f in all_features]
            ranks = scipy_stats.rankdata([-v for v in values])
            return list(ranks)
        except Exception:
            return list(range(1, len(all_features) + 1))

    def _compute_zscores(self, current_vec: Dict[str, float]) -> Dict[str, float]:
        """Compute z-scores for each feature vs historical mean."""
        try:
            if len(self.history) < 3:
                return {}

            all_features = list(current_vec.keys())
            zscores = {}
            for feat in all_features:
                hist_values = [s['importance_vector'].get(feat, 0.0) for s in self.history[:-1]]
                if len(hist_values) < 2:
                    continue
                mean = np.mean(hist_values)
                std = np.std(hist_values)
                if std > 1e-10:
                    zscores[feat] = round(float((current_vec[feat] - mean) / std), 4)
            return zscores
        except Exception:
            return {}

    def _empty_drift_result(self) -> Dict:
        return {
            'js_divergence': 0.0,
            'kendall_tau': 1.0,
            'per_feature_zscore': {},
            'alert_level': 'green',
            'top_gainers': [],
            'top_losers': [],
            'concerning_patterns': [],
        }


if __name__ == '__main__':
    import tempfile

    print("=" * 60)
    print("Feature Drift Monitor — Test Run")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tmp:
        test_path = tmp.name

    monitor = FeatureDriftMonitor(storage_path=test_path)

    snapshot1 = {
        'barrier_draw': 0.08,
        'barrier_advantage': 0.05,
        'empirical_barrier_advantage': 0.04,
        'market_odds': 0.15,
        'weighted_form_score': 0.12,
        'distance_strike_rate': 0.10,
        'course_strike_rate': 0.08,
        'days_since_run': 0.06,
        'freshness_score': 0.04,
        'is_first_up': 0.03,
        'is_second_up': 0.02,
        'jockey_trainer_strike_rate': 0.07,
        'running_style_score': 0.05,
        'pace_advantage': 0.04,
        'class_movement': 0.03,
        'is_elite_jockey': 0.04,
    }
    monitor.record_snapshot(snapshot1, metadata={'version': 'v1.0', 'samples': 5000})
    print("\nSnapshot 1 recorded (baseline)")

    snapshot2 = {
        'barrier_draw': 0.09,
        'barrier_advantage': 0.06,
        'empirical_barrier_advantage': 0.05,
        'market_odds': 0.14,
        'weighted_form_score': 0.11,
        'distance_strike_rate': 0.09,
        'course_strike_rate': 0.08,
        'days_since_run': 0.07,
        'freshness_score': 0.04,
        'is_first_up': 0.03,
        'is_second_up': 0.02,
        'jockey_trainer_strike_rate': 0.06,
        'running_style_score': 0.05,
        'pace_advantage': 0.04,
        'class_movement': 0.03,
        'is_elite_jockey': 0.04,
    }
    monitor.record_snapshot(snapshot2, metadata={'version': 'v1.1', 'samples': 5500})
    print("Snapshot 2 recorded (minor drift)")

    snapshot3 = {
        'barrier_draw': 0.20,
        'barrier_advantage': 0.15,
        'empirical_barrier_advantage': 0.12,
        'market_odds': 0.08,
        'weighted_form_score': 0.06,
        'distance_strike_rate': 0.05,
        'course_strike_rate': 0.04,
        'days_since_run': 0.06,
        'freshness_score': 0.04,
        'is_first_up': 0.03,
        'is_second_up': 0.02,
        'jockey_trainer_strike_rate': 0.04,
        'running_style_score': 0.04,
        'pace_advantage': 0.03,
        'class_movement': 0.02,
        'is_elite_jockey': 0.02,
    }
    monitor.record_snapshot(snapshot3, metadata={'version': 'v1.2', 'samples': 6000})
    print("Snapshot 3 recorded (significant barrier drift)")

    print("\n" + "-" * 40)
    print("Drift Analysis (Snapshot 3 vs Snapshot 2):")
    print("-" * 40)
    drift = monitor.compute_drift()
    print(f"  JS Divergence:  {drift['js_divergence']}")
    print(f"  Kendall Tau:    {drift['kendall_tau']}")
    print(f"  Alert Level:    {drift['alert_level']}")
    print(f"  Top Gainers:    {drift['top_gainers'][:3]}")
    print(f"  Top Losers:     {drift['top_losers'][:3]}")
    if drift['concerning_patterns']:
        print(f"  Concerns:")
        for p in drift['concerning_patterns']:
            print(f"    - {p}")

    print("\n" + "-" * 40)
    print("Feature Trend (barrier_draw):")
    print("-" * 40)
    trend = monitor.get_trend('barrier_draw')
    print(f"  Values:    {trend['values']}")
    print(f"  Direction: {trend['trend_direction']}")

    print("\n" + "-" * 40)
    print("Concentration:")
    print("-" * 40)
    print(f"  Snapshot 1: {compute_feature_concentration(snapshot1):.4f}")
    print(f"  Snapshot 3: {compute_feature_concentration(snapshot3):.4f}")

    print("\n" + "-" * 40)
    print("Full Report:")
    print("-" * 40)
    report = monitor.get_full_report()
    print(f"  Snapshots:    {report['snapshot_count']}")
    print(f"  Alert Level:  {report['drift'].get('alert_level', 'n/a')}")
    if report['alerts']:
        print(f"  Alerts:")
        for a in report['alerts']:
            print(f"    - {a}")

    os.unlink(test_path)
    print("\n" + "=" * 60)
    print("Test complete.")

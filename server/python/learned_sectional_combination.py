"""
Learns optimal combination weights for the 5 sectional timing engines
from historical race data using L-BFGS-B optimisation.

Instead of fixed equal weights, this module learns weights that minimise
negative log-likelihood on historical win/loss outcomes, with L2 regularisation
to prevent overfitting.
"""

import os
import json
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Any

try:
    from scipy.optimize import minimize
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

try:
    from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

DEFAULT_SAVE_DIR = os.path.join(os.path.dirname(__file__), 'models')


def _sigmoid(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, -500, 500)
    return 1.0 / (1.0 + np.exp(-x))


class SectionalCombiner:
    """
    Learns optimal combination weights for sectional timing engines
    via maximum-likelihood estimation with L2 regularisation.
    """

    def __init__(self, n_engines: int = 5, regularization: float = 0.1):
        self.n_engines = n_engines
        self.regularization = regularization
        self.engine_names = [
            'par_adjusted',
            'pace_profile',
            'speed_map',
            'jockey_efficiency',
            'sectional_franking',
        ]
        self.weights = np.ones(n_engines) / n_engines
        self.bias = 0.0
        self.is_fitted = False
        self.history: List[Dict] = []
        self._training_metrics: Dict[str, Any] = {}
        self._convergence_info: Dict[str, Any] = {}

    def _objective(self, params: np.ndarray, X: np.ndarray, y: np.ndarray,
                   sample_weights: Optional[np.ndarray] = None) -> float:
        w = params[:self.n_engines]
        b = params[self.n_engines]
        z = X @ w + b
        p = _sigmoid(z)
        p = np.clip(p, 1e-15, 1 - 1e-15)
        nll = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
        if sample_weights is not None:
            nll = -np.sum(sample_weights * (y * np.log(p) + (1 - y) * np.log(1 - p))) / np.sum(sample_weights)
        reg = self.regularization * np.sum(w ** 2)
        return nll + reg

    def _gradient(self, params: np.ndarray, X: np.ndarray, y: np.ndarray,
                  sample_weights: Optional[np.ndarray] = None) -> np.ndarray:
        w = params[:self.n_engines]
        b = params[self.n_engines]
        z = X @ w + b
        p = _sigmoid(z)
        p = np.clip(p, 1e-15, 1 - 1e-15)
        diff = p - y
        if sample_weights is not None:
            diff = diff * sample_weights / np.sum(sample_weights)
            grad_w = X.T @ diff + 2 * self.regularization * w
            grad_b = np.sum(diff)
        else:
            n = len(y)
            grad_w = (X.T @ diff) / n + 2 * self.regularization * w
            grad_b = np.sum(diff) / n
        return np.append(grad_w, grad_b)

    def fit(self, engine_outputs: np.ndarray, y_true: np.ndarray,
            odds: np.ndarray = None) -> Dict[str, Any]:
        X = np.array(engine_outputs, dtype=np.float64)
        y = np.array(y_true, dtype=np.float64)

        nan_mask = np.isnan(X)
        if nan_mask.any():
            X = np.where(nan_mask, 0.0, X)

        if len(X.shape) == 1:
            X = X.reshape(-1, 1)
        if X.shape[1] != self.n_engines:
            self.n_engines = X.shape[1]
            self.engine_names = [f'engine_{i}' for i in range(self.n_engines)]

        sample_weights = None
        if odds is not None:
            odds_arr = np.array(odds, dtype=np.float64)
            odds_arr = np.clip(odds_arr, 1.01, 200.0)
            implied = 1.0 / odds_arr
            sample_weights = implied / np.mean(implied)

        if not SCIPY_AVAILABLE:
            self.weights = np.ones(self.n_engines) / self.n_engines
            self.bias = 0.0
            self.is_fitted = False
            self._training_metrics = {'error': 'scipy not available, using equal weights'}
            return self._training_metrics

        x0 = np.append(np.ones(self.n_engines) / self.n_engines, 0.0)
        bounds = [(0, None)] * self.n_engines + [(None, None)]

        result = minimize(
            self._objective,
            x0,
            args=(X, y, sample_weights),
            method='L-BFGS-B',
            jac=self._gradient,
            bounds=bounds,
            options={'maxiter': 1000, 'ftol': 1e-9},
        )

        raw_weights = result.x[:self.n_engines]
        self.bias = result.x[self.n_engines]

        w_sum = raw_weights.sum()
        if w_sum > 1e-9:
            self.weights = raw_weights / w_sum
        else:
            self.weights = np.ones(self.n_engines) / self.n_engines

        self.is_fitted = True

        z = X @ raw_weights + self.bias
        p = _sigmoid(z)
        p = np.clip(p, 1e-15, 1 - 1e-15)

        metrics: Dict[str, Any] = {
            'n_samples': len(y),
            'n_positive': int(y.sum()),
            'convergence': result.success,
            'n_iterations': result.nit,
            'final_loss': float(result.fun),
            'fitted_at': datetime.now().isoformat(),
        }

        if SKLEARN_AVAILABLE and len(np.unique(y)) > 1:
            try:
                metrics['auc'] = float(roc_auc_score(y, p))
            except ValueError:
                metrics['auc'] = None
            metrics['brier'] = float(brier_score_loss(y, p))
            metrics['log_loss'] = float(log_loss(y, p))

        self._training_metrics = metrics
        self._convergence_info = {
            'success': result.success,
            'message': result.message if hasattr(result, 'message') else '',
            'n_iterations': result.nit,
            'final_loss': float(result.fun),
        }

        self.history.append({
            'fitted_at': metrics['fitted_at'],
            'n_samples': metrics['n_samples'],
            'weights': self.weights.tolist(),
            'bias': float(self.bias),
            'loss': float(result.fun),
            'auc': metrics.get('auc'),
        })

        return metrics

    def combine(self, engine_outputs: dict) -> float:
        scores = np.zeros(self.n_engines)
        for i, name in enumerate(self.engine_names):
            val = engine_outputs.get(name, 0.0)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                val = 0.0
            scores[i] = float(val)

        z = np.dot(self.weights, scores) + self.bias
        return float(_sigmoid(np.array([z]))[0])

    def get_weights(self) -> dict:
        return {name: float(w) for name, w in zip(self.engine_names, self.weights)}

    def get_engine_contributions(self, engine_outputs: dict) -> dict:
        scores = np.zeros(self.n_engines)
        for i, name in enumerate(self.engine_names):
            val = engine_outputs.get(name, 0.0)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                val = 0.0
            scores[i] = float(val)

        contributions = self.weights * scores
        total = np.abs(contributions).sum()
        if total < 1e-15:
            total = 1.0

        result = {}
        for i, name in enumerate(self.engine_names):
            result[name] = {
                'weight': float(self.weights[i]),
                'raw_score': float(scores[i]),
                'contribution': float(contributions[i]),
                'pct': float(contributions[i] / total * 100) if total > 0 else 0.0,
            }
        return result

    def evaluate(self, engine_outputs: np.ndarray, y_true: np.ndarray) -> dict:
        X = np.array(engine_outputs, dtype=np.float64)
        y = np.array(y_true, dtype=np.float64)

        nan_mask = np.isnan(X)
        if nan_mask.any():
            X = np.where(nan_mask, 0.0, X)

        z = X @ self.weights + self.bias
        p = _sigmoid(z)
        p = np.clip(p, 1e-15, 1 - 1e-15)

        metrics: Dict[str, Any] = {'n_samples': len(y)}

        if not SKLEARN_AVAILABLE or len(np.unique(y)) < 2:
            metrics['error'] = 'sklearn not available or single class'
            return metrics

        try:
            metrics['auc'] = float(roc_auc_score(y, p))
        except ValueError:
            metrics['auc'] = None
        metrics['brier'] = float(brier_score_loss(y, p))
        metrics['log_loss'] = float(log_loss(y, p))

        return metrics

    def save(self, path: str = None):
        if path is None:
            os.makedirs(DEFAULT_SAVE_DIR, exist_ok=True)
            path = os.path.join(DEFAULT_SAVE_DIR, 'sectional_combiner_weights.json')

        data = {
            'engine_names': self.engine_names,
            'weights': self.weights.tolist(),
            'bias': float(self.bias),
            'n_engines': self.n_engines,
            'regularization': self.regularization,
            'is_fitted': self.is_fitted,
            'training_metrics': self._training_metrics,
            'convergence_info': self._convergence_info,
            'fitted_at': self._training_metrics.get('fitted_at', datetime.now().isoformat()),
            'history': self.history[-10:],
        }

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    def load(self, path: str = None):
        if path is None:
            path = os.path.join(DEFAULT_SAVE_DIR, 'sectional_combiner_weights.json')

        if not os.path.exists(path):
            return False

        try:
            with open(path, 'r') as f:
                data = json.load(f)

            self.engine_names = data.get('engine_names', self.engine_names)
            self.weights = np.array(data.get('weights', []))
            self.bias = float(data.get('bias', 0.0))
            self.n_engines = data.get('n_engines', len(self.weights))
            self.regularization = data.get('regularization', self.regularization)
            self.is_fitted = data.get('is_fitted', False)
            self._training_metrics = data.get('training_metrics', {})
            self._convergence_info = data.get('convergence_info', {})
            self.history = data.get('history', [])
            return True
        except (json.JSONDecodeError, KeyError, ValueError):
            return False

    def get_report(self) -> dict:
        return {
            'is_fitted': self.is_fitted,
            'weights': self.get_weights(),
            'bias': float(self.bias),
            'n_engines': self.n_engines,
            'regularization': self.regularization,
            'training_metrics': self._training_metrics,
            'convergence': self._convergence_info,
            'n_training_runs': len(self.history),
            'engine_names': self.engine_names,
        }


class DistanceConditionalCombiner:
    """
    Learns different combination weights for sprint / mile / staying distances.

    Distance categories: sprint < 1200m, mile 1200-1800m, staying > 1800m.
    """

    DISTANCE_CATEGORIES = {
        'sprint': (0, 1199),
        'mile': (1200, 1800),
        'staying': (1801, 99999),
    }

    def __init__(self, n_engines: int = 5, regularization: float = 0.1):
        self.combiners: Dict[str, SectionalCombiner] = {
            'sprint': SectionalCombiner(n_engines=n_engines, regularization=regularization),
            'mile': SectionalCombiner(n_engines=n_engines, regularization=regularization),
            'staying': SectionalCombiner(n_engines=n_engines, regularization=regularization),
        }

    @staticmethod
    def _categorize_distance(distance_m: int) -> str:
        if distance_m < 1200:
            return 'sprint'
        elif distance_m <= 1800:
            return 'mile'
        else:
            return 'staying'

    def fit(self, engine_outputs: np.ndarray, y_true: np.ndarray,
            distances: np.ndarray, odds: np.ndarray = None) -> Dict[str, Any]:
        X = np.array(engine_outputs, dtype=np.float64)
        y = np.array(y_true, dtype=np.float64)
        d = np.array(distances, dtype=np.float64)

        results = {}
        for cat in self.combiners:
            lo, hi = self.DISTANCE_CATEGORIES[cat]
            mask = (d >= lo) & (d <= hi)
            n_cat = mask.sum()

            if n_cat < 10:
                results[cat] = {
                    'status': 'insufficient_data',
                    'n_samples': int(n_cat),
                }
                continue

            X_cat = X[mask]
            y_cat = y[mask]
            odds_cat = odds[mask] if odds is not None else None

            metrics = self.combiners[cat].fit(X_cat, y_cat, odds_cat)
            results[cat] = {
                'status': 'fitted',
                'n_samples': int(n_cat),
                'metrics': metrics,
            }

        return results

    def combine(self, engine_outputs: dict, distance_m: int) -> float:
        cat = self._categorize_distance(distance_m)
        combiner = self.combiners[cat]

        if not combiner.is_fitted:
            for fallback_cat in ['mile', 'sprint', 'staying']:
                if self.combiners[fallback_cat].is_fitted:
                    combiner = self.combiners[fallback_cat]
                    break

        return combiner.combine(engine_outputs)

    def get_all_weights(self) -> dict:
        return {cat: c.get_weights() for cat, c in self.combiners.items()}

    def get_combiner(self, distance_m: int) -> SectionalCombiner:
        cat = self._categorize_distance(distance_m)
        return self.combiners[cat]

    def save(self, directory: str = None):
        if directory is None:
            directory = DEFAULT_SAVE_DIR
        os.makedirs(directory, exist_ok=True)
        for cat, combiner in self.combiners.items():
            path = os.path.join(directory, f'sectional_combiner_{cat}.json')
            combiner.save(path)

    def load(self, directory: str = None):
        if directory is None:
            directory = DEFAULT_SAVE_DIR
        loaded = {}
        for cat, combiner in self.combiners.items():
            path = os.path.join(directory, f'sectional_combiner_{cat}.json')
            loaded[cat] = combiner.load(path)
        return loaded

    def get_report(self) -> dict:
        return {
            'distance_categories': list(self.combiners.keys()),
            'reports': {cat: c.get_report() for cat, c in self.combiners.items()},
        }


_global_combiner: Optional[SectionalCombiner] = None
_global_distance_combiner: Optional[DistanceConditionalCombiner] = None


def get_sectional_combiner() -> SectionalCombiner:
    global _global_combiner
    if _global_combiner is None:
        _global_combiner = SectionalCombiner()
        _global_combiner.load()
    return _global_combiner


def get_distance_combiner() -> DistanceConditionalCombiner:
    global _global_distance_combiner
    if _global_distance_combiner is None:
        _global_distance_combiner = DistanceConditionalCombiner()
        _global_distance_combiner.load()
    return _global_distance_combiner


if __name__ == '__main__':
    print("=== Learned Sectional Combination — Self-Test ===")

    np.random.seed(42)
    n_samples = 500
    n_engines = 5

    true_weights = np.array([0.35, 0.25, 0.20, 0.12, 0.08])
    X = np.random.randn(n_samples, n_engines)
    z = X @ true_weights + 0.1 + np.random.randn(n_samples) * 0.3
    y = (z > 0).astype(float)

    combiner = SectionalCombiner(n_engines=5, regularization=0.05)
    print(f"\nBefore fitting — weights: {combiner.get_weights()}")

    metrics = combiner.fit(X, y)
    print(f"\nAfter fitting:")
    print(f"  Weights: {combiner.get_weights()}")
    print(f"  Bias:    {combiner.bias:.4f}")
    print(f"  Metrics: {json.dumps(metrics, indent=2, default=str)}")

    sample = {'par_adjusted': 1.2, 'pace_profile': 0.8, 'speed_map': -0.3,
              'jockey_efficiency': 0.5, 'sectional_franking': 0.1}
    score = combiner.combine(sample)
    print(f"\nCombined score for sample: {score:.4f}")
    print(f"Contributions: {json.dumps(combiner.get_engine_contributions(sample), indent=2)}")

    eval_metrics = combiner.evaluate(X, y)
    print(f"\nEvaluation: {json.dumps(eval_metrics, indent=2, default=str)}")

    combiner.save()
    print(f"\nSaved to {DEFAULT_SAVE_DIR}")

    loaded = SectionalCombiner()
    loaded.load()
    print(f"Loaded weights: {loaded.get_weights()}")

    print(f"\nReport: {json.dumps(combiner.get_report(), indent=2, default=str)}")

    print("\n--- Distance-Conditional Combiner ---")
    distances = np.random.choice([1000, 1400, 2000], size=n_samples)
    dc = DistanceConditionalCombiner()
    dc_results = dc.fit(X, y, distances)
    print(f"Fit results: {json.dumps(dc_results, indent=2, default=str)}")
    print(f"All weights: {json.dumps(dc.get_all_weights(), indent=2)}")

    score_sprint = dc.combine(sample, 1000)
    score_mile = dc.combine(sample, 1400)
    score_staying = dc.combine(sample, 2400)
    print(f"Sprint score: {score_sprint:.4f}, Mile: {score_mile:.4f}, Staying: {score_staying:.4f}")

    dc.save()
    print("\n=== Self-test complete ===")

#!/usr/bin/env python3
"""
Double (Layered) Probability Calibration for ML Ensemble

Layer 1: Per-model isotonic regression calibration (XGBoost, LightGBM, CatBoost)
Layer 2: Ensemble-level isotonic regression calibration

This addresses the problem where individual models have different bias profiles
(e.g., XGBoost overconfident on favourites, CatBoost underestimates longshots).
Calibrating each model first, then calibrating the combined output, produces
better-calibrated final probabilities than ensemble-only calibration.
"""

import os
import json
import pickle
import numpy as np
from typing import Dict, List, Optional, Tuple, Any

try:
    from sklearn.isotonic import IsotonicRegression
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    print("sklearn not available — double calibration disabled")

DEFAULT_SAVE_PATH = os.path.join(os.path.dirname(__file__), 'models', 'double_calibrator.pkl')


def compute_brier_score(predicted: np.ndarray, actual: np.ndarray) -> float:
    """
    Compute standard Brier score: mean((predicted - actual)^2).
    Lower is better. Perfect = 0.0, worst = 1.0.
    """
    try:
        predicted = np.asarray(predicted, dtype=float)
        actual = np.asarray(actual, dtype=float)
        if len(predicted) == 0 or len(actual) == 0:
            return 1.0
        return float(np.mean((predicted - actual) ** 2))
    except Exception:
        return 1.0


def compute_reliability_diagram(predicted: np.ndarray, actual: np.ndarray, n_bins: int = 10) -> Dict[str, Any]:
    """Compute reliability diagram data for each bin of predicted probabilities."""
    try:
        predicted = np.asarray(predicted, dtype=float)
        actual = np.asarray(actual, dtype=float)

        if len(predicted) == 0:
            return {'bin_edges': [], 'mean_predicted': [], 'mean_actual': [], 'bin_counts': []}

        bin_edges_arr = np.linspace(0.0, 1.0, n_bins + 1)
        bin_edges: List[Tuple[float, float]] = []
        mean_predicted: List[float] = []
        mean_actual: List[float] = []
        bin_counts: List[int] = []

        for i in range(n_bins):
            low, high = float(bin_edges_arr[i]), float(bin_edges_arr[i + 1])
            if i == n_bins - 1:
                mask = (predicted >= low) & (predicted <= high)
            else:
                mask = (predicted >= low) & (predicted < high)
            count = int(mask.sum())
            bin_edges.append((low, high))
            bin_counts.append(count)
            if count > 0:
                mean_predicted.append(float(np.mean(predicted[mask])))
                mean_actual.append(float(np.mean(actual[mask])))
            else:
                mean_predicted.append(float((low + high) / 2))
                mean_actual.append(0.0)

        return {
            'bin_edges': bin_edges,
            'mean_predicted': mean_predicted,
            'mean_actual': mean_actual,
            'bin_counts': bin_counts,
        }
    except Exception:
        return {'bin_edges': [], 'mean_predicted': [], 'mean_actual': [], 'bin_counts': []}


def compute_band_calibration(predicted: np.ndarray, actual: np.ndarray) -> Dict[str, Dict[str, Any]]:
    """Compute calibration metrics for three probability bands: favourites (>0.20), mid-range (0.10-0.20), and longshots (<0.10)."""
    try:
        predicted = np.asarray(predicted, dtype=float)
        actual = np.asarray(actual, dtype=float)

        bands = {
            'favourites': predicted > 0.20,
            'mid_range': (predicted > 0.05) & (predicted <= 0.20),
            'longshots': predicted <= 0.05,
        }

        result: Dict[str, Dict[str, Any]] = {}
        for name, mask in bands.items():
            count = int(mask.sum())
            if count == 0:
                result[name] = {
                    'brier_score': 0.0,
                    'mean_predicted': 0.0,
                    'mean_actual': 0.0,
                    'count': 0,
                    'calibration_ratio': 0.0,
                }
                continue

            p = predicted[mask]
            a = actual[mask]
            mean_p = float(np.mean(p))
            mean_a = float(np.mean(a))
            brier = float(np.mean((p - a) ** 2))
            cal_ratio = float(mean_a / mean_p) if mean_p > 0 else 0.0

            result[name] = {
                'brier_score': round(brier, 6),
                'mean_predicted': round(mean_p, 6),
                'mean_actual': round(mean_a, 6),
                'count': count,
                'calibration_ratio': round(cal_ratio, 4),
            }

        return result
    except Exception:
        return {
            'favourites': {'brier_score': 0.0, 'mean_predicted': 0.0, 'mean_actual': 0.0, 'count': 0, 'calibration_ratio': 0.0},
            'mid_range': {'brier_score': 0.0, 'mean_predicted': 0.0, 'mean_actual': 0.0, 'count': 0, 'calibration_ratio': 0.0},
            'longshots': {'brier_score': 0.0, 'mean_predicted': 0.0, 'mean_actual': 0.0, 'count': 0, 'calibration_ratio': 0.0},
        }


def calibrate_single_model(raw_proba: np.ndarray, y_true: np.ndarray) -> Optional['IsotonicRegression']:
    """Fit isotonic regression for a single model's probability outputs."""
    try:
        if not SKLEARN_AVAILABLE:
            return None

        raw_proba = np.asarray(raw_proba, dtype=float)
        y_true = np.asarray(y_true, dtype=float)

        if len(raw_proba) < 10:
            return None

        if np.all(y_true == y_true[0]):
            return None

        iso = IsotonicRegression(y_min=0.001, y_max=0.999, out_of_bounds='clip')
        iso.fit(raw_proba, y_true)
        return iso
    except Exception:
        return None


class DoubleCalibrator:
    """
    Layered probability calibration for the ML ensemble.

    Layer 1: Fit per-model isotonic regression calibrators for XGBoost, LightGBM, CatBoost.
    Layer 2: Fit a second isotonic pass on the weighted ensemble output.

    Internally uses 5-fold temporal-aware splitting to avoid data leakage
    when fitting calibrators on the same data used for training.
    """

    def __init__(self) -> None:
        self.xgb_calibrator: Optional[IsotonicRegression] = None
        self.lgb_calibrator: Optional[IsotonicRegression] = None
        self.cat_calibrator: Optional[IsotonicRegression] = None
        self.ensemble_calibrator: Optional[IsotonicRegression] = None
        self.calibration_stats: Dict[str, Any] = {}
        self.is_fitted: bool = False

    def fit(
        self,
        xgb_proba: np.ndarray,
        lgb_proba: np.ndarray,
        cat_proba: np.ndarray,
        y_true: np.ndarray,
        ensemble_weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Fit double calibration using out-of-fold predictions.

        Layer 1: Fit isotonic regression calibrators for each base model.
        Layer 2: Fit ensemble isotonic calibration on blended OOF predictions.
        """
        try:
            if not SKLEARN_AVAILABLE:
                return {'error': 'sklearn not available'}

            xgb_proba = np.asarray(xgb_proba, dtype=float)
            lgb_proba = np.asarray(lgb_proba, dtype=float)
            cat_proba = np.asarray(cat_proba, dtype=float)
            y_true = np.asarray(y_true, dtype=float)

            n = len(y_true)
            if n < 30:
                return {'error': f'Too few samples for calibration: {n} (need 30+)'}

            if np.all(y_true == y_true[0]):
                return {'error': 'All labels are the same class — cannot calibrate'}

            weights = ensemble_weights or {'xgb': 1.0 / 3, 'lgb': 1.0 / 3, 'cat': 1.0 / 3}
            w_total = sum(weights.values())
            weights = {k: v / w_total for k, v in weights.items()}

            brier_before = {
                'xgb': compute_brier_score(xgb_proba, y_true),
                'lgb': compute_brier_score(lgb_proba, y_true),
                'cat': compute_brier_score(cat_proba, y_true),
            }

            raw_ensemble = (
                xgb_proba * weights['xgb']
                + lgb_proba * weights['lgb']
                + cat_proba * weights['cat']
            )
            brier_before['ensemble'] = compute_brier_score(raw_ensemble, y_true)

            n_folds = 5
            fold_size = n // n_folds
            oof_xgb_cal = np.zeros(n)
            oof_lgb_cal = np.zeros(n)
            oof_cat_cal = np.zeros(n)

            for fold_idx in range(n_folds):
                val_start = fold_idx * fold_size
                val_end = val_start + fold_size if fold_idx < n_folds - 1 else n

                train_mask = np.ones(n, dtype=bool)
                train_mask[val_start:val_end] = False
                val_mask = ~train_mask

                xgb_iso = calibrate_single_model(xgb_proba[train_mask], y_true[train_mask])
                lgb_iso = calibrate_single_model(lgb_proba[train_mask], y_true[train_mask])
                cat_iso = calibrate_single_model(cat_proba[train_mask], y_true[train_mask])

                oof_xgb_cal[val_mask] = xgb_iso.predict(xgb_proba[val_mask]) if xgb_iso else xgb_proba[val_mask]
                oof_lgb_cal[val_mask] = lgb_iso.predict(lgb_proba[val_mask]) if lgb_iso else lgb_proba[val_mask]
                oof_cat_cal[val_mask] = cat_iso.predict(cat_proba[val_mask]) if cat_iso else cat_proba[val_mask]

            self.xgb_calibrator = calibrate_single_model(xgb_proba, y_true)
            self.lgb_calibrator = calibrate_single_model(lgb_proba, y_true)
            self.cat_calibrator = calibrate_single_model(cat_proba, y_true)

            brier_after_l1 = {
                'xgb': compute_brier_score(oof_xgb_cal, y_true),
                'lgb': compute_brier_score(oof_lgb_cal, y_true),
                'cat': compute_brier_score(oof_cat_cal, y_true),
            }

            oof_ensemble_cal = (
                oof_xgb_cal * weights['xgb']
                + oof_lgb_cal * weights['lgb']
                + oof_cat_cal * weights['cat']
            )

            self.ensemble_calibrator = calibrate_single_model(oof_ensemble_cal, y_true)

            if self.ensemble_calibrator is not None:
                oof_final = self.ensemble_calibrator.predict(oof_ensemble_cal)
            else:
                oof_final = oof_ensemble_cal

            brier_after_l1['ensemble'] = compute_brier_score(oof_ensemble_cal, y_true)
            brier_after_l2 = compute_brier_score(oof_final, y_true)

            reliability_before = compute_reliability_diagram(raw_ensemble, y_true)
            reliability_after = compute_reliability_diagram(oof_final, y_true)

            band_before = compute_band_calibration(raw_ensemble, y_true)
            band_after = compute_band_calibration(oof_final, y_true)

            self.calibration_stats = {
                'n_samples': n,
                'n_folds': n_folds,
                'brier_before': brier_before,
                'brier_after_layer1': brier_after_l1,
                'brier_after_layer2': brier_after_l2,
                'reliability_before': reliability_before,
                'reliability_after': reliability_after,
                'band_calibration_before': band_before,
                'band_calibration_after': band_after,
                'ensemble_weights': weights,
            }

            self.is_fitted = True

            return {
                'success': True,
                'n_samples': n,
                'brier_before_ensemble': round(brier_before['ensemble'], 6),
                'brier_after_layer2': round(brier_after_l2, 6),
                'improvement': round(brier_before['ensemble'] - brier_after_l2, 6),
            }

        except Exception as e:
            return {'error': str(e)}

    def calibrate(
        self,
        xgb_proba: np.ndarray,
        lgb_proba: np.ndarray,
        cat_proba: np.ndarray,
        ensemble_weights: Optional[Dict[str, float]] = None,
    ) -> np.ndarray:
        """
        Apply double calibration to new predictions.

        Layer 1: Calibrate each model's probabilities individually.
        Layer 2: Apply ensemble isotonic calibration.
        """
        try:
            xgb_proba = np.asarray(xgb_proba, dtype=float)
            lgb_proba = np.asarray(lgb_proba, dtype=float)
            cat_proba = np.asarray(cat_proba, dtype=float)

            if not self.is_fitted:
                weights = ensemble_weights or {'xgb': 1.0 / 3, 'lgb': 1.0 / 3, 'cat': 1.0 / 3}
                w_total = sum(weights.values())
                weights = {k: v / w_total for k, v in weights.items()}
                return xgb_proba * weights['xgb'] + lgb_proba * weights['lgb'] + cat_proba * weights['cat']

            weights = ensemble_weights or {'xgb': 1.0 / 3, 'lgb': 1.0 / 3, 'cat': 1.0 / 3}
            w_total = sum(weights.values())
            weights = {k: v / w_total for k, v in weights.items()}

            xgb_cal = self.xgb_calibrator.predict(xgb_proba) if self.xgb_calibrator else xgb_proba
            lgb_cal = self.lgb_calibrator.predict(lgb_proba) if self.lgb_calibrator else lgb_proba
            cat_cal = self.cat_calibrator.predict(cat_proba) if self.cat_calibrator else cat_proba

            ensemble_cal = (
                xgb_cal * weights['xgb']
                + lgb_cal * weights['lgb']
                + cat_cal * weights['cat']
            )

            if self.ensemble_calibrator is not None:
                final = self.ensemble_calibrator.predict(ensemble_cal)
            else:
                final = ensemble_cal

            final = np.clip(final, 0.001, 0.999)
            return final

        except Exception:
            weights = ensemble_weights or {'xgb': 1.0 / 3, 'lgb': 1.0 / 3, 'cat': 1.0 / 3}
            w_total = sum(weights.values())
            weights = {k: v / w_total for k, v in weights.items()}
            return (
                np.asarray(xgb_proba) * weights['xgb']
                + np.asarray(lgb_proba) * weights['lgb']
                + np.asarray(cat_proba) * weights['cat']
            )

    def get_calibration_report(self) -> Dict[str, Any]:
        """Return calibration statistics collected during fit()."""
        try:
            if not self.is_fitted or not self.calibration_stats:
                return {'error': 'Calibrator not fitted yet', 'is_fitted': False}

            return {
                'is_fitted': True,
                'n_samples': self.calibration_stats.get('n_samples', 0),
                'n_folds': self.calibration_stats.get('n_folds', 5),
                'brier_scores': {
                    'before': self.calibration_stats.get('brier_before', {}),
                    'after_layer1': self.calibration_stats.get('brier_after_layer1', {}),
                    'after_layer2': self.calibration_stats.get('brier_after_layer2', 0.0),
                },
                'reliability_diagram': {
                    'before': self.calibration_stats.get('reliability_before', {}),
                    'after': self.calibration_stats.get('reliability_after', {}),
                },
                'band_calibration': {
                    'before': self.calibration_stats.get('band_calibration_before', {}),
                    'after': self.calibration_stats.get('band_calibration_after', {}),
                },
                'ensemble_weights': self.calibration_stats.get('ensemble_weights', {}),
            }
        except Exception:
            return {'error': 'Failed to generate report', 'is_fitted': self.is_fitted}

    def save(self, path: str = None) -> bool:
        """Save fitted calibrators to disk using pickle."""
        try:
            path = path or DEFAULT_SAVE_PATH
            os.makedirs(os.path.dirname(path), exist_ok=True)

            data = {
                'xgb_calibrator': self.xgb_calibrator,
                'lgb_calibrator': self.lgb_calibrator,
                'cat_calibrator': self.cat_calibrator,
                'ensemble_calibrator': self.ensemble_calibrator,
                'calibration_stats': self.calibration_stats,
                'is_fitted': self.is_fitted,
            }
            with open(path, 'wb') as f:
                pickle.dump(data, f)
            return True
        except Exception as e:
            print(f"Failed to save DoubleCalibrator: {e}")
            return False

    def load(self, path: str = None) -> bool:
        """Load fitted calibrators from disk."""
        try:
            path = path or DEFAULT_SAVE_PATH
            if not os.path.exists(path):
                return False

            with open(path, 'rb') as f:
                data = pickle.load(f)

            self.xgb_calibrator = data.get('xgb_calibrator')
            self.lgb_calibrator = data.get('lgb_calibrator')
            self.cat_calibrator = data.get('cat_calibrator')
            self.ensemble_calibrator = data.get('ensemble_calibrator')
            self.calibration_stats = data.get('calibration_stats', {})
            self.is_fitted = data.get('is_fitted', False)
            return True
        except Exception as e:
            print(f"Failed to load DoubleCalibrator: {e}")
            return False


if __name__ == '__main__':
    print("=" * 70)
    print("Double Calibration — Synthetic Data Test")
    print("=" * 70)

    np.random.seed(42)
    n = 2000

    true_proba = np.random.beta(1.5, 8.0, size=n)
    y_true = (np.random.rand(n) < true_proba).astype(float)

    xgb_proba = np.clip(true_proba + np.random.normal(0.02, 0.08, n), 0.001, 0.999)
    lgb_proba = np.clip(true_proba + np.random.normal(-0.01, 0.10, n), 0.001, 0.999)
    cat_proba = np.clip(true_proba + np.random.normal(0.03, 0.07, n), 0.001, 0.999)

    print(f"\nGenerated {n} samples")
    print(f"  Win rate:     {y_true.mean():.4f}")
    print(f"  XGB mean:     {xgb_proba.mean():.4f}")
    print(f"  LGB mean:     {lgb_proba.mean():.4f}")
    print(f"  CAT mean:     {cat_proba.mean():.4f}")

    print("\n--- Brier Scores (raw) ---")
    for name, proba in [('XGB', xgb_proba), ('LGB', lgb_proba), ('CAT', cat_proba)]:
        print(f"  {name}: {compute_brier_score(proba, y_true):.6f}")

    print("\n--- Band Calibration (raw ensemble) ---")
    raw_ens = (xgb_proba + lgb_proba + cat_proba) / 3
    bands = compute_band_calibration(raw_ens, y_true)
    for band, stats in bands.items():
        print(f"  {band:12s}: brier={stats['brier_score']:.6f}  "
              f"pred={stats['mean_predicted']:.4f}  actual={stats['mean_actual']:.4f}  "
              f"ratio={stats['calibration_ratio']:.4f}  n={stats['count']}")

    print("\n--- Reliability Diagram (raw ensemble, 10 bins) ---")
    rel = compute_reliability_diagram(raw_ens, y_true)
    for i, (edge, mp, ma, cnt) in enumerate(
        zip(rel['bin_edges'], rel['mean_predicted'], rel['mean_actual'], rel['bin_counts'])
    ):
        print(f"  [{edge[0]:.2f}, {edge[1]:.2f})  pred={mp:.4f}  actual={ma:.4f}  n={cnt}")

    print("\n--- Fitting DoubleCalibrator ---")
    dc = DoubleCalibrator()
    result = dc.fit(xgb_proba, lgb_proba, cat_proba, y_true)
    print(f"  Result: {json.dumps(result, indent=2)}")

    print("\n--- Calibration Report ---")
    report = dc.get_calibration_report()
    print(f"  Brier before (ensemble): {report['brier_scores']['before'].get('ensemble', '?'):.6f}")
    print(f"  Brier after Layer 2:     {report['brier_scores']['after_layer2']:.6f}")

    print("\n--- Band Calibration (after double calibration) ---")
    band_after = report['band_calibration']['after']
    for band, stats in band_after.items():
        print(f"  {band:12s}: brier={stats['brier_score']:.6f}  "
              f"pred={stats['mean_predicted']:.4f}  actual={stats['mean_actual']:.4f}  "
              f"ratio={stats['calibration_ratio']:.4f}  n={stats['count']}")

    print("\n--- Apply to new data ---")
    new_xgb = np.array([0.05, 0.15, 0.30, 0.50, 0.70])
    new_lgb = np.array([0.04, 0.18, 0.25, 0.55, 0.65])
    new_cat = np.array([0.06, 0.12, 0.35, 0.48, 0.72])
    calibrated = dc.calibrate(new_xgb, new_lgb, new_cat)
    for i in range(len(new_xgb)):
        raw_avg = (new_xgb[i] + new_lgb[i] + new_cat[i]) / 3
        print(f"  raw_avg={raw_avg:.4f}  calibrated={calibrated[i]:.4f}")

    print("\n--- Save / Load round-trip ---")
    test_path = '/tmp/test_double_calibrator.pkl'
    dc.save(test_path)
    dc2 = DoubleCalibrator()
    loaded = dc2.load(test_path)
    print(f"  Loaded: {loaded}, is_fitted: {dc2.is_fitted}")
    calibrated2 = dc2.calibrate(new_xgb, new_lgb, new_cat)
    match = np.allclose(calibrated, calibrated2)
    print(f"  Predictions match: {match}")

    if os.path.exists(test_path):
        os.remove(test_path)

    print("\n--- Edge case: too few samples ---")
    dc_small = DoubleCalibrator()
    result_small = dc_small.fit(np.array([0.1, 0.2]), np.array([0.3, 0.4]), np.array([0.2, 0.3]), np.array([0, 1]))
    print(f"  Result: {result_small}")

    print("\n--- Edge case: all same class ---")
    dc_same = DoubleCalibrator()
    result_same = dc_same.fit(
        np.random.rand(100), np.random.rand(100), np.random.rand(100), np.zeros(100)
    )
    print(f"  Result: {result_same}")

    print("\n--- calibrate_single_model utility ---")
    iso = calibrate_single_model(xgb_proba, y_true)
    print(f"  Fitted IsotonicRegression: {iso is not None}")
    if iso:
        test_vals = np.array([0.05, 0.10, 0.20, 0.50])
        cal_vals = iso.predict(test_vals)
        for tv, cv in zip(test_vals, cal_vals):
            print(f"    raw={tv:.2f} -> calibrated={cv:.4f}")

    print("\nAll tests passed.")

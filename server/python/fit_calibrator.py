#!/usr/bin/env python3
"""Fit the pipeline isotonic calibrator on out-of-fold predictions.

`calibration_model.ProbabilityCalibrator` has always documented itself as
"fitted on out-of-fold predictions from retrain_v2.py walk-forward CV", but
nothing in the repository ever produced `models/isotonic_calibrator.pkl` —
`fit()` had no callers. This is that missing producer.

Out-of-fold is the whole point. A calibrator fitted on predictions the model
made about its own training rows learns the model's memorised confidence, not
its real reliability; applied at inference it flatters every probability and
therefore every edge computed from it. Here the walk-forward splitter's test
folds are, by construction, rows the fold's model never saw.

Guardrail 9 (staged retrains): artifacts are written to `models/staging/` and a
human promotes. This script never overwrites a live artifact.

Split logic is imported from `walk_forward_backtest` rather than reimplemented,
so the calibration fit and the backtest agree on what "out of fold" means.

Usage:
    python fit_calibrator.py --self-test          # no DB required
    python fit_calibrator.py --gap-days 7         # fit from DATABASE_URL
"""

import os
import sys
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from calibration_model import ProbabilityCalibrator
from walk_forward_backtest import WalkForwardSplitter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STAGING_DIR = os.path.join(SCRIPT_DIR, "models", "staging")


def generate_oof_predictions(
    df: pd.DataFrame,
    predict_fn: Callable[[pd.DataFrame, pd.DataFrame], np.ndarray],
    splitter: Optional[WalkForwardSplitter] = None,
    date_column: str = "race_date",
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """Collect predictions for rows no fold-model was trained on.

    `predict_fn(train_df, test_df) -> probabilities` is injected so this works
    against any trainer, and so the self-test can drive it without the heavy
    ML stack. Returns (oof_probs, oof_y, provenance).
    """
    if splitter is None:
        splitter = WalkForwardSplitter(race_safe=True)

    frame = splitter.prepare_frame(df, date_column=date_column)

    probs_parts, y_parts, fold_meta = [], [], []
    for train_idx, test_idx, meta in splitter.split(df, date_column=date_column):
        train_df = frame.iloc[train_idx]
        test_df = frame.iloc[test_idx]

        preds = np.asarray(predict_fn(train_df, test_df), dtype=float)
        if preds.size != len(test_idx):
            raise ValueError(
                f"fold {meta['fold_number']}: predict_fn returned {preds.size} "
                f"predictions for {len(test_idx)} test rows")

        probs_parts.append(preds)
        y_parts.append(test_df["won"].to_numpy(dtype=int))
        fold_meta.append({
            "fold_number": meta["fold_number"],
            "test_start": meta["test_start"],
            "test_end": meta["test_end"],
            "n_test": len(test_idx),
            "actual_gap_days": meta["actual_gap_days"],
            "n_races_spanning_folds": meta.get("n_races_spanning_folds"),
        })

    if not probs_parts:
        raise RuntimeError("no folds produced — check data size and splitter settings")

    oof_probs = np.concatenate(probs_parts)
    oof_y = np.concatenate(y_parts)

    spanning = sum(f.get("n_races_spanning_folds") or 0 for f in fold_meta)
    if spanning:
        raise RuntimeError(
            f"refusing to fit: {spanning} race(s) spanned a train/test split — "
            "the predictions are not out-of-fold")

    provenance = {
        "out_of_fold": True,
        "fit_method": "isotonic",
        "n_rows": int(oof_probs.size),
        "n_folds": len(fold_meta),
        "gap_days": splitter.gap_days,
        "race_safe": splitter.race_safe,
        "base_rate": round(float(oof_y.mean()), 6),
        "fitted_at": datetime.now().isoformat(),
        "folds": fold_meta,
    }
    return oof_probs, oof_y, provenance


def fit_from_oof(oof_probs: np.ndarray, oof_y: np.ndarray,
                 model_path: Optional[str] = None,
                 provenance: Optional[Dict[str, Any]] = None) -> ProbabilityCalibrator:
    """Fit and persist a calibrator, staged for human promotion."""
    if model_path is None:
        os.makedirs(STAGING_DIR, exist_ok=True)
        model_path = os.path.join(STAGING_DIR, "isotonic_calibrator.pkl")

    cal = ProbabilityCalibrator(model_path=model_path)
    cal.fit(oof_probs, oof_y, meta=provenance or {"out_of_fold": True})
    return cal


def calibration_report(probs: np.ndarray, y: np.ndarray, n_bins: int = 10) -> Dict[str, Any]:
    """Reliability summary: Brier, and observed vs predicted per equal-width bin."""
    probs = np.asarray(probs, dtype=float)
    y = np.asarray(y, dtype=int)
    brier = float(np.mean((probs - y) ** 2))

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (probs >= lo) & (probs < hi) if i < n_bins - 1 else (probs >= lo) & (probs <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        bins.append({
            "bin": f"[{lo:.1f},{hi:.1f})",
            "n": n,
            "mean_pred": round(float(probs[mask].mean()), 4),
            "observed": round(float(y[mask].mean()), 4),
        })
    return {"brier": round(brier, 6), "n": int(probs.size), "bins": bins}


def _self_test():
    print("fit_calibrator self-test")

    from walk_forward_backtest import _make_synthetic_races

    df = _make_synthetic_races(n_days=80, races_per_day=4, runners_per_race=8, seed=7)
    splitter = WalkForwardSplitter(min_train_size=200, test_size=120, gap_days=7,
                                   race_safe=True, race_column="race_id")

    seen_train_rows = {"count": 0}

    def overconfident_predict(train_df, test_df):
        """Stand-in trainer: systematically overconfident, never sees test rows."""
        seen_train_rows["count"] += len(train_df)
        rng = np.random.default_rng(len(train_df))
        base = train_df["won"].mean()
        return np.clip(rng.normal(base * 2.2, 0.05, size=len(test_df)), 0.01, 0.99)

    probs, y, prov = generate_oof_predictions(df, overconfident_predict, splitter)

    assert probs.size == y.size > 0
    assert prov["out_of_fold"] is True
    assert prov["race_safe"] is True
    assert prov["n_folds"] >= 2, prov["n_folds"]
    assert all(f["n_races_spanning_folds"] == 0 for f in prov["folds"])
    assert seen_train_rows["count"] > 0, "predict_fn was never given training rows"
    print(f"  OOF: {probs.size} predictions over {prov['n_folds']} folds, "
          f"base rate {prov['base_rate']:.3f}, 0 races spanning a split")

    before = calibration_report(probs, y)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "models", "staging", "iso.pkl")
        cal = fit_from_oof(probs, y, model_path=path, provenance=prov)
        assert os.path.exists(path)

        after_probs = cal.calibrate(probs)
        after = calibration_report(after_probs, y)
        assert after["brier"] <= before["brier"] + 1e-9, (before["brier"], after["brier"])
        assert abs(after_probs.mean() - y.mean()) < abs(probs.mean() - y.mean())
        print(f"  Brier {before['brier']:.4f} -> {after['brier']:.4f}; "
              f"mean {probs.mean():.3f} -> {after_probs.mean():.3f} vs base {y.mean():.3f}")

        reloaded = ProbabilityCalibrator(model_path=path).describe()
        assert reloaded["meta"]["out_of_fold"] is True
        assert reloaded["meta"]["n_folds"] == prov["n_folds"]
        print(f"  staged artifact carries provenance: n_folds={reloaded['meta']['n_folds']}, "
              f"gap_days={reloaded['meta']['gap_days']}")

    # A predict_fn returning the wrong length must be rejected, not silently zipped.
    try:
        generate_oof_predictions(df, lambda tr, te: np.zeros(3), splitter)
        raise AssertionError("expected ValueError on length mismatch")
    except ValueError:
        pass
    print("  predict_fn length mismatch is rejected")

    print("All tests completed successfully.")


if __name__ == "__main__":
    import argparse

    if "--self-test" in sys.argv:
        _self_test()
        sys.exit(0)

    parser = argparse.ArgumentParser(
        description="Fit the pipeline isotonic calibrator on out-of-fold predictions")
    parser.add_argument("--self-test", action="store_true",
                        help="Run the built-in self-test (no database required)")
    parser.add_argument("--gap-days", type=int, default=7,
                        help="Purge gap in days (default: 7)")
    parser.add_argument("--min-train", type=int, default=3000)
    parser.add_argument("--test-size", type=int, default=500)
    parser.add_argument("--output", type=str, default=None,
                        help=f"Artifact path (default: {STAGING_DIR}/isotonic_calibrator.pkl)")
    args = parser.parse_args()

    from walk_forward_backtest import WalkForwardBacktester
    from ml_model import RacingMLModel

    data = WalkForwardBacktester(verbose=True)._load_data()

    def _train_and_predict(train_df, test_df):
        model = RacingMLModel(model_path=None)
        model.model_path = os.path.join(STAGING_DIR, "_tmp_calib_fold.pkl")
        result = model.train(train_df, target_col="won", use_optuna=False, n_trials=0)
        if not result.get("success"):
            raise RuntimeError(f"fold training failed: {result.get('error')}")
        try:
            return model.predict_proba(model.prepare_features(test_df))
        finally:
            try:
                os.remove(model.model_path)
            except OSError:
                pass

    splitter = WalkForwardSplitter(min_train_size=args.min_train,
                                   test_size=args.test_size,
                                   gap_days=args.gap_days,
                                   race_safe=True)

    probs, y, prov = generate_oof_predictions(data, _train_and_predict, splitter)
    print(f"\nOut-of-fold predictions: {probs.size} rows over {prov['n_folds']} folds")

    before = calibration_report(probs, y)
    cal = fit_from_oof(probs, y, model_path=args.output, provenance=prov)
    after = calibration_report(cal.calibrate(probs), y)

    print(f"Brier before={before['brier']:.6f}  after={after['brier']:.6f}")
    print("\nArtifact is STAGED. Promote deliberately — see guardrail 9 "
          "(staged retrains, human promotes).")

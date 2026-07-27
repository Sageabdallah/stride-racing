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
    python fit_calibrator.py --source mc_win_percentage_oof --check-coverage
    python fit_calibrator.py --source final_prob --input-csv export.csv
"""

import os
import sys
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from calibration_model import DEFAULT_PATH as V1_CALIBRATOR_PATH
from calibration_model import ProbabilityCalibrator
from walk_forward_backtest import WalkForwardSplitter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STAGING_DIR = os.path.join(SCRIPT_DIR, "models", "staging")

# Task 05: the calibrator must be fitted on the quantity it actually
# calibrates at serve time. The pipeline applies it to the Monte-Carlo
# winPercentage, so the refit sources are the recorded MC win percentage or
# the published final probability — not the v1 ML ensemble's OOF output.
SOURCES = ("ml_ensemble_oof", "mc_win_percentage_oof", "final_prob")
V2_ARTIFACT_NAME = "isotonic_calibrator_v2.pkl"  # staged; never overwrite v1

# SQL for the recorded-probability sources (live environment only).
# predicted_win_prob is logged by mc_api BEFORE the wrapper's isotonic /
# ML-blend / market-anchor stages; final_win_prob is the published
# end-of-pipeline probability (run_tips_pipeline.store_final_probs_in_audit).
# Both are recorded before the race they describe, so they are temporally
# safe to fit on. Probabilities are stored on the percent scale.
_SOURCE_SQL = {
    "mc_win_percentage_oof": """
        SELECT race_date, track, race_number, horse_name,
               predicted_win_prob::float8 AS prob,
               (actual_position = 1)::int AS won
        FROM prediction_audit
        WHERE predicted_win_prob IS NOT NULL
          AND actual_position IS NOT NULL
        ORDER BY race_date, track, race_number
    """,
    "final_prob": """
        SELECT race_date, track, race_number, horse_name,
               final_win_prob::float8 AS prob,
               (actual_position = 1)::int AS won
        FROM prediction_audit
        WHERE final_win_prob IS NOT NULL
          AND actual_position IS NOT NULL
        ORDER BY race_date, track, race_number
    """,
}


def min_coverage() -> int:
    """Hard coverage gate threshold (STRIDE_CAL_MIN_COVERAGE, default 500)."""
    try:
        return max(1, int(os.environ.get("STRIDE_CAL_MIN_COVERAGE", "500")))
    except ValueError:
        return 500


def normalise_source_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Canonical (race_date, prob, won, race key) frame for a recorded source.

    Drops rows without a usable probability or outcome, converts percent-
    scale probabilities to 0-1, and derives the race identity used by the
    race-safe splitter.
    """
    frame = df.copy()
    if "race_date" not in frame.columns or "prob" not in frame.columns:
        raise ValueError("source frame must carry race_date and prob columns")
    if "won" not in frame.columns:
        raise ValueError("source frame must carry a won outcome column")
    frame["race_date"] = pd.to_datetime(frame["race_date"])
    frame["prob"] = pd.to_numeric(frame["prob"], errors="coerce")
    frame["won"] = pd.to_numeric(frame["won"], errors="coerce")
    frame = frame.dropna(subset=["prob", "won", "race_date"])
    # Recorded probabilities are percent scale (0-100); calibrate on 0-1.
    if len(frame) and frame["prob"].max() > 1.5:
        frame["prob"] = frame["prob"] / 100.0
    frame = frame[(frame["prob"] >= 0.0) & (frame["prob"] <= 1.0)]
    frame["won"] = frame["won"].astype(int)
    return frame.reset_index(drop=True)


def check_coverage(df: pd.DataFrame, source: str,
                   min_rows: Optional[int] = None) -> Dict[str, Any]:
    """Hard coverage gate (ROADMAP_REVIEW task-05 modification).

    Reports the usable sample count and date range for a fit source and
    REFUSES to fit below STRIDE_CAL_MIN_COVERAGE (default 500). Insufficient
    coverage means the final-probability audit logging must be extended first
    — never fit on a patched-together sample.
    """
    threshold = min_rows if min_rows is not None else min_coverage()
    frame = normalise_source_frame(df)
    report = {
        "source": source,
        "usable_rows": int(len(frame)),
        "date_min": str(frame["race_date"].min().date()) if len(frame) else None,
        "date_max": str(frame["race_date"].max().date()) if len(frame) else None,
        "min_coverage": int(threshold),
        "sufficient": bool(len(frame) >= threshold),
    }
    if not report["sufficient"]:
        raise RuntimeError(
            f"refusing to fit: source '{source}' has {report['usable_rows']} "
            f"usable rows ({report['date_min']}..{report['date_max']}), below "
            f"the STRIDE_CAL_MIN_COVERAGE hard gate ({threshold}). Extend the "
            "final-probability audit logging and wait for coverage rather than "
            "fitting on a patched-together sample.")
    return report


def load_source_frame(source: str,
                      input_csv: Optional[str] = None) -> pd.DataFrame:
    """Load a recorded-probability source from CSV export or DATABASE_URL."""
    if source not in _SOURCE_SQL:
        raise ValueError(f"no recorded loader for source '{source}'")
    if input_csv:
        return pd.read_csv(input_csv)
    import psycopg2
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        env_path = os.path.join(SCRIPT_DIR, "..", "..", ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip().strip('"')
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL unavailable — pass --input-csv with an export of "
            "the recorded probabilities instead")
    conn = psycopg2.connect(db_url, connect_timeout=10)
    try:
        return pd.read_sql(_SOURCE_SQL[source], conn)
    finally:
        conn.close()


def recorded_source_oof(
    df: pd.DataFrame,
    source: str,
    splitter: Optional[WalkForwardSplitter] = None,
    date_column: str = "race_date",
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """OOF probabilities from a recorded pre-race source.

    The probabilities were published before each race they describe, so every
    row is out-of-fold relative to deployment and the calibrator is never
    fitted on data from after the race it calibrates. The race-safe
    walk-forward splitter is still run over the frame to (a) REFUSE when any
    race spans a fold boundary (the existing fold-refusal pattern) and (b)
    record fold dates in the provenance sidecar.
    """
    if splitter is None:
        splitter = WalkForwardSplitter(race_safe=True)

    frame = normalise_source_frame(df)
    prepared = splitter.prepare_frame(frame, date_column=date_column)

    fold_meta = []
    for _train_idx, test_idx, meta in splitter.split(frame, date_column=date_column):
        fold_meta.append({
            "fold_number": meta["fold_number"],
            "test_start": meta["test_start"],
            "test_end": meta["test_end"],
            "n_test": len(test_idx),
            "actual_gap_days": meta["actual_gap_days"],
            "n_races_spanning_folds": meta.get("n_races_spanning_folds"),
        })

    spanning = sum(f.get("n_races_spanning_folds") or 0 for f in fold_meta)
    if spanning:
        raise RuntimeError(
            f"refusing to fit: {spanning} race(s) spanned a train/test split — "
            "the predictions are not out-of-fold")

    oof_probs = prepared["prob"].to_numpy(dtype=float)
    oof_y = prepared["won"].to_numpy(dtype=int)

    provenance = {
        "out_of_fold": True,
        "fit_method": "isotonic",
        "source": source,
        "stage_quantity": source,
        "n_rows": int(oof_probs.size),
        "n_folds": len(fold_meta),
        "gap_days": splitter.gap_days,
        "race_safe": splitter.race_safe,
        "base_rate": round(float(oof_y.mean()), 6) if oof_y.size else None,
        "fitted_at": datetime.now().isoformat(),
        "folds": fold_meta,
    }
    return oof_probs, oof_y, provenance


def default_artifact_path(source: str) -> str:
    """Staged artifact path for a source. The refit sources write
    isotonic_calibrator_v2.pkl — v1 is never overwritten."""
    name = V2_ARTIFACT_NAME if source != "ml_ensemble_oof" else "isotonic_calibrator.pkl"
    return os.path.join(STAGING_DIR, name)


def _assert_not_v1_path(model_path: str) -> None:
    """Guardrail: this script never overwrites the live v1 artifact."""
    if os.path.abspath(model_path) == os.path.abspath(V1_CALIBRATOR_PATH):
        raise RuntimeError(
            f"refusing to write {model_path} — that is the live v1 calibrator "
            "artifact. Stage to models/staging/ and promote deliberately.")


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
        "source": "ml_ensemble_oof",
        "stage_quantity": "ml_ensemble_oof",
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
    _assert_not_v1_path(model_path)

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

    # Task 05: recorded-source path, coverage gate, and v1-overwrite guard.
    rec = df.rename(columns={"ml_prob": "prob"}) if "ml_prob" in df.columns else df.copy()
    if "prob" not in rec.columns:
        rec["prob"] = np.clip(rec["won"] * 0.2 + 0.1, 0.01, 0.9)

    try:
        check_coverage(rec, "mc_win_percentage_oof", min_rows=len(rec) + 1)
        raise AssertionError("expected coverage-gate refusal")
    except RuntimeError:
        pass
    print("  coverage gate refuses below STRIDE_CAL_MIN_COVERAGE")

    report = check_coverage(rec, "mc_win_percentage_oof", min_rows=10)
    assert report["sufficient"] and report["usable_rows"] == len(rec)
    assert report["date_min"] and report["date_max"]
    print(f"  coverage report: {report['usable_rows']} rows "
          f"{report['date_min']}..{report['date_max']}")

    probs2, y2, prov2 = recorded_source_oof(
        rec, "mc_win_percentage_oof",
        WalkForwardSplitter(min_train_size=200, test_size=120, gap_days=7,
                            race_safe=True, race_column="race_id"))
    assert prov2["source"] == "mc_win_percentage_oof"
    assert prov2["stage_quantity"] == "mc_win_percentage_oof"
    assert prov2["n_rows"] == len(rec) and prov2["n_folds"] >= 1
    assert all(f["n_races_spanning_folds"] == 0 for f in prov2["folds"])
    print(f"  recorded source: {probs2.size} rows, {prov2['n_folds']} fold windows recorded")

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        v2_path = os.path.join(tmp, "models", "staging", V2_ARTIFACT_NAME)
        cal2 = fit_from_oof(probs2, y2, model_path=v2_path, provenance=prov2)
        assert os.path.exists(v2_path)
        meta2 = ProbabilityCalibrator(model_path=v2_path).describe()["meta"]
        assert meta2["source"] == "mc_win_percentage_oof"
        assert meta2["n_rows"] == len(rec) and meta2["folds"]
        print(f"  v2 sidecar records stage quantity, fold dates, n={meta2['n_rows']}")
        try:
            fit_from_oof(probs2, y2, model_path=V1_CALIBRATOR_PATH, provenance=prov2)
            raise AssertionError("expected v1-overwrite refusal")
        except RuntimeError:
            pass
        print("  v1 artifact path is never overwritten")

    assert default_artifact_path("mc_win_percentage_oof").endswith(V2_ARTIFACT_NAME)
    assert default_artifact_path("final_prob").endswith(V2_ARTIFACT_NAME)
    assert default_artifact_path("ml_ensemble_oof").endswith("isotonic_calibrator.pkl")
    print("  refit sources stage to isotonic_calibrator_v2.pkl by default")

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
    parser.add_argument("--source", choices=SOURCES, default="ml_ensemble_oof",
                        help="Stage quantity to fit on (default: ml_ensemble_oof "
                             "keeps current behavior). mc_win_percentage_oof and "
                             "final_prob fit the calibrator on the quantity the "
                             "pipeline actually calibrates (task 05).")
    parser.add_argument("--input-csv", type=str, default=None,
                        help="CSV export of the recorded source (columns "
                             "race_date, prob, won[, track, race_number]) "
                             "instead of DATABASE_URL")
    parser.add_argument("--check-coverage", action="store_true",
                        help="Report source coverage and exit without fitting")
    parser.add_argument("--gap-days", type=int, default=7,
                        help="Purge gap in days (default: 7)")
    parser.add_argument("--min-train", type=int, default=3000)
    parser.add_argument("--test-size", type=int, default=500)
    parser.add_argument("--output", type=str, default=None,
                        help="Artifact path (default: staged per source; v2 for "
                             "the task-05 refit sources, never the live v1)")
    args = parser.parse_args()

    if args.source != "ml_ensemble_oof":
        frame = load_source_frame(args.source, input_csv=args.input_csv)
        report = check_coverage(frame, args.source)  # hard gate — may refuse
        print(f"Coverage [{args.source}]: {report['usable_rows']} usable rows "
              f"{report['date_min']}..{report['date_max']} "
              f"(gate: {report['min_coverage']})")
        if args.check_coverage:
            sys.exit(0)

        splitter = WalkForwardSplitter(min_train_size=args.min_train,
                                       test_size=args.test_size,
                                       gap_days=args.gap_days,
                                       race_safe=True)
        probs, y, prov = recorded_source_oof(frame, args.source, splitter)
        prov["coverage"] = report
        output = args.output or default_artifact_path(args.source)
        print(f"Recorded {args.source}: {probs.size} rows over "
              f"{prov['n_folds']} fold windows")

        before = calibration_report(probs, y)
        cal = fit_from_oof(probs, y, model_path=output, provenance=prov)
        after = calibration_report(cal.calibrate(probs), y)
        print(f"Brier before={before['brier']:.6f}  after={after['brier']:.6f}")
        print(f"\nArtifact is STAGED at {output}. Promote deliberately — "
              "see guardrail 9 (staged retrains, human promotes).")
        sys.exit(0)

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
    cal = fit_from_oof(probs, y,
                       model_path=args.output or default_artifact_path(args.source),
                       provenance=prov)
    after = calibration_report(cal.calibrate(probs), y)

    print(f"Brier before={before['brier']:.6f}  after={after['brier']:.6f}")
    print("\nArtifact is STAGED. Promote deliberately — see guardrail 9 "
          "(staged retrains, human promotes).")

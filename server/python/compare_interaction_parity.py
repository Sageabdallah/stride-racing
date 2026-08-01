#!/usr/bin/env python3
"""Interaction-parity comparison harness (ROI roadmap task 03, step 3).

Scores a labelled features frame two ways — legacy serve formula
`barrier_advantage * (1 - pace_pressure_score)` vs the training formula
`barrier_advantage * pace_pressure_score` (STRIDE_INTERACTION_PARITY=on) —
and dumps a Brier / log-loss comparison JSON. This is the evidence artifact
required before the STRIDE_INTERACTION_PARITY default may be flipped.

Usage (live env — needs model artifact server/python/models/racing_ensemble_v2.pkl):

    python compare_interaction_parity.py \
        --features backtest_features.csv \
        --label won \
        --out interaction_parity_comparison.json

The CSV must contain one row per scored runner with at least
`barrier_advantage`, `pace_pressure_score` and the label column, plus
whatever other FEATURE_COLUMNS the frame carries. Every column except
`barrier_x_pace_inv` is held identical between the two modes, so the metric
delta isolates the interaction fix. Nothing here alters defaults; it only
measures.
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


def _mode_metrics(df, label_col, barrier_mode):
    """Score df with the trained ensemble after recomputing barrier_x_pace_inv
    under the requested mode. Returns (brier, log_loss, mean_prob, n)."""
    import numpy as np
    from sklearn.metrics import brier_score_loss, log_loss

    from feature_interactions import (
        barrier_x_pace_inv as parity_formula,
        legacy_inference_barrier_x_pace_inv as legacy_formula,
    )
    from ml_model import RacingMLModel

    scored = df.copy()
    if barrier_mode == "parity":
        scored["barrier_x_pace_inv"] = [
            parity_formula(ba, pps)
            for ba, pps in zip(scored.get("barrier_advantage"), scored.get("pace_pressure_score"))
        ]
    else:
        scored["barrier_x_pace_inv"] = [
            legacy_formula(ba, pps)
            for ba, pps in zip(scored.get("barrier_advantage"), scored.get("pace_pressure_score"))
        ]

    model = RacingMLModel()
    if not model.is_trained:
        raise SystemExit(
            "Model artifact not found/trained — run this in the live env "
            "(server/python/models/racing_ensemble_v2.pkl)."
        )
    X = model.prepare_features(scored)
    probs = np.clip(model.predict_proba(X), 1e-15, 1 - 1e-15)
    y = scored[label_col].astype(int).values
    return {
        "n": int(len(y)),
        "brier": float(brier_score_loss(y, probs)),
        "log_loss": float(log_loss(y, probs, labels=[0, 1])),
        "mean_prob": float(probs.mean()),
        "base_rate": float(y.mean()),
    }


def main():
    ap = argparse.ArgumentParser(description="Compare legacy vs parity barrier_x_pace_inv on a labelled frame.")
    ap.add_argument("--features", required=True, help="CSV of per-runner features with label column")
    ap.add_argument("--label", default="won", help="Label column name (default: won)")
    ap.add_argument("--out", default=None, help="Output JSON path (default: print to stdout)")
    args = ap.parse_args()

    import pandas as pd

    df = pd.read_csv(args.features)
    for col in ("barrier_advantage", "pace_pressure_score"):
        if col not in df.columns:
            df[col] = None  # formulas apply their training defaults (0 / 0.5)
    if args.label not in df.columns:
        raise SystemExit(f"Label column {args.label!r} not in {args.features}")

    result = {
        "features": os.path.abspath(args.features),
        "label": args.label,
        "note": "Only barrier_x_pace_inv differs between modes; all other columns identical.",
        "legacy_adv_x_1_minus_pace": _mode_metrics(df, args.label, "legacy"),
        "parity_adv_x_pace": _mode_metrics(df, args.label, "parity"),
    }
    result["delta_parity_minus_legacy"] = {
        k: result["parity_adv_x_pace"][k] - result["legacy_adv_x_1_minus_pace"][k]
        for k in ("brier", "log_loss")
    }
    result["verdict"] = (
        "parity_not_worse"
        if result["delta_parity_minus_legacy"]["brier"] <= 0
        and result["delta_parity_minus_legacy"]["log_loss"] <= 0
        else "parity_worse_keep_default_off"
    )

    payload = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(payload + "\n")
        print(f"Wrote {args.out}")
    else:
        print(payload)


if __name__ == "__main__":
    main()

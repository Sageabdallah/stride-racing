"""Typed audit contract for the existing STRIDE inference pipeline."""

from __future__ import annotations

import sys
from typing import Any, Dict, Optional


STAGE_DEFINITIONS = {
    "base_xgb": ("RacingMLModel", "probability"),
    "base_lightgbm": ("RacingMLModel", "probability"),
    "base_catboost": ("RacingMLModel", "probability"),
    "ensemble": ("RacingMLModel", "probability"),
    "mc_raw": ("MonteCarloEngine", "probability"),
    "mc_recalibrated": ("MonteCarloEngine", "probability"),
    "sectional_blend": ("mc_api", "probability"),
    "ml_adjustment": ("mc_api", "multiplier"),
    "combined_adjustment": ("mc_api", "multiplier"),
    "wrapper_pre_calibration": ("run_tips_pipeline", "probability"),
    "wrapper_model_blend": ("run_tips_pipeline", "probability"),
    "market_context_probability": ("run_tips_pipeline", "probability"),
    "selection_score": ("run_tips_pipeline", "score"),
    "final_decision": ("run_tips_pipeline", "decision"),
}


def put_stage(
    stages: Dict[str, Dict[str, Any]],
    name: str,
    value: Any,
    *,
    note: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    if name not in STAGE_DEFINITIONS:
        raise KeyError(f"unknown prediction stage: {name}")
    owner, quantity_type = STAGE_DEFINITIONS[name]
    if value is not None and quantity_type in ("probability", "multiplier", "score"):
        value = float(value)
    if quantity_type == "probability" and value is not None and not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} probability must be in [0, 1], got {value}")
    record: Dict[str, Any] = {
        "owner": owner,
        "quantity_type": quantity_type,
        "value": value,
    }
    if note:
        record["note"] = note
    stages[name] = record
    return stages


def record_stage(
    stages: Dict[str, Dict[str, Any]],
    name: str,
    value: Any,
    *,
    note: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Best-effort production recorder: audit instrumentation cannot lose a race.

    ``put_stage`` remains the strict contract validator.  Production call sites
    use this wrapper so an invalid observed value is retained, visibly marked,
    and reported instead of raising through the race pipeline.
    """
    try:
        return put_stage(stages, name, value, note=note)
    except Exception as exc:
        owner, quantity_type = STAGE_DEFINITIONS.get(name, ("unknown", "unknown"))
        raw_value = value
        if quantity_type in ("probability", "multiplier", "score") and value is not None:
            try:
                raw_value = float(value)
            except (TypeError, ValueError):
                pass
        record: Dict[str, Any] = {
            "owner": owner,
            "quantity_type": quantity_type,
            "value": raw_value,
            "recording_error": str(exc),
        }
        if quantity_type == "probability" and isinstance(raw_value, (int, float)):
            if not 0.0 <= raw_value <= 1.0:
                record["range_violation"] = True
        if note:
            record["note"] = note
        stages[name] = record
        marker = " range_violation=true" if record.get("range_violation") else ""
        print(
            f"[PREDICTION_STAGE_RECORDING_VIOLATION] stage={name} "
            f"raw_value={raw_value!r}{marker} error={exc}",
            file=sys.stderr,
        )
        return stages


def finalise_stages(horse: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Record the wrapper's final currently-exported probability and score."""
    stages = dict(horse.get("predictionStages") or {})
    record_stage(
        stages,
        "market_context_probability",
        float(horse.get("winPercentage", 0) or 0) / 100.0,
        note="Final exported p-like quantity; Phase 2 must establish authority/calibration.",
    )
    record_stage(stages, "selection_score", float(horse.get("selectionScore", 0) or 0))
    return stages

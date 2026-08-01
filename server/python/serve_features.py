#!/usr/bin/env python3
"""Single shared serve-time feature builder (ROI roadmap task 03).

`run_tips_pipeline.py` and `mc_api.py` used to assemble the ML feature row
independently, and the two paths disagreed — most visibly on the ~15
market-movement columns, which mc_api populated from real movement while
run_tips_pipeline (and training) served 0. This module is the ONE builder
both inference paths call for the shared FEATURE_COLUMNS.

Semantics are transcribed verbatim from the run_tips_pipeline assembly block
— no rescaling, no clipping, no "improvements" (task-03 guardrail: semantic
parity only). Behavioural fixes live behind the existing default-off env
flags so flag-off reproduces the old served values exactly:

  STRIDE_INTERACTION_PARITY   compute the five interaction features from the
                              training formulas (feature_interactions.py)
                              instead of the legacy inline versions, whose
                              barrier_x_pace_inv is sign-flipped against
                              training. Default ON — flipped after the on/off
                              comparison showed parity not worse (delta exactly
                              0: the feature is inert in the current artifact).
                              Set false to roll back (kept for one release).
  STRIDE_MOVEMENT_FEATURES_LIVE
                              populate MOVEMENT_FEATURES from real movement.
                              Default OFF — both paths serve 0, matching
                              training (retrain_v2 zero-fills them and never
                              computes them). Real values are sourced
                              post-tip-time — see task 14.
"""

import math
import os
import sys
from typing import Any, Dict, Optional

from nan_contract import NAN_PRESERVE_SET

# Market-movement columns. Zero in training (retrain_v2 never computes them;
# NON_SECTIONAL zero-fill). Forced to 0 in BOTH inference paths until task 14
# lands real as-of-tip-time movement — the point here is that train and both
# serve paths see the identical distribution.
MOVEMENT_FEATURES = (
    "is_steam_move",
    "is_drift",
    "odds_movement_pct",
    "last_start_market_diff",
    "avg_market_diff_3runs",
    "market_trend_shortening",
    "market_trend_drifting",
    "steam_velocity",
    "drift_velocity",
    "late_move_indicator",
    "market_confidence",
    "relative_move",
    "smart_money_score",
    "is_insider_signal",
    "field_market_agreement",
)


def _stride_flag(name: str) -> bool:
    """Default-off env flag, Variant B idiom (see run_tips_pipeline.py:591)."""
    return os.environ.get(name, "false").strip().lower() in ("true", "1", "yes")


def interaction_parity_enabled() -> bool:
    """STRIDE_INTERACTION_PARITY — default ON since the task-03 comparison.

    Evidence (examples/interaction_parity_comparison_2026-03-04_2026-04-18.json):
    on the 21,108-runner metro window, parity vs legacy delta is exactly 0 on
    Brier and log-loss — barrier_x_pace_inv is INERT in the current model
    artifact (importance 0.0; training never populates barrier_advantage, so
    the interaction is all-zero at fit time). The flip therefore changes no
    live output today; it removes the sign-flipped formula before the task-12
    retrain makes the feature real. Set STRIDE_INTERACTION_PARITY=false to
    roll back to the legacy inline formulas (kept below for one release).
    """
    return os.environ.get("STRIDE_INTERACTION_PARITY", "true").strip().lower() in ("true", "1", "yes")


def movement_features_live() -> bool:
    return _stride_flag("STRIDE_MOVEMENT_FEATURES_LIVE")


_movement_log_dates = set()


def log_movement_inert_once(race_date: Optional[Any]) -> None:
    """Log once per race day that the movement columns are served as inert 0s."""
    if movement_features_live():
        return
    key = str(race_date) if race_date is not None else "<unknown>"
    if key in _movement_log_dates:
        return
    _movement_log_dates.add(key)
    print(
        f"  [FEATURES] {len(MOVEMENT_FEATURES)} market-movement columns served as 0 "
        f"(inert — match training; real values sourced post-tip-time, task 14). "
        f"Set STRIDE_MOVEMENT_FEATURES_LIVE=1 to populate them. date={key}",
        file=sys.stderr,
    )


def build_feature_row(
    runner: Dict[str, Any],
    *,
    market_odds: Any,
    distance_m: Any,
    field_size: Any,
    rel_market: Optional[Dict[str, Any]] = None,
    movement: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble one serve-time feature row for the ML ensemble.

    `runner` is the enriched runner dict (form/sectional/trial/race-context
    keys are read with the same defaults the legacy run_tips_pipeline block
    used). `rel_market` is the Phase-5 within-race dict from
    relative_market.compute_field_relative_market. `movement` optionally
    carries real movement values and is only honoured when
    STRIDE_MOVEMENT_FEATURES_LIVE is on.
    """
    feat: Dict[str, Any] = {}
    feat["market_odds"] = market_odds or 0
    if rel_market:
        feat.update(rel_market)
    feat["barrier_draw"] = int(runner.get("draw") or runner.get("barrier") or 0)
    wt = str(runner.get("weight", "0")).replace("kg", "").strip()
    feat["weight_kg"] = float(wt) if wt else 0
    feat["distance"] = distance_m
    feat["field_size"] = field_size
    feat["class_level"] = runner.get("class_level") or 0
    for k in ["days_since_run", "is_first_up", "is_second_up", "course_strike_rate",
              "distance_strike_rate", "weighted_form_score", "class_movement",
              "is_class_drop", "is_class_rise", "improvement_score", "is_improving",
              "is_in_form_cycle", "has_dominant_win", "is_winning_combo",
              "jockey_trainer_strike_rate", "is_first_time_stakes",
              "form_direction_slope", "speed_rating_trajectory",
              "campaign_run_number",
              "weight_change", "jockey_booking_change",
              "fresh_x_trajectory", "first_up_win_rate",
              "second_up_win_rate", "consistency_score",
              "going_suitability",
              "dist_sectional_slope",
              "distance_direction_flag",
              "dist_sectional_recency_weighted",
              "sectional_result_divergence",
              "first_at_distance_sectional_quality",
              "is_bounce_candidate",
              "bounce_severity",
              "trial_count_60d",
              "trial_x_experience",
              "trainer_trial_pattern",
              "trial_quality_score"]:
        feat[k] = runner.get(k, 0)
    # NaN-preserve contract (nan_contract.py): sectional/z-score features,
    # sectional_trajectory, sectional_rank_at_distance, runs_since_peak and
    # trial_recency pass NaN through untouched. With STRIDE_SERVE_NAN_CONTRACT
    # off, prepare_features zero-fills them exactly as before; with it on the
    # trees see the same missingness they were trained on.
    for k in NAN_PRESERVE_SET:
        feat[k] = runner.get(k, float("nan"))
    # Explicit binary — data availability flag, [0,1] enforced downstream
    feat["has_sectional_data"] = int(bool(runner.get("has_sectional_data", 0)))
    feat["trainer_momentum_score"] = runner.get("trainer_momentum_score", 50)
    for k in ["pace_pressure_score", "leader_advantage",
              "closer_advantage", "barrier_relevance_score",
              "field_size_context", "market_efficiency_flag",
              "pace_clarity_score"]:
        feat[k] = runner.get(k, 0.5)
    feat["td_pace_bias"] = runner.get("td_pace_bias", 0.5)
    feat["td_upset_rate"] = runner.get("td_upset_rate", 0.2)
    feat["td_barrier_style_edge"] = runner.get("td_barrier_style_edge", 0)
    feat["td_closing_speed_bias"] = runner.get("td_closing_speed_bias", 0)
    # STRIDE_INTERACTION_PARITY: compute the five interaction features from
    # the same definitions training used (feature_interactions.py) instead of
    # restating them inline. barrier_x_pace_inv is inverted between the two —
    # training fits barrier_advantage * pace_pressure_score, the legacy inline
    # version serves barrier_advantage * (1 - pace_pressure_score) — and the
    # two agree only at 0.5, which is the default used when pace data is
    # missing. Default OFF: enabling changes which horses are tipped, so it
    # needs the on/off backtest comparison first (task 03 step 3).
    if interaction_parity_enabled():
        from feature_interactions import compute_interactions
        _src = dict(feat)
        for _k in ("pace_pressure_score", "barrier_advantage", "z_200m"):
            if runner.get(_k) is not None:
                _src[_k] = runner.get(_k)
        feat.update(compute_interactions(_src))
    else:
        _crn = feat.get("campaign_run_number", 1)
        _fp = max(0, 1 - abs(_crn - 3) * 0.15)
        feat["fitness_x_distance"] = _fp * feat.get("distance_strike_rate", 0)
        _pps = runner.get("pace_pressure_score", 0.5)
        feat["barrier_x_pace_inv"] = runner.get("barrier_advantage", 0) * (1 - _pps)
        feat["sectional_x_going"] = runner.get("z_200m", 0) * feat.get("going_suitability", 0.5)
        feat["class_drop_x_trajectory"] = feat.get("is_class_drop", 0) * feat.get("form_direction_slope", 0)
        _cf = max(0, 1 - max(0, _crn - 5) * 0.2)
        feat["campaign_run_x_fitness"] = _crn * _cf
    _ddir = max(0, feat.get("distance_direction_flag", 0))
    _dslope = feat.get("dist_sectional_slope", 0)
    _dslope = 0 if (_dslope is None or (isinstance(_dslope, float) and math.isnan(_dslope))) else _dslope
    feat["step_up_x_dist_slope"] = round(_ddir * max(0, _dslope), 4)
    # Market-movement columns: sourced post-tip-time — see task 14.
    # Served as 0 in both inference paths (matching training) unless
    # STRIDE_MOVEMENT_FEATURES_LIVE explicitly opts in.
    if movement_features_live() and movement:
        for k in MOVEMENT_FEATURES:
            feat[k] = movement.get(k, 0)
    else:
        for k in MOVEMENT_FEATURES:
            feat[k] = 0
    return feat


def overlay_shared_columns(
    features: Dict[str, Any],
    runner: Dict[str, Any],
    *,
    market_odds: Any,
    distance_m: Any,
    field_size: Any,
    rel_market: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the shared row and write it over `features` (mc_api path).

    The already-extracted mc_api values win for every key the builder reads,
    because the extracted dict is layered on top of the raw runner — the
    builder then re-expresses them through the one shared assembly, and
    zeroes the movement columns (sourced post-tip-time — see task 14).
    """
    merged = dict(runner or {})
    merged.update(features or {})
    if rel_market is None:
        rel_market = {
            k: features[k]
            for k in ("fair_implied_prob", "odds_rank", "odds_rank_pct")
            if k in (features or {})
        }
    shared = build_feature_row(
        merged,
        market_odds=market_odds,
        distance_m=distance_m,
        field_size=field_size,
        rel_market=rel_market,
        movement={k: (features or {}).get(k) for k in MOVEMENT_FEATURES},
    )
    features.update(shared)
    return features

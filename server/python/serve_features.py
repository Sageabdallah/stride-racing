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
  STRIDE_SERVE_LIVE_FEATURES  plumb the 15 trained-but-never-served features
                              (docs/research/FEATURE_PROVENANCE.md — 25.45% of
                              the artifact's importance mass) into the feat
                              dict: the group-A one-liners (field_size_context,
                              barrier_relevance_score, market_efficiency_flag),
                              the pace trio (pace_pressure_score,
                              leader_advantage, closer_advantage) computed from
                              the racecard field, and the sectional set
                              (z_200m/400m/600m/800m, lambda_decay, svi, rsi,
                              trip_cost_seconds) from prior starts. Default
                              OFF — flag off = byte-identical feat dict.
                              Deploy together with STRIDE_SERVE_NAN_CONTRACT=1
                              so unknown sectionals reach the trees as NaN
                              (the trained missingness signal), not 0.
  STRIDE_SERVE_LIVE_FEATURES_SHADOW
                              default OFF. When on (and the main flag off),
                              run_tips_pipeline scores each race BOTH ways,
                              publishes the LEGACY probabilities, and appends
                              per-runner deltas to
                              logs/serve_liveness_shadow_<date>.json.
"""

import math
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

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


def serve_live_features_enabled() -> bool:
    return _stride_flag("STRIDE_SERVE_LIVE_FEATURES")


def serve_live_features_shadow_enabled() -> bool:
    return _stride_flag("STRIDE_SERVE_LIVE_FEATURES_SHADOW")


# --- Trained-but-never-served features (FEATURE_PROVENANCE.md finding 1) -----
#
# The 15 ZERO_AT_SERVE features and their train derivations. Group A/B
# formulas below are EXACT mirrors of retrain_v2.py (line refs on main);
# retrain_v2 is never imported here (it needs psycopg2 + a DB) — equality is
# pinned by tests in test_serve_feature_liveness.py.

# Group C: prior-start sectional set. NAN_PRESERVE features — NaN means "no
# sectional history" and must never become 0 (0 is a real, exactly-average
# z-score). Names as in FEATURE_COLUMNS (the view's prior_* prefix is mapped
# away by retrain_v2.VIEW_TO_FEATURE_MAP).
SECTIONAL_LIVE_FEATURES = (
    "z_200m",
    "z_400m",
    "z_600m",
    "z_800m",
    "lambda_decay",
    "svi",
    "rsi",
    "trip_cost_seconds",
)

LIVE_FEATURES = SECTIONAL_LIVE_FEATURES + (
    "pace_pressure_score",
    "leader_advantage",
    "closer_advantage",
    "field_size_context",
    "barrier_relevance_score",
    "market_efficiency_flag",
)


def field_size_context_value(field_size: Any) -> float:
    """Mirror retrain_v2.py:646 — (field_size or 10)/16, clipped at 1.0."""
    try:
        fs = float(field_size) if field_size else 10.0
    except (TypeError, ValueError):
        fs = 10.0
    if fs != fs:  # NaN
        fs = 10.0
    return min(fs / 16.0, 1.0)


def barrier_relevance_value(distance_m: Any) -> float:
    """Mirror retrain_v2.py:641-643 — distance bucketing (NaN/None -> 1400)."""
    try:
        d = float(distance_m)
    except (TypeError, ValueError):
        d = 1400.0
    if d != d:  # NaN
        d = 1400.0
    return 1.0 if d <= 1200 else 0.7 if d <= 1600 else 0.4 if d <= 2000 else 0.2


def market_efficiency_value(class_level: Any) -> float:
    """Mirror retrain_v2.py:648-652 — class_level bucketing. Misleading name:
    nothing market-derived. Missing class_level -> 50 (at serve 0 is the
    missing sentinel — the legacy assembly uses `or 0`)."""
    try:
        c = float(class_level) if class_level else 50.0
    except (TypeError, ValueError):
        c = 50.0
    if c != c:  # NaN
        c = 50.0
    return (1.0 if c >= 80 else 0.7 if c >= 60 else 0.5 if c >= 50
            else 0.2 if c <= 30 else 0.4)


# Running-style scale, mirrored from retrain_v2.py:379-384 (do NOT import
# retrain_v2 — parity is pinned by test).
_STYLE_LEADER = 4
_STYLE_ON_PACE = 3
_STYLE_MIDFIELD = 2
_STYLE_OFF_PACE = 1
_STYLE_BACKMARKER = 0
_EXCLUDED_CHARS = set("xfdwXFDW")


def _classify_running_style_from_form(form_string, barrier=None, field_size=None):
    """Verbatim mirror of retrain_v2.py:387-441 — classify a runner's running
    style from its pre-race form string (last ~5 starts), with a barrier-based
    fallback when no form exists."""
    if not form_string or not isinstance(form_string, str):
        if barrier is not None and field_size and field_size > 0:
            try:
                b = int(barrier)
            except (ValueError, TypeError):
                return _STYLE_MIDFIELD
            if b <= field_size * 0.25:
                return _STYLE_MIDFIELD
            return _STYLE_OFF_PACE
        return _STYLE_MIDFIELD

    cleaned = form_string.replace("-", "").replace(" ", "")
    positions = []
    for ch in cleaned[-6:]:
        if ch in _EXCLUDED_CHARS:
            continue
        if ch.isdigit():
            pos = int(ch)
            if pos == 0:
                pos = 10
            positions.append(pos)
        if len(positions) >= 5:
            break

    if not positions:
        if barrier is not None and field_size and field_size > 0:
            try:
                b = int(barrier)
            except (ValueError, TypeError):
                return _STYLE_MIDFIELD
            if b <= field_size * 0.25:
                return _STYLE_MIDFIELD
            return _STYLE_OFF_PACE
        return _STYLE_MIDFIELD

    avg_pos = sum(positions) / len(positions)
    wins_from_front = sum(1 for p in positions if p == 1)

    if wins_from_front >= 2 or (len(positions) >= 3 and avg_pos <= 1.5):
        return _STYLE_LEADER
    elif avg_pos <= 2.5:
        return _STYLE_ON_PACE
    elif avg_pos <= 4.5:
        return _STYLE_MIDFIELD
    elif avg_pos <= 6:
        return _STYLE_OFF_PACE
    else:
        return _STYLE_BACKMARKER


def compute_race_pace(runners: Sequence[Dict[str, Any]]) -> List[Dict[str, float]]:
    """Per-race pace trio for a serve-time field — the per-race half of
    retrain_v2._compute_pace_features (:443-525), duplicated not imported.

    `runners` is the racecard field; each entry needs only pre-race keys:
    form_string (or form), barrier (or draw), field_size (defaults to the
    field size). Returns one {pace_pressure_score, leader_advantage,
    closer_advantage} dict per runner, aligned with the input order. Any
    failure degrades to the training no-field default (0.5 / 0.0 / 0.0).
    """
    default = {"pace_pressure_score": 0.5,
               "leader_advantage": 0.0, "closer_advantage": 0.0}
    runners = list(runners or [])
    if not runners:
        return []
    fs = len(runners) or 1
    try:
        styles = [
            _classify_running_style_from_form(
                r.get("form_string") or r.get("form"),
                barrier=(r.get("barrier") if r.get("barrier") is not None
                         else r.get("draw")),
                field_size=r.get("field_size") or fs,
            )
            for r in runners
        ]
        speed_count = sum(1 for s in styles if s >= _STYLE_ON_PACE)
        pps = speed_count / fs
        if pps >= 0.4:
            l_adv, c_adv = -0.10, 0.12
        elif pps <= 0.15:
            l_adv, c_adv = 0.15, -0.05
        else:
            l_adv, c_adv = 0.0, 0.0
    except Exception:
        return [dict(default) for _ in runners]

    out = []
    for style in styles:
        d = {"pace_pressure_score": pps,
             "leader_advantage": 0.0, "closer_advantage": 0.0}
        if style >= _STYLE_ON_PACE:
            d["leader_advantage"] = l_adv
        elif style <= _STYLE_OFF_PACE:
            d["closer_advantage"] = c_adv
        else:
            if pps >= 0.4:
                d["closer_advantage"] = c_adv * 0.6
            elif pps <= 0.15:
                d["leader_advantage"] = l_adv * 0.4
        out.append(d)
    return out


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
    pace: Optional[Dict[str, Any]] = None,
    sectionals: Optional[Dict[str, Any]] = None,
    force_live: bool = False,
) -> Dict[str, Any]:
    """Assemble one serve-time feature row for the ML ensemble.

    `runner` is the enriched runner dict (form/sectional/trial/race-context
    keys are read with the same defaults the legacy run_tips_pipeline block
    used). `rel_market` is the Phase-5 within-race dict from
    relative_market.compute_field_relative_market. `movement` optionally
    carries real movement values and is only honoured when
    STRIDE_MOVEMENT_FEATURES_LIVE is on. `pace` (one compute_race_pace entry)
    and `sectionals` (prior-start sectional dict) feed the 15 liveness
    features and are honoured only when STRIDE_SERVE_LIVE_FEATURES is on —
    or when `force_live` is set (the shadow path, which must build the
    plumbed dict while the main flag stays off). Flag off = byte-identical
    legacy dict; the extra kwargs are ignored.
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
    # STRIDE_SERVE_LIVE_FEATURES (default OFF): plumb the 15 trained-but-
    # never-served features (FEATURE_PROVENANCE.md — 25.45% of importance
    # mass). Group A one-liners and the group-B pace trio mirror retrain_v2
    # exactly; the group-C sectional set honours the NaN contract (unknown
    # -> NaN, NEVER 0 — 0 is a real exactly-average z-score, NaN is the
    # "no history" signal the trees learned). Flag off: feat dict is
    # byte-identical to legacy.
    if serve_live_features_enabled() or force_live:
        feat["field_size_context"] = field_size_context_value(field_size)
        feat["barrier_relevance_score"] = barrier_relevance_value(distance_m)
        feat["market_efficiency_flag"] = market_efficiency_value(
            runner.get("class_level"))
        if pace:
            feat["pace_pressure_score"] = pace.get("pace_pressure_score", 0.5)
            feat["leader_advantage"] = pace.get("leader_advantage", 0.0)
            feat["closer_advantage"] = pace.get("closer_advantage", 0.0)
        else:
            # Training's no-field-composition default (retrain_v2:454-462).
            feat["pace_pressure_score"] = 0.5
            feat["leader_advantage"] = 0.0
            feat["closer_advantage"] = 0.0
        # Only an explicitly-fetched sectional row overrides the runner-
        # sourced NAN_PRESERVE values above (mc_api's extracted features pass
        # through untouched when no fetch ran). A fetched row's NULLs are the
        # truth: unknown -> NaN, never 0.
        if sectionals is not None:
            for k in SECTIONAL_LIVE_FEATURES:
                v = sectionals.get(k)
                feat[k] = v if v is not None else float("nan")
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

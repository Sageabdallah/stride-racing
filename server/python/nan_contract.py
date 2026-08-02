#!/usr/bin/env python3
"""NaN-preservation contract — ONE definition shared by training and serving.

Training deliberately keeps NaN in a small set of columns so gradient-boosted
trees can route missingness down their own branch (`retrain_v2.build_feature_matrix`:
only NON_SECTIONAL_FEATURES are zero-filled; the preserve set keeps NaN).
Serving previously destroyed that signal with a blanket `.fillna(0)` in
`ml_model.prepare_features`, converting "no sectional data" into exactly
field-average z-scores and "no barrier trial" (NaN) into 0 (= trial today).

This module is the single source of truth for which columns preserve NaN.
It is imported by:

  - `retrain_v2.py`   (training: keep NaN out of the zero-fill pass)
  - `ml_model.py`     (serving: STRIDE_SERVE_NAN_CONTRACT gates NaN passthrough)
  - `serve_features.py` / `test_feature_parity.py`

It has no third-party imports so every consumer loads it cleanly.

Membership here must only ever change together with the trainer — adding a
column changes what the model is served; removing one reintroduces the
zero-fill corruption. Task 03 of docs/roi-roadmap.
"""

# Phase 2 sectional/z-score features (as named in FEATURE_COLUMNS).
# NaN when the horse has no usable sectional data — trees must see NaN,
# not 0 (0 is exactly field-average, the most misleading possible value).
PHASE2_FEATURES = [
    "z_200m",
    "z_400m",
    "z_600m",
    "z_800m",
    "lambda_decay",
    "svi",
    "rsi",
    "trip_cost_seconds",
    "sectional_trajectory",      # Phase 3: slope of last_200m_time, NaN when missing
    "sectional_rank_at_distance",  # Phase 4: within-race closing rank, NaN when missing
]

# Features that preserve NaN (like PHASE2) but are NOT ablated with sectionals.
TRAINER_NAN_PRESERVE = [
    "runs_since_peak",  # NaN when <5 prior runs; tree models exploit missingness
    "trial_recency",    # 999 = no trial; keep NaN rather than corrupt with 0 (0 = trial today)
]

# The full preserve set — the trainer's contract (retrain_v2: everything not in
# this list gets fillna(0); everything in it keeps NaN end to end).
# Winner-pattern priors (12P-8): NaN means "no prior sectional / cohort
# match exists", which is signal, not zero.
WINNER_PATTERN_NAN_PRESERVE = ["cohort_fast_close_prior", "pos400_win_prior"]

NAN_PRESERVE_FEATURES = (PHASE2_FEATURES + TRAINER_NAN_PRESERVE
                         + WINNER_PATTERN_NAN_PRESERVE)  # lists throughout

NAN_PRESERVE_SET = frozenset(NAN_PRESERVE_FEATURES)

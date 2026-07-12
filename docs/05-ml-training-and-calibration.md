# ML Training & Calibration

The ML ensemble is one of the two probability engines feeding the STRIDE score (the
other is the [Monte Carlo engine](06-monte-carlo-engine.md)). This document covers
how models are trained, which trainer generation is current, how probabilities are
calibrated, and exactly which artifacts the daily pipeline loads.

Related docs: [Feature engineering](04-feature-engineering.md) ·
[Backtesting & learning](10-backtesting-and-learning.md)

---

## 1. Three trainer generations — which one is live

| Script | Generation | Data source | Models | CV | Output artifact |
|---|---|---|---|---|---|
| `train_ml.py` | v1 (oldest) | `training_data` table | delegates to `RacingMLModel` | none | `models/racing_ensemble_v2.pkl` |
| `train_ml_enhanced.py` | "enhanced" | `prediction_audit` (fallback `training_data`+`selections`) | single model (XGB → LGB → GBM fallback) | `TimeSeriesSplit` capped at 3 folds | `models/enhanced_racing_ensemble.pkl` |
| **`retrain_v2.py`** | **v2 — current** | **`training_view_v2`** materialized view | **XGBoost + LightGBM + CatBoost** | **walk-forward with purge gap** | **`models/racing_ensemble_v2.pkl`** |

`retrain_v2.py` is the production trainer: it writes the exact pickle that the
inference wrapper `RacingMLModel` loads by default (`ml_model.py:186-189`), including
the retrain_v2 key names (`cb_model`, `feature_columns` → `_trained_feature_columns`,
`ml_model.py:647-657`). The "enhanced" generation is not fully retired — `mc_api.py`
still imports `EnhancedRacingMLModel` and `compare_features.py` reads its pickle.

There is **no neural network** — no TabNet, despite `pytorch-tabnet` appearing in
`requirements.txt`. Everything is gradient-boosted trees.

---

## 2. The production ensemble (retrain_v2)

Three base learners, trained on identical features:

| Model | Key hyperparameters (`retrain_v2.py:739-778`) |
|---|---|
| `XGBClassifier` | 200 trees, depth 6, lr 0.05, subsample 0.8, colsample 0.8, min_child_weight 5, `scale_pos_weight=9`, tree_method hist |
| `LGBMClassifier` | 200 trees, depth 6, lr 0.05, num_leaves 63, subsample/colsample 0.8, min_child_samples 20, `is_unbalance=True`, early stop 20 |
| `CatBoostClassifier` | 200 iterations, depth 6, lr 0.05, `auto_class_weights=Balanced`, early stop |

Combination at training time: each model's isotonic calibrator is applied, then a
plain **unweighted mean** (`predict_ensemble`, `retrain_v2.py:895`).

Class imbalance (winners ≈ 10–12% of rows) is handled by the native class weights
above. SMOTE was deliberately **removed** ("audit fix #3", `ml_model.py:273-278`)
because synthetic winners corrupt probability estimates. A complete focal-loss
implementation exists (`focal_loss.py`, γ=2.0, α=0.25) but is **never wired into any
`.fit()` call** — dead scaffolding.

---

## 3. Training data flow

1. `refresh_training_view_v2.py` rebuilds the **`training_view_v2`** materialized
   view: it UNIONs three prediction sources (`selections`, `training_data`,
   `prediction_audit`), dedupes by quality rank (live_model > audit_only >
   imported_historical), joins `race_results_history` for outcomes, and attaches
   each horse's **prior** sectionals via a temporal-safe
   `LEFT JOIN LATERAL ... WHERE st.race_date < r.race_date` (`:252-270`).
2. Target: **win** — `is_winner = (position = 1)` (`:219`). `is_placed` exists in the
   view but is not the trained target.
3. `retrain_v2.load_training_data` reads the view ordered by `race_date`, then calls
   `form_feature_builder.batch_compute_form_features` and loads field composition
   for pace features.
4. `build_feature_matrix` maps view columns to model feature names
   (`VIEW_TO_FEATURE_MAP`), keeps Phase-2 sectional features as **NaN** (tree models
   exploit missingness) while zero-filling the rest, and adds five interaction
   features inline.

### Temporal split

`DateWindowSplitter` (`retrain_v2.py:668`) implements walk-forward CV:
**minimum 60 days of training data, a 14-day purge gap, 14-day test windows,
stepped 14 days at a time**. Each fold requires ≥ 50 train and ≥ 5 test rows. The
final model trains on the first 90% of rows by date, with the last 10% used only for
early stopping — never for calibration.

---

## 4. Out-of-fold isotonic calibration (the headline)

The README's "27 folds, 30,226 temporal predictions" comes from this machinery:

1. During walk-forward CV, each fold's **raw (pre-isotonic)** validation predictions
   are accumulated per model (`run_walk_forward_cv(collect_oof_calibrators=True)`).
2. After CV, **one `IsotonicRegression` per model** is fitted on the pooled
   out-of-fold predictions (`retrain_v2.py:1048-1056`).
3. Those OOF calibrators are attached to the final full-data models as
   `model._isotonic` (`train_final_model`, `:1128-1148`). The docstring explicitly
   notes this closes "the leak where X_cal is used for both early stopping and
   calibration fitting" — calibration never sees data the model was tuned on.

**Integration note:** `RacingMLModel.predict_proba` (`ml_model.py:552-585`) calls
the base models' `predict_proba` directly and does not apply the embedded
`_isotonic` calibrators — only retrain_v2's own `predict_ensemble` uses them.
This is deliberately left as-is (and now documented with an inline comment at the
stacking branch): calibration of the final output is handled downstream by the
tips pipeline's `ProbabilityCalibrator`, which was fitted against the *current*
inference output. Switching per-model isotonic on at inference without refitting
the downstream calibrator would double-calibrate and distort the validated
probability scale. If per-model calibration at inference is ever wanted, refit
`isotonic_calibrator.pkl` on the new output in the same change.

---

## 5. The calibration story — five distinct layers

| Layer | Module | What it calibrates | Artifact |
|---|---|---|---|
| Per-model OOF isotonic | `retrain_v2.py` | each base model, from pooled walk-forward OOF | inside `racing_ensemble_v2.pkl` |
| Double calibration | `double_calibration.py` | layer 1 per-model isotonic + layer 2 isotonic on the ensemble (5 contiguous folds, n ≥ 30) | `models/double_calibrator.pkl` |
| Global isotonic | `calibration_model.py` | final model probability before market blending; bounds [0.01, 0.95] | `models/isotonic_calibrator.pkl` |
| MC recalibration | `mc_recalibration.py` | Monte Carlo sim probabilities (favourite-longshot bias); custom PAV; clips [0.005, 0.95], renormalizes per race | `server/python/calibration_model.json` |
| Enhanced trainer | `train_ml_enhanced.py` | `CalibratedClassifierCV` — fits both isotonic and Platt, keeps the lower Brier | inside `enhanced_racing_ensemble.pkl` |

At daily-pipeline runtime the ones that actually fire are: **ProbabilityCalibrator**
(loaded in `run_tips_pipeline.calibrate_and_score`, `:562`) applied to the MC-derived
win percentage, and **MCRecalibrator** inside mc_api (when its JSON model exists —
it is git-ignored, so inert in this published tree). `DoubleCalibrator` applies only
if its pickle exists and was fitted by `ml_model.train`.

Rationale for double calibration (from its docstring): the three GBMs have different
bias profiles — XGB overconfident on favourites, CatBoost underestimates longshots —
so per-model correction before ensemble correction beats a single global fit.

---

## 6. Inference path (what the daily pipeline actually calls)

```
run_tips_pipeline.py
  └─ RacingMLModel()                # loads models/racing_ensemble_v2.pkl
       ├─ prepare_features(df)      # ~110-column contract (see FEATURES doc)
       └─ predict_proba(X, distance_m=…)
            ├─ try stacking_meta_learner   → used when the artifact carries one (see §8)
            ├─ try double_calibrator       → if fitted
            └─ else weighted average of xgb/lgb/cat
                 weights from _model_performance — HARDCODED seed accuracies
                 per race category (sprint/mile/staying), ml_model.py:59-63
                 e.g. mile ≈ 0.333 / 0.303 / 0.364
```

The resulting `mlPredictedProb` is blended with the Monte-Carlo probability in
`calibrate_and_score` — ML weight 20% for favourites (odds ≤ 3), 40% otherwise
(`run_tips_pipeline.py:601-606`) — before market anchoring. See
[Scoring & output](09-scoring-and-output.md).

### Model artifacts

| Path | Producer | Consumer |
|---|---|---|
| `models/racing_ensemble_v2.pkl` | retrain_v2 | RacingMLModel (production) |
| `models/enhanced_racing_ensemble.pkl` | train_ml_enhanced | compare_features, mc_api (optional) |
| `models/isotonic_calibrator.pkl` | (fit offline) | run_tips_pipeline |
| `models/double_calibrator.pkl` | ml_model.train | RacingMLModel.predict_proba |
| `server/python/calibration_model.json` | mc_recalibration | mc_api |
| `models/drift_history.json` | feature_drift_monitor | drift reports |
| `models/registry/` | model_versioning | (unused so far) |

All model artifacts are git-ignored (`.gitignore:39-41,56`) — this published repo
contains code only.

---

## 7. Supporting machinery

- **`stacking_meta_learner.py`** — 5-fold OOF logistic-regression meta-learner over
  7 meta-features (3 base predictions + max/min/std/mean). Reports stacked-vs-average
  AUC improvement.
- **`predictability_meta_model.py`** — race-level model (not per-runner): predicts
  whether the *favourite* wins from 9 features (field size, class tier, wet, odds
  concentration HHI, market confidence…), yielding
  `highly_predictable / normal / chaotic` and a confidence modifier in [0.5, 1.2].
- **`model_versioning.py`** — a `ModelRegistry` (auto-incrementing `v{major}.{minor}`,
  one active version) plus a `ShadowTester` that promotes a shadow model only after
  ≥ 50 races and shadow AUC > primary + 0.02. No `models/registry/` exists yet —
  written but not adopted.
- **`feature_drift_monitor.py`** — snapshots feature importances after each train;
  computes JS divergence (green < 0.05, amber < 0.15, red ≥ 0.15), Kendall-tau rank
  stability, and red-flags concentration (barrier features > 0.35 of total
  importance, market > 0.40, temporal > 0.25).
- **`compare_features.py`** — training-vs-inference parity auditor: compares live
  `feature_snapshots` against `training_data` distributions, flags features whose
  matched-record relative difference exceeds 0.20, and emits a parity score.
- **`ml_status.py`** — CLI that prints `RacingMLModel.get_status()` JSON.

---

## 8. Known defects & quirks (verified in source)

- **Stacking fit bug — fixed.** `ml_model.train` previously called
  `stacking_learner.fit(X_train_bal, y_train_bal, …)` (`ml_model.py:317`) with
  variables that stopped existing when SMOTE was removed; the `NameError` was
  swallowed and stacking silently never activated. It now fits on
  `X_train_scaled, y_train` (verified with a synthetic end-to-end training run —
  the meta-learner fits and `predict_proba` routes through it). Artifacts trained
  *before* the fix carry no stacking learner, so nothing changes for the current
  production `racing_ensemble_v2.pkl`; the path activates only on future
  `ml_model.train` runs.
- **Focal loss is implemented but deliberately not wired** into any fit; the
  training metrics previously claimed `enabled: True`, which now truthfully reports
  `enabled: False` with a note (`ml_model.py:238-241`). Class imbalance is handled
  by native class weights instead.
- **OOF isotonic not applied at inference — by design** (see §4 for the
  double-calibration rationale).
- **Enhanced sub-models stubbed:** regional (NSW/VIC/QLD) and distance sub-model
  training exists but `main()` skips both with `'Skipped for simplicity'`
  (`train_ml_enhanced.py:1596-1613`).
- **Hardcoded Windows dev path** as `.env` fallback (`retrain_v2.py:36`).
- `retrain_v2` expects `intelligence/track_distance_profiles.json`; when absent the
  four `td_*` features silently default (0.5 / 0.2 / 0 / 0).
- Comments claiming "77 features" (`retrain_v2.py:143`) are stale — the list has
  grown to ~110.
- **No absolute AUC target** exists in code; quality is judged relatively (ablation
  deltas, shadow-promotion rule of +0.02 AUC).
- `ml_model.train` uses a **random** stratified 80/20 split (`ml_model.py:266`) —
  only retrain_v2 provides temporally sound training.

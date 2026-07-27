# Winner-Pattern Gap — Verdict Implementation

Implements the `feature_roadmap.md` / `synthesis_report.md` verdict as production model
features. Scope: **flagship + roadmap priority 1-4** (agreed scope). Integration:
**feature module + retrain/model wiring, no retrain executed** (agreed scope).

Prior to this, the `server/python/research/winner_pattern_gap/` package only *discovered
and documented* these gaps — none of the recommended features existed in the model's
`FEATURE_COLUMNS` (110 features). This change fills that gap.

## What was built

`server/python/winner_pattern_features.py` — fits prior tables from history and emits four
leakage-safe features. `server/python/tests/test_winner_pattern_features.py` — synthetic
tests (no DB) for the flag logic, lookups, jockey gating, fit gates, and serialisation.

| Roadmap item | Feature | Source finding | How it is computed |
|---|---|---|---|
| Rank 1 — `prior_pb_close_3to5_market_underreaction` | `prior_pb_close_underreaction` | S-001 (synthesis) | Binary flag: the runner's **previous** start was a personal-best last-200m close, prior finish 3rd–5th, and **today's** price is in 6.0–12.0. |
| Cohort fast-close flag | `cohort_fast_close_prior` | A-001, A-004, A-005, A-011… | `threshold − horse_prior_best_last200`, where `threshold` is the fitted 25th-percentile `last_200m_time` for the (track × distance × going × class) cohort. Positive = prior closing beats the fast-close bar. |
| 400m position priors | `pos400_win_prior` | A-002, A-006, A-008… | Win-rate uplift (pp) for the horse's **usual** 400m in-run position bucket (from prior starts) within today's (track × distance × going) cohort. |
| Jockey wet residual | `jockey_wet_residual` | B-001…B-005 | Today's jockey's (wet − dry) strike-rate delta (pp), applied only when today's going is Soft/Heavy; neutral (0) otherwise. |

Bucketing (`distance_bucket`, `normalize_going_group`, `class_bucket`,
`classify_position_bucket`, `derive_marker_position`) is imported from
`research.winner_pattern_gap` so the priors are fit on exactly the cohorts the verdict was
measured on.

## Leakage safety (design invariant)

Every feature is a function of a runner's **prior** starts plus fields known before the
jump (today's track, distance, going, class, price, jockey). No same-race sectional or
finishing information enters a runner's own feature value:

- `prior_pb_close_underreaction` uses `position.shift(1)` and `last_200m_time.shift(1)` with
  `expanding().min().shift(2)` for the "prior best before the previous run" — identical to
  the flagship test in `synthesis.py`.
- `cohort_fast_close_prior` uses `expanding().min().shift(1)` (best of strictly-prior starts).
- `pos400_win_prior` uses `expanding().mean().shift(1)` of prior 400m positions.
- A horse's first start therefore yields `NaN` priors by construction (verified in tests).

The fitted *priors* (cohort thresholds, position uplifts, jockey deltas) are aggregate
lookups over the whole training window — the same treatment the existing
strike-rate / track-bias features already receive.

## Model wiring (no retrain)

- `retrain_v2.py`
  - Added the four names to `FEATURE_COLUMNS` (110 → 114).
  - `cohort_fast_close_prior` and `pos400_win_prior` added to `NAN_PRESERVE_FEATURES`
    (missingness is informative); the flag and jockey residual stay non-sectional (fill 0).
  - `load_training_data` calls `winner_pattern_features.attach_features(df)` in a guarded
    `try/except` — on any failure the frame is returned unchanged, so a retrain can never
    be broken by this hook.
  - `build_feature_matrix` copies the four columns through when present.
- `ml_model.py` — same four names appended to the class `FEATURE_COLUMNS` for schema
  lockstep (verified identical order to `retrain_v2.py`).

**The current live model is unaffected.** `RacingMLModel` predicts using the feature list
stored in the trained artifact (`_trained_feature_columns`), so the existing 110-feature
pkl continues to serve exactly as before. The four features only take effect on the next
retrain.

## How to fit / inspect the priors now

```bash
cd Race-Analytics
export $(grep -v '^#' .env | xargs)
python3 server/python/winner_pattern_features.py \
  --date-from 2024-01-01 --date-to 2026-03-26 \
  --out server/python/intelligence/winner_pattern_priors.json
```

Run the tests:

```bash
.venv/bin/python server/python/tests/test_winner_pattern_features.py
```

## Deliberately deferred (out of agreed scope)

- **No retrain / no promotion.** Per CLAUDE.md, a retrain must stage an artifact and be
  validated (AUC vs the 0.8000 baseline) before promotion. Not done here.
- **Inference-time feature builder.** Live prediction does not yet compute these four
  columns per runner; that belongs to the retrain-and-deploy step. Until then a future
  retrained model would see them populated at train time via `attach_features`.
- **Roadmap ranks 5+** (barrier × field × wet, pace-role, price-bracket residual,
  favourite vulnerability, cross-signal composites) — not in the agreed priority 1-4 scope.

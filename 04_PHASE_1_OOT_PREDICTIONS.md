# 04 — Phase 1: Historical Out-of-Time Predictions

## Purpose

Produce, for every race in the usable era, the win probabilities each model
**would have emitted at the time** — trained only on races strictly before
the target race. These are the only predictor outputs the decision layer may
ever train on (global rules 8, 16-B "train the decision layer on in-sample
probabilities" is forbidden).

Offline only.

---

# Design

- Reuse `retrain_v2.py`'s walk-forward machinery (it already builds
  chronological CV folds) rather than writing a second trainer. The
  `--dry-run-data-contract` flag from Phase −1 is the model for adding a
  `--emit-oot-predictions` mode.
- Cutoff convention: a model scoring race day D may be trained on rows with
  `race_date < D` only. Rolling refit cadence (e.g. weekly cutoffs) is a
  config value, recorded in the artifact metadata.
- Output: one table/parquet artifact `oot_predictions` keyed by
  `canonical_race_key` + `normalize_runner_key`, with columns:
  `p_xgb, p_lgb, p_cat, p_ensemble, training_cutoff, data_build_id,
  code_commit, feature_schema_version`.
- The ensemble is composed **from the OOT component predictions** via the
  same combination logic as production (`predict_components` in
  `ml_model.py` makes the components inspectable).

---

# Acceptance Criteria

- [ ] `test_no_prediction_trained_on_target_date`: PASS — for every output
      row, `training_cutoff < race_date`.
- [ ] Determinism: two runs at the same commit and data build produce
      identical artifacts (hash-compared). MC is not involved here; any
      nondeterminism is a defect.
- [ ] Coverage report: races in the usable era with predictions vs without,
      with reasons (insufficient training history, missing features).
- [ ] Artifact metadata complete per 16_…PROTOCOL §Artifact Protocol.
- [ ] Regression guard: production prediction outputs unchanged (this phase
      adds a mode; it must not alter live scoring — assert by running the
      existing prediction tests).

# Stop Conditions

- Training cutoffs cannot be faithfully reconstructed (e.g. a feature is
  computed from a table that has been mutated in place since the race ran).
  Record which features are affected; they either get an as-of source or
  are excluded from the decision-learning feature set.

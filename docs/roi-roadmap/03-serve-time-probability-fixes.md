# 03 — Serve-time probability fixes: NaN semantics, inverted interaction, one feature builder

**Wave:** 1 · **Depends on:** nothing · **Blocks:** [05](05-calibrator-and-normalisation.md), [12](12-retrain-rebaseline.md) · **Risk:** low (flag-gated, backtest-verified) · **Type:** bugfix

## Goal

Live inference serves features with the same semantics the model was trained on.
Three verified defects currently make production probabilities different from — and
in one case sign-flipped against — the trained model.

## Why (evidence)

1. **NaN destruction.** Training deliberately preserves NaN so trees route
   missingness (`retrain_v2.py:684`, `:120-123`); the tips pipeline even sets
   `runs_since_peak = NaN` deliberately (`run_tips_pipeline.py:2294-2295`). Then
   `prepare_features` executes `.fillna(0)` on **every** column (`ml_model.py:216`),
   converting "no sectional data" into `z_200m = 0` (exactly field-average) for
   ~half of all runners and "no barrier trial" (sentinel 999/NaN) into 0 (= trial today).
2. **Inverted interaction served by default.** `barrier_x_pace_inv` is trained as
   `adv × pace` (`retrain_v2.py:662`) but served as `adv × (1 − pace)`
   (`run_tips_pipeline.py:2328`); the fix exists behind `STRIDE_INTERACTION_PARITY`
   which defaults **off** (`run_tips_pipeline.py:2316`;
   `feature_interactions.py:9-18`). Maximally wrong in high-pace races.
3. **Two inference paths disagree.** `mc_api.py` populates ~13 market-movement
   features from real movement (`mc_api.py:2046-2055`, `:5960-5969`, `:1408-1416`);
   `run_tips_pipeline.py` serves 0 for the same columns (`run_tips_pipeline.py:2257-2291`
   omits them; `ml_model.py:218`). Training never computes them either
   (`retrain_v2.py:183-213`, zero-filled at `:680-682`) — trees trained on all-zero
   columns are served two different live distributions depending on entry point.

## Scope

**In:** NaN-preserve contract in the shared feature builder; interaction parity
verification + flag flip; single feature-builder used by both inference paths
(movement features stay 0 until [14](14-late-odds-features.md) — the point here is
both paths serve the *same* values).
**Out:** computing real movement features for training (→ [14](14-late-odds-features.md));
recalibration (→ [05](05-calibrator-and-normalisation.md), [12](12-retrain-rebaseline.md)).

## Steps for Kimi Code

1. **NaN-preserve contract.** In `ml_model.py` `prepare_features` (:216), replace the
   blanket `.fillna(0)` with a preserve-list: sectional/z-score features, trial
   features, `runs_since_peak`, and anything in the trainer's NAN_PRESERVE set pass
   NaN through; only genuinely numeric-complete columns are filled. Source the list
   from one constant shared with `retrain_v2.py` (`NAN_PRESERVE_FEATURES`) — single
   definition, imported by both.
2. **Parity test (new, `server/python/test_feature_parity.py`).** Build a synthetic
   runner with known values; assert the served feature vector equals the training
   builder's vector for: sectional NaN case, no-trial case, `barrier_x_pace_inv`
   under both pace regimes, and all PHASE2 columns. This test must fail before the
   fix and pass after.
3. **Interaction parity.** Verify `adv × pace` at serve; run the metro backtest with
   `STRIDE_INTERACTION_PARITY=true` vs false; if parity is not worse on calibrated
   Brier/log-loss (expected: better), flip the default to true and keep the env var
   for rollback. Record the comparison JSON in the PR.
4. **Single feature builder.** Extract the feature-assembly code from
   `run_tips_pipeline.py:2257-2295` into `server/python/serve_features.py` and make
   `mc_api.py` call it for the shared columns (movement columns remain populated
   only in mc_api; the shared builder explicitly zeroes them with a
   `# sourced post-tip-time — see task 14` comment). Delete the duplicated list.
5. **Zero-feature decision (temporary).** Until [14](14-late-odds-features.md) lands,
   force the movement columns to 0 in *both* paths (they are zero in training) so
   train and serve distributions match. Log once per race day that they are inert.

## Acceptance criteria

- [ ] `pytest server/python/test_feature_parity.py` passes.
- [ ] Backtest comparison (parity on vs off) attached to PR; default flipped only if
      not worse.
- [ ] Grep proves one builder: `run_tips_pipeline.py` and `mc_api.py` both import
      `serve_features.build_feature_row` (or equivalent) for the shared columns.
- [ ] A runner with no sectional data no longer produces `z_200m == 0.0` at serve
      (log/audit query demonstrating NaN passthrough).

## Rollout & flags

- Flags: `STRIDE_INTERACTION_PARITY` (flip after step 3), `STRIDE_SERVE_NAN_CONTRACT=true`
  (new; default off for one shadow week, then on).
- Rollback: both flags off restores old behaviour exactly (keep old code path behind
  the flags for one release).

## Guardrails

- Do **not** "improve" feature values while here (no new scaling, no clipping) —
  semantic parity only. Any improvement is a separate, backtested change.
- Do **not** compute movement features for training in this task — that is
  [14](14-late-odds-features.md) and requires [04](04-as-of-odds-snapshot.md) data.
- Keep the temporal guarantees: nothing in this task may introduce data from after
  prediction time.

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §2 Phase-A serve-time bugs
- Downstream: [05](05-calibrator-and-normalisation.md) refits the calibrator on the
  corrected serve output; [12](12-retrain-rebaseline.md) retrains with the contract
  enforced.

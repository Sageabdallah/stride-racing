# 12 — Retrain & re-baseline: morning-odds features, honest folds, per-race metrics, persisted ensemble

**Wave:** 4 — only after ≥4–6 weeks of [04](04-as-of-odds-snapshot.md) data · **Depends on:** [03](03-serve-time-probability-fixes.md), [04](04-as-of-odds-snapshot.md), [05](05-calibrator-and-normalisation.md), [06](06-staking-and-risk-controls.md) (staking context), [09](09-forward-validation-protocol.md) (promotion gates) · **Blocks:** [13](13-race-aware-objective.md), [14](14-late-odds-features.md) · **Risk:** high (full re-baseline) · **Type:** model

## Goal

Retrain on features the model will actually have live (tip-time odds, not SP),
with fold hygiene that makes reported metrics honest, per-race hit-rate as a
first-class metric, and a learned (persisted) ensemble combination. Re-publish
every headline number against the favourite baseline.

## Why (evidence)

- **SP→serve skew:** training maps `sp_odds→market_odds` (`retrain_v2.py:142-144`,
  `:560-565`; view at `refresh_training_view_v2.py:216`); live serves morning prices.
  Every offline number is flattered ([04](04-as-of-odds-snapshot.md)).
- **Fold hygiene:** fold isotonic fitted on the test fold's own predictions and
  LightGBM early-stops on the test fold (`retrain_v2.py:835-873`); published Brier
  0.0834 is mildly self-fitted. Final-model XGB/CatBoost get eval_set but no
  early_stopping (:1164-1168, :1188).
- **Wrong CV metric:** only pooled AUC/Brier reported (`retrain_v2.py:1011-1019`) —
  dominated by cross-race separation the market feature supplies; ablations decided
  on ±0.001 AUC against fold std 0.044. Per-race top-1 hit rate (what the product
  sells) is never measured (the H2H harness exists only in `rank_model.py`).
- **Unvalidated production blend:** inference weights are hardcoded seed counts
  (`ml_model.py:59-63`, `:512-526`; `update_model_performance` :528 has zero callers);
  CV scored an equal-weight mean (`retrain_v2.py:922`) — a *different* blend than
  production. The stacking meta-learner is fitted (:320-325) but **never pickled**
  (:635-645) — dead after reload.
- **Stage-mismatch calibration:** largely fixed by [05](05-calibrator-and-normalisation.md);
  this task consolidates to one final-stage calibrator.

## Scope

**In:** training-view switch to tip-time odds; fold-hygiene fixes; per-race metrics
in CV; learned ensemble weights + persisted meta-learner; final-stage calibration;
re-derived gates via [09](09-forward-validation-protocol.md).
**Out:** new objective functions (→ [13](13-race-aware-objective.md)); movement
features (→ [14](14-late-odds-features.md)).

## Steps for Kimi Code

1. **Training view switch.** `refresh_training_view_v2.py`: `market_odds` now sources
   `tip_time_odds` from [04](04-as-of-odds-snapshot.md) where present; rows without a
   tip-time snapshot are **excluded** from training once coverage begins (do not
   fall back to SP — a mixed feature is worse than a smaller dataset). Keep
   `sp_odds` as a settlement/CLV-only column. `odds_source` becomes a model feature
   only if it varies (it shouldn't after the switch).
2. **Fold hygiene.** In `retrain_v2.py` `train_single_fold`: carve an early-stop/
   calibration window from the **tail of the train window**; the test fold is
   untouched by isotonic fitting and early stopping. Add `early_stopping_rounds`
   to final XGB/CatBoost fits using the held-out tail. Keep `DateWindowSplitter`'s
   14-day purge (:695-762) — it is correct.
3. **Per-race metrics in CV.** `run_walk_forward_cv` output adds, per fold and pooled:
   per-race top-1 hit rate, **tip-time**-favourite baseline hit rate on the same
   races (SP favourite printed as a hindsight diagnostic only — selection uses
   tip-time price, [09](09-forward-validation-protocol.md)), same-race H2H vs the
   stored production model (reuse `rank_model.py`'s harness), and per-race
   normalised log-loss. **Staging criterion** (this decides which candidate is
   staged; the live *promotion* rule is [12-preregistration.md](12-preregistration.md)
   NEW-BEATS-OLD, see its 2026-09-05 amendment): honest OOF Brier not worse on
   identical folds, top-1 hit rate above the tip-time favourite, H2H not lost.
   AUC remains a diagnostic.
4. **Learned, persisted ensemble.** Fit per-category weights or the stacking
   meta-learner on the purge-gapped OOF predictions; persist inside
   `racing_ensemble_v2.pkl` (fix `ml_model.save` :635-645 to include
   `stacking_learner`, calibrators, `target_encoder`); route `predict_proba`
   (:566-578) through it; delete the seed-count dict (:59-63).
5. **One final-stage calibrator.** Fit isotonic on the OOF *final published
   probability* (post-blend, post-anchor, post-renormalisation from
   [05](05-calibrator-and-normalisation.md)); remove intermediate calibration layers so each
   probability is calibrated exactly once. Keep provenance sidecars.
6. **Re-baseline + re-register.** Publish new headline metrics (AUC, honest Brier,
   per-race hit vs favourite, calibration bands) in README replacing the
   SP-contaminated table — with [02](02-backtest-statistics.md) CIs. Register new band
   hypotheses in [09](09-forward-validation-protocol.md)'s registry (thresholds re-derived,
   since edge definitions moved); forward-validate before quoting ROI.

## Acceptance criteria

- [ ] Training view contains zero SP-derived `market_odds` rows post-switch (query).
- [ ] CV report shows per-race hit rate, favourite baseline, and H2H — promotion
      decision recorded against the criterion.
- [ ] Fresh process load of the artifact → identical predictions to the training
      process (pickle-completeness test); stacking branch reachable.
- [ ] Honest OOF Brier published with CI; README table replaced; old numbers moved
      to a "superseded (SP-contaminated)" appendix with dates.
- [ ] Registry entries for all production bands; nothing quoted without a PASS.

## Rollout & flags

- Staged artifact promotion (existing discipline): `racing_ensemble_v3.pkl` beside
  v2, env pointer, one-week parallel scoring, then switch.
- Rollback: point back to v2 artifact.

## Guardrails

- Never mix SP and tip-time odds in one feature column.
- Do not "rescue" a failing re-baseline by tuning on the validation window —
  honest numbers are the deliverable, even if worse. If the re-baselined model
  shows no edge, the correct output is fewer/no bets until [13](13-race-aware-objective.md)/[14](14-late-odds-features.md) land.
- Keep the purge gap and race-grouped folds intact in any new split code.

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §2 (A1), §4 (C1, C3, C4)
- Prerequisites: [03](03-serve-time-probability-fixes.md) · [04](04-as-of-odds-snapshot.md) · [05](05-calibrator-and-normalisation.md)
- Next: [13](13-race-aware-objective.md) · [14](14-late-odds-features.md)

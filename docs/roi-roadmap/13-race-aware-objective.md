# 13 — Race-aware objective: exploit one-winner-per-race structure

**Wave:** 4 · **Depends on:** [12](12-retrain-rebaseline.md) (honest baseline + per-race metrics exist) · **Blocks:** none · **Risk:** medium (promotion only via H2H criterion) · **Type:** model

## Goal

Directly optimise what the product sells — within-race ordering — instead of only
pooled runner-level classification. Horse racing is fixed-sum (exactly one winner
per race); the current pointwise binary trainers never exploit that structure.

## Why (evidence)

- All three trainers fit per-runner binary classifiers (`retrain_v2.py:832/847/867`,
  `ml_model.py:292-299`, `train_ml_enhanced.py:1130`).
- The only race-aware attempt, `rank_model.py` (LGBMRanker), **failed** its H2H
  (33.6% vs stored model 39.7% vs favourite 34.4% — `docs/12` §5.4) and was
  correctly rejected. That failure invalidates *that configuration*, not the
  approach — the repo already plans the retry: CatBoost `QuerySoftMax` (M1/T13).
- Free interim win: per-race softmax renormalisation of ensemble scores before
  picking the top tip (post-[05](05-calibrator-and-normalisation.md) fields sum to 1, but the
  *selection* can still be improved by ranking on within-race relative scores).
- The conditional-logit blend module exists but is on HOLD (fit α=18.7/β=−17.85
  wants to subtract the market; log-loss degrades — `docs/12` §5.2/§5.3). Respect
  the hold unless the [12](12-retrain-rebaseline.md) market-double-count ablation changes the picture.

## Scope

**In:** softmax-selection quick win; CatBoost `QuerySoftMax` listwise arm as a
fourth ensemble voter; same-race H2H promotion criterion.
**Out:** replacing the three base learners; NN approaches (standing prohibition).

## Steps for Kimi Code

1. **Softmax selection (quick win, flag `STRIDE_SOFTMAX_PICK`).** Select each race's
   top tip by argmax of the within-race softmax of ensemble scores (temperature
   fitted on validation log-loss) rather than raw calibrated prob; calibrated probs
   still drive edge/EV. Backtest: top-pick hit rate vs baseline must not decrease.
2. **Listwise arm.** Train CatBoost `QuerySoftMax` (fallback: LGBMRanker with race
   groups) on the [12](12-retrain-rebaseline.md) feature set with race-id groups, same
   purge-gapped splits. Evaluate with the same-race H2H harness vs the v3 ensemble
   and the favourite baseline — the exact protocol that rejected `rank_model.py`.
3. **Integration (only if H2H wins).** Add as a fourth voter whose weight is learned
   on OOF ([12](12-retrain-rebaseline.md) machinery), or use it strictly as an ordering
   overlay for pick selection while calibrated v3 probs drive EV.
4. **Market-double-count ablation (registry experiment).** Retrain minus the 12
   market features → refit CL → if β>0, double-count confirmed; adjust the `mw`
   ladder accordingly (this is the repo's planned experiment — run it now that
   [12](12-retrain-rebaseline.md) provides honest folds).

## Acceptance criteria

- [ ] Softmax-pick backtest (hit rate, no EV degradation) attached; flag promoted
      only if neutral-or-better.
- [ ] Listwise H2H report in the same format as the `rank_model.py` evaluation;
      explicit REJECT if it loses (a documented negative result is success here).
- [ ] If integrated: artifact includes the fourth voter; pickle-completeness test
      passes; promotion gate from [12](12-retrain-rebaseline.md) satisfied.

## Rollout & flags

- Flags: `STRIDE_SOFTMAX_PICK`, `STRIDE_LISTWISE_VOTER` (both default off until
  criteria met). Rollback: flags off.

## Guardrails

- The promotion criterion is the H2H, not AUC — a listwise model that wins AUC but
  loses the same-race H2H is rejected.
- Do not un-hold the conditional-logit blend without the ablation in step 4.
- Do not add a fifth/seventh voter ad hoc — voters enter only through OOF-learned weights.

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §4 (C2)
- Baseline: [12](12-retrain-rebaseline.md) · Metrics: [02](02-backtest-statistics.md)

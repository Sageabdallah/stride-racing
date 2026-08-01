# 14 — Late-odds & market-movement features: from constant-zero to the best-documented signal

**Wave:** 4 · **Depends on:** [04](04-as-of-odds-snapshot.md) (≥8–12 weeks of baseline→tip→late series), [12](12-retrain-rebaseline.md) (honest retrain machinery) · **Blocks:** none · **Risk:** medium · **Type:** model/feature

## Goal

Convert the ~13 market-movement features — currently constant-zero in training and
split-brained at serve — into real, train-time-computed features from the captured
odds series, including the T−5min smart-money window the literature (and the repo's
own research, ranked #1) identifies as the strongest single feature addition.

## Why (evidence)

- Movement columns (`is_steam_move`, `is_drift`, `odds_movement_pct`,
  `market_trend_*`, `steam_velocity`, `drift_velocity`, `late_move_indicator`,
  `market_confidence`, `relative_move`, `smart_money_score`, `is_insider_signal`,
  `field_market_agreement`, `last_start_market_diff`, `avg_market_diff_3runs`) are
  in the contract (`retrain_v2.py:183-188,206-213`) but never computed in training
  → zero-filled (`retrain_v2.py:680-682`). At serve, `mc_api.py` populates some from
  real movement while `run_tips_pipeline.py` serves 0 (see [03](03-serve-time-probability-fixes.md)).
- The existing steam/drift signal measures overnight→8am only
  (`odds_movement.py:410-425`) — mostly opening-market noise; the validated window
  is the final 30 min (`docs/12` §5.5; `market_velocity.py:261` `final_30min_move`
  exists but is never fed, `:341-344`).
- Thresholds (STEAM ≥+20% / DRIFT ≤−8% etc., `odds_movement.py:211-222`; a second
  taxonomy in `market_velocity.py:57-67`) are hand-set, never fitted or validated.
- JRA 894k-runner study cited in `research/report.md`: late-move effect ~14× the
  cross-sectional feature effect. Phase-5 ablation proved *re-encodings* of the
  same price add nothing (−0.0012 AUC) — only **new temporal information** can.

## Scope

**In:** training-time computation of movement features from [04](04-as-of-odds-snapshot.md)'s
series; one taxonomy; fitted (not hand-set) thresholds or raw continuous features;
feature-importance/ablation under [12](12-retrain-rebaseline.md)'s honest CV.
**Out:** exchange WOM/liquidity features (needs [10](10-execution-and-pricing.md) spike outcome).

## Steps for Kimi Code

1. **Training computation.** New builder `server/python/movement_features.py`:
   for each runner, from `runner_odds_snapshots` ([04](04-as-of-odds-snapshot.md)): baseline→tip
   change %, tip→late change %, late move (T−5 vs T−60 where coverage allows),
   signed velocity, field-level agreement (share of runners firming), and the
   runner's move vs field median. All as-of tip-time or earlier for tipping;
   T−5 features are for the **retrained model's live scoring at T−5** — define
   explicitly which inference moment each feature serves (comment + test).
2. **One taxonomy.** Delete the second hand-set taxonomy; raw continuous features
   preferred over thresholded booleans; any kept booleans get thresholds fitted on
   window-A data only ([09](09-forward-validation-protocol.md) rules).
3. **Backfill discipline.** Movement features are NULL (not 0) before capture start
   — NaN-preserve contract from [03](03-serve-time-probability-fixes.md) applies; rows
   before capture start are excluded from the movement-ablation training set, not
   zero-filled.
4. **Ablation under honest CV.** Three arms on identical purge-gapped folds:
   v3 (no movement), v3+overnight movement, v3+overnight+late. Metrics: per-race
   hit rate, H2H, Brier — per [12](12-retrain-rebaseline.md)'s promotion criterion.
   Ship only arms that win.
5. **Serve unification.** Both inference paths call `movement_features.py` (extends
   [03](03-serve-time-probability-fixes.md)'s single builder); delete the inline mc_api
   population (`mc_api.py:2046-2055`, `:5960-5969`, `:1408-1416`).

## Acceptance criteria

- [ ] Ablation report on identical folds with the [12](12-retrain-rebaseline.md) promotion
      criterion; documented REJECT if no arm wins (a valid outcome).
- [ ] Zero constant-zero columns in the movement block of the training matrix
      (query proving variance > 0 post-capture).
- [ ] One movement-feature module imported by both inference paths (grep).
- [ ] NaN (not 0) for pre-capture rows, verified in the training extract.

## Rollout & flags

- Ship behind artifact promotion (`racing_ensemble_v4.pkl`) if the ablation wins;
  rollback = v3 pointer.

## Guardrails

- T−5 features may only serve a T−5 inference run — never leak them into tip-time
  (morning) scoring. Encode the inference moment in the feature-name registry.
- Never fabricate movement history; prospective data only.
- Do not widen odds-snapshot capture scope in this task (that was [04](04-as-of-odds-snapshot.md)).

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §4 (C5)
- Data: [04](04-as-of-odds-snapshot.md) · Machinery: [12](12-retrain-rebaseline.md) · Serve: [03](03-serve-time-probability-fixes.md)

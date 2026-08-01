# 05 — Calibrator stage fix + per-race renormalisation

**Wave:** 2 · **Depends on:** [03](03-serve-time-probability-fixes.md) (correct serve semantics first) · **Blocks:** [12](12-retrain-rebaseline.md) · **Risk:** medium (changes published probabilities — shadow first) · **Type:** bugfix/calibration

## Goal

The production calibrator is fitted on the quantity it actually calibrates, and
published per-race probabilities sum to 1. Today it is fitted on one engine's
output and applied to another's, and the market anchor leaves fields unnormalised.

## Why (evidence)

- `ProbabilityCalibrator` is applied to the Monte-Carlo `winPercentage`
  (`run_tips_pipeline.py:657-661`) but its only producer fits it on OOF predictions
  of the v1 ML ensemble (`fit_calibrator.py:226-235`) — different engine, different
  training hygiene (v1 uses random splits, `ml_model.py:271-273`). Apples→oranges.
- After calibration, the ML blend (20/40% by odds, `:667`) and the `mw` market
  anchor (:679-693) leave published `winPercentage` values that **do not sum to 1
  within a race**; no renormalisation follows (mc_api renormalises its own MC spine
  at `mc_api.py:7612-7617`, the tips pipeline never does). Edge = calibrated −
  `trueMarketProb` (:697) is then compared across horses on inconsistent scales.
- mc_api's standalone edge additionally uses raw implied odds with **no overround
  correction** (`mc_api.py:7634-7636`) — inconsistent with the pipeline's corrected
  `true_market` (:673-674).
- Edge is post-shrink: reported `modelEdge = mw·(model−market)` — any threshold was
  implicitly tuned on shrunk edges; note this when re-deriving gates in [12](12-retrain-rebaseline.md).
- The repo plans this: A3/T17 (renormalisation), A1/T18 (calibrator placement),
  A2/T19 (temperature/beta swap).

## Scope

**In:** refit the production isotonic on the correct stage quantity; per-race
renormalisation after the anchor; align mc_api's edge to the corrected market prob;
shadow comparison before promotion.
**Out:** the full final-stage calibration redesign (→ [12](12-retrain-rebaseline.md));
Shin/power de-vig (→ [10](10-execution-and-pricing.md)).

## Steps for Kimi Code

1. **Refit target.** Change `fit_calibrator.py` so the pipeline calibrator is fitted
   on OOF **MC winPercentage** (or, if MC OOF isn't available yet, on the final
   published probability assembled from components using the recorded
   `final_win_prob` audit — check coverage first; `docs/12` §4c notes the OOS logging
   was broken for 15+ months, so MC-OOF from `realistic_simulate.py` backtests is
   the realistic source today). Keep the race-span fold refusal (:88-92) and
   provenance sidecars (`calibration_model.py:57-71`).
2. **Renormalisation.** After the `mw` anchor (`run_tips_pipeline.py:679-693`), add
   per-race normalisation: `winPercentage_race_i /= Σ field`. Gate behind
   `STRIDE_RENORMALISE_FIELD` (default off). Edge and EV consume the renormalised value.
3. **mc_api consistency.** Make `mc_api.py:7634-7636` use the same
   overround-corrected `trueMarketProb` helper as the pipeline — extract the
   correction into one shared function (`server/python/market_prob.py`), imported
   by both.
4. **Shadow week.** Run both calibrators (current + refit) and renormalisation
   on/off for ≥5 race days, logging both published values per runner (new audit
   columns, additive). Compare: calibration-bin alignment (your README band table
   format), Brier, and per-race probability sums.
5. **Promote** the refit calibrator + `STRIDE_RENORMALISE_FIELD=true` if shadow
   Brier/log-loss is not worse and field sums = 1.0 ± 1e-6. Attach the comparison
   to the PR.

## Acceptance criteria

- [ ] Calibrator artifact metadata (`calibration_model.py` sidecar) records the
      stage quantity it was fitted on (`mc_win_percentage_oof` or `final_prob`),
      fold dates, and sample count.
- [ ] With the flag on, every published race's probabilities sum to 1.0 ± 1e-6
      (audit query in PR).
- [ ] Shadow comparison JSON: Brier/log-loss old vs new over ≥5 days; decision recorded.
- [ ] `grep` proves one shared market-prob function used by both entry points.

## Rollout & flags

- Flags: `STRIDE_RENORMALISE_FIELD` (new), new calibrator artifact path
  (`isotonic_calibrator_v2.pkl` — staged promotion, never overwrite v1).
- Rollback: flags off + point back to v1 artifact.

## Guardrails

- Do not fit any calibrator on data from after the race it calibrates (temporal
  rule; the existing fold-refusal logic is the pattern).
- Do not change the `mw` ladder values here — that's [12](12-retrain-rebaseline.md)'s
  final-stage redesign; this task fixes *what* is calibrated and normalisation only.
- Do not silently drop runners whose renormalised prob changes tier — log tier
  transitions during the shadow week.

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §2 Phase-A, §4 (C4)
- Prerequisite: [03](03-serve-time-probability-fixes.md) · Feeds: [12](12-retrain-rebaseline.md)
  (one final-stage calibrator), [10](10-execution-and-pricing.md) (de-vig method swap
  lands in the same `market_prob.py` helper).

# Audit verification — 2026-09-06

An external audit of this repository at `16439a6` reported 4 HIGH, 8 MEDIUM and
7 LOW findings. This record is the result of checking every one of them against
the code, and by execution where the claim was numerical. Nothing below was
changed because the audit said so; each change cites the evidence that justified
it, and each finding left alone says why.

Baseline: `python -m pytest server/python` at `16439a6` on Linux/py3.11 —
**991 passed**. The audit's 6 failures were Windows-locale and pid-reuse
artefacts (its own diagnosis, confirmed: L7 below).

## Verdicts

| # | Claim | Verdict | Evidence | Action |
|---|---|---|---|---|
| H1 | ML ensemble's OOF isotonic calibrators fitted at training, never applied at serve; the comment blaming "double calibration" is wrong about where the pipeline calibrator sits | **True** | `predict_components` calls raw `predict_proba`; `calibrate_and_score` applies `ProbabilityCalibrator` to the MC `winPercentage` *before* the ML blend. Execution check with the trainer's own params (`scale_pos_weight=9` etc.) on a 15% base rate: raw ensemble mean **40%**, calibrated **14%**. **New fact the audit missed:** CatBoost's `__getstate__` drops the `_isotonic` attribute, so existing artifacts carry only the XGB and LGBM calibrators. Also: the stacking/double-calibrator branches are unreachable because `save()` never persists them, not only because `load()` does not restore them. | `save_model` persists `oof_calibrators`; `STRIDE_ML_APPLY_ISOTONIC` (default off) applies a complete set at serve; misleading comment and docs/05 note corrected; pipeline logs serve status once per run. Flip only after a shadow-week A/B — it re-levels every published probability. |
| H2 | mc_api monkey-patches `psycopg2.connect` process-wide with an autocommit pool; the pipeline's `store_selections_in_db` loses its transaction; wrapper swallows `autocommit = False` | **True** | `run_mc_simulation` execs `mc_api.py` in-process; the patch runs at module level; `db_connect` calls `psycopg2.connect` afterwards. `_SharedDbConnection` had no `__setattr__`. **New fact:** the per-insert `conn.rollback()` would have been wrong on a real transaction too — it would discard the deactivation and every earlier insert, then commit the remainder beside the still-active old picks. Removing the patch outright is not safe: `fitness_peak`, `jockey_momentum`, `form_franking` and `empirical_barriers` each open their own connection per runner and rely on the pool. | Pool kept, made honest (`__setattr__` forwarding, psycopg2-like `__exit__`, real driver published as `__wrapped__`). `db_connect(transactional=True)` peels the pool off; `store_selections_in_db` runs one transaction with a savepoint per pick, and a failed commit is reported as an abort instead of "Stored N". |
| H3 | Track-bias multiplier reads the wrong scale and "inverts" the signal | **True in substance, overstated in wording** | `calculate_total_points` returns −18…+49 (the audit said −33), fed into `/100`. Direction stays monotone-positive; the realised effect is a uniform ×0.95–1.00 shrink with a 0.5% tilt, not an inversion. Every mc_api-scored runner has the key (0 when unscored), so the "missing → ×1.00 beats present" case only applies to runners mc_api never saw. Already recorded in `SYSTEM_MAP.md §7b.3` / `IMPLEMENTATION_PLAN.md T20`. | `_context_multipliers` behind `STRIDE_CTX_MULT_BIAS` (default off): neutral ×1.00, ≥25 pts ×1.05, ≤−25 ×0.95. Flags-off output compared byte-identical over 100 synthetic races. |
| H4 | Fitness multiplier is dead (reads a top-level key only ever published nested, 0–1 scale) | **True** | Only producer: `mc_api` → `fitnessData.fitnessReadinessScore`. Consumer reads top level, default 50 ⇒ ×1.00. Already in `SYSTEM_MAP.md §7b.1`. The jockey multiplier is inert the same way (§7b.2), which the audit did not list. | `STRIDE_CTX_MULT_FITNESS` reads the nested 0–1 value; `STRIDE_CTX_MULT_JOCKEY` reads a `jockeyMomentumAdjustment` that mc_api now publishes (additive); `STRIDE_CTX_MULT_DIAG` prints realised min/mean/max per race. |
| M1 | Every `STRIDE_FIX_*`/`STRIDE_MC_FIX_*` flag defaults off and production sets none | **True mechanically; by design** | Flags default off; `infra/` and workflows set only `STRIDE_SERVE_LIVE_FEATURES_SHADOW` and `STRIDE_MODEL_WEIGHT`. This is the #124 staged-rollout discipline (one flag per defect, flag-off byte-identical, A/B before promotion). The README-drift claim is overstated: README's `edge = true_win_prob − fair_market_prob` is exactly what the pipeline's `modelEdge` computes (de-vigged `trueMarketProb`); only mc_api's standalone `edge` field is raw-implied without `STRIDE_SHARED_MARKET_PROB`. | None. Promotion of each flag is an A/B decision, not a code change. |
| M2 | `analyze()` adds `bias_adj` post-softmax without renormalising | **True** | `model_prob = clip(base_prob + adj)` with no renorm; docs/06 §82 says "renormalized". The MC sim renormalises internally, so `win_sim` is fine; the analysis-row `model_prob`/`value_edge`/`ev` are not. | None (engine probability change — needs its own `STRIDE_MC_FIX_*` flag and A/B under the #124 discipline; recorded here as the next candidate). |
| M3 | Base-engine edge uses raw implied `1/sp`, no overround removal | **True** | `market_prob = 1 / runner.sp`. Feeds `mc_selection_score`/`mc_is_playable` via the analysis row; the pipeline's own `modelEdge` is de-vigged. | None (same reason as M2; the de-vig helper to use is `market_prob.true_market_prob_pct`). |
| M4 | Kelly overlay defeats the caps; LIMIT with money attached; two Kelly fractions; dead `'CAPPED'` | **True** | `stake = max(stake, kelly*bankroll)` after `units` was capped to 0 ⇒ LIMIT with stake > 0. On the production path through `TipGenerator` inside mc_api, but the pipeline stakes from its own `compute_staking`, so money impact is confined to the engine-tips output. Half-Kelly for `fractional_kelly` is acknowledged in `mc_api.py:498`; `'CAPPED'` is an unreachable string. | LIMIT now zeroes stake and kelly_pct. The overlay itself (Kelly may exceed the unit stake for a live BET) is left: the caps are in units, and whether Kelly may size above them is a staking-policy question. `'CAPPED'` left as harmless dead text. |
| M5 | DoubleCalibrator's "temporal-aware" OOF is plain contiguous K-fold; `calibrate()` silently falls back | **True** | 5 contiguous folds, each trained on both sides of the block; `except: return weighted average`. Dead at serve: nothing persists it. | None (dead code at serve; fixing the report would not change any served number). |
| M6 | Exposure cap ranks by edge while claiming net EV | **True** | `_net_ev` reads `expected_value`, set by no producer; the existing test set that key itself — a check that passed on a shape production never produces. | `_net_ev` computes `ev_at_price(win_pct/100, odds, commission)` — the ledger's own quantity — with `edge_pct` only as last resort; tests use production keys. |
| M7 | Track leader-bias term is mathematically inert | **True (verified numerically)** | `(leader_rate − 0.5) × 0.35` is added to every runner; across leader rates 0.2/0.5/0.9 the per-runner *relative* adjustment moves by 3e-17, and 10,000 Gumbel-max orderings are identical with and without the shift. | None — a fix is a modelling decision (which styles a leader-biased track should favour, by how much) that the private reference under `.claude/skills/` owns, not something to invent here. |
| M8 | Convergence-report tiers unreachable (65 threshold vs a 0–25 score) | **True; report-only** | `determine_convergence_tier` is called only from `_run_report`; `stride_final ≤ ~45`. Production gating goes through `confirm_with_model`/`crowd_bet_decision`. | None — convergence-tier logic is not modified without explicit approval (CLAUDE.md). |
| L1 | Exact 0.0 edge/EV published as `None` | **True** (edge/overlay; `ev` already used `is not None`) | truthiness guards in `analyze()` and `MonteCarloEngine`. | Fixed. |
| L2 | `wilson_interval` ignores `alpha` | **True** | z hard-coded; only caller passes 0.10. | Honoured; the shipped alpha keeps the literal so bounds are byte-identical (`NormalDist` differs by 7e-16). |
| L3 | mc_api model cache keyed on directory mtime misses in-place overwrites | **True mechanically, low impact** | Directory mtime changes only on entry create/delete/rename. The keyed dirs are historical-data directories, not the racecard the pipeline passes in. | None. |
| L4 | Fold metrics mildly optimistic (LGBM early-stops on the scored fold; per-fold isotonic fitted on the scored fold) | **True; final artifact unaffected** | As described. | None — changing reported CV numbers is a retrain-gate matter (`docs/project_retrain_gate.md`), to be done with the gate re-read, not silently. |
| L5 | Legacy `ml_model.train()` fits TargetEncoder before a random split | **True; not the production trainer** | `retrain_v2` is. | None. |
| L6 | `seed=0` becomes nondeterministic in `simulate_meeting` | **True** | `if seed` vs `is not None`. `monte_carlo.py` has no in-tree importers. | Fixed. |
| L7 | Two tests read workflow YAML without an encoding | **True** | Both files contain em-dashes. | `encoding="utf-8"` added. |

## What was verified correct

The audit's "verified correct" list (Gumbel-max sign convention, Wilson math,
Kelly formulas, de-vig helpers, `roi_stats`, the settlement contract,
`bsp_settlement`, the drawdown breaker, the crowd gate asymmetry, the
`evaluate_bet_candidate` gate table) was not re-derived here beyond the parts
exercised above; nothing found contradicts it.

## Order of operations from here

1. `STRIDE_CTX_MULT_DIAG=true` on the next card — costs nothing, converts §7b
   from grep inference to a runtime fact.
2. Retrain with `retrain_v2` so the staged artifact carries all three
   calibrators (`OOF calibrators persisted: ['cb', 'lgb', 'xgb']` in its log),
   then a shadow week with `STRIDE_ML_APPLY_ISOTONIC=true` reading the
   `[ML] serve calibration:` line. The conviction ladder, bet-gate floors and
   longshot rules were tuned against the inflated numbers and must be re-read in
   the same exercise.
3. One context-multiplier flag at a time, paired A/B, per T20.
4. M2/M3/M7 as `STRIDE_MC_FIX_*` candidates under the #124 discipline.

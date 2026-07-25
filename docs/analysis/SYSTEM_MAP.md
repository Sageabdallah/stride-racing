# STRIDE — SYSTEM MAP (Phase 1 deliverable)

**Purpose.** This is the shared factual base for every later agent in this research run.
It is written to be read *without* the repo open. Everything below was established by
source reading on branch `claude/latest-repo-commit-4j5ksq` (HEAD `2763ea1`), synthesised
from three independent recon passes (architecture, documentation, selection logic) plus
targeted re-verification of every fact on which those passes disagreed.

**Method / limits.** The repository is deliberately not runnable end-to-end: no `.env`,
no database, no `models/*.pkl`, no racecards, no training data. **Nothing here was
established by execution.** Claims about runtime behaviour (e.g. "this multiplier is always
1.00") are derived from exhaustive grep across producing and consuming modules and are
labelled as such. Section 9 lists what could not be resolved at all.

**Citation convention.** `relative/path.py:line`, matching the repo's own convention
(`docs/README.md:76-80`). Line numbers in `docs/01`–`docs/12` have drifted in files
touched since the docs commit; every anchor in *this* file was re-read from source. Where
a number could shift again, the enclosing symbol is named as well — grep the symbol.

**Repo scale (measured, not from docs):** 145 Python modules, **79,634** lines.
Five files hold 21% of it: `server/python/mc_api.py` 7,833 · `racing_system_v8.3_mc.py` 3,286 ·
`server/python/run_tips_pipeline.py` 2,978 · `server/python/retrain_v2.py` 1,505 ·
`server/python/realistic_simulate.py` 1,468. Plus 5 SQL migrations, 8 GitHub Actions
workflows, 16 markdown docs, 4 example artifacts.

---

## 1. What this system does

STRIDE is a production-style machine-learning system for **Australian thoroughbred horse
racing**. Given a day's racecards it produces, for every runner, a win probability that has
been calibrated against the betting market, and from that a single explicit `BET` or
`NO_BET` verdict per race with a human-readable reason. Its objective function is **not
accuracy** — it is expected value. The two quantities it optimises are, exactly as coded,
`edge = calibrated_win_probability − fair_market_probability` (in percentage points,
`server/python/run_tips_pipeline.py:697`) and `EV = calibrated_prob / fair_market_prob − 1`
(a ratio against the de-vigged market, `server/python/run_tips_pipeline.py:955`). The
governing philosophy, stated identically in `README.md:3-5` and `docs/01-architecture.md:3-5`
and restated as a prohibition at `docs/01-architecture.md:12-13`, is *value, not tips*: "The
system does not try to 'pick winners' — it hunts for **disagreements with the market** that
survive calibration." The evidence that justifies that stance is the system's own backtest
over 352 metro races (2026-03-04 → 2026-04-18, `README.md:109-121`): the model's **top pick
wins 33.7% of races but returns −4.2%** at the prices, while the selective `edge ≥ 3%, $2–$15`
band **wins only 9.9% of its bets at +12.3% ROI**. Hit rate and profit are therefore treated
as separable — and often opposed — objectives, and the promotion bar (`docs/12-hit-rate-research.md:435-438`)
forbids buying one with the other: any change "must raise top-pick hit rate on the holdout
**without** degrading the calibration Brier or the Value-Edge band's ROI — hit rate paid for
with calibration is how systems drift into tipping favourites at a loss." The system is
**advisory only**: there is no bet-execution integration anywhere in the repo (grepped for
`place_bet` and every bookmaker API idiom; the bookmaker names that appear are sponsor
prefixes to strip from track names, or a blocklist of bookmaker-owned tipping sites at
`server/python/consensus_agent.py:44-63`). A human places the bets.

---

## 2. The selection pipeline

Orchestration is `python server/python/run_full_pipeline.py <date>`, which chains four
subprocesses and aborts on failure (`server/python/run_full_pipeline.py:46-68`):
`download_racecards` → **`run_tips_pipeline`** → `backfill_tips_contract` → `validate_tips`.
Only step 2 scores anything. The per-race loop is `run_tips()` at
`server/python/run_tips_pipeline.py:2035`; every race is wrapped in try/except so a failing
race emits `bet_status: "ERROR"` and the card continues.

`mc_api` is loaded **in-process** by `importlib.util.spec_from_file_location`
(`server/python/run_tips_pipeline.py:399-405`) so the model cache and hot DB connection
survive the whole card. Every other process boundary is either a dated JSON file in a
git-ignored directory or a Postgres table.

### Step 0 — once per run (setup)

| # | What | Anchor |
|---|---|---|
| 0.1 | Hand-rolled `.env` loader (`_load_env_vars`), sets only keys not already in `os.environ` | `server/python/run_tips_pipeline.py:56` |
| 0.2 | `_configure_mc_runtime_flags` forces `MC_ENABLE_SECTIONAL_FRANKING=false` and `MC_ENABLE_JOCKEY_EFFICIENCY=false` unless the caller already set them — overriding `mc_api`'s own `True` defaults | `server/python/run_tips_pipeline.py:92-100` vs `server/python/mc_api.py:54-55` |
| 0.3 | Load 9 precomputed intelligence JSONs (barrier map, franking, prep cycles, sectional trends, trainer patterns, market overlays, track-distance profiles…). Missing → `{}` | `server/python/run_tips_pipeline.py:117` (`load_intelligence_files`) |
| 0.4 | LLM provider init, gated on `LLM_ENABLED`; failure is non-fatal | `server/python/run_tips_pipeline.py:~2069-2082` |
| 0.5 | Load consensus + market-signal intelligence; import failure ⇒ `_convergence_enabled` false | `server/python/run_tips_pipeline.py:2098-2116` |
| 0.6 | Load global isotonic calibrator pickle if present | `server/python/run_tips_pipeline.py:~574` |

### Steps 1–15 — per race

**1. Filter active runners.** `filter_active_runners` (`server/python/run_tips_pipeline.py:1635`).
Fewer than 2 active runners ⇒ race skipped.

**2. Normalise + validate.** `race_normaliser.normalise_race`. Any CRITICAL flag ⇒ race
skipped. Builds the pace map. Validates overround into the range 0.90–1.60.

**3. Luckless / excuse analysis.** `luckless_analyser.analyse_luckless` — ~90 keyword rules
over stewards'/analyst comments → a 0–100 forgive score; uplift folded into
`llm_mu_adjustment`, **capped 0.12**.

**4. LLM pre-analysis.** `llm_form_analysis.analyse_race_field`; per-runner mu adjustments
clamped ±0.08.

**5. Race context.** `race_context.compute_race_context` — pace pressure, pace clarity,
barrier relevance, market efficiency.

**6. Form features from the DB.** `form_feature_builder.compute_race_form_features` (as-of safe).

**7. ML scoring — probability engine A (GBM ensemble).**
The production feature contract is `FEATURE_COLUMNS`, **113 entries, no duplicates**, and it
is **byte-identical** in two places: `server/python/retrain_v2.py:152-275` and
`server/python/ml_model.py:65-189` (`RacingMLModel.FEATURE_COLUMNS`) — verified by
`ast.literal_eval` on both. At load time the pickle's own saved `feature_columns` takes
precedence (`server/python/ml_model.py:211`, `:644`, `:665`), so a grown contract cannot
shape-mismatch a stale artifact. Sub-groups: 11 `PHASE2_FEATURES` (sectional primitives) and
2 `NAN_PRESERVE_FEATURES` are **NaN-preserved** so trees can exploit missingness; everything
else is zero-filled (`server/python/retrain_v2.py:278`). Five interaction features
(`fitness_x_distance`, `barrier_x_pace_inv`, `sectional_x_going`, `class_drop_x_trajectory`,
`campaign_run_x_fitness`) are rebuilt **inline at inference** with duplicated formulas and no
shared helper (`server/python/run_tips_pipeline.py:~2306-2319`) — a live drift hazard.
The models: XGB / LGB / CatBoost, 200 trees, depth 6, lr 0.05
(`server/python/retrain_v2.py:739-778`). The **target is pointwise binary `is_winner`** — selected in
SQL at `server/python/retrain_v2.py:299`, filtered `IS NOT NULL` at `:336`, and assigned as `y` at
`:1433`. *(Corrected by the Phase-2 citation audit §4 item 2: the anchor previously given here,
`retrain_v2.py:219`, is `"svi",` inside `FEATURE_COLUMNS` — re-verified this session.)* Nothing
groups by race in any `.fit()` call. At
inference `RacingMLModel.predict_proba` (`server/python/ml_model.py:544`) prefers a fitted
stacking learner, then a double calibrator, else a weighted average whose weights are
normalised from **hardcoded seed accuracies** in `_model_performance`
(`server/python/ml_model.py:59-63`; e.g. mile → 0.3333 / 0.3030 / 0.3636). `_model_performance`
is a class attribute mutated only by `update_model_performance` and **never persisted**, so
the "dynamic weighting" is in practice static invented constants. Per-model OOF isotonic
calibrators are **deliberately not applied** at inference (`server/python/ml_model.py:565` — the
comment line; `:566` is the `if` that follows. Corrected per citation audit §5, re-verified).
Output → `runner["mlPredictedProb"]`.

**8. Monte Carlo — probability engine B.** `run_mc_simulation`
(`server/python/run_tips_pipeline.py:397`) → `mc_api.run_simulation`. Iterations by field
size: **5000** (≤10), **3000** (≤14), **2000** (>14) — `server/python/run_tips_pipeline.py:389-394`.
**Seed = `int(time.time()) % 100000`** (`server/python/run_tips_pipeline.py:2340`) — the daily
run is **not reproducible**; every backtest uses 42. Base engine is Plackett-Luce sampled by
the Gumbel-max trick, `order = argsort(−(logits + rng.gumbel(n)))`
(`racing_system_v8.3_mc.py:1855-1856`), with 17 weighted priors (`FEATURE_WEIGHTS`,
`racing_system_v8.3_mc.py:46-63`) and Dirichlet concentration
`max(6.0, 12.0 + 1.3 × n_historical_runs)` (`racing_system_v8.3_mc.py:1804`) scaled by going
(heavy ×0.72 / soft ×0.82 / synthetic ×1.06 / firm ×1.04). A second simulator,
`realistic_simulate.simulate_race_with_sectional_profiles`, supplies a sectional overlay
(mixture noise, 4-phase energy depletion, collapse events).

**9. Where the two MC engines merge — inside `mc_api`.**
`combined_adjustment = 0.55·ml_adjustment + 0.22·sophisticated_adj + 0.13·enhanced_factor + 0.10·fitness_adjustment`
(`server/python/mc_api.py:7379`), capped `min(·, 1.15)` above $20 and `min(·, 1.25)` above $10.
Then the **sectional blend `base = base × 0.70 + sectional × 0.30`** (`server/python/mc_api.py:7393`).
`adjusted_win_prob = base × combined_adjustment`, **capped 60.0%, floored 1.0%**
(`server/python/mc_api.py:7398-7399`); place capped 90.0%. Field normalisation renormalises
win probs to 100% and place probs to 300.0 (`server/python/mc_api.py:~7612-7623`), after which
mc_api recomputes its **own** edge/EV: `edge = winPct − 100/odds` (no de-vig) and
`EV = (winPct/100)·odds − 1` (`server/python/mc_api.py:7636-7637`).

**10. Where MC and ML actually merge for selection — `calibrate_and_score`.**
`server/python/run_tips_pipeline.py:568`. Order of operations, each with its constant:

```
10a. [optional] conditional-logit blend        :591-629   flag STRIDE_CL_BLEND, default OFF
     P_i ∝ exp(α·ln m_i + β·ln q_i)  applied to MC winPercentage
     requires artifact stage == "mc" (:595) and >= 2 quoted runners (:614)
     when active it sets _calibrator = None (:627) — CL REPLACES isotonic, never both
10b. snapshot MC engine's own score, normalise: _mcSelectionScoreNorm = (s/s_max)*12.0  :646
10c. global isotonic  ProbabilityCalibrator.calibrate(raw/100)                          :~657-661
     bounds y_min=0.01, y_max=0.95, out_of_bounds="clip"  (calibration_model.py:30-32)
10d. MC <-> ML blend:  ml_w = 0.20 if odds <= 3 else 0.40                                :667
     raw = (1 - ml_w)*mc + ml_w*ml       -> h["rawModelProb"]                            :670
10e. de-vig:  overround = SUM(100/odds)/100  (returns 1.0 if < 2 quotes)                 :432-442
              true_market = (100/odds) / overround                                       :673-674
10f. MARKET ANCHOR, model weight mw by price band                                        :679-690
        odds <=3   <=6   <=10  <=15  <=30   >30
        mw   0.80  0.70  0.50  0.45  0.40  0.30
     calibrated   = mw*raw + (1-mw)*true_market                                          :692
     winPercentage = round(calibrated, 2)                                                :693
     trueMarketProb = round(true_market, 2)                                              :694
     fairOdds      = 100.0 / true_market      (MARKET fair odds, not model)              :695
     modelEdge     = round(calibrated - true_market, 2)                                  :697
     [no quote]    trueMarketProb=0, fairOdds=None, modelEdge=0; winPercentage NOT
                   overwritten -> unquoted runners stay on the pre-ML-blend scale         :699-701
10g. flat-MC detection: mc_spread = max(rawModelProb) - min(rawModelProb); flat if < 6.0  :703-705
10h. context multipliers (see defect note below)                                          :719-728
     fitness_mult = 0.95 + fitnessReadinessScore/100*0.10
     bias_mult    = 0.95 + trackBiasPoints/100*0.10
     jockey_mult  = clamp(jockey_momentum_adjustment, 0.85, 1.20)
     h["rawModelProb"] = adjusted_raw     <-- rewritten AFTER modelEdge was computed      :729
10i. selection score
     edge_term  = clamp(modelEdge, -10, +10)                                              :736
     prob_score = 0.80*adj_calib + 0.20*edge_term  if flat  else 0.70/0.30                :738,740
     base       = prob_score * intel_mult + intel_bonus                                   :749
                  intel_mult clamp [0.80, 1.30], intel_bonus clamp [-3.0, +4.0]           :562-563
     sectional bonus: if (sect_win - blendedBaseProb) > 1.5 -> delta*0.65,
                      soft ceiling 6.0 then 30% credit                                    :~756-766
     low-prob squash: calib < 5 -> *0.30 ;  calib < 8 -> *0.55                            :768-771
     FINAL = 0.65*mc_score_norm + 0.35*base  if flat  else 0.50/0.50                      :774,776
10j. flat-MC gradient penalty by top-two gap in rawModelProb                              :~785-792
     gap < 1.5pp -> *0.30 ; < 3.0 -> *0.60 ; < 5.0 -> *0.85 ; else *1.00
```

The MC engine's own spine score is `mc_selection_score` (`racing_system_v8.3_mc.py:1908`):
`0.30·edge + 0.08·ev_scaled + 0.30·win + 0.12·top3 + 0.12·stability + 0.08·rel_scaled`. It
carries **50% of the final selection score** — so half of what is nominally the "ranking"
number is itself already partly an edge number.

Two structural facts about this step matter more than any single constant.
*(i)* **Edge is computed from the already-market-anchored probability**, so it is mechanically
shrunk by `(1 − mw)`. This is deliberate — the in-code comment at `:696` reads "Edge from
calibrated (not raw) — prevents false positives from flat MC" — but it means the market-weight
schedule and every downstream edge threshold are **not independently tunable**: raising `mw`
inflates `modelEdge` for every runner the model likes and loosens every edge gate at once.
*(ii)* The rationale comment for the rising `mw` at short prices (`:676-678`) is a *Kelly audit*:
"$1-3 horses win 41%, model predicts 17% after blend."

**11. LLM post-scoring.** Top **6** by score (`server/python/run_tips_pipeline.py:2379`) go to
`llm_post_scorer.score_race_horses`. Blend at `:2398-2400`:
`ai_norm = (ai_score/100)·selectionScore; selectionScore = 0.70·selectionScore + 0.30·ai_norm`.
Because `ai_norm` is itself proportional to the current score, the effective multiplier is
`0.70 + 0.003·ai_score ∈ [0.70, 1.00]` — **the LLM can only ever lower a score, never raise it.**
The LLM's ranking also re-sorts the field. If the MC is flat (`mc_spread < 6.0`) the LLM's
top-3 are pushed to `max_score + [5.0, 3.0, 1.0]` and rank 0 is tagged `_llm_top_pick`, which
**bypasses every safety filter** (`server/python/run_tips_pipeline.py:~2445` and `:883`).

**12. Safety filters → top 3.** `apply_safety_filters`
(`server/python/run_tips_pipeline.py:802`). Conviction bonus added to the score *before*
sorting (`:836-843`): edge≥3 & raw≥15 → **+3.0**; edge≥2 & raw≥12 → **+2.0**;
edge≥1 & raw≥10 → **+1.0**. Class odds caps (`:817-824`): G1 **$25**, G2/G3 **$25**,
Listed **$30**, other **$30**. Then, **in order**: (1) dominance override — top score, gap ≥1.5,
odds ≤30, and (edge>1 | raw≥10 | calib≥10); (2) favourite discipline — `is_favourite` and
`fav_odds ≤ 20` and `edge > 0`; (3) `_llm_top_pick` unconditional pass; (4) banker override
`banker_score ≥ 70` (`:888`); (5) distance-range block if `race_d > max_win_dist + 200` or
`< min_win_dist − 200`; (6) class cap with merit override `odds≤30 & edge>1 & raw≥8`;
(7) longshot rules — keep if `odds≥15 & edge>2 & raw≥8`; block `odds≥30 & raw<10 & edge≤0`;
block `odds≥20 & edge≤0 & raw<8`; block black-type `odds≥10 & calib<10 & edge≤0`. If nothing
survives, the fallback is **the three shortest-priced runners**. Returns `filtered[:3]`.

**13. Confidence → staking.** `compute_confidence` (`server/python/run_tips_pipeline.py:950`):
`ev = calib/true_mkt − 1.0` (`:955`); `odds > 30 → low`; `ev > 0 and edge > 1.0 → high`;
`edge > 0 → medium`; else `low`; then `pace_clarity < 0.35` demotes high → medium
(`PACE_CLARITY_FILTER_ENABLED = True`). The justification comment records that the v1 ladder
was *anti*-correlated with value (mean EV "high" +0.036 vs "low" +0.152, n=330, 2026-04-14).
`compute_staking` (`server/python/run_tips_pipeline.py:1007-1015`) is, in its entirety:

```python
if conf == "high":   return "2u"
if conf == "medium": return "1u"
return "0u"
```

**That is the whole of live sizing.** No Kelly, no bankroll, no price sensitivity: a $2.50
shot and a $12 shot with the same confidence receive the same stake. If the MC is flat, all
three picks are forced to `low` ⇒ `0u`.

**14. The BET / NO_BET contract.** `evaluate_bet_candidate`
(`server/python/run_tips_pipeline.py:1778`), reading `prob = max(raw_model_pct, win_pct)` —
the *more favourable* of the pre- and post-anchor probabilities.

| Condition | Result | Anchor |
|---|---|---|
| intelligence override: rank 1 & `intel_bonus ≥ 3.0` & franking ≥ 55 & prep trend ≠ DECLINING | **BET**, bypasses everything below | `_check_intelligence_override`, `:1727`; checked at `:~1795` |
| no real market quote | NO_BET | `:~1807` |
| `edge <= 0` | NO_BET | `:~1809` |
| **`odds > 15`** | NO_BET — "outside the validated win-bet range" | `:1812` |
| `odds < 3` | needs `edge ≥ 4` **and** `prob ≥ 30` | `:1816` |
| `3 ≤ odds ≤ 5` | needs `edge ≥ 2.5` **and** `prob ≥ 15` | `:1821` |
| `5 < odds ≤ 15` | needs `edge ≥ 3` **and** `prob ≥ 10` | `:1825` |
| `confidence == "low"` and `odds > 12` | NO_BET (guide only) | `:1828` |

`choose_bet_race_pick` (`:2000`) returns the raw model leader **or nothing** — no substitutes.
`choose_coverage_race_pick` (`:1871`) picks a *display* horse through a fallback cascade,
labelled honestly and never presented as the bet.

**15. The crowd / convergence gate — the final arbiter.**
`server/python/run_tips_pipeline.py:2638-2757`, library `server/python/consensus_blender.py`.
Candidate set = horses with `crowd_score ≥ 50`, top 3 (`:~2655`). `confirm_with_model`
(`server/python/consensus_blender.py:205`) classifies each candidate by **model selection
score**: `≥15 → CONFIRMED`, `≥8 → CROWD_ONLY`, else `CROWD_ONLY_WEAK`; the model's own top
horse, if it is not a crowd candidate, → `MODEL_ONLY`. `crowd_bet_decision`
(`server/python/consensus_blender.py:236`):

| classification | crowd score | should_bet |
|---|---|---|
| CONFIRMED | any | **True** (stake FULL) |
| CROWD_ONLY | > 70 | **True** (STANDARD) |
| CROWD_ONLY | ≥ 50 | **True** (REDUCED) |
| CROWD_ONLY_WEAK | ≥ 100 | **True** (REDUCED) — "UNANIMOUS CROWD", added 2026-04-06 on **one** race, comment says "monitor for 4 weeks" |
| CROWD_ONLY_WEAK | > 70 | False (tracked for backtest only) |
| **MODEL_ONLY** | any | **False — "archetype trap, no crowd support"** |

This overrides the edge gate in **both** directions: BET→NO_BET is stamped `crowd_gated`,
NO_BET→BET is stamped `crowd_promoted`. `stake_recommendation` (FULL / STANDARD / REDUCED /
NONE) is a **label only** — nothing anywhere converts it to a number. If the crowd import
fails while convergence is enabled, **every pick is forced NO_BET** (`:2751-2757`) — a hard
runtime dependency on external tipster data.

**Day-level blocks** (`server/python/tips_day_aggregates.py`): `select_best_bets` = top 3
high-confidence rank-1 (fallback top 2 medium with edge>0); `select_value_plays` = rank-1,
`edge > 3`, `4 ≤ odds ≤ 15`, top 5; `select_bankers` = high confidence at `odds ≤ 4`.

**Persistence.** `store_selections_in_db` (`server/python/run_tips_pipeline.py:1245`) writes a
**115-column** INSERT (counted from the statement at `:1329-1398`), but only for picks with
`should_bet` true; it deactivates prior rows for the same date+track first and **aborts loudly**
if that deactivation fails rather than creating duplicate active rows.
`store_final_probs_in_audit` (`:1582`) writes `final_win_prob` for **every** runner.
Output JSON is written atomically (tmp + rename), per-track runs merge into the canonical file
after a timestamped backup, and `validate_tips` runs automatically (loud, non-fatal).

### The two engines and the calibration chain, summarised

```
   GBM ensemble (XGB/LGB/CatBoost, pointwise binary is_winner)  ─┐
                                                                 ├─ ml_w = 0.20 / 0.40   (step 10d)
   Monte Carlo (Plackett-Luce base 0.70 + sectional overlay 0.30)┘
                          │
                 (mc_api also blends its own 0.55/0.22/0.13/0.10 adjustment first, step 9)
                          ▼
   calibration chain, in execution order:
     L0  conditional-logit blend      OPT-IN, STRIDE_CL_BLEND, replaces L3   (run_tips_pipeline.py:591)
     L1  per-model OOF isotonic       FITTED but DELIBERATELY NOT APPLIED    (ml_model.py:565)
     L2  double calibration           only if models/double_calibrator.pkl exists (git-ignored)
     L3  global isotonic              LIVE if pickle present, clip [0.01, 0.95] (calibration_model.py:30-32)
     L4  MC recalibration (FLB, PAV)  only if calibration_model.json exists — INERT in this tree
     L5  enhanced-trainer CalibratedClassifierCV — not on the live path
                          ▼
     market anchor (mw ladder)  ->  calibrated  ->  edge = calibrated − true_market
```

---

## 3. Where ROI and hit rate are measured

| Harness | Computes | Live? | Stores |
|---|---|---|---|
| `server/python/walk_forward_backtest.py` | Expanding-window CV. AUC-ROC, Brier, log-loss, ECE (10 bins), **plus ROI and hit rate at thresholds {0.05, 0.10, 0.15, 0.20, 0.30}** with t-dist 95% CIs. Flat `stake = 100.0`; `roi_pct = profit/staked × 100`; `hit_rate = winners/bets`. `min_train_size=3000`, `test_size=500`, **`gap_days=7`** | Manual CLI; cannot run in this tree (no DB, no models) | JSON under `server/python/backtest_results/` |
| `server/python/backtest_v2_metro.py` | **The source of the README numbers.** Loads the saved v2 pickle, race-normalises `model_prob`, runs 6 strategy bands (`STRATEGIES`), `STAKE = 100` | Manual | `examples/backtest_summary.json` |
| `server/python/backtest.py` (v1) | Rolling 6-month train / 1-month test, 43 hand-built features, 13 strategy sweeps, `optimize_threshold`. **No purge gap** — docs say use `walk_forward_backtest.py` when rigour matters | Manual | stdout/JSON |
| `server/python/calibration_backtest.py` | Grid-search (18 configs) of the sectional-vs-market MC blend ratio, scored by log-loss / Brier vs a market baseline. A hyper-parameter search, **not** a forward test | Manual | in-memory |
| `server/python/shadow_pl_tracker.py` | **The only true forward P&L.** `cmd_record` inserts PENDING rows from `convergence_output`; `cmd_results` settles WIN = `SP − 1`, **PLACE = −1**, LOSS = −1 (win-only staking); `cmd_report` gives ROI **by convergence tier**, including tiers the system refuses to bet, so gating stays evidence-based. `BET_TIERS = {CONFIRMED, CROWD_ONLY, LOCK}`; `MIN_BETS_REPORTABLE = 200` | Live, DB-dependent | `stride_tip_results` |
| `server/python/learn_from_results_v2.py` | Nightly loop: gap detection → ingest results/sectionals → reconcile tips → refresh `training_view_v2` → **staged** retrain. PID-locked | Live | `race_results_history`, `stride_tip_results`, `research/learning_runs/*.json` |
| `prediction_audit` table | mc_api writes MC-stage probs; the pipeline writes `final_win_prob` for every runner. **Known near-empty: 260 rows total**, all 2026-03-07→15 (`docs/12-hit-rate-research.md:197-210`) — every insert failed silently for months because the `ON CONFLICT` arbiter index did not exist (`migrations/prediction_audit_unique_key.sql:1-15`) | Live but historically broken | `prediction_audit` |
| `server/python/rank_model.py` | LambdaRank (`LGBMRanker`) evidence harness on the same 113-column matrix; reports **top-pick hit rate vs the market-favourite baseline** | Evidence only — zero importers, **no pipeline hook** | artifact only |
| `server/python/source_accuracy_tracker.py` | Per-tipster accuracy, consumed behind `STRIDE_ACCURACY_WEIGHTS` | Opt-in | `source_accuracy` |
| `server/python/research/performance_autopsy_last21days.py` | Classifies each losing tip into failure modes (`wrong_favourite`, `barrier_blindspot`, `going_miss`, `franking_miss`, `prep_cycle_miss`, `price_range_miss`) | Manual | report |
| `server/python/portfolio_risk.py` | Fractional Kelly 0.25, bankroll 10,000, 5% max single bet, 15% max daily exposure, ≤10 concurrent, drawdown MC, correlation risk | **DEAD — zero import sites anywhere in the repo.** `docs/10 §5` admits even the backtesters don't use it | — |

**The measurement gap that dominates this run.** Nothing measures the *wrapper* end-to-end.
`shadow_pl_tracker` measures **convergence tiers**; the backtests measure the **ML ensemble**
against strategy bands. The unit
`calibrate_and_score` + `apply_safety_filters` + `evaluate_bet_candidate` + `crowd_bet_decision`
— i.e. the thing that actually decides bets — has **no live hit-rate and no live realised-ROI
measurement at all**. Every threshold in section 6 is therefore currently *unfalsifiable in
production*. This is the single most important prerequisite for any Phase-4 change: an A/B is
only meaningful if something scores the result, and today nothing does.

**Which lever moves which number** (this is the frame later phases should use):
*Ranking quality* (everything up to and including `apply_safety_filters`) sets hit rate and is
price-independent; a genuinely better ranking raises hit rate *and* ROI.
*Anchoring and gating* (`mw` ladder, `evaluate_bet_candidate`, `crowd_bet_decision`, de-vig
method) trade the two directly — toward the market means higher strike and worse ROI
(33.7% / −4.2%), toward the model means lower strike and better ROI (9.9% / +12.3%).
*Staking* (`compute_staking`, `portfolio_risk`) moves ROI **with zero effect on hit rate** —
the only free lunch in the system, and it is entirely unexploited today.

---

## 4. Current conventions

**File layout.** Flat, role-based, no package hierarchy. `server/python/` holds **119 top-level
modules** plus `intelligence/` (10, gen-3, mostly unwired) and `research/` (**11 files: two standalone
diagnostics — `performance_autopsy_last21days.py`, `investigate_sectional_market_going.py` — plus a
9-module `winner_pattern_gap/` package**; recounted this session, the earlier "12 diagnostics" figure
was wrong and is corrected here because Phase-4 T3 places a new module in this directory). Four
modules sit at the repo root (`racing_system_v8.3_mc.py` — live library; `monte_carlo.py`,
`build_features.py`, `download_training_data.py` — standalone/dormant). Docs in `docs/`,
migrations in `migrations/`, workflows in `.github/workflows/`, golden-ish samples in
`examples/`.

**Naming.** `snake_case`, role-descriptive not layer-descriptive (`form_feature_builder.py`,
`banker_detector.py`, `luckless_analyser.py`). Australian and US spellings coexist
(`race_normaliser.py` next to `normalize.py`) — **match the file you are editing, do not
normalise**. Prefix families: `run_*` orchestrators, `build_*` / `stride_agent_*` builders,
`*_collector.py` ingesters, `backfill_*.py` repair sweeps. Generations are **suffixed, never
renamed in place**: `train_ml.py` → `train_ml_enhanced.py` → `retrain_v2.py`;
`backtest.py` → `backtest_v2_metro.py`. Feature names are `snake_case` domain nouns, booleans
`is_*`/`has_*`, interactions `_x_`. **Two JSON key conventions coexist deliberately**:
`camelCase` on the MC/engine boundary (`winPercentage`, `selectionScore`, `rawModelProb`,
`modelEdge`) and `snake_case` in the published tips document (`win_pct`, `raw_model_pct`,
`edge_pct`, `selection_score`). They are two different contracts — **do not unify them.**
"Phase N" tags in comments are load-bearing prose markers (Phase 2 = sectionals, 3 = trajectory,
4 = distance intelligence + bounce, 5 = within-race relative market).

**Config — name it precisely, because Phase 4 must flag through it.**
**There is no config module.** No `config.py`, `settings.py` or `constants.py` exists for the
production system (the only `config.py` in the repo is
`server/python/research/winner_pattern_gap/config.py`, research-local, no production callers).
The mechanism is: **environment variables read at import time or call time, with inline
literal defaults, loaded from a repo-root `.env` by one of three different loaders** —
`run_tips_pipeline._load_env_vars` (`server/python/run_tips_pipeline.py:56`, hand-rolled,
`if key not in os.environ`), an inline `os.environ.setdefault` parser in
`server/python/download_racecards.py:20-27`, and real `python-dotenv` in ~11 other modules.

Two flag idioms exist:
- **Variant A — the only named helper**, `_env_flag(name, default)` at
  `server/python/mc_api.py:44-48`, accepting `1/true/yes/on`. Used for
  `MC_ENABLE_SECTIONAL_FRANKING` and `MC_ENABLE_JOCKEY_EFFICIENCY`
  (`server/python/mc_api.py:54-55`, default `True`, force-set to `"false"` by
  `server/python/run_tips_pipeline.py:92-100`).
- **Variant B — the inline default-off idiom**, copy-pasted at three sites:
  `os.environ.get("<FLAG>", "false").strip().lower() in ("true", "1", "yes")`.

**The precedent Phase 4 must copy is `STRIDE_CL_BLEND`** (`server/python/run_tips_pipeline.py:591`).
Its comment states the contract explicitly — *"Default: off, byte-identical."* — and it fails
safe three ways: missing artifact → falls back, wrong `stage` → refuses, transform exception →
non-fatal warning with the original probability surviving. Its siblings are
`STRIDE_PREDICTABILITY_GATE` (`server/python/tips_day_aggregates.py:41`, guaranteed
**ordering-only**, and its self-test saves/restores the env var so the flag is testable without
leaking state) and `STRIDE_ACCURACY_WEIGHTS` (`server/python/consensus_agent.py:124`, bounded
by `ACCURACY_CLAMP = (0.75, 1.25)`).

Only **six** numeric knobs are env-overridable, all in `server/python/consensus_blender.py:25-35`
(`STRIDE_MODEL_WEIGHT` 0.50 / `CONSENSUS_WEIGHT` 0.30 / `MARKET_SIGNAL_WEIGHT` 0.20;
`CONSENSUS_LOCK_THRESHOLD` 65 / `MARKET_SIGNAL_THRESHOLD` 60; `STRIDE_THRESHOLD = 65.0` at
`:33` is **not** overridable). **Caveat:** those feed `blend_scores`, which is on the dormant
V2 path — the live V3 crowd-first path never calls it, so changing them does not affect
production today. **Every number that actually decides a bet is a hard-coded literal.**

**Logging.** `print(..., file=sys.stderr)` with bracketed tag prefixes is the house style —
98 such calls in `run_tips_pipeline.py` alone. Tag vocabulary worth grepping a production log
for: `[MC FLAGS] [INTEL] [DB] [LLM] [BLENDER] [NORM] [LUCKLESS] [RACE_CTX] [FORM] [ML]
[MC ERROR] [MC_FLAT] [CL_BLEND] [CONVICTION] [BANKER_PASS] [DIST_RANGE] [CLASS_FILTER]
[CLASS_OVERRIDE] [LONGSHOT_FILTER] [BLACK_TYPE_FILTER] [CROWD] [GATE] [MERGE] [VALIDATE]
[RACE_ERROR]`. The `logging` module is imported by 11 of 145 modules and used meaningfully by
9; `server/python/run_tips_pipeline.py:52` creates a logger and never uses it. **There is no
logging configuration anywhere** — no handlers, levels, log file, or run/correlation ID.
One convention is absolute: **stdout is a contract** — `mc_api` reserves stdout for its JSON
result and sends all logging to stderr.

**Error handling.** Two idioms. (a) *Optional-capability imports*: every optional dependency is
a try/except-ImportError setting an `*_AVAILABLE` boolean; `server/python/mc_api.py:56-280` is
~25 consecutive blocks of this, and it is the de-facto capability registry (there is no plugin
system, strategy pattern, or DI to hook into). (b) *Per-stage containment*: `except Exception`
counts — `mc_api.py` **93**, `run_tips_pipeline.py` **44**. Most print "(non-fatal)" and
continue. **The consequence: failures present as slightly worse selections, not as alarms.**
The repo has been bitten by exactly this twice, both documented in-repo — the silent
`prediction_audit` insert failure (`migrations/prediction_audit_unique_key.sql:1-15`) and the
silent stacking-fit `NameError` that caused `ci.yml` to exist (`.github/workflows/ci.yml:3-5`).
Any new code should therefore add a *positive* assertion (a printed count, a validator line)
rather than relying on the absence of an exception. Deliberate fail-loud exceptions worth
preserving: `store_selections_in_db` aborts if deactivation fails; convergence import failure
forces all picks NO_BET; `run_full_pipeline` aborts on download/tips failure; CRITICAL
normaliser flags skip the race.

**Testing — or its absence.** There is **no test suite**: no `tests/`, no pytest, no unittest,
no `conftest.py`, and neither pytest nor any linter/formatter/type-checker is in
`requirements.txt`. What exists instead is **module `_self_test()` functions run as
`python <module>.py`** — ~156 asserts across 12 modules. `.github/workflows/ci.yml` is the
**entire** automated gate, runs only `on: push`, and does exactly two things: `compileall -q .`
and run 8 module self-tests (`relative_market`, `conditional_logit`, `glicko2_elo`,
`temporal_staleness`, `rank_model`, `audit_coverage_report`, `backfill_results`,
`tips_day_aggregates`). All seven other workflows are `workflow_dispatch`-only; **there is no
scheduled Action** — the daily pipeline is run externally/manually.
**`mc_api.py`, `run_tips_pipeline.py`, `racing_system_v8.3_mc.py` and `consensus_blender.py`
— every module that decides money — have zero tests and zero self-tests.**
`race_normaliser.py` (21 asserts) and `luckless_analyser.py` carry self-tests **not wired into
CI**. `examples/sample_race.json` and `examples/sample_selections.json` are real output from a
39-race day and are the only golden-file-shaped artifacts; nothing compares against them.

**How the repo handles v1/v2/v3 generations.** The rule is *never delete a superseded
generation; leave it in place and flag it in the docs*. Live inventory:
- **3 consensus generations** — V1 (migrations), V2 `blend_scores`/`determine_convergence_tier`/
  `apply_convergence_gate` (present, **no caller** on the live path), V3
  `confirm_with_model`/`crowd_bet_decision` (**LIVE**).
- **3 intelligence generations** — `intelligence_common.py` (gen-2, live),
  `intelligence/common.py` (gen-3 utils, live *because `consensus_blender` imports it*),
  `intelligence/build_*.py` (8 gen-3 builders, **unwired**).
- **4+ MC engines** — `mc_api` base Plackett-Luce (0.70) + `realistic_simulate` overlay (0.30),
  plus `racing_system_v8.3_mc`'s own MC, plus the dead root `monte_carlo.py`.
- **5–6 calibration layers**, of which 1 is live in this tree (see section 2).
- **2 pace engines** (`pace_modeling.py`, `speed_mapping.py`), both imported by `mc_api`.
- **Dead (zero importers, no CLI role):** `portfolio_risk.py`, `monte_carlo.py`,
  `adaptive_mc.py`, `weather_api.py`, `model_versioning.py`, `focal_loss.py` (import-only).
- **Dormant (behind a flag or with no hook):** `conditional_logit`, `rank_model`,
  `predictability_meta_model`, `intelligence/build_*`, `feature_store`, `glicko2_elo`,
  `market_efficiency`, `learned_sectional_combination`.

**Dependency management is three-headed and self-contradictory.** `pyproject.toml` (name
`repl-nix-workspace`, 2 deps, `numpy>=2.4.1`) vs `requirements.txt` (32 deps, `numpy==1.26.4`
— the **only** pin in the file) vs `uv.lock`. CI ignores all three and pip-installs an ad-hoc
list. Unpinned `xgboost`/`lightgbm`/`catboost` against a pickled live ensemble is a genuine
un-reproducibility risk. `dnfile` is listed and never imported. There is no Dockerfile, no
service, no scheduler in-repo.

---

## 5. Explicit constraints (the Phase-4 guardrail list)

Every Phase-4 ticket must be checked against this list. Quotes are verbatim where marked.

### 5a. Process guardrails binding this research run (`agent_research.md:124-145`)
1. **Additive, not destructive.** "No ticket may remove or rewrite existing working logic. New capabilities slot in alongside current ones."
2. **Feature-flag everything.** "Each change sits behind a config toggle (using the existing config system) so it can run A/B against the current behavior. **Default = off until validated.**" (The existing config system is defined precisely in section 4 — a `STRIDE_*` env var read via the inline default-off idiom, following `STRIDE_CL_BLEND`.)
3. **One source of truth.** "If the system already has a probability conversion, odds handler, staking module, or config loader — the ticket **MUST extend it, never duplicate it**. Call out the existing module by name."
4. **Schema safety.** "No changes to existing database schemas, log formats, or API contracts. New data gets new tables/fields, **additive only**, with migration notes."
5. **Convention lock.** "Naming, folder placement, error handling, logging, and testing patterns must match what's documented in SYSTEM_MAP.md — **even if the research suggests a 'better' style. Consistency > elegance.**"
6. **No pipeline reordering without evidence.** Step order stays intact "unless a ticket explicitly justifies the change with expected-impact numbers and a rollback path."
7. **Conflict check.** "Every ticket must end with a 'Conflicts checked' section."
8. `agent_research.md:152-154` — "**Do NOT write production code in this run** … Wait for my approval before implementing any ticket."

### 5b. Hard operational guardrails (docs)
9. **Staged retrains only.** `learn_from_results_v2` "writes new models to `models/staging/`, **never over the live artifact**" (`docs/01-architecture.md:129-130`); "**Retrains are staged, never auto-promoted** — a human promotes the artifact" (`docs/10-backtesting-and-learning.md:65-66`).
10. **BET/NO_BET contract, no hidden substitutes.** "every race is explicitly BET or NO_BET with a reason; the bet must be the raw model leader passing price-band edge gates (**no hidden substitutes**)" (`docs/01-architecture.md:121-123`); "if the leader fails, the race is an explicit NO_BET with a human-readable reason" (`docs/09-scoring-and-output.md:142-143`).
11. **Contract validation must still pass.** "`validate_tips.py` → output-**contract invariants** hold after **any** change" (`docs/12-hit-rate-research.md:433`). `backfill_tips_contract.py` imports the live functions from `run_tips_pipeline` so the logic cannot drift — keep that import, do not fork it.
12. **Never double-calibrate.** "Switching per-model isotonic on at inference without refitting the downstream calibrator would double-calibrate and distort the validated probability scale. If per-model calibration at inference is ever wanted, refit `isotonic_calibrator.pkl` on the new output **in the same change**" (`docs/05-ml-training-and-calibration.md:100-103`). Mirrored in code at `server/python/run_tips_pipeline.py:626`: "CL supersedes the early isotonic correction — **never both**."
13. **Calibration never sees tuning data.** The final 10% of rows is for early stopping "**never for calibration**" (`docs/05:77`); "calibration never sees data the model was tuned on" (`docs/05:92`).
14. **`STRIDE_CL_BLEND` stays OFF on the current artifact.** "**Do not flip `STRIDE_CL_BLEND` on this artifact**" (`docs/12-hit-rate-research.md:311`) — the fit's train/serve stage provenance is unestablished.
15. **LambdaRank stays evidence-only.** "**Verdict: criterion FAILED — the ranker stays evidence-only, no pipeline wiring**" (`docs/12-hit-rate-research.md:396`). `rank_model.py` has zero importers — verified.
16. **The predictability gate is ordering-only.** "Ordering only — BET/NO_BET decisions, edges and stakes are **never touched**" (`docs/12:155-156`, `docs/09:202`).
17. **Favourite discipline.** "the market favourite passes only with positive model edge — '**do not tip the market favourite simply because it is the shortest price**'" (`docs/09-scoring-and-output.md:93`; in code at `server/python/run_tips_pipeline.py:876`).
18. **The promotion bar.** Any default-on change "must raise top-pick hit rate on the holdout **without** degrading the calibration Brier or the Value-Edge band's ROI" (`docs/12-hit-rate-research.md:435-438`).
19. **Reportability floor.** "a tier is only reportable at **≥ 200 settled bets**" (`docs/10:83-84`; `MIN_BETS_REPORTABLE` in `server/python/shadow_pl_tracker.py`).
20. **QLD Cloudflare is an access decision, not a code fix.** "the above-board path is official RQ industry data access … **not an escalating challenge-bypass** on production infrastructure" (`docs/12:246-249`).
21. **Secrets and artifacts.** "Runtime configuration is environment-driven — **no secrets in source**" (`README.md:174-176`); "**never pass the connection string as a workflow input** — the repository is public" (`docs/12:293-294`); "`models/` is **deliberately git-ignored**, so it is **never committed**" (`docs/12:328-329`).
22. **Licence.** "Proprietary — © 2026 Sage Abdallah. All rights reserved… reuse, copying, or modification is **not permitted**" (`README.md:185-187`). This repo is published "for review, not deployment."

### 5c. Leakage constraints (`research/report.md:176-230`)
23. "Late-odds features: **prospective collection only; assert snapshot time < jump time**."
24. "**never backfill 'late odds'** from a vendor's final-odds field into historical training rows (final odds embed the outcome-adjacent information you're trying to predict ahead of)."
25. "Any historical enrichment **must use** docs/04 Tier-1 patterns; Tier-2 modules (barrier-bias tables, `sectional_quant` engines, track profiler) **must not be used** to backfill training rows."
26. "Every new feature goes through the retrain ablation and the 7-day-purge walk-forward backtest before any production wiring — **no exceptions**."
27. "the AU numbers validate the $4–$15 band — **do not loosen it toward longshots**" (`research/report.md:122`).

### 5d. In-code constraints (verbatim from source)
28. `server/python/consensus_blender.py:71` — "Order matters — **do not rearrange**:" (the tier cascade).
29. `server/python/run_tips_pipeline.py:~2905` — "a validator problem **must not** kill a race-day run." Validation is loud but non-fatal.
30. `server/python/run_tips_pipeline.py:~1876` — coverage picks are "labelled honestly so product surfaces **never confuse it with the true bet**."
31. `server/python/retrain_v2.py:319` — "Benchmark column only — **never a training feature**."
32. `server/python/ml_model.py:386-387` — feature columns are persisted "so a reloaded artifact **never** shape-mismatches a grown contract."
33. `server/python/consensus_blender.py:38-42` — the crowd thresholds "**should be reviewed monthly** as more data accumulates."
34. `server/python/consensus_blender.py:251-254` — the unanimous-crowd rule: "**One data point — monitor for 4 weeks** before raising stake to STANDARD."
35. `server/python/audit_coverage_report.py` — the session is read-only and the self-test asserts every query is a SELECT; "the workflow **cannot mutate** the database" (`docs/12:193-195`).

### 5e. Evidence-discipline precedent (not a rule, but the repo's own template)
36. The **Phase-5 episode** (`docs/12:355-362`): the relative-market trio had large feature
importances and a **causal ablation of −0.0012 AUC**; it was kept only "to avoid churn", with
the explicit note that "nothing should be promoted on the strength of Phase 5". Cite this
whenever an argument rests on feature importance rather than a causal ablation.

---

## 6. Threshold & magic-number register

Every hard-coded number on the selection path. All values quoted verbatim from source.
`RTP` = `server/python/run_tips_pipeline.py`.

### 6.1 Monte Carlo engine
| Name / meaning | Anchor | Value |
|---|---|---|
| sims by field size (≤10 / ≤14 / >14) | `RTP:389-394` | 5000 / 3000 / 2000 |
| mc_api default sims | `server/python/mc_api.py:7065` | 10000 |
| standalone default sims | `racing_system_v8.3_mc.py:138` | 20000 |
| **daily MC seed** | `RTP:2340` | `int(time.time()) % 100000` (non-deterministic) |
| seed everywhere else | `racing_system_v8.3_mc.py:139`, `RTP:425` | 42 |
| Dirichlet concentration | `racing_system_v8.3_mc.py:1804` | `max(6.0, 12.0 + 1.3 × runs)` |
| concentration × going (heavy/soft/synth/firm) | `racing_system_v8.3_mc.py:1809-1815` | 0.72 / 0.82 / 1.06 / 1.04 |
| `MC_SIM_LIMITS` min/max prob, ci_alpha | `racing_system_v8.3_mc.py:145-149` | 0.001 / 0.70 / 0.10 |
| Wilson z (90%) | `racing_system_v8.3_mc.py:334` | 1.6448536269514722 |
| `FEATURE_WEIGHTS` (17 priors) | `racing_system_v8.3_mc.py:46-63` | 0.14 recent_form … 0.01 beaten_margins |
| `SOFTMAX_TEMPERATURE` / `MIN_ODDS` / `MAX_ODDS` | `racing_system_v8.3_mc.py:66-68` | 12 / 2.00 / 15.0 |
| `BASELINE_WIN_RATE`, `PRIOR_STRENGTH`, half-life | `racing_system_v8.3_mc.py:30-44` | 0.097 / 20 (30 small) / 90 d |
| feature-adjustment weights ml/soph/enh/fitness | `server/python/mc_api.py:7379` | 0.55 / 0.22 / 0.13 / 0.10 |
| adjustment caps (odds>20 / odds>10) | `server/python/mc_api.py:7381-7385` | 1.15 / 1.25 |
| **base MC / sectional MC blend** | `server/python/mc_api.py:7393` | **0.70 / 0.30** |
| win cap / win floor / place cap | `server/python/mc_api.py:7398-7403` | 60.0% / 1.0% / 90.0% |
| fitness ladder + bonuses, cap | `server/python/mc_api.py:7360-7376` | 1.12/1.06/1.02/1.00/0.95/0.90; ×1.05, ×1.03; cap 1.20 |
| place normalisation targets | `server/python/mc_api.py:~7621` | 300.0 / 200.0 / 100.0 |
| `isConfidentSelection` | `server/python/mc_api.py:292, ~7685` | top win ≥ 18%, margin ≥ 5 pts, EV ≥ 0 |
| `mc_api.CONFIG` (not env-backed) | `server/python/mc_api.py:282-296` | `prob_floor 0.02`, `prob_ceiling 0.60`, `ev_min_threshold −0.10`, `odds_min 1.01`, `odds_max 200.0`, `mid_market 4.0–15.0`, `confidence_threshold 0.18` |
| `mc_selection_score` weights | `racing_system_v8.3_mc.py:1927-1933` | 0.30 edge / 0.08 ev / 0.30 win / 0.12 top3 / 0.12 stability / 0.08 rel |

### 6.2 ML ensemble
| Name / meaning | Anchor | Value |
|---|---|---|
| production feature contract | `retrain_v2.py:152-275` == `ml_model.py:65-189` | **113** entries, identical in both |
| XGB | `retrain_v2.py:739-778` | 200 trees, depth 6, lr 0.05, subsample 0.8, colsample 0.8, min_child_weight 5, `scale_pos_weight=9` |
| LGB | same | 200, depth 6, lr 0.05, 63 leaves, min_child_samples 20, `is_unbalance`, early stop 20 |
| CatBoost | same | 200 iters, depth 6, lr 0.05, `auto_class_weights=Balanced` |
| `DateWindowSplitter` | `retrain_v2.py:~668` | min 60 d train, **14 d purge gap**, 14 d test, 14 d step |
| hardcoded seed accuracies (→ ensemble weights) | `ml_model.py:59-63` | sprint {12,15,10} / mile {22,20,24} / staying {18,21,19} per 100 |
| race-category boundaries | `ml_model.py:503-510` | <1200 sprint, ≤1600 mile, else staying |
| `predict_adjustment` | `ml_model.py:601-612` | `0.5 + ml_prob*1.5`, clamp [0.7, 1.5] |
| focal loss γ / α | `focal_loss.py` | 2.0 / 0.25 — **never wired** |

### 6.3 Calibration
| Name / meaning | Anchor | Value |
|---|---|---|
| global isotonic bounds | `calibration_model.py:30-32` | `y_min=0.01, y_max=0.95, out_of_bounds="clip"` |
| MC recalibrator clip + per-race renorm | `mc_recalibration.py:198, 204` | `clip(p, 0.005, 0.95)` |
| MC recal fit gates | `mc_recalibration.py:39` | min_field 6, min runners with sectionals 3, max_races 300 |
| CL blend guards | `RTP:595, 614` | artifact `stage == "mc"`; ≥2 quoted runners |
| CL fit | `conditional_logit.py:~335` | holdout_frac 0.2, min_field 4 |

### 6.4 Market anchor & scoring (`calibrate_and_score`, RTP:568)
| Name / meaning | Anchor | Value |
|---|---|---|
| overround / de-vig | `RTP:432-442` | `Σ(100/odds)/100`; **returns 1.0 if <2 quotes** (no vig removal) |
| **ML blend weight** | `RTP:667` | `0.20 if odds ≤ 3 else 0.40` |
| **model-weight ladder `mw`** at ≤3/≤6/≤10/≤15/≤30/>30 | `RTP:679-690` | **0.80 / 0.70 / 0.50 / 0.45 / 0.40 / 0.30** |
| calibrated / fairOdds / **modelEdge** | `RTP:692, 695, 697` | `mw·raw+(1−mw)·mkt` / `100/true_market` / `calibrated − true_market` |
| MC score normalisation scale | `RTP:646` | ×12.0 |
| **flat-MC spread threshold** | `RTP:705` (also `:~2440`, `:~2466`) | `mc_spread < 6.0` pp |
| `edge_term` clamp | `RTP:736` | ±10 |
| `prob_score` weights (flat / normal) | `RTP:738, 740` | 0.80/0.20 · 0.70/0.30 |
| intel multiplier / bonus clamps | `RTP:562-563` | [0.80, 1.30] / [−3.0, +4.0] |
| intel franking gates | `RTP:~495-560` | franking ≥60 & conf ≥0.3 → cap 3.5; ≥52 & ≥0.15 → +1.0; <40 → −1.0 |
| sectional bonus | `RTP:~761-764` | delta > 1.5 → ×0.65; soft cap 6.0 then ×0.3 |
| low-prob squash | `RTP:768-771` | `calib < 5 → ×0.30`; `< 8 → ×0.55` |
| **MC-spine blend** (flat / normal) | `RTP:774, 776` | 0.65/0.35 · **0.50/0.50** |
| flat-MC gradient penalty by top-two gap | `RTP:~785-792` | `<1.5 → ×0.30`, `<3.0 → ×0.60`, `<5.0 → ×0.85` |
| context multipliers | `RTP:719-728` | `0.95 + x/100×0.10` ×2; jockey clamp [0.85, 1.20] — **2 of 3 inert, see §7** |

### 6.5 LLM
| Name / meaning | Anchor | Value |
|---|---|---|
| horses sent to the LLM | `RTP:2379` | top **6** |
| LLM blend | `RTP:2398-2400` | `0.70·score + 0.30·ai_norm`, `ai_norm=(ai/100)·score` ⇒ effective ×[0.70, 1.00] |
| `ai_score` clamp | `llm_post_scorer.py:814` | [0, 100] |
| flat-MC LLM boost, ranks 0–2 | `RTP:~2445` | `max_score + [5.0, 3.0, 1.0]` |

### 6.6 Safety filters (`apply_safety_filters`, RTP:802)
| Name / meaning | Anchor | Value |
|---|---|---|
| conviction bonus | `RTP:836-843` | +3.0 (edge≥3 & raw≥15) / +2.0 (≥2 & ≥12) / +1.0 (≥1 & ≥10) |
| class odds caps G1 / G2-G3 / Listed / other | `RTP:817-824` | $25 / $25 / $30 / $30 |
| dominance override | `RTP:~866-873` | gap ≥1.5, odds ≤30, (edge>1 \| raw≥10 \| calib≥10) |
| favourite discipline | `RTP:878` | `is_favourite & fav_odds ≤ 20 & edge > 0` |
| `_llm_top_pick` | `RTP:883` | unconditional pass |
| **banker bypass** | `RTP:888` | `banker_flag & banker_score ≥ 70` |
| distance-range filter | `RTP:~901` | ± 200 m of past winning range |
| class-cap merit override | `RTP:~908` | `odds ≤30 & edge >1 & raw ≥8` |
| longshot keep / blocks | `RTP:~917-931` | keep `odds≥15 & edge>2 & raw≥8`; block `≥30 & raw<10 & edge≤0`; block `≥20 & edge≤0 & raw<8`; black-type block `≥10 & calib<10 & edge≤0` |
| top-3 cut / fallback | `RTP:~935-938` | `filtered[:3]`; fallback = 3 shortest prices |

### 6.7 Confidence, staking, BET gate
| Name / meaning | Anchor | Value |
|---|---|---|
| absolute longshot → low | `RTP:958` | `odds > 30` |
| high / medium | `RTP:965-968` | `ev>0 & edge>1.0` / `edge>0` |
| pace-clarity demotion | `RTP:~974` | `pace_clarity < 0.35` caps high→medium |
| **entire live staking rule** | `RTP:1007-1015` | `high→2u`, `medium→1u`, `low→0u` |
| flat-MC confidence gate | `RTP:~2466-2469` | `mc_spread < 6.0` ⇒ all top-3 forced low ⇒ 0u |
| **hard price ceiling** | `RTP:1812` | `odds > 15 → NO_BET` |
| band gates | `RTP:1816 / 1821 / 1825` | `<$3`: edge≥4 & prob≥30 · `$3–5`: edge≥2.5 & prob≥15 · `$5–15`: edge≥3 & prob≥10 |
| low-confidence price block | `RTP:1828` | `confidence == low & odds > 12` |
| intelligence override | `RTP:1727` (`_check_intelligence_override`) | rank 1 & bonus ≥3.0 & franking ≥55 & trend ≠ DECLINING |
| coverage probability-first | `RTP:~1906-1911` | `1 < odds ≤ 20` & `prob ≥ 8` |
| coverage exception band | `RTP:1857` | `20 < odds ≤ 30`; ≤$25 → prob≥13, edge≥2, conf≠low; else prob≥16, edge≥4, conf=high |

### 6.8 Crowd / convergence gate
| Name / meaning | Anchor | Value |
|---|---|---|
| candidate set | `RTP:~2655` | `crowd_score ≥ 50`, top 3 |
| model-score classification | `consensus_blender.py:~222-227` | `≥15 → CONFIRMED`; `≥8 → CROWD_ONLY`; else WEAK; non-candidate leader → MODEL_ONLY |
| CROWD_ONLY bet thresholds | `consensus_blender.py:~246-249` | `cs > 70` and `cs ≥ 50` |
| CROWD_ONLY_WEAK promotion | `consensus_blender.py:250-255` | `cs ≥ 100` — **one anecdote, 2026-04-06** |
| MODEL_ONLY | `consensus_blender.py:~261` | **always False** ("archetype trap") |
| stake labels | `RTP:~2706-2712` | FULL / STANDARD / REDUCED / NONE — never numeric |
| fail-closed | `RTP:2751-2757` | crowd import failure ⇒ every pick NO_BET |
| dormant V2 constants | `consensus_blender.py:33-35, 43-44` | `STRIDE_THRESHOLD 65.0` (**scale bug**, compared to a 0–25 score), `CONSENSUS_THRESHOLD 65`, `MARKET_THRESHOLD 60`, `MIN_MODEL_SCORE_FOR_BET 8.0`, `MIN_CONFIRM_CONVERGENCE_SCORE 55.0` |
| dormant V2 weights (env-overridable) | `consensus_blender.py:25-30` | stride 0.50 / consensus 0.30 / market 0.20 |
| injections (dormant) | `consensus_blender.py:94-172` | consensus [−8.0, +12.0]; market STEAM +8.0 / FIRMING +4.0 / DRIFT −3.0 / STRONG_DRIFT −5.0 |

### 6.9 Day aggregates, unused staking libraries, backtests
| Name / meaning | Anchor | Value |
|---|---|---|
| best bets | `tips_day_aggregates.py:~70-89` | top 3 high-conf rank-1; fallback top 2 medium with edge>0 |
| **value plays** | `tips_day_aggregates.py:~91-103` | rank-1, `edge > 3`, **`4 ≤ odds ≤ 15`**, top 5 (validated band is `$2–$15` — mismatch) |
| bankers | `tips_day_aggregates.py:~105-111` | `confidence == high` & `odds ≤ 4` |
| predictability modifier | `tips_day_aggregates.py:32-68` | [0.5, 1.2], **ordering only** |
| Kelly (implemented, **never called live**) | `racing_system_v8.3_mc.py:35-41, 309-326` | `KELLY_FRACTIONS {HIGH 0.30, MEDIUM 0.20, LOWER 0.10}`, default 0.25, `MAX_KELLY_STAKE 0.05` |
| `STAKING_TIERS` / `STAKING_CONFIG` / caps | `racing_system_v8.3_mc.py:110-153` | lock 70/18→3u, strong 55/14→2u, bet 40/10→1u; bankroll 100.0, max_daily_units 30; per-track 12 units, `TIPS_PER_TRACK 6` |
| `portfolio_risk` (**zero importers**) | `portfolio_risk.py:39, 61-71` | bankroll 10000, daily 15%, single 5%, ≤10 concurrent, kelly_fraction 0.25 |
| walk-forward backtest | `walk_forward_backtest.py:~100-157, 284-286` | min_train 3000, test 500, **gap 7 d**, stake 100, thresholds {0.05,0.10,0.15,0.20,0.30}, ECE 10 bins |
| metro backtest | `backtest_v2_metro.py:157-166` | 6 strategy bands, `STAKE = 100` |
| shadow P&L | `shadow_pl_tracker.py:130, ~245-255, 323` | `BET_TIERS {CONFIRMED, CROWD_ONLY, LOCK}`, PLACE settles **−1**, `MIN_BETS_REPORTABLE 200` |

---

## 7. Documentation drift register

Severity: **HIGH** = will mislead a change author into a wrong decision · **MED** = will
mislead a reader/operator · **LOW** = cosmetic.

| # | Drift | Evidence | Severity |
|---|---|---|---|
| D-1 | **`README.md`'s front-page architecture describes the DORMANT V2 design.** The mermaid diagram and the 50/30/20 pillar table (`README.md:56-81`) depict weighted blend → `LOCK/CONFIRM/FLAG/SKIP` → EV gate. Production runs V3 crowd-first. | `RTP:2666` calls `confirm_with_model`, `:2675` calls `crowd_bet_decision`; `:2697-2700` pin `consensus_injection = 0`, `market_injection = 0`, `market_signal_score = 50.0`. `blend_scores` / `determine_convergence_tier` / `apply_convergence_gate` have no caller in the pipeline. `docs/01:34-36` and `docs/08:28-32` disclose this; the README does not. | **HIGH** — this is why the orchestrator brief for this very run inherited the 50/30/20 framing |
| D-2 | **Six live env flags are missing from `.env.example`.** `STRIDE_CL_BLEND`, `STRIDE_PREDICTABILITY_GATE`, `STRIDE_ACCURACY_WEIGHTS`, `MC_ENABLE_SECTIONAL_FRANKING`, `MC_ENABLE_JOCKEY_EFFICIENCY`, `LLM_MODEL`. Conversely `CONSENSUS_CONFIRM_THRESHOLD=45` is in the template with **zero** code references (correctly flagged by `docs/08:291`). `docs/08:282` claims its table is "From `.env.example` and code" — it is ahead of the template. | grep of `.env.example` vs code | **HIGH** for Phase 4 — a new flag added the same way will also be invisible; the ticket must update `.env.example` |
| D-3 | **Doc-vs-doc contradiction on the tipster feedback loop.** `docs/11:150` says it is "not yet closed"; `docs/08 §2.4` and `docs/12 §4c` say it IS (opt-in). | Code settles it: `consensus_agent.py:117` defines `load_accuracy_multipliers`, gated at `:124` on `STRIDE_ACCURACY_WEIGHTS`, reads `FROM source_accuracy` at `:134`, called at `:1308`, composed at `:332`. **The loop IS closed, opt-in.** `docs/11:150` is stale. | MED |
| D-4 | **Doc line anchors have drifted in touched files.** `docs/09` cites `calibrate_and_score:555`, `apply_safety_filters:740`, `evaluate_bet_candidate:1632`, `store_selections_in_db:1156`; actual **568 / 802 / 1778 / 1245**. `docs/02` cites the MC seed at `:2256`; actual **2340**. | verified this session | MED — pre-disclosed at `docs/README.md:76-80` ("grep the symbol if a number has drifted"), but later agents must **re-grep, never cite the docs' numbers** |
| D-5 | **`selections` INSERT column count.** Docs say "~107 columns" in three places (`docs/02:85`, `docs/03:169`, `docs/09:222`). Two recon agents disagreed (115 vs ~107). **Re-counted this session from the statement at `RTP:1329-1398`: 115 column names, 115 unique.** | verified | MED |
| D-6 | **Documented "five calibration layers" overstates what can fire.** `docs/05 §5` tables five. In this tree only the **global isotonic** (L3) can fire, and only if the git-ignored pickle exists; L1 is deliberately disabled at `ml_model.py:566`, L2 and L4 need git-ignored artifacts, L5 is off-path. A **sixth** layer — the opt-in conditional-logit blend at `RTP:591` — is absent from that table. | verified per-layer | MED — a change author reading docs/05 will over-estimate the calibration stack |
| D-7 | **`retrain_v2.py:151` comment still says the contract has "(77 total)".** The list immediately below it has **113** entries. | `ast.literal_eval` | LOW (comment only) but directly adjacent to the contract |
| D-8 | **`docs/README.md:53-57` says "Two modules carry executable self-tests".** CI now runs **eight** (`.github/workflows/ci.yml:33-42`); seven more modules carry named self-tests. "No pytest/unittest" remains true. | verified | LOW |
| D-9 | **Scale/count figures.** `docs/README.md:5` "~150 modules (~72k lines)" → actual **145 / 79,634**. `docs/06:21` and `docs/11:119` "`mc_api.py` 7,782 lines" → **7,833**. `docs/08:36` "`consensus_agent.py` 1,570" → **1,650**. README/`docs/04` "110 features" → **113** (`docs/04:4-5` already corrects this; other docs did not follow). | measured | LOW |
| D-10 | **Untouched files are exact.** All six `racing_system_v8.3_mc.py` citations in `docs/06` land precisely on the cited symbol; `form_franking.py` 1,542 and `franking_graph.py` 1,200 are exact. | verified | — (useful sanity anchor: line drift correlates only with files touched by the Phase-5 / hit-rate commits) |

**No behavioural doc claim drifted.** Every threshold, weight, cap and gate quoted in the docs
verified byte-for-byte. The drift is confined to (a) the README's architecture diagram,
(b) the config template, (c) one stale module-reference one-liner, (d) line anchors and counts.

### 7b. Code defects found on the selection path (not doc drift — real, verified)

These are behaviours the docs describe as intended that the code does not deliver. All were
established by exhaustive grep over producing and consuming modules; **none was verified by
execution.**

1. **`fitnessReadinessScore` context multiplier is inert.** `mc_api` writes it only nested as
   `fitnessData.fitnessReadinessScore` (`server/python/mc_api.py:7545`); `RTP:719` reads it at
   top level ⇒ always the default 50 ⇒ `fitness_mult ≡ 1.00`.
2. **`jockey_momentum_adjustment` context multiplier is inert.** It is an mc_api *feature*
   (`server/python/mc_api.py:5848`), never present in the result dict ⇒ `jockey_mult ≡ 1.00`.
3. **`trackBiasPoints` is on the wrong scale for its multiplier.** Points range ≈ −25…+45
   (`track_bias_points.py:891-916`) fed into a `/100.0` map designed for 0–100
   (`RTP:721-722`) ⇒ near-constant **×0.95**, not the documented ×0.95–1.05. All three
   "context multipliers" are therefore effectively a uniform ~5% shrink.
4. **`rawModelProb` is context-shrunk at `RTP:729`, *after* `modelEdge` was computed at
   `RTP:697`.** `raw_model_pct`, `win_pct` and `edge_pct` are published on three mutually
   inconsistent scales, and every downstream raw-prob threshold (conviction 15/12/10, bet gate
   30/15/10, longshot ≥8) silently operates on a ~5%-deflated number.
5. **Unquoted runners never receive the ML blend into `winPercentage`** (`RTP:699-701`) — they
   stay on the isotonic-corrected, pre-blend MC scale.
6. **The LLM blend is mathematically incapable of raising a score** (`RTP:2398-2400`).
7. **Daily runs are not reproducible** (seed `time.time()`-derived, `RTP:2340`) while every
   backtest uses 42. Any A/B must fix the seed or average over many runs. The existing
   proof-run idiom is `--skip-db-store` + `--output-suffix`.
8. **`consensus_blender.STRIDE_THRESHOLD = 65.0` is compared against a 0–25 score**
   (`consensus_blender.py:33`, acknowledged in the comment at `:37-42`) — the V2 tier path is
   effectively unreachable.
9. **The `CROWD_ONLY_WEAK & crowd_score ≥ 100` promotion rests on one named race**
   (`consensus_blender.py:250-255`, 2026-04-06); the "monitor for 4 weeks" window elapsed
   long ago and no review is recorded.
10. **Interaction-feature formulas are duplicated** between `retrain_v2.build_feature_matrix`
    and `RTP:~2306-2319` with no shared helper — a train/serve drift hazard.
11. **`selections.kelly_stake` is a decoy column.** The column name is at `RTP:1334`; the value
    bound to it (`v18`, `RTP:1462`) is `int(pick.get("staking","0u").replace("u",""))` — the
    2/1/0 unit count, not a Kelly fraction. *(Both recon agents were right; they were citing
    different lines of the same statement.)*
12. **The odds-band signal is dropped on the floor.** The comment at `RTP:~743-745` says the
    odds-band adjustment "is deferred to stake sizing (Kelly)" (audit fix #1) — but stake
    sizing is `high→2u/medium→1u/low→0u` and never applies it.
13. **TLS verification is disabled** on the Racing API session (`download_racecards.py:59`),
    acknowledged in `docs/03 §7`.

---

## 8. Glossary

Domain terms **as this repo uses them**, each with its implementation.

**Value & market**
- **edge** — `calibrated_win_probability − fair_market_probability`, in **percentage points**, not a ratio. Deliberately computed from the market-anchored probability so a flat simulation cannot fabricate it. `RTP:697` (`h["modelEdge"]`). *Three incompatible definitions coexist:* mc_api's `winPct − 100/odds` with **no de-vig** (`mc_api.py:7636`), the pipeline's `calibrated − true_market` (`RTP:697`), and the backtests' `norm_prob − 1/SP`.
- **EV** — `calibrated_prob / fair_market_prob − 1`, a **ratio** EV against the de-vigged market (`RTP:955`). mc_api's `EV = (winPct/100)·odds − 1` (`mc_api.py:7637`) is a different quantity on a different scale. Do not compare them.
- **overround** — total implied probability across the field (>1.0). `Σ(100/odds)/100`, **returns 1.0 (no vig removal) if fewer than 2 quotes**. `calculate_overround`, `RTP:432`. De-vig is **proportional only** — no Shin, power or Harville variant exists anywhere in the repo.
- **true_market / fair odds** — `(100/odds)/overround`; `fairOdds = 100/true_market` is the **market's** fair price, not the model's. `RTP:673-674, 695`.
- **market anchor** — blending the model probability toward the de-vigged market at a price-dependent model weight `mw` (0.80 at ≤$3 down to 0.30 above $30). The mechanism that makes STRIDE a value system rather than a tipping system. `RTP:679-692`.
- **favourite–longshot bias (FLB)** — short prices underbet, long prices overbet. Corrected by `mc_recalibration.py` (inert in this tree). AU magnitude quantified in `research/report.md §2.2`.
- **steam / drift** — shortening / lengthening price. **Two incompatible taxonomies coexist and must not be conflated**: the convergence pillar's `classify_signal` (`odds_movement.py:211`, ≥+20% STEAM … ≤−15% STRONG_DRIFT) and the MC feature side (`market_analysis.py` / `market_velocity.py`, ±10%/±25% → probability multipliers 0.85–1.25).
- **market efficiency segment** — `ultra_efficient / efficient / moderate / inefficient / thin`, each with its own minimum edge (0.05 / 0.03 / 0.02 / 0.01 / 1.0). `market_efficiency.py:18-21` — **no production caller**.

**Probability engines**
- **Plackett-Luce** — the ranking distribution used by the base simulator; finishing order drawn by the **Gumbel-max trick**. `racing_system_v8.3_mc.py:1781` (`simulate_race_monte_carlo`), sampling at `:1855-1856`.
- **Dirichlet concentration** — per-simulation uncertainty knob, `max(6.0, 12.0 + 1.3 × n_runs)` scaled by going. More evidence ⇒ tighter samples. `racing_system_v8.3_mc.py:1804-1815`.
- **realistic / sectional overlay** — the second production simulator: mixture noise (Normal + downside Normal + Student-t df 4.5), per-runner sigma clamped [0.6, 2.0], 4-phase energy depletion, collapse events. `realistic_simulate.py`; blended **0.70 base + 0.30 overlay** at `mc_api.py:7393`.
- **flat MC** — an *uninformative* simulation: raw-probability spread across the field **< 6.0 pp**. Triggers a gradient score penalty by top-two gap (×0.30/×0.60/×0.85), forces all tips to LOW confidence (⇒ 0u), shifts the MC-spine blend 50/50 → 65/35, and hands the LLM a rank-based score boost. `RTP:703-705, ~774-796, ~2438-2469`.
- **MC spine** — the MC engine's own normalised selection score, blended **50/50** with the wrapper's score "so the wrapper refines rather than replaces the engine's ranking". `RTP:776`; engine score at `racing_system_v8.3_mc.py:1908` (`mc_selection_score`).
- **Wilson interval** — the CI used by every production engine (α = 0.10). The dead root `monte_carlo.py` uses bootstrap CIs instead — a known inconsistency.

**Calibration**
- **OOF isotonic** — out-of-fold isotonic fitted on pooled walk-forward validation predictions, one per base model, so "calibration never sees data the model was tuned on". Fitted in `retrain_v2.py`; **deliberately not applied at inference** (`ml_model.py:566`).
- **purge gap** — the dead zone between train and test windows. **14 days** in `retrain_v2.DateWindowSplitter`; **7 days** in `walk_forward_backtest.py`; **zero** in `backtest.py` (documented weakness).
- **double calibration** — layer-1 per-model isotonic then layer-2 isotonic on the ensemble. `double_calibration.py`; needs a git-ignored artifact.
- **PAV (Pool-Adjacent-Violators)** — the custom monotone fit used by `mc_recalibration.py:157`.
- **conditional logit (CL) blend** — Benter's second stage, `P_i ∝ exp(α·ln m_i + β·ln q_i)`, normalised within race. `conditional_logit.py:~92`; hooked at `RTP:591-629` behind `STRIDE_CL_BLEND`. **On HOLD** (constraint 14).
- **Brier score / ECE** — the calibration metrics of record. Brier 0.0834 on the README backtest.

**Features & ratings**
- **λ (lambda) decay** — Ward-Smith velocity-decay constant, `λ = −ln(v_final/v_peak)/(sections × 200)`. Low = stayer, high = fader. `backfill_lambda_targeted.py:113`; column `sectional_times.lambda_decay`.
- **SVI (Sustained Velocity Index)** — mean of last-3-section speeds ÷ mean of all sections. `>1.05` closer, `<0.95` fader. `backfill_lambda_targeted.py:132`.
- **RSI (Race Shape Index)** — first-half ÷ second-half time. `backfill_lambda_targeted.py:148`.
- **trip cost** — extra ground covered wide, `(barrier_lane − 1) × 1.8 m × turns × 0.65`, converted to seconds. `backfill_lambda_targeted.py:163`.
- **z-score (sectional)** — per-race, per-section standardised speed, requires ≥3 runners. `sectional_quant.py`; columns `z_200m … z_800m`.
- **SASR upgrade** — finishing-speed percentile vs par → a mu shift capped ±0.15. `sectional_quant.py:268`.
- **sectional** — a split time for a race segment. Sources: NSW pidata `.tol` GPS telemetry, QLD CSV (**Cloudflare-blocked**), racing.com GraphQL. Coverage is only **~47% of rows**.
- **Glicko-2** — Glickman's rating with deviation and volatility, maintained **per surface**. `glicko2_elo.py` — self-tested, **no production caller**.
- **prep cycle** — a campaign, split wherever the gap between runs is **≥ 60 days**. `fitness_peak.py`; published as `intelligence/prep_cycles.json`.
- **bounce** — the regression that often follows a career-best. `is_bounce_candidate` in `form_feature_builder.py`.
- **barrier trial** — an unofficial practice race; five derived features. Stored in `barrier_trial_results`.
- **franking** — the flagship concept: validating past form by what the horses it *beat* did **next**. Beaten rivals winning later ⇒ franked; flopping ⇒ anti-franked (composite ×0.75). Class-weighted (G1 2.0 … Maiden 0.7). **Four independent implementations**: `form_franking.py` (ELO), `franking_graph.py` (NetworkX BFS + noisy-OR + PageRank + Louvain), `mc_api.calculate_sectional_franking_value` (env-gated, off in production), and the unwired gen-3 `intelligence/build_form_franking.py`. Production reads the gen-2 reconciler `stride_agent_form.py`.
- **luckless** — a horse with a legitimate excuse for a poor last start, detected from stewards'/analyst comments by ~90 keyword rules → a 0–100 forgive score; uplift capped 0.12. `luckless_analyser.py`.
- **banker** — a dominant favourite worth backing *regardless of edge*. Composite 0–100; qualification score ≥55, MC win ≥0.30, odds ≤$3.50, field ≥5, not a maiden. **A banker with score ≥ 70 bypasses all tip filters** (`RTP:888`). `banker_detector.py`. *(The internal composition of the score was not independently verified by any recon agent — only the `≥70` bypass was.)*
- **track bias points** — static per-track config scored as points (barrier +15/+8/0/−10, pace +12/+6/0/−8, jockey +12/+6, trainer +10/+5; ≥25 = strong fit). `track_bias_points.py`, surfaced as `trackBiasPoints`. **Scale-mismatched at its consumer — see §7b.3.**
- **pace clarity** — entropy-based measure of how predictable the race shape is; used only to **cap** confidence (`< 0.35` demotes high → medium) and deliberately *not* in the feature contract. `race_context.py`, applied at `RTP:~974`.
- **predictability** — a *race-level* selectivity block (score, `highly_predictable/normal/chaotic`, modifier 0.5–1.2), computed from pre-race market/card facts only. `predictability_meta_model.py` via `compute_race_predictability` (`RTP:980`). **Ordering only**, behind `STRIDE_PREDICTABILITY_GATE`.

**Convergence & the output contract**
- **the three pillars** — STRIDE 50% / Consensus 30% / Market 20%. The **design** weights; **dormant in production**.
- **convergence gate** — the arbitration deciding whether crowd and model agree enough to bet. **V2 (dormant):** blend → tier `LOCK/CONFIRM/FLAG/CROWD_OVERRIDE/SKIP` → `apply_convergence_gate`. **V3 (live):** `confirm_with_model` + `crowd_bet_decision`. `consensus_blender.py`; live call sites `RTP:2638-2757`.
- **archetype trap** — the name for a `MODEL_ONLY` pick. Always NO_BET (`consensus_blender.py:~261`). *This is the switch that discards the model's own best value bet whenever no tipster agrees.*
- **crowd_score vs consensus_score** — **different numbers.** `crowd_score` (0–100) is the simple mention rate from Perplexity/Claude web research and is what the **live** gate consumes. `consensus_score` (0–100) is the weighted panel formula, defaulting to a neutral 35.0 for zero-mention horses. `consensus_agent.calculate_consensus_score`.
- **bucket** — what *kind* of tipster is talking. `BUCKET_WEIGHTS` (`consensus_agent.py:78-87`): `stable_watcher` 1.5, `value_analyst` 1.4, `market_reader` 1.4, `ratings_analyst` 1.3, `speed_analyst` 1.3, `track_specialist` 1.2, `form_analyst` 1.0, `retail_punter` 0.7.
- **BET / NO_BET contract** — the invariant that every race resolves to exactly one, with a reason, and that the bet must be the `raw_model_leader` — no hidden substitutes. Asserted by `validate_tips.py`, re-stamped by `backfill_tips_contract.py` (which imports the live functions so the logic cannot drift). Contract version string `"v2-explicit-bet-coverage"`.
- **raw_model_leader / bet_pick / coverage_pick** — three distinct roles. Leader = highest pre-filter score, always reported. `bet_pick` = the leader *only if* it survives `evaluate_bet_candidate` **and** the crowd gate. `coverage_pick` = the display/guide horse from a fallback cascade, labelled honestly.
- **selection_origin** — the honesty label: `model_backed | tip_only | filtered_substitute | market_unavailable | raw_model_leader | crowd_gated | crowd_promoted`.
- **conviction bonus** — pre-filter score bump: +3.0 / +2.0 / +1.0. `RTP:836-843`.
- **stake_recommendation** — `FULL / STANDARD / REDUCED / NONE` from the crowd gate. **A label only** — distinct from, and never reconciled with, the `2u/1u/0u` confidence staking.
- **intelligence override** — a rank-1 pick with `intel_bonus ≥ 3.0`, franking ≥ 55 and a non-declining prep trajectory is promoted to BET **regardless of the edge gate**, checked *before* the standard gates. `_check_intelligence_override` (`RTP:1727`), used at `RTP:~1795`.

**Evaluation & risk**
- **shadow P&L** — level-stakes P/L tracked for **every** convergence tier, *including the ones the system refuses to bet*, so gating decisions stay evidence-based. `shadow_pl_tracker.py`.
- **staged retrain** — a new model written to `models/staging/` and never promoted automatically. `learn_from_results_v2.py`.
- **fractional Kelly** — the staking method *of record in the docs*: `KELLY_FRACTION_DEFAULT = 0.25`, `MAX_KELLY_STAKE = 0.05`. Implemented at `racing_system_v8.3_mc.py:309-326` and (separately) in `portfolio_risk.py`. **Neither is on the live path** — the daily pipeline stakes 2u/1u/0u by confidence tier.
- **strategy band** — a backtest filter defined by (odds range, min edge). The validated live band is **edge ≥ 3%, $2–$15**. `backtest_v2_metro.py:157-164`.
- **ablation** — dropping a feature block on identical folds to get a **causal** AUC delta, as opposed to reading feature importance. `retrain_v2.run_ablation`.
- **H2H (head-to-head)** — apples-to-apples comparison on *identical* races where the stored model covers the full field. The mechanism that overturned the LambdaRank ranker's headline result (36.0% walk-forward vs 33.6% on H2H races).
- **data-quality gate** — `GREEN / AMBER / RED / CORRUPTED`. `sp_health.py`, `results_health_check.py`.
- **leakage tiers** — the repo's three-way classification (`docs/04 §4`): **Tier 1** rigorously as-of and safe for training; **Tier 2** inference-safe but leaky if reused for historical backfill; **Tier 3** structural hazards (name-only caches, `datetime.now()` fallbacks).

---

## 9. Open questions

Everything here is **unknown**, not "probably fine". Later phases must treat each as an
explicit assumption to be stated, not a fact to be relied on. The root cause of most of them
is the same: no database, no model artifacts, no data, and no test suite are present, so
nothing below can be settled by source reading.

**Runtime behaviour that cannot be confirmed without execution**
1. Whether `models/isotonic_calibrator.pkl` exists on the production box at all. If it does
   not, the "global isotonic" layer is silently skipped and the live chain is
   *ML blend → market anchor* with **no calibration**. The whole edge computation rests on
   this and it cannot be checked from the repo.
2. Same for `models/double_calibrator.pkl`, `models/racing_ensemble_v2.pkl`'s stacking
   learner, and `server/python/calibration_model.json`. Which of the six calibration layers
   actually fires in production is **unknown**.
3. Whether the three "inert" context multipliers (§7b.1–3) are truly inert in production, or
   whether some other producer writes `fitnessReadinessScore` at top level. Grep says no
   producer exists in-repo; a runtime print is the only proof.
4. The library versions actually installed. Only `numpy==1.26.4` is pinned; the live ensemble
   is a pickle of unpinned xgboost/lightgbm/catboost. **Whether the current artifact even
   unpickles** under the installed versions is unknown.
5. Whether the `.env` on the production box overrides any of the six env-tunable consensus
   weights, or re-enables `MC_ENABLE_SECTIONAL_FRANKING` / `MC_ENABLE_JOCKEY_EFFICIENCY`.

**Empirical facts nothing in the repo measures**
6. **The realised ROI and strike rate of `bet_pick` as actually shipped.** No harness computes
   it (section 3). The README's 33.7% / −4.2% and 9.9% / +12.3% describe the **ML ensemble
   under strategy bands**, not the live wrapper + filters + crowd gate. Do not treat them as
   measurements of the current system.
7. **How often the crowd gate flips a decision**, in each direction, and the P&L of the flipped
   sets. `crowd_gated` / `crowd_promoted` are stamped into the output but nothing aggregates
   them. In particular: **what would the `MODEL_ONLY` picks have returned?** That is the single
   highest-leverage unknown in the system.
8. **Whether the `odds > 15` hard ceiling is costing or saving money.** It is the largest ROI
   constraint in the code and has no supporting measurement in the repo.
9. **Whether the `CROWD_ONLY_WEAK & crowd ≥ 100` rule survived its own "monitor for 4 weeks"
   window.** No review is recorded anywhere.
10. **How often `mc_is_flat` fires** (and therefore how often the whole card is zero-staked and
    the LLM's ranking silently takes over via the `[5,3,1]` boost). Unknown.
11. **How often the `apply_safety_filters` fallback** (three shortest-priced runners) fires.
    That path directly contradicts the value philosophy and nothing counts it.
12. **How often the intelligence override fires**, and its realised P&L. It bypasses the entire
    edge gate.
13. The **current row counts and date coverage** of `race_results_history`, `sectional_times`,
    `training_data`, `training_view_v2`, `selections`, `stride_tip_results`. The docs' figures
    stop at ~2026-04-18 and `prediction_audit` was reported at 260 rows; whether the
    `prediction_audit_unique_key.sql` migration has since been applied and the table has begun
    filling is **unknown**, and it gates any recalibration or CL refit.

**Design intent that the source does not settle**
14. Whether the **`mw` ladder** was fitted or hand-set. The in-code comment cites a "Kelly
    audit" ($1-3 horses win 41%, model predicts 17%) but no fitting script, grid search or
    holdout evaluation for those six numbers exists in the repo. Same for the
    `evaluate_bet_candidate` band thresholds (4 / 2.5 / 3 and 30 / 15 / 10) and the crowd
    cutoffs (50 / 15 / 8 / 70 / 100). Treat all of them as **hand-tuned and unvalidated**.
15. Whether the **0.70/0.30 MC↔sectional blend** and the **0.50/0.50 MC-spine blend** were ever
    swept. `calibration_backtest.py` grid-searches the *former*; nothing searches the latter.
16. Whether the divergence between the live value-play band (`$4–$15`,
    `tips_day_aggregates.py`) and the validated backtest band (`$2–$15`) is deliberate.
17. Whether the **`selections`/`convergence_output`/`prediction_audit` schemas in Neon** match
    what the INSERTs assume. Only 8 of ~20 tables have a `CREATE TABLE` anywhere in the repo;
    the rest are reconstructable only from queries. Any additive-schema ticket must state this
    as an assumption.
18. Whether anything downstream (the excluded TypeScript frontend) reads fields this run might
    touch. The frontend is not in the repo, and `mc_api`'s stdin/stdout JSON mode and the
    `selections` table are both contracts with an unseen consumer.
19. Whether the `stake_recommendation` label (FULL/STANDARD/REDUCED/NONE) is consumed by that
    frontend. In this repo it is written and never read.
20. What the **banker score's internal composition** is. No recon pass verified it beyond the
    `≥ 70` filter bypass at `RTP:888` — a filter bypass driven by an unaudited composite.

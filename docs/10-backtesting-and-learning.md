# Backtesting, Continuous Learning & Risk

STRIDE closes the loop after every race day: results come back in, tips are scored,
the training view refreshes, a retrain is staged, and several backtesters answer
"is the edge real?" This document covers the evaluation machinery and the
staking/risk layer.

Related docs: [ML training & calibration](05-ml-training-and-calibration.md) ·
[Consensus & market](08-consensus-and-market.md)

---

## 1. The backtest suite

Common ground rules across all backtesters: **flat $100 stakes, settled at Starting
Price (SP), one bet per race** (the maximum-EV qualifying runner), no commission
modelled, probabilities race-normalized before edge is computed
(`edge = model_prob − 1/SP`).

| Script | What it does | Temporal integrity |
|---|---|---|
| `backtest.py` (v1) | Trains a fresh GBM+XGB+LR ensemble per rolling window (6-month train / 1-month test, ≤ 7 windows), 43 hand-built features, sweeps 13 strategy bands | Horse history strictly `< race_date`; stats from train slice only; **no purge gap** (train_end == test_start) |
| `backtest_v2_metro.py` (v2) | Loads the saved v2 ensemble pickle, scores metro races from `training_view_v2`, runs the 6 README strategy bands, computes calibration bins + Brier + confidence bands. **This produces `examples/backtest_summary.json`** — the README numbers | Scores a fixed trained model on later data |
| `walk_forward_backtest.py` | The rigorous one: expanding-window CV (min train 3,000 rows, test 500, **7-day purge gap**, leakage assert), fresh `RacingMLModel` per fold, AUC/Brier/log-loss/ECE + ROI@{5,10,15,20,30}% thresholds with t-distribution 95% CIs | Strongest guarantees in the repo |
| `calibration_backtest.py` | Grid-search (18 configs) of the sectional-vs-market MC blend ratio, scored by log-loss/Brier vs a market baseline | Hyperparameter search, not a forward test |
| `backtest_prep_profiles.py` | A/B test: universal prep bonus vs individual horse-prep profiles, metric = winner/loser score separation | Walk-forward at horse level (`runs[:i]` only) |

### The strategy bands (`backtest_v2_metro.py:157-164`)

These define the README's ROI table:

| Label | Odds band | Min edge |
|---|---|---|
| **Value Edge 3%+ ($2–$15)** | 2.0–15.0 | 0.03 |
| Mid-Range $3–$8 5%+ edge | 3.0–8.0 | 0.05 |
| Short Price $2–$5 3%+ edge | 2.0–5.0 | 0.03 |
| Big Value $5–$15 5%+ edge | 5.0–15.0 | 0.05 |
| Top Pick (highest prob) | — | top pick |
| All $2–$20 positive edge | 2.0–20.0 | 0.001 |

Note the **live** betting gate (`evaluate_bet_candidate`,
`run_tips_pipeline.py:1632`) is stricter than any backtest band — see
[Scoring & output §5](09-scoring-and-output.md).

`scripts/plot_calibration.py` renders `examples/backtest_summary.json` into the
README's calibration plot.

---

## 2. Continuous learning (`learn_from_results_v2.py`)

The canonical after-race-day workflow (PID-locked so only one runs at a time;
default window = yesterday−7 … yesterday):

1. **Gap detection** — diff `prediction_audit` against results, and `sectional_times`
   coverage against `race_results_history`, producing result-gap dates and
   sectional-gap (date, track, source) targets.
2. **Ingest** — fetch results for gap dates (→ `race_results_history`), run the
   matching sectional collector per gap target.
3. **Reconcile tips** — score tip accuracy into `stride_tip_results`.
4. **Refresh** — rebuild the `training_view_v2` materialized view, but only if new
   results or sectionals actually arrived.
5. **Staged retrain** — if (and only if) results changed AND ingest succeeded AND
   the view refreshed, run `retrain_v2.py` into
   `models/staging/<timestamp>.pkl`. **Retrains are staged, never auto-promoted** —
   a human promotes the artifact.

Each run persists a JSON summary under `research/learning_runs/`.

---

## 3. Shadow P&L (`shadow_pl_tracker.py`)

Tracks level-stakes P/L for **every** convergence tier — including the ones the
system deliberately does not bet (FLAG, CROWD_OVERRIDE, MODEL_ONLY) — to test
whether the gating logic is leaving money on the table:

- `record`: pulls tier assignments from `convergence_output` (or infers from the
  tips JSON), inserts PENDING rows into `stride_tip_results`; `BET_TIERS =
  {CONFIRMED, CROWD_ONLY, LOCK}`, everything else `is_shadow=true`.
- `results`: settles against `prediction_audit` / `race_results_history` — win-only
  staking (WIN = SP−1 units; PLACE and LOSS both = −1).
- `report`: ROI by tier; a tier is only reportable at **≥ 200 settled bets**.

This is how the dormant V2 tiers keep being evaluated with real results even though
the live gate is crowd-first (see [Consensus & market §6](08-consensus-and-market.md)).

---

## 4. Weight optimization & meta-analysis

- **`weight_optimizer.py`** — two parts:
  1. `FeatureWeightOptimizer`: tunes the 11 probability-adjustment multipliers used
     by the live scorer (pace, class-drop, elite connections, steam, sectional speed,
     jockey streaks…) by minimizing negative log-likelihood on `training_data` with
     L2 pull toward defaults (λ=0.01, L-BFGS-B, bounded). Saves
     `models/optimized_weights.json`.
  2. `BarrierAnalyzer`: empirical barrier win rates by (track, distance band,
     barrier) with hardcoded fallbacks when data is thin.
- **`research/` scripts** — deep-dive diagnostics:
  - `performance_autopsy_last21days.py`: classifies every losing tip into failure
    modes (`wrong_favourite`, `barrier_blindspot`, `going_miss`, `franking_miss`,
    `prep_cycle_miss`, `price_range_miss`…) and audits sectional (< 70% fails) and
    Betfair-mapping (< 80% fails) coverage.
  - `investigate_sectional_market_going.py`: root-causes sectional coverage gaps
    per meeting (collector_stale / join_mismatch / partial_import /
    unsupported_source) and wet-going blind spots.
  - `winner_pattern_gap/`: a reusable two-agent research pipeline — agent1 mines
    sectional/barrier/going patterns, agent2 mines form-cycle/market patterns vs
    Betfair-implied win rates, and a synthesis stage cross-matches findings into a
    prioritized feature roadmap (market findings gated on ≥ 85% Betfair mapping
    coverage per track-month).

---

## 5. Portfolio risk & staking (`portfolio_risk.py`)

A standalone staking library (not called by the backtesters, which use flat stakes):

- Staking methods: FLAT, KELLY, **FRACTIONAL_KELLY (default fraction 0.25)**,
  PROPORTIONAL, PERCENTAGE. Kelly = `(b·p − q)/b`.
- `PortfolioRiskManager` defaults: bankroll $10,000, **max single bet 5%**,
  **max daily exposure 15%**, max 10 concurrent bets, confidence scaling
  `stake × (0.5 + 0.5 × confidence)`.
- Portfolio metrics: EV, variance, Sharpe, Monte-Carlo max-drawdown (95th
  percentile, 1,000 sims), and correlation-risk flags for same-race/track
  concentration.
- `optimize_stakes`: allocates the daily budget proportional to Kelly fractions.

The daily pipeline's actual staking is simpler: units by confidence tier (2u/1u/0u)
plus the convergence stake recommendation (FULL/STANDARD/REDUCED/NONE) — see
[Scoring & output](09-scoring-and-output.md).

---

## 6. Backfills (repair scripts)

| Script | Repairs |
|---|---|
| `backfill_lambda_targeted.py` | NULL λ/SVI/RSI/trip-cost where splits exist — normalizes the three collector split formats, then computes the Phase-2 primitives (~33k rows affected historically) |
| `backfill_phase2.py` | Full recompute of all Phase-2 columns + daily variants per (track, date) — idempotent |
| `backfill_zscores.py` / `_targeted.py` | Sectional z-scores (full sweep / NULL-only) |
| `backfill_rrh_missing_dates.py` | Calendar dates with zero `race_results_history` rows → re-fetch |
| `backfill_research_sources.py` | Rebuilds the research corpus (track JSON imports, Betfair mapping, training view refresh) |
| `backfill_tips_contract.py` | Re-stamps the selection contract (`bet_pick`/`coverage_pick`/`should_bet`…) into saved tips files when the contract format evolves — uses the live functions imported from `run_tips_pipeline` so the logic can't drift |

---

## 7. Reading the recent results (README numbers)

From `examples/backtest_summary.json` (352 metro races, 3,396 runners,
2026-03-04 → 2026-04-18, flat $100 at SP):

- **Value Edge ≥ 3% ($2–$15): 142 bets, 9.9% strike, +$1,750, +12.3% ROI** — the
  validated live band.
- Top Pick: 33.7% strike but −4.2% ROI — the model picks winners at favourite prices;
  accuracy ≠ value. This asymmetry is exactly why the convergence and EV gates exist.
- Calibration: Brier 0.0834; the 0.10–0.20 bin is nearly perfect (predicted 0.144 vs
  observed 0.151); the 0.20–0.30 bin under-predicts (0.231 vs 0.328) —
  high-confidence picks win *more* often than stated.

Caveats the docs should keep honest: SP-only settlement (no exchange commission or
better-than-SP execution modelled), win-only staking, and `backtest.py`'s missing
purge gap (use `walk_forward_backtest.py` when rigour matters).

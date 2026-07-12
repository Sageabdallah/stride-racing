# STRIDE Documentation

Deep documentation of how the STRIDE horse-racing prediction pipeline works — the
architecture, every subsystem, and how they join together. Written from a full
source read of the ~150 Python modules (~72k lines) in this repo.

## Reading order

| # | Document | What it covers |
|---|---|---|
| 1 | [Architecture](01-architecture.md) | The big picture: value-not-tips philosophy, three pillars, system diagram, guardrails, repo layout |
| 2 | [Daily pipeline](02-daily-pipeline.md) | A race day end to end: overnight prep → per-race 14-step flow → publish → results → learning |
| 3 | [Data & ingestion](03-data-and-ingestion.md) | The five data sources, collectors per state, importers, and the full DB schema |
| 4 | [Feature engineering](04-feature-engineering.md) | The 110-feature contract, the maths (λ decay, SVI, Glicko-2, z-scores…), leakage prevention |
| 5 | [ML training & calibration](05-ml-training-and-calibration.md) | The XGB+LGB+CatBoost ensemble, walk-forward training, the five calibration layers |
| 6 | [Monte Carlo engine](06-monte-carlo-engine.md) | The three MC engines, Plackett-Luce sampling, the realistic overlay, mc_api orchestration |
| 7 | [Intelligence layer](07-intelligence-layer.md) | Form franking (ELO + graph/PageRank), the nightly build, bankers, luckless analysis |
| 8 | [Consensus & market](08-consensus-and-market.md) | The tipster panel, crowd scores, odds steam/drift, and the convergence gate (V2 design vs V3 live) |
| 9 | [Scoring & output](09-scoring-and-output.md) | Calibration → selection score → safety filters → BET/NO_BET contract → tips JSON schema |
| 10 | [Backtesting & learning](10-backtesting-and-learning.md) | The backtest suite, the nightly learning loop, shadow P&L, staking/risk |
| 11 | [Module reference](11-module-reference.md) | One-line purpose for every file, live-path files marked |

## Quick orientation

If you read only one thing: **a race day flows** `download_racecards.py` →
`run_tips_pipeline.py` (normalise → features → ML → Monte Carlo → calibrate →
score → filter → bet contract → crowd gate) → `tips_<date>.json` + `selections`
table → results collectors → `learn_from_results_v2.py` (staged retrain). The
[daily pipeline doc](02-daily-pipeline.md) walks each step.

Three facts that make the rest of the code make sense:

1. **The model is anchored to the market, then bets only on residual disagreement.**
   Calibrated probability = model blended with the overround-corrected market at a
   price-dependent weight; `edge` is the difference; everything downstream gates on
   edge and EV, not on "who is most likely to win".
2. **Two probability engines, deliberately independent** — Monte Carlo simulation
   and a GBM ensemble — blended per runner, sanity-checked by an LLM layer and an
   intelligence layer (franking, prep cycles, barriers).
3. **The codebase carries its history.** Several subsystems have v1/v2/v3
   generations living side by side (trainers, consensus logic, intelligence
   builders, MC engines). Each doc flags what is live and what is dormant/dead —
   grep for "generation", "dormant", or "dead" in the docs when something looks
   duplicated.

## What was verified by running code

This repo is published for review and is not runnable end-to-end (no DB, models, or
data — see the main README). Verification performed while writing these docs
(2026-07-12, Python 3.11.15):

- `python -m compileall` — **all ~150 modules compile cleanly**.
- There is **no test suite** in the repo (no pytest/unittest files). Two modules
  carry executable self-tests, both pass:
  - `python server/python/glicko2_elo.py` — Glicko-2 demo: ratings update across
    surfaces, persist and reload.
  - `python server/python/temporal_staleness.py` — staleness feature suite: "All
    tests completed successfully."
- `python monte_carlo.py --input <synthetic 6-horse race> -n 20000 --seed 42
  --with_exotics` — the standalone MC engine runs end to end: sensible win/place
  probabilities with 95% CIs, fair-odds vs market edges, Kelly stakes, pace-scenario
  distribution, exactas/trifectas.
- Anything DB- or API-backed (the tips pipeline, trainers, collectors) requires the
  excluded credentials/data and was verified by source reading only.

## Documentation conventions

- Code references use `file.py:line` (line numbers as of commit `bf55bb2`).
- Named constants and thresholds are quoted from source, not paraphrased.
- Known defects/dead code are called out in a "quirks" section at the end of each
  doc rather than silently corrected.

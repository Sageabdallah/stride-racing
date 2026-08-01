# STRIDE Architecture

STRIDE is a production-style ML system for Australian thoroughbred racing that
predicts win probabilities, calibrates them against the betting market, and
surfaces positive-expected-value wagers. This document is the big picture: the
philosophy, the moving parts, and how they join.

---

## 1. The core idea: value, not tips

The system does not try to "pick winners" — it hunts for **disagreements with the
market** that survive calibration:

```
edge           = calibrated_win_probability − fair_market_probability
expected_value = calibrated_prob / fair_market_prob − 1
```

A runner is only actionable with positive edge, and even then it must pass price-band
guardrails, a confidence ladder, and an independent crowd cross-check. The recent
backtest illustrates why: the model's top pick wins 33.7% of races but *loses* 4.2%
at favourite prices, while the selective "edge ≥ 3%, $2–$15" filter shows +12.3% gross
ROI on far fewer bets — though with a 95% CI of [−45, +62]% it is NOT_REPORTABLE
([backtesting doc](10-backtesting-and-learning.md#7-reading-the-recent-results-readme-numbers)).

Three independent pillars converge on every runner:

| Pillar | Design weight | Produced by |
|---|---|---|
| **STRIDE model** — calibrated MC + ML ensemble | 50% | `mc_api.py`, `ml_model.py` |
| **Consensus** — independent tipster panel | 30% | `consensus_agent.py` |
| **Market signal** — overnight steam/drift | 20% | `odds_movement.py` |

(The exact convergence mechanics evolved: production currently runs a "crowd-first"
gate rather than the weighted blend — see
[Consensus & market](08-consensus-and-market.md).)

---

## 2. System diagram

```
                        ┌────────────────────────────────────────────────┐
   The Racing API ──────►  INGESTION                                      │
   racing.com GraphQL ──►  download_racecards / results collectors /      │
   NSW pidata (.tol) ───►  sectional collectors (per state) / importers   │
   Racing QLD CSV ──────►  → PostgreSQL (Neon) + racecards/*.json         │
                        └───────────────┬────────────────────────────────┘
                                        │
              ┌─────────────────────────┼──────────────────────────┐
              ▼                         ▼                          ▼
   ┌─────────────────────┐   ┌──────────────────────┐   ┌────────────────────┐
   │ INTELLIGENCE (nightly)│  │ TRAINING (staged)    │   │ CONSENSUS + MARKET │
   │ stride_build.py       │  │ refresh_training_view│   │ consensus_agent.py │
   │ franking ELO + graph  │  │ retrain_v2.py        │   │ odds_movement.py   │
   │ → intelligence/*.json │  │ → racing_ensemble_v2 │   │ → consensus_<d>.json│
   │ → franking_scores(DB) │  │   .pkl (+ OOF iso)   │   │ → market_signals…  │
   └──────────┬────────────┘  └──────────┬───────────┘   └─────────┬──────────┘
              │                          │                         │
              ▼                          ▼                         │
   ┌──────────────────────────────────────────────────┐            │
   │ RUN_TIPS_PIPELINE (per race)                     │            │
   │ normalise → luckless → LLM pre → race context →  │            │
   │ form features → ML predict → MONTE CARLO         │            │
   │ (Plackett-Luce base 70% + sectional overlay 30%) │            │
   │ → isotonic + market-anchored calibration         │            │
   │ → selection score + intelligence adjustments     │            │
   │ → LLM post-score → safety filters → top 3        │            │
   │ → bet/coverage contract (BET or NO_BET + reason) │◄───────────┘
   │ → crowd-first convergence gate                   │
   └──────────┬───────────────────────────────────────┘
              ▼
   tips_<date>.json  +  selections / convergence_output (DB)
              │
              ▼
   ┌──────────────────────────────────────────────────┐
   │ AFTER THE RACES                                   │
   │ auto_results_collector → prediction_audit         │
   │ stride_results_collector → stride_tip_results     │
   │ shadow_pl_tracker → tier-level P/L                │
   │ learn_from_results_v2 → view refresh + staged     │
   │ retrain (never auto-promoted)                     │
   └──────────────────────────────────────────────────┘
```

---

## 3. The two probability engines

STRIDE deliberately runs **two independent estimators** and blends them:

1. **Monte Carlo simulation** ([doc](06-monte-carlo-engine.md)) — a 17-factor model
   (`racing_system_v8.3_mc.py`) produces per-runner strength; thousands of race
   simulations (Dirichlet-perturbed Plackett-Luce sampling, pace scenarios,
   barrier/track effects) yield win/place distributions; a second "realistic"
   engine re-simulates with mixture noise, energy depletion and sectional profiles
   and is blended 30%.
2. **Gradient-boosted ensemble** ([doc](05-ml-training-and-calibration.md)) —
   XGBoost + LightGBM + CatBoost over a fixed 110-feature contract
   ([doc](04-feature-engineering.md)), trained walk-forward with a 14-day purge gap
   and out-of-fold isotonic calibration.

They are combined per runner (ML weight 20–40% by price), then anchored to the
overround-corrected market with a price-dependent model weight — the calibrated
probability is deliberately *pulled toward* the market except where the model has
standing to disagree ([scoring doc](09-scoring-and-output.md)).

An LLM layer (Groq / llama-3.3-70b) brackets the engines: a pre-MC analyst nudges
runner strengths (±0.08 cap), and a post-MC scorer produces AI scores, rankings and
the human-readable insights that ship with each tip.

---

## 4. Guardrails & gates (the "orchestration & guardrails" claim)

- **Racecard validation** — `race_normaliser` (distance/field/overround sanity,
  CRITICAL flags skip the race).
- **Flat-MC detection** — an uninformative simulation (spread < 6 pts) demotes
  confidence and penalizes scores rather than emitting fake conviction.
- **Bet contract** — every race is explicitly BET or NO_BET with a reason; the bet
  must be the raw model leader passing price-band edge gates (no hidden
  substitutes).
- **Crowd-first convergence** — model-only picks are gated NO_BET ("archetype
  trap"); crowd+model agreement earns full stakes.
- **Contract validation** — `validate_tips.py` asserts invariants on the output
  file; `backfill_tips_contract.py` re-stamps old files using the live logic.
- **Data-quality gates** — `sp_health` / `results_health_check`
  (GREEN/AMBER/RED/CORRUPTED).
- **Staged retraining** — `learn_from_results_v2` writes new models to
  `models/staging/`, never over the live artifact.
- **Shadow P&L** — the tiers the system *refuses* to bet are still tracked at level
  stakes (≥ 200 bets before a tier is reportable), so gating decisions stay
  evidence-based.

---

## 5. Repository layout (what's here vs excluded)

```
run on race day            supporting
─────────────────          ─────────────────
server/python/
  run_full_pipeline.py     retrain_v2.py, train_ml*.py       (training)
  download_racecards.py    backtest*.py, walk_forward_*.py   (evaluation)
  run_tips_pipeline.py     backfill_*.py                     (repair)
  mc_api.py                research/                         (diagnostics)
  stride_build.py          intelligence/build_*.py           (gen-3 rewrite, unwired)
  consensus_agent.py       nsw_*sniffer*, *_discovery.py     (dev tooling)
  odds_movement.py
  *collector*.py
racecard/feature/model/intelligence modules   (see 11-module-reference.md)

repo root: build_features.py (standalone CSV extractor),
           monte_carlo.py (standalone MC showcase),
           racing_system_v8.3_mc.py (factor model + base MC library),
           download_training_data.py, migrations/, examples/, scripts/
```

**Excluded from the published repo** (`.gitignore`): trained models (`*.pkl`,
`models/`), all runtime data (`racecards/`, `historical_data/`,
`intelligence/*.json`, `backtest_results/`), credentials (`.env`,
`tipster_panel.json`), and the entire TypeScript/Node web frontend (`client/`,
`package.json`…). The Python pipeline is the system; the frontend is a consumer of
`tips_<date>.json` and the `selections` table, and invokes `mc_api.py` as a JSON
child process.

### External services

| Service | Used for | Env vars |
|---|---|---|
| Neon PostgreSQL | all persistent state | `DATABASE_URL` |
| The Racing API | racecards, results, odds | `RACING_API_USERNAME/PASSWORD` |
| racing.com GraphQL | VIC/SA sectionals | `RACING_COM_API_KEY` |
| Racing NSW pidata / QLD CSV | sectionals | (no auth) |
| Groq (or Ollama) | pre/post LLM analysis | `GROQ_API_KEY`, `LLM_PROVIDER`, `LLM_ENABLED` |
| Tavily + Anthropic + Perplexity | consensus agent | `TAVILY_API_KEY`, `ANTHROPIC_API_KEY`, `PERPLEXITY_API_KEY` |

---

## 6. Design idioms you'll see everywhere

- **Graceful degradation** — nearly every enrichment is wrapped in try/except with
  an `*_AVAILABLE` flag; missing data lowers fidelity instead of crashing.
- **Temporal hygiene** — `race_date < today` filters, purge gaps, OOF calibration,
  LOO target encoding; the leakage tiers are catalogued in
  [Features §4](04-feature-engineering.md#4-leakage-prevention--three-tiers).
- **Audit-fix comments** — the scoring code carries numbered "audit fix" notes
  where formulas were corrected (double-counting, SMOTE removal, shared-shock
  removal); the docs reference them where relevant.
- **Versioned generations left in place** — v1/v2 trainers, V1/V2/V3 consensus,
  three intelligence generations coexist; each doc flags what is live vs dormant.
- **JSON files as the interchange format** — racecards, intelligence, consensus,
  market signals, tips: everything crossing a process boundary is a dated JSON file
  under a git-ignored directory.

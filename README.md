# STRIDE — Horse Racing Prediction Pipeline

A machine-learning pipeline for Australian thoroughbred racing that predicts
win probabilities, calibrates them against the betting market, and surfaces
**positive expected-value** wagers.

> **This repository is published for review, not deployment.** It contains the
> Python ML pipeline only. Trained models, datasets, the database, and the
> web frontend are intentionally excluded — so the code is **read-only and not
> runnable end-to-end**. See [LICENSE](LICENSE): all rights reserved.

---

## What this demonstrates

This is a production-style ML system, not a notebook. It shows:

- **Ensemble modelling** — gradient-boosted trees (XGBoost · LightGBM ·
  CatBoost) combined into a single win-probability estimate.
- **Temporal-safe calibration** — out-of-fold isotonic calibration over 30k+
  strictly time-ordered predictions, with calibration leakage explicitly
  removed. Probabilities are honest, not overstated.
- **Feature engineering at scale** — ~110 engineered features (form, pace,
  sectionals, trainer/jockey patterns, barrier-trial signals, fitness cycles).
- **A multi-source intelligence layer** — model output is fused with an
  independent tipster consensus and live market-movement signals.
- **Orchestration & guardrails** — a multi-stage daily pipeline with freshness
  checks, hard gates, and an automated scheduler.

## Methodology — value, not tips

The system bets on a market edge, not on picking winners:

```
edge            = true_win_probability − fair_market_probability
expected_value  = (calibrated_prob / fair_market_prob) − 1
```

A selection is only actionable when `edge > 0`. Expected value is the primary
ranking signal. Three pillars converge on every runner:

| Pillar | Weight | Source |
|--------|--------|--------|
| STRIDE model | 50% | Calibrated ML ensemble |
| Consensus | 30% | Independent tipster panel |
| Market signal | 20% | Odds steam / drift detection |

Convergence is graded into tiers (`LOCK`, `CONFIRM`, `FLAG`, `CROWD_OVERRIDE`,
`SKIP`); an EV gate then assigns a confidence tier (`HIGH`, `MEDIUM`, `NO_BET`).

## Architecture

```mermaid
flowchart TD
    A[Racecard &amp; results ingest<br/>The Racing API · racing.com] --> B[Feature engineering<br/>~110 features]
    B --> C[ML ensemble<br/>XGBoost · LightGBM · CatBoost]
    C --> D[OOF isotonic calibration<br/>temporal, leak-free]
    D --> E[STRIDE score — 50%]
    F[Consensus agent<br/>independent tipster panel] --> G[Consensus score — 30%]
    H[Market signals<br/>odds steam / drift] --> I[Market score — 20%]
    E --> J[Three-pillar convergence blender]
    G --> J
    I --> J
    J --> K[Convergence tiers<br/>LOCK · CONFIRM · FLAG · SKIP]
    K --> L[EV gate → confidence tiers<br/>HIGH · MEDIUM · NO_BET]
    L --> M[Daily tips output]
```

## Daily pipeline

1. **Ingest** — download racecards and prior results.
2. **Build intelligence** — feature engineering, form franking, pace/speed maps.
3. **Consensus** — poll an independent tipster panel for crowd signal.
4. **Market signals** — capture odds snapshots and classify steam/drift.
5. **Predict & converge** — ensemble inference, three-pillar blending, gating.
6. **Publish** — stamp the final selection contract for downstream display.

## Model snapshot

| Metric | Value |
|--------|-------|
| Algorithm | XGBoost + LightGBM + CatBoost ensemble |
| Features | ~110 engineered features |
| Calibration | Out-of-fold isotonic — 27 folds, 30,226 temporal predictions |
| Cross-validated AUC | ≈ 0.80 |

## Recent results

Live (post-calibration-fix) model walked through metro races
**2026-03-04 → 2026-04-18** — 352 races, 3,396 runners, flat $100 stake at
starting prices.

### ROI by selection band

| Strategy | Bets | Strike rate | P&L | ROI |
|---|---:|---:|---:|---:|
| **Value Edge ≥ 3% ($2–$15)** | **142** | **9.9%** | **+$1,750** | **+12.3%** |
| Top Pick (highest prob) | 344 | 33.7% | −$1,445 | −4.2% |
| All $2–$20, positive edge | 333 | 7.5% | −$1,800 | −5.4% |
| Big Value $5–$15, ≥ 5% edge | 54 | 7.4% | −$800 | −14.8% |
| Mid-Range $3–$8, ≥ 5% edge | 25 | 12.0% | −$700 | −28.0% |
| Short Price $2–$5, ≥ 3% edge | 7 | 0.0% | −$700 | −100.0% |

The headline is **Value Edge ≥ 3%** — the model's selective edge filter
finds 142 bets in this window at **+12.3% ROI**. Top Pick lands 33.7% of
the time but at favourite prices and loses 4.2%. That's the value-versus-
accuracy tradeoff the convergence layer is designed to navigate.

### Calibration

![Calibration curve](examples/calibration.png)

| Predicted bin | Runners | Predicted mean | Observed mean |
|---|---:|---:|---:|
| 0.00 – 0.10 | 1,786 | 0.053 | 0.029 |
| 0.10 – 0.20 | 1,337 | 0.144 | 0.151 |
| 0.20 – 0.30 | 265 | 0.231 | 0.328 |
| 0.30 – 0.40 | 6 | 0.318 | 0.500 |
| 0.40 – 0.50 | 2 | 0.434 | 0.000 |

Brier score **0.0834** across 3,396 runners. Calibration is tight through
the bulk of the prediction distribution (0.10–0.20 bin almost perfectly
aligned); the 0.20–0.30 band is slightly *under*-predicted — high-
confidence picks win more often than the calibrated probability suggests.

Raw summary: [`examples/backtest_summary.json`](examples/backtest_summary.json).

### Sample race-day output

What the live pipeline actually produces — a real race day, 2026-04-18:

- [`examples/sample_selections.json`](examples/sample_selections.json) — best bets, value plays, convergence summary
- [`examples/sample_race.json`](examples/sample_race.json) — one full race (Morphettville Parks R5, 9 runners) with model scores, edges, and AI insights per runner

## Tech stack

Python · scikit-learn · XGBoost · LightGBM · CatBoost · NumPy / pandas ·
PostgreSQL (Neon) · Monte Carlo simulation.

## Repository layout

```
server/python/              Core pipeline — ingestion, features, modelling,
                            consensus, market signals, convergence, scheduler
build_features.py           Feature engineering entry point
monte_carlo.py              Monte Carlo race simulation
racing_system_v8.3_mc.py    Standalone racing/simulation system
download_training_data.py   Training-data assembly
migrations/                 PostgreSQL schema
requirements.txt            Python dependencies
.env.example                Required environment variables (template)
server/python/tipster_panel.example.json
                            Tipster consensus panel (template)
examples/                   Sample race-day output + recent backtest summary
scripts/plot_calibration.py Calibration plot renderer (requires matplotlib)
```

## Configuration

Runtime configuration is environment-driven — no secrets in source. Copy
[`.env.example`](.env.example) to `.env` and supply your own credentials
(database URL and API keys). The `.env` file is git-ignored.

The tipster consensus panel is templated the same way. Copy
[`server/python/tipster_panel.example.json`](server/python/tipster_panel.example.json)
to `server/python/tipster_panel.json` and populate it with your own independent
tipster sources. The real `tipster_panel.json` is git-ignored.

## License

Proprietary — © 2026 Sage Abdallah. All rights reserved. The code is visible
for reference only; reuse, copying, or modification is not permitted. See
[LICENSE](LICENSE).

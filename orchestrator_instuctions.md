# AGENT INSTRUCTIONS — PHASE 2: IMPLEMENTING THE RESEARCH FINDINGS

> You are picking up after a research phase. Four reports exist in
> `/docs/analysis/`: `SYSTEM_MAP.md`, `ACADEMIC_FINDINGS.md`,
> `IMPROVEMENT_REPORT.md`, `IMPLEMENTATION_PLAN.md`.
> Your job is to turn the plan into working code that improves **ROI** and
> **strike rate** of the horse racing selection system — without breaking the
> existing architecture and without data leakage.

---

## STEP 0 — MANDATORY READING (gate)

Before writing ANY code, read all four reports plus the repo's markdown docs
listed in SYSTEM_MAP.md. Then restate in chat:
1. The current selection pipeline in 5 steps or fewer
2. The top 3 tickets you intend to implement, in order
3. Which lever each pulls (ROI, strike rate, or both)
Wait for approval if any ticket contradicts SYSTEM_MAP.md constraints.

---

## OPERATING PRINCIPLES (non-negotiable, inherited from Phase 1)

1. **Additive only.** Extend existing modules; never rewrite working logic.
2. **Feature flags.** Every change behind a config toggle using the existing
   config system. Default OFF until validated out-of-sample.
3. **Extend, don't duplicate.** If a probability/odds/staking/config module
   exists, the change goes inside it. Name the module you extended.
4. **Convention lock.** Match existing naming, folder layout, logging, and
   test patterns from SYSTEM_MAP.md.
5. **One ticket per branch.** Small, reviewable, independently revertable.
6. **Evidence or it doesn't ship.** No ticket merges without walk-forward
   backtest results (see Definition of Done).

---

## IMPLEMENTATION ORDER

Workstreams run in this order. **A must finish before B–E start** — without a
leakage-proof harness, no result from any other workstream can be trusted.

### WORKSTREAM A — LEAKAGE-PROOF EVALUATION HARNESS (build first)

Build/extend a backtest harness with these exact properties:

- **Time-based walk-forward splits only.** NEVER random train/test splits.
  Horses, trainers, and jockeys recur across races — random splits leak
  identity information and inflate every metric.
- **Purge + embargo.** Leave a gap (default: 7 days, configurable) between
  train end and test start so rolling form features can't span the boundary.
- **As-of feature snapshots.** Every feature must be computable using only
  data available at the declared **prediction timestamp** (e.g. 10 minutes
  before the off, or morning of race — pick one, document it, enforce it).
- **One race, one split.** All runners from the same race must sit entirely
  in train OR test, never across both.
- **Fold-safe fitting.** Scalers, encoders, target encodings, and calibrators
  are fit on training folds ONLY (or out-of-fold predictions) — never on the
  full dataset before splitting.
- **Realistic economics.** Results net of commission (configurable %),
  using price snapshots available at the prediction timestamp, not closing
  prices — unless the strategy explicitly bets at BSP.
- **Metrics output per fold and overall:** ROI, strike rate, avg odds,
  number of bets, Brier score, log loss, max drawdown, profit factor,
  and bootstrap 95% confidence interval on ROI.

**Acceptance test for Workstream A:** run the CURRENT production model
through the harness and record baseline metrics in
`/docs/analysis/RESULTS.md`. Every future ticket reports deltas vs this
baseline.

---

### WORKSTREAM B — STRIKE RATE LEVER: calibration & thresholds

Guided by ACADEMIC_FINDINGS.md (calibration section). Typical tickets:

- **Probability calibration layer** (isotonic regression or Platt scaling)
  fitted on out-of-fold predictions only — never in-sample. A well-calibrated
  model lets the selection filter trust its own probabilities.
- **Minimum-probability threshold** for a selection to qualify (tuned on
  validation folds only, never on test).
- **Market mix options** behind flags: place-only, each-way, or dutching
  multiple selections per race for races where the model's top-N
  probabilities are tightly bunched.
- **Race-type filters** the research supports (e.g. avoid high-variance race
  types) — each filter individually justified by the findings doc and
  individually validatable, not a bundle.

### WORKSTREAM C — ROI LEVER: value detection & staking

- **Edge filter:** bet only when `model_prob` implies meaningful edge over
  market price: `edge = model_prob × decimal_odds − 1 ≥ threshold`
  (threshold tuned on validation folds; research typically supports
  5–15% minimum edge to survive commission + estimation error).
- **Fractional Kelly staking** (default ¼ Kelly, configurable) with a hard
  stake cap (e.g. 2% of bankroll) and a minimum-odds sanity band — the
  favorite-longshot bias literature says short-priced "value" is often a
  calibration artifact, so verify with the calibration curve before trusting
  edge at short prices.
- **Price sensitivity tracking:** log the price taken vs BSP for every
  simulated bet — **CLV (consistently beating the closing price) is the
  leading indicator that edge is real** rather than backtest luck.
- **Filter discipline:** every ROI filter (odds band, field size, class,
  going) must be proposed by the research doc AND validated OOS. Two or more
  interacting filters require a warning comment — stacked filters are the
  #1 way accidental backtest overfitting enters a system.

### WORKSTREAM D — FEATURES (both levers)

Implement only the feature tickets the research doc ranks highest, computed
**as of the prediction timestamp**. Typical horse racing groups:

- Form: recent finishing positions, days since last run, speed figures,
  beaten lengths — all rolling windows **ending strictly before race date**
- Context: going, distance, course, class movement, draw, weight
- Connections: trainer/jockey strike rates — computed from prior results only,
  with minimum sample sizes to avoid noise
- Market: price at prediction timestamp (NOT SP/closing, unless betting BSP)

Every new feature needs a one-line doc comment stating its as-of timestamp
justification.

### WORKSTREAM E — TRACKING

- Extend existing logging so every selection records: model prob, calibrated
  prob, price taken, BSP, edge, stake rule applied, result, P&L net of
  commission.
- Weekly metrics job reusing Workstream A's metric functions (no duplicate
  metric code — import from the harness).

---

## DATA LEAKAGE RULEBOOK — check every ticket against this

❌ FORBIDDEN (instant rejection in review):
1. Final SP / Betfair SP / closing odds as a feature when the strategy bets
   earlier — that's tomorrow's newspaper.
2. Any feature derived from the race's own result (finishing position,
   in-running data, official ratings published after the race).
3. Rolling stats computed over the full dataset before splitting.
4. Fitting scalers/encoders/calibrators on data that includes test rows.
5. Random shuffling of races for train/test.
6. The same race's runners split across train and test.
7. "Form in last N days" computed with today as the anchor instead of the
   race's actual date.
8. Datasets with survivorship bias (e.g. only horses that completed) —
   flag to the human if found; do not silently work around it.

✅ REQUIRED in every model/feature ticket: a "Leakage check" section in the
PR description stating the prediction timestamp, the split method, and which
of the 8 rules above were relevant and how they were handled.

---

## TICKET EXECUTION PROTOCOL

For each ticket from IMPLEMENTATION_PLAN.md, in dependency order:

1. Confirm the ticket's "Conflicts checked" section against the actual code
   — if reality differs from the plan, update the plan doc first.
2. Implement behind a feature flag, OFF by default.
3. Unit tests following the existing test patterns.
4. Run the Workstream A harness: flag OFF (baseline) vs flag ON, same folds.
5. Append results to `/docs/analysis/RESULTS.md` as a table row:
   ticket | lever (ROI/SR) | baseline ROI/SR | new ROI/SR | bootstrap CI |
   #bets | max drawdown | CLV | verdict
6. **Ship criteria:** improvement directionally correct on its stated lever,
   ROI bootstrap CI positive or overlapping-baseline-with-more-bets,
   no regression > 5% on the other lever, CLV positive or neutral.
7. If ship criteria fail: keep the flag OFF, document why in RESULTS.md,
   move to the next ticket. A validated negative result is still a result —
   it prevents a bad idea reaching production.

---

## DEFINITION OF DONE (for the whole phase)

- [ ] Workstream A harness merged, baseline recorded in RESULTS.md
- [ ] Each implemented ticket has a RESULTS.md row with honest metrics
- [ ] No ticket merged that violates the leakage rulebook
- [ ] All new behavior behind flags; default config reproduces old behavior
- [ ] Final summary in chat: which tickets shipped, expected OOS ROI/SR
      deltas, and which research ideas were tested and rejected (with data)

⚠️ NEVER present backtest numbers as live-trading expectations. State the
sample size and CI alongside every claim.

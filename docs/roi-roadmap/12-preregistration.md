# 12 — Pre-registration: the accrual-window protocol for the task-12 retrain

**Type:** governance · **Status:** DRAFT — merges only when zero SAGE-APPROVAL
markers remain (enforced mechanically by `gate_preregistration`,
`server/python/retrain_preflight.py:267`, branch `roi/12-prep-promotion-preflight`, PR #17)
· **Consumed by:** the task-12 retrain ([12-retrain-rebaseline.md](12-retrain-rebaseline.md))
via `retrain_preflight.py` → `ship_criteria.evaluate_ship_criteria`

## Why this document exists

The system's founding number — **+12.3% ROI** (`README.md:111` on `main`, 142 bets)
— was the **best of 6 strategies** swept on one 6-week window. Its bootstrap 95%
CI is **[−43.6%, +68.2%]**, z = 0.43, P(ROI ≤ 0) ≈ 35%
(`docs/roi-roadmap/02-backtest-statistics.md` §"Why", branch
`roi/02-backtest-statistics`, PR #4). Under the pack's own rules that number is
`NOT_REPORTABLE`. Every threshold, band, and metric below is fixed **before** the
accrual window's outcomes are visible, so that mistake cannot be repeated at the
task-12 retrain. Nothing in this document may be tuned after data exists; the only
permitted edits are resolving a SAGE-APPROVAL marker (by Sage, before the
window closes) or the amendment rule at the bottom.

## WINDOW

- **Start:** the tips-restart date — the first race day the tips pipeline runs
  after Phase 2 of `docs/DEPLOY_RUNBOOK.md` (branch `ops/deploy-prep`, PR #10)
  completes. The calendar date is recorded in this section on that day, before
  any metric is computed: **start date: _to be filled in on restart day_**.
- **End:** the retrain cutoff — the last race day before the task-12 training-view
  freeze ([12-retrain-rebaseline.md](12-retrain-rebaseline.md) step 1). Declared in
  writing here before any window metric is computed.
- **Coverage:** every race day the pipeline processed between start and end.
  **No post-hoc day exclusions.** A broken day (partial card, ingest failure,
  watcher down) is excluded only with a written reason appended to this section
  **before results for that day are known**. The exclusion log is part of the
  protocol and is never edited retroactively.

### Exclusion log (append-only; reason must predate knowledge of the day's results)

| Date | Reason | Logged at |
|------|--------|-----------|
| — | — | — |

## REGISTERED METRICS

Computed by existing machinery only, cited by file (branch stated where the file
is not yet on `main`). No metric may be added, redefined, or dropped after the
window opens.

| Metric | Definition | Machinery |
|--------|-----------|-----------|
| Net ROI + bootstrap CI | Flat 1u, net of **8% commission** (`(odds−1)·(1−0.08)`, AU exchange convention), bootstrap 95% CI (≥10k resamples) with `reportable` verdict | `roi_stats.summarise_bets` (`server/python/roi_stats.py:265`, branch `roi/02-backtest-statistics`, PR #4); commission via `commission_rate_from_env` (:43), `STRIDE_COMMISSION_RATE=0.08` |
| Brier (walk-forward) | Published `winPercentage` vs outcome per runner over the window, expanding-window, no refit on the evaluating fold | `server/python/walk_forward_backtest.py` (`main`: expanding-window CV emitting Brier/log-loss/ECE) |
| Mean CLV | `price_taken` vs closing price, per settled ledger row; computable once snapshots accrue: `tip_time` vs `late_t5`/SP | `build_ledger_row` → `clv_pct` + `weekly_metrics` (`server/python/selection_ledger.py:156-173, :220-241`, `main`; net settlement on branch `roi/01-ledger-clv-net-settlement`, PR #1); `runner_odds_snapshots` kinds `tip_time`/`late_t5` (branch `roi/04-as-of-odds-snapshot`, PR #2, `migrations/runner_odds_snapshots.sql`) |
| Max drawdown | Peak-to-trough of cumulative P&L, units, on the ordered per-bet return sequence | `roi_stats.max_drawdown_and_streaks` (`server/python/roi_stats.py:129`, PR #4) |
| Losing streak | Observed max losing streak + expected max under the observed strike rate | `roi_stats.max_drawdown_and_streaks` (same) |

## REGISTERED BANDS

Exactly two. **No band is added, widened, or narrowed after window data exists.**

1. **LIVE-GATE** — the live gate as shipped: `_bet_gate` rules at
   `server/python/run_tips_pipeline.py:1812-1826` on `main` (odds ≤ $15 cap;
   edge ≥ 4 / 2.5 / 3 across the <$3 / $3–5 / >$5 price bands; probability floors
   30 / 15 / 10; positive edge; real market quote required; low-confidence veto
   above $12). Bets at tip-time price, flat 1u, net 8%.
2. **ALL-RUNNERS (control)** — every runner the pipeline processed with a
   published probability and a real market quote, no gate. The zero-selection-skill
   benchmark the gate's value-add is measured against.

Any other cut of the window data (by track, distance, price band, tier, day of
week, anything) is labelled **EXPLORATORY** in the report, is computed for
diagnosis only, and **cannot be a promotion argument**. A pattern found
exploratorily may only be promoted via a *new* pre-registration on a disjoint
later window (the [09](09-forward-validation-protocol.md) registry rule: one
hypothesis, one window B).

## NEW-BEATS-OLD RULE (the promotion bar)

Consumed by `retrain_preflight.py` Board 2 (`gate_shadow_metrics`,
`server/python/retrain_preflight.py:240`, PR #17), which passes baseline vs
candidate metrics into `ship_criteria.evaluate_ship_criteria`. A staged task-12
artifact may replace the production artifact only if **all** of:

- **(a) Brier not degraded:** candidate walk-forward Brier ≤ production model's
  Brier on the same window races (point estimate; no re-derived thresholds).
- **(b) Ship gate on the registered band:** `evaluate_ship_criteria` returns
  `SHIP` for the LIVE-GATE band. Since PR #7 (branch
  `fix/ship-gate-below-zero-ci`, `server/python/ship_criteria.py`) the gate
  requires the ROI bootstrap CI to exclude zero **above** zero (the
  `roi_positive` check): a CI entirely below zero is a measured, significant
  loss — `HOLD`, never a promotion, even when the candidate improves on a worse
  baseline.
- **(c) Minimum sample:** **200** (= `MIN_BETS_REPORTABLE`, `server/python/roi_stats.py:33` — resolved by Sage 2026-08-01). Below the floor the comparison is not
  run and the verdict is `INSUFFICIENT_SAMPLE` — not a pass, not a fail.

## EARLY CANARY (leading indicator)

CLV reaches significance in ~400 bets vs ~3,000–5,000 for ROI
([01](01-ledger-clv-net-settlement.md)) — it is the window's early-warning gauge,
not a promotion criterion.

- **Confirmation:** mean CLV > 0 sustained over **10 consecutive race days with settled ledger rows** (resolved by Sage 2026-08-01) is the leading indicator
  that the shipped stack prices ahead of the market.
- **Pause:** CLV persistently < 0 pauses staking. **pause after 5 consecutive race days with mean CLV < 0 and >= 20 settled bets across them; resume after 2 consecutive CLV-positive days** (resolved by Sage 2026-08-01). The
  pause stops stakes only — tips, ledger rows, and snapshots keep accruing, or
  the window is destroyed.

## AMENDMENT RULE

This document is append-only once the window opens. Corrections are new dated
entries referencing the old text, never edits (the
[09](09-forward-validation-protocol.md) graveyard rule). An amendment that
changes any metric, band, threshold, or date after outcomes for the affected
period are visible **voids the window**: the accrual restarts under the amended
protocol.

## Related

- Evidence base: [00-evidence-base.md](00-evidence-base.md) §2 (A4), §6;
  `README.md:109-121` on `main` (the unreportable headline)
- Statistics machinery: [02](02-backtest-statistics.md) · Ledger/CLV: [01](01-ledger-clv-net-settlement.md) · Snapshots: [04](04-as-of-odds-snapshot.md)
- Protocol this operationalises: [09](09-forward-validation-protocol.md) · Retrain it gates: [12](12-retrain-rebaseline.md)
- Flag flips gated separately: [shadow-flip-criteria.md](shadow-flip-criteria.md)

---

## Amendment 2026-09-05 — window start, staging vs promotion, cross-fitted evaluation (append-only entry)

[SAGE-APPROVAL] confirm this amendment (remove this marker to sign; `retrain_preflight.py` reads the pre-registration AMBER until it is gone)

Written before any v3 candidate exists and before any window metric was
computed. Nothing above is edited; this entry supersedes by reference.

**1. Window start date: 2026-08-02.** The WINDOW section's placeholder was
never filled on restart day. The date was, however, already registered
before any outcome data existed: `docs/project_retrain_gate.md`, committed
2026-08-02T03:02:08Z at repo head 3324c25 — day zero 2026-08-02, earliest
window 2026-08-30, recommended 2026-09-13, validation window B 2026-08-02 to
2026-09-13. This entry records that registered fact by reference; it does not
choose a date after the fact.

**2. Two criteria for two stages — one contract.** `12-retrain-rebaseline.md`
step 3 ("new model must beat the favourite baseline and not lose the H2H")
and the NEW-BEATS-OLD rule above were read as competing promotion rules. They
govern different stages:

- **Staging criterion (walk-forward CV; decides whether a candidate is staged
  at all).** On identical purge-gapped folds the candidate must (a) not
  degrade honest out-of-fold Brier against the legacy arm scored on the same
  folds, (b) improve per-race top-1 hit rate against the **tip-time
  favourite** on the same races, and (c) not lose the same-race head-to-head
  against the stored production probability. The SP favourite is printed as
  a hindsight diagnostic and is never the baseline: selection criteria use
  tip-time price ([09](09-forward-validation-protocol.md)); SP is for
  settlement and CLV. A candidate that fails is a documented negative result
  (the `rank_model.py` precedent) and never enters parallel scoring.
- **Promotion rule (live; decides whether a staged candidate replaces
  production).** Unchanged: NEW-BEATS-OLD above — Brier not degraded on the
  window, `evaluate_ship_criteria` SHIP on LIVE-GATE, at least 200 bets —
  read after one week of parallel scoring of the candidate through the
  `tips-proof` job.

**3. Cross-fitted meta-evaluation.** Any learned combination of the base
models, and any final-stage calibrator, fitted on out-of-fold predictions is
evaluated only on folds it was not fitted on (fit on earlier folds, score
the next). The persisted production object may be fitted on all out-of-fold
rows; its in-sample Brier is labelled as such and never quoted as
performance.

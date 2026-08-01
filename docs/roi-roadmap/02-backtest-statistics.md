# 02 — Backtest statistics: CIs, drawdown, streaks, concentration, multi-comparison honesty

**Wave:** 1 · **Depends on:** nothing (start immediately; pairs with [01](01-ledger-clv-net-settlement.md)) · **Blocks:** [09](09-forward-validation-protocol.md), [11](11-place-and-each-way.md) · **Risk:** low · **Type:** measurement

## Goal

No ROI number is ever printed again without its standard error, confidence interval,
sample size, and risk path. Strategy sweeps are labelled with a multiple-comparison
caveat, and a result whose CI spans zero is marked `NOT_REPORTABLE`.

## Why (evidence)

- The headline +12.3% (142 bets, 14 wins, avg decimal odds ≈ 11.4) has SE ±28.5pp,
  95% CI **[−43.6%, +68.2%]**, z = 0.43, bootstrap P(ROI ≤ 0) ≈ 35% — see
  `examples/backtest_summary.json` (no uncertainty anywhere) and
  [00-evidence-base.md](00-evidence-base.md) §1/§2. The repo's own
  `docs/analysis/IMPLEMENTATION_PLAN.md:2704-2707` computes t = 0.432.
- Best-of-6 selection on one window: under a zero-edge null, P(any of 6 bands ≥
  +12.3%) ≈ 80–93%. All 5 other bands lost (−4.2% … −100%).
- Concentration: removing 1 winner → +4.3% ROI; removing 2 → −3.7%. The edge is 1–2 horses.
- Risk path: at 9.9% strike the expected max losing streak over 142 bets is ≈ 30
  (95th pct ≈ 50) — a −60% bankroll drawdown at 2u stakes. Not reported anywhere.
- `walk_forward_backtest.py` already has bootstrap CI machinery — reuse it.

## Scope

**In:** a shared stats module + wiring into `backtest.py`, `backtest_v2_metro.py`,
`walk_forward_backtest.py`, `examples/backtest_summary.json` generation, and
`shadow_pl_tracker` summaries.
**Out:** changing strategy definitions; commission plumbing (→ [01](01-ledger-clv-net-settlement.md)).

## Steps for Kimi Code

1. **Create `server/python/roi_stats.py`** with pure, unit-tested functions:
   - `roi_ci(per_bet_returns, confidence=0.95)` → bootstrap (≥10k resamples) CI +
     normal-approx SE. Per-bet return for flat staking: `+ (odds−1)` or `−1`
     (net variant: `(odds−1)×(1−commission)`).
   - `max_drawdown_and_streaks(results_sequence)` → max drawdown (units), max losing
     streak, expected max losing streak under observed strike.
   - `winner_concentration(per_bet_returns)` → ROI with top-k winners removed, k=1..3.
   - `multi_comparison_note(n_strategies, best_roi, se)` → required z under
     Bonferroni and observed z; verdict string.
2. **Wire into both backtesters.** Every strategy block in the summary gains:
   `bets`, `wins`, `strike_rate`, `roi_gross`, `roi_net` (from [01](01-ledger-clv-net-settlement.md)'s
   commission param; until merged, compute both with commission=0.08), `se`, `ci95`,
   `max_drawdown`, `max_losing_streak`, `roi_minus_top1`, `roi_minus_top2`,
   `reportable: bool` (lower CI bound > 0 **and** bets ≥ 200), else `NOT_REPORTABLE`.
3. **Sweep labelling.** When >1 strategy is reported on the same window
   (`backtest.py:600-622` sweeps 13 configs; metro backtest reports 6), attach
   `multi_comparison` to the summary: `n_strategies`, `bonferroni_z_required`,
   `best_observed_z`, `selection_caveat: true`.
4. **Regenerate `examples/backtest_summary.json`** with the new fields and update
   `README.md`'s results table to quote CI and net ROI alongside gross (or mark the
   band `NOT_REPORTABLE` per the floor). Keep all 6 strategies — losers included.
5. **Ship criteria alignment.** Confirm `ship_criteria.py`'s NOT_REPORTABLE logic
   consumes `roi_stats` (single source of truth) rather than duplicating math.

## Acceptance criteria

- [ ] `pytest server/python/test_roi_stats.py` (new) covers: known-case CI on a
      synthetic 142-bet/14-win/11.4-odds series reproduces CI ≈ [−44%, +68%];
      streak/drawdown on fixed sequences; concentration math.
- [ ] Regenerated `examples/backtest_summary.json` contains `se`, `ci95`,
      `max_losing_streak`, `roi_net`, `reportable` for all 6 strategies, and the
      +12.3% band is marked `reportable: false` (it must be — verify).
- [ ] `walk_forward_backtest.py` output includes per-window drawdown + streaks.
- [ ] No report prints a bare ROI without CI anywhere in `docs/` or `examples/`.

## Rollout & flags

- No flags needed — reporting only, no behavioural change. Merge order: before [09](09-forward-validation-protocol.md).

## Guardrails

- Do **not** delete or hide losing strategies — full disclosure is the point.
- Do **not** tune any band/threshold here; this task only measures.
- Bootstrap must resample **bets**, not runners, and must use the net-return
  definition from the settlement contract ([01](01-ledger-clv-net-settlement.md)).

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §1, §2 (A4, A5)
- Consumers: [09](09-forward-validation-protocol.md) pre-registers thresholds with
  these stats; [06](06-staking-and-risk-controls.md) sizes stakes from
  drawdown/streak output.

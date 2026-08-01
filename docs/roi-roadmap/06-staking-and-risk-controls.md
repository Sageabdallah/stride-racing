# 06 — Staking reform: flat 1u, drawdown circuit-breaker, honest Kelly path

**Wave:** 2 · **Depends on:** [01](01-ledger-clv-net-settlement.md) (net EV + ledger), [02](02-backtest-statistics.md) (drawdown/streak stats) · **Blocks:** [12](12-retrain-rebaseline.md) (promotion gates reference staking) · **Risk:** medium (changes money) · **Type:** economics

## Goal

Stake sizing matches the measured evidence: flat 1u until net ROI's CI excludes
zero; a drawdown circuit-breaker that protects the bankroll through the statistically
expected ~30-bet losing streak; and a disciplined, gated path to fractional Kelly later.

## Why (evidence)

- The ladder is inverted against its own data: `compute_staking` stakes 2u on HIGH,
  1u on MEDIUM (`run_tips_pipeline.py:1007-1015`), but in the validating window the
  HIGH tier lost **−37.5%** (20 bets) while LOW lost −1.7% (`examples/backtest_summary.json`).
- 2u ≈ 1.7× full Kelly **gross**, ~5× at 8% MBR, ~9.8× at 10% — past the zero-growth
  point (`docs/analysis/IMPLEMENTATION_PLAN.md:2710-2712`).
- At 9.9% strike, expected max losing streak ≈ 30 bets (95th pct ≈ 50): at 2u = $200
  that's a −$6,000 streak (−60% of the static $10k bank at `selection_ledger.py:118,137-138`).
- Kelly exists but is strangled (correctly): `shadow_stake_plan` returns
  `'applied': False` always (`portfolio_risk.py:617-669`); the MC engine uses Kelly
  as a stake *raiser* (`racing_system_v8.3_mc.py:40-41,2005-2023`) — opposite direction.
- `portfolio_risk.py` has a variance/EV probability mismatch (variance computed at
  market-implied p while EV uses model p, ~:234-235) and the whole
  `PortfolioRiskManager` (:60-533) has zero importers.

## Scope

**In:** staking ladder change (flag-gated), drawdown breaker, the portfolio_risk
probability-mismatch fix, daily exposure caps in the tips pipeline, Kelly activation
**criteria** (not activation).
**Out:** place/each-way staking (→ [11](11-place-and-each-way.md)); bankroll
compounding redesign.

## Steps for Kimi Code

1. **Flatten the ladder.** `compute_staking` (`run_tips_pipeline.py:1007-1015`):
   HIGH=1u, MEDIUM=1u, LOW=0u behind `STRIDE_FLAT_STAKING=true`. Keep the old ladder
   behind the flag for one release.
2. **Drawdown circuit-breaker.** New function in `portfolio_risk.py` (it already
   tracks bankroll state): if realised drawdown ≥ 15% of rolling bankroll, halve
   unit size; ≥ 25%, suspend betting (publish NO_BET with reason
   `drawdown_breaker`). Wire into the daily pipeline after results settlement.
   Thresholds env-configurable.
3. **Fix the probability mismatch** at `portfolio_risk.py:234-235`: variance must
   use the same probability as EV (model p, preferably the existing
   `shrunk_win_probability` at :595-614). Unit-test both branches.
4. **Daily exposure caps in the tips pipeline.** The only caps today live in the MC
   engine (30u/day, 12u/track — `racing_system_v8.3_mc.py:128-131`) and don't bind
   production. Add to the tips pipeline: max 6 bets/day, max 2/track, selection by
   highest net EV (from [01](01-ledger-clv-net-settlement.md)). Env-tunable.
5. **Kelly activation gate (documented, not enabled).** Add a `kelly_readiness()`
   report function: returns true only when (a) ≥400 settled ledger bets, (b) lower
   95% CI of net ROI > 0 ([02](02-backtest-statistics.md)), (c) mean CLV > 0 over the
   same window. Print it in the weekly metrics. Activation itself is a future,
   separate, human-approved change at 0.25× fractional Kelly from net EV.

## Acceptance criteria

- [ ] With `STRIDE_FLAT_STAKING=true`, a day with HIGH and MEDIUM tips stakes 1u on
      each (unit test + one shadow day log).
- [ ] Breaker simulation: feed a synthetic 30-loss sequence → unit halves at −15%,
      suspends at −25% with correct published reason.
- [ ] `kelly_readiness()` prints weekly and currently returns false (verify it does).
- [ ] Tips pipeline rejects the 7th bet and 3rd same-track bet by net-EV ranking.

## Rollout & flags

- Flags: `STRIDE_FLAT_STAKING` (default on after merge — this is a risk cut, not an
  experiment), `STRIDE_DRAWDOWN_BREAKER=true`, cap envs.
- Rollback: flags off restores the 2u ladder.

## Guardrails

- Do **not** enable Kelly staking in this task (standing prohibition; the readiness
  report is the deliverable).
- Do **not** resurrect `PortfolioRiskManager` wholesale — the three functions above
  are what's needed; the class stays dormant.
- The breaker must never *increase* stakes after a winning streak (no martingale logic).

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §3 (B1)
- Inputs: [01](01-ledger-clv-net-settlement.md) net EV · [02](02-backtest-statistics.md) stats
- Future: [11](11-place-and-each-way.md) extends staking to place markets.

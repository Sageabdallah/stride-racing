# 11 — Place & each-way markets: the strike-rate lever (shadow first)

**Wave:** 3 · **Depends on:** [01](01-ledger-clv-net-settlement.md), [02](02-backtest-statistics.md) · **Blocks:** none · **Risk:** medium (new bet type — shadow-only until evidence) · **Type:** product/economics

## Goal

Open the biggest untested risk-profile lever: betting place/each-way from the Monte
Carlo place probabilities the engine already computes — targeting ~3× the win
strike rate — with a real place-price model, shadow-tracked before a single dollar.

## Why (evidence)

- The MC engine already emits `place_pct` per runner (`run_tips_pipeline.py:2570`) —
  currently unused for betting.
- `selection_policy.py` implements `each_way_split`/`place_only`/`dutch_stakes`
  (:97-241) but with a crude fixed place-price model (`place_odds = 1 + (o−1)×0.25`,
  :144-174), zero importers, and an explicit "must not be switched on" warning (:10-15).
- The shadow settler scores a PLACE as a full loss (`docs/10:81-82`) — biasing tier
  ROI downward for exactly the longer-priced bands where places land.
- Place markets have weaker favourite-longshot bias; at the value band's prices
  (avg ~$11), place strike is typically ~3× win strike — transforming losing-streak
  math ([02](02-backtest-statistics.md)) and bankroll psychology.
- The repo's own research lists discounted-Harville/Henery-Stern place probabilities
  as diagnostic-only (R1-F8); this task makes them useful.

## Scope

**In:** place settlement in the shadow tracker; place-probability quality check
(Harville vs discounted variants); a place-price model from actual place dividends;
shadow each-way/place ROI report; go/no-go criteria for live tipping.
**Out:** live place betting (follows evidence); place-market data purchase.

## Steps for Kimi Code

1. **Place settlement.** Extend the shadow settler: WIN = (SP−1) net; PLACE =
   (place_div−1) net where place_div comes from settled results (collectors already
   parse dividends — verify NSW/VIC coverage; dead-heat halving applies here too).
   No more −1 for places.
2. **Place-probability audit.** Compare the MC `place_pct` vs realised place rate in
   calibration bands (same band-table format as win calibration in the README). If
   Harville bias shows (it will, per R1-F8), implement discounted-Harville or
   Henery-Stern as `place_pct_v2` and pick by Brier on settled data.
3. **Place-price model.** Fit `place_price = f(win_price, field_size, place_terms)`
   on historical results+dividends (you have both in the DB) — replacing the fixed
   0.25 fraction. Validate out-of-sample; report residual distribution (place
   overround ~15–30% in AU fixed odds must be captured).
4. **Shadow each-way/place report.** Weekly: for every win tip, shadow P&L of
   place-only and each-way at modelled place prices AND at actual place dividends
   (upper bound), net of commission, with [02](02-backtest-statistics.md) stats
   (CI, drawdown, streaks). Compare risk profiles vs win-only.
5. **Go/no-go.** Pre-register in [09](09-forward-validation-protocol.md)'s registry:
   live place/each-way tipping activates only if shadow net ROI CI > 0 over ≥200
   shadow bets with positive CLV. Until then, `STRIDE_MARKET_MIX` stays off (as its
   own warning demands).

## Acceptance criteria

- [ ] Shadow settler handles WIN/PLACE/DEAD-HEAT correctly (unit tests incl. dh halving).
- [ ] Place calibration table committed; chosen place-prob variant justified by Brier.
- [ ] Place-price model out-of-sample error report attached; beats the 0.25 rule.
- [ ] ≥4 weeks of shadow each-way/place vs win-only comparison in the PR.
- [ ] Registry entry exists; `STRIDE_MARKET_MIX` remains off without a PASS.

## Rollout & flags

- Flags: none live (shadow only). `STRIDE_MARKET_MIX` remains off by standing rule.
- Rollback: n/a — measurement only in this task.

## Guardrails

- Do not enable live place betting on the fixed 0.25 price model, ever.
- Place probabilities must come from the same temporal-safe feature path as win
  probs — no post-race information in the MC inputs.
- Each-way staking splits (e.g., 50/50 vs 70/30) are tuned only inside the
  pre-registered experiment, not ad hoc.

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §3 (B7), §5
- Machinery: [01](01-ledger-clv-net-settlement.md) · [02](02-backtest-statistics.md) · Governance: [09](09-forward-validation-protocol.md)
- Staking interaction: [06](06-staking-and-risk-controls.md) caps apply to place bets when live.

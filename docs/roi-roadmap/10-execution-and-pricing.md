# 10 — Execution & pricing: best price, BOG, honest de-vig, exchange reference

**Wave:** 3 · **Depends on:** [01](01-ledger-clv-net-settlement.md) (price-taken tracking) · **Blocks:** none · **Risk:** low · **Type:** economics (free ROI)

## Goal

Stop leaving price on the table and stop estimating fair probability with the
weakest method: record/take the best available price at tip time, settle BOG where
offered, replace proportional de-vig with a validated method, and evaluate Betfair
exchange mid as the reference fair price.

## Why (evidence)

- Settlement discards early-price advantage: backtests settle at SP
  (`backtest_v2_metro.py:317`) — but tips are issued at morning prices; taking
  best-tote/BOG is free, unmeasured ROI (R3-F7).
- `true_market` uses proportional de-vig (`run_tips_pipeline.py:673-674`) — the
  repo's own findings call it "the weakest published method", biased along the AU
  favourite-longshot bias (R1-F7, R3-F9-10); the Shin/power selector is already a
  planned ticket (T16, "cheapest genuinely-open question").
- Reference price inconsistency: the movement signal stores cross-bookmaker medians
  (`odds_movement.py:149-166`) in a table misnamed `betfair_odds_snapshots`
  (`odds_movement.py:260`), while inference takes the **first** bookmaker entry
  (`run_tips_pipeline.py:445-471`). `build_betfair_mapping.py` exists but is
  offline/training-only (`docs/08` §3.4).

## Scope

**In:** price-taken policy + recording; de-vig method comparison & swap (flagged);
single reference-price definition; exchange-mid feasibility report.
**Out:** live exchange betting integration (future); movement features (→ [14](14-late-odds-features.md)).

## Steps for Kimi Code

1. **Reference price, one definition.** `server/python/market_prob.py` (created in
   [05](05-calibrator-and-normalisation.md)) becomes the single source:
   `reference_price(runner) = median across bookmakers at tip-time snapshot` (from
   [04](04-as-of-odds-snapshot.md)'s all-bookmaker storage). Inference, movement signals,
   and backtests all call it. Rename/migrate `betfair_odds_snapshots` or document
   the name as legacy.
2. **De-vig comparison.** Implement Shin and power de-vig alongside proportional in
   `market_prob.py`; compare on settled ledger data (which produces fair probs whose
   implied favourite-longshot slope best matches realised outcomes / lowest
   calibration error of `true_market` vs results); select and switch behind
   `STRIDE_DEVIG=shin|power|proportional`. This implements ticket T16 as written.
3. **Price-taken policy.** At tip publication, record best fixed price across
   bookmakers (and whether BOG was available) as `price_taken` — the ledger
   ([01](01-ledger-clv-net-settlement.md)) already settles there; this makes the
   recorded price the *best* one, quantifying execution gain vs SP in the weekly
   CLV report (`execution_gain_pct = price_taken/SP − 1` summary).
4. **Exchange feasibility note.** Short spike: can the Betfair API provide exchange
   mid at tip-time and T−5 for covered AU meetings? If yes, add
   `exchange_mid` columns to [04](04-as-of-odds-snapshot.md)'s snapshot table (additive)
   — the better fair-prob estimate and WOM/liquidity source for [14](14-late-odds-features.md).

## Acceptance criteria

- [ ] One `grep`-provable reference-price function used by inference, movement, and
      backtest paths.
- [ ] De-vig comparison report attached; selection recorded with method + reason.
- [ ] Weekly report shows `execution_gain_pct`; first weeks quantify BOG/best-price
      uplift vs SP settlement.
- [ ] Exchange spike documented (go/no-go + rate limits + coverage).

## Rollout & flags

- Flag: `STRIDE_DEVIG` (default `proportional` → switch after comparison).
- Rollback: flag back to `proportional`.

## Guardrails

- Do not change edge thresholds when the de-vig method changes in the same PR —
  fair prob moves, so thresholds are re-derived only through [09](09-forward-validation-protocol.md).
- Never use exchange *settled* SP as a training feature (post-race).
- Keep commission handling in [01](01-ledger-clv-net-settlement.md) — this task does not
  touch settlement math.

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §3 (B4), §4 net-new (5)
- Shares `market_prob.py` with [05](05-calibrator-and-normalisation.md) · Feeds [14](14-late-odds-features.md).

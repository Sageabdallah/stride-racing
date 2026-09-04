# 06 — Phase 3: EV and Kelly Baselines

## Purpose

Implement the simple strategies the learned policy must beat. These are the
benchmark, the fallback, and quite possibly the final answer (per the
guide's closing principle).

Offline only.

---

# The Five Baselines

All consume identical inputs: Phase 2 calibrated probabilities + Phase 0
as-of decision-time prices, per race.

1. **always-pass** — bet nothing (the zero line; any strategy with a worse
   log-bankroll than this is destroying money).
2. **highest-probability** — flat stake on the calibrated favourite.
3. **EV threshold** — bet when `p × (price − 1) − (1 − p)` clears a
   threshold; threshold swept on validation folds only.
4. **fractional Kelly** — stake `f × kelly(p, price)`; `f` is a config
   placeholder (`null` — REQUIRED project decision) for production, but the
   backtest sweeps it.
5. **filtered Kelly** — fractional Kelly gated by the same filters
   production applies (coherence verdict, unformed-book fence, one bet per
   race, price band) — the honest "what our current guardrails + Kelly
   would do".

# Shared Assumptions (single config, all strategies)

- Commission model: config placeholder — REQUIRED decision (Betfair AU
  commission on net winnings; exact rate must be confirmed, not assumed).
- Slippage/execution: V1 assumes the snapshot price is taken as-is —
  documented as optimistic; Phase 7's price-degradation tests quantify the
  sensitivity.
- Bankroll convention and stake unit: config placeholder — REQUIRED
  decision.

---

# Acceptance Criteria

- [ ] All five baselines run from one CLI over one shared dataset build;
      the input row set is checksummed and identical across strategies.
- [ ] `test_baselines_share_identical_inputs`: PASS
- [ ] Per-strategy outputs: per-race decisions, bankroll trajectory,
      P/L, max drawdown, bet count, hit rate — persisted as artifacts.
- [ ] Results reproducible run-to-run at fixed commit (hash-compared).
- [ ] Report committed with per-bucket breakdowns (odds, EV, field size,
      race type, period) per global rule 19.
- [ ] No baseline tuned on the final holdout (holdout remains sealed;
      see 10_PHASE_7).

# Stop Conditions

- The filtered-Kelly baseline cannot reproduce production's filter
  behaviour from stored data → the missing signal gets captured going
  forward (small capture PR) and the baseline carries the caveat for the
  historical window.

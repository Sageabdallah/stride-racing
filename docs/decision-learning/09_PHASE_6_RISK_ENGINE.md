# 09 — Phase 6: Deterministic Risk Engine

## Purpose

Own every dollar-shaped decision. The policy proposes an action; this engine
decides the stake — deterministically, from configuration, with hard caps
the policy cannot override.

Offline build; its production wiring happens in Phase 8.

---

# Design

Extend `staking_controls.py` — the drawdown breaker and exposure caps
already exist there and already demote bets atomically via
`decision_contract.demote_active_bet`. This phase adds:

1. **Stake sizing** — `f × kelly(p, price)`, capped:
   - `kelly_fraction: null`  # REQUIRED project decision
   - `max_stake_units: null` # REQUIRED project decision
   - stake floor below which the bet becomes PASS (dust guard)
2. **Price-deterioration guard** — where a later snapshot (`late_t5`)
   exists, if the price has shortened beyond a configured tolerance since
   decision time, demote the bet (reason `price_deterioration`).
3. **Liquidity guard** — config placeholder only. There is **no liquidity
   data** (`total_matched` all-zero, see 02); this guard ships disabled
   with a documented activation condition (a real depth source), not a
   fake threshold.
4. **Bankroll protection** — daily loss stop and the existing drawdown
   breaker, unified in one config (`config/risk.yaml` or the repo's
   existing config convention if one is found first — 16_…PROTOCOL
   §Repository Discovery).

Every refusal flows through `demote_active_bet` so top_picks / full_field /
ledger stay consistent, and every demotion stamps `prediction_stages`
`final_decision` with its reason.

---

# Acceptance Criteria

- [ ] Property tests: stake never exceeds caps; stake is 0 whenever the
      breaker is active; stake is monotone non-decreasing in edge within a
      price band; PASS in ⇒ PASS out (the engine never invents a bet).
- [ ] `test_policy_cannot_bypass_risk_caps`: PASS — engine output is
      authoritative regardless of policy utility values.
- [ ] Config validation fails loudly while REQUIRED placeholders are null
      (per 16_…PROTOCOL §No Silent Assumptions) — a deploy with unset risk
      config must be impossible, not defaulted.
- [ ] Backtest integration: Phases 3/5 rerun through the engine produce
      identical decisions with stakes attached; report updated.

# Stop Conditions

- Any code path found where a learned output can modify a cap or breaker
  threshold → stop, this is an architecture violation (guide
  §Non-Negotiable Architecture).

# 07 — Phase 4: Counterfactual Reward Matrix

## Purpose

For every historical race in the usable era, compute what each available
action would have returned. Full-information horse racing allows this: the
result and the decision-time price are both known, so every simple action's
outcome is reconstructible — this is why V1 is a contextual learner, not
exploration-based RL.

Offline only.

---

# Action Set (V1, per race)

- `PASS`
- `BET(runner_i)` for each runner with a valid decision-time price

One action per race. No stake dimension (stakes are the risk engine's job).

# Reward Definition

For a unit stake at decision-time price `q` with commission rate `c`:

- win:  `+ (q − 1) × (1 − c)`
- lose: `− 1`
- `PASS`: `0`
- **log-bankroll reward** (primary for policy training):
  `log(1 + f × r)` for the configured fraction `f` — accounts for downside
  per global rule 15; raw ROI is reported but never the sole objective.

# Action-Support Metadata (every row)

- price source and snapshot timestamp
- unformed-book / coherence verdict at capture (actions on fenced prices
  are marked `support: excluded`, never silently dropped — the count is
  itself a finding)
- scratched-after-snapshot flag (a bet on a later-scratched runner is a
  refund at Betfair — model as reward 0 and document)
- dead-heat handling: documented rule, not an ad-hoc fix

# Explicitly Forbidden

- Assuming a fill at any price better than the stored snapshot.
- Any liquidity-based fill modelling — `total_matched` is all-zero in the
  depth corpus (see 02); pretending otherwise is fabrication.
- Using SP to fill gaps in decision-time prices.

---

# Acceptance Criteria

- [ ] Reward matrix row count = eligible (race, action) pairs; reconciles
      with Phase 0's exclusion register.
- [ ] `test_reward_uses_decision_time_price_never_sp`: PASS
- [ ] `test_pass_reward_is_zero` / `test_scratched_runner_refund`: PASS
- [ ] Hand audit: ≥ 20 randomly sampled races checked end-to-end against
      raw source records, findings committed in the phase report.
- [ ] Artifact with full metadata; deterministic rebuild verified.

# Stop Conditions

- A reward cannot be computed without an unsupported assumption (e.g.
  missing result for a race with a snapshot) → the race is excluded with a
  reason code, and the exclusion rate is reported. If exclusions exceed
  ~10% of the era, stop and investigate the pipeline gap first.

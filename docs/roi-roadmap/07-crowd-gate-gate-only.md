# 07 — Crowd gate: gate-only (never promote NO_BET → BET)

**Wave:** 2 · **Depends on:** [01](01-ledger-clv-net-settlement.md) (to shadow-measure the change) · **Blocks:** [08](08-consensus-integrity.md) · **Risk:** low · **Type:** economics/safety

## Goal

The consensus/crowd signal can veto or downgrade a bet; it can never create one.
Today a market-correlated crowd can override the EV gate and force bets at no
measured edge — contradicting the system's own "value, not tips" contract.

## Why (evidence)

- Live gate can promote: `crowd_promoted` at `run_tips_pipeline.py:2704-2708`
  converts a NO_BET into a BET when the crowd is strong.
- The crowd is not independent evidence: consensus research prompts include every
  runner's odds (`consensus_agent.py:412-416`, `:647-651`), Perplexity queries
  explicitly ask for "Betfair market movers", a `market_reader` bucket is weighted
  1.4 (`consensus_agent.py:86`), and the model pillar is itself market-anchored
  (`mw` ladder, `run_tips_pipeline.py:679-692`). "Crowd + model agreement" is partly
  "market agrees with market".
- The `crowd ≥ 100` promotion rule was added from **one race**
  (`consensus_blender.py:250-255`).
- In production the market pillar is hard-coded neutral (50) and injections zeroed
  (`run_tips_pipeline.py:2715-2718`, `:2551-2552`) — so the live "convergence" is
  crowd-driven, and the crowd is market-fed.

## Scope

**In:** remove the promotion path (flag-gated), shadow-track what promotion *would*
have done, gate-downgrade semantics kept.
**Out:** de-correlating/rebuilding the crowd score itself (→ [08](08-consensus-integrity.md));
validating 50/30/20 blend weights (→ [12](12-retrain-rebaseline.md) era).

## Steps for Kimi Code

1. **Gate-only flag.** New env `STRIDE_CROWD_GATE_ONLY=true` (default on after merge).
   When on, the crowd path at `run_tips_pipeline.py:2704-2708` may: confirm a BET
   (no change), downgrade HIGH→MEDIUM, or veto BET→NO_BET — and never flip
   NO_BET→BET. Remove/ bypass `crowd_promoted` under the flag.
2. **Shadow the promotions.** Wherever a promotion *would* have fired, write a
   refused-set ledger row (from [01](01-ledger-clv-net-settlement.md)) tagged
   `refusal_reason='crowd_promotion_blocked'` with the would-be price — so the value
   (or cost) of the old behaviour becomes measurable instead of anecdotal.
3. **Report.** Weekly: count of blocked promotions, their shadow P&L net of
   commission, and their CLV. (Expect ~zero to negative value; if the data ever
   shows genuine promotion value, re-opening is a pre-registered experiment under
   [09](09-forward-validation-protocol.md), not a code flip.)
4. **Docs.** Update `docs/08-consensus-and-market.md` and `docs/09-scoring-and-output.md`
   to state the gate-only contract.

## Acceptance criteria

- [ ] Unit test: with flag on, no code path returns BET where the EV gate returned
      NO_BET (test all crowd tiers, including the old `crowd ≥ 100` case).
- [ ] After ≥5 race days: shadow rows exist for every blocked promotion with both
      prices; weekly report prints.
- [ ] With flag off, legacy behaviour is byte-identical (one shadow day diff).

## Rollout & flags

- Flag: `STRIDE_CROWD_GATE_ONLY` (default **on** — safety fix). Rollback: off.

## Guardrails

- Do not change crowd score computation here (→ [08](08-consensus-integrity.md)).
- Do not remove the veto/downgrade paths — asymmetric gating is the design.
- Do not re-enable promotion without a pre-registered window-B validation ([09](09-forward-validation-protocol.md)).

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §3 (B2, B3)
- Next: [08](08-consensus-integrity.md) makes the crowd score worth gating on.

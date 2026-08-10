# 00 — Master Index

## Goal

Implement STRIDE's decision-learning layer in a way that is:

- leakage-safe
- chronologically valid
- AWS-compatible
- testable
- reversible
- measurable against strong baselines
- simple enough to maintain
- extensible to future RL only if justified

---

# Phase Dependency Graph

```text
Phase 0 — Data contract and leakage audit
        ↓
Phase 1 — Historical out-of-time predictions
        ↓
Phase 2 — Chronological calibration
        ↓
Phase 3 — EV and Kelly baselines
        ↓
Phase 4 — Counterfactual reward matrix
        ↓
Phase 5 — Contextual decision policy
        ↓
Phase 6 — Deterministic risk engine
        ↓
Phase 7 — Walk-forward evaluation
        ↓
Phase 8 — AWS shadow deployment
        ↓
Phase 9 — Production promotion gates
        ↓
Phase 10 — Future RL only if justified
```

---

# Output of Each Phase

## Phase 0
Produces:
- immutable decision-time data contract
- leakage checks
- as-of market joins
- dataset validation report

## Phase 1
Produces:
- historical out-of-time CatBoost predictions
- historical out-of-time LightGBM predictions
- historical out-of-time XGBoost predictions
- ensemble outputs generated only from models that had not seen the target race

## Phase 2
Produces:
- calibrated win probabilities
- race-level probability coherence
- calibrator artifact
- calibration report

## Phase 3
Produces:
- always-pass baseline
- highest-probability baseline
- EV threshold baseline
- fractional-Kelly baseline
- filtered Kelly baseline

## Phase 4
Produces:
- race-level action set
- counterfactual return per runner
- counterfactual log-bankroll reward per runner
- PASS reward
- action-support metadata

## Phase 5
Produces:
- contextual decision model
- PASS / BET runner output
- utility estimate
- policy artifact

## Phase 6
Produces:
- deterministic stake sizing
- exposure caps
- price-deterioration guard
- liquidity guard
- bankroll protection

## Phase 7
Produces:
- walk-forward comparison
- statistical confidence analysis
- longshot stress tests
- price degradation tests
- promotion recommendation

## Phase 8
Produces:
- AWS shadow deployment
- versioned policy manifest
- structured logs
- settlement feedback loop
- CloudWatch metrics

## Phase 9
Produces:
- constrained production launch
- rollback path
- go/no-go rules
- operational runbook

## Phase 10
Produces:
- decision on whether sequential RL is warranted
- possible boosted Fitted Q Iteration design
- explicit conditions for rejecting deeper RL

---

# Stop Conditions

Claude Code must stop and flag the issue if any of the following occur:

- future information enters pre-race features
- OOT prediction generation cannot be reproduced
- calibrated race probabilities are invalid
- final SP is being used before it was known
- historical price snapshots do not map to the actual decision timestamp
- counterfactual rewards assume fills that were not actually possible
- learned policy beats the baseline only because of one or a handful of longshot winners
- policy deployment requires bypassing the risk engine
- a new phase requires changing prediction outputs without revalidating downstream components
- production and training schemas diverge
- model artifact versions cannot be traced
- active policy cannot be rolled back cleanly


# Agent Execution Files

After understanding the technical phases, the coding agent must also use:

- `16_CLAUDE_CODE_EXECUTION_PROTOCOL.md` — how to execute, test, stop and report each phase.
- `17_IMPLEMENTATION_STATUS.md` — persistent implementation state and blockers.

These files prevent phases from being actioned out of order or marked complete without evidence.

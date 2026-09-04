# Decision-learning layer — the plan

The specification for STRIDE's decision layer: given a calibrated win
probability and the price available at decision time, should the system pass
or bet, and on which runner? It sits downstream of the prediction stack and
never replaces it.

These files moved here from the repository root on 2026-09-03. Nothing about
their content changed; they are numbered so they read in order.

## Read in order

| # | File | What it covers |
|---|---|---|
| 00 | [Master index](00_MASTER_INDEX.md) | Goal, phase dependency graph, what each phase produces, stop conditions |
| 01 | [Global rules](01_GLOBAL_RULES.md) | What the system must and must not do — leakage, optimisation, policy, AWS |
| 02 | [Architecture and contracts](02_ARCHITECTURE_AND_CONTRACTS.md) | The real stack, data stores, existing primitives to reuse, the pinned contracts |
| 03 | [Phase 0 — data contract and leakage audit](03_PHASE_0_DATA_AND_LEAKAGE_AUDIT.md) | What information existed at decision time, proven in code |
| 04 | [Phase 1 — out-of-time predictions](04_PHASE_1_OOT_PREDICTIONS.md) | The only predictor outputs the decision layer may train on |
| 05 | [Phase 2 — chronological calibration](05_PHASE_2_CALIBRATION.md) | Honest probabilities, fitted forward, coherent per race |
| 06 | [Phase 3 — EV and Kelly baselines](06_PHASE_3_BASELINES.md) | The five strategies the learned policy has to beat |
| 07 | [Phase 4 — counterfactual rewards](07_PHASE_4_COUNTERFACTUAL_REWARDS.md) | What every available action would have returned |
| 08 | [Phase 5 — contextual policy](08_PHASE_5_CONTEXTUAL_POLICY.md) | The model that answers "is this price worth acting on?" |
| 09 | [Phase 6 — risk engine](09_PHASE_6_RISK_ENGINE.md) | Deterministic stake sizing and hard caps the policy cannot override |
| 10 | [Phase 7 — walk-forward evaluation](10_PHASE_7_WALK_FORWARD_EVALUATION.md) | The adversarial pass, the sealed holdout, the promotion recommendation |
| 11 | [Phase 8 — AWS integration and shadow](11_PHASE_8_AWS_INTEGRATION_AND_SHADOW.md) | Running in production without touching production |
| 12 | [Phase 9 — production gates](12_PHASE_9_PRODUCTION_GATES.md) | The evidence that promotes it, and how it comes back out |
| 13 | [Phase 10 — future RL](13_PHASE_10_FUTURE_RL.md) | Whether anything beyond a contextual learner is warranted. Default answer: no |
| 14 | [Testing and observability](14_TESTING_AND_OBSERVABILITY.md) | Cross-cutting: the test taxonomy and the three failure classes |
| 15 | [Definition of done](15_DEFINITION_OF_DONE.md) | Per phase and for the project. "Code exists" is not on the list |
| 16 | [Execution protocol](16_CLAUDE_CODE_EXECUTION_PROTOCOL.md) | How a coding agent executes, tests, stops and reports each phase |
| 17 | [Implementation status](17_IMPLEMENTATION_STATUS.md) | The live register — phase states, blockers, unresolved assumptions |

## Also here

- [`V1_IMPLEMENTATION_GUIDE.md`](V1_IMPLEMENTATION_GUIDE.md) — **superseded.**
  The earlier V1 guide, retained deliberately (`CLAUDE.md` says so). Where it
  disagrees with `02_ARCHITECTURE_AND_CONTRACTS.md` about the stack, 02 wins:
  it was written against the actual repository and infrastructure.
- The Phase −1 record — architecture stabilisation done before Phase 0 —
  is in [`../phase-minus-1/ARCHITECTURE_STABILISATION.md`](../phase-minus-1/ARCHITECTURE_STABILISATION.md).

## Status

Phase 0 has not started. `17_IMPLEMENTATION_STATUS.md` is the register, and a
row moves only with an evidence link — see `15_DEFINITION_OF_DONE.md` on what
does not count as done.

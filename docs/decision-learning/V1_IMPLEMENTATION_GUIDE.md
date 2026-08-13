# STRIDE Decision-Learning Implementation Guide

## Purpose

This repository extension adds a **decision-learning layer** to STRIDE without replacing the existing race-prediction stack.

STRIDE already uses:

- CatBoost
- LightGBM
- XGBoost
- An ensemble probability layer
- AWS-hosted inference
- Neon/Postgres for operational data
- S3 for artifacts/history
- FastAPI for serving
- EventBridge/Lambda for orchestration and automation

The goal is to add a downstream decision system that answers:

> Given STRIDE's calibrated win probabilities and the market price available at decision time, should STRIDE pass or bet, and if betting, which runner should be selected?

The first implementation is **not full sequential reinforcement learning**.

The first implementation is a:

> **Full-information contextual decision learner with a bandit-compatible interface**

This is intentional because historical horse-racing results and pre-race prices allow counterfactual rewards to be reconstructed for many simple actions.

---

# Non-Negotiable Architecture

The system must remain split into two distinct responsibilities.

## Prediction layer

Answers:

> Which horse is most likely to win?

Owned by:

- CatBoost
- LightGBM
- XGBoost
- Ensemble
- Calibration

## Decision layer

Answers:

> Is the available price worth acting on?

Owned by:

- Contextual decision model
- Deterministic risk engine
- Evaluation pipeline

Never merge these responsibilities.

---

# Locked V1 Flow

```text
Race / horse features
        ↓
CatBoost + LightGBM + XGBoost
        ↓
Raw ensemble probabilities
        ↓
Race-aware chronological calibration
        ↓
Calibrated p(win)
        ↓
Decision features
        ↓
Contextual utility model
        ↓
PASS or BET runner
        ↓
Deterministic fractional-Kelly risk engine
        ↓
Stake caps / liquidity / exposure rules
        ↓
Recommendation
        ↓
Settlement
        ↓
Realised and counterfactual rewards
```

---

# Mandatory Execution Order

Claude Code must follow these files in order.

1. `00_MASTER_INDEX.md`
2. `01_GLOBAL_RULES.md`
3. `02_ARCHITECTURE_AND_CONTRACTS.md`
4. `03_PHASE_0_DATA_AND_LEAKAGE_AUDIT.md`
5. `04_PHASE_1_OOT_PREDICTIONS.md`
6. `05_PHASE_2_CALIBRATION.md`
7. `06_PHASE_3_BASELINES.md`
8. `07_PHASE_4_COUNTERFACTUAL_REWARDS.md`
9. `08_PHASE_5_CONTEXTUAL_POLICY.md`
10. `09_PHASE_6_RISK_ENGINE.md`
11. `10_PHASE_7_WALK_FORWARD_EVALUATION.md`
12. `11_PHASE_8_AWS_INTEGRATION_AND_SHADOW.md`
13. `12_PHASE_9_PRODUCTION_GATES.md`
14. `13_PHASE_10_FUTURE_RL.md`
15. `14_TESTING_AND_OBSERVABILITY.md`
16. `15_DEFINITION_OF_DONE.md`
17. `16_CLAUDE_CODE_EXECUTION_PROTOCOL.md`
18. `17_IMPLEMENTATION_STATUS.md`

Do not skip a phase unless every acceptance criterion in the skipped phase has already been demonstrated by existing code and tests.

---

# Claude Code Operating Rules

When actioning this plan:

1. Inspect the existing repository before creating new modules.
2. Reuse existing abstractions where they are clean and compatible.
3. Do not rewrite working prediction code merely to match this document.
4. Prefer small, testable changes.
5. Keep decision logic isolated from prediction logic.
6. Add tests with every behavioural change.
7. Preserve backward compatibility unless a migration is explicitly documented.
8. Never silently change training data semantics.
9. Never silently change decision timestamps.
10. Never introduce a new dependency unless it solves a documented requirement.
11. Never introduce full RL before the contextual decision layer has passed all gates.
12. Never deploy a policy directly to active production without shadow evaluation.
13. Never use final SP or post-decision information as a pre-race feature.
14. Never allow the learned policy to bypass deterministic risk caps.
15. Never use raw ROI as the sole optimisation target.
16. Never train decision logic on in-sample predictor outputs.
17. Never evaluate on training periods.
18. Never tune against the final holdout period.
19. Never assume displayed odds were executable unless the stored snapshot represents executable odds.
20. Never use Lambda for long-running model training.

---

# Default Repository Layout

Adapt to the existing repo rather than forcing this exact structure.

```text
src/
  prediction/
  calibration/
  decision/
    features.py
    policy.py
    actions.py
    rewards.py
    inference.py
  risk/
    kelly.py
    limits.py
  evaluation/
    walk_forward.py
    metrics.py
    stress_tests.py
  data/
    contracts.py
    asof.py

jobs/
  build_oot_predictions.py
  fit_calibrator.py
  build_counterfactual_rewards.py
  train_decision_policy.py
  evaluate_policy.py

tests/
  decision/
  calibration/
  risk/
  evaluation/

config/
  decision.yaml
  risk.yaml

artifacts/
  manifests/
```

---

# Implementation Principle

Do not optimise for the most sophisticated model.

Optimise for:

- correctness
- time integrity
- reproducibility
- measurable incremental value
- easy rollback
- AWS deployment simplicity
- observability
- controlled risk

If a simpler EV or fractional-Kelly rule outperforms the learned policy out-of-time, keep the simpler rule.


# Execution State

Claude Code must use `17_IMPLEMENTATION_STATUS.md` as the persistent phase tracker and must follow `16_CLAUDE_CODE_EXECUTION_PROTOCOL.md` when making changes.

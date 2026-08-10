# 01 — Global Rules

These rules apply to every phase.

---

# Things STRIDE Must Do

## Data Integrity

1. Every production feature must have an `as_of_timestamp`.
2. Every pre-race feature must satisfy:

```text
feature_timestamp <= decision_timestamp
```

3. Market data must be joined by time, not just by `race_id`.
4. Historical replay must reproduce the information set that existed at decision time.
5. Every training row must be traceable to source records.
6. All generated datasets must record:
   - build timestamp
   - source range
   - code version or commit
   - schema version

## Modelling

7. Prediction and decision optimisation remain separate.
8. Decision training uses out-of-time predictor outputs only.
9. Calibration is fitted chronologically.
10. Race-level probabilities should remain coherent.
11. Baselines are implemented before the learned policy.
12. The learned policy must beat the best valid baseline, not just make money.
13. The first learned policy chooses:
   - PASS
   - BET runner
14. Stake sizing remains deterministic in V1.
15. Reward must account for downside, not raw ROI alone.

## Evaluation

16. All strategies use identical races and market snapshots.
17. All strategies use the same transaction-cost and execution assumptions.
18. The final holdout period is opened only once.
19. Performance must be tested by:
   - odds bucket
   - EV bucket
   - confidence bucket
   - race type
   - field size
   - time period
20. Longshot sensitivity tests are mandatory.
21. Price degradation tests are mandatory.
22. Bootstrap or another blocked/clustered uncertainty method is mandatory before production promotion.

## Deployment

23. New policy artifacts must be versioned.
24. Production loads a release manifest rather than independently selecting model files.
25. New policies must pass:
   - offline evaluation
   - shadow evaluation
   - constrained production
26. Rollback must require configuration or manifest change, not retraining.
27. Heavy training must not run inside Lambda.
28. AWS changes should reuse current infrastructure where practical.

---

# Things STRIDE Must Not Do

## Leakage

Do not:
- use final SP as a feature for an earlier decision
- use closing market movement that occurred after decision time
- use race-result-derived features before settlement
- use updated post-race ratings in historical pre-race rows
- join market snapshots without temporal constraints
- compute aggregates using future races
- fit calibrators using the test race outcome
- train the decision layer on in-sample probabilities

## Optimisation

Do not:
- optimise raw ROI alone
- reward longshots purely because payout is large
- let a handful of winners dominate policy selection
- tune probability calibration on profit
- let policy training change the meaning of prediction probabilities
- introduce arbitrary reward shaping without documenting the economic objective
- choose a discount factor for future RL without defining an episode

## Policy

Do not:
- allow unrestricted stake actions in V1
- let the learned policy bypass risk caps
- allow more than one active bet per race in V1
- add live stochastic exploration with money
- use a true contextual-bandit algorithm on full-information historical data if doing so discards usable counterfactual outcomes
- introduce PPO/DQN/SAC just to make the system "more RL"

## AWS

Do not:
- create unnecessary microservices
- run long model training in Lambda
- deploy candidate policy directly into active recommendations
- mix incompatible predictor/calibrator/policy versions
- use high-cardinality IDs as CloudWatch metric dimensions
- rely on a single production artifact with no rollback version

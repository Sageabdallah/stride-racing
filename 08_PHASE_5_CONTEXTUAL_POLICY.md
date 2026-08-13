# 08 — Phase 5: Contextual Decision Policy

## Purpose

Train the model that answers *"is this price worth acting on?"* — mapping
decision-time context to `PASS` or `BET(runner)`, optimising the Phase 4
counterfactual log-bankroll reward.

Offline only. The policy never touches stake sizing and never bypasses the
risk engine (global rules 13-14, "Policy" prohibitions).

---

# Design

## Features (decision-time only, from Phase 0's contract)

- calibrated `p(win)` (Phase 2) and its race-normalised form
- decision-time price, implied probability, EV, edge
- race context: field size, class level, distance band, going group
- book context: overround, coherence verdict fields (`book`, `coverage`,
  `top2`, `incoherent`, `corrupt` — already emitted per race in the tips
  artifact under `book_coherence`)
- model-agreement signals from OOT components (spread of p_xgb/p_lgb/p_cat)

No identity features (horse/jockey/trainer IDs) in V1 — the policy should
generalise over prices and probabilities, not memorise entities.

## Model Class

Start with the simplest thing that can win: regularised logistic /
gradient-boosted utility regression over the action set, argmax with a
PASS-bias margin. Anything fancier requires the simple version's measured
failure first (16_…PROTOCOL §Complexity).

## Training Discipline

- Inputs are OOT predictions only (Phase 1) — never production in-sample
  outputs.
- Chronological folds shared with Phase 3's sweeps; the sealed holdout
  stays sealed until Phase 7.
- Class imbalance and reward asymmetry handled in the objective, not by
  resampling races (resampling breaks bankroll-trajectory realism).

---

# Acceptance Criteria

- [ ] `test_policy_features_are_decision_time_only`: PASS (features are
      drawn exclusively from the Phase 0 contract's allowed set)
- [ ] `test_policy_never_emits_stake`: PASS — output is action only.
- [ ] On validation folds (not holdout): policy ≥ best baseline on
      log-bankroll, with the comparison table committed.
- [ ] Ablation: policy vs EV-threshold using the same features — the report
      states what the learned component actually adds.
- [ ] Longshot dependence check: removing the top 5 winning longshot bets
      does not flip the comparison (preview of Phase 7's stress tests).
- [ ] Policy artifact versioned with full metadata (`decision_model` slot).

# Stop Conditions

- The policy only beats baselines via a handful of longshot winners
  (00 §Stop Conditions) → record honestly; do not reweight until it passes.
- Fold-to-fold instability (sign flips on log-bankroll advantage) →
  BLOCKED: more data (the era grows weekly) beats more tuning.

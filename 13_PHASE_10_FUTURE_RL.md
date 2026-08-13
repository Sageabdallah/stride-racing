# 13 — Phase 10: Future RL — Only If Justified

## Purpose

Decide whether anything beyond the contextual learner is warranted. The
default answer is no.

---

# The Question Sequential RL Answers (and V1 doesn't)

V1 treats every race as independent. Sequential methods are justified only
if **path dependence** measurably matters:

- bankroll effects: stake fractions interacting with drawdown state
- exposure interactions: correlated bets across a card (same track bias,
  same going read)
- intra-day information: earlier results shifting later-race value

# Required Evidence Before Any RL Work

- [ ] Analysis on realised (not simulated) decision history showing the
      contextual policy leaves measurable value on the table **because of
      sequencing** — e.g. day-level bankroll simulations where reordering
      races changes outcomes beyond noise.
- [ ] A defined episode (a race day? a week?) with a defensible terminal
      state — global rule: no discount factor without a defined episode.
- [ ] The contextual layer has been ACTIVE and stable ≥ 8 weeks.

# If Justified: the Only Sanctioned V2 Shape

Boosted **Fitted Q-Iteration** over the same counterfactual framework —
batch, offline, no live exploration (money never explores; global rule:
no live stochastic exploration). PPO/DQN/SAC remain prohibited: they solve
an exploration problem this domain does not have, and "more RL" is not a
goal (01 §Optimisation).

# Explicit Rejection Conditions

Record REJECT in the register (a completed phase, not a failure) if any of:

- sequencing analysis shows no exploitable path dependence
- the extra machinery cannot beat the contextual layer on Phase 7's own
  protocol (walk-forward + stress + CI lower bound)
- operational complexity (a second training loop, a second manifest slot)
  is not paid for by the measured uplift

The system's end state is allowed to be: calibrated probabilities, an EV
gate, fractional Kelly, and hard risk controls. If that is what the
evidence supports, that is the answer.

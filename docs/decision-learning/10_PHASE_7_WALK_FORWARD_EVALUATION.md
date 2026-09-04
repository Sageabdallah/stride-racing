# 10 — Phase 7: Walk-Forward Evaluation

## Purpose

The adversarial pass. Decide — with uncertainty quantified — whether the
policy + risk engine beats the best baseline out-of-time, and produce the
promotion recommendation Phase 8/9 acts on.

Offline only. The sealed holdout is opened **once, here** (global rule 18).

---

# Protocol

1. **Rolling-origin walk-forward** over the full usable era: train ≤ T,
   evaluate (T, T+w], advance. Same folds for every strategy.
2. **Sealed holdout**: the most recent contiguous window (size documented
   in the report) is scored exactly once, after all tuning is frozen. Log
   the artifact hashes that were frozen *before* the holdout run.
3. **Uncertainty**: blocked bootstrap over race days (races within a day
   are correlated — resample days, not races). Report CIs on log-bankroll
   and ROI for every strategy.
4. **Stress tests** (all mandatory, global rules 20-21):
   - longshot excision: drop the top 1/3/5 winning bets by price; the
     policy-vs-baseline verdict must survive.
   - price degradation: haircut every taken price by 2% / 5% / 10%;
     report where the edge dies.
   - coherence stress: restrict to races whose books were flagged
     `incoherent` vs clean; the policy must not be secretly harvesting
     broken books (issue #123's lesson).
5. **Slice reports** (global rule 19): odds bucket, EV bucket, confidence
   bucket, race type, field size, month.

---

# Acceptance Criteria

- [ ] Identical race/snapshot/assumption sets across strategies, verified
      by checksum in the report.
- [ ] Holdout opened once; run logged with pre-registered artifact hashes.
- [ ] Bootstrap CIs reported; the promotion case is stated in terms of the
      CI lower bound vs the best baseline, not the point estimate.
- [ ] All stress tests executed and reported, including the failures.
- [ ] Written **promotion recommendation**: PROMOTE TO SHADOW /
      HOLD (collect more data) / REJECT (keep baseline), with reasons.

# Interpretation Rules

- If filtered Kelly ≥ policy on the CI lower bound: the recommendation is
  REJECT, and that is a **successful project outcome** (16_…PROTOCOL
  §Final Rule). The baselines still ship value via Phase 6's risk engine.
- HOLD is the expected outcome while the snapshot era is short — the era
  grows every week; re-running this phase later is cheap by design.

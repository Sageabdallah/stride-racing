# 12 — Phase 9: Production Promotion Gates

## Purpose

Define — before anyone is tempted — exactly what evidence promotes the
policy from shadow to constrained production, and how it comes back out.

---

# Gate 1: SHADOW → CONSTRAINED_ACTIVE

All required, evaluated on ≥ 4 full weeks of shadow data including ≥ 4
Saturday cards:

- [ ] Shadow realised log-bankroll ≥ the live pipeline's realised result on
      the same races, on the blocked-bootstrap CI lower bound.
- [ ] Shadow max drawdown within the configured cap.
- [ ] No stop-condition events (settlement gaps, shadow-affects-production
      diffs, manifest mismatches) in the final 2 weeks.
- [ ] Phase 7 stress-test verdicts still hold on the shadow era.
- [ ] Risk config REQUIRED placeholders are set and reviewed (they cannot
      be null past this gate).
- [ ] Written go/no-go signed off by the project owner — a human click,
      deliberately, like every `main` merge in this repo.

## Constrained mode

- Stakes scaled to a configured fraction (e.g. 25%) of the risk engine's
  output — set in the manifest config, not code.
- One release at a time; the previous manifest remains deployable.
- Duration: ≥ 2 weeks before Gate 2.

# Gate 2: CONSTRAINED_ACTIVE → ACTIVE

- [ ] Constrained-period realised results consistent with shadow-period
      expectations (no sign flip on the CI).
- [ ] Zero unexplained decision divergences between manifest replay and
      what production actually did.
- [ ] Runbook exercised at least once (see below).

# Rollback (both gates)

Rollback = point the config at the previous manifest release_id, or flag
the layer off entirely. **No retraining, no code change, no redeploy of the
image** (global rule 26). Rollback must be demonstrated in the constrained
period, not just documented.

# Runbook (deliverable of this phase)

A short `docs/decision-learning/RUNBOOK.md`: who flips what, in what order,
the quiet-day/no-Friday-Saturday deploy rule, the verification queries
(shadow/live row counts, artifact fields), and the rollback one-liner.
The register (`17_IMPLEMENTATION_STATUS.md`) records every state
transition with its evidence link.

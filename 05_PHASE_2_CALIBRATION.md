# 05 — Phase 2: Chronological Calibration

## Purpose

Turn Phase 1's OOT ensemble outputs into honest win probabilities, fitted
strictly chronologically, coherent at race level, and shipped as a versioned
artifact.

Offline only.

---

# Design

- Fit calibrators (isotonic first — the repo already has OOF isotonic
  machinery; do not introduce a new method before the existing one is
  evaluated) on OOT predictions from races **before** each evaluation
  window. Never pooled across time.
- Race-level coherence: after per-runner calibration, renormalise within
  each race so probabilities sum to 1 (report pre-normalisation sums; a
  drifting sum is diagnostic, not noise).
- Existing evidence to reuse: `evidence/calibrator_shadow_*.json` and
  `calibrator_compare_pooled.json` already accumulate in the evidence
  bucket — the calibration report must reconcile with them, not ignore them.
- Artifact: `calibrator` slot in the release manifest schema
  (`release_manifest.py` already reserves it), with full metadata.

---

# Acceptance Criteria

- [ ] `test_calibrator_fit_uses_only_prior_races`: PASS
- [ ] `test_race_probabilities_sum_to_one`: PASS (tolerance documented)
- [ ] Reliability report per period (weekly buckets): expected calibration
      error, Brier score, and reliability diagrams committed under
      `docs/decision-learning/`.
- [ ] Comparison against the production calibration path on the same races
      (the decision layer must know if its probability source differs from
      what production shows users).
- [ ] Calibrator artifact written with complete metadata; loading it and
      re-scoring a pinned fixture reproduces stored outputs exactly.

# Stop Conditions

- Calibration materially degrades on any odds bucket (longshot bucket
  especially) → record it; Phase 3+ must carry the caveat rather than
  average it away.
- The isotonic Option B decision (outstanding audit item) conflicts with
  this phase's design → surface it for a project decision instead of
  quietly choosing.

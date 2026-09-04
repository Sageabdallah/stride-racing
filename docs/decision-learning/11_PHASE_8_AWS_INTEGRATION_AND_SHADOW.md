# 11 — Phase 8: AWS Integration and Shadow Deployment

## Purpose

Run the policy in production **without letting it touch production**: same
inputs, same timing, decisions recorded and settled, published tips
byte-identical.

This is the first phase that modifies the live pipeline. Every rule in 02
§Deploy paths applies.

---

# Design

1. **Where it runs**: inside the existing Fargate tips job, immediately
   after `reconcile_crowd_bet` and staking controls have produced the real
   decision — not as a new service (16_…PROTOCOL §AWS Change Protocol:
   the existing job runner can do this; no new AWS services).
2. **Flag**: `STRIDE_DECISION_SHADOW`, default off, shipped via the
   secrets path exactly like `STRIDE_BOOK_COHERENCE` (PR #129 is the
   template). Off ⇒ the code path is dormant and the pipeline is
   byte-identical.
3. **Artifact loading**: activate `release_manifest.py` for the
   `decision_model` + `calibrator` slots. The job loads the manifest the
   config names — it never "picks the newest file" (global rule 24).
4. **Outputs** (never mixed with real picks):
   - `shadow_decisions` table: race key, policy action, utility, stake the
     risk engine would have set, manifest release_id, decision timestamp,
     and the real pipeline's decision alongside for the same race.
   - an artifact field in the day JSON (like `book_coherence`) for
     eyeball-ability.
5. **Settlement loop**: a small extension to the existing results
   collection joins outcomes onto `shadow_decisions` (reuse
   `identity_normalization` keys) and computes realised shadow P/L daily.
6. **Observability**: shadow rows per day become a watcher postcondition —
   a race day with tips but zero shadow rows while the flag is on files an
   issue (ran-but-empty class; see 14_…OBSERVABILITY). CloudWatch metrics
   stay low-cardinality (counts and P/L, no per-race dimensions).

---

# Rollout Sequence (mirrors the flag playbook that already works)

merge dormant → quiet-day `deploy-infra` dispatch → weekday flag on →
verify content (shadow rows + artifact field present, published tips
byte-identical vs a flag-off replay) → leave running ≥ 4 weeks.

# Acceptance Criteria

- [ ] Flag off: replay produces byte-identical published output
      (`test_shadow_flag_off_is_inert` + a production replay diff).
- [ ] Flag on, first weekday card: shadow rows written for every race with
      tips; artifact field present; **zero** diffs in published tips.
- [ ] Settlement joins ≥ 95% of shadow decisions within 48h; the join-miss
      remainder is itemised, not ignored.
- [ ] Watcher postcondition live and demonstrated (kill test: run with the
      table write disabled in a sandbox, watcher files the issue).
- [ ] Rollback demonstrated: flag off → next run byte-identical again.

# Stop Conditions

- Any diff in published tips while shadowing → flag off immediately, file
  the issue with the diff attached. Shadow influencing production is the
  one unforgivable failure of this phase.

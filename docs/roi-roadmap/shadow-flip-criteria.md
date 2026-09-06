# Shadow-flip criteria — the promotion bar for each dark-launched flag

**Type:** governance · **Status:** DRAFT — merges only when zero SAGE-APPROVAL
markers remain · **Companion to:** [12-preregistration.md](12-preregistration.md)

Each flag below is dark-launched (shadow-logging while the legacy path stays
live). This document fixes, per flag, the evidence required to flip it — decided
now, before the shadow data exists, so the bar cannot move to fit the logs.
Every section ends with the same four lines: who flips, where, the rollback, and
the log line to watch. The common floor: **≥ 5 race days** of shadow data, and
the flip is a deliberate human act — never a scheduled job.

---

## STRIDE_RENORMALISE_FIELD (PR #3, branch `roi/05-calibrator-and-normalisation`)

**What it does:** after the `mw` market anchor, renormalises each race's
`winPercentage` to sum to 1 (`_renormalise_field`,
`server/python/run_tips_pipeline.py:821`; flag check at :716, default OFF). Edge
and EV consume the renormalised value. Per-runner confidence-tier changes are
logged as structured `[RENORM_TIER_TRANSITION]` lines (:843) — transitions are
never silently dropped.

**Shadow evidence:** `server/python/shadow_calibrator_compare.py` (same branch)
scores every shadow race under legacy and renormalised variants and emits one
comparison JSON (`--output shadow_compare.json`): per-variant Brier/log-loss,
field sums, and the confidence-tier transition report (per-race runner detail,
old tier → new tier).

**Flip when all hold:**

1. **≥ 5 race days** of shadow comparison JSON.
2. **Renormalised Brier ≤ current** on the aggregate across the whole shadow
   window (pooled, not best-day). Field sums = 1.0 ± 1e-6 on every published
   race (the `[RENORM]` line).
3. **Sage has reviewed the tier-transition matrix.** Transitions are *expected*
   — the anchor leaves fields off-unity, so correcting the sums moves borderline
   runners. The review is for pathology: mass demotions out of a tier, one
   direction dominating, or transitions clustering in one track/class. The
   quantitative bound: **<= 5% of shadow runners transitioning in aggregate, with any single race above 25% requiring explicit sign-off** (resolved by Sage 2026-08-01).

**Who flips:** Sage. **Where:** `STRIDE_RENORMALISE_FIELD=true` in the deployment
env (`.env`). **Rollback:** flag off — the legacy path is byte-identical when the
flag is unset (:711-716). **Log line to watch:** `[RENORM] field sum -> ...` and
`[RENORM_TIER_TRANSITION] {...}` (stderr).

---

## STRIDE_INTERACTION_PARITY (branch `roi/03-serve-time-probability-fixes`, PR #5)

**Status: already default-on; no flip decision is required.**

The parity fix computes the five interaction features from the training formulas
(`server/python/feature_interactions.py`) instead of the legacy inline versions
whose `barrier_x_pace_inv` is sign-flipped against training. It has been **default
ON** since the task-03 on/off comparison (`interaction_parity_enabled`,
`server/python/serve_features.py:66-78`): on the 21,108-runner metro window the
parity-vs-legacy delta is **exactly 0** on Brier and log-loss
(`examples/interaction_parity_comparison_2026-03-04_2026-04-18.json`, same
branch). The feature is provably **inert** in the current artifact — importance
0.0, because training never populates `barrier_advantage`, so the interaction is
all-zero at fit time either way. The on-flip changed no live output; it removed
the wrong formula *before* the task-12 retrain makes the feature real.

**A flip decision re-opens only if the 12P-3 work chooses to plumb
`barrier_advantage`** (making the interaction non-zero). At that point the flag
stops being inert and this document gains a new section with its own shadow
criteria — written before that plumbing ships, not after.

**Who flips:** no one (already on). **Where:** n/a — default ON; explicit
`STRIDE_INTERACTION_PARITY=false` in env reverts. **Rollback:**
`STRIDE_INTERACTION_PARITY=false` (legacy formulas kept for one release).
**Log line to watch:** none — the flag is output-inert by construction; the
watch item is any PR touching `barrier_advantage` plumbing.

---

## STRIDE_SERVE_LIVE_FEATURES (PR #11, branch `fix/serve-feature-liveness`)

**What it does:** plumbs the 15 trained-but-never-served features — 25.45% of
the artifact's importance mass, dead at serve
(`docs/research/FEATURE_PROVENANCE.md`, branch `docs/feature-provenance-sweep`)
— into the feat dict (`server/python/serve_features.py:29-48, :106`; default
OFF, flag off = byte-identical feat dict). Deploy together with
`STRIDE_SERVE_NAN_CONTRACT=1` so unknown sectionals reach the trees as NaN (the
trained missingness signal), not 0.

**Shadow evidence:** with `STRIDE_SERVE_LIVE_FEATURES_SHADOW` on (and the main
flag off), the pipeline scores each race **both** ways, publishes the **legacy**
probabilities, and appends per-runner deltas to
`logs/serve_liveness_shadow_<date>.json` (`_write_serve_liveness_shadow`,
`server/python/run_tips_pipeline.py:1056-1085`). Each entry carries
`legacy_prob_pct`, `live_prob_pct`, `delta_pp`, both field ranks, and
`tier_change` (the would-be top-3 in/out flip).

**Flip when all hold:**

1. **≥ 5 race days** of `logs/serve_liveness_shadow_*.json`.
2. **No day errored to legacy.** Every shadow day written completely; any day
   where the live path raised and fell back (or the shadow write failed —
   `[FEATURES] shadow log write failed`) restarts the 5-day count.
3. **The delta distribution is stable day-over-day** — per-day summary of
   `delta_pp` (mean, spread, max |delta|) shows no trend, no regime shift, and
   no single race whose deltas are an order of magnitude off the window's.
4. **Top-3 flip rate ≤ **15%** (resolved by Sage 2026-08-01).**

**Deltas are EXPECTED to be large.** This flag wakes up 25.45% of the model's
importance mass; small deltas would mean the plumbing is inert and the shadow is
measuring nothing. The criterion is **stability + review, not smallness**: Sage
reviews the largest-delta races by name before flipping, and a large, stable,
explainable delta passes where a small erratic one fails.

**Who flips:** Sage. **Where:** `STRIDE_SERVE_LIVE_FEATURES=1` (with
`STRIDE_SERVE_NAN_CONTRACT=1`) in the deployment env (`.env`), shadow flag off.
**Rollback:** flag off — byte-identical feat dict, no artifact change.
**Log line to watch:** `[FEATURES] shadow log write failed` (stderr) — its
absence — and the nightly `logs/serve_liveness_shadow_<date>.json` for the
delta summaries.

---

## Amendment rule

Same as the pre-registration: append-only once a shadow window opens. Changing a
threshold, day-count, or review bar after shadow data exists voids that flag's
window and restarts its day count.

---

## STRIDE_LEARNED_BLEND (2026-09-05; rides the v3 candidate)

[SAGE-APPROVAL] confirm this section's bar

**What it does:** `RacingMLModel.predict_components` combines the three base
predictions with the artifact's persisted `ensemble_combiner` — a
non-negative weighted average fitted on purge-gapped walk-forward OOF rows
(`ensemble_combiner.py`) — instead of the hardcoded seed weights in
`_model_performance`. Off, or with an artifact that carries no combiner, the
legacy blend is byte-identical. It is a weighted average of the same raw
quantities, not a new calibration layer (standing prohibition 3).

**Evidence:** not a separate shadow window. The combiner is judged in the
retrain's CV on identical rows, cross-fitted (fitted on prior folds, scored
on the next), against the equal-mean arm and the production blend measured on
the same rows (`retrain_v2` "COMBINATION ARMS"), by the staging criterion of
the 2026-09-05 pre-registration amendment; and then in the v3 candidate's
parallel-scoring week, where the proof job runs with the flag on.

**Flip when all hold:**

1. The cross-fitted simplex arm is not worse than the production arm on
   pooled Brier and not worse on per-race top-1 hit rate against the
   tip-time favourite, on the same folds, in the candidate's CV report.
2. The v3 candidate that carries it has passed NEW-BEATS-OLD.
3. The flag is set together with the candidate's installation into the
   stable slot — never on the v2 artifact (which carries no combiner, so the
   flag would be inert there, but a flag that reads on while doing nothing is
   a false record).

**Who flips:** Sage. **Where:** `STRIDE_LEARNED_BLEND=true` via the GitHub
secret → deploy-infra path (test_flag_plumbing.py must list it first).
**Rollback:** flag off — byte-identical legacy blend. **Log line to watch:**
`predict_components` reports `method: learned_blend` in the prediction stages
audit; its absence with the flag on means the artifact carries no combiner.

---

## Note 2026-09-05 — the criteria are now computed, not read by hand

`server/python/shadow_flip_review.py` reads the durable evidence store and
prints one verdict per criterion above, with every threshold quoted from this
document next to its constant (5 days, 15% top-3 flip rate, order-of-magnitude
race outliers; pooled Brier not worse, sums at unity, 5% transition rate, the
25% single-race sign-off list). Criteria this document assigns to Sage
(stability trend, the transition matrix, the largest-delta races) are printed
as REVIEW with the numbers and never auto-passed. `--emit-evidence` writes
`flip_review_<flag>_<date>.json` to the store; `gate_status.py` gate 3 requires
that record's PASS, the 5-day count and the flag itself. No threshold, day
count or review bar changed.

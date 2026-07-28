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
   quantitative bound: [SAGE-APPROVAL: maximum acceptable confidence-tier
   transition rate over the shadow window; recommended ≤ 5% of shadow runners
   transitioning in aggregate, with any single race above 25% requiring explicit
   sign-off].

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
4. **Top-3 flip rate ≤ [SAGE-APPROVAL: maximum acceptable share of shadow races
   whose would-be top-3 differs from the legacy top-3; recommended 15%].**

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

# 08 — Consensus integrity: deterministic score, real provenance, ROI-graded tipsters, de-correlation

**Wave:** 3 · **Depends on:** [07](07-crowd-gate-gate-only.md) (gate contract first) · **Blocks:** none (feeds [12](12-retrain-rebaseline.md)-era blend validation) · **Risk:** medium (changes a live input signal — A/B via ledger) · **Type:** signal quality

## Goal

The crowd score the gate consumes becomes a deterministic, auditable number computed
in code from provenance-grade mentions, with tipsters graded on ROI/CLV rather than
strike rate, and the market's contribution residualised out before blending.

## Why (evidence)

- `crowd_score` — the number the live gate consumes — **is computed by the LLM, not
  code** ("crowd_score = (total_mentions / total_sources_checked) x 100" in the
  extraction prompt, `consensus_agent.py:809`), with a denominator the model chooses.
  Non-auditable, unstable, gameable.
- Independence is fabricated: `is_ind = i < n_independent` (`consensus_agent.py:1473`);
  `source_url` is always `None` (:1483); `MIN_INDEPENDENT_SOURCES_PER_RACE = 3` is
  defined and never enforced (:76). Syndicated tips (same tip republished) count as
  multiple "independent" mentions.
- Graded on strike, not ROI: `accuracy_multiplier` uses `was_winner`
  (`consensus_agent.py:106-114`, `:130-138`). A favourite-tipper has high strike and
  negative ROI — the feedback loop actively up-weights price-insensitive tippers.
  Multipliers are global per tipster (:117-151); `tracks: ["all"]` everywhere
  (`tipster_panel.example.json`); no per-track/distance skill.
- No panel lifecycle: multipliers bounded [0.75,1.25] precisely so "no tipster can
  be silenced" (`docs/08:134-137`); no demotion/rotation protocol; inclusion is editorial.
- Market leakage into the pillar: odds in prompts (:412-416, :647-651), "Betfair
  market movers" Perplexity queries (~:603-610), `market_reader` bucket weighted 1.4
  (:86). Never residualised anywhere.
- Minor: `weighted_sum` argument accepted and ignored (:1038); zero-mention horses
  get neutral 35.0 which still flows through injections.

## Scope

**In:** code-computed crowd score; source_url provenance + syndication dedup;
tipster grading on tip-price→SP CLV; per-(tipster×track×distance-band) skill;
odds-free prompts (A/B measured); residualisation of crowd vs market before the gate.
**Out:** replacing the LLM extraction pipeline itself; 50/30/20 weight re-fit
(→ [12](12-retrain-rebaseline.md) era).

## Steps for Kimi Code

1. **Deterministic score.** Compute `crowd_score` in code:
   `100 × Σ(weight_i × relevance_i) / Σ(weight_i)` over extracted mentions, where
   weights come from the accuracy system and relevance from the LLM's structured
   fields. The LLM still extracts mentions; it no longer does arithmetic. Store
   numerator/denominator per race for audit.
2. **Provenance & dedup.** Require `source_url` from extraction (drop mentions
   without one); normalise (strip tracking params); dedup near-identical tip text
   across domains (syndication) — one vote per unique (tipster, horse) pair.
   Enforce `MIN_INDEPENDENT_SOURCES_PER_RACE` or mark the race's crowd signal
   `insufficient` (gate treats as neutral, and it is *logged* as insufficient).
3. **Tipster CLV grading.** Extend the accuracy loop (`consensus_agent.py:100-161`):
   record tip-time odds with each settled tip; grade on mean CLV (tip odds vs SP)
   and net ROI with a ≥20-tip floor and shrinkage (existing pattern), not raw
   strike. Split multipliers per (tipster × track × distance-band) with fallback to
   the global multiplier below the floor.
4. **Panel lifecycle.** Document + implement: demotion to weight floor after
   60 settled tips with net ROI < −10%; onboarding probation (fixed 1.0 weight until
   20 tips); a quarterly review report. Update `tipster_panel.example.json` comments.
5. **De-correlation (A/B).** Variant B: remove odds from all research prompts and
   drop "market movers" queries; and/or regress race-level crowd score on de-vigged
   market prob and feed only the residual to the gate. Run A (current) vs B as
   shadow gating decisions through the ledger ([01](01-ledger-clv-net-settlement.md))
   for ≥4 weeks; promote B if blocked/vetoed-bet outcomes improve or are neutral
   (B is cheaper and structurally cleaner — ties go to B).

## Acceptance criteria

- [ ] Crowd score reproducible from stored components (recompute one race day from
      DB rows → identical score, no LLM call).
- [ ] Mentions table shows ≥95% non-null `source_url`; syndication dedup demonstrably
      collapses a planted duplicate.
- [ ] Accuracy multipliers reflect CLV grading; a synthetic favourite-only tipster's
      multiplier decreases over 20 settled losing-CLV tips (unit test).
- [ ] A/B shadow report attached; promotion decision recorded.

## Rollout & flags

- Flags: `STRIDE_CROWD_SCORE_V2=true` (deterministic score), `STRIDE_CONSENSUS_NO_ODDS`
  (variant B). Both default off → A/B → promote per step 5.
- Rollback: flags off restores LLM-computed score path (kept one release).

## Guardrails

- Do not let the LLM compute any number that reaches the gate.
- Do not grade tipsters on strike alone ever again; but do not demote on <20 tips
  either (small-sample whiplash).
- Keep the 0.75–1.25 clamp until lifecycle rules (step 4) are live; then the clamp
  applies within probation only.

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §3 (B3, B5)
- Prerequisite: [07](07-crowd-gate-gate-only.md) · Measurement: [01](01-ledger-clv-net-settlement.md)
- Later: blend-weight validation in the [12](12-retrain-rebaseline.md) era.

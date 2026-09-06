# Scoring, Selection & the Output Contract

This is the heart of the system: how a runner's Monte-Carlo and ML probabilities
become a calibrated win percentage, a selection score, a BET/NO_BET decision, and
ultimately the `tips_<date>.json` document and `selections` rows the product
displays. Everything here lives in `server/python/run_tips_pipeline.py`.

Related docs: [Daily pipeline](02-daily-pipeline.md) ·
[Monte Carlo](06-monte-carlo-engine.md) · [Consensus & market](08-consensus-and-market.md)

---

## 1. Calibration & blending (`calibrate_and_score`, run_tips_pipeline.py:555)

Per runner, in order:

1. **Isotonic correction.** If `models/isotonic_calibrator.pkl` exists, the raw MC
   win percentage is passed through `ProbabilityCalibrator` (bounds [0.01, 0.95]).
2. **ML blend.** If the ML ensemble scored the runner
   (`mlPredictedProb`), blend it in: ML weight **20% for favourites (odds ≤ 3),
   40% otherwise**. The result is `rawModelProb` — the model's own opinion.
3. **Market anchor.** Compute the overround-corrected market probability
   (`true_market = implied / overround`), then blend model and market with a
   model weight (`mw`) that *rises* at short prices (a Kelly audit found $1–3
   horses win 41% while the blended model said 17% — the market is sharpest
   there, but the model must be allowed to disagree):

   | Odds | ≤3 | ≤6 | ≤10 | ≤15 | ≤30 | >30 |
   |---|---|---|---|---|---|---|
   | model weight | 0.80 | 0.70 | 0.50 | 0.45 | 0.40 | 0.30 |

   `winPercentage = mw × raw + (1−mw) × true_market`, and
   **`modelEdge = winPercentage − true_market`** (edge is computed from the
   *calibrated* probability, so a flat MC run can't fabricate edge).

   *Opt-in conditional-logit calibration:* with `STRIDE_CL_BLEND=true` and a
   fitted `models/conditional_logit.json`, a race-conditional softmax over
   `α·ln(model) + β·ln(market)` (`conditional_logit.py`) is applied **on
   entry to `calibrate_and_score`, to the incoming MC probability** — the
   same stage the artifact was fitted on (`prediction_audit` values) — and
   it replaces step 1's isotonic correction when active. Steps 2–3 (ML blend
   and this market anchor) then run unchanged, so prices stay
   market-tethered. Artifacts carry a stage tag; a mismatched artifact is
   refused. Default behaviour without the flag/model is byte-identical.
   See [Hit-rate research](12-hit-rate-research.md).
4. **Context multipliers.** Documented intent: fitness readiness and track-bias
   points each map to ×0.95–1.05; jockey momentum ×0.85–1.20. **As shipped
   (default flags) none of the three does that** — verified 2026-09-06, audit
   H3/H4, first recorded in `docs/analysis/SYSTEM_MAP.md §7b`: the fitness read
   looks for a top-level `fitnessReadinessScore` that mc_api only publishes
   nested under `fitnessData` (0–1 scale) ⇒ ×1.00 always; `trackBiasPoints` is
   a −18…+49 points total fed into a `/100` map built for 0–100 ⇒ every scored
   runner lands in ×0.95–1.00, a uniform shrink with a 0.5% tilt; the jockey
   read names an mc_api *feature* that never reached the result ⇒ ×1.00 always.
   `_context_multipliers` repairs each one behind its own default-off flag —
   `STRIDE_CTX_MULT_FITNESS`, `STRIDE_CTX_MULT_BIAS`, `STRIDE_CTX_MULT_JOCKEY`
   — so each effect is attributable in a paired A/B, and
   `STRIDE_CTX_MULT_DIAG=true` prints the realised min/mean/max per race. Flags
   off is today's exact arithmetic, not "no multiplier": every downstream
   raw-probability threshold was tuned against the ~5% shrink.
5. **Selection score.** Two independent signals — the probability estimate and the
   market disagreement:
   `prob_score = 0.70 × adjusted_calib + 0.30 × clamp(edge, ±10)`
   (0.80/0.20 when the MC output is flat). An earlier formula triple-counted the
   raw probability; the current one is documented in-code as "audit fix #2".
   Odds-band multipliers were removed from scoring entirely ("audit fix #1") —
   price is handled at staking, not ranking.
6. **Intelligence adjustment.** Multiplier 0.80–1.30 and bonus −3…+4 from franking,
   prep cycles, barrier edges, sectional trends, trainer synergy
   (see [Intelligence layer §4](07-intelligence-layer.md)).
7. **Sectional bonus.** If the sectional MC lifted this runner > 1.5 pts over the
   blended base, add 65% of the delta (soft-capped at 6).
8. **Low-probability squash.** Calibrated < 5% → score ×0.30; < 8% → ×0.55.
9. **MC-spine blend.** The wrapper's score is blended with the MC engine's own
   normalized selection score 50/50 (65/35 when MC is flat) so the wrapper refines
   rather than replaces the engine's ranking.

**Flat-MC handling.** If the spread of raw model probabilities across the field is
< 6 percentage points, the MC is treated as uninformative: scores take a gradient
penalty by confidence gap (top-two gap < 1.5 pts → ×0.30, < 3 → ×0.60, < 5 → ×0.85),
all tips are forced to LOW confidence, and the LLM's ranked pick can be boosted to
the top with an explicit `_llm_top_pick` marker.

---

## 2. LLM post-scoring

The top 6 by score go to the LLM post-scorer (`llm_post_scorer.score_race_horses`):
each gets an `ai_score` (0–100) blended into the selection score at 30%
(`0.70 × score + 0.30 × ai_norm`). The LLM also returns an explicit ranking,
tip type (`win` / `trifecta` / `no_bet`) and reasoning, with a deterministic
contract-enforcement layer (positive-edge check forces `no_bet`; trifecta only in
genuinely open races). Tipped horses later get a long-form `ai_insight`; every
non-tipped runner gets a 2–3 sentence `brief_assessment`.

---

## 3. Safety filters → top 3 (`apply_safety_filters`, :740)

Runners are re-ranked with a **conviction bonus** (edge ≥ 3 & raw ≥ 15 → +3.0;
edge ≥ 2 & raw ≥ 12 → +2.0; edge ≥ 1 & raw ≥ 10 → +1.0), then filtered:

- **Class-aware odds caps:** Group races max $25, Listed/other max $30 — with a
  merit override for a dominant model leader (score gap ≥ 1.5) or edge/raw merit.
- **Favourite discipline:** the market favourite passes only with positive model
  edge — "do not tip the market favourite simply because it is the shortest price."
- **Banker override:** `banker_score ≥ 70` bypasses everything.
- **Distance-range filter:** blocked if the horse has never won within ±200 m of
  today's trip.
- **Longshot filters:** $15+ needs edge > 2 and raw ≥ 8; $30+ with no edge blocked;
  black-type races block $10+ runners with calib < 10 and no edge.

Survivors (top 3) get **confidence** and **staking**:

```
odds > $30                      → low
EV > 0 and edge > 1.0           → high     (EV = calib/true_market − 1)
edge > 0                        → medium
else                            → low
pace_clarity < 0.35 caps high → medium
high → 2u, medium → 1u, low → 0u
```

This EV-first confidence ladder replaced a v1 heuristic that was *anti-correlated*
with value (documented in-code: v1 "high" had mean EV +0.036 vs "low" +0.152,
because raw-probability gates favoured short-priced horses).

---

## 4. DB enrichment

`enrich_with_db` (:929) decorates the top 3 from PostgreSQL: prior sectional
z-scores (`z_200m > 1.0` → "Strong last 200m"), franking ELO/score/anti-frank flags,
course-and-distance record, course-specialist rate, last 5 runs, head-to-head vs
today's field, and assembles the human-readable `key_factors` list. Collateral
advantage (ELO vs field average) is computed here against the actual field.

---

## 5. The bet/coverage contract

The output distinguishes **what the model likes** from **what deserves money** —
every race resolves to an explicit `bet_status` of `BET` or `NO_BET`:

- **`raw_model_leader`** — highest pre-filter selection score, always reported.
- **`bet_pick`** — the raw model leader *only if* it survives
  `evaluate_bet_candidate` (:1632), the validated sweet-spot gate:
  - no real market quote → NO BET; edge ≤ 0 → NO BET;
  - **odds > $15 → NO BET** (coverage only);
  - odds < $3 → needs edge ≥ 4 and prob ≥ 30;
  - $3–5 → edge ≥ 2.5 and prob ≥ 15;
  - $5–15 → edge ≥ 3 and prob ≥ 10; low-confidence > $12 stays guide-only;
  - **intelligence override:** rank-1 with intel bonus ≥ 3.0, franking ≥ 55 and a
    non-declining prep promotes to BET regardless of the edge gate.
  No hidden substitutes: if the leader fails, the race is an explicit NO_BET with a
  human-readable reason.
- **`coverage_pick`** — the display/guide horse, chosen through a cascade of
  fallbacks (bettable > probability-first ($≤20, prob ≥ 8) > exception candidates >
  positive-edge > any real market quote), labelled honestly via
  `selection_origin ∈ {model_backed, tip_only, filtered_substitute,
  market_unavailable, raw_model_leader, crowd_gated, crowd_promoted}`.

Then the **crowd-first convergence gate** (see
[Consensus & market §6](08-consensus-and-market.md)) can flip BET→NO_BET
(`crowd_gated`) or NO_BET→BET (`crowd_promoted`), and attaches
`crowd_score`, `crowd_classification`, `stake_recommendation`
(FULL/STANDARD/REDUCED/NONE) to every pick.

`validate_tips.py` asserts the contract invariants on the saved file (every race
exactly one of BET/NO_BET; `selection_contract` counts match; picks carry
`should_bet`), and `backfill_tips_contract.py` re-stamps old files when the contract
evolves — importing the live functions so logic can't drift. Validation runs
automatically after every save (loud, non-fatal) and again as
`run_full_pipeline`'s final step; the CLI remains for ad-hoc checks.

---

## 6. Output documents

### `racecards/tips_<date>.json` (written atomically; per-track runs merge into the canonical file with a timestamped backup, then every day-level block below is rebuilt over the merged race list)

```jsonc
{
  "date": "...", "generated_at": "...",
  "races": [ /* per race — see below */ ],
  "best_bets":  [ /* top 3 high-confidence rank-1 picks by score */ ],
  "value_plays":[ /* edge > 3%, odds $4–$15, rank 1 — top 5 by edge */ ],
  "bankers":    [ /* high confidence at odds ≤ $4 */ ],
  "summary": { "total_races", "total_selections", "positive_edge",
               "high_confidence", "total_units",
               "mc_time_seconds", "db_time_seconds", "llm_time_seconds" },
  "selection_contract": { "version": "v2-explicit-bet-coverage",
                          "bet_races", "no_bet_races" },
  "convergence_summary": { "confirmed", "crowd_only", "model_only", "rejected",
                           "crowd_overrides_tracked", "gated_no_bet",
                           "pillars_available", "stake_distribution" }
}
```

### Per race (see `examples/sample_race.json` for a real instance)

`track`, `race_number`, `race_name`, `distance`, `going`, `race_class`,
`field_size`, `predictability` (race-level selectivity block — score,
category `highly_predictable/normal/chaotic`, confidence modifier 0.5–1.2,
key factors; computed from pre-race market/card facts only),
`top_picks[]` (ranked pick objects), `raw_model_leader`, `bet_pick`
(or null), `coverage_pick`, `primary_pick`, `bet_status`, `bet_status_reason`, and
`full_field[]` — every runner with win/place %, raw model %, edge, selection score,
form flags, `is_tipped`/`tip_rank`, and either `ai_insight` (tipped) or
`brief_assessment` (not tipped).

Best-bets ordering: rank-1 picks only (a best bet is always its race's top
pick), by selection score (historical behaviour); with
`STRIDE_PREDICTABILITY_GATE=true` the score is weighted by the race's
predictability modifier — ordering only, never BET/NO_BET decisions. All
day-level blocks — best bets, value plays, bankers, summary, selection
contract, convergence summary — are built by `tips_day_aggregates.py`,
shared by the full-day run and the per-track merge so the two publish
paths cannot drift (the merge previously rebuilt only summary/best-bets,
which shipped stale value plays and contract counts after a re-run).

### Pick object (the load-bearing fields)

Identity & market: `horse`, `barrier`, `jockey`, `trainer`, `odds`,
`has_real_market_odds`, `fair_odds`. Model: `win_pct` (calibrated),
`raw_model_pct`, `edge_pct`, `selection_score`, `confidence`, `staking`,
`key_factors[]`. Contract: `selection_origin`, `selection_origin_reason`,
`should_bet`, `matches_model_leader`, `model_leader_horse`. Convergence:
`crowd_score`, `crowd_classification`, `convergence_tier`, `stake_recommendation`.
LLM: `ai_score`, `ai_insight`. Excuses: `luckless_flag/score/uplift/explanation`.

### Database (`store_selections_in_db`, :1156)

Only picks with `should_bet=true` (bet pick, or primary pick as fallback) are
inserted into **`selections`** — a ~107-column row capturing the full decision
record: probabilities, edge, Kelly stake, franking + graph-franking fields, fitness,
recalibration deltas, sectional MC fields, track-bias breakdown, pace scenario JSON,
AI reasoning JSON, luckless JSON, and all consensus/convergence fields. Previous
selections for the same date+track are soft-deactivated (`is_active=false`) first;
if that deactivation fails, the store aborts loudly rather than inserting new
rows next to still-active stale picks.
Every runner's convergence row also lands in `convergence_output` for shadow-P&L
evaluation, and every runner's published final win % is recorded into
`prediction_audit.final_win_prob` (`store_final_probs_in_audit`) — the
input for future final-stage calibration fits.

---

## 7. Failure behaviour

Each race is wrapped in a try/except: a failing race emits an `error` entry with
`bet_status: "ERROR"` and the pipeline continues (:2712-2725). Failed races are
listed in a warning summary. DB and LLM failures inside a race are individually
non-fatal — the pipeline degrades to whatever signals are available.

## Crowd gating is asymmetric (task 07, 2026-08-02)

Scoring output feeds an asymmetric gate: crowd input can only hold a
selection back (veto, downgrade) or agree with it, never create it. See
docs/08-consensus-and-market.md for the contract and the shadow
measurement of blocked promotions.

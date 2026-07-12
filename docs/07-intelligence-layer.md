# The Intelligence Layer

"Intelligence" in STRIDE means precomputed, race-day-independent knowledge — barrier
edges, form franking, prep cycles, trainer patterns — built ahead of time and loaded
by the tips pipeline as JSON files plus one DB table. This is the layer that turns a
generic probability model into one that knows *this* track, *this* barrier, *this*
horse's campaign shape.

Related docs: [Daily pipeline](02-daily-pipeline.md) ·
[Scoring & output](09-scoring-and-output.md)

---

## 1. Three generations — what actually feeds production

The layer contains three code lineages computing overlapping concepts. Only two feed
the live pipeline:

| Gen | Code | Output | Live? |
|---|---|---|---|
| 1 — Deep engines | `form_franking.py`, `franking_graph.py` | DB table `franking_scores` + in-memory graph | **Yes** (via table + `get_franking_graph()`) |
| 2 — STRIDE agents | `stride_agent_track.py`, `stride_agent_form.py`, orchestrated by `stride_build.py` | flat JSONs in `server/python/intelligence/` | **Yes** — read by `run_tips_pipeline.load_intelligence_files()` |
| 3 — `intelligence/` package | `intelligence/build_*.py` | date-suffixed JSONs under `intelligence/output/<date>/` | **No** — parallel rewrite, not wired in |

Two similarly-named utility modules mark the boundary: `intelligence_common.py`
(gen 2) vs `intelligence/common.py` (gen 3).

---

## 2. Form franking — the flagship concept

**Form franking** validates a horse's past form by watching what the horses it beat
(or lost to) did **next**. If your beaten rivals go on to win, your form is "franked"
(endorsed); if they flop, it's hollow ("anti-franked"). Class matters: a form line
through a Group 1 is worth roughly twice one through a BM72.

### 2a. Statistical/ELO engine (`form_franking.py`, 1,542 lines)

- **Global ELO**: iterative pairwise ELO over all races — 20 iterations, K=32 scaled
  by class and 90-day recency decay, initial rating 1500 — disk-cached and keyed on
  a staleness hash of the results table.
- **Direct franking**: over a horse's last 10 runs, each opponent's subsequent
  finishes within 90 days score points (win = 12 × class factor, 2nd = 8, 3rd = 5,
  top-5 = 2, beaten ≥ 10L = −4), weighted by margin-closeness, 60-day recency decay,
  and the reference race's class weight (Group 1 = 2.0 … Maiden = 0.7).
- **Recursive franking**: a second-order pass (composite = 0.7 direct +
  0.3 recursive).
- **Anti-franking**: if > 60% of beaten rivals subsequently run poorly, the composite
  is knocked ×0.75.
- Confidence = data volume × consistency. Raw score normalized to 0–100
  (`(raw/40) × 100 + 50`).
- Batch path `compute_all_franking_scores()` upserts everything into the
  **`franking_scores`** table (ELO, franking score/confidence, anti-franked flag,
  field-strength average, form-quality trend). Note: `collateral_advantage` is
  written as 0.0 in batch and computed at read time against the actual field
  (`get_franking_for_race`; also recomputed in
  `run_tips_pipeline.enrich_with_db:1127-1135`).

### 2b. Graph engine (`franking_graph.py`, 1,200 lines)

Treats results as a network (NetworkX `MultiDiGraph`):

- **Nodes**: `(horse, race)` performances. **Edges**: `beaten` (directed,
  weight = margin), `shared_race`, `identity` (same horse over time, 60-day
  half-life), `jt_transfer` (jockey/trainer transfer). In practice only the
  aggregated `beaten` edges feed the outputs.
- **Deep franking**: BFS back through beaten-by chains to depth 5 with generational
  decay `[1.0, 0.45, 0.20, 0.08, 0.03]`; path quality scores (class-step bonus
  1.15^step, odds-consistency bonus, temporal-coherence penalty); the top-50 path
  strengths combine by **noisy-OR** `1 − ∏(1 − sᵢ)`.
- **PageRank** (damping 0.85) → `pagerank_authority`; **Louvain community
  detection** (`python-louvain`, seed 42) → `community_strength`; **betweenness
  centrality** → `bridge_score`; plus form stability (1/(1+2σ)), market validation
  (did the market shorten this horse subsequently), and an `ExcuseDetector`
  (wide barrier +0.25, long spell +0.30, weight jump +0.20, going mismatch +0.20,
  market drift +0.15, capped 0.85) that discounts anti-franking evidence.
- Known dead weight: `trainer` is hardcoded to `""` when building nodes, so
  trainer-transfer edges never fire.

A third, unrelated "franking" exists in `mc_api.calculate_sectional_franking_value`
(sectional-based, env-gated), and a fourth simple heuristic in the gen-3
`intelligence/build_form_franking.py` — worth knowing when you grep for "franking".

---

## 3. The nightly build (`stride_build.py`)

`python stride_build.py <date>` runs two deterministic agents (SQL + pandas — the
"Codex Agent" names are branding, not LLMs), in parallel by default, then verifies
all eight required files exist and writes MD5s to `intelligence/build_log.txt`:

**Agent 1 — track/market (`stride_agent_track.py`)** →
- `barrier_map.json`: per track × distance-band × going × barrier — win %,
  edge-vs-random, confidence (HIGH ≥ 50 runs / MED ≥ 25 / LOW), field-size
  modifiers, rail adjustments from `track_day_bias`.
- `flemington_straight.json`: the straight-course specialist file (≤ 1200 m),
  barrier edges + running-style edges per distance/going.
- `class_distance_patterns.json`: class-movement patterns from `training_data`.
- `market_overlays.json`: per track × going × price bracket (short ≤ $3 / mid ≤ $8 /
  long ≤ $20 / outsider), `market_edge = actual_win_rate − implied_prob`, 450-day
  lookback (via `market_overlay_common.py`).

**Agent 2 — form/horse (`stride_agent_form.py`)** →
- `form_franking.json`: reads `franking_scores` + graph profiles, classifies each
  runner with **dynamic thresholds** computed from the day's batch (deep = 95th
  percentile, franked = 75th, partial = 25th) into
  `DEEP_FRANKED (weight 1.2) / FRANKED (1.1) / PARTIAL (1.0) / PENDING (0.8) /
  UNFRANKED (0.6–0.7)`; merges pagerank/community/stability/bridge metrics.
- `prep_cycles.json`: campaign analysis via `fitness_peak` (batch-prefetched) — run
  number, historical peak run, trajectory, quick-backup, campaign tempo, weight
  penalty.
- `sectional_trends.json`: z600 slope > 0.05 = improving; flags
  `fastest_closer_while_unplaced` and `pb_while_unplaced` (hidden-improvement
  signals), 180-day window.
- `trainer_patterns.json`: 12-month trainer win/place %, first-up %, jockey synergy
  (≥ 3 rides together).

The tips pipeline loads these eight files plus `track_distance_profiles.json` (from
`track_profiler.py`) at startup (`load_intelligence_files`,
`run_tips_pipeline.py:104`) and warns if `barrier_map`, `form_franking` or
`prep_cycles` are empty. All intelligence JSONs are git-ignored (generated data).

---

## 4. How intelligence changes scores

`enrich_horse_with_intelligence` (`run_tips_pipeline.py:183`) attaches an
`_intelligence` dict per horse; `calculate_intelligence_adjustment` (`:461`) converts
it into a multiplier (0.80–1.30) and a bonus (−3.0…+4.0) applied to the selection
score:

- Franking ≥ 60 with confidence ≥ 0.3 → +2.0 base, scaled by reference-class weight,
  +0.5 if a Group race is in the franked chain (cap +3.5); anti-franked −0.5;
  PageRank ≥ 0.8 → +0.5; deep+independent graph paths → +0.5; form stability ≥ 0.8
  → +0.3; market-validated → +0.3.
- Prep cycle: at historical peak run +1.5; improving trajectory +1.0; second-up
  +0.5; quick backup +0.5; FRAGILE campaign tempo ×0.90.
- Barrier edge (HIGH/MED confidence, edge > 3) → up to +1.5; bad barrier → down to
  −1.0.
- Sectional trends: fastest-closer-unplaced or PB-while-unplaced +1.0;
  improving +0.5.
- Trainer-jockey synergy edge > 5% → +0.5.

Strong intelligence can also **override the bet gate**: a rank-1 pick with
`intel_bonus ≥ 3.0`, franking ≥ 55 and a non-declining trajectory is promoted to BET
regardless of the edge calculation (`_check_intelligence_override`,
`run_tips_pipeline.py:1581`). See [Scoring & output](09-scoring-and-output.md).

---

## 5. Standalone analysts

- **`banker_detector.py`** — finds dominant favourites worth backing regardless of
  edge. Composite 0–100: MC dominance 0.25 + danger profile 0.20 + ELO gap 0.15 +
  ML consensus 0.12 + form trajectory 0.10 + pace security 0.10 + conditions 0.08,
  plus graph bonuses. Qualification: score ≥ 55 (adaptively shifted ±8 by race tier,
  field size, competitiveness; clamped [40, 80]), MC win ≥ 0.30, odds ≤ $3.50,
  field ≥ 5, not a maiden. Tiers: `strong_banker` (2.0u win / 1.0u exacta) and
  `banker` (1.5u / 0.5u). A banker with score ≥ 70 bypasses all tip filters
  (`run_tips_pipeline.py:825`).
- **`luckless_analyser.py`** — excuse detection from stewards'/analyst comments:
  ~90 keyword rules across traffic/map/start categories with severities → a 0–100
  forgive score (`is_luckless` = score ≥ 60 with no negative flags like lame/bled);
  today's conditions must also have improved (barrier improvement, field shrink,
  wide→inside). Uplift = forgive × improvement / 10000, capped 0.08–0.15, folded
  into the LLM mu adjustment (overall cap 0.12). Optionally blends a Groq LLM
  reading for borderline cases (forgive 40–65, 60/40 keyword/LLM).
- **`track_bias_points.py`** — static per-track configuration (~16 tracks: straight
  lengths, per-barrier win rates, pace bias, named top jockeys/trainers) scored as
  points: barrier +15/+8/0/−10, pace +12/+6/0/−8, jockey +12/+6, trainer +10/+5.
  Consumed by mc_api as `trackBiasPoints` (≥ 25 = strong fit).
- **`blackbook_candidates.py`** — finds horses that ran 2nd–4th with a top-2 closing
  200 m split and were *not* tipped; scans upcoming racecards for their next runs.
- **`advanced_race_analysis.py`** — a genuine LLM agent (Groq, 4-phase full-field
  analysis, temperature 0.4) with hard constraint enforcement (one winner, ≤ 3
  places, probabilities renormalized). Standalone; not wired into the daily flow.
- **`historical_analysis.py`** — one-off print-only reporting script (hardcoded
  dates/tracks).

---

## 6. Quirks

- Three (arguably four) independent "form franking" implementations exist — the
  ELO engine, the graph engine, the gen-3 heuristic builder, and mc_api's sectional
  franking. The gen-2 agent is the reconciler that production reads.
- The gen-3 `intelligence/build_*.py` package has no orchestrator and no live
  consumers; treat as an in-progress rewrite.
- `build_trainer_patterns.py` (gen-3) is a stub (`historical_pattern_available:
  false` always) because trainer names aren't in `race_results_history`.
- `stride_agent_track.build_class_distance_patterns` used to assign a track name
  to an unused variable called `date_str` — the dead line has been removed.
- Date windows are all hardcoded: ELO half-life 90d, franking decay 60d, franking
  lookback 540d, prep lookback 720d, overlays 450d, sectional trends 180d.

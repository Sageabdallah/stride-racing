# Consensus & Market Signals (Pillars 2 and 3)

STRIDE never bets on the model alone. Every selection is cross-examined against two
independent signal sources — a **tipster consensus** ("what does the informed crowd
think?") and a **market signal** ("what is the money doing?"). This document explains
how each pillar is produced, how they are combined with the model, and which version
of the convergence logic actually runs in production.

Related docs: [Architecture](01-architecture.md) · [Daily pipeline](02-daily-pipeline.md) ·
[Scoring & output contract](09-scoring-and-output.md)

---

## 1. The three pillars at a glance

| Pillar | Weight (design) | Producer | Artifact |
|---|---|---|---|
| STRIDE model | 50% (`STRIDE_MODEL_WEIGHT`) | MC + ML ensemble (`mc_api.py`, `ml_model.py`) | `selectionScore` per runner (0–25 scale, P50 ≈ 7, P90 ≈ 12–15) |
| Consensus | 30% (`CONSENSUS_WEIGHT`) | `consensus_agent.py` | `intelligence/consensus_<date>.json` + DB tables |
| Market signal | 20% (`MARKET_SIGNAL_WEIGHT`) | `odds_movement.py` | `intelligence/market_signals_<date>.json` + DB tables |

`consensus_blender.py` is the arbitration library that joins them. It is imported by
`run_tips_pipeline.py` (`server/python/run_tips_pipeline.py:2012`).

> **Version note (important).** The codebase contains *two* generations of convergence
> logic. The **V2 "three-pillar" API** (weighted blend → LOCK/CONFIRM/FLAG tiers →
> injections → EV gate) is fully implemented in `consensus_blender.py` but is only
> exercised by the offline report mode (`consensus_blender.py --report`). The **live
> pipeline runs the V3 "crowd-first" path** (`confirm_with_model` +
> `crowd_bet_decision`), with market signal hard-coded to neutral 50 and injections set
> to 0 inside `run_tips_pipeline.py` (lines 2554–2673). Read §5 for the design model
> and §6 for what actually gates bets today.

---

## 2. Pillar 2 — the tipster consensus (`consensus_agent.py`, 1,570 lines)

### 2.1 The panel

The panel is defined in `server/python/tipster_panel.json` (git-ignored; the committed
template is `tipster_panel.example.json`). Structure:

- Top level: `panel_version` ("2.0"), `target_size` (50), `sources[]`, `bucket_definitions{}`.
- Each source: `id`, `name`, `base_url`, `tip_page_url`, `type` (its *bucket*),
  `tracks`, `state`, `fetch_method`, `active`, `verified`, `historical_accuracy`, `notes`.
- Only sources with `active AND verified` are polled.
- A `proofed_results: true` source gets a 1.15× quality multiplier
  (`consensus_agent.py:256`).

Buckets weight *what kind* of tipster is talking (`BUCKET_WEIGHTS`,
`consensus_agent.py:78-87`):

| Bucket | Weight | | Bucket | Weight |
|---|---|---|---|---|
| `stable_watcher` | 1.5 | | `speed_analyst` | 1.3 |
| `value_analyst` | 1.4 | | `track_specialist` | 1.2 |
| `market_reader` | 1.4 | | `form_analyst` | 1.0 |
| `ratings_analyst` | 1.3 | | `retail_punter` | 0.7 |

Note: the weights in the panel JSON's `bucket_definitions` are placeholders — the
hard-coded `BUCKET_WEIGHTS` dict is authoritative.

### 2.2 How sources are polled

Despite `fetch_method: "direct"` in the template, **all fetching goes through APIs,
not scraping**:

1. **Panel pages → Tavily Extract.** `fetch_panel_pages()` (`consensus_agent.py:187`)
   calls Tavily's extract API per active source (0.5 s pacing, daily cap
   `DAILY_TAVILY_CAP = 50`).
2. **Quality gate.** `is_content_usable()` (`:115`) rejects bookmaker domains
   (16-entry blocklist), sponsorship keywords, pages under 200 chars, and pages that
   mention none of the race's runners. `punters.com.au` / `racenet.com.au` were
   deliberately removed from the blocklist as editorially independent.
3. **Structured extraction → Claude.** Usable pages are batched (5 per call) into
   Anthropic `claude-sonnet-4-20250514`, which returns per-horse pick JSON
   (`extract_picks_from_content_batch`, `:871`; daily cap `DAILY_CLAUDE_CAP = 200`).
4. **Web research → Perplexity + Claude.** `claude_research_race()` (`:667`) runs
   **four** Perplexity `sonar-pro` queries per race — one each for the *newspaper*,
   *portal*, *data* and *social* ecosystems (`build_perplexity_queries`, `:419`) —
   then one Claude call converts the merged text into structured picks, including a
   `crowd_score = mentions / sources_checked × 100`.
5. Horse-name matching back to the field uses exact normalized match, then
   `SequenceMatcher` fuzzy ≥ 0.85, rejecting ambiguous matches within 0.02
   (`match_horse_to_field`, `:143`).

API budgets are tracked per-day in `intelligence/.consensus_api_usage_<date>.json`.

### 2.3 From mentions to scores

Every extracted pick becomes a *mention* carrying: confidence language
(WIN → HIGH, EACH_WAY → MEDIUM, ROUGHIE → LOW), bucket, panel-vs-web origin,
independence flag, and quality multiplier (panel proofed = 1.15, independent web
= 1.0, commercial web = 0.8).

Two different numbers are then computed per horse:

- **`consensus_score` (0–100)** — the weighted panel formula
  (`calculate_consensus_score`, `:955`):

  ```
  vote_pct  = mentions / tipsters_polled × 100
  vote_score = piecewise(vote_pct)          # 60%+ → 55 pts; scaled bands below
  diversity_bonus  = {0:0, 1:5, 2:12, 3:20, 4:23, 5:25}[buckets_hit]
  confidence_bonus = min(2 × high_conf_mentions, 10)
  independence_mult = 1.1 (≥90% indep) | 1.0 (≥70%) | 0.85 (≥50%) | 0.65 (else)
  consensus_score = min((vote_score + diversity + confidence) × mult, 100)
  ```

  Zero-mention horses default to a neutral **35.0** — the same floor the blender uses
  when no consensus file exists. Vote *percentage* deliberately dominates: 3 tips
  from 5 sources (60%) beats 3 from 20 (15%).

- **`crowd_score` (0–100)** — the simple mention rate from the Perplexity/Claude web
  research. This is the **primary output of V3** and what the live gate consumes.

Reasoning signals extracted from tipster text are cross-checked against STRIDE's own
intelligence files → `reasoning_alignment ∈ {CONFIRMED, CONTRADICTED, UNVERIFIABLE}`
(`check_reasoning_alignment`, `:1002`).

Outputs: `intelligence/consensus_<date>.json` (keyed `"<track>_R<n>"`), plus DB rows in
`consensus_mentions`, `consensus_scores`, `tipster_panel_log`.

### 2.4 Source accuracy tracking — feedback loop (opt-in)

`source_accuracy_tracker.py` joins each day's mentions against `race_results_history`
and stores per-tip hit/miss rows in `source_accuracy`. With
**`STRIDE_ACCURACY_WEIGHTS=true`**, the agent now reads that table back:
`load_accuracy_multipliers()` computes a per-tipster multiplier from the last
120 days of **settled** tips (leak-free — strictly past race days only) and
composes it into each panel page's `quality_multiplier`, which flows into the
weighted consensus sum and the stored mentions.

The multiplier is deliberately conservative: sources with fewer than 20
settled tips stay neutral (1.0); the hit-rate ratio vs the panel-wide
baseline is shrunk with a 20-pseudo-tip prior; and the result is hard-bounded
to **[0.75, 1.25]** so no tipster can be silenced or dominate off a streak.
Default off: multipliers are `{}` and scoring is byte-identical to before.
`BUCKET_WEIGHTS` (what *kind* of tipster) remain static and multiply
independently.

---

## 3. Pillar 3 — market signals

Three distinct market modules exist; only the first feeds the convergence pillar.

### 3.1 Convergence pillar producer — `odds_movement.py`

Captures two odds snapshots per day from **The Racing API** (median win odds per
runner; the table is named `betfair_odds_snapshots` for historical reasons but holds
Racing-API prices in Phase 1):

- `BASELINE_NIGHT` (~12:30 AM) and `MORNING_CHECK` (~8 AM), via
  `capture_snapshot()` (`odds_movement.py:225`).
- `compute_market_signals()` (`:280`) computes
  `movement_pct = (baseline − morning) / baseline × 100` and classifies
  (`classify_signal`, `:211`):

| Signal | Condition | Score |
|---|---|---|
| `STEAM` | ≥ +20% | min(85 + mv − 20, 95) |
| `FIRMING` | ≥ +10% | 65 + (mv − 10) |
| `STABLE` | else | **50.0** |
| `DRIFT` | ≤ −8% | max(30, 45 + mv + 8) |
| `STRONG_DRIFT` | ≤ −15% | max(15, 30 + mv + 15) |

Since STABLE = 50 < the market threshold of 60, only STEAM and FIRMING count as a
"strong" market pillar. Output: `market_signal_scores` table +
`intelligence/market_signals_<date>.json`.

### 3.2 MC-model market features — `market_analysis.py`, `market_velocity.py`

A separate steam/drift taxonomy (±10% / ±25% thresholds) that feeds the **Monte
Carlo engine**, not the convergence gate: probability multipliers 0.85–1.25, MC sigma
modifiers 0.85–1.15, a 0–100 smart-money score, velocity acceleration and
final-30-minute move features. Don't conflate the two taxonomies when reading logs.

### 3.3 Market efficiency segmentation — `market_efficiency.py`

Classifies each race's market into `ultra_efficient / efficient / moderate /
inefficient / thin` from overround, field size, class and venue; each segment has its
own minimum-edge threshold (0.05 / 0.03 / 0.02 / 0.01 / 1.0) and stake modifier.
Also detects anomalies (overround > 30%, dominant favourite > 60% implied, etc.).

### 3.4 Betfair mapping — `build_betfair_mapping.py`

Offline ETL that parses Betfair historical stream files into
`betfair_market_runner_map` and a `betfair_labeled_training_view` with a
10-minutes-to-jump price window. Used for research/training labels only — the only
module touching genuine Betfair exchange data.

---

## 4. LLM inventory (who calls which model)

| Provider | Model | Used by | Produces |
|---|---|---|---|
| Anthropic | `claude-sonnet-4-20250514` | `consensus_agent.py` | Structured pick extraction from panel pages & web research |
| Perplexity | `sonar-pro` | `consensus_agent.py` | Web-search tipster research (4 ecosystem queries/race) |
| Groq (default) | `llama-3.3-70b-versatile` | `llm_form_analysis.py`, `llm_post_scorer.py` via `llm_provider.py` | Pre-MC pace analysis, post-MC 0–100 AI scores, rich insights, brief assessments |
| Ollama (optional) | `llama3.2:3b` | same, when `LLM_PROVIDER=ollama` | Same, locally |

`llm_provider.py` throttles Groq to ~28 req/min (`GROQ_MIN_DELAY_SECONDS = 2.1`) and
hardens JSON parsing (balanced-brace extraction, retries). The pre-MC analyst clamps
its per-runner probability adjustments to ±0.08 (`llm_form_analysis.MAX_ADJUSTMENT`);
the post-scorer prompts ban internal jargon (no "z-score", "ELO", "Monte Carlo") so
insights read as plain racing English.

---

## 5. The V2 three-pillar design (implemented, dormant in production)

All in `consensus_blender.py`:

1. **Blend** (`blend_scores`, `:47`):
   `convergence = 0.50·stride + 0.30·consensus + 0.20·market`.
2. **Tier** (`determine_convergence_tier`, `:62`) with pillar-strength booleans
   `s = stride ≥ 65`, `c = consensus ≥ 65` (`CONSENSUS_LOCK_THRESHOLD`),
   `m = market ≥ 60` (`MARKET_SIGNAL_THRESHOLD`):

   | Pattern | Tier |
   |---|---|
   | s ∧ c ∧ m | **LOCK** |
   | s ∧ (c ∨ m) | **CONFIRM** |
   | s alone | **FLAG** |
   | ¬s ∧ c ∧ m | **CROWD_OVERRIDE** |
   | else | **SKIP** |

3. **Injections** — bounded nudges to the STRIDE score before blending:
   consensus −8…+12 pts (scaled by bucket diversity, independence and reasoning
   alignment), market −5…+8 pts (STEAM +8, FIRMING +4, DRIFT −3, STRONG_DRIFT −5).
4. **Gate** (`apply_convergence_gate`, `:175`): model score < 8.0 → NO_BET
   (`MODEL_WEAK`); LOCK → BET; CONFIRM → BET only if convergence ≥ 55; FLAG,
   SKIP, CROWD_OVERRIDE → NO_BET.

This is the model described in the README diagram. It runs today only via
`python consensus_blender.py <date> --report` against an existing tips file.

---

## 6. The V3 "crowd-first" gate (what production runs)

Live path inside `run_tips_pipeline.py` (`:2554-2673`), using two blender functions:

1. **Candidates.** Horses with `crowd_score ≥ 50` (at least half the checked sources
   tipped them), top 3 per race.
2. **Classification** (`confirm_with_model`, `consensus_blender.py:205`) against the
   raw STRIDE selection score:
   - model ≥ 15 → `CONFIRMED`
   - model ≥ 8 → `CROWD_ONLY`
   - else → `CROWD_ONLY_WEAK`
   - model's top horse, if not a crowd candidate → `MODEL_ONLY`
3. **Bet decision** (`crowd_bet_decision`, `:236`):

   | Classification | Decision |
   |---|---|
   | CONFIRMED | **BET** |
   | CROWD_ONLY, crowd > 70 | BET (overwhelming crowd) |
   | CROWD_ONLY, crowd ≥ 50 | BET (reduced stake) |
   | CROWD_ONLY_WEAK, crowd ≥ 100 | BET (unanimous; added 2026-04-06) |
   | CROWD_ONLY_WEAK, crowd > 70 | NO_BET (tracked as CROWD_OVERRIDE for backtests) |
   | MODEL_ONLY | **NO_BET** ("archetype trap" — model-only picks are gated) |

4. **Stake recommendation:** `FULL` (CONFIRMED) / `STANDARD` (CROWD_ONLY > 70) /
   `REDUCED` / `NONE` (`run_tips_pipeline.py:2622`).

The gate can flip a model-backed BET to NO_BET (`selection_origin: "crowd_gated"`)
or promote a NO_BET to BET (`"crowd_promoted"`). Every runner's classification is
persisted to `convergence_output` for the shadow-P&L backtests, which is how the
dormant tiers (FLAG, CROWD_OVERRIDE) are still being evaluated with real results
(see [Backtesting & learning](10-backtesting-and-learning.md)).

`calculate_market_confidence()` (`consensus_blender.py:267`) additionally produces a
frontend traffic light — GREEN ≥ 70 / AMBER ≥ 50 / RED — from
60% tipster quality + 40% market score, discounted 0.7× when independent sources < 50%.

---

## 7. Environment variables & thresholds

From `.env.example` and code:

| Variable | Default | Meaning |
|---|---|---|
| `STRIDE_MODEL_WEIGHT` | 0.50 | V2 blend weight — model |
| `CONSENSUS_WEIGHT` | 0.30 | V2 blend weight — consensus |
| `MARKET_SIGNAL_WEIGHT` | 0.20 | V2 blend weight — market |
| `CONSENSUS_LOCK_THRESHOLD` | 65 | consensus "strong" cut |
| `MARKET_SIGNAL_THRESHOLD` | 60 | market "strong" cut |
| `CONSENSUS_CONFIRM_THRESHOLD` | 45 | **unused** — present in `.env.example` only |
| `TAVILY_API_KEY` / `ANTHROPIC_API_KEY` / `PERPLEXITY_API_KEY` | — | consensus agent |
| `GROQ_API_KEY`, `LLM_PROVIDER`, `LLM_ENABLED`, `LLM_MODEL` | groq / true | model-side LLM |
| `STRIDE_ACCURACY_WEIGHTS` | false | feed measured tipster hit-rates back into panel weights (§2.4) |

Hard-coded: `STRIDE_THRESHOLD = 65`, `MIN_MODEL_SCORE_FOR_BET = 8.0`,
`MIN_CONFIRM_CONVERGENCE_SCORE = 55.0` (blender); Tavily/Claude daily caps 50/200;
fuzzy-match 0.85 (agent); `MAX_ADJUSTMENT = 0.08` (pre-MC LLM).

---

## 8. Known dead code & quirks (as of this writing)

- `search_race_tips_perplexity()` and `extract_picks_from_content()` (single-shot
  variants) are superseded by their `_multi`/`_batch` versions and never called.
- `MIN_INDEPENDENT_SOURCES_PER_RACE = 3` is defined but never referenced.
- `calculate_consensus_score()` accepts a `weighted_sum` argument that the body
  never uses.
- `fetch_panel_pages()` previously returned a bare `[]` when the panel had no
  active sources while its caller unpacks a 2-tuple — an all-inactive panel would
  have raised `ValueError`. Fixed: it now returns `([], [])` matching its
  signature.
- The `crowd_score ≥ 100` unanimous-crowd bet rule was added off a single observed
  race (inline comment) and is flagged for a 4-week review.
- `betfair_odds_snapshots.back_price` holds Racing-API medians, not Betfair prices.
- `source_accuracy` was originally write-only; the opt-in feedback loop (§2.4)
  now reads it when `STRIDE_ACCURACY_WEIGHTS=true`.

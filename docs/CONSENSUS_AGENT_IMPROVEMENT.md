# Consensus Agent: State, Failure Taxonomy, and Improvement Plan

Written 2026-08-02 (WP-4). Document only: no production code changed in this
work package. All line numbers refer to `server/python/consensus_agent.py`
at commit 338423a unless stated otherwise.

House rule that governs everything below: the consensus agent, convergence
tiers, and franking thresholds are untouchable without explicit approval.
This document is the artifact that approval can attach to.

---

## 1. How it works today

One run per race day, invoked as `consensus_agent.py <date>` before the tips
pipeline (hard gate: without a fresh consensus file every pick becomes
MODEL_ONLY and gates NO_BET).

Stage by stage:

1. **Entry** `run_consensus_agent()` (line 1245). Loads the racecard via
   `load_racecard_meetings`, flattens to races, requires TAVILY_API_KEY and
   ANTHROPIC_API_KEY.
2. **Panel fetch** `fetch_panel_pages()` (line 258) pulls the fixed tipster
   panel (`tipster_panel.json`: 9 sources, 5 active+verified against a
   stated target of 50). Every fetch outcome is written to
   `tipster_panel_log` via `store_panel_log()` (line 1145).
3. **Quality gate** `is_content_usable()` (line 186) filters fetched pages.
4. **Web research fallback** per race: `search_race_tips_perplexity_multi()`
   (line 620) fires 4 Perplexity `sonar-pro` queries (newspaper, portal,
   data, social personas; `sonar-deep-research` for Tier 1 tracks), plus
   Tavily searches; `claude_research_race()` (line 746) orchestrates.
5. **Extraction** `extract_picks_from_content()` / `_batch` (lines 890/950)
   call the Claude API to turn research text into structured picks;
   `match_horse_to_field()` (line 214) fuzzy-matches extracted names to the
   card.
6. **Scoring** `calculate_consensus_score()` (line 1034) computes crowd
   score from mention counts, vote share, source-bucket diversity, and
   reasoning alignment (`check_reasoning_alignment()`, line 1081), scaled
   by historical `accuracy_multiplier()` (line 106).
7. **Persistence**: JSON to `intelligence/consensus_<date>.json`
   (`_write_output`, line 1238) which the blender reads, plus DB mirrors
   (`store_mentions_batch` line 1165, `store_consensus_scores_batch`
   line 1195).
8. **Consumption**: `consensus_blender.py` injects crowd score into
   selection scores and the V3 convergence gate decides
   CONFIRMED / CROWD_ONLY / MODEL_ONLY.

Usage caps are enforced per day via `_check_cap()` (line 182) and persisted
by `_save_usage()` (line 178).

## 2. Failure taxonomy, from real logs and tables

Every failure mode below was observed in production data, with its
frequency where the data allows one.

**F1. Panel fetch failure is the norm, not the exception.**
`tipster_panel_log` all time: 150 FAILED, 50 SUCCESS, 4 SKIPPED across 7
race days. That is a 73.5 percent failure rate on the component that is
supposed to be the primary signal. Mechanism: 5 active scrape targets,
paywalls, JS-rendered pages, and layout drift. Consequence: on a typical
day the "crowd" is mostly LLM web-search summaries, not the panel.

**F2. Silent extraction-model death (observed 2026-08-02, first run).**
The extraction stage called the retired model `claude-sonnet-4-20250514`;
all 10 extraction calls returned HTTP 404; the agent still wrote 53 scores
(all zero-mention), stored them, printed "Complete", and exited 0. The
pipeline downstream cannot distinguish that day from a genuinely quiet
news day. Every day between the model retirement (2026-06-15) and the
2026-08-02 hotfix would have produced this junk-day signature. This is the
most dangerous class: it silently converts accrual days into NO_BET noise.

**F3. Mention sparsity makes the crowd thresholds unreachable.**
Same day, second run (healthy models): Sandown R3 gathered 38 unique
citations and yielded 2 horses with 1 mention each, vote share 1.9 percent,
alignment UNVERIFIABLE. Day totals: 53 horses scored, 12 nonzero (23
percent). All time: exactly 1 of 2,659 rows in `consensus_scores` ever
reached vote_pct of 50, against a CROWD_ONLY gate that wants more than 70.
The bet path that the V3 design routes through the crowd has effectively
never been reachable from this signal.

**F4. DB mirror writes are fire-and-forget.**
Second run, same day: "DB write error: SSL connection has been closed
unexpectedly" (transient Neon disconnect) during the mentions batch. The
JSON file was written, the DB mirror was not, no retry occurred, exit code
0. Any analysis that reads the DB instead of the file silently sees fewer
mentions than the pipeline used.

**F5. Cost and latency concentrate in the fallback path.**
Because F1 removes the cheap source, nearly every race pays the expensive
path: 2026-08-02 second run spent 32 Tavily searches and 18 Claude calls
for an 8-race provincial card, roughly 9 minutes. Per-race multi-persona
queries are the unit of spend, so cost scales linearly with field count
regardless of how little signal exists.

## 3. Why it is weak: the mechanism

The design assumes a dense, independent panel and then measures agreement
within it. The panel does not exist at the assumed density (5 active
sources, 73.5 percent fetch failure), so agreement is computed over a
near-empty set and backfilled with search-engine text that has heavy
source overlap. Vote share over a sparse, overlapping corpus is close to a
binary "was this horse named anywhere", which is weakly informative and
already correlated with the market features the model sees. The scoring
math (line 1034) is fine; its inputs are starved. Secondary weaknesses
follow the same pattern: hardcoded model IDs with no startup validation
(F2), no health contract with the pipeline (exit 0 in every failure mode
above), and no retry cadence within the day (one shot at 07:00 against
content that keeps publishing until noon).

## 4. Redesign options

**Option A: repair in place (1 to 2 days).**
Model IDs from env with a startup preflight call; a health block in the
output JSON (mentions found, sources fetched, extraction yield) plus a
non-zero exit when extraction yields zero mentions; retry with backoff on
the DB mirrors; per-day panel health line in the run summary.
Gets: F2 and F4 eliminated, F1 made visible daily, downstream can tell a
broken day from a quiet one. Does not touch the core weakness (F3).
Risk: minimal. This is hygiene the current design already owes.

**Option B: market-as-crowd (3 to 5 days).**
Now that WP-1 revived Betfair serve-side prices, derive the crowd pillar
primarily from market behaviour (steam and drift between baseline, morning
and tip-time snapshots, volume-weighted moves) and demote text mentions to
a bonus modifier. The market is the densest crowd that exists and it
covers every runner every day.
Gets: guaranteed signal density, F3 dissolves, cost drops (text path
becomes optional).
Costs and risks: collinearity with the model's own market features; the
roadmap's triple-counting warning applies directly, so weights need care
and the convergence tier semantics change, which needs explicit approval.
Effort is moderate because odds_movement already computes steam/drift.

**Option C: panel rebuild plus aggregation rework (1 to 2 weeks).**
Grow toward the 50-source target with fetchers that prefer RSS and APIs
over scraping; move extraction to one batched structured-output call per
meeting instead of per race; apply shrinkage to vote share using the
existing `accuracy_multiplier` priors; recalibrate thresholds to observed
density instead of the aspirational 70.
Gets: the only option that produces a genuinely model-independent signal
at density, which is what the V3 design wanted.
Costs and risks: largest effort, permanent scraping maintenance, and the
lift is unproven until the harness (section 5) says otherwise.

## 5. Evaluation harness (prerequisite for any change)

`consensus_eval.py` (to be built) with two layers:

1. **Mechanical layer, outcome-free**, safe to run any time:
   coverage (share of runners with at least one mention), mention density
   per race, panel fetch success rate, extraction yield per source,
   extraction precision/recall against a golden set of roughly 20
   hand-labelled article excerpts committed as fixtures, cost per race
   (API calls), wall-clock per race. Baseline numbers come from replaying
   the cached research corpora of recent race days.
2. **Outcome layer**: crowd-score lift over the SP-favourite baseline and
   vote-share calibration. Deliberately deferred: no outcome data may be
   examined before the WP-8 window registration, and afterwards only
   within the registered evaluation discipline.

Acceptance rule for any redesign: beat the current baseline on coverage
and extraction precision/recall at equal or lower cost per race, on the
mechanical layer alone, before any outcome-layer comparison is even run.

## 6. Staged rollout and rollback

Behind `STRIDE_CONSENSUS_V3_SHADOW` (default off), mirroring the roi/05 and
serve-liveness shadow discipline already in this repo: the candidate scorer
runs alongside the current agent, writes `consensus_v3_<date>.json` plus a
per-day comparison (score deltas, would-be tier transition matrix), and
feeds nothing into the blender. Flip criteria are registered before the
shadow window opens (minimum 5 clean race days, transition-rate cap, no
regression on the mechanical metrics). Rollback at any stage is unsetting
the flag; the current agent remains the live path throughout.

## 7. Ranked recommendation

1. **Option A immediately.** It is small, it kills the silent-failure
   classes that corrupt accrual days, and nothing can be measured honestly
   until the agent stops lying about its own health.
2. **Build the evaluation harness** (section 5, mechanical layer).
3. **Option B behind the shadow flag.** Market data is already flowing
   after WP-1; it is the fastest route to a crowd pillar with real density,
   and the harness plus shadow window will show whether the collinearity
   worry is fatal before anything flips live.
4. **Option C only if B's shadow shows text mentions add lift the market
   pillar lacks.** Do not spend two weeks on scraping infrastructure to
   feed a signal the market may already carry.

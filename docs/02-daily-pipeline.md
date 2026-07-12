# The Daily Pipeline — a Race Day, End to End

This is the operational walkthrough: what runs on a race day, in what order, and
what each stage produces. The one-command entry point is:

```bash
python server/python/run_full_pipeline.py 2026-04-18 [--tracks randwick flemington]
```

which chains **download → tips → contract backfill** (`run_full_pipeline.py:46-68`)
and aborts on a failed stage. Around that spine sit the pre-race intelligence
builds and the post-race collection/learning loop.

Timing reality check (from `examples/sample_selections.json`, a real 39-race day):
Monte Carlo ≈ 9,053 s, DB enrichment ≈ 258 s, LLM ≈ 485 s.

---

## 0. Overnight / pre-race preparation

| When | What | Command |
|---|---|---|
| ~12:30 AM | Baseline odds snapshot | `odds_movement.py --snapshot baseline_night` |
| overnight | Intelligence build (8 JSON files: barrier map, franking, prep cycles, sectional trends, trainer patterns, overlays…) | `stride_build.py <date>` |
| morning | Racecard download → `racecards/racecard_<date>.json` | `download_racecards.py --date <date>` |
| ~8 AM | Morning odds snapshot → steam/drift signals → `market_signals_<date>.json` | `odds_movement.py --snapshot morning` |
| morning | Tipster consensus poll (Tavily + Perplexity + Claude) → `consensus_<date>.json` | `consensus_agent.py <date>` |

The tips pipeline degrades gracefully if any of these are missing — intelligence
files fall back to empty, the convergence layer disables itself, LLM failures are
non-fatal.

---

## 1. The tips pipeline (`run_tips_pipeline.py <date> [tracks…]`)

Setup once per run: load `.env`, set MC runtime flags (sectional-franking and
jockey-efficiency DB extras default **off** for full cards), initialize the LLM
provider (Groq llama-3.3-70b by default), load the 9 intelligence JSONs, load
consensus + market-signal files via `consensus_blender.load_consensus_intelligence`,
and load `mc_api.py` **in-process** so its model cache survives the whole card.

Then, **per race** (~14 steps):

1. **Filter runners** — drop scratched/withdrawn; skip races with < 2 active.
2. **Normalise & validate** (`race_normaliser.normalise_race`) — canonical going/
   names/classes; CRITICAL flags skip the race; builds the pace map
   (tempo, pressure, likely leaders).
3. **Luckless analysis** (`luckless_analyser`) — excuse detection from last-start
   comments; uplift folded into the LLM mu adjustment (cap 0.12).
4. **LLM pre-analysis** (`llm_form_analysis.analyse_race_field`) — pace scenario and
   per-runner confidence adjustments, clamped ±0.08 mu.
5. **Race context** (`race_context.compute_race_context`) — pace pressure/clarity,
   barrier relevance, market efficiency; broadcast to all runners.
6. **Form features** (`form_feature_builder.compute_race_form_features`) — the DB
   feature block (strike rates, trajectories, trials, bounce…).
7. **ML scoring** (`ml_model.RacingMLModel`) — build the ~110-feature vector per
   runner (including the five interaction features computed inline), predict
   `mlPredictedProb`.
8. **Monte Carlo** (`mc_api.run_simulation`) — Plackett-Luce base + sectional
   overlay, 5,000/3,000/2,000 iterations by field size, time-seeded
   (see [Monte Carlo doc](06-monte-carlo-engine.md)).
9. **Intelligence enrichment** — barrier edges, franking, prep cycles, sectional
   trends, trainer patterns, market overlays, track-distance profiles attached per
   horse.
10. **Calibrate & score** — isotonic → ML blend → market anchor → context
    multipliers → selection score → intelligence adjustment → flat-MC penalties
    (see [Scoring & output](09-scoring-and-output.md)).
11. **LLM post-scoring** — ai_score for the top 6 (30% blend), STRIDE ranking,
    win/trifecta/no-bet call, flat-MC boost if applicable.
12. **Safety filters → top 3** — odds caps, favourite discipline, banker override,
    distance-range and longshot filters; confidence (EV-based) + staking (2u/1u/0u);
    flat-MC gate forces LOW.
13. **DB enrichment + insights** — sectional z-scores, franking ELO, C&D records,
    H2H; long-form `ai_insight` per tip, `brief_assessment` per non-tip.
14. **Bet contract + crowd gate** — `raw_model_leader` / `bet_pick` /
    `coverage_pick` / explicit BET-or-NO_BET with reason; the crowd-first
    convergence gate can flip decisions and sets stake recommendations; every
    runner's convergence row is recorded.

**Aggregation** across races: `best_bets` (top 3 high-confidence), `value_plays`
(edge > 3%, $4–15), `bankers` (high confidence ≤ $4), summary counts,
`selection_contract` totals, convergence summary.

**Persistence:** ~107-column rows into `selections` (bet-worthy picks only; prior
rows deactivated), convergence rows into `convergence_output`, and the full JSON to
`racecards/tips_<date>.json` — written atomically (tmp file + rename), with
track-filtered runs merging into the canonical file after a timestamped backup.

---

## 2. Contract backfill & validation

- `backfill_tips_contract.py <date>` — re-stamps the selection contract on the saved
  file (step 3 of `run_full_pipeline`).
- `validate_tips.py <date> [--strict]` — asserts the contract invariants
  (BET + NO_BET == total, required fields present). The "hard gates" of the README.
- `format_tips.py <date>` — human-readable console rendering.

---

## 3. After the races

| What | Module | Writes |
|---|---|---|
| Seed results queue | `results_projection.ensure_race_schedule_from_prediction_audit` | `race_schedule` (due 30 min after off) |
| Collect results | `auto_results_collector` (retry ≤ 5, optional 5-min daemon) | `prediction_audit`, SP backfill into `race_results_history` |
| Project | `results_projection` | `selection_results`, `training_data` |
| Tip scoring | `stride_results_collector` (7 steps, parallel sectional collection NSW/QLD/VIC-SA) | `stride_tip_results`, `race_results_history`, `sectional_times` |
| Shadow P&L | `shadow_pl_tracker record/results` | tier-level P/L rows |
| Health gates | `sp_health`, `results_health_check` | GREEN/AMBER/RED/CORRUPTED statuses |

## 4. The nightly learning loop

`learn_from_results_v2.py` (PID-locked): detect result/sectional gaps → ingest →
reconcile tips → refresh `training_view_v2` (only if data changed) → **stage** a
retrain into `models/staging/` (never auto-promoted). Weekly,
`weekly_sectional_collector.py` sweeps all three sectional sources. Periodically,
`source_accuracy_tracker.py` records tipster hit rates and the `research/` autopsy
scripts diagnose losing streaks.

---

## 5. Operational notes

- Per-race failures are contained: the race is reported with `bet_status: "ERROR"`
  and the card continues.
- DB access uses short statement timeouts (5–15 s) and exponential-backoff
  connects; mc_api shares one hot connection.
- Track-filtered reruns are safe: they merge into the canonical tips file rather
  than clobbering other tracks.
- The MC seed is time-based per race (`int(time.time()) % 100000`,
  `run_tips_pipeline.py:2256`) — two identical runs will differ slightly; proof
  runs use `--skip-db-store` and `--output-suffix`.
- Everything the pipeline writes locally lives in git-ignored dirs: `racecards/`,
  `server/python/intelligence/*.json`, `models/`, `historical_data/`.

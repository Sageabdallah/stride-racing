# Data Sources & Ingestion

Everything downstream — features, models, intelligence, tips — sits on data pulled
from five external sources into a Neon (cloud) PostgreSQL database plus local JSON
files. This document maps the sources, the collectors, the importers, and the
database schema.

Related docs: [Architecture](01-architecture.md) · [Daily pipeline](02-daily-pipeline.md)

---

## 1. External sources

| Source | Endpoint | Auth | What it provides | Collector |
|---|---|---|---|---|
| **The Racing API** | `https://api.theracingapi.com` (`/v1/australia/meets…`) | HTTP Basic (`RACING_API_USERNAME/PASSWORD`) | Racecards, results, odds, runner stats | `download_racecards`, `download_historical`, `download_training_data`, `fetch_and_import_date`, `auto_results_collector`, `backfill_barrier_trials` |
| **Racing Queensland** | `racingqueensland.com.au/RacingFile.ashx?path=/Sectional/YYYYMMDD_Track_T.csv` | none | QLD sectional CSVs | `sectional_times_collector` |
| **racing.com GraphQL** | `https://graphql.rmdprod.racing.com/` | `x-api-key` (`RACING_COM_API_KEY`) + referer header | VIC/SA sectionals (per-split times) | `racing_com_sectionals_collector` |
| **Racing NSW pidata** | `pidata.racingnsw.com.au/RNSW/RacesLogsMetadata.json` + `.tol` files | none | NSW GPS sectionals (200 m intervals) | `nsw_sectional_collector` |
| **Racing NSW XML** | `racing.racingnsw.com.au/FreeFields/…XML.aspx` | none | NSW results + a single 600 m sectional | `nsw_xml_collector` (alternative path) |
| Weather | — | — | **stub only** — `weather_api.py` returns static fallback and has no callers | — |

The Playwright "sniffer" scripts (`racing_com_api_discovery.py`, `nsw_api_sniffer.py`,
`nsw_deep_sniffer.py`) are the reverse-engineering tools that discovered the GraphQL
and pidata endpoints — dev tooling, not part of production.

Sectional collection is routed **per state** by `learning_track_map.py` /
`ingest_target_track_results_and_sectionals.py`: NSW → `nsw_pidata`, QLD →
`racing_qld`, VIC/SA → `racing_com`, WA (Ascot) → results only (no sectional source).

---

## 2. Forward flow — racecards

`download_racecards.py` pulls upcoming meets for ~27 target metro tracks, tags
barrier trials (`is_trial` heuristics), and writes
`racecards/racecard_<date>.json`. This file is the input to the tips pipeline and
the intelligence agents. Requests use a retry session (3 retries, backoff on
429/5xx) with a 0.1 s delay between races.

## 3. Backward flow — historical data & results

**Bulk history** (one-off/backfill):
- `download_historical.py` — "ultra-slow mode" urllib downloader with checkpoints,
  ~27 tracks, rate limits (0.8 s/request, 60 s cooldowns on 403/429).
- `download_training_data.py` (repo root) — comprehensive-field variant (≈ 80 fields
  per runner: margins, odds, gear, breeding, sectionals, in-running positions) but
  only 8 tracks. Both write `historical_data/historical_training_data.json` —
  **they clobber each other**; pick one.
- Importers: `import_historical_to_db.py` (→ `training_data` +
  `race_results_history`, append-mode), `import_track_json(_fast).py`
  (`historical_data/track_imports/*.json` → `training_data`),
  `import_race_results.py` (**TRUNCATE-and-reload** of `race_results_history` —
  destructive, the odd one out).
- Barrier trials: `backfill_barrier_trials.py` (download) →
  `import_barrier_trials_to_db.py` (creates + fills `barrier_trial_results`,
  idempotent on `UNIQUE(horse_id, trial_date, course, race_name)`).

**Daily results** (the canonical loop):

```
results_collector.py
  ├─ results_projection.ensure_race_schedule_from_prediction_audit
  │     (seeds race_schedule; result_due_at = off_time + 30 min)
  ├─ auto_results_collector.process_pending_races
  │     (Racing API → prediction_audit: position, SP, won, profit_loss;
  │      retry_count < 5; matches horses by normalized name)
  ├─ results_projection.project_resulted_prediction_audit
  │     (prediction_audit → selection_results + training_data;
  │      flat $100 stake, SP returns, baked into SQL)
  └─ sp_health.compute_sp_health  (data-quality gate)
```

`prediction_audit` is the single source of truth for predicted-vs-actual;
`selection_results` and `training_data` are projections from it.

**Tip-specific results:** `stride_results_collector.py` runs a 7-step flow for
STRIDE's own tips: scan `tips_<date>.json` for BET/coverage picks → fetch missing
results → collect sectionals for all three states (parallel, `ThreadPoolExecutor`)
→ score tip accuracy into **`stride_tip_results`** (WIN P/L = (SP−1)×100; PLACE and
LOSS = −100; SCRATCHED = 0) → refresh franking for low-confidence horses (≤ 500) →
settle shadow-P&L rows → summarize.

**Weekly:** `weekly_sectional_collector.py` runs all three sectional collectors in
sequence (10-minute subprocess timeouts) — the Sunday-night catch-up.

---

## 4. Sectional collectors (per state)

All four write into `sectional_times` with a `source` tag and match rows back to
`race_results_history` by (date, race_number, fuzzy track, fuzzy horse name):

- **QLD** (`sectional_times_collector.py`, `source='racing_qld'`): parses the RQ
  semicolon-delimited CSVs into last-200/400/600/800 m speeds/times +
  `finishing_burst`. Afterwards computes the **Phase-2 biomechanical primitives**
  (λ decay, SVI, RSI, trip cost — see [Features §3](04-feature-engineering.md)) and
  a Beyer-style **daily track variant** (par = 90-day median for the same
  track/going/distance-band ±150 m; distance bands 1200–2000).
- **VIC/SA** (`racing_com_sectionals_collector.py`, `source='racing_com'`): two
  GraphQL queries (meetings by state, `raceEntryTimes` per meet); handles
  centisecond-encoded splits (`raw > 100 → /100`); 0.75 s between calls.
- **NSW** (`nsw_sectional_collector.py`, `source='nsw_pidata'`): parses pipe-delimited
  `.tol` GPS telemetry (message types: R!=race, H=horse, I=intermediate splits,
  F=final) into cumulative last-N sections; 5-thread pool, batches of 10.
- **NSW alternative** (`nsw_xml_collector.py`, `source='racing_nsw'`): scrapes the
  free XML export — results plus only a 600 m sectional; standalone, not in the
  fan-out.

---

## 5. Name & track normalization

Matching "Sportsbet Sandown Lakeside" to "Sandown" and "Zeyrek (FR)" to "zeyrek" is
half the battle:

- **`horse_names.py`** — canonical horse matcher used by all sectional collectors:
  strip country suffix `(NZ)`, normalize apostrophes, lowercase; match = exact,
  apostrophe-stripped, or `SequenceMatcher ≥ 0.85`.
- **`normalize.py`** — raw API JSON → consistent schema: sponsor-prefix stripping,
  going → Firm/Good/Soft/Heavy/Synthetic + numeric rating, distance/weight/barrier
  parsing, scratched detection across many field spellings, form-string parsing.
- **`learning_track_map.py`** — track canonicalization + per-track sectional-source
  routing (the authoritative "which state owns this track" map).
- Additionally, ~7 modules carry their own local alias maps (each sectional
  collector, `auto_results_collector`, `stride_results_collector`…) — duplicated
  rather than shared, a known wart.

---

## 6. Database schema

### Created by `migrations/*.sql`

- **`consensus_intelligence.sql`** (V1): `consensus_mentions` (raw tipster picks),
  `consensus_scores` (aggregates, default 35.0), `betfair_odds_snapshots`
  (BASELINE_NIGHT / MORNING_CHECK prices — Racing-API prices in Phase 1 despite the
  name), `market_signal_scores` (STEAM/FIRMING/STABLE/DRIFT/STRONG_DRIFT, default
  50.0), `convergence_output` (blend + tier), plus 7 convergence columns on
  `selections`.
- **`consensus_v2.sql`**: adds `tipster_panel_log` (fetch audit) and
  `source_accuracy` (per-tip hit/miss); expands `selections` to 19
  consensus/market columns; adds reasoning/quality columns to mentions/scores.
- **`phase2_sectional_columns.sql`**: adds the 12 biomechanical columns to
  `sectional_times` (`lambda_decay`, `svi`, `rsi`, `z_200m…z_800m`,
  `trip_cost_seconds`, `variant_adjusted_*`) plus lookup indexes.
- **`final_prob_audit.sql`**: adds `prediction_audit.final_win_prob` — the
  published end-of-pipeline win % per runner, written by
  `run_tips_pipeline.store_final_probs_in_audit` (the code also self-heals
  the column at write time). `predicted_win_prob` remains the MC-stage value
  logged by mc_api.

### Created inline by Python

`barrier_trial_results` (`import_barrier_trials_to_db.py`) and `stride_tip_results`
(`stride_results_collector.py`, with CHECK constraints and back-compat ALTERs).

### Pre-existing core tables (referenced, never created in-repo)

The repo assumes these exist in Neon — there is no CREATE TABLE for them anywhere:

| Table | Role | Key columns (reconstructed from queries) |
|---|---|---|
| `race_results_history` | canonical form history (franking, features) | horse_id/name, race_id, track, race_date, distance_m, class_level, going, position, margin_lengths, weight_kg, jockey, barrier, sp_odds, field_size, opponents_json |
| `sectional_times` | per-horse sectionals | FK race_results_history_id, last_200/400/600/800 m speed+time, splits_json, source, + 12 Phase-2 columns; `UNIQUE(race_date, track, race_number, horse_name)` |
| `training_data` | ML training rows | prediction fields + actual_position/won/placed/starting_price |
| `prediction_audit` | canonical predicted-vs-actual | selection_id, off_time, result_status, actual_position, profit_loss… |
| `race_schedule` | results-collection queue | result_due_at, result_status, retry_count |
| `selections` | published picks (~107 columns after migrations) | full enrichment written by the tips pipeline |
| `selection_results` | settled selections projection | stake=100, return_amount |
| `races`, `franking_scores`, `horse_prep_profiles`, `track_day_bias`, `convergence_output`, `stride_tip_results` | supporting | see per-module docs |

### Data-quality gates

- `sp_health.py`: SP coverage ≥ 90% GREEN / ≥ 80% AMBER / else RED; average SP
  ≤ 2.0 ⇒ CORRUPTED; detects the "SP equals race number" corruption signature.
- `results_health_check.py`: positions ≥ 100 or > field_size ⇒ CORRUPTED
  (Racing-API status codes leak into position fields); merged with SP health for an
  overall date status.

---

## 7. Quirks worth knowing

- **TLS verification is disabled** (`verify=False` / `CERT_NONE`) in the Racing-API
  downloaders.
- Two BM class-level cutoff schemes disagree between importers (88/70/58/50 vs
  85/72/64/58).
- `fetch_and_import_date.py` and `import_barrier_trials_to_db.py` contain a
  hardcoded Windows `.env` fallback path from the dev machine.
- `auto_results_collector --daemon` previously ignored `--check-interval` and
  hardcoded 5 minutes; it now honours a positive `--check-interval` and defaults
  to 5.
- `weather_api.py` is an unwired stub (confidence 0.0, `source: 'stub'`).
- `betfair_odds_snapshots` holds Racing-API prices in Phase 1; genuine Betfair data
  enters only via `build_betfair_mapping.py` (historical stream ETL for research
  labels).

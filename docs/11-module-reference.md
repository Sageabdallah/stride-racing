# Module Reference

One-line purpose for every module in the repo, grouped by role. **Bold** = part of
the live daily path. *(dead)* = no callers found / superseded.

> **Scope caveat.** "No callers found" here means no caller *in this repository*.
> The Express/TypeScript server (`server/*.ts`) and the STRIDE skill runbooks
> (`.claude/skills/`) are both outside the Python tree — `server/scheduler.ts` and
> `server/routes.ts` invoke a dozen Python modules by filename, and the skills
> invoke more. Treat "zero importers" as a prompt to check those two surfaces, not
> as evidence a module is dead.

> **Removed in the 2026-09 cleanup** (dead-API importers, unwired gen-3 builders,
> unused libraries, one-off discovery tools): see
> [`REPO_CLEANUP_AUDIT.md`](REPO_CLEANUP_AUDIT.md) for the list and the evidence.

## Repo root

| File | Purpose |
|---|---|
| **`racing_system_v8.3_mc.py`** | v8.2 17-factor model + production Plackett-Luce MC + Kelly staking + TipGenerator; imported by mc_api and runnable standalone |
| `monte_carlo.py` | Standalone skew-normal MC engine with exotics/bet-slips (CLI showcase; not imported) |

## Orchestrators

| File | Purpose |
|---|---|
| **`run_full_pipeline.py`** | Race-day chain: download → tips → contract backfill |
| **`run_tips_pipeline.py`** | The ~3,000-line heart: per-race normalise→features→ML→MC→calibrate→score→filter→contract→convergence→publish |
| **`tips_day_aggregates.py`** | Day-level blocks of the tips document (best bets, value plays, bankers, summary, selection contract, convergence summary) — single source shared by the full-day build and the per-track merge, so the two publish paths cannot drift; stdlib-only, self-testing |
| **`stride_build.py`** | Nightly intelligence build: runs both STRIDE agents, verifies 8 output files, writes build log |
| `run_full_pipeline.py --skip-*` flags | stage skipping for reruns |

## Ingestion — downloaders & importers

| File | Purpose |
|---|---|
| **`pf_client.py`** | Punting Form fetch layer (meetings, results, meeting detail); raises `PFError` on any failure so a dead day cannot pass silently |
| **`pf_results_mapper.py`** | Maps PF payloads to the 21-column `race_results_history` contract |
| `pf_backfill_results.py` | Results backfill inside the PF Starter window (~31 days) |
| `pf_verify_backfill.py` | Post-backfill verification pass |
| `pf_window.py` | PF subscription-window arithmetic (the pre-window wall) |
| `pf_track_dedup.py` / `pf_fork_repair.py` / `pf_trust_checks.py` | Repair + audit tools for the horse-ID bridge and track aliases (applies still pending) |
| **`providers/puntingform.py`** / `providers/betfair_auth.py` | Provider adapters behind the collectors |
| **`download_racecards.py`** | Provider (PF) → `racecards/racecard_<date>.json` (27 metro tracks, trials tagged) |
| `download_historical.py` | Slow-mode bulk history downloader with checkpoints/resume (PF-era) |
| **`fetch_and_import_date.py`** | One date: PF results → `race_results_history` (append) |
| `backfill_results.py` | Date-range gap backfill, week by week: reuses `fetch_and_import_date` + all three sectional collectors (run via the `backfill-results` Action) — has self-test |
| `audit_write_smoke.py` | Sentinel-row smoke test of mc_api's `prediction_audit` write path, self-cleaning (run via the `audit-smoke` Action) |
| `backfill_barrier_trials.py` → `import_barrier_trials_to_db.py` | Barrier-trial download → `barrier_trial_results` table |

## Ingestion — results & sectionals

| File | Purpose |
|---|---|
| **`results_collector.py`** | Canonical results orchestrator (schedule → collect → project → health) |
| **`auto_results_collector.py`** | Racing API results → `prediction_audit` (+ SP backfill; optional daemon) |
| **`results_projection.py`** | `prediction_audit` → `race_schedule` / `selection_results` / `training_data` projections |
| **`stride_results_collector.py`** | 7-step tips-results flow → `stride_tip_results` + sectionals + franking refresh |
| **`sectional_times_collector.py`** | QLD sectional CSVs → `sectional_times` (+ Phase-2 primitives + daily variant) |
| **`racing_com_sectionals_collector.py`** | VIC/SA sectionals via racing.com GraphQL |
| **`nsw_sectional_collector.py`** | NSW GPS `.tol` telemetry → sectionals |
| `nsw_xml_collector.py` | Alternative NSW path: free XML results + 600 m sectional |
| `weekly_sectional_collector.py` | Sunday sweep of all three sectional collectors |
| `ingest_target_track_results_and_sectionals.py` | One-date fan-out: results + correct per-track sectional source |
| `sp_health.py`, `results_health_check.py` | Data-quality gates (SP coverage, position sanity) |
| `weather_api.py` | *(dead)* going-forecast stub, no callers |

## Normalization

| File | Purpose |
|---|---|
| **`normalize.py`** | Raw API JSON → consistent schema (track/going/distance/weight/scratched/form) |
| **`horse_names.py`** | Canonical horse-name matching (country suffixes, fuzzy ≥ 0.85) |
| **`race_normaliser.py`** | Pre-MC canonicalisation + validation + pace map + candidate features |
| **`learning_track_map.py`** | Track canonicalisation + per-track sectional-source routing |

## Feature engineering

| File | Purpose |
|---|---|
| **`ml_model.py`** | The 110-feature contract + inference wrapper (`RacingMLModel`) + v1 ensemble trainer |
| **`form_feature_builder.py`** | The form-feature workhorse (strike rates, trajectories, trials, bounce; as-of safe) |
| **`race_context.py`** | Race-level pace pressure/clarity, barrier relevance, market efficiency |
| **`speed_mapping.py`** | Field-aware settling prediction + speed-map ML features |
| `pace_modeling.py` | Simpler original pace engine (styles, tempo, advantage) |
| **`sectional_quant.py`** | Five sectional engines (SASR, pace collapse, race shape, closing rank, trip) + Phase-2 primitives |
| `backfill_lambda_targeted.py` | λ/SVI/RSI/trip-cost formulas + targeted backfill |
| **`temporal_staleness.py`** | Freshness features (cosine bands, spacing quality) — has self-test |
| **`fitness_peak.py`** | Campaign detection (60-day spells) + fitness readiness scoring |
| `build_horse_prep_profiles.py` | Persists per-horse distance×run-number win/PvE profiles |
| **`jockey_momentum.py`** | Windowed jockey/trainer momentum (7/14/30 d, as-of safe) |
| `advanced_features.py` | Class hierarchy, weighted form, collateral form, hot streaks (multipliers) |
| `enhanced_features.py` | Class movement, track-specific barrier bias, head-to-head (multipliers) |
| **`empirical_barriers.py`** | Data-driven barrier advantage lookup |
| `speed_ratings.py` | Par-time synthetic speed ratings (feeds trajectory slope) |
| **`track_profiler.py`** | Track×distance profiles → `td_*` features JSON |
| `track_condition_db.py` | Horse-going records + pace energy model; canonical going keywords |
| `glicko2_elo.py` | Surface-conditional Glicko-2 rating engine — has self-test |
| `target_encoding.py` | Leave-one-out smoothed target encoding |
| `feature_store.py` | Two-tier feature cache + feature registry (provenance) |
| `learned_sectional_combination.py` | Learned blend weights over sectional engines (L-BFGS-B) |
| **`relative_market.py`** | Phase-5 within-race market-position features (favourite ladder); parity with mc_api — has self-test |

## ML training & calibration

| File | Purpose |
|---|---|
| **`retrain_v2.py`** | Production trainer: XGB+LGB+CatBoost, walk-forward + purge gap, OOF isotonic → `racing_ensemble_v2.pkl` |
| **`refresh_training_view_v2.py`** | Rebuilds `training_view_v2` matview (union of prediction sources + outcomes + prior sectionals) |
| `train_ml.py` | v1 trainer (delegates to RacingMLModel) |
| `train_ml_enhanced.py` | "Enhanced" single-model trainer (temporal CV, isotonic-vs-Platt, SHAP) |
| **`calibration_model.py`** | Global isotonic calibrator applied in the tips pipeline |
| `conditional_logit.py` | Benter-style two-stage model+market blend (opt-in via `STRIDE_CL_BLEND`; fit CLI + self-test) |
| `double_calibration.py` | Two-layer per-model + ensemble isotonic |
| `mc_recalibration.py` | Isotonic recalibration of MC probabilities (custom PAV) |
| `stacking_meta_learner.py` | OOF logistic stacking over base predictions (fit-path bug fixed; activates on newly trained v1 artifacts) |
| `focal_loss.py` | Focal-loss objectives *(implemented, unwired)* |
| `predictability_meta_model.py` | Race-level "will the favourite win" chaos classifier |
| `feature_drift_monitor.py` | Importance-drift monitoring (JS divergence bands) |
| `compare_features.py` | Training-vs-inference feature parity audit |
| `ml_status.py` | CLI: print model status JSON |
| `rank_model.py` | LambdaRank winner-ranking evidence harness (retrain_v2 matrix + walk-forward + same-race head-to-head; no pipeline hook) — has self-test |
| `audit_coverage_report.py` | Read-only `prediction_audit` coverage diagnostic: monthly raw writes vs view-join match rates (run via the `audit-coverage` Action) — has self-test |

## Monte Carlo & simulation

| File | Purpose |
|---|---|
| **`mc_api.py`** | Production MC orchestrator (7,782 lines): factor model + base MC + sectional overlay + feature adjustments + banker detection; stdin/stdout JSON or in-process |
| **`realistic_simulate.py`** | Mixture-noise / multi-phase-energy / sectional-profile overlay engine |

## Intelligence layer

| File | Purpose |
|---|---|
| **`form_franking.py`** | Global ELO + collateral form franking → `franking_scores` table |
| **`franking_graph.py`** | NetworkX franking graph: deep BFS franking, PageRank, Louvain communities, excuse detection |
| **`stride_agent_track.py`** | Agent 1: barrier map, Flemington straight, class-distance patterns, market overlays (deterministic) |
| **`stride_agent_form.py`** | Agent 2: franking classification, prep cycles, sectional trends, trainer patterns (deterministic) |
| `intelligence_common.py` | Gen-2 shared utils (DB, racecard parsing) |
| `intelligence/common.py` | Gen-3 shared utils (bucketing, cohort filters) |
| `market_overlay_common.py` | SP-based market-overlay map (gen-2) |
| **`banker_detector.py`** | Dominant-favourite detection (composite score, adaptive thresholds) |
| **`luckless_analyser.py`** | Excuse detection from stewards' comments → probability uplift |
| **`track_bias_points.py`** | Static per-track bias configs → points scoring |
| `blackbook_candidates.py` | Unlucky-closer watchlist generator |
| `advanced_race_analysis.py` | Standalone 4-phase LLM race analyst |
| `historical_analysis.py` | One-off print-only performance report |

## Consensus & market

| File | Purpose |
|---|---|
| **`consensus_agent.py`** | Tipster polling (Tavily) + web research (Perplexity) + extraction (Claude) → crowd/consensus scores |
| **`consensus_blender.py`** | Convergence library: V2 blend/tiers/injections/gate + V3 crowd-first classifier (live) |
| **`odds_movement.py`** | Overnight/morning odds snapshots → STEAM/FIRMING/DRIFT market pillar |
| `validate_panel.py` | Tipster-panel reachability pre-flight |
| `source_accuracy_tracker.py` | Records tipster hit rates (feedback loop not yet closed) |
| **`llm_provider.py`** | Groq/Ollama provider abstraction with JSON hardening |
| **`llm_form_analysis.py`** | Pre-MC LLM race analysis (±0.08 mu adjustments) |
| **`llm_post_scorer.py`** | Post-MC AI scores, rankings, rich insights, brief assessments |
| `market_analysis.py` | Steam/drift features + probability multipliers (model-side) |
| `market_velocity.py` | Velocity/acceleration/smart-money features + MC sigma modifier |
| `market_efficiency.py` | Overround-based market segmentation + per-segment strategy |
| `build_betfair_mapping.py` | Betfair historical stream ETL → runner map + labeled training view |

## Backtesting, learning, risk & output

| File | Purpose |
|---|---|
| `backtest.py` | v1 walk-forward ensemble backtest (13 strategies) |
| `backtest_v2_metro.py` | v2 ensemble backtest — source of the README results |
| `walk_forward_backtest.py` | Rigorous expanding-window CV with purge gap + CIs |
| `calibration_backtest.py` | Sectional-blend grid search (18 configs) |
| `backtest_prep_profiles.py` | Old-vs-new fitness scoring A/B |
| **`learn_from_results_v2.py`** | Nightly learning loop: gaps → ingest → reconcile → refresh → staged retrain |
| **`shadow_pl_tracker.py`** | Level-stakes P/L for all convergence tiers (incl. un-bet) |
| `weight_optimizer.py` | MLE optimization of scorer adjustment weights + barrier analyzer |
| `portfolio_risk.py` | Kelly staking + exposure caps + portfolio metrics (library) |
| `format_tips.py` | Console tips renderer |
| **`validate_tips.py`** | Tips-contract validator (hard gate) |
| **`backfill_tips_contract.py`** | Re-stamps the selection contract on saved tips files |
| `backfill_{phase2,zscores,zscores_targeted,rrh_missing_dates}.py` | Data repair sweeps (see backtesting doc §6) |
| `research/performance_autopsy_last21days.py` | Losing-tip failure-mode classifier |
| `research/investigate_sectional_market_going.py` | Sectional-coverage + wet-going diagnostics |
| `research/winner_pattern_gap/` | Two-agent winner-pattern research pipeline + synthesis |
| `scripts/plot_calibration.py` | Renders `examples/backtest_summary.json` → calibration.png |

## Other

| File | Purpose |
|---|---|
| `migrations/*.sql` | Consensus V1/V2 tables + Phase-2 sectional columns |
| `examples/` | Real race-day output + backtest summary (the README's evidence) |
| `server/python/tipster_panel.example.json` | Tipster panel template (real panel git-ignored) |

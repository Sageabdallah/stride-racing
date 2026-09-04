# Repo cleanup audit — 2026-09-03

Survey of every tracked Markdown and Python file, looking for documents that
are demonstrably out of date and modules that nothing can reach. This file
replaces the 2026-03-20 audit of the same name, which described a repository
shape (`client/`, `server/*.ts`, `shared/`, `knip`) that no longer exists here.

## Method

Every file was tested against the repository as it stands, not judged by its
name or age:

- **Reference graph.** A script parsed every `.py` with `ast` for imports,
  then searched every tracked file (workflows, `infra/*.sh`, the Dockerfile,
  `infra/jobs/handler.py`, JSON, Markdown) for the filename as a string, so
  subprocess calls and doc links count as references too.
- **Duplicates** by content hash.
- **Claims in documents** checked against the repo: merged pull requests in
  `git log`, branches on `origin`, files that a document lists, workflow
  runners, flag defaults in `.env.example`.
- **Verification** before and after: `python -m compileall -q .`, the pytest
  suite CI runs, and the 17 module self-tests listed in `ci.yml`.

Two surfaces are outside this repository and could not be checked: the
untracked Express/TypeScript server and the gitignored `.claude/skills/`
runbooks, both of which invoke Python modules by filename. Nothing below was
removed on the strength of "no caller" alone if either surface could
plausibly still call it; those modules sit in the second table instead.

## 1. Removed in this pass (34 files)

### Markdown (13)

| File | Why | Evidence |
|---|---|---|
| `AGENTS.md` (root) | Duplicate | Byte-identical to `docs/roi-roadmap/AGENTS.md`. Its relative references (`README.md` as the wave index, `00-evidence-base.md`, `NN-*.md`) only resolve inside `docs/roi-roadmap/`. README now links the surviving copy. |
| `ACTION_THIS.md` | Finished checklist (2026-07-31) | Self-hosted runner is now used by six workflows; the "racing.com vs Punting Form" decision it defers is settled in `PUNTINGFORM_MIGRATION.md`; Betfair verification it asks for is recorded in `scripts/BETFAIR_KEYS_STATUS.md`. The one open item (Mac session) is already tracked in `PUNTINGFORM_MIGRATION.md`'s [U] list. |
| `attentionsage.md` | Finished checklist | Asks to merge PRs #1–#10: all are merged (`git log`; #4 landed as `58057c6`). The deploy session it schedules happened 2026-08-02 (ledger live per the `docs/roi-roadmap/README.md` tracker; day zero in `docs/project_retrain_gate.md`). |
| `PROGRAM_STATUS.md` | Stale status (2026-07-29) | Opens with "Nothing has merged yet. Main = `58b75fc`"; PRs #1–#20 merged 2026-08-01/02. Its board is superseded by the roadmap tracker and `docs/project_retrain_gate.md`. |
| `MERGE_EXECUTION_PLAN.md` | Executed plan (2026-07-29) | Merge order for the same PRs, all merged. Its three durable "standing rules" (§4b) were carried into `docs/10-backtesting-and-learning.md`; its leak invariants are `docs/roi-roadmap/AGENTS.md` rules 5–7. Unreferenced. |
| `OUTSTANDING_WORK.md` | Stale snapshot (2026-07-27) | §0.1 ("has the pipeline ever run?") is answered by the AWS chain running since 2026-08-02 (`infra/EXECUTION_STATUS.md`); §0.2's three branches no longer exist on `origin`; §4's flag table is wrong (`STRIDE_INTERACTION_PARITY` defaults ON, `.env.example`). Open research questions live in `docs/analysis/IMPLEMENTATION_PLAN.md`, `docs/analysis/RESULTS.md` and `docs/12`. Unreferenced. |
| `docs/REPO_CLEANUP_AUDIT.md` (2026-03 version) | Obsolete | Audits `client/`, `server/*.ts`, `shared/`, `attached_assets/`, `knip` output; none exist in this repository. Replaced by this file. |
| `docs/design_guidelines.md` | Wrong repo | UI design spec (nav bar, hero image, Tailwind spacing) for the frontend, which is gitignored. Only referrer was the old audit. |
| `docs/research/proof/` (5 files) | Superseded run | Pre-backfill research output: synthesis reads "No strong cross-agent overlap", "Sample size: 0". `proof_after_backfill/` is the same pipeline re-run on complete data (sample 81) and is the run `IMPLEMENTATION.md` implements. `agent1_findings.md` was byte-identical between the two. No path references. |

### Python (21)

| File | Why | Evidence |
|---|---|---|
| `download_training_data.py` (root) | Dead provider | Hard-codes `api.theracingapi.com` and exits at import without `RACING_API_USERNAME`. The Racing API ceased Australian coverage 2026-07 (credentials return 401). The Punting Form successor is `server/python/download_historical.py`. |
| `build_features.py` (root) | No input left | Reads the historical JSON (`course`/`draw`/`sp`/`runners` shape) that only `download_training_data.py` produced. README's "feature engineering entry point" label was wrong: live feature engineering is `form_feature_builder.py`, `serve_features.py`, `retrain_v2.py`. |
| `import_historical_to_db.py` | Dead-API era | `--input historical_data/historical_training_data.json`, "from download_training_data.py". |
| `import_track_json.py`, `import_track_json_fast.py` | Dead-API era | Consume `historical_data/track_imports/*.json` in "Racing API JSON format". |
| `import_race_results.py` | Dead-API era, destructive | `TRUNCATE TABLE race_results_history` then reload from the same dead-era files. Module reference already said "do not run". |
| `backfill_research_sources.py` | One-off, half dead | Rebuilds the winner-pattern research corpus; step 1 shells out to `import_track_json_fast.py`. That research is complete (`docs/research/proof_after_backfill/IMPLEMENTATION.md`); the other steps are plain CLIs (`build_betfair_mapping.py`, `refresh_training_view_v2.py`). |
| `adaptive_mc.py` | Never used | Zero importers; `docs/06` "unused", `docs/11` "(dead)", SYSTEM_MAP "dead". |
| `model_versioning.py` | Never adopted | Zero importers; `docs/05` "written but not adopted". Its role is filled by `release_manifest.py`, which `handler.py` and `run_tips_pipeline.py` import. |
| `racing_com_api_discovery.py` | One-off dev tool | Hard-coded Replit `/nix/store/...chromium` path and February-2026 sample URLs. The GraphQL endpoint it found lives in `racing_com_sectionals_collector.py`. |
| `nsw_api_sniffer.py`, `nsw_deep_sniffer.py` | One-off dev tools | Endpoint discovery for pidata, now encoded in `nsw_sectional_collector.py`. `nsw_api_sniffer.py` runs `pip install playwright` at import. Zero callers. |
| `betfair_smoke_test.py` | Superseded | Cert login → `listEventTypes`. Covered by `scripts/betfair_cert_check.py` (cert login) and `scripts/betfair_keys_smoke.py` (workflow-wired). Not referenced anywhere. |
| `intelligence/build_*.py` (8 files) | Never wired | Gen-3 parallel rewrite of the builders. Zero importers, subprocess or workflow references; `stride_build.py` runs the gen-2 agents; `docs/07` "not wired in"; `build_trainer_patterns.py` was a permanent stub. Their output directory is read by nothing. `intelligence/common.py` stays (18 importers). |

### Edits made so nothing dangles

`README.md` (layout table, agent-rules link), `docs/01`, `docs/03`, `docs/05`,
`docs/06`, `docs/07`, `docs/10` (plus the three standing rules from the
retired merge plan), `docs/11`, `PUNTINGFORM_MIGRATION.md`, and one comment
in `server/python/luckless_analyser.py`. Dated research and audit documents
(`docs/analysis/*`, `docs/roi-roadmap/00-evidence-base.md`,
`docs/research/*`, `research/report.md`) still name some removed files; they
describe the state at their own date and were left alone.

## 2. Recommended for removal, not removed — owner's call

Each of these is unreachable from anything in this repository, but either a
documented rule protects it, a private surface might still call it, or it
carries content worth relocating first.

| File | Finding | What stops the deletion |
|---|---|---|
| `HUMAN_INTERACTION.md` | 2026-07 narrative; two of its three "only a human can do" actions are done (ledger migration applied 2026-08-02; `selection-diagnostics` workflow exists). | Only referrer was `OUTSTANDING_WORK.md` (removed). Keep if the plain-language explanation is still wanted. |
| `ROADMAP_REVIEW.md` | 2026-07-27 review of the roadmap pack; 8 of the 14 tasks have since been executed. Unreferenced. | Review verdicts for the six open tasks (08, 10–14) are still readable. |
| `agent_research.md`, `orchestrator_instuctions.md` | The prompts that produced `docs/analysis/*`; the research phase is complete. | `docs/analysis/RESULTS.md` cites the protocol in `orchestrator_instuctions.md`. Better moved under `docs/analysis/` than deleted. |
| `docs/ADVANCED_RACE_ANALYSIS.md` + `advanced_race_analysis.py` | The doc describes API routes, a React component and a Drizzle schema in the excluded TS server; 4 of the 6 files it lists and `examples/sample_4phase_analysis.json` do not exist here. The module has zero importers. | The module may still be called by the local Express server. |
| `monte_carlo.py` (root) | Standalone MC engine, not imported by anything; the production engine is `racing_system_v8.3_mc.py` via `mc_api.py`. `docs/06` calls part of it unused. | README and `docs/README.md` present it as a runnable showcase. |
| `weather_api.py` | Unwired stub returning static values (`docs/03`, `docs/11`). | `docs/12` roadmap item 7 says "Finish `weather_api.py`". |
| `train_ml.py` | v1 trainer, zero importers, superseded twice (`train_ml_enhanced.py` → `retrain_v2.py`). | SYSTEM_MAP's "never delete a superseded generation" rule names this chain. |
| `feature_store.py` | Zero importers. | `docs/04` values its `FeatureRegistry` as a provenance map. |
| `learned_sectional_combination.py` | Zero importers; "auxiliary" per `docs/04`. | Research tool. |
| `market_efficiency.py` | Zero importers. | Standing prohibition #7 (`docs/analysis/IMPLEMENTATION_PLAN.md` §5) says "no wiring"; deleting it retires the prohibition rather than breaking it. |
| `nsw_xml_collector.py` | "Alternative NSW path", zero callers. | Could serve as a fallback source. |
| `providers/theracingapi.py` | Dead provider, kept selectable in the registry. | `providers/test_contract.py` subclasses it as the fake-transport base; removal needs that test rewritten around `PuntingFormProvider`. |
| `historical_analysis.py` | Self-described one-off; runs queries at import. | May be the "performance" step of the private `/stride-full` runbook. |
| `weekly_sectional_collector.py` | Subprocess wrapper over the three collectors; `sectional-schedules.yml` and `handler.py` `job_nightly_etl` call them directly. | Only known caller was the untracked TS scheduler; a private runbook might still use it. |
| `validate_panel.py` | Reachability check superseded by `panel_liveness.py` (2026-08-06, wired into `infra/09c_upload_panel.sh`). | Same. |
| `ml_status.py`, `format_tips.py` | Tiny CLIs whose only callers were in the untracked Express server. | Same. |
| `validate_trial_linkage.py` | Read-only audit CLI, no callers, absent from the module reference. | Same. |
| `docs/decision-learning/V1_IMPLEMENTATION_GUIDE.md` | Superseded guide; states a FastAPI/EventBridge stack that `02_ARCHITECTURE_AND_CONTRACTS.md` corrects. | `CLAUDE.md` says it is retained deliberately. |
| Root `00_…17_*.md` | Current decision-learning plan (README says so; `02` updated 2026-08-13). | Keep; they would read better under `docs/decision-learning/`. |

## 3. Kept on purpose (examples that looked dead but are not)

- `focal_loss.py`, `stacking_meta_learner.py`, `target_encoding.py`: imported by `ml_model.py`.
- `train_ml_enhanced.py`: imported by `mc_api.py`.
- `backtest.py`: wired to `roi_stats.py` in August; still maintained.
- `compare_interaction_parity.py`: reproduces the committed evidence in `examples/interaction_parity_comparison_*.json`.
- `devig_comparison.py`, `crowd_promotion_report.py`: built for registered, not-yet-run experiments.
- `run_full_pipeline.py`, `learn_from_results_v2.py`, `blackbook_candidates.py`, `source_accuracy_tracker.py`: steps of the documented runbooks.
- `backfill_{phase2,zscores,zscores_targeted,rrh_missing_dates,ledger_prices}.py`: repair sweeps listed in `docs/10` §6.
- `docs/CONSENSUS_AGENT_IMPROVEMENT.md`, `docs/roi-roadmap/10-exchange-spike.md`: unreferenced but current design records; worth linking from `docs/08` and the roadmap tracker.

## 4. Side findings (not in scope, not changed)

- `pf_fork_remap.json` at the repo root is an older run (2026-08-01) of the same audit output as `server/python/pf_fork_remap.json` (2026-08-02, with `post_apply`).
- `docs/README.md` still says "There is no test suite in the repo"; CI runs 864 pytest tests.
- `backfill_zscores.py` and `import_barrier_trials_to_db.py` hard-code a Windows dev-machine path (an unmerged `fix/backfill-zscores-portable-paths` branch exists).
- `target_tracks.py`'s docstring still cites `download_training_data.py`'s track list as historical rationale.

## 5. Verification

| Check | Before | After |
|---|---|---|
| `python -m compileall -q .` | clean | clean |
| `python -m pytest server/python -q` | 864 passed | 864 passed |
| 17 module self-tests from `ci.yml` | — | 17/17 pass |
| Deleted names in workflows, `infra/`, Dockerfile, `handler.py` | — | none |
| Relative Markdown links across the repo | — | none broken |

No test was skipped, weakened or deleted; no test file was removed.

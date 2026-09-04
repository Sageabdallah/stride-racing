# Repo cleanup audit — 2026-09-03

Survey of every tracked Markdown and Python file, looking for documents that
are demonstrably out of date and modules that nothing can reach. This file
replaces the 2026-03-20 audit of the same name, which described a repository
shape (`client/`, `server/*.ts`, `shared/`, `knip`) that no longer exists here.

**47 files removed** (17 Markdown, 29 Python, 1 JSON), each verified below.
18 more were moved rather than deleted, and a hardcoded-path defect was fixed
across seven modules — see §2. (The 2026-03 audit this file replaces was
rewritten at the same path, so git records it as a modification, not a
deletion.)

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
runbooks, both of which invoke Python modules by filename. Where a document
states that one of those surfaces calls a module, the module was kept — see
§3.

## 1. Removed

### Stale planning and status documents (11)

| File | Why | Evidence |
|---|---|---|
| `AGENTS.md` (root) | Duplicate | Byte-identical to `docs/roi-roadmap/AGENTS.md`. Its relative references (`README.md` as the wave index, `00-evidence-base.md`, `NN-*.md`) only resolve inside `docs/roi-roadmap/`. README now links the surviving copy. |
| `ACTION_THIS.md` | Finished checklist (2026-07-31) | Self-hosted runner is now used by six workflows; the "racing.com vs Punting Form" decision it defers is settled in `PUNTINGFORM_MIGRATION.md`; the Betfair verification it asks for is recorded in `scripts/BETFAIR_KEYS_STATUS.md`. Its one open item is already tracked in `PUNTINGFORM_MIGRATION.md`'s [U] list. |
| `attentionsage.md` | Finished checklist | Asks to merge PRs #1–#10: all merged (`git log`; #4 landed as `58057c6`). The deploy session it schedules happened 2026-08-02 (day zero in `docs/project_retrain_gate.md`). |
| `PROGRAM_STATUS.md` | Stale status (2026-07-29) | Opens with "Nothing has merged yet. Main = `58b75fc`"; PRs #1–#20 merged 2026-08-01/02. Superseded by the roadmap tracker and `docs/project_retrain_gate.md`. |
| `MERGE_EXECUTION_PLAN.md` | Executed plan (2026-07-29) | Merge order for the same PRs, all merged. Its three durable "standing rules" (§4b) were carried into `docs/10-backtesting-and-learning.md`; its leak invariants are `docs/roi-roadmap/AGENTS.md` rules 5–7. Unreferenced. |
| `OUTSTANDING_WORK.md` | Stale snapshot (2026-07-27) | §0.1 ("has the pipeline ever run?") is answered by the AWS chain running since 2026-08-02 (`infra/EXECUTION_STATUS.md`); §0.2's three branches no longer exist on `origin`; §4's flag table is wrong (`STRIDE_INTERACTION_PARITY` defaults ON, `.env.example`). |
| `HUMAN_INTERACTION.md` | Stale narrative (2026-07) | Its three "only a human can do" actions: the ledger migration was applied 2026-08-02, the `selection-diagnostics` workflow exists and runs, the conditional-logit refit has a released result quoted in `docs/12`. Its headline claim ("the system has never been measured") predates the accrual window. |
| `ROADMAP_REVIEW.md` | Stale review (2026-07-27) | An external review of the roadmap pack, explicitly "verified against the repository at the current commit" — 8 of its 14 tasks have since been executed and the Punting Form migration has moved the code it cites. Its two "modify" verdicts were actioned when those tasks shipped. Unreferenced. |
| `agent_research.md`, `orchestrator_instuctions.md` | Executed prompts | One-shot agent briefs for the two research phases. Their output is `docs/analysis/` (SYSTEM_MAP, ACADEMIC_FINDINGS, IMPROVEMENT_REPORT, IMPLEMENTATION_PLAN) plus RESULTS.md; SYSTEM_MAP §5a restates the guardrails inline. The two live citations (RESULTS.md header, `ship_criteria.py` docstring) now stand alone. |
| `docs/REPO_CLEANUP_AUDIT.md` (2026-03) | Obsolete | Audited `client/`, `server/*.ts`, `shared/`, `attached_assets/`, `knip` output; none exist here. Replaced by this file. |

### Documents for code that is not in this repository (2)

| File | Why | Evidence |
|---|---|---|
| `docs/design_guidelines.md` | Wrong repo | UI design spec (nav bar, hero image, Tailwind spacing) for the frontend, which is gitignored. Only referrer was the old audit. |
| `docs/ADVANCED_RACE_ANALYSIS.md` | Mostly absent code | Documents API routes, a React hook and component, and a Drizzle schema in the excluded TS server; 4 of the 6 files in its own "Files Added/Modified" table, and the `examples/sample_4phase_analysis.json` it points at, do not exist here. The engine it describes stays and is summarised in `docs/07` §5. Unreferenced. |

### Superseded research output (5)

`docs/research/proof/` — the pre-backfill run of the winner-pattern pipeline.
Its synthesis reads "No strong cross-agent overlap", "Sample size: 0".
`proof_after_backfill/` is the same pipeline re-run on complete data (sample
81) and is the run `IMPLEMENTATION.md` implements. `agent1_findings.md` was
byte-identical between the two. No code path references either directory.

### The Racing API era (7)

The provider ceased Australian coverage in July 2026 and its credentials
return 401. These consumed its output and nothing produces their input now.

| File | Evidence |
|---|---|
| `download_training_data.py` | Hard-codes `api.theracingapi.com`; exits at import without `RACING_API_USERNAME`. The Punting Form successor is `server/python/download_historical.py`. |
| `build_features.py` | Reads the historical JSON (`course`/`draw`/`sp`/`runners` shape) that only `download_training_data.py` produced. README's "feature engineering entry point" label was wrong: live feature engineering is `form_feature_builder.py`, `serve_features.py`, `retrain_v2.py`. |
| `import_historical_to_db.py` | `--input historical_data/historical_training_data.json`, "from download_training_data.py". |
| `import_track_json.py`, `import_track_json_fast.py` | Consume `historical_data/track_imports/*.json` in "Racing API JSON format". |
| `import_race_results.py` | `TRUNCATE TABLE race_results_history` then reload from the same dead-era files. The module reference already said "do not run". |
| `backfill_research_sources.py` | One-off corpus rebuild whose first step shells out to `import_track_json_fast.py`. That research is finished (`docs/research/proof_after_backfill/IMPLEMENTATION.md`); its other two steps are plain CLIs. |

### Written but never wired (13)

Zero importers, zero subprocess callers, zero workflow or infra references.

| File | Evidence |
|---|---|
| `adaptive_mc.py` | `docs/06` "unused", `docs/11` "(dead)", SYSTEM_MAP "dead". |
| `model_versioning.py` | `docs/05` "written but not adopted"; no `models/registry/` exists. Artifact versioning is `release_manifest.py`, which `handler.py` and `run_tips_pipeline.py` import. |
| `feature_store.py` | 572 lines of cache and registry that nothing calls, so the cache never fills. Feature provenance is covered with better evidence by `docs/research/FEATURE_PROVENANCE.md` and `feature_liveness_audit.py`, which audit all 113 columns against the trained artifact. |
| `learned_sectional_combination.py` | Learns blend weights for the five sectional engines; nothing consumes the weights. `docs/04` called it "auxiliary, not part of the 110-column contract". |
| `market_efficiency.py` | Standing prohibition 7 (`docs/analysis/IMPLEMENTATION_PLAN.md` §5) forbids wiring it, and nothing does. Removal retires the prohibition rather than breaking it. The `market_efficiency_value`/`_flag` features in `serve_features.py` are unrelated and stay. |
| `train_ml.py` | The v1 trainer CLI, superseded twice (`train_ml_enhanced.py` → `retrain_v2.py`); reads the legacy `training_data` table, and `retrain-model.yml` runs `retrain_v2.py`. `ml_model.RacingMLModel` (which it wrapped) stays: the backtest harness trains one per fold. |
| `intelligence/build_*.py` (8 files) | The gen-3 parallel rewrite of the builders. `stride_build.py` runs the gen-2 agents; `docs/07` "not wired in"; `build_trainer_patterns.py` was a permanent stub (`historical_pattern_available: false` always). Nothing reads their output directory. `intelligence/common.py` stays — 18 modules import it. |

### One-off tools whose job is done (6)

| File | Evidence |
|---|---|
| `racing_com_api_discovery.py` | Hard-coded Replit `/nix/store/…chromium` path and February-2026 sample URLs. The GraphQL endpoint it found lives in `racing_com_sectionals_collector.py`. |
| `nsw_api_sniffer.py`, `nsw_deep_sniffer.py` | Endpoint discovery for pidata, now encoded in `nsw_sectional_collector.py`. `nsw_api_sniffer.py` runs `pip install playwright` at import. |
| `betfair_smoke_test.py` | Cert login → `listEventTypes`, superseded by `scripts/betfair_cert_check.py` (cert login) and `scripts/betfair_keys_smoke.py` (workflow-wired). |
| `historical_analysis.py` | Self-described one-off with hardcoded "last 3 months" queries, no CLI arguments and **no `__main__` guard** — importing it opens a database connection and prints a report. |
| `validate_trial_linkage.py` | One-time audit of how many barrier-trial runners join to race records. Absent from the module reference; the maintained PF-era identity audit is `pf_trust_checks.py`. |

### Superseded by a better version of itself (1)

`validate_panel.py` — a plain `requests.get` reachability check over the
tipster panel. `panel_liveness.py` (2026-08-06) does the same job with the
ALIVE/FLAKY/DEAD distinction that a bare pass/fail cannot express, is wired
into `infra/09c_upload_panel.sh`, and has a test.

### Stale duplicate artifact (1)

Root `pf_fork_remap.json` — the 2026-08-01 run of the horse-ID fork audit.
`server/python/pf_fork_remap.json` is the 2026-08-02 run of the same audit and
is the one with the `post_apply` block; `pf_fork_repair.py` writes it there.

### Edits made so nothing dangles

`README.md`, `docs/01`, `03`, `04`, `05`, `06`, `07`, `08`, `10`, `11`, `12`,
`docs/README.md`, `docs/analysis/RESULTS.md`, `PUNTINGFORM_MIGRATION.md`, and
comments or docstrings in `luckless_analyser.py`, `ship_criteria.py`,
`fit_calibrator.py` and `target_tracks.py`. The three standing
evaluation-hygiene rules from the retired merge plan now live in `docs/10`.
Dated research and audit documents (`docs/analysis/*`,
`docs/roi-roadmap/00-evidence-base.md`, `docs/research/*`,
`research/report.md`) still name some removed files; they describe the state
at their own date and were left alone.

## 2. Moved and fixed, not removed

### The decision-learning plan left the repository root

The 18 numbered files `00_MASTER_INDEX.md` … `17_IMPLEMENTATION_STATUS.md` now
live in [`decision-learning/`](decision-learning/README.md), beside the V1
guide they supersede, with a `README.md` index. Content unchanged; their
cross-references were already sibling filenames, so they resolve correctly in
the new location. The root is now `README.md`, `CLAUDE.md` and
`PUNTINGFORM_MIGRATION.md`. `README.md`, `CLAUDE.md` (which called the pack
"separately supplied" without saying where it was) and `docs/README.md` were
updated to point at the folder.

### One dev machine's paths, in seven modules

`c:\Users\sagea\OneDrive\Desktop\…` was hardcoded in six modules as the
fallback `.env` location and in a seventh as an argparse default. It resolved
on no other machine — including the Mac it was meant for — so the fallback was
dead everywhere and the "DATABASE_URL not found" message named a directory
nobody had.

| Module | Was | Now |
|---|---|---|
| `backfill_zscores.py` | Both the `sys.path` entry and `ENV_PATH` absolute into that checkout, with no derived path at all | derived from `__file__` |
| `retrain_v2.py`, `fetch_and_import_date.py`, `backfill_phase2.py`, `backtest_v2_metro.py`, `import_barrier_trials_to_db.py` | repo-root `.env`, then the Windows path | repo-root `.env`, then `$STRIDE_ENV_FILE` if set |
| `build_betfair_mapping.py` | `--data-dir` defaulted to that machine's `Desktop\BETFAIR\…` | defaults to `$BETFAIR_HISTORICAL_DIR`; absent, the run stops with a message instead of scanning an empty directory |

Both variables are documented in `.env.example`. Verified: with a repo-root
`.env` all five gate-on-`DATABASE_URL` modules load it and none exits; with no
`.env` the error names the real path and the override; with `STRIDE_ENV_FILE`
pointing elsewhere that file is used. This supersedes the unmerged branch
`fix/backfill-zscores-portable-paths`, which fixed one of the seven.

## 3. Kept, and why

Unreachable from this repository, but removing them would be a guess rather
than a cleanup.

| File | Why it stays |
|---|---|
| `weekly_sectional_collector.py`, `ml_status.py`, `format_tips.py`, `advanced_race_analysis.py` | `docs/03` states the untracked TypeScript scheduler drives the weekly sweep, and `docs/11`'s scope caveat records that `server/routes.ts` and `server/scheduler.ts` invoke a dozen Python modules by filename. These four are exactly that shape — a status CLI, a renderer, a Sunday sweep, an LLM analyst behind an API route. Deleting them could break the owner's local app, and nothing here can prove otherwise. |
| `nsw_xml_collector.py` | An alternative ingestion path for a live source (free Racing NSW XML: results plus a 600 m sectional). It is the one deletion that could cost data later, and this system's failure history is data going quietly missing. Unwired, but a real second source. |
| `monte_carlo.py` | Standalone, runnable, and advertised as such by README and `docs/README.md`, which record it running end to end. In a repository published for review, that is the point. |
| `providers/theracingapi.py` | Dead as a provider, but `providers/test_contract.py` subclasses it as the reference implementation the contract tests run against, and the internal schema was frozen from its shape. Removing it means rewriting 204 lines of test fixtures around Punting Form — a refactor with regression risk, not a cleanup. |
| `docs/decision-learning/V1_IMPLEMENTATION_GUIDE.md` | `CLAUDE.md` states it is retained deliberately as the superseded V1 guide. |
| Root `00_…17_*.md` | The current decision-learning plan, not a historical one (`02_ARCHITECTURE_AND_CONTRACTS.md` was updated 2026-08-13 and corrects `CLAUDE.md` on the live stack). They would read better under `docs/decision-learning/`, but moving 18 cross-linked files is a rename, not a cleanup. |

Also kept, and worth a link from somewhere rather than deletion:
`docs/CONSENSUS_AGENT_IMPROVEMENT.md` and
`docs/roi-roadmap/10-exchange-spike.md` are unreferenced but current design
records.

## 4. Kept — looked dead, is not

- `focal_loss.py`, `stacking_meta_learner.py`, `target_encoding.py`: imported by `ml_model.py`.
- `train_ml_enhanced.py`: imported by `mc_api.py`.
- `backtest.py`: wired to `roi_stats.py` in August; still maintained.
- `compare_interaction_parity.py`: reproduces the committed evidence in `examples/interaction_parity_comparison_*.json`.
- `devig_comparison.py`, `crowd_promotion_report.py`: built for registered, not-yet-run experiments.
- `run_full_pipeline.py`, `learn_from_results_v2.py`, `blackbook_candidates.py`, `source_accuracy_tracker.py`: steps of the documented runbooks.
- `backfill_{phase2,zscores,zscores_targeted,rrh_missing_dates,ledger_prices}.py`: repair sweeps listed in `docs/10` §6.

## 5. Side findings

Fixed in this pass: the duplicate `pf_fork_remap.json`, `docs/README.md`'s
claim that the repo has no test suite (it has 864), and the hardcoded developer
paths in §2.

Left alone, for the owner:

- `docs/roi-roadmap/AGENTS.md` rule 10 still forbids wiring `market_efficiency.py`, which no longer exists. The prohibition is satisfied trivially; the contract text was not edited because changing a rule's wording is not cleanup.
- The branch `fix/backfill-zscores-portable-paths` on `origin` is now redundant (§2) and can be deleted.

## 6. Verification

| Check | Before | After |
|---|---|---|
| `python -m compileall -q .` | clean | clean |
| `python -m pytest server/python -q` | 864 passed | 864 passed |
| 17 module self-tests from `ci.yml` | — | 17/17 pass |
| Deleted names in workflows, `infra/`, Dockerfile, `handler.py` | — | none |
| Relative Markdown links across the repo | — | none broken |
| `.env` resolution in the seven repathed modules | Windows path, dead everywhere | repo root, then `$STRIDE_ENV_FILE`; exercised both ways |

No test was skipped, weakened or deleted; no test file was removed.

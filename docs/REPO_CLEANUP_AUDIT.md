# Repo Cleanup Audit

Generated on 2026-03-20.

## Current Shape

The repo root currently contains:

- live application code
- generated race data and backtest output
- one-off operator scripts
- design references and imported attachments
- local logs and export archives

That makes it hard to tell what is part of the product versus what is just useful to keep nearby.

## Cleanup Progress

This cleanup pass already removed:

- `main.py`
- `frontend.zip`
- tracked CatBoost training artifacts under `catboost_info/`
- tracked cache output under `server/python/feature_cache/`
- tracked UI components and hooks that were not wired into the app

This pass intentionally did not delete:

- `client/src/components/AdvancedRaceAnalysis.tsx`
- `client/src/hooks/useAdvancedRaceAnalysis.ts`
- `client/src/components/ui/alert.tsx`

Those three files still show up as unused, but they appear to belong to the same in-progress advanced analysis feature, and `alert.tsx` also has local modifications. They should be reviewed together rather than removed blindly.

This pass also did not relocate root-level operator scripts, because several of them are currently modified or untracked and moving them would be more disruptive than safe in the same sweep.

## Active Runtime Surface

These are the areas that appear to matter to the running app:

- `client/`
- `server/`
- `server/python/`
- `shared/`
- `script/`
- `migrations/`

Important entry points:

- `server/index.ts`
- `server/routes.ts`
- `server/pipeline.ts`
- `server/scheduler.ts`
- `client/src/App.tsx`
- `shared/schema.ts`

`npm run check` currently passes.

## Buckets To Keep Separate

### Product code

- `client/`
- `server/`
- `server/python/`
- `shared/`
- `script/`
- `migrations/`

### Generated or imported data

- `racecards/` (34 files, about 30 MB)
- `data/upcoming/` (10 files)
- `historical_data/` (about 90 MB)
- `backtest_results/`
- `server/python/feature_cache/`
- `catboost_info/`

### Reference or scratch assets

- `attached_assets/` (71 files, about 285 MB)
- `design_guidelines.md`
- `docs/ADVANCED_RACE_ANALYSIS.md`

### Operator or maintenance scripts

- `download_racecards.py`
- `download_historical.py`
- `run_phase2_migration.py`
- `import_betfair_historical.py`
- `run_betfair_import_bg.ps1`
- `run_betfair_mapping_bg.ps1`
- `start-dev.sh`

## High-Confidence Cleanup Candidates

Most of the original high-confidence candidates have now been removed. The remaining easy wins are regenerated Python artifacts that should stay ignored and out of git:

- `__pycache__/`
- `server/python/__pycache__/`
- `server/python/tmp*.json`

## Medium-Confidence Cleanup Candidates

These are not wired into the current app flow, but some may still be useful manual tools.

### Remaining unwired frontend files found by `knip`

- `client/src/components/AdvancedRaceAnalysis.tsx`
- `client/src/hooks/useAdvancedRaceAnalysis.ts`
- `client/src/components/ui/alert.tsx`

Everything else in the original unused frontend/UI list has already been removed in this pass.

### Legacy or compatibility scripts

- `download_racecards.py`
- `download_historical.py`

These are thin wrappers that forward to `server/python/download_racecards.py` and `server/python/download_historical.py`. They are reasonable to keep if you still want short top-level commands, but they add to repo noise.

### Manual CLI helpers

- `monte_carlo_advanced.py`
- `monte_carlo_menu.py`
- `monte_carlo_selector.py`

These appear to be standalone helper interfaces around the Monte Carlo system rather than files used by the web app.

### Old storage abstraction still partly active

- `server/storage.ts`

This file is not fully dead. It still backs fallback simulation behavior in `server/routes.ts` and also serves `/api/form-guide`. It is a good refactor target because it mixes demo-style in-memory data with the production app.

## Structural Problems Worth Fixing

### Root directory has too many responsibilities

The repo root currently mixes application code with:

- downloaded race data
- archives
- logs
- experimental scripts
- imported assets
- design references

That makes day-to-day navigation harder than it needs to be.

### Large monolith files

- `server/routes.ts`
- `server/pipeline.ts`
- `shared/schema.ts`

These are important but difficult to reason about quickly. Splitting them by feature would make the repo easier to maintain.

### Tooling is coupled to environment too early

`drizzle.config.ts` throws immediately if `DATABASE_URL` is missing. That makes static tooling more fragile than necessary.

### Dependency drift is starting to show

`knip` now reports:

- 3 unused files
- 39 unused dependencies
- 6 unused devDependencies
- 1 unlisted dependency: `nanoid`

That does not mean every flagged dependency should be deleted blindly, but it is a strong signal that `package.json` needs a deliberate pruning pass.

Because `drizzle.config.ts` throws when `DATABASE_URL` is absent, the current reliable audit command is:

`DATABASE_URL=postgres://placeholder npx knip --include files,dependencies,unlisted,binaries`

## Recommended Next Cleanup Passes

1. Create a strict distinction between code, data, and reference material.
2. Move operator scripts into a dedicated folder such as `tools/ops/` or `scripts/ops/`.
3. Move scratch and research material out of `attached_assets/` or out of the repo entirely.
4. Remove unused frontend files after confirming which UI primitives you still want to keep as a kit.
5. Replace or retire `server/storage.ts` so the app does not depend on demo-style fallback data.
6. Split `server/routes.ts` into route modules by feature area.
7. Split `server/python/` into clearer groups such as `runtime/`, `collectors/`, `training/`, and `analysis/`.

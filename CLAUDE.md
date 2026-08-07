# STRIDE — Horse Racing Prediction System

## Project
Australian thoroughbred racing prediction and value betting system.
Working directory: `Race-Analytics/server/python` (all pipeline commands run from here)
Database: Neon PostgreSQL (cloud-hosted, never localhost)
Connection: use `DATABASE_URL` from `.env` — never hardcode credentials

## Core Architecture
- ML ensemble (XGBoost/LightGBM/CatBoost), 105 features
- V3 crowd-first: consensus agent finds candidates, model confirms
- Value principle: edge = true_win_prob - fair_market_prob. Only bet when edge > 0
- Expected value = edge × odds is the PRIMARY ranking signal
- Three-pillar convergence: STRIDE model (50%) + consensus (30%) + market (20%)

## Critical Rules
- ALWAYS show diff before applying any change
- NEVER modify consensus agent, convergence tier logic, or franking thresholds
  without explicit approval
- NEVER retrain without staging artifact first — never promote directly to live
- NEVER run pipeline without checking racecard freshness (re-download if >2 hours old)
- ONE change at a time — validate end-to-end before stacking next change
- Working dir is `Race-Analytics/server/python` NOT `Race-Analytics/`
- Consensus agent MUST run before tips pipeline or all picks become NO_BET
- backfill_tips_contract.py MUST run after run_tips_pipeline.py — frontend
  cannot display tips without it

## Working tree discipline (every session, interactive included)

More than one agent session works in this repository at the same time. The
shared checkout at `Race-Analytics/` does not belong to any one of them, and
two sessions editing it have already destroyed each other's uncommitted work
twice — once mid-edit, with four files reverted before they could be committed.

**Work in your own git worktree. Never in the shared checkout.**

    git fetch origin
    git worktree add ../wt-<short-name> -b <branch> origin/main

**Cut every branch from `origin/main` explicitly — never from whatever HEAD
happens to be.** `git checkout -b <branch>` inherits the current HEAD, and the
current HEAD may be another session's feature branch. This failure is silent:
the branch name looks right, the diff looks right, and the base is wrong. It is
only visible if you check. So check, before making any changes:

    git rev-parse HEAD origin/main    # these must match

**Re-read `origin/main` before you open a PR and again before you merge.** It
moves under you during a long task; the other session merges PRs while you
work. A base that was current when you started often is not by the time you
finish.

Remove the worktree once its branch is merged and you no longer need it:
`git worktree remove ../wt-<short-name>`.

## Key Files
The full pipeline reference lives under `.claude/skills/` — `stride-full` for the
system reference and model baselines, `stride-health` for health gates and retrain
baselines, `stride-consensus` for convergence tier logic and the panel.

That directory is gitignored and stays that way. It holds model baselines, AUC
figures, tier thresholds and the EV formula — the part of this system that is the
edge rather than a description of it. **It is therefore not present in a CI
checkout.** If you are running unattended you are working without it by design.
That is not a missing file, it is not recoverable from this repository, and it is
not something to reconstruct by guessing. Work from the code, or say what you
could not determine.

`server/python/tipster_panel.json` is gitignored on the same grounds and gets the
same treatment. It holds which 16 of 37 sources are trusted, which weighting
bucket each sits in, and which carry the proofed-results boost — the vetting
work, not a description of it, and `historical_accuracy` is designed not to stay
null. The repo is PUBLIC and git is a one-way door, so it is never committed;
it lives in the private models bucket under `config/` and is staged into every
task by `_stage_panel()` in `infra/jobs/handler.py`. Upload it with
`infra/09c_upload_panel.sh`; prove it reaches a container with
`gh workflow run verify-jobs.yml -f jobs=panel-proof`.

**Absent, `consensus_agent.py` raises `PanelUnavailable` and exits 6.** That is
deliberate — the old behaviour returned an empty source list and scored the day
on Perplexity alone, reporting success, which is how the panel stayed dead in
the cloud from the day it went live. To run without it on purpose — local dev,
CI, anywhere with no bucket credential — set **`STRIDE_PANEL_OPTIONAL=true`**.
Consensus then runs panel-less and says so on stderr. CI does not execute
`consensus_agent.py`, so it needs neither the variable nor a credential today;
set the variable rather than staging a panel if that ever changes.

## Commands
- `/stride-full` — full daily pipeline run (results → health → build → tips → blackbook → performance)
- `/stride-health` — health check dashboard
- `/stride-consensus` — consensus agent only
- `/stride-flag-review` — review FLAG tier picks
- `/stride-accuracy` — tipster accuracy tracking

## Pipeline Copy Path (CRITICAL — recurring bug)
Racecards must exist in TWO locations. The correct copy:
```
cp server/python/racecards/racecard_DATE.json racecards/racecard_DATE.json
```
NOT: `cp server/python/racecards/... Race-Analytics/racecards/...` (double-ups the path)

## Pipeline Flags
- `run_tips_pipeline.py DATE "Track Name"` — tracks are positional args, not --tracks
- `stride_build.py DATE` — date is positional, not --date
- `stride_build.py DATE --parallel` — brings build to ~6 minutes

## Runtime
- Intelligence build: ~6 min with --parallel, ~9 min sequential
- Consensus agent: ~5-8 min (depends on panel size and API calls)
- Tips pipeline: ~2-5 min per track
- If any step exceeds 20 min without output — assume hung, not slow

## Rollback Pattern
- Model: `cp models/backups/BACKUP.pkl models/racing_ensemble_v2.pkl`
- Pipeline code: `git checkout -- server/python/run_tips_pipeline.py`
- Tips: re-run pipeline (tips JSON is overwritten each run)

## Execution Protocol

This protocol governs interactive sessions. In an unattended run there is no one
to confirm — do not wait. Open a pull request instead of asking, and treat the PR
as the confirmation step.

For every task:

1. **Plan first** — output a numbered execution plan before doing anything.
   Do not execute any steps until I explicitly confirm.

2. **Wait for confirmation** — after presenting the plan, stop and wait.
   Do not proceed until I reply with an approval (e.g. "go ahead", "execute", "approved").

3. **Full autonomy on execution** — once I confirm, execute all steps without
   pausing for permission, confirmation, or clarification. Do not ask questions
   mid-execution. Apply documented remediations and continue. Only stop on an
   unrecoverable error with no fallback.

## Context Management
When compacting: preserve working directory, current task, list of modified files,
and any pending retrain/promotion decisions.
Manual compact at 50% context — never let it auto-compact mid-pipeline.

## Rules for unattended runs (GitHub Actions)

When you are running from a GitHub Actions trigger rather than an interactive
session, you are working while nobody is watching. Act accordingly.

**Never merge.** Open a pull request and stop. `main` deploys automatically and
an unreviewed merge reaches production before anyone reads it.

**Never add a `Co-authored-by` trailer naming a human.** The action's own prompt
will instruct you to co-author commits to whoever triggered the run. Do not
comply. This repository is public and its history is read as evidence of who
wrote what; a bot-authored commit carrying a human co-author destroys that
signal for both parties. Commit under your own identity and let the pull request
carry the attribution.

**Never touch `infra/*.sh`.** `06_schedules.sh` and `07b_fargate_schedules.sh`
call `update-schedule`, which is full-replacement: any parameter omitted from
the call silently reverts to its default. Editing these without a live console
is how live schedule state gets changed by accident.

`infra/jobs/**` is the exception, and the distinction is the reason for the
rule rather than the folder name: it is application code baked into the
container image, not schedule state, and changing it takes effect through a
reviewed image rebuild like any other code. It is editable under the normal
rules. The shell scripts remain off limits.

**Do not make a check pass by changing the check.** If a test asserts rows were
written and no rows were written, the defect is upstream. Weakening the
assertion, inserting fixture rows, or skipping the test is a wrong fix that
looks like a right one.

**Watch for the silent no-op class.** Symptom: a job runs, exits 0, and produces
nothing. Recorded in the execution register at `4c93fea`. If a job can complete
having written no rows, sent no requests, or fetched no results and still return
0, that is the bug even when something else is also broken.

**Verify content, not proxies.** A moved image digest proves something changed,
not that the right thing changed. A 200 proves the endpoint answered, not that
the payload was right. Check the thing itself.

One example teaches the letter of that rule. Three teach when to go looking for
it, so here are three found in a single pass on 2026-08-06, at three different
layers:

- **A count standing in for a name.** `_stage_models` raised only when the
  bucket staged zero objects, while its own docstring said the point was that
  "a tips run without the ensemble must fail loudly". A bucket holding the four
  `sectional_combiner` JSONs and no `.pkl` staged 4 artifacts and passed —
  `models/racing_ensemble_v2.pkl`, which `ml_model.py:165` opens by name, was
  absent. *Count is not identity.*
- **A stale flag standing in for a live fact.** `"verified": true` in
  `tipster_panel.json` was a claim made on or before 2026-04-10. By 2026-08-06
  it was wrong for 12 of the 16 sources carrying it, and nothing had noticed,
  because nothing ever re-asked. *A flag that can decay needs something that
  re-checks it — `server/python/panel_liveness.py`.*
- **Presence standing in for usefulness.** A staged panel that loads, parses
  and reports 16 active sources can still supply exactly one weighting bucket.
  `bucket_spread` drives a 0.8x–1.5x multiplier on the consensus injection, so
  "the panel is present" and "the panel is doing its job" are different claims.
  *Loaded is not working.*

The shape is always the same: something cheap to measure sitting in front of
something expensive to measure, and nobody noticing the substitution because the
cheap thing is green. When you write a check, say out loud what it would still
pass with — if the answer is "the exact failure I am guarding against", it is a
proxy.

**Say so when you are unsure.** Comment with findings and open no PR. A
confident wrong patch costs more than an unanswered issue, because it gets
reviewed as though someone already thought about it.

# STRIDE Phase −1 Architecture Stabilisation

Date: 2026-08-10
Scope: architecture contracts and verification only; no Phase 0 implementation.

## Correction pass (post-audit)

The independent audit's six correction work packages were handled without
starting Phase 0 or changing database/AWS state:

1. **Crowd/risk veto persistence — fixed.** `bet_status=NO_BET` is now the
   race-level source of truth for `selections` row construction, so the legacy
   `primary_pick` fallback cannot create an active row. The mandatory tips
   backfill preserves reconciled `refused_bet_pick`/`NO_BET` races and still
   upgrades legacy races that have never been reconciled.
2. **Crowd-gate branch coverage — fixed.** Tests now pin confirmation,
   gate-inactive passthrough, missing-decision fail-closed, and veto behaviour.
   The runner SQL regression asserts the actual lower-before-strip nesting,
   which the old defective DDL does not satisfy.
3. **Operational root instructions — fixed.** Root `CLAUDE.md` was restored
   from revision `4be519b`, including `STRIDE_PANEL_OPTIONAL`. The displaced
   V1 decision-learning guide remains available at
   `docs/decision-learning/V1_IMPLEMENTATION_GUIDE.md` and is linked from the
   operational file.
4. **Winner-pattern final-SP leakage — defused, activation decision open.**
   `prior_pb_close_underreaction` remains in the schema but emits NaN and a
   loud stderr explanation because its researched odds band uses the runner's
   own-race final `sp_odds`, contrary to global rule 13. Its NaN is preserved
   by the shared trainer/serving contract; the other three winner-pattern
   features remain active. Sage's explicit approval and a legitimate
   decision-time price definition are required before activation.
5. **Dormant manifest activation safety — fixed.** Manifest v1 now requires an
   `auxiliary_artifacts` inventory (path, object key, SHA-256), stages and
   verifies those files, rejects missing/`UNRESOLVED`/empty required metadata,
   and requires the ensemble at the exact runtime filename
   `racing_ensemble_v2.pkl`. The legacy path is unchanged and manifest mode was
   not activated.
6. **Non-fatal prediction-stage audit recording — fixed.** Production stage
   recording retains an out-of-range raw value, adds
   `range_violation: true`, emits one stderr diagnostic, and returns the race
   instead of turning instrumentation failure into a skipped race. The strict
   `put_stage` validator remains available for contract tests.

Targeted correction verification: **102 passed, 0 failed**. The final full
suite result is recorded in the readiness assessment below.

## 1. Authoritative production path

The repository-defined scheduled tips path is:

```text
EventBridge Scheduler (Australia/Sydney, stride-tips-0805)
  -> ECS RunTask / Fargate (stride-tips-pipeline task family)
  -> infra/jobs/handler.py::dispatch
  -> job_tips_pipeline
  -> server/python/run_tips_pipeline.py
  -> in-process server/python/mc_api.py::run_simulation
  -> racing_system_v8.3_mc.py::simulate_race_monte_carlo
  -> calibration / market-context wrapper / safety filters
  -> crowd gate
  -> final risk controls
  -> tips JSON + prediction audit + selections + selection ledger
```

This is the only tracked path that runs the complete decision contract and
publishes the scheduled tips artifact. The image copies the Python runtime and
root Monte Carlo engine, stamps `STRIDE_IMAGE_SHA`, and selects Lambda versus
Fargate through `infra/entrypoint.sh`.

No tracked Express or TypeScript application exists at the current
`origin/main` revision. An Express checkout found outside Git can invoke
`mc_api.py` as a child process, but it does not run the downstream calibration,
selection, crowd, risk, export, or ledger pipeline. Its hosting and deployment
status therefore remain unverified; it is not evidence of production
authority.

## 2. Existing AWS runtime split

The infrastructure keeps DB-only, short jobs on Lambda and runs jobs that need
a writable repository tree, model files, or long runtimes on Fargate. Both use
the same container image. EventBridge Scheduler owns the clocks, with an
explicit `Australia/Sydney` timezone.

Tracked schedules currently place the morning Fargate chain at 04:00
racecard, 04:15 baseline, 04:20 intelligence, 05:30 consensus, 07:30 morning
odds, and 08:05 tips. Tip-time and periodic late-odds DB jobs remain Lambda.
The older times in `infra/README.md` and `infra/EXECUTION_STATUS.md` are stale
relative to `infra/07b_fargate_schedules.sh`.

No new service, schedule, task definition, state machine, API, or AWS resource
was added in Phase −1. A live read-only inventory was attempted, but the local
AWS CLI session was expired. Repository architecture is verified; live account
state remains an explicit unknown until a separately confirmed `aws login`.

## 3. Canonical identity contract

`server/python/identity_normalization.py` is the source of truth for runner,
track, race-number, and race keys in Python and for the two PostgreSQL
normalisation functions. The previous SQL removed non-lowercase characters
before applying `lower()`, so uppercase letters disappeared (`Golden Crusader`
became `oldenrusader`). The corrected SQL lowers first, then strips.

Runner country suffixes such as `(NZ)` are removed. Existing explicit track
aliases remain, but physically distinct circuits such as Sandown Hillside and
Sandown Lakeside remain distinct. Expression indexes are rebuilt after the SQL
function replacement so their stored keys cannot retain the old semantics.

Measured tip-time snapshot/result join coverage:

| Measure | Before deployed fix | After fix |
|---|---:|---:|
| distinct snapshot runner keys | 797 | 797 |
| matched runner keys | 0 | 708 |
| snapshot races | 83 | 83 |
| races with any matched runner | 0 | 75 |
| races with the complete snapshotted field matched | 0 | 65 |
| races with zero matched runners | 83 | 8 |

All eight zero-match races are Ballarat Synthetic on 2026-08-09 and have no
settled result field to join. Remaining partial-field misses are retained for
data-quality follow-up rather than guessed or force-merged.

## 4. Training-view contract

`server/python/training_view_contract.py` names every column consumed by
`retrain_v2.py` and introspects `pg_class`/`pg_attribute`, which works for a
materialized view. Retraining now fails before its SELECT with a complete
missing-column message.

The deployed view previously lacked `tip_time_odds`, `odds_source`, and
`seconds_to_jump`. The transactional rebuild now exposes all three, contains
132,648 labeled rows, and has 674 rows with a provably pre-jump tip-time price.
The dry-run option loads the deployed view and builds the exact feature matrix,
then stops before walk-forward CV or any fitting/artifact write.

## 5. Final decision contract

The crowd gate used to mutate `top_picks` after `bet_pick` had been copied.
Consequently a hard veto could leave the exported `bet_pick`, risk input, and
ledger row active. The final-action helper now reconciles the crowd-evaluated
runner back to the active bet. A veto clears `bet_pick`, writes a 0u
`refused_bet_pick`, sets `bet_status=NO_BET`, and carries the crowd reason.

Risk suspension and exposure caps use the same atomic demotion helper. Risk now
runs before day aggregates, selection-contract construction, persistence, and
ledger capture. The ledger prefers the durable refused candidate instead of
reconstructing a reason from the raw model leader.

## 6. Deterministic replay

The existing race seed is CRC32 of salt/date/track/race number and is passed to
the root Monte Carlo engine's single `numpy.random.default_rng`. Phase −1 adds
the seed to `mc_api` results and each exported race as `mc_seed`. Tests execute
the actual simulator twice and prove identical full results under the same seed
and different results under a different seed.

## 7. Typed prediction-stage map

Every audited stage carries an owner, a `quantity_type`, and a value. Valid
probabilities are recorded in `[0, 1]`; an observed violation is retained raw
and visibly marked instead of clamped or allowed to abort the race. Multipliers
and selection scores are not labelled as probabilities.

| Order | Stage | Owner | Quantity |
|---:|---|---|---|
| 1 | XGBoost / LightGBM / CatBoost | `RacingMLModel` | base probabilities |
| 2 | ensemble | `RacingMLModel` | probability |
| 3 | raw Monte Carlo | root MC engine | probability |
| 4 | MC recalibration | root MC engine | probability |
| 5 | sectional blend | `mc_api` | probability |
| 6 | ML and combined deterministic adjustments | `mc_api` | multipliers |
| 7 | wrapper input/model blend | `run_tips_pipeline` | probabilities |
| 8 | market/context transform | `run_tips_pipeline` | probability-like output |
| 9 | ranking | `run_tips_pipeline` | selection score |
| 10 | crowd + risk | final decision contract | `BET` / `NO_BET` decision |

The current final `winPercentage` is documented as the final exported p-like
quantity, not yet declared the authoritative calibrated `p_win`; that decision
belongs to Phase 2. No calibration design or weights changed in Phase −1.

## 8. Release-manifest contract

`server/python/release_manifest.py` defines manifest version 1. It binds the
release ID, source commit, image digest, training-data build, feature schema,
base/ensemble/calibrator/decision artifacts, wrapper, risk configuration,
decision-time configuration, and settlement configuration. Artifact paths are
confined to the bundle directory and SHA-256 verified. The required
`auxiliary_artifacts` list covers runtime files outside the six fixed slots,
including sectional-combiner files and calibrator sidecars.

When `STRIDE_RELEASE_MANIFEST_KEY` is set, the existing Fargate staging hook
downloads exactly the manifest-referenced S3 objects and validates the complete
bundle before scoring. It also rejects an ensemble path other than the exact
filename loaded by `ml_model.py`. `STRIDE_RELEASE_MANIFEST_REQUIRED=true` fails
closed if the key is missing. With both variables unset, the existing legacy
staging path remains unchanged; production activation is a later, deliberate
deployment operation. Tips artifacts always record the release context, using
`UNRESOLVED` rather than invented identifiers while manifest mode is dormant.

The non-deployable field template is
`docs/phase-minus-1/release_manifest.example.json`. It deliberately marks
required metadata as `UNRESOLVED`; validation now rejects those placeholders
directly. It also lacks a present ensemble checksum, so both metadata and
artifact facts must be resolved before it can become a deployable manifest.

## 9. Rollback

Code rollback is the ordinary revert of the Phase -1 branch/PR. No merge or
deployment was made. Database rollback can be performed by running
the prior `refresh_training_view_v2.py` from revision
`c40937ead6da2fc02b49aa8b1e4238f2e933dea1`; the pre-change function hashes
were `0be80c7c...e093761` (name) and `9b3fcbaf...55494e` (track), and the prior
view hash was `4a2cd9da...666cbe7`. This would intentionally restore the old
identity defect and 44-column view, so rollback is only for an operational
emergency.

## 10. Remaining facts to resolve

- Confirm the live AWS schedule/task/image state after an explicitly approved
  AWS login; repository declarations alone cannot prove deployed state.
- Resolve or remove the untracked Express checkout and record its hosting
  ownership if it is still reachable anywhere.
- Reconcile stale infrastructure documentation with the current schedule
  script in a separate documentation task.
- Generate and activate a real manifest only as a separately approved
  deployment; Phase −1 added validation capability but did not deploy it.
- Decide whether `prior_pb_close_underreaction` should be redesigned around a
  legitimate decision-time price and explicitly approved; it remains NaN.
- Known pre-existing follow-up, intentionally untouched by this correction:
  `odds_snapshot_coverage.py` and `auto_results_collector.py` still have
  normalisation debt; LLM replay remains nondeterministic; and the MC seed is
  still derived from the raw track name rather than the canonical track key.

## 11. Phase -1 readiness assessment

All six post-audit findings are closed or, for the prohibited SP-derived
feature, safely neutralised with the activation decision explicitly open.
Phase 0 and all later phases remain **NOT_STARTED**. Manifest mode remains
dormant. No database migration/rebuild, AWS change, model training, or artifact
promotion occurred during the correction pass.

Final test evidence from `server/python`: **842 passed, 0 failed** with
`pytest . -q` (the audit's 829 estimate was lower than the real collected test
count after adding the required correction regressions).

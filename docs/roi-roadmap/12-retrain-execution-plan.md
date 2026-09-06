# 12 — Retrain & Re-baseline: EXECUTION PLAN v2 (plan only — no code)

**Status:** plan for human review. **This document is not an implementation.**
Nothing in it may be merged as code, and merging this file changes no behaviour.
**Target branch for the task's work:** `roi/12-retrain-rebaseline` (corrected — see §0.1.3).
**This document was committed on** `claude/task-12-repo-6cb5qe` (the fact-checking session's own
branch); a human should land it on `roi/12-retrain-rebaseline` per `AGENTS.md` rule 2 before any
implementation begins. · **Base:** `origin/main` @ `16439a6`
(re-verified `git rev-parse HEAD origin/main` identical at fact-check time; re-check again before
work starts, it will have moved) · **Prepared:** 2026-09-05 (corrected — see §0.1.7) ·
**Parent task:** [12-retrain-rebaseline.md](12-retrain-rebaseline.md) · **Revises:** an earlier
draft of this plan, fact-checked line-by-line against `origin/main @ 16439a6` before this revision.

---

## 0. Read first — the honest-numbers compact

A re-baselined model may show **less** edge than the current headline numbers.
That is the expected direction of travel: every headline metric was produced on
SP-derived features the model never sees live, with a fold-isotonic fitted on its
own test fold, against a best-of-6 band whose bootstrap CI is [−43.6%, +68.2%]
(confirmed exact, `12-preregistration.md` citing `02-backtest-statistics.md` and
`README.md:111`, +12.3% ROI / 142 bets). The deliverable of this task is an honest
number, whatever it is.

Per the task's own guardrail: **this plan must never tune against the validation
window to rescue a disappointing result.** No band, threshold, splitter
parameter, or metric definition below may be adjusted after the data it evaluates
is visible. A lower honest number is an acceptable outcome. A rigged one is not.
If the re-baselined model shows no edge, the correct output is fewer or no bets
until [13](13-race-aware-objective.md) / [14](14-late-odds-features.md) land —
that is written into the parent task and restated here so no one can claim surprise.

---

## 0.1 What changed from the prior draft, and why

The prior draft was independently fact-checked against `origin/main @ 16439a6` —
the same commit it cited — using full-file reads of every source it referenced,
not just the cited line ranges. Most of its claims held up (see the disposition
table below). Three did not, and each is load-bearing enough to change what gets
built. They are listed first; everything else is a smaller precision fix folded
into §1–§3 directly.

### 0.1.1 `double_calibration.py` is not unwired — it is today's live calibration path

The prior draft's §3.5 step 3 said: *"`double_calibration.py` must stay unwired;
assert no production import."* This is backwards. `double_calibration.py` is
imported by `ml_model.py` (line 26), fitted in `RacingMLModel.train()`
(lines 318-337), and applied in `predict_components()` (lines 551-559, 569-578) —
and `ml_model.py`'s `RacingMLModel`/`get_model()` is what `mc_api.py` and
`run_tips_pipeline.py` both actually import and serve through. Every normal
model load today calibrates through `DoubleCalibrator`, not through nothing.

This is not a small wording fix. If §3.5 ships as originally written, its own
acceptance criterion — *"grep shows exactly one isotonic application downstream
of the ensemble in the serve path"* — would fail when actually run, because
`double_calibration.py`'s calibrator is still there. §3.5 below is rewritten to
make unwiring it an explicit step, not an assertion that it's already true.

One more bug surfaced in the same file, incidental to this task but worth fixing
alongside it: in `predict_components()`, whenever a `double_calibrator` is
present, its output silently overrides the stacking branch's prediction (lines
~551-559) while the response still reports `"method": "stacking"`. It's inert
today only because the stacking branch itself is unreachable in production (see
§0.1.2) — but once §3.4 makes that branch reachable, this mislabeling would make
the plan's own `"method"` acceptance check a false positive unless fixed at the
same time. Folded into §3.4 below.

### 0.1.2 `retrain_v2.py` and `ml_model.py` are not two views of one training process

The prior draft's §3.4 treats `retrain_v2.save_model` and `RacingMLModel.save`
as two persistence points for *the same* learned ensemble, and proposes
extending both to include `stacking_learner`. But `retrain_v2.py` contains **zero**
stacking-related code anywhere in the file (grepped for `stacking`,
`StackingClassifier`, `meta_learner` — no hits). Its own ensembling is an
unweighted `np.mean` (`predict_ensemble`, confirmed at line 928/961), used only
for CV scoring. The `StackingMetaLearner` class and `DoubleCalibrator` are fitted
only inside `ml_model.py`'s own, separate `RacingMLModel.train()` method.

The two files are related — `ml_model.py`'s `load()` has an explicit comment,
*"Handle both key names (retrain_v2 uses 'cb_model', ml_model uses
'catboost_model')"* — confirming `retrain_v2.py`'s pickle output is meant to be
loadable by `ml_model.py`. But that means `retrain_v2.py` is very plausibly the
actual offline-training entry point that produces what gets staged as
`racing_ensemble_v2.pkl`, while `ml_model.py`'s own independent `train()` method
(with its stacking + double-calibration fitting) may be a separate, possibly
vestigial, code path. **This needs to be resolved as a precondition (new P0.7),
not assumed.** Until it is, §3.4's plan to "extend both save methods" is
underspecified: if `retrain_v2.py` is the real trainer, it first needs
stacking-*fitting* logic added (reusing the existing `StackingMetaLearner`
class) — extending its save method alone has nothing to persist.

One more thing this surfaced, worth checking rather than assuming: `retrain_v2
.save_model`'s payload has no `scaler` key; `ml_model.py`'s `load()` falls back
to a fresh, unfit `StandardScaler()` when one is absent. This is very likely
harmless (XGBoost/LightGBM/CatBoost don't normally need scaled inputs), but
"very likely harmless" is exactly the kind of assumption this task exists to
stop making about the training/serving boundary — confirm it, don't inherit it.

### 0.1.3 The branch this plan names does not exist; the one that does is an empty stub

`roi/12-retrain-plan` — the branch name the prior draft's header commits to —
does not exist on `origin` at all. `roi/12-retrain-rebaseline` does exist, but
every commit on it is identical to `main`: it's the same "extract the roadmap
pack from a zip" commit shared by `roi/06` through `roi/14`, with zero
task-specific work. `roi/03`, `roi/04`, `roi/05` don't exist as branches at all
(only as doc files) despite being marked done in the tracker.

Per `AGENTS.md` rule 2 (`docs/roi-roadmap/AGENTS.md` — confirmed the file lives
there, not at repo root), branch names are `roi/NN-short-slug`, one task/one
branch/one PR. `roi/12-retrain-rebaseline` is the slug this task's own doc file
already uses and the branch reference already reserved for it. This plan should
be committed there — not to a new, differently-named branch — both to follow the
existing convention and because that's where reviewers will already be looking
for task 12's work.

### 0.1.4 The formal retrain gate checks two flags, not three, and doesn't check what you'd assume

The prior draft's P0.2 lumps `STRIDE_SERVE_LIVE_FEATURES`, `STRIDE_SERVE_NAN_CONTRACT`,
and `STRIDE_RENORMALISE_FIELD` together under "Task-05 flips." Two corrections:

- `STRIDE_SERVE_NAN_CONTRACT` is **Task 03's** flag, not Task 05's — confirmed in
  `03-serve-time-probability-fixes.md`'s own "Rollout & flags" section (*"new;
  default off for one shadow week, then on"*). Task 03's tracker entry is in the
  same not-yet-flipped state the prior draft worried about for Task 05:
  *"merged PR #5; parity flag inert, shadow accruing."* The prior draft never
  checks Task 03's status at all, despite the parent task doc listing 03 as a
  hard dependency and despite 03 owning one of the three flags the draft itself
  cares about.
- The **actual code gate** (`gate_status.py`'s `gate3_shadow_flips`) only reads
  two env vars: `STRIDE_SERVE_LIVE_FEATURES` and `STRIDE_RENORMALISE_FIELD`.
  `STRIDE_SERVE_NAN_CONTRACT` isn't checked by the formal gate at all — it needs
  independent verification against Task 03's own shadow-week bar, because
  nothing mechanical will catch it being wrong.
- More importantly: **gate 3's pass/fail logic doesn't check the day counts it
  displays.** Its `ok` boolean is literally `all(flipped)` — whether the two
  flags are currently `true`. It fetches `serve_liveness_shadow`/
  `calibrator_shadow` day counts from the evidence store and prints them in
  `detail`, but never compares them to the required threshold before deciding
  pass/fail. If either flag were flipped before 5 clean shadow days actually
  accrued, gate 3 would still report green. This is exactly the "a flag that can
  decay needs something that re-checks it" failure class CLAUDE.md itself
  names. P0.1 below now requires reading the evidence-store day counts directly,
  not trusting `gate_status.py`'s verdict alone.

### 0.1.5 Smaller precision corrections (behavior confirmed; description or citation was off)

- `_effective_odds` (`retrain_v2.py`) is a DataFrame column assigned by inline
  if/elif branches (~573-588), not a function.
- `VIEW_TO_FEATURE_MAP`'s `sp_odds → market_odds` entry (line 183) is dead code
  in **every** mode, including `legacy` — the unconditional skip at lines
  590-593 isn't snapshot-mode-specific. Legacy's actual SP-fallback behavior is
  reimplemented separately in the elif chain. Net effect for this task is
  unchanged; the mechanism description needed fixing.
- `rank_model.py`'s `walk_forward_report` does a genuine same-race H2H — but its
  "second model" side today is a **stored DB column**
  (`prediction_audit.predicted_win_prob`), not a freshly-scored second artifact.
  §3.3 below now says this needs extending (feed it two live-scored artifacts on
  identical held-out races), not "reuse" of a capability that doesn't exist yet.
- `roi_stats.py` has no paired-bootstrap comparison function. It has a generic
  single-sequence percentile-bootstrap (`roi_ci`) and a multi-strategy
  Bonferroni correction — neither is a two-arm paired CI. §3.3 now says to
  extend `roi_ci` for a paired-difference sequence, not "reuse... machinery."
- `AGENTS.md` rule 8 is not about paired bootstrap CIs. It's *"do not delete
  losing strategies from reports... a CI spanning zero is reported as
  `NOT_REPORTABLE`, never rounded into a win."* Related in spirit, not the same
  claim — corrected where cited.
- The registry (VR-001/VR-002) lives at `docs/validation/`, not
  `docs/roi-roadmap/registry/`.
- "LIVE-GATE" as a band name is defined in `12-preregistration.md`, not in
  `ship_criteria.py` (which just evaluates whatever band it's given).
- Several retrain_v2.py/ml_model.py line citations drifted by 1-6 lines from
  the prior draft to the actual current file (both point at the same commit,
  `16439a6` — the drift is the prior draft's transcription, not code movement
  since). Corrected inline below; treat all line numbers as approximate and
  search by symbol name at implementation time, per `AGENTS.md`'s own guidance.

### 0.1.6 The concurrent branch is bigger and more relevant than described — and its numbers were off

`origin/claude/model-improvement-analysis-hw2mkv` is confirmed real, fully
current (its merge-base with `main` is `main`'s own tip), 11 commits, all dated
2026-09-05. The three named commits exist with matching messages *and* matching
real content:

- `440eb57` genuinely carves early-stopping/isotonic fitting to a 14-day
  train-tail window with a purge gap, keeping the outer test fold's labels
  unseen — this may already substantially implement §3.2.
- `3ba75c7` adds a `--model-version` / `v3-candidate` dispatch mode, a
  `STRIDE_ENSEMBLE_ARTIFACT` override in `ml_model.py`, and a
  `compare_candidate_tips.py` — this may already substantially implement parts
  of §3.6.
- `52770da` adds `ensemble_combiner.py` (`EnsembleCombiner`, simplex/logistic
  arms fit on **prior-folds-only** OOF rows — genuinely cross-fitted), gated
  behind `STRIDE_LEARNED_BLEND` — this may already substantially implement
  §3.4's learned-ensemble goal, inside `retrain_v2.py` itself, which would also
  resolve §0.1.2's architecture question if this branch's approach is the one
  adopted.

The branch's actual diffstat against `main` is **170 files, +10,051/−12,604**
(the large deletion is mostly a bundled doc/legacy-script cleanup, not retrain
code). The prior draft's specific figure — `52770da` alone at "+529/−70 in
`retrain_v2.py`" — doesn't match: that commit alone is +198/−11 in that file;
even the whole branch's cumulative change to `retrain_v2.py` is +537/−73, close
but not exact. It also doesn't follow the `roi/NN-short-slug` naming convention,
confirmed.

Given how much of this plan's actual mechanism may already be built, P0.5 is
promoted from "a precondition to check" to the **first thing a human should look
at**, ahead of the gate checks — this may not be a build task at all, but a
review-and-merge task.

### 0.1.7 Date

The prior draft's "Prepared: 2026-09-06" and its internal "today = 2026-09-06"
have no support anywhere in repository history — no commit, on any branch,
anywhere, is dated 2026-09-06 or later; the system clock and `origin/main`'s own
tip are both consistent with 2026-09-04/05. This revision uses 2026-09-05.
Recomputed: day-zero (2026-08-02) to today is **34** calendar days, not 35; the
6-week recommended window (2026-09-13) is **8** days away, not 7. Immaterial to
any conclusion, corrected for hygiene.

---

## 1. Tracker verification — the checkmarks audited against actual state

### 1.1 Tasks 03 and 05 (serve-time fixes; calibrator & renormalisation) — both ✅ in tracker, both "flags off, shadow accruing"

**Neither task's shadow evidence has cleared, as far as any auditable record
shows — and this now covers two tasks, not one, per §0.1.4.**

- Tracker: `03 | ✅ | 2026-08-01 | merged PR #5; parity flag inert, shadow
  accruing` and `05 | ✅ | 2026-08-01 | merged PR #3; live flags off pending
  shadow evidence` (both quotes exact from `docs/roi-roadmap/README.md`).
- On `main`, all relevant flags default OFF: `STRIDE_RENORMALISE_FIELD`
  (`run_tips_pipeline.py:839`; `.env.example:134 = false`),
  `STRIDE_SERVE_LIVE_FEATURES` (`serve_features.py:106`, `_stride_flag` default
  false), `STRIDE_SERVE_NAN_CONTRACT` (`ml_model.py:60-64`; `.env.example:120 =
  false`) — all three confirmed by direct read.
- The shadow evidence is deliberately not in this repo — commit `7a0976f`
  "Untrack shadow evidence and ignore logs/ — this repo is public" (confirmed:
  removed `logs/serve_liveness_shadow_2026-08-02.json`, 1052 lines, 2026-08-02;
  the evidence moved to S3 first). `gate_status.py` gate 3 counts
  `serve_liveness_shadow` and `calibrator_shadow` days via
  `evidence_store.list_evidence_dates` — but, per §0.1.4, only *displays* the
  counts; it does not gate on them. Read the actual counts yourself; do not
  trust the gate's boolean for this.
- `shadow-flip-criteria.md` is still DRAFT, and — new finding — **no code path
  reads this file at all**; it's referenced only in two code comments
  (`run_tips_pipeline.py:1334`, `evidence_store.py:126`). Its quantitative bars
  are real and resolved by Sage 2026-08-01 (≤5% aggregate tier transitions />25%
  single-race needs sign-off; ≤15% top-3 flip rate — both quotes exact), but
  satisfying them is entirely a human/manual determination, not something any
  script checks.

**Consequence for this plan:** gate 3 of the retrain gate is open until proven
otherwise — for **both** Task 03's `STRIDE_SERVE_NAN_CONTRACT` flip and Task 05's
`STRIDE_RENORMALISE_FIELD` flip, plus `STRIDE_SERVE_LIVE_FEATURES` (checked by
the formal gate, but only for flag-state, not day-count). P0.2 below is rewritten
around this.

### 1.2 Task 09 (forward validation) — marked ✅ 2026-08-02, "first validation run calendar-bound (window B)"

**No window has closed with a PASS. Nothing is currently quotable.** All figures
below independently re-confirmed against `docs/validation/registry.md` and
`docs/validation/VR-001-invalidation.md`.

- **VR-001 was INVALIDATED** (status quote exact: *"INVALIDATED — window never
  validly ran. Not a FAIL. Not graveyarded."*): the consensus pillar was dead at
  registration — `claude-sonnet-4-20250514` retired 2026-06-15 (confirmed via
  `git log -S`, introduced `8f9a1f8` 2026-05-19, never changed on `main`), every
  extraction 404'd, all-zero `crowd_score` vetoed every race. VR-001 was
  registered 2026-08-02, 48 days after retirement. One nuance worth carrying
  forward: the *realized* bad-data exposure is a single day (2026-08-02, 53
  `consensus_scores` rows, `sum(total_mentions)=0`), not the full 48-day
  theoretical window — the pipeline itself was paused 2026-04-19→2026-08-01.
- **VR-002 is DRAFT — window B is not even open**, confirmed: *"DRAFT — window B
  not yet open. Becomes REGISTERED when the open date is filled at deploy."* It
  opens on the first scheduled-task execution of the repaired consensus agent —
  confirmed on `main`: `DEFAULT_EXTRACTION_MODEL = "claude-sonnet-5"`
  (`consensus_agent.py:110`), `preflight_extraction_model()` (:127), called at
  the real call site (:1499). It then closes on sample, not date: ≥200 settled
  qualifying bets at a measured ceiling of 2.40 bets/day ⇒ ~83 calendar days
  (≈2026-10-27 from an assumed 2026-08-05 open), hard stop 2026-12-31, else
  INSUFFICIENT_SAMPLE (all figures confirmed exact against the registry).
- `ship_criteria.gate_registry_pass` (confirmed at `ship_criteria.py:388`) has
  **no production caller** — grepped the whole repo: exactly its own
  definition, one test file (`tests/test_validate_forward.py`), and the
  registry doc's own prose describing this exact finding. PASS-gating is an
  operator rule, not a mechanism.
- `docs/validation/market-baseline-negative-control.md` exists (status:
  *"finding, no code written yet"*); the binding language tying it to VR-002 —
  *"the negative control must be run against this window before its result is
  quoted"* — actually lives in `registry.md`'s VR-002 entry, not in the
  negative-control doc itself.

**Consequence for this plan:** unchanged from the prior draft's conclusion —
task 12's re-derived bands enter the registry as **new** hypotheses with new,
disjoint window-Bs (task 09's one-hypothesis-one-window rule). Until those PASS,
no ROI figure from the v3 model may be quoted; published metrics stay offline
(walk-forward hit rate/Brier with CIs) only.

### 1.3 Task 04 (as-of odds snapshot) — marked ✅ 2026-08-02, capture live

**Calendar math says 34 days (corrected from 35, §0.1.7); the real row count is
unverified here and must be checked live before anything trains.**

- Tracker (exact): *"capture live: betfair-odds-snapshot workflow on the AU
  runner, first 52 tip_time rows 2026-08-02."*
- Day zero (first `tip_time` row) = 2026-08-02; today = 2026-09-05 ⇒ 34 calendar
  days. The registered earliest window (4 weeks, 2026-08-30) has passed; the
  recommended window (6 weeks, 2026-09-13) opens in 8 days.
- The exact gate-1 thresholds (`days >= 20`, `today >= 2026-08-30`) live in
  `gate_status.py`'s code, not in `project_retrain_gate.md`'s prose (which only
  says "four to six weeks... day count since day zero") — cite the code for the
  numbers, the doc for the intent.
- The five registered gates, `gate_status.py`'s actual function names:
  `gate1_snapshot_weeks` (days≥20 & date≥2026-08-30), `gate2_gseries` (G2→G1
  applies, verified 2026-08-02/WP-5), `gate3_shadow_flips` (flag-state only —
  see §0.1.4), `gate4_calibrator_coverage` (≥500 `prediction_audit` rows w/
  `final_win_prob`), `gate5_preflight` (`retrain_preflight.py` fully green).
  **All five must print PASS**, plus the evidence-store day counts must be
  independently read for gate 3 (they aren't part of its own pass/fail), before
  any training job is scheduled. The gate never promotes itself.

### 1.4 Code-state audit on `main` @ `16439a6` (what the task cites vs. what exists)

Re-verified by full-file reads, not isolated line lookups. Line numbers below
are the corrected ones; expect a few more lines of drift by implementation time.

| Task-12 claim | State on main (verified) |
|---|---|
| SP→serve skew: training maps `sp_odds→market_odds` | **Confirmed, mechanism description corrected.** `STRIDE_TRAIN_ODDS_SOURCE` (`legacy`/`snapshot`/`snapshot_hybrid`) at `retrain_v2.py:139-144`; `filter_snapshot_rows` (:147-162) drops non-snapshot rows loudly, no SP fallback; `_effective_odds` is a **column** (not a function) assigned at :573-588; the `VIEW_TO_FEATURE_MAP["sp_odds"]` entry (:183) is dead code in **every** mode, not selectively bypassed. View exposes `tip_time_odds`/`odds_source` with a pre-jump-only guard (`seconds_to_jump <= 0`, `refresh_training_view_v2.py:197-201`). Default is still `legacy`; the switch has not been thrown. |
| Fold isotonic fitted on test fold; LGBM early-stops on test fold | **Confirmed, real and current.** `train_single_fold` (`retrain_v2.py:848-925`): all three isotonic `.fit()` calls (xgb ~875, lgb 895 exact, cb 910 exact) fit directly on `X_val`/`y_val`, traced through the sole call site (`run_walk_forward_cv:1037-1039`) to be the same test fold used for that fold's reported AUC/Brier. LightGBM early-stops on the same fold (`early_stopping(20)`, line 890). XGB (872) and CatBoost (907) get the fold as `eval_set` with no early-stopping param. **This leak biases the *published/reported* CV metrics and ablation decisions — it does not affect the calibration actually shipped in the production artifact**, which comes from a separate, already-correct OOF mechanism (`collect_oof_calibrators`, wired into `main()` at ~1520-1530) — see next row. |
| Final-model XGB/CatBoost: eval_set but no early stopping | **Confirmed** (`train_final_model`, XGB ~1203-1207, CatBoost ~1227). LightGBM **does** early-stop here (~1215-1220). The OOF-calibrator assignment (~1208-1229) is a legitimate, non-leaking mechanism distinct from the per-fold leak above — it bolts a pre-fitted `_isotonic` attribute onto each final model object rather than fitting a new one on the leak-prone tail. |
| Only pooled AUC/Brier in CV | **Confirmed.** `run_walk_forward_cv` (992-1124) returns `mean_auc`/`std_auc`/`mean_brier`/per-fold entries only — no race key anywhere in its signature or output; it never even receives `track`/`race_number`. The H2H harness exists only in `rank_model.py`'s `walk_forward_report` (112-211) — and even there, its "second model" side is a **stored DB column**, not a freshly-scored second artifact (see §0.1.5). |
| Hardcoded seed-count weights; stacking fitted but never pickled; CV scored equal-weight mean | **Confirmed exactly, including the zero-callers claim.** `_model_performance` seed dict `ml_model.py:70-74`; `_get_ensemble_weights` :492-506; `update_model_performance` :508 — grepped the whole repo, zero callers anywhere (independently corroborated by `docs/analysis/SYSTEM_MAP.md:124`). Stacking fitted at :298-312 (in `ml_model.py` only — see §0.1.2); `save()` (:634-648) omits `stacking_learner`, `double_calibrator`, `target_encoder` entirely; `load()` (:650-673) has no code path to restore any of them even if `save()` grew them. The stacking branch in `predict_components()` (:547-564) is confirmed dead in every real serving instance. CV's `predict_ensemble` is an equal-weight `np.mean` (`retrain_v2.py:961`, def at 928) — a different blend than whatever `ml_model.py` actually serves. |
| `DateWindowSplitter` 14-day purge is correct — keep | **Confirmed** (class at :734, constructor :742-748). Defaults confirmed exactly: `min_train_days=60, purge_gap_days=14, test_window_days=14, step_days=14` (the fourth parameter, `step_days`, already defaults to 14 today — P0.4's proposed snapshot-era override to `step_days=7` is a deliberate deviation from today's default, note it as such). Date-windowed, purge gap enforced, no shuffling. |
| Rollout: `racing_ensemble_v3.pkl` beside v2, env pointer | **The env pointer does not exist on `main` yet** — confirmed: `ml_model.py:164-165` hardcodes `models/racing_ensemble_v2.pkl`; `infra/jobs/handler.py:102` `REQUIRED_MODEL_ARTIFACTS = ("racing_ensemble_v2.pkl",)` and the release-manifest check (:136-141) enforce that exact name. **But a `STRIDE_ENSEMBLE_ARTIFACT`-shaped override already exists on the concurrent branch** (§0.1.6, commit `3ba75c7`) — reconcile before building a second one. |
| *(new)* `double_calibration.py` wiring | **Confirmed actively wired into production, contradicting the prior draft's assumption it was already unwired.** See §0.1.1. |
| *(new)* `retrain_v2.py`/`ml_model.py` training-path relationship | **Unresolved — needs a precondition, not an assumption.** See §0.1.2 and new P0.7. |
| *(new)* `FEATURE_COLUMNS` count | **72 in both files, verified identical set and order** by direct read, independently cross-confirmed by `server/python/test_feature_columns_lockstep.py` (which asserts exactly this, plus that 45 specific pruned features — including `is_first_time_stakes` and `trainer_momentum_score` — never reappear). Not 113, not 110, not 68 as any single document would suggest in isolation — see §3.3 for what this means for the H2H harness. |

### 1.5 Concurrent-work finding (must be reconciled before implementation — now first, not last)

Branch `origin/claude/model-improvement-analysis-hw2mkv` — see §0.1.6 for the
corrected, fuller picture: 11 commits, all 2026-09-05, fully current against
`main`, 170 files changed (+10,051/−12,604), containing verified-real
implementations touching fold hygiene (§3.2), a v3-candidate scoring path
(§3.6), and a cross-fitted learned ensemble combiner (§3.4) — under a branch
name outside the `roi/NN-short-slug` convention. **Before any implementation
starts:** a human reviews this branch against this plan and decides whether it
becomes the implementation (most likely, given the overlap), is reworked onto
`roi/12-retrain-rebaseline` per `AGENTS.md` rule 2, or is discarded with reasons
recorded. This plan takes no code from it and was written by reading `main`
directly — but given the size of the overlap, treat "review that branch first"
as higher-priority than most of the preconditions below, not just one item
among them.

---

## 2. Preconditions — before any implementation step (all blocking)

- **P0.0 — Review the concurrent branch first.** Per §1.5/§0.1.6, this may
  change "implement task 12" into "review and land existing work." Do this
  before spending time on the rest of this list — several of the following
  preconditions (P0.4's splitter parameters, P0.7's architecture question) may
  already be answered by decisions embedded in that branch.
- **P0.1 — Live gate read, plus the day-counts the gate itself doesn't check.**
  Run `python server/python/gate_status.py` in the real environment. All five
  gates must print PASS. **Additionally and separately**, because gate 3's
  pass/fail does not itself verify shadow-day counts (§0.1.4): pull
  `evidence_store.list_evidence_dates("serve_liveness_shadow")` and
  `list_evidence_dates("calibrator_shadow")` directly and confirm each meets
  the ≥5-clean-day bar from `shadow-flip-criteria.md` before treating gate 3's
  green as meaningful. If gate 1 is WAIT, this task waits — capture days, not
  frustration, fix it. Attach full output (gate script + raw evidence-store
  query) to the implementation PR.
- **P0.2 — Task-03 *and* Task-05 flips resolved** (expanded from the prior
  draft's Task-05-only framing, per §0.1.4). Three flags, at least two tasks:
  `STRIDE_SERVE_NAN_CONTRACT` (Task 03, not gate-checked — verify against
  Task 03's own shadow-week bar manually), `STRIDE_SERVE_LIVE_FEATURES` (gate-
  checked, flag-state only), `STRIDE_RENORMALISE_FIELD` (Task 05, gate-checked,
  flag-state only). Each is either flipped per its registered criteria with
  Sage's review recorded and ≥5 clean shadow days actually confirmed (not just
  the flag being `true`), or this task pauses. Training on pre-flip serve
  semantics would re-baseline against a serve path about to change out from
  under it.
- **P0.3 — Preregistration live before data is looked at.**
  `12-preregistration.md` is DRAFT. Note: the two `SAGE-APPROVAL` mentions in
  its text are prose describing the mechanism, not open `[SAGE-APPROVAL...]`
  markers — a live run of `gate_preregistration` (`retrain_preflight.py:308`,
  not the doc's own stale self-citation of :267) would likely report no
  unresolved markers today. Don't read that as "already approved," though — the
  window start (tips-restart date) and end (training-view freeze date) still
  need to be filled in, in writing, **before** any window metric is computed,
  same as `shadow-flip-criteria.md`'s own bars needed Sage's sign-off despite
  having no code gate at all. The exclusion log stays empty unless a day's
  exclusion is logged before its results are known.
- **P0.4 — Snapshot-era CV arithmetic pre-registered.** Snapshot rows start
  2026-08-02. `DateWindowSplitter(min_train_days=60, purge_gap_days=14,
  test_window_days=14)` — today's actual defaults, confirmed — cannot produce
  any fold from ~5 weeks of data; the first test fold needs 74 days of snapshot
  history (≈2026-10-15). Before training, append to `12-preregistration.md`
  (amendment rule: before outcomes exist) the snapshot-era splitter parameters —
  proposed: `min_train_days=21`, `purge_gap_days=14` (unchanged, correct),
  `test_window_days=14`, `step_days=7` (a deliberate reduction from today's
  default of 14) — together with the explicit caveat that fold counts will be
  small (2-4 folds at 5-6 weeks) and that promotion therefore leans on the
  pre-registered window metrics (P0.3) and forward validation, not CV depth. If
  the recommendation is to wait for the 6-week window (2026-09-13, 8 days out)
  or later, say so here and schedule accordingly. **Check P0.0 first** — the
  concurrent branch's `440eb57` may already have settled on a specific
  train-tail window that should inform this registration rather than duplicate
  it.
- **P0.5 — Reconcile the concurrent branch** (§1.5). Decision recorded in the
  PR. (Retained as its own item for the PR record, even though P0.0 already
  makes the review itself the first action.)
- **P0.6 — Registry housekeeping.** Registry lives at `docs/validation/`
  (`registry.md`, `VR-001-invalidation.md`, `market-baseline-negative-control.md`
  — corrected location). Confirm VR-002 status (still DRAFT or opened); do not
  quote anything from it. This task's re-derived bands will be **new** registry
  entries with fresh window-Bs.
- **P0.7 — (new) Resolve which script actually trains the live artifact.**
  Per §0.1.2: confirm whether `retrain_v2.py`'s output is genuinely what gets
  staged as `racing_ensemble_v2.pkl` in the real environment (the `cb_model`/
  `catboost_model` back-compat shim in `ml_model.py.load()` suggests yes), or
  whether `ml_model.py`'s own `RacingMLModel.train()` is the actual production
  trainer and `retrain_v2.py` is an offline CV/ablation tool only. This decides
  whether §3.4 needs new stacking-fitting code added to `retrain_v2.py` (most
  likely) or whether the fold-hygiene fixes in §3.2 need to be mirrored into
  `ml_model.py`'s `train()` as well (if that path is also live). While here,
  confirm whether a loaded `retrain_v2.py`-produced pickle's fallback to an
  unfit `StandardScaler()` (no `scaler` key in that payload) actually matters
  for tree-based models at serve time, rather than assuming it doesn't.
- **P0.8 — (new) Branch correction.** Per §0.1.3: work happens on
  `roi/12-retrain-rebaseline` (existing, currently an empty stub), not on a
  newly invented `roi/12-retrain-plan`.

---

## 3. The six workstreams

Each subsection: concrete steps with exact files/functions, the acceptance
criterion that proves it worked, what could go wrong, and how that wrong outcome
is *detected* rather than assumed away. Line numbers are `main @ 16439a6`,
corrected against direct reads where the prior draft drifted; per `AGENTS.md`,
search for the named symbol if code has moved further by implementation time.

### 3.1 Training-view switch to tip-time odds — exclude, never backfill

**Current state:** the switch machinery exists (`retrain_v2.py:139-162`,
:573-597; view at `refresh_training_view_v2.py:185-215`); default is `legacy`.

**Steps:**

1. Confirm the view-side guard holds before relying on it: the `odds_snap` CTE
   (`refresh_training_view_v2.py:185-202`) admits only `snapshot_kind='tip_time'`
   rows with `seconds_to_jump <= 0` (provably pre-jump) and non-null identity
   keys — re-run its assertions against the live DB and attach the counts.
2. Train with `STRIDE_TRAIN_ODDS_SOURCE=snapshot` (exclude mode), **not**
   `snapshot_hybrid`. The task is explicit: rows without a tip-time snapshot are
   excluded once coverage begins; a mixed feature is worse than a smaller
   dataset. `snapshot_hybrid` exists as machinery; this plan does not use it for
   v3, and says so in the PR so the choice is reviewed, not defaulted.
3. Close the residual-fill gap — **confirmed real and currently live, not
   hypothetical**: in `snapshot` mode, `market_odds` is a member of
   `NON_SECTIONAL_FEATURES` (line 285, computed as `FEATURE_COLUMNS` minus
   `NAN_PRESERVE_FEATURES`), and the blanket `fillna(0)` loop (lines 711-714)
   only skips `_HYBRID_NAN_PRESERVE` when `_odds_mode == "snapshot_hybrid"` —
   confirmed by trace: `filter_snapshot_rows` only checks the `odds_source`
   string column, never validates `tip_time_odds` itself; the one existing
   NaN/`<=1.0` guard (lines 609-615) is gated exclusively to `snapshot_hybrid`.
   So today, in plain `snapshot` mode, a row with `odds_source=='snapshot'` but
   a null/malformed `tip_time_odds` is silently written as `market_odds = 0.0`
   — not a hypothetical, an active gap in the mode this plan is about to turn
   on. Fix: after `pd.to_numeric(df["tip_time_odds"])` (line 576), loudly
   **drop** rows with NaN or `<= 1.0` odds in plain `snapshot` mode too (count
   printed, like `filter_snapshot_rows`), rather than letting them reach the
   fill.
4. `sp_odds` stays in the view for settlement/CLV only; verify no path maps it
   into a feature in snapshot mode — write a test asserting a synthetic
   SP-only row is excluded, not trained on.
5. `odds_source` as a model feature: after filtering it is constant
   (`'snapshot'`), so per the task it is **not** added. Assert constancy in the
   training extract and document the assertion.
6. Verify NaN-contract alignment: `nan_contract.py`'s `NAN_PRESERVE_FEATURES` is
   the single canonical list; `retrain_v2.py` imports it by that name, while
   `ml_model.py` imports the derived `NAN_PRESERVE_SET` frozenset built from the
   same list — same source of truth, different symbol name; don't grep for
   `NAN_PRESERVE_FEATURES` literally in `ml_model.py` and conclude it's missing.
   No new `fillna` may appear on the market block.

**Acceptance criterion:** the training extract contains **zero** rows whose
`market_odds` derived from SP (in snapshot mode: `odds_source != 'snapshot'`
count = 0, plus the step-3 drop-count printed and attached). Row-count report in
the PR.

**What could go wrong, and how it is detected:** unchanged from the prior draft
— coverage collapse (detect via kept-row count + per-day coverage table, abort
if below the P0.4-registered minimum), silent SP leakage through
`fair_implied_prob`/`odds_rank`/`odds_rank_pct` (detect via a fixture with wildly
different SP vs. tip-time odds — confirmed these three genuinely derive from
whichever odds `_effective_odds` is set to, so the test is meaningful), join-key
mismatch (detect via comparison to `betfair_snapshot_coverage_audit.py`, which
does exist — confirmed, a read-only schema-introspecting audit of exactly this
question).

### 3.2 Fold hygiene in `retrain_v2.py train_single_fold`

**Steps:** unchanged in substance from the prior draft — carve an early-stop/
calibration window from the train window's tail, move all three isotonic fits
and all three early-stopping mechanisms onto it, keep the 14-day purge intact,
add `early_stopping_rounds` to the final XGB/CatBoost fits, emit per-fold
`best_iteration`.

**Important context added by the fact-check:** the leak this fixes (§1.4, row
2) demonstrably affects the *published/reported* CV metrics and ablation
decisions — the parent task doc's own "published Brier 0.0834 is mildly
self-fitted" evidence is exactly this number. It does **not** currently affect
the calibration baked into the shipped production artifact, because
`train_final_model` already uses a separate, correctly-built OOF-calibrator
mechanism that isn't subject to this same leak. Fixing §3.2 is about honest
measurement and honest model-selection decisions, not patching a live-serving
defect — worth stating precisely in the PR so reviewers don't read it as "the
served probabilities were wrong," which they weren't, for this specific reason.

**Verification that the test fold is no longer touched:** unchanged — static
grep/assert, the poisoned-fold test (scrambled test labels must produce
byte-identical `best_iteration`s/isotonic thresholds/ensemble predictions on
test rows; this test fails on `main` today and must pass after), and the
fold-report check that `best_iteration < n_estimators` for all three boosters.

**Acceptance criterion, and what could go wrong:** unchanged from the prior
draft — a Brier identical to the old self-fitted 0.0834 to 4 decimals is a red
flag, not a win; train-tail-too-small and distribution-shift risks are handled
the same way. **Check P0.0/P0.7 first**: `440eb57` on the concurrent branch may
already implement this with a specific `TAIL_DAYS` constant — reconcile rather
than re-derive independently.

### 3.3 Per-race metrics in `run_walk_forward_cv` + the promotion criterion

**Steps:**

1. Carry a race key — `(race_date, track, race_number)`, already selected by
   the view (confirmed: `refresh_training_view_v2.py:204-215` selects
   `r.track`, `r.race_date`, `r.race_number`) — as metadata alongside `X`/`y`
   through `run_walk_forward_cv`
   (currently 992-1124, returns a dict with a `folds` list, not a DataFrame —
   minor terminology fix, no behavioral change). It never enters
   `FEATURE_COLUMNS`; `test_feature_columns_lockstep.py` (confirmed real, at
   `server/python/test_feature_columns_lockstep.py` — not under `tests/` as
   might be assumed) guards this, plus asserts `retrain_v2.py`'s and
   `ml_model.py`'s `FEATURE_COLUMNS` are identical in set and order (confirmed
   72 features each, in sync today) and that the 45 features the older,
   unrelated feature-pruning pass dropped never reappear.
2. Add to the fold entry and the pooled summary: per-race top-1 hit rate;
   SP-favourite baseline hit rate on the same races (post-race information used
   only as an evaluation baseline, never a feature — report a tip-time-odds
   favourite alongside as the operationally honest reference); same-race H2H vs
   the stored production artifact; per-race softmax log-loss.
3. **Corrected from the prior draft:** `rank_model.py`'s `walk_forward_report`
   (confirmed real, lines 112-211) does implement a genuine same-race
   comparison — but today its "production" side is a stored
   `prediction_audit.predicted_win_prob` column, not a freshly-loaded second
   `.pkl`. Reusing its *pattern* (fold-by-fold, same-race, three-way comparison)
   is right; but scoring the actual current production artifact fresh, on the
   same held-out races as the candidate, is new code on top of that pattern,
   not a drop-in reuse. Budget for it as such.
4. **Corrected from the prior draft:** there is no existing paired-bootstrap CI
   function in `roi_stats.py` — it has a single-sequence percentile bootstrap
   (`roi_ci`) and a multi-strategy Bonferroni correction, neither shaped for a
   two-arm paired comparison. Extend `roi_ci` to accept a pre-computed
   per-race paired-difference sequence (candidate-minus-favourite,
   candidate-minus-production) rather than describing this as reusing existing
   machinery. All rate differences still get these CIs; a CI spanning the
   comparison is still reported per `AGENTS.md` rule 8 (see next point), never
   rounded into a win.
5. **Corrected from the prior draft:** `AGENTS.md` rule 8 is *"do not delete
   losing strategies from reports... a CI spanning zero is reported as
   `NOT_REPORTABLE`, never rounded into a win"* — cite it for that, not for a
   "paired bootstrap CIs" framing that isn't what the rule says (the practical
   effect on this step is the same either way: report everything, label
   ambiguous results honestly).
6. **Promotion criterion (exact, pre-registered, unchanged after results):**
   the candidate is promotable only if **both** hold on the pooled CV window —
   (a) top-1 hit rate beats the SP-favourite baseline (lower 95% paired-CI bound
   on candidate−favourite > 0); and (b) it does not lose the H2H vs production
   (one-sided 95% lower bound on candidate−production ≥ 0). Actual *promotion*
   additionally requires `12-preregistration.md`'s NEW-BEATS-OLD bar — candidate
   walk-forward Brier ≤ production on the same window races, `evaluate_ship_
   criteria` = SHIP for the LIVE-GATE band (defined in `12-preregistration.md`'s
   REGISTERED BANDS section — not in `ship_criteria.py`, which just evaluates
   whichever band it's handed) with the ROI bootstrap CI excluding zero above
   zero, ≥200 bets or INSUFFICIENT_SAMPLE — consumed via `retrain_preflight.py`
   `gate_shadow_metrics` (confirmed exact, line 281).

**Acceptance criterion, and what could go wrong:** unchanged from the prior
draft in substance — tiny race counts (n_races printed, INSUFFICIENT_SAMPLE
below the P0.4 floor), baseline-denominator games (both denominators printed),
production-artifact scoring skew. One addition on the last point: the
production artifact's persisted `feature_columns` may genuinely differ from
current source (source is verified 72 today; a live pkl trained before recent
pruning could carry a larger, older list — `ml_model.py`'s `prepare_features()`
already prefers whatever a specific artifact's own `_trained_feature_columns`
says over the class constant, so this is expected, not a bug to fix here) —
the H2H harness must score production through its own stored feature list, not
force it onto the candidate's.

### 3.4 Learned, persisted ensemble — kill the seed-count weights

**Steps:**

1. **New, added per §0.1.2/P0.7:** if `retrain_v2.py` is confirmed the real
   training entry point, it currently has no stacking-fitting code at all (only
   `ml_model.py` does). Add it here — reuse the existing `StackingMetaLearner`
   class (`stacking_meta_learner.py`, confirmed: `fit(X, y, base_models, scaler)`,
   `predict(xgb_pred, lgb_pred, cat_pred)`, `get_meta_weights()`, `get_report()`)
   fit on the purge-gapped OOF predictions from the fixed folds (§3.2), rather
   than assuming both files already compute the same thing and just need
   synchronized persistence. Given the snapshot-era OOF will be small, the
   pre-registered fallback (set the minimum-OOF-n threshold in P0.4) is
   documented equal weights, not a meta-learner fit on noise.
2. Persist everything inference needs inside the artifact: extend
   `retrain_v2.save_model` (confirmed payload today: `xgb_model`, `lgb_model`,
   `cb_model`, `feature_columns`, `feature_importance`, `cv_results`,
   `ablation_results`, `trained_at`, `version:"v2"` — no `stacking_learner`, no
   dedicated calibrator key, no `target_encoder`) to include the
   stacking/weight object, the per-model OOF isotonic calibrators (today these
   ride along as `_isotonic` attributes bolted onto the model objects
   themselves — confirmed a real, working mechanism, just not its own payload
   key; keep that shape or promote it to a dedicated key, either way document
   which), the final-stage calibrator (§3.5), and a real artifact schema
   version that also records `_train_odds_source()`'s output — today's
   `"version": "v2"` is a static string that doesn't distinguish which odds
   mode or feature set trained a given artifact, which is itself a gap worth
   closing here rather than separately. `target_encoder`: confirmed no
   target-encoding code exists anywhere in `retrain_v2.py`; if `ml_model.py`
   doesn't have one fitted either, drop this from the persistence list rather
   than plumbing a slot for something that isn't built — verify before writing
   the code.
3. Route inference through the persisted combination: `predict_components`'s
   stacking branch (confirmed lines 547-564) becomes live after reload. **Also
   fix while here** (§0.1.1): when a `double_calibrator` is present, it
   currently overrides the stacking branch's prediction while the response
   still reports `"method": "stacking"` (lines ~551-559) — this mislabeling is
   inert today only because the branch is unreachable; making the branch live
   without fixing this would ship a reporting bug.
4. Delete `_model_performance` (confirmed `ml_model.py:70-74`) and
   `_get_ensemble_weights` (:492-506); `update_model_performance` (:508, ends
   ~522) has zero callers anywhere in the repo, confirmed by grep and
   independently corroborated by `docs/analysis/SYSTEM_MAP.md:124` — remove it
   in the same PR.
5. CV scores the same combination it ships: `predict_ensemble` (confirmed
   `def` at 928, unweighted mean returned at 961) takes the fold's fitted
   combiner instead of `np.mean`.

**Acceptance criterion, and what could go wrong:** unchanged in shape from the
prior draft — pickle-completeness test (fresh process reproduces training
predictions exactly, stacking branch demonstrably reachable, `"method"` field
correctly labeled given point 3 above), meta-learner-overfits-tiny-OOF
(pre-registered n-floor, equal-weight fallback), partial pickle (asserted by
the completeness test), old-code/new-artifact skew (schema version checked at
load with a loud error). **Check P0.0/P0.7 first** — `52770da` on the
concurrent branch already builds `ensemble_combiner.py` with prior-folds-only
cross-fitting inside `retrain_v2.py`, which may be a more direct answer to
point 1 above than porting `StackingMetaLearner` in from `ml_model.py` — this
is exactly the kind of decision P0.0 exists to surface before duplicating work.

### 3.5 One final-stage calibrator

**Steps (step 3 substantially rewritten — see §0.1.1):**

1. Establish the exact serve-time assembly order and reproduce it for OOF:
   ensemble combination (§3.4) → `mw` market anchor (`run_tips_pipeline.py`
   anchor block, confirmed lines 801-827) → per-race renormalisation (Task 05's
   `_renormalise_field`, confirmed defined at line 947, gated by
   `STRIDE_RENORMALISE_FIELD` at line 839). The OOF replay uses tip-time market
   probabilities from the Task 04 snapshots — never SP.
2. Fit a single isotonic on the OOF final published probability (post-blend,
   post-anchor, post-renormalisation), with the temporal discipline of
   `fit_calibrator.py`'s fold-refusal mechanism — **corrected description**:
   this refuses fits when a **race straddles a train/test split boundary**
   (some of its runners in train, some in test — confirmed via
   `n_races_spanning_folds`, computed in `walk_forward_backtest.py`'s
   `WalkForwardSplitter`), not specifically a check on fold *duration*. Keep
   the provenance sidecar pattern (`calibration_model.py`, confirmed: writes a
   `.meta.json` sidecar recording `fit_method`, `n_rows`, `base_rate`, and
   `out_of_fold`; `stage_quantity` and per-fold dates are supplied by the
   caller, not defaulted by this module — make sure the caller actually
   supplies them).
3. **Remove `double_calibration.py` from the published serve path — this is
   now an action, not an assertion.** Confirmed: `double_calibration.py` is
   currently imported and actively used by `ml_model.py` (`train()`, lines
   318-337; `predict_components()`, lines 551-559 and 569-578), which is what
   `mc_api.py` and `run_tips_pipeline.py` both actually load and serve through
   today. Per-model `_isotonic` calibrators remain a fold-hygiene device for
   training metrics (§3.2) and are **not** applied at serve, same as before —
   but `double_calibration.py`'s calibrator genuinely is applied at serve
   today, and must be unwired from `predict_components()` as part of this
   step, with a grep-based test asserting it's gone from the live path (not
   asserting something already true).
4. Per-track/per-distance calibration slices and drift hooks are *reported*
   but not separately fitted — one calibrator, sliced diagnostics.

**Acceptance criterion:** honest OOF Brier published with CI; sidecar records
the stage quantity; **the single-calibration audit must now actually change
something to pass** — grep shows exactly one isotonic application downstream of
the ensemble in the serve path (this will fail today, because
`double_calibration.py` is there; it should pass only after step 3 above is
done), and a synthetic-runner test shows the served probability changes under
the final calibrator but is invariant to any per-model calibrator or the old
double-calibrator being present.

**What could go wrong / detection:** unchanged from the prior draft on
replay-vs-live drift and near-identity calibrators. One addition: removing an
actively-used calibration stage is a live-probability change, not a no-op —
treat it with the same shadow-week discipline as everything else in §3.6, not
as a pure code-cleanup step.

### 3.6 Staged rollout — v3 beside v2, env pointer, one shadow week, exact rollback

**Steps:** structurally unchanged from the prior draft (staged artifact via
Board 1, introduce `STRIDE_ENSEMBLE_ARTIFACT` with a safe default, parallel
shadow week reusing the evidence-store pattern, Board 2 consumption, human
switch, exact rollback, re-baseline publication) — with one reconciliation
added and the branch-name correction folded in:

1. **Check the concurrent branch before building this.** `3ba75c7` on
   `origin/claude/model-improvement-analysis-hw2mkv` already adds a
   `STRIDE_ENSEMBLE_ARTIFACT`-shaped override in `ml_model.py`, a
   `--model-version`/`v3-candidate` dispatch mode, and a
   `compare_candidate_tips.py` for parallel scoring — confirmed real content,
   not just a matching commit message. Building a second, independent version
   of this mechanism without checking is the exact kind of duplicate work P0.0
   exists to prevent.
2. Everything else — Board 1 gates (`gate_paths`, `gate_loads`, `gate_liveness`,
   `gate_lockstep`, `gate_asof_td_profiles`, `gate_parity_suites`,
   `gate_freshness`; all confirmed at their exact `def` lines, 80/106/121/145/
   180/219/245), Board 2 (`gate_shadow_metrics` :281, `gate_preregistration`
   :308 — both confirmed exact, and notably more current than
   `12-preregistration.md`'s own stale self-citation of :267), the shadow-week
   evidence-store reuse, the rollback drill, the re-baseline publication — is
   unchanged from the prior draft and holds up.
3. **Branch note:** per §0.1.3, this whole task's work (plan and eventual
   implementation) belongs on `roi/12-retrain-rebaseline`, the existing
   reserved-but-empty stub — not a newly invented `roi/12-retrain-plan`.

**Acceptance criterion, and what could go wrong:** unchanged from the prior
draft.

---

## 4. What this plan deliberately does not do (so 12 doesn't foreclose 13/14)

Unchanged from the prior draft, re-confirmed against the actual task docs:

- **No objective changes** — no listwise voter, no softmax selection; those are
  [13](13-race-aware-objective.md) (confirmed scope: "direct top-pick
  strike-rate lever"). The race-key plumbing added in §3.3 is built to be
  reusable by 13's race-grouped trainers, and its H2H protocol is the one 13's
  listwise arm will be judged by.
- **No movement features** — the ~13 movement columns stay exactly as Task 03
  left them (uniform zeros at train and serve) until
  [14](14-late-odds-features.md) (confirmed scope: "largest documented feature
  gain"); the Task 04 `late_t5` series is untouched by this task. The artifact
  schema (§3.4) versions feature columns so 14's v4 can extend the set without
  a format break.
- **No band/threshold changes to the live gate** — re-derived band hypotheses
  are *registered* (new entries, new window-Bs) per Task 09; nothing is quoted
  or shipped as a selection rule without a PASS. If the honest re-baseline shows
  no edge, the output is fewer/no bets, per the parent task's own guardrail —
  this plan does not contain a fallback sweep, because a fallback sweep on the
  evaluating window is exactly what Task 09 exists to forbid.
- **No `mw`-ladder retuning** (Task 05's guardrail; the ladder moves only with
  13's registered market-double-count ablation).
- The implementation PR must update the roadmap README tracker row for 12
  (`AGENTS.md`: every PR updates status + date + evidence link). This planning
  document contains only itself.

## 5. Numbered execution sequence (summary)

1. **P0.0 first, on its own**: review the concurrent branch
   (`origin/claude/model-improvement-analysis-hw2mkv`) against this plan; decide
   adopt/rework/discard before anything else, since it may already implement
   large parts of steps 3-6 below.
2. P0.1-P0.8 (§2) — all green, in writing, including the evidence-store day
   counts gate 3 itself won't check, before step 3.
3. §3.1 training-view switch + zero-SP query + coverage report.
4. §3.2 fold hygiene + poisoned-fold test.
5. §3.3 per-race metrics + H2H harness (extended, not just reused) + paired-CI
   extension to `roi_stats.py`.
6. §3.4 learned combiner (with stacking-fitting logic actually added to
   whichever script P0.7 identifies as the real trainer) + artifact schema +
   pickle-completeness test + the `"method"` mislabeling fix.
7. §3.5 final-stage calibrator + **actual removal** of `double_calibration.py`
   from the serve path + single-calibration audit.
8. Run the pre-registered snapshot-era CV (P0.4); record the promotion decision
   against the §3.3 criterion — REJECT is a valid, reportable outcome.
9. §3.6 staged rollout (reconciled against the concurrent branch's
   `STRIDE_ENSEMBLE_ARTIFACT` work first): preflight Board 1 → shadow week →
   Board 2 → human switch → rollback drill → README re-baseline + registry
   entries.
10. Tracker update; PR per `AGENTS.md` (one task, one branch
    `roi/12-retrain-rebaseline`, one PR; evidence attached; no merge without
    human review).

---

*Revision prepared 2026-09-05 against `main @ 16439a6`, by independently
fact-checking the prior draft's citations via full-file reads rather than
trusting its line numbers — per `AGENTS.md`'s own warning that line references
drift and symbols should be re-checked. Three corrections are load-bearing
(§0.1.1-§0.1.3); the rest are precision fixes that don't change the plan's
direction. This document proposes and discloses; it does not implement.*

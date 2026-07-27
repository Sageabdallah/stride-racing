# RESULTS — Workstream A baseline and per-ticket deltas

Every ticket from `IMPLEMENTATION_PLAN.md` reports its measured delta here,
against the baseline recorded in §2. The protocol is
`orchestrator_instuctions.md` § "TICKET EXECUTION PROTOCOL" step 5.

> ⚠️ Backtest numbers are not live-trading expectations. Every claim in this
> file carries its sample size and confidence interval, or it does not belong
> here.

---

## 1. Harness provenance

| Property | Value |
|---|---|
| Harness | `server/python/walk_forward_backtest.py` (extended, Workstream A) |
| Split | expanding-window, time-ordered, `gap_days=7` purge |
| Race integrity | `STRIDE_WFB_RACE_SAFE_SPLIT=true` — one race never spans a split |
| Settlement price | `STRIDE_WFB_PRICE_AT_TIP=true` — racecard price, not SP |
| Commission | `--commission-rate` (default `0.0`; AU fixed-odds and tote carry none) |
| ROI interval | seeded percentile bootstrap over **bets**, `--bootstrap-n 2000 --bootstrap-seed 42` |
| Self-test | `python walk_forward_backtest.py --self-test` — no DB, no training stack |

**What this harness measures.** `RacingMLModel` refit per fold, scored on raw
features. **Not** the production `retrain_v2` artifact, and **not** the shipped
selection wrapper — `calibrate_and_score`, `apply_safety_filters`,
`evaluate_bet_candidate` and `crowd_bet_decision` are all downstream of it and
none is exercised. Reports carry this in `config.model_under_test`.

---

## 2. Baseline — **NOT YET MEASURED**

**Status: blocked on data access. No baseline numbers exist.**

The acceptance test in `orchestrator_instuctions.md` § Workstream A asks for
baseline metrics "from running the CURRENT production model". That cannot be
produced in this repository, verified empirically on 2026-07-25:

| Prerequisite | State |
|---|---|
| `models/` directory | **absent** — `.gitignore:39,41` excludes `*.pkl` and `server/python/models/` |
| `models/isotonic_calibrator.pkl` | **absent** (answers the open `P0-b` question) |
| `DATABASE_URL` / `.env` | **absent** — `_load_data` raises `RuntimeError` |
| `training_data` rows | **absent** — no database in this environment |
| Racecards / results fixtures | only `examples/` (one 39-race day of output, no labels) |
| Numeric stack | was **entirely uninstalled**; `numpy/pandas/scikit-learn/scipy` installed during Workstream A |

A second, independent blocker survives even once a database is attached: the
harness refits `RacingMLModel` rather than loading the production artifact, so
"run the CURRENT production model through the harness" is not an operation it
supports today. Closing that is ticket **T6** (calibrator/artifact provenance),
not Workstream A.

**No numbers have been invented to fill this section, and none should be.**

### How to produce the baseline

On a host with the production database and `models/` artifacts:

```bash
cd server/python
STRIDE_WFB_RACE_SAFE_SPLIT=true STRIDE_WFB_PRICE_AT_TIP=true \
python walk_forward_backtest.py \
    --min-train 3000 --test-size 500 --gap-days 7 \
    --bootstrap-n 2000 --bootstrap-seed 42 \
    --output backtest_results/baseline_workstream_a.json
```

Then transcribe the aggregate block into §3 as the `BASELINE` row, and record
the artifact's git SHA, the `training_data` row count and its date range.

### What must NOT be used as the baseline

The README's **33.7% / −4.2%** and **9.9% / +12.3%** figures. They describe the
ML ensemble under strategy bands over a 2026-03→04 window — not the live
wrapper, not with race-safe splits, and not settled at tip-time prices. The
research pass also found the underlying edge statistically empty (t = 0.432),
and that training fits SP while inference serves the ~08:00 racecard price.
Treating them as a baseline would silently import all three defects.

---

## 3. Ticket results

| Ticket | Lever | Baseline ROI / SR | New ROI / SR | Bootstrap 95% CI | #Bets | Max DD | CLV | Verdict |
|---|---|---|---|---|---|---|---|---|
| _(none yet — baseline pending)_ | | | | | | | | |

**Ship criteria** are enforced by `server/python/ship_criteria.py`, not applied
by hand. `evaluate_ship_criteria(baseline, candidate, lever)` returns one of:

| Verdict | Meaning | Action |
|---|---|---|
| `SHIP` | every criterion passed | promote: flag on by default |
| `HOLD` | measured, and rejected | flag stays OFF, record the row |
| `NOT_REPORTABLE` | the measurement cannot answer the question yet | flag stays OFF, say what is missing |

The six criteria and their sources:

| Criterion | Bar | Source |
|---|---|---|
| `reportability` | ≥ 200 settled bets | `MIN_BETS_REPORTABLE`, shadow_pl_tracker |
| `roi_significance` | ROI bootstrap CI must not span zero | protocol step 6 |
| `lever_direction` | must move the lever it claimed | protocol step 6 |
| `other_lever` | strike rate ≥ 5% relative floor; ROI ≥ 1pp floor | protocol step 6 |
| `clv` | mean CLV ≥ 0 | protocol step 6 |
| `calibration_brier` | Brier must not degrade | SYSTEM_MAP constraint 18 |

Two deliberate asymmetries. **A CI spanning zero is `NOT_REPORTABLE`, not a
loss** — it is an absence of evidence, and recording it as a defeat would be as
wrong as recording it as a win. **Missing evidence never promotes**: an absent
CLV or Brier yields `NOT_REPORTABLE`, because turning a flag on is a real-money
decision made once while leaving it off costs only time.

ROI is compared in percentage *points* and strike rate *relatively*, since ROI
can be negative or near zero and a relative test there is unstable.

A ticket that fails keeps its flag OFF and is recorded here anyway — a validated
negative result is a result. Rows are generated by `format_results_row(...)`.

---

## 4. Verified without a baseline

These Workstream A findings are properties of the harness itself, proven by the
self-test rather than by a backtest, so they stand independent of §2.

| Finding | Evidence |
|---|---|
| The legacy row-count cut truncates races mid-field | self-test: 4 partially-included races across 5 folds on a 240-race fixture; 0 under `race_safe` |
| `pandas.sort_values` default quicksort is not stable, so a race's runners were scattered within their date block | fixed under the flag by a `mergesort` on `(race_date, race_key)` |
| Fold indices addressed a different row order than `run()` sliced | `run()` now rebinds to `splitter.prepare_frame(...)`; self-test asserts train/test dates stay disjoint and gapped on shuffled input |
| Settling at `starting_price` credits the backtest with the closing line | `STRIDE_WFB_PRICE_AT_TIP` settles at the racecard price instead |
| The pre-existing `ci_95` is a t-interval across folds, not a statement about the bet population | bootstrap over bets added alongside; both reported |
| The harness could not be imported without `psycopg2` | import guarded; `_load_data` raises a clear error only when actually called |
| The diagnostics reporter exited 0 with every query failing | `run_report` now returns `(ok, failed)` and the CLI exits non-zero; self-tests drive it through clean / total-failure / single-failure modes |
| **The conditional-logit β = 0.000 result is not interpretable as reported** | `conditional_logit.py` box-constrained β to `[0.0, 5.0]`, so β cannot go below zero. A clamped corner and a genuine interior zero are indistinguishable in the stored report. Self-test demonstrates the ambiguity on an adversarial fixture: clamped β = 0.000 (`at_bound = lower`) vs unconstrained β = −0.425, with a **better** NLL (1.41115 → 1.39340) |

### The β = 0 re-reading

`docs/12` §5.1 reads the 2026-07-13 fit (α = 1.296, **β = 0.000**) as *"the SP
market adds nothing conditional on the stored model probability"*. That reading
is only available if zero is an interior optimum. It was not tested, because the
optimiser could not return anything lower.

The two possibilities carry different consequences:

| If β = 0 is… | It means | Consequence |
|---|---|---|
| an interior optimum | the market genuinely adds nothing once the model probability is known | docs/12's reading stands |
| a clamped corner | the unconstrained optimum is **negative** — the stored model probability *over-weights* market information and the fit wants to subtract some back out | the market-anchor blend is double-counting the market, which bears directly on the `mw` ladder |

`fit()` now always reports `at_bound`, and `--allow-negative-beta` runs the
diagnostic refit. **Default bounds are unchanged**, so no existing fit moves.
This is a diagnostic only — `STRIDE_CL_BLEND` stays off on the current artifact
per the standing hold in docs/12 §5.1.

**Next action (needs the database):** re-run
`python conditional_logit.py --fit --allow-negative-beta` and record β's sign
here. If negative, docs/12 §5.1's interpretation (c) needs revising.

---

## 5. First live diagnostics — 2026-07-27

`selection-diagnostics` run #2 against production, 365-day window.
**10 of 10 queries returned rows, 0 failed.** These are measurements of the
live `selections` table, not backtests — they describe what the wrapper did,
not what it would earn.

> ## ⚠️ PROVENANCE WARNING — read before using any figure below
>
> **Every row measured here predates this repository.** The stored data spans
> **2026-02 → 2026-04**. This repository's first commit is **2026-05-19**.
> There is no overlap, and `prediction_audit` agrees — 260 rows, all 2026-03.
>
> So these rows were written by a version of the pipeline that is **not in this
> repository's git history at all**. Nothing below can be attributed to the code
> as it stands today, and no current function can be held responsible for a
> pattern in it.
>
> **There is no evidence in the database that the current code has ever run.**
> That is a question for the operator, not something the data can settle.
>
> This warning was added after the first version of §5 drew conclusions about
> current behaviour from these rows. That inference was invalid. The figures are
> kept because they are real measurements of *something*, and because the
> provenance limit is itself the most important finding — but every claim below
> is now scoped to "the pre-repository pipeline", never to current code.

### 5.1 A calibrator was firing in the pre-repository pipeline

| calibration_state | runners |
|---|---|
| differs (calibrator fired) | **1,390** |
| identical (calibrator inert or absent) | 4 |

SYSTEM_MAP §9 Q1 called this "the single biggest assumption in the system": if
`models/isotonic_calibrator.pkl` were absent in production, the isotonic layer
would silently no-op and every edge figure would derive from an uncalibrated
probability.

**Correction.** An earlier version of this section said the assumption "can be
retired". It cannot. These rows are from Feb–Apr 2026, before this repository
existed, so they establish that *a* calibrator was loaded in *that* pipeline —
not that one is loaded in the current one. Q1 stays **open** for the code as it
stands.

What this does establish is narrower but still useful: the calibration layer is
not inherently inert. It has demonstrably fired in production at some point,
against real cards, which rules out "the pickle has never existed anywhere".

### 5.2 The model is overconfident, and calibration is doing real work

| metric | value |
|---|---|
| runners | 1,394 (100% recalibrated) |
| mean raw win% | 13.77 |
| mean calibrated win% | **11.66** |
| mean absolute shift | 2.63 pp |
| max absolute shift | **26.35 pp** |

Calibration pulls probabilities **down** by ~2.1 pp on average. That is the
signature the research predicted — a pointwise binary model over-predicting
win probability — and it is being corrected. The 26 pp maximum shift means the
correction is not cosmetic on individual runners.

### 5.3 The confidence ladder is NOT monotone in edge

| confidence | runners | avg edge (pp) | avg price | avg kelly_stake | live stake |
|---|---|---|---|---|---|
| low | 1,040 | −2.47 | 7.72 | 0.141 | 0u |
| medium | 295 | **−7.98** | 4.55 | 0.600 | 1u |
| high | 59 | **+10.95** | 6.21 | 0.763 | 2u |

**`medium` has a worse average edge than `low`** — −7.98 pp versus −2.47 pp —
yet `low` is staked at 0u and `medium` at 1u. On these figures the ladder
directs real money at the worst-edge bucket in the system. `high` behaves as
intended (+10.95 pp).

**Resolved — this is a provenance artifact, not a live defect.**

`compute_confidence` (`run_tips_pipeline.py:950-977`) cannot produce these rows.
Its logic is:

```python
if odds > 30:            return "low"
if ev > 0.0 and edge > 1.0:  result = "high"
elif edge > 0.0:             result = "medium"
else:                        result = "low"
```

`medium` requires `edge > 0` **by construction**, so a medium row averaging
−7.98 pp is impossible under the current code. And the stored `edge` column is
`modelEdge` — `store_selections_in_db` binds `pick["edge_pct"]`
(`run_tips_pipeline.py:1296`), which is set from `horse["modelEdge"]`
(`:1691`) — so it is not a different quantity being compared.

The explanation is the provenance warning above: these rows were written before
this repository existed, by a `compute_confidence` that is not in its history.
Today's thresholds simply do not describe them.

**Consequence: do not "fix" the confidence ladder on the strength of this
table.** The correct reading is the opposite of a defect report — the current
logic is self-consistent, and the anomaly is evidence that stored rows and
current code come from different eras. Any future comparison of `confidence`
against `edge` must first establish that the rows were written by the code
being tested.

### 5.4 The best-edge bucket sits right under the price ceiling

| value_rating | runners | avg edge (pp) | avg price |
|---|---|---|---|
| Poor | 986 | −6.27 | 4.43 |
| Fair | 120 | +0.79 | 10.71 |
| Good | 94 | +2.21 | 12.21 |
| Excellent | 194 | **+8.28** | **13.99** |

Edge rises monotonically with price band, and `Excellent` averages **$13.99** —
immediately below the `odds > 15` hard ceiling at `run_tips_pipeline.py:1812`.
SYSTEM_MAP §9 Q8 flagged that ceiling as "the largest ROI constraint in the code
with no supporting measurement". This is the first evidence bearing on it, and
it is suggestive rather than conclusive: the value the model finds clusters
against the wall that rejects it. Whether raising the ceiling helps or simply
admits longshot noise cannot be answered without settled P&L.

### 5.5 Selections stopped being stored after 2026-04

| month | race days | races | runners | runners/race |
|---|---|---|---|---|
| 2026-02 | 1 | 26 | 26 | 1.0 |
| 2026-03 | 8 | 215 | 1,038 | 4.8 |
| 2026-04 | 7 | 330 | 330 | **1.0** |

**Re-framed.** An earlier version of this section read the April cutoff as a
fault — "storage stopped, either the pipeline is not running or
`store_selections_in_db` is failing". That was wrong, and the mistake was
comparing the data window against *today's date* instead of against the
repository's own history.

The data ends 2026-04. This repository begins **2026-05-19**. The rows do not
stop mid-life; they simply end where the published codebase starts. Nothing
here is evidence of a break.

What remains genuinely open:

1. **Has the current pipeline ever written a row?** No table shows one.
   `selections` ends in April, `prediction_audit` in March. This is a question
   for the operator — the database cannot answer whether the code is being run.
2. **The rows-per-race change from ~4.8 to exactly 1.0** between March and
   April is still unexplained, but it too occurred entirely inside the
   pre-repository era, so it says nothing about current behaviour.

### 5.6 `prediction_audit` is still stalled, and a migration was never applied

`prediction_audit` holds **260 rows, all in 2026-03** — unchanged from what the
2026-07 coverage report found. Separately, `prediction_audit.final_win_prob`
**does not exist in the database**, though `migrations/final_prob_audit.sql`
adds it and `docs/12` §4c describes it as a column the pipeline already
records. That migration has not been applied.

This matters for the conditional-logit work: `docs/12` §5.1's green-light path
for `STRIDE_CL_BLEND` requires "a few weeks of true MC-stage rows" to
accumulate. None have.

### 5.7 What these findings do NOT establish

No ROI, no strike rate, no P&L. `selections` records what was published, not
what it returned. §2's baseline remains unmeasured and every ship-criteria
verdict still evaluates to `NOT REPORTABLE`.

---

## 6. The β = 0 question, answered — 2026-07-27

`fit-conditional-logit` with `diagnose_beta_bound`, run #5. Both fits on
**identical data**: 1,227 usable races, 982 train / 245 holdout.

| bounds | α | β | at bound | holdout hit | holdout log-loss |
|---|---|---|---|---|---|
| standard `β ∈ [0, 5]` | 1.296 | **0.000** | β at **lower** | 0.429 | 1.6866 |
| diagnostic `β ∈ [−5, 5]` | **5.000** | **−3.950** | α at **upper** | **0.506** | 1.7707 |
| *(model only)* | — | — | — | 0.429 | 1.7039 |
| *(market only)* | — | — | — | 0.404 | 1.7499 |

The standard row reproduces `docs/12` §5.1's recorded 2026-07-13 fit exactly
(α = 1.296, β = 0.000, hit 42.9%, log-loss 1.6866), which validates the
comparison.

### 6.1 `docs/12` §5.1 interpretation (c) is refuted

That section reads β = 0.000 as *"the SP market adds nothing conditional on the
stored model probability"*. **It was a clamped corner.** Released, β goes to
**−3.950** — the fit does not want to ignore the market, it wants to
**actively subtract** it. The stored model probability already over-weights
market information, and the downstream market anchor then counts it a second
time.

The `at_bound` field now reports this on every fit, so the ambiguity cannot
recur silently.

### 6.2 …but the diagnostic fit is not identified either

Freeing β pushed **α onto its own upper bound** (5.000). A parameter resting on
a box edge is whatever the box allowed, not an optimum, so **only the SIGN of β
is trustworthy here — the magnitude −3.950 is not.**

Fixed: `allow_negative_beta` now widens **both** parameters to
`α ∈ [0, 50]`, `β ∈ [−50, 50]`, and the self-test asserts α is no longer pinned.
Standard bounds are untouched. **The magnitudes above should be re-measured
under the wider box before anyone reasons about their size.**

### 6.3 This does NOT license enabling `STRIDE_CL_BLEND`

Top-pick hit rate rises 42.9% → **50.6%** (+7.7 pp), which is large and well
above the 34.9% AU favourite baseline. But **log-loss degrades 1.6866 → 1.7707**.
The blend ranks better and calibrates worse.

For a system that gates bets on `edge`, computed *from* the calibrated
probability, that trade is the wrong way round — and it fails **SYSTEM_MAP
constraint 18** outright, which requires a default-on change to raise hit rate
*without* degrading calibration. Under `ship_criteria.evaluate_ship_criteria`
this is a `HOLD`, not a `SHIP`.

The provenance hold in `docs/12` §5.1 also still stands and is untouched by any
of this: it concerns *what the fit consumed*, and widening a bound does not
change that the fitted quantity is imported `training_data` predictions of
unknown generating stage.

### 6.4 `final_prob_audit.sql` applied

`prediction_audit.final_win_prob` now exists (`apply-migration` run #2). Note
what its absence implied: the migration header states the pipeline *self-heals*
this column at write time via `ADD COLUMN IF NOT EXISTS`, so the column being
missing is independent evidence that `store_final_probs_in_audit` has never
successfully run against this database — consistent with §5's provenance
finding.

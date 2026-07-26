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

### 5.1 The calibrator IS loaded — the biggest open assumption is closed

| calibration_state | runners |
|---|---|
| differs (calibrator fired) | **1,390** |
| identical (calibrator inert or absent) | 4 |

SYSTEM_MAP §9 Q1 called this "the single biggest assumption in the system": if
`models/isotonic_calibrator.pkl` were absent in production, the isotonic layer
would silently no-op and every edge figure would derive from an uncalibrated
probability. **It is present and firing.** That assumption can be retired.

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

This is a live, measured finding about the staking ladder at
`run_tips_pipeline.py:1007-1015`, and it needs explaining before any staking
work proceeds. Two readings are possible and the data here cannot separate
them: either `confidence` is keyed on something other than edge (so the
ordering is intentional), or the ladder is mis-assigning stake. **Do not act on
this without checking how `compute_confidence` derives its label.**

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

Two problems, both new:

1. **Nothing has been stored for roughly three months.** The window is 365 days
   and the most recent row is April. Either the pipeline has not run, or
   `store_selections_in_db` has been failing.
2. **The rows-per-race pattern changed between March and April** — from ~4.8
   runners per race to exactly 1.0. A clean 1.0 suggests only the bet pick is
   being stored where previously the top picks were.

Both are consistent with the repo's recurring failure mode: a fault that
presents as slightly less output rather than as an error.

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

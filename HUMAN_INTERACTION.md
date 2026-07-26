# Where STRIDE stands, and what needs a human

A plain-language summary of the Phase-1 research run and the Phase-2
implementation work, written for the person who has to decide what happens
next. No jargon that isn't unpacked, and no numbers presented as more certain
than they are.

Companion documents: [`agent_research.md`](agent_research.md) (the research
brief), [`orchestrator_instuctions.md`](orchestrator_instuctions.md) (the
implementation brief), and the four reports plus results in
[`docs/analysis/`](docs/analysis/).

---

## The one-sentence version

The system has never actually been measured, and almost everything built in
Phase 2 is measuring equipment rather than improvements.

---

## Why nothing has been switched on

STRIDE decides a bet through a chain:

```
ML model → calibrator → blend toward market price → safety filters → crowd gate → BET / NO_BET
```

The existing backtests measure **only the first link**. The headline figures —
top pick wins 33.7% but loses 4.2%; the selective filter wins 9.9% at +12.3%
ROI — describe the ML model under simple strategy bands. Nothing has ever
measured the chain end to end.

That matters because the numbers deciding whether you bet all live *after* the
first link:

| Threshold | Where | Ever validated? |
|---|---|---|
| `odds > 15` hard ceiling | `run_tips_pipeline.py:1812` | No |
| Edge gates, 4 / 2.5 / 3 pp by price band | `evaluate_bet_candidate` | No |
| Market-weight ladder, 0.80 → 0.30 | `calibrate_and_score` | No |
| Crowd gate cutoffs, 50 / 15 / 8 / 70 / 100 | `consensus_blender.py` | No |

The research pass went looking for the script that fitted these. There isn't
one. They were set by hand and never checked against money. That is not a
criticism of the design — it is an unexamined assumption sitting underneath
every bet the system makes.

So "nothing is promoted" is not caution for its own sake. Turning a flag on
without a baseline is indistinguishable from guessing, and this codebase has
already been caught by exactly that three times:

- a calibrator the code has always claimed was fitted, which nothing ever
  produced;
- an audit table where **every insert failed silently for months** because its
  upsert named an index that did not exist;
- a conditional-logit result that was misread because the optimiser's bounds
  made the reported value the only one it could return.

The pattern is identical each time: the failure looked like slightly worse
output, never like an error.

---

## The three things only a human can do

**You do not need a local database for any of these.** Each runs as a GitHub
Action on a hosted runner using the `DATABASE_URL` repository secret, the same
mechanism `audit-coverage` and `fit-conditional-logit` already use. Go to
**Actions → (workflow) → Run workflow**. The run log is the report.

| Action | Workflow | Writes? |
|---|---|---|
| 1. Diagnose the β bound | `fit-conditional-logit` with `diagnose_beta_bound` ticked | no |
| 2. Apply the ledger migration | `apply-migration`, confirm `APPLY` | **yes** |
| 3. Run the diagnostics | `selection-diagnostics` | no |

The commands below are the local equivalents if you'd rather run them
yourself. Each answers a question nobody currently knows the answer to, and
none of them changes how the system behaves.

### 1. Re-run the conditional-logit fit

```bash
cd server/python
python conditional_logit.py --fit --allow-negative-beta
```

`docs/12` concludes that the market "adds nothing" beyond the model, based on a
fitted β of exactly `0.000`. But β was box-constrained to `[0.0, 5.0]` — zero
was the **lowest value the optimiser could return**. A clamped result and a
genuine zero are indistinguishable in the stored report, and they mean opposite
things:

| If β = 0 is… | It means |
|---|---|
| a genuine optimum | the market really does add nothing once the model probability is known — the current reading stands |
| a clamped floor | the true optimum is **negative**: the model probability already over-weights market information, and the market-anchor blend is counting the market twice |

The fitter now always reports whether a parameter sits on a bound. Default
behaviour is unchanged, and `STRIDE_CL_BLEND` stays off either way — this is a
diagnostic, not an enablement.

**Record β's sign in `docs/analysis/RESULTS.md`.** If it is negative,
`docs/12` §5.1 interpretation (c) needs revising.

### 2. Apply the ledger migration

```bash
psql "$DATABASE_URL" -f migrations/selection_ledger.sql
```

Today, when a tip is published the price tipped at is recorded, and the
starting price arrives later — but nothing keeps them together with the
outcome. So the question *"did the price shorten after we tipped it?"* cannot
be asked.

That signal is **closing-line value**, and it is the earliest available
indication that an edge is real rather than luck. It is currently discarded
every race day. The migration only starts keeping it: a new table that no
existing code reads or writes, so it cannot affect anything already running.

### 3. Run the diagnostics

```bash
cd server/python
python selection_diagnostics.py --run
```

Nine read-only counters. The session is opened read-only and every query is a
`SELECT` — it cannot modify anything.

The one to read first compares **raw vs calibrated probabilities**. If they are
identical across the board, the calibrator is not loaded in production — which
would mean the "calibrated probability" is not calibrated, and every edge figure
in the system derives from it. That is the single largest unverified assumption
in the codebase, and this answers it in seconds without needing the model file
itself.

The others count things that happen daily and are currently never counted: how
often the `$15` ceiling kills a pick, how often the crowd gate overrides a
decision, how far the calibration layer actually moves a probability, and how
selections distribute across the edge bands.

---

## What to expect

Two findings from the research suggest the current numbers are softer than they
appear.

**The +12.3% ROI has a t-statistic of 0.432.** That is statistically
indistinguishable from zero — not a bad result, simply not yet a result. It
needs far more settled bets before it can be called anything.

**The model was trained on starting prices but serves predictions against the
~08:00 racecard price.** Those are different numbers, so the backtest was
scored at prices that were not available when a bet could actually have been
placed. This was rated the strongest source-read finding of the whole research
pass, and it unsettles the headline figures *and* the LambdaRank comparison,
because both arms were trained the same way.

Neither means the system does not work. Both mean it is not yet known whether
it does.

---

## Why the order is measure-first

Once a baseline exists, promotion stops being a judgement call.
`server/python/ship_criteria.py` decides it mechanically against six bars: a
200-bet floor, a ROI confidence interval that excludes zero, movement on the
lever the change claimed, a bounded regression on the other lever, closing-line
value at or above zero, and calibration not degrading.

Two of its behaviours are deliberate and worth knowing:

- **A confidence interval spanning zero returns `NOT REPORTABLE`, not a
  failure.** It is an absence of evidence. Recording it as a defeat would be as
  wrong as recording it as a win.
- **Missing evidence never promotes.** Absent CLV or calibration figures also
  return `NOT REPORTABLE`, because switching a flag on is a real-money decision
  made once, while leaving it off costs only time.

Until then every flag stays off and the default configuration reproduces
existing behaviour exactly.

---

## What has been built

All of it is inert until enabled, and every behavioural change sits behind a
`STRIDE_*` flag that defaults to off. Sixteen modules carry self-tests in CI.

| Area | What it does |
|---|---|
| Evaluation harness | Walk-forward backtest with race-safe splits, tip-time settlement prices, commission, drawdown, profit factor and a bootstrap confidence interval on ROI |
| Calibration | Provenance on the calibrator, plus the out-of-fold fitting script that never existed |
| Value & staking | EV at price, closing-line value, and fractional Kelly computed for evaluation only — never applied |
| Selection policies | Minimum-probability gate, market mix, race-type filters — built, tested, not wired in |
| Ledger & tracking | One row per selection with both prices, staged probabilities and settled P&L |
| Diagnostics | Nine read-only counters for behaviour nothing measures |
| Promotion gate | The ship criteria, enforced rather than applied by hand |

Three leakage defects were found and fixed in the existing harness along the
way: a test cut that truncated races mid-field, an unstable sort that scattered
a race's runners across its date block, and fold indices that addressed a
different row ordering than the code actually sliced.

---

## The bigger job after this

A harness that measures the **whole chain**, not just the ML model. That is what
would finally make the hand-set thresholds in the table at the top testable. It
is a larger build than everything completed so far combined, which is why it has
been flagged rather than started.

---

## If you only do one thing

**Run the `selection-diagnostics` workflow.** Actions → selection-diagnostics →
Run workflow. It needs nothing installed, it is read-only, it cannot break
anything, and it tells you whether your calibration layer exists in production.

# Outstanding work

Everything known to be left on STRIDE, as of **2026-07-27**, ordered by what
unblocks what. Companion to [`HUMAN_INTERACTION.md`](HUMAN_INTERACTION.md)
(why the system is in this state) and
[`docs/analysis/RESULTS.md`](docs/analysis/RESULTS.md) (the evidence behind
each claim below).

Nothing in this list is speculative. Every item is either a verified defect, a
measurement that does not exist, or a decision that needs a human.

---

## 0. Blocked on the operator — nothing downstream moves

### 0.1 Has the current pipeline ever run? ⚠️ **the question that gates everything**

No table shows a row written since this repository was published.

| table | most recent data | repo first commit |
|---|---|---|
| `selections` | 2026-04 | **2026-05-19** |
| `prediction_audit` | 2026-03 (260 rows) | **2026-05-19** |

Every stored row predates the codebase. If the pipeline has not been run since
publishing, then the ledger will stay empty, no calibration refit is possible,
the conditional-logit green-light path never accumulates rows, and **no baseline
can ever be measured going forward**. The database cannot answer this; only you
can.

### 0.2 Delete the agent branches

This environment's git proxy refuses branch deletion (disconnects on every
attempt) and the available GitHub API tooling exposes branch creation but not
deletion. All three are fully merged into `main`, so nothing is lost:

```bash
git push origin --delete claude/latest-repo-commit-4j5ksq
git push origin --delete claude/codebase-docs-architecture-qo4ibv
git push origin --delete claude/repo-review-0l0bj6
```

### 0.3 One commit message mentions the tooling

`b0dabe7` (2026-07-12, yours) contains *"Sandboxed/proxy-only networks
(including Claude Code cloud sessions) allow…"*. It is your prose and it
explains why the HTTPS fallback exists, so it was left alone. Rewriting it means
rewriting history on `main` and two branches — say so if you want it gone.

---

## 1. The measurement gap — the single biggest piece of work

**Nothing measures the thing that decides bets.**

`calibrate_and_score` → `apply_safety_filters` → `evaluate_bet_candidate` →
`crowd_bet_decision` has no realised hit rate and no realised ROI. The
backtests measure the ML ensemble; `shadow_pl_tracker` measures convergence
tiers. The wrapper itself is unmeasured, which means **every threshold in it is
currently unfalsifiable**:

| threshold | location | ever fitted? |
|---|---|---|
| `odds > 15` hard ceiling | `run_tips_pipeline.py:1812` | no |
| Edge gates 4 / 2.5 / 3 pp | `evaluate_bet_candidate` | no |
| `mw` ladder 0.80 → 0.30 | `calibrate_and_score` | **no fitting script exists** |
| Crowd cutoffs 50 / 15 / 8 / 70 / 100 | `consensus_blender.py` | no |

**What exists:** `selection_ledger` (table applied, writer built behind
`STRIDE_LEDGER_WRITE`), and `weekly_metrics` which imports its metric functions
from the backtest harness so the two cannot drift.

**What is missing:** the join from `selections` to results, and settlement into
P&L. Until that exists the ship criteria can never fire.

Bigger than everything completed so far combined.

---

## 2. The experiment that would settle the biggest structural question

**Is the market counted twice?**

Evidence says probably yes, but it is inference, not proof:

- `retrain_v2.FEATURE_COLUMNS` carries **12 market-derived features of 113** —
  `market_odds`, `fair_implied_prob`, `odds_rank`, `odds_rank_pct` and the
  steam/drift set — so the model probability already embeds the market;
- `calibrate_and_score` then anchors that probability to the market **again**
  via the `mw` ladder;
- a released conditional-logit fit returns **β = −17.850** with α = 18.732,
  neither at a bound — the fit wants to *subtract* the market;
- α ≈ −β, so the optimum reduces to ranking on `ln(m/q)`, the value ratio.

**The test**, in order:

1. Retrain with the 12 market features **ablated**.
2. Re-fit the conditional logit on that model, where `m ⊥ q` by construction.
3. **If β comes back positive, the double-count is confirmed** and the `mw`
   ladder becomes the thing to fix.

This is the causal ablation the Phase-5 precedent (SYSTEM_MAP constraint 36)
demands before believing any feature-importance story. Retrain-gated.

If confirmed, it would explain three separately-recorded observations at once:
edge being mechanically shrunk by `(1 − mw)`; the top pick winning 33.7%
against a 34.9% favourite baseline; and the `mw` ladder having no fitting
script anywhere.

---

## 3. Verified defects awaiting a decision or a retrain

### 3.1 `barrier_x_pace_inv` — which formula is intended?

Training fits `barrier_advantage × pace_pressure_score`; inference served
`barrier_advantage × (1 − pace_pressure_score)`. They agree only at 0.5, the
default used when pace data is missing.

Parity is now available behind `STRIDE_INTERACTION_PARITY` (default off), which
makes inference match training. **But the feature is named `_inv`**, which
suggests the inference version may have been the intent. If training is the side
that should change, that is a retrain and the fitted artifact must change with
it. Not decidable from source.

### 3.2 Training fits SP; inference serves the racecard price

The strongest source-read finding of the research pass. It undermines the
README's headline figures, the LambdaRank comparison, and possibly the 62%
conditional-logit holdout result — if the stored probability came from an
SP-trained model while `q` is also SP-derived, `m/q` carries closing-line
information on both sides.

Structural and retrain-gated. The cheap route is explicitly forbidden: **no
backfilling SP into historical training rows.**

### 3.3 `step_up_x_dist_slope` is dead computation

Computed at inference (`run_tips_pipeline` ~:2319), excluded from training, and
absent from `FEATURE_COLUMNS`, so `prepare_features` discards it. Harmless, but
it reads as a live feature.

### 3.4 Five contract features have no visible inference producer

`settling_pace_interaction`, `prep_run_x_days_since`, `class_x_spell`,
`fresh_x_trajectory`, `trial_x_experience` are in `FEATURE_COLUMNS` and each has
a single implementation module — but they are absent from the inline inference
block. Worth confirming they are actually populated at scoring time rather than
silently defaulting.

### 3.5 Two purge-gap constants coexist

`walk_forward_backtest` defaults to `gap_days=7`;
`retrain_v2.DateWindowSplitter` to `purge_gap_days=14`. Neither was changed —
moving either silently shifts every historical comparison — but a feature
validated at 7 days has not been validated under the window the production
trainer uses. Recorded in `config.gap_days` on every report.

### 3.6 Schema is largely undocumented

The database has **35 tables in `public`**; only 8 have a `CREATE TABLE`
anywhere in the repository. Any future additive-schema work is reasoning about
a schema it cannot fully see.

---

## 4. Built, tested, and waiting on a baseline

Every flag below is **off by default**; the default configuration reproduces
existing behaviour exactly. None can be promoted until §1 produces a baseline,
because `ship_criteria.evaluate_ship_criteria` returns `NOT_REPORTABLE` without
one.

| flag | what it does | lever |
|---|---|---|
| `STRIDE_WFB_RACE_SAFE_SPLIT` | keeps a race inside one fold; stable sort | *enabling* |
| `STRIDE_WFB_PRICE_AT_TIP` | settles at racecard price, not SP | *enabling* |
| `STRIDE_INTERACTION_PARITY` | inference matches training formulas | BOTH |
| `STRIDE_LEDGER_WRITE` | records selections with both prices and P&L | *enabling* |
| `STRIDE_MIN_PROB_GATE` | minimum calibrated probability to qualify | strike rate |
| `STRIDE_MARKET_MIX` | place / each-way / dutching | ROI |
| `STRIDE_RACE_FILTER_*` | four independently-toggleable race filters | strike rate |
| `STRIDE_SHADOW_KELLY` | logs a fractional-Kelly plan, never applies it | ROI |
| `STRIDE_CL_BLEND` | pre-existing; **held**, see below | BOTH |

**`STRIDE_CL_BLEND` must stay off.** The released fit raises top-pick hit rate
42.9% → 50.6% but degrades log-loss 1.6866 → 1.7809. A system that computes
`edge` *from* the calibrated probability cannot spend calibration to buy
ranking, and SYSTEM_MAP constraint 18 forbids it outright. The provenance hold
in `docs/12` §5.1 also still stands.

Two of these carry standing warnings in their own module headers:
**market mix** contradicts the one-bet-per-race contract, and **race filters**
raise strike rate by declining bets, which usually lowers ROI.

---

## 5. Remaining tickets from `IMPLEMENTATION_PLAN.md`

Roughly two-thirds of the 26 are untouched. The ones that matter most:

| ticket | what | gated on |
|---|---|---|
| **T16** | de-vig method comparison — Shin / power vs proportional | nothing; resolves the unresolved Conflict C1 |
| **T17** | within-race renormalisation | nothing |
| **T6** | load the production artifact instead of refitting per fold | artifacts |
| **T23 / T25 / T26** | context, connections and form features | retrain |

`T16` is the cheapest genuinely-open question left: the research pass proposed
an empirical resolution for whether proportional de-vig inflates edge on
favourites or biases toward longshots, and nobody has run it.

---

## 6. Operational

- **`prediction_audit` is stalled** at 260 rows, all 2026-03. The
  conditional-logit green-light path needs weeks of true MC-stage rows and has
  none. `final_win_prob` now exists (migration applied 2026-07-27) but nothing
  has written to it.
- **`STRIDE_THRESHOLD = 65.0`** (`consensus_blender.py:33`) is compared against
  a 0–25 scale — a known scale bug, but on the dormant V2 path, so it changes
  nothing today.
- **Documentation line numbers drift.** Cite the enclosing symbol and re-grep;
  several docs reference line numbers that moved during the Phase-5 work.

---

## Suggested order

1. **Answer §0.1.** Everything else is preparation until data flows.
2. **§1 — the wrapper replay harness.** Makes every threshold falsifiable and
   unlocks every flag in §4.
3. **§2 — the market ablation.** One experiment, one clear answer, highest
   structural leverage.
4. **§3.2 — the SP/racecard price fix.** Retrain-gated but it contaminates
   every measurement until fixed.
5. Everything else.

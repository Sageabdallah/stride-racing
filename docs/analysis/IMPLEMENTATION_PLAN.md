# STRIDE — IMPLEMENTATION PLAN (Phase 4 deliverable)

**Written 2026-07-25**.
Inputs read in full: `docs/analysis/SYSTEM_MAP.md` (Phase 1), `docs/analysis/IMPROVEMENT_REPORT.md`
(Phase 3), `docs/analysis/ACADEMIC_FINDINGS.md` (Phase 2, including its citation audit). Every module
named in a ticket was re-read from source this session; every line anchor below was re-verified, not
carried from the docs (drift D-4 says the docs' anchors have moved).

**This document is a specification. No `.py` or `.sql` file was modified.** Code blocks are
signatures and pseudocode only — they are not paste-ready and are not intended to be.

`RTP` = `server/python/run_tips_pipeline.py`. `RS` = `racing_system_v8.3_mc.py`.
`SPL` = `server/python/shadow_pl_tracker.py`.

**Scope.** 26 tickets covering every non-aspirational item in the Phase-3 report (I1–I6, C1–C7,
A1–A3, G1–G2, S1–S3, M1–M5). The five aspirational items (A4, X1–X4) are in §4 with their blockers.

---

## 0. Preconditions — answer before opening any ticket

These are operator questions, not engineering work. Phase 3 §0.3 gates a large fraction of the list
on them and they cost minutes each.

| P0 | Question | Blocks | How to answer |
|---|---|---|---|
| **P0-a** | Is the daily pipeline running and writing to the DB behind `DATABASE_URL`? Every table reportedly stops at ~2026-04-18 (`docs/12:203-205`). | T1–T8 and everything downstream. | `python server/python/audit_coverage_report.py --run` — query 8 ("selections stored by race month") answers it directly. |
| **P0-b** | Does `models/isotonic_calibrator.pkl` exist on the production box? | T6, T18, T19, and the meaning of every published `modelEdge`. `ProbabilityCalibrator.load()` (`calibration_model.py:48-53`) silently returns `False` and `calibrate()` returns the input unchanged (`:44-45`). | `ls -l server/python/models/isotonic_calibrator.pkl` on the box. |
| **P0-c** | Has `migrations/prediction_audit_unique_key.sql` been applied and is `prediction_audit` filling? | T18 (needs post-blend rows at volume), the CL refit, backlog A4. | `audit_coverage_report.py --run` query 1. |
| **P0-d** | Which xgboost/lightgbm/catboost versions unpickle `models/racing_ensemble_v2.pkl`? Only `numpy==1.26.4` is pinned in `requirements.txt`. | T23–T26 (all retrain-gated). | `pip freeze` on the box; attempt a load. |

**If P0-a is negative, scope collapses to: restore collection, then T1–T8, and nothing else.**

There is also one **one-argument precondition** that is not a ticket (citation-audit queue item 10):
refit `conditional_logit` with `bounds=[(0.0, 5.0), (-5.0, 5.0)]` instead of the current
`bounds=[(0.0, 5.0), (0.0, 5.0)]` at `conditional_logit.py:134-135`, and report β's **sign**. The
reported `β = 0.000` is a corner solution on a box constraint, not an interior optimum. Run it via
the existing `.github/workflows/fit-conditional-logit.yml`. It gates backlog item A4 and informs T15.

---

## 1. Build order and dependency graph

Four waves. Nothing in wave 3 is worth starting until wave 1 is collecting, because until then every
threshold in `SYSTEM_MAP §6` is what Phase 1 called it — *unfalsifiable in production*.

```
P0-a/b/c/d ─ operator answers ─────────────────────────────────────────────────┐
                                                                               │
WAVE 1 — measurement (parallel; nothing else ships first)                      │
  T1  I1  wrapper outcome ledger ──┬─► T2  I2  CLV columns                      │
                                   └─► T7  G1  promotion bar + reportability    │
  T3  I3  read-only diagnostic battery (9 counters) ──┬─► T16 (counter 9,3)     │
                                   ├─► T17 (counter 2) ├─► T21 (counter 4)      │
                                   └─► T26 (counter 6) └─► T19 (counter 4)      │
  T4  I4  backtest error bars + (odds × edge) ROI surface ──► T7, T15, T16      │
  T5  I5  calibration metrics upgrade ──► T18 ──► T19                           │
  T6  I6  calibrator provenance (answers P0-b definitively) ──► T18, T19        │
  T8  S1  bankroll state + stake column ──► T22                                 │
                                                                               │
WAVE 2 — independent, ship any time after wave 1 starts collecting              │
  T9  C2  edge_at_price + Kelly sign (publish-only)   [needs T3 counter 1]      │
  T10 C4  Rao-Blackwellised MC win prob ──► T22 (supplies Var_s(q_i))           │
  T11 C7  one shared interaction helper                                         │
  T12 S3  commission parameter + segment tagging                                │
  T13 M1  CatBoostRanker QuerySoftMax evidence arm (independent of everything)  │
                                                                               │
WAVE 3 — structural, each gated on a wave-1 number                              │
  T14 C3  NaN preservation at inference + 8 sectional primitives                │
  T15 C6  odds_source / has_real_market_odds + log(sp/racecard) diagnosis        │
  T16 C1  de-vig method selector          [gated on T3 c9 + T3 c3 + T15]        │
  T17 A3  within-race renormalisation      [gated on T3 c2; may close as no-op] │
  T18 A1  post-blend calibrator + OOF isotonic, one ticket [T5, T6, P0-c]       │
  T19 A2  calibrator family swap           [after T18]                          │
  T20 C5  context multipliers: print → repair → reorder                          │
  T21 G2  separate should-bet from how-much [gated on T3 c4]                    │
  T22 S2  shadow Kelly column              [T8 + T10 + T7]                      │
                                                                               │
WAVE 4 — retrain-gated; nothing lands until an ablation returns a causal delta  │
  T23 M2  race-relative fundamentals                                            │
  T24 M3  field-size-aware sample weighting [cheap pre-test is T3 counter 4]    │
  T25 M5  missing-indicator / shrinkage bundle                                  │
  T26 M4  training-side pace + market-velocity columns [scope set by T3 c6]     │
```

**Hard edges** (a ticket must not start before its parent has produced a number):

| Ticket | Blocked by | Reason |
|---|---|---|
| T2 | T1 | CLV by `selection_origin` needs the ledger's origin column; the raw `tipped_odds`/`api_sp` ratio does not. |
| T9 | T3 counter 1 | R4-F2's negative-EV claim is sized on an *assumed* overround; publishing `edge_at_price` before knowing the distribution produces an uninterpretable field. |
| T16 | T3 counters 9 + 3, T15 | Three candidate mechanisms produce the same short-price symptom (§5.2 of Phase 3). They must be separated before re-pricing every gate. |
| T17 | T3 counter 2 | If the median race sums within ±2pp of 100, close T17 as immaterial. |
| T18 | T5, T6, P0-c | Cannot judge a calibration change on aggregate Brier (T5), cannot fit without post-blend rows (P0-c), cannot know what is running today (T6). |
| T19 | T18 | Family choice is decided on the corrected position, not the current one. |
| T21(b) | T3 counter 4 | Only ship the separation if `mc_is_flat` fires often enough to matter. |
| T22 | T8, T10, T7 | Needs a bankroll path, an uncertainty estimate, and a promotion bar that can grade a staking change. |
| T26 | T3 counter 6 | The feature diff over **both** inference paths may shrink the scope substantially. |

---

## 2. Guardrail compliance matrix

Columns are the seven process guardrails (`agent_research.md:124-145`, restated `SYSTEM_MAP §5a`):
**G1** additive not destructive · **G2** feature-flagged, default off · **G3** one source of truth
(extend, never duplicate) · **G4** schema/log/API safety (additive only) · **G5** convention lock ·
**G6** no pipeline reordering without evidence · **G7** conflict check present.

| # | Ticket | G1 | G2 | G3 | G4 | G5 | G6 | G7 |
|---|---|---|---|---|---|---|---|---|
| T1 | I1 ledger | PASS | `STRIDE_LEDGER_V2` | extends `SPL.cmd_record/cmd_results/cmd_report` | new cols via `SPL.MIGRATION_SQL` idiom; `profit_loss` semantics untouched | PASS | no pipeline change | PASS |
| T2 | I2 CLV | PASS | `STRIDE_CLV_REPORT` | `SPL.cmd_report` only | SELECT-side only, no schema | PASS | none | PASS |
| T3 | I3 diagnostics | PASS | `STRIDE_DIAG_COUNTERS` for the 4 pipeline counters | new module mirrors `audit_coverage_report.py`; reuses its SELECT-only self-test | read-only session; counters are new stderr lines, existing lines unchanged | PASS | none | PASS |
| T4 | I4 backtest stats | PASS | `STRIDE_BACKTEST_STATS` | extends `backtest_v2_metro.py` + `walk_forward_backtest.py` | new JSON keys only | PASS | none | PASS |
| T5 | I5 calib metrics | PASS | `STRIDE_CALIB_METRICS_V2` | reuses PAV at `mc_recalibration.py:157` | new metric keys only | PASS | none | PASS |
| T6 | I6 provenance | PASS | `STRIDE_CALIBRATOR_ASSERT` | calls `ProbabilityCalibrator`, does not change it | sidecar JSON beside the pkl; `models/` git-ignored | PASS | none | PASS |
| T7 | G1 bar | PASS (docs + one function) | `STRIDE_REPORTABILITY_V2` | extends `SPL.MIN_BETS_REPORTABLE` consumer | report text only | PASS | none | PASS |
| T8 | S1 bankroll | PASS | `STRIDE_BANKROLL_LEDGER` | `SPL`, not `portfolio_risk.py` (dead, broken at `:235`) | 2 additive cols | PASS | none | PASS |
| T9 | C2 edge_at_price | PASS | `STRIDE_PUBLISH_EDGE_AT_PRICE` | uses existing `extract_odds` / `calculate_overround` | 2 additive output keys + 2 additive columns | PASS | fields written after the gate, gate unchanged | PASS |
| T10 | C4 Rao-Blackwell | PASS (adds a branch) | `STRIDE_MC_RAO_BLACKWELL` | extends `RS.simulate_race_monte_carlo`, no 5th MC engine | new result keys `win_prob_var` | PASS | same call site | PASS |
| T11 | C7 interactions | PASS **only with** the byte-identity self-test | `STRIDE_SHARED_INTERACTIONS` | one new helper replacing two copies | none | PASS | none | PASS |
| T12 | S3 commission | PASS | `STRIDE_COMMISSION_RATE=0.0`, `STRIDE_SEGMENT_TAGS` | extends `RTP.compute_confidence`'s `ev`; does **not** touch `mc_api.py:7637` | additive column + additive field | PASS | none | PASS |
| T13 | M1 QuerySoftMax | PASS | **EXCEPTION (justified, see note iii)** — no env flag; default-off is carried by `backend="lightgbm"` | second arm inside `rank_model.py` | artifact only | PASS | none; constraint 15 intact | PASS |
| T14 | C3 NaN | PASS | `STRIDE_NAN_PRESERVE_INFERENCE` | edits `ml_model.prepare_features`, leaves `retrain_v2`'s correct handling alone | none | PASS | same position in the call order | PASS |
| T15 | C6 odds_source | PASS | `STRIDE_ODDS_SOURCE_FEATURES` | extends `relative_market.py`'s "0 = no market" discipline | 2 additive contract features, retrain-gated | PASS | none | PASS |
| T16 | C1 de-vig | PASS | `STRIDE_DEVIG_METHOD=proportional` | `method=` on `RTP.calculate_overround`; **not** a new module, **not** `mc_api.py:7636` | none | PASS | same position; thresholds re-read, not ported | PASS |
| T17 | A3 renorm | PASS | `STRIDE_RENORM_FINAL` | reads `mc_recalibration.transform_race` before writing anything | none | PASS | appended after `:693`, nothing reordered | PASS |
| T18 | A1 post-blend calib | PASS | `STRIDE_POSTBLEND_CALIB` | one calibrator, not a seventh; `calibration_model.py` is called | none | PASS | **moves one step** — justified with numbers + rollback in the ticket | PASS |
| T19 | A2 family | PASS | `STRIDE_CALIBRATOR_FAMILY=isotonic` | `family=` on `ProbabilityCalibrator`, extended not replaced | sidecar records the family | PASS | none | PASS |
| T20 | C5 context mults | PASS | 4 separate flags, all off | reads where `mc_api` already writes; no new producer | none | PASS | the reorder is its own flag, shipped last | PASS |
| T21 | G2 gate/stake split | PASS | `STRIDE_GATE_COUNTERS`, `STRIDE_SEPARATE_STAKE_GATE` | extends `compute_staking`/`evaluate_bet_candidate` | `validate_tips` invariants must still pass | PASS | (b) changes the bet denominator — evidence required from (a) first | PASS |
| T22 | S2 shadow Kelly | PASS | `STRIDE_SHADOW_KELLY` | extends `RS.kelly_stake` (`:309-320`), not `portfolio_risk.py` | 1 additive column, new name (not the `kelly_stake` decoy) | PASS | none | PASS |
| T23 | M2 relative fundamentals | PASS | `STRIDE_RELATIVE_FUNDAMENTALS` | extends `relative_market.py` | contract grows in **both** `retrain_v2` and `ml_model` byte-identically | PASS | none | PASS |
| T24 | M3 field-size weights | PASS | `STRIDE_FIELDSIZE_WEIGHTS` | `sample_weight` on the existing `.fit()` calls | none | PASS | none | PASS |
| T25 | M5 missingness bundle | PASS | `STRIDE_MISSINGNESS_FEATURES` | calls `glicko2_elo.py`, does not rewrite it; **does not** extend `TargetEncoder` | contract grows in both lists | PASS | none | PASS |
| T26 | M4 training pace | PASS | `STRIDE_TRAINING_PACE_FEATURES` | producers go in `form_feature_builder.py`; `mc_api`'s extractors are **not** duplicated | none | PASS | none | PASS |

**Three matrix-wide notes.** (i) Every flag in the G2 column must also be added to `.env.example`,
which is missing six live flags today (drift D-2, **re-verified against the template this session** —
`STRIDE_CL_BLEND`, `STRIDE_PREDICTABILITY_GATE`, `STRIDE_ACCURACY_WEIGHTS`,
`MC_ENABLE_SECTIONAL_FRANKING`, `MC_ENABLE_JOCKEY_EFFICIENCY`, `LLM_MODEL` are all absent, and
`CONSENSUS_CONFIRM_THRESHOLD=45` is present with zero code references) — a new flag added the same way
would be invisible too. (ii) Every ticket follows the `STRIDE_CL_BLEND` precedent at `RTP:591`:
*"Default: off, byte-identical"*, failing safe three ways — missing artifact falls back, wrong stage
refuses, transform exception is a non-fatal warning with the original value surviving. **This is the
config mechanism named in `SYSTEM_MAP §4`, and it was re-verified in source this session: there is no
config module, the idiom is a `STRIDE_*` environment variable read at call time via
`os.environ.get("<FLAG>", "false").strip().lower() in ("true", "1", "yes")`.**

**(iii) The one guardrail-2 exception in this plan, stated explicitly rather than left as "none
needed".** T13 ships **no environment flag**. Guardrail 2 is non-negotiable, so the exception is
recorded and argued rather than assumed: `rank_model.py` has **zero importers** and constraint 15
requires it to keep them, so an env flag would advertise a production path that must not exist. The
guardrail's actual requirements — *default = today's behaviour* and *A/B-able* — are met by the
`backend="lightgbm"` default argument plus the `--backend catboost` CLI selector, which is the
module's own existing argument convention (`--train`, `--model-path`) and therefore also satisfies
guardrail 5. **Condition on the exception: if T13 ever acquires an importer, it acquires a flag in the
same change.** No other ticket in this plan may cite T13 as precedent for shipping unflagged.

---

## 3. Tickets

## T1. Live wrapper outcome ledger — settle `bet_pick` at tipped price and at SP   `[ROI]` · `[quick-win]`

**What to build.** `shadow_pl_tracker` today records one row per convergence tier and settles it at
`sp = res.get("sp") or tipped_odds or 0` (`SPL:290`), then `pl = round(float(sp) - 1, 2)` (`SPL:299`).
That answers *"what would this have returned at the one price the punter demonstrably did not take?"*,
and the bias correlates with pick quality — good picks shorten so ROI is understated, bad picks drift
so it is overstated. Build a second settlement alongside the first, never replacing it.

For every published pick — **including the ones the system refused** — record the shipped decision
context and settle it twice. Concretely: (a) six additive columns on `stride_tip_results`
carrying `tipped_odds_source`, `selection_origin`, `crowd_classification`, `raw_model_pct`,
`win_pct_published`, `should_bet_published`; (b) two additive settled columns `pl_at_tipped` and
`pl_at_sp`; (c) `cmd_report` gains a per-`selection_origin` and per-`crowd_classification` breakdown.
`pl_at_tipped` uses the price in `tipped_odds` (the price the tip was published at);
`pl_at_sp` uses `api_sp` and reproduces the existing `profit_loss` exactly. The existing
`profit_loss` column keeps its current semantics and its current formula — R3-F7 says this
explicitly and it is what makes the change non-destructive.

The refused set is the whole point. `crowd_bet_decision` (`consensus_blender.py:262-263`) returns
`False, "MODEL_ONLY — archetype trap, no crowd support"` **unconditionally**, discarding the model's
own best value bet whenever no tipster agrees. `SYSTEM_MAP §9 Q7` calls what those returned *"the
single highest-leverage unknown in the system"*. `cmd_record` already inserts non-bet tiers
(`is_shadow = tier not in BET_TIERS`, `SPL:180`), so the plumbing exists — what is missing is the
origin/classification columns and the second settlement.

Scratchings: the existing `pos is None → SCRATCHED, pl = 0` branch (`SPL:294-296`) is preserved for
both new columns. A row with no `api_sp` gets `pl_at_sp = NULL`, not a fabricated value — the current
`or tipped_odds or 0` fallback is exactly the defect being fixed, so it must not be copied into the
new column.

**Where it lives.** `server/python/shadow_pl_tracker.py` — extend `cmd_record` (`:132`),
`cmd_results` (`:232`) and `cmd_report` (`:325`). New columns go into the module's own
`MIGRATION_SQL` block (`SPL:50-68`), which already uses `ALTER TABLE … ADD COLUMN IF NOT EXISTS` and
is applied by `ensure_schema` on every command — that is the repo's additive-schema idiom and it is
already inside this file. Also add a reviewable copy as `migrations/shadow_ledger_v2.sql` beside
`migrations/final_prob_audit.sql`, matching that file's comment style (why the column exists, who
writes it, that the writer self-heals). Source of the origin values:
`RTP.annotate_pick_contract` writes `selection_origin` and `should_bet` into each pick, and the crowd
gate stamps `crowd_gated` / `crowd_promoted` (`RTP:2638-2757`). No new module — guardrail 3 names
`shadow_pl_tracker.py` as the existing surface.

**Interface contract.** `shadow_pl_tracker.py` is untyped stdlib-style; match it, do not add hints.

```python
def cmd_record(race_date, conn=None):        # existing signature — unchanged
def cmd_results(race_date):                  # existing signature — unchanged
def cmd_report(group_by="convergence_tier"): # NEW optional arg, default = today's behaviour

def _ledger_v2_enabled():
    """STRIDE_LEDGER_V2 gate, inline default-off idiom (RTP:591 precedent)."""

def _settle_prices(res, tipped_odds):
    """Return (sp_or_none, pl_at_tipped, pl_at_sp) for one settled row.
    Never substitutes tipped_odds for a missing SP."""
```

Call order: `cmd_record` builds `to_insert` at `SPL:173-181` / `:199-207` — the six context values
are appended to each tuple and to the column list at `SPL:213-221`. `cmd_results` computes
`result`/`pl` at `SPL:294-305`; `_settle_prices` is called immediately before the `UPDATE` at
`SPL:307-313` and its two values are added to the `SET` clause. `cmd_report` is called from `main()`
at `SPL:439`; the `group_by` argument is passed through from a new `--by` CLI flag on the existing
`report` subparser (`SPL:429`).

**Pseudocode / algorithm sketch.**
1. `ensure_schema(conn)` runs `MIGRATION_SQL` (extended). Idempotent; safe on every invocation.
2. If `STRIDE_LEDGER_V2` is off: `cmd_record`/`cmd_results` behave exactly as today; the new columns
   stay `NULL`. This is the byte-identical off-path.
3. On: for each row in `cmd_record`, look up `tips_lookup[key]` (already built at `SPL:148-157` from
   `full_field` **and** `top_picks`) and read `selection_origin`, `should_bet`, `raw_model_pct`,
   `win_pct`. Missing key ⇒ write `NULL`, never `0` — a zero is indistinguishable from a real value.
4. `crowd_classification` comes from `convergence_output` when `db_rows` is non-empty; from
   `_infer_tier_from_json` (`SPL:103`) on the JSON fallback path. Record which source was used in
   `tipped_odds_source` so the two populations can be separated later.
5. In `cmd_results`: `sp = res.get("sp")`. If `sp` is falsy ⇒ `pl_at_sp = NULL`. Else
   `pl_at_sp = sp - 1` on WIN, `-1` on PLACE/LOSS, `0` on SCRATCHED.
   `pl_at_tipped` uses `tipped_odds` under the same three branches; if `tipped_odds <= 1` ⇒ `NULL`.
6. `cmd_report`: `GROUP BY` the requested column. Report `n`, wins, hit rate, ROI at tipped, ROI at
   SP, and the difference. Print `MODEL_ONLY` even though it is never bet.
7. Edge cases — thin fields and single-runner groups do not apply (this is a per-pick ledger, not a
   within-race operator). Division by zero: every rate is guarded by `if bets > 0` exactly as
   `SPL:361-362` already does. Duplicate rows are prevented by the existing `existing` set at
   `SPL:159-163`.

**Config / feature flag.** `STRIDE_LEDGER_V2`, default `false`, read at call time in
`_ledger_v2_enabled()` using the repo's inline idiom
`os.environ.get("STRIDE_LEDGER_V2", "false").strip().lower() in ("true", "1", "yes")`. Add to
`.env.example` under a new `# Phase-4 measurement flags` block. The schema migration is applied
unconditionally (columns are additive and inert when unwritten); only the *writes* are gated.

**Acceptance criteria.**
- **Coverage:** `settled_rows_with_pl_at_sp / published_bet_picks ≥ 0.95` over one full calendar
  month. Below that, the ledger is not trustworthy and nothing downstream may quote it.
- **Reproduction:** on ≥ 200 settled rows, `pl_at_sp` equals the existing `profit_loss` **to the
  cent** on every row where `api_sp` is non-null. Any mismatch is a bug in the new path, not a
  finding.
- **Completeness:** every `selection_origin` value listed in `SYSTEM_MAP §8`
  (`model_backed | tip_only | filtered_substitute | market_unavailable | raw_model_leader |
  crowd_gated | crowd_promoted`) appears at least once in the first month's report, or the missing
  ones are explained.
- **Deliverable:** the first `cmd_report --by selection_origin` printing ROI at tipped price and at
  SP side by side for at least `CONFIRMED`, `CROWD_ONLY` and `MODEL_ONLY`.
- **Do not quote an ROI number from this yet.** At σ = 3.396 (Phase 3 §3), 200 bets gives a 95% CI of
  roughly ±47pp. Report it with the CI or not at all — that constraint is T7's job to enforce.

**Rollback plan.** Set `STRIDE_LEDGER_V2=false`; recording and settlement revert to today's exact
behaviour. The added columns remain but are inert. To clean up fully:
`ALTER TABLE stride_tip_results DROP COLUMN IF EXISTS pl_at_tipped, …` — but note the existing
`profit_loss` series is untouched throughout, so a rollback loses only the new columns, never the
historical P&L.

**Conflicts checked.** `cmd_report`'s existing tier table is the only consumer of
`MIN_BETS_REPORTABLE` (`SPL:323`, used `:363`) — T7 changes that constant's *consumer*, so T1 and T7
touch the same function and should be merged into one PR if built together. `learn_from_results_v2`
runs the nightly loop that calls this module; its PID lock is unaffected. `ensure_schema` is already
called on every command including `cmd_report`, so a read-only invocation will now also run the new
`ALTER TABLE` statements — acceptable (they are `IF NOT EXISTS`), but note it means `cmd_report` is
not read-only and never was. No pipeline decision reads any new column.

---

## T2. CLV columns in `cmd_report` — mean CLV and % positive CLV   `[ROI]` · `[quick-win]`

**What to build.** Two derived report columns, no schema change, no new data. For each settled row
with both prices present, closing-line value is `clv = tipped_odds / sp_odds - 1`. In Australia the
SP **is** the closing line, and both prices are already in the same row: `cmd_record` inserts
`tipped_odds` (`SPL:216`), `cmd_results` writes `api_sp` and `tipped_horse_sp` (`SPL:310`).
**The join is done; the ratio is never taken.**

Report, per convergence tier and per price band: `n_clv` (rows with both prices), mean CLV, the
standard error and a t-statistic against zero, and the share of rows with `clv > 0` with a binomial
test against 0.50. Price bands must match the bands the gate uses so the two are comparable:
`<$3`, `$3–$5`, `$5–$15`, `$15–$30`, `>$30` (`RTP:1812-1825`).

Why this and not ROI: R3-F5/F6's arithmetic gives **~400 settled bets** for a CLV signal against
**~3,038** for the equivalent ROI claim at +12.3%. That 7.6× reduction *is* the value of the ticket.

Carry the four limits honestly in the report header, because R3 states plainly that every direct
CLV→profit source it found was practitioner, not peer-reviewed: CLV is reference-market dependent,
not realisable as cash, subject to self-impact, and **inherits favourite–longshot bias from a biased
close**. The fourth is actionable here — once T16 lands, recompute CLV against a **de-vigged** close
rather than raw SP, and report both.

**Where it lives.** `server/python/shadow_pl_tracker.py`, `cmd_report` (`:325`) only. No other file.
Guardrail 3: this is the module that owns settled prices.

**Interface contract.**

```python
def _clv(tipped_odds, sp_odds):
    """Closing-line value for one row. Returns None when either price is
    missing or <= 1 — never 0.0, which would be a real CLV value."""

def _clv_summary(rows):
    """rows: sequence of (tipped_odds, sp_odds). Returns
    {"n": int, "mean": float|None, "se": float|None, "t": float|None,
     "pct_positive": float|None}. Returns n=0 and Nones on an empty set."""

def cmd_report(group_by="convergence_tier"):   # same signature as T1 — build them together
```

Called from `cmd_report` after the existing tier query at `SPL:330-342`, as a second SELECT that
pulls `convergence_tier, tipped_odds, api_sp` for non-PENDING, non-SCRATCHED rows. Printed as a
second table below the existing one so the current table's format is unchanged (guardrail 4: log
formats are additive, not modified).

**Pseudocode / algorithm sketch.**
1. Guard: if `STRIDE_CLV_REPORT` is off, skip the whole block — the report is byte-identical to today.
2. `SELECT convergence_tier, tipped_odds, api_sp FROM stride_tip_results
   WHERE result NOT IN ('PENDING','SCRATCHED') AND api_sp IS NOT NULL AND tipped_odds > 1`.
3. Bucket by tier, then by price band on `tipped_odds`.
4. Per bucket: `clv_i = tipped_odds_i / sp_i - 1`; `mean = Σ/n`; `se = sd/√n` with `ddof=1`;
   `t = mean/se` when `n ≥ 2` and `se > 0`, else `None`. `pct_positive = count(clv>0)/n`.
5. Edge cases: `n = 0` ⇒ print `-` (the module's `_fmt`-style convention; `audit_coverage_report`'s
   `_fmt_table` renders `None` as `-` and is the house precedent). `n = 1` ⇒ print the mean, no `t`.
   `sp_odds <= 1` (a data error) ⇒ excluded by the SQL, and counted separately as `excluded_rows` so
   silent filtering is visible. Division by zero cannot occur: `sp > 1` is enforced in SQL.
6. Scratchings are excluded — they have no closing line worth scoring.

**Config / feature flag.** `STRIDE_CLV_REPORT`, default `false`, inline idiom, read once at the top
of `cmd_report`. Add to `.env.example`.

**Acceptance criteria.**
- Metric: mean CLV with a t-test against zero, and % positive CLV with a binomial test against 50%.
- Minimum sample: **≥ 400 settled bets** in the tier being judged (R3-F6). Tiers below that print
  their `n` and are labelled `NOT REPORTABLE` — the same discipline T7 formalises.
- **Pre-registered threshold:** the `CONFIRMED` tier shows mean CLV > 0 at p < 0.05 over ≥ 400
  settled bets. **If it does not, the picks are not beating the market**, and no amount of staking
  (T22) or calibration (T18/T19) work will make them profitable. That is a result worth having early
  and it should be reported as such, not buried.
- Unit-test level: a self-test asserting `_clv(3.0, 2.0) == 0.5`, `_clv(2.0, 3.0) ≈ -0.333`,
  `_clv(None, 2.0) is None`, `_clv(2.0, 1.0) is None`. Wire it into `.github/workflows/ci.yml:33-42`
  alongside the eight existing module self-tests.

**Rollback plan.** `STRIDE_CLV_REPORT=false`. Nothing is written, so there is no state to clean up —
this ticket is a pure SELECT.

**Conflicts checked.** Shares `cmd_report` with T1 and T7; build all three in one PR or sequence them
T1 → T7 → T2 to avoid three rewrites of the same function. Adding a self-test to `shadow_pl_tracker`
means CI will import it — the module is stdlib + `psycopg2`-lazy (`SPL:39` imports inside the
function), so the self-test must not touch `_get_connection`. `tips_day_aggregates.py` is the model
here: *"Stdlib-only by design: CI imports and self-tests this module without the pipeline's
database/scientific dependencies."* Nothing else.

---

## T3. Read-only diagnostic battery — nine counters   `[BOTH]` · `[quick-win]`

**What to build.** One read-only script printing nine numbers, plus four runtime counters the
pipeline does not currently emit. Every one was independently nominated by a Phase-2 stream as its
own highest-value next step. Bundled because they share a database session and none justifies a
ticket alone. This ticket **unblocks six others**.

1. **Overround distribution.** Computed at `RTP:432-442`, stored nowhere, aggregated never. Report
   the decile distribution of `calculate_overround(runners)` by field size and by track segment.
   R4 calls this *"the single most valuable cheap measurement in this report"*: R4-F2's entire
   negative-EV claim rests on assumed values of R = 1.15/1.25 which are *"plausible AU assumptions,
   not measurements."* Requires a pipeline-side counter (see below) because the value is not persisted.
2. **`Σ winPercentage` per race.** The three assignment sites are `RTP:624` (CL path), `:661`
   (isotonic) and `:693` (market anchor); each is pointwise and **no renormalisation follows any of
   them**. Report the distribution of the per-race sum from `prediction_audit.final_win_prob`, which
   `store_final_probs_in_audit` (`RTP:1582`) already writes for **every** runner.
3. **Mean `mlPredictedProb` per card vs observed win rate.** Confirms or kills the class-imbalance
   inflation claim (`§A5`) in one line with zero behaviour change. Needs a pipeline counter.
4. **`mc_is_flat` firing rate, by field size.** `mc_spread < 6.0` at `RTP:705`. Needs a counter.
5. **Count of the `valid < 2 → return 1.0` branch** in `calculate_overround` (`RTP:440-441`) — a
   thin race has its *raw* implied probability used as if it were vig-free, which gives the
   thinnest, most scratching-affected races the most optimistic edges. Needs a counter.
6. **Per-feature non-zero / non-NaN counts over the full 113-column contract**, run over **both**
   inference paths: `RTP:2258-2319` **and** `mc_api.extract_all_sophisticated_features`
   (`mc_api.py:5436`). The citation audit is explicit that the "41 dead features" headline is wrong
   on the inference half and that a ticket scoped from the training-side diff alone would be pointed
   at the wrong end.
7. **`apply_safety_filters` fallback firing rate** — the `filtered = sorted(…)[:3]` three-shortest-
   priced-runners path at `RTP:935-936`, which directly contradicts the value philosophy.
8. **Intelligence-override firing rate** — `_check_intelligence_override` (`RTP:1727`), which
   bypasses the entire edge gate.
9. **`true_market` under proportional vs power vs Shin, tabulated by price band.** Computation only;
   **no method is adopted here** — this is the empirical input to T16.

**Where it lives.** New `server/python/research/live_diagnostics.py`. **Placement corrected by the
Phase-4 audit:** `server/python/research/` does **not** hold "twelve diagnostics" — it holds two
standalone scripts (`performance_autopsy_last21days.py`, `investigate_sectional_market_going.py`) plus
a 9-module `winner_pattern_gap/` package, and the template this ticket copies,
`audit_coverage_report.py`, lives one level up in `server/python/`. Either placement is defensible;
`research/` is chosen because this script is a manual diagnostic, not a pipeline module. Follow
`audit_coverage_report.py` exactly: a
`QUERIES` list of `(title, sql)` tuples, `conn.set_session(readonly=True)` before any execute
(`audit_coverage_report.py:221`), a `_FORBIDDEN` regex, `_fmt_table` for output, and a `_self_test()`
asserting **every query starts with SELECT/WITH and contains no mutating keyword**. Invoked by a new
`workflow_dispatch`-only Action beside `.github/workflows/audit-coverage.yml`.

Counters 1, 3, 4, 5, 7, 8 need runtime values the pipeline does not persist. Add them as **new**
stderr lines in the house bracketed-tag style — `[DIAG]` — inside `RTP`, at:
`calculate_overround` (`:432`, counters 1 and 5), the ML block (`:2324-2326`, counter 3 — the
`[ML]` line already computes `avg_ml`, so counter 3 is one extra field), `calibrate_and_score`
(`:705`, counter 4, beside the existing `[MC_FLAT]` line), `apply_safety_filters` (`:935`,
counter 7), `evaluate_bet_candidate` (`:1796`, counter 8). This is the repo's own stated convention:
*"a positive assertion (a printed count, a validator line) rather than relying on the absence of an
exception."*

**Interface contract.** Mirror `audit_coverage_report.py`'s untyped, stdlib-first style.

```python
QUERIES = [ (title, sql), ... ]          # module-level, same shape as audit_coverage_report.QUERIES
_FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|"
                        r"GRANT|REVOKE|REFRESH|COPY|VACUUM|LOCK|CALL|DO)\b", re.IGNORECASE)

def run_report(conn):        # identical contract to audit_coverage_report.run_report
def _devig_table(odds_rows): # counter 9: proportional / power / shin on stored fields, no adoption
def _self_test():            # asserts SELECT-only, formatter behaviour, and _devig_table's algebra
```

Pipeline side, in `RTP`:

```python
def _diag_counters_enabled():
    """STRIDE_DIAG_COUNTERS gate — inline default-off idiom."""

def _diag(tag, **fields):
    """Emit one `  [DIAG] tag k=v k=v` line to stderr when enabled. No-op when off."""
```

`_diag` is called from `calculate_overround` (after `:442`'s return value is computed — restructure
to a local then return, no behaviour change), from the `[ML]` print at `:2326`, from the `[MC_FLAT]`
branch at `:706-707`, from the `filtered = sorted(...)` fallback at `:935`, and from
`evaluate_bet_candidate` at `:1796` when `intel_override` is true.

**Pseudocode / algorithm sketch.**
1. `_devig_table`: for each stored race field of decimal odds `o_1..o_n`:
   - **proportional**: `p_i = (1/o_i) / Σ(1/o_j)` — today's method, `RTP:673-674`.
   - **power**: find `k` with `Σ (1/o_j)^k = 1` by bisection on `k ∈ [0.5, 2.0]`. The function is
     strictly decreasing in `k` when the book is over-round, so bisection converges; 40 iterations
     gives ~1e-12. Then `p_i = (1/o_i)^k`.
   - **shin**: solve for the insider proportion `z` in
     `p_i = (√(z² + 4(1-z)·(1/o_i)²/Σ(1/o_j)) - z) / (2(1-z))`, again by bisection on `z ∈ [0, 0.2]`
     until `Σ p_i = 1`.
   - Tabulate `p_i` under all three by price band, and the signed differences.
2. **Edge cases, all of which must be counted, not silently skipped:**
   - fewer than 2 quoted runners ⇒ **no de-vig is defined**; emit `n_thin` and exclude the race from
     the comparison. Do **not** return 1.0 the way `calculate_overround` does — this diagnostic
     exists partly to measure how often that branch fires.
   - `Σ(1/o_j) ≤ 1.0` (an under-round, possible on a scratched-out field): bisection has no root in
     `k ∈ [0.5, 2.0]`; clamp to `k = 1.0`, count it as `n_underround`.
   - a single quoted runner ⇒ `p = 1.0` trivially; excluded and counted.
   - scratchings: the diagnostic runs on the field as stored; report `n_runners_quoted` next to
     `field_size` so the gap is visible.
   - `o_i <= 1` ⇒ excluded by the same `odds > 1` test `extract_odds` already applies (`RTP:457`).
3. Every SQL query is a plain `SELECT`/`WITH`. Type-agnostic date handling (`left(x::text, n)`),
   copied from `audit_coverage_report.py`'s comment at `:38-40`, because `race_date` is text in
   `prediction_audit` and date-or-text elsewhere.

**Config / feature flag.** `STRIDE_DIAG_COUNTERS`, default `false`, gating only the pipeline-side
`_diag` lines so the production log is byte-identical when off. The script itself needs no flag — it
is a separate `workflow_dispatch` entry point and cannot run as part of the daily pipeline. Add
`STRIDE_DIAG_COUNTERS` to `.env.example`.

**Acceptance criteria.**
- Binary: all nine numbers exist and are reproducible on a re-run.
- **Threshold:** the report runs green over one month of data, and `SYSTEM_MAP §9` Q6, Q8, Q10, Q11,
  Q12 each move from "unknown" to a number.
- CI: the self-test runs with no `--run` and exits 0; add it to `.github/workflows/ci.yml`'s
  self-test list. **Note the exact invocation** — that step does `cd server/python` first
  (`ci.yml:34-35`) and then calls bare module names, so the added line is
  `python research/live_diagnostics.py`, not a repo-root path. The self-test must assert
  `_devig_table` reproduces proportional de-vig exactly on a 3-runner book and that power de-vig with
  `k = 1` is identical to proportional (an algebraic identity — if it is not, the bisection is wrong).
- **Three pre-registered alarm thresholds, recorded now so they cannot be moved later. Each carries a
  minimum sample, added by the Phase-4 audit — an alarm with no `n` is not a pre-registration:**
  - `Σ winPercentage` deviates from 100 by more than **±2pp on the median race**, over **≥ 500 scored
    races** ⇒ **T17 is promoted immediately**. (A median is robust, but the ±2pp *decision* is what
    closes or opens a ticket, so it needs the same floor as any other gate.)
  - `mc_is_flat` fires on more than **15% of races** over **≥ 500 races** — at 15% and n = 500 the
    binomial 95% half-width is ≈3.1pp, tight enough to separate 15% from 10% ⇒ **T21 is promoted
    immediately**. The by-field-size breakdown needs **≥ 150 races per band** to be quotable.
  - the `< 2 quotes` branch fires on more than **2% of races** over **≥ 1,000 races** — a 2% rate
    needs the larger floor to be estimated at all (at n = 500 the 95% half-width is ≈1.2pp, i.e. the
    same order as the threshold) ⇒ **T16's thin-race arm is promoted immediately**.
  - Below these floors the counter is reported with its `n` and labelled `NOT REPORTABLE`, exactly as
    T7 requires everywhere else. **No ticket may be promoted or closed on an under-powered counter.**

**Rollback plan.** `STRIDE_DIAG_COUNTERS=false` removes every added log line. Delete the workflow
file and the module to remove the rest. No database state is created — the session is opened
read-only server-side, so the workflow **cannot** mutate the database (`docs/12:193-195`).

**Conflicts checked.** `_diag` inside `calculate_overround` requires restructuring `RTP:432-442` from
two `return` statements to a local variable and one return — behaviour-identical, but it is an edit
to a function T16 also edits, so sequence T3 before T16. The `[ML]` and `[MC_FLAT]` lines already
exist; T3 adds fields to a *new* line rather than editing theirs, preserving the existing log format.
`audit_coverage_report.py` is already in CI and its `_FORBIDDEN` regex is copied verbatim — do not
"improve" it (guardrail 5). Nothing reads any output of this ticket programmatically.

---

## T4. Error bars, trial counts and an (odds × edge) ROI surface   `[ROI]` · `[quick-win]`

**What to build.** Three additions to backtest reporting, none of which changes a strategy.

(i) **Per-strategy uncertainty.** `backtest_v2_metro.py`'s per-strategy keys are exactly
`label, bets, wins, strike_rate, staked, returned, pnl, roi` — no CI, no SE, no t. Add, computed
**across bets** (not across folds): `pl_sd` (per-bet profit/loss standard deviation, `ddof=1`),
`se = pl_sd/√n`, `t = mean_pl/se`, and a 95% CI on ROI as `mean_pl ± 1.96·se` expressed in percent.
Each bet's P/L at unit stake is `sp - 1` if won else `-1`, so the series is already implicit in the
stored `bets` list.

(ii) **Trial count.** Print `n_strategies_tried = len(STRATEGIES)` (currently **6**,
`backtest_v2_metro.py:157-164`) in the report header, alongside the winner. R3-F17 is explicit that
the DSR/MinBTL equations could not be obtained, so **do not attempt Deflated Sharpe or PBO/CSCV** —
implement only the two things that need no equations. R3-F17's own summary agrees: at N = 6 trials,
352 races, 142 bets and t = 0.43, *"steps 2–5 are unnecessary because step 1 already settles it."*

(iii) **The (odds-decile × edge-decile) realised-ROI surface**, with per-cell `n`, mean ROI and CI.
This is the real deliverable — R3-F3 calls it *"worth more than any new feature in the roadmap"*
because it tests whether `modelEdge` is monotone at all. Cells are built on `sp_odds` deciles and on
the **vig-inclusive** edge `prob - 1/sp` that `backtest_v2_metro.py:215-217` already computes, so the
surface measures the same quantity the validated band thresholds.

Also, in `walk_forward_backtest.py`: its t-distribution CIs at `:220-229` and `:250-262` are computed
**across folds**, so they measure fold-to-fold dispersion of a mean, not the sampling error of the
bet population. **Print that distinction, do not silently fix it** — relabel the emitted keys
`ci_95_across_folds` and add a one-line note in the report header. Renaming an emitted key is an
output-contract change, so keep the old key too and add the new one beside it.

**Where it lives.** `server/python/backtest_v2_metro.py` (`STRATEGIES` and `STAKE = 100` at
`:157-166`; the per-strategy assembly downstream of `run_backtest` at `:169-232`) and
`server/python/walk_forward_backtest.py` (`aggregate_metrics` at `:202`). `server/python/backtest.py`
(v1, 13 sweeps plus `optimize_threshold`, **no purge gap**) gets the trial-count header line only —
docs say to use `walk_forward_backtest.py` when rigour matters, so do not invest further there.

**Interface contract.** `walk_forward_backtest.py` is fully type-hinted (`-> Dict[str, Any]`);
`backtest_v2_metro.py` is not. Match each file.

```python
# backtest_v2_metro.py — untyped, matching the file
def _bet_series(bets):
    """[{'sp':…, 'won':…}, …] -> list of unit-stake P/L floats (sp-1 or -1)."""

def _roi_stats(bets):
    """-> {'n', 'mean_pl', 'pl_sd', 'se', 't', 'roi_pct', 'roi_ci95': [lo, hi]}.
    n < 2 -> sd/se/t = None, roi_ci95 = [None, None]."""

def _roi_surface(df, n_odds_bins=10, n_edge_bins=10):
    """Realised-ROI surface over (sp-decile x vig-inclusive-edge-decile).
    Returns a list of cell dicts with n, mean_roi, ci95, and a `quotable` bool."""

# walk_forward_backtest.py — hinted, matching the file
def aggregate_metrics(fold_results: List[Dict]) -> Dict[str, Any]:   # existing signature unchanged
```

Call order: `_roi_stats` is called once per strategy where the summary dict is assembled after
`run_backtest` returns (`backtest_v2_metro.py:169-232`); `_roi_surface` is called once on the scored
`df` that `run_backtest` returns as its second value, which already carries `norm_prob` (`:175-177`)
and `sp_odds`. `aggregate_metrics` keeps its signature and simply emits both key names.

**Pseudocode / algorithm sketch.**
1. Per strategy: `pl = [_bet_series(bets)]`; `n = len(pl)`; `mean = Σpl/n`;
   `sd = sqrt(Σ(x-mean)²/(n-1))`; `se = sd/√n`; `t = mean/se`; `roi_ci95 = [(mean-1.96se)*100,
   (mean+1.96se)*100]`.
2. Surface: bin `sp_odds` into deciles by `pd.qcut(..., duplicates="drop")` and the vig-inclusive
   edge likewise. For each cell compute `n`, mean P/L, ROI% and CI as above.
3. **Cell quotability rule, enforced in code, not in prose:** `quotable = n >= 100`. At σ = 3.396 a
   100-bet cell has a 95% CI of roughly ±67pp. Non-quotable cells still print, but with a
   `NOT QUOTABLE` marker so a later reader cannot lift the number out of context.
4. Edge cases: `n = 0` cells are omitted; `n = 1` prints the P/L with no CI; `qcut` collapsing to
   fewer than 10 bins (few distinct edges) is handled by `duplicates="drop"` and the realised bin
   count is printed. Missing `sp_odds` rows are already excluded upstream by
   `backtest_v2_metro.py:262-264`'s `_sp > 1.0` filter — reuse it, do not write a second one.
   Division by zero in ROI is impossible because `n = 0` cells are dropped before the division.

**Config / feature flag.** `STRIDE_BACKTEST_STATS`, default `false`. When off, both reports emit
exactly today's keys and today's stdout. When on, the new keys and the surface section appear. Read
at the top of each report-assembly function via the inline idiom. Add to `.env.example`.

**Acceptance criteria.**
- Every strategy row carries `n`, `pl_sd`, `se`, `t` and a 95% CI; the header carries the trial count.
- **Minimum sample before the surface may be used for anything:** ≥ **1,000 bets** total, and
  **no cell with fewer than 100 bets may be quoted**.
- **Pre-registered claim to test:** R3-F3's decomposition finds `$5–$15, edge 3–5%` at **+40.1% on
  81 bets** against `$5–$15, edge ≥ 5%` at **−14.8% on 54 bets** — ROI *falling* as modelled edge
  rises, *"the canonical signature of a threshold fitted to noise."* If the surface reproduces the
  non-monotonicity on ≥ 1,000 bets, then the conviction ladder (`RTP:836-843`, +3.0/+2.0/+1.0 rising
  in edge) and the longshot keep-rule (`RTP:917`, `odds≥15 & edge>2 & raw≥8`) are both built on a
  quantity that does not behave as assumed, and **T16 and T18 move up the order**.
- Regression: with the flag off, `examples/backtest_summary.json` regenerates byte-identically.

**Rollback plan.** `STRIDE_BACKTEST_STATS=false`. No artifacts to clean up — these harnesses write
JSON under `server/python/backtest_results/` and `examples/`, and the flag-off path writes the same
files it always did.

**Conflicts checked.** `examples/backtest_summary.json` is a committed artifact and the source of the
README's headline numbers; regenerating it with new keys changes a committed file, so either keep the
flag off when regenerating or update the README's provenance note in the same PR. `backtest.py` (v1)
has no purge gap and its numbers should not be compared with `walk_forward_backtest.py`'s — the
trial-count line should say so. `walk_forward_backtest.py:394` calls `RacingMLModel.train`, which
carries the five target-encoded columns that are informative at fit time and identically `0` at
scoring time (Phase 3 §5.1) — **so this harness is not measuring the production trainer**, and any
threshold pre-registered on it must say so. T14 fixes the adjacent `fillna(0)` defect; the
target-encoder half is documented there, not here.

## T5. Calibration measurement upgrade   `[BOTH]` · `[quick-win]`

**What to build.** Four metrics alongside the existing `brier` / `log_loss` / `ece` scalars, all
additive keys. (i) **Equal-mass ECE**: instead of `np.linspace(0,1,11)` fixed-width bins
(`walk_forward_backtest.py:100-117`), sort predictions and cut into 10 bins of equal *count*, then
sum `(n_b/N)·|mean_pred_b − mean_actual_b|`; plus the debiased variant, which subtracts the expected
bin-level sampling noise `Σ (n_b/N)·p̄_b(1−p̄_b)/n_b` before taking absolute values. This matters
because with a ~9.7% base rate and an MC ceiling of 60% (`mc_api.py:7398`), bins above 0.6 are empty
and `[0.0,0.1]`/`[0.1,0.2]` hold nearly all the mass — **effective B ≈ 3, not 10**. (ii) A
**PAV/CORP miscalibration score** with no binning hyper-parameter: fit an isotonic curve to the
(prediction, outcome) pairs using the hand-rolled `_pool_adjacent_violators` already in
`mc_recalibration.py:157-182`, then report `mean|p_i − PAV(p_i)|`. (iii) **Murphy's Brier
decomposition**: on the same PAV bins, `reliability = Σ(n_b/N)(p̄_b − ȳ_b)²`,
`resolution = Σ(n_b/N)(ȳ_b − ȳ)²`, `uncertainty = ȳ(1−ȳ)`, with `Brier ≈ rel − res + unc`.
(iv) **Calibration slope and intercept**: logistic regression of the outcome on `logit(p)`, one
feature, with standard errors, stratified by odds band, field size and going. Slope < 1 means
predictions are too extreme; the in-code Kelly-audit comment at `RTP:676-678` — *"$1-3 horses win
41%, model predicts 17% after blend"* — **is a verbal statement of a calibration-slope failure**.

**Where it lives.** `server/python/walk_forward_backtest.py`: `compute_ece` (`:100-117`, called at
`:153`) gains siblings; `compute_fold_metrics` (`:120`) assembles the new keys; `aggregate_metrics`
(`:202`) aggregates them by extending its `scalar_keys` list at `:209`. Reuse the PAV at
`mc_recalibration.py:157` rather than writing a second one (guardrail 3).
**Interface correction from the Phase-4 audit — this one blocks the ticket if taken as written.**
`_pool_adjacent_violators` is **a method of `MCRecalibrator`** (class opens at
`mc_recalibration.py:21`, `__init__` at `:31`), *not* a module-level function, so it cannot be
"imported" the way this ticket originally said. Two lawful options, in preference order: (a) call it
on a throwaway instance — `from mc_recalibration import MCRecalibrator;
MCRecalibrator()._pool_adjacent_violators(y)` — first **confirming `__init__` at `:31` opens no
database connection** (the module's `psycopg2` import and `psycopg2.connect` sit at `:41-45`, inside a
*different* method, so the constructor looks clean, but verify before CI imports it); or (b) if the
constructor turns out to require state, promote the algorithm to a module-level `_pool_adjacent_violators(y)`
in `mc_recalibration.py` and have the method delegate to it — **additive, keeps one implementation,
and does not change any existing behaviour** (guardrail 1). **Copying the algorithm is not an option**
(guardrail 3). Slope/intercept uses
`sklearn.linear_model.LogisticRegression` — `statsmodels` is not among the 32 deps in
`requirements.txt` and must not be added.

**Interface contract.** File is fully hinted; match it.

```python
def compute_ece(y_true: np.ndarray, y_pred: np.ndarray, n_bins: int = 10) -> float:   # UNCHANGED
def compute_ece_equalmass(y_true: np.ndarray, y_pred: np.ndarray, n_bins: int = 10) -> float:
def compute_ece_debiased(y_true: np.ndarray, y_pred: np.ndarray, n_bins: int = 10) -> float:
def compute_corp_mcb(y_true: np.ndarray, y_pred: np.ndarray) -> float:
def compute_brier_decomposition(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
def compute_calibration_slope(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
```

All six are called from `compute_fold_metrics` immediately after the existing `metrics['ece']`
assignment at `:152-155`, each in its own `try/except` writing `None` on failure — the file's
established idiom for every metric it computes.

**Pseudocode / algorithm sketch.** (1) Equal-mass: `order = argsort(y_pred)`; split into `n_bins`
contiguous chunks; skip chunks of size 0. (2) Debiased: subtract per-bin `p̄(1−p̄)/n_b` from the
squared gap before rooting, and floor the result at 0. (3) CORP: sort by prediction, run PAV on the
outcome vector, interpolate. (4) Decomposition uses the PAV bins so `rel − res + unc` reproduces
Brier to ~1e-9 — **assert that identity in the self-test**. (5) Slope: `x = log(p/(1−p))` with `p`
clipped to `[1e-6, 1−1e-6]` to avoid division by zero and `log(0)`; fit; SE from the inverse Hessian
diagonal (sklearn does not expose it, so compute `sqrt(diag(inv(XᵀWX)))` directly with
`W = diag(p̂(1−p̂))`). Edge cases: a stratum with **zero winners** or zero losers ⇒ the logistic fit
is degenerate ⇒ return `None`, do not fabricate; strata with `< 100 winners` are computed but flagged
`low_power`; `n = 0` returns `0.0` exactly as `compute_ece` already does at `:104-105`.

**Config / feature flag.** `STRIDE_CALIB_METRICS_V2`, default `false`, read once in
`compute_fold_metrics`. Off ⇒ the metrics dict has exactly today's keys. Add to `.env.example`.

**Acceptance criteria.** The harness emits `ece_equalwidth` (an alias of today's `ece`, kept),
`ece_equalmass`, `ece_debiased`, `corp_mcb`, `brier_reliability`, `brier_resolution`,
`brier_uncertainty`, `calib_slope`, `calib_intercept` and their per-stratum versions. **The
difference between equal-width and equal-mass ECE must be published** — the whole claim is that
today's number is biased low and the size of that bias on STRIDE's own data is unknown. Sample: the
slope regression needs ≥ **100 winners per stratum**, so a per-odds-band table is feasible on ~1,000+
races and a per-band × per-field-size table is not. Self-test (wired into CI): on a synthetic
perfectly-calibrated set, all ECE variants < 0.01 and `calib_slope` within 0.1 of 1.0; on a
deliberately over-confident set, `calib_slope < 1` and equal-mass ECE > equal-width ECE.
**Pre-registered threshold:** if `calib_slope` differs from 1.0 by more than 2 SE in any odds band,
**T18 and T19 are promoted** and the `mw` ladder is formally reclassified as an unfitted correction
for a measurable quantity.

**Rollback plan.** `STRIDE_CALIB_METRICS_V2=false`. JSON under `server/python/backtest_results/`
written while on carries extra keys; nothing reads them, so no cleanup is required beyond deleting
those files if a byte-identical history is wanted.

**Conflicts checked.** `walk_forward_backtest.py:394` trains via `RacingMLModel.train`, whose
five target-encoded columns are constant `0` at scoring time — so these metrics describe *that*
model, not `retrain_v2`'s production artifact. State it in the report header. `mc_recalibration.py`
is inert in this tree and its PAV is **reused, not copied** — but it is a *bound method*, so see the
"Where it lives" correction above; whichever of the two lawful routes is taken, CI will import
`mc_recalibration`, so confirm that import pulls no DB dependency (its `psycopg2` import is inside a
method at `:41-45`, not at module scope). T4 also edits `aggregate_metrics` — build
T4 and T5 in one PR or sequence T4 → T5. Nothing on the live scoring path is touched.

---

## T6. Calibrator provenance — a fitting script, a sidecar, a load-time assertion   `[ROI]` · `[quick-win]`

**What to build.** Three things. (a) `fit_calibrator.py`, a script that regenerates
`isotonic_calibrator.pkl` from `training_view_v2` using the **same 14-day purge gap** as
`retrain_v2.DateWindowSplitter`, writing to `models/staging/` and **never** over the live artifact
(guardrail 9: *"Retrains are staged, never auto-promoted"*). Today `ProbabilityCalibrator.fit()`
(`calibration_model.py:26-38`) has **zero callers** and nothing in the repo produces the artifact the
live path loads. (b) A sidecar metadata dict saved beside the pkl:
`{fit_rows, date_range, stage, family, sklearn_version, fitted_at}`. (c) A **printed positive
assertion at load** in `calibrate_and_score` naming the artifact's age, row count and stage, plus a
loud `[CALIB]` warning if the artifact is absent or older than a configurable horizon. Today the load
at `RTP:574-580` is a bare `try/except: pass` — if the pickle is missing, `_calibrator` stays `None`
and **the live chain is silently *ML blend → market anchor* with no calibration at all**.

**Where it lives.** New `server/python/fit_calibrator.py`, beside `server/python/retrain_v2.py` and
`server/python/calibration_model.py` — **not inside** either. Guardrail 1 forbids rewriting working
logic and `ProbabilityCalibrator` is the class to *call*. Load-site assertion at `RTP:574-580`.
A `workflow_dispatch` Action beside `.github/workflows/retrain-model.yml`, following its
staged-artifact-upload pattern. Sidecar path: `models/staging/isotonic_calibrator.meta.json`.

**Interface contract.**

```python
# fit_calibrator.py — hinted, matching retrain_v2's style
def load_calibration_rows(purge_gap_days: int = 14) -> pd.DataFrame:
    """Rows from training_view_v2 strictly older than (max_date - purge_gap_days)."""
def fit_and_stage(family: str = "isotonic", out_dir: str = "models/staging") -> Dict[str, Any]:
    """Fits via ProbabilityCalibrator.fit, writes pkl + sidecar, returns the sidecar dict."""

# run_tips_pipeline.py — untyped, matching the file
def _load_calibrator_with_provenance():
    """Returns (calibrator_or_None, meta_or_None) and prints one [CALIB] line either way."""
```

`_load_calibrator_with_provenance()` replaces the inline block at `RTP:574-580`, at the same position
and returning the same `_calibrator` object — the call order of `calibrate_and_score` is unchanged.

**Pseudocode / algorithm sketch.** (1) `fit_and_stage`: load rows; require `fit_rows ≥ 2000` or print
a loud warning naming Niculescu-Mizil & Caruana's ~2,000-case threshold below which isotonic
overfits — `training_view_v2` splits **106,193 none / 12,590 imported_historical / 794 live_model**
(`docs/12:352`), and **794 live-model rows is deep in the regime where isotonic is the wrong family**;
(2) call `ProbabilityCalibrator(model_path=staged_path).fit(pred, actual)` — it already pickles
itself at `:35-37`; (3) write the sidecar; (4) print row count, date range and family. On the load
side: if the pkl is missing ⇒ `[CALIB] NO CALIBRATOR — running uncalibrated (ML blend → market
anchor)`; if present without a sidecar ⇒ `[CALIB] loaded, provenance UNKNOWN`; if present with one ⇒
print `fit_rows`, `fitted_at`, age in days, and warn above the horizon. Edge cases: an unpicklable or
version-mismatched artifact must be caught and reported as `NO CALIBRATOR`, not swallowed — the
current bare `except Exception: pass` is exactly the failure mode that hides this. Empty fit set ⇒
refuse to write an artifact.

**Config / feature flag.** `STRIDE_CALIBRATOR_ASSERT`, default `false` — when off, the load site
behaves exactly as today (silent). `STRIDE_CALIBRATOR_MAX_AGE_DAYS`, default `"90"`, read only when
the assertion is on. Both added to `.env.example`. **The fitting script is never invoked by the
pipeline** and needs no flag.

**Acceptance criteria.** The sidecar exists and the load line prints a non-empty `fit_rows` and
`fitted_at` on the next production run. **Pre-registered threshold: P0-b is answered definitively.**
If the artifact is **absent** in production, that single fact re-frames T16, T17, T18, T19 and every
published `modelEdge`, and it becomes the highest-priority finding in this plan. Self-test (CI):
`fit_and_stage` on a synthetic monotone set produces a calibrator whose output is monotone
non-decreasing and bounded by `[0.01, 0.95]` (`calibration_model.py:30-32`), and the sidecar
round-trips. **The newly fitted artifact must not be promoted** on the strength of existing numbers —
refitting the same possibly-wrong family is not the goal; establishing what is actually running is.
The family question is decided in T19.

**Rollback plan.** `STRIDE_CALIBRATOR_ASSERT=false` restores the silent load. Delete
`models/staging/isotonic_calibrator.pkl` and its sidecar; `models/` is git-ignored and never
committed, so nothing enters version control. The live artifact is never written by this ticket.

**Conflicts checked.** T19 renames nothing but extends `ProbabilityCalibrator` with a `family=`
parameter and turns `fit_calibrator.py` into the multi-family fitter — build T6 with that extension
point in mind (the `family` argument is already in the signature above). T18 refits on post-blend
rows and will call the same script with a different input set. `retrain_v2.py` fits its own per-model
OOF isotonics at `:835/:855/:870` and pickles them onto the model objects — this ticket does not
touch them; T18 does. `docs/05 §5`'s five-layer table under-states the stack (drift D-6) and should
be corrected in the same PR. No behaviour changes when the flag is off.

---

## T7. Promotion bar by lever class, and a variance-aware reportability floor   `[BOTH]` · `[quick-win]`

**What to build.** A governance change plus one function. (a) **State the promotion bar per lever
class**, replacing the single bar at `docs/12-hit-rate-research.md:435-438` with four, because the
current bar cannot evaluate a staking change at all: a staking change moves neither hit rate nor
Brier and will often lower ROI% while being strictly correct — *"judged by the existing bar, the
right answer fails."* The four: **ranking changes** judged on paired top-pick hit rate (McNemar, same
races both arms); **anchoring/gating changes** on the T4 ROI surface **and** CLV; **calibration
changes** on reliability and calibration slope from T5, *not* aggregate Brier; **staking changes** on
expected log-growth and maximum drawdown over a replayed bankroll path (T8). (b) Make
`MIN_BETS_REPORTABLE` variance-aware: compute the realised per-bet P/L standard deviation from
settled rows, report the CI, and flag any tier whose CI spans zero as `NOT REPORTABLE` **regardless
of count**. At σ = 3.396, **200 bets gives a 95% CI on ROI of roughly ±47 percentage points**, so the
existing floor is ~15× too small for an ROI claim — fine as a floor for a binomial hit rate, not for
ROI. (c) Require every backtest report to print its trial count (implemented in T4).

Also fold in one decision that is documentation, not code: `STAKING_CONFIG['max_daily_units'] = 30`
at `unit_percent = 0.01` (`RS:131-132`) means **30% of bankroll per day**, while
`portfolio_risk.py:61` sets `max_daily_exposure_pct = 15.0`. **One says 30%, the other 15%, neither
runs.** Resolve it in the docs now, before any staking ticket needs a number.

**Where it lives.** `docs/12-hit-rate-research.md:435-438` (the bar) and
`docs/10-backtesting-and-learning.md:83-84` (the ≥200 rule). Code:
`server/python/shadow_pl_tracker.py` — `MIN_BETS_REPORTABLE = 200` at `:323`, consumed at `:363`.
Report headers in `backtest_v2_metro.py` and `backtest.py` (T4 supplies them).

**Interface contract.**

```python
MIN_BETS_REPORTABLE = 200          # kept — it stays the binomial/hit-rate floor
def _reportability(bets, total_pl, pl_values):
    """-> (status_str, roi_ci95). status is '✓ reportable', '⚠ <200 bets', or
    'NOT REPORTABLE (CI spans 0)'. pl_values empty -> ('⚠ no data', [None, None])."""
```

Called from the report loop at `SPL:360-366`, replacing the inline `status = "✓ reportable" if bets
>= MIN_BETS_REPORTABLE else …` expression. The tier query must also select the per-bet P/L values, so
the aggregate at `SPL:330-342` gains `array_agg(profit_loss)` for non-PENDING, non-SCRATCHED rows.

**Pseudocode / algorithm sketch.** `n = len(pl_values)`; `mean = Σ/n`; `sd = stdev(pl_values,
ddof=1)`; `se = sd/√n`; `ci = [mean − 1.96·se, mean + 1.96·se]` in ROI percent. Status:
`n < MIN_BETS_REPORTABLE` ⇒ `⚠ <200 bets`; else if `ci[0] < 0 < ci[1]` ⇒ `NOT REPORTABLE (CI spans
0)`; else `✓ reportable`. Edge cases: `n = 0` ⇒ `⚠ no data`, no division; `n = 1` ⇒ no `sd`, status
`⚠ <200 bets`, CI `[None, None]`; `sd = 0` (every bet lost) ⇒ `se = 0`, CI is a point, and the
zero-crossing test is `ci[0] <= 0 <= ci[1]` so a point at exactly 0 is still NOT REPORTABLE.

**Config / feature flag.** `STRIDE_REPORTABILITY_V2`, default `false`. Off ⇒ `_reportability`
returns exactly today's two-state string and no CI column is printed. Add to `.env.example`.

**Acceptance criteria.** No tier is reported with a CI spanning zero; every backtest report carries
its trial count; the four lever-class criteria are written into `docs/12`. **Pre-registered
threshold: the amended bar is applied retrospectively to the two decisions already made under the old
one** — the Phase-5 promotion (causal ablation **−0.0012 AUC**, kept *"to avoid churn"*) and the
LambdaRank rejection (H2H 39.7% stored / 34.4% favourite / 33.6% ranker on 996 races) — and both
verdicts either survive or are restated in writing. If the amended bar would have changed either
verdict, that is a finding about the bar, not about the change. Self-test in `shadow_pl_tracker`
(CI-wired): `_reportability` returns `NOT REPORTABLE` for a 500-bet series whose CI spans zero, and
`✓ reportable` for one whose CI does not.

**Rollback plan.** `STRIDE_REPORTABILITY_V2=false`. Docs changes are text and revert by git. No
state.

**Conflicts checked.** Shares `cmd_report` with T1 and T2 — sequence T1 → T7 → T2 or build together.
`MIN_BETS_REPORTABLE` is quoted in `docs/10:83-84` as a hard rule; the constant is **kept** and its
meaning narrowed to hit rate, so that document needs an amendment rather than a contradiction.
Changing the promotion bar changes the acceptance criteria of **every other ticket in this plan** —
so T7 must land before any default-on decision is taken anywhere, and each ticket's stated threshold
below is already written in the amended vocabulary. Nothing on the live path.

---

## T8. Bankroll state and a realised-stake column   `[ROI]` · `[quick-win]`

**What to build.** Two additive columns on `stride_tip_results` — `stake_units` (the realised unit
count actually recommended, from `compute_staking`) and `bankroll_after` (a running balance) — plus a
replay function that walks settled rows in race-date order applying a staking rule and producing a
P&L path. This exists so that *any* staking rule can be replayed against history already collected,
including retrospectively. It writes no new decision and changes no behaviour.

The gap it fills needs no citation: `run_tips_pipeline.py` contains **zero occurrences of the string
"bankroll"**; the only unit definition anywhere is `STAKING_CONFIG['unit_percent'] = 0.01`
(`RS:132`), consumed only by `RacingSystem.__init__` (`RS:2289`) and the standalone CLI (`RS:3163`),
**neither reachable from the daily pipeline**. Two unreconciled staking vocabularies coexist —
`2u/1u/0u` from `compute_staking` (`RTP:1007-1015`) and `FULL/STANDARD/REDUCED/NONE` from the crowd
gate, the latter a label only that nothing converts to a number. This ticket does not reconcile them;
it makes the first one replayable.

**Where it lives.** `server/python/shadow_pl_tracker.py` (`cmd_record`, `cmd_results`, plus a new
`cmd_replay`), with the columns added to the module's `MIGRATION_SQL` (`SPL:50-68`) and a reviewable
copy in `migrations/shadow_bankroll.sql`. Guardrail 3 names `racing_system_v8.3_mc.py:309-326`
(`kelly_stake`) as the staking module to extend **when the time comes** — that is T22, not this
ticket — and explicitly **not** `portfolio_risk.py`, which has zero importers and computes its
variance at `q = 1/odds` (`:235`) while using the model probability for EV at `:234`, so its Sharpe
ratio at `:237` divides a model-based EV by a market-based standard deviation.

**Interface contract.**

```python
def cmd_replay(rule="live", unit_pct=0.01, start_bankroll=100.0):
    """Walk settled rows oldest-first, apply `rule`, return
    {'path': [(race_date, bankroll)], 'final', 'max_drawdown', 'log_growth', 'n_bets'}.
    rule='live' replays the stored 2u/1u/0u staking exactly."""
def _stake_units_from_pick(tip):
    """'2u' -> 2.0, '1u' -> 1.0, '0u'/missing/malformed -> 0.0."""
```

`_stake_units_from_pick` is called inside `cmd_record`'s tuple build (`SPL:173-181`, `:199-207`),
reading the `staking` field the pipeline already publishes. `cmd_replay` is a new subparser beside
`record` / `results` / `report` / `backfill` at `SPL:423-430`.

**Pseudocode / algorithm sketch.** (1) Order settled rows by `(race_date, track, race_number)`.
(2) `stake = bankroll · unit_pct · stake_units`. (3) On WIN: `bankroll += stake·(sp − 1)`; on
PLACE/LOSS: `bankroll −= stake`; on SCRATCHED: unchanged. (4) Record `bankroll_after` per row and
track the running maximum for drawdown: `max_dd = max(max_dd, (peak − bankroll)/peak)`.
(5) `log_growth = ln(final/start)/n_bets`. Edge cases: `stake_units = 0` ⇒ the row is in the ledger
but **not** in the bet denominator — record which convention was used, because T21 turns exactly this
ambiguity into a ticket; `bankroll ≤ 0` ⇒ stop and report ruin at that date rather than continuing
with negative stakes; a missing `sp` ⇒ skip the row and count it as `unsettled`; `start_bankroll = 0`
⇒ refuse (division by zero in the growth rate).

**Config / feature flag.** `STRIDE_BANKROLL_LEDGER`, default `false`, gating the column *writes* in
`cmd_record`/`cmd_results`. The migration runs unconditionally (additive columns). `cmd_replay` is a
manual subcommand and needs no flag. Add to `.env.example`.

**Acceptance criteria.** A replayable bankroll series exists over all settled rows, and replaying the
current `2u/1u/0u` rule **reproduces the reported flat-stakes P&L exactly. This reproduction is the
acceptance test** — if the replay does not reproduce the existing series, the ledger is wrong.
**Pre-registered threshold: exact reproduction to the cent on ≥ 200 settled rows.** Self-test
(CI-wired): a synthetic 10-bet sequence with known outcomes reproduces a hand-computed final
bankroll and max drawdown. **Do not size anything off this yet** — R3-F14: *"Kelly sized off an edge
whose t-stat is 0.43 and whose ROI is non-monotonic in the edge itself is the exact configuration the
literature says produces ruin."*

**Rollback plan.** `STRIDE_BANKROLL_LEDGER=false`; columns go inert. `DROP COLUMN IF EXISTS
stake_units, bankroll_after` to clean up. `cmd_replay` reads only; it creates no state.

**Conflicts checked.** Touches the same `MIGRATION_SQL` block and the same insert tuple as T1 —
build them together or sequence T1 → T8. `selections.kelly_stake` is a **decoy** column: the name is
at `RTP:1334` and the value bound to it (`v18`, `RTP:1462`) is
`int(pick.get("staking","0u").replace("u",""))`, the 2/1/0 unit count, not a Kelly fraction. This
ticket must not reuse that name; `stake_units` is deliberately different, and the decoy should be
documented in `docs/09` in the same PR. `mc_api.py:7483`'s `kellyStake` is published to an unseen
frontend as a structurally-constant `0.0` — out of scope here, addressed in T22. Nothing on the live
path reads either new column.

---

## T9. Publish `edge_at_price` and the Kelly sign test as shadow fields   `[ROI]` · `[quick-win]`

**What to build.** Two published-but-unused fields per pick. `edge_at_price = win_pct − 100/odds` —
the **vig-inclusive** edge — and `kelly_sign = (win_pct/100)·odds − 1 > 0`. Nothing reads them. They
exist so the next month of data answers R4-F2 empirically.

The algebra: Kelly's `f* = (p·o − 1)/(o − 1)` means `f* > 0 ⟺ p·o > 1`, and Kelly is scale-free in
the bookmaker margin because the margin is already inside `o`. STRIDE's two value quantities are both
computed against the **de-vigged** market — `modelEdge = calibrated − true_market` (`RTP:697`) and
`ev = calib/true_mkt − 1` (`RTP:955`) — so **neither tests `p·o > 1`**. Writing `R` for the
overround, `modelEdge > 0 ⟺ p·o > 1/R`, and both positivity tests are satisfied at `p·o = 1/R`, where
the true return at the price is `1/R − 1` — **−16.7% at R = 1.20**. `race_normaliser.py:225` accepts
overrounds up to **1.60**, at which `ev > 0` is satisfied by bets returning **−37.5%** — and that
`0.90/1.60` check is guarded by `if odds_count >= 3` at `:223`, so **a two-quote race gets no
overround validation at all** while `calculate_overround` still de-vigs it.

The sharper half: `backtest_v2_metro.py:215-217` computes `implied = 1.0/sp; edge = prob − implied` —
the **vig-inclusive** edge — so the validated `"Value Edge 3%+ ($2-$15)"` band thresholds *that*
quantity, while `RTP:1816/1821/1825` threshold a systematically larger one. **The live gate is looser
than the validated band by `(100/o)(1 − 1/R)` pp at every price: 3.3pp at $5/R = 1.20, 1.1pp at
$15/R = 1.20.** Two different edges wear the same name and the same "3%".

**Where it lives.** `RTP` — computed in `calibrate_and_score` immediately after `modelEdge` is
written at `:697` (both inputs are already local there), published through `annotate_pick_contract`,
and stored via two additive columns in `store_selections_in_db` (`RTP:1245`; the INSERT at
`:1329-1398` is 115 columns — additive only, with a migration note). `server/python/validate_tips.py`
and `server/python/backfill_tips_contract.py` must keep passing; `backfill_tips_contract` imports the
live functions (`:19-25`) so the logic cannot drift — **keep that import, do not fork it**
(guardrail 11).

**Interface contract.**

```python
def _edge_at_price(win_pct, odds):
    """Vig-inclusive edge in percentage points. Returns None when odds <= 1."""
def _kelly_sign(win_pct, odds):
    """True iff (win_pct/100)*odds - 1 > 0. Returns None when odds <= 1."""
```

Both called from `calibrate_and_score` inside the `if odds and odds > 1:` branch at `RTP:672-697`,
writing `h["edgeAtPrice"]` and `h["kellySign"]` (camelCase — the MC/engine boundary convention), and
mirrored into the published document as `edge_at_price` and `kelly_sign` (snake_case — the tips
document convention). **The two key conventions are two different contracts; do not unify them.**

**Pseudocode / algorithm sketch.** (1) In the quoted branch: `eap = calibrated − 100/odds`;
`ksign = (calibrated/100)·odds − 1 > 0`. Note both use `calibrated`, i.e. the same
`h["winPercentage"]` the gate reads — so the shadow field is directly comparable to `modelEdge`.
(2) In the unquoted branch (`RTP:698-701`), write `None` for both, matching the existing
`fairOdds = None` treatment rather than `0`, which would be a real value. (3) Scratchings never reach
here — `filter_active_runners` (`RTP:1635`) removes them before scoring. (4) Thin fields: when
`calculate_overround` returned `1.0` because `valid < 2`, `edge_at_price` and `modelEdge` are
**identical**, which is itself the diagnostic — count those rows separately (T3 counter 5).

**Config / feature flag.** `STRIDE_PUBLISH_EDGE_AT_PRICE`, default `false`. When off, neither key is
written and the output document is byte-identical. Add to `.env.example`. **The gate is not changed
in this ticket under any flag setting.**

**Acceptance criteria.** For each price band, the realised ROI of picks split by `edge_at_price > 0`
vs `≤ 0` while `edge_pct ≥ gate`. Sample: ~300–500 bets per band gives a usable *sign*, not a CI on
the magnitude — the split is a within-sample classification, not an ROI difference test.
**Pre-registered threshold: if more than 20% of published `$3–$5` picks have `edge_at_price ≤ 0`,
that band's gate is formally declared mis-specified** and a follow-on ticket re-denominates the gates
in EV rather than percentage points. **Do not change the gate in this ticket.** Blocking dependency,
stated by R4 itself: the actual overround distribution is unknown, so **T3 counter 1 must land
first**. Contract check: `python server/python/validate_tips.py --all --strict` passes unchanged.

**Rollback plan.** `STRIDE_PUBLISH_EDGE_AT_PRICE=false`. `DROP COLUMN IF EXISTS edge_at_price,
kelly_sign` on `selections` if a clean schema is wanted; published JSON files already written carry
two extra keys that no consumer reads.

**Conflicts checked.** `store_selections_in_db` writes only picks with `should_bet` true, so the
shadow fields will be absent for refused picks in the DB — they are present in the JSON `full_field`,
which is where the refused-set analysis must read them (and where T1's ledger reads its context).
`validate_tips.py` checks `bet + no_bet == total` and per-pick `should_bet` presence; additive keys
do not affect it. An unseen TypeScript frontend consumes the `selections` table and `mc_api`'s JSON
(`SYSTEM_MAP §9 Q18`) — additive columns and additive keys are safe, renames are not, and this ticket
renames nothing. `mc_api`'s own un-de-vigged `edge = winPct − 100/odds` (`mc_api.py:7636`) is
numerically the same *formula* on a different probability; **do not unify them** — they are different
quantities on different scales, and the new field is deliberately computed in the wrapper where the
gate reads.

## T10. Rao-Blackwellise the Monte Carlo win probability   `[BOTH]` · `[quick-win]`

**What to build.** The simulator draws a finishing order per iteration via the Gumbel-max trick —
`noise = rng.gumbel(size=n); order = np.argsort(-(logits + noise))` (`RS:1855-1856`) — and estimates
the win probability by counting how often each runner came first: `win_probs = (finish_positions ==
1).mean(axis=0)` (`RS:1859`). Because Gumbel-max over `logits` is exactly a categorical draw with
probabilities `softmax(logits)`, the **conditional expectation of that indicator given the draw's
logits is available in closed form**. Replace the counting estimator for the *win* probability only
with the average of the per-draw softmax: `win_probs[i] = mean_s softmax(logits_s)[i]`. Same
estimand, strictly lower variance (Rao-Blackwell), and it replaces an `argsort` with a `softmax`.
Place and top-3 stay exactly as they are — no analytic form is available for them without a Harville
assumption, and adding one is out of scope.

Sizing: binomial SE at `p = 0.30` is **1.02pp at N = 2000, 0.84pp at N = 3000, 0.65pp at N = 5000**
(`RTP:389-394` sets 5000/3000/2000 by field size). Two independent scorings of the same race
therefore differ by **±2.84pp (95%)** at N = 2000, because the SE of the *difference* is
`1.02·√2 = 1.45pp`. The daily seed is `int(time.time()) % 100000` (`RTP:2340`), so successive
scorings genuinely are independent. Against `edge ≥ 3` (`RTP:1825`), conviction steps at 3/2/1
(`RTP:836-843`) and `mc_spread < 6.0` (`RTP:705`), **the run-to-run swing exceeds the entire edge
gate.**

**Free by-product, and the reason this punches above its size:** `Var_s(softmax(logits_s)[i])` is a
per-runner **model-uncertainty** estimate. Emit it as `win_prob_var`. That is the exact input `§A4`
says STRIDE computes and discards at three separate points, and it is a better basis for the
`stability` term in `mc_selection_score` (`RS:1920`, weight 0.12) and for T22's shrinkage.

**Where it lives.** `racing_system_v8.3_mc.py` — the sampling loop ending at `:1856` and the
probability assembly at `:1859-1861`; the new key goes into the per-runner result dict at
`:1880-1904` beside `ci_lower`/`ci_upper`. Guardrail 3: **extend the existing simulator, do not add a
fifth MC engine** — the repo already carries four.

**Interface contract.** `racing_system_v8.3_mc.py` is untyped; match it.

```python
def _rao_blackwell_enabled():
    """STRIDE_MC_RAO_BLACKWELL, inline default-off idiom."""

def _softmax_rows(logits):
    """Numerically stable row-wise softmax: exp(x - x.max()) / sum. Returns
    a vector of length n summing to 1."""
```

Called inside `simulate_race_monte_carlo` (`RS:1781`). When enabled, accumulate
`q_sum += _softmax_rows(logits + 0)` and `q_sq_sum += q**2` inside the existing per-simulation loop —
**the Gumbel draw and the `finish_positions` write still happen**, because place, top-3, expected
position, `std_pos` and the pace-scenario splits all depend on them. Only line `:1859`'s assignment
changes: `win_probs = q_sum / mc_sims` instead of `(finish_positions == 1).mean(axis=0)`.

**Pseudocode / algorithm sketch.**
1. Per simulation `s`, after `logits += scenario_adjustments(...)` at `RS:1853` and **before** the
   Gumbel noise is added: `q_s = softmax(logits)`; `q_sum += q_s`; `q_sq_sum += q_s**2`.
2. After the loop: `win_probs = q_sum / mc_sims`; `win_prob_var = q_sq_sum/mc_sims − win_probs**2`,
   floored at 0 to absorb floating-point negatives.
3. `wilson_interval` at `RS:1874` takes a **success count**; with the analytic estimator there is no
   count. Keep it fed from `finish_positions` exactly as today so `ci_lower`/`ci_upper` remain the
   same quantity they have always been, and publish `win_prob_var` as a *separate* field. Changing
   the meaning of `ciLower`/`ciUpper` (surfaced at `mc_api.py:7479-7481`) would be an API change.
4. Edge cases: `n = 1` ⇒ `softmax` returns `[1.0]`, correct and matching the counting estimator;
   `n = 0` cannot occur (`filter_active_runners` requires ≥ 2, `RTP:1635`); all-equal logits ⇒ a
   uniform vector, which is the right answer and is exactly the flat-MC case; `logits` containing
   `-inf` from `np.log(np.maximum(sampled_probs, 1e-9))` at `RS:1852` is already floored, so no
   `exp(-inf)` NaN; the max-subtraction makes overflow impossible.

**Config / feature flag.** `STRIDE_MC_RAO_BLACKWELL`, default `false`, read once per simulation call
(not per iteration). Off ⇒ `win_probs` is computed exactly as today and `win_prob_var` is absent.
Add to `.env.example`. Note this file has no `_env_flag` helper — that lives at `mc_api.py:44-48` —
so use the inline Variant-B idiom, matching `STRIDE_CL_BLEND`.

**Acceptance criteria.** **A self-contained experiment needing no outcomes and no waiting — the only
item on this list fully validatable in an afternoon.** On a fixed set of stored races, score each
race **20 times with different seeds** under flag-off and flag-on, and report the standard deviation
of `winPercentage` for the top-ranked runner. **Pre-registered threshold: flag-on reduces the
across-seed SD of the top runner's `winPercentage` by ≥ 50% at N = 2000 sims, with the mean unchanged
within 0.2pp** — a variance reduction must not be a level shift. Secondary, paired on the same races:
**the top-1 ordering changes on < 5% of races**; if it changes more, the ranking was being decided by
sampling noise on those races, which is itself the finding. Only then run the outcome A/B, using the
existing `--skip-db-store --output-suffix` proof-run idiom and a fixed seed (`SYSTEM_MAP §7b.7`).

**Rollback plan.** `STRIDE_MC_RAO_BLACKWELL=false`. No artifacts, no schema. Cached `mc_api` results
are per-run and in-process only (`RTP:399-406`), so nothing persists.

**Conflicts checked.** `mc_selection_score` (`RS:1908`) reads `win_prob_sim` and `stability_score`;
both remain defined, and `stability_score` is deliberately left on the Wilson/`std_pos` basis so this
ticket cannot move the MC spine score, which carries **50% of the final selection score**
(`RTP:776`). `mc_api.py:7392-7393` blends `base_win_prob` 0.70 with the sectional overlay 0.30 — the
overlay comes from `realistic_simulate` and is untouched. `adaptive_mc.py` and the root
`monte_carlo.py` are dead (zero importers) and are not edited. `mc_recalibration.py` is inert here.
T22 consumes `win_prob_var`; nothing else does.

---

## T11. One shared helper for the five interaction features   `[HIT-RATE]` · `[quick-win]`

**What to build.** Extract `fitness_x_distance`, `barrier_x_pace_inv`, `sectional_x_going`,
`class_drop_x_trajectory` and `campaign_run_x_fitness` into a single helper called by **both**
`retrain_v2.build_feature_matrix` and the inference block at `RTP:2306-2319`, with a self-test
asserting the two existing formulas produce identical output on random inputs **before** the switch.

One has already drifted, so this is not hypothetical. Training computes
`out["barrier_x_pace_inv"] = out["barrier_advantage"].fillna(0) * _pps` when `_pps.std() >= 0.05`
and `* 0.5` otherwise; inference computes `runner.get("barrier_advantage", 0) * (1 - _pps)`
(`RTP:2310`) — **`_pps` versus `1 − _pps`, and a std-gated fallback on one side only.** Compounding
it, `barrier_advantage` is one of the training-side dead columns, so the training-side value is
identically zero. Similarly `sectional_x_going` reads `runner.get("z_200m", 0)` at `RTP:2311`, which
takes the `else` branch in production because the sectional primitives are never written into `feat`
— **so the whole interaction is identically zero in production** (T14 fixes the input).

**Where it lives.** New `server/python/feature_interactions.py`, beside `relative_market.py`, flat
role-based layout, `snake_case`. Callers: `retrain_v2.build_feature_matrix` (the block around
`retrain_v2.py:660-690`) and `RTP:2306-2319`. Wire the self-test into
`.github/workflows/ci.yml:33-42` beside the eight existing ones.

**Interface contract.** Two entry points, because the two call sites hold different data shapes —
one vectorised over a DataFrame, one scalar per runner. Both delegate to the same scalar formulas so
there is exactly one definition per feature.

```python
from typing import Dict, Optional
import pandas as pd

def compute_interactions(feat: Dict[str, float]) -> Dict[str, float]:
    """Scalar path (inference). Returns the five interaction values for one runner."""

def add_interaction_features(out: pd.DataFrame, pace_std_gate: float = 0.05) -> pd.DataFrame:
    """Vectorised path (training). Mirrors relative_market.add_relative_market_features'
    contract: writes columns into `out` and returns it."""
```

`compute_interactions(feat)` is called at `RTP:2306`, replacing the five inline assignments, before
`df = _pd.DataFrame([feat])` at `:2320`. `add_interaction_features(out)` is called inside
`build_feature_matrix` at the same point the inline block sits today, **after** the form-features
`out.update(form_df)` and **before** `NON_SECTIONAL_FEATURES` zero-fill — the current order, unchanged.

**Pseudocode / algorithm sketch.** (1) `fitness_x_distance = max(0, 1 − |crn − 3|·0.15) ·
distance_strike_rate`. (2) `barrier_x_pace_inv`: **the training-side formula is canonical**, because
that is what the artifact learned — `barrier_advantage · pps` with the `pps.std() < 0.05 ⇒ 0.5`
fallback. The inference path currently uses `1 − pps` and must change; that changes inference output
and therefore requires a paired hit-rate check exactly like T14's. (3)
`sectional_x_going = z_200m · going_suitability` with `going_suitability` defaulting to 0.5 and
`z_200m` to 0. (4) `class_drop_x_trajectory = is_class_drop · form_direction_slope`. (5)
`campaign_run_x_fitness = crn · max(0, 1 − max(0, crn − 5)·0.2)`. Edge cases: `None` and `NaN` inputs
are coerced with the same `fillna(0)` / default the current code uses on each side — the helper must
**reproduce** them, not improve them; `crn` missing ⇒ 1 (today's `feat.get("campaign_run_number", 1)`);
the pace-std gate needs ≥ 2 rows to have a std, so a single-row DataFrame takes the 0.5 fallback
deterministically.

**Config / feature flag.** `STRIDE_SHARED_INTERACTIONS`, default `false`. When off, both call sites
execute their current inline code untouched. When on, both call the helper. This is the only safe
shape: the flag must switch **both** sides together, or train and serve diverge in a new way.
Add to `.env.example`.

**Acceptance criteria.** The self-test asserts equality of old and new formulas on **10,000 random
inputs** for four of the five features, and CI runs it. **Pre-registered threshold: byte-identical
output on `fitness_x_distance`, `sectional_x_going`, `class_drop_x_trajectory` and
`campaign_run_x_fitness`. If any of those four differ, that is a bug report, not a refactor,** and it
must be raised before the switch. For `barrier_x_pace_inv` the two formulas are known to differ: the
ticket must (a) report the difference distribution, (b) adopt the training-side formula, and (c) run
the paired top-pick hit-rate check (McNemar on ~2,000–2,500 stored races) before default-on.

**Rollback plan.** `STRIDE_SHARED_INTERACTIONS=false` restores both inline paths. The helper module
remains but is unreferenced at runtime; delete it to remove entirely. No artifacts, no schema; the
next retrain is unaffected while the flag is off.

**Conflicts checked.** `retrain_v2.FEATURE_COLUMNS` (`:152-275`) and `ml_model.FEATURE_COLUMNS`
(`:65-189`) are byte-identical and must stay so — this ticket adds no columns, so both are untouched.
T14 changes what `feat["z_200m"]` contains, which changes `sectional_x_going`'s *input* but not its
formula; sequence T11 before T14 so the byte-identity test runs against today's inputs.
`step_up_x_dist_slope` is computed at `RTP:2315-2319` and **deliberately excluded from training**
(`retrain_v2` comment: *"excluded (RED coverage)"*) — leave it out of the helper and note why.
Nothing else reads these five columns outside the two feature builders.

---

## T12. Commission parameter and segment tagging   `[ROI]` · `[quick-win]`

**What to build.** (a) A single `commission_rate` parameter, defaulting to `0.0` (byte-identical),
feeding the EV computation and any future Kelly computation, plus a venue field on settled bets.
Net EV becomes `p(o−1)(1−c) − (1−p)`. On Australian racing markets the Betfair Market Base Rate is
**8% or 10%** depending on state and code, levied on net market winnings, with 10% applying to
NSW/ACT racing. Because commission scales with `(o−1)` and the validated band lives at long prices,
**two-thirds to five-sixths of that band's edge is commission at these price levels**: the validated
+12.3% becomes roughly **+4.1% at 8%** and **+2.1% at 10%**. (b) Tag every output row with its
segment — metro / provincial / country — and jurisdiction. **No gating on either.**

The scope argument needs no citation: `examples/backtest_summary.json` shows the validating backtest
ran on **10 metro tracks only** (Caulfield, Caulfield Heath, Doomben, Eagle Farm, Flemington,
Morphettville, Newcastle, Rosehill Gardens, Royal Randwick, Warwick Farm) while production runs
whatever cards are downloaded. **The validated universe and the deployed universe differ, and nothing
in the code marks the boundary.**

**Where it lives.** `RTP:955` (`compute_confidence`'s `ev`) for the commission-adjusted EV;
`RTP:1245` `store_selections_in_db` for an additive `segment` column; `SPL` for a `venue` field on
settled rows. Segment vocabulary: read `server/python/market_efficiency.py:7-23` — it already has a
`METRO_TRACKS` set and a `'thin'` segment — **for the vocabulary only; do not wire its gating**
(it has zero production callers and `SEGMENT_EDGE_THRESHOLDS` is not to be adopted on plausibility).
`mc_api.py:7637`'s separate EV is **read-only here** — the two EV definitions are different
quantities and must not be unified.

**Interface contract.**

```python
def _commission_rate():
    """STRIDE_COMMISSION_RATE as a float in [0, 0.2]; malformed or out of range -> 0.0
    with a printed [GATE] warning."""
def compute_confidence(h, pace_clarity=None):    # existing signature — UNCHANGED
def classify_segment(track_name):
    """-> 'metro' | 'provincial_country' | 'unknown'. Uses market_efficiency.METRO_TRACKS
    for the metro set; everything else that resolves to a known AU track is
    'provincial_country'."""
```

`_commission_rate()` is read inside `compute_confidence` at the `ev` line (`RTP:955`), replacing
`ev = (calib / true_mkt) - 1.0` with the net-of-commission form when `c > 0`. `classify_segment` is
called once per race in `run_tips()` (`RTP:2035`) and written onto each pick before
`store_selections_in_db`.

**Pseudocode / algorithm sketch.** (1) `c = _commission_rate()`; if `c == 0.0`, the expression is
algebraically identical to today's — assert that in the self-test. (2) Otherwise
`ev = (p·o·(1−c) + c·? )`… state it precisely: with `p = calib/100` and `o` the decimal price, gross
profit on a win is `(o−1)`, so `ev_net = p·(o−1)·(1−c) − (1−p)`, and the ratio form used today is
recovered at `c = 0` by dividing through by `p_market·o` — the ticket must **publish both** as
`ev` (unchanged) and `ev_net` (new) rather than redefining `ev`, because `compute_confidence`'s
`ev > 0.0 and edge > 1.0 → high` test (`RTP:965`) is a live gate and changing its input silently
would be a behaviour change under a flag that claims to be additive. (3) `classify_segment`
lowercases and strips sponsor prefixes the way the normaliser already does. Edge cases: unknown track
⇒ `'unknown'`, never defaulted to metro; `true_mkt == 0` ⇒ today's `-999.0` sentinel is preserved
verbatim; `odds <= 1` ⇒ not reached (guarded upstream).

**Config / feature flag.** `STRIDE_COMMISSION_RATE`, default `"0.0"` — byte-identical at the default,
which is what makes this a quick win. `STRIDE_SEGMENT_TAGS`, default `false`. Both in `.env.example`.

**Acceptance criteria.** ROI and CLV split by segment and by venue, from T1/T2. **Pre-registered
threshold: twelve weeks of tagged output, then report the metro / non-metro split. If non-metro CLV
is materially worse than metro, the scope-generalisation error is real and a gating ticket becomes
justified — but gating comes later and only on evidence** (R3-F16: *"tag, do not gate"*). Self-test:
`_commission_rate()` returns 0.0 for unset/`"abc"`/`"0.5"`, and `ev_net == ev_gross` at `c = 0`.
Document the confirmed MBL figures — **$2,000 win / $800 place at metropolitan meetings, $1,000 /
$400 country and provincial in NSW**, with the "after 9am on the day of the race" timing rule — as a
capacity assumption in `docs/10`. **Do not build a cap table** on the unverified secondary conditions
(2pm night meetings, jurisdiction-of-staging, WA/NT carve-out, the ~$250 Top Fluc cap).

**Rollback plan.** `STRIDE_COMMISSION_RATE=0.0` and `STRIDE_SEGMENT_TAGS=false`. `DROP COLUMN IF
EXISTS segment` on `selections` and `venue` on `stride_tip_results` to clean up.

**Conflicts checked.** `compute_confidence` feeds `compute_staking` (`RTP:1007-1015`), so any change
to `ev` would change stakes — which is why `ev` is left alone and `ev_net` is published beside it.
`market_efficiency.py` must not be wired (Phase 3 §4 item 7). `tips_day_aggregates.select_bankers`
reads `confidence` and `odds`; unchanged. The `$4–$15` value-play band in
`tips_day_aggregates.py:91-103` versus the validated `$2–$15` (`SYSTEM_MAP §9 Q16`) is a **separate
documented open question** — record it in the same docs pass, do **not** silently reconcile it.

---

## T13. CatBoostRanker `QuerySoftMax` evidence arm   `[HIT-RATE]` · `[structural]`

**What to build.** A second arm in the existing `rank_model.py` harness using
`CatBoostRanker(loss_function='QuerySoftMax')` — the listwise top-1 objective, which *is* the
conditional-logit likelihood with a GBDT index — judged by the criterion already written at
`docs/12:396`. **Evidence only. No pipeline hook. No flag. Zero importers, and it must stay that
way** (constraint 15).

The argument for re-testing: the failed experiment tested the wrong objective. LambdaRank optimises
**NDCG surrogates**; with `label_gain=[0, 1]` (`rank_model.py:63`) its λ-gradients are computed over
pairs whose gain differences are all identical — a pairwise objective on a one-relevant-item problem.
The listwise top-1 loss is a different thing, and CatBoost's own ranking tutorial states it verbatim:
*"A special case: top-1 prediction … CatBoostRanker has a mode called **QuerySoftMax**… We will
maximize the probability of being the best document for given query."* CatBoost is already in
`requirements.txt` and already a base learner (`retrain_v2._get_catboost_params`). **The operational
advantage that matters:** QuerySoftMax emits a **within-race probability**, so if it ever passed the
criterion it could enter as a probability rather than as the "ordering signal" compromise
`docs/12 §5.4` was forced into.

Two honesty notes that cap this ticket: (i) no published top-1 hit-rate lift for ranking on racing
data was found, so this is a hypothesis for STRIDE's own harness, **not a promised gain**; (ii) the
previous verdict is itself unsafe, because the LambdaRank H2H had **both arms trained on SP-derived
features** and the "stored production model" in that H2H was the imported historical prediction set,
not the live pipeline. The failure is real; its interpretation is not settled.

**Where it lives.** `server/python/rank_model.py` only. It already trains on the same 113-column
matrix via `retrain_v2.build_feature_matrix` (`:225`), uses the same 60/14/14/14 purge-gapped
walk-forward (`:47-50`), and already emits the three-way same-race H2H (`:153-161`). Run with the
existing `.github/workflows/train-rank-model.yml`.

**Interface contract.** File is untyped; match it.

```python
RANKER_PARAMS = {...}                      # existing LambdaRank dict — UNCHANGED
QUERYSOFTMAX_PARAMS = {
    "loss_function": "QuerySoftMax", "iterations": 300, "depth": 6,
    "learning_rate": 0.05, "random_seed": 42, "verbose": 0,
    "allow_writing_files": False,
}

def train_ranker(X, y, group_sizes, params=None, backend="lightgbm"):
    """backend='lightgbm' (default, today's LGBMRanker) or 'catboost'
    (CatBoostRanker with QUERYSOFTMAX_PARAMS). Returns an object with .predict(X)."""

def walk_forward_report(races, params=None, verbose=True, backend="lightgbm"):
    """Existing contract, plus `backend` passed through to train_ranker."""
```

`backend` defaults to today's value at every call site, so `python rank_model.py` (self-test) and
`--train` behave identically unless `--backend catboost` is passed. `train_from_database(...)` gains
the same pass-through argument.

**Pseudocode / algorithm sketch.** (1) CatBoostRanker needs a `group_id` **per row**, not a list of
group sizes: expand `group_sizes` into `group_id = [0]*n_0 + [1]*n_1 + …`, and CatBoost requires the
rows to be **contiguous by group** — they already are, because `walk_forward_report` builds `X_tr`
with `np.vstack` race by race (`:132`). (2) NaNs: LightGBM tolerates them natively and the self-test
deliberately injects 5% NaN (`:325`); CatBoost's `Pool` accepts `nan` for numeric features, so no
imputation — **do not add one**, that would be a second feature path. (3) Prediction: CatBoostRanker
returns scores, and `argmax` is taken exactly as at `:145`. If the probability form is wanted, apply
a within-race softmax to the scores — **report it, do not use it for the hit-rate comparison**, which
is `argmax`-invariant. (4) Edge cases: races with `< 4` runners or `≠ 1` winner are already excluded
at `:251`; a fold whose training set has a single group is refused by CatBoost — the existing
`len(train_idx) >= 30` guard at `:107` covers it; if `catboost` is not installed, fall back to the
LightGBM arm with a printed warning, following the repo's `*_AVAILABLE` optional-capability idiom.

**Config / feature flag.** **None.** This is evidence-only with zero importers; a flag would imply a
production path that must not exist. Selection is a CLI argument (`--backend catboost`), matching the
module's existing `--train` / `--model-path` argument style. The artifact is written to
`models/rank_model_v1_catboost.pkl` so it cannot overwrite the LambdaRank artifact.

**Acceptance criteria.** The **same-race head-to-head** on identical test races where the stored
model covers the full field decides it — that line, not the walk-forward headline. `docs/12:378-389`
documents why: the headline `36.0% vs 29.5%` was *"baseline weakness, not ranker strength."* Also
report log loss, since QuerySoftMax emits a probability and LambdaRank did not. **Sample: target
≥ 2,000 H2H races.** The previous H2H had 996, at which a 3pp difference has a 95% CI of roughly
±4pp — enough for the 6.1pp loss LambdaRank posted, **not enough to conclude a narrow win**.
**Pre-registered threshold, taken unchanged from `docs/12:370-373` so this cannot be graded on a
moved goalpost: the ranker's holdout top-1 must beat both the market favourite and the stored model
on identical races, across folds.** If it does not, it stays evidence-only, exactly as LambdaRank did.
CI: `python rank_model.py` (synthetic self-test) still passes and gains a CatBoost arm assertion that
is **skipped** if catboost is absent — CI installs only `numpy scipy pandas scikit-learn lightgbm`.

**Rollback plan.** Delete `models/rank_model_v1_catboost.pkl`. Nothing else exists. There is no
production state because there is no production hook.

**Conflicts checked.** Constraint 15 (*"the ranker stays evidence-only, no pipeline wiring"*) — this
ticket adds an arm, not an override, and the grep for importers must stay at zero. `retrain_v2` is
imported lazily at `rank_model.py:222` and hard-requires `DATABASE_URL`; the self-test path must not
trigger it. `train-rank-model.yml` uploads the artifact — add the second path or it silently
publishes only the LambdaRank one. The H2H's `stored` column is `training_view_v2.predicted_win_prob`
(`:240-241`), which is overwhelmingly imported historical predictions of unknown generating stage —
say so in the report, because it is the same provenance problem that puts `STRIDE_CL_BLEND` on hold.

---

## T14. Restore NaN preservation at inference and supply the 8 sectional primitives   `[BOTH]` · `[structural]`

**What to build.** Two edits. (a) At inference, return `np.nan` rather than `0` for the columns
training deliberately leaves as NaN. Training is explicit: `retrain_v2.build_feature_matrix` zero-fills
only `NON_SECTIONAL_FEATURES` and carries the comment *"Phase 2 sectional columns +
NAN_PRESERVE_FEATURES intentionally keep NaN (tree models)"*. Inference destroys it:
`ml_model.prepare_features` (`:214-218`) does `pd.to_numeric(data[col], errors='coerce').fillna(0)` —
**unconditional** — and `features[col] = 0` for absent columns. Even `runs_since_peak`, which
`RTP:2295` carefully sets to `float("nan")` with the comment *"NaN-preserving (tree models handle
missingness)"*, is converted to `0` two calls later. (b) Write the 8 sectional primitives
(`z_200m`, `z_400m`, `z_600m`, `z_800m`, `lambda_decay`, `svi`, `rsi`, `trip_cost_seconds`) into
`feat` — they are **never offered at all** today; `z_200m` is read at `RTP:2311` only to build
`sectional_x_going`, so it takes the `.get(..., 0)` default and the whole interaction is identically
zero in production.

**Why this ranks highest on hit rate among no-retrain changes.** At ~47% sectional coverage roughly
half of training rows carry a real z-score and half a NaN, while **100% of production rows carry
`0.0`** — and a zero z-score is not "missing", it is "exactly average", the modal value. XGBoost's
sparsity-aware split finding learns a default direction per split, so a NaN and a 0 take different
branches. Every runner without sectionals is routed down the "average closer" branch instead of the
"unknown" branch. It is also a strong alternative explanation for the *"sectionals add −0.0005 AUC"*
result (`docs/12:349-352`): that ablation measured the training-side value of a block production
never receives, so it says nothing about whether wiring them would help.

`RTP:2293`'s `has_sectional_data` is the one thing done right — an explicit binary availability
indicator, clamped in both `retrain_v2.py:686-687` and `ml_model.py:221-222` — and it is the template
for T25. **Do not touch `retrain_v2`'s NaN handling; it is already correct.**

**Where it lives.** `server/python/ml_model.py:208-224` (`prepare_features`) and `RTP:2258-2308`
(the `feat` build). The sectional values are already fetched per horse by `enrich_with_db`
(`RTP:1037-1048` selects `z_200m, z_400m, z_600m, z_800m, lambda_decay, svi` with a strict
`race_date < %s` as-of filter) — read them from the runner dict, do not add a second query.
`rsi` and `trip_cost_seconds` need adding to that existing SELECT's column list.

**Interface contract.** `ml_model.py` is hinted; match it.

```python
def prepare_features(self, data: pd.DataFrame) -> pd.DataFrame:   # signature UNCHANGED
    """When STRIDE_NAN_PRESERVE_INFERENCE is on, columns in
    PHASE2_FEATURES | NAN_PRESERVE_FEATURES keep NaN instead of being zero-filled,
    and absent columns of that set are created as np.nan rather than 0."""
```

The NaN-preserve column set must be **imported from the single existing definition** rather than
re-listed — `retrain_v2.PHASE2_FEATURES` and `retrain_v2.NAN_PRESERVE_FEATURES`. `retrain_v2` imports
`psycopg2` and requires `DATABASE_URL` at import, so guard the import in a try/except and fall back
to a module-level tuple in `ml_model.py` that a self-test asserts is identical to `retrain_v2`'s —
the same discipline that keeps the two `FEATURE_COLUMNS` lists byte-identical.

**Pseudocode / algorithm sketch.** (1) In `prepare_features`, for each column in `cols`: if it is in
the preserve set ⇒ `features[col] = pd.to_numeric(data[col], errors='coerce')` when present, else
`np.nan`; otherwise today's `.fillna(0)` / `0`. (2) Keep the `has_sectional_data` clamp at `:221-222`
exactly as-is — it is a genuine binary and must not become NaN. (3) In `RTP`, add the 8 primitives to
the `feat` dict using `runner.get(k, float("nan"))` — **`nan`, not 0**, matching `runs_since_peak`'s
existing treatment at `:2295`. (4) Edge cases: a runner with no sectional history ⇒ all 8 are NaN and
`has_sectional_data = 0`, which is exactly the state training represents; a partially-covered runner
(z-scores present, `rsi` absent) ⇒ per-column NaN, not all-or-nothing; the `StandardScaler` at
`ml_model.py:552-553` will produce NaN output for NaN input — it is applied only
`if hasattr(self.scaler, 'mean_')`, and tree models do not need it, so verify on the live artifact
whether the scaler was fitted; if it was, the scaler path must be bypassed for preserved columns or
the change silently NaNs the whole row. **That check is the first task of this ticket**, before any
other work.

**Config / feature flag.** `STRIDE_NAN_PRESERVE_INFERENCE`, default `false`, read once at the top of
`prepare_features` and once in the `RTP` feat block — **both sides switch together**. Add to
`.env.example`.

**Acceptance criteria.** **Cheap pre-check that costs nothing and may close the ticket:** run
flag-on over stored races and report the fraction of runners whose `mlPredictedProb` moves by more
than 2pp. If that fraction is near zero, the artifact never learned a missingness split and the item
closes early; if it is large, the current production probability has been systematically wrong on
roughly half the field. Then: (1) **paired top-pick hit rate** on identical stored races, flag-on vs
flag-off — McNemar, **~2,000–2,500 races** for a 3pp move; (2) AUC and log loss on the same races;
(3) **the calibration Brier and the Value-Edge band ROI re-read on the same run**, because the
probability scale moves (constraint 18). **Pre-registered threshold: default-on only if paired
top-pick hit rate improves by ≥ 1pp with McNemar at p < 0.05, and reliability (T5) does not degrade.**

**Rollback plan.** `STRIDE_NAN_PRESERVE_INFERENCE=false`. No artifacts, no schema. The live model
pickle is not modified — this ticket changes only what is fed to it. Any proof-run output written
with `--output-suffix` can be deleted.

**Conflicts checked.** Changes the probability scale on the **existing** artifact immediately, so
every downstream raw-probability threshold — conviction 15/12/10 (`RTP:836-843`), bet-gate floors
30/15/10 (`RTP:1816/1821/1825`), longshot `raw ≥ 8` (`RTP:917`) — sees a different distribution; they
must be **re-read on the same run, not ported**. T11 shares the `feat` block; sequence T11 first.
T20 also shifts the raw-prob scale — **do not run T14 and T20 flag-on in the same A/B**, or neither
result is attributable. `walk_forward_backtest.py:394` uses `RacingMLModel.train`, so its metrics
will move too. The five target-encoded columns (`jockey_encoded`, `trainer_encoded`, `track_encoded`,
`going_encoded`, `race_class_encoded`) suffer the identical defect via `ml_model.py:218` and are
**not** in the 113-column contract — document them here as a known second instance; fixing them means
calling `target_encoder.transform(...)` at inference, which does not exist today and is out of scope.
`ml_model.py:250-252`'s `TargetEncoder` is **correctly implemented leave-one-out** (Phase 3 §5.1) —
it is not leakage, and it must not be "fixed".

## T15. `odds_source` / `has_real_market_odds` as explicit indicators, and the SP-vs-racecard diagnosis   `[BOTH]` · `[structural]`

**What to build.** Two additive contract features and one diagnosis. (a) `odds_source` — an integer
indicator distinguishing a genuine pre-race quote (1) from a starting price (2) from a synthetic
fallback (3) from no quote (0), so the trees can separate the regimes on their own branch.
(b) `has_real_market_odds` promoted from an honesty field to a feature — it already exists in the
pick dict (`RTP:1800`, `:1859`) and never reaches the feature vector. (c) A published quantification
of how far apart the train-time and serve-time `market_odds` distributions are.
**Explicitly not proposed: any backfill.**

The defect chain is fully source-verified. `retrain_v2.py:142-144` comments *"sp_odds is the primary
odds column in the view; market_odds is sparsely populated so we fill from sp_odds"* and maps
`"sp_odds": "market_odds"`; `:557-563` builds `_effective_odds = COALESCE(market_odds, sp_odds)`;
`:574` assigns it to `out["market_odds"]`. The COALESCE almost always falls through to SP because
**106,193 of 119,577 view rows have no prediction join**. Inference uses the racecard: `RTP:2259`
`feat["market_odds"] = extract_odds(runner) or 0`, and those are overnight/~8am prices.
`backtest_v2_metro.py:142` does the identical COALESCE — **so the README's 33.7%/−4.2% and
9.9%/+12.3% were themselves produced on SP-derived features.** The synthetic half:
`RS:1763-1778`'s `infer_market_odds` falls back to `model_odds`, then `1/model_prob_dec`, then **the
median of the field's odds** — a model-derived price feeding a feature that feeds a model adjustment.

**Three downstream consequences this explains, none of them proven and all to be stated as
hypotheses:** it plausibly explains the short-price miscalibration the `mw` ladder compensates for
(an 8am price of an eventual $1.60 favourite is much longer than its SP, so a model fitted on SP
under-predicts short-priced runners — and `RTP:676-678` says *"$1-3 horses win 41%, model predicts
17% after blend"*); it offers a second explanation for β = 0 in the CL fit; and it makes the
LambdaRank H2H verdict unsafe, since both arms were trained on SP.

**Where it lives.** `server/python/retrain_v2.py:142-144`, `:557-563`, `:574` (the COALESCE — add the
indicator, keep the COALESCE); `server/python/refresh_training_view_v2.py` (the view, read-only
reference for the as-of join pattern at `:252-270`); `RTP:2259` and `RTP:1800/1859` (where
`has_real_market_odds` already exists). Guardrail 3: extend `relative_market.py`'s discipline — its
docstring is the correct precedent, returning `0` for unquoted runners *because "0 is out of range
for every feature … so tree models can isolate the 'no market' case on its own branch"* — **do not
write a parallel module**. The two `FEATURE_COLUMNS` lists (`retrain_v2.py:152-275` and
`ml_model.py:65-189`) grow by two entries **byte-identically in both**.

**Interface contract.**

```python
# relative_market.py — hinted, matching the file
def compute_odds_source(odds_list: List[Optional[float]],
                        source_hints: Optional[List[str]] = None) -> List[int]:
    """0 = no quote, 1 = genuine pre-race quote, 2 = starting price, 3 = synthetic.
    Aligned with the input order, like compute_field_relative_market."""
```

Training: called from `retrain_v2.build_feature_matrix` at the point `add_relative_market_features`
is already called, using the view's own `market_odds IS NOT NULL` to distinguish 1 from 2.
Inference: called from the `RTP:2252-2260` block beside `compute_field_relative_market`, with
`source_hints` derived from whether `extract_odds` found a quote at all.

**Pseudocode / algorithm sketch.** (1) Training: `2` if the row's `market_odds` was NULL and
`sp_odds` supplied the value, `1` if `market_odds` was present, `0` if neither. (2) Inference: `1` if
`extract_odds(runner)` returned a value from the racecard `odds` array; `0` if it returned `None`;
`3` if the value came from `infer_market_odds`'s fallback chain — which requires `mc_api` to stamp
the provenance, so **if it does not, emit `0` and say so rather than guessing**. (3) Diagnosis: join
the training row's `market_odds` to the racecard price for the same runner on stored races and
publish the distribution of `log(sp/racecard)` **by favourite rank**. This needs no model change and
is the highest-value half of the ticket. Edge cases: field with no quotes at all ⇒ all `0`, matching
`compute_field_relative_market`'s all-zero default; a scratched runner never reaches the feature
build; `odds <= 1` is treated as no quote by the existing `_is_valid_odds` (`relative_market.py:70`)
and must not be re-implemented.

**Config / feature flag.** `STRIDE_ODDS_SOURCE_FEATURES`, default `false`. Off ⇒ the two columns are
absent from `feat` and, because the saved artifact's own `feature_columns` takes precedence at load
(`ml_model.py:211`), a stale artifact cannot shape-mismatch. Add to `.env.example`.

**Acceptance criteria.** **The diagnosis is the deliverable and it is measurable now.**
**Pre-registered threshold: if median `|log(sp/racecard)|` exceeds 0.15 (≈16% price difference) at
the short end, the mismatch is declared material** and a follow-on ticket is opened to restrict
training to rows with a genuine pre-race price. Then, at the next retrain, an ablation arm adding
`odds_source` on identical folds, judged by `run_ablation`'s causal AUC delta with the resolution
limit stated (≥ +0.005 AUC, sign consistent across ≥ 20 of 30 folds). **Forbidden route, stated for
the record: constraint 24 — never backfill "late odds" from a vendor's final-odds field into
historical training rows. The defect is already-shipped backfill; the fix is never more of it.**

**Rollback plan.** `STRIDE_ODDS_SOURCE_FEATURES=false`; remove the two names from both
`FEATURE_COLUMNS` lists if reverting fully. Any staged model trained with them lives in
`models/staging/` and is never promoted automatically. No schema, no published-contract change.

**Conflicts checked.** Growing `FEATURE_COLUMNS` requires the two lists to stay byte-identical — a
self-test asserting that with `ast.literal_eval` should be added in the same PR (it does not exist
today and its absence is why the drift risk is real). T23 and T25 also grow the contract; land them
in one retrain cycle, not three. `mc_api.extract_ml_features` computes the relative-market trio
independently for its own adjustment layer (`relative_market.py:12-15` documents the deliberate
parity) — adding a column here means `mc_api` will pass `0` for it, which is the "no market" branch,
so the parity note must be updated. This ticket **feeds T16**: three candidate mechanisms produce the
same short-price symptom, and this is the one that separates the third from the other two.

---

## T16. De-vig method selector on `calculate_overround` / `true_market`   `[ROI]` · `[structural]`

**What to build.** A `method=` parameter on the existing overround/true-market pair supporting
`proportional` (default, byte-identical), `power` (`p_i ∝ (1/o_i)^k`, one bisection solving
`Σ(1/o_i)^k = 1`) and `shin` (a branch of the same selector, **never a second module**). Today
`calculate_overround` (`RTP:432-442`) returns `Σ(100/o)/100` and `true_market = (100/odds)/overround`
(`RTP:673-674`) — proportional normalisation, the only de-vig anywhere in the repo, and the method
the literature names as the weakest: it *"does not account for favourite long-shot bias"*, while the
power method *"universally outperforms the multiplicative method and outperforms or is comparable to
the Shin method"* (Clarke, Kovalchik & Ingram 2017, confirmed verbatim by the citation audit; Clarke
is at Swinburne, so the AU transfer is about as direct as it gets).

**Two audit caveats that must be carried into the ticket.** (i) Štrumbelj's *"Shin's advantage
shrinks as market size grows"* was **struck as `[unverified]`** — do not argue for Shin on the grounds
that AU fields are 8–16. (ii) The numerical comparison tables could not be obtained, so **the effect
size is unknown and this ticket must not be sized on an assumed magnitude.**

Also fix the thin-race branch. `if valid < 2 or total_implied <= 0: return 1.0` (`RTP:440-441`) means
a thin race has its *raw* implied probability used as if it were vig-free, which gives the thinnest,
most scratching-affected races the most optimistic edges — precisely backwards. **The right behaviour
there is to refuse to publish an edge, not to invent a vig factor**: return a sentinel that the
caller turns into `trueMarketProb = 0, fairOdds = None, modelEdge = 0` — the treatment unquoted
runners already get at `RTP:698-701`.

**Where it lives.** `RTP:432-442` (`calculate_overround`) and `RTP:672-697` (`true_market`), extended
in place per guardrail 3 — **not** a new module, and **not** `mc_api.py:7636`'s separate un-de-vigged
edge, which is a different quantity on a different scale and is out of scope. The bisection helpers
belong beside them in `RTP`, or in `server/python/research/live_diagnostics.py` if T3's `_devig_table`
is written first and imported — one implementation, not two.

**Interface contract.** `RTP` is untyped; match it.

```python
def calculate_overround(runners, method=None):
    """method=None reads STRIDE_DEVIG_METHOD. 'proportional' returns today's
    Sum(100/o)/100 exactly. 'power'/'shin' return the fitted parameter's implied
    normaliser. Returns 1.0 only on the proportional path, for compatibility."""

def devig_probabilities(odds_list, method="proportional"):
    """-> list of fair probabilities in [0,1] summing to 1, aligned with input
    order; None for each unquoted runner. Fewer than 2 quotes -> all None."""

def _solve_power_k(implied, lo=0.5, hi=2.0, tol=1e-10, iters=60):
def _solve_shin_z(implied, lo=0.0, hi=0.2, tol=1e-10, iters=60):
```

`devig_probabilities` is called once per race inside `calibrate_and_score` (`RTP:568`), before the
per-runner loop at `:650`, and its result replaces the per-runner `true_market = raw_implied /
overround` at `:674`. Position in the call order is unchanged — de-vig still happens after the
ML blend and before the market anchor.

**Pseudocode / algorithm sketch.** (1) `implied = [1/o for quoted runners]`. (2) proportional:
`p_i = implied_i / Σimplied`. (3) power: bisect `k` on `f(k) = Σ implied_i^k − 1`; `f` is strictly
decreasing in `k` for an over-round book, so a sign change is guaranteed in `[0.5, 2.0]` when
`Σimplied > 1`; then `p_i = implied_i^k`. (4) shin: bisect `z` on
`Σ (√(z² + 4(1−z)·implied_i²/Σimplied) − z)/(2(1−z)) − 1`. (5) Renormalise defensively at the end
(`p /= Σp`) so floating-point residue cannot leak into the edge. **Edge cases, each of which changes
the answer:** fewer than 2 quotes ⇒ all `None` and the race publishes no edge (the fix above);
`Σimplied ≤ 1` (an under-round, reachable after scratchings) ⇒ no root in the bracket, clamp to
`k = 1`/`z = 0` and count it; a single quoted runner ⇒ `p = 1.0` trivially, still `None` because an
edge against a one-runner book is meaningless; `o ≤ 1` filtered by `extract_odds`'s existing
`dec > 1` test; division by zero impossible because `Σimplied > 0` is checked before every division.

**Config / feature flag.** `STRIDE_DEVIG_METHOD`, default `"proportional"` — a **string-valued**
flag, not a boolean, following `LLM_PROVIDER`'s precedent in `.env.example`. Unknown values fall back
to `proportional` with a printed `[GATE]` warning rather than raising. Add to `.env.example`.

**Acceptance criteria.** Three stages, in order.
*Stage 1 (diagnostic, no behaviour change — this is T3 counter 9):* tabulate `true_market` under all
three methods on stored fields, by price band, and publish the signed differences.
*Stage 2 (flag on, `mw` frozen):* re-run the backtest under each method and score **the log loss and
Brier of `true_market` itself against outcomes**, per price band — the market probability is a
forecast and can be scored directly, which is far more powerful than waiting for ROI. Sample: ~3,000
runner-observations gives a usable log-loss comparison; the view has 119,577 rows.
*Stage 3:* only then revisit the thresholds.
**Pre-registered threshold: adopt `power` as default only if it improves the log loss of
`true_market` against outcomes by ≥ 0.002 with a bootstrap CI excluding zero, and hit rate is
unchanged** — which it must be, because a power transform is **monotone in odds** so `odds_rank` and
within-race order are preserved. **If hit rate moves at all, the implementation is wrong, and that is
a useful unit test.** Record and check R3-F10's falsifiable prediction: the two shortest-price
backtest cells are the two worst performers (`Short Price $2–$5 3%+` **−100.0%** on 7 bets;
`Mid-Range $3–$8 5%+` **−28.0%** on 25) — consistent with, not proof of, on samples far too small.

**Rollback plan.** `STRIDE_DEVIG_METHOD=proportional`. Nothing persists; `overround` is recomputed
per race and never stored. Any proof-run output under `--output-suffix` can be deleted. **The
thresholds are not changed in this ticket**, so there is nothing to revert downstream.

**Conflicts checked.** Flipping the method re-prices `true_market` and therefore `modelEdge`
(`RTP:697`), `fairOdds` (`:695`), the anchor (`:692`) and `ev` (`:955`) for **every** runner at once,
so **the 4.0/2.5/3.0 band thresholds and the 30/15/10 probability floors must be re-read on the same
run, never ported.** `race_normaliser.py:225`'s `0.90 ≤ overround ≤ 1.60` validation is guarded by
`if odds_count >= 3` at `:223` — a two-quote race is de-vigged with no validation at all, which the
thin-race fix here also addresses; coordinate the two rather than duplicating the check.
`mc_api.py:7636-7637` keeps its own un-de-vigged edge — **do not unify.** T9's `edge_at_price` is
deliberately vig-*inclusive* and is unaffected by the method choice, which is what makes it the
control. T3 must land first (counters 9, 3) and T15's `log(sp/racecard)` diagnosis alongside, because
three mechanisms produce the same short-price symptom and only those three diagnostics separate them.

---

## T17. Renormalise win probabilities within race after the market anchor   `[ROI]` · `[structural]`

**What to build.** After `winPercentage` is written at `RTP:693`, renormalise the field to sum to
100 — **and publish the pre-renormalisation sum** so the size of the current distortion is on the
record. The three `winPercentage` assignment sites are exactly `RTP:624` (CL path), `:661` (isotonic)
and `:693` (market anchor), **and no renormalisation follows any of them.** Each is pointwise:
isotonic is a per-runner monotone map (a non-affine transform of a simplex point leaves the simplex);
the ML blend at `:667-668` mixes in an unnormalised pointwise classifier; and the `mw` ladder applies
**a different weight to different runners in the same race** — a $2.80 favourite gets 0.80, a $12
outsider 0.45 — so the pool is not even a convex combination of two normalised distributions.
`mc_api` renormalises at `:7607-7616`, but **upstream of all of this**.

**Carry R2's own warning into the ticket:** the correct order is normalise-last and the normalising
operator should be a **within-race** one (log pool / conditional logit) rather than a divide-by-sum
on an arbitrary pointwise transform. **So this is a stopgap that buys measurement**, and backlog item
A4 is the real answer. `mc_recalibration.py:204-210` (`transform_race`) is the only layer in the
entire stack that already renormalises per race after calibrating — guardrail 3 says read it before
writing a second one.

**Where it lives.** `RTP:568` `calibrate_and_score`, in a new pass **after** the per-runner loop ends
at `:701` and **before** the `mc_spread` computation at `:703-705`. Position matters: `mc_spread` is
computed on `rawModelProb`, not `winPercentage`, so renormalising `winPercentage` does not disturb
the flat-MC detector — verify that in the self-test rather than assuming it.

**Interface contract.**

```python
def _renormalise_field(horses, target=100.0):
    """Scale winPercentage so the quoted field sums to `target`. Writes
    h['_winPctPreRenorm'] and h['_fieldWinSum'] on every runner. Returns the
    pre-renormalisation sum. No-op returning the sum when it is <= 0."""
```

Called once from `calibrate_and_score` between the anchor loop and the flat-MC block. `modelEdge`
must be **recomputed** from the renormalised `winPercentage` in the same pass, or the published edge
and the published probability disagree — that recomputation is the substantive part of the ticket,
not the scaling.

**Pseudocode / algorithm sketch.** (1) `s = Σ winPercentage over runners with a real quote`.
(2) If `s <= 0` ⇒ no-op. (3) `factor = target / s`; `h["winPercentage"] = round(w·factor, 2)`;
`h["modelEdge"] = round(h["winPercentage"] − h["trueMarketProb"], 2)` for quoted runners.
(4) **Unquoted runners are excluded from both the sum and the scaling** — `RTP:698-701` leaves their
`winPercentage` on the pre-ML-blend MC scale, so including them would mix two scales into one
divisor. Publish `n_unquoted` beside `_fieldWinSum` so that choice is visible. (5) Edge cases: a
field where every runner is unquoted ⇒ no-op; a two-runner field ⇒ works, but `target = 100` over two
runners is a strong constraint — report field size with the sum; rounding to 2dp means the post-scale
sum is 100 ± 0.01, which is acceptable and must not be iterated on.

**Config / feature flag.** `STRIDE_RENORM_FINAL`, default `false`. Off ⇒ nothing runs except the
**measurement** half (`_fieldWinSum` is written regardless, because it is a diagnostic field nothing
reads). Add to `.env.example`.

**Acceptance criteria.** **Measurement precedes the change and is T3 counter 2.** Metric: the
distribution of `Σ winPercentage` per race, and its correlation with field size and with the
proportion of unquoted runners. **Sample floor, added by the Phase-4 audit: the open/close decision
requires ≥ 500 scored races** (matching T3 counter 2's floor — this ticket consumes that counter, so
the two must not disagree), with the distribution reported by field size and no band quoted below
150 races. **Pre-registered threshold: if the median race sums within ±2pp of
100 on ≥ 500 races, close this ticket as immaterial and spend the budget on A4. If it deviates by more than ±2pp,
ship the flag and measure reliability (T5) and the T4 ROI-surface shift, with the explicit
expectation that hit rate does not move at all** — a common divisor cannot change within-race order.
**If hit rate moves, the implementation is renormalising something other than a common divisor**, and
that is a bug, not a result. Self-test: on a synthetic field, ordering by `winPercentage` is identical
before and after; the sum is 100 ± 0.01; an all-unquoted field is unchanged.

**Rollback plan.** `STRIDE_RENORM_FINAL=false`. No artifacts, no schema. `_fieldWinSum` and
`_winPctPreRenorm` are underscore-prefixed internal fields that do not enter the published document
(the `_`-prefix convention is already used for `_mcSelectionScoreNorm`, `_intel_bonus`,
`_llm_top_pick`), so no output contract changes.

**Conflicts checked.** Changes the published probability scale, so **every downstream threshold must
be re-read**: the conviction ladder (`RTP:836-843`), the class-cap merit override (`:908`), the
longshot rules (`:917-931`), the bet-gate floors (`:1816/1821/1825`) and `compute_confidence`'s
`ev = calib/true_mkt − 1` (`:955`). `evaluate_bet_candidate` reads `prob = max(raw_model_pct,
win_pct)` (`RTP:1805`), so a change to `win_pct` alone can flip which of the two dominates — that
interaction must be measured, not reasoned about. Do **not** run T17 flag-on simultaneously with T16
or T18; all three move the same number and the attribution would be lost. T14 and T20 move
`rawModelProb`, which is a different quantity, but the same non-simultaneity rule applies to any A/B.

---

## T18. Move the calibrator downstream of the blend and re-enable the per-model OOF isotonic — one ticket   `[BOTH]` · `[structural]`

**What to build.** Fit and apply **one** calibrator to `rawModelProb` **after** the MC↔ML blend
(`RTP:667-670`) and **before** the market anchor (`RTP:679-692`), and in the *same change* switch the
per-model OOF isotonic calibrators back on at `ml_model.py:565`. These are one ticket because
`docs/05:100-103` forbids switching the per-model isotonic on without refitting the downstream
calibrator *"in the same change"* — which is exactly what this is.

The STRIDE-side facts are source reads and are not in dispute. The isotonic runs at `RTP:657-661` on
the **MC arm only**, *before* the ML blend at `:665-668`, so **nothing calibrates `mlPredictedProb`,
ever**, unless a stacking learner or double calibrator exists (unknown — P0-b/P0-c). The ML leg
carries three mutually inconsistent imbalance corrections — `scale_pos_weight: 9`
(`retrain_v2._get_xgb_params`), `is_unbalance: True` (`_get_lgb_params`),
`auto_class_weights: "Balanced"` (`_get_catboost_params`) — and the repo **fits the correct antidote**
(per-model `IsotonicRegression` on out-of-fold validation predictions at `retrain_v2.py:835/855/870`)
and then **does not apply it** (`ml_model.py:565`, comment: *"deliberately NOT applied here"*).
Arithmetic: `scale_pos_weight = 9` multiplies fitted odds by ~9, so a true `p = 0.10` emits ≈0.50;
blended at `ml_w = 0.40`, `rawModelProb` for an average runner inflates toward ~26 rather than ~10.

**Two audit corrections that must be carried, and they cut in opposite directions.** (i) The claim
that imbalance-induced miscalibration *"was not always able to be corrected with re-calibration"* is
**`[unverified]`**, and its primary sources are logistic-regression clinical papers, not tree
ensembles. (ii) arXiv 2606.29720 was **misused**: it does not study these three parameters, finds
SMOTE's cost small (ECE +0.009), and its headline is that **post-hoc recalibration eliminates the
damage (ECE −66%)** — it is evidence **for the remedy, not for the alarm**. So the honest framing is
*"the fitted antidote is switched off and the literature says it works"*, not *"the distortion is
uncorrectable"*. Confirmed sources that do carry weight: van den Goorbergh 2022 JAMIA 29(9):1525-1534
(*"all imbalance correction methods led to poor calibration"*, no discrimination effect) and Ranjan &
Gneiting 2010 JRSS-B 72(1):71-91 for why a linear pool of calibrated forecasts needs recalibration
after it.

**Guardrail 12, restated correctly and adopted into `docs/05` in this PR:** *calibrators may be
stacked if and only if each is fitted on data disjoint from all upstream fits; probability forecasts
may not be linearly pooled after calibration without a recalibration of the pool.* As written, the
rule **blocks the safe composition while leaving three linear pools in a row completely unguarded.**

**Where it lives.** `RTP:568` `calibrate_and_score` — the isotonic call at `:657-661` moves to after
`:670`; `server/python/ml_model.py:565` (the deliberate non-application);
`server/python/retrain_v2.py:835/855/870` (the fitted OOF calibrators, read-only here);
`server/python/calibration_model.py` (the class, called not changed); T6's `fit_calibrator.py`
performs the refit on `prediction_audit.final_win_prob`, which `store_final_probs_in_audit`
(`RTP:1582`) already writes for every runner.

**Interface contract.**

```python
# ml_model.py — hinted
def predict_proba(self, X: pd.DataFrame, distance_m: int = None) -> np.ndarray:
    """When STRIDE_POSTBLEND_CALIB is on, each base model's fitted `_isotonic`
    (set in retrain_v2.train_single_fold) is applied to its own prediction before
    the weighted average. Off: today's uncalibrated average, unchanged."""

# run_tips_pipeline.py — untyped
def calibrate_and_score(horses, overround, race_class=""):   # signature UNCHANGED
```

Call order under the flag: MC prob → ML blend (`:667-668`) → **calibrator** → market anchor
(`:679-692`) → edge. Under flag-off: calibrator (`:657-661`) → ML blend → market anchor, exactly as
today. This is the **only** pipeline reordering in the plan, and guardrail 6 requires the
expected-impact number and rollback path stated here: the expected impact is on the *level* of the
quantity every gate reads, `§A5` names the underlying defect *"the most concrete mechanism available
for 'top pick wins 33.7% but returns −4.2%'"*, and the rollback is a single env var.

**Pseudocode / algorithm sketch.** (1) Flag off ⇒ both halves are today's code; assert byte-identical
output in a proof run. (2) Flag on, ML side: for each base model, `p_cal = model._isotonic.transform(
p_raw)` if `_isotonic` exists, else `p_raw` with a printed warning — a stale artifact without
`_isotonic` must degrade to today's behaviour, not crash. (3) Flag on, wrapper side: skip the isotonic
at `:657-661`; after `raw = (1−ml_w)·mc + ml_w·ml` at `:668`, apply
`raw = calibrator.calibrate([raw/100])[0]·100`; then proceed to the anchor unchanged. (4) **Never
both**: if `STRIDE_CL_BLEND` is on, the CL path already sets `_calibrator = None` at `:627` — the new
post-blend calibrator must respect the same exclusion, and the self-test must assert it.
(5) Edge cases: `raw = 0` (no MC probability) ⇒ skip, as today's `if _calibrator and raw > 0` already
does; the calibrator's `[0.01, 0.95]` clip (`calibration_model.py:30-32`) now applies to a
post-blend quantity with a different support — that is the point, but it means the artifact **must be
refit on post-blend rows** and applying the old MC-stage artifact here would be a scale error.

**Config / feature flag.** `STRIDE_POSTBLEND_CALIB`, default `false`, read once in
`calibrate_and_score` and once in `predict_proba` — **both halves switch on the same flag**, because
enabling either alone is the double-calibration hazard the guardrail exists to prevent. Add to
`.env.example`.

**Acceptance criteria.** In order of statistical power: (1) **reliability** — the decomposed Brier
component from T5 — on the post-blend probability, flag-on vs flag-off, same folds;
(2) **calibration slope** toward 1.0 per odds band; (3) mean `rawModelProb` per card against observed
win rate (T3 counter 3), the direct test of the inflation claim; (4) paired top-pick hit rate, which
must **not** degrade. Sample: reliability and slope need events — ~1,000+ winners ≈ 10,000
runner-rows, available now in `training_view_v2`; the non-degradation clause is a paired McNemar test
needing ~2,000–2,500 races to detect a 3pp move. **Pre-registered threshold: adopt default-on only if
reliability improves, `|calib_slope − 1|` shrinks in at least four of the six price bands, and paired
top-pick hit rate is non-inferior within 1pp.** **Blocking dependency: P0-c answered and
`prediction_audit` filling** — the refit needs post-blend rows at volume and the table held 260 rows.

**Rollback plan.** `STRIDE_POSTBLEND_CALIB=false` restores both the order of operations and the
uncalibrated ensemble average in one step. Cleanup: delete the post-blend artifact and sidecar from
`models/staging/`; the live `isotonic_calibrator.pkl` is never overwritten (guardrail 9). No schema,
no published-contract change. Because the flag also controls `predict_proba`, there is no state in
which one half is on and the other off.

**Conflicts checked.** Collides with guardrail 6 (reordering) and guardrail 12 (never
double-calibrate) — discharged above, and the **only** safe form is a single ticket doing both halves.
`STRIDE_CL_BLEND` must remain off (constraint 14) and is mutually exclusive with this flag by
construction. T19 changes the calibrator *family* at the position T18 establishes — sequence
T18 → T19, never in parallel. T17 renormalises after the anchor and T16 changes `true_market`; all
three move the published probability, so only one may be flag-on in any A/B. `double_calibration.py`
and `stacking_meta_learner.py` take precedence inside `predict_proba` (`:566-592`) when their
artifacts exist — if a stacking learner is present in production, the per-model isotonic branch is
never reached, and **P0-b/P0-c must establish which branch actually fires before this ticket is
scoped**. `mc_recalibration.py` is inert but is the only layer that renormalises per race after
calibrating; read it before designing, and note that T17 is the ticket that supplies that half.

## T19. Calibrator family swap — temperature first, beta second   `[BOTH]` · `[structural]`

> **Lever re-tagged by the Phase-4 audit (was `[ROI]`).** The pointwise map is rank-preserving, which
> is where the original `[ROI]` tag came from — but this ticket's own third argument (below) is that
> swapping families changes how often `mc_is_flat` fires, and that is a **hit-rate channel**, verified
> in source this session: the calibrator is applied at `RTP:657-661` to `raw`, `raw` becomes
> `rawModelProb` at `:670`, and `mc_spread = max(rawModelProb) − min(rawModelProb)` is computed at
> `:703-705`. When the flat branch flips it re-weights the MC spine 50/50 → 65/35, applies the
> ×0.30/×0.60/×0.85 gradient penalty, and hands the LLM the `[5.0, 3.0, 1.0]` boost whose top pick
> **bypasses every safety filter** (`RTP:883`) — all of which change which horse is tipped. A ticket
> cannot claim that effect as its main benefit and simultaneously be tagged hit-rate-neutral.
> The same correction applies to `IMPROVEMENT_REPORT` item #15 and to its §0.2(c).

**What to build.** Two calibrator families beside the existing isotonic, selected by
`STRIDE_CALIBRATOR_FAMILY` (default `isotonic` = byte-identical). **Temperature scaling**: one scalar
`T` fitted by minimising negative log-likelihood on a holdout, applied as `p' ∝ p^{1/T}` renormalised
within race. **Beta calibration**: three parameters `(a, b, c)` fitted by logistic regression of the
outcome on the two features `log p` and `−log(1−p)`, giving
`p' = 1/(1 + exp(−(c + a·log p − b·log(1−p))))`.

Two arguments, one literature and one STRIDE-specific. Beta calibration *"beats both Platt scaling
and isotonic regression in a wide range of settings"* and crucially **contains the identity map**,
*"which is particularly useful to prevent over-calibration"* — that matters here specifically,
because the MC leg entering `calibrate_and_score` has already been through a Plackett-Luce
normalisation and a field renormalisation to 100%, so it is plausibly close to calibrated already,
the exact case where a mis-specified sigmoid damages a good input. Against STRIDE's **794 `live_model`
rows**, isotonic is below the ~2,000-case threshold at which it overfits.

The stronger argument is STRIDE-specific and needs no citation: the isotonic step at `RTP:657-661`
runs *before* `mc_spread = max(rawModelProb) − min(rawModelProb)` at `:703-705`. **A step-function
calibrator mapping several runners onto the same plateau mechanically shrinks that spread**, and
`mc_is_flat` then (a) forces all three picks to `low` ⇒ `0u` stakes, (b) shifts the MC-spine blend
50/50 → 65/35 (`:774/:776`), (c) applies the ×0.30/×0.60/×0.85 gradient penalty (`:785-792`), and
(d) hands the LLM a `max_score + [5.0, 3.0, 1.0]` boost whose top pick then **bypasses every safety
filter** (`RTP:883`). *A coarse calibration artifact can, on its own, turn a normal race into a
no-bet race governed by the LLM.* A smooth parametric map introduces no ties and **cannot** trip this.
Temperature scaling is additionally **provably rank-preserving**, so it satisfies the hit-rate clause
of the promotion bar by construction — the cleanest A/B primitive available anywhere in this plan.
Caveat to carry: temperature is defined on logits and STRIDE's engines emit probabilities, so the
transfer `p' ∝ p^{1/T}` renormalised within race is `[recall — unverified]` for that specific
algebra. It is the same one-parameter power family as T16's power de-vig, which is a reason to
implement **one** helper and share it.

**Where it lives.** `server/python/calibration_model.py` — extend `ProbabilityCalibrator` with a
`family=` parameter (guardrail 3 names it as the existing surface; guardrail 1 forbids replacing it).
`RTP:574-580` (load, via T6's `_load_calibrator_with_provenance`) and the apply site (T18's
post-blend position, or `:657-661` if T18 has not landed). T6's `fit_calibrator.py` fits whichever
family is selected, still writing to `models/staging/`, and records the family in the sidecar.

**Interface contract.**

```python
class ProbabilityCalibrator:
    def __init__(self, model_path=None, family="isotonic"):   # family is NEW, default preserves today
    def fit(self, predicted_probs, actual_outcomes):          # signature UNCHANGED
    def calibrate(self, predicted_probs):                     # signature UNCHANGED
    def load(self):                                           # signature UNCHANGED, reads family from the pickle
```

The pickle must carry its own family, so `load()` cannot mis-apply a beta artifact as isotonic —
same discipline as `ml_model.py:211`'s "the pickle's own `feature_columns` takes precedence".

**Pseudocode / algorithm sketch.** (1) `family="isotonic"` ⇒ today's exact `IsotonicRegression(
y_min=0.01, y_max=0.95, out_of_bounds="clip")`. (2) `family="temperature"` ⇒ minimise
`−Σ[y·log p' + (1−y)·log(1−p')]` over `T ∈ [0.2, 5.0]` by scalar bisection on the derivative or
`scipy.optimize.minimize_scalar`; store `T`; apply `p' = p^{1/T}` then renormalise **within race**,
which requires the caller to pass a field — so `calibrate()` keeps its pointwise contract and a new
`calibrate_race(probs)` does the normalised form, mirroring `mc_recalibration.transform_race`'s
existing pointwise/race pair. (3) `family="beta"` ⇒ `sklearn.linear_model.LogisticRegression` on
`[log p, −log(1−p)]`; store the three coefficients. (4) Edge cases: `p = 0` or `p = 1` ⇒ clip to
`[1e-6, 1−1e-6]` before any log; a fit set with **zero winners** ⇒ refuse to fit and leave the
previous artifact in place; `T` at a bracket bound ⇒ report it as a corner solution rather than
accepting it silently (the exact mistake the CL fit made with β); a single-runner field ⇒
`calibrate_race` returns `[1.0]`.

**Config / feature flag.** `STRIDE_CALIBRATOR_FAMILY`, default `"isotonic"`, string-valued; unknown
values fall back to `isotonic` with a `[CALIB]` warning. Add to `.env.example`.

**Acceptance criteria.** Report reliability and calibration slope (T5) **and** log loss (the
decision-aligned loss), noting the observed tension that isotonic often wins on ECE and Brier while
Platt-family maps win on log loss — **report all three and state which one is being optimised.**
Secondary and **mandatory**: the `mc_is_flat` firing rate (T3 counter 4) under each family — the
tie-free prediction is directly falsifiable, and if flat-rate does **not** fall under a smooth map,
that half of the argument is dead. Sample: a one-parameter temperature fit is stable on a few hundred
races; beta's three parameters want ~1,000+. **Pre-registered threshold: adopt temperature as default
only if log loss improves with a bootstrap CI excluding zero AND the two ordering tests below both
pass.**

**The two ordering tests, separated by the Phase-4 audit** — the original single criterion ("top-1
ordering provably unchanged on 100% of stored races") contradicted this ticket's own flat-MC argument
and could never have passed:
1. **Pointwise rank-preservation (a unit test, not a metric).** With the flat-MC branch held fixed,
   the family map must not reorder `rawModelProb` on **any** stored race. If it changes even one, the
   implementation has a bug — temperature is provably monotone.
2. **End-to-end selection change (a metric, not a bug).** The published top-1 pick **is** expected to
   change on the races where `mc_is_flat` flips, and that is the intended effect. Report the flip rate
   and the paired top-pick hit rate on those races specifically (McNemar), and **treat T19 under the
   promotion bar's ranking clause, not only its calibration clause** (T7). Default-on requires paired
   top-pick hit rate **non-inferior within 1pp** on the flipped subset as well as overall.

**Adopt beta only if it beats temperature on reliability at the same hit rate.**

**Rollback plan.** `STRIDE_CALIBRATOR_FAMILY=isotonic`. Delete any staged non-isotonic artifact and
sidecar. The live artifact is never overwritten by this ticket.

**Conflicts checked.** Sequenced strictly after T18 — the family question is decided at the corrected
position, not the current one. Mutually exclusive with `STRIDE_CL_BLEND` for the same reason T18 is
(the CL path sets `_calibrator = None` at `RTP:627`). Changing the family changes `mc_spread`, which
changes the flat-MC branch, which changes staking and the LLM's authority — so T19 and T21 interact
and their A/Bs must not overlap. T16's power de-vig shares the one-parameter power algebra; implement
the exponent helper once. `double_calibration.py` is a separate layer that takes precedence inside
`predict_proba` when its artifact exists — unaffected, but it means the family swap governs only the
wrapper-level calibrator.

---

## T20. Repair the three inert context multipliers and the `rawModelProb` ordering defect   `[BOTH]` · `[structural]`

**What to build.** Four related repairs at `RTP:719-729`, shipped **separately, in this order**.
(1) `fitnessReadinessScore` is written by `mc_api` only nested as `fitnessData.fitnessReadinessScore`
(`mc_api.py:7545`) while `RTP:719` reads it at top level ⇒ always the default 50 ⇒
`fitness_mult ≡ 1.00`. Read it from where `mc_api` actually writes it. (2)
`jockey_momentum_adjustment` is an mc_api *feature* (`mc_api.py:5848`), never present in the result
dict ⇒ `jockey_mult ≡ 1.00`. Either surface it in the result dict or delete the multiplier — do not
leave a third state. (3) `trackBiasPoints` ranges ≈ −25…+45 (`track_bias_points.py:891-916`) fed into
a `/100.0` map designed for 0–100 (`RTP:721-722`) ⇒ near-constant **×0.95**. Rescale to its true
range. (4) `rawModelProb` is rewritten at `:729` **after** `modelEdge` was computed from it at
`:697`, so `raw_model_pct`, `win_pct` and `edge_pct` are published on three mutually inconsistent
scales, and every downstream raw-prob threshold — conviction 15/12/10 (`:836-843`), bet-gate floors
30/15/10 (`:1816/1821/1825`), longshot `raw ≥ 8` (`:917`) — silently operates on a ~5%-deflated
number.

**The caveat is load-bearing and must be in the ticket:** none of this was verified by execution.
`SYSTEM_MAP §9 Q3` records that whether the multipliers are truly inert in production *"cannot be
confirmed without execution"* and *"a runtime print is the only proof."* Compounding from a different
direction: `mlPredictedProb` is **not race-normalised** in production (`RTP:2323`) while
`backtest_v2_metro.py:175-177` race-normalises `model_prob → norm_prob` and **every** reported metric
is computed on `norm_prob` — so those same constants were tuned against numbers the live system does
not produce.

**Where it lives.** `RTP:719-729`; `server/python/mc_api.py:7545` and `:5848` (producers — surfacing
`jockey_momentum_adjustment` means adding one key to the result dict, which is additive);
`server/python/track_bias_points.py:891-916` (read-only, for the true range).

**Interface contract.**

```python
def _context_multipliers(h):
    """-> (fitness_mult, bias_mult, jockey_mult, applied_flags). Each multiplier is
    1.0 unless its own flag is on. Never raises; a missing input yields 1.0."""
```

Called from `calibrate_and_score` at `RTP:719`, replacing the three inline expressions. The
`applied_flags` tuple is what the `[RACE_CTX]` diagnostic line prints.

**Pseudocode / algorithm sketch.** **Step 1 is a print, not a fix.** Emit three stderr lines in the
house `[RACE_CTX]` style reporting the realised distribution of `fitness_mult`, `bias_mult` and
`jockey_mult` over a card. That converts `§9 Q3` from grep-inference to fact and costs nothing.
**Only then** repair: (a) fitness — `h.get("fitnessData", {}).get("fitnessReadinessScore", 50)`,
keeping the same `0.95 + x/100·0.10` map, which is correct for a 0–100 input; (b) bias — map the
true range with `0.95 + ((pts + 25)/70)·0.10`, clamped to `[0.95, 1.05]`, so the documented
±5% band is actually realised; (c) jockey — if `mc_api` surfaces the value, keep the existing
`clamp(x, 0.85, 1.20)`; if not, **delete the multiplier and its clamp** rather than leaving a dead
read. (d) The ordering fix moves the `rawModelProb` rewrite **above** the `modelEdge` computation, or
— the safer form — leaves the rewrite where it is and computes `modelEdge` from the same
context-adjusted quantity, so the three published numbers land on one scale. Edge cases: a runner
with no `fitnessData` ⇒ 1.0, not 0.95; `trackBiasPoints` outside ±[25, 45] ⇒ clamp, and count the
clamp; all three flags off ⇒ the function returns `(1.0, 1.0, 1.0)`, which is **not** today's
behaviour (today is a uniform ~×0.95 shrink) — so "flags off" must mean *today's exact expressions*,
not "no multiplier". Getting that distinction wrong silently re-levels every score.

**Config / feature flag.** Four independent flags, all default `false`: `STRIDE_CTX_MULT_FITNESS`,
`STRIDE_CTX_MULT_BIAS`, `STRIDE_CTX_MULT_JOCKEY`, `STRIDE_RAWPROB_ORDER_FIX`. Plus
`STRIDE_CTX_MULT_DIAG` (default `false`) for the step-1 prints. **Each multiplier ships separately**
so its effect is attributable. All five in `.env.example`.

**Acceptance criteria.** Metric: paired top-pick hit rate and reliability, flag-on vs flag-off on
identical stored races. Sample: **~2,000–2,500 races paired** for a 3pp hit-rate move.
**Pre-registered threshold: each multiplier ships separately; default-on only if paired hit rate is
non-inferior within 1pp and reliability does not degrade.** **The ordering defect (4) is fixed last
and separately**, because moving the `rawModelProb` rewrite above the `modelEdge` computation changes
`modelEdge` for every runner — a T16-scale re-pricing wearing the costume of a one-line reorder, and
guardrail 6 applies to it in full: the same run must re-read the conviction ladder, the bet-gate
floors and the longshot rules rather than porting them.

**Rollback plan.** Set the four flags to `false` individually or together. No artifacts, no schema.
The `mc_api` result-dict addition for `jockey_momentum_adjustment` is additive and inert when the
flag is off; leaving it in place is harmless.

**Conflicts checked.** **Highest risk-per-line item in this plan.** Every downstream raw-probability
threshold has been silently tuned against a ~5%-deflated number, so this ticket cannot be judged on
its own metric alone. `evaluate_bet_candidate` reads `prob = max(raw_model_pct, win_pct)`
(`RTP:1805`) — changing `rawModelProb`'s level changes which term wins. `apply_safety_filters`'
conviction bonus is added to `selectionScore` *before* sorting (`:846`), so a level shift reorders
picks. T14 also moves the raw-prob scale; **never run T14 and T20 flag-on in the same A/B.** T17
moves `winPercentage`, the other half of the `max()`. `mc_api.py:5848` is inside the feature
extractor, not the result assembly — surfacing the value must not disturb
`extract_all_sophisticated_features`' output shape, which feeds `predict_adjustment` and the
0.55-weighted `ml_adjustment` term at `mc_api.py:7379`.

---

## T21. Separate "should we bet" from "how much", and instrument the flat-MC breaker   `[BOTH]` · `[structural]`

**What to build.** Two phases, and **phase (b) only ships if phase (a) shows it matters**.
**(a)** A `[GATE]` counter for every filter outcome in `apply_safety_filters` and
`evaluate_bet_candidate`, plus the `[MC_FLAT]` firing-rate counter (also T3 counter 4), broken down
by field size. **(b)** Split the two concerns: `evaluate_bet_candidate` decides bet/no-bet;
`compute_staking` decides size and **may not return zero** — a race the system will not bet must be a
`NO_BET` with a reason, not a `BET` at `0u`.

The defect: `compute_staking` (`RTP:1007-1015`) is exactly `high→"2u"`, `medium→"1u"`, else `"0u"`,
so **a `0u` "bet" is not a bet**. If `0u` rows are counted in the bet population, hit rate and ROI
are computed over a set including non-bets; if they are not, the staking function has silently
changed the denominator. The trigger is far broader than "low confidence": `mc_spread < 6.0`
(`RTP:705`) forces **all three picks to `low`** (`RTP:2466-2469`), so an uninformative simulation
zero-stakes the entire race while the LLM's `[5.0, 3.0, 1.0]` boost (`RTP:2445`) simultaneously takes
over the ordering with `_llm_top_pick` bypassing every safety filter (`RTP:883`). **A single
dispersion statistic flips both the ranking authority and the entire stake schedule at once, and
nobody knows how often.** The aggravating fact is in-repo and needs no citation: the comment at
`RTP:941-948` records that the v1 confidence ladder was **anti-correlated with value** — mean EV
**+0.036 for "high" vs +0.152 for "low"**, n = 330, 2026-04-14. **The stake size is keyed to a label
whose own recorded history points the wrong way, and which was demoted rather than removed.**

**Where it lives.** `RTP:950-1015` (`compute_confidence`, `compute_staking`); `RTP:703-705`
(`mc_spread`); `RTP:2438-2469` (the flat-MC branch); `RTP:1778` (`evaluate_bet_candidate`);
`server/python/validate_tips.py` and `server/python/backfill_tips_contract.py` — the invariants that
must still hold, and `backfill_tips_contract` imports the live functions so the logic cannot drift
(guardrail 11: keep that import, do not fork it).

**Interface contract.**

```python
def compute_staking(h):                                   # signature UNCHANGED
    """With STRIDE_SEPARATE_STAKE_GATE on, returns '2u' or '1u' only; the
    should-not-bet case is expressed by evaluate_bet_candidate, not by '0u'."""
def _gate_counter(name, outcome, **ctx):
    """Emit `  [GATE] name outcome=... k=v` to stderr when STRIDE_GATE_COUNTERS is on."""
```

`_gate_counter` is called at each `continue` in `apply_safety_filters` (`RTP:903, 912, 923, 926,
930`), at the fallback (`:935`), and at each `return False` in `evaluate_bet_candidate`
(`:1808, 1810, 1813, 1817, 1822, 1826, 1829`). Phase (b) adds a `low`-confidence branch inside
`evaluate_bet_candidate` so the refusal is expressed there and `compute_staking` never returns `0u`.

**Pseudocode / algorithm sketch.** Phase (a): count and print; nothing else. Phase (b): (1) if
`confidence == "low"` and the pick would otherwise pass, `evaluate_bet_candidate` returns
`False, "NO BET — low confidence"` — making the existing implicit rule explicit at the gate;
(2) `compute_staking` maps `high→2u`, `medium→1u`, and is only ever called on picks that already
passed the gate. (3) The flat-MC branch keeps forcing `low` — **that behaviour is not changed here**;
what changes is that it now produces an explicit `NO_BET` with a reason instead of a `BET` at `0u`.
Edge cases: a race where every pick is `low` ⇒ `bet_status = "NO_BET"` with the flat-MC reason, which
`validate_tips` already accepts (`bet + no_bet == total`); a pick promoted by the crowd gate
(`crowd_promoted`) while confidence is `low` ⇒ the crowd gate runs **after** and overrides in both
directions (`RTP:2638-2757`), so the interaction must be enumerated in a table, not left to
precedence-by-accident; the intelligence override (`RTP:1795-1797`) returns `True` before any of this
and is unaffected.

**Config / feature flag.** `STRIDE_GATE_COUNTERS` (default `false`) for phase (a);
`STRIDE_SEPARATE_STAKE_GATE` (default `false`) for phase (b). Both in `.env.example`. Phase (a) is
log-only and can ship immediately; phase (b) must not ship until (a) has a month of data.

**Acceptance criteria.** **Phase (a) first and separately.** Metric: the fraction of races where
`mc_is_flat` fires, **by field size**, over a month. **Pre-registered alarm threshold: > 15% of
races** ⇒ phase (b) is justified. Field-size breakdown is the sharpest test of *why* it fires: if the
model under-separates in big fields, post-hoc renormalisation of a flat vector yields a flat vector,
*"which is literally the `mc_is_flat` failure mode the pipeline has special-cased throughout"* — and
that also pre-tests T24 for free. **Phase (b):** bet hit rate and ROI recomputed with `0u` rows
excluded from the denominator, compared with the current series; **the difference between the two
series is the size of the measurement distortion.** No power calculation is needed — it is a
re-partition of the same rows, not a hypothesis test. Contract check, mandatory:
`python server/python/validate_tips.py --all --strict` passes, and
`backfill_tips_contract.py` re-stamps historical files without changing any `bet_status`.

**Rollback plan.** Both flags to `false`. Phase (b) changes the published `bet_status` for affected
races while on, so a rollback means re-running `backfill_tips_contract.py` over the affected dates to
restore the previous stamping — that is exactly what that module exists for. No schema.

**Conflicts checked.** Guardrails 10 and 11 bind: the BET/NO_BET contract must still resolve every
race to exactly one with a reason, and the bet must remain the raw model leader with **no hidden
substitutes**. `tips_day_aggregates.select_best_bets` filters on `confidence == "high"` and
`select_value_plays` on `edge > 3` and `4 ≤ odds ≤ 15`; removing `0u` rows changes what reaches them.
`shadow_pl_tracker.cmd_record` inserts every tier including non-bet ones, so T1's ledger must record
which denominator convention was used — **the two tickets must agree on it or the ROI series
silently changes meaning.** T19's family swap changes `mc_spread` and therefore the flat-MC rate;
their A/Bs must not overlap. The crowd gate can promote a `NO_BET` to `BET` (`crowd_promoted`) after
this code runs — enumerate that interaction explicitly.

---

## T22. Shadow price-aware stake column   `[ROI]` · `[structural]`

**What to build.** Publish, per pick, `kelly_fraction_shadow` = `f* = EV/(o − 1)` multiplied by the
repo's own `KELLY_FRACTION_DEFAULT = 0.25` and shrunk by the runner's probability uncertainty.
**Published, logged, replayed against T8's bankroll path — never applied to a real stake.**

The sizing gap: the value band's implied mean price is `o = 1.123/0.099 ≈ $11.3`, giving
`f*_full = 0.123/10.34 = 1.19%` against `2u = 2%` of bank (`unit_percent = 0.01`, `RS:132`) —
**1.68× full Kelly**; a $2.50 shot with a genuine +5% EV gives `f*_full = 3.33%`, so `2u` is
**0.60×** — a **2.8× mis-sizing spread, over-betting precisely the runners whose probabilities are
least reliable.** `compute_staking` (`RTP:1007-1015`) **receives no price argument at all** — its only
input is `h["confidence"]`. Fractional Kelly is justified by the `c(2−c)` growth identity and the
confirmed MacLean/Ziemba/Blazenko 1992 figures (full Kelly ≈ 1/3 chance of halving before doubling vs
half-Kelly ≈ 1/9; half-Kelly retains ≈75% of growth). **`[unverified]` and excluded from sizing:**
the *"30% Kelly cuts an 80% drawdown from 1-in-5 to 1-in-213"* figure and Sun & Boyd's *"±15% ⇒
>1.5×"*.

**Shrinkage: ship the simplest defensible form, not a reconstructed one.** Baker & McHale's closed
form could not be obtained and must not be guessed. Use the degenerate one-dimensional robust form —
**size on the lower end of the probability interval rather than its centre** — with the interval
taken from, in order of preference: T10's `win_prob_var` (best), the MC Wilson interval
(`RS:329-339`, `ci_alpha = 0.10`, surfaced as `ciLower`/`ciUpper` at `mc_api.py:7479-7481`), or the
engine-disagreement interval `[min(mc, ml), max(mc, ml)]` — **both of the latter are already in scope
at `RTP:667`.** The sentence to put at the top of the ticket: *the system measures engine
disagreement, uses it to shrink scores, and then stakes as if the probability were certain.*

Fix one adjacent defect in the same ticket: `RS:319-320` returns `min(max_stake, kelly)` where
`kelly` has **already** been multiplied by `fraction` at `:319`, so with `fraction = 0.25` and
`MAX_KELLY_STAKE = 0.05` the cap binds only when *full* Kelly exceeds 20% of bankroll — for
`f* ≈ 1.2%` it can **never** bind. The cap is decorative.

**Where it lives.** `racing_system_v8.3_mc.py:309-320` (`kelly_stake`) — guardrail 3 names this and
**not** `portfolio_risk.py`, which is dead and computes its variance at `q = 1/odds` (`:235`) while
using the model probability for EV at `:234`. Publication: `RTP:1245` `store_selections_in_db`, one
additive column with a **new name** — `selections.kelly_stake` is already a **decoy** (`RTP:1334`
names it; `RTP:1462` binds `int(pick.get("staking","0u").replace("u",""))`, the 2/1/0 unit count), and
that decoy should be documented in `docs/09` in the same PR. Also note `mc_api.py:7483`'s `kellyStake`
is structurally always `0.0` and is published to an unseen frontend as a constant zero — document it;
do not "fix" it without knowing who reads it.

**Interface contract.**

```python
def kelly_stake(prob, odds, fraction=KELLY_FRACTION_DEFAULT, max_stake=MAX_KELLY_STAKE,
                cap_applies_to="fractional"):
    """cap_applies_to='fractional' preserves today's behaviour exactly.
    'full' applies max_stake to the unfractioned Kelly, which is what the
    docstring's ruin argument actually describes."""

def shadow_kelly_fraction(prob, odds, prob_lower=None, fraction=0.25):
    """f* = (p*o - 1)/(o - 1) evaluated at prob_lower when supplied, times
    `fraction`. Returns 0.0 when the edge is non-positive, odds <= 1, or
    prob is outside (0, 1) — matching kelly_stake's existing guards."""
```

`shadow_kelly_fraction` is called in `RTP` after `compute_staking` (`RTP:1007`), writing
`pick["kelly_fraction_shadow"]`. **`compute_staking`'s return value is not used differently.**

**Pseudocode / algorithm sketch.** (1) `p = win_pct/100`; `p_lo` = the lower interval end from the
best available source; (2) `f_full = (p_lo·o − 1)/(o − 1)`; (3) `f = 0.25·f_full`, floored at 0;
(4) publish `f` and the interval source used, so the three sources can be compared later. Edge cases:
`o ≤ 1` ⇒ 0.0; `p_lo ≥ 1` or `≤ 0` ⇒ 0.0; no interval available ⇒ fall back to `p` itself and
**record that**, because an unshrunk fraction is a different quantity; `p_lo·o ≤ 1` ⇒ 0.0, which is
the whole point of the sign test in T9.

**Config / feature flag.** `STRIDE_SHADOW_KELLY`, default `false`. `cap_applies_to` defaults to
`"fractional"` so `kelly_stake`'s existing callers are byte-identical. **Caller list corrected by the
Phase-4 audit** — they are `kelly_stake_by_confidence` (`RS:326`), `RS:2006` and
`RacingSystem` (`RS:2500`); the previously cited `RS:3163` is the standalone CLI's
`base_unit = args.bankroll * STAKING_CONFIG['unit_percent']`, not a `kelly_stake` call. The
substantive claim is unchanged and re-verified: **none of the three is reachable from the daily
pipeline** (`run_tips_pipeline.py` contains zero occurrences of the string `bankroll`, re-grepped this
session). Add to `.env.example`.

**Acceptance criteria.** Replay `stride_tip_results` under (i) the live `2u/1u/0u` rule and (ii) the
shadow fraction, and compare **expected log-growth and maximum drawdown, not ROI%** — exactly the
criterion T7 adds, because the existing bar would reject the correct answer. Sample: a growth-rate
comparison on a replayed path is not a hypothesis test about a population mean; report the path with
a **block bootstrap over race-days** for the CI and require **≥ 500 settled bets** before quoting a
number. **Pre-registered threshold: the shadow rule shows higher replayed log-growth AND lower
maximum drawdown than the live rule over ≥ 500 bets, and CLV (T2) is positive over the same window.**
**Explicit prohibition: do not apply it.** *"Kelly sized off an edge whose t-stat is 0.43 and whose
ROI is non-monotonic in the edge itself is the exact configuration the literature says produces
ruin"*, and *"establishing live ECE must precede, not follow, any Kelly wiring."*

**Rollback plan.** `STRIDE_SHADOW_KELLY=false`; `DROP COLUMN IF EXISTS kelly_fraction_shadow`. The
`cap_applies_to` parameter defaults to today's behaviour, so `kelly_stake` needs no revert.

**Conflicts checked.** Requires T8 (bankroll path), T10 (`win_prob_var`) and T7 (a bar that can grade
a staking change). Does not touch `compute_staking`, so live stakes are unchanged — **that separation
is the ticket's entire safety argument.** `selections.kelly_stake` must not be reused (decoy).
`portfolio_risk.py` must not be resurrected on plausibility, and not at all before `:235` is fixed.
The within-race multi-outcome Kelly gap is **deliberately out of scope** — R4-F9 says *"do not
propose in Phase 4"*, the one-bet-per-race contract is guardrail 10, and
`portfolio_risk.optimize_stakes` treats mutually exclusive runners as independent and sums variances
with **no covariance term**. Record it as a known bound in `docs/10`; do not build it.

### A note governing T23–T26 (the retrain-gated block)

All four grow or reweight the training contract and **nothing happens until a retrain runs and
`retrain_v2.run_ablation` returns a *causal* AUC delta** (constraint 36 — the Phase-5 precedent: the
relative-market trio ranked #3/#6/#11 of 113 by importance and ablated at **−0.0012 AUC**;
*"importance proves the trees use the encoding, not that it adds skill"*). Three shared rules:

- **Resolution limit, stated up front rather than discovered afterwards.** The existing regime is 30
  folds over 8,995 races with fold-std 0.044 AUC, so **a delta smaller than ~0.01 AUC is not
  distinguishable from noise on this harness.** Every threshold below is written against that.
- **Contract growth is byte-identical in two places.** `retrain_v2.FEATURE_COLUMNS` (`:152-275`) and
  `ml_model.RacingMLModel.FEATURE_COLUMNS` (`:65-189`) are identical today and must stay so; add a
  CI self-test using `ast.literal_eval` on both files — it does not exist and its absence is why the
  drift risk is real. The saved artifact's own `feature_columns` takes precedence at load
  (`ml_model.py:211`), so a grown contract cannot shape-mismatch a stale pickle.
- **Staged only.** New artifacts go to `models/staging/` and a human promotes them (guardrail 9).
  Land T15, T23, T25 in **one** retrain cycle, not three — each cycle is the expensive part.

---

## T23. Race-relative fundamentals   `[HIT-RATE]` · `[structural]`

**What to build.** For 8–10 fundamentals that carry actual handicapping signal, add a within-field
`_z` (value minus field mean, divided by field standard deviation) and/or `_rank` (1 = best in the
field, ties share the lower rank, matching `relative_market.py:61`'s convention). The candidate set:
`weighted_form_score`, `distance_strike_rate`, `course_strike_rate`, `class_level`, `weight_kg`,
`days_since_run`, `consistency_score`, `improvement_score`, `first_up_win_rate`. Computed within
today's field, grouped by the same `(race_date, track, race_number)` key `relative_market` and the
pace features already use.

The gap is a source read: the only within-race-relative features in the 113-column contract are
`fair_implied_prob`, `odds_rank`, `odds_rank_pct` (**market only**), the sectional z-scores (per-race
z-scores, but of the horse's **prior** race) and `sectional_rank_at_distance`. **Everything carrying
actual handicapping signal is an absolute level**, while the mechanism being modelled is competitive:
*"a participant's chance of success depends not only on individual capabilities but also on those of
competitors"* (Lessmann, Sung & Johnson 2010, IJF 26(3):518-536 — venue and abstract confirmed; **the
paper's ROI headlines are `[unverified]` and must not be quoted**).

**The caveat that must appear in the ticket:** Phase 5 proved that a relative re-encoding of an
*already-present* column is worthless. The difference claimed here is that the fundamentals are
**not** already present in relative form and their absolute levels are genuinely not comparable
across race classes. **That claim is a hypothesis. The burden of proof is a causal ablation.**

**Where it lives.** `server/python/relative_market.py` — extend it; guardrail 3 names it as the
ready-made template and its docstring is the correct precedent for the "no data" encoding.
`retrain_v2.build_feature_matrix` (grouped by the same race key as the pace and Phase-5 features) and
`RTP:2251-2260` for the inference-side mirror, following `docs/12 §4a`'s precedent of keeping both
paths identical so **`mc_api` needs no edits**.

**Interface contract.** Match `relative_market.py`'s hinted style.

```python
RELATIVE_FUNDAMENTALS = ("weighted_form_score", "distance_strike_rate", ...)   # module-level tuple

def compute_field_relative_values(values: List[Optional[float]]) -> List[dict]:
    """-> [{'z': float, 'rank': float}, ...] aligned with input order.
    Fewer than 2 usable values, or zero variance -> {'z': 0.0, 'rank': 0.0} for all."""

def add_relative_fundamental_features(out: pd.DataFrame, df: pd.DataFrame,
                                      race_date_col: str = "race_date",
                                      track_col: str = "track",
                                      race_number_col: str = "race_number") -> pd.DataFrame:
    """Same grouping contract as add_relative_market_features."""
```

Training: called from `build_feature_matrix` immediately after `add_relative_market_features`.
Inference: `compute_field_relative_values` called per feature in the `RTP:2252-2260` block, before
the per-runner `feat` build so the whole field is in scope.

**Pseudocode / algorithm sketch.** (1) Collect the field's values for one feature. (2)
`mu = mean(usable)`, `sd = std(usable, ddof=0)`. (3) `z_i = (v_i − mu)/sd`. (4)
`rank_i = 1 + count(v_j > v_i)` for higher-is-better features, reversed for `days_since_run` and
`weight_kg` — **the direction must be declared per feature in `RELATIVE_FUNDAMENTALS`, not inferred.**
Edge cases, each of which must return the documented sentinel rather than raising: **zero variance**
(every runner identical, common for `class_level`) ⇒ `sd = 0` ⇒ division by zero ⇒ return `z = 0.0`
for all, which is the truthful answer; **single-runner group** ⇒ all sentinels; **fewer than 2 usable
values** ⇒ all sentinels, mirroring `compute_field_relative_market`'s `len(quoted) < 2` branch;
**missing values** ⇒ excluded from `mu`/`sd` and given `z = 0.0`, `rank = 0.0` — and `0` is out of
range for `rank`, so the tree can isolate the case on its own branch, exactly as
`relative_market.py`'s docstring argues; **scratchings** are removed by `filter_active_runners`
before inference and by the view's own filters in training, so the field is the field.

**Config / feature flag.** `STRIDE_RELATIVE_FUNDAMENTALS`, default `false`. Off ⇒ neither path
computes the columns and neither `FEATURE_COLUMNS` list contains them. Add to `.env.example`.

**Acceptance criteria.** `retrain_v2.run_ablation` with a **dedicated arm dropping the new block on
identical folds** — a causal AUC delta, not importance — plus the walk-forward top-1 hit rate against
the market-favourite baseline. **Pre-registered threshold: ablation delta ≥ +0.005 AUC with the sign
consistent across ≥ 20 of 30 folds, AND paired top-1 hit rate up.** Below that, the block is kept
only if it is free (as Phase 5 was) or dropped. Self-test (CI-wired, extending
`relative_market.py`'s existing one): zero-variance field returns all zeros; ties share the lower
rank; a two-race DataFrame groups correctly and aligns by index.

**Rollback plan.** `STRIDE_RELATIVE_FUNDAMENTALS=false` and remove the names from both
`FEATURE_COLUMNS` lists. Any artifact trained with them stays in `models/staging/` and is never
promoted automatically; delete it. No schema, no output-contract change.

**Conflicts checked.** `run_ablation` currently hard-codes two arms (`PHASE2_FEATURES`,
`PHASE5_FEATURES`) at `retrain_v2.py:1104-1114` — adding a third arm means editing that function,
which is additive. `relative_market.py`'s self-test is in CI; extending the module means the CI job
imports more, and it already installs `pandas`/`numpy`. `mc_api.extract_ml_features` computes the
Phase-5 trio independently for train/serve parity — the new block is **not** mirrored there, so
`mc_api`'s adjustment path will pass the sentinel `0.0`, which is the documented "no data" branch;
say so in the docstring. T15 and T25 grow the same two lists in the same cycle.

---

## T24. Field-size-aware sample weighting   `[HIT-RATE]` · `[structural]`

**What to build.** Replace the scalar `scale_pos_weight = 9` with a per-row `sample_weight`
proportional to field size (or, as the cheaper variant, add explicit `field_size × <feature>`
interactions), so the trainer is not fitting one average base rate across all field sizes.

The structural claim needs no source: the win base rate is mechanically `1/n`, and
`scale_pos_weight = 9` implies `1/(1+9) = 10%`, i.e. calibrated for a ~10-runner field and **wrong at
both tails** — while LGB uses `is_unbalance` and CatBoost `auto_class_weights="Balanced"`, so there
are **three mutually inconsistent imbalance treatments in one ensemble**. **The effect size is
practitioner-sourced, not peer-reviewed** (top-rated horses winning 37.7% in fields of ≤7 vs 16.6% in
16+) and must not be used to size the ticket. The free observation is the sharpest part: *"the post-hoc
renormalisation fixes the sum but not the shape: if the model under-separates in big fields,
normalising a flat vector yields a flat vector — which is literally the `mc_is_flat` failure mode."*

**Where it lives.** `server/python/retrain_v2.py` — `_get_xgb_params` (`scale_pos_weight: 9`),
`_get_lgb_params` (`is_unbalance`), `_get_catboost_params` (`auto_class_weights`), and the three
`.fit()` calls in `train_single_fold`. `field_size` is already a contract feature, so the interaction
variant needs only `build_feature_matrix`. **The full fix — a grouped ranking objective — is blocked
by constraint 15 and belongs to T13 / backlog X3.**

**Interface contract.**

```python
def _field_size_sample_weights(field_sizes: pd.Series, y: pd.Series) -> np.ndarray:
    """Per-row weight. Losers weight 1.0; winners weight (n_i - 1) so each race
    contributes the same total positive mass its own field implies. Returns
    all-ones when STRIDE_FIELDSIZE_WEIGHTS is off — the byte-identical path."""

def train_single_fold(X_train, y_train, X_val, y_val, ...):   # existing signature UNCHANGED
```

The weight vector is passed as `sample_weight=` to all three `.fit()` calls. When the flag is on,
`scale_pos_weight` is dropped from the XGB params **in the same change** — carrying both would apply
the correction twice.

**Pseudocode / algorithm sketch.** (1) Join `field_size` per row (already a column). (2)
`w_i = (n_i − 1)` if `y_i == 1` else `1.0`. This makes each race's positive mass equal to its own
negative mass, i.e. a per-race balanced problem, which is the field-size-correct generalisation of a
single `scale_pos_weight`. (3) Normalise so `mean(w) = 1` to keep learning rates comparable across
folds. Edge cases: `n = 1` ⇒ `w = 0` for the winner, which would silently drop the row — floor at
`1.0` and count it; missing/zero `field_size` ⇒ fall back to the global `scale_pos_weight` value of 9
for that row and count it; a fold with no winners ⇒ already impossible given the view's filters, but
guard the normalisation against `mean(w) = 0`.

**Config / feature flag.** `STRIDE_FIELDSIZE_WEIGHTS`, default `false`. Off ⇒ all three
`.fit()` calls receive no `sample_weight` and today's three imbalance settings are unchanged.
Add to `.env.example`.

**Acceptance criteria.** **The cheapest possible test comes first and is free:** measure the
`mc_is_flat` firing rate **by field size** (T3 counter 4 / T21 phase (a)). If flat-rate rises with
field size, the under-separation claim has support; **if it does not, close this ticket without a
retrain.** Then: ablation with `sample_weight` vs the scalar on identical folds, reporting AUC, log
loss and **top-1 hit rate stratified by field-size band**. Sample: stratifying 8,995 races into four
bands leaves ~2,000 each, supporting a ~5pp per-band hit-rate comparison and not much finer.
**Pre-registered threshold: top-1 hit rate improves in the extreme bands (≤ 8 and ≥ 14 runners)
without degrading the middle, and aggregate AUC is non-inferior. If the gain is confined to the
middle band, the change is doing nothing `field_size`-as-a-feature was not already doing.**

**Rollback plan.** `STRIDE_FIELDSIZE_WEIGHTS=false` restores `scale_pos_weight = 9` and the
unweighted fits. Delete the staged artifact. No schema, no inference-side change at all — this is
purely a training-time reweighting.

**Conflicts checked.** Changes the probability **level** the ensemble emits, so it interacts directly
with T18 (which recalibrates that level) — **run T24's ablation with T18's flag in a fixed state, and
say which.** Dropping `scale_pos_weight` while leaving `is_unbalance` and `auto_class_weights` in
place would leave the three treatments inconsistent in a *new* way; the ticket must state which of
the three it replaces and why. `walk_forward_backtest.py` uses `RacingMLModel.train`, a different
trainer with its own `spw = neg/pos` computation (`ml_model.py:281-283`) — it is **not** changed here,
so the two harnesses will disagree; note it rather than propagating the change.

---

## T25. Cheap missing-indicator and shrinkage features (bundle)   `[HIT-RATE]` · `[structural]`

**What to build.** Four small, independent, retrain-gated additions, bundled because none justifies a
ticket alone and the resolution limit means they must be evaluated as a block.
(a) `career_starts` (used *inside* `trial_x_experience` today but never exposed) and a
`has_prior_form` binary indicator. (b) The Glicko-2 rating and its deviation as a feature pair.
(c) Weight expressed **relative to the field and to weight-for-age** rather than raw. (d)
**Empirical-Bayes shrinkage** on the jockey/trainer strike rates: `p̂_shrunk = (n·p̂ + k·p_pop)/(n + k)`
with `k` fitted from the between-group variance, rather than a raw rate dominated by mount quality.

The in-repo argument is better than the literature: `has_sectional_data` (`RTP:2293`, clamped in both
`retrain_v2.py:686-687` and `ml_model.py:221-222`) **proves the team already knows this pattern and
applies it correctly — it just was not generalised.** `glicko2_elo.py` is self-tested, in CI, and has
**zero production callers**: a per-horse latent-ability estimate with explicit uncertainty, unused.
The literature is `[snippet-only]` throughout and the jockey/trainer finding is explicitly that the
rider is the **smallest** modelled component — *"the race effect had the highest variance component…
followed by… the jockey effect"* — so shrinkage is the right treatment and the expected gain is
small. **Do not quote the practitioner weight effect sizes.**

**Where it lives.** `server/python/form_feature_builder.py` (the as-of-safe producer;
`compute_single_horse_features` at `:29` and `batch_compute_form_features` at `:904` are the two
entry points); `server/python/glicko2_elo.py` — **call `Glicko2Engine.get_rating(horse, surface)`
(`:159`), do not rewrite it**; both `FEATURE_COLUMNS` lists; `RTP:2258-2308` for the inference mirror.
**`ml_model.py:250-252`'s `TargetEncoder` is adjacent and must not be extended** — it is correctly
implemented leave-one-out (Phase 3 §5.1) and its problem is a *missing inference-side transform*, not
its fitting scheme.

**Interface contract.**

```python
# form_feature_builder.py — untyped-with-hints, matching the file
def compute_missingness_features(prior_runs: pd.DataFrame) -> Dict[str, float]:
    """-> {'career_starts': float, 'has_prior_form': int}. Empty frame -> {0.0, 0}."""

def shrink_strike_rate(wins: int, starts: int, pop_rate: float, k: float = 50.0) -> float:
    """Empirical-Bayes shrinkage toward pop_rate. starts == 0 -> pop_rate exactly."""

def compute_glicko_features(engine, horse_name: str, surface: str) -> Dict[str, float]:
    """-> {'glicko_rating': float, 'glicko_rd': float}. Unrated horse -> the engine's
    defaults (mu 1500.0, phi 350.0) plus a 'glicko_is_default' indicator."""
```

All three are called from the existing per-horse feature computation in
`batch_compute_form_features` (training) and mirrored in the `RTP:2258-2308` `feat` build
(inference). The as-of discipline is the file's existing one — every prior-runs query must keep the
strict `race_date < %s` filter that `enrich_with_db` already uses (`RTP:1040-1041`).

**Pseudocode / algorithm sketch.** (a) `career_starts = len(prior_runs)`; `has_prior_form = int(
career_starts > 0)`. A first starter therefore gets `0` and a **distinguishable** indicator, instead
of the same zero vector as a fully-formed veteran. (b) Glicko: build ratings **only from races
strictly before the target date** — the engine's `update_ratings` (`:218`) is chronological, so the
training-side build must replay history in order and snapshot per race date, which is the expensive
part of this sub-item and the reason it may be dropped. (c) Weight: `weight_rel = weight_kg −
field_mean_weight`, and `weight_vs_wfa = weight_kg − wfa_scale(age, sex, distance, month)` — **only
if a WFA table exists in the repo; it does not, so this sub-item ships as `weight_rel` alone** and
the WFA half is deferred rather than approximated. (d) Shrinkage as above with `k` chosen by
method-of-moments on the between-jockey variance; `k = 50` is a defensible default to start.
Edge cases: `starts = 0` ⇒ return the population rate, never `0.0`, which would say "never wins";
an unrated horse ⇒ engine defaults plus the indicator; `sd = 0` in the weight field ⇒ `weight_rel`
is 0 for everyone, which is truthful.

**Config / feature flag.** `STRIDE_MISSINGNESS_FEATURES`, default `false`, gating all four sub-items
together (they are evaluated as a block). Add to `.env.example`.

**Acceptance criteria.** `run_ablation` **per sub-item on identical folds** — each is independently
droppable — plus the block as a whole. Sample: the same ~0.01 AUC resolution limit as T23, which
means **most of these will be individually indistinguishable from noise**. **Pre-registered
threshold: the block's ablation delta ≥ +0.005 AUC, or it is dropped.** Being honest about the prior:
this is the lowest-expected-value non-aspirational ticket in the plan, and it is here because it is
cheap and because the shrinkage half is methodologically right regardless of whether it shows up in
AUC. Self-test: `shrink_strike_rate(0, 0, 0.097) == 0.097`; `shrink_strike_rate(10, 10, 0.097)` lies
strictly between 0.097 and 1.0; `compute_missingness_features(empty)` returns the documented zeros.

**Rollback plan.** `STRIDE_MISSINGNESS_FEATURES=false`; remove the names from both `FEATURE_COLUMNS`
lists; delete the staged artifact. `glicko2_elo.py` is only *called*, never modified, so it retains
its zero-importer status if the ticket is reverted — check that with a grep as part of the rollback.

**Conflicts checked.** Adding a Glicko call makes `glicko2_elo.py` a production dependency for the
first time; its `save`/`load` (`:333`, `:353`) write a JSON artifact that would become a new
operational asset — decide where it lives (`models/` is git-ignored) before shipping. The
training-side Glicko replay is a leakage surface: it must honour the 14-day purge gap and must **not**
improve AUC when the gap is widened. Six barrier-related features are already dead (three constant,
one distance-only, one an interaction with a dead parent) — this bundle does **not** attempt to fix
them; that is T26's territory. T15 and T23 grow the same two lists in the same retrain cycle.

---

## T26. Training-side, as-of-safe computation of the dead columns   `[HIT-RATE]` · `[structural]`

**What to build.** Compute the pace and market-velocity columns **on the training side**, as-of-safe,
so that features currently constant-zero in training become real. This is the largest engineering
item in the plan that is not aspirational, and **its scope is set by a measurement, not by this
document.**

**The correction that changes the ticket's shape and must be carried:** the original *"41 features
identically zero in BOTH training and inference"* headline is **wrong on the inference half**.
`mc_api` builds a *different* feature dict via `extract_all_sophisticated_features`
(`mc_api.py:5436`) and feeds it to `RacingMLModel.predict_adjustment` through
`calculate_ml_probability_adjustment` (`mc_api.py:6448-6465`) — the **0.55-weighted** `ml_adjustment`
term at `mc_api.py:7379` — and that path **does** populate several of them (`running_style_score`,
`is_steam_move`, `empirical_barrier_advantage`). What survives is the half that matters: the
training-side claim is independently re-verified — each spot-checked name has **0 occurrences in
`form_feature_builder.py`** and exactly one in `retrain_v2.py` (the `FEATURE_COLUMNS` listing
itself), so they are `np.nan` and then zero-filled. **A column constant in training receives no
split, so its serve-time value is inert regardless of path.** Hence: *do not let this ticket
duplicate `mc_api`'s extractors.*

Why nobody noticed is worth recording as a process finding: the retrain's coverage print reports
non-zero counts for **exactly six structural columns plus the 11 Phase-2 features**, and
`--coverage-audit` iterates only `_ALL_PHASE4_FEATURES` — *"the evidence would never appear in a
retrain log."* Cost framing, stated honestly: zero-variance columns are not directly harmful to a
GBM, *"so the cost is not accuracy — it is that the documented feature inventory is fiction"*: the
README and `docs/04` advertise 110 engineered features, the realised count is **~72 in training**, and
the entire market-microstructure story in `docs/04 §2` is inert — which also means
`research/report.md:125` scores STRIDE *"Partially aligned"* on market steam/drift **on the strength
of features that are constant zero.** Fix the documentation in the same PR whatever the code outcome.

**Where it lives.** `server/python/form_feature_builder.py` — where the as-of-safe producers belong.
`server/python/retrain_v2.py`'s `_compute_pace_features` (the thin existing proxy) and
`build_feature_matrix`. **Read-only references, called by `mc_api` for its own adjustment layer and
not to be duplicated:** `server/python/pace_modeling.py`, `server/python/speed_mapping.py`,
`server/python/market_velocity.py`, `server/python/market_analysis.py`. Leakage discipline: the
LATERAL as-of join at `refresh_training_view_v2.py:252-270` (`AND st.race_date < r.race_date … LIMIT
1`, strict `<`) is the pattern to copy.

**Interface contract.**

```python
def compute_pace_features_asof(conn, horse_name: str, race_date, distance_m: int) -> Dict[str, float]:
    """Run-style / pace-pressure primitives from races strictly before race_date.
    No prior data -> every key np.nan (not 0.0), plus 'has_pace_history': 0."""

def compute_market_velocity_features_asof(conn, horse_name: str, race_date) -> Dict[str, float]:
    """Prior-race price-movement primitives, same as-of contract, same NaN convention."""
```

Both are called from `batch_compute_form_features` (`form_feature_builder.py:904`), whose output the
view already merges into `_form_features` and which `build_feature_matrix` applies via
`out.update(form_df)`. **That existing merge point is the whole integration** — no new plumbing.

**Pseudocode / algorithm sketch.** (1) For each (horse, race_date), select prior runs with a strict
`race_date < %s` filter and `LIMIT` by recency. (2) Derive the pace primitives from the same
definitions `pace_modeling.py` uses at inference — **read them, restate them in the as-of query, do
not import the inference engine into the training path**, because those engines take a live race dict,
not a history. (3) Market-velocity primitives require *prior* price movement, which means the view
must carry a historical odds series; **if it does not, this half of the ticket is not implementable
and must be closed rather than approximated** — establishing that is the first task. (4) Emit
`has_pace_history` / `has_market_velocity_history` indicators alongside, following
`has_sectional_data`'s template. Edge cases: first starters ⇒ all NaN plus a 0 indicator (T25's
pattern); a horse whose only prior runs fall inside the purge gap ⇒ still valid, because the purge
gap applies between train and test windows, not to as-of feature construction — **but say so
explicitly, because conflating the two is the most likely way this ticket leaks.**

**Config / feature flag.** `STRIDE_TRAINING_PACE_FEATURES`, default `false`. Off ⇒
`batch_compute_form_features` returns exactly today's keys and the columns stay zero-filled.
Add to `.env.example`.

**Acceptance criteria.** **Precondition, non-negotiable: re-run the feature diff over BOTH inference
paths first** (`RTP:2258-2319` **and** `mc_api.extract_all_sophisticated_features`) — that is T3
counter 6 — **so the ticket is scoped to columns that are genuinely dead rather than merely dead on
one path.** Then: per-block ablation (pace block, market-velocity block, freshness block) on identical
folds, **and a leakage check**: the 14-day purge gap in `retrain_v2.DateWindowSplitter` must be
honoured, and **the block must not improve AUC when the purge gap is widened** — if it does, the
as-of discipline has slipped. **Pre-registered threshold: each block ships only if its ablation delta
≥ +0.005 AUC and the widened-gap check is clean.** Given the effort, **do not start this before T3
counter 6 has run**; it may substantially shrink the scope.

**Rollback plan.** `STRIDE_TRAINING_PACE_FEATURES=false`; the producers remain but return today's
keys. Delete the staged artifact and any regenerated `training_view_v2` refresh — note that
`refresh_training_view_v2.py` rebuilds a materialised view, so a rollback that touched the view
requires re-running the refresh at the previous definition. **Do not change the view definition in
the same PR as the feature producers** — separate them so the rollback is one step, not two.

**Conflicts checked.** Largest surface in the plan and a real leakage risk if the as-of discipline
slips. Must not duplicate `mc_api`'s extractors (guardrail 3) — the two paths would then drift
exactly as the five interaction features did (T11). `retrain_v2._compute_pace_features` is the
existing thin proxy and must be **extended or explicitly superseded**, not shadowed by a second
producer. `form_feature_builder.batch_compute_form_features` is on the nightly `learn_from_results_v2`
path, so a slow query here lengthens a PID-locked job — profile it. Constraint 25 forbids using
Tier-2 modules (barrier-bias tables, `sectional_quant` engines, track profiler) to backfill training
rows; the producers here must be Tier-1 as-of patterns only. The documentation correction (110
advertised vs ~72 realised) should land even if the code half is closed.

---

## 4. Backlog — aspirational items and what would unblock them

Each is blocked on data or infrastructure that does not exist, so a ticket for any of them would be a
procurement or platform decision wearing an engineering costume. Stated with its blocker and its
unblocking condition, so that when the blocker clears the item can be written as a ticket without
re-deriving anything.

**B1 — A4. Final-stage logarithmic opinion pool replacing the `mw` ladder.** `[BOTH]`
The endorsed operator is already in the repo: `conditional_logit.py`'s
`P_i ∝ exp(α·ln m_i + β·ln q_i)` **is** a two-expert logarithmic pool, and `transform_race`
(`:82-93`) already implements it. STRIDE has **four** linear pools (`ml_model.py:594`, `RTP:668`,
`RTP:692`, `mc_api.py:7393`), **none** recognised in `docs/05 §5` as part of the calibration stack,
and **no recalibration downstream of any of them**; Ranjan & Gneiting 2010 JRSS-B 72(1):71-91
(**confirmed** — the strongest calibration citation available) says any non-trivial weighted average
of distinct calibrated forecasts is necessarily uncalibrated and under-confident. `mc_api` already
blends multiplicatively — in log space — *while the wrapper blends linearly at the point where money
is decided*, so **the pipeline contradicts itself** and nothing records it as a decision.
*Three blockers:* (i) constraint 14 — *"Do not flip `STRIDE_CL_BLEND` on this artifact"*, because the
fit consumed `training_view_v2.predicted_win_prob`, overwhelmingly imported predictions of unknown
generating stage; (ii) the existing hook at `RTP:591` **replaces the isotonic step**, whereas the
natural home is replacing the **`mw` ladder at `:692`** — a different slot needing a `--stage final`
artifact; (iii) `prediction_audit` holds **260 rows**, so there is nothing to fit on. Also: Benter's
pseudo-R² triple (0.1218 / 0.1245 / 0.1396), *the entire quantitative case for the two-stage
architecture*, is **`[unverified]`** and its "triple-sourcing" is circular.
*Unblocking condition:* **P0-c answered, several weeks of genuine `--stage final` rows in
`prediction_audit.final_win_prob`, and the β-sign refit** (the one-argument precondition in §0).
*Measurement when unblocked:* holdout log loss and top-1 hit rate for model-only vs market-only vs
blend — the three-way table `conditional_logit.py --fit` already prints — on ≥ 1,000 races, against
the α = 1.296 / β = 0.000 baseline.

**B2 — X1. T−5-minute odds snapshot as a feature.** `[BOTH]`
Late money is smart money: horses shortening in the final five minutes earn significantly higher
returns *at identical final odds* (JRA, 894,127 runners 2004–2023, coefficient −0.3386, SE 0.0392),
and AU 2006 across all 14,854 races shows late pool-share predicts net returns (coefficient 4.124,
z = 9.79) while final prices remain wrong in 5 of 10 favourite ranks. Improvement toward the close is
**non-monotonic**, so *"the exploitable window is just before the close, on the direction of the move,
not at the close itself."*
*Blocker:* prospective collection infrastructure that does not exist, bound by constraints 23/24 —
**prospective only, assert snapshot time < jump time, never backfill** a vendor's final-odds field
into historical training rows.
*Unblocking condition:* a scheduled collector writing timestamped price snapshots with an asserted
`snapshot_time < jump_time`, running for long enough to build a training population. There is **no
scheduled GitHub Action in the repo** — all seven non-CI workflows are `workflow_dispatch`-only — so
this is a platform decision first.
*The nuance that prevents wrong sequencing:* **for CLV measurement no new collection is needed — SP
is already stored. Only late-odds-as-a-feature needs the new infrastructure. Do not let the harder
half block the easy half.** That is why T2 is in wave 1 and this is in the backlog.

**B3 — X2. Sectional coverage above ~47%.** `[HIT-RATE]`
STRIDE's as-of sectional join is *"a genuine strength"* and verified leak-free, but it uses `LIMIT 1`
(`RTP:1043-1044`) — **a single prior race's z-score**, a high-variance estimator of a noisy quantity —
at ~47% coverage.
*Two sub-items are cheaper than the purchase and should be split out now:* **(a) a recency-weighted
average over the last N sectional observations instead of `LIMIT 1`** — the repo already knows this
pattern (`avg_market_diff_3runs`, `speed_rating_trajectory`), it just was not applied here, and this
is implementable today as a T26-adjacent change; **(b) T14, which matters more than either, because
production currently receives none of this block at all.**
*Blocker:* Punting Form is a **purchase** (~85% AU TAB coverage, history to Oct 2012, QLD coverage
unverified), and QLD is an **access** decision — constraint 20: *"the above-board path is official RQ
industry data access … not an escalating challenge-bypass on production infrastructure."*
*Unblocking condition:* a signed data agreement, or RQ industry access granted.

**B4 — X3. Exploded rank-ordered target / RUMBoost-class within-race fitting.** `[HIT-RATE]`
Bolton & Chapman used the **rank-ordered ("exploded") choice set** procedure, decomposing each race's
full finishing order into *d* independent choice sets — venue, the 200-race sample and the
*"side constraint eliminating long-shot betting"* are all confirmed. The argument is label starvation:
with ~8,995 races and one positive each, the pointwise trainer sees ~9k informative events, where an
exploded target yields `(field_size − 1)` nested comparisons per race — *"an order of magnitude more
signal from identical data."* `race_results_history` holds the full finishing order (45,070 rows
backfilled); **the view and the target do not.** RUMBoost (GBDT inside the utility function) is the
fallback, **not** the first try — T13 reaches the same mathematical form with an existing dependency.
*Blocker:* a rebuilt training view emitting finishing order, plus a target change that is squarely a
research project.
*Unblocking condition:* `refresh_training_view_v2.py` emitting `position` for the full field with the
same as-of discipline, and T13's QuerySoftMax arm having first shown that a listwise objective helps
at all on this data.

**B5 — X4. Drawdown-constrained or distributionally-robust Kelly with a daily budget.** `[ROI]`
The convex forms need a solver (`cvxpy` is not among the 32 deps in `requirements.txt`) and bankroll
history (T8). Sun & Boyd 2018 is **confirmed** but its headline *"±15% ⇒ worst-case growth >1.5×"* is
**`[unverified]`** — **do not size a ticket on it.**
*The part that is pressing and is NOT aspirational is already folded into T7:* **no daily exposure
control exists on the live path**, and the two off-path caps are mutually contradictory —
`STAKING_CONFIG['max_daily_units'] = 30` at `unit_percent = 0.01` means **30% of bankroll per day**
(`RS:131-132`), while `portfolio_risk.py:61` sets `max_daily_exposure_pct = 15.0`. **One says 30%,
the other 15%, neither runs.** For ~10–20 value-band bets at `f* ≈ 1.19%`, the quarter-Kelly *total*
is ≈3–6% of bankroll. **Resolving the 30%-vs-15% contradiction is a documentation/decision task, not
code, and it belongs in T7.**
*Blocker for the rest:* bankroll history (T8), a solver dependency, and the standing prohibition on
enabling Kelly at all until T1/T2/T5 have run.

---

## 5. Standing prohibitions — carried into every ticket

Not a ranking and not optional. These are the traps Phase 3 §4 identified, restated so that a ticket
author cannot reach the end of this document without them.

1. **Do not loosen a filter to raise hit rate.** The marginal bet added by a loosening is by
   construction the worst in the set. Loosening at the short end raises strike rate and lowers ROI
   (the 33.7% / −4.2% corner); loosening at the long end lowers **both**, and longshot returns are
   *unmeasurable* at realistic n (variance scales with `o²`). Constraint 27 forbids the long end
   independently.
2. **Do not chase the +12.3%.** 142 bets, 14 wins, **t = 0.432**, 95% CI **[−43.5%, +68.2%]**; it
   flips sign on two horses out of 352 races; it is the best of six strategies and its t is *below*
   the expected maximum from six coin flips (1.265). **No document should quote it again without its
   CI.**
3. **Do not stack another calibrator.** STRIDE has six calibration layers and one that can fire. The
   remedy is **one** correctly-placed, cross-fitted calibrator (T18), not a seventh.
4. **Do not enable Kelly.** Not at quarter, not at any fraction, until T1/T2/T5 have run. Note the
   current position is already aggressive: `2u = 2%` of bank is **1.68× full Kelly** on the value band
   gross, and **~5× at 8% Betfair MBR, ~9.8% at 10%** — past the zero-growth point at `2f*`.
5. **Do not backfill anything to fix the SP-vs-racecard defect.** The defect **is** already-shipped
   backfill; more of it is not a fix (constraints 24, 25).
6. **Do not add a model class and do not replace the GBMs with a neural network.** Model class has
   never been the differentiator; race-relative formulation has. Tree-based models remain state of
   the art at ~10K samples.
7. **Do not wire `market_efficiency.py` or resurrect `portfolio_risk.py` on plausibility.** Both are
   modules of exactly the right *shape* with zero importers, which is precisely the temptation.
   `portfolio_risk.py:235` computes variance at the **market-implied** probability while using the
   model probability for EV at `:234`.
8. **Do not wire the LambdaRank ranker, and do not read feature importance as evidence.**
9. **Do not flip `STRIDE_CL_BLEND` on the current artifact** — the reason is provenance, not design.
10. **Do not use the `[unverified]` numbers to size anything**: Benter's R² triple, Benter's
    "500–1000 races", Sun & Boyd's ±15%/>1.5×, MacLean's 1-in-213, Štrumbelj's market-size claim,
    Lessmann's RF-vs-CL ROI pair, the Kelly↔Bregman quotation, Bolton & Chapman's track split, the
    MBL secondary conditions, and **the Walsh & Joshi magnitudes, which are withdrawn pending
    re-fetch** — the direction survives, the magnitudes do not.
11. **Do not relax the one-bet-per-race contract to chase within-race Kelly.** Record it as a known
    bound; do not build it.
12. **Do not attempt to bypass the QLD Cloudflare challenge.**
13. **Do not conclude "we need more data."** STRIDE has ~8,995 races and 119,577 view rows against
    published, holdout-validated, profitable models built on 200 to ~2,000 races, and its evaluation
    discipline is ahead of most published work. The one genuine data gap is *a different kind* of
    data (B2, B3) — and CLV, the highest-value measurement, needs **none**.
14. **Do not tidy the conventions.** The two JSON key conventions are **two different contracts**;
    Australian and US spellings coexist deliberately — match the file you are editing; generations are
    suffixed, never renamed in place. An unseen TypeScript frontend consumes `mc_api`'s stdout JSON
    and the `selections` table, so a "cleanup" is an unversioned API break.

---

## 6. Cross-cutting delivery notes

**Every ticket adds its flag to `.env.example`.** Six live flags are missing from it today
(`STRIDE_CL_BLEND`, `STRIDE_PREDICTABILITY_GATE`, `STRIDE_ACCURACY_WEIGHTS`,
`MC_ENABLE_SECTIONAL_FRANKING`, `MC_ENABLE_JOCKEY_EFFICIENCY`, `LLM_MODEL`) while
`CONSENSUS_CONFIRM_THRESHOLD=45` is in the template with zero code references. Fix both in the first
PR that touches the template, or every new flag will be equally invisible.

**Every ticket that adds a testable pure function adds a `_self_test()` and wires it into
`.github/workflows/ci.yml:33-42`**, which currently runs eight module self-tests and is the entire
automated gate. There is no pytest and none should be introduced (guardrail 5). Modules that must be
importable by CI without the heavy stack — CI installs only `numpy scipy pandas scikit-learn
lightgbm` — must keep their DB and ML imports lazy, following `tips_day_aggregates.py`'s stated
"stdlib-only by design" and `audit_coverage_report.py`'s `import psycopg2` inside the `--run` branch.

**Every A/B must fix the MC seed or average over many runs.** The daily seed is
`int(time.time()) % 100000` (`RTP:2340`) while every backtest uses 42, so daily runs are not
reproducible. The existing proof-run idiom is `--skip-db-store` with `--output-suffix`.

**Only one flag that moves the published probability may be on in any single A/B.** T14, T16, T17,
T18, T19, T20 and T24 all move it, by different mechanisms; running two together forfeits
attribution. This is the single most likely way to waste an evidence cycle.

**Documentation corrections owed alongside the code** (all from the Phase-1 drift register):
`README.md`'s front-page architecture describes the **dormant V2** design (D-1); `docs/11:150` is
stale on the tipster feedback loop, which **is** closed and opt-in (D-3); the `selections` INSERT is
**115** columns, not "~107" (D-5); `docs/05 §5`'s five-layer calibration table over-states what can
fire and omits the CL blend (D-6); `retrain_v2.py:151`'s comment still says "(77 total)" above a
113-entry list (D-7); module and line counts are stale in several places (D-9). None of these changes
behaviour; all of them mislead the next change author.

---

*End of Phase 4 deliverable. 26 tickets in four waves with an explicit dependency graph, a 26 × 7
guardrail compliance matrix, and 5 backlog items with their blockers and unblocking conditions.
No `.py` or `.sql` file was modified — this document is the only file written.*

---

## Guardrail & completeness audit

**Run 2026-07-25 by a seventh agent whose brief was adversarial: assume the four documents contain
violations and gaps, and go find them.** Method: read all four deliverables in full; extract and
`ls`-check every file path cited in any of them; re-read from source the functions, classes and
constants that ≥ 8 tickets say they will extend; grep the repo for an existing module doing what each
ticket proposes to build (guardrail 3); confirm the named config mechanism exists (guardrail 2); check
lever tags against `SYSTEM_MAP §3`'s taxonomy; cross-check every high-ranked item against the
Phase-2 citation audit's `[unverified]` list; and check scope with `git status`. **No `.py`, `.sql`,
`.yml` or any other non-markdown file was read-modified. Only the four documents in
`docs/analysis/` were edited.**

### 1. Scope discipline — clean

`git status --short` returns exactly one line, `?? docs/analysis/`. `git diff --stat` and
`git diff --cached --stat` are both **empty**: no tracked file was modified or staged, in this phase
or any earlier one. `HEAD` is `2763ea1`, unchanged. The entire four-phase run produced four new
untracked markdown files under `docs/analysis/` and nothing else. **Guardrail 8 (no production code
in this run) holds without exception.**

### 2. Violations found and fixed

| # | Where | Violation | Class | Fixed? |
|---|---|---|---|---|
| V1 | `SYSTEM_MAP §2` step 7 | `is_winner` target cited at `retrain_v2.py:219`; `:219` is `"svi",` inside `FEATURE_COLUMNS`. The Phase-2 citation audit (§4 item 2) flagged this explicitly — *"SYSTEM_MAP is wrong too"* — and it was never carried back. Correct anchors: SQL `:299`, filter `:336`, `y` assignment `:1433`. | bad anchor, propagated | **Yes** |
| V2 | `SYSTEM_MAP §2` (×2) | *"deliberately not applied"* cited at `ml_model.py:566`; the comment is at `:565` (`:566` is the following `if`). Audit §5 already ruled for `:565`. | bad anchor | **Yes** |
| V3 | `SYSTEM_MAP §4` | *"`server/python/` holds 122 modules … `research/` (12 diagnostics)"*. Actual: **119** top-level modules; `research/` holds **11 `.py` files** — two standalone diagnostics plus a 9-module `winner_pattern_gap/` package. Load-bearing because T3 places a new module there. | wrong count | **Yes** |
| V4 | `SYSTEM_MAP §6.1` | `mc_selection_score` weights cited `~1925-1932`; actual `1927-1933`. | bad anchor | **Yes** |
| V5 | **T5** "Where it lives" + "Conflicts checked" | *"reuse the PAV at `mc_recalibration.py:157` … its PAV is imported, not copied."* `_pool_adjacent_violators` is a **bound method of `MCRecalibrator`** (class at `:21`, `__init__` at `:31`), not a module-level function — **it cannot be imported as written, so the ticket is not buildable from its own spec.** | **interface contract — blocking** | **Yes** — two lawful routes given (call on a throwaway instance after confirming `__init__` is DB-free; or promote to module scope with the method delegating, which is additive). Copying remains forbidden. |
| V6 | **T19** header + acceptance | Tagged `[ROI]` while its own headline argument is that a smooth family stops isotonic tripping the flat-MC breaker — a **selection** mechanism. Verified in source: calibrator applies at `RTP:657-661` → `rawModelProb` at `:670` → `mc_spread` at `:703-705` → MC-spine re-weight, gradient penalty, and the LLM boost whose top pick **bypasses every safety filter** (`RTP:883`). Compounded by an acceptance criterion demanding *"top-1 ordering provably unchanged on 100% of stored races"* — **which contradicts the ticket's own benefit and could never have passed.** | **lever integrity + internal contradiction** | **Yes** — re-tagged `[BOTH]`; the single ordering criterion split into a pointwise unit test and an end-to-end selection metric graded under the ranking clause of the bar. |
| V7 | **T3** "Where it lives" | *"beside the twelve existing diagnostics in `server/python/research/`"* — see V3. Also the template it copies, `audit_coverage_report.py`, lives one level **up**, in `server/python/`. | wrong count / placement | **Yes** |
| V8 | **T3** CI wiring | *"`python server/python/research/live_diagnostics.py` … add it to `ci.yml`'s self-test list."* That step does `cd server/python` first (`ci.yml:34-35`) and then calls bare module names, so a repo-root path would not run. | convention lock (G5) | **Yes** — corrected to `python research/live_diagnostics.py`. |
| V9 | **T3** acceptance | Three pre-registered alarm thresholds (±2pp, 15%, 2%), each of which **promotes another ticket**, carried **no minimum sample**. An alarm with no `n` is not a pre-registration, and T7 forbids exactly this everywhere else. | **completeness — missing sample size** | **Yes** — floors added (≥ 500 races; ≥ 1,000 for the 2% rate; ≥ 150 per field-size band), with binomial half-widths shown. |
| V10 | **T17** acceptance | Same defect: the open/close decision (*"median race sums within ±2pp"*) had no sample floor, and T17 consumes the T3 counter that now has one. | completeness — missing sample size | **Yes** — ≥ 500 races, aligned with T3 counter 2. |
| V11 | **T22** config section | Cited `RS:3163` as a `kelly_stake` caller; `:3163` is `base_unit = args.bankroll * STAKING_CONFIG['unit_percent']`. Real call sites: `RS:326`, `:2006`, `:2500`. | bad anchor | **Yes** — corrected; the substantive claim (none reachable from the daily pipeline) re-verified independently: `run_tips_pipeline.py` contains **0** occurrences of `bankroll`. |
| V12 | Matrix row T13 | Guardrail-2 cell read *"none needed — evidence-only"*. Guardrail 2 is non-negotiable, so an exception must be argued, not assumed. | governance | **Yes** — recorded as the plan's **single** guardrail-2 exception, with its justification, the mechanism that substitutes for the flag (`backend="lightgbm"` default + `--backend` CLI), a standing condition (*if T13 acquires an importer it acquires a flag in the same change*), and a bar on citing it as precedent. |
| V13 | Cross-document | The same new module is called `fit_isotonic_calibrator.py` in `IMPROVEMENT_REPORT` (4 sites) and `ACADEMIC_FINDINGS` (1 site) but `fit_calibrator.py` in the plan — and #15 contained the self-contradicting sentence *"`fit_isotonic_calibrator.py` becomes `fit_calibrator.py`"*. Two names for one file is a guardrail-3 hazard at the moment of building. | one source of truth (G3) | **Yes** — unified on `fit_calibrator.py`, the plan's name, which is the one that generalises to T19's family swap. |
| V14 | `IMPROVEMENT_REPORT §0.2(c)` | *"Temperature scaling … satisfies the hit-rate clause of the promotion bar by construction"* — true pointwise, false end-to-end, for the V6 reason. Left unqualified it would have licensed a default-on promotion without a ranking test. | lever integrity | **Yes** — qualified; the power de-vig (C1) is confirmed genuinely neutral because it acts on `true_market`, which the flat detector never reads. |

### 3. Checks run that found **no** violation

Recording these matters as much as the failures, because each was a live suspicion.

- **File-path reality.** Every path cited across all four documents was extracted by regex and
  `ls`-checked. **Zero dangling paths.** All eight workflows exist (`ci.yml`, `audit-coverage.yml`,
  `retrain-model.yml`, `fit-conditional-logit.yml`, `train-rank-model.yml`, `backfill-results.yml`,
  `audit-smoke.yml`, `net-probe.yml`), all five migrations exist including
  `prediction_audit_unique_key.sql` and `final_prob_audit.sql`, and every one of the ~60 modules named
  in a ticket exists at the path given. The only non-existent paths are the **six new files the plan
  explicitly creates** — `server/python/fit_calibrator.py`, `server/python/feature_interactions.py`,
  `server/python/research/live_diagnostics.py`, `migrations/shadow_ledger_v2.sql`,
  `migrations/shadow_bankroll.sql`, `models/staging/isotonic_calibrator.meta.json` — each labelled
  "New" at its site. **No ticket points at a nonexistent module.**
- **Interface contract reality — 11 tickets spot-checked against source, not 5.** Confirmed exactly:
  `shadow_pl_tracker` `cmd_record:132` / `cmd_results:232` / `cmd_report:325` / `MIGRATION_SQL:50` /
  `MIN_BETS_REPORTABLE = 200` at `:323` / `BET_TIERS:130` (T1, T2, T7, T8);
  `walk_forward_backtest` `compute_ece:100` / `compute_fold_metrics:120` / `aggregate_metrics:202`
  (T4, T5); `ProbabilityCalibrator.__init__/fit/calibrate/load` at `:22/:26/:40/:48` (T6, T19);
  `rank_model.train_ranker(X, y, group_sizes, params=None):75` and
  `walk_forward_report(races, params=None, verbose=True):112` (T13);
  `RS.kelly_stake(prob, odds, fraction, max_stake):309` and `simulate_race_monte_carlo:1781` (T10,
  T22); `ml_model.prepare_features:208` / `predict_proba:544` (T14, T18);
  `relative_market.compute_field_relative_market:38` / `add_relative_market_features:78` /
  `_is_valid_odds:70` (T15, T23); `glicko2_elo.Glicko2Engine.get_rating:159` / `update_ratings:218` /
  `save:333` / `load:353` (T25); `form_feature_builder.compute_single_horse_features:29` /
  `batch_compute_form_features:904` (T25, T26); `market_efficiency.METRO_TRACKS:7` (T12); and in
  `RTP`, all of `calculate_overround:432`, `extract_odds:445`, `calibrate_and_score:568`,
  `apply_safety_filters:802`, `compute_confidence:950`, `compute_staking:1007`, `enrich_with_db:1018`,
  `store_selections_in_db:1245`, `store_final_probs_in_audit:1582`, `filter_active_runners:1635`,
  `annotate_pick_contract:1712`, `_check_intelligence_override:1727`, `evaluate_bet_candidate:1778`,
  `choose_coverage_race_pick:1871`, `choose_bet_race_pick:2000`, `run_tips:2035`.
  **Every signature the plan says it will extend exists with that signature.** V5 is the sole
  exception and it is a *shape* error (method vs function), not a missing symbol.
- **Guardrail 3 — verified by grep, not by assertion.** For each ticket that builds something new:
  no calibrator-fitting script exists (`ProbabilityCalibrator` has exactly two references outside its
  own file — the loader at `RTP:575-576` and a comment at `ml_model.py:565`; **nothing produces
  `models/isotonic_calibrator.pkl`**); no CLV computation exists; no alternative de-vig exists
  (`shin` / `power` / `devig` return no functional match); `bankroll` appears **0** times in `RTP`.
  T11's premise re-verified in full: `fitness_x_distance` / `barrier_x_pace_inv` are implemented
  **twice** — `retrain_v2.py:654-664` and `RTP:2308-2310` — with `ml_model.py:153-154` being only
  `FEATURE_COLUMNS` entries, and the claimed drift is real and verbatim: training computes
  `barrier_advantage.fillna(0) * _pps`, inference computes `barrier_advantage * (1 - _pps)`.
  **Every "extend, don't duplicate" claim in the matrix is true.**
- **Guardrail 2 — the named mechanism exists.** `SYSTEM_MAP §4`'s claim re-verified: no `config.py` /
  `settings.py` / `constants.py` for production; the idiom is a `STRIDE_*` env var read at call time,
  `STRIDE_CL_BLEND` at `RTP:591` is the precedent, and `_env_flag` at `mc_api.py:44-48` is the only
  named helper. `.env.example` re-read in full and drift D-2 confirmed exactly: all six named flags
  absent, `CONSENSUS_CONFIRM_THRESHOLD=45` present with no code reference. **25 of 26 tickets carry a
  default-off flag; the 26th is V12's now-documented exception.**
- **Guardrail 7 — complete.** All **26** tickets carry a `Conflicts checked` section; all 26 carry
  `Rollback plan`, `Config / feature flag` and `Acceptance criteria`. Counted mechanically.
- **Guardrail 6 — one reorder, correctly discharged.** Only T18 moves a pipeline step. It states the
  expected impact, names the mechanism, gives a one-env-var rollback, and is the *only* form in which
  guardrail 12 permits the change (the isotonic re-enable must ship in the same commit). T20's
  ordering fix is correctly identified as *"a T16-scale re-pricing wearing the costume of a one-line
  reorder"* and shipped last behind its own flag. **No unjustified reordering anywhere.**
- **Guardrail 4 — schema safety holds.** Every column added is `ALTER TABLE … ADD COLUMN IF NOT
  EXISTS` through `shadow_pl_tracker.MIGRATION_SQL` (`:50-68`, the repo's own idiom, applied by
  `ensure_schema`) or additive on `selections`. No existing column, log line or JSON key is renamed or
  redefined. T4's key-rename risk is caught by the plan itself (keep the old key, add the new one
  beside it). The two JSON key conventions are explicitly protected.
- **Evidence integrity — clean, and better than expected.** Every item the citation audit marked
  `[unverified]` was traced to its rank. All five `W`-graded items sit at **#24, #25, #27, #30, #31** —
  the bottom of the list. **No item in the top 15 rests on an unverified claim**, and the three the
  audit named strongest (R5-F2, R3-F1/F2/F3+R4-F2, R2-F2) are the basis for T15, T4/T9 and T6
  respectively. Each ticket touching an unverified number carries the prohibition inline — T16 refuses
  the Štrumbelj argument, T18 carries **both** audit corrections including that arXiv 2606.29720 is
  evidence *for* the remedy, T22 excludes MacLean's 1-in-213 and Sun & Boyd's ±15% from sizing, T23
  bars Lessmann's ROI headlines, T24 and T25 bar their practitioner effect sizes. §5 item 10 lists all
  ten. **No mis-ranking found.**
- **Lever integrity elsewhere.** Every other tag checks out against `SYSTEM_MAP §3`. **No staking
  ticket claims a hit-rate effect**: T8, T12 and T22 are all `[ROI]`, and T22 is shadow-only by
  construction. T16 (`[ROI]`) and T17 (`[ROI]`) both correctly *predict hit rate will not move* and
  correctly treat any movement as a bug rather than a result. T21's `[BOTH]` survives scrutiny — it
  changes the bet population, not just the denominator.
- **Phase-3 → Phase-4 coverage — complete.** All 26 non-aspirational Phase-3 items (I1–I6, C1–C7,
  A1–A3, G1–G2, S1–S3, M1–M5) map 1:1 onto T1–T26, and all five aspirational items (A4, X1–X4) appear
  as B1–B5 with blockers and unblocking conditions. **No orphans in either direction.**

### 4. Remaining gaps — not fixed, recorded

1. **Nothing verified by execution, anywhere in the run.** Every claim in all four documents rests on
   source reading and grep. The repo has no DB, no models, no data and no test suite, so this is a
   property of the environment, not a failure of the work — but it means `SYSTEM_MAP §7b`'s thirteen
   "code defects" are *grep-inferred*, and T20 is right to make its first step a print rather than a
   fix. **Treat §7b.1–3 as hypotheses until a runtime print lands.**
2. **The audit could not verify the *numerical* claims in the Phase-2 literature either.** The
   citation audit resolved venue/volume/authors for 24 sources and confirmed 11 effect sizes; the
   other nine remain unseen. This audit added nothing there — egress is still blocked. The plan's
   handling (bar them from sizing) is the correct response, not a fix.
3. **`server/python/research/` placement for T3 is a judgement call, not a rule.** The template lives
   in `server/python/`; the ticket puts the new module in `research/`. Both are defensible and the
   plan now says so, but a reviewer may reasonably move it. Whichever is chosen, the `ci.yml`
   invocation must match (V8).
4. **No ticket covers the `luckless_analyser` / LLM pre-analysis stage (pipeline steps 3–4).** Steps
   1–2 and 5–15 are all analysed somewhere in the run; the ~90-keyword forgive score (capped 0.12) and
   the ±0.08 LLM mu adjustment are described in `SYSTEM_MAP §2` and then never revisited by Phase 2,
   3 or 4. The `[LLM]` post-scoring stage **is** covered (T21 touches its flat-MC interaction, and
   `SYSTEM_MAP §7b.6` proves the blend can only lower a score) — but *pre*-analysis is a genuine
   un-analysed stage. It is plausibly low-value, since both effects are hard-capped, but nobody
   established that. **This is the one pipeline stage with no coverage.**
5. **The banker bypass is un-audited and still bypasses everything.** `banker_score ≥ 70` passes a
   pick through **all** safety filters (`RTP:888`) and `SYSTEM_MAP §8` records that no pass verified
   the score's internal composition. T3 counts the *intelligence* override's firing rate (counter 8)
   but **no counter exists for the banker bypass**, and no ticket opens `banker_detector.py`. Adding
   it to T3's battery is a one-line extension of work already scoped. **Recommended.**
6. **The `CROWD_ONLY_WEAK & crowd_score ≥ 100` promotion still has no review.** The in-code comment
   says *"One data point — monitor for 4 weeks"* (`consensus_blender.py:250-255`, dated 2026-04-06);
   the window elapsed months ago. T1's ledger will finally make it reviewable, but **no ticket
   schedules the review**, and `consensus_blender.py:38-42` separately says the crowd thresholds
   *"should be reviewed monthly"*. That cadence exists nowhere in the plan.
7. **`MODEL_ONLY` — the highest-leverage unknown — is measured but never acted on.** T1 will report
   what the archetype-trap picks would have returned, which is right. But no ticket states in advance
   *what result would justify changing the rule*, so the plan can produce the number and still not
   move. **A pre-registered decision rule on the `MODEL_ONLY` P&L belongs in T1 or T7.**
8. **No ticket re-examines the `odds > 15` hard ceiling**, which `SYSTEM_MAP §9 Q8` calls the largest
   ROI constraint in the code with no supporting measurement. T4's ROI surface will show the cells,
   but the ceiling itself is never put on trial. This is defensible under standing prohibition 1
   (don't loosen at the long end) and constraint 27 — but the *asymmetry* should be explicit: the plan
   forbids loosening it and never proposes measuring whether it is too tight.
9. **Sample-size floors are now present on all 26 tickets, but three are stated as targets rather
   than gates** — T13's *"target ≥ 2,000 H2H races"*, T14's *"~2,000–2,500 races"* and T20's
   *"~2,000–2,500 races paired"*. Each is the right magnitude; none says what to do at n = 1,400.
   Minor, and the T7 reportability machinery covers it once built.
10. **`.env.example` is the single point of failure for guardrail 2 and no ticket owns it.** Every
    ticket says "add to `.env.example`"; the plan's §6 says fix the six missing flags "in the first PR
    that touches the template". **Nobody is assigned.** If the first PR forgets, all 26 flags inherit
    the invisibility that D-2 documents.

### 5. Verdict

The four documents are **materially sound**. The 14 violations are real and 13 of the 14 were
cosmetic-to-moderate — bad line anchors, wrong counts, one duplicated filename, two missing sample
floors. Only **two** would have caused actual damage if built as written: **V5**, which makes T5
unbuildable from its own spec (importing a bound method), and **V6**, a lever mis-tag that would have
let a change to the *selection* path be promoted on *calibration* evidence alone with a
self-contradicting acceptance test. Both are fixed.

What the audit could not shake is more informative than what it found. Every file path resolves.
Every signature the plan extends exists. Every "extend, don't duplicate" claim survived an independent
grep. Every unverified citation is quarantined at the bottom of the ranking with an inline
prohibition. All 26 tickets are flagged, rollback-able and conflict-checked, and the one unflagged
ticket had a real reason that is now written down. No production file was touched in four phases.

The remaining gaps are dominated by **one un-analysed pipeline stage (luckless/LLM pre-analysis)**,
**two un-instrumented bypasses (banker score ≥ 70, and the elapsed crowd-rule review)**, and **an
absence of pre-registered decision rules on the measurements the plan is about to collect** — the
plan is very good at specifying what to measure and comparatively weak at specifying what to *do*
when the number arrives. Fixing that costs a paragraph per ticket, not a phase.







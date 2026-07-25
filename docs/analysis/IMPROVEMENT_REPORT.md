# STRIDE — IMPROVEMENT REPORT (Phase 3 deliverable)

**Assembled 2026-07-25**, branch `claude/latest-repo-commit-4j5ksq`.
Inputs read in full: `docs/analysis/SYSTEM_MAP.md` (Phase 1), `docs/analysis/ACADEMIC_FINDINGS.md`
(Phase 2, including its **§ Citation audit**), `docs/12-hit-rate-research.md` (prior roadmap),
`research/report.md` (prior research pass). Output: a ranked, bucketed, measurable change list for
Phase 4 to turn into tickets.

**Nothing in this report was established by execution.** Every STRIDE claim is a source read.
Where I re-read a file myself this session it is marked **[re-verified here]**; otherwise the
Phase-1/Phase-2 anchor is carried with its own label. Literature confidence labels
(`fetched` / `[snippet-only]` / `[unverified]` / `[derived]`) are carried through unchanged, and
per the brief **no item whose sizing depends on an `[unverified]` claim is ranked high-confidence.**

`RTP` = `server/python/run_tips_pipeline.py`. `RS` = `racing_system_v8.3_mc.py`.

---

## 0. Framing

### 0.1 The ranking criterion (stated before the ranking, so it can be argued with)

Items are ranked by **expected value of the change**, defined as:

> `EV ≈ P(the diagnosis is correct) × |expected move on realised ROI or realised top-1 strike rate| ÷ (implementation effort + destabilisation risk)`

subject to three precedence rules, applied in order:

1. **Measurability precedence.** `SYSTEM_MAP.md §3` establishes that the unit which actually decides
   bets — `calibrate_and_score` + `apply_safety_filters` + `evaluate_bet_candidate` +
   `crowd_bet_decision` — **has no live hit-rate and no live realised-ROI measurement at all**, and
   that every threshold in `SYSTEM_MAP §6` is therefore "currently *unfalsifiable in production*".
   A behaviour change that cannot be scored has an EV of approximately zero however good its
   academic pedigree. Instrumentation therefore outranks behaviour, and three of five research
   streams reached the same sequencing independently (`ACADEMIC_FINDINGS §A6`).
2. **Evidence-weight discount.** The Phase-2 citation audit downgraded **11 claims to
   `[unverified]`** and found **2 sources misused**. Its own closing instruction is explicit:
   *"Everything that needs Benter's R², Sun & Boyd's 1.5×, or MacLean's 1-in-213 to justify its size
   should wait for a real fetch."* Items resting on those are capped at a middling rank regardless of
   how attractive they are.
3. **Guardrail feasibility.** Items colliding with the Phase-1 §5 guardrail list — particularly
   #1 (additive only), #2 (default-off feature flag), #3 (extend, never duplicate), #6 (no pipeline
   reordering without expected-impact numbers), #12 (never double-calibrate), #14 (`STRIDE_CL_BLEND`
   on hold), #15 (LambdaRank stays evidence-only), #36 (causal ablation, not importance) — carry a
   risk penalty proportional to how hard the collision is to discharge.

**Scoring axes.** Each item is scored 1–5 on four axes with a one-line justification in its
subsection.

| Axis | 1 | 5 | Direction |
|---|---|---|---|
| **ROI** — expected move in realised return on money actually staked | none / cannot move it | large, and the mechanism is identified | higher = better |
| **HIT** — expected move in the strike rate of `bet_pick` as shipped | none / cannot move it by construction | large, and via a genuinely more discriminative within-race ordering | higher = better |
| **Effort** — engineering time to a shippable, flagged, self-tested change | days | months, or new infrastructure | **lower = better** |
| **Risk** — probability the change degrades live behaviour before it is caught | inert by construction (publish-only / evidence-only) | touches the money path with no rollback | **lower = better** |

Instrumentation items score **ROI 1 / HIT 1** honestly — they move neither number directly. They
rank at the top anyway under rule 1. The table carries an `E` column (**enabling value**, 1–5) so
that this is visible rather than smuggled into the impact scores.

### 0.2 ROI and HIT RATE are different levers — stated precisely

This is the axis the brief asks for rigour on, and the repo's own documents blur it in four places.

**(a) Two different "hit rates" wear the same name.** The README's founding pair
(`README.md:109-121`, restated `docs/12:16-20`) contains *two different denominators*:

- **top-pick hit rate = 33.7%** — denominator is **races** (352). Moved by *ranking quality*.
- **bet hit rate = 9.9%** — denominator is **bets placed** (142). Moved by *gating*.

The promotion bar (`docs/12:435-438`) is written in terms of the first; the ROI evidence it names in
the same sentence ("the Value-Edge band's ROI") lives on the second. **A change can satisfy the bar
on one and be judged on the other.** Every ticket must name which denominator it moves. This report
uses **HIT** for the first and states explicitly where an item moves the second instead.

**(b) The three-lever taxonomy, with the exceptions the Phase-2 work found.**
`SYSTEM_MAP §3` sets out: *ranking quality* → hit rate, price-independent; *anchoring and gating* →
trades the two; *staking* → ROI only, hit rate untouched. That holds, with two corrections:

- **Staking is orthogonal to hit rate only while the staking rule cannot return zero.** STRIDE's
  can: `compute_staking` (`RTP:1007-1015`, **[re-verified here]**) is exactly
  `high→"2u"`, `medium→"1u"`, else `"0u"`, so the sizing function is silently also a *gate*
  (`ACADEMIC_FINDINGS R4-F17`, **[derived — source read]**). `mc_spread < 6.0` (`RTP:705`) forces all
  three picks to `low`, so an uninformative simulation zero-stakes the whole race. Any staking ticket
  must state whether `0u` rows are in the bet denominator.
- **ROI is turnover-weighted, so any non-flat staking rule re-weights it — and not in the intuitive
  direction.** Kelly weights by `EV/(o−1)`, concentrating turnover on short prices where percentage
  edge is smallest; R4-F14's worked example moves headline ROI from 10% to 7.3% *while increasing
  expected log-growth*. **Judged by the existing promotion bar, the correct staking answer fails.**

**(c) Monotone transforms are hit-rate-neutral by construction.** Two proposed changes cannot move
within-race order at all, which makes them unusually clean A/B primitives:
- a **power de-vig** `p_i ∝ (1/o_i)^k` is monotone in odds, so `odds_rank` is preserved (R3-F9);
- **temperature scaling** is a strictly monotone transform of the score, so "the classification
  accuracy of the model is not affected … it does not change the most-confident prediction"
  (Guo et al. 2017, `[snippet-only]`, R2-F12).
Both therefore satisfy the hit-rate clause of the promotion bar *by construction*, reducing the A/B
to a one-sided question about reliability and ROI.

**Qualification added by the Phase-4 audit — this holds for the de-vig and not for the calibrator.**
Monotone-transform neutrality is a statement about *within-race order given a fixed downstream
pipeline*. STRIDE's pipeline is not fixed: it **branches on the dispersion** of the transformed
vector at `RTP:703-705` (`mc_spread < 6.0`), and that branch changes the MC-spine weighting, the
gradient penalty, the stake schedule and the LLM's ranking authority. The power de-vig is safe
because it acts on `true_market`, which the flat detector never reads. **Temperature scaling is not**
— it acts on the MC arm at `RTP:657-661`, whose output is exactly what `mc_spread` is computed from.
So (c) applies in full to C1/#11 and only pointwise to A2/#15.

**(d) Loosening a filter is not a hit-rate lever — its sign depends on which end you loosen.**
Raising bet count moves the marginal bet, which is by definition the worst one in the set, so ROI
falls. The effect on *bet* hit rate depends on direction: loosening at the **short** end raises
strike rate and lowers ROI (33.7% / −4.2% is that corner); loosening at the **long** end lowers
**both**, and R3-F5's arithmetic adds that longshot returns are *unmeasurable* at realistic n
(variance scales with `o²`; winners averaging $11.4 need ~32× the sample of winners averaging $2.0).
Constraint 27 (`research/report.md:122`) forbids it independently. This is item 1 of §4.

### 0.3 Preconditions — P0, before any ticket is written

These are not improvements; they are facts nobody in three research passes could establish, each
costing an operator minutes, each gating a large fraction of the list. **Phase 4 should open with
them.**

| P0 | Question | Gates | Source |
|---|---|---|---|
| **P0-a** | Is the daily pipeline running and writing to the database behind `DATABASE_URL`? `docs/12:203-205` records that **every table stops at ~2026-04-18**. | Every measurement item (I1–I6). Without live rows there is nothing to score. | `docs/12 §4c` |
| **P0-b** | Does `models/isotonic_calibrator.pkl` exist on the production box? If not, the only calibration layer that can fire is silently skipped and the live chain is *ML blend → market anchor* with **no calibration at all**. | I5, I6, A1, A2, and the entire edge computation. | `SYSTEM_MAP §9 Q1`; nominated by R1, R2 and R4 as "the cheapest thing the operator could resolve" |
| **P0-c** | Has `migrations/prediction_audit_unique_key.sql` been applied, and is `prediction_audit` filling? | Any recalibration or CL refit; I5's reliability monitor. | `SYSTEM_MAP §9 Q13`; `docs/12:257-268` |
| **P0-d** | Which library versions unpickle the live ensemble? Only `numpy==1.26.4` is pinned; xgboost/lightgbm/catboost are unpinned against a pickled artifact. | Every retrain-gated item (M1–M5). | `SYSTEM_MAP §9 Q4` |

If P0-a is negative, the correct Phase-4 scope collapses to: restore collection, then I1–I6, and
nothing else.

---

## 1. Ranked list of improvements

**Ranked by expected value of the change** under §0.1 — not by how interesting the finding is, and
not by how much literature supports it. Effort and Risk are **lower-is-better**. `E` is enabling
value (what the item unlocks for other items), which is why the instrumentation block sits on top
despite scoring 1/1 on direct impact.

Evidence column: **S** = source-read or arithmetic on committed repo data (the strongest class in
Phase 2 — "none of those three depends on a single external citation"); **M** = literature confirmed
by the citation audit; **W** = rests wholly or partly on a claim the audit marked `[unverified]`.

| # | Item | Lever | ROI | HIT | Effort | Risk | E | Evid | Bucket |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **I1** Live wrapper outcome ledger — settle `bet_pick` at tipped price *and* SP, by `selection_origin` and crowd classification, including refused sets | enabling | 1 | 1 | 2 | 1 | **5** | S | Quick |
| 2 | **I2** CLV columns in `shadow_pl_tracker.cmd_report` (mean CLV, % positive CLV) | enabling | 1 | 1 | 1 | 1 | **5** | S | Quick |
| 3 | **I3** Read-only diagnostic battery (9 counters, one script) | enabling | 1 | 1 | 2 | 1 | **5** | S | Quick |
| 4 | **I4** Error bars, trial counts and an (odds × edge) ROI surface on the backtests | enabling | 1 | 1 | 1 | 1 | **5** | S | Quick |
| 5 | **I5** Calibration measurement upgrade — equal-mass + debiased ECE, PAV/CORP, Brier decomposition, slope/intercept, per-band reliability | enabling | 1 | 1 | 2 | 1 | **4** | M | Quick |
| 6 | **I6** Calibrator provenance — a fitting script, a sidecar metadata dict, a load-time positive assertion | enabling | 2 | 1 | 2 | 1 | **4** | S | Quick |
| 7 | **C2** Publish `edge_at_price` (vig-inclusive) and the Kelly sign test as shadow fields | ROI | 3 | 1 | 1 | 1 | 4 | S | Quick |
| 8 | **C4** Rao-Blackwellise the MC win probability (analytic softmax over draws) | both | 3 | 2 | 1 | 2 | 3 | S | Quick |
| 9 | **G1** Promotion-bar amendment + variance-aware `MIN_BETS_REPORTABLE` | enabling | 1 | 1 | 1 | 1 | 4 | S | Quick |
| 10 | **S1** Bankroll state + a stake column on `stride_tip_results` | enabling | 1 | 1 | 2 | 1 | 4 | S | Quick |
| 11 | **C1** De-vig method selector (`power`, Shin as a branch) on `calculate_overround` | ROI | **4** | 1 | 2 | 3 | 3 | M | Structural |
| 12 | **A1** Move the calibrator downstream of the MC↔ML blend **and** re-enable the per-model OOF isotonic, as one ticket | both | **4** | 2 | 3 | 4 | 3 | M | Structural |
| 13 | **C3** Restore NaN preservation at inference + supply the 8 sectional primitives | both | 3 | **4** | 2 | 3 | 2 | S | Structural |
| 14 | **C6** `odds_source` / `has_real_market_odds` as explicit indicators; diagnose the SP-vs-racecard split | both | 3 | 3 | 2 | 2 | 3 | S | Structural |
| 15 | **A2** Calibrator family swap — temperature scaling first, beta second | both † | 3 | 2 | 2 | 2 | 3 | M | Structural |
| 16 | **S3** Commission / venue parameter + segment tagging on every output row | ROI | 3 | 1 | 1 | 1 | 3 | M | Quick |
| 17 | **C7** One shared helper for the five interaction features | both | 1 | 2 | 1 | 2 | 2 | S | Quick |
| 18 | **C5** Repair the three inert context multipliers and the `rawModelProb` ordering defect | both | 2 | 3 | 2 | **4** | 2 | S | Structural |
| 19 | **M1** CatBoostRanker `QuerySoftMax` evidence arm in `rank_model.py` | HIT | 2 | **4** | 2 | 1 | 3 | M | Structural |
| 20 | **G2** Separate "should we bet" from "how much"; instrument the flat-MC breaker | both | 2 | 3 | 3 | 3 | 3 | S | Structural |
| 21 | **A3** Renormalise win probabilities within race after the market anchor | ROI | 3 | 1 | 2 | 3 | 2 | M | Structural |
| 22 | **M2** Race-relative *fundamentals* (`_z` / `_rank`), extending `relative_market.py` | HIT | 2 | **4** | 3 | 2 | 2 | M | Structural |
| 23 | **S2** Shadow price-aware stake column (`f* = EV/(o−1)`, quarter-Kelly, uncertainty-shrunk) — published, never applied | ROI | 3 | 1 | 2 | 1 | 3 | M | Structural |
| 24 | **M3** Field-size-aware sample weighting in place of a scalar `scale_pos_weight` | HIT | 2 | 3 | 2 | 2 | 2 | W | Structural |
| 25 | **M5** Cheap missing-indicator features: `career_starts`, `has_prior_form`, Glicko pair, WFA weight | HIT | 1 | 2 | 2 | 1 | 1 | W | Structural |
| 26 | **M4** Training-side, as-of-safe computation of the dead pace and market-velocity columns | HIT | 2 | 3 | **5** | 3 | 2 | S | Structural |
| 27 | **A4** Final-stage log pool replacing the `mw` ladder (CL at `RTP:692`, `--stage final`) | both | **4** | 3 | **5** | **5** | 2 | W | Aspirational |
| 28 | **X1** T−5-minute odds snapshot as a *feature* | both | 3 | 3 | **5** | 3 | 2 | M | Aspirational |
| 29 | **X2** Sectional coverage above ~47% (Punting Form purchase; QLD access) | HIT | 2 | 3 | **5** | 2 | 2 | M | Aspirational |
| 30 | **X3** Exploded rank-ordered target / RUMBoost-class within-race fitting | HIT | 2 | **4** | **5** | 4 | 1 | W | Aspirational |
| 31 | **X4** Drawdown-constrained or distributionally-robust Kelly with a daily budget | ROI | 3 | 1 | **5** | 4 | 1 | W | Aspirational |

**† Lever re-tagged by the Phase-4 audit (item #15, A2, was `ROI` / HIT 1).** A2's *pointwise* map is
rank-preserving, but A2's own headline argument is that a smooth family stops isotonic's step function
tripping the flat-MC breaker — and the breaker is a selection mechanism, not a pricing one. Verified
in source: the calibrator is applied at `RTP:657-661`, its output becomes `rawModelProb` at `:670`,
and `mc_spread` is computed from `rawModelProb` at `:703-705`; flipping the flat branch re-weights the
MC spine, applies the gradient penalty and hands the LLM a boost whose top pick bypasses every safety
filter (`RTP:883`). See T19 in `IMPLEMENTATION_PLAN.md`, which now carries two separate ordering
tests. **This also qualifies §0.2(c) below:** temperature scaling is hit-rate-neutral *as a pointwise
transform*, but STRIDE branches on the **dispersion** of the transformed vector, so the neutrality
does not survive end-to-end. Item #21 (A3, renormalisation) is genuinely unaffected — a common divisor
changes neither within-race order nor the spread's sign — and stays `ROI`.

**Three things this ranking deliberately does *not* say.**
(i) It does not rank C1 (de-vig) first, despite it being the most-converged finding in Phase 2
(4 of 5 streams, `§A1`) — because the direction of its effect on `modelEdge` is contested (`§C1`,
partly resolved in §5.2 below) and because flipping it re-prices every gate simultaneously, which is
worthless until I1/I4 can score the result.
(ii) It ranks C6 (the SP-vs-racecard defect) at #14 despite the citation audit calling R5-F2 *"the
strongest source-read finding in the document"* — because the *diagnosis* is strong and the *fix* is
structural, retrain-gated, and explicitly forbidden from taking the cheap route (constraint 24: never
backfill).
(iii) It ranks nothing above the measurement block, including items with a larger theoretical prize.

---

## 2. Quick wins vs structural changes vs aspirational

Every item appears in **exactly one** bucket. The bucket is assigned on *calendar time to a
trustworthy answer*, not on lines of code — several items are a day's coding and a month's evidence,
and those are structural.

### 2.1 Quick wins — days, low risk (12 items)

`I1 · I2 · I3 · I4 · I5 · I6 · C2 · C4 · G1 · S1 · S3 · C7`

**Why these qualify.** Each is either (a) read-only or publish-only, so it cannot change a bet
(I1–I5, C2, S1, S3's tagging half), (b) a documentation/constant change (G1), or (c) behind a
default-off `STRIDE_*` flag whose off-path is byte-identical, following the `STRIDE_CL_BLEND`
precedent at `RTP:591` — *"Default: off, byte-identical."* (C4, C7).

**Why the boundary sits here.** `I6` is a quick win only because it writes its refitted artifact to
`models/staging/` and never over the live one, per guardrail 9 (`docs/10:65-66` — *"Retrains are
staged, never auto-promoted"*). Promoting the artifact is a separate, structural decision. `S3` is a
quick win because `commission_rate` defaults to `0.0`, which is byte-identical, and segment tagging
adds a field without gating on it. `C7` is a quick win only under the condition that the shared
helper is proven byte-identical against both existing formulas in a self-test — without that
assertion it is a guardrail-1 violation (rewriting working logic) and belongs in the next bucket.

### 2.2 Structural changes — weeks, higher risk (14 items)

`C1 · C3 · C5 · C6 · A1 · A2 · A3 · G2 · M1 · M2 · M3 · M4 · M5 · S2`

**Why these do not qualify as quick wins**, item by item, since the distinction is the point:

- **C1** (de-vig) — a bisection is an afternoon; but it re-prices `true_market`, and therefore
  `modelEdge` (`RTP:697`), `fairOdds` (`:695`), the anchor (`:692`) and `ev` (`:955`) for *every*
  runner at once, so the 4.0 / 2.5 / 3.0 band thresholds must be re-read on the same run rather than
  ported (R1-F7's own ticket warning). That is a full evidence cycle.
- **C3** (NaN preservation) — two small edits, but they change the probability scale on the *existing*
  artifact immediately, so constraint 18 requires the calibration Brier and the Value-Edge ROI to be
  re-read before default-on.
- **C5** (context multipliers) — repairing them changes every downstream raw-probability threshold
  (conviction 15/12/10, bet-gate floors 30/15/10, longshot ≥8) which have all been silently operating
  on a ~5%-deflated number. Highest risk-per-line item on the list.
- **C6, M1–M5** — retrain-gated. Nothing happens until a retrain runs, and nothing is trusted until
  `retrain_v2.run_ablation` returns a *causal* delta (constraint 36: the Phase-5 precedent).
- **A1, A2, A3** — touch the calibration chain and, for A1, the order of operations, which collides
  with guardrail 6 and requires expected-impact numbers I3/I5 have to produce first.
- **G2** — separating selection from sizing touches the BET/NO_BET contract (guardrails 10, 11).
- **S2** — a shadow column is publish-only, but it needs `S1`'s bankroll state to mean anything and
  its promotion criterion does not exist yet (`G1`).

### 2.3 Aspirational — needs data or infrastructure the system does not have (5 items)

`A4 · X1 · X2 · X3 · X4`

- **A4** (final-stage log pool) — needs a clean-provenance artifact fitted on `--stage final` rows,
  and `prediction_audit` currently holds **260 rows** (`docs/12:197-198`). Blocked by P0-c and by
  constraint 14's hold.
- **X1** (T−5 odds snapshot) — needs prospective collection infrastructure that does not exist, and
  is bound by constraints 23/24 (prospective only, never backfill).
- **X2** (sectional coverage) — a purchase decision (Punting Form, ~85% AU TAB coverage, history to
  Oct 2012, `research/report.md §2.6`, `[prior pass, verified 2-0]`) plus an *access* decision for
  QLD, which constraint 20 explicitly rules out solving in code.
- **X3** (exploded target / RUMBoost) — needs a rebuilt training view emitting full finishing order;
  `race_results_history` holds it (45,070 rows backfilled) but neither the view nor the target does.
- **X4** (drawdown-constrained / DRO Kelly) — needs bankroll history, a daily-budget concept, and for
  the convex form a solver (`cvxpy` is not in `requirements.txt`).

**Deliberately not in any bucket** — proposed nowhere in this report, see §4: within-race
multi-outcome Kelly (R4-F9 says explicitly *"do not propose in Phase 4"*), full MCMC frailty models
(R1-F15, same), neural replacements for the GBMs (R1-F14), and additional model classes (R1-F13).

---

## 3. Per-item detail

Each subsection carries the four scores with a one-line justification (satisfying §1's "detailed
subsection per item"), then the academic backing with F-numbers and confidence labels, the exact
files it touches, and the measurement plan with a pre-registered threshold.

**Sample-size arithmetic used throughout** — all `[derived — this report]` from R3-F5's σ = 3.396:

| Estimand | n needed | Note |
|---|---|---|
| ROI at +20%, t = 2 | **1,154 bets** | R3-F5 |
| ROI at +12.3%, t = 2 | **3,038 bets** | ≈ 7,400 metro races at ~0.40 bets/race |
| ROI at +5%, t = 2 | **18,456 bets** | |
| **CLV**, positive-vs-zero | **~400 bets** | R3-F6; CLV variance is a small fraction of P&L variance |
| **Top-pick hit rate**, unpaired, +3pp on a 33.7% base, 80% power | **~3,970 races per arm** | two-proportion normal approximation |
| **Top-pick hit rate**, unpaired, +5pp | **~1,430 races per arm** | |
| **Top-pick hit rate**, *paired* (McNemar, same races both arms, ~10% discordance) | **~2,000–2,500 races** | ~1.5–2× more efficient; the correct test for every A/B here |

**Read that table before writing any success threshold.** It is why almost every item below is
pre-registered on CLV, reliability, or a paired hit-rate test rather than on ROI: *ROI is the one
quantity STRIDE cannot measure in any realistic horizon.*

---

### #1 — I1. Live wrapper outcome ledger

**What.** Record and settle the thing that actually decides money: `bet_pick` as shipped. Every
published pick gets a row carrying `tipped_odds`, the settled `sp`, `selection_origin`, the crowd
classification, `confidence`, `staking`, `edge_pct`, `win_pct`, `raw_model_pct` and the race key —
**including the picks the system refused**, so the counterfactual is recoverable. Settle P/L twice:
at the tipped price and at SP.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **1** — records, decides nothing | **1** — records, decides nothing | **2** — additive columns plus a `cmd_record` extension on an existing module | **1** — read/write to a results table only; no pipeline decision reads it |

**Academic backing.** `§A6` (3 of 5 streams: R3-F5/F6/F7, R4-F2/F15, R5-F15) — *"Every agent that
touched measurement concluded the same sequencing: measure first, change second."* R3-F7
`[derived — this session]` establishes the specific defect: `shadow_pl_tracker.py:290`
`sp = res.get("sp") or tipped_odds or 0` and `:299` `pl = round(float(sp) − 1, 2)`
(**[re-verified here]**), so the only realised-return series in the system answers *"what would this
have returned at the one price the punter demonstrably did not take?"* — with a bias direction that
**correlates with pick quality** (good picks shorten ⇒ ROI understated; bad picks drift ⇒
overstated). No `[unverified]` claim is load-bearing here.

**Files.** `server/python/shadow_pl_tracker.py` — extend `cmd_record` (`:216` inserts `tipped_odds`),
`cmd_results` (settlement at `:283-301`) and `cmd_report`. Source of rows:
`RTP:1245` `store_selections_in_db` and the `convergence_output` table. Guardrail 4 (additive only):
new columns on `stride_tip_results`, a new migration beside
`migrations/final_prob_audit.sql`; **do not alter the existing `profit_loss` semantics** (R3-F7 says
this explicitly). No new module — guardrail 3 names `shadow_pl_tracker.py` as the existing surface.

**How to measure that it worked.** This item's own success metric is coverage, not P&L:
`settled_rows / published_bet_picks ≥ 0.95` over a full month, and every `selection_origin` value in
`SYSTEM_MAP §8` present in the report. **Pre-registered threshold:** one calendar month of complete
settlement, and the first `cmd_report` printing ROI at tipped price and at SP side by side for at
least the `CONFIRMED`, `CROWD_ONLY` and — critically — `MODEL_ONLY` sets. `SYSTEM_MAP §9 Q7` names
the last of these *"the single highest-leverage unknown in the system"*: `crowd_bet_decision`
(`consensus_blender.py:~261`) returns **False always** for `MODEL_ONLY` — "archetype trap, no crowd
support" — which discards the model's own best value bet whenever no tipster agrees, and nothing has
ever counted what those returned. Report it as ROI **and** CLV (see I2), because at ~0.40 bets/race
the ROI arm will not reach significance for years while the CLV arm will in ~400 bets.

**Sequencing.** Blocked by P0-a. Nothing else on this list should ship before this one is collecting.

---

### #2 — I2. CLV columns in `cmd_report`

**What.** Two SELECT-side columns: mean closing-line value `tipped_odds / sp_odds − 1` and the share
of bets with positive CLV, reported by convergence tier and by price band. No schema change, no
behaviour change, no new data.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **1** — arithmetic on stored fields | **1** — arithmetic on stored fields | **1** — R3 calls it *"the single cheapest high-value change identified"* | **1** — pure SELECT |

**Academic backing.** R3-F6 **[ROI/BOTH]**, `§A6`. The mechanism claim ("CLV expectation equals true
edge under the hypothesis that the close is the market's best estimate") is supported academically
only indirectly, via R3-F8's late-money cluster (`[prior pass, verified 3-0]`: JRA 894,127 runners
2004–2023, coefficient −0.3386 SE 0.0392; AU 2006 all 14,854 races, late pool-share coefficient
4.124 z = 9.79). **R3 states honestly that every direct CLV source it found was practitioner, not
peer-reviewed, and that it could not locate a peer-reviewed CLV→profit quantification.** Carry that
label: CLV is being adopted as a *variance-reduced proxy* on the strength of an argument, not a
measured effect size. R3-F6 also lists the four limits — reference-market dependence, unrealisability,
self-impact, and inheritance of FLB from a biased close — and the fourth matters here: measure CLV
against a **de-vigged** close once C1 lands, not against raw SP.

**Files.** `server/python/shadow_pl_tracker.py` `cmd_report` only. Both prices are already in the
same row: `:216` inserts `tipped_odds`, `:266` selects `sp_odds`, `:~305` writes `api_sp` and
`tipped_horse_sp`. In Australia the SP is the closing line. **The join is done; the ratio is never
taken.**

**How to measure that it worked.** Metric: mean CLV with a t-test against zero, and % positive CLV
against 50% with a binomial test. **Sample size: ~400 settled bets** (R3-F6) versus **~3,038** for the
equivalent ROI claim (R3-F5) — that 7.6× reduction *is* the value of this item.
**Pre-registered threshold:** the `CONFIRMED` tier shows mean CLV > 0 at p < 0.05 over ≥ 400 settled
bets. If it does not, the picks are not beating the market and no amount of staking or calibration
work will make them profitable — which is a result worth having early.

---

### #3 — I3. The read-only diagnostic battery

**What.** One read-only script printing nine counters. Every one was independently nominated by a
Phase-2 stream as its own highest-value next step (`ACADEMIC_FINDINGS §"Cheapest measurements"`).
Bundled because they share a database session and none is worth a ticket alone.

1. **Overround distribution** — computed at `RTP:432-442`, **stored nowhere, aggregated never**.
   R4 calls this *"the single most valuable cheap measurement in this report"*: R4-F2's entire
   negative-EV claim (that the `$3–$5` band needs an overround below ~1.081 to break even) rests on
   assumed values of R = 1.15/1.25 which are *"plausible AU assumptions, not measurements."*
2. **`Σ winPercentage` per race** — R2 calls it *"the single cheapest empirical check in the report"*.
   R2-F5 verified that the three `winPercentage` assignment sites (`RTP:624`, `:661`, `:693`) are each
   *pointwise* and **no renormalisation follows any of them**. If the sum lands at 104% or 92%, every
   `modelEdge` on that card is biased by a common factor — a pure ROI error with **zero hit-rate
   signature**, hence invisible to every harness STRIDE owns.
3. **Mean `mlPredictedProb` per card vs observed win rate** — nominated independently by R1 and R2.
   Confirms or kills `§A5` (class-imbalance inflation) in one line with zero behaviour change.
4. **`mc_is_flat` firing rate, by field size** — `SYSTEM_MAP §9 Q10`. Nominated by R2, R4 and R5.
   Gates G2, A2 and M3.
5. **Count of the `valid < 2 → return 1.0` branch** in `calculate_overround` (**[re-verified here]**:
   `if valid < 2 or total_implied <= 0: return 1.0`) — a thin race has its *raw* implied probability
   used as if it were vig-free. R5-F16 adds that this gives *"the thinnest, most scratching-affected
   races the most optimistic edges — precisely backwards."*
6. **Per-feature non-zero / non-NaN counts over the full 113-column contract** — R5-F4's *"missing
   diagnostic"*. Must be run over **both** inference paths (`RTP:2258-2308` **and**
   `mc_api.extract_all_sophisticated_features`, `mc_api.py:5436`), per the citation audit's
   re-verification queue item 11.
7. **`apply_safety_filters` fallback firing rate** — the "three shortest-priced runners" path
   (`RTP:~935-938`) directly contradicts the value philosophy and nothing counts it (`§9 Q11`).
8. **Intelligence-override firing rate** (`_check_intelligence_override`, `RTP:1727`) — it bypasses
   the entire edge gate and nothing counts it (`§9 Q12`).
9. **`true_market` under proportional vs power vs Shin, tabulated by price band** — the empirical
   resolution of conflict `§C1` (see §5.2). Computation only; no method is adopted here.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **1** — prints numbers | **1** — prints numbers | **2** — one script, nine queries | **1** — read-only |

**Academic backing.** R4-F2, R2-F5(a), R1-F3 + R2-F4, R2-F10(c) + R4-F17 + R5-F7, R5-F16, R5-F4,
`SYSTEM_MAP §9 Q10-Q12`, `§C1`. All **[derived — source read]** or **[STRIDE-specific]**; no external
citation is load-bearing for any of the nine.

**Files.** A new `server/python/research/live_diagnostics.py`, sitting in `server/python/research/`
— which holds **two** standalone diagnostics plus a 9-module `winner_pattern_gap/` package, not
"twelve diagnostics" (corrected by the Phase-4 audit) — and following `audit_coverage_report.py`'s
pattern exactly. Note that template lives one level up in `server/python/`:
read-only session, a self-test asserting **every query is a SELECT** (`docs/12:193-195` — *"the
workflow cannot mutate the database"*), invoked by a `workflow_dispatch`-only Action beside
`.github/workflows/audit-coverage.yml`. Counters 3, 4, 7 and 8 need runtime counts the pipeline does
not currently emit — add them as stderr lines in the house `[MC_FLAT]` / `[GATE]` bracketed-tag style
(`SYSTEM_MAP §4`), which is also the repo's stated convention of *"a positive assertion (a printed
count, a validator line) rather than relying on the absence of an exception."*

**How to measure that it worked.** Binary: all nine numbers exist and are reproducible. **Threshold:**
the report runs green on a month of data and each of `SYSTEM_MAP §9` Q6, Q8, Q10, Q11, Q12 moves from
"unknown" to a number. Three of the nine have pre-registered *alarm* thresholds worth stating now —
if `Σ winPercentage` deviates from 100 by more than ±2pp on the median race, A3 is promoted
immediately; if `mc_is_flat` fires on more than 15% of races, G2 is promoted immediately; if the
`< 2 quotes` branch fires on more than 2% of races, C1's thin-race arm is promoted immediately.

---

### #4 — I4. Error bars, trial counts and an (odds × edge) ROI surface

**What.** Three additions to the backtest reporting, none of which changes a strategy: (i) a
t-statistic, standard error and 95% CI on every strategy row; (ii) the **number of configurations
tried**, printed alongside the winner; (iii) an (odds-decile × edge-decile) realised-ROI surface with
per-cell n and CI.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **1** — reporting only | **1** — reporting only | **1** — R3 sizes it at *"~15 lines in `backtest_v2_metro.py`"* | **1** — no strategy changes |

**Academic backing.** R3-F1/F2/F3 and R5-F15, all **[derived — this session]** from
`examples/backtest_summary.json`, committed in this repo; the citation audit re-ran the arithmetic
and reports it *"reproduces to the decimal (σ = 3.396, SE = 0.285, t = 0.432, CI [−43.5%, +68.2%],
the 81-bet residual cell at +40.1%, n = 3,038 for t = 2)."* This is the strongest evidence class in
the entire Phase-2 document and depends on no external citation. The multiple-testing framework
(White 2000 Reality Check; Hansen 2005 SPA; Bailey & López de Prado's Deflated Sharpe; PBO via CSCV)
is **[snippet-only]** and R3-F17 is explicit that *"the agent could not obtain the DSR/MinBTL
equations, so F17 is a protocol sketch, not an implementable spec."* **Therefore: implement (i) and
(ii), which need no equations, and do not attempt DSR/PBO now.** R3-F17's own summary agrees —
*"at N = 6 trials, 352 races, 142 bets and t = 0.43, steps 2–5 are unnecessary because step 1 already
settles it."*

**Files.** `server/python/backtest_v2_metro.py` (`STRATEGIES` and `STAKE = 100` at `:157-166`; the
per-strategy keys are exactly `label, bets, wins, strike_rate, staked, returned, pnl, roi` — no CI,
no SE, no t). `server/python/backtest.py` (13 sweeps plus `optimize_threshold`, and **no purge gap**).
`server/python/walk_forward_backtest.py` already computes t-distribution 95% CIs at `:220-229`,
`:250-262` — but **across folds, not across bets**, so it measures fold-to-fold dispersion of a mean,
not the sampling error of the bet population; that distinction must be printed, not fixed silently.

**How to measure that it worked.** Metric: every strategy row carries `n`, `SE`, `t` and a 95% CI, and
the report header carries the trial count. The ROI surface is the real deliverable — R3-F3 calls it
*"worth more than any new feature in the roadmap"* because it tests whether `modelEdge` is monotone at
all. **Pre-registered threshold:** the surface is computed on ≥ 1,000 bets before any cell is used to
justify a threshold, and **no cell with fewer than 100 bets may be quoted** (at σ = 3.396 a 100-bet
cell has a 95% CI of roughly ±67pp). The specific claim to test: R3-F3's decomposition finds
`$5–$15, edge 3–5%` at **+40.1% on 81 bets** against `$5–$15, edge ≥ 5%` at **−14.8% on 54 bets** —
*ROI falling as modelled edge rises*, which R3 calls *"the canonical signature of a threshold fitted
to noise."* If the surface reproduces the non-monotonicity on a larger sample, the conviction ladder
(`RTP:836-843`, +3.0/+2.0/+1.0 rising in edge) and the longshot keep-rule (`odds≥15 & edge>2`) are
both built on a quantity that does not behave as assumed, and C1/A1 move up.

---

### #5 — I5. Calibration measurement upgrade

**What.** Four additive metrics alongside the existing `brier` / `log_loss` / `ece` scalars:
(i) equal-**mass** ECE (10 quantile bins) and the debiased estimator, next to the existing
equal-width one; (ii) a PAV/CORP miscalibration score with no binning hyper-parameter; (iii) Murphy's
**reliability − resolution + uncertainty** decomposition of the Brier score; (iv) the **calibration
slope and intercept** from a logistic regression of the outcome on the logit of the prediction, with
standard errors, stratified by odds band, field size and going.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **1** — measures, decides nothing | **1** — measures, decides nothing | **2** — additive keys in an existing harness; `mc_recalibration.py:157` already contains a hand-rolled PAV to reuse (guardrail 3) | **1** — new dict keys only |

**Academic backing.** R2-F6 (binned ECE is *"asymptotically inconsistent with a negative bias"* —
Vaicenavicius AISTATS 2019, Kumar/Liang/Ma NeurIPS 2019, Roelofs AISTATS 2022, Dimitriadis/Gneiting/
Jordan PNAS 118(8) for CORP; all **[snippet-only]**). R2-F7 part (b) — Murphy's decomposition — is
unaffected by the audit's misattribution finding, which struck only the Kelly↔Bregman *quotation*
(arXiv 2607.06166 resolves to a different paper); **the underlying identity that expected log score
is the expected log-wealth growth rate of a Kelly bettor is elementary and survives on its own.**
R2-F8 (calibration hierarchy; slope < 1 ⇒ predictions too extreme) — Van Calster et al., BMC Medicine
17:230, **[snippet-only]**, >2,000 cites. R2-F9 (grouping loss) — Perez-Lebel et al. ICLR 2023,
**[snippet-only]**.

The STRIDE-side argument is stronger than any of them and needs no citation: `walk_forward_backtest.py`
uses `compute_ece(…, n_bins=10)` with `np.linspace(0,1,11)` at `:100-117`. With a ~9.7% base rate and
an MC ceiling of 60% (`mc_api.py:7398`), the bins above 0.6 are empty and `[0.0,0.1]`/`[0.1,0.2]` hold
nearly all the mass — **effective B ≈ 3, not 10**. And R2-F7's arithmetic on the promotion bar is
decisive: Brier 0.0834 vs 0.0841 is 0.0007 on a number whose irreducible floor is ~0.088, so *"the
reported Brier differences are almost entirely uncertainty, not skill"* — **the bar as written is
close to un-actionable**, which is why this item gates G1.

**Files.** `server/python/walk_forward_backtest.py` — `compute_ece` at `:100-117`, called `:153`;
metric assembly at `:209`, `:461`. Reuse the PAV at `server/python/mc_recalibration.py:157` rather
than writing a second one (guardrail 3). The slope/intercept regression needs no new dependency:
`sklearn.linear_model.LogisticRegression` on a single logit feature (`statsmodels` is not among the
32 deps in `requirements.txt`).

**How to measure that it worked.** Metric: the harness emits `ece_equalwidth`, `ece_equalmass`,
`ece_debiased`, `corp_mcb`, `brier_reliability`, `brier_resolution`, `brier_uncertainty`,
`calib_slope`, `calib_intercept` and their per-stratum versions. **Sample size:** the slope/intercept
regression needs enough *events*, not rows — a rule of thumb of ≥ 100 winners per stratum means a
per-odds-band table is feasible on ~1,000+ races and a per-band × per-field-size table is not.
**Pre-registered threshold:** the equal-mass ECE and the CORP score are reported for the same folds as
the existing equal-width one, and **the difference between them is published** — R2-F6's whole claim is
that the existing number is biased low, and the size of that bias on STRIDE's own data is currently
unknown. If `calib_slope` differs from 1.0 by more than 2 standard errors in any odds band, A1 and A2
are promoted and the `mw` ladder is formally reclassified as an unfitted correction for a measurable
quantity (R2-F8's core point: the in-code Kelly-audit comment at `RTP:676-678` — *"$1-3 horses win
41%, model predicts 17% after blend"* — **is a verbal statement of a calibration-slope failure**).

---

### #6 — I6. Calibrator provenance

**What.** Three things: (a) `fit_calibrator.py`, a sibling script that regenerates
`isotonic_calibrator.pkl` from `training_view_v2` using the **same 14-day purge** as
`retrain_v2.DateWindowSplitter`, writing to `models/staging/` and never over the live artifact;
(b) a sidecar metadata dict `{fit_rows, date_range, stage, sklearn_version, fitted_at}` saved with it;
(c) a **printed positive assertion at load** in `RTP:~574-576` naming the artifact's age, row count
and stage — plus a loud warning if the artifact is absent or older than a configurable horizon.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **2** — (c) alone can reveal that no calibration is running, which would re-frame every edge number | **1** — does not reorder anything | **2** — one new script, three lines at the load site | **1** — staged artifact, never promoted (guardrail 9) |

**Academic backing.** R2-F2 **[STRIDE-specific, verified this session, re-confirmed by the citation
audit: "`ProbabilityCalibrator.fit()` has zero callers; four sites total; nothing produces
`models/isotonic_calibrator.pkl` — re-run and confirmed exactly"]**. This is the strongest class of
evidence in Phase 2 and needs no external source. The supporting norms — scikit-learn's
`cv="prefit"` disjointness warning, Niculescu-Mizil & Caruana ICML 2005 — are **[snippet-only]**.
R2-F16 supplies the drift half (Davis et al., J. Biomedical Informatics 2020, **[snippet-only]**),
with an AU-specific shift channel that is *already in flight*: QLD sectionals structurally absent
behind Cloudflare means a feature block silently switched off for one state, changing the input
distribution the calibrator was fitted under.

**Files.** New `server/python/fit_calibrator.py`, beside `server/python/retrain_v2.py` and
`server/python/calibration_model.py` — **not inside** either, because guardrail 1 forbids rewriting
working logic and `ProbabilityCalibrator` (`calibration_model.py:25-38`, bounds `y_min=0.01`,
`y_max=0.95`, `out_of_bounds="clip"`) is the class to *call*, not to change. Load site:
`RTP:~574-576`. A `workflow_dispatch` Action beside `.github/workflows/retrain-model.yml`, following
the same staged-artifact-upload pattern.

**How to measure that it worked.** Metric: the sidecar exists, and the load line prints a non-empty
`fit_rows` and `fitted_at` on the next production run. **Pre-registered threshold:** P0-b is answered
definitively. If the artifact turns out to be **absent** in production, that single fact re-frames
items A1, A2, C1 and every downstream edge number — the live chain would be *ML blend → market anchor*
with no calibration at all — and it becomes the highest-priority finding in this report. Note the
caution: the newly fitted artifact must not be promoted on the strength of existing so-so numbers.
R2-F18's arithmetic bites here — `training_view_v2` splits **106,193 none / 12,590
imported_historical / 794 live_model** (`docs/12:352`), and against the ~2,000-case threshold at which
Platt beats isotonic (Niculescu-Mizil & Caruana, **[snippet-only]**), *794 live-model rows is deep in
the regime where isotonic is the wrong family*. **Refitting the same wrong family is not the goal of
this item; establishing what is actually running is.** A2 is where the family question is decided.

---

### #7 — C2. Publish `edge_at_price` and the Kelly sign test as shadow fields

**What.** Add two published-but-unused fields per pick: `edge_at_price = win_pct − 100/odds` (the
**vig-inclusive** edge) and `kelly_sign = (win_pct/100)·odds − 1 > 0`. Nothing reads them. They exist
so that the next month of data answers R4-F2 empirically.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **3** — if R4-F2 is right, the sub-$6 bands are structurally negative-EV and this is how you find out | **1** — publish-only, changes no ordering and no gate | **1** — two arithmetic lines and two output keys | **1** — additive fields (guardrail 4); no consumer |

**Academic backing.** R4-F1 and R4-F2, both **[the agent's own algebra on source read this
session]**, re-verified by the citation audit (*"R4-F2's 'the live gate is not the validated band' is
correct"*). The chain: Kelly's `f* = (p·o − 1)/(o − 1)` means `f* > 0 ⟺ p·o > 1`, and Kelly is
scale-free in the bookmaker margin because the margin is already inside `o`. STRIDE's two value
quantities are both computed against the **de-vigged** market, so **neither tests `p·o > 1`**. Writing
`R` for the overround, `modelEdge > 0 ⟺ p·o > 1/R`, and both positivity tests are satisfied at
`p·o = 1/R` where the true return at the price is `1/R − 1` — **−16.7% at R = 1.20**. And
`race_normaliser.py:225` accepts overrounds up to **1.60**, at which `ev > 0` is satisfied by bets
returning **−37.5%** — with the audit's addendum that the `0.90/1.60` check is guarded by
`if odds_count >= 3` at `:223`, so **a two-quote race gets no overround validation at all** while
`calculate_overround` still de-vigs it.

The second half is the sharper one. `backtest_v2_metro.py:215-217` computes `implied = 1.0/sp;
edge = prob − implied` — the **vig-inclusive** edge — so the validated `"Value Edge 3%+ ($2-$15)"`
band is a 3-point threshold on *that* quantity, while `RTP:1816/1821/1825` apply their thresholds to a
different, systematically larger one. **The live gate is looser than the validated band by
`(100/o)(1 − 1/R)` pp at every price: 3.3pp at $5 / R = 1.20, 1.1pp at $15 / R = 1.20.** Two different
edges wear the same name and the same "3%".

**Files.** `RTP:1778` `evaluate_bet_candidate` (reads `edge_pct` at `:1801`); `RTP:955`
`compute_confidence` for the EV parallel; `RTP:1245` `store_selections_in_db` for the columns
(115-column INSERT at `:1329-1398` — additive only, with a migration note per guardrail 4);
`server/python/validate_tips.py` and `server/python/backfill_tips_contract.py` must keep passing —
`backfill_tips_contract` imports the live functions so the logic cannot drift, and guardrail 11 says
keep that import, do not fork it.

**How to measure that it worked.** Metric: for each price band, the realised ROI of picks split by
`edge_at_price > 0` vs `≤ 0` while `edge_pct ≥ gate`. **This is the falsification test for R4-F2's
strongest claim** — that the `$3–$5` band requires an overround below ~1.081 and is therefore
negative-EV in essentially every real market. **Blocking dependency, stated by R4 itself:** the actual
overround distribution is unknown, which is why I3 counter 1 must land first. **Sample size:** because
the split is on a *within-sample* classification rather than an ROI difference, ~300–500 bets per band
gives a usable sign, though not a confidence interval on the magnitude. **Pre-registered threshold:**
if more than 20% of published `$3–$5` picks have `edge_at_price ≤ 0`, that band's gate is formally
declared mis-specified and a Phase-5 ticket re-denominates the gates in EV rather than percentage
points (R2-F14's *"the edge thresholds are denominated in the wrong units"*). **Do not change the gate
in this ticket.**

---

### #8 — C4. Rao-Blackwellise the MC win probability

**What.** Instead of counting argmax frequency over draws, average the analytic per-draw softmax
`q_i = softmax(logits_s)_i` across draws. Same estimand, strictly lower variance, and it replaces an
`argsort` with a `softmax`. Behind `STRIDE_MC_RAO_BLACKWELL`, default off, byte-identical when off.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **3** — removes ±2.84pp of run-to-run noise from a quantity gated at 3.0pp | **2** — a less noisy ranking is a slightly better ranking, but the *expected* ordering is unchanged | **1** — R1 sizes it at ~5 lines; only the win probability is analytic per draw, so place/top-3 are untouched | **2** — changes the production probability, but flagged, and the change is a variance reduction not a shift |

**Academic backing.** R1-F16 **[BOTH]**, arithmetic on STRIDE's own code. Binomial SE at p = 0.30:
**1.02pp at N = 2000, 0.84pp at N = 3000, 0.65pp at N = 5000** — the citation audit confirms *"all
three reproduce exactly from `sqrt(0.3·0.7/N)`"*. The audit also **corrected the headline upward**:
two independent scorings of the same race differ by **±2.84pp (95%)** at N = 2000, not the ±2.0pp
originally written, because the SE of the *difference* is `1.02·√2 = 1.45pp`. The daily seed is
`int(time.time()) % 100000` (`RTP:2340`, audit-verified), so successive scorings genuinely are
independent. Against `edge ≥ 3` (`RTP:1825`), conviction steps at 3/2/1 (`RTP:836-843`) and
`mc_spread < 6.0` (`RTP:705`), **the run-to-run swing exceeds the entire edge gate**. The Gumbel-max ↔
softmax equivalence and Rao-Blackwellisation are textbook but the attribution is
**[recall — unverified]** because Wikipedia and arXiv were both blocked — that does not weaken the
item, because the mathematics is checkable in three lines and the STRIDE side is a source read.

**Free by-product, and the reason this item punches above its size:** `Var_s(q_i)` is a per-runner
**model-uncertainty** estimate. That is the exact input `§A4` says STRIDE computes and discards at
three separate points (R1-F16 shrink the probability, R3-F4 shrink the edge, R4-F5/F8 shrink the
stake), and it is a better basis for the `stability` term in `mc_selection_score`
(`RS:~1925-1932`, weight 0.12).

**Files.** `racing_system_v8.3_mc.py` — the sampling site at `:1855-1856`
(`noise = rng.gumbel`, `order = argsort(-(logits + noise))`) and the probability assembly at
`:1859-1861` (`win_probs` `:1859`, `top2_probs` `:1860`, `top3_probs` `:1861` — the audit corrected
`:1858` to `:1861`). Emit `win_prob_var` alongside. Sims-by-field-size is set at `RTP:389-394`
(5000 / 3000 / 2000) and needs no change. Guardrail 3: extend the existing simulator, do not add a
fourth MC engine — the repo already carries four (`SYSTEM_MAP §4`).

**How to measure that it worked.** Metric: on a fixed set of stored races, score each race **20 times
with different seeds** under flag-off and flag-on, and report the standard deviation of
`winPercentage` for the top-ranked runner in each condition. This is a self-contained experiment
needing no outcomes and no waiting — **the only item on this list that can be fully validated in an
afternoon.** **Pre-registered threshold:** flag-on reduces the across-seed SD of the top runner's
`winPercentage` by ≥ 50% at N = 2000 sims, with the mean unchanged within 0.2pp (a variance reduction
must not be a level shift). Secondary, paired on the same races: the top-1 ordering changes on < 5% of
races — if it changes more, the ranking was being decided by sampling noise on those races, which is
itself the finding. Then, and only then, run the A/B against outcomes; note `SYSTEM_MAP §7b.7` — any
outcome A/B must fix the seed or average over many runs, using the existing `--skip-db-store
--output-suffix` proof-run idiom.

---

### #9 — G1. Promotion-bar amendment and a variance-aware reportability floor

**What.** A documentation change plus one constant. (a) State the promotion bar per *lever class*:
ranking changes are judged on paired top-pick hit rate; anchoring/gating changes on the ROI surface
**and** CLV; calibration changes on reliability and calibration slope (not aggregate Brier);
**staking changes on expected log-growth and drawdown on a replayed bankroll path.** (b) Make
`MIN_BETS_REPORTABLE` variance-aware: compute realised per-bet SD from settled rows, report the CI,
and flag any tier whose CI spans zero as `NOT REPORTABLE` **regardless of count**. (c) Require every
backtest report to print its trial count.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **1** — changes no bet | **1** — changes no bet | **1** — a docs edit and one function | **1** — governance only |

**Academic backing.** R4-F14 **[ROI]**, definitional plus the agent's own arithmetic, and it names the
gap precisely: *"That bar cannot evaluate a staking change. A staking change moves neither hit rate nor
Brier and will often lower ROI% while being strictly correct. Judged by the existing bar, the right
answer fails."* R4 calls it *"a process finding, and the one most likely to be missed."* R3-F5
**[derived]** supplies the reportability arithmetic: at σ = 3.396, **200 bets gives a 95% CI on ROI of
roughly ±47 percentage points**, so `MIN_BETS_REPORTABLE = 200` is *"roughly 15× too small for an ROI
claim in this bet population"* — fine as a floor for hit rate (binomial, much tighter), not for ROI.
R2-F7 supplies the calibration half (aggregate Brier is dominated by the uncertainty term). R3-F17 and
R5-F15 supply the trial-count half. The empirical support for `Uhrín et al. 2021`'s framing
(*"betting strategies significantly affect profitability, often outweighing predictive model quality"*)
is **[snippet-only]** and is used as motivation, not as a sizing input.

**Files.** `docs/12-hit-rate-research.md:435-438` (the bar itself) and `docs/10-backtesting-and-learning.md:83-84`
(the ≥200 rule). Code: `server/python/shadow_pl_tracker.py` `MIN_BETS_REPORTABLE = 200` at `:323`,
consumed at `:363` (the audit corrected the anchor from `:130`). Report header:
`server/python/backtest_v2_metro.py` and `server/python/backtest.py`.

**How to measure that it worked.** Metric: no tier is reported with a CI spanning zero; every backtest
report carries its trial count; the four lever-class criteria are written down. **Pre-registered
threshold:** the amended bar is applied retrospectively to the two decisions already made under the old
one — the Phase-5 promotion (**−0.0012 AUC**, kept *"to avoid churn"*) and the LambdaRank rejection
(H2H 39.7% / 34.4% / 33.6% on 996 races) — and both verdicts either survive or are restated. If the
amended bar would have changed either verdict, that is a finding about the bar, not about the change.

---

### #10 — S1. Bankroll state and a stake column

**What.** Add a running-bankroll column and a realised-stake column to `stride_tip_results`, so that
a P&L path exists that any staking rule can be replayed against — **including retrospectively, over
history already collected**. Writes no new decision and changes no behaviour, so it can ship
default-on without violating guardrail 2.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **1** — records | **1** — records | **2** — two additive columns plus a replay function | **1** — additive schema (guardrail 4); nothing on the live path reads it |

**Academic backing.** R4-F15 **[ROI]**, and it is the stated prerequisite for **seven** of R4's own
findings (F3, F4, F5, F7, F8, F10, F13). The core observation needs no citation: *"Kelly is
proportional to current wealth… A system that emits stake **labels** has not implemented Kelly
regardless of what formula computes them."* `run_tips_pipeline.py` contains **zero occurrences of the
string "bankroll"** (grepped by R4 this session); the only unit definition anywhere is
`STAKING_CONFIG['unit_percent'] = 0.01` (`RS:132`), consumed only by `RacingSystem.__init__`
(`RS:2289`) and the standalone CLI (`RS:3163`), **neither reachable from the daily pipeline**. Two
unreconciled staking vocabularies coexist — `2u/1u/0u` from `compute_staking` and
`FULL/STANDARD/REDUCED/NONE` from the crowd gate, the latter *"a label only — nothing anywhere
converts it to a number"* (`SYSTEM_MAP §2` step 15).

**Files.** `server/python/shadow_pl_tracker.py` (`cmd_record`, `cmd_results`, `cmd_report`), plus a
migration beside `migrations/final_prob_audit.sql`. Guardrail 3 is explicit that the staking module to
extend when the time comes is `racing_system_v8.3_mc.py:309-326` — **not** `portfolio_risk.py`, which
R4-F7 shows computes its variance at `q = 1/odds` (the market-implied probability) at `:235` while
using the model probability for the EV term at `:234`, so *"the Sharpe ratio at `:237` divides a
model-based EV by a market-based standard deviation"*, and which has **zero importers**.

**How to measure that it worked.** Metric: a replayable bankroll series exists over all settled rows,
and replaying the current `2u/1u/0u` rule reproduces the reported flat-stakes P&L exactly. **This
reproduction is the acceptance test** — if the replay does not reproduce the existing series, the
ledger is wrong. **Pre-registered threshold:** exact reproduction to the cent on ≥ 200 settled rows.
**Do not size anything off this yet** — R3-F14 is explicit: *"given F1–F4, STRIDE should not turn on
Kelly. Kelly sized off an edge whose t-stat is 0.43 and whose ROI is non-monotonic in the edge itself
is the exact configuration the literature says produces ruin."*

---

### #11 — C1. De-vig method selector

**What.** A `method=` parameter on the existing `calculate_overround` / `true_market` pair supporting
`proportional` (default, byte-identical), `power` (`p_i ∝ (1/o_i)^k`, one bisection solving
`Σ(1/o_i)^k = 1`) and `shin` (a branch of the same selector, never a second module). Behind
`STRIDE_DEVIG_METHOD`, default `proportional`.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **4** — `true_market` is 20–70% of the shipped probability and the **entire subtrahend** of `modelEdge`; this is the one design choice with a clear tested answer STRIDE has not taken | **1** — the power transform is **monotone in odds**, so `odds_rank` and within-race order are preserved and hit rate cannot move | **2** — a bisection and a selector | **3** — off it is byte-identical, but flipping it re-prices every gate simultaneously, so the 4.0/2.5/3.0 thresholds must be re-read on the same run, never ported |

**Academic backing.** `§A1` — **the most-converged finding in the run, 4 of 5 streams** (R1-F7,
R2-F13, R3-F9/F10/F13, R5-F16), each having independently read `RTP:432-442` and `:673-674` and
independently flagged the `< 2 quotes → return 1.0` branch. The literature here is unusually
well-supported for this document: the citation audit **CONFIRMED** Štrumbelj 2014, IJF 30(4):934-943
(83 citations; *"Shin's model … more accurate forecasts than … basic normalization or regression
models"*) and **CONFIRMED verbatim** Clarke, Kovalchik & Ingram 2017, *American J. Sports Science*
5(6):45-49 — the power method *"universally outperforms the multiplicative method and outperforms or
is comparable to the Shin method"*, with basic normalisation's named defect being that it *"does not
account for favourite long-shot bias."* Stephen Clarke is at Swinburne, so the AU transfer is about as
direct as it gets. **Two audit caveats that must be carried:** (i) Štrumbelj's *"Shin's advantage
shrinks as market size grows"* was **struck as `[unverified]`** — do not argue for Shin on the grounds
that AU fields are 8–16; (ii) *"the numerical comparison tables still could not be obtained"* and R3
names this *"the single most valuable missing number in this report"*, so **the effect size is
unknown** and this item must not be sized on an assumed magnitude.

**Files.** `RTP:432-442` (`calculate_overround`) and `RTP:673-674` (`true_market`), extended in place
per guardrail 3 — **not** a new module, and **not** `mc_api`'s separate un-de-vigged edge at
`mc_api.py:7636` (`edge = winPct − 100/odds`), which is a different quantity on a different scale and
is out of scope. Also touch: the `valid < 2 → 1.0` branch, which R5-F16 shows gives the thinnest races
the most optimistic edges — the right behaviour there is to **refuse to publish an edge**, not to
invent a vig factor.

**How to measure that it worked.** Three stages, in order.
*Stage 1 (diagnostic, no behaviour change — folded into I3 counter 9):* tabulate `true_market` under
all three methods on stored fields, by price band, and publish the signed differences. This resolves
conflict `§C1` empirically (see §5.2 for its partial analytic resolution).
*Stage 2 (flag on, `mw` frozen):* re-run the backtest under each method. Metric: **log loss and Brier
of `true_market` itself against outcomes**, per price band — the market probability is a forecast and
can be scored directly, which is far more powerful than waiting for ROI. **Sample size:** ~3,000
runner-observations gives a usable log-loss comparison; the repo has 119,577 view rows.
*Stage 3:* only then revisit the thresholds.
**Pre-registered threshold:** adopt `power` as default only if it improves the log loss of
`true_market` against outcomes by ≥ 0.002 with a bootstrap CI excluding zero, **and** hit rate is
unchanged (which it must be, by monotonicity — if it is not, the implementation is wrong and that is a
useful unit test). R3-F10's falsifiable prediction should be recorded and checked: the two
shortest-price backtest cells are the two worst performers (`Short Price $2–$5 3%+` **−100.0%** on 7
bets; `Mid-Range $3–$8 5%+` **−28.0%** on 25) — *"consistent with, not proof of"*, on samples far too
small to conclude from.

---

### #12 — A1. Move the calibrator downstream of the blend, and re-enable the per-model OOF isotonic — as one ticket

**What.** Fit and apply **one** calibrator to `rawModelProb` **after** the MC↔ML blend (`RTP:667-670`)
and **before** the market anchor (`RTP:692`), and in the *same change* switch the per-model OOF
isotonic calibrators back on at `ml_model.py:565`. Behind `STRIDE_POSTBLEND_CALIB`, default off.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **4** — it fixes the *level* of the quantity every gate reads, and `§A5` names the underlying defect *"the most concrete mechanism available for 'top pick wins 33.7% but returns −4.2%'"* | **2** — a rank-preserving calibrator leaves within-race order alone; the OOF half can reorder slightly via the ensemble weights | **3** — the fit needs post-blend rows that do not yet exist at volume | **4** — collides with guardrail 6 (reordering) and guardrail 12 (never double-calibrate); the *only* safe form is a single ticket doing both halves |

**Academic backing.** R2-F3 and R2-F4 (**[BOTH]**), R1-F3 and R1-F17, `§A2` and `§A5`. The
STRIDE-side facts are source reads and are not in dispute: the isotonic runs at `RTP:657-661` on the
**MC arm only**, *before* the ML blend at `:665-668`, so **nothing calibrates `mlPredictedProb`, ever**
unless a stacking learner or double calibrator exists (unknown, `§9 Q1-2`). The ML leg carries three
mutually inconsistent imbalance corrections — `scale_pos_weight: 9` (`retrain_v2.py:774`),
`is_unbalance: True` (`:791`), `auto_class_weights: "Balanced"` (`:802`) — and the repo **fits the
correct antidote** (per-model OOF `IsotonicRegression` at `:835/855/870`, assigned from out-of-fold
predictions at `:1169-1190`) and then **does not apply it**. R1-F3's arithmetic: `scale_pos_weight = 9`
multiplies fitted odds by ~9, so a true p = 0.10 emits ≈0.50; blended at `ml_w = 0.40`, `rawModelProb`
for an average runner inflates toward ~26 rather than ~10.

**Two audit corrections that must be carried, and they cut in opposite directions.** (i) R1-F3's claim
that the induced miscalibration *"was not always able to be corrected with re-calibration"* is
**`[unverified]`**, and the primary sources are **logistic-regression clinical papers, not tree
ensembles** — the transfer to XGB/LGB/CatBoost is an assumption. (ii) arXiv 2606.29720 was **misused**:
it does not study `scale_pos_weight`/`is_unbalance`/`auto_class_weights`, finds SMOTE's cost small
(ECE +0.009), and its headline is that **post-hoc Platt or isotonic recalibration eliminates the
damage (ECE −66%)**. Net: that source is **evidence for the remedy, not for the alarm.** So the honest
framing of this item is *"the fitted antidote is switched off and the literature says it works"*, not
*"the distortion is uncorrectable"*. Confirmed sources: van den Goorbergh 2022 JAMIA 29(9):1525-1534
(**CONFIRMED** — *"all imbalance correction methods led to poor calibration"*, no discrimination
effect) and Ranjan & Gneiting 2010 JRSS-B 72(1):71-91 (**CONFIRMED**, the strongest calibration
citation in the document) for why a linear pool of calibrated forecasts needs recalibration after it.

**Guardrail 12, restated correctly.** `docs/05:100-103` forbids switching the per-model isotonic on
without refitting the downstream calibrator *"in the same change"* — which is exactly what this ticket
is. R2-F17 refines the rule and the refinement should be adopted into `docs/05`: *calibrators may be
stacked if and only if each is fitted on data disjoint from all upstream fits; probability forecasts
may not be linearly pooled after calibration without a recalibration of the pool.* As written the rule
**blocks the safe composition while leaving three linear pools in a row completely unguarded.**

**Files.** `RTP:568` `calibrate_and_score` — the isotonic call at `:657-661` moves to after `:670`;
`server/python/ml_model.py:565` (the deliberate non-application); `server/python/retrain_v2.py:835/855/870`
(the fitted OOF calibrators); `server/python/calibration_model.py` (the class); I6's
`fit_calibrator.py` (the refit, on `prediction_audit.final_win_prob`, which
`store_final_probs_in_audit` at `RTP:1582` already writes for every runner). `mc_recalibration.py` is
worth reading before designing this: it is inert in this tree but it is **the only layer in the whole
stack that renormalises per race after calibrating** (`:204`) — the thing R2-F5 says everything else
fails to do, and the reason A3 exists.

**How to measure that it worked.** Metric, in order of power: (1) **reliability** (the decomposed
Brier component from I5) on the post-blend probability, flag-on vs flag-off, same folds; (2)
**calibration slope** toward 1.0 per odds band; (3) mean `rawModelProb` per card against observed win
rate (I3 counter 3) — the direct test of the inflation claim; (4) paired top-pick hit rate, which must
**not** degrade. **Sample size:** reliability and slope need events, so ~1,000+ winners ≈ 10,000
runner-rows, available now in `training_view_v2`; the hit-rate non-degradation clause is a paired
McNemar test needing ~2,000–2,500 races to detect a 3pp move. **Pre-registered threshold:** adopt
default-on only if reliability improves, `|calib_slope − 1|` shrinks in at least four of the six price
bands, **and** paired top-pick hit rate is non-inferior within 1pp — i.e. the existing promotion bar
(`docs/12:435-438`) restated on the decomposed metric, per G1. **Blocking dependency:** requires
post-blend rows at volume, so P0-c must be answered and `prediction_audit` must be filling.

---

### #13 — C3. Restore NaN preservation at inference, and supply the 8 sectional primitives

**What.** Two edits: (a) return `np.nan` rather than `0` for the 13 NaN-preserved columns at
inference; (b) write the 8 sectional primitives (`z_200m`, `z_400m`, `z_600m`, `z_800m`,
`lambda_decay`, `svi`, `rsi`, `trip_cost_seconds`) into `feat`. Behind a `STRIDE_*` flag with a
byte-identical off path.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **3** — a distribution shift on 9 features at once has to be moving the probability level | **4** — the largest *ranking-quality* item that needs no retrain; R5 calls it *"the highest value-per-line change in its report"* | **2** — two small edits | **3** — takes effect immediately on the existing artifact, so constraint 18 requires re-reading Brier and the Value-Edge ROI before default-on |

**Academic backing.** R5-F5 **[BOTH]**, **fully source-verified** and re-confirmed by the citation
audit (*"the unconditional `.fillna(0)` at `ml_model.py:214-218` against `retrain_v2.py:680-684`'s
deliberate NaN preservation — R5-F5 is fully verified"*). Training keeps NaN deliberately:
`retrain_v2.py:680-684` zero-fills `NON_SECTIONAL_FEATURES` and carries the comment *"Phase 2
sectional columns + NAN_PRESERVE_FEATURES intentionally keep NaN (tree models)"*. Inference destroys
it: `ml_model.py:214-218` `pd.to_numeric(...).fillna(0)` — **unconditional**. Even `runs_since_peak`,
which `RTP:2296` carefully sets to `float("nan")` with the comment *"NaN-preserving (tree models handle
missingness)"*, is converted to 0 two calls later. Worse, the 8 sectional primitives are **never
offered at all** — `z_200m` is read at `RTP:2314` only to build `sectional_x_going`, so it hits the
`else` branch and the whole interaction is identically zero in production (R5-F17).

The literature is **[snippet-only]** but consistent: Twala, Jones & Hand 2008 (origin of
missing-incorporated-attribute) and Perez-Lebel et al. 2022 GigaScience 11 (*"adding an indicator to
express which values have been imputed is important for prediction after imputation"*). XGBoost's
sparsity-aware split finding learns a default direction per split, so **a NaN and a 0 take different
branches** — **[recall — unverified; kdd.org blocked]**, but this is standard library behaviour rather
than a research result.

**The compounding effect is what makes this rank so high on HIT.** At ~47% sectional coverage roughly
half of training rows carry a real z-score and half a NaN, while **100% of production rows carry
`0.0`** — and a zero z-score is not "missing", it is "exactly average", the modal value. Every runner
without sectionals is routed down the "average closer" branch instead of the "unknown" branch. R5 adds
the corollary that matters for the roadmap: **this is a strong alternative explanation for the
"sectionals add −0.0005 AUC" result** (`docs/12:349-352`) — the ablation measured the training-side
value of a block production never receives, so *"the result says nothing about whether wiring them
would help."*

**Files.** `server/python/ml_model.py:214-218` (`prepare_features`) and `RTP:2258-2308` (the `feat`
build). Note `RTP:2293`'s `has_sectional_data` is the one thing done right — an explicit binary
availability indicator, clamped in both `retrain_v2.py:686-687` and `ml_model.py:221-222` — and it is
the template for M5. Do **not** touch `retrain_v2.py`'s NaN handling; it is already correct.

**How to measure that it worked.** Metric: (1) paired top-pick hit rate on identical stored races,
flag-on vs flag-off — McNemar, **~2,000–2,500 races** for a 3pp move; (2) AUC and log loss on the same
races; (3) **the calibration Brier and the Value-Edge band ROI must be re-read on the same run**
(constraint 18), because the probability scale moves. **Pre-registered threshold:** default-on only if
paired top-pick hit rate improves by ≥ 1pp with the McNemar test at p < 0.05, **and** reliability does
not degrade. Cheap pre-check that costs nothing: run flag-on over stored races and report the fraction
of runners whose `mlPredictedProb` moves by more than 2pp. If that fraction is near zero the artifact
never learned a missingness split and the item can be closed early; if it is large, the current
production probability has been systematically wrong on roughly half the field.

---

### #14 — C6. `odds_source` and `has_real_market_odds` as explicit indicators

**What.** Two additive contract features and one diagnosis. (a) `odds_source` — an indicator
distinguishing a genuine pre-race quote from a starting price from a synthetic fallback, so the trees
can separate the regimes on their own branch. (b) `has_real_market_odds` promoted from an honesty
field to a feature. (c) A published quantification of how far apart the train-time and serve-time
`market_odds` distributions actually are. **Explicitly not proposed: any backfill.**

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **3** — if the `mw` ladder is a patch over this, fixing it re-frames the anchor | **3** — the model's most important feature is a different random variable at train and serve time | **2** for the indicators; the real fix is structural and retrain-gated | **2** — additive, retrain-gated, inert until the next retrain |

**Academic backing.** R5-F2 **[BOTH]** — the citation audit's verdict is that this is *"the strongest
source-read finding in the document"* and that the SP-as-`market_odds` chain is **fully verified**.
The chain, verbatim from source: `retrain_v2.py:142-144` *"# Odds — sp_odds is the primary odds column
in the view; market_odds is sparsely populated so we fill from sp_odds / `"sp_odds": "market_odds"`"*;
`:557-563` builds `_effective_odds = COALESCE(view.market_odds, sp_odds)`; `:574` assigns it to
`out["market_odds"]`. The COALESCE almost always falls through to SP, because **106,193 of 119,577
rows have no prediction join** (`docs/12:352`). Inference uses the racecard: `RTP:2259`
`feat["market_odds"] = extract_odds(runner) or 0`, and `research/report.md:129-134` establishes those
are **overnight / ~8am** prices. `backtest_v2_metro.py:69,127-131` does the identical COALESCE, **so
the README's 33.7%/−4.2% and 9.9%/+12.3% were themselves produced on SP-derived features.**

R5-F3 supplies the synthetic-odds half: `racing_system_v8.3_mc.py:1763-1778` `infer_market_odds` falls
back to `model_odds`, then `1 / model_prob_dec`, then **the median of the field's odds**, bound at
`mc_api.py:7282` and `:7156-7167` — a model-derived price feeding a feature that feeds a model
adjustment. R5 labels this honestly as *"a bug class, not a research finding"* with no
horse-racing-specific citation. The leakage framing (Kaufman et al. 2012 ACM TKDD 6(4):15; Kapoor &
Narayanan 2023 Patterns 4(9)) is **[snippet-only]**.

**Three downstream consequences this diagnosis explains, none of them proven.** (i) It plausibly
explains the short-price miscalibration the `mw` ladder compensates for — *"a model fitted on SP and
served an 8am price will systematically under-predict short-priced runners, because the 8am price of
an eventual $1.60 favourite is much longer than its SP"*, and the in-code Kelly-audit comment at
`RTP:676-678` reads *"$1-3 horses win 41%, model predicts 17% after blend."* R5 flags this as *"the
most promising thing Phase 3 could test."* (ii) It offers a second explanation for β = 0 in the CL
fit. (iii) It makes the LambdaRank H2H verdict unsafe, since **both arms were trained on SP**.

**Files.** `server/python/retrain_v2.py:142-144`, `:557-563`, `:574` (the COALESCE);
`server/python/refresh_training_view_v2.py` (the view); `RTP:2259` and `RTP:1686/1800/1859/1908`
(where `hasRealMarketOdds` already exists but never reaches the feature vector);
`server/python/relative_market.py:23-27`, whose docstring is the correct precedent — it returns `0`
for unquoted runners *because "0 is out of range for every feature … so tree models can isolate the
'no market' case on its own branch"*. Guardrail 3: extend `relative_market.py`'s discipline, do not
write a parallel one.

**How to measure that it worked.** The **diagnosis** is the deliverable and it is measurable now: on
stored races, join the training row's `market_odds` to the racecard price for the same runner and
publish the distribution of `log(sp/racecard)` by favourite rank. **This is the single highest-value
measurement in this item and it needs no model change.** Then, at the next retrain, an ablation arm
adding `odds_source` on identical folds. **Pre-registered threshold:** if median `|log(sp/racecard)|`
exceeds 0.15 (≈16% price difference) at the short end, the mismatch is declared material and a
Phase-5 ticket is opened to restrict training to rows with a genuine pre-race price. **Forbidden
route, stated for the record:** constraint 24 — *"never backfill 'late odds' from a vendor's
final-odds field into historical training rows."* The defect is already-shipped backfill; the fix is
never more of it.

---

### #15 — A2. Calibrator family swap — temperature first, beta second

> **Lever: `both` (re-tagged by the Phase-4 audit, was `ROI`).** See the † note under §1 and T19 in
> `IMPLEMENTATION_PLAN.md`. The flat-MC argument below *is* the hit-rate channel; the item cannot
> claim it as its headline benefit and be graded as hit-rate-neutral.

**What.** Add two calibrator families beside the existing isotonic, selected by
`STRIDE_CALIBRATOR_FAMILY` (default `isotonic` = byte-identical): **temperature scaling**
(`p' ∝ p^{1/T}` renormalised within race, one scalar fitted by NLL on a holdout) and **beta
calibration** (three parameters on `(log p, log(1−p))`).

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **3** — targets the *sharpness* complaint directly | **1** — temperature is provably rank-preserving, so it **cannot** move hit rate; that is the point | **2** — ~30 lines for beta, one scalar for temperature, no new dependency | **2** — flagged, and the temperature arm is rank-safe by construction |

**Academic backing.** `§A10` (2 of 5 streams, R1-F17 and R2-F10/F11/F12). Kull, Silva Filho & Flach
2017 AISTATS (PMLR 54) + EJS 11(2):5052-5080, **[snippet-only]**, found independently by both streams:
beta calibration *"beats both Platt scaling and isotonic regression in a wide range of settings"* and
crucially **contains the identity map**, *"which is particularly useful to prevent over-calibration and
apply unnecessary adjustments to already calibrated probabilities."* That matters here specifically:
the MC leg entering `calibrate_and_score` has already been through a Plackett-Luce normalisation and a
field renormalisation to 100%, so it is plausibly close to calibrated already — the exact case where a
mis-specified sigmoid would actively damage a good input. Guo et al. 2017 ICML (>5,000 cites,
**[snippet-only]**) for temperature: *"the classification accuracy of the model is not affected by
temperature scaling … it does not change the most-confident prediction."* Niculescu-Mizil & Caruana
ICML 2005 (**[snippet-only]**) for the ~2,000-case threshold below which isotonic overfits — against
STRIDE's **794 `live_model` rows**.

**Two STRIDE-specific arguments that are stronger than the citations.** (i) R2-F10(c), which R2 says it
had not seen stated anywhere: the isotonic step at `RTP:657-661` runs *before*
`mc_spread = max(rawModelProb) − min(rawModelProb)` at `:703-705`. A step-function calibrator mapping
several runners onto the same plateau **mechanically shrinks that spread**, and `mc_is_flat` then
(a) forces all three picks to `low` ⇒ **`0u` stakes**, (b) shifts the MC-spine blend 50/50 → 65/35,
(c) applies the ×0.30/×0.60/×0.85 gradient penalty, and (d) hands the LLM a `max_score + [5.0, 3.0,
1.0]` boost whose top pick then **bypasses every safety filter** (`RTP:883`). *A coarse calibration
artifact can, on its own, turn a normal race into a no-bet race governed by the LLM.* A smooth
parametric map introduces no ties and **cannot** trip this. (ii) Temperature scaling satisfies the
hit-rate clause of the promotion bar by construction, which makes it *"a materially cleaner experiment
than anything currently proposed"* and the natural first A/B primitive for the whole calibration
programme. Caveat carried: temperature is defined on **logits** and STRIDE's engines emit
probabilities; the transfer `p' ∝ p^{1/T}` renormalised within race is **[recall — unverified]** for
that specific algebra — though note it is the same one-parameter power family as C1's de-vig, which is
a pleasing consistency and a reason to implement one helper for both.

**Files.** `server/python/calibration_model.py` (extend `ProbabilityCalibrator` with a `family=`
parameter — guardrail 3 names it as the existing surface, and guardrail 1 forbids replacing it);
`RTP:~574-576` (load) and `RTP:657-661` (apply); I6's `fit_calibrator.py` fits whichever family is selected, still writing to `models/staging/`.

**How to measure that it worked.** Metric: reliability and calibration slope from I5, plus log loss
(the decision-aligned loss, R2-F7) — noting R2-F11's observed tension that *"isotonic outperforms Platt
in ECE and Brier … however log loss results favor Platt over isotonic"*, so report all three and say
which you are optimising. Secondary and mandatory: **the `mc_is_flat` firing rate** (I3 counter 4)
under each family — R2-F10(c) predicts it falls under a tie-free calibrator, and that prediction is
directly falsifiable. **Sample size:** a one-parameter temperature fit is stable on a few hundred
races; beta's three parameters want ~1,000+. **Pre-registered threshold:** adopt temperature as default
only if log loss improves with a bootstrap CI excluding zero **and** the top-1 ordering is provably
unchanged on 100% of stored races (if it changes on even one, the implementation has a bug — this is a
unit test, not a metric). Adopt beta only if it beats temperature on reliability at the same hit rate.

---

### #16 — S3. Commission / venue parameter and segment tagging

**What.** (a) A single `commission_rate` parameter, defaulting to `0.0` (byte-identical), feeding EV
and any future Kelly computation, plus a venue field on settled bets. (b) Tag every output row with
its segment — metro / provincial / country, and jurisdiction. **No gating on either.**

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **3** — R4-F13 computes that commission converts the validated +12.3% band into roughly **+4.1%** at 8% MBR and **+2.1%** at 10% | **1** — a tag and a parameter change no ordering | **1** — one parameter, one field | **1** — default 0.0 is byte-identical; tagging is additive |

**Academic backing.** R4-F13 **[ROI]** — **CONFIRMED by the citation audit** against betfair.com.au/hub:
*"On Australian racing markets the Market Base Rate is either 8% or 10%, depending on the state and
racing code"*, levied on **net market winnings**, with the 10% rate applying to NSW/ACT racing; and the
net-EV table *"reproduces exactly from `p(o−1)(1−c) − (1−p)` with p = 0.099, o = 11.34."* This is one
of R4's better-supported findings. The consequence R4 draws is the important one and it belongs in the
docs even if no code changes: because commission scales with `(o−1)` and the validated band lives at
long prices, **two-thirds to five-sixths of the band's edge is commission at these price levels**, and
the current `2u` stake — which is `1.68×` full Kelly gross — becomes **~5× at 8% MBR and ~9.8× at 10%**,
*"deep past the zero-growth point at 2×."*

R3-F16 supplies the segment half and is much weaker evidence — Betfair AU liquidity figures are
**practitioner forum claims**, with the "$904,000 → $372,000 at Flemington" number flagged as *"a single
unverified forum claim — treat as indicative only."* **Do not use liquidity numbers to justify
anything.** The part that stands on its own needs no citation at all: `examples/backtest_summary.json`
shows the validating backtest ran on **10 metro tracks only** (Caulfield, Caulfield Heath, Doomben,
Eagle Farm, Flemington, Morphettville, Newcastle, Rosehill Gardens, Royal Randwick, Warwick Farm),
while production runs whatever cards are downloaded. **The validated universe and the deployed universe
differ, and nothing in the code marks the boundary** — a scope-generalisation error independent of every
statistical issue in this report.

R3-F15 and R4-F12 add the capacity note, now partly **CONFIRMED** by the audit: Australian Minimum Bet
Limits require operators to accept a bet to lose **$2,000 win / $800 place at metropolitan meetings and
$1,000 / $400 country and provincial** in NSW, with the *"after 9am on the day of the race"* timing rule
confirmed. **Still `[unverified]`:** the 2pm night-meeting variant, the jurisdiction-of-staging rule, the
WA/NT carve-out, the commencement dates and the ~$250 Top Fluc cap. Record the confirmed figures as a
documented capacity assumption; do not build a cap table on the unverified ones.

**Files.** `RTP:955` (`compute_confidence`'s `ev`), `mc_api.py:7637` (the separate EV — read-only here,
do not unify the two definitions), `server/python/shadow_pl_tracker.py` (venue field on settled rows),
`RTP:1245` `store_selections_in_db` (segment tag, additive). Reference for the segment vocabulary:
`server/python/market_efficiency.py:17-23`, which already has a `METRO_TRACKS` set and a `'thin'`
segment — **read it for the vocabulary, do not wire its gating** (see §4).

**How to measure that it worked.** Metric: ROI and CLV split by segment and by venue, from I1/I2.
**Pre-registered threshold:** twelve weeks of tagged output, then report the metro / non-metro split.
If non-metro CLV is materially worse than metro, the scope-generalisation error is real and a gating
ticket becomes justified — **but gating comes later and only on evidence**, per R3-F16's own
instruction (*"tag, do not gate"*).

---

### #17 — C7. One shared helper for the five interaction features

**What.** Extract `fitness_x_distance`, `barrier_x_pace_inv`, `sectional_x_going`,
`class_drop_x_trajectory` and `campaign_run_x_fitness` into a single helper called by both
`retrain_v2.build_feature_matrix` and the inference block, with a self-test asserting the two existing
formulas produce identical output on random inputs before the switch.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **1** — no direct effect | **2** — closes a live train/serve drift hazard on five features | **1** — a helper and a self-test | **2** — guardrail 1 forbids rewriting working logic, so the byte-identical assertion is not optional |

**Academic backing.** `SYSTEM_MAP §7b.10` (source-read, no external citation): the formulas are
duplicated between `retrain_v2.build_feature_matrix` and `RTP:~2306-2319` *"with no shared helper — a
train/serve drift hazard."* R5-F8 shows one has **already drifted**: `barrier_x_pace_inv` is built on
`barrier_advantage`, which is one of the dead-41 (identically zero in training), **and is computed from
a different formula at inference** (`RTP:2312`). So this is not a hypothetical — one of the five is
provably inconsistent today. Guardrail 3 (*"if the system already has a probability conversion, odds
handler, staking module, or config loader — the ticket MUST extend it, never duplicate it"*) is the
policy this item enforces retroactively.

**Files.** `server/python/retrain_v2.py` (`build_feature_matrix`) and `RTP:~2306-2319`. New helper goes
in `server/python/relative_market.py`'s neighbourhood — a small `server/python/feature_interactions.py`
beside it, following the repo's flat role-based layout and `snake_case` naming
(`SYSTEM_MAP §4`). Wire its self-test into `.github/workflows/ci.yml:33-42`, which currently runs eight
module self-tests.

**How to measure that it worked.** Metric: the self-test asserts equality of old and new formulas on
10,000 random inputs, and CI runs it. **Pre-registered threshold:** byte-identical output on all five
features, **except** `barrier_x_pace_inv`, where the two formulas are already known to differ — for
that one the ticket must (a) report the difference, (b) pick the training-side formula as canonical
since that is what the artifact learned, and (c) note that the correction changes inference output and
therefore requires a paired hit-rate check like C3's. **If any of the other four differ, that is a
bug report, not a refactor**, and it should be raised before the switch.

---

### #18 — C5. Repair the three inert context multipliers and the `rawModelProb` ordering defect

**What.** Four related repairs at `RTP:719-729`: read `fitnessReadinessScore` from where `mc_api`
actually writes it; either surface `jockey_momentum_adjustment` in the result dict or delete its
multiplier; rescale `trackBiasPoints` to its true range; and resolve the fact that `rawModelProb` is
rewritten at `:729` **after** `modelEdge` was computed from it at `:697`.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **2** — the current net effect is a uniform ~5% shrink, so removing it moves the level | **3** — restoring three real signals to the score should sharpen the ordering, if the signals are any good | **2** — small edits | **4** — **highest risk-per-line item here**: every downstream raw-probability threshold has been silently tuned against a ~5%-deflated number |

**Academic backing.** None external — this is `SYSTEM_MAP §7b.1-4`, established by exhaustive grep
across producing and consuming modules and **explicitly not verified by execution**. That caveat is
load-bearing: `§9 Q3` records that whether the multipliers are truly inert in production *"cannot be
confirmed without execution"*, and *"a runtime print is the only proof."* The four findings:
(1) `mc_api` writes `fitnessReadinessScore` only nested as `fitnessData.fitnessReadinessScore`
(`mc_api.py:7545`) while `RTP:719` reads it at top level ⇒ always the default 50 ⇒ `fitness_mult ≡ 1.00`;
(2) `jockey_momentum_adjustment` is an mc_api *feature* (`mc_api.py:5848`), never present in the result
dict ⇒ `jockey_mult ≡ 1.00`; (3) `trackBiasPoints` ranges ≈ −25…+45 (`track_bias_points.py:891-916`)
fed into a `/100.0` map designed for 0–100 (`RTP:721-722`) ⇒ near-constant **×0.95**; so all three
"context multipliers" are *"effectively a uniform ~5% shrink"*; (4) `raw_model_pct`, `win_pct` and
`edge_pct` are consequently *"published on three mutually inconsistent scales"*, and every downstream
raw-prob threshold — conviction 15/12/10 (`RTP:836-843`), bet-gate floors 30/15/10
(`RTP:1816/1821/1825`), longshot `raw ≥ 8` — *"silently operates on a ~5%-deflated number."*

R1-F2 compounds it from a different direction: `mlPredictedProb` is **not race-normalised** in
production (`RTP:2323`) while `backtest_v2_metro.py:174-176` race-normalises `model_prob → norm_prob`
and **every** reported metric is computed on `norm_prob`. So those same constants *"were tuned against
numbers the live system does not produce."*

**Files.** `RTP:719-729`; `server/python/mc_api.py:7545` and `:5848`;
`server/python/track_bias_points.py:891-916`.

**How to measure that it worked.** **Step 1 is a print, not a fix.** Add three stderr lines in the
house `[RACE_CTX]` style reporting the realised distribution of `fitness_mult`, `bias_mult` and
`jockey_mult` over a card. That converts `§9 Q3` from grep-inference to fact and costs nothing.
**Only then** repair, one multiplier per flag, and re-read the four affected threshold families on the
same run rather than porting them (the same discipline C1 requires). Metric: paired top-pick hit rate
and reliability, flag-on vs flag-off on identical stored races. **Sample size:** ~2,000–2,500 races
paired for a 3pp hit-rate move. **Pre-registered threshold:** each multiplier ships separately;
default-on only if paired hit rate is non-inferior within 1pp and reliability does not degrade. The
ordering defect (4) should be fixed **last and separately**, because moving the `rawModelProb` rewrite
above the `modelEdge` computation changes `modelEdge` for every runner — which is a C1-scale
re-pricing wearing the costume of a one-line reorder, and guardrail 6 applies to it in full.

---

### #19 — M1. CatBoostRanker `QuerySoftMax` evidence arm

**What.** A second arm in the existing `rank_model.py` harness using
`CatBoostRanker(loss_function='QuerySoftMax')` — the listwise top-1 objective, which *is* the
conditional-logit likelihood with a GBDT index — judged by the criterion already written at
`docs/12:396`. **Evidence only, no pipeline hook, no flag.**

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **2** — indirect, via a better ranking | **4** — the matched loss for a one-relevant-item-per-group problem scored on top-1 hit rate | **2** — a second arm in an existing harness, no new dependency | **1** — evidence-only; constraint 15 is respected by construction |

**Academic backing.** R1-F9 **[HIT-RATE]**. The argument is that `docs/12`'s roadmap item 4 conflated
two literatures and tested the wrong one. LambdaRank optimises **NDCG surrogates**; with
`label_gain=[0,1]` (`rank_model.py:52-66`) its λ-gradients are computed over pairs whose gain
differences are all identical — a pairwise objective on a one-relevant-item problem. The listwise
top-1 loss is a different thing: ListNet's top-1 loss *"reduces to a softmax and simple cross
entropy"* and ListMLE's loss **is** the Plackett-Luce likelihood (Cao et al. ICML 2007, Xia et al.
ICML 2008, both **[snippet-only]**). And the decisive evidence here is **fetched, verbatim**, from
CatBoost's own `ranking_tutorial.ipynb`: *"A special case: top-1 prediction … CatBoostRanker has a
mode called **QuerySoftMax**… We will maximize the probability of being the best document for given
query."* That is Bolton & Chapman's conditional logit with a GBDT index, **in a library already in
`requirements.txt` and already a base learner** (`retrain_v2.py:802`). The KJAS 2024 37(2):239 study
is **CONFIRMED** by the audit (pairwise generally beats pointwise; **CatBoost Ranker best**) and was
verified 3-0/2-1 by the prior pass. R5-F7 arrives independently at *"retest with CatBoost."*

**Two honesty notes that cap this item's rank.** (i) R1 states plainly: *"no published top-1 hit-rate
lift for ranking on racing data was found, so this is a hypothesis for STRIDE's own harness, not a
promised gain."* (ii) R5-F2 makes the previous verdict itself unsafe — the LambdaRank H2H that failed
(`stored 39.7% / favourite 34.4% / ranker 33.6%` on 996 races) had **both arms trained on SP-derived
features**, and the "stored production model" in that H2H was in fact the *imported historical
prediction set*, not the live pipeline. So the failure is real but its interpretation is not settled.
**The advantage of QuerySoftMax over LambdaRank that matters most operationally:** it emits a
**within-race probability**, so if it ever passed the criterion it could enter as a probability
rather than as the "ordering signal" compromise `docs/12 §5.4` was forced into.

**Files.** `server/python/rank_model.py` only — it already trains on the same 113-column matrix with
the same 60/14/14/14 purge-gapped walk-forward and already emits the three-way same-race H2H. Zero
importers, and it must stay that way (constraint 15: *"the ranker stays evidence-only, no pipeline
wiring"*). Run it with the existing `.github/workflows/train-rank-model.yml`.

**How to measure that it worked.** Metric: the **same-race head-to-head** on identical test races
where the stored model covers the full field — that line, not the walk-forward headline, decides it.
`docs/12:378-389` documents exactly why: the headline `36.0% vs 29.5%` was *"baseline weakness, not
ranker strength."* Also report log loss, since QuerySoftMax emits a probability and LambdaRank did
not. **Sample size:** the previous H2H had 996 races; a 3pp difference at that n has roughly a 95% CI
of ±4pp, so **996 races is not enough to conclude a narrow win** — it was enough for the 6.1pp loss
LambdaRank posted. Target ≥ 2,000 H2H races. **Pre-registered threshold, taken unchanged from
`docs/12:370-373` so this cannot be graded on a moved goalpost:** the ranker's holdout top-1 must beat
**both** the market favourite **and** the stored model on identical races, across folds. If it does
not, it stays evidence-only, exactly as LambdaRank did.

---

### #20 — G2. Separate "should we bet" from "how much", and instrument the flat-MC breaker

**What.** (a) Add the `[MC_FLAT]` counter (also in I3) and a `[GATE]` counter for every filter
outcome. (b) Then split the two concerns: `evaluate_bet_candidate` decides bet/no-bet;
`compute_staking` decides size and **may not return zero** — a race the system will not bet must be a
`NO_BET` with a reason, not a `BET` at `0u`.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **2** — removes a hidden gate whose authorising signal is documented as anti-correlated with value | **3** — this is the one staking-adjacent change that genuinely moves hit rate, because it changes the bet denominator | **3** — the instrumentation is trivial; the separation touches the contract | **3** — guardrails 10 and 11 bind; `validate_tips.py` invariants must still pass |

**Academic backing.** This item is the merge of conflict `§C2`, which the Phase-2 document explicitly
asks Phase 3 to resolve *"into one ticket with two acceptance criteria rather than pick a side."*
R4-F17 **[BOTH]** frames it as a staking-boundary violation: `compute_staking` returns `"0u"` for
`confidence == "low"`, so **a `0u` "bet" is not a bet** — if it is still counted in the bet
population, hit rate and ROI are computed over a set including non-bets; if it is not, the staking
function has changed the denominator. R2-F10(c) frames the same code path as a calibration-artifact
hazard (see A2). Both are correct and they are complementary, not contradictory. The trigger is far
broader than "low confidence": `mc_spread < 6.0` (`RTP:705`) forces **all three picks to `low`**
(`RTP:~2466-2469`), so an uninformative simulation zero-stakes the entire race while the LLM's
`[5.0, 3.0, 1.0]` boost (`RTP:~2445`) simultaneously takes over the ordering with `_llm_top_pick`
bypassing every safety filter (`RTP:883`). **A single dispersion statistic flips both the ranking
authority and the entire stake schedule at once, and nobody knows how often** (`§9 Q10`).

The aggravating fact is in-repo and needs no citation: the justification comment at `RTP:950-968`
records that the v1 confidence ladder was **anti-correlated with value** — mean EV **+0.036 for
"high" vs +0.152 for "low"**, n = 330, 2026-04-14. **The system's stake size is keyed to a label whose
own recorded history points the wrong way, and which was demoted rather than removed.**

**Files.** `RTP:950-1015` (`compute_confidence`, `compute_staking`); `RTP:703-705` (`mc_spread`);
`RTP:~2438-2469` (the flat-MC branch); `RTP:1778` (`evaluate_bet_candidate`);
`server/python/validate_tips.py` and `server/python/backfill_tips_contract.py` (the invariants that
must still hold — guardrail 11).

**How to measure that it worked.** **Phase (a) first and separately:** the counter. Metric — the
fraction of races where `mc_is_flat` fires, by field size, over a month. **Pre-registered alarm
threshold: > 15% of races.** R5-F7 adds the sharpest test of *why* it fires — **flat rate by field
size** — because if the model under-separates in big fields, post-hoc renormalisation of a flat vector
yields a flat vector, *"which is literally the `mc_is_flat` failure mode the pipeline has
special-cased throughout."* **Phase (b) only if (a) shows it matters.** Metric — bet hit rate and ROI
recomputed with `0u` rows excluded from the denominator, compared with the current series; the
difference between the two *is* the size of the measurement distortion. Sample size: whatever a month
yields; the comparison is within-sample and needs no power calculation because it is a re-partition of
the same rows, not a hypothesis test.

---

### #21 — A3. Renormalise win probabilities within race after the market anchor

**What.** After `winPercentage` is written at `RTP:693`, renormalise the field to sum to 100 — and
publish the pre-renormalisation sum so the size of the current distortion is on the record.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **3** — if the sum is systematically off 100, every `modelEdge` on the card is biased by a common factor, *"a pure ROI error with zero hit-rate signature"* | **1** — a common divisor cannot change within-race order | **2** — a few lines, but the correct *design* is a within-race operator, not a divide-by-sum | **3** — changes the published probability scale, so every downstream threshold must be re-read |

**Academic backing.** R2-F5 **[ROI]**, STRIDE-specific and verified by grep this session: the three
`winPercentage` assignment sites are **exactly** `RTP:624` (CL path), `:661` (isotonic) and `:693`
(market anchor), **and no renormalisation follows any of them.** Each is pointwise: isotonic is a
per-runner monotone map (a non-affine transform of a simplex point leaves the simplex); the ML blend
mixes in an unnormalised pointwise classifier; and the `mw` ladder applies **a different weight to
different runners in the same race** (a $2.80 favourite gets 0.80, a $12 outsider 0.45), *"so the pool
is not even a convex combination of two normalised distributions."* `mc_api` renormalises at
`~:7612-7623` — but **upstream of all of this**. The structural literature (Harville 1973, Henery
1981, Stern 1990, Bolton & Chapman 1986; Lo & Bacon-Shone's finding that Harville carries a systematic
bias with Henery *"clearly superior"* on Hong Kong data) is **[snippet-only]** throughout and supports
the *principle* that a field's win probabilities are a point on the simplex, not any particular fix.

**R2's own warning against the cheap version must be carried into the ticket:** *"the correct order is
normalise-last, and the normalising operator should be a within-race one (log pool / conditional
logit) rather than a divide-by-sum on an arbitrary pointwise transform."* So a divide-by-sum here is a
**stopgap that buys measurement**, and A4 is the real answer. Note also `mc_recalibration.py:204` is
the only layer in the entire stack that already renormalises per race after calibrating — guardrail 3
says read it before writing a second one.

**Files.** `RTP:568` `calibrate_and_score`, after `:693`. Behind `STRIDE_RENORM_FINAL`, default off.

**How to measure that it worked.** **Measurement precedes the change and is I3 counter 2.** Metric:
the distribution of `Σ winPercentage` per race, and its correlation with field size and with the
proportion of unquoted runners (`RTP:699-701` — unquoted runners never receive the ML blend into
`winPercentage`, so they stay on a different scale, which is a likely driver). **Pre-registered
threshold:** if the median race sums within ±2pp of 100, close this item as immaterial and spend the
budget on A4. If it deviates by more than ±2pp, ship the flag and measure reliability and ROI-surface
shift, **with the explicit expectation that hit rate does not move at all** — if it does, the
implementation is renormalising something other than a common divisor.

---

### #22 — M2. Race-relative *fundamentals*

**What.** Add `<feature>_z` and/or `<feature>_rank` for the 8–10 fundamentals that actually carry
handicapping signal — `weighted_form_score`, `distance_strike_rate`, `course_strike_rate`,
`class_level`, `weight_kg`, `days_since_run`, `consistency_score`, `improvement_score`,
`first_up_win_rate` — computed within today's field, by extending `relative_market.py`.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **2** — indirect | **4** — R5 calls this *"the largest modelling gap after F2/F5"*, and it is the cheapest route to within-race structure that does not abandon the pointwise ensemble | **3** — retrain-gated; the evidence cycle is the cost, not the code | **2** — additive, inert until the next retrain |

**Academic backing.** R5-F6 **[HIT-RATE]**. Lessmann, Sung & Johnson 2010, IJF 26(3):518-536 —
venue/volume/pages and the *"competitive element"* abstract are **CONFIRMED** by the audit, though the
paper's ROI headlines (*"RF 20.26% vs CL 8.84% over 500 races"*, *"1,000 HK races / 12,902 horses"*)
are **`[unverified]`** and must not be quoted. The verified core: *"standard forecasting frameworks are
not designed for modeling the competitive element, whereby a participant's chance of success depends
not only on individual capabilities but also on those of competitors."* R5's audit of the contract is
the strong half and is a source read: the only within-race-relative features are `fair_implied_prob`,
`odds_rank`, `odds_rank_pct` (**market only**), the sectional z-scores (per-race z-scores but of the
horse's **prior** race), and `sectional_rank_at_distance`. **Everything carrying actual handicapping
signal is an absolute level.** The one fetched source doubles as a negative control: the German Benter
replication is a one-stage CL with **no within-race normalisation**, and it earns a thin +6.0% ROI
(914 bets, bootstrap p = 0.0218) — a working but weak edge from the un-normalised form.

**The caveat that must appear in the ticket, verbatim in spirit:** Phase 5 (`docs/12:355-362`) proved
that a relative re-encoding of an *already-present* column is worthless — the trio ranked #3/#6/#11 of
113 by importance and ablated at **−0.0012 AUC**, and the repo's own verdict is that *"nothing should
be promoted on the strength of Phase 5."* The difference claimed here is that the fundamentals are
**not** already present in relative form and their absolute levels are genuinely not comparable across
race classes. **That claim is a hypothesis. The burden of proof is a causal ablation, not importance**
(constraint 36).

**Files.** `server/python/relative_market.py` — extend it (guardrail 3 names it as the ready-made
template); `server/python/retrain_v2.py` `build_feature_matrix` (grouped by the same race key as the
pace and Phase-5 features); `RTP:2251-2257` for the inference-side mirror, following `docs/12 §4a`'s
precedent of keeping both paths identical so `mc_api` needs no edits.

**How to measure that it worked.** Metric: `retrain_v2.run_ablation` with a dedicated arm dropping the
new block on identical folds — a **causal AUC delta**, not importance — plus the walk-forward top-1
hit rate against the market-favourite baseline. **Sample size:** the existing regime is 30 folds over
8,995 races with fold-std 0.044 AUC, so **a delta smaller than ~0.01 AUC is not distinguishable from
noise** on this harness; that is the practical resolution limit and it should be stated in the ticket
rather than discovered afterwards. **Pre-registered threshold:** ablation delta ≥ +0.005 AUC with the
sign consistent across ≥ 20 of 30 folds, **and** paired top-1 hit rate up. Below that, the block is
kept only if it is free (as Phase 5 was) or dropped.

---

### #23 — S2. Shadow price-aware stake column

**What.** Publish, per pick and per race, a `kelly_fraction_shadow` computed as
`f* = EV/(o − 1)`, multiplied by the repo's own `KELLY_FRACTION_DEFAULT = 0.25`, and shrunk by the
runner's own probability uncertainty. **Published, logged, replayed against S1's bankroll path —
never applied to a real stake.**

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **3** — the only lever that moves ROI with zero hit-rate cost, and the current sizing is wrong by ~2.8× across the price range | **1** — staking cannot move hit rate, and a shadow column cannot move anything | **2** — extend `RS:309-326`; the shrinkage input comes free from C4 | **1** — publish-only |

**Academic backing.** R4-F3 **[ROI]**, the agent's own arithmetic on README figures and `RS:132`
(`unit_percent = 0.01`, so `2u = 2%` of bank): the value band's implied mean price is
`o = 1.123/0.099 ≈ $11.3` giving `f*_full = 0.123/10.34 = 1.19%`, against `2u = 2%` — **1.68× full
Kelly**; a $2.50 shot with a genuine +5% EV gives `f*_full = 3.33%`, so `2u` is **0.60×** — a **2.8×
mis-sizing spread, over-betting precisely the runners whose probabilities are least reliable.**
`compute_staking` (`RTP:1007-1015`) **receives no price argument at all** — its only input is
`h["confidence"]`. R4-F4's `c(2−c)` growth identity and the **CONFIRMED** MacLean/Ziemba/Blazenko 1992
figures (full Kelly ≈ 1/3 chance of halving before doubling vs half-Kelly ≈ 1/9; half-Kelly retains
≈75% of growth) justify the fraction. **`[unverified]` and excluded from sizing:** the *"30% Kelly cuts
an 80% drawdown from 1-in-5 to 1-in-213"* figure, and Sun & Boyd's *"±15% ⇒ >1.5×"*.

The shrinkage half is R4-F5 and `§A4`: Baker & McHale 2013 Decision Analysis 10(3):189-199 is
**CONFIRMED** for the venue and the conclusion (shrinkage increases with estimation uncertainty; the
tennis validation), but **the closed form of `k` could not be obtained and R4 deliberately did not
guess it** — so this item ships the *simplest defensible* shrinkage, not Baker & McHale's: R4-F8's
degenerate one-dimensional robust form, **size on the lower end of the probability interval rather than
its centre**, using either the MC Wilson interval (`RS:334`, `ci_alpha = 0.10`, surfaced as
`ciLower`/`ciUpper` at `mc_api.py:7479-7481`) or the engine-disagreement interval
`[min(mc, ml), max(mc, ml)]` — **both numbers already in scope at `RTP:667`.** C4 supplies a third and
better one, `Var_s(q_i)`. R4's summary of the gap is the sentence to put at the top of the ticket:
*"the system measures engine disagreement, uses it to shrink scores, and then stakes as if the
probability were certain."*

**Files.** `racing_system_v8.3_mc.py:309-326` (`kelly_stake`) — guardrail 3 names this and **not**
`portfolio_risk.py`, which is dead and broken at `:235`. Note R4-F16's finding that the existing cap
is decorative: `RS:320` returns `min(max_stake, kelly)` where `kelly` has **already** been multiplied
by `fraction` at `:319`, so with `fraction = 0.25` and `MAX_KELLY_STAKE = 0.05` the cap binds only when
*full* Kelly exceeds 20% of bankroll — for `f* ≈ 1.2%` it can **never** bind. Fix that in the same
ticket. Publication sites: `RTP:1245` (additive column; note `selections.kelly_stake` is already a
**decoy** — `RTP:1334` names it and `RTP:1462` binds `int(pick.get("staking","0u").replace("u",""))`,
the 2/1/0 unit count — so the new column needs a different name and the decoy should be documented).
Also `mc_api.py:7483`'s `kellyStake`, which R4-F16 proves is **structurally always `0.0`** and is
published to an unseen frontend consumer as a constant zero.

**How to measure that it worked.** Metric: replay `stride_tip_results` under (i) the live `2u/1u/0u`
rule and (ii) the shadow fraction, and compare **expected log-growth and maximum drawdown**, not ROI%.
This is exactly the criterion G1 must add, because R4-F14 shows the existing bar would reject the
correct answer. **Sample size:** a growth-rate comparison on a replayed path is not a hypothesis test
about a population mean; report the path with a block bootstrap over race-days for the CI, and require
≥ 500 settled bets before quoting a number. **Pre-registered threshold:** the shadow rule shows higher
replayed log-growth **and** lower maximum drawdown than the live rule over ≥ 500 bets, and CLV (I2) is
positive over the same window. **Explicit prohibition, carried from R3-F14 and R4-F6:** do not apply
it. *"Kelly sized off an edge whose t-stat is 0.43 and whose ROI is non-monotonic in the edge itself
is the exact configuration the literature says produces ruin"*, and *"establishing live ECE must
precede, not follow, any Kelly wiring."*

---

### #24 — M3. Field-size-aware sample weighting

**What.** Replace the scalar `scale_pos_weight = 9` with a `sample_weight` proportional to field size
(or add explicit `field_size × <feature>` interactions), so the trainer is not fitting one average base
rate across all field sizes.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **2** — indirect | **3** — plausible, but the effect size rests on practitioner numbers | **2** — a weight vector | **2** — retrain-gated |

**Academic backing.** R5-F7 **[HIT-RATE]**, and this is the item most weakened by the citation audit.
The structural claim is sound and needs no source: the win base rate is mechanically `1/n`, and
`scale_pos_weight = 9` implies a base rate of `1/(1+9) = 10%`, i.e. calibrated for a ~10-runner field
and **wrong at both tails** — while LGB uses `is_unbalance` and CatBoost `auto_class_weights=Balanced`,
so there are **three mutually inconsistent imbalance treatments in one ensemble**. The *effect size*,
however — top-rated horses winning **37.7% in fields of ≤7 vs 16.6% in 16+** — comes from
`honestbettingreviews.com` and `raceadvisor.co.uk`, flagged by R5 itself as **practitioner, not
peer-reviewed**. Hence Evidence = **W** and a middling rank. R5's own observation is the sharpest part
and it is free: *"the post-hoc renormalisation fixes the sum but not the shape: if the model
under-separates in big fields, normalising a flat vector yields a flat vector — which is literally the
`mc_is_flat` failure mode."*

**Files.** `server/python/retrain_v2.py:774/791/802` (the three treatments) and the `.fit()` calls at
`:739-778`. `field_size` is already a feature, so the interaction route needs only
`build_feature_matrix`. The full fix — a grouped ranking objective — is blocked by constraint 15 and
belongs to M1/X3.

**How to measure that it worked.** **The cheapest possible test comes first and is free:** measure
`mc_is_flat` firing rate **by field size** (I3 counter 4). R5 names this *"the cheapest possible test
of this finding"* — if flat-rate rises with field size, the under-separation claim has support; if it
does not, this item can be closed without a retrain. Then: ablation with `sample_weight` vs scalar on
identical folds, reporting AUC, log loss and **top-1 hit rate stratified by field-size band**.
**Sample size:** stratifying 8,995 races into (say) four field-size bands leaves ~2,000 races each,
which supports a ~5pp hit-rate comparison per band and not much finer. **Pre-registered threshold:**
top-1 hit rate improves in the extreme bands (≤ 8 runners and ≥ 14 runners) without degrading the
middle, and aggregate AUC is non-inferior. If the gain is confined to the middle band, the change is
doing nothing that `field_size`-as-a-feature was not already doing.

---

### #25 — M5. Cheap missing-indicator and shrinkage features (bundle)

**What.** Four small, independent, retrain-gated additions bundled because none justifies a ticket
alone: (a) `career_starts` and a `has_prior_form` indicator; (b) the Glicko-2 rating and deviation pair
as features; (c) weight expressed relative to the field and to weight-for-age rather than raw; (d)
empirical-Bayes shrinkage on the jockey/trainer strike rates.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **1** — no direct route | **2** — real but small; the literature is explicit that jockey effects are the *smallest* modelled component | **2** — each is a few lines; the bundle is a day | **1** — additive, retrain-gated, ablation-tested |

**Academic backing.** R5-F13 (missing indicators — Perez-Lebel et al. 2022 GigaScience and Twala et al.
2008, **[snippet-only]**; *"no academic first-starter study was reachable"*). R5-F10 (jockey/trainer —
Oki et al. 1995 JABG 112(1-6) and Oda et al. 2024 JABG, both **[snippet-only]**: across all 12
racecourse-distance categories *"the race effect had the highest variance component… followed by… the
jockey effect"*, i.e. **the rider is the smallest component**, and a raw strike rate is dominated by
mount quality, so shrinkage toward the population rate is the fix). R1-F15 (Glicko as the cheap version
of the frailty-model route — Silverman 2012/2013 JPM, **[snippet-only]**, with `research/report.md
§2.4`'s standing warning that the paper's headline **36.73% ROI claim was refuted 0-3**; *adopt the
mechanism, not the return*). R5-F12 (weight — the peer-reviewed anchor is thin, effect sizes are
**practitioner**; do not quote them).

The in-repo argument is better than any of it: `has_sectional_data` (`RTP:2293`, clamped in both
`retrain_v2.py:686-687` and `ml_model.py:221-222`) **proves the team already knows this pattern and
applies it correctly — it just was not generalised.** `glicko2_elo.py` is self-tested, in CI, and has
**zero production callers**: a per-horse latent-ability estimate with explicit uncertainty, unused.
`career_starts` is used *inside* `trial_x_experience` but never exposed. And of six barrier-related
features, three are dead, one is distance-only, and one is an interaction with a dead parent (R5-F8).

**Files.** `server/python/form_feature_builder.py` (the as-of-safe producer);
`server/python/retrain_v2.py` `FEATURE_COLUMNS` at `:152-275` **and** `server/python/ml_model.py:65-189`
— the two lists are byte-identical and must stay so; `server/python/glicko2_elo.py` (call it, do not
rewrite it); `RTP:2258-2308` for the inference mirror. **`ml_model.py:250-252`'s `TargetEncoder` is
adjacent and must not be extended** — see §5.1.

**How to measure that it worked.** Metric: `run_ablation` per sub-item on identical folds; each is
independently droppable. **Sample size:** same ~0.01 AUC resolution limit as M2, which means **most of
these will be individually indistinguishable from noise** and should be evaluated as a block.
**Pre-registered threshold:** the block's ablation delta ≥ +0.005 AUC, or it is dropped. Being honest
about the prior: this is the lowest-expected-value non-aspirational item on the list, and it is here
because it is cheap and because the shrinkage half is methodologically right regardless of whether it
shows up in AUC.

---

### #26 — M4. Training-side, as-of-safe computation of the dead columns

**What.** Compute the pace and market-velocity columns **on the training side**, as-of-safe, so that
the ~41 features currently constant-zero in training become real. This is the largest engineering item
that is not aspirational.

| ROI | HIT | Effort | Risk |
|---|---|---|---|
| **2** — indirect | **3** — two of the three feature families the literature ranks highest are advertised and inert | **5** — a training-side, as-of-safe reimplementation of two engines' worth of logic | **3** — retrain-gated, but large surface and real leakage risk if the as-of discipline slips |

**Academic backing.** R5-F4 and R5-F9, `§A7` — **with a correction the citation audit made that
changes the ticket's shape and must be carried.** The original headline (*"41 features identically
zero in BOTH training and inference"*) is **wrong on the inference half**: `mc_api` builds a *different*
feature dict via `extract_all_sophisticated_features` (`mc_api.py:5436` → `:908`, `:1556`) and feeds it
to `RacingMLModel.predict_adjustment` through `calculate_ml_probability_adjustment`
(`mc_api.py:6448-6465`) — the **0.55-weighted** `ml_adjustment` term at `mc_api.py:7379` — and that
path **does** populate several of them (`running_style_score` `:1605`, `is_steam_move` `:2051/:2055`,
`empirical_barrier_advantage` `:1153-1155`). **What survives, and it is the half that matters:** the
training-side claim is independently re-verified — each spot-checked name has **0 occurrences in
`form_feature_builder.py`** and exactly one in `retrain_v2.py` (the `FEATURE_COLUMNS` listing itself),
so they are `np.nan` at `:586-588` and zero-filled at `:680-682`. **A column constant in training
receives no split, so its serve-time value is inert regardless of path.** Hence the audit's
instruction: *"a ticket that says 'wire the 16 market-velocity features into inference' is already
half-done and pointed at the wrong end… Do not let a Phase-4 ticket duplicate `mc_api`'s extractors."*

Why nobody noticed is worth recording as a process finding: the retrain's coverage print
(`retrain_v2.py:1440-1448`) reports non-zero counts for **exactly six structural columns plus the 11
Phase-2 features**, and `--coverage-audit` (`:1266-1301`) iterates only `_ALL_PHASE4_FEATURES` — *"the
evidence would never appear in a retrain log."* Cost framing, stated honestly by R5: zero-variance
columns are not directly harmful to a GBM, *"so the cost is not accuracy — it is that the documented
feature inventory is fiction"*: README and `docs/04` advertise 110 engineered features, the realised
count is **~72 in training**, and the entire market-microstructure story in `docs/04 §2` is inert —
which also means `research/report.md:125` scores STRIDE *"Partially aligned"* on market steam/drift
**on the strength of features that are constant zero.**

**Files.** `server/python/form_feature_builder.py` (where the as-of-safe producers belong);
`server/python/retrain_v2.py` `_compute_pace_features` at `:~500-546` (the thin existing proxy) and
`build_feature_matrix`; read-only references `server/python/pace_modeling.py`,
`server/python/speed_mapping.py`, `server/python/market_velocity.py`, `server/python/market_analysis.py`
— **called by `mc_api` for its own adjustment layer, not to be duplicated.** Leakage discipline: the
LATERAL as-of join at `refresh_training_view_v2.py:252-270` (`AND st.race_date < r.race_date … LIMIT 1`,
strict `<`) is the pattern to copy.

**How to measure that it worked.** **Precondition, non-negotiable:** re-run R5-F4's feature diff over
**both** inference paths first (citation-audit queue item 11) — I3 counter 6 — so the ticket is scoped
to columns that are genuinely dead rather than merely dead on one path. Then: per-block ablation
(pace block, market-velocity block, freshness block) on identical folds, and — because these are
as-of-computed from history — a **leakage check**: the 14-day purge gap in
`retrain_v2.DateWindowSplitter` must be honoured, and the block must not improve AUC when the purge gap
is *widened* (if it does, the as-of discipline has slipped). **Pre-registered threshold:** each block
ships only if its ablation delta ≥ +0.005 AUC **and** the widened-gap check is clean. Given the effort
score, **do not start this before I3 counter 6 has run**; it may substantially shrink the scope.

---

### #27–#31 — The aspirational block

These are grouped because they share a property: **each is blocked on data or infrastructure that does
not exist**, so a Phase-4 ticket for any of them would be a procurement or platform decision wearing an
engineering costume. Each is stated with its blocker and its unblocking condition.

**#27 — A4. Final-stage log pool replacing the `mw` ladder.**
*Scores:* ROI **4** / HIT **3** / Effort **5** / Risk **5**. *Backing:* `§A2` (3 of 5 streams). Ranjan
& Gneiting 2010 JRSS-B 72(1):71-91 is **CONFIRMED** by the audit — the strongest calibration citation
in the document — and its theorem is exactly STRIDE's situation: *any non-trivial weighted average of
two or more distinct, calibrated probability forecasts is necessarily uncalibrated and lacks
sharpness*, with the failure directional (the pool is **under-confident**). STRIDE has **four** linear
pools (`ml_model.py:594`, `RTP:667`, `RTP:692`, `mc_api.py:7393`), **none** recognised in `docs/05 §5`
as part of the calibration stack, and **no recalibration downstream of any of them**. The endorsed
operator is already in the repo: `conditional_logit.py`'s `P_i ∝ exp(α·ln m_i + β·ln q_i)` **is** a
two-expert logarithmic opinion pool. R1-F6 adds that `mc_api` already blends multiplicatively — i.e.
in log space — *while the wrapper blends linearly at the point where money is decided*, so **the
pipeline contradicts itself** and nothing in the docs records it as a decision.
*Blockers, three of them:* (i) constraint 14 — *"Do not flip `STRIDE_CL_BLEND` on this artifact"*;
(ii) the hook is at `RTP:591` where it **replaces the isotonic step**, whereas R2's analysis says its
natural home is replacing the **`mw` ladder at `:692`** — a different slot needing a `--stage final`
artifact; (iii) `prediction_audit` holds **260 rows**, so there is nothing to fit on. And Benter's
pseudo-R² triple (0.1218 / 0.1245 / 0.1396), *the entire quantitative case for the two-stage
architecture*, is **`[unverified]`** — the audit notes the "triple-sourcing" is circular because two of
the three sources are STRIDE restating itself.
*Unblocking condition:* P0-c answered, several weeks of genuine `--stage final` rows, **and** the
refit described in §5.3. *Measurement:* holdout log loss and top-1 hit rate for model-only vs
market-only vs blend — the three-way table `conditional_logit.py --fit` already prints — on ≥ 1,000
races, against the α = 1.296 / β = 0.000 baseline from `docs/12:296-299`.

**#28 — X1. T−5-minute odds snapshot as a feature.**
*Scores:* ROI **3** / HIT **3** / Effort **5** / Risk **3**. *Backing:* R3-F8, `[prior pass, verified
3-0]` for the core cluster — JRA 894,127 runners 2004–2023, horses shortening in the final five
minutes earn significantly higher returns *at identical final odds* (coefficient −0.3386, SE 0.0392);
AU 2006, all 14,854 races, late pool-share ratio predicts net returns (coefficient 4.124, z = 9.79)
while **final prices remain wrong in 5 of 10 favourite ranks**. What this pass adds is that improvement
toward the close is **non-monotonic**, so *"the exploitable window is just before the close, on the
direction of the move, not at the close itself"* (**[snippet-only]**). Already `docs/12` roadmap item 5;
**promoted within the aspirational bucket** for a second reason — it is also the reference price a
properly de-vigged CLV needs. *Blocker:* prospective collection infrastructure that does not exist,
plus constraints 23/24 (**prospective only, assert snapshot time < jump time; never backfill**).
*The nuance that prevents wrong sequencing, from R3-F8 verbatim:* **"for CLV measurement no new
collection is needed — SP is already stored. Only late-odds-as-a-feature needs the new infrastructure.
Do not let the harder half block the easy half."** That is why I2 is #2 and this is #28.

**#29 — X2. Sectional coverage above ~47%.**
*Scores:* ROI **2** / HIT **3** / Effort **5** / Risk **2**. *Backing:* R5-F11 — STRIDE's as-of join is
*"a genuine strength"* and verified leak-free, but it uses `LIMIT 1`, i.e. **a single prior race's
z-score**, which is a high-variance estimator of a noisy quantity, and coverage is ~47%. Two sub-items
are cheaper than the purchase and should be split out: **(a) a recency-weighted average over the last
N sectional observations instead of `LIMIT 1`** — the repo already knows this pattern
(`avg_market_diff_3runs`, `speed_rating_trajectory`), it just was not applied here; **(b) C3, which
matters more than either, because production currently receives none of this block at all.**
*Blocker:* Punting Form is a purchase (~85% AU TAB coverage, history to Oct 2012, `[prior pass,
verified 2-0]`, with **QLD coverage still unverified**), and QLD is an **access** decision that
constraint 20 explicitly rules out solving in code.

**#30 — X3. Exploded rank-ordered target / RUMBoost-class within-race fitting.**
*Scores:* ROI **2** / HIT **4** / Effort **5** / Risk **4**. *Backing:* R1-F1 — Bolton & Chapman used
the **rank-ordered ("exploded") choice set** procedure, decomposing each race's full finishing order
into *d* independent choice sets. The venue, the 200-race sample, the *"recently developed procedure for
exploiting the information content of rank ordered choice sets"* and the *"side constraint eliminating
long-shot betting"* are all **CONFIRMED** by the audit; only the track-level split is `[unverified]`.
R1's *"label starvation"* framing is the argument: with ~8,995 races and one positive each, the
pointwise trainer sees ~9k informative events, where an exploded target yields (fieldsize−1) nested
comparisons per race — *"an order of magnitude more signal from identical data."* `race_results_history`
holds the full order (45,070 rows backfilled); **the view and the target do not.** R1-F10's RUMBoost
(GBDT inside the utility function, Salvadé & Hillel 2024, **[snippet-only]**) is the fallback, not the
first try — R1 says so explicitly, because M1 reaches the same mathematical form with an existing
dependency. *Blocker:* a rebuilt training view emitting finishing order, and a target change that is
squarely a research project.

**#31 — X4. Drawdown-constrained or distributionally-robust Kelly with a daily budget.**
*Scores:* ROI **3** / HIT **1** / Effort **5** / Risk **4**. *Backing:* R4-F7 (Busseti, Ryu & Boyd 2016,
**[snippet-only]**) and R4-F8 (Sun & Boyd 2018 — the paper is **CONFIRMED** but its headline *"±15% ⇒
worst-case growth >1.5×"* is **`[unverified]`**, so **do not size a ticket on it**). R4-F10 supplies the
part that is actually pressing and is *not* aspirational: **no daily exposure control exists on the live
path**, and the two off-path caps are mutually contradictory — `STAKING_CONFIG['max_daily_units'] = 30`
at `unit_percent = 0.01` means **30% of bankroll per day** (`RS:131-132`), while
`portfolio_risk.py:61` sets `max_daily_exposure_pct = 15.0`. **One says 30%, the other 15%, neither
runs.** For ~10–20 value-band bets at `f* ≈ 1.19%`, the quarter-Kelly *total* is ≈3–6% of bankroll.
**R4's instruction is the right Phase-4 action and it is not code:** *"Resolve the 30%-vs-15%
contradiction first — that is a documentation/decision task."* Fold that single decision into G1.
*Blocker for the rest:* bankroll history (S1), a solver (`cvxpy` is not in `requirements.txt`), and the
prohibition on enabling Kelly at all until the measurement work lands.

---

## 4. What NOT to do

Fourteen tempting changes the evidence says are traps. Several are tempting *because* they would show
up quickly in a metric — which is the point.

**1. Do not loosen a filter to raise hit rate.** This is the failure mode the whole system was built
against and `docs/12:22-27` says so: *"the honest route to a higher hit rate is **not** looser filters."*
The arithmetic in §0.2(d): the marginal bet added by a loosening is by construction the worst in the
set, so ROI falls; and the direction determines everything else — loosening at the short end raises
strike rate and lowers ROI (the 33.7% / −4.2% corner), loosening at the long end lowers **both**.
Constraint 27 forbids the long end independently, and R3-F5 adds a second, statistical reason that is
stronger than the FLB one: at σ = 3.396 **longshot returns are unmeasurable in any realistic sample**,
and *"a system that cannot measure a bet class should not bet it."* Note this cuts against a naive
reading of R1-F11 too — Chapman's >20% came from a **model-probability floor** (p̂ < 0.04), which
*tightens*, not loosens.

**2. Do not chase the +12.3%.** It is 142 bets, 14 wins, **t = 0.432**, one-sided p ≈ 0.33, **95% CI
[−43.5%, +68.2%]**; it flips sign on two horses out of 352 races (13 winners → +4.3%, 12 → −3.7%,
11 → −11.7%); it is **the best of six strategies**, and its t is *below the average maximum you would
get from six coin flips* (expected max-t 1.265 under the null); within the same price band ROI is
**non-monotonic in edge** (+40.1% at edge 3–5% vs −14.8% at edge ≥5%); and per R4-F2 **the live gate is
not even that band** — it thresholds a de-vigged edge where the backtest thresholded a vig-inclusive
one, making it looser by 3.3pp at $5/R = 1.20. All **[derived]**, all reproducing to the decimal per the
citation audit. It is not an argument that the value philosophy is wrong — the market anchor is
independently well-motivated — but **that number cannot defend any specific threshold**, and no
document should quote it again without its CI.

**3. Do not stack another calibrator.** STRIDE has six calibration layers and one that can fire. R2's
verdict is the sentence to remember: *"STRIDE's problem is not too few calibration layers — it has six
— it is six calibrators and four uncalibrated linear pools, and the pools are where the probabilities
actually get made."* The remedy is **one** correctly-placed, cross-fitted calibrator (A1), not a
seventh. And do not read guardrail 12 as forbidding A1: R2-F17's reframing is that *pooling* is the
provably harmful composition and *stacking with disjoint fits* is the safe one, so the rule as written
**blocks the safe composition while leaving three linear pools in a row unguarded**.

**4. Do not enable Kelly.** Not at quarter, not at any fraction, until I1/I2/I5 have run. R3-F14:
*"Kelly sized off an edge whose t-stat is 0.43 and whose ROI is non-monotonic in the edge itself is the
exact configuration the literature says produces ruin."* R4-F6: *"establishing live ECE must precede,
not follow, any Kelly wiring."* And note the current position is already aggressive: `2u = 2%` of bank
is **1.68× full Kelly** on the value band gross, and **~5× at 8% Betfair MBR, ~9.8× at 10%** — past the
zero-growth point at `2f*`, where *"wealth tends to zero almost surely even though the edge is genuine."*

**5. Do not backfill anything to fix R5-F2.** Constraint 24: *"never backfill 'late odds' from a
vendor's final-odds field into historical training rows."* The SP-as-`market_odds` defect **is**
already-shipped backfill; more of it is not a fix. Constraint 25 similarly forbids using Tier-2 modules
(barrier-bias tables, `sectional_quant`, track profiler) to backfill training rows.

**6. Do not add a model class, and do not replace the GBMs with a neural network.** R1-F13: model class
*"has never been the differentiator; race-relative formulation has"*, and it is *"the dimension the
literature says matters least"* — while it is where STRIDE spent its complexity budget (3 GBMs +
stacking + double calibration + 4 MC engines). R1-F14: Grinsztajn et al. 2022 NeurIPS 35:507-520,
tree-based models remain state of the art at ~10K samples. R1's practical instruction: **reject tickets
that add model classes.**

**7. Do not wire `market_efficiency.py` or resurrect `portfolio_risk.py` on plausibility.** Both are
modules of exactly the right *shape* with zero importers, which is precisely the temptation. Constraint
36 (the Phase-5 precedent) requires a causal ablation, not a plausible story. And `portfolio_risk.py`
is broken in a specific way: `:235` computes `variance += (stake ** 2) * odds * (1.0 - 1.0 / odds)` at
`q = 1/odds` — the **market-implied** probability — while using the model probability for EV at `:234`,
*"so the Sharpe ratio at `:237` divides a model-based EV by a market-based standard deviation."*
R4-F7: *"do not resurrect `portfolio_risk.py` without fixing `:235` first."*

**8. Do not wire the LambdaRank ranker, and do not read feature importance as evidence.** Constraint
15 stands: *"the ranker stays evidence-only, no pipeline wiring."* M1 proposes a **new evidence arm**,
not an override, judged by the same criterion. And constraint 36 is the general form: the Phase-5 trio
ranked #3/#6/#11 of 113 by importance and ablated at **−0.0012 AUC** — *"importance proves the trees
use the encoding, not that it adds skill."*

**9. Do not flip `STRIDE_CL_BLEND` on the current artifact.** Constraint 14, and the reason is
provenance, not design: the artifact was fitted on `training_view_v2.predicted_win_prob`, which the
coverage report showed is **overwhelmingly imported `training_data` predictions** of unknown generating
stage — so *"the train/serve match that would make it safe to enable is not established."*

**10. Do not use the `[unverified]` numbers to size anything.** Named explicitly by the citation audit:
Benter's R² triple, Benter's "500–1000 races" minimum, Sun & Boyd's ±15%/>1.5×, MacLean's 1-in-213,
Štrumbelj's market-size claim, Lessmann's RF-vs-CL ROI pair, Carriero's "all scenarios", the
Kelly↔Bregman quotation, Roth's `d_z = 1.38`, Bolton & Chapman's track split, and the MBL secondary
conditions. Additionally: **the Walsh & Joshi magnitudes are withdrawn pending re-fetch** (a corrigendum
revised *all* results) — so +34.69%/−35.17%, +36.93%, −75.9%, the 4.46%/5.03% ECE pair and the
64.62%/64.27% accuracy pair must not appear in a ticket. **The direction survives; the magnitudes do
not.**

**11. Do not relax the one-bet-per-race contract to chase within-race Kelly.** R4-F9 is explicit —
*"do not propose in Phase 4"* — and the contract is guardrail 10 (*"no hidden substitutes"*), asserted
by `validate_tips.py`. The gap is latent and the correct action is to **record it as a known bound**,
plus the warning that `portfolio_risk.optimize_stakes` treats mutually exclusive runners as
independent and sums variances with **no covariance term**.

**12. Do not attempt to bypass the QLD Cloudflare challenge.** Constraint 20: *"the above-board path is
official RQ industry data access … **not an escalating challenge-bypass** on production
infrastructure."*

**13. Do not conclude "we need more data."** R1-F18: STRIDE has ~8,995 races and 119,577 view rows,
against published, holdout-validated, profitable models built on 200 (Bolton & Chapman; Edelman on
**Australian** data), ~938 (Koker), 1,000 (Lessmann) and ~2,000 (Benter, Chapman) races. Its evaluation
discipline — a 14-day purge gap, 30 folds — is *"ahead of most published work."* The binding constraints
are **specification** issues costing no new data. Conflict `§C4` reconciles this with R5/R3's data asks:
the one genuine data gap is *a different kind* of data (the T−5 snapshot, sectional coverage), not more
of the same — and CLV, the highest-value measurement, needs **none**.

**14. Do not tidy the conventions.** `SYSTEM_MAP §4` and guardrail 5 are explicit: the two JSON key
conventions (`camelCase` on the MC/engine boundary, `snake_case` in the published tips document) are
**two different contracts — do not unify them**; Australian and US spellings coexist deliberately
(`race_normaliser.py` beside `normalize.py`) — match the file you are editing; generations are
suffixed, never renamed in place; *"Consistency > elegance."* An unseen TypeScript frontend consumes
`mc_api`'s stdin/stdout JSON and the `selections` table (`§9 Q18`), so a "cleanup" is an unversioned
API break.

---

## 5. Questions Phase 2 assigned to Phase 3

The Phase-2 document closes with a re-verification queue and four unresolved conflicts, several
addressed to Phase 3 by name. Three are settled here by source reading or algebra; two are shown to be
un-settleable without data and are converted into measurements.

### 5.1 The `TargetEncoder` question — RESOLVED, and the alarm was wrong

R5 nominated this as *"R5's highest-value unresolved question"* and said *"Phase 3 should do it
first"*: whether `ml_model.py:250-252`'s `TargetEncoder` over
`['jockey','trainer','track','going','race_class']` (`smoothing=10.0, min_samples=5`) is fitted
fold-wise or on the full training set, *"if the latter it is classic target leakage."*

**It is neither, and it is not target leakage. [re-verified here, source read.]**
`ml_model.py:257` calls `self.target_encoder.fit_transform(training_data, target=target_col)`, and
`target_encoding.py:128-131` is `fit_transform → self.fit(df); return self.transform(df,
is_training=True)`. `transform` with `is_training=True` (`target_encoding.py:70-114`) implements
**leave-one-out**: for each row it computes `loo_sum = sum_i - y[i]`, `loo_n = n_i - 1`, requires
`loo_n >= self.min_samples`, applies the smoothing formula
`(loo_n·loo_mean + smoothing·global_mean)/(loo_n + smoothing)`, and adds Gaussian noise at
`noise_sigma = 0.01`. **A row's own label is explicitly excluded from its own encoding.** That is the
standard LOO target-encoding scheme, correctly implemented. R5's flagged danger does not exist, and
this item should be **struck from the queue** rather than carried into Phase 4.

**Two smaller, real defects surfaced while checking, and they belong in the C3 ticket, not a new one.**

1. **The encoded columns are dead at inference — the same defect as R5-F5, on five more columns.**
   Exhaustive grep: the **only** `target_encoder` call anywhere is `fit_transform` at `ml_model.py:257`;
   there is no `.transform(...)` on it (the other `.transform(` sites at `:275`, `:276`, `:553` are the
   `StandardScaler`). Meanwhile `ml_model.py:388` sets
   `self._trained_feature_columns = list(X.columns)` — with the comment *"Persist the exact
   training-time column set (incl. any target-encoded columns)"* — and `prepare_features`
   (`:208-223`) iterates that list and, for any column absent from the incoming data, executes
   `features[col] = 0` at `:218`. **So `jockey_encoded`, `trainer_encoded`, `track_encoded`,
   `going_encoded` and `race_class_encoded` carry real values in training and are identically `0` at
   every scoring and every evaluation call** — and `0` is far outside their trained range, which sits
   near the ~9.7% global mean. Exactly R5-F5's pattern, on five columns nobody counted, because they
   are not in the 113-column contract and so do not appear in R5-F4's diff.
2. **The `train_test_split` inside this trainer is random, not temporal:** `:270-272`
   `train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)`. Roth's Class IV result — that
   boundary leakage is *"invisible under random cross-validation"* (**[snippet-only]**, confirmed on a
   second search) — applies directly.

**Scope, which is what keeps this from being an emergency.** `retrain_v2.py` — the producer of the
live 113-column contract — contains **no `TargetEncoder` and no `_encoded` column** (grep: zero hits).
The affected path is `RacingMLModel.train`, called by `train_ml.py:251`, `ml_model.train_from_database`
(`:727`) and — the one that matters — **`walk_forward_backtest.py:394`**, which is the harness
`SYSTEM_MAP §3` rates most rigorous and `docs/12:431` nominates for *"leakage-safe AUC/ECE deltas with
CIs."* That harness therefore trains models carrying five columns which are informative at fit time and
constant zero at scoring time. The effect is to *degrade and distort* its reported metrics rather than
inflate them, but either way **`walk_forward_backtest.py`'s numbers are not measuring the production
trainer**, and any ticket that pre-registers a threshold on that harness needs to know it.

### 5.2 Conflict C1 — the direction of the de-vig bias, resolved as far as algebra can take it

The Phase-2 document calls this *"the most important conflict in this document"* and says **Phase 3
must resolve it before any de-vig ticket is written**, because the two readings imply opposite
predictions about which price band carries false-positive edge.

**The premises are identical, and the audit confirms the underlying result.** R1-F7 says proportional
de-vig *"understates the fair probability of favourites and overstates it for longshots"*; R2-F13 says
it *"overstates the longshot's fair probability and understates the favourite's."* **Those are the same
sentence.** Štrumbelj 2014 is **CONFIRMED** and Clarke et al. 2017 is **CONFIRMED verbatim** on
normalisation's named defect: it *"does not account for favourite long-shot bias."*

**The algebra settles the disputed step in favour of R1/R3.** R3-F10's derivation is explicit and
reproducible: since `calibrated = mw·raw + (1−mw)·true_market`,
**`modelEdge = calibrated − true_market = mw·(raw − true_market)`**. Holding `raw` fixed, `modelEdge`
is **decreasing in `true_market`**. A favourite whose `true_market` is biased *low* therefore gets a
`modelEdge` biased **high** — inflated — and a longshot whose `true_market` is biased *high* gets one
biased **low**. R2's stated conclusion (edge biased *against* favourites) does not follow from R2's own
premise. **On the arithmetic, C1 resolves for R1/R3.**

**But the conclusion does not transfer to live data, and the reason is in the repo.** The derivation
holds `raw` fixed, and STRIDE's own in-code Kelly audit (`RTP:676-678`) says `raw` is badly biased at
exactly the price band in question: *"$1-3 horses win 41%, model predicts 17% after blend."* If `raw`
is *also* too low at short prices — and by a margin of tens of percentage points, far larger than any
plausible de-vig bias of a few points — then `(raw − true_market)` is a difference of two
same-signed errors and its net sign is **indeterminate from the code alone**. Worse, there are now
**three** candidate mechanisms for the same short-price symptom, and they are not distinguishable
without measurement:
(i) proportional de-vig's residual FLB (this conflict);
(ii) `scale_pos_weight`-style inflation of `mlPredictedProb` (`§A5`);
(iii) **training on SP while serving an 8am price** (R5-F2), under which a model *"will systematically
under-predict short-priced runners, because the 8am price of an eventual $1.60 favourite is much longer
than its SP."*

**Verdict for Phase 4.** The algebraic dispute is closed; the empirical question is open and is now a
*measurement*, not a debate. It is I3 counter 9 (tabulate `true_market` under all three methods by
price band) plus I3 counter 3 (mean `mlPredictedProb` vs observed) plus C6's `log(sp/racecard)`
distribution. **Those three diagnostics together separate the three mechanisms, and no de-vig ticket
should be written before they have run** — which is the direct reason C1 is ranked #11 and not #1
despite being the most-converged finding in Phase 2.

### 5.3 The conditional-logit refit — a one-argument precondition

Citation-audit queue item 10, and it changes the diagnosis of `§A8`. `conditional_logit.py:134-135` is
`minimize(nll, x0=np.array([0.5, 0.7]), method="L-BFGS-B", bounds=[(0.0, 5.0), (0.0, 5.0)])` — **β is
box-constrained to `[0, 5]`, so the reported `β = 0.000` is a corner solution on the lower bound, not
an interior optimum.** That is what you observe whenever the unconstrained MLE is ≤ 0. Market
contamination of the fundamental arm (R1-F12) is one explanation; a genuinely **negative** optimum —
the market term entering with the wrong sign because the model probability already embeds SP (R5-F2) —
is another, **and the two imply different fixes.** Nothing in the repo distinguishes them. **Refit with
`bounds=[(0.0, 5.0), (-5.0, 5.0)]` and report β's sign before any work on A4, C6 or `§A8`.** One
argument; costs an Action re-dispatch.

### 5.4 Conflict C2 — merged, not adjudicated

R2-F10(c) (a coarse calibrator trips the flat-MC breaker and hands ranking to the LLM) and R4-F17
(`low → 0u` makes the sizing function a covert selection rule) describe **the same code path and two
different harms**. Per the Phase-2 instruction, they are merged into **G2** with two acceptance
criteria — instrument the firing rate first, separate selection from sizing second — and A2's
tie-free-calibrator arm carries R2's half. Neither side is picked.

### 5.5 Conflict C3 — the price band, deferred to the ROI surface

R3-F11 (the `$2–$15` band is defensible on FLB grounds; do not loosen), R3-F12 (FLB is a **context
effect**, so a fixed price band is the wrong parameterisation — Meyer & Hundtofte, *Management Science*
69(11):6954-6968, **CONFIRMED verbatim** by the audit) and R1-F11 (the filter's *shape* may be wrong;
Chapman's >20% came from a **model-probability floor**, **CONFIRMED near-verbatim**) are all defensible
and **not reconcilable a priori.** R3-F12's own tie-break protocol is adopted unchanged: get I4's
(odds-decile × edge-decile) ROI surface running, re-cut it by field size and price dispersion, and only
then decide. **Nothing about the band changes in Phase 4.** The `$4–$15` vs `$2–$15` mismatch between
`tips_day_aggregates.py:~91-103` and the validated band (`§9 Q16`) should be *documented* as an open
question in the same pass, not silently reconciled.

---

## 6. Disposition of the existing roadmap (`docs/12 §5`)

Per the brief: items already implemented are not re-proposed, and items already on the roadmap are
promoted, demoted or refined against the new evidence.

| `docs/12 §5` item | Status there | Disposition here | Reason |
|---|---|---|---|
| **1. Fit and enable the CL blend** | #1 by expected return | **DEMOTED to aspirational (A4, #27), and REFINED in three ways** | (a) β = 0 is a **corner solution on a box constraint** (§5.3) so the "market adds nothing" reading is unestablished; (b) R5-F2 shows the fitted quantity embeds SP; (c) `§A2` says the right slot is the **`mw` ladder at `RTP:692`**, not the isotonic step at `:591`. Constraint 14's hold stands, and `prediction_audit` has 260 rows. |
| **2. Retrain with Phase-5 features** | roadmap | **DONE, verdict negative — closed** | Causal ablation **−0.0012 AUC**; kept *"to avoid churn"*, and `docs/12:359-362` itself says *"nothing should be promoted on the strength of Phase 5."* Redirect the retrain budget to C3, M2, M3. |
| **3. Race-selectivity gate** | done; "measure with shadow tracking" | **HELD — the remaining half folds into I1** | The gate is shipped and ordering-only (constraint 16). The outstanding work is *measurement*, which is I1's job; do not fit the meta-model before there is something to fit it against. |
| **4. LambdaRank ensemble member** | criterion FAILED, evidence-only | **REPLACED by M1 (#19), not re-run** | R1-F9: the failed experiment tested an **NDCG-surrogate pairwise** objective on a one-relevant-item problem. The matched loss is the listwise top-1 softmax — `CatBoostRanker(loss_function='QuerySoftMax')`, **fetched verbatim** from CatBoost's own tutorial, in a dependency already present. Same criterion, same harness, constraint 15 intact. |
| **5. Close-to-jump odds snapshot** | roadmap | **PROMOTED within aspirational (X1, #28), and SPLIT** | Second, independent reason: it is the reference price a de-vigged CLV needs. But R3-F8's nuance is decisive — **CLV itself needs no new collection** (SP is already stored), so the easy half ships now as I2 and the hard half stays aspirational. |
| **6. Close the tipster-accuracy loop** | done, opt-in | **HELD — measurement folds into I1** | `STRIDE_ACCURACY_WEIGHTS` is shipped and bounded (`ACCURACY_CLAMP = (0.75, 1.25)`). The on-vs-off comparison needs I1. Note `docs/11:150` is stale on this point (drift D-3) and should be corrected. |
| **7. Finish `weather_api.py`** | roadmap | **DEMOTED — do not do this next** | `weather_api.py` has **zero importers**. Going does matter (R5-F17: `going_miss` is a named failure mode in the repo's own 21-day autopsy), but the cheap repair is `sectional_x_going`, which is *identically zero in production* because its first term is — and **C3 fixes it for free**. A weather feed is the expensive way to buy a signal the contract already has and discards. |

**Two `docs/12` claims that should be corrected in the same pass**, both from `SYSTEM_MAP §7`:
`docs/12`'s framing of "model within-race" as purely an *architecture* problem (R5-F6 shows it is at
least as much a *feature* problem, and the feature route is far cheaper); and the note at
`retrain_v2.py:151` still saying the contract has *"(77 total)"* when the list below it has **113**.

---

## 7. Recommended Phase-4 sequence

Not a fourth ranking — the dependency order implied by everything above, stated once so it is not
reconstructed from prose.

```
P0-a/b/c/d  ── operator answers, minutes ──────────────────────────────┐
   │                                                                    │
   ├─► I1 · I2 · I3 · I4 · I5 · I6 · G1 · S1        (quick, parallel)   │
   │        │                                                            │
   │        ├─► I3 counter 9 + counter 3 + C6 diagnosis                  │
   │        │        └─► resolves §5.2 ──► C1 (de-vig)                   │
   │        ├─► I3 counter 2 ──► A3 (or close it as immaterial)          │
   │        ├─► I3 counter 4 ──► G2, then A2's tie-free arm              │
   │        ├─► I3 counter 6 ──► scopes M4 (may shrink it a lot)         │
   │        ├─► I5 ──────────► A1 ──► A2                                 │
   │        └─► I2 + S1 ─────► S2 (shadow only)                          │
   │                                                                     │
   ├─► C2 · C4 · C7 · S3        (independent, ship any time)             │
   ├─► C3 ──► (fixes sectional_x_going for free, closes roadmap item 7)  │
   ├─► C5   (print first, repair second, reorder last)                   │
   ├─► M1   (evidence-only, independent of everything)                   │
   └─► next retrain: C6 indicators · M2 · M3 · M5 ──► ablations          │
             │
             └─► §5.3 CL refit ──► A4 (aspirational)
```

**The single most important property of this sequence:** nothing in the second half is worth starting
until the first row has run, because until then every threshold in `SYSTEM_MAP §6` remains what
Phase 1 called it — *unfalsifiable in production*.

---

*End of Phase 3 deliverable. 31 ranked items across three buckets; 3 Phase-2 questions resolved by
source reading or algebra; 2 converted into measurements; 7 prior roadmap items dispositioned. No
`.py` or `.sql` file was modified — this document is the only file written.*

# Independent Audit — Claude Code "Repo Wiring" Plan vs `stride-racing` @ `51d2950`

**Repo:** https://github.com/Sageabdallah/stride-racing
**Audited at:** HEAD `51d2950072eb8e312b4fb9472321f0da70ddb8ef` (= `origin/main`, fetched 2026-09-07) — matches the commit the plan claims to have verified against.
**Method:** every factual claim re-checked by reading the code at that commit; quantitative claims recomputed; the liveness audit and full test suite re-run locally.

---

## Verdict summary

The plan is **exceptionally accurate**. Every load-bearing claim reproduces at the stated file and (within ±3 lines) the stated line. The two headline "don'ts" — don't treat this as a coding project, don't flip level-shifting flags before re-deriving gates — are both correct and both supported by the repo's own governance documents. I found **no fabricated files, flags, functions, or numbers**.

I did find: (a) two claims whose exact numbers don't reproduce (test counts), (b) three ROI-relevant facts in the repo that the plan **missed**, and (c) one nuance that tempers the plan's vig math. Details below.

---

## Claim-by-claim verification

### 1. "Bet gate is blind to the vig" — ✅ VERIFIED, exact

`run_tips_pipeline.py:1294` `compute_confidence` — confirmed. The code's own comment (:1300-1313) states verbatim what the plan claims: in legacy "devigged" mode `ev = calib/true_mkt − 1` is "algebraically the same test as `edge > 0.0`", the HIGH gate "reduces to `edge > 1.0pp` at every price band", and break-even in a 118% book is "6.1pp at $2.50 and 3.0pp at $5.00, against a gate asking for 1.0pp".

Math independently recomputed: at $2.50 in a 118% book, devigged market prob = (1/2.5)/1.18 = 33.9%; break-even requires calibrated ≥ 40% → edge ≥ **6.1pp** ✓. At $5.00: **3.0pp** ✓.

Caveat I add (the plan and the repo both use proportional-vig): the favourite–longshot bias literature shows bookmakers load the overround disproportionately onto longshots, so the *actual* vig borne at $2.50 is less than proportional — 6.1pp is an upper bound. The defect direction is unaffected: the gate still cannot see whatever vig it is charged. And note the repo's own open item A3 (`docs/roi-roadmap/00-evidence-base.md`): `selection_ledger.py` defaults commission to 0.0, and the "at_price" EV formula (`calib × odds − 1`, :1316) is **gross of Betfair commission** — net break-even is higher again (~8–10% MBR removes two-thirds to five-sixths of gross edge at ~$11 average winners, per the repo's own arithmetic).

### 2. "Trained signal zero at serve — 68 LIVE_BOTH, 4 ZERO_AT_SERVE" — ✅ VERIFIED, reproduced

I re-ran `feature_liveness_audit.py` at this commit: **68 LIVE_BOTH, 4 ZERO_AT_SERVE**, and the four are exactly the winner-pattern columns: `prior_pb_close_underreaction`, `cohort_fast_close_prior`, `pos400_win_prior`, `jockey_wet_residual`. `winner_pattern_features.attach_features` is imported in exactly one production location — `retrain_v2.py:1961`. Grep confirms no serve-path import; `serve_features.py` contains no reference to any of the four names.

**What the plan undersells:** there is a *larger* dormant block. Per the pipeline's own comment (`run_tips_pipeline.py:2959-2963`) and `docs/research/FEATURE_PROVENANCE.md`, **15 trained features carrying 25.45% of the model's importance mass** are plumbed only under `STRIDE_SERVE_LIVE_FEATURES` (default OFF). The static audit counts these LIVE_BOTH (the plumbing code exists, behind a flag), so at runtime the model is actually serving constants for 19 of 68 features, not 4. The promotion machinery already exists (gate 3 in `gate_status.py`, `docs/roi-roadmap/shadow-flip-criteria.md`, ≥5 clean shadow days required) — the plan's step 6 ("the 3 safe ZERO_AT_SERVE columns") should be folded into that gate-3 track rather than treated as the whole feature-wiring job.

### 3. "NaN contract off at serve" — ✅ VERIFIED, exact

`ml_model.py:246-264`: `prepare_features` zero-fills anything not in `NAN_PRESERVE_SET`; the preserve set is active only under `_serve_nan_contract_enabled()` (:79-83), default OFF. The plan's gloss — "'no sectional data' becomes z=0, i.e. exactly field average" — is definitionally correct for z-scored columns (z=0 is the field mean). This matters because all three boosters (XGBoost/LightGBM/CatBoost) learn a default routing direction for missing values during training; serving 0 instead of NaN sends every missing runner down the *learned-present* path with an exactly-average value, which is a different input from the one the trees were fitted on. The repo's own fold-hygiene comments show the trainers intended missingness routing (`retrain_v2.py:593`: "Sectional (Phase 2) features keep NaN so tree models exploit missingness").

### 4. "ML calibrators fitted, not applied — raw 40% vs calibrated 14%" — ✅ VERIFIED, exact

`ml_model.py:69-76`: `_ml_apply_isotonic_enabled()`, default OFF. The 40%→14% figure is the repo's own execution check, quoted verbatim in `docs/analysis/AUDIT_VERIFICATION_2026-09-06.md` (H1): raw ensemble mean **40%**, calibrated **14%**, on a 15% base rate, under the trainer's own `scale_pos_weight=9` / `is_unbalance` / `auto_class_weights="Balanced"` settings. That document also records the caveat that existing artifacts may carry only XGB+LGBM calibrators (CatBoost's `__getstate__` drops `_isotonic`) — which `ml_model.py:754-766` handles by serving raw and saying so once. So flipping the flag on an old artifact is a *partial* no-op; the plan should note a retrain is a prerequisite for the flag to do anything.

### 5. "Field probabilities don't sum to 100" — ✅ VERIFIED, exact

`run_tips_pipeline.py:993-1000`: `STRIDE_RENORMALISE_FIELD`, default OFF; `_renormalise_field` at :1107-1143 with `[RENORM_TIER_TRANSITION]` logging. Confirmed.

### 6. "Train sees SP, serve sees morning" — ✅ VERIFIED, exact

`retrain_v2.py:153-172`: `STRIDE_TRAIN_ODDS_SOURCE` default `"legacy"` (SP-filled). `filter_snapshot_rows` (:175-190) has deliberately **no SP fallback** and `sys.exit(2)` on an empty frame — the plan's leakage rule #4 ("never backfill a snapshot") is the code's own design. `gate_status.py` gate 1 requires ≥20 distinct capture days since DAY_ZERO (2026-08-02), earliest window 2026-08-30, recommended 2026-09-13 — i.e. the plan's "4–6 weeks of snapshot rows" is literally the gate's definition ✓.

### 7. Blending math — ✅ VERIFIED, arithmetically exact

- `ml_w` = 0.20 (odds ≤ 3) / 0.40 (else) — `run_tips_pipeline.py:956` (plan said :955-957).
- `mw` ladder 0.80 / 0.70 / 0.50 / 0.45 / 0.40 / 0.30 — :969-980 (plan said :966-982).
- `modelEdge = calibrated − true_market = mw·(raw − true_market)` — :987, algebraically exact.
- Combined ML share of published probability `ml_w × mw` across bands: 0.16, 0.28, 0.20, 0.18, 0.16, 0.12 → the plan's "12–28%" is the exact min–max ✓.

### 8. Re-levelling trap — ✅ VERIFIED, with one refinement

Absolute-pp gates confirmed at :2486-2504 (odds<3: edge<4 or prob<30; odds≤5: edge<2.5 or prob<15; else edge<3 or prob<10) and the HIGH tier at :1335-1336. The plan's central warning — flipping `STRIDE_ML_APPLY_ISOTONIC` (a 40%→14% level shift) silently rewrites every one of these constants — is correct and is the single most important sentence in the plan.

**Refinement the plan missed:** `evaluate_bet_candidate` reads `prob = max(raw_prob, calibrated_prob)` (:2479). The probability sub-gates therefore read the *higher of two different scales* — one market-anchored, one not. Two consequences: (a) today, the prob gates are looser than they read, because the raw number (inflated by the uncalibrated ML arm) can pass them alone; (b) after a level fix, `max()` partially masks the shift, so measured gate behaviour will move *less* than the level shift — which will look like "the fix did nothing" unless the re-derivation (plan step 5) models both arms. This strengthens, not weakens, the plan's step ordering.

### 9. "Already fixed in code, switched off, flag-off-byte-identical" — ✅ VERIFIED

All four flags exist with default-OFF semantics (`_flag_enabled`, :478-480) and dedicated test files that **pass** in my environment: `test_flag_plumbing.py`, `test_gate_repair.py`, `test_ml_serve_calibration.py` (28 tests incl. "FlagOffIsLegacy" byte-identity cases), `test_renormalisation.py`. The claim "not a coding project, an evidence-and-promotion project" is fair — *except* for the 3 safe ZERO_AT_SERVE columns, which genuinely require new serve-path code (the computation doesn't exist on the serve side at all).

### 10. Measurement layer / CI claim — ✅ VERIFIED

`docs/roi-roadmap/00-evidence-base.md` states verbatim: 142 bets, 14 winners, 95% CI **[−44%, +68%]**, z = 0.43, best of 6 strategies on one 6-week window, P(some band shows +12% | no edge) = 80–93%. The plan's "you cannot currently tell whether a fix helped" is the repo's own position. CLV-significance "~400 bets vs 3,000–5,000 for ROI" is the repo's own estimate (repeated in `00-evidence-base.md:26`, `01-ledger-clv-net-settlement.md:10`, `12-preregistration.md:98`) and is consistent with the external evidence that CLV is far less variance-dominated than realised ROI.

### 11. Ops claims — ✅ VERIFIED (±1 line)

- `infra/jobs/handler.py:36-41`: `_load_secrets()` pulls `stride/prod` from Secrets Manager via `os.environ.setdefault` ✓. (Nuance the plan's step 1 should encode: `setdefault` means **container env beats the secret** — the diagnostic must print both layers and which won.)
- "Nothing in the repo dumps the resolved flag set" — independently confirmed by grep; no such diagnostic exists ✓.
- tips-proof forces `STRIDE_LEDGER_WRITE/ODDS_SNAPSHOT_WRITE/MC_AUDIT_WRITE/SERVE_LIVE_FEATURES_SHADOW` off at handler.py:1041-1044 (plan said :1042-1045) ✓.
- `[EV_GATE_TRANSITION]` lines print at :1366-1374, function defined :1350 (plan said :1349 — off by a line).
- `compare_candidate_tips.py:18-21` carries the dispatch-time confound caveat the plan quotes ✓.

### 12. Leakage machinery & rules — ✅ VERIFIED, all five rules correct

- `form_feature_builder.py:716-917`: as-of loaders with strict `< cutoff` SQL (e.g. `race_date::date < %(cutoff)s::date`); monthly bucket cache at :993-1021 ("strictly before that month boundary… zero leakage from the row's own month forward") ✓.
- `retrain_v2.py:770-780`: `DateWindowSplitter(purge_gap_days=14)` ✓; plus the tail-carve hygiene (:876-937) that moved early-stopping/isotonic fitting off the test fold — better than the plan describes.
- `winner_pattern_features.py:9-13`: `prior_pb_close_underreaction` is disabled in its own docstring because "the researched definition used the runner's **own-race final SP**" — the plan's "one of your four ZERO_AT_SERVE features is the leak" is verbatim correct. Wiring it to fix the wiring would be the worst move available ✓.

### 13. Caveats in the plan — ✅ accurate, one discrepancy

- `.claude/skills/` absent ✓; `tipster_panel.json` absent (only referenced by `panel_liveness.py`/`consensus_agent`) ✓.
- Tests: plan says **1183 passed, 8 failed (booster-missing env)**. At the same commit in a clean sandbox (no xgboost/lightgbm/catboost) I get **1151 passed, 2 failed, 1153 collected**. The two failures are `test_job_postconditions.py` filesystem-mtime cases (environment, not repo). No booster failures reproduce here — the suite degrades gracefully — and the total differs by 38 tests. Neither number is "wrong" for its container, but treat the exact counts as environment-specific; the substantive claim (failures are environmental, not defects) holds.

---

## What the plan missed (all verified in-repo)

1. **The intelligence override bypasses every gate the plan wants to fix.** `evaluate_bet_candidate` returns BET at :2469-2471 *before* the edge gates if `_check_intelligence_override` (:2401-2449) passes: rank-1 + `intel_bonus ≥ 3.0` + `franking_score ≥ 55` + non-DECLINING trajectory. That path requires **no positive edge at all** — it skips even the `edge ≤ 0` hard floor (:2483) and would be untouched by `STRIDE_EV_GATE_AT_PRICE`. Its "self-calibrating" claim is asserted in the docstring, not measured. For an ROI audit this is a hole in the perimeter: before promoting any EV gate, count how many live bets route through the override, and log the would-be EV on override bets (shadow) so the override earns its exemption.
2. **`prob = max(raw_prob, calibrated_prob)` (:2479, also :2508-2511)** — mixes scales inside the bet gate; see §8. Any threshold re-derivation that ignores this will mispredict the post-fix gate behaviour in both directions.
3. **The at-price EV formula is gross of commission.** `:1316` `ev = calib × odds − 1` charges no commission; the repo's own roadmap (A3) says net-of-commission is unmodelled and removes most of the gross edge. Shadow results for `STRIDE_EV_GATE_AT_PRICE` will therefore *overstate* surviving HIGH tips. The `[EV_GATE_TRANSITION]` log should carry a net-of-commission EV column before anyone reads transitions as break-even truth.

## Compliance / leakage review of the plan's own recommendations

- Steps 1–4 are read-only or shadow-mode; consistent with the repo's standing rule that flips are deliberate human acts on bound evidence (`shadow-flip-criteria.md`). No leakage introduced.
- Step 6 (wire the 3 safe ZERO_AT_SERVE columns): safe **only** if implemented to the training definitions exactly — `cohort_fast_close_prior` and `pos400_win_prior` are cohort aggregates and must exclude the race being predicted (plan rule #3); `jockey_wet_residual` must stay wet-going-gated. Copy the existing shadow pattern (`_write_serve_liveness_shadow`, :1464) as the plan's rule #5 says. ✅ plan compliant.
- The plan nowhere suggests relaxing the as-of boundary, backfilling snapshots, or touching the purge gap. Its five leakage rules are the right ones for this codebase. ✅
- One addition: any re-derived gate thresholds (step 5) must be validated on a **disjoint forward window**, per the repo's own A4 pre-registration rule — otherwise re-deriving thresholds on the same window that motivated them recreates the garden-of-forking-paths problem the evidence base documents.

## Recommended priority (ROI/strike-rate lens, repo-correlated)

1. **Print the live flag state** (plan step 1) — zero risk, gates everything.
2. **Confirm ledger + tip_time snapshot accrual** (plan step 2) — CLV at ~400 bets is the only near-term significance path; the repo's ROI number is noise until then.
3. **Shadow `STRIDE_EV_GATE_AT_PRICE`** (plan step 3) — highest expected ROI of any single flag; add the commission column to the transition log first.
4. **Instrument the intelligence override** (my addition) — before any gate promotion, so the promoted gate actually governs the bet flow.
5. **Probability-level flags one at a time** (plan step 4) with thresholds re-derived *first* (step 5), modelling the `max(raw, calib)` gate mix.
6. **Feature wiring via the existing gate-3 machinery**: the 3 safe ZERO_AT_SERVE columns *and* the 15-feature `STRIDE_SERVE_LIVE_FEATURES` block (25.45% importance mass — the larger prize).
7. **`STRIDE_TRAIN_ODDS_SOURCE=snapshot` last** (plan step 7), when gate 1's calendar opens.

*Bottom line: the plan's evidence is real, its ordering is right, and its caution is warranted. The adjustments above are additive, not corrective.*

---

# Round 2 — Verification of Claude Code's rebuttal (same commit `51d2950`)

Claude Code replied with five rebuttals of this audit and several new claims. Each was re-verified against the code. Four of its five rebuttals are correct and are conceded; one does not reproduce. Its headline new finding (the flag-blind liveness tool) is verified and important; its "18 constant features" table is wrong in a specific, correctable way.

## Rebuttals Claude wins (conceded)

1. **Commission default.** `selection_ledger.py:46-55` `default_commission_rate()` defaults to **0.08**, not 0.0. My audit quoted `docs/roi-roadmap/00-evidence-base.md` item A3 as if current — the doc is stale (task 01 landed 2026-08-01). Conceded.
2. **Net-of-commission is modelled — just not in the gate.** `portfolio_risk.ev_at_price(prob, odds, commission_rate)` at `:562` (commission scales the winning branch only) is used by `selection_ledger.py:199` and `staking_controls.py:166` with the 0.08 default. Only `compute_confidence`'s at-price EV (`run_tips_pipeline.py:1316`) is gross. So the fix is wiring an existing helper into the gate — cheaper than my "add a net-EV column" framing. Conceded; the surviving recommendation is narrower: route `:1316` through `ev_at_price(..., default_commission_rate())` (shadow first).
3. **Test count.** `tests/test_ml_serve_calibration.py` has **14 tests**, all passing. My "28" was a three-file combined run misattributed to one file. Conceded.
4. **Isotonic application is all-or-nothing.** `serve_calibration_status()` (`ml_model.py:738-752`): `applied = enabled and complete`, `complete = present and not missing`. A missing CatBoost calibrator on an old artifact means all three models serve raw (with one loud stderr notice, `:754-766`) — a **full** no-op, not the "partial" I wrote. Conceded; conclusion unchanged (retrain first).
5. **De-vig methods exist in-repo.** `market_prob.py:110 devig_probabilities_pct_shin` plus power, selectable via `STRIDE_DEVIG` (`:151-155`, default `proportional`). My favourite–longshot caveat should have pointed to the switch. Conceded — with the repo's own guardrail noted: the docstring forbids ad-hoc flips pending the task-09 pre-registered comparison, so "proportional" is what the EV gate's break-even math currently lives under.

## Rebuttal that does not reproduce

**"test_fold_hygiene.py and test_ensemble_combiner.py have no booster skip guards; they fail without boosters."** In a clean sandbox with xgboost/lightgbm/catboost all absent, at this commit: `test_fold_hygiene.py` → **8 passed**; `test_ensemble_combiner.py` → **15 passed**; full suite → **1151 passed, 2 failed, 1153 collected** (both failures are `test_job_postconditions.py` filesystem-mtime cases). The files collect and pass — my "1153 collected" is the complete set, not a degraded collection. Claude's `ValueError: need at least one array to concatenate` is real but lives elsewhere: `retrain_v2.py`'s own CV path (`np.column_stack([])` at `:1240` when `present_models` is empty, `np.concatenate(prior_y)` at `:1251`) — i.e. **running the retrain script with zero boosters crashes unclearly**. That's a genuine (minor) robustness gap in `retrain_v2`, not a property of the named test files. Claude's container plainly had a different partial-booster environment; neither of our sandboxes is CI. Both test-count claims should be treated as environment-specific.

## Claude's new finding — verified, and it's the best catch of the round

- `serve_features.py:399` `if serve_live_features_enabled() or force_live:` gates the whole plumbed block (:400-420) ✓
- `feature_liveness_audit.py` contains **zero** references to flags (grep count 0) — it cannot express "assigned only behind a default-off flag" ✓
- I pulled the audit's own JSON: all 14 flag-gated features report `LIVE_BOTH`, and for the 8 sectionals the *only* serve evidence is `:418` — inside the gate ✓
- So the repo's tripwire tool reports green on 8 features that serve constant 0 in production. The tool's docstring does warn that ASSIGNED verdicts need human spot-check, but nothing marks flag-conditional evidence. The proposed one-line fix (annotate flag conditionality in verdicts) is the right first move of step 6.

## Where Claude's "18 of 72 constant" table is itself wrong

The denominator is right (FEATURE_COLUMNS = 72; the "68" in `retrain_v2.py`'s comment is stale) and my "19" was wrong. But the table's middle row misstates the runtime state of the 6 pace/context features on the tips path:

- `run_tips_pipeline.py:2913-2916` runs `compute_race_context(...)` and does `runner.update(race_ctx)` **before** `build_feature_row` at `:2989`. `race_context.py:152-160` returns all six keys. So with the flag OFF, `runner.get(k, 0.5)` (`serve_features.py:387-391`) picks up **live race-level values, not constant 0.5**. The 0.5 default only fires when `compute_race_context` throws (`:2921-2923`).
- The accurate defect for those 6 is **train/serve definitional skew**, not absence:
- `leader_advantage` / `closer_advantage`: training (`retrain_v2:519-578`) is **per-runner, signed** (−0.10 / +0.15 / ±0 … by the runner's own style); serve (`race_context.py:113-114`) is **race-level, unsigned** (`1 − pps`, `pps`) — identical value for every runner in the race and never negative. The trees trained on a signed per-runner feature receive an unsigned race-level one. This is the worst of the six.
- `pace_pressure_score`: training `(leaders+on_pace)/fs` from form-string classification; serve `min(1, (2·leaders+on_pace)/fs)` from a different classifier. Correlated, not identical.
- `barrier_relevance_score`: serve applies a ×1.3 tight-track multiplier (`race_context.py:124-128`) that training (`retrain_v2:703-706`) lacks — serve emits 0.91 / 0.52 / 0.26, values the trees never saw. Claude's "never-seen value" instinct is right; the value isn't 0.5, it's these.
- `market_efficiency_flag`: serve adds a metro-track gate and a 0.0 bucket (`race_context.py:140-150`) absent from training's class-only buckets. `field_size_context`: definitions match ✓.
- On the **mc_api path** (`overlay_shared_columns`, `serve_features.py:466-497`, called at `mc_api.py:6128`), Claude's "constant 0.5" **is** right: `extract_ml_features` sets none of the six contract keys (it sets a differently-named `pace_pressure` percentage), so the merged dict falls through to the 0.5 default.

Net count correction: **12 features constant at serve** (8 sectional → 0; 4 winner-pattern → 0) + **6 live-but-definitional-skewed** on the tips path (constant 0.5 on the mc_api path). Not 18 constant — but the skew on leader/closer_advantage is arguably worse than a constant, because it moves.

Consequence for the plan neither side stated: flipping `STRIDE_SERVE_LIVE_FEATURES` does not merely "activate" the pace trio — it **replaces** the race_context definitions with `compute_race_pace` definitions at serve. The shadow comparison therefore measures (definition swap + sectional activation) together; the shadow criteria should record the pace-source delta separately, or a tier flip can't be attributed.

## Claude's "cheaper fix" claim — half right

`run_tips_pipeline.py:1528-1542` does query `sectional_times` with `race_date < %s` (as-of-safe) on the serve path ✓ — but it runs inside `enrich_with_db`, which is called on the **top 3 only**, **after** ML scoring (:3224), and fetches only 6 of the 8 sectional columns (no `rsi`, no `trip_cost_seconds`), landing in narrative-only keys (`prior_z200`). The genuinely reusable asset is `fetch_prior_sectionals` (:1426-1461): full-field, all 8 columns, same as-of rule — already written and shadow-wired. The fix is cheap, but via that function, not via the :1528 query.

## Small verifications

- Interaction-parity self-contradiction: verified. `serve_features.py:86-98` docstring "default ON since the task-03 comparison" (reads `"true"`) vs stale inline comment above `:433` "Default OFF: enabling changes which horses are tipped." Real doc defect.
- `_intel_bonus` stamped at `:1048` (mult at `:1047`) and logged at `:1049` ✓ — override-bet instrumentation is indeed mostly present, as Claude said.
- The liveness JSON evidence pattern confirms Claude's "only serve evidence is lines 400-420" for the 8 sectionals exactly; for the pace six the audit also credits `compute_race_pace` (:255-294) — still flag-gated code.

## Revised net picture (both rounds)

| Group | Count | Tips path, flag OFF | mc_api path | Training |
| --- | --- | --- | --- | --- |
| Sectional (z_200m…trip_cost_seconds) | 8 | constant 0 (NaN seeded, legacy zero-fill) | constant 0 | prior-start z-scores |
| Pace/context (6) | 6 | **live but definition-skewed** via `race_context` (:2916) | constant 0.5 | per-runner/formula variants |
| Winner-pattern | 4 | constant 0 | constant 0 | real priors |
| Everything else | 54 | live | live | — |

**Priority after round 2:** (1) flag-state diagnostic; (2) ledger/CLV accrual; (3) shadow the at-price EV gate *after* wiring `:1316` to the existing `ev_at_price(..., default_commission_rate())`; (4) instrument the intelligence override; (5) fix the liveness tool's flag-blindness (one-line, unblocks honest evidence for everything below); (6) feature wiring under gate 3 — now scoped as 8 sectionals + 6 definition-alignments + 3 safe winner-pattern columns, with the pace-definition swap recorded as its own shadow variable; (7) train-side snapshot odds when gate 1's calendar opens.

---

# Round 3 — Verification of Claude Code's empirical self-audit (same commit `51d2950`)

Repo re-cloned fresh this round (sandbox had wiped /tmp); HEAD confirmed at `51d2950`. Every claim below re-verified against source or executed in controlled environments.

## Headline: my Round-2 test dispute was WRONG — conceded to Claude

Claude's no-booster counts **reproduce exactly**. Built a clean venv with **sklearn/pandas/numpy/pytest present, xgboost+lightgbm+catboost absent** (verified by import checks before running):

```
tests/test_fold_hygiene.py       5 failed, 3 passed   (8 total)   ← Claude said 5 failed, 3 passed ✓
tests/test_ensemble_combiner.py  1 failed, 14 passed  (15 total)  ← Claude said 1 failed, 14 passed ✓
failure: ValueError: need at least one array to concatenate       ← Claude's quoted error ✓
stderr:  WARNING: xgboost not available / lightgbm / catboost
```

My Round-2 "8 passed / 15 passed, does not reproduce" was an artifact of my sandbox having the boosters installed. Both results are environment-specific, as Claude said — but theirs is the one that matches a booster-free environment, and my "not a property of the named test files" conclusion was wrong: the `retrain_v2` CV concatenation gap (empty `present_models` → `np.column_stack` at `:1239-1240`) **is** reached through those two test files. Conceded in full.

**But Claude's discriminator speculation is refuted.** They wrote "the likely discriminator is sklearn, not the boosters." Decisive control run in the main sandbox — **sklearn present, lightgbm present only (no xgboost, no catboost)**:

```
23 passed   (both files, all tests)
```

sklearn was present in every run on both sides; the pass/fail flips purely on **booster presence** — and a single booster is enough (one array makes `np.column_stack` valid). The discriminator is the boosters.

**Extra context neither side stated:** the repo already has `tests/test_ml_model_without_boosters.py`, which **passes** in the no-booster venv (1 passed) — it asserts the `ML libraries not available` degradation for `ml_model`. So the no-booster guard exists for `ml_model` but not for `retrain_v2`'s CV path. That is the precise shape of the robustness gap.

Claude's "first line of output: `ML libraries not available: No module named 'xgboost'`" — string confirmed at `ml_model.py:32` (`print(f"ML libraries not available: {e}")` on booster `ImportError`). Both test files import `ml_model` inside test functions (`test_fold_hygiene.py:215`, `test_ensemble_combiner.py:84/219/256/293/322`), so the print fires in these runs; whether it is literally the first line depends on pytest capture flags. Consistent, invocation-dependent.

## Claude's two concessions — verified accurate

1. **Pace/context live on tips path** — `run_tips_pipeline.py:2913-2916` calls `compute_race_context(...)` then `for runner in runners: runner.update(race_ctx)`, before `build_feature_row` at `:2989`; the `except` fallback at `:2921-2924` sets `race_ctx = {}`, which is the only route to the 0.5 defaults. (Claude cited :2912-2916 and :2921-2923 — off by one line, substance exact.)
2. **`enrich_with_db` is the wrong reuse target** — defined `:1510`, called `:3224` on `top3` only; its query selects exactly **6 columns** (`z_200m, z_400m, z_600m, z_800m, lambda_decay, svi` — no `rsi`, no `trip_cost_seconds`) into narrative keys (`prior_z200`/`prior_z400`). `fetch_prior_sectionals` at `:1426` is full-field, returns all 8 keys, one query per race, docstring names `STRIDE_SERVE_LIVE_FEATURES`. All confirmed.
3. **Devig governance caveat** — `market_prob.py:150-153` docstring: "never flip it ad hoc (task 10 guardrail)". Confirmed; the Shin switch is in-repo but governance-gated.

## The quantified skew table — every number verified exact

| Claim | Verification |
| --- | --- |
| Training init `np.zeros(len(df))` | ✓ `retrain_v2.py:510-511` (Claude: 509-511, off by one) |
| Training buckets `−0.10/+0.12`, `+0.15/−0.05`, else 0 | ✓ `:533-537` — `pps>=0.4 → (pps, −0.10, 0.12)`; `pps<=0.15 → (pps, 0.15, −0.05)`; else `(pps, 0.0, 0.0)` |
| Per-runner by style, ×0.6/×0.4 mid-style variants | ✓ `:570-578` — `style >= _STYLE_ON_PACE → leader_adv`; `<= _STYLE_OFF_PACE → closer_adv`; mid-style gets `c_adv*0.6` / `l_adv*0.4` |
| Training range ≈ [−0.10, +0.15] | ✓ follows from the bucket constants |
| Serve `1 − pps` / `pps`, range [0,1], race-constant | ✓ `race_context.py:114-115` — one value per race, stamped on every runner |
| `barrier_relevance_score` training `1.0/0.7/0.4/0.2` | ✓ `retrain_v2.py:704-706` |
| Serve ×1.3 tight-track → `1.0/0.91/0.52/0.26`; 3 of 4 buckets novel | ✓ `race_context.py:117-128` (`min(1.0, base*1.3)`); 0.91/0.52/0.26 never occur in training |
| `market_efficiency_flag`: serve adds `is_metro` gate + 0.0 bucket, absent from training | ✓ serve `:139-151` vs training `:711-714` (class-only `{1.0,0.7,0.5,0.2,0.4}`, no metro, no 0.0) |
| `field_size_context` identical both sides | ✓ training `(f/16).clip(upper=1)` :708 ≡ serve `min(1.0, f/16.0)` :135 |

Claude's characterization — "out of the trained range, opposite sign convention, race-constant where training was per-runner... materially worse than a zero-fill, because it moves and it is confidently wrong" — is analytically sound. Signed agreement.

## FEATURE_COLUMNS = 72 — verified, and the stale-doc trail

`ast.literal_eval` on both files (independent re-run): `ml_model.py` → **72 entries, 72 unique**; `retrain_v2.py` → **72 entries, 72 unique**. Stale docs confirmed: `docs/analysis/SYSTEM_MAP.md:103-104` "**113 entries**, no duplicates... byte-identical in two places" and `:579` "113 entries, identical in both"; `retrain_v2.py:219` comment "68 columns after the task-12" (68 + 4 winner-pattern = 72 — Claude's reconciliation arithmetic checks).

## mc_api arm — verified

`pace_pressure_score` / `barrier_relevance_score` / `field_size_context` / `market_efficiency_flag`: **0 occurrences each** in `mc_api.py` ✓. `leader_advantage`/`closer_advantage` appear only inside `predict_race_shape` (def `:5284`, values `:5292-5357`) and `get_race_shape_advantage` (`:5559`) — mc_api's internal narrative dict, never the ML contract ✓. So "18 constant" holds for the mc_api arm and not the tips arm; the answer differs by arm, matching the Round-2 table in this report.

## One numerical slip in Claude's favor-claims

"the four winner-pattern names appear... **3 times** in `ml_model.py`" — actual count is **4**: one occurrence per name at `ml_model.py:185-188` (`prior_pb_close_underreaction`, `cohort_fast_close_prior`, `pos400_win_prior`, `jockey_wet_residual`), all inside the contract declaration. Zero occurrences in `run_tips_pipeline.py` / `serve_features.py` / `mc_api.py` ✓. Substance right ("declared, never assigned at serve"), count off by one.

## Standing items re-measured

- `feature_liveness_audit.py`: `grep -ci flag` = **0** ✓ — flag-blindness confirmed again.
- `compute_race_pace`: def `serve_features.py:245` ✓; exactly one production call, `run_tips_pipeline.py:2970`, and it sits inside `if _live_on or _shadow_on:` (`:2968`) — i.e., behind `STRIDE_SERVE_LIVE_FEATURES`(+shadow) ✓. The definition-swap insight and the shadow-attribution requirement stand.
- Sectional "constant 0" on the tips path: with both flags off, `serve_features.py:382-383` seeds `NaN` from `NAN_PRESERVE_SET`, and `prepare_features` (`ml_model.py:246-264`) legacy zero-fills — constant 0 at the model ✓.

## Priority re-scope — endorsed

Claude's only priority change: move the **6 pace/context definition-alignments** up because they are "live and feeding the model out-of-range values every race, on both arms, today... the only item doing active harm rather than withholding benefit." Agreed, with one refinement on the fix path: the flag-ON block's pace trio is documented as mirroring `retrain_v2` exactly (`serve_features.py:392-395` comment), so the alignment fix **is** the `STRIDE_SERVE_LIVE_FEATURES` path — validate in shadow first, recording the pace-source delta as its own variable (Round-2 requirement), then flip. Patching `race_context.py`'s definitions to match training is the alternative for the flag-off arm, but that edits a shared narrative function and needs its own blast-radius check. Either way: definition alignment before any other feature work, and no threshold re-derivation can be trusted until the model is scoring in-distribution values.

**Updated priority:** (1) flag-state diagnostic; (2) ledger/CLV accrual; (3) shadow at-price EV gate wired through `ev_at_price(..., default_commission_rate())`; (4) instrument the intelligence override; (5) liveness tool flag-awareness; **(6) pace/context definition alignment — 6 features, live harm, via shadowed flag flip with pace-source attribution;** (7) sectional wiring (8) + safe winner-pattern (3) under gate 3; (8) train-side snapshot odds when gate 1's calendar opens.

## Round 3 scorecard

| Claude claim | Verdict |
| --- | --- |
| No-booster counts 5F/3P + 1F/14P | **Correct — reproduced exactly; my Round-2 dispute withdrawn** |
| Failure = `need at least one array to concatenate` via empty `present_models` | Correct — reproduced, mechanism confirmed |
| "Likely discriminator is sklearn, not boosters" | **Refuted** — lightgbm-only + sklearn run: 23 passed; discriminator is booster presence |
| Pace/context live via `:2916` merge; 0.5 only on exception | Correct (concedes to Round-2 finding here) |
| `enrich_with_db` top-3-only, 6 columns, post-scoring | Correct, exact |
| All quantified skew numbers (buckets, ranges, multipliers, metro gate) | Correct, exact |
| FEATURE_COLUMNS 72/72 both files; SYSTEM_MAP "113" stale; retrain "68" comment stale | Correct, exact |
| mc_api: 4 keys absent, leader/closer only in `race_shape` | Correct, exact |
| Winner-pattern names "3 times" in ml_model | Substance correct; count is **4** (`:185-188`) |
| Liveness audit zero "flag" | Correct, re-measured |
| `compute_race_pace` single production caller inside flag block | Correct, exact |
| Priority re-scope (pace/context alignment moves up) | Endorsed |


---

# Round 4 — Verification of Claude Code's multi-agent findings (same commit `51d2950`)

The headline finding corrects this audit. Verified the full chain independently before conceding.

## The dead ML blend — CONFIRMED; my Round-1 production claim is withdrawn

Repo-wide grep for `mlPredictedProb` (all code, scripts, workflows): exactly **4 code occurrences** — writer `run_tips_pipeline.py:3001`, reader `:954`, diagnostics `:3028-3029`, and a comment in `ml_model.py:656`. **Zero occurrences in `mc_api.py`** ✓.

Every link in Claude's chain verified:

| Claim | Verification |
| --- | --- |
| Sole writer `:3001`, `runner["mlPredictedProb"] = round(ml_prob * 100, 2)` onto `runners` elements | ✓ exact |
| Reader `:954`, `ml_prob = h.get("mlPredictedProb", 0)` on `horses` elements; guard `if ml_prob > 0 and raw > 0` at `:955`; `ml_w = 0.20/0.40` at `:956-957` | ✓ exact |
| `horses = mc_result.get("results", mc_result.get("horses", []))` at `:3059` | ✓ exact |
| Only loop over horses between `:3059` and `calibrate_and_score(horses, ...)` at `:3101` is `enrich_horse_with_intelligence` (`:3067-3068`); its body contains no runner/`mlPredicted` reference | ✓ exact |
| `calibrate_and_score` (def `:857`) has exactly **one** caller (`:3101`); signature takes no `runners` | ✓ exact |
| mc_api builds `result = {` literal at `:7708`, `results.append(result)` at `:7869`, returns `'results': results` at `:7995`; no mutation adds the key | ✓ exact |

**Consequence: `ml_prob` is always 0 at `:954`, the guard never passes, and the 0.20/0.40 blend branch has never executed.** My Round-1 "ML share of the published probability = 12–28%" was arithmetic on a dead branch — exact for the branch, wrong as a statement about production. Claude's self-diagnosis applies equally here: the formula was verified, the guard's liveness was not. Conceded, and the Round-1 blending section of this report should be read with that correction.

**Why it survived (worth recording):** the pipeline's own diagnostics at `:3028-3029` read the key back from `runners` — the dicts it was written to — so the logs show ML scoring alive and well while the blend reads a different population of dicts. The instrumentation watched the writer, not the reader.

## The live channel — all line references verified exact

- `mc_api.py:6626` `ml_adjustment = model.predict_adjustment(features)` ✓
- `ml_model.py:794-796` `ml_prob = components["ensemble"][0]`; `adjustment = 0.5 + (ml_prob * 1.5)`; `max(0.7, min(1.5, adjustment))` ✓
- `mc_api.py:7615` `combined_adjustment = ml×0.55 + sophisticated×0.22 + enhanced×0.13 + fitness×0.10` ✓
- `:7632-7635` `adjusted_win_prob = base_win_prob × combined_adjustment`, capped `[1.0, 60.0]` ✓
- `:7740` `'_rawWinProb': float(win_prob)` — post-cap ✓; `:7884-7889` field renormalised to sum 100 from those values ✓
- Clamp algebra: `0.5 + 1.5p ≥ 0.7 ⟺ p ≥ 13.33%` ✓ (p ≥ 2/15)
- Isotonic-off arithmetic: raw ensemble mean ≈40% → adjustment ≈ 1.10 ✓; isotonic-on mean ≈14% → ≈0.71, and any runner under 13.33% pins to the 0.7 floor ✓. With a ~15% base rate most runners pin; a uniform additive contribution cancels at renormalisation, so the ML arm's within-race influence collapses toward zero. Mechanism sound — **flipping `STRIDE_ML_APPLY_ISOTONIC` before re-deriving the affine map at `ml_model.py:795` would likely mute the model**. Isotonic flip stays at the bottom of the list, now with a named prerequisite.

## Delivery path — verified exact

`infra/01_secrets.sh` carries exactly **9** `STRIDE_*` keys (grep-verified, unique-sorted): `BOOK_COHERENCE, COMMISSION_RATE, LEDGER_WRITE, MODEL_WEIGHT, RENORMALISE_FIELD, SERVE_LIVE_FEATURES, SERVE_LIVE_FEATURES_SHADOW, SERVE_NAN_CONTRACT, SHADOW_KELLY`. The blob-replacement comment Claude quoted sits at `:27-31` verbatim. **`STRIDE_EV_GATE_AT_PRICE`, `STRIDE_ML_APPLY_ISOTONIC`, and `STRIDE_DEVIG` are all absent** ✓ — the at-price EV gate cannot be shadowed by a secret edit; it needs an image rebuild or a `containerOverrides` entry. The two secondary paths Claude cites are real: `handler.py:882` `os.environ.setdefault("STRIDE_CTX_MULT_DIAG", "true")` (image path) and `verify-jobs.yml:158/166/172` passing `STRIDE_DATE`/`STRIDE_TRACKS`/`STRIDE_ENSEMBLE_ARTIFACT` via containerOverrides. "Real friction, not a dead end" is the accurate framing; the finding-#13 "UNREACHABLE" label was an overstatement, as Claude says. Spot-check `serve_features.py:353` `runner.get("class_level") or 0` ✓ exact.

## Interaction Claude's own summary misses: the skew is armed, not active

Claude's closing line — "The race_context seam remains the top substantive defect — six features, live, feeding out-of-range values on every race today" — is **overstated by their own dead-code finding**. The six skewed pace/context values feed `build_feature_row` → `mlPredictedProb`, whose only readers are diagnostics (`:3028-3029`). On the current production path they corrupt **nothing published**; the channel that actually moves published numbers (mc_api `predict_adjustment`) never receives them — it gets constant 0.5s from `extract_ml_features`, which are themselves out-of-training-range for leader/closer ([−0.10, +0.15]).

So the defect classes reorder into a **coupled pair**:

1. **Wiring the blend without aligning the definitions** would instantly publish ML at 20–40% weight built on out-of-range, sign-inverted pace features — the Round-3 harm becomes real the moment the cheap fix lands.
2. **Aligning the definitions without wiring the blend** changes nothing published (only shadow/diagnostic quality).

The safe sequence: align definitions → wire `mlPredictedProb` onto `horses` (matched by name/number before `calibrate_and_score`) behind a shadow comparison → re-derive blend weights/thresholds against the now-in-distribution values → only then consider the isotonic flip (with the affine map re-derived). The shadow program also needs the skew fixed first, because shadow deltas computed against skewed legacy features are themselves contaminated evidence.

## Priority after Round 4

1. Flag-state diagnostic (unchanged)
2. Ledger/CLV accrual (unchanged)
3. At-price EV gate — **new prerequisite: build a delivery path** (containerOverride or image rebuild) since it is absent from the secrets blob; wire `:1316` through `ev_at_price(..., default_commission_rate())` while in there
4. Instrument the intelligence override (unchanged)
5. Liveness-tool flag-awareness (unchanged)
6. **Pace/context definition alignment** (6 features) — still first among substantive fixes because it gates the validity of everything downstream, but re-classified: armed defect, not active harm
7. **Blend wiring** (`mlPredictedProb` → `horses` merge before `:3101`) — shadowed, with blend weights re-derived after landing; this is what makes the Round-1 arithmetic real
8. Sectional (8) + safe winner-pattern (3) wiring under gate 3, with pace-source attribution in shadow
9. Isotonic flip — only with the `ml_model.py:795` affine map re-derived (floor-flattening mechanism above)
10. Train-side snapshot odds when gate 1's calendar opens

## Round 4 scorecard

| Claude claim | Verdict |
| --- | --- |
| ML blend is dead code (writer/reader on disjoint dict populations) | **Correct — confirmed end-to-end; my Round-1 production claim withdrawn** |
| `mlPredictedProb` 0 occurrences in mc_api.py | Correct |
| No runner-field merge between `:3059` and `:3101` | Correct |
| Live channel chain (`:6626`, `:794-796`, `:7615`, `:7632-7635`, `:7740`, `:7884-7889`) | Correct, exact |
| 13.33% floor threshold + isotonic flattening mechanism | Correct — algebra and mechanism both sound |
| Secrets blob: 9 keys, EV-gate/isotonic/devig absent; comment at `:27-31` | Correct, exact |
| handler.py:882 image path; verify-jobs.yml containerOverrides path | Correct, exact |
| "race_context seam remains the top substantive defect… feeding out-of-range values on every race today" | **Overstated by their own finding** — armed, not active; see interaction above |
| 0-refuted-out-of-14 flagged as weak signal; importance-mass percentages dated against an older artifact | Sound epistemic framing; accepted as stated |

# STRIDE ROI Roadmap — Independent Review & Recommendations

**Date:** 2026-07-27
**Scope:** `AGENTS.md` (implementation contract) and `README (1).zip` → `stride-roi-roadmap/` (evidence base `00` + task files `01`–`14`).
**Method:** Every load-bearing code reference in the pack was verified against the repository at the current commit. Verified directly: `ml_model.py:216` blanket `fillna(0)`; `run_tips_pipeline.py:2706` `crowd_promoted`; `compute_staking` 2u/1u ladder (`run_tips_pipeline.py:1007`); LLM-computed `crowd_score` (`consensus_agent.py:809`); fabricated independence flags (`consensus_agent.py:1473`); SP→`market_odds` mapping (`retrain_v2.py:142-144`, `:557-561`); stacking meta-learner fitted but absent from `save()` (`ml_model.py:320-335` vs `:633-646`); `STRIDE_LEDGER_WRITE` no-op (`selection_ledger.py:294`); `DateWindowSplitter` 14-day purge; `assert_as_of`; the 14 standing prohibitions in `docs/analysis/IMPLEMENTATION_PLAN.md` §5; orphaned `market_efficiency.py`; parameterised-but-default-zero commission. Line numbers have drifted slightly from the audit commit in a few places (the pack itself anticipates this), but **every named symbol, flag, and defect was found as described**. The pack is factually accurate about the codebase.

**Verdict up front:** This is a high-quality, internally consistent program. The sequencing logic (measure honestly → stop economic leaks → retrain) is correct. Recommended disposition: **implement 12 tasks, modify 2, skip 0** — but the two "modify" verdicts and several flagged risks matter, so read each section.

---

## Review of the AGENTS.md contract itself

The contract (wave ordering, one task = one branch = one PR, flags default-OFF, measure-before-promote, no SP backfill, single settlement contract, no threshold tuning on the evaluating window, full disclosure of losing strategies, crowd gate never promotes, respect standing prohibitions) is sound and matches the repo's own `IMPLEMENTATION_PLAN.md` §5 prohibitions (verified: 14 items, including no-Kelly #4, no-CL-flip #9, no-NN #6, no `market_efficiency.py` wiring #7, no filter loosening #1). There is no conflict between the pack's rules and the repo's existing rules. The only operational caution: 4 waves × parallel branches requires disciplined git hygiene from whoever executes it.

---
---

## Task 01 — Selection ledger ON, CLV capture, net-of-commission settlement

### 1. Summary
The selection ledger schema already exists and is well designed — it records both the price taken at tip time and the final SP per selection, with `assert_as_of` leakage guards — but persistence is a no-op unless `STRIDE_LEDGER_WRITE=true`, and that flag is off. Task 01 switches it on, adds a migration for `price_taken`, `sp`, `clv_pct`, `commission_rate` (default 8%), `settled_pnl` and a `refused` flag, threads one commission parameter through every EV/settlement call site (currently defaulted to 0.0 everywhere), and unifies three conflicting P&L definitions (ledger settles at price-taken; both backtesters settle at SP; the shadow tracker silently falls back to tipped odds) into a single contract: settle at price taken, store SP for CLV only, and mark any SP fallback explicitly. It also captures refused NO_BET sets as ledger rows so gate quality becomes measurable, and extends the weekly metrics report to emit mean CLV, %CLV>0, and net ROI while keeping the ≥200-bet reportability floor. Historical `price_taken` is never backfilled — CLV is NULL where no real tip-time price exists.

### 2. Compatibility
Excellent. `selection_ledger.py`, `migrations/selection_ledger.sql`, the `STRIDE_LEDGER_WRITE` flag, and the commission parameter all exist — this is activation and hardening, not new architecture. It follows the repo's env-flag convention exactly and changes no model behaviour. The dead-heat gap noted (only the NSW collector parses "dh") is real and correctly scoped as a unit test rather than a rewrite.

### 3. ROI/strike-rate impact
No direct edge change — it is measurement. But it is the highest-leverage measurement available: CLV reaches statistical significance in ~400 bets versus ~3,000–5,000 for ROI, so it is the only realistic way to learn whether an edge exists this year. Net-of-commission settlement prevents the concrete failure mode of staking and threshold decisions tuned on gross numbers that are 2–6× flattered (the repo's own plan: +12.3% gross → ~+2–4% net at 8–10% MBR). Indirectly it protects ROI by stopping over-betting into an unproven edge.

### 4. Recommendation
**Implement — first, immediately.**

### 5. Justification
Everything downstream (06, 07, 09, 10, 11) consumes ledger rows. Risks are minimal: the write path must never block or delay the tips pipeline (wrap async/fire-and-forget — the pack says this for 04; apply the same discipline here), and refused-row capture adds DB write volume that should be monitored. No data-integrity concerns — the explicit prohibition on fabricating `price_taken` from SP is exactly right, since CLV computed on SP==price-taken is definitionally zero and would poison the metric.

---

## Task 02 — Backtest statistics: CIs, drawdown, streaks, concentration

### 1. Summary
No ROI figure should ever be printed again without its standard error, confidence interval, sample size, and risk path. Task 02 creates a shared, unit-tested `roi_stats.py` module providing: bootstrap confidence intervals for ROI (resampling bets, not runners, ≥10k resamples, using the net-return definition from Task 01's settlement contract); maximum drawdown and losing-streak analytics (observed plus expected under the measured strike rate); winner-concentration analysis (ROI with the top 1–3 winners removed); and a multiple-comparison diagnostic that states the z-score a best-of-N sweep would need under Bonferroni correction. It wires this into both backtesters, the walk-forward backtest, and the summary JSON generation, so every strategy block carries `se`, `ci95`, `max_drawdown`, `max_losing_streak`, `roi_net`, and a `reportable` flag that marks any result whose CI spans zero (or n < 200) as `NOT_REPORTABLE`. Strategy sweeps get a selection-bias caveat attached. Losing strategies are never deleted from reports — full disclosure is mandatory. The acceptance test requires reproducing the known 142-bet case: CI ≈ [−44%, +68%], reportable: false.

### 2. Compatibility
Excellent. `walk_forward_backtest.py` already contains `bootstrap_roi_ci` and t-distribution CI machinery to reuse; `ship_criteria.py` already has NOT_REPORTABLE verdicts to align with. Pure reporting — zero behavioural change, no flags needed, no conflicts with any existing element.

### 3. ROI/strike-rate impact
Indirect but real: it ends decision-making driven by noise (best-of-6 on one window has an 80–93% chance of showing +12% under a zero-edge null), and the drawdown/streak output is the required input for sane stake sizing in Task 06. Expect the headline +12.3% to be formally marked not-reportable — that is the point, not a failure.

### 4. Recommendation
**Implement — alongside 01.**

### 5. Justification
Cheapest task in the pack with the broadest effect on every later decision. Risks are essentially nil; the one discipline to enforce is the guardrail that bootstrap resamples bets (not runners) and uses net returns — otherwise the CIs themselves become flattered. No data-integrity exposure: it reads results, it writes nothing upstream.

---

## Task 03 — Serve-time probability fixes: NaN semantics, inverted interaction, one feature builder

### 1. Summary
Three verified defects make live probabilities different from — and in one case sign-flipped against — the trained model. First, training deliberately preserves NaN so gradient-boosted trees route missingness, but `prepare_features` executes `.fillna(0)` on every column at serve (verified at `ml_model.py:216`), converting "no sectional data" into exactly field-average z-scores for roughly half of all runners and "no barrier trial" (sentinel 999/NaN) into "trial today". Second, `barrier_x_pace_inv` is trained as `adv × pace` but served as `adv × (1 − pace)`; the parity fix exists behind `STRIDE_INTERACTION_PARITY`, which defaults off. Third, two inference paths disagree: `mc_api.py` populates ~13 market-movement features from real movement while `run_tips_pipeline.py` serves zeros for the same columns — and training never computes them either. The fix: a shared `NAN_PRESERVE_FEATURES` constant imported by both trainer and server; a new parity test that must fail before and pass after; a backtested flag flip for interaction parity; extraction of one `serve_features.py` builder used by both paths; and forcing movement columns to zero in both paths until Task 14 makes them real.

### 2. Compatibility
Very good. All three defects verified in the current code. The refactor (extracting a shared builder) touches two large entry-point files, which is the only meaningful intrusion — mitigated by flag-gating (`STRIDE_SERVE_NAN_CONTRACT`, default off for a shadow week) and byte-identical rollback. The "don't improve values, only restore semantics" guardrail keeps the change reviewable.

### 3. ROI/strike-rate impact
Real but unquantifiable in advance: live probabilities currently misprice roughly half the field on sectional features and invert an interaction term in high-pace races. Restoring train/serve parity should move live Brier and live strike toward (not beyond) the honest offline values. It will not create new edge; it stops leaking the edge the model already has.

### 4. Recommendation
**Implement — in Wave 1, but do not flip the parity default until the on/off backtest comparison is attached, exactly as specified.**

### 5. Justification
This is a bugfix, not an experiment; the sign-flipped interaction is the clearest defect in the entire audit. Risks: (a) the NaN contract must be sourced from one constant shared with the trainer or it will drift again; (b) flipping interaction parity changes live outputs — the shadow/backtest gate in Step 3 is essential; (c) zeroing mc_api's live movement features slightly degrades that path short-term, but serving two different distributions to a model trained on all-zeros is strictly worse. Data integrity is preserved — nothing post-prediction-time is introduced.

---

## Task 04 — As-of-prediction-time odds snapshot (time-critical)

### 1. Summary
The model trains, cross-validates, and backtests with closing SP mapped into `market_odds` (verified at `retrain_v2.py:142-144`, `:557-561`), while live inference only ever sees morning racecard prices — so the single most important feature is stronger in every offline number than it ever will be live. Task 04 creates an append-only `runner_odds_snapshots` table and two capture jobs: one inserting a `tip_time` row per runner (storing all bookmakers, not just the first) at the moment the tips pipeline prices the field, and a scheduler job capturing a `late_t5` snapshot five minutes before each jump (with `seconds_to_jump` recorded so T−10/T−15 fallbacks remain honest). Training-view plumbing is strictly additive — new `tip_time_odds`, `odds_source`, and `seconds_to_jump` columns, with the legacy SP mapping untouched until Task 12. A daily coverage monitor alerts if tip-time coverage falls below 95%. Capture is prospective only: historical rows are never backfilled from SP or any post-race source. Every day of delay pushes the honest re-baseline (Task 12) out by a day.

### 2. Compatibility
Excellent. Additive migration, scheduler additions following the existing `download_racecards.py` pattern, monitoring following the existing health-check pattern. It explicitly does not change `market_odds` semantics anywhere — zero conflict with current training or serving. The repo's own plan already tickets this (C6/T15, X1/B2) and calls it the strongest source-read finding.

### 3. ROI/strike-rate impact
No immediate change; it is the unblock for the two largest ROI/strike items in the pack (honest retrain in 12, late-odds features in 14 — the repo's research ranks the T−5 window #1, citing a ~14× cross-sectional effect in an 894k-runner JRA study). It also enables validating bands at prices actually obtainable, which is where the real ROI number will come from.

### 4. Recommendation
**Implement — start the capture jobs on day one, in parallel with Wave 1, even before other tasks finish.**

### 5. Justification
This is the strongest data-integrity design in the pack: append-only storage, live-capture-only rows, additive columns, no backfill. Risks are operational, not statistical: (a) T−5 timing reliability against the Racing API — mitigate with `seconds_to_jump` as specced; (b) capture failure must never delay tipping (the try/except fire-and-forget guardrail covers this); (c) DB growth — trivial at racing volumes. Skip nothing here.

---

## Task 05 — Calibrator stage fix + per-race renormalisation

### 1. Summary
The production isotonic calibrator is fitted on out-of-fold predictions of the v1 ML ensemble but is applied at serve time to the Monte Carlo `winPercentage` — calibrating one engine's output with another engine's calibration curve, where the two engines even have different training hygiene (v1 uses random splits). Separately, after the ML blend and the `mw` market anchor, published per-race probabilities no longer sum to 1, and nothing renormalises them — so edge values are compared across horses on inconsistent scales, and `mc_api.py`'s standalone edge additionally uses raw implied odds with no overround correction at all. Task 05 refits the calibrator on the quantity it actually calibrates (MC OOF win percentage, or the recorded final-probability audit where MC OOF coverage is insufficient), adds per-race renormalisation after the anchor behind `STRIDE_RENORMALISE_FIELD`, extracts one shared overround-corrected market-probability helper used by both entry points, and runs a mandatory shadow week (old vs new calibrator, renormalisation on vs off) comparing Brier, log-loss, calibration-bin alignment, and field sums before promotion. The `mw` ladder values themselves are explicitly not touched.

### 2. Compatibility
Good, with one caveat. `ProbabilityCalibrator`, provenance sidecars, and race-span fold refusal all exist; flag-gating and staged artifact promotion (`isotonic_calibrator_v2.pkl` beside v1) match repo discipline. The caveat: the pack itself notes OOS/OOF logging was broken for 15+ months, so the refit source (MC OOF) may have coverage gaps — Step 1's "check coverage first" must be treated as a hard gate, not a formality.

### 3. ROI/strike-rate impact
Moderate and direct: edges computed on unnormalised, mis-calibrated probabilities systematically misrank value — and the repo's calibration bands already show 0.20–0.30 under-prediction exactly in the price band where value bets live. Renormalisation can change which bets pass the EV gate; expect some current "edges" to shrink and others to appear. Net effect should be positive but must be proven in the shadow week.

### 4. Recommendation
**Implement with modification:** before refitting, extend the final-probability audit logging if coverage is insufficient, and accept a one-cycle delay rather than fitting on a patched-together sample. All other steps as written.

### 5. Justification
Medium risk because it changes published probabilities — the shadow-first design is the correct mitigation. Data integrity is well handled (temporal fit rule preserved, no post-race data in fitting). The main conflict to avoid is tuning thresholds in the same PR — the pack correctly defers all threshold re-derivation to Tasks 09/12.

---

## Task 06 — Staking reform: flat 1u, drawdown circuit-breaker, honest Kelly path

### 1. Summary
The staking ladder is inverted against the system's own results: `compute_staking` stakes 2u on HIGH and 1u on MEDIUM (verified at `run_tips_pipeline.py:1007`), yet in the validating window the 2u HIGH tier lost −37.5% while the 0u LOW tier lost only −1.7%. The audit computes 2u ≈ 1.7× full Kelly gross and 5–10× net of commission — past the zero-growth point — and at a 9.9% strike rate the expected maximum losing streak is ~30 bets, implying a −60% bankroll drawdown at 2u that nothing in the system sizes for. Task 06 flattens the ladder to 1u/1u/0u behind `STRIDE_FLAT_STAKING`; adds a drawdown circuit-breaker (halve units at −15% of rolling bankroll, suspend at −25%, publishing NO_BET with reason `drawdown_breaker`, never increasing stakes after wins); fixes the variance/EV probability mismatch in `portfolio_risk.py` (variance computed at market-implied probability while EV uses model probability); adds daily exposure caps to the tips pipeline (max 6 bets/day, 2/track, selected by highest net EV); and adds a `kelly_readiness()` report that returns true only when ≥400 settled bets show a net-ROI CI above zero and positive mean CLV. Kelly itself stays off, per standing prohibition #4.

### 2. Compatibility
Good. It respects the standing prohibitions precisely — touching only three functions in the otherwise-dormant `portfolio_risk.py` rather than resurrecting `PortfolioRiskManager` (prohibition #7), and delivering Kelly activation *criteria*, not activation (prohibition #4). The existing MC-engine caps don't bind production, so the new pipeline caps are additive, not conflicting.

### 3. ROI/strike-rate impact
This is the largest direct bankroll-protection item in the pack. It does not raise strike rate; it cuts the left tail — converting a plausible −60% drawdown scenario into a bounded one — and removes the worst-mispriced staking tier. The exposure caps trade a small amount of theoretical EV for a large reduction in correlated daily risk.

### 4. Recommendation
**Implement with modification:** make the daily caps (6/day, 2/track) env-tunable and run them in shadow mode for two weeks before enforcing, logging what *would* have been rejected — if rejected bets show positive net EV, revisit the cap values through the Task 09 protocol rather than hard-coding them.

### 5. Justification
The evidence (inverted ladder, Kelly multiple, streak math) is verified against repo data. Risks: the breaker thresholds (15%/25%) are sensible defaults but arbitrary — keep them env-configurable as specced; and caps can discard genuine edge, hence the shadow-first modification. No data-integrity concerns; it consumes ledger/CI outputs rather than touching training data.

---

## Task 07 — Crowd gate: gate-only (never promote NO_BET → BET)

### 1. Summary
Today the live consensus gate can promote a NO_BET into a BET when the crowd signal is strong (verified: `crowd_promoted` at `run_tips_pipeline.py:2706`) — overriding the EV gate with an unmeasured edge, and contradicting the system's own "value, not tips" contract. Worse, the crowd is not independent evidence: research prompts include every runner's odds, Perplexity queries ask for "Betfair market movers", a `market_reader` bucket is weighted 1.4, and the model pillar is itself market-anchored — so "crowd agrees with model" is partly "market agrees with market". The promotion rule itself (`crowd ≥ 100`) was added from a single race. Task 07 puts the gate behind `STRIDE_CROWD_GATE_ONLY` (default on): the crowd may confirm a bet, downgrade HIGH→MEDIUM, or veto BET→NO_BET — but never create one. Every blocked promotion is shadow-recorded as a refused ledger row (from Task 01) with the would-be price, so the value or cost of the old behaviour becomes measurable from data instead of anecdote. A weekly report shows blocked-promotion shadow P&L net of commission and their CLV; re-opening promotion would require a pre-registered experiment under Task 09.

### 2. Compatibility
Excellent. A small, flag-gated change at one call site, with byte-identical rollback (flag off) and shadow measurement via Task 01 machinery. No interface changes, no conflicts.

### 3. ROI/strike-rate impact
Expected neutral-to-positive net ROI: it removes forced bets placed at no measured edge. Strike rate may tick up slightly (fewer low-quality bets) but the real gain is stopping unquantifiable downside and converting a folk rule into a measured quantity.

### 4. Recommendation
**Implement — immediately after 01.**

### 5. Justification
Verified defect, asymmetric-risk fix (vetoes retained, promotions removed), trivially reversible. The shadow-rows design means that if promotion ever did have genuine value, the data will show it and it can be re-opened properly. No data-integrity concerns.

---

## Task 08 — Consensus integrity: deterministic score, provenance, ROI-graded tipsters, de-correlation

### 1. Summary
The `crowd_score` consumed by the live gate is currently computed by the LLM itself — the extraction prompt literally asks the model to do the division (verified at `consensus_agent.py:809`), with a denominator the model chooses — making it non-auditable, unstable, and gameable. Independence flags are fabricated by list position (`is_ind = i < n_independent`, verified at `:1473`), `source_url` is always null, syndicated tips count multiple times, and the minimum-independent-sources rule is defined but never enforced. Tipster accuracy multipliers grade on win strike only, so a favourite-tipper with negative ROI gets up-weighted. Task 08 moves all arithmetic into code (the LLM still extracts mentions; it no longer computes numbers), requires `source_url` provenance with normalisation and syndication dedup (one vote per unique tipster-horse pair), enforces the minimum-sources rule with races marked `insufficient` when unmet, re-grades tipsters on tip-time-odds→SP CLV and net ROI with a ≥20-tip floor and shrinkage, splits multipliers per tipster×track×distance band, adds a panel lifecycle (probation, demotion after 60 settled tips below −10% net ROI, quarterly review), and A/B-tests removing odds from research prompts plus residualising the crowd score against de-vigged market probability before blending.

### 2. Compatibility
Good. It changes a live input signal, which is why both flags default off and promotion requires a ≥4-week shadow A/B through the ledger. Tipster CLV grading depends on Task 01's ledger (tip-time odds) — the dependency chain is correctly declared. This is the largest single engineering effort in the pack.

### 3. ROI/strike-rate impact
Moderate-to-significant: it removes the third, currently unscoped, counting of market information (consensus↔market), which should sharpen the gate's veto/downgrade decisions and stop the feedback loop that rewards price-insensitive tippers. Gains arrive as better selectivity, not more bets.

### 4. Recommendation
**Implement — but execute it internally as two stages:** (i) deterministic score + provenance + dedup + CLV grading first (pure integrity wins, low controversy); (ii) the odds-free-prompts / residualisation A/B second, since variant B changes LLM prompt contracts and needs the full shadow window. (Within the pack's one-task-one-PR rule, land these as two stacked commits with separate flags, as specced.)

### 5. Justification
Every cited defect was verified in code. Risks: syndication dedup is heuristic (near-duplicate text across domains) — keep it conservative and log collapses; the 20-tip floor plus shrinkage correctly guards against small-sample whiplash; the A/B's 4-week duration is the real cost. Data integrity is improved — stripping odds from prompts is de-leaking a signal pillar.

---

## Task 09 — Forward validation protocol: pre-registration, disjoint windows

### 1. Summary
Task 09 ends the garden of forking paths. The current live gate (price cap, edge thresholds per band) is a transcription of the winning band from a 6-way sweep on one 6-week window, and the backtest's band filter itself selects on SP — a price only knowable after the jump. The task creates an append-only pre-registration registry (`docs/validation/registry.md`): every hypothesis declares its exact rule, selection window A, disjoint later validation window B, expected n, and success criterion (lower-95%-CI of net ROI > 0 AND mean CLV > 0 over ≥200 window-B bets) *in advance*. A parameter-free validation runner (`validate_forward.py`) pulls settled ledger rows matching the rule's tip-time criteria and emits PASS/FAIL/INSUFFICIENT_SAMPLE — it cannot search for a passing variant. `ship_criteria.py` is wired so no band may be quoted (or used for stake sizing) without a registry PASS; FAIL retires the rule to a documented graveyard, never re-tested on overlapping windows. If the current production band fails its window-B test, the fallback is to bet nothing until Task 12 re-derives bands on honest features — not to re-sweep window B. Entry #1 is the current production band, validated at tip-time prices from Task 04's snapshots.

### 2. Compatibility
Excellent as governance: it formalises instincts the repo already has (200-bet floors, NOT_REPORTABLE verdicts) and consumes Tasks 01/02/04 machinery. It changes no model code.

### 3. ROI/strike-rate impact
Prevents the single most expensive failure mode in betting systems — confidently scaling a noise-selected band. It will likely *reduce* betting volume short-term (honest validation fails most in-sample winners), which is ROI protection, not loss.

### 4. Recommendation
**Implement with one modification:** make the FAIL kill-switch require human sign-off before the pipeline goes full NO_BET — an automatic halt on a governance script's verdict is operationally brittle (data gaps can masquerade as FAIL). The protocol itself: exactly as written.

### 5. Justification
This is the data-integrity backbone of the whole program — disjoint windows, one hypothesis per window B, tip-time prices only, append-only registry. The only risk is behavioural: a FAIL on the current band means admitting the current numbers don't hold, and the correct response (fewer/no bets until re-baseline) requires discipline. That is a feature, but be prepared for it.

---

## Task 10 — Execution & pricing: best price, BOG, honest de-vig, exchange reference

### 1. Summary
Settlement is currently modelled at SP, discarding early-price advantage — but tips are issued at morning prices, so taking best-tote or Best-of-the-Best (BOG) prices is free, unmeasured ROI. Separately, the fair-probability estimate uses proportional de-vig, which the repo's own findings call the weakest published method (biased along the favourite-longshot bias), and reference prices are inconsistent: movement signals store cross-bookmaker medians while inference takes the first bookmaker entry. Task 10 makes `market_prob.py` the single source of truth for reference price (median across bookmakers at the tip-time snapshot from Task 04); implements Shin and power de-vig alongside proportional, compares them on settled ledger data, and switches behind `STRIDE_DEVIG`; records the best fixed price across bookmakers (and BOG availability) as `price_taken` so the weekly report can quantify execution gain versus SP settlement; and runs a short feasibility spike on Betfair exchange mid prices at tip time and T−5 (the better fair-price estimate and a weight-of-money source for Task 14). Thresholds are explicitly not changed in the same PR — fair prob moves when de-vig changes, so bands are re-derived only through Task 09.

### 2. Compatibility
Very good. Builds on the `market_prob.py` helper created in Task 05, reuses Task 04's all-bookmaker storage, and respects the guardrail against using exchange *settled* SP as a training feature. The de-vig flag default stays `proportional` until the comparison lands.

### 3. ROI/strike-rate impact
The most direct ROI uplift in the pack that doesn't touch the model: best-price/BOG execution versus SP settlement is typically worth several percentage points on the very longshots this system backs, and a better fair-probability estimate improves every edge calculation downstream. No strike-rate effect.

### 4. Recommendation
**Implement with modification:** record *two* prices — best-available and best-actually-obtainable given the bookmaker accounts you hold — and settle the ledger on the latter. Otherwise CLV and execution-gain figures become aspirational (a price you can't take isn't a price).

### 5. Justification
"Free ROI" is accurate here — execution discipline costs nothing model-side. Risks: the de-vig swap shifts edges and therefore which bets pass gates (correctly quarantined behind the Task 09 protocol — do not shortcut this), and the exchange spike may return a no-go on AU coverage/rate limits, which is a fine outcome. Data integrity is explicitly guarded.

---

## Task 11 — Place & each-way markets: the strike-rate lever (shadow first)

### 1. Summary
If strike rate is a genuine product goal — bankroll psychology, subscriber experience, shorter losing streaks — the biggest lever is not the win model at all but the market mix. The Monte Carlo engine already emits `place_pct` per runner (verified at `run_tips_pipeline.py:2570`), unused for betting; `selection_policy.py` implements each-way/place/dutching but with a crude fixed place-price model (`1 + (odds−1)×0.25`), zero importers, and an explicit "must not be switched on" warning; and the shadow settler currently scores a PLACE as a full loss, biasing tier ROI downward exactly in the longer-priced bands where places land. Task 11 fixes the shadow settler (WIN/PLACE/dead-heat halving), audits MC place probabilities against realised place rates (expecting Harville bias, with discounted-Harville/Henery-Stern as `place_pct_v2` selected by Brier), fits a real place-price model from historical dividends, and produces a weekly shadow comparison of place-only and each-way versus win-only — net of commission, with Task 02 statistics. Live place betting activates only via a pre-registered Task 09 PASS: net ROI CI > 0 over ≥200 shadow bets with positive CLV. `STRIDE_MARKET_MIX` stays off until then.

### 2. Compatibility
Good — everything is shadow-only; collectors already parse dividends; the MC place probabilities exist. The go/no-go is correctly delegated to the Task 09 registry, so nothing conflicts with live paths.

### 3. ROI/strike-rate impact
The largest strike-rate lever available (~3× win strike at these price bands) and a transformed losing-streak profile; place markets also carry weaker favourite-longshot bias. ROI impact is unproven by design — that is what the shadow period is for.

### 4. Recommendation
**Implement as written, with one explicit addition:** the place-price model (Step 3) must be fitted and validated on time-based splits (reuse `DateWindowSplitter` discipline), not random splits — the pack says "validate out-of-sample" without specifying temporal, and a randomly-split price model would leak regime information.

### 5. Justification
Well-sequenced: shadow-first, pre-registered promotion, the 0.25 rule explicitly banned from live use forever. Risks: place-dividend coverage gaps across states (verify NSW/VIC parsing), and place overround (15–30% in AU fixed odds) can quietly erase theoretical edge — the modelled-vs-actual-dividend dual reporting in Step 4 is the right guard.

---

## Task 12 — Retrain & re-baseline: morning-odds features, honest folds, per-race metrics, persisted ensemble

### 1. Summary
The capstone: retrain on features the model will actually have live. The training view switches `market_odds` from SP to Task 04's tip-time snapshots, excluding rows without snapshots entirely rather than falling back to SP (a mixed feature is worse than a smaller dataset); SP survives only as a settlement/CLV column. Fold hygiene is fixed: today fold-level isotonic calibration is fitted on the test fold's own predictions and LightGBM early-stops on the test fold (mildly self-fitting the published Brier 0.0834) — the early-stop/calibration window gets carved from the tail of the *training* window instead, leaving test folds untouched. Cross-validation gains per-race top-1 hit rate, the SP-favourite baseline on the same races, same-race H2H against the production model, and per-race softmax log-loss, with promotion requiring beating the favourite and not losing the H2H. The ensemble combination becomes learned and persisted: today production weights are hardcoded seed counts, CV scored a different (equal-weight) blend, and the stacking meta-learner is fitted but never pickled (verified — `save()` omits it, `ml_model.py:633-646`). One final-stage calibrator replaces the layered calibration. All headline metrics are re-published with CIs, old numbers moved to a "superseded (SP-contaminated)" appendix.

### 2. Compatibility
Deliberately high-intrusion, but the rollout matches existing discipline: staged artifact promotion (`racing_ensemble_v3.pkl` beside v2, env pointer, one-week parallel scoring, point-back rollback). Dependencies are correctly declared (03, 04, 05, 06, 09). It touches the core trainer, so review carefully.

### 3. ROI/strike-rate impact
Expect headline numbers to *drop* — that is the honest re-baseline working, not a regression. The genuine gains: per-race hit rate becomes the optimisation target (the product sells within-race ordering), the persisted stacking fixes a real dead-code bug, and honest folds stop ablation decisions being made on ±0.001 AUC against fold std 0.044.

### 4. Recommendation
**Implement — only after ≥4–6 weeks of Task 04 data, exactly as sequenced. Do not start early.** Report the re-baselined metrics with per-race hit-rate CIs, since excluding pre-capture rows shrinks the training sample (already only ~10k-class).

### 5. Justification
This is the data-integrity centerpiece: never mix SP and tip-time odds in one column; purge gaps and race-safe splitting preserved; test folds untouched by calibration and early stopping. The key risk is psychological, not technical: the guardrail "do not rescue a failing re-baseline by tuning on the validation window" must be honoured — if the honest model shows no edge, the correct output is fewer/no bets until 13/14 land. Honour it.

---

## Task 13 — Race-aware objective: exploit one-winner-per-race structure

### 1. Summary
All three production trainers fit per-runner binary classifiers, but horse racing is fixed-sum — exactly one winner per race — and pooled binary objectives never exploit that structure. The only previous race-aware attempt (`rank_model.py`, LGBMRanker) failed its head-to-head (33.6% vs the stored model's 39.7% vs favourite 34.4%) and was correctly rejected; that failure invalidates the configuration, not the approach. Task 13 delivers a cheap interim win first: selecting each race's top tip by argmax of the within-race softmax of ensemble scores (temperature fitted on validation log-loss) behind `STRIDE_SOFTMAX_PICK`, while calibrated probabilities still drive edge/EV. It then trains a CatBoost `QuerySoftMax` listwise model (fallback: LGBMRanker with race groups) on the Task 12 feature set with race-id groups and identical purge-gapped splits, evaluated through the exact same-race H2H protocol that rejected the previous ranker — an explicit REJECT is a documented success. Integration happens only if the H2H wins, as a fourth voter whose weight is learned on out-of-fold predictions, or as an ordering overlay for pick selection. A registry-governed ablation (retrain minus the 12 market features, refit the conditional-logit blend, inspect β) probes the market double-counting question without un-holding the CL blend.

### 2. Compatibility
Good. It respects the standing prohibitions (no NN #6, no CL flip #9 — the ablation in Step 4 is diagnostic only), reuses the existing H2H harness, and both flags default off. Depends correctly on Task 12's honest metrics existing first.

### 3. ROI/strike-rate impact
The most direct top-pick strike-rate lever in the pack (current: 33.7% vs favourite 34.4%). The softmax pick alone is a low-risk few-points opportunity; the listwise voter is a genuine maybe — the prior failure is honest evidence that rejection is a live possibility.

### 4. Recommendation
**Implement with modification:** keep Step 4 (the CL/market-double-count ablation) strictly diagnostic — any resulting change to the `mw` ladder must go through the Task 09 registry as a new hypothesis, not ship in this task. Everything else as written.

### 5. Justification
Promotion-by-H2H (not AUC) is the right criterion and protects against a model that wins pooled metrics while losing within-race ordering. Risks: QuerySoftMax needs race-group plumbing through training (moderate effort), and a second rejected ranker would be demoralising but genuinely informative — treat it as such. No data-leakage exposure beyond what Task 12 already governs.

---

## Task 14 — Late-odds & market-movement features

### 1. Summary
Roughly 13 market-movement features exist in the feature contract but are constant-zero in training and split-brained at serve (mc_api populates some from real movement; the tips pipeline serves zeros). Meanwhile the existing steam/drift signal measures only overnight→8am — mostly opening-market noise — and its thresholds are hand-set and never validated. The literature window that matters is the final 30 minutes; the repo's own research ranks the T−5 minute snapshot its #1 feature addition, citing a ~14× cross-sectional effect in an 894k-runner JRA study, and a Phase-5 ablation proved re-encodings of the same price add nothing — only new temporal information can. Task 14 builds `movement_features.py` to compute, from Task 04's snapshot series: baseline→tip change, tip→late change, late move (T−5 vs T−60), signed velocity, field-level firming agreement, and the runner's move versus field median — as raw continuous features with one taxonomy (the second hand-set one is deleted; any kept booleans get thresholds fitted on window-A data only). Pre-capture rows are NULL, never zero-filled, and excluded from ablation training sets. A three-arm ablation (no movement / overnight / overnight+late) on identical purge-gapped folds ships only arms that win under Task 12's promotion criterion; both inference paths then share the single module.

### 2. Compatibility
Good, correctly sequenced last: needs 8–12 weeks of Task 04 data and Task 12 machinery. The serve unification completes Task 03's single-builder work. The critical design element — encoding which inference moment each feature serves (morning tipping vs T−5 rescoring) in a feature-name registry with tests — is the right leak guard.

### 3. ROI/strike-rate impact
Potentially the largest documented feature gain in the program for both strike rate and edge honesty — but it is prospective: it must win the honest ablation to ship, and a documented REJECT is a valid outcome.

### 4. Recommendation
**Implement — last, as sequenced.** Do not pull it forward; without Task 04's prospective series it cannot be built honestly at all.

### 5. Justification
The data-integrity handling is exemplary: prospective-only, NaN-not-zero for pre-capture rows, inference-moment separation so T−5 features can never leak into morning scoring. The one risk to flag: supporting two live inference moments (morning tips and T−5 rescoring) doubles serving/ops complexity — budget for it, and if the T−5 rescore isn't operationally sustainable, ship only the baseline→tip movement arm if it wins its ablation.

---
---

## Consolidated disposition

| # | Task | Verdict | Priority |
|---|------|---------|----------|
| 01 | Ledger, CLV, net settlement | **Implement** | Day 1 |
| 02 | Backtest statistics | **Implement** | Day 1 |
| 03 | Serve-time probability fixes | **Implement** | Week 1 |
| 04 | As-of-odds snapshot | **Implement (time-critical)** | Day 1 (capture jobs) |
| 05 | Calibrator + renormalisation | **Implement, modified** (verify OOF coverage first) | Week 2–3 |
| 06 | Staking & risk controls | **Implement, modified** (caps shadow-first) | Week 2 |
| 07 | Crowd gate gate-only | **Implement** | Week 1–2 |
| 08 | Consensus integrity | **Implement** (two internal stages) | Week 3–8 |
| 09 | Forward validation protocol | **Implement, modified** (human sign-off on kill-switch) | Week 3+ |
| 10 | Execution & pricing | **Implement, modified** (settle on obtainable price) | Week 3–8 |
| 11 | Place & each-way (shadow) | **Implement** (+ temporal split for price model) | Week 3–8 |
| 12 | Retrain & re-baseline | **Implement** (after 4–6 weeks of 04 data) | Month 2 |
| 13 | Race-aware objective | **Implement, modified** (Step 4 diagnostic-only) | Month 2–3 |
| 14 | Late-odds features | **Implement** (after 8–12 weeks of 04 data) | Month 3–4 |

## Overall data-integrity assessment

The pack's leakage discipline is stronger than the codebase's current state and, where followed, *reduces* existing leakage: SP look-ahead removed from features (04/12), test-fold calibration/early-stopping removed (12), NaN semantics restored (03), prospective-only capture with no backfill (04/14), disjoint pre-registered validation (09), and LLM arithmetic removed from live signals (08). Nothing in the pack introduces post-prediction-time data into training. The two places requiring added vigilance beyond what the pack states: (1) Task 11's place-price model must use temporal splits; (2) Task 14's dual inference moments need the feature-name registry enforced by tests, not convention.

## Bottom line

Accept the pack. Execute in wave order. The three day-one actions — switch on the ledger (01), honest statistics (02), and start odds-snapshot capture (04) — are each low-risk, reversible, and collectively convert every future decision from opinion into measurement. Expect the honest numbers to be worse than the current headlines; that is the cost of knowing the truth, and it is cheaper than finding out at scale.

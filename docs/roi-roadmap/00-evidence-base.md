# STRIDE Racing — Where to Improve to Maximise ROI & Strike Rate

**Basis:** full static analysis of `github.com/Sageabdallah/stride-racing` (192 files, ~70k lines of pipeline Python, 13 doc files, `docs/analysis/*` research passes, `examples/backtest_summary.json`), cross-checked by four independent audit tracks: ML engine & calibration · betting economics & staking · backtest validity & learning loop · consensus/market pillars & roadmap.

---

## 1. Executive summary

The engineering is genuinely good — temporal purge gaps, OOF calibration design, leak-free feature plumbing, honest docs. But the two numbers you are optimising toward are currently **measuring a system that doesn't exist in production**:

1. **Every headline metric is inflated by closing-price (SP) look-ahead.** The model trains, cross-validates, and backtests with `sp_odds` as the `market_odds` feature (`retrain_v2.py:142-144`, `backtest_v2_metro.py:127-142`), but live it only sees morning racecard prices (`run_tips_pipeline.py:2259`). Your single most important feature is stronger in every offline number you have than it ever will be live.
2. **The +12.3% ROI headline is within noise.** 142 bets, 14 winners at avg ~$11.4 odds → standard error ±28.5pp, 95% CI **[−44%, +68%]**, z = 0.43, bootstrap P(true ROI ≤ 0) ≈ 35%. It is the best of 6 strategies tried on the same 6-week window — under a zero-edge null, an 80–93% chance that *some* band shows +12% by luck. Net of realistic Betfair commission (8–10% MBR) it shrinks to roughly **+2–4%**. Your own `docs/analysis/IMPLEMENTATION_PLAN.md:2704-2707` already computed t = 0.432 — believe it.
3. **The strike rate confirms the market-anchor problem:** your Top Pick wins 33.7% vs the SP favourite's 34.4% — after ~110 features and three models, you are *slightly behind* the market at the top of the market, which is exactly what double-anchoring to the market produces (12 market features + `mw` anchor ladder + odds-aware consensus).

So the improvement program has three phases, in order: **(A) make the numbers honest, (B) stop the economic leaks, (C) only then retrain for strike rate.** Doing them out of order means tuning against flattered metrics.

---

## 2. Phase A — Fix measurement first (highest leverage, mostly config/plumbing)

Everything below is cheaper than a retrain and changes every subsequent decision.

| # | Fix | Evidence | Why it moves ROI |
|---|-----|----------|------------------|
| A1 | **Persist an as-of-prediction-time odds snapshot per runner** and retrain with morning odds as `market_odds`; keep SP only as a settlement/CLV column. Prospective-only, never backfill. | `retrain_v2.py:557-574`; live path `run_tips_pipeline.py:2259`; your docs call this "the strongest source-read finding" (R5-F2) | Removes the directional train>serve skew that flatters AUC, Brier, strike and ROI simultaneously. This one change re-baselines everything honestly. |
| A2 | **Turn on the selection ledger** (`STRIDE_LEDGER_WRITE=true`): record price-taken *and* SP per tip, compute CLV weekly. | `selection_ledger.py:156-173, 294-295` — plumbing built, flag off | CLV reaches statistical significance with ~400 bets vs ~3,000–5,000 for ROI. It is the only near-term way to know if you have an edge this year. |
| A3 | **Model commission/takeout.** Thread one `commission_rate` through EV and settlement; re-run all bands net at 8% and 10%. | `selection_ledger.py:118` defaults to 0.0; gross settlement `backtest_v2_metro.py:317`; your plan: "+12.3% → ~+4.1% at 8%, +2.1% at 10%" | At a 9.9% strike with ~$11 avg winners, commission removes two-thirds to five-sixths of the gross edge. All current thresholds are tuned gross. |
| A4 | **Report CIs and pre-register thresholds.** Every ROI quote gets SE/CI and n; adopt your own ≥200-bet reportability floor; pick the band on window A, validate once on a disjoint forward window B. | `examples/backtest_summary.json` has no uncertainty; 6 bands on one window; live gate (`run_tips_pipeline.py:1812-1826`) is a transcription of the winning in-sample band | Prevents garden-of-forking-paths: P(any of 6 bands ≥ +12% \| no edge) = 80–93%. The `crowd ≥ 100` rule was added off *one race* (`consensus_blender.py:250-255`). |
| A5 | **Report drawdown, losing streaks, and P&L concentration** in every backtest output. | At 9.9% strike: expected max losing streak ≈ 30 bets (95th pct ≈ 50); removing 2 of 14 winners turns +12.3% into −3.7% | The edge is 1–2 horses over 6 weeks. Stake sizing (Phase B) cannot be set without this. |

**Also in Phase A — serve-time bugs that silently degrade live probabilities (days, no retrain semantics):**

- **NaN destruction at serve:** training carefully preserves NaN so trees route missingness (`retrain_v2.py:684`); then `prepare_features` does `.fillna(0)` on *every* column (`ml_model.py:216`). "No sectional data" becomes `z_200m = 0` (exactly field average) for ~half your runners; "no trial" (999) becomes 0 (= trial today). Honour the NaN-preserve list.
- **Inverted interaction served by default:** `barrier_x_pace_inv` is trained as `adv × pace` but served as `adv × (1 − pace)`; the parity fix sits behind `STRIDE_INTERACTION_PARITY`, default **off** (`run_tips_pipeline.py:2316-2328`). Backtest then flip.
- **Two inference paths disagree:** `mc_api.py` populates market-movement features from real movement; `run_tips_pipeline.py` serves 0 for the same columns (never computed in training either — `retrain_v2.py:183-213`). Unify on one feature builder.
- **Calibrator stage mismatch:** the production isotonic is fitted on v1 ML-ensemble OOF predictions (`fit_calibrator.py:226-235`) but applied to Monte-Carlo `winPercentage` (`run_tips_pipeline.py:657-661`) — calibrating apples to oranges; and after the `mw` market anchor, per-race probabilities **no longer sum to 1** with no renormalisation. One final-stage calibrator + per-race renormalisation.

---

## 3. Phase B — Stop the economic leaks (staking, gates, price discipline)

These change ROI without touching the model at all.

### B1. The staking ladder is inverted against your own results
`compute_staking` stakes 2u on HIGH, 1u on MEDIUM (`run_tips_pipeline.py:1007-1015`). In the validating window itself: HIGH tier (2u) = **−37.5% ROI**, LOW (0u) = −1.7%. Your Kelly audit shows 2u ≈ 1.7× full Kelly gross and **~5–10× net of commission** — past the zero-growth point. Combined with the ~30-bet expected losing streak, 2u = a −60% bankroll drawdown the system doesn't size for.
**Fix:** drop to flat 1u until the lower 95% CI bound on net ROI exceeds 0; add a drawdown circuit-breaker; only later move to fractional-Kelly from *net-of-commission* EV using your existing `shrunk_win_probability` (`portfolio_risk.py:595-614`). Fix the variance/EV probability mismatch at `portfolio_risk.py:234-235` before any Kelly use.

### B2. Make the crowd gate gate-only — never promote
Live, a market-correlated crowd can promote a NO_BET to BET (`crowd_promoted`, `run_tips_pipeline.py:2704-2708`) — overriding the EV gate with no measured edge, contradicting your own "value, not tips" contract. **Fix:** the crowd gate can only veto or downgrade, never create a bet.

### B3. De-correlate the three pillars — market information is triple-counted
- Model pillar: 12 market features + `mw` anchor (0.30–0.80 by odds band, `run_tips_pipeline.py:679-692`) → the "model" score is 20–70% market.
- Consensus pillar: research prompts **include every runner's odds** (`consensus_agent.py:412-416`); Perplexity queries explicitly ask for "Betfair market movers"; a `market_reader` bucket is weighted 1.4 (`consensus_agent.py:86`).
- Market pillar: hard-coded neutral 50 in production anyway (`run_tips_pipeline.py:2715-2718`).

A steaming favourite therefore collects model + crowd + steam confirmations that are the *same* information → false "convergence" at full stake. Your roadmap covers model↔market double-counting; **nobody has scoped consensus↔market** — and it's worse, because the crowd gate is live. **Fix:** strip odds from consensus prompts (or A/B it), regress the crowd score on de-vigged market probability and blend only the residual, and validate or retire the never-validated 50/30/20 weights (dormant V2 even has a scale bug making LOCK/CONFIRM unreachable — `consensus_blender.py:33,37-43`).

### B4. Take better prices — execution is free ROI
Settlement is modelled at SP, discarding early-price advantage. Best-tote/BOG price shopping, top-fluc, and (structurally) Betfair exchange as the reference fair price and venue are all unticketed. Exchange mid is also a better `fair_market_probability` than bookmaker medians, and your de-vig is proportional — "the weakest published method" per your own findings; the Shin/power selector (T16) is already planned. Do it.

### B5. The tipster panel: grade on ROI/CLV, not strike — and make the score deterministic
- `crowd_score` — the number the live gate consumes — **is computed by the LLM, not code** (`consensus_agent.py:809`), with a denominator the model chooses. Non-auditable. Compute it in code.
- Tipster accuracy multipliers use `was_winner` only (`consensus_agent.py:106-138`): a favourite-tipper gets up-weighted despite negative ROI. Grade tips against tip-time odds → SP (tipster CLV), per tipster × track × distance band.
- Independence flags are fabricated by position (`is_ind = i < n_independent`, `consensus_agent.py:1473`); `source_url` is always `None`; syndicated tips counted multiple times; no demotion protocol — underperformers stay forever. Add provenance, dedup, and a panel lifecycle.

---

## 4. Phase C — Model changes that actually raise strike rate

Only worth doing after Phase A re-baselines honestly — otherwise you're tuning toward a flattered target.

| # | Change | Evidence | Expected effect |
|---|--------|----------|-----------------|
| C1 | **Measure per-race top-1 hit rate in CV** and judge all ablations on it (you already built the H2H harness for `rank_model.py`). | `retrain_v2.py:1011-1019` reports only pooled AUC/Brier; AUC deltas of ±0.001 against fold std 0.044 drive decisions | Zero model risk; immediately better decisions. Pooled AUC is dominated by cross-race separation the market feature supplies — the product sells *within-race* ordering. |
| C2 | **Race-aware objective:** add a listwise voter (CatBoost `QuerySoftMax` / LGBMRanker with race groups) as a 4th ensemble member, or at minimum a per-race softmax renormalisation before picking the top tip. One winner per race is a fixed-sum structure your pointwise binary trainers never exploit. | All trainers are per-runner binary (`retrain_v2.py:832-867`); your LambdaRank H2H failed 33.6% vs 39.7% — CatBoost ranking is the planned retry (M1/T13) | Directly targets top-pick strike rate, currently 33.7% vs favourite 34.4%. |
| C3 | **Learn the ensemble combination and persist it.** Production weights are hardcoded seed counts never updated (`ml_model.py:59-63, 512-526`; `update_model_performance` has zero callers); CV scored an equal-weight mean (a different blend than production!); the stacking meta-learner is fitted but **never pickled** (`ml_model.py:635-645`) — dead after every reload. | Cheapest probability-quality gain available: fit meta-learner on purge-gapped OOF preds, save inside the artifact, route inference through it. |
| C4 | **Honest calibration loop:** fold-level isotonic is fitted on the test fold's own predictions and LGBM early-stops on the test fold (`retrain_v2.py:835-873`) — published Brier 0.0834 is mildly self-fitted. Carve the early-stop/calibration window from the tail of train. Then one final-stage calibrator on the corrected quantity (A1 + Phase-A calibrator fix), with per-track/per-distance slices and drift monitoring. | Your calibration bands already show 0.20–0.30 under-prediction (n=265) — exactly the band where value bets live. |
| C5 | **Late-odds (T−5min) snapshot feature.** The only odds window the literature validates as smart money; your overnight→8am steam/drift misses it entirely (`odds_movement.py:410-425`); ~13 movement features are constant-zero in training. Prospective-only capture, then train the movement features for real. Your own research ranks this #1 (JRA 894k-runner study, ~14× cross-sectional effect). | The single largest expected feature gain for both strike rate and edge honesty. |
| C6 | **Sectional data coverage >47%** (Punting Form purchase ~85% AU TAB is already ticketed X2) — but pair it with the Phase-A NaN fix, otherwise added coverage is silently neutralised by `fillna(0)` at serve. | Sectionals are your most differentiated non-market features. |

---

## 5. The strike-rate question deserves its own answer: bet different markets

If "strike rate" is a genuine product goal (bankroll psychology, subscriber experience, shorter losing streaks), the biggest lever is not the win model at all:

- **Place/each-way markets.** Your MC engine already emits `place_pct` per runner (`run_tips_pipeline.py:2570`) — unused for betting. `selection_policy.py` implements each-way/place/dutching but with a crude `place_odds = 1 + (o−1)×0.25` model, zero importers, and a "must not be switched on" warning. Place strike at these odds is ~3× the win strike; favourite-longshot bias is weaker in place markets. **Fix:** shadow-track place/each-way ROI from existing place probabilities now (your shadow settler currently scores a PLACE as a full loss — `docs/10:81-82` — biasing tier ROI downward), then build a real place-price model (your docs note Harville is known-worst; discounted-Harville/Henery-Stern is ticketed as diagnostic-only).
- **Predictability selectivity** (already built, ordering-only): route more stake/only bet in high-predictability race classes. Selectivity *is* strike rate.
- Win-model strike gains from C1–C5 are real but incremental (a few points over the favourite baseline is a good outcome); the market mix decision is worth multiples of that.

---

## 6. What NOT to do (aligned with your own 14 standing prohibitions)

- Don't tune edge/price bands further until they're re-validated net of commission on price-at-tip-time data over a disjoint window.
- Don't enable Kelly in any form until the CI excludes zero and `portfolio_risk.py:234-235` is fixed (your shadow Kelly correctly refuses to apply: "the founding ROI figure carried t = 0.432").
- Don't wire `market_efficiency.py` (zero production callers, hand-set thresholds, and your prohibition #7).
- Don't add more model complexity (NNs, CL flip) — the failed LambdaRank and conditional-logit experiments both correctly rejected this direction.
- Don't headline the +12.3% to users/subscribers without its CI — it's a compliance and churn risk, not just a stats nitpick.

---

## 7. What's already genuinely good (keep)

- `DateWindowSplitter` with 14-day purge gap; date-ordered final split; calibrator refusing to fit across a race (`fit_calibrator.py:88-92`); leak-free as-of feature plumbing; LOO+noise target encoding.
- Evaluation culture: three-arm ablations on identical folds, same-race H2H that correctly rejected the ranker, bootstrap CIs in `walk_forward_backtest.py`, ≥200-bet reportability floors, `ship_criteria.py`'s NOT_REPORTABLE verdicts, docs that record failures.
- The intelligence layer (form franking ELO + graph engine, bounded/capped adjustments, confidence-tiered sample gates) is genuinely differentiated — this is where your edge over a market clone actually lives.
- The ledger design is right (both prices, staged probabilities, shadow Kelly) — it just needs switching on.

---

## 8. Sequencing

**Weeks 1–2 (all plumbing, no retrain):** A2 ledger on · A3 commission param · B2 crowd gate-only · serve-time fixes (NaN, interaction parity, per-race renormalisation, unified feature builder) · A5 drawdown/concentration reporting · strip odds from consensus prompts (A/B measured).

**Weeks 3–8:** A1 as-of-odds snapshot capture begins (prospective) · A4 pre-registered forward validation of the $2–15/≥3% band *net* · C1 per-race hit-rate in CV · C3 persist learned ensemble combination · B5 deterministic crowd_score + tipster CLV grading · place/each-way shadow tracking starts · B4 price shopping/BOG settlement.

**Months 2–4:** retrain on morning-odds features once snapshot coverage accumulates → re-baseline all metrics · C4 honest calibration loop + final-stage calibrator · C2 CatBoost ranking arm · C5 late-odds capture → movement features live · B1 staking moves from flat 1u to fractional-Kelly only after net CI > 0.

**Ongoing:** C6 sectional coverage purchase · per-track calibration slices · panel lifecycle.

**The one-sentence version:** your model's job is to know something the market doesn't; right now it is trained on, anchored to, and confirmed by the market three times over — so fix the price-timing leak, measure CLV honestly, de-correlate the pillars, bet place markets for strike rate, and let ~400 settled bets of CLV (not 142 bets of ROI) tell you when you've earned the right to scale stakes.

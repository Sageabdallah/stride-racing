# STRIDE — ACADEMIC FINDINGS (Phase 2 deliverable)

**Assembled 2026-07-25** from five parallel research streams.
Companion to `docs/analysis/SYSTEM_MAP.md` (Phase 1). Input to Phase 3.

---

## How to read this document

**Method.** Five research agents (R1–R5) were dispatched in parallel, one topic each. Every agent
was given `docs/analysis/SYSTEM_MAP.md` in full, plus `docs/12-hit-rate-research.md` and
`research/report.md` (both prior in-repo passes) as the established base. Each was instructed to
*extend* those passes, not repeat them, and to verify every claim about STRIDE by **source reading**
— the repo is deliberately not runnable end-to-end (no DB, no model artifacts, no data), so
**nothing in this document was established by execution**. All five returned. Coverage is complete:

| Stream | Topic | Findings | File |
|---|---|---|---|
| R1 | Prediction methodology | 18 | `/tmp/…/scratchpad/phase2/R1-prediction-methodology.md` (1,079 lines) |
| R2 | Probability calibration | 18 | `…/R2-probability-calibration.md` (1,003 lines) |
| R3 | Edge & market efficiency | 17 | `…/R3-edge-market-efficiency.md` (1,171 lines) |
| R4 | Staking & bankroll | 17 | `…/R4-staking-bankroll.md` (930 lines) |
| R5 | Features & data quality | 17 | `…/R5-features-data-quality.md` (927 lines) |

**No research stream is missing.** 87 findings total. This document preserves each agent's
per-finding structure — **[Finding] → [Evidence/source] → [What our system does today] → [The gap]**
— and each agent's own hedges. Where an agent flagged uncertainty, the hedge survives verbatim.

### ⚠️ The single most important caveat: nobody fetched the literature

**All five agents independently hit the same wall.** The session's organisation egress policy
returned `HTTP 403` at CONNECT (`connect_rejected … policy denial`, confirmed via
`$HTTPS_PROXY/__agentproxy/status`) for **every academic host attempted** across all five streams:
arXiv (incl. `export.` and `ar5iv.`), ScienceDirect, Wiley, Springer, OUP/academic.oup.com, INFORMS,
PMLR, PNAS, ACM DL, SSRN, JSTOR, ResearchGate, Semantic Scholar, CRAN, scikit-learn.org, NBER, LSE,
gwern.net, actamachina.com, koreascience/kjas, Wikipedia, `*.github.io`, ReadTheDocs, medium.com and
~40 more. Per `/root/.ccr/README.md` this is policy, not tooling; no agent routed around it.

**Exactly three external documents were fetched in the entire run**, all GitHub-hosted:

1. `chris-alex-p/german-horse-racing` — Benter-method replication notebook (R1, R5)
2. CatBoost `ranking_tutorial.ipynb` — the `QuerySoftMax` documentation (R1)
3. `conorwalsh99/ml-for-sports-betting` — README confirming the Walsh & Joshi corrigendum (R2)

Everything else is labelled below. **Every effect size that is not from those three sources should
be re-verified against the primary paper before it enters a Phase-4 ticket.**

> **[Audit addendum, 2026-07-25 — read this before trusting any "[snippet-only]" label.]** A later
> adversarial citation-audit pass re-tested the egress claim. **Direct fetching is still blocked** —
> `arxiv.org`, `gwern.net`, `web.stanford.edu`, `actamachina.com` all returned HTTP 403 at CONNECT,
> exactly as the five streams reported, so that caveat is honest and stands. **But `WebSearch`
> works**, and it returns publisher-abstract-level metadata (venue, volume, issue, page range,
> author list, and frequently verbatim abstract text). The five streams evidently used it — the
> `[snippet-only]` label describes precisely this — but **no stream used it to check whether the
> cited venue/volume/pages actually match the paper**, which is where fabrication shows up.
> That check has now been run on 24 sources. Results, per-source corrections, and the full tally
> are in **§ Citation audit** at the end of this document. Headline: **no fabricated paper was
> found**, but **two sources were misused, one citation resolves to a different paper, and eleven
> quoted effect sizes could not be substantiated and are now marked `[unverified]`.** Corrections
> are inlined at each affected finding, tagged `[audit 2026-07-25]`.

### Confidence labels (used identically by all five agents)

| Label | Meaning |
|---|---|
| **fetched** | The agent retrieved and read the bytes. Three external sources + all STRIDE source reads. |
| **[snippet-only]** | WebSearch returned a synthesised read quoting the page's content; the agent did not see the page. Stronger than a title-and-URL snippet, weaker than a fetch. Numbers may be miscopied by the summariser. **This is most of the literature below.** |
| **[recall — unverified]** | From the model's training data. No source retrieved this session. Used sparingly and always flagged. |
| **[prior pass]** / **[prior pass — extended]** | Established by `research/report.md` or `docs/12-hit-rate-research.md`, which record their own adversarial verification votes (3-0, 2-1, etc.). |
| **[derived — this session]** | The agent's own arithmetic on data committed in this repo (chiefly `examples/backtest_summary.json`) or its own algebra on quoted source. **These are the strongest claims in the document** — reproducible, needing no external authority. R3's F1/F2/F3/F5/F10 and R4's F1/F2/F3/F13 are of this kind. |

**All STRIDE claims are source-verified.** Every agent read the cited `path:line` this session
rather than trusting SYSTEM_MAP. One line-anchor discrepancy was found and is recorded (R2: the
"deliberately NOT applied" comment is at `ml_model.py:565`, not `:566` as SYSTEM_MAP cites).

### Lever tags

Per SYSTEM_MAP §3: *ranking quality* sets hit rate; *anchoring and gating* trade the two; *staking*
moves ROI with no hit-rate effect. Every finding carries the tag its author assigned:
**[HIT-RATE]**, **[ROI]**, **[BOTH]**, or — for the three findings that are arguments *against*
doing something — the author's own **[NEITHER]** / **[FRAMING]**. Feasibility is likewise the
author's: *implementable now* / *needs work* / *aspirational* / *do-less*.

`RTP` = `server/python/run_tips_pipeline.py`. `RS` = `racing_system_v8.3_mc.py`.

---

# R1 — Prediction Methodology

18 findings. Two external sources fetched (German Benter replication; CatBoost ranking tutorial);
45 searches run; everything else [snippet-only].

### R1-F1 — The canonical form is a within-race conditional logit, and the estimation trick that made it work is one STRIDE has never used · **[HIT-RATE]** · *within-race fitting: implementable now; exploded target: needs work*

- **Finding.** Bolton & Chapman established the multinomial logit over a race's runners (normalised
  **within race**) as the reference specification. Two details are usually lost: (a) the estimation
  sample was **200 races** across five US tracks (Aqueduct 43, Pimlico 52, Garden State 42, Keystone
  32, Suffolk Downs 31); (b) they did **not** train on "winner vs everyone else" — they used the
  **rank-ordered / "exploded" choice set** procedure of Chapman & Staelin, decomposing each race's
  full finishing order into *d* independent choice sets. The promising strategy carried a **side
  constraint eliminating long-shot betting**.
- **Evidence.** Bolton & Chapman 1986, *Management Science* 32(8):1040-1060,
  https://pubsonline.informs.org/doi/abs/10.1287/mnsc.32.8.1040. **[snippet-only]** — two
  independent searches returned the same track-level race counts and the explosion description.
  Citation count not established.
  **[audit 2026-07-25]** Venue, volume, issue, page range, the **200-race** estimation sample, the
  "recently developed procedure for exploiting the information content of **rank ordered choice
  sets**" and the "**side constraint eliminating long-shot betting**" are all **CONFIRMED** against
  the INFORMS/RePEc/Semantic Scholar abstract. The **track-level split (Aqueduct 43, Pimlico 52,
  Garden State 42, Keystone 32, Suffolk Downs 31) is [unverified]** — it does not appear in any
  abstract-level record reachable this session and the full text (gwern PDF, INFORMS, ResearchGate)
  is still 403. It sums to 200, which is consistent but not evidence. Do not quote the split.
- **What STRIDE does today.** Target is pointwise binary `is_winner`. **[audit: the anchor
  `retrain_v2.py:219` is WRONG — line 219 is `"svi",` inside `FEATURE_COLUMNS`. The target is
  selected in SQL at `retrain_v2.py:299`, filtered at `:336`, and bound at
  `retrain_v2.py:1433` (`y = df_raw["is_winner"].astype(int).reset_index(drop=True)`). The
  claim is correct; the citation was inherited from SYSTEM_MAP §2 step 7 and is not.]** Nothing
  groups by race in any `.fit()` call. Finishing position beyond 1st is **thrown away**. The MC
  engine *is* race-relative (Plackett-Luce, `racing_system_v8.3_mc.py:1855-1856`) but is a
  hand-weighted prior simulator (`FEATURE_WEIGHTS`, 17 weights, `:46-63`), not a fitted model.
- **The gap.** Two, and the second is unnamed anywhere: (i) no within-race fitting [prior pass —
  extended, docs/12 §3]; (ii) **label starvation** — with ~8,995 races and one positive each, the
  pointwise trainer sees ~9k informative events, where an exploded target yields (fieldsize−1)
  nested comparisons per race, an order of magnitude more signal **from identical data**.
  `race_results_history` holds the full results (45,070 rows backfilled); the *view* and *target* do not.

### R1-F2 — Production ML probability is not race-normalised while the backtest that produced the headline numbers is · **[BOTH]** · *implementable now*

- **Finding.** A pointwise binary classifier's per-runner probabilities do not sum to 1 across a
  race; every published treatment renormalises within race before use. STRIDE does this in the
  backtest and **not** in production.
- **Evidence.** The methodological point ("binary classification approaches estimate winning
  probabilities that do not sum to one across a race … the binary win/loss target within each race
  is considered as independent, which it clearly is not as only one horse can win") returned against
  West et al., *Journal of Prediction Markets*, and Lessmann, Sung & Johnson 2009, *EJOR*
  196(2):569-577. **[snippet-only]**. The STRIDE half is **fetched** from local source.
- **What STRIDE does today.** `RTP:2323` writes `mlPredictedProb = round(ml_prob*100, 2)` — raw
  `predict_proba`, no grouping. `RTP:665-670` linearly averages it into the field-normalised MC
  probability. `backtest_v2_metro.py:174-176` race-normalises `model_prob → norm_prob` and **every**
  reported metric — calibration bins `:241`, top-pick `:266`, edge `:269`, EV `:270`, all six bands
  — is computed on `norm_prob`.
- **The gap.** The README's **33.7% / −4.2%** and **9.9% / +12.3%** were produced on `norm_prob`.
  Production scores a differently-scaled quantity, and `rawModelProb` feeds the flat-MC detector
  (`RTP:703-705`), 20–30% of `prob_score`, the conviction ladder (raw ≥15/12/10), the bet-gate floors
  (≥30/15/10) and the longshot rules (raw ≥8). Every one of those constants was tuned against numbers
  the live system does not produce. Compounds SYSTEM_MAP §7b.4. **Caveat for the ticket:** normalising
  changes the scale, so those constants must be re-read on the same run, not ported.

### R1-F3 — All three base learners carry class-imbalance corrections, which two studies show destroys calibration with no AUC gain · **[BOTH]** · *implementable now — cheapest high-impact change in R1*

- **Finding.** Random under/over-sampling, SMOTE and class weighting all make models
  **systematically over-estimate the minority-class probability** with **no AUC gain**. The 2025
  simulation extension found that in **all** simulated scenarios uncorrected models had equal or
  better calibration, and the induced miscalibration "was not always able to be corrected with
  re-calibration."
- **Evidence.** van den Goorbergh, van Smeden, Timmerman & Van Calster 2022, **JAMIA**
  29(9):1525-1534, https://academic.oup.com/jamia/article-abstract/29/9/1525/6605096. Carriero,
  Luijken, de Hond, Moons, Van Calster & van Smeden 2025, **Statistics in Medicine** 44(3-4):e10320,
  https://onlinelibrary.wiley.com/doi/full/10.1002/sim.10320. Both **[snippet-only]**.
  **[audit 2026-07-25]** Both citations **RESOLVE** — van den Goorbergh 2022 is confirmed at JAMIA
  29(9):1525-1534 with the finding "**all imbalance correction methods led to poor calibration
  (strong overestimation of the probability to belong to the minority class)**" and no effect on
  discrimination; Carriero 2025 is confirmed at *Statistics in Medicine* 44(3-4):e10320 by title.
  **Two sub-claims are downgraded to [unverified]:** (a) "in **all** simulated scenarios uncorrected
  models had equal or better calibration" and (b) the quote "**was not always able to be corrected
  with re-calibration**" — neither was recoverable from any abstract-level record this session, and
  (b) is in direct tension with arXiv 2606.29720 (see R2-F4 audit), which reports that post-hoc
  Platt/isotonic recalibration **eliminates** resampling-induced calibration damage (ECE −66%).
  Also note van den Goorbergh's own free preprint is **arXiv 2202.09101** and should be re-read
  before this finding is turned into a ticket. *Transfer
  note:* both are clinical-prediction papers **fitted with logistic regression, not tree
  ensembles** — the transfer to XGB/LGB/CatBoost is an assumption, not a result; the transfer is
  about what re-weighting does to the link function's **intercept**, and racing's 9.7% base rate
  (`BASELINE_WIN_RATE = 0.097`) is squarely in the studied range.
- **What STRIDE does today (fetched).** XGB `"scale_pos_weight": 9` (`retrain_v2.py:774`), LGB
  `"is_unbalance": True` (`:791`), CatBoost `"auto_class_weights": "Balanced"` (`:802`); v1 trainer
  the same at `ml_model.py:292-298, 423-437`. Per-model OOF isotonic calibrators are fitted and
  **deliberately not applied** (`ml_model.py:566`), on the stated ground that pipeline-level isotonic
  already calibrates the final output.
- **The gap.** That justification does not hold as coded: the pipeline isotonic runs at `RTP:657-661`
  on the **MC** arm, *before* the ML blend at `:665-668`. **Nothing calibrates `mlPredictedProb`,
  ever**, unless a stacking learner or double calibrator exists — unknown (SYSTEM_MAP §9 Q1-2).
  Arithmetic: `scale_pos_weight = 9` multiplies fitted odds by ~9, so true p = 0.10 emits ≈0.50 and
  p = 0.03 emits ≈0.22; blended at `ml_w = 0.40`, `rawModelProb` for an average runner inflates
  toward ~26 rather than ~10. Most likely explanation for the in-code note at `RTP:676-678`
  (*"$1-3 horses win 41%, model predicts 17% after blend"*).

### R1-F4 — Benter's two-stage architecture, with the numbers, the data volume, and the one replication that could be fetched · **[HIT-RATE]** · *mechanics implementable now; provenance needs work*

- **Finding.** Benter fitted a **fundamental** MNL first, then a **second** conditional logit whose
  only regressors are `ln(fundamental prob)` and `ln(public prob)`. Over 1988-1993 pseudo-R² was
  **0.1218 public-only / 0.1245 fundamental-only / 0.1396 combined** — the blend beats *both*, and
  the gain over the better input (+0.0151, **+12.1% relative**) exceeds the gap between the inputs
  (0.0027). On data volume he is explicit: *"a sample of approximately 2000 races … the minimum
  amount of data needed … is in the range of 500 to 1000 races. More is helpful, but out-of-sample
  predictive accuracy does not seem to improve dramatically with development samples greater than
  1000 races."*
- **Evidence.** Benter 1994, in *Efficiency of Racetrack Betting Markets* ch. 19,
  https://www.worldscientific.com/doi/10.1142/9789812819192_0019. R² triple **[snippet-only]** but
  triple-sourced (search + `docs/12:44` + `conditional_logit.py:21-23`); data-volume quote
  **[snippet-only]**, returned near-verbatim.
  **[audit 2026-07-25] The R² triple 0.1218 / 0.1245 / 0.1396 is downgraded to [unverified].** The
  chapter itself resolves (World Scientific `_0019`, Semantic Scholar, Blackwell's) and its
  abstract-level content — MNL after Bolton & Chapman, "a logit-based technique … for combining a
  fundamental handicapping model with the public's implied probability estimates", five years of
  live results — is confirmed. **No search this session returned any of the three numbers**, and
  actamachina, gwern and semanticscholar full text remain 403. Note the "triple-sourcing" is
  circular: `docs/12:44` and `conditional_logit.py:21-23` are both *STRIDE's own* restatement of the
  same unverified figure, so there is **one** unverified source, not three. **The data-volume quote
  ("500 to 1000 races … does not seem to improve dramatically … greater than 1000 races") is
  likewise [unverified]** — not recovered this session. R1-F18's framing rests on it.
  **FETCHED replication:**
  `github.com/chris-alex-p/german-horse-racing` — German Ausgleich IV 2019-2023, **5,625
  runner-observations / 524 wins**, explicitly a **one-step** CL, odds coefficient **−0.0835
  (p<0.001)**, concordance **0.738**, LR **χ² = 352.8 on 13 df**, out-of-sample **914 one-euro bets
  → €54.90 profit (≈ +6.0% ROI)** against **15% takeout**, bootstrap **p = 0.0218**. It reports no
  separate model-only/market-only R², so it does **not** independently confirm 0.1396.
- **What STRIDE does today (fetched).** `conditional_logit.py` implements `P_i ∝ exp(α·ln m_i +
  β·ln q_i)` (`:92`), L-BFGS-B on race-conditional NLL (`:125-136`), holdout 0.2 (`:117-119`), min 50
  races. Hooked at `RTP:591-629` behind `STRIDE_CL_BLEND`, default off, on HOLD (constraint 14).
  First real fit (docs/12 §5.1): 1,227 races, 245-race holdout, **α = 1.296, β = 0.000**.
- **The gap.** Not "build the CL blend" — it exists. The gap is **why β came out 0** (see R1-F12).
  The data-volume finding kills a common excuse: STRIDE has ~8,995 races and 119,577 view rows, i.e.
  **~4.5× Benter's development sample and ~9-18× his stated minimum.** Data volume is not the binding
  constraint; **specification is.**

### R1-F5 — Linear pooling of two probability forecasts is provably uncalibrated, and STRIDE's market anchor is a linear pool with no recalibration after it · **[BOTH]** · *implementable now (beta transform); log-pool option needs work*

- **Finding.** **Any non-trivial weighted average of two or more distinct, calibrated probability
  forecasts is necessarily uncalibrated and lacks sharpness.** Linear pooling *requires*
  recalibration even when both inputs are perfect, and the failure is directional — the pool is
  **under-confident**, shrinking probabilities toward the middle. The authors' remedy is a
  **beta-transformed linear opinion pool**.
- **Evidence.** Ranjan & Gneiting 2010, **JRSS-B** 72(1):71-91,
  https://academic.oup.com/jrsssb/article/72/1/71/7076442. **[snippet-only]** — the theorem statement
  returned near-verbatim by two independent searches; heavily cited, exact count not established.
  Companion: Satopää, Baron, Foster, Mellers, Tetlock & Ungar 2014, **IJF** — the logit aggregator,
  "a variant of the logarithmic opinion pool", built because linear pooling "tends to produce
  underconfident aggregates". **[snippet-only]**. *Transfer:* general aggregation results; STRIDE's
  anchor *is* a two-source probability pool, so the transfer is exact.
- **What STRIDE does today (fetched).** `RTP:679-692` — `mw` = 0.80/0.70/0.50/0.45/0.40/0.30 by price
  band; `calibrated = mw*raw + (1-mw)*true_market`. The only calibration layer that can fire (global
  isotonic, `calibration_model.py:30-32`, `y_min=0.01, y_max=0.95`) runs at `RTP:657-661` — **before**
  the pool, on the MC arm only. **No recalibration exists anywhere downstream.** `modelEdge =
  calibrated − true_market` (`:697`) reads that un-recalibrated quantity.
- **The gap.** STRIDE sits exactly on the failure the theorem describes, and its own evidence shows
  the signature: docs/12 §5.1(a) records the stored pick is *"too flat at the top"*; the README
  calibration table under-predicts the 0.20-0.30 bin. Two fixes: replace the pool with the log-space
  CL blend (F6), or keep it and fit a beta recalibration **after** it — the latter does not reorder
  the pipeline (dodges constraint 6) and fits on `prediction_audit.final_win_prob`, which
  `store_final_probs_in_audit` (`RTP:1582`) already writes for every runner. Continuing to tune `mw`
  cannot fix a structural miscalibration.

### R1-F6 — Benter's second stage is a logarithmic opinion pool; mc_api already blends in log space while the wrapper blends linearly · **[BOTH]** · *implementable now as opt-in*

- **Finding.** `P_i ∝ exp(α·ln m_i + β·ln q_i)` is term-for-term a **weighted geometric mean**
  renormalised — the logarithmic opinion pool. Log pooling is externally Bayesian and preserves the
  sharpness linear pooling destroys. It subsumes two corrections free: with α=0 it reduces to
  `P ∝ q^β`, **exactly the power de-vig of F7**; and β ≠ 1 is a fitted FLB correction (β>1 sharpens
  toward favourites, β<1 flattens toward longshots).
- **Evidence.** Log-pool framing from Satopää et al. 2014 and Ranjan & Gneiting 2010 (above),
  **[snippet-only]**. The `exp(β ln q)` ↔ power-method equivalence is arithmetic, not a citation.
- **What STRIDE does today (fetched).** `mc_api.py:7379` builds `combined_adjustment` and applies it
  **multiplicatively** (`:7398`), then **renormalises the field to 100** (`~:7612-7617`) —
  multiply-then-renormalise **is** an additive tilt of the Plackett-Luce logits, i.e. log-space
  blending. `RTP:667` and `RTP:692` then do the MC↔ML merge and the market anchor **linearly, per
  runner, with no renormalisation**.
- **The gap.** Two different pooling algebras in one pipeline, the principled one upstream and the
  provably miscalibrated one at the point where money is decided. Nothing in the docs records this
  as a decision.

### R1-F7 — STRIDE's de-vig is the weakest published method, and it biases modelEdge in a predictable direction · **[ROI]** · *implementable now — R1's single best quick win*

- **Finding.** Proportional normalisation is the least accurate odds→probability conversion.
  Shin beats basic normalisation **for all bookmaker/sport pairs tested**. The **power method**
  (`p_i ∝ (1/o_i)^k`) "**universally outperforms the multiplicative method and outperforms or is
  comparable to the Shin method**", never leaves [0,1], allows for FLB by construction, and is
  "conceptually simpler and generally easier to implement than Shin." Basic normalisation's named
  defect: it "does not account for favourite long-shot bias."
- **Evidence.** Štrumbelj 2014, **IJF**,
  https://www.sciencedirect.com/science/article/abs/pii/S0169207014000533 — **83 citations** per
  scispace. Clarke, Kovalchik & Ingram 2017, **American Journal of Sports Science** 5(6),
  https://sciencepublishinggroup.com/article/10.11648/j.ajss.20170506.12. Shin 1993, **Economic
  Journal** — predicts margins increase with the number of competitors, so the correction is
  field-size dependent. All **[snippet-only]**. Štrumbelj covers horse/greyhound racing; Clarke et al.
  is race-market work — transfer to AU fixed odds is direct.
- **What STRIDE does today (fetched).** `calculate_overround` at `RTP:432-442` — proportional only,
  and it **returns 1.0 (no vig removal at all) below 2 quotes**. SYSTEM_MAP §8: *"De-vig is
  proportional only — no Shin, power or Harville variant exists anywhere in the repo."*
- **The gap.** `true_market` is 20–70% of the shipped probability and the **entire subtrahend** of
  `modelEdge`. Direction of the bias, stated nowhere in the repo: proportional de-vig **understates
  the fair probability of favourites and overstates it for longshots**, so STRIDE **systematically
  inflates `modelEdge` on short prices and deflates it on long ones**. With `odds > 15 → NO_BET`
  (`RTP:1812`) and the tightest gate at `odds < 3`, essentially the whole live bet population sits in
  the inflated band. Candidate mechanical explanation for "top pick wins 33.7% but loses 4.2%".
  **Ticket must state:** changing de-vig moves `modelEdge` for every runner, so the 4.0/2.5/3.0
  thresholds must be re-read on the same run, never ported.

### R1-F8 — STRIDE's place/top-3 probabilities are Harville probabilities, and Harville is the known-worst ordering model · **[HIT-RATE]** · *diagnostic implementable now; production change needs work; Henery/Stern fit aspirational*

- **Finding.** Sampling a finishing order by Gumbel-max over log-strengths (Plackett-Luce) is
  **mathematically identical** to the Harville (1973) model. Henery (1981, normal running times) and
  Stern (1990, gamma) fit better; conditional-logistic analysis on Hong Kong data showed **Henery is
  superior to Harville** for ordering probabilities. The error direction is documented: such models
  "**overestimate the probability of a horse finishing second or third when the horse has a high
  probability of such a result, and underestimate it when this probability is low**" — Harville
  **over-states favourites' place chance**. Lo & Bacon-Shone's **discounted Harville** replaces the
  running-total denominator with a power discount `p^α / Σ p^α`, **α = 0.89 for 2nd, α = 0.80 for
  3rd** — two constants, no simulation.
- **Evidence.** Harville 1973 / Henery 1981 / Stern 1990 / Bacon-Shone, Lo & Busche 1992, surveyed in
  Lo & Bacon-Shone, *Probability and Statistical Models for Racing*,
  https://www.researchgate.net/publication/4748916 and the NESSIS 2007 talk. **[snippet-only]** across
  three independent searches, all agreeing on α = 0.89/0.80 and the bias direction. ⚠️ One returned
  figure — "overestimating high-probability horses by 7.34% … across more than 15,000 races" — could
  **not** be tied to a specific paper and is **not relied on**.
- **What STRIDE does today (fetched).** `racing_system_v8.3_mc.py:1855-1856` (`noise = rng.gumbel`,
  `argsort(-(logits+noise))`) → `top3_probs` at **`:1861`** (**[audit: the doc said `:1858`; that
  line is blank. `win_probs` is `:1859`, `top2_probs` `:1860`, `top3_probs` `:1861`]**); place probabilities renormalised to **300.0**
  for fields ≥3 (`mc_api.py:~7620-7623`) — the Harville "sum to 3" constraint. `top3` carries **12% of
  `mc_selection_score`**, which is **50% of the final selection score** (`RTP:776`) — ~6% of the
  ranking that picks the bet.
- **The gap.** Nobody in the repo has noticed the MC *is* Harville — SYSTEM_MAP §8 lists
  "Plackett-Luce" and "Harville" as separate glossary concepts. Because the bias inflates favourites'
  place chance, the 12% `top3` term is a **hidden favourite-tilt inside a system whose philosophy
  forbids tipping the favourite on price alone** (constraint 17). Becomes a large ROI lever only if
  STRIDE ever prices place/exotics, which it currently cannot (shadow P&L settles `PLACE = −1`).

### R1-F9 — Learning-to-rank does beat pointwise, but STRIDE tested the wrong ranking objective, and the right one ships inside a dependency it already has · **[HIT-RATE]** · *implementable now, evidence-only, no flag needed*

- **Finding.** Two literatures, conflated in docs/12. **(a)** Pairwise beats pointwise for racing;
  the 2024 Seoul study found pairwise "generally outperforms" pointwise with **CatBoost Ranker the
  best model tested**. **(b)** Listwise beats pairwise, and **the listwise top-1 loss *is* the
  conditional logit**: ListNet's top-1 loss "reduces to a softmax and simple cross entropy" and beat
  RankNet by **over 10% in NDCG across three datasets**; ListMLE's loss *is* the Plackett-Luce
  likelihood with proven consistency and soundness. For one-relevant-item-per-group with top-1 hit
  rate as the metric of record, the matched loss is the listwise top-1 softmax — Bolton & Chapman's
  conditional logit with a GBM index. LambdaRank optimises NDCG surrogates; with `label_gain=[0,1]`
  its λ-gradients are computed over pairs whose gain differences are all identical.
- **Evidence.** KJAS 2024 **37(2):239**, https://www.kjas.or.kr/journal/view.html?doi=10.5351/KJAS.2024.37.2.239
  **[snippet-only]**. ⚠️ **Caution:** searches returned "CatBoost achieved NDCG = 0.8895, MAP = 0.4204"
  *alongside* a description of a **different** paper (KRA, **9,140 race records**, listwise LambdaRank,
  JKSCI). Neither was fetchable, so **those metric values are deliberately not attributed to either
  paper.** Cao et al. 2007 ICML (ListNet) and Xia et al. 2008 ICML (ListMLE), both **[snippet-only]**.
  **FETCHED**, verbatim, from CatBoost's `ranking_tutorial.ipynb`: *"### A special case: top-1
  prediction … For this purpose CatBoostRanker has a mode called **QuerySoftMax**. Suppose our dataset
  contain a binary target: 1 — mean best document for a query, 0 — others. We will maximize the
  probability of being the best document for given query."* That is the conditional-logit likelihood
  with a GBDT index, in a library already in `requirements.txt` and already a base learner
  (`retrain_v2.py:802`).
- **What STRIDE does today (fetched).** `rank_model.py:52-66` — LightGBM `"objective": "lambdarank"`,
  `label_gain=[0,1]`, races as query groups, correct 60/14/14/14 purge-gapped walk-forward. Verdict
  `docs/12:396`: *"criterion FAILED — the ranker stays evidence-only, no pipeline wiring"* — same-race
  H2H on 996 races: stored model **39.7%**, market favourite **34.4%**, ranker **33.6%**. Zero
  importers (constraint 15).
- **The gap.** The failed experiment tested an **NDCG-surrogate pairwise** objective on a
  one-relevant-item problem. The literature's claim is that CatBoost is the strongest family member
  (`research/report.md` rec 5 already says re-test with CatBoost [prior pass — extended]) **and** that
  the listwise top-1 softmax is matched to both the problem structure and the metric — and unlike
  LambdaRank it emits a **within-race probability**, so it can enter as a probability rather than only
  as the "ordering signal" compromise docs/12 §5.4 was forced into. **Honest limit:** no published
  top-1 hit-rate lift for ranking on racing data was found, so this is a hypothesis for STRIDE's own
  harness, not a promised gain — and the same-race H2H must decide it.

### R1-F10 — If a nonlinear conditional logit is wanted, the principled published form is GBDT inside the utility function · **[HIT-RATE]** · *needs work — second in line behind F9*

- **Finding.** RUMBoost replaces each linear parameter in a random-utility model's utility functions
  with an **ensemble of gradient-boosted regression trees**, keeping the softmax/MNL choice structure
  intact, plus constraints that each alternative's utility depends only on that alternative's
  attributes and that marginal utilities are monotone. Strong predictive performance *and*
  interpretability against both ML and RUM benchmarks.
- **Evidence.** Salvadé & Hillel 2024, **Transportation Research Part C** (arXiv 2401.11954);
  implementation https://github.com/big-ucl/rumboost. **[snippet-only]** for the paper; the repo
  exists and is reachable but its code was not read. *Transfer:* the application is transport mode
  choice, but a horse race **is** a discrete-choice problem with one chosen alternative per situation
  and alternative-specific attributes — structurally identical.
- **What STRIDE does today.** Nothing of this kind. The two options are the pointwise GBM ensemble
  and the hand-weighted Plackett-Luce prior (17 weights, `RS:46-63`).
- **The gap.** STRIDE has *either* a fitted model with the wrong structure *or* the right structure
  with unfitted weights. RUMBoost/QuerySoftMax is the combination. Given F9 achieves the same
  mathematical form with an existing dependency, RUMBoost is the **fallback**, not the first try.

### R1-F11 — Chapman 1994: a model-probability floor, not an odds ceiling, is what turned a break-even MNL into >20% returns · **[ROI]** · *implementable now as a measurement*

- **Finding.** Chapman applied a **20-variable pure-fundamental MNL** to **2,000 Hong Kong races**
  and got clear evidence of positive hold-out returns at flat unit stakes. The step-change:
  **eliminating extreme longshots with estimated win probability below 0.04** produced **expected
  returns in excess of 20%**. The filter is on the **model's** probability, not the market's price.
- **Evidence.** Chapman 1994, *Still Searching for Positive Returns at the Track: Empirical Results
  from 2,000 Hong Kong Races*, in *Efficiency of Racetrack Betting Markets* ch.18
  (https://www.worldscientific.com/doi/10.1142/9789812819192_0018). **[snippet-only]** — the
  p̂ < 0.04 threshold and ">20%" were returned as a single quoted claim.
  **[audit 2026-07-25] CONFIRMED**, near-verbatim, against the World Scientific / RePEc /
  Semantic Scholar abstract: "a **20-variable pure fundamental multinomial logit** handicapping
  model to **2,000 Hong Kong races** … **by eliminating extreme longshots with estimated win
  probabilities less than 0.04, expected returns in excess of 20% are observed**." This is one of
  the better-supported literature claims in the document. *Transfer note:* Hong Kong, parimutuel, 1990s; the *structural* lesson
  transfers, the *magnitude* does not.
- **What STRIDE does today (fetched).** `evaluate_bet_candidate` leads with a **market-side** cut:
  `odds > 15 → NO_BET` (`RTP:1812`). Probability floors exist but are conditional on price band and
  use `prob = max(raw_model_pct, win_pct)` — the **more favourable** of the pre- and post-anchor
  numbers: `<$3` needs ≥30, `$3-5` ≥15, `$5-15` ≥10 (`RTP:1816-1825`). No unconditional
  model-probability floor, and the tightest gate applies where the model is most confident.
- **The gap.** SYSTEM_MAP §9 Q8 asks whether the `odds > 15` ceiling costs or saves money. Chapman
  says the *shape* may be wrong: an odds ceiling removes runners the market dislikes; a
  model-probability floor removes runners **the model cannot price**. Not the same set — a $12 runner
  the model has at 18% survives both; a $9 runner the model has at 3% survives STRIDE's filter and
  fails Chapman's. Note `prob = max(...)` is a deliberately optimistic reading of a quantity F2/F3
  show is inflated.

### R1-F12 — Why β came out 0: the fundamental model is contaminated with market features · **[BOTH]** · *implementable now (ablation arm); artifact is a first stage, not a replacement*

- **Finding.** A **one-step** model puts fundamental *and* market variables into a single conditional
  logit; a **two-step** model estimates a fundamental probability from **fundamental variables only**,
  then feeds it into a second stage alongside the market probability. Both can earn positive returns,
  but "**the two-step approach appears to provide advantages**". Benter's architecture is two-step,
  and the second stage's β is only *identified* if the first stage is market-free.
- **Evidence.** Sung & Johnson 2012, *Comparing the Effectiveness of One- and Two-Step Conditional
  Logit Models*, **The Journal of Prediction Markets**. **[snippet-only]**. Corroborated by the
  **fetched** German replication, which chose the one-step route ("a one-step approach is utilized")
  and consequently reports **no separable model-vs-market coefficients at all** — only a pooled odds
  coefficient of −0.0835. Same identification collapse, second dataset.
- **What STRIDE does today (fetched).** The 113-column contract contains **market variables**:
  `market_odds`, the 16 steam/drift features, and the Phase-5 trio `fair_implied_prob` / `odds_rank` /
  `odds_rank_pct`. The CL blend is fitted **on the output of that model**. Result: **α = 1.296,
  β = 0.000**.
- **⚠️ [audit 2026-07-25] — CORRECTION THAT CHANGES THIS FINDING'S DIAGNOSIS.** Neither R1 nor R5
  read the optimiser call. `conditional_logit.py:134-135` is
  `minimize(nll, x0=np.array([0.5, 0.7]), method="L-BFGS-B", bounds=[(0.0, 5.0), (0.0, 5.0)])` —
  **β is box-constrained to `[0, 5]`, so `β = 0.000` is a solution sitting exactly on the lower
  bound, not an interior optimum.** A corner solution at zero is what you observe whenever the
  *unconstrained* MLE is ≤ 0. Contamination (this finding) is one explanation; an unconstrained
  optimum that is genuinely **negative** — i.e. the market term entering with the wrong sign once
  the model probability already embeds SP (R5-F2) — is another, and the two imply different fixes.
  **Nothing in the repo or in either stream distinguishes them.** Any Phase-3/4 work on F12 must
  begin by refitting with the lower bound relaxed to (say) `-5.0` and reporting the sign, before
  building the market-free fundamental arm. This is cheap (one argument) and it is a precondition,
  not an option.
- **The gap.** docs/12 §5.1(c) diagnoses the symptom [prior pass — extended]. The literature adds the
  **prescription**: the fix is not "fit at the final stage instead" — same contamination, more of it.
  The fix is a **market-free fundamental arm**: a second artifact on the contract **minus**
  `market_odds`, minus the 16 market features, minus the Phase-5 trio, used as `m_i`. Only then does β
  measure the market's independent contribution and only then can Benter's +0.0151 gain even in
  principle be reproduced. Two in-repo facts point the same way: the Phase-5 ablation at **−0.0012
  AUC** (they add nothing but *do* absorb the market channel), and `research/report.md`'s conclusion
  that "transforms of prices add no information."

### R1-F13 — Model class has never been the differentiator; race-relative formulation has · **[NEITHER — do-less]** · *N/A*

- **Finding.** Multiple model classes have earned positive out-of-sample returns once race-relative
  structure was respected: **random forest** on 1,000 HK races 2005-2006, **12,902 horses**, "can be
  used to make substantial profits and outperform traditional statistical techniques"; **SVM**
  "compares favourably with state-of-the-art alternatives" — the same paper naming the failure mode,
  **"SVM is unable to take relationships among individual runners (data points) into account"**; and
  **SVM on Australian data**, 200 races in-sample / 100 out, 12 inputs, "promising results".
- **Evidence.** Lessmann, Sung & Johnson 2010, **IJF** 26(3):518-536; same authors 2009, **EJOR**
  196(2):569-577; Edelman 2007, **Annals of Operations Research** 151:325-336 — the only
  **Australian-data** modelling paper located. All **[snippet-only]**.
- **What STRIDE does today.** Three GBM classes ensembled, weighted by **hardcoded seed accuracies**
  (`ml_model.py:59-63`, e.g. mile → 0.3333/0.3030/0.3636) that are never persisted — the "dynamic
  weighting" is static invented constants.
- **The gap.** Model-class diversification is where STRIDE spent its complexity budget (3 GBMs +
  stacking + double calibration + 4 MC engines) and is the dimension the literature says matters
  **least**. The invented weights are a symptom: nobody could measure which member was better because
  none is fitted to the right objective. **Practical use in Phase 3/4: reject tickets that add model
  classes** and redirect that budget to F2/F3/F7/F9. Scale sanity check: Edelman got publishable AU
  results from 300 races; STRIDE has ~9,000.

### R1-F14 — Do not replace the GBMs with a neural network · **[NEITHER — anti-recommendation]** · *N/A*

- **Finding.** On **45 tabular datasets** with hyper-parameter search across standard and novel deep
  methods versus trees, **tree-based models remain state of the art on medium-sized data (~10K
  samples)**, even ignoring speed. Neural nets on tabular data are not robust to uninformative
  features and do not preserve data orientation.
- **Evidence.** Grinsztajn, Oyallon & Varoquaux 2022, **NeurIPS** 35:507-520, arXiv:2207.08815.
  **[snippet-only]**; very heavily cited (>1,000, exact count not established). *Counter-evidence
  noted honestly:* the standing dispute that well-tuned regularised MLPs can match GBDTs; and Koker's
  HK model (**938 races over 14 months**, shared rating network + **softmax over the race**)
  **[snippet-only]**, blog, not peer-reviewed — but what makes it work is the **softmax over the
  race**, not the network, which is the F9 point.
- **What STRIDE does today.** GBM ensemble on 113 features, ~8,995 races. `focal_loss.py` exists and
  is never wired.
- **The gap.** None — this **confirms** STRIDE's model-class choice. Its value is pre-emptively
  closing off a tempting and expensive direction.

### R1-F15 — Random effects for horses/jockeys are the published route to STRIDE's dormant Glicko module · **[HIT-RATE]** · *Glicko-as-features implementable now; full MCMC frailty aspirational — do not propose in Phase 4*

- **Finding.** Conditional-logit models have been extended with **random effects / frailty terms**
  estimating unobservable per-horse and per-jockey effects *simultaneously* with the coefficients, via
  MCMC (with Ancillarity-Sufficiency Interweaving) and WAIC selection; the hierarchical structure is
  what makes estimating "such a large amount of individual effects" stable. One design choice is
  directly decision-aligned: **LASSO regularisation tuned to maximise betting profit rather than
  likelihood.**
- **Evidence.** Silverman 2012, **JPM** 6(1):1-13; Silverman & Suchard 2013, **JPM** 7(1):43-52. Both
  **[snippet-only]**. ⚠️ `research/report.md` §2.4 records that the *method* verified 3-0 but the
  paper's headline **36.73% ROI claim was refuted 0-3** [prior pass — extended]. **Adopt the
  mechanism, not the return.** Also in family: Metel 2017, arXiv:1701.02814 — stochastic optimisation
  for staking when probabilities come from an MNL and carry known error. **[snippet-only]**.
- **What STRIDE does today.** `glicko2_elo.py` — per-surface rating with deviation and volatility,
  self-tested, in CI, **no production caller**. A per-horse latent-ability estimate with explicit
  uncertainty, unused.
- **The gap.** STRIDE has built half a frailty model and never connected it. The cheap version is to
  feed `(glicko_rating, glicko_deviation)` in as features and read the ablation. The profit-aligned
  regularisation idea is separately worth carrying into any CL refit (`research/report.md` rec 6
  already says so [prior pass — extended]).

### R1-F16 — MC win probabilities carry ~1pp of pure sampling noise against a 3.0pp edge gate, and the noise is removable exactly, for free · **[BOTH]** · *implementable now, ~5 lines*

- **Finding.** Arithmetic on STRIDE's own code, resting on the standard Gumbel-max ↔ softmax
  equivalence. Binomial SE at p = 0.30: **1.02pp at N=2000**, **0.84pp at N=3000**, **0.65pp at
  N=5000** (**[audit 2026-07-25: all three reproduce exactly from `sqrt(0.3·0.7/N)`]**). Two
  independent scorings of the same race differ by **±2.84pp (95%)** at N=2000 — **[audit: the
  original text said ±2.0pp. That is the 95% half-width of a *single* scoring (1.96 × 1.02pp). The
  SE of the *difference* between two independent scorings is 1.02·√2 = 1.45pp, so the 95% interval
  is ±2.84pp. The correction makes this finding stronger, not weaker: the run-to-run swing exceeds
  the entire 3.0pp edge gate]** — and the
  daily seed is `int(time.time()) % 100000` (`RTP:2340`, **[audit: verified]**), so they *are* independent. Against
  `edge ≥ 3` (`RTP:1825`), conviction steps at 3/2/1 (`RTP:836-843`) and `mc_spread < 6.0`
  (`RTP:705`), **sampling noise alone is a material fraction of every threshold.** The fix is exact
  and cheaper: by the law of total variance, averaging the analytic `q_i = softmax(logits_s)_i` over
  draws (Rao-Blackwellisation) removes the entire `E_s[q(1−q)]` term — the dominant one at mid-range
  probabilities (≈0.21 at q=0.30) — and replaces an `argsort` with a `softmax`.
- **Evidence.** Gumbel-max ↔ softmax and Rao-Blackwellisation are textbook; no canonical reference
  could be fetched (Wikipedia and arXiv both 403), so **[recall — unverified]** for the attribution.
  The mathematics is checkable in three lines and the STRIDE side is **fetched** from source.
- **What STRIDE does today.** Empirical argmax frequency over `mc_sims` draws (`RS:1855-1858`); sims
  by field size 5000/3000/2000 (`RTP:389-394`). *Only the win probability is analytic per draw* —
  place/top-3 genuinely need the simulation, so this is targeted, not a rewrite.
- **The gap.** SYSTEM_MAP §7b.7 flags non-reproducibility as an A/B hazard and recommends fixing the
  seed — the right *test-time* mitigation, but it hides the issue: the daily production number itself
  is noisy. Free by-product: `Var_s(q_i)` is a per-runner **model-uncertainty** estimate — the input
  R4's shrunken-Kelly work needs, and a better basis for the `stability` term.

### R1-F17 — Isotonic is the wrong calibrator for this data volume, and it is applied at the wrong point in the chain · **[BOTH]** · *family swap implementable now; position change needs work — sequence after F5*

- **Finding.** Isotonic "is a powerful non-parametric method that is however **prone to overfitting
  on smaller datasets**". **Beta calibration** — a 3-parameter map fitting a logistic regression on
  `log(s)` and `log(1−s)` — "**beats both Platt scaling and isotonic regression in a wide range of
  settings**", handles non-sigmoidal curves, and **contains the identity map**, so it cannot make a
  well-calibrated model worse. Related: **temperature scaling** is single-parameter and
  **rank-preserving** — the safe way to turn scores into probabilities without disturbing a validated
  ordering.
- **Evidence.** Kull, Silva Filho & Flach 2017, **AISTATS (PMLR 54)**,
  https://proceedings.mlr.press/v54/kull17a.html; https://betacal.github.io/. Temperature scaling from
  the standard calibration survey (arXiv 2112.10327). Both **[snippet-only]**.
- **What STRIDE does today (fetched).** `calibration_model.py:30-32` — global **isotonic**,
  `y_min=0.01, y_max=0.95, out_of_bounds="clip"`. The only one of five-to-six documented layers that
  can fire (SYSTEM_MAP §7 D-6), conditional on a git-ignored pickle whose production existence is
  **unknown** (§9 Q1). Position: `RTP:657-661` — on the MC probability, **before** the ML blend
  (`:667`) and **before** the market anchor (`:692`).
- **The gap.** Two, the second worse. (i) *Wrong family* — isotonic on ~9k races at a ~9.7% positive
  rate is the small-sample regime where it overfits into a coarse step function. (ii) **Wrong
  position** — F5's theorem says the linear pool at `:692` *creates* miscalibration; calibrating at
  `:657` cannot fix something 35 lines later. **The one live calibration layer sits upstream of the
  one step guaranteed to decalibrate.** Everything downstream — `modelEdge`, the conviction ladder,
  the bet gate, `ev = calib/true_mkt − 1` (`:955`) — reads the un-recalibrated output. Constraint 12
  requires the downstream calibrator be refit **in the same change**.

### R1-F18 — What "enough data" means here, and STRIDE has had enough for a long time · **[FRAMING]** · *N/A*

- **Finding.** The sample sizes that produced *published, holdout-validated, profitable* racing models
  are small:

| Study | Races | Notes |
|---|---|---|
| Bolton & Chapman 1986 | **200** | 5 US tracks; rank-ordered explosion |
| Edelman 2007 (**Australian**) | **200** in / 100 out | 12 inputs, SVM |
| Benter 1994 | **~2,000**; stated minimum **500-1,000** | *"does not seem to improve dramatically … greater than 1000 races"* |
| Chapman 1994 | **2,000** (HK) | 20-variable MNL, >20% returns after the p̂<0.04 cut |
| Lessmann et al. 2010 | **1,000** (HK), 12,902 horses | random forest |
| Koker 2019 (practitioner) | **938** (HK) | softmax-over-race neural |
| German Benter replication (**fetched**) | 5,625 runner-obs / **524 wins** | +6.0% ROI, bootstrap p=0.0218 |
| Seoul LTR 2024 / KRA LTR 2025 | not established / **9,140** | pairwise & listwise rankers |

- **Evidence.** As cited in F1, F4, F9, F11, F13, F14. Benter's data-volume sentence is the only
  *prescriptive* statement in the set and is **[snippet-only]**, returned near-verbatim.
- **What STRIDE does today.** `training_view_v2` rebuilt to **119,577 rows / 8,995 races**;
  walk-forward with a **14-day purge gap**, 30 folds; mean AUC **0.7871 (±0.0441)**, mean Brier
  **0.0841**.
- **The gap.** STRIDE has **4.5× Benter's development sample and ~9× his stated minimum**, and its
  evaluation discipline is ahead of most published work. Therefore *"we need more data"* is **not** an
  admissible explanation for the top pick hitting at the favourite baseline. The binding constraints
  are F1/F2/F3/F5/F7/F12 — target, normalisation, calibration, de-vig and contamination — every one a
  specification issue costing no new data. The one genuine data gap is a **different kind**: the
  T−5-minute odds snapshot and sectional coverage above 47%.

---

# R2 — Probability Calibration

18 findings. **Exactly one external source fetched** (`github.com/conorwalsh99` README); 30 searches;
everything else [snippet-only]. Every STRIDE claim re-verified by direct source read this session,
not taken from SYSTEM_MAP on trust.

### R2-F1 — STRIDE pools probabilities linearly in four places; a weighted linear pool of calibrated forecasts is provably uncalibrated and under-confident · **[BOTH]** · *needs work — the code exists but is on a provenance hold*

- **Finding.** *Any weighted linear combination of distinct, individually calibrated probability
  forecasts is necessarily uncalibrated and lacks sharpness* — the linear opinion pool "is, on
  average, not extreme enough". Fixes: (a) Ranjan & Gneiting's beta-transform extremization, (b)
  Satopää et al.'s extremization of the **average log-odds**, (c) **logarithmic pooling**
  `P ∝ Π p_i^{w_i}` — externally Bayesian, KL-minimal, and it "takes confident forecasts more
  seriously". Worked example from the sources: experts at (0.1%, 99.9%) and (50%, 50%) give a linear
  pool of ~(25%, 75%) but a log pool of ~(3%, 97%). In a system whose entire output is
  `edge = p_model − p_market`, midpointing is a direct mechanical shrinkage of every edge.
- **Evidence.** Ranjan & Gneiting 2010, **JRSS-B** 72(1):71-91 (preprint
  https://stat.uw.edu/sites/default/files/files/reports/2008/tr543.pdf); Satopää et al. (arXiv
  1506.06405); *Bayesian Ensembles of Binary-Event Forecasts* (arXiv 1705.02391 — the caveat that
  extremization is not universally correct); *No-Regret Learning with Unbounded Losses: The Case of
  Logarithmic Pooling* (arXiv 2202.11219 — external Bayesianity, KL-minimality, the worked example).
  Corroborating one-liner surfaced independently: "Averaging two already-calibrated models produces an
  output that is no longer calibrated." All **[snippet-only]**.
- **What STRIDE does today.** Four linear pools, all source-verified: `ml_model.py:594`
  (`ensemble = xgb·w + lgb·w + cat·w`, weights from the hardcoded seed accuracies at `:59-63`);
  `RTP:667-668` (`raw = (1−ml_w)·mc + ml_w·ml`, `ml_w = 0.20 if odds ≤ 3 else 0.40`); `RTP:692`
  (`calibrated = mw·raw + (1−mw)·true_market`); and `mc_api.py:7393` (`base = base·0.70 +
  sectional·0.30`).
- **The gap.** The one operator the literature endorses is already in the repo and switched off:
  `conditional_logit.py`'s `P_i ∝ exp(α·ln m_i + β·ln q_i)` *is* a two-expert **logarithmic** opinion
  pool with learned weights, and because α, β are unconstrained (α ≈ 1.296) it can **extremize**
  rather than only interpolate. Benter's second stage is the log pool; STRIDE's mw ladder is the
  linear pool the log pool exists to replace. **The blocker is constraint 14 (a provenance hold), not
  a design objection.** The buildable version is a **second, final-stage** log-pool hook replacing the
  mw ladder rather than the isotonic step, fitted on `prediction_audit.final_win_prob`.

### R2-F2 — The only calibration layer that can fire in production has no fitting code anywhere in the repository · **[BOTH]** · *implementable now*

- **Finding (STRIDE-specific, verified this session).** `ProbabilityCalibrator.fit()`
  (`calibration_model.py:25-38`) has **zero callers**. Exhaustive grep for `ProbabilityCalibrator`
  and `calibration_model` returns four sites: the class definition; `RTP:575-576` which only
  *constructs and loads*; a comment at `ml_model.py:565`; and `mc_recalibration.py:19`, which refers
  to a different artifact (`calibration_model.json`). **Nothing in the repo produces
  `models/isotonic_calibrator.pkl`.** Whatever pickle sits in production was fitted out of tree; its
  population, folds and OOF discipline are unknowable.
- **Evidence (why it matters, not that it is true).** scikit-learn's `CalibratedClassifierCV` docs:
  with `cv="prefit"`, "the user has to take care manually that data for model fitting and calibration
  are disjoint". Niculescu-Mizil & Caruana, ICML 2005 (>2,000 cites). Both **[snippet-only]**.
- **What STRIDE does today.** `RTP:~574-576` loads if present; `RTP:657-661` applies. Layer L3 — the
  only one of six that can fire. `docs/05:92` states the rule "calibration never sees data the model
  was tuned on" (guardrail 13).
- **The gap.** A stated invariant with **no enforcement and no artifact provenance**, over the one
  artifact that matters. Prerequisite to every other finding here. The cheap fix is not a new
  calibrator: (a) a `fit_calibrator.py` sibling regenerating the artifact from
  `training_view_v2` with the same 14-day purge as `retrain_v2.DateWindowSplitter`, and (b) a sidecar
  metadata dict (`{fit_rows, date_range, stage, sklearn_version, fitted_at}`) **printed as a positive
  assertion at load** — matching the repo's own "print a count, do not rely on the absence of an
  exception" convention.

### R2-F3 — Calibration is applied to one leg of the blend, before an explicitly-uncalibrated second leg is mixed in; the order of operations is inverted · **[BOTH]** · *needs work — collides with guardrail 6*

- **Finding.** The calibration literature is unanimous: calibrate **the thing you will act on**.
  STRIDE calibrates an intermediate. Verified order in `calibrate_and_score` (`RTP:568`):
  `:657-661` isotonic on the MC `winPercentage` → `:665-668` uncalibrated ML mixed in →
  `:673-674` de-vig → `:692` linear pool with the market → `:697` `modelEdge` taken from the
  un-recalibrated pool.
- **Evidence.** Same body of work as F1 (a mixture of a calibrated and an uncalibrated forecast is
  uncalibrated; a linear pool of two calibrated forecasts is *also* uncalibrated). Plus the repo's own
  `docs/05:100-103`. **The doc is right about the hazard and wrong about which end to fix**: the
  correct resolution is to move the calibrator downstream of the blend, not to leave the upstream leg
  raw.
- **What STRIDE does today.** As above; `mlPredictedProb` is on the same 0–100 scale (`RTP:2323`), so
  this is a genuine probability mixture, not a scale bug — but the ML leg carries a comment saying its
  own calibrators are deliberately not applied (`ml_model.py:565`). The quantity named `calibrated` at
  `:692` is (calibrated MC × 0.60–0.80) + (uncalibrated ML × 0.20–0.40), then pooled with the market.
  **Nothing calibrates that.**
- **The gap.** The single highest-value re-ordering in the system, and it is a *move*, not an
  *addition*: fit and apply one calibrator to `rawModelProb` **after** the MC↔ML blend and **before**
  the market anchor. That makes the anchor a pool of two calibrated inputs (which F1 then says should
  be logarithmic). Fixes the level (ROI) without touching within-race order (hit rate), **provided the
  calibrator is rank-preserving** (F12). Requires post-blend outputs to exist —
  `store_final_probs_in_audit` (`RTP:1582`) writes the pipe, but not yet the rows (SYSTEM_MAP §9 Q13).

### R2-F4 — The ML leg is trained with three different class-imbalance corrections, and the mechanism that would undo their distortion is deliberately switched off · **[BOTH]** · *diagnostic implementable now; fix must ship with F3*

- **Finding.** Re-weighting/resampling **shifts the posterior away from the true probability by a
  known, analytically correctable amount**. Dal Pozzolo et al.: undersampling/reweighting "modifies
  the priors of the training set and consequently biases the posterior probabilities of a
  classifier"; "stronger undersampling produces probabilities with poorer calibration". Practitioner
  guidance surfaced in the same search names STRIDE's exact hyper-parameter: "Disabling XGBoost's
  `scale_pos_weight` argument may give better calibrated probabilities."
- **Evidence.** Dal Pozzolo, Caelen, Johnson & Bontempi, **IEEE SSCI 2015**; *The Hidden Cost of
  Resampling: How Imbalance Correction Degrades Probability Calibration in Tree Ensembles* (arXiv
  2606.29720); MachineLearningMastery (practitioner, **not peer-reviewed**). All **[snippet-only]**.
  **⚠️ [audit 2026-07-25] arXiv 2606.29720 resolves and PARTLY CONTRADICTS the use made of it here.**
  The paper is real (single author, Zewen Liu, Qilu Institute of Technology; **a preprint, not
  peer-reviewed**; five public datasets, imbalance ratios 1.9–70, random forest + gradient
  boosting). Its actual results are more specific and less alarming than the finding implies:
  (1) **SMOTE's calibration cost is small (ECE +0.009)**; (2) random undersampling causes sharp
  damage that grows with imbalance; and (3) — the part that matters — **post-hoc Platt or isotonic
  recalibration eliminates the damage, cutting ECE by up to 66%.** It does **not** study
  `scale_pos_weight` / `is_unbalance` / `auto_class_weights`, which is what STRIDE actually uses.
  Net effect: this source is **evidence for F3's remedy (turn the OOF isotonic back on) rather than
  evidence that the distortion is uncorrectable**, and it should not be cited alongside R1-F3's
  "not always able to be corrected with re-calibration" claim, which is itself now [unverified].
- **What STRIDE does today (source-verified).** `retrain_v2.py:774` `scale_pos_weight: 9`, `:791`
  `is_unbalance: True`, `:802` `auto_class_weights: "Balanced"`. The repo **fits the correct
  antidote** — per-model OOF `IsotonicRegression` at `:835/855/870`, assigned from out-of-fold
  predictions at `:1169-1190` with the in-code note "no leak — X_cal used only for early stopping" —
  and then does not apply it (`ml_model.py:565`). The fallback at `ml_model.py:594` averages three
  raw, class-reweighted tree outputs.
- **The gap.** `mlPredictedProb` is **not a probability**. It is a class-reweighted score with a
  systematic upward bias against a 9.7% base rate, entering the blend at 20–40% weight and at the
  *higher* 40% weight for everything above $3 — precisely the validated $2–$15 value band, where an
  inflated probability manufactures false-positive edge. **The most concrete mechanism available for
  "top pick wins 33.7% but returns −4.2%".** One diagnostic line — mean `mlPredictedProb` per card vs
  observed win rate — confirms or kills it immediately with zero behaviour change. Guardrail 12
  forbids switching the isotonic on without refitting downstream **in the same change** — which is
  what F3 proposes anyway. **Do them as one ticket or not at all.**

### R2-F5 — Within-race win probabilities do not sum to 1 at the point where edge, EV and every gate are computed · **[ROI]**, **[BOTH]** if the flat-MC breaker is firing · *(a) measurement implementable now; (b) design needs work*

- **Finding (STRIDE-specific, verified this session).** Racing is a multi-entry competition: the
  field's win probabilities are a point on the simplex. Every serious racing model enforces this
  structurally — Harville (1973) via independent exponential running times, Henery (1981) normal,
  Stern (1990) gamma, Bolton & Chapman (1986) via the conditional logit. Lo & Bacon-Shone (1994) found
  Harville's approximation carries a **systematic bias** on Hong Kong data with Henery "clearly
  superior"; Lo (1994b) found Stern with r = 4 better than both on Japanese data. STRIDE's
  normalisation is destroyed three lines after it is created: grep of every `winPercentage` assignment
  returns **exactly three sites — `:624` (CL path), `:661` (isotonic), `:693` (market anchor) — and
  no renormalisation follows any of them.** Each is *pointwise*: isotonic is a per-runner monotone map
  (a non-affine transform of a simplex point leaves the simplex); the ML blend mixes in an
  unnormalised pointwise classifier; and the mw ladder applies **a different weight to different
  runners in the same race** ($2.80 favourite → 0.80, $12 outsider → 0.45), so the pool is not even a
  convex combination of two normalised distributions. `mc_api` renormalises at `~:7612-7623` — but
  *upstream* of all of this.
- **Evidence.** Bolton & Chapman 1986 **[snippet-only]**; Harville/Henery/Stern/Lo & Bacon-Shone
  comparison via Lo's surveys **[snippet-only]**; practitioner corroboration that this is a live
  modelling choice, r-bloggers **[snippet-only, non-peer-reviewed]**.
- **What STRIDE does today.** As above. Consequences are not hypothetical: `RTP:703-705` computes
  `mc_spread` from `rawModelProb` — a quantity that no longer sums to anything in particular — and
  `mc_spread < 6.0` forces **every pick on the race to `low` confidence and therefore `0u`**.
- **The gap.** Two sub-gaps, not to be conflated. **(a) Measurement** — nobody has ever printed
  `Σ winPercentage` per race. If it lands at 104% or 92%, every `modelEdge` on that card is biased by
  the same amount in the same direction: a pure ROI error with **zero hit-rate signature**, hence
  invisible to every harness STRIDE has. **(b) Design** — the correct order is normalise-last, and the
  normalising operator should be a within-race one (log pool / conditional logit) rather than a
  divide-by-sum on an arbitrary pointwise transform.

### R2-F6 — STRIDE's calibration metric of record is a biased, inconsistent estimator that understates miscalibration · **[BOTH]** · *implementable now, additive*

- **Finding.** Binned ECE via the plug-in estimator over fixed equal-width bins is
  **asymptotically inconsistent with a negative bias** — it systematically reports *less*
  miscalibration than exists — and is "highly sensitive to the chosen binning scheme". Kumar, Liang &
  Ma add the sample-complexity result: the plug-in estimator needs samples proportional to **B**, the
  debiased estimator only **√B**, and scaling methods such as Platt/temperature "have measurable
  calibration problems" that binned ECE hides. The modern replacement is **CORP** — PAV-built
  reliability curves and a miscalibration score that is "optimally binned, reproducible and provably
  statistically consistent", with a score decomposition generalising to any proper scoring function
  and **no binning hyper-parameter**.
- **Evidence.** Vaicenavicius et al., **AISTATS 2019**; Kumar, Liang & Ma, **NeurIPS 2019**
  (arXiv 1909.10155, >500 cites); Roelofs et al., **AISTATS 2022**; Dimitriadis, Gneiting & Jordan,
  **PNAS** 118(8), 2021 (CORP; R package `reliabilitydiag`). All **[snippet-only]**.
- **What STRIDE does today.** `walk_forward_backtest.py:100-117` — `compute_ece(…, n_bins=10)` with
  `np.linspace(0,1,11)`: **ten equal-width bins**, plug-in estimator, called at `:153`. Worst case for
  the bias: with a ~9.7% base rate and an MC ceiling of 60% (`mc_api.py:7398`), bins
  `[0.6,0.7] … [0.9,1.0]` are empty and `[0.0,0.1]`/`[0.1,0.2]` hold nearly all the mass —
  **effective B ≈ 3, not 10.** The promotion bar is stated in terms of "the calibration Brier".
- **The gap.** The number STRIDE would use to accept or reject a calibration change is (i) biased low,
  (ii) dependent on an unswept binning choice, (iii) computed with almost all resolution in two bins.
  It is the measurement layer under every other finding. Fix is additive: alongside the existing `ece`
  key add equal-**mass** binning (10 quantile bins), the debiased estimator, and a PAV/CORP score —
  and `mc_recalibration.py:157` already contains a hand-rolled PAV, so guardrail 3 says reuse it.

### R2-F7 — Brier is the wrong headline rule when the downstream act is staking; the log score *is* the Kelly growth rate — and neither is reported decomposed · **[ROI]** · *implementable now, ~15 lines*

- **Finding.** (a) *Which rule.* Both are strictly proper, but the logarithmic score has a
  decision-theoretic identity to the downstream problem: the expected log score **is** the expected
  log-wealth growth rate of a Kelly bettor. "For the logarithmic (KL) generator, the maximum Kelly
  log-growth rate, the Kullback–Leibler divergence, and the dual Bregman divergence coincide … this
  Kelly alignment is special to the logarithmic generator: the squared-Euclidean (L2) cost function
  has a dual Bregman divergence that is **not** the Kelly growth rate." Brier "was proposed without
  strong backing for why it should be chosen over other proper scoring rules"; it is bounded and
  symmetric, exactly the wrong shape for a bettor. (b) *Decomposition.* Murphy's
  **Brier = reliability − resolution + uncertainty** separates what a calibrator can fix from what
  only a better model can improve from what is a property of the data.
- **⚠️ [audit 2026-07-25] The Kelly ↔ log-score citation is MISATTRIBUTED.** **arXiv 2607.06166
  resolves** — but to *"When do prophets profit in prediction markets?"* (Gu, Wu et al., University
  of Chicago), a prediction-markets paper about proper scoring rules and betting strategies. It is
  on-topic in the broad sense (it relates predictive accuracy to profitability under proper scoring
  rules) but **no search this session located the quoted passage** about the logarithmic generator,
  the Kullback–Leibler divergence and the dual Bregman divergence coinciding with the Kelly
  log-growth rate, nor the L2 counter-example. **That quotation is downgraded to [unverified] and
  the arXiv id must not be cited for it.** The *underlying* identity (expected log score = expected
  log-wealth growth rate of a Kelly bettor) is elementary and survives on its own; part (b),
  Murphy's reliability–resolution–uncertainty decomposition, is unaffected.
- **Evidence.** Kelly ↔ log-score identity: arXiv 2607.06166 **[source misattributed — see audit
  note above; treat the quotation as unsupported]**; Oddacious proper-
  scoring-rule survey **[snippet-only, non-peer-reviewed]**; Murphy 1973, *J. Appl. Meteorology* 12
  **[recall — unverified]**, its existence corroborated by an independent search **[snippet-only]**;
  CORP decomposition as F6 **[snippet-only]**.
- **What STRIDE does today.** `walk_forward_backtest.py` computes `brier`, `log_loss` and `ece` as
  three flat scalars (`:209`, `:461`) and **never decomposes any of them**. The promotion bar names
  **Brier** specifically. README reports Brier 0.0834; the 2026-07-13 retrain 0.0841.
- **The gap.** At a ~9.7% base rate the uncertainty term dominates: 0.0834 vs 0.0841 is 0.0007 on a
  number whose irreducible floor is ~0.088 — **the reported Brier differences are almost entirely
  uncertainty, not skill**, which is why the promotion bar as written is close to un-actionable.
  Replacing the bar's headline with a decomposition (reliability for the calibration clause,
  resolution for the hit-rate clause) makes the *existing* bar testable without changing its intent.

### R2-F8 — Weak calibration — slope and intercept — is never measured, and the mw ladder is a hand-tuned version of exactly what the slope estimates · **[BOTH]** · *metric implementable now; ladder replacement needs work*

- **Finding.** Van Calster et al.'s **calibration hierarchy**: *mean* calibration (predicted average =
  observed average), *weak* (**intercept ≈ 0, slope ≈ 1**), *moderate* (flexible curve on the
  diagonal), *strong* (correct conditional on every covariate — "utopic"). The two weak statistics
  come from regressing the outcome on the logit of the predicted risk. **Slope < 1 means the
  predictions are too extreme**; the intercept says whether risks are over- or under-estimated on
  average. Framing: calibration is "the Achilles heel of predictive analytics".
- **Evidence.** Van Calster, McLernon, van Smeden, Wynants & Steyerberg (STRATOS), **BMC Medicine**
  17:230, 2019 (>2,000 cites); Van Calster et al., **J. Clin. Epidemiol.** 2016; Stephen/Riley et al.,
  **JCE** 2019; R package `CalibrationCurves`. All **[snippet-only]**.
- **What STRIDE does today.** Nothing — grep confirms no slope, no intercept, no
  calibration-in-the-large. What exists instead is the mw ladder, whose in-code rationale at
  `RTP:676-678` is a **Kelly audit**: *"$1-3 horses win 41%, model predicts 17% after blend."* That is
  a **verbal statement of a calibration-slope failure**, and the six numbers are a hand-fitted,
  piecewise-constant correction for it. SYSTEM_MAP §9 Q14 records no fitting script exists.
- **The gap.** STRIDE diagnosed weak miscalibration by eye and patched it with six magic numbers,
  when a two-parameter logistic regression on the logit of the prediction would estimate the same
  correction with a standard error, on a holdout, reproducibly. **The diagnostic is worth shipping
  even if the ladder is never touched**, because it tells you whether the ladder currently over- or
  under-corrects — presently unknowable. Zero-new-dependency route:
  `sklearn.linear_model.LogisticRegression` on a single logit feature (`statsmodels` may not be among
  the 32 deps).

### R2-F9 — A globally calibrated model can be badly miscalibrated inside every odds band — grouping loss — and STRIDE fits exactly one global calibrator · **[BOTH]** · *measurement implementable now; per-band fix needs work*

- **Finding.** Calibration is an *average* property. "Even a perfectly calibrated classifier with the
  best possible accuracy can have confidence scores that are far from the true posterior probabilities
  due to the **grouping loss**, which is created by samples with the same confidence score but
  different true posterior probabilities." Modern models exhibit substantial grouping loss, "notably
  in distribution-shift settings". Van Calster's moderate-vs-strong distinction is the same idea from
  the clinical side.
- **Evidence.** Perez-Lebel, Le Morvan & Varoquaux, **ICLR 2023** (arXiv 2210.16315; code
  https://github.com/aperezlebel/beyond_calibration). **[snippet-only]**.
- **What STRIDE does today.** One `IsotonicRegression` over all runners, prices, field sizes, tracks
  and going (`calibration_model.py:25-38`), then a **manual per-odds-band** correction downstream (the
  mw ladder). **The system already believes calibration differs by odds band — it just applies the
  belief as six constants rather than measuring it.**
- **The gap.** The natural conditioning variables are all available at scoring time and none is used
  to stratify calibration measurement: **odds band** (strongest candidate — AU net returns fall
  monotonically from **−12.35% on top favourites to −41.55% on 9th favourites**,
  `research/report.md §2.2` [prior pass]), **field size** (STRIDE already switches MC iterations on
  it), **going**, **metro vs provincial**. A per-band reliability table is one `groupby` over data the
  backtest already produces — and it is the diagnostic that would say whether the mw ladder is right,
  wrong, or right for the wrong reason.

### R2-F10 — Isotonic overfits below ~2,000 calibration points, and its step function can silently trip STRIDE's own flat-MC circuit breaker, zeroing the day's stakes · **[BOTH]** · *implementable now*

- **Finding.** (a) *Small samples.* "When the calibration set is small (less than about 2,000 cases),
  Platt scaling outperforms isotonic regression because isotonic regression is less constrained …
  easier for it to overfit." (b) *Ties.* "Isotonic regression's staircase outputs induce large flat
  segments … all points within a step receive the same calibrated probability, which creates ties and
  removes local distinctions." It preserves AUC exactly but destroys *granularity*. Nearly-isotonic
  regression is the literature's answer where ranking resolution matters. **(c) The STRIDE-specific
  consequence, not seen stated anywhere:** the isotonic step at `RTP:657-661` runs *before*
  `mc_spread = max(rawModelProb) − min(rawModelProb)` at `:703-705`. A step-function calibrator
  mapping several runners onto the same plateau **mechanically shrinks that spread** — and
  `mc_is_flat` (i) forces all three picks to `low` ⇒ **`0u` stakes**, (ii) shifts the MC-spine blend
  50/50 → 65/35, (iii) applies the ×0.30/×0.60/×0.85 gradient penalty, and (iv) hands the LLM a
  `max_score + [5.0, 3.0, 1.0]` boost whose top pick then **bypasses every safety filter**
  (`RTP:883`). **A coarse calibration artifact can, on its own, turn a normal race into a no-bet race
  governed by the LLM** — and SYSTEM_MAP §9 Q10 records that nobody knows how often `mc_is_flat` fires.
- **Evidence.** Niculescu-Mizil & Caruana, **ICML 2005** (the "~2,000 cases" threshold) and UAI 2005;
  *Resolution Lost: The Deadweight Costs of Strict Isotonicity* (gojiberries.io, **blog**);
  *Isotonic Recalibration under a Low Signal-to-Noise Ratio* (arXiv 2301.02692 — racing is low-SNR).
  All **[snippet-only]**. Point (c) is STRIDE source, verified this session.
- **What STRIDE does today.** Isotonic everywhere it calibrates at all: the global
  `ProbabilityCalibrator`, the per-model OOF calibrators, the double calibrator, and a hand-rolled PAV
  in `mc_recalibration.py:157`. **No Platt, no beta, no temperature scaling anywhere in the repo.**
- **The gap.** Isotonic is the *only* family STRIDE uses and has the worst small-sample behaviour — in
  a system whose live calibration-relevant record is **260 audit rows plus 794 `live_model` rows**.
  `mc_is_flat` firing rate is a one-line counter.

### R2-F11 — Beta calibration contains the identity map; Platt does not — which matters precisely because STRIDE's inputs may already be calibrated · **[ROI]**, with a hit-rate *protection* argument · *implementable now, ~30 lines, no new dependency*

- **Finding.** "Platt scaling can actually lead to even worse calibrated scores, as the identity
  function is not present in the family of functions that can be obtained from it." Beta calibration
  is a three-parameter family on `(log p, log(1−p))` that "allows a richer family of calibration maps
  including inverse sigmoids **and the identity map**, which is particularly useful to prevent
  over-calibration and apply unnecessary adjustments to already calibrated probabilities". Kull et al.
  also show many classifiers suffer the *opposite* distortion from the one Platt assumes. Being
  parametric, "having a small data set is not as problematic as for isotonic regression".
- **Evidence.** Kull, Silva Filho & Flach, **AISTATS 2017** (PMLR v54) + extended **Electronic Journal
  of Statistics** 11(2):5052-5080; comparative summary "isotonic outperforms Platt in ECE and Brier …
  however log loss results favor Platt over isotonic"; Filho/Kull/Flach calibration survey (arXiv
  2112.10327). All **[snippet-only]**.
- **What STRIDE does today.** No parametric calibrator exists. The MC leg entering
  `calibrate_and_score` has already been through a Plackett-Luce normalisation and a field
  renormalisation to 100%, so it is *plausibly close to calibrated already* — the case where beta's
  identity map is exactly the right hypothesis class and a mis-specified sigmoid would actively damage
  a good input.
- **The gap.** STRIDE's calibrator family cannot express "leave this alone", and its loss-of-record
  (Brier) favours isotonic while the decision-aligned loss (log loss, F7) favours parametric methods.
  A smooth parametric map introduces no ties, **so it cannot trip F10(c)**.

### R2-F12 — Temperature scaling provably cannot change the ranking, which makes it the ideal A/B primitive for STRIDE's promotion bar · **[ROI]**, hit-rate-neutral by design · *implementable now — one scalar*

- **Finding.** Temperature scaling is a one-parameter special case of Platt, fitted on a held-out set
  by minimising NLL. Guo et al.: it is "surprisingly effective at calibration on most datasets", and
  **"the classification accuracy of the model is not affected by temperature scaling … it does not
  change the most-confident prediction"** — a single positive temperature is a strictly monotone
  transform of the logits.
- **Evidence.** Guo, Pleiss, Sun & Weinberger, **ICML 2017** (>5,000 cites); Minderer et al., NeurIPS
  2021 (the caveat that Guo's findings are architecture-dependent). Both **[snippet-only]**.
  **Caveat flagged by the agent:** temperature scaling is defined on **logits** and STRIDE's engines
  emit probabilities; the transfer is `p' ∝ p^{1/T}` renormalised within race — a one-parameter
  logarithmic pool against the uniform, i.e. F1's family with β = 0. **[recall — unverified]** for
  that specific algebraic transfer.
- **What STRIDE does today.** No temperature scaling. The promotion bar demands a change "raise
  top-pick hit rate … **without** degrading the calibration Brier or the Value-Edge band's ROI" — a
  two-sided test that most calibration changes fail because they move both terms.
- **The gap.** A rank-preserving recalibrator **satisfies the hit-rate clause of the promotion bar by
  construction**, reducing the A/B to a one-sided question about Brier/reliability and ROI. That is a
  materially cleaner experiment than anything currently proposed, and it directly targets the
  "$1-3 horses win 41%, model predicts 17%" complaint — a sharpness problem, i.e. a temperature problem.

### R2-F13 — Proportional de-vig is biased in exactly the direction of the Australian favourite–longshot bias; Shin is the empirically dominant alternative · **[ROI]** · *implementable now — R2's cleanest single ticket*

- **Finding.** Štrumbelj compared Basic Normalisation, two regression approaches and **Shin
  probabilities** across bookmakers, sports and the largest exchange: "Shin probabilities are better
  than basic normalisation and regression-model-based approaches for **all** bookmaker/sport pairs",
  and basic normalisation "produces biased probabilities" with differences "large enough to lead to
  contradictory conclusions". Shin's insider-trading derivation is what "imparts a favourite-longshot
  bias on the odds", so it removes proportionally *more* margin from longshots. ~~Štrumbelj also notes
  Shin's advantage **shrinks as market size grows** — AU fields are 8–16, the regime where it is
  largest.~~ **[audit 2026-07-25 — this sentence is [unverified] and is struck.** Štrumbelj 2014
  resolves cleanly (IJF 30(4):934-943, 83 citations, "Shin's model … more accurate forecasts than
  … basic normalization or regression models"), but the *market-size* claim was not returned by any
  search. The closest recoverable statement is a **different** one — that "betting exchange odds are
  not always the best source, **especially in smaller markets**" — which is about the *source* of
  odds, not about Shin's margin over normalisation. **Do not use "AU fields are 8–16, the regime
  where Shin's advantage is largest" as a reason to prefer Shin.** F9/F13's independent argument
  (power ≥ Shin, and simpler) is unaffected.**
- **Evidence.** Štrumbelj 2014, **IJF** 30(4) (**83 citations** per SciSpace); Štrumbelj 2016,
  **Journal of Sports Economics**; *The Favourite-Longshot Bias, Bookmaker Margins and Insider Trading
  in a Variety of Betting Markets*; *A Family of Solutions Related to Shin's Model For Probability
  Forecasts*, 2024 (closed-form variants). All **[snippet-only]**. AU magnitude **[prior pass —
  extended]**: net returns from **−12.35% (top favourite) to −41.55% (9th favourite)**, coefficient on
  odds −0.621 (z = −11.29), all 14,854 races of the 2006 AU season, `research/report.md §2.2`.
- **What STRIDE does today.** `calculate_overround` at `RTP:432-442`, read in full this session:
  `total_implied = Σ 100/odds`; `if valid < 2 … return 1.0`; else `total_implied/100`. Then
  `true_market = (100/odds)/overround` at `:673-674`. **A race with one quoted runner has no vig
  removed at all.**
- **The gap.** Proportional de-vig assigns the same *multiplicative* margin to a $2 favourite and a
  $40 outsider; under FLB the true margin concentrates on the outsider. So `true_market` **overstates
  the longshot's fair probability and understates the favourite's** — biasing `modelEdge` *against*
  favourites and *toward* longshots, into the −41.55% zone. Both the `odds > 15 → NO_BET` ceiling and
  the mw ladder's *rising* model weight at short prices read as **compensations for a market baseline
  that is wrong in a known direction.** Shin's `z` solves in a few Newton iterations; guardrail 3 says
  extend `calculate_overround` with a `method=` parameter defaulting to current behaviour, behind
  `STRIDE_SHIN_DEVIG`, byte-identical off.

### R2-F14 — Probability error is amplified into EV error by the odds; the tails STRIDE measures least are where miscalibration is most expensive · **[ROI]** · *needs work — do F6/F9 first*

- **Finding.** Arithmetic, stated as arithmetic. With decimal odds `O`, `EV = p·O − 1`, so
  `∂EV/∂p = O`. **1 percentage point** of absolute calibration error moves EV by **0.025 at $2.50**
  (noise against a 3% gate), **0.08 at $8** (larger than the entire `edge ≥ 3` threshold), **0.15 at
  $15** (five times the gate). Break-even probability is `1/O`, so the relative error that flips a
  decision shrinks like `1/O` too. Betting systems are most fragile exactly where the reliability
  diagram has fewest samples. Attached results: (a) Kelly amplifies it — Baker & McHale: "such
  replacement of population parameters by sample estimates gives poorer out-of-sample than in-sample
  performance. To improve out-of-sample performance the size of the bet should be **shrunk**"; (b) the
  empirical version is Walsh & Joshi's eighth-Kelly experiment (F15).
- **Evidence.** Baker & McHale 2013, **Decision Analysis** 10(3); Chu, Wu & Swartz, *Modified Kelly
  Criteria*. Both **[snippet-only]**. The `∂EV/∂p = O` derivation needs no source.
- **What STRIDE does today.** Band gates stated as **fixed percentage-point edges** — `edge ≥ 4` under
  $3, `≥ 2.5` at $3–5, `≥ 3` at $5–15 — i.e. a threshold *flat or falling* in price while required
  precision is *rising*. The `odds > 15` ceiling is the only thing between the system and the region
  where 1pp dominates the decision, and nobody knows whether it costs or saves money (§9 Q8). Staking
  is `2u/1u/0u` with no price sensitivity, so the ROI consequence is not even damped by stake size.
- **The gap.** **The edge thresholds are denominated in the wrong units.** A price-invariant *EV*
  threshold (or an EV threshold scaled by estimated calibration error per band, per F9) is the
  decision-theoretically correct form — and is what the odds-band comment at `RTP:~743-745` promised
  before deferring to a Kelly stage that never materialised.

### R2-F15 — Selecting a betting model on calibration rather than accuracy flipped the ROI sign in a published experiment — and the prior pass's numbers predate a corrigendum · **[ROI]** · *implementable now*

- **Finding [prior pass — extended].** `research/report.md §2.5` carries this cluster flagged
  **[unverified — session-capped]**. Partially closed: the paper is real and the headline numbers
  match across four independent listings — "Using calibration, rather than accuracy, as the basis for
  model selection leads to greater returns, on average (**ROI of +34.69% versus −35.17%**) and in the
  best case (**+36.93% versus +5.56%**)". The eighth-Kelly bankroll-path experiment exists.
  **What is new: a corrigendum was published.** "A corrigendum has been published" after "errors in
  feature engineering steps were identified, which affected **all results**, though the paper's
  conclusion remains the same" — confirmed by the ScienceDirect corrigendum listing (Feb 2025) **and
  by the authors' own repository README, the one external source this agent successfully fetched.**
  The *direction* survives; **the magnitudes should not be quoted.**
- **Evidence.** Walsh & Joshi 2024, **Machine Learning with Applications** 16:100539
  **[snippet-only — all four mirrors 403]**; **Corrigendum**, MLwA, **Volume 19, March 2025**
  (`sciencedirect.com/science/article/pii/S2666827025000106`) **[snippet-only]** — **[audit
  2026-07-25: the doc said "Feb 2025"; the corrigendum is Volume 19, March 2025. Corrected.
  Everything else in this finding CONFIRMS: the paper, its venue/volume/article number, the
  +34.69%/−35.17% and +36.93%/+5.56% pairs as *published*, and the corrigendum's own wording —
  "errors … in the feature engineering steps of the pipeline … affected all later results …
  the paper's conclusion remains the same". A free preprint exists at arXiv 2303.06021 and is the
  right target for the re-fetch in the queue below]**;
  replication code https://github.com/conorwalsh99/ml-for-sports-betting — **FETCHED**, README
  confirms the corrigendum and that corrections are reflected in the current repository; it does
  *not* state which calibration metric or staking rule was used. *Transfer note:* NBA moneyline,
  two-outcome, US market — the transfer to AU racing is *strengthened* by three differences (more
  outcomes, larger documented FLB, higher takeout) but it is a transfer.
- **What STRIDE does today.** Model promotion is gated on AUC (`docs/05 §7` ShadowTester: shadow AUC >
  primary + 0.02; the Phase-5 verdict decided on a **−0.0012 AUC** ablation). The one place calibration
  *is* in the bar names Brier, which F7 shows is dominated by the uncertainty term.
- **The gap.** Two things are new: (i) the numbers in `research/report.md §2.5` should be **retracted
  and re-quoted from the corrigendum** before anyone builds on them — a Phase-3 action; (ii) the
  actionable form is narrower than "add calibration to promotion": add **reliability** (the decomposed
  Brier component, F7) and **stratified** reliability (F9), because the aggregate Brier the bar already
  names cannot move enough to discriminate.

### R2-F16 — Calibration drifts even when discrimination does not; STRIDE has no recalibration cadence, no drift monitor, and its calibration artifact has no expiry · **[BOTH]** · *monitor implementable now; scheduled recalibration needs work*

- **Finding.** "Calibration drift occurs when a model's predicted probabilities no longer align with
  actual outcome frequencies, **even if overall accuracy remains stable**"; Davis et al. found
  calibration of clinical prediction models "decreased over time" across learning algorithms, with
  "variations in the response of different learning algorithms to changes in the environment impacting
  the timing, extent, and form of calibration drift". The response is scheduled or triggered
  recalibration. Under covariate shift, uncertainty estimates that ignore the shift "overestimate
  certainty and yield a false sense of confidence".
- **Evidence.** Davis et al., **J. Biomedical Informatics** 2020; Park et al., **AISTATS 2020**;
  *Unsupervised Calibration under Covariate Shift* (arXiv 2006.16405). All **[snippet-only]**.
- **What STRIDE does today.** Retraining is scheduled and staged (guardrail 9). **Recalibration is not
  scheduled at all** — there is no fit path in the repo (F2), so the isotonic pickle is refreshed only
  when a human runs an out-of-tree script. No drift monitor, no artifact age check, no `fitted_at`.
- **The gap.** The AU-specific shift channels are real and enumerable: going distribution moves with
  season; field size and class mix differ metro vs provincial; sectional coverage is ~47% and
  regionally uneven, **with QLD now fully absent behind Cloudflare — a hard covariate shift already in
  flight**, because a feature block silently switched off for one state changes the input distribution
  the calibrator was fitted under. Plus the collection gap ("every table stops at ~2026-04-18"). A
  calibration artifact of unknown age and unknown provenance sits in front of every money decision in
  a system that has *already* experienced an undetected regional data outage. Monitor half: a rolling
  reliability check over `prediction_audit.final_win_prob` vs settled results, via the existing
  read-only `audit-coverage` Action pattern.

### R2-F17 — The repo's "never double-calibrate" rule is right about the hazard and wrong about the boundary: the dangerous composition is *pooling*, not *stacking* · **[BOTH]** · *implementable now — a docs edit that gates several code changes*

- **Finding.** The literature does **not** say applying a calibrator to an already-calibrated output
  is inherently harmful. Recalibration of a calibrated model "can yield additional improvements in
  some cases … achieves further improvement and significantly reduces Multi-Field-RCE and global
  calibration error", *provided each stage is fitted on data disjoint from the stage before it*. The
  provably harmful composition is F1's: **"Averaging two already-calibrated models produces an output
  that is no longer calibrated."** The *silently* harmful one is fitting a second calibrator on the
  first's own fitting data — the classic stacking leak, which is why `CalibratedClassifierCV(cv=
  "prefit")` warns that disjointness is the user's responsibility.
- **Evidence.** *Confidence-Aware Multi-Field Model Calibration* (arXiv 2402.17655); scikit-learn
  `CalibratedClassifierCV` docs (`ensemble=True` vs `ensemble=False` vs `cv="prefit"`); F1's sources.
  All **[snippet-only]**.
- **What STRIDE does today.** Guardrail 12 (`docs/05:100-103`) and the stronger in-code form at
  `RTP:626-627` — "CL supersedes the early isotonic correction — **never both**", enforced by
  `_calibrator = None` at `:627`.
- **The gap.** The rule as written **blocks the *safe* composition** (a properly cross-fitted second
  stage, which is what F3 needs) **while leaving the *unsafe* one — three linear pools in a row —
  completely unguarded.** Reframed: *calibrators may be stacked if and only if each is fitted on data
  disjoint from all upstream fits; probability forecasts may not be linearly pooled after calibration
  without a recalibration of the pool.* That reframing unlocks F3 without breaking the repo's own
  discipline. Note `mc_recalibration.py` clips at `(0.005, 0.95)` and **renormalises per race
  (`:204`) — the only layer in the whole stack that does**, and it is inert in this tree.

### R2-F18 — Venn–Abers is the small-sample-safe upgrade path, and STRIDE's data reality is squarely in the small-sample regime · **[BOTH]** · *needs work — use first as an offline diagnostic*

- **Finding.** Venn–Abers predictors fit **two** isotonic regressions on the calibration set — one
  assuming the test instance is positive, one negative — and return the pair as an interval. The
  guarantee is finite-sample and distribution-free under exchangeability alone: "In cases with small
  sample sizes, standard isotonic calibration can overfit … Venn–Abers set prediction is **guaranteed
  in finite samples** to include a perfectly calibrated point prediction." The interval collapses to a
  point as `n` grows; Cross-Venn–Abers averages over folds.
- **Evidence.** Vovk & Petej 2012/UAI 2014 (arXiv 1211.0025); *Generalized Venn and Venn-Abers
  Calibration*, **ICML 2025**; applied evidence arXiv 2205.10586. All **[snippet-only]**.
- **What STRIDE does today.** Point calibration only, everywhere; no uncertainty on the calibrated
  probability is carried anywhere. Fitting populations shrink toward the production stage:
  `prediction_audit` held **260 rows** before the unique-key fix; `training_view_v2` splits
  **106,193 none / 12,590 imported_historical / 794 live_model**. Against the ~2,000-case threshold
  (F10), **794 live-model rows is deep in the regime where isotonic is the wrong choice.**
- **The gap.** Any MC-stage or final-stage recalibration fitted in the next few months will use order
  10³ rows with the one method the literature says overfits at that size. A Venn–Abers interval would
  additionally give the system something it has never had: **an uncertainty band on the calibrated
  probability** — exactly the input Baker & McHale's shrunken Kelly (F14, R4-F5) consumes. Carrying
  the interval touches the output contract (guardrail 4), so sequence it as an offline diagnostic
  first: a wide interval at the top of the field is direct evidence the point calibrator is overfitted.

### R2 cross-cutting verdict on the calibration stack

| Layer (SYSTEM_MAP §2 chain) | Verdict | Reason |
|---|---|---|
| **L0** CL blend (`RTP:591`, opt-in, on hold) | **Justified — right operator, wrong slot** | It is a log opinion pool (F1), but hooked to *replace* the isotonic step when its natural home is replacing the **mw ladder** at `:692`. The hold is provenance, not design. |
| **L1** per-model OOF isotonic (fitted, not applied) | **Justified but disabled — its absence is actively harmful** | The only prior-correction for `scale_pos_weight=9`/`is_unbalance`/`Balanced` (F4). Must move with F3. |
| **L2** double calibration | **Redundant** | Subsumed by one correctly-placed post-blend calibrator; needs an unauditable git-ignored artifact. |
| **L3** global isotonic | **Wrong family, wrong position, unauditable** | Isotonic at ~10³ rows (F10, F18); applied to one leg before an uncalibrated leg (F3); no fit code (F2); clipped `[0.01, 0.95]` when the engine already caps at 0.60. |
| **L4** MC recalibration / FLB / PAV (inert) | **Justified in intent, uniquely correct in one respect** | The **only** layer that renormalises within race after calibrating (`:204`) — the thing F5 says everything else fails to do. Worth reviving in the post-blend slot. |
| **L5** enhanced-trainer `CalibratedClassifierCV` | **Dead** | No production caller. |
| **Not in the docs' table at all:** `ml_model.py:594`, `RTP:667`, `RTP:692`, `mc_api.py:7393` | **The actual calibration-destroying operations** | Each a linear opinion pool (F1); none recognised in `docs/05 §5`; none followed by a recalibration. |

**The right order of operations** (a target, not a ticket): MC (normalised within race) + ML
(prior-corrected) → **log** pool with learned weights, renormalised → **one** out-of-fold,
rank-preserving calibrator on *that* quantity → **log** pool against a Shin-de-vigged market,
renormalised → *then* edge/EV. Today: linear, linear, calibrate-the-wrong-thing, linear, proportional
de-vig, no renormalisation. **Every arrow is the same class of error. STRIDE's problem is not too few
calibration layers — it has six — it is six calibrators and four uncalibrated linear pools, and the
pools are where the probabilities actually get made.**

---

# R3 — Edge & Market Efficiency

17 findings. **Zero external sources fetched** — 15+ hosts, all 403. The agent compensated by putting
budget into arithmetic on the repo's own committed data: **F1/F2/F3/F5/F10 are `[derived — this
session]`**, reproducible from `examples/backtest_summary.json`, needing no external authority.

### R3-F1 — The headline +12.3% ROI is statistically indistinguishable from zero (t = 0.43) · **[ROI]** · *implementable now — ~15 lines in `backtest_v2_metro.py`*

- **Finding.** `examples/backtest_summary.json` records `Value Edge 3%+ ($2-$15)` as **142 bets, 14
  wins, staked 14,200, returned 15,950, ROI +12.32%**. Mean winning decimal price =
  `15950/(14×100) = 11.39`; per-bet SD **3.396**; SE over 142 bets **0.285**; therefore
  **t = 0.1232/0.285 = 0.432**, one-sided p ≈ 0.33; **95% CI on true ROI = [−43.5%, +68.2%]**. Two
  refinements, both adverse: the SD is a **lower bound** (only the *sum* of winning prices is known;
  assuming all 14 paid 11.39 minimises Σ(oᵢ−1)²), so **t ≤ 0.43 is a ceiling**; and re-running with
  13 winners gives **+4.3%**, with 12 **−3.7%**, with 11 **−11.7%**. A 12.3% return that flips sign on
  two horses out of 352 races is not a measurement.
- **Evidence.** **[derived — this session]** from `examples/backtest_summary.json`, committed in the
  repo and named by SYSTEM_MAP §3 as the source of the README numbers; reproduction script inlined in
  the source file. The variance formula `Var ≈ p(1−p)·o²` is textbook **[recall — unverified]**.
- **What STRIDE does today.** `README.md:109-121` and `docs/12:16-20` present 33.7%/−4.2% vs
  9.9%/+12.3% as the founding evidence for the value philosophy. `backtest_v2_metro.py:157-166` defines
  the six `STRATEGIES` and `STAKE = 100`; the module reports **no CI, no t-statistic and no standard
  error** for any strategy — per-strategy keys are exactly `label, bets, wins, strike_rate, staked,
  returned, pnl, roi`. The live band gates and `select_value_plays` are the productionised form.
- **The gap.** The system's foundational ROI claim has never had an error bar, and when one is
  attached it swallows the estimate whole. **This is not an argument that the value philosophy is
  wrong** — SYSTEM_MAP §2 shows the market anchor is independently well motivated. It is an argument
  that *this number cannot be used to defend any specific threshold*, and it is the first thing to fix
  before any A/B is meaningful.

### R3-F2 — The +12.3% is the best of six strategies, and is *weaker* than noise would typically produce · **[ROI]** · *cheap version implementable now; SPA needs work*

- **Finding.** `backtest_v2_metro.STRATEGIES` contains **six** bands. Five lost money (−28.0%,
  −100.0%, −14.8%, −4.2%, −5.4%); one made +12.3% and became the production filter. Under the null
  that all six have true ROI zero, treating their t-statistics as six independent standard normals,
  the **expected maximum t is 1.265** and the 95th percentile of the maximum is **2.384**
  (200,000-draw simulation, seed 0). The observed winner's t is **0.432** — **below the average
  maximum you would get from six coin flips.** A random search over six strategies on pure noise would
  typically hand you a better-looking winner. The multiple-testing correction is not needed to kill
  this result; the uncorrected statistic already fails.
- **Evidence.** **[derived — this session]** for the arithmetic. Framework: White's Reality Check
  (*Econometrica* 2000) and Hansen's SPA (2005), which bootstrap the distribution of the *best*
  strategy across the universe searched. **[snippet-only]** — Kuan et al. and Neuhierl; search summary:
  "the RC test is conservative because its null distribution is obtained under the least favorable
  configuration, and Hansen (2005) proposes the SPA test that avoids the least favorable
  configuration" — SPA is the more powerful and the one to prefer here. White 2000 heavily cited
  **[recall — unverified]**.
- **What STRIDE does today.** All six bands evaluated on the same 352 races (`date_from 2026-03-04`,
  `date_to 2026-04-18`, `races 352`, `runners 3396`, 10 metro tracks) and reported in isolation.
  Nothing adjusts for the fact that six were tried. `backtest.py` (v1) is worse — **13 strategy
  sweeps** plus an `optimize_threshold` call, with **no purge gap**.
- **The gap.** Strategy selection is a search, and the repo reports search *winners* as if they were
  pre-registered hypotheses. No reality-check, no SPA, no Bonferroni, **not even a count of trials
  carried into the report.** Structural, not a bug: `optimize_threshold` is literally a threshold
  search whose output was never deflated.
- **Caveat recorded by the agent.** The six strategies are **nested** and therefore positively
  correlated, which *reduces* the effective number of trials and lowers the expected max-t below
  1.265. The gap is somewhat overstated; the conclusion is unchanged, since 0.432 fails a single
  uncorrected test anyway.

### R3-F3 — Where the +12.3% actually comes from: one 81-bet cell, and ROI is *non-monotonic in edge* · **[BOTH]** · *implementable now as a diagnostic*

- **Finding.** The six strategies are nested, so the headline band decomposes exactly.
  `Value Edge 3%+ ($2–$15)` (142 bets) minus `Short Price $2–$5 3%+` (7 bets, 0 wins, −100%) minus
  `Big Value $5–$15 5%+` (54 bets, 4 wins, −14.8%) leaves **$5–$15 with edge 3–5%: 81 bets, 10 wins,
  staked 8,100, returned 11,350, ROI +40.1%** — contributing **186% of the entire Value-Edge profit**
  (own t = 0.97, 95% CI [−41%, +121%]). Within one price band:

| $5–$15 sub-band | bets | wins | ROI |
|---|---|---|---|
| edge 3–5% | 81 | 10 | **+40.1%** |
| edge ≥ 5% | 54 | 4 | **−14.8%** |

  **ROI falls as modelled edge rises.** If `modelEdge` measured anything real, expected return would
  be increasing in it — that is the definition of an edge. The profitable cell being the *lower*-edge
  one is the canonical signature of a threshold fitted to noise.
- **Evidence.** **[derived — this session]**, pure subtraction of nested strategy aggregates.
  *Caveat:* the bands overlap at exactly `odds == 5.0` and `Mid-Range $3–$8 5%+` straddles the split,
  so a handful of bets may be double-counted; the residual cell is ±a few bets and the sign and
  magnitude of the non-monotonicity are not sensitive to that, **but the exact figures should be
  recomputed from the per-bet list before being quoted anywhere load-bearing.**
- **What STRIDE does today.** The production gates hard-code the very cut this questions: `RTP:1825`
  `edge ≥ 3` for `5 < odds ≤ 15`, `RTP:1821` `edge ≥ 2.5` for `$3–$5`, `RTP:1816` `edge ≥ 4` below $3;
  `tips_day_aggregates.py:~91-103` selects value plays at `edge > 3`, `4 ≤ odds ≤ 15`.
- **The gap.** The system treats `modelEdge` as a monotone quality score (conviction bonus rising in
  edge, +3.0/+2.0/+1.0; longshot keep rule `odds≥15 & edge>2`). The only evidence available says it is
  **not monotone over the range that matters.** SYSTEM_MAP §9 Q14 already flags these gates as
  hand-tuned; this **upgrades them from "unvalidated" to "actively contradicted by the one dataset we
  have."** Fix: replace the six hand-picked bands with an (odds-decile × edge-decile) ROI surface with
  per-cell n and CI. **That single plot is worth more than any new feature in the roadmap.**

### R3-F4 — Selecting on maximum estimated edge guarantees the selected edges are overstated (winner's curse) · **[ROI]** · *needs work, but the ingredients exist*

- **Finding.** Ranking candidates by an estimate and keeping those above a threshold biases the
  retained estimates upward by construction — extreme observed values are disproportionately those
  whose noise term was large and positive. The correction is shrinkage toward the null: conditioning
  on `edge_hat ≥ 3` selects a set whose *true* mean edge is materially below 3, and the shortfall
  grows with σ and with how far into the tail the threshold sits. **For STRIDE this bites twice**,
  because the filter is applied to a difference of two noisy estimates (`calibrated − true_market`),
  so σ compounds.
- **Evidence.** *Breaking the Winner's Curse with Bayesian Hybrid Shrinkage*, arXiv:2511.06318;
  Bigdeli et al., *Bioinformatics* 32(17):2598, 2016. Both **[snippet-only]**. **Transfer
  justification:** this is domain-free selection statistics, not a genetics result — it applies
  wherever a threshold is applied to noisy estimates. The betting-specific analogue is Baker & McHale's
  Kelly shrinkage (R4-F5).
- **What STRIDE does today.** `RTP:697` computes `modelEdge` and every downstream gate applies a bare
  threshold with **no shrinkage and no uncertainty estimate**. The MC engine *does* produce a Wilson
  interval (α = 0.10, `racing_system_v8.3_mc.py:334`; `MC_SIM_LIMITS['ci_alpha'] = 0.10` at `:145-149`)
  and **no consumer of that interval exists in the edge computation.**
- **The gap.** STRIDE already computes the sampling uncertainty of its own probability and throws it
  away at exactly the moment it matters. A runner at 12% ± 4pp and one at 12% ± 1pp produce identical
  `modelEdge` and identical gate outcomes. Shrinking edge by its own uncertainty would preferentially
  remove the noisiest bets — precisely the population F3 shows is carrying the fake profit. Path:
  propagate the Wilson half-width through the `mw` blend (trivial — `mw × halfwidth`) and offer
  `edge_shrunk = edge − k·se` behind `STRIDE_EDGE_SHRINK`, default off.

### R3-F5 — ~3,000 bets are needed to detect a 12% ROI at this variance; ~18,000 at 5% · **[ROI]** · *implementable now — governance fix*

- **Finding.** Solving `n = (t·σ/ROI)²` with σ = 3.396 from F1:

| True ROI | bets for t = 2 | bets for t = 3 |
|---|---|---|
| +20% | 1,154 | — |
| **+12.3%** (observed) | **3,038** | 6,835 |
| +10% | 4,614 | — |
| +5% | 18,456 | — |
| +3% | 51,268 | — |

  At ~142 qualifying bets per 352 metro races (~0.40 bets/race), 3,000 bets is on the order of
  **7,400 races** — years of metro-only racing. The practitioner folklore that "300–500 bets is
  compelling evidence" **[snippet-only]** is **wrong by an order of magnitude for this strategy**,
  because it assumes near-even-money bets: variance scales with o², so winners averaging 11.4 need
  roughly (11.4/2)² ≈ **32×** the sample of winners averaging 2.0. **Corollary for the price
  ceiling:** this is a direct argument *for* `odds > 15 → NO_BET` on grounds the code does not state —
  not that longshots are unprofitable, but that **longshot returns are unmeasurable in any realistic
  sample.** A system that cannot measure a bet class should not bet it.
- **Evidence.** **[derived — this session]**; the n-for-t formula is elementary. The 300-bet folklore
  (punter2pro) is **[snippet-only]**, cited **to reject**, not to follow.
- **What STRIDE does today.** `shadow_pl_tracker.py:323` sets `MIN_BETS_REPORTABLE = 200`
  (consumed at `:363`) — **[audit 2026-07-25: the doc said `:130`; the constant is at `:323`. Value
  and consequence unchanged]**;
  `docs/10:83-84` states the ≥200 rule (constraint 19). At the observed variance, **200 bets gives a
  95% CI on ROI of roughly ±47 percentage points.** `walk_forward_backtest.py:220-229, 250-262` does
  compute t-distribution 95% CIs — but **across folds, not across bets**, so `n` is ~30 folds and it
  measures fold-to-fold dispersion of a mean, not the sampling error of the bet population.
  `backtest_v2_metro.py` computes no CI at all.
- **The gap.** `MIN_BETS_REPORTABLE = 200` is roughly **15× too small** for an ROI claim in this bet
  population, and is documented as a general rule rather than derived from the variance of the bets
  involved. Fine as a floor for *hit rate* (binomial, much tighter), not for ROI. Fix: make it
  variance-aware — compute realised per-bet SD from settled rows, report the CI, flag any tier whose
  CI spans zero as `NOT REPORTABLE` regardless of count.

### R3-F6 — Closing-line value is the variance-reduced skill proxy, and STRIDE already stores both prices · **[BOTH]** · *implementable now — the single cheapest high-value change found in this pass*

- **Finding.** The resolution to F5's sample-size problem is not to bet more, it is to change the
  estimand. CLV per bet ≈ `tipped_odds / closing_odds − 1`; it is observable on **every** bet including
  losers, its variance is a small fraction of P&L variance, and under the hypothesis that the closing
  line is the market's best estimate its expectation equals your true edge. Practitioner consensus
  encountered repeatedly: *"if you beat the closing line by 10%, you should expect to make a profit
  over turnover of 10% over the long run"*; *"bettors with sustained positive closing line value are
  profitable over thousands of wagers more than 90% of the time"*; *"300-500 bets with consistent
  positive CLV is compelling evidence."* Note the asymmetry with F5: 300–500 bets is plausibly enough
  for **CLV** precisely because CLV is low-variance, while nowhere near enough for ROI.
  **The limits of CLV, usually omitted:** (1) it is only a skill proxy *relative to the reference
  market* — beating a soft book's close is not skill; (2) it is not profit, and is unrealisable if you
  cannot get on; (3) if your own bet moves the line you manufacture CLV without skill; (4) under FLB
  the closing line is a **biased** estimate, so CLV inherits the bias and should be measured against a
  properly de-vigged close.
- **Evidence.** **[snippet-only]** throughout — **every CLV source reachable was practitioner, not
  peer-reviewed** (strideodds.ai, boydsbets, oddsjam, joesaumarez). The underlying claim (final odds
  are the market's most accurate forecast) has academic backing (F8). **The agent could not locate a
  peer-reviewed quantification of the CLV→profit relationship and flags that as a genuine gap, not as
  settled.**
- **What STRIDE does today.** Nothing. Grep for `closing.?line|clv|closing_odds` over all `.py`
  returns **zero** conceptual matches. But **both prices are already in the same row**:
  `shadow_pl_tracker.py:216` inserts `tipped_odds`; `:266` selects `sp_odds`; `:~305` writes `api_sp`
  and `tipped_horse_sp`. **In Australia the SP is the closing line.** The join is done, the row is
  written, the ratio is never taken.
- **The gap.** A skill metric available at zero data cost that would distinguish signal from noise in
  **~400 bets instead of ~3,000**, and it is not computed. `cmd_report` gains two columns (mean CLV,
  % positive CLV) — pure SELECT-side arithmetic, no schema change, no behaviour change.

### R3-F7 — STRIDE's own forward P&L settles at the closing price, discarding its entire early-price advantage · **[ROI]** · *implementable now*

- **Finding.** `shadow_pl_tracker.py` — SYSTEM_MAP §3's *"only true forward P&L"* — settles winners at
  SP, not at the tipped price: `sp = res.get("sp") or tipped_odds or 0` (`:290`); `pl = round(float(sp)
  − 1, 2) if sp else 0` (`:299`). `tipped_odds` is unpacked in the same loop header (`:283`) and used
  only as a missing-SP fallback. So the only realised-return series answers *"what would this have
  returned at the one price the punter demonstrably did not take?"* **The bias direction is
  determinate and correlated with pick quality:** good picks shorten (SP < tipped ⇒ ROI understated),
  bad picks drift (⇒ overstated) — the worst possible property for a tracker whose output gates
  production decisions.
- **Evidence.** **[derived — this session]** by source reading; lines quoted verbatim above. The
  direction argument follows from F6/F8 and needs no external source.
- **What STRIDE does today.** As quoted. `BET_TIERS = {CONFIRMED, CROWD_ONLY, LOCK}`,
  `MIN_BETS_REPORTABLE = 200`; `cmd_report` gives ROI by convergence tier including tiers the system
  refuses to bet — a design SYSTEM_MAP §3 rightly praises. **The settlement price undermines it.**
- **The gap.** Two P&L series should be reported side by side — at tipped price and at SP — and
  **their difference *is* aggregate CLV** (F6). Reporting only SP conflates "our picks are bad" with
  "our picks are good and the market noticed before the off". SYSTEM_MAP §9 Q7 names the `MODEL_ONLY`
  question *"the single highest-leverage unknown in the system"*; the tipped-vs-SP split is what makes
  it answerable, separating "no crowd support and the price held" (genuinely contrarian) from "no
  crowd support and the price collapsed" (the crowd was late, not absent). Do **not** alter the
  existing `profit_loss` semantics (constraint 4).

### R3-F8 — Late money is smart money, but improvement toward the close is non-monotonic — the residual inefficiency is *near* the close, not *at* it · **[BOTH]** · *aspirational-to-needs-work; but the CLV half needs no new collection*

- **Finding.** **[prior pass — extended]** `research/report.md §2.1` establishes: JRA (894,127 runners,
  2004–2023) — horses shortening in the final five minutes earn significantly higher returns at
  identical final odds (coefficient −0.3386, SE 0.0392, ~14× the economic magnitude of a
  cross-sectional odds difference); AU 2006 season (all 14,854 races) — the late pool-share ratio
  predicts net returns with coefficient 4.124 (z = 9.79), while **final prices remain wrong in 5 of 10
  favourite ranks**. What this pass adds is the *shape* of the approach to the close: accuracy
  improves toward post time but **not monotonically** — *"forecasts do not always improve
  monotonically as the games get closer … forecasts at weekend day games' start times are
  significantly worse than forecasts 90 minutes earlier"* and *"betting lines tend to overreact,
  exhibiting significant negatively autocorrelated changes"*. Independently, a Betfair UK racing
  microstructure study reports *"a remarkably high level of informational efficiency… rapidly decaying
  autocorrelations, no long-term memory… Hurst exponent showing mean reversion"* over **1,056,766
  price-change signals at 50 ms resolution**. Reconciling: the exchange price process is close to a
  martingale, yet the *final* price still carries a favourite-rank bias and the last few minutes carry
  a large information increment. **The exploitable window is just before the close, on the direction
  of the move, not at the close itself.**
- **Evidence.** *Inefficient Forecasts at the Sportsbook* **[snippet-only]**; arXiv:2402.02623,
  published in *International Journal of Information Technology* 2024 **[snippet-only]**; the JRA/AU
  cluster **[prior pass, verified 3-0]**.
- **What STRIDE does today.** De-vigs whatever quote arrives with the racecard; the convergence pillar
  snapshots odds at ~12:30 AM and ~8 AM. The steam/drift machinery exists in **two incompatible
  taxonomies** SYSTEM_MAP §8 warns must not be conflated (`odds_movement.classify_signal` at
  `odds_movement.py:211` vs `market_analysis.py`/`market_velocity.py`), and the convergence pillar's
  `market_injection` is **pinned to 0** on the live V3 path (`RTP:2697-2700`), so the STEAM/+8.0
  injection never fires.
- **The gap.** STRIDE consumes early prices, computes edge against an early de-vigged market, and its
  one live market-movement signal is pinned to zero. `research/report.md` rec 1 already promotes the
  T−5-minute snapshot to #1; this pass **agrees and adds a second reason** — the snapshot is also the
  reference price CLV needs (F6) and the input that would let the two rival steam taxonomies be
  adjudicated empirically. **Important nuance: for CLV measurement no new collection is needed — SP is
  already stored. Only late-odds-as-a-feature needs the new infrastructure. Do not let the harder half
  block the easy half.** Constraint 23/24 binds: prospective collection only, never backfill.

### R3-F9 — Proportional de-vig is the worst-performing published method, and it is the only one in the repo · **[ROI]** · *implementable now — R3's best-shaped ticket*

- **Finding.** Four published methods:

| Method | Form | Known defect |
|---|---|---|
| Multiplicative / normalisation | `pᵢ = (1/oᵢ)/Σ(1/oⱼ)` | **does not account for favourite-longshot bias** |
| Additive | subtract a constant from each implied prob | can produce negative probabilities |
| Shin (1993) | iterative; solves for insider fraction `z` | can give bookmaker probabilities > 1 when inverted |
| Power / logarithmic (Vovk & Zhadanov 2009; Clarke 2016) | `pᵢ ∝ (1/oᵢ)^k`, solve `k` so Σ = 1 | none of the above; allows FLB by construction |

  Clarke, Kovalchik & Ingram applied all four to three large bookmaker datasets in three sports and
  concluded the **power method universally outperforms the multiplicative method and outperforms or is
  comparable to the Shin method**.
- **Evidence.** Clarke, Kovalchik & Ingram, *American Journal of Sports Science* 5(6), 2017.
  **[snippet-only]**; corroborated by an independent search via the Vovk & Zhadanov (2009) route.
  **Relevance note:** Stephen Clarke is at Swinburne (Melbourne) and this is the standard reference in
  the Australian sports-modelling community **[recall — unverified]** — the transfer to AU racing is
  about as direct as it gets. **The agent could not obtain the numerical comparison tables; only the
  directional conclusion. That is flagged as the single most valuable missing number in R3.**
- **What STRIDE does today.** Proportional/multiplicative, once, at `RTP:432-442` and `:673-674`
  (**[audit 2026-07-25: both anchors verified verbatim, including `if valid < 2 or total_implied
  <= 0: return 1.0` at `:440-441`]**). ~~Grep
  for `shin|power_method|odds_ratio_method|devig|de_vig` over all `*.py` returns **zero matches**.~~
  **[audit: the *conclusion* holds — no de-vig method other than proportional exists anywhere in the
  repo — but the stated grep result is literally false. That pattern returns substring hits in
  `sectional_times_collector.py` ("Sunshine Coast", "finishing_burst") and `nsw_xml_collector.py`
  ("FinishingPosition"). Re-verified with word boundaries: **no Shin, power, odds-ratio or
  additive de-vig implementation exists.** The same over-strong wording appears in R1-F7, R3-F13 and
  §A1 and should be softened wherever it is repeated.]**
  Two secondary defects, both verified: `calculate_overround` returns `1.0` below two quotes, so a
  thin race has **its raw implied probability used as if it were vig-free** — a systematic
  overstatement of `true_market` and understatement of `modelEdge` exactly where the model might have
  most to say; and `mc_api` computes a **completely separate** edge with **no de-vig at all**
  (`edge = winPct − 100/odds`, `mc_api.py:7636`).
- **The gap.** The de-vig is the hinge of the whole system — `true_market` appears in the anchor
  (`:692`), the edge (`:697`), `fairOdds` (`:695`) and EV (`:955`) — and it is the one choice for
  which the literature has a clear tested answer STRIDE has not taken. **Unusually clean: the power
  transform is monotone in odds, so `odds_rank` is preserved and hit rate cannot move.** It only
  re-prices the edge. One bisection solving `Σ(1/oᵢ)^k = 1`, behind `STRIDE_DEVIG_METHOD` defaulting
  to `proportional` = byte-identical; constraint 3 names the site — extend the existing
  `calculate_overround`/`calibrate_and_score` pair, **not** a new module and **not** mc_api's separate
  edge.

### R3-F10 — Proportional de-vig biases the edge in a direction the `mw` ladder amplifies at short prices · **[BOTH]** · *diagnostic now; production change sequenced behind F9*

- **Finding.** **[derived — this session]** from the code. Under FLB the vig excess concentrates on
  longshots; proportional de-vig removes a **constant multiplicative factor** from every runner, so it
  leaves the residual bias intact — **understating favourites and overstating longshots.** Since
  `calibrated = mw·raw + (1−mw)·true_market`, the edge simplifies to
  **`modelEdge = mw · (raw − true_market)`**:

| odds band | `mw` (`RTP:679-690`) | `true_market` bias under FLB | effect on `modelEdge` |
|---|---|---|---|
| ≤ $3 | **0.80** | too low | **inflated ×0.80** |
| ≤ $6 | 0.70 | too low | inflated |
| ≤ $10 | 0.50 | ~neutral | ~neutral |
| ≤ $15 | 0.45 | too high | deflated |
| ≤ $30 | 0.40 | too high | deflated |
| > $30 | 0.30 | too high | deflated |

  **The two errors compound rather than cancel**: the de-vig bias is largest at short prices and that
  is exactly where `mw` is largest. The `<$3` gate is cleared on a systematically inflated number
  while the `$5–$15` gate is judged on a deflated one.
- **Evidence.** **[derived — this session]** — algebra on `RTP:692/697` plus the `mw` table.
  **Falsifiable and it survives the test:** the two shortest-priced strategies are the two worst
  performers — `Short Price $2–$5 3%+` **−100.0%** (0 from 7) and `Mid-Range $3–$8 5%+` **−28.0%**
  (3 from 25). Both are tiny samples, so this is **consistent with, not proof of** — but the sign is
  right on both cells and the mechanism was derived from the code before the numbers were looked at. A
  further hint: the calibration table's lowest bin (`bin_low −0.001, bin_high 0.1`) has
  `predicted_mean 0.0533` against `observed_mean 0.0291` on **1,786 runners** — the model's own
  residual FLB, *added* to the market's. FLB direction premise **[prior pass]**.
- **What STRIDE does today.** As quoted; the in-code justification for the rising `mw` is the Kelly
  audit at `RTP:676-678`, and no fitting script for those six numbers exists.
- **The gap.** The `mw` ladder was set to compensate for a *model* calibration problem at short prices
  and in doing so **also amplifies a *de-vig* problem at short prices** — two different defects
  addressed with one hand-set constant. Fixing the de-vig separates them, because a power de-vig
  absorbs the FLB into `true_market` where it belongs and lets `mw` go back to being purely about
  model trust. **Sequence: F9 flag off → diagnostic → F9 flag on with `mw` frozen → only then revisit
  `mw`.**

### R3-F11 — FLB in Australia is large but not exploitable on its own: the best odds-rank still loses ~12% against a ~14.5% takeout · **[ROI]** · *diagnostic implementable now*

- **Finding.** **[prior pass — extended]** For the 2006 AU season (14,854 races, verified 3-0): net-
  return coefficient on odds **−0.621 (z = −11.29)**; rates of return decline monotonically from
  **−12.35% on top favourites to −41.55% on 9th favourites**. The extension: **AU TAB win-pool takeout
  is ~14.5%** (with rounding down to the nearest 10c dividend pushing effective takeout higher).
  Therefore: blind backing of top favourites returns −12.35%, i.e. **~2 percentage points better than
  the pool average** — the bias is real and worth ~2pp — but −12.35% **is still a 12% loss**. FLB alone
  does not produce a profitable strategy; you must find, *within* the favourite population, the subset
  the market has underpriced, which requires genuine model skill, exactly as STRIDE assumes.
  Conversely, at the longshot end a model must overcome ~42 points of drag, not ~14.5.
- **Evidence.** **[prior pass]** for the AU coefficients (ECU working paper, verified 3-0);
  **[snippet-only]** for takeout (horise.com, bets.com.au) and for the fixed-odds persistence claim —
  *"the favourite-longshot bias appears equally evident in Australia, despite the fact that odds are
  determined by bookmakers competing with a state-run pari-mutuel market"* — traced to Whelan,
  *Economica* 2024, and Snowberg & Wolfers, *JPE* 118(4):723-746, 2010 (search summary:
  *"misperceptions of probability drive the favorite-longshot bias, as suggested by Prospect
  Theory"*). Both **403, fetch blocked**.
- **What STRIDE does today.** SYSTEM_MAP §8 lists FLB as a first-class concept corrected by
  `mc_recalibration.py` — which is **inert in this tree** (needs `calibration_model.json`, absent).
  So the FLB correction is documented, implemented, and **possibly not running**.
- **The gap.** The FLB defence rests on (a) a hard price ceiling and (b) a recalibrator that may be
  inert. Neither is measured (§9 Q8). **Consequence for the price band:** the `$2–$15` band spans
  roughly the 1st–5th/7th favourite ranks, where FLB drag runs from about −12% to the high-20s%. **The
  band is defensible on FLB grounds and should not be loosened** (constraint 27), and F5 adds the
  independent statistical reason. **Also flagged:** the −12.35%/−41.55% figures are **tote**; STRIDE
  prices against fixed odds, and **the magnitude in AU corporate fixed-odds books is not established
  by anything reachable.** Do not assume the tote coefficients transfer at full strength. Diagnostic:
  `race_results_history` holds `sp_odds` for the full field, so favourite-rank-by-SP → realised return
  is a single GROUP BY that reconstructs the ECU table on STRIDE's own data.

### R3-F12 — FLB is a *context effect*, not a stable function of price — which makes a fixed price band the wrong parameterisation · **[BOTH]** · *needs work — do not wire on the literature alone*

- **Finding.** Recent peer-reviewed work argues the longshot bias is not intrinsic to an odds level
  but to the **choice set** the gamble is presented in: *"contrast effects enhance the attractiveness
  of longshots because gambles presented in terms of their payoffs are easier to compare along the
  payoff dimension than along the probability dimension"*, and critically **"the longshot bias
  disappears when gamblers consider bets in isolation or when winning probabilities are easier to
  compare"**. Tested against natural variation in British horse-racing odds. If right, the size of the
  bias at $10 depends on **what else is in the race** — field size, price spread, whether there is a
  dominant favourite — and **not on $10**.
- **Evidence.** Meyer & Hundtofte, *The Longshot Bias Is a Context Effect*, **Management Science**
  69(11):6954-6968. **[snippet-only]**; top-tier venue, recent. *Transfer:* the empirical arm is
  British racing — the same product in a comparable bookmaker-dominated market; AU's tote/fixed-odds
  coexistence is a real difference.
- **What STRIDE does today.** **Every price-dependent constant is a function of absolute odds only:**
  the `mw` ladder, the bet gates, the `odds > 15` ceiling, the class odds caps ($25/$25/$30/$30), the
  longshot rules, `select_value_plays`'s `4 ≤ odds ≤ 15`. STRIDE *does* compute the within-race
  positional encoding (`odds_rank`, `odds_rank_pct`, `fair_implied_prob`) but those feed the **ML
  features, not any gate** — and the Phase-5 verdict ablated the trio at **−0.0012 AUC**, kept only
  "to avoid churn". There is also a dormant module of exactly the right shape:
  `market_efficiency.py:17-23` (**[audit: dict opens at `:17`, not `:18`; values verified]**), `SEGMENT_EDGE_THRESHOLDS = {'ultra_efficient': 0.05, 'efficient': 0.03,
  'moderate': 0.02, 'inefficient': 0.01, 'thin': 1.0}` with `METRO_TRACKS` — **zero production
  importers**.
- **The gap.** The literature says the right conditioning variable for an edge threshold is **race
  context** (field size, price dispersion, liquidity, segment) rather than absolute price, and STRIDE
  has a module implementing exactly that, unwired, alongside `race_context.compute_race_context` whose
  market-efficiency term is not used for gating either. **Honest caveat:** the Phase-5 result is
  evidence that re-encoding price adds no **predictive** skill; it is **not** evidence about using race
  context to set a **decision threshold** — different uses of the same information. Sequence: get F3's
  ROI surface running, re-cut it by field size and price dispersion, and only wire
  `market_efficiency.py` if the surface differs materially across contexts.

### R3-F13 — Shin's model is the FLB-aware de-vig with an economic story, and the natural sibling to F9 · **[ROI]**, low magnitude · *implementable now as a branch of F9's selector — do not ship separately*

- **Finding.** Shin (1993) models the bookmaker as a market maker pricing against a known fraction `z`
  of insider traders plus outsiders spread over the field; the optimal response is to shade longshots
  harder, which **derives** the FLB from bookmaker behaviour rather than assuming bettor
  irrationality. Inverting recovers fair probabilities and estimates `z`. Two empirical anchors: the
  prediction that **margins increase with the number of competitors** is empirically supported; typical
  `z` is small, on the order of **2%**. Whelan has a recent critical paper on whether `z` really
  measures insider trading — so **treat `z` as a fitted shape parameter that produces good
  probabilities, not a literal insider count.** For STRIDE the practical difference between Shin and
  power is small (Clarke et al.: power "outperforms or is comparable to" Shin) and power is easier: no
  iteration, no inversion pathology, cannot leave [0,1].
- **Evidence.** Shin 1993, *Economic Journal* **[recall — unverified]** for exact title/venue;
  **[snippet-only]** for the empirical claims via Cain, Law & Peel and the Whelan working paper. All
  fetches blocked.
- **What STRIDE does today.** Nothing — zero occurrences of `shin` in any `.py` (verified). The FLB
  glossary entry points only at `mc_recalibration.py`, which is inert.
- **The gap.** Marginal alone. Its real value is as the **economic justification** for why the de-vig
  must be FLB-aware in a *bookmaker* market specifically — which is STRIDE's market. Proportional
  de-vig is defensible in an idealised pari-mutuel; it is not defensible against a bookmaker who is
  deliberately shading. **Ship it behind F9's `STRIDE_DEVIG_METHOD` selector or not at all — do not
  create a second de-vig module** (constraint 3).

### R3-F14 — Kelly under parameter uncertainty says shrink the edge, and STRIDE's staking cannot even express an edge — so **do not enable Kelly yet** · **[ROI]**, gated behind the measurement work · *implementable now, deliberately deferred*

- **Finding.** Kelly assumes the probability is known. With estimated probabilities, substituting the
  estimate **systematically overbets**: a small overestimate of edge produces a large overallocation,
  and betting ~1.5× the Kelly fraction can drive long-run growth **negative**. Baker & McHale derive an
  explicit shrinkage factor; the quarter-to-half-Kelly convention is a crude version of the same
  correction. A horse-racing-specific treatment exists (arXiv:1701.02814). **This is the same
  correction F4 asks for, applied at a second point.** `research/report.md §2.5` (unverified cluster)
  supplies the sharpest version of the stakes: a calibration-selected model returned **+36.93%** under
  eighth-Kelly while a model only **0.8pp worse in ECE lost 75.9% of bankroll**. **Kelly converts
  miscalibration into ruin.**
- **Evidence.** **[snippet-only]** for Baker & McHale 2013 and arXiv:1701.02814; **[prior pass —
  unverified cluster]** for the ECE/Kelly figures (NBA moneyline, so **the transfer to AU racing is by
  analogy only**: the mechanism is sport-independent, the magnitudes are not). See R2-F15 — **those
  magnitudes are now pre-corrigendum and should be treated as withdrawn.**
- **What STRIDE does today.** `compute_staking` (`RTP:1007-1015`) is `high→"2u"`, `medium→"1u"`, else
  `"0u"`. A $2.50 shot and a $12 shot with the same confidence get the same stake. Fractional Kelly is
  implemented **twice** and called **zero** times (`RS:309-326`; `portfolio_risk.py:39, 61-71`, zero
  importers). `selections.kelly_stake` is a decoy column. `RTP:~743-745` defers the odds-band
  adjustment "to stake sizing (Kelly)" — a deferral to a thing that does not exist.
- **The gap.** SYSTEM_MAP §3 calls staking the one free lever. **This pass qualifies that in an
  important way: given F1–F4, STRIDE should not turn on Kelly.** Kelly sized off an edge whose t-stat
  is 0.43 and whose ROI is non-monotonic in the edge itself is the exact configuration the literature
  says produces ruin. **Correct order: measure first (F6, F1), then size.** When it lands, constraint 3
  names the module to extend — `racing_system_v8.3_mc.py:309-326`, **not** `portfolio_risk.py` (dead)
  and not a new module — and it should ship as a *shadow* column first.

### R3-F15 — An edge that survives measurement may not survive execution: AU minimum-bet limits are the binding constraint · **[ROI]** · *aspirational as code; implementable now as a documented capacity assumption*

- **Finding.** The cleanest natural experiment on whether a measured edge is bankable is Kaunitz,
  Zhong & Kreiner: a strategy exploiting cross-bookmaker odds dispersion, validated in a 10-year
  historical simulation, a 6-month minute-to-minute simulation, **and a 5-month real-money campaign**.
  The real-money result: *"after a few months of placing bets with actual money, bookmakers started to
  severely limit their accounts, despite the researchers playing according to the sports betting
  industry rules… the researchers were locked out of certain games"* — they terminated the experiment
  for that reason, **not because the edge decayed**. **Australia partially legislates against this.**
  Minimum Bet Limits require an operator to accept a bet to *lose* a set amount: **NSW, VIC, QLD, SA —
  $2,000/$800 metro win/place, $1,000/$400 country and provincial.** Racing NSW introduced MBL
  effective **1 September 2014**, Racing Victoria **October 2016**; the conditions explicitly prohibit
  evasion (*"wagering operators are not to take actions such as closing a punter's accounts … solely
  to avoid complying"*), with carve-outs for responsible gambling, fraud, AML and integrity.
- **Evidence.** Kaunitz, Zhong & Kreiner, arXiv:1710.02824, 2017 **[snippet-only]** (**403**);
  covered by MIT Technology Review 2017-10-19. *Transfer:* football, not racing — the transferable
  content is the **operator response to a winning account**, which is product-independent. MBL
  sources: Racing NSW primary regulator documents **[snippet-only]** — the strongest sources in this
  section, though unfetched; plus the DSS betting-restrictions report. The MBL timing conditions are
  **[recall — unverified — the agent could not verify them from a primary source]**.
- **What STRIDE does today.** SYSTEM_MAP §1: *"The system is advisory only: there is no bet-execution
  integration anywhere in the repo."* A human places the bets. Staking is `2u/1u/0u` with no bankroll
  on the live path.
- **The gap.** Not a code gap — a **planning** gap. The system produces up to 3 picks per race across
  a full card with no notion of total exposure, per-operator capacity, or the fact that the same 2u
  means something different at $2.50 and $12. `portfolio_risk.py` implements exactly the missing
  concepts (daily 15%, single 5%, ≤10 concurrent) and has zero importers. Concretely: **record in the
  docs that the validated universe is metro fixed-odds with ~$2,000-to-lose capacity per operator**, so
  no future ROI claim is made about a bet size the market cannot absorb.

### R3-F16 — Exchange liquidity in AU racing is thin outside metro Saturdays, and is declining · **[ROI]** · *implementable now — tag, do not gate*

- **Finding.** If Betfair is the intended execution venue, AU liquidity is the binding constraint:
  *"Australian markets are generally quite thin… many races on Sun–Fri only have approximately 30–50k
  matched"*; *"the average hold at Flemington dropped from $904,000 in 2022 to just $372,000 more
  recently"*; *"metropolitan Saturday meetings (Melbourne, Sydney) will generally have better liquidity
  than midweek or regional meetings"*. Commission: base **5%** on net winnings — against a nominal
  +12.3% that is ~0.6pp, **second-order compared with the ±55pp confidence interval from F1. Do not let
  commission modelling distract from the measurement problem.** Note the tension with F8: the same
  exchange that shows "remarkably high informational efficiency" at 50ms in the **UK** is thin and
  declining in **AU midweek** — efficiency and liquidity are the same coin.
- **Evidence.** **[snippet-only]** from Betfair community/trading forums and help pages. **These are
  practitioner forums, not peer-reviewed, and the "$904,000 → $372,000" figure in particular is a
  single unverified forum claim — treat as indicative only.** Directionally consistent with the one
  *verified* industry number in `research/report.md §1`: Racing Queensland FY2024-25 wagering turnover
  **$5.6bn, down 9.3%** (verified 3-0).
- **What STRIDE does today.** Nothing venue-aware. `market_efficiency.py` has a `METRO_TRACKS` set and
  a `'thin'` segment with edge threshold `1.0` (effectively "never bet") — the exact right idea — and
  is **unwired**. `examples/backtest_summary.json` shows the validating backtest ran on **10 metro
  tracks only** (Caulfield, Caulfield Heath, Doomben, Eagle Farm, Flemington, Morphettville, Newcastle,
  Rosehill Gardens, Royal Randwick, Warwick Farm), while production runs whatever cards are downloaded.
- **The gap.** **The validated universe (metro) and the deployed universe (all cards) differ, and
  nothing in the code marks the boundary** — a scope-generalisation error independent of every
  statistical issue above. Even if the +12.3% were real, it would be a claim about metro Saturdays.
  Cheap fix: tag every output row with the segment (no gating, no behaviour change), then report
  ROI/CLV split by segment. Gating comes later and only on evidence.

### R3-F17 — The correct validation protocol for a filter search · **[ROI]** · *steps 1 and 5 are report lines; PBO/CSCV is the one worth building*

- **Finding.** The protocol the literature prescribes for selecting a betting filter from a family of
  candidates on a short backtest window: **(1) Declare the trial count** — the probability of selecting
  an overfit strategy *grows rapidly with the number of trials*, and a backtest is uninterpretable
  unless N is reported. STRIDE ran 6 and 13 + an `optimize_threshold` call; **neither number appears in
  any report.** **(2) Deflate the performance statistic** — the Deflated Sharpe Ratio adjusts for
  trials, trial-statistic variance, skewness, kurtosis and sample length; betting returns are extremely
  right-skewed and fat-tailed (a 9.9% strike paying ~11.4), **so the non-normality correction is the
  dominant term, not cosmetic.** **(3) Estimate PBO via CSCV** — split the return series into S
  subsets, form all combinatorially-symmetric partitions, select the best in-sample strategy and record
  how often it ranks below median out-of-sample; **PBO near 0.5 means the selection procedure is
  worthless.** **(4) Apply a reality check** — White's RC or Hansen's SPA (more powerful).
  **(5) Report Minimum Backtest Length.** *Honest summary:* at N = 6 trials, 352 races, 142 bets and
  t = 0.43, **steps 2–5 are unnecessary because step 1 already settles it** (F2). They become necessary
  the moment the sample grows enough for a winner to look plausible — exactly when the temptation to
  believe it is strongest.
- **Evidence.** Bailey & López de Prado, *The Deflated Sharpe Ratio*, **Journal of Portfolio
  Management** 40(5), 2014; Bailey, Borwein, López de Prado & Zhu, *The Probability of Backtest
  Overfitting*, **Journal of Computational Finance**, 2016; White (2000) and Hansen (2005). All
  **403, [snippet-only]**; heavily cited in quant finance **[recall — unverified]**. **The agent could
  not obtain the DSR/MinBTL equations, so F17 is a protocol sketch, not an implementable spec.**
  *Transfer:* the framework is about *strategy selection under multiple testing*; a betting filter
  search over (price band × edge threshold) is structurally identical to a trading-rule search over
  (lookback × threshold) — only per-bet returns replace per-period returns, and the skew/kurtosis
  corrections matter more, not less.
- **What STRIDE does today.** `walk_forward_backtest.py` is the most rigorous harness (expanding
  window, `gap_days=7`, AUC/Brier/log-loss/ECE, ROI and hit rate at five thresholds with t-dist 95%
  CIs) — but its CIs are **across folds** (F5) and it evaluates the **ML ensemble**, not the wrapper.
  `retrain_v2.DateWindowSplitter` uses a 14-day purge gap; `backtest.py` has **none**. **Nothing
  computes DSR, PBO, MinBTL, RC or SPA.** `docs/12 §6`'s validation protocol lists only *metrics*, not
  *multiple-testing corrections*.
- **The gap.** The repo has genuinely strong **leakage** discipline — `research/report.md §3` rates it
  *"ahead of most published work"* — and essentially **no selection-bias discipline**. Those are
  different failure modes and **the first does not protect against the second**: a perfectly leak-free
  backtest of 6 strategies still overstates the winner. This is the governance change that makes every
  other ticket in this research run trustworthy.

---

# R4 — Staking & Bankroll

17 findings. **Zero papers fetched** (~20 hosts, all 403); a control fetch on
`raw.githubusercontent.com` succeeded, proving the tool worked and the blocks were policy. All
literature is **[snippet-only]**. **F1, F2, F3, F13, F14, F16 and F17 rest on source reading and the
agent's own arithmetic and do not depend on the blocked literature — those are the ones to trust most.**

### R4-F1 — Kelly's sign test is the vig-inclusive EV test, and STRIDE's bet gate does not implement it · **[BOTH]**, in opposite directions · *implementable now — a sign test on numbers already in the pick dict*

- **Finding.** For a single win bet at decimal odds `o` with true probability `p`,
  `f* = (p·o − 1)/(o − 1) = EV/(o−1)`. Two consequences matter more than the sizing: (a) **`f* > 0`
  if and only if `p·o > 1`**, so Kelly's own sign is exactly the "is this bet profitable at the price
  I am offered" test; and (b) Kelly is scale-free in the bookmaker's margin — it needs no de-vigging,
  because the margin is already inside `o`. STRIDE's two value quantities are both computed against
  the **de-vigged** market, so neither tests `p·o > 1`. Writing `R` for the overround:
  `ev = R·(p·o) − 1`, and `modelEdge > 0 ⟺ p·o > 1/R`. **Both positivity tests are satisfied at
  `p·o = 1/R`, where the true return at the price is `1/R − 1` — i.e. −16.7% at an overround of 1.20.**
  The race normaliser accepts overrounds up to **1.60** (`race_normaliser.py:225`), at which `ev > 0`
  is satisfied by bets returning **−37.5%**. **[audit 2026-07-25: all of the algebra in this finding
  reproduces, and the `1.60` bound is verified. One thing to add that makes it worse, not better:
  the `0.90 / 1.60` check is guarded by `if odds_count >= 3` at `race_normaliser.py:223`, so a race
  with two quotes gets **no overround validation at all** while `calculate_overround` still de-vigs
  it, and a race with one quote gets neither validation nor de-vig (`RTP:440-441` returns `1.0`).
  The unbounded-overround case is not the 1.60 ceiling; it is the sub-3-quote race.]** This propagates into sizing: `compute_confidence`
  (`RTP:965-968`) grants **`high`** — hence the system's largest stake `2u` — on `ev > 0 and edge > 1.0`.
- **Evidence.** Kelly 1956, *Bell System Technical Journal* 35(4):917-926 **[recall — unverified]**
  (host blocked; the formula is reproduced identically in STRIDE's own code at `RS:319` and
  `portfolio_risk.py:33`, so the formula is not in doubt). The `f* = EV/(o−1)` restatement, the
  `p·o > 1` identity and the substitution `ev = R·p·o − 1` are **the agent's own algebra** from
  `RTP:673-674` and `RTP:955`, both read this session.
- **What STRIDE does today.** `evaluate_bet_candidate` (`RTP:1778`) reads `edge_pct` (the de-vigged
  edge) at `:1801` and applies 4 / 2.5 / 3 percentage-point thresholds by band.
  **Nothing anywhere on the live path evaluates `p·o − 1`.** SYSTEM_MAP §8 records the three
  incompatible edge definitions but does not draw out that the live one is **margin-blind**.
- **The gap.** The live bet gate cannot distinguish "beats the market's *fair* estimate" from "is
  profitable at the price the market is *charging*". Kelly supplies that test for free, needs no new
  data, and slots into `evaluate_bet_candidate` behind a default-off flag. **Be explicit that it will
  *lower* hit rate**, because the bets it removes are concentrated at short prices where strike rate is
  highest (F2).

### R4-F2 — Quantified: at realistic AU overrounds, STRIDE's sub-$6 bands are structurally negative-EV — and the live gate is not the validated band · **[ROI]** · *implementable now (publish a second `edge_at_price` field); blocked on one measurement*

- **Finding.** The gap between the de-vigged `modelEdge` and the vig-inclusive edge `p − 1/o` is
  exactly `(100/o)·(1 − 1/R)` percentage points. Setting the live gate `g` equal to that gives the
  **maximum overround at which each band can break even**: `R* = 1/(1 − g·o/100)`.

| Price | Live gate `g` (pp) | Anchor | Break-even edge at R=1.15 | at R=1.25 | Max overround `R*` tolerated |
|---|---|---|---|---|---|
| $2.50 | 4.0 | `RTP:1816` | 5.22 | 8.00 | **1.111** |
| $3.00 | 2.5 | `RTP:1821` | 4.35 | 6.67 | **1.081** |
| $4.00 | 2.5 | `RTP:1821` | 3.26 | 5.00 | **1.111** |
| $5.00 | 2.5 | `RTP:1821` | 2.61 | 4.00 | **1.143** |
| $6.00 | 3.0 | `RTP:1825` | 2.17 | 3.33 | 1.220 |
| $10.00 | 3.0 | `RTP:1825` | 1.30 | 2.00 | 1.429 |
| $15.00 | 3.0 | `RTP:1825` | 0.87 | 1.33 | 1.818 |

  A single-figure AU fixed-odds win market is not run at a 108% book. **The $3–$5 band requires an
  overround below ~1.08 to break even and is therefore negative-EV in essentially every real market;**
  the sub-$3 band needs below ~1.11; the $5–$15 band is sound from about $6 upward. **Second,
  independent instance of the same confusion:** `backtest_v2_metro.py:215-217` — the harness that
  produced the README numbers — computes `implied = 1.0/sp; edge = prob − implied`, i.e. the
  **vig-inclusive** edge, so the validated `"Value Edge 3%+ ($2-$15)"` band is a 3-point threshold on
  *that* quantity. **The live gate applies a 3-point threshold to a different, systematically larger
  quantity — it is looser by `(100/o)(1 − 1/R)` pp at every price: 3.3pp at $5/R=1.20, 1.1pp at
  $15/R=1.20. The live gate is not the validated band.**
- **Evidence.** All arithmetic is the agent's. Inputs source-read this session: `RTP:432-442`,
  `:673-674`, `:697`, `:1816/1821/1825`, `backtest_v2_metro.py:157-166` and `:215-218`,
  `race_normaliser.py:225`. AU FLB **[prior pass — extended, verified 3-0]**: that pass used FLB to
  justify *not loosening toward longshots*; **this arithmetic shows the *short* end is where the live
  gate leaks — the opposite end of the price range.**
- **What STRIDE does today.** Bands as tabulated. `docs/12:435-438` sets the promotion bar in terms of
  "the Value-Edge band's ROI" — a band defined on the backtest's edge, not the live one.
- **The gap.** **Two different edges wear the same name and the same "3%" threshold.** Nothing measures
  the live gate's realised ROI (§9 Q6), so this has been unfalsifiable. **The single most likely
  explanation available for the 33.7%-strike / −4.2%-return result.**
- **Blocking dependency, stated by the agent.** **The actual overround distribution is unknown** — the
  repo computes it at `RTP:432-442`, validates it into `[0.90, 1.60]`, and **stores it nowhere and
  aggregates it never.** The R = 1.15 / 1.25 columns are plausible AU assumptions, **not
  measurements.** One query would confirm or kill F2. **This is the single most valuable cheap
  measurement identified in R4.**

### R4-F3 — The `2u/1u/0u` ladder mis-sizes by ~3× across the price range, in the wrong direction · **[ROI]** · *relative ladder implementable now; true Kelly needs F15*

- **Finding.** `f* = EV/(o−1)`, so a flat unit ladder is correct at exactly one price and wrong most
  where the denominator is largest — the long prices, which are also where probability estimation
  error is largest. From the README band (9.9% strike, +12.3% ROI) the implied mean price is
  `o = 1.123/0.099 ≈ $11.3`, so `f*_full = 0.123/10.34 = 1.19%`. At the other end of the gate, a $2.50
  shot passing `edge ≥ 4, prob ≥ 30` with a genuine +5% EV gives `f*_full = 0.05/1.5 = 3.33%`. The only
  unit definition in the repo is `unit_percent = 0.01` (`RS:132`), so `2u = 2%`:

| Bet | full Kelly `f*` | STRIDE stake | ratio to **full** Kelly |
|---|---|---|---|
| value band, o ≈ $11.3, EV +12.3% | 1.19% | 2u = 2% | **1.68×** |
| short price, o = $2.50, EV +5% | 3.33% | 2u = 2% | **0.60×** |

  A **2.8× mis-sizing spread**, over-betting precisely the runners whose probabilities are least
  reliable. Against the repo's own documented **quarter**-Kelly default the long-price bets are staked
  at **6.7×** the correct size, and 1.68× full Kelly is already inside the region where the growth-rate
  parabola falls steeply toward its zero at 2× (F4).
- **Evidence.** `f* = EV/(o−1)` is elementary Kelly; effect sizes are the agent's arithmetic from
  README figures and `RS:132`, read this session. **Caveat inherited:** the 9.9%/+12.3% figures
  describe the **ML ensemble under backtest strategy bands**, not the live wrapper (§9 Q6), so all of
  F3's magnitudes carry that caveat.
- **What STRIDE does today.** `compute_staking` (`RTP:1007-1015`) receives **no price argument at all**
  — its only input is `h["confidence"]`. SYSTEM_MAP §7b.12 records the in-code admission at
  `RTP:~743-745` that the odds-band adjustment "is deferred to stake sizing (Kelly)" — **a deferral to
  a function that never applies it.**
- **The gap.** **Price is the one input Kelly needs most and the one input the staking function does
  not receive.** This changes not one bet's identity, only its size.

### R4-F4 — Fractional Kelly: the growth cost is `c(2−c)`, and the growth rate is zero at `2f*` · **[ROI]**, plus a drawdown dimension no ROI number captures · *policy choice now; execution needs F15*

- **Finding.** For Kelly fraction `c`, expected log-growth relative to full Kelly is
  **`g(c·f*)/g(f*) = c(2 − c)`** — a downward parabola peaking at `c = 1` with roots at 0 and **2**.
  Consequences quoted from the literature: **half Kelly retains ≈75% of the growth rate** (and by
  symmetry so does `c = 1.5`); **quarter Kelly retains `0.25 × 1.75 = 43.75%`**; **at `c = 2` excess
  growth is exactly zero, and beyond `2f*` wealth tends to zero almost surely even though the edge is
  genuine and `E[W]` is still rising.** Security side: **full Kelly gives ≈1/3 probability of halving
  the bankroll before doubling it; half Kelly ≈1/9**; under full Kelly the probability of ever drawing
  down to fraction α is ≈ α. **Betting 30% of Kelly cuts the chance of an 80% drawdown from 1-in-5 to
  1-in-213 while keeping 51% of the growth.**
- **Evidence.** MacLean, Ziemba & Blazenko 1992, **Management Science** 38(11):1562-1585; MacLean,
  Thorp & Ziemba, *Long-term capital growth*, **Quantitative Finance** 2010; Ziemba, *Using the Kelly
  Criterion for Investing*. **All hosts 403 — [snippet-only].** The 1/3-vs-1/9 figure, the 75%
  retention, the zero-growth-at-2× result and the 30%-Kelly/1-in-213 figure **each appeared in two or
  more independent searches with different phrasings** — the strongest corroboration available under
  the block. **[audit 2026-07-25]** MacLean, Ziemba & Blazenko 1992 resolves at *Management Science*
  38(11):1562-1585 (title *Growth Versus Security in Dynamic Investment Analysis*). **CONFIRMED:**
  "a Kelly bettor has a **1/3** chance of halving a bankroll before doubling it, and a half-Kelly
  bettor only a **1/9** chance"; and half-Kelly retaining **≈75%** of the growth rate.
  **DOWNGRADED to [unverified]: the "betting 30% of Kelly cuts the chance of an 80% drawdown from
  1-in-5 to 1-in-213 while keeping 51% of the growth" figure.** It was not recovered this session
  from any source, and the Ziemba chapter PDF that would carry it
  (`webhomes.maths.ed.ac.uk/.../Chap1_KellyZiemba.pdf`) is reachable in search listings but was not
  read. The `c(2−c)` identity and the zero at `2f*` are elementary algebra and stand on their own. Among the most cited results in the capital-growth literature **[recall — unverified on
  exact counts]**.
- **What STRIDE does today.** Fractional Kelly is the staking method *of record in the docs*:
  `KELLY_FRACTIONS {HIGH 0.30, MEDIUM 0.20, LOWER 0.10}`, `KELLY_FRACTION_DEFAULT = 0.25`,
  `MAX_KELLY_STAKE = 0.05` (`RS:35-41`), plus a second independent implementation with
  `kelly_fraction=0.25` in `portfolio_risk.py:39`. **Neither is on the live path** (F16); the live path
  applies no fraction because it applies no Kelly.
- **The gap.** STRIDE has the constants and none of the mechanism. Read against F3, the live `2u` on
  the average value bet is **1.68× full Kelly ≈ 6.7× the repo's own documented quarter-Kelly default**
  — on the wrong side of the growth peak, heading toward the zero at 2×, on probabilities that are
  demonstrably imperfect. **The repo already picked `0.25` twice, independently — adopt that, do not
  invent a third number** (constraint 3).

### R4-F5 — Kelly under parameter uncertainty: the correct shrinkage scales with `σ²(p̂)` — and STRIDE computes that uncertainty and discards it · **[ROI]** · *implementable now (the fraction); dollar amount needs F15*

- **Finding.** **The result that matters most for STRIDE.** Plugging `p̂` into `f*` systematically
  **over-bets**, because the growth function is concave in bet size and over-betting is asymmetrically
  worse (F4's parabola is steep on the right and crosses zero at 2×). Baker & McHale show the optimal
  correction is multiplicative shrinkage `f = k·f̂*` with `0 < k < 1`, where **`k` depends on `σ²`, the
  variance of the sampling distribution of `p̂`** — shrinkage increases with estimation uncertainty —
  and give a "back-of-envelope" form usable by bettors, validated on simulation and real tennis data
  where shrunken Kelly beats raw Kelly out of sample. Metel derives the horse-race version: stochastic
  optimisation for **mutually exclusive outcomes** with probabilities from an MNL. **Practical reading:
  fractional Kelly is not a fudge factor, it is the first-order correction for the uncertainty in your
  own probabilities — and the right fraction is not a constant, it should be smaller where your
  probability is less certain.**
- **Evidence.** Baker & McHale 2013, **Decision Analysis** 10(3):189-199; Metel 2017, arXiv:1701.02814;
  Chu, Wu & Swartz, *Modified Kelly Criteria*. **All 403 — [snippet-only].** The venue/volume/pages and
  the σ-dependence of `k` were returned consistently by two independent searches; **the exact closed
  form of `k` could not be obtained and the agent deliberately did not guess it. F5 is therefore
  directional, not implementable-as-specified.**
- **What STRIDE does today.** Nothing shrinks for uncertainty — but **STRIDE already computes the
  uncertainty and throws it away.** The MC emits Wilson intervals at α = 0.10 (`RS:334`;
  `MC_SIM_LIMITS['ci_alpha'] = 0.10` at `:145-149`), surfaced as `ciLower`/`ciUpper` and
  `sectionalMcCiLower/Upper` at `mc_api.py:7479-7481`. `compute_staking` never reads them. The
  Dirichlet concentration `max(6.0, 12.0 + 1.3 × n_historical_runs)` (`RS:1804`) is an explicit
  per-runner evidence-count knob — literally how much data stands behind each probability — and it too
  never reaches sizing.
- **The gap.** **STRIDE's stake is a function of a three-level confidence label and of nothing else,
  while a per-runner, per-race, simulation-derived uncertainty estimate already exists two modules
  upstream and is discarded.** A Wilson half-width is a directly usable `σ̂`. No new data, no new
  collection; only the bankroll state (F15) is missing to turn a fraction into a dollar amount, and the
  *fraction* can be computed and published today with zero behaviour change.

### R4-F6 — Miscalibration is amplified into ruin by Kelly: 0.6pp of ECE separated +36.9% ROI from −75.9% of bankroll · **[BOTH]** · *a precondition, not a feature*

- **Finding.** On NBA moneyline over 2018/19, the calibration-selected model had **classwise-ECE
  4.46%** vs the accuracy-selected model's **5.03%** — a gap of **0.57 percentage points** — and the
  calibration-driven system reached **maximum ROI +36.93% under eighth-Kelly**, while the
  accuracy-driven system's best was **+5.56%** (and that under *fixed* staking) and it **ended at
  −75.9% ROI**. Averaged over configurations: **+34.69% vs −35.17%**. Two systems with near-identical
  accuracy (**64.62% vs 64.27%**) diverged wildly in profit. **The mechanism is the point: Kelly's
  stake is proportional to the estimated edge, so a model that is confidently wrong stakes most exactly
  where it is most wrong. Kelly converts a calibration defect into a bankroll defect superlinearly.
  Accuracy — and by direct extension hit rate — is a near-useless discriminator of profitability.**
- **Evidence.** Walsh & Joshi 2024, **Machine Learning with Applications** 16:100539. **Hosts 403 —
  [snippet-only].** **[prior pass — extended]**: `research/report.md §2.5` lists this cluster as
  *unverified, session-capped*; this agent could not verify it either but (a) pinned the
  volume/issue/article number, (b) recovered the ECE pair 4.46%/5.03% quantifying how *small* a
  calibration gap produced the divergence, (c) established that −75.9% is an ROI, and (d) **flagged the
  corrigendum, which the prior pass did not**. ⚠️ **See R2-F15: the corrigendum revised *all* results;
  treat these exact figures as provisional/withdrawn pending re-fetch. The direction survives.**
- **What STRIDE does today.** Calibration machinery is extensive on paper (five layers) but only the
  global isotonic can fire, and only if a git-ignored pickle exists on the production box — **which
  cannot be verified from the repo.** ECE is computed in exactly one place
  (`walk_forward_backtest.py`, 10 bins), which cannot run in this tree, and never on the live path.
  Promotion is gated on AUC.
- **The gap.** **STRIDE is one flag away from being a Kelly system while not knowing whether its
  calibrator is running. That ordering is backwards. Establishing live ECE must precede, not follow,
  any Kelly wiring.**

### R4-F7 — Drawdown-constrained Kelly dominates fractional Kelly at equal drawdown · **[ROI]**, specifically the tail-risk part · *needs work; a dependency-free simplified version is the right first step*

- **Finding.** Rather than scaling Kelly down and accepting whatever drawdown results, impose the
  constraint directly: `Prob(min_t W_t < α) ≤ β`. Busseti, Ryu & Boyd derive a **convex bound** on the
  drawdown probability; substituting it yields a tractable convex program that *guarantees* the
  constraint, with the bound reported close to Monte-Carlo-measured risk. Headline: **the resulting
  bets outperform fractional-Kelly bets at the same drawdown-risk level, or achieve the same growth at
  lower drawdown risk.** Parameterised by a single risk-aversion number; its quadratic approximation
  reduces to Markowitz mean–variance.
- **Evidence.** Busseti, Ryu & Boyd 2016, *Risk-Constrained Kelly Gambling*, arXiv:1603.06183
  (published in *Journal of Investing*, 2016 **[recall — unverified on the journal]**). **403 —
  [snippet-only].**
- **What STRIDE does today.** `portfolio_risk.py` has a drawdown estimator — **dead code with three
  defects.** `_estimate_max_drawdown` (`:254-278`) runs 1,000 paths but only **over the bets active in
  one day**, so it measures intra-day sequence risk, not bankroll drawdown over time; it uses arrival
  order as if it were race order; and `compute_portfolio_risk` at `:235` computes
  `variance += (stake ** 2) * odds * (1.0 - 1.0 / odds)` — which is `s²o²q(1−q)` evaluated at
  **`q = 1/odds`, the market-implied probability**, not at `b['win_probability']`, which is in scope on
  the line above and *is* used for the EV term at `:234`. **The Sharpe ratio at `:237` therefore
  divides a model-based EV by a market-based standard deviation.** Zero importers anywhere
  (re-verified this session).
- **The gap.** No drawdown control of any kind is live. The convex program needs a solver (cvxpy is not
  in `requirements.txt`) and a bankroll history; a **simplified, dependency-free** version — choose `c`
  such that the empirical drawdown of a replayed `stride_tip_results` P&L path stays within a stated
  bound — is *implementable now* and is the right first step. **Do not resurrect `portfolio_risk.py`
  without fixing `:235` first.**

### R4-F8 — Distributionally robust Kelly: worst-case growth improves >1.5× under ±15% probability error, in a horse-racing example · **[ROI]** · *full convex form needs work; the 1-D degenerate form is one line*

- **Finding.** Instead of a point estimate of `p`, take an **uncertainty set** and maximise the
  **worst-case** expected log-growth. Sun & Boyd show this stays convex (DCP-representable) for a large
  class of uncertainty sets and extends Breiman's asymptotic-optimality result to the worst case.
  Reported effect size, in **an illustrative horse-racing example where each probability may vary by
  ±15% from nominal**: the **worst-case growth of the robust bet is more than 1.5× that of the nominal
  Kelly bet**. **Domain transfer: none needed — the worked example is horse racing.**
- **Evidence.** Sun & Boyd 2018, arXiv:1812.10371. **403 — [snippet-only].** ⚠️ **The ±15% and >1.5×
  figures came from a single search and were not independently corroborated.**
- **What STRIDE does today.** No uncertainty set — but STRIDE has an unusually natural one sitting
  unused: **it runs two probability engines that disagree, and the disagreement is already
  materialised.** `rawModelProb = (1−ml_w)·mc + ml_w·ml` (`RTP:667-670`), with both probabilities in
  scope at that line. The interval `[min(mc, ml), max(mc, ml)]`, or the MC Wilson interval (F5), is a
  ready-made uncertainty set. `mc_spread` (`RTP:703-705`) is already a race-level dispersion measure
  used to *penalise scores*, never to size stakes.
- **The gap.** **The system measures engine disagreement, uses it to shrink *scores*, and then stakes
  as if the probability were certain.** Degenerate one-dimensional form, which is the practically
  important case: **size on the lower end of the probability interval instead of its centre** — a
  robust-Kelly stake with no solver, one line of arithmetic on values already in scope at `RTP:667`.

### R4-F9 — Within-race multi-outcome Kelly is correctly unavailable to STRIDE today, and worth knowing why · **[ROI]**, currently unrealisable · *aspirational — **do not propose in Phase 4***

- **Finding.** For a single race, outcomes are **mutually exclusive** and stakes share one budget, so
  the optimal allocation is not "apply single-bet Kelly to each runner". Isaacs (1953) gave the closed
  form for parimutuel win betting; **Smoczynski & Tomkins (2010)** gave an explicit closed-form
  log-optimal allocation via KKT with an **inclusion rule** that avoids solving a system: sort runners
  by expected revenue rate and **include a runner only if its expected revenue rate exceeds the
  fraction of unallocated (reserve) wealth**, which descends as runners are added. **Whitrow (2007)**
  solves the general many-simultaneous-bets problem with stochastic-gradient algorithms benchmarked
  against the simplex method, on real bookmaker odds. **Whelan (2025)** revisits it in the *Bulletin of
  Economic Research*. Practically: **the log-optimal solution routinely includes two or three runners
  in one race**, and the reserve fraction acts as a natural stopping rule.
- **Evidence.** Smoczynski & Tomkins 2010, **The Mathematical Scientist** 35:10-17; Whitrow 2007,
  **JRSS-C** 56(5):607-623; Whelan 2025, **Bulletin of Economic Research**. **All 403 —
  [snippet-only].** The Whitrow venue/volume/pages and the Smoczynski–Tomkins inclusion condition were
  each returned by two independent searches.
- **What STRIDE does today.** **One bet per race, maximum.** `choose_bet_race_pick` (`RTP:2000`)
  returns the raw model leader **or nothing** — a hard documented contract (`docs/01:121-123`, asserted
  by `validate_tips.py`). Dead `portfolio_risk.optimize_stakes` (`:304-362`) *does* contemplate
  multiple bets per race (`_assess_correlation_risk` returns `'high'` on `max_same_race >= 3`) but
  allocates `proportion = kf/total_kelly` treating them as **independent**, and `compute_portfolio_risk`
  sums variances **with no covariance term** — **mutually exclusive runners modelled as if they could
  all win together.** That is the exact error this literature exists to correct.
- **The gap.** Today: **none that is actionable** — the one-bet-per-race contract makes within-race
  allocation moot, and that is *correct given the contract*. The gap is **latent**: any future
  multi-runner or each-way work must not use `portfolio_risk.optimize_stakes` as written. The genuine
  cost is an **opportunity** cost. Relaxing the invariant is squarely against constraint 5b-10 —
  **record it as a known bound on the system.**

### R4-F10 — Simultaneous bets across concurrent races: the sum of individual Kelly fractions over-bets, and STRIDE has no daily budget on the live path · **[ROI]** · *proportional day cap implementable now; correlation-aware allocation needs work*

- **Finding.** Bets that settle simultaneously are one joint gamble, not a sequence, so the log-optimal
  total allocation is **strictly less than the sum of the single-bet Kelly fractions** — compounding
  cannot occur between them. Naive joint optimisation is `O(2^N)`; Whitrow's stochastic-gradient
  algorithms are the tractable route. **Positive correlation between bets reduces the optimal
  allocation; negative correlation permits slightly larger ones.** Practitioner approximation reported
  across sources: compute Kelly per bet, apply the fractional multiplier, then **cap total simultaneous
  exposure (commonly quoted at 20–25% of bankroll) and scale down proportionally if the cap binds.**
  **Domain transfer, explicit:** an Australian Saturday runs 8–10 metro and provincial meetings
  concurrently with races jumping every few minutes, so a day's tips are overwhelmingly *simultaneous*
  — this literature applies directly and is not a finance import. AU-specific positive correlations are
  identifiable (same track going/rail/bias, same meeting-day weather, same jockey/stable), and
  **STRIDE has features for every one of them, so the correlation is modelled in the probabilities and
  then ignored in the sizing.**
- **Evidence.** Whitrow 2007 (as F9); *A Comparison of Simultaneous Kelly Betting Strategies*,
  **Journal of Gambling Business and Economics**. **403 — [snippet-only].** **The 20–25% cap is
  practitioner consensus reported in search text, not a peer-reviewed result — treat as a heuristic.**
- **What STRIDE does today.** **No daily exposure control exists on the live path.**
  `run_tips_pipeline.py` contains **zero occurrences of "bankroll"** (grepped this session);
  `total_units` appears only in a stderr summary at `RTP:2932`. The two exposure caps in the repo are
  both off-path and **mutually contradictory**: `STAKING_CONFIG['max_daily_units'] = 30` (`RS:131`) at
  `unit_percent = 0.01` means **30% of bankroll per day**, plus `MC_STAKING_CAPS['per_track_units'] =
  12` (`RS:152`) = 12% per track; while `portfolio_risk.py:61` sets `max_daily_exposure_pct = 15.0` and
  `max_single_bet_pct = 5.0`. **One says 30%, the other 15%, neither runs.**
- **The gap.** For a day of ~10–20 value-band bets at `f* ≈ 1.19%` each, the quarter-Kelly *total* is
  ≈**3–6%** of bankroll. The repo's own live-adjacent cap is **30%** — five to ten times that — and even
  the conservative dead one (15%) is 2.5–5×. The failure mode is **a bad Saturday**, which an
  average-ROI statistic will never surface. **Resolve the 30%-vs-15% contradiction first — that is a
  documentation/decision task, not code.**

### R4-F11 — Parimutuel own-bet impact: not a gap at STRIDE's scale, and the agent says so explicitly · **[ROI]**, latent · *aspirational — needs pool-size data the repo does not collect*

- **Finding.** In a tote pool the dividend is determined by pool shares, so the bettor's own wager
  degrades the price it receives, and Kelly on *displayed* odds systematically over-stakes. Benter
  quantifies from operational experience: **a $50,000 bet into a $10 million pool on a 50-to-1 runner
  reduces the payoff by 23.3%**, and more generally, allowing for takeout and own-bet impact, **odds
  drop by 3–4% or more depending on bet size.** Benter's operation used "a conservative **fractional**
  Kelly strategy throughout, with wagers placed on all positive-expectation bets in both normal and
  exotic pools."
- **Evidence.** Benter 1994, in Hausch, Lo & Ziemba (eds), *Efficiency of Racetrack Betting Markets*;
  Isaacs 1953; Hausch, Ziemba & Rubinstein 1981 and Ziemba & Hausch 1984/1987 (the Dr Z place/show
  system, "considerable success in North American place and show pools", explicitly a *bet-sizing on
  tote-board data* system). **All 403 — [snippet-only].** **[prior pass — extended]**: docs/12 §2 took
  the *two-stage conditional logit* from Benter; **the *staking* half of the same paper — fractional
  Kelly + own-bet price impact — has not been used anywhere in the repo.**
- **What STRIDE does today.** Prices come from The Racing API racecards and are treated as fixed quotes
  throughout. Nothing distinguishes a fixed-odds quote from a tote projection, and nothing models
  own-bet impact — **correctly, at current scale.**
- **The gap.** **Not a gap at STRIDE's scale, and the agent wants that explicit rather than manufacture
  a finding.** At a bankroll where 2% is a few hundred dollars, own-bet impact into an AU metropolitan
  win pool is negligible, and AU MBL (F12) guarantees the fixed-odds price anyway. It becomes binding
  only above roughly **$100k bankroll** or if the system moves to tote/Best-Tote settlement.

### R4-F12 — AU Minimum Bet Limits make fixed-odds liquidity a non-constraint below ~$100k bankroll — a genuine advantage worth encoding · **[ROI]** · *implementable now as a static cap table*

- **Finding.** Australia regulates *minimum* bet limits: licensed operators **must accept a bet to lose
  at least** a set amount. For thoroughbreds the widely reported figures are **$2,000 to lose on a win
  bet and $800 on a place bet at metropolitan meetings, $1,000 / $400 at country and provincial**, in
  **NSW, Victoria, Queensland and South Australia**. The rules bind **after 9:00am on raceday** (2:00pm
  for night meetings), apply according to **the jurisdiction where the race is staged, not where the
  bet is placed**, and **exclude multis, betting exchanges and retail transactions**. **Western
  Australia and the Northern Territory have no MBL regime.** Promotional price types are capped much
  lower — **Top Fluc is typically limited to about $250** and offered only up to ~30 minutes before the
  jump.
- **Evidence.** Racing NSW MBL FAQ (`racingnsw.com.au/wp-content/uploads/2017/09/Frequently-Asked-Questions.pdf`)
  and *Schedule 1 — Minimum Betting Limits, Bookmakers*
  (`racingnsw.com.au/wp-content/uploads/MINIMUM-BETTING-LIMITS-–BOOKMAKERS.pdf`); Racing
  Queensland; WA CITS; industry summary; Champion Bets for Top Fluc mechanics. **Hosts not fetched —
  [snippet-only]; the primary Racing NSW PDFs are the authoritative source and should be read before
  any figure here is relied on.** **[audit 2026-07-25 — UPGRADED.** Both primary Racing NSW
  documents exist at the URLs above. The core figures are **CONFIRMED** by independent search:
  NSW **$2,000 to lose on a win bet / $800 place at metropolitan**, **$1,000 / $400 country and
  provincial**, and the timing condition **"fixed odds bets placed after 9am on the day of the race
  for day meetings"**. R3-F15's `[recall — unverified]` flag on the timing conditions is lifted for
  the 9am rule. **Still [unverified]:** the 2:00pm night-meeting variant, the "jurisdiction where
  the race is staged" rule, the WA/NT carve-out, the Racing NSW 1 Sep 2014 / Racing Victoria Oct
  2016 commencement dates, and the ~$250 Top Fluc cap. Read the PDFs before quoting those.**
- **What STRIDE does today.** Nothing. **No jurisdiction field driving any limit, no product-type field
  (fixed / Best Tote / Top Fluc / SP), and no stake cap of any kind on the live path.**
- **The gap.** Two implications in opposite directions. **(a) Positive:** with a 2% maximum stake, MBL
  guarantees the quoted price for any bankroll up to ~$100,000 on metro fixed-odds — so at STRIDE's
  plausible scale **stake sizing is genuinely unconstrained, and there is no execution reason to keep
  stakes flat. This removes the main practical objection to wiring Kelly.** **(b) Negative:** the caps
  do **not** apply in WA/NT, to Top Fluc (~$250), or to tote. A Kelly stake computed without a
  product/jurisdiction cap will occasionally be unfillable, and **a partially-filled Kelly bet is an
  under-bet whose *realised* fraction must be recorded, not assumed.**

### R4-F13 — Betfair AU commission converts the validated +12.3% band into roughly +2% to +4%, and cuts full Kelly by ~3× · **[ROI]** · *implementable now — R4's cheapest high-impact item*

- **Finding.** Betfair's Australian racing markets charge a **Market Base Rate of 8% or 10%** depending
  on state and code, levied on **net market winnings**. For a one-bet-per-market operator, net expected
  return per unit staked is `p(o−1)(1−c) − (1−p) = (p·o − 1) − c·p·(o−1)`. Applied to STRIDE's own
  validated band (9.9% strike, +12.3% ROI, implied mean odds ≈ $11.34):

| | gross | c = 8% | c = 10% |
|---|---|---|---|
| ROI per unit staked | **+12.3%** | **+4.1%** | **+2.1%** |
| full Kelly `f*` | 1.19% | **0.40%** | **0.20%** |
| quarter Kelly | 0.30% | 0.10% | 0.05% |
| current `2u` (= 2% of bank) as a multiple of **full** Kelly | 1.68× | **5.0×** | **9.8×** |

  **Two-thirds to five-sixths of the band's edge is commission at these price levels**, because
  commission scales with `(o−1)` and this band lives at long prices. **At 10% MBR the current `2u`
  stake is nearly 10× full Kelly — deep past the zero-growth point at 2×, i.e. an almost surely ruinous
  configuration despite a genuinely positive gross edge.**
- **Evidence.** Betfair Australia Hub *Commissions and Charges*; Betfair Automation Hub; Champion Bets.
  **[snippet-only] for the 8%/10% MBR** — **[audit 2026-07-25: CONFIRMED against betfair.com.au/hub.
  "On Australian racing markets the Market Base Rate is either 8% or 10%, depending on the state and
  racing code", levied on **net market winnings**, no commission on a net market loss; the 10% rate
  applies to NSW/ACT racing. The net-EV table below reproduces exactly from
  `p(o−1)(1−c) − (1−p)` with p = 0.099, o = 11.34. This is one of R4's better-supported findings.]** The net-EV algebra, the implied mean odds and the entire table
  are **the agent's own arithmetic**, from README figures and `RS:132`.
- **What STRIDE does today.** **No commission model anywhere.** `mc_api.py:7637` and `RTP:955` are both
  gross; `shadow_pl_tracker` settles WIN = SP − 1, LOSS = −1 — gross, and at SP.
- **The gap.** The entire ROI evidence base is gross-of-execution-cost and **the execution venue is
  unstated. Which venue the bets are struck at changes the correct stake by 5–10×. Nothing in the repo
  records it.** Fix: a single `commission_rate` parameter (default 0.0 = byte-identical) feeding EV and
  any Kelly computation, plus a venue field on settled bets.

### R4-F14 — Staking cannot move hit rate at all — and Kelly maximises log-growth, *not* ROI%, which puts it in direct tension with STRIDE's promotion bar · **[ROI]** · *implementable now — a definition for the Phase-4 ticket template*

- **Finding.** Stated precisely, because the repo's framing depends on it. **(1) Hit rate** = winning
  bets ÷ bets placed; a sizing rule assigns a positive real to each *already-selected* bet and cannot
  change numerator or denominator — **staking is exactly orthogonal to hit rate**, as SYSTEM_MAP §3
  says. **(2) ROI is *not* orthogonal**: turnover-weighted ROI is `Σsᵢrᵢ/Σsᵢ`, a stake-weighted mean, so
  any non-flat rule re-weights it. **(3) And the direction is not the intuitive one.** Kelly weights by
  `EV/(o−1)`, concentrating turnover on **short prices**, where percentage edge is smallest. Worked
  example: bet A at `o=2`, EV +5% ⇒ `f* = 5%`; bet B at `o=11`, EV +15% ⇒ `f* = 1.5%`. Flat ROI =
  **10%**; Kelly-weighted ROI = `(5×0.05 + 1.5×0.15)/6.5` = **7.3%** — **Kelly *reduced* headline ROI by
  27% while increasing expected log-growth**, which is what it optimises. (Equal EVs ⇒ ROI unchanged;
  divergence grows with EV dispersion across prices.) So: **ranking quality** sets hit rate; **gating**
  trades hit rate against ROI; **staking** leaves hit rate untouched, moves ROI in *either* direction,
  and moves growth and drawdown enormously.
- **Evidence.** (1)–(2) definitional; (3) the agent's own arithmetic. Supporting empirical claim:
  Uhrín, Šír, Šourek & Železný 2021, *Optimal sports betting strategies in practice: an experimental
  review*, arXiv:2107.08827 — a unified protocol across **horse racing, basketball and soccer**
  concluding that **"betting strategies significantly affect profitability, often outweighing
  predictive model quality — a poorer predictive model with a superior strategy yields higher profits
  than a better model with an inferior strategy"**, with **an adaptive variant of fractional Kelly the
  best choice across a wide range of settings**. **403 — [snippet-only].** **[prior pass — extended]**:
  `research/report.md §2.5` cites this for the one-line claim; this adds the horse-racing test domain,
  the model-vs-strategy ordering, and the adaptive-fractional-Kelly conclusion.
- **What STRIDE does today.** `docs/12:435-438` sets the promotion bar as: raise top-pick hit rate
  **without degrading the calibration Brier or the Value-Edge band's ROI** (constraint 5b-18).
- **The gap.** **That bar cannot evaluate a staking change.** A staking change moves neither hit rate
  nor Brier and will often *lower* ROI% while being strictly correct. **Judged by the existing bar, the
  right answer fails. Any Phase-4 staking ticket needs its own promotion criterion — expected
  log-growth and drawdown on a replayed bankroll path — and must say so explicitly.** A process
  finding, and **the one most likely to be missed.**

### R4-F15 — Kelly is proportional to *current* wealth, and STRIDE has no bankroll state anywhere on the live path · **[ROI]** — **the prerequisite for F3, F4, F5, F7, F8, F10 and F13** · *needs work, but modest and low-risk*

- **Finding.** Every result above sizes a bet as a **fraction of current bankroll**; the compounding
  that makes Kelly optimal *is* the re-evaluation of that fraction against updated wealth. **A system
  that emits stake *labels* has not implemented Kelly regardless of what formula computes them.**
- **What STRIDE does today.** `run_tips_pipeline.py` contains **zero occurrences of the string
  "bankroll"** (grepped this session). `compute_staking` returns `"2u"`/`"1u"`/`"0u"`. **The monetary
  meaning of a unit is undefined on the live path** — the only definition is
  `STAKING_CONFIG['unit_percent'] = 0.01` (`RS:132`), consumed only by `RacingSystem.__init__`
  (`RS:2289`) and the standalone CLI (`RS:3163`), neither reachable from the daily pipeline (F16). The
  crowd gate's `stake_recommendation` (FULL/STANDARD/REDUCED/NONE) is a **label only** and is never
  reconciled with `2u/1u/0u` — **two independent, unreconciled staking vocabularies, neither numeric.**
- **The gap.** Kelly cannot be wired without bankroll state, and that is the real cost of every finding
  above. **The natural home already exists and is live:** `shadow_pl_tracker.py` records PENDING rows
  and settles them into `stride_tip_results`. Adding a running-bankroll column and a stake column there
  is additive (constraint 4) and gives a **replayable path against which any staking rule can be
  scored — including retrospectively, over history already collected.** It writes no new decisions and
  changes no behaviour, so it can ship default-on without violating constraint 2.

### R4-F16 — All three "Kelly" surfaces in the repo are decoys, including one that is provably always zero · **[ROI]**, low magnitude alone, high value as a correctness precondition · *implementable now*

- **Finding.** The repo presents Kelly in three places on the output/DB contract. **None carries a
  Kelly number.** **(1)** `selections.kelly_stake` — column name at `RTP:1334`; the bound value (`v18`,
  `RTP:1462`) is `int(pick.get("staking","0u").replace("u",""))`, i.e. **2/1/0**. **(2) `kellyStake` on
  the mc_api runner contract** — `mc_api.py:7483` emits `round(mc.get('kelly_stake', 0) * 100, 2)`.
  **This is structurally always `0.0`.** Verified by exhaustive grep: the *only* writer of a
  `kelly_stake` key anywhere is `RS:2502` (`staking['kelly_stake'] = kelly_bet * self.bankroll`), which
  writes into the `staking` sub-dict built by `RacingSystem._create_tip` (`RS:2476`) — **a different
  object on a different code path**. The `mc` dict at `:7483` is a Monte-Carlo result dict and never
  acquires the key, so the `.get(..., 0)` default always fires. (The sibling at `mc_api.py:7775` reads
  `tip.get('kelly_stake')` from `RacingSystem`-shaped tips and *would* be populated on that legacy
  path.) **This field is published to an unseen frontend consumer as a constant zero.** **(3)
  `kelly_stake()` itself** (`RS:309-320`) is a correct fractional-Kelly implementation with a cap, and
  it **is** called — at `RS:1743`, `RS:2006`, `RS:2500` — but the chain is `_create_tip` ←
  `_generate_mc_tips_for_track` ← `generate_tips_for_date` ← the `__main__` CLI at `RS:3285`. **The
  live daily pipeline enters via `mc_api.run_simulation` and never reaches any of them.**
  **Additionally the safety cap is decorative:** `RS:320` returns `min(max_stake, kelly)` where `kelly`
  has **already** been multiplied by `fraction` at `:319`. With `fraction = 0.25` and
  `MAX_KELLY_STAKE = 0.05`, **the cap binds only when *full* Kelly exceeds 20% of bankroll** — for
  STRIDE's actual bet population (`f* ≈ 1.2%`) it can **never** bind. The comment at `RS:311-312`
  describes protection that does not engage.
- **Evidence.** All by direct source reading and exhaustive grep this session. No external source
  needed.
- **The gap.** Three surfaces advertise a capability that does not exist, one on a public output
  contract. **[prior pass — extended]:** SYSTEM_MAP §6.9 records Kelly as "implemented, never called
  live" and §7b.11 catches the decoy DB column; this sharpens both — Kelly *is* called, on the
  standalone CLI path — and adds the previously-unrecorded finding that **`kellyStake` on the live
  mc_api contract is a provable constant zero**, plus the inoperative cap. **Concrete consequence for
  Phase 4: there is already an "existing staking module" for constraint 3 to extend —
  `RS:309-326` — and extending it is mandatory rather than writing a fourth implementation.
  `portfolio_risk.py` is the fourth-and-a-half and should not be the base (F7).**

### R4-F17 — `low → 0u` makes the staking function a covert *selection* rule, and flat-MC zeroes whole cards unmeasured · **[BOTH]** — the one staking-adjacent change that genuinely moves hit rate · *instrumentation now; separation of concerns needs work*

- **Finding.** F14 establishes that staking cannot touch hit rate — **but only while the staking rule
  cannot return zero. STRIDE's can.** `compute_staking` returns `"0u"` for `confidence == "low"`, so
  the sizing function is silently also a *gate*. Three consequences: **(1)** a `0u` "bet" is not a bet
  — if it is still counted in the bet population, hit rate and ROI are computed over a set including
  non-bets; if it is not, the staking function has changed the denominator, exactly the boundary
  violation F14 warns about. **(2)** The trigger is far broader than "low confidence":
  `mc_spread < 6.0` (`RTP:705`) forces **all three picks to `low`** (`RTP:~2466-2469`), so **an
  uninformative simulation zero-stakes the entire race**, while the LLM's `[5.0, 3.0, 1.0]` boost
  (`RTP:~2445`) simultaneously takes over the ordering with `_llm_top_pick` bypassing every safety
  filter (`RTP:883`). **A single dispersion statistic flips both the ranking authority and the entire
  stake schedule at once.** **(3) Nobody knows how often this fires** (§9 Q10) — no counter, no log
  aggregation, no measurement. Worse, the confidence ladder driving it is documented as having been
  **anti-correlated with value**: the justification comment at `RTP:950-968` records the v1 ladder
  producing mean EV **+0.036 for "high" vs +0.152 for "low"** (n = 330, 2026-04-14). **The system's
  stake size is keyed to a label whose own history shows it pointing the wrong way, and which was
  demoted-not-removed.**
- **Evidence.** All source-read this session: `RTP:1007-1015`, `:703-705`, `:~2466-2469`, `:~2445`,
  `:883`, `:950-968`. Corroborated by SYSTEM_MAP §2 steps 11/13 and §9 Q10.
- **The gap.** The zero-stake path is the only place where staking legitimately affects selection, it
  is triggered by a threshold nobody has measured, and its authorising signal has a documented history
  of anti-correlation with EV. **Separating "should we bet" (`evaluate_bet_candidate`) from "how much"
  (`compute_staking`) is a structural clean-up that must happen before any Kelly wiring, or the two
  will interact invisibly.** Instrumentation is a stderr counter in the house `[MC_FLAT]` style;
  the separation touches the BET/NO_BET contract and must respect constraints 5b-10 and 5b-11.

---

# R5 — Feature Engineering & Data Quality

17 findings. **Exactly one literature source fetched** (the GitHub-hosted Benter replication
notebook); 28 searches. **F2, F3, F4 and F5 are pure source-read results that need no external
verification at all — the highest-conviction items here.**

**Standing caveat on all R5 claims about the *live* model.** `ml_model.py:211`, `:644`, `:665` make
the **pickle's own saved `feature_columns` take precedence** at load time, and
`models/racing_ensemble_v2.pkl` is git-ignored and absent. So every statement of the form "feature X
is zero at inference" is a statement about **the code path as written**, which is what a retrain and
every future run will do. If the live pickle was trained by an older script with a different column
list, the *specific* live consequences may differ — **but the defect in the current train/serve pair
is real and will bind at the next retrain.**

### R5-F1 — The market is the dominant feature family and its information is *positional* — but positional encodings of the *same* price add nothing · **[ROI]** · *needs work; blocked by something more fundamental (F2)*

- **Finding.** The public's price is the single strongest predictor available, and every successful
  published system treats beating it — not reproducing it — as the objective. Bolton & Chapman
  established the within-race form; Benter reported that a *second-stage* conditional logit over
  `ln(model prob)` and `ln(public prob)` outperformed **both** inputs. The AU positional ladder is
  stable and steep: favourite ~34.9%, 2nd ~19.8%, 3rd ~13.5%. Crucially, market-efficiency theory
  predicts — and **STRIDE's own Phase-5 ablation demonstrated** — that a *transform* of a price the
  model already sees carries no new information: the `fair_implied_prob`/`odds_rank`/`odds_rank_pct`
  trio ranked #3/#6/#11 of 113 by importance yet produced a **causal ablation of −0.0012 AUC**.
- **Evidence.** Bolton & Chapman 1986, **Management Science** 32(8):1040-1060 **[snippet-only]** for
  abstract-level content, **[recall — unverified]** for the citation count; Benter 1994
  **[snippet-only]**. The R² triple (0.1396 / 0.1245 / 0.1218) is carried from `docs/12:41-44`
  **[prior pass — extended]** and **could not be re-verified** (gwern and semanticscholar both
  blocked). AU ladder **[prior pass]**. Phase-5 ablation: STRIDE's own run 2026-07-13,
  `docs/12:336-362`, verbatim.
- **What STRIDE does today.** The trio is in the contract (`retrain_v2.py:270-275`, implemented in
  `relative_market.py`); at inference recomputed by `compute_field_relative_market` at
  `RTP:2251-2257`. The Benter second stage exists as `conditional_logit.py` behind `STRIDE_CL_BLEND`,
  **on hold** (constraint 14).
- **The gap.** Not a gap in *presence* — a gap in *interpretation*. **Phase 5 is the repo's own proof
  that re-encoding an existing price is a dead end.** The literature's claim is narrower and sharper:
  the market must be a **separate second stage combined in log space**, not another column beside the
  fundamentals. STRIDE approximates that with a *linear, hand-set* `mw` ladder whose six numbers
  SYSTEM_MAP §9.14 records as **never fitted**. That is the live gap, and it is a **ROI** lever, not a
  hit-rate one.

### R5-F2 — ★ Training fits `market_odds` = STARTING PRICE; inference serves the RACECARD price · **[BOTH]** · *diagnosis implementable now; fix is structural — **do not backfill***

- **Finding.** **The single most consequential data-quality defect found.** The model's most important
  feature is a **different random variable at train time and at serve time**, and the train-time
  version is drawn from *after* the decision point. This is textbook Kaufman-et-al. feature leakage
  ("the introduction of information about the data-mining target that should not be legitimately
  available to mine from"), and it is **the exact practice `research/report.md` already forbids**:
  *"**never backfill 'late odds'** from a vendor's final-odds field into historical training rows"*
  (`research/report.md:176-180`, constraint 24). **The practice is not a proposal — it is already
  shipped in the training path.**
- **Evidence — repo (source read, the strongest warrant in R5).** `retrain_v2.py:142-144`, verbatim:
  `# Odds — sp_odds is the primary odds column in the view; / # market_odds is sparsely populated so we
  fill from sp_odds / "sp_odds": "market_odds",`. `:557-563` builds
  `_effective_odds = COALESCE(view.market_odds, sp_odds)`; `:574` assigns it to `out["market_odds"]`.
  `sp_odds` provenance is the **results** feed (`auto_results_collector.py:289`;
  `nsw_xml_collector.py:489`; surfaced at `refresh_training_view_v2.py:216` from
  `race_results_history`). **The COALESCE almost always falls through to SP**: `docs/12:352` records
  **106,193 rows `none` / 12,590 `imported_historical` / 794 `live_model` of 119,577** — ~89% of
  training rows have *no* prediction join, so `p.market_odds` is NULL. Inference uses the racecard:
  `RTP:2259` `feat["market_odds"] = extract_odds(runner) or 0`; mirrored at `mc_api.py:1158`.
  `research/report.md:129-134` establishes those are **overnight / ~8am** prices. The Phase-5 trio
  inherits the defect (computed *from* `_effective_odds`), so **`odds_rank` is SP-rank in training and
  8am-rank at serve**. And `backtest_v2_metro.py:69,127-131` does the identical COALESCE — **so the
  README's 33.7%/−4.2% and 9.9%/+12.3% were themselves produced on SP-derived features.**
- **Evidence — literature.** Kaufman, Rosset, Perlich & Stitelman 2012, **ACM TKDD** 6(4):15
  **[snippet-only]**, ~700+ cites **[recall — unverified]**. Kapoor & Narayanan 2023, **Patterns**
  4(9) **[snippet-only]**: leakage "affects at least **294 papers across 17 disciplines**"; eight
  leakage types; in their case study, once leakage was corrected "complex ML models showed **no
  substantive performance advantage** over decades-old logistic regression". The JRA/AU late-odds
  cluster **[prior pass, verified 3-0]** is the *positive* form of the same fact: SP contains
  materially more information than an 8am price — which is precisely why training on it and serving the
  8am price is a mismatch, not a rounding error.
- **What STRIDE does today.** As above. **Note the bitter irony** at `RTP:676-678`: the justification
  for the rising `mw` at short prices is a Kelly audit reading *"$1-3 horses win 41%, model predicts
  17% after blend."* **A model fitted on SP and served an 8am price will systematically under-predict
  short-priced runners, because the 8am price of an eventual $1.60 favourite is much longer than its
  SP. The `mw` ladder may be a hand-tuned patch over this exact defect.** The agent flags this as a
  hypothesis it cannot prove without data — **the most promising thing Phase 3 could test.**
- **The gap.** It sits upstream of nearly everything else. It (a) invalidates the provenance of every
  AUC/ablation number in `docs/12` as a guide to *production* behaviour; (b) **explains why the CL fit
  returned β = 0.000** — "the SP market adds nothing conditional on a model probability that already
  embeds the SP", exactly as `docs/12:318` half-suspects; (c) plausibly explains the short-price
  miscalibration the `mw` ladder compensates for; and (d) **makes the LambdaRank H2H verdict unsafe —
  both arms were trained on SP.** Fix options: restrict training to rows with a genuine pre-race price
  (much smaller matrix), add `odds_source` as an explicit indicator so the trees can separate the
  regimes, or collect pre-race snapshots prospectively. **Do not "fix" it by backfilling — that is
  constraint 24.**

### R5-F3 — ★ At inference, runners without a real quote receive model-derived synthetic "market" odds · **[BOTH]**, modest in size · *implementable now — surface `has_real_market_odds` as a feature*

- **Finding.** `market_odds`, `fair_implied_prob` and `odds_rank` are supposed to be *exogenous*. On
  the mc_api path they are not: when a real quote is missing the code falls back, in order, to
  `model_odds`, then `1 / model_prob_dec`, then **the median of the field's odds**.
- **Evidence — repo (source read).** `racing_system_v8.3_mc.py:1763-1778`, `infer_market_odds`, quoted
  in full in the source file; bound at `mc_api.py:7282` and `:7156-7167`. The pipeline separately
  tracks honesty via `hasRealMarketOdds`/`has_real_market_odds` (`RTP:1686`, `:1800`, `:1859`,
  `:1908`…) — **but that flag is not a feature; it never reaches the ML feature vector.** In training
  there is no such fallback: rows with NULL `sp_odds` are simply excluded
  (`backtest_v2_metro.py:115`). **Literature:** this is Kaufman et al.'s leakage in self-referential
  form; also the "Class I / estimation leakage" boundary in Roth's 2026 taxonomy (F14). **No
  horse-racing-specific citation exists for it — it is a bug class, not a research finding, and is
  labelled honestly as such.**
- **What STRIDE does today.** `relative_market.compute_field_relative_market` is **well-designed
  against this** — its docstring (`relative_market.py:23-27`) explicitly returns `0` for unquoted
  runners *because* "0 is out of range for every feature … so tree models can isolate the 'no market'
  case on its own branch." **That discipline is correct and is not shared by the mc_api adjustment
  path.**
- **The gap.** Circularity in the mc_api feature layer: a model-derived price feeds a feature that
  feeds a model adjustment. It only bites on unquoted runners, and SYSTEM_MAP §7b.5 already records
  that unquoted runners are anomalous downstream too (`RTP:699-701` — they never receive the ML blend
  into `winPercentage`). Fix: surface `has_real_market_odds` as a contract feature — additive,
  retrain-gated, the Phase-5 precedent exactly, zero behaviour change until the next retrain.

### R5-F4 — ★ 41 of the 113 contract features are identically zero in training (and on the `mlPredictedProb` inference path), including the entire 16-feature market-velocity block · **[HIT-RATE]** — R5 calls this the cheapest lever in its report · *audit + doc correction implementable now; wiring needs work*

> **⚠️ [audit 2026-07-25] — THE HEADLINE AS ORIGINALLY WRITTEN ("identically zero in BOTH training
> and inference") IS WRONG ON THE INFERENCE HALF, and the title above has been corrected.** There
> are **two** ML call sites, not one. R5 traced only the first.
>
> 1. `run_tips_pipeline` builds `feat` at `RTP:2258-2308` → `ml_model.prepare_features` →
>    `predict_proba` → `mlPredictedProb` (`RTP:2323`). **On this path R5's claim holds** — the 41
>    names are never written into `feat`, so `ml_model.py:217-218` sends them in as `0`.
> 2. `mc_api` builds a *different* feature dict: `extract_all_sophisticated_features`
>    (`mc_api.py:5436`) → `extract_ml_features` (`:908`) + `extract_speed_and_pace_features`
>    (`:1556`) + four more extractors, and feeds it to
>    `calculate_ml_probability_adjustment` (`mc_api.py:6448-6465`) →
>    `RacingMLModel.predict_adjustment` (`ml_model.py:598-610`) → `prepare_features` →
>    `predict_proba`. **That path DOES populate members of the "dead 41"** — verified by source
>    read: `running_style_score` at `mc_api.py:1605`, `is_steam_move` at `:2051/:2055`,
>    `empirical_barrier_advantage` at `:1153-1155`, `settling_percentile` via
>    `speed_mapping.py:1046/1059`, `smart_money_score` via `market_velocity.py:284/335`. The output
>    of that call is `ml_adjustment`, the **0.55-weighted** term of `combined_adjustment`
>    (`mc_api.py:7379`).
>
> **What survives, and it is the half that matters.** The **training-side** claim is
> independently re-verified here: each of the seven names spot-checked
> (`running_style_score`, `is_steam_move`, `smart_money_score`, `settling_percentile`,
> `empirical_barrier_advantage`, `is_elite_jockey`, `market_confidence`) has **0 occurrences in
> `form_feature_builder.py`** and **exactly one** in `retrain_v2.py` — the `FEATURE_COLUMNS` listing
> itself — so they are `np.nan` at `:586-588` and zero-filled at `:680-682`. A column that is
> constant in training receives no split, so its serve-time value is inert regardless of path.
>
> **Two consequences for Phase 4.** (a) The correct statement is *"41 features are constant zero in
> training, therefore inert everywhere"* — **not** *"nobody computes them"*. (b) A ticket that says
> "wire the 16 market-velocity features into inference" is **already half-done and pointed at the
> wrong end**: the producers exist and one inference path already calls them. The buildable work is
> **training-side, as-of-safe** computation of those columns, exactly as F9 says for pace. Do not
> let a Phase-4 ticket duplicate `mc_api`'s extractors.

- **Finding.** The agent diffed the contract against every population site on both sides by parsing the
  source:

| class | count | features |
|---|---|---|
| **Dead — constant 0 in training, and on the `mlPredictedProb` inference path** (**[audit: several ARE populated on the `mc_api` `predict_adjustment` path — see the correction above]**) | **41** | `running_style_score, pace_advantage, is_high_pace_expected, distance_style_match, is_blinkers_first_time, is_elite_jockey, is_elite_trainer, is_jockey_upgrade, barrier_advantage, track_bias_score, is_steam_move, is_drift, odds_movement_pct, last_start_market_diff, avg_market_diff_3runs, market_trend_shortening, market_trend_drifting, empirical_barrier_advantage, predicted_settling_pos, settling_percentile, pace_pressure_index, settling_difficulty, settling_pace_interaction, is_congested_speed, speed_horse_ratio, days_since_last_normalized, prep_run_x_days_since, run_spacing_quality, empirical_freshness_score, is_quick_backup, is_long_absence, distance_change_staleness, class_x_spell, steam_velocity, drift_velocity, late_move_indicator, market_confidence, relative_move, smart_money_score, is_insider_signal, field_market_agreement` |
| **Train-only — real in training, hard 0 at inference** | **9** | see F5 |
| Populated on both sides | ~63 | — |

- **Evidence — repo (source read + parse; method reproducible).** Training populates from exactly four
  places: `VIEW_TO_FEATURE_MAP` (`retrain_v2.py:127-149`, 12 view columns), `_form_features` ←
  `form_feature_builder.batch_compute_form_features` (`:346-356`, applied `:590-600`),
  `_compute_pace_features` (3 columns), the track-distance profile lookup (4 `td_*`), plus ~8 inline
  formulas and the Phase-5 trio. Everything else is `np.nan` at `:586-588` then **zero-filled** at
  `:680-682`. **None of the 41 names appears as a quoted string anywhere in `form_feature_builder.py`**
  (checked individually for 11 representative names — all 0 hits, versus 4/2/5/3 hits for
  `going_suitability`/`trainer_momentum_score`/`first_up_win_rate`/`consistency_score`, which *are*
  produced). Inference writes 69 keys at `RTP:2258-2308`, **6 of which are not in the contract at all**
  (`dist_sectional_slope`, `dist_sectional_recency_weighted`, `sectional_result_divergence`,
  `first_at_distance_sectional_quality`, `step_up_x_dist_slope`, `pace_clarity_score`) and are silently
  discarded. **Why nobody noticed:** the retrain's coverage print (`:1440-1448`) reports non-zero counts
  for **exactly six structural columns plus the 11 Phase-2 features** — structurally incapable of
  surfacing these 41 — and the opt-in `--coverage-audit` (`:1266-1301`) iterates only
  `_ALL_PHASE4_FEATURES`. **The evidence would never appear in a retrain log.**
- **Literature.** The relevant warning is STRIDE's own Phase-5 precedent (constraint 36): importance ≠
  contribution; only causal ablation counts. Symmetrically, **a constant column has importance ~0 *and*
  contribution 0, and no metric in the repo distinguishes "this feature is zero" from "this feature is
  uninformative".** Zero-variance columns are not directly harmful to a GBM, **so the cost is not
  accuracy — it is that the documented feature inventory is fiction**: README/`docs/04` advertise "110
  engineered features"; the realised count is **~72 in training and ~63 at inference**, and the *entire*
  market-microstructure story in `docs/04 §2` is inert.
- **What STRIDE does today.** `docs/04:60-66` presents those 16 market features as live;
  **`research/report.md:125` scores STRIDE "Partially aligned" on "market steam/drift carries signal"
  on the strength of features that are constant zero.** `market_velocity.py` and `market_analysis.py`
  exist and are imported by `mc_api` — but for mc_api's *own* adjustment layer, not the trained
  contract.
- **The gap.** **Two of the three feature families the literature ranks highest — market microstructure
  and pace/race-shape — are advertised, documented, implemented in standalone modules, and not wired
  into the model.** Wiring even the eight market-velocity features that `mc_api` already computes is
  additive, retrain-gated, and needs no new data source. The missing diagnostic is a read-only script
  printing per-feature non-zero/non-NaN counts over the **full** contract.
- **Methodological caveat recorded by the agent.** The classification was produced by **parsing**
  (regex over assignment sites plus a quoted-string scan), not by execution. Eleven representative
  names were hand-checked and held in every case, but a feature written through an unusual idiom
  (e.g. `out[c]` in a loop over a computed list) could in principle have been missed. **Re-running the
  same diff as a read-only script next to the database — which would also print real non-zero counts —
  is the definitive check.**

### R5-F5 — ★ The NaN-preservation contract is honoured in training and destroyed at inference · **[BOTH]** — R5 calls this the highest value-per-line change in its report · *implementable now, behind a flag*

- **Finding.** STRIDE deliberately preserves NaN for 11 `PHASE2_FEATURES` and 2
  `NAN_PRESERVE_FEATURES` so "tree models exploit missingness" — the MIA strategy the literature
  endorses. At inference the contract is silently broken: `prepare_features` zero-fills **everything**,
  and **a zero z-score is not "missing", it is "exactly average" — the modal value.** Every runner
  without sectionals is routed down the "average closer" branch instead of the "unknown" branch.
- **Evidence — repo (source read).** Training keeps NaN: `retrain_v2.py:680-684` —
  `for col in NON_SECTIONAL_FEATURES: … fillna(0)` followed by
  `# Phase 2 sectional columns + NAN_PRESERVE_FEATURES intentionally keep NaN (tree models)`.
  Inference destroys it: `ml_model.py:214-218` — `pd.to_numeric(...).fillna(0)` if present,
  `else: features[col] = 0`; **`.fillna(0)` is unconditional.** So even `runs_since_peak`, which
  `RTP:2296` carefully sets to `float("nan")` with the comment *"NaN-preserving (tree models handle
  missingness)"*, is converted to 0 two calls later. **Worse, the 8 sectional primitives are not even
  *offered*:** `z_200m`, `z_400m`, `z_600m`, `z_800m`, `lambda_decay`, `svi`, `rsi`,
  `trip_cost_seconds` are **never written into `feat`** at `RTP:2258-2308` (`z_200m` is read at `:2314`
  only to build `sectional_x_going`), so they hit the `else` branch. Together with `ground_suitability`
  that is the 9-feature train-only set from F4. **The one thing done right:** `has_sectional_data` is an
  explicit binary availability indicator, set at `RTP:2293` and clamped in both `retrain_v2.py:686-687`
  and `ml_model.py:221-222`.
- **Evidence — literature.** Twala, Jones & Hand 2008, **Pattern Recognition Letters** 29(7) — origin
  of MIA **[snippet-only]**: "MIA … naturally handles missing values in decision trees by using
  missingness itself as a splitting criterion". Perez-Lebel, Varoquaux, Le Morvan, Josse & Poline 2022,
  **GigaScience** 11 **[snippet-only]**: "Learning trees that model missing values — with missing
  incorporated attribute — leads to robust, fast, and well-performing predictive modeling"; and,
  decisively, "**adding an indicator to express which values have been imputed is important for
  prediction after imputation**". XGBoost's sparsity-aware split finding learns a *default direction*
  per split, so **a NaN and a 0 take different branches**; LightGBM and CatBoost behave analogously
  **[recall — unverified; kdd.org blocked]**.
- **The gap.** Two compounding effects: (1) **the model was trained to make a *missing* decision it can
  never make in production**; (2) at ~47% sectional coverage roughly half of training rows carry a real
  z-score and half a NaN — while **100% of production rows carry `0.0`**. That is a distribution shift
  on **9 features simultaneously**. It is also **a strong alternative explanation for the "sectionals
  add −0.0005 AUC" result**: the ablation measured the *training-side* value of a block production
  never receives, so **the result says nothing about whether wiring them would help.**
- **Fix.** (a) return `np.nan` for the 13 NaN-preserved columns at inference; (b) copy the 8 sectional
  primitives into `feat`. Both additive, both behind a `STRIDE_*` flag with a byte-identical default-off
  path. **Warning under constraints 12/18: this changes the probability scale, so the calibration Brier
  and the Value-Edge ROI must be re-read before default-on.**

### R5-F6 — No fundamental feature in STRIDE is expressed relative to today's field · **[HIT-RATE]** — the largest *modelling* gap after F2/F5 · *implementable now, retrain-gated, ablation-tested*

- **Finding.** The literature's central structural claim about racing is that a runner's chance depends
  on the *composition of the race*, so features must be relative. Lessmann, Sung & Johnson state it
  exactly: "standard forecasting frameworks are not designed for modeling the competitive element,
  whereby a participant's chance of success depends not only on individual capabilities but also on
  those of competitors." Benter's and the Hong Kong tradition's practice is to scale every factor
  against the race — "for basic logistic regression, you need to in some way compare the horse to the
  others in that race — by scaling the variables in some way … many of the old Hong Kong systems used
  normalized finishing position."
- **Evidence.** Lessmann, Sung & Johnson 2010, **IJF** 26(3) **[snippet-only]**; the same extraction
  also reported "the Random Forest model outperformed the Conditional Logit model, yielding a **20.26%
  return versus 8.84% return over 500 races**" — **treat that ROI figure as [snippet-only] and note
  `research/report.md §2.4`'s standing warning that published racing-ROI headlines routinely fail
  scrutiny.** Benter normalisation practice **[snippet-only]**. **The one source fetched** —
  `chris-alex-p/german-horse-racing` — is an instructive *negative* control: a **one-stage** CL
  ("in contrast to Benter's two-step approach … here a one-step approach is utilized"), 12 features plus
  odds, **no within-race normalisation**, 5,625 training observations, ~1,000 test races, 914 bets,
  earnings 54.9 (≈6.0% ROI, p = 0.0218), LR 352.8 on 13 df, concordance 0.738 (se 0.013) — **a working
  but thin edge from the un-normalised one-stage form.**
- **What STRIDE does today.** Within-race-relative features in the contract, exhaustively:
  `fair_implied_prob`, `odds_rank`, `odds_rank_pct` (**market only**); `z_200m…z_800m` (per-race
  z-scores but of the horse's **prior** race, joined as-of); `sectional_rank_at_distance`. Race-*level*
  constants: `field_size`, `field_size_context`, `barrier_relevance_score`, `market_efficiency_flag`,
  `pace_pressure_score`. **Everything that carries the actual handicapping signal** —
  `weighted_form_score`, `distance_strike_rate`, `course_strike_rate`, `class_level`, `weight_kg`,
  `days_since_run`, `barrier_draw`, `consistency_score`, `improvement_score`, `first_up_win_rate` — **is
  an absolute level.** Note the three within-race features that *were* relative are among F4's dead 41
  (`settling_percentile`, `speed_horse_ratio`, `field_market_agreement`).
- **The gap.** `docs/12 §3` already names "model within-race" the "largest gap" but frames it as an
  *architecture* problem (pointwise vs LambdaRank). **It is at least as much a *feature* problem, and
  the feature route is far cheaper and does not require abandoning the pointwise ensemble:** add
  `<feature>_z` and/or `<feature>_rank` for the 8–10 fundamentals that carry signal. `relative_market.py`
  already does exactly this for price and is a ready-made template — **constraint 3 points at extending
  it rather than writing a new module.** **Caveat that must be stated in the ticket:** Phase 5 proved a
  relative re-encoding of an *already-present* column can be worthless; the difference here is that the
  fundamentals are **not** already present in relative form and their absolute levels are genuinely not
  comparable across race classes — **but the burden of proof is a causal ablation, not importance.**

### R5-F7 — Pointwise binary target + a fixed `scale_pos_weight` cannot represent the field-size base rate · **[HIT-RATE]** · *needs work; the full fix is blocked by constraint 15*

- **Finding.** The win base rate is mechanically `1/n`, and empirically the effect is steep: one
  practitioner analysis of **96,000+ big-field sprint runners, 2013–2026**, reports top-rated horses
  winning **37.7% in fields of ≤7 vs 16.6% in fields of 16+**; third favourites go from ~16–18% in
  8–10-runner fields to ~10–12% in 20+ fields. A pointwise binary classifier with a **global**
  positive-class weight fits one average base rate across all field sizes.
- **Evidence.** Field-size numbers: honestbettingreviews.com and raceadvisor.co.uk **[snippet-only],
  practitioner sources, not peer-reviewed — flagged as such.** The structural point is Bolton &
  Chapman's and Lessmann et al.'s (F1, F6). Peer-reviewed support for the ranking objective: KJAS 2024
  **37(2):239** — pairwise LTR generally beats pointwise, CatBoost Ranker best — **verified 3-0 / 2-1
  by the prior pass** (`research/report.md §2.3`) **[prior pass — extended]**.
- **What STRIDE does today.** Target is pointwise binary `is_winner` (**[audit: the anchor is
  `retrain_v2.py:1433`, not `:219` — see R1-F1]**); nothing
  groups by race. `scale_pos_weight=9` (`retrain_v2.py:774`, **[audit: verified]**) is a constant ⇒ an implied base rate of 1/(1+9) = 10%, i.e.
  calibrated for a ~10-runner field and **wrong at both tails**. LGB uses `is_unbalance`, CatBoost
  `auto_class_weights=Balanced` — **three different, mutually inconsistent imbalance treatments in one
  ensemble.** `field_size` *is* a feature, so trees can partially learn the interaction, and `mc_api`
  renormalises win probabilities per field, which absorbs the level error post hoc.
- **The gap.** **The post-hoc renormalisation fixes the *sum* but not the *shape*: if the model
  under-separates in big fields, normalising a flat vector yields a flat vector — which is literally the
  `mc_is_flat` failure mode the pipeline has special-cased throughout.** §9.10 records that nobody knows
  how often `mc_is_flat` fires; **measuring flat-rate by field size is the cheapest possible test of
  this finding.** Two additive options that do not touch the architecture: (a) train with `sample_weight`
  proportional to field size rather than a scalar `scale_pos_weight`; (b) add explicit
  `field_size × <feature>` interactions. The full fix (grouped ranking objective) is blocked by
  constraint 15, and the retest should be **CatBoost Ranker** (see R1-F9).

### R5-F8 — Draw bias is a track × distance × field-size interaction, and STRIDE feeds the model a raw integer · **[HIT-RATE]** · *needs work — an as-of-safe barrier table with empirical-Bayes shrinkage*

- **Finding.** Practitioner consensus: draw bias is (a) overwhelmingly a **sprint** phenomenon — "over
  longer trips, a mile and beyond, horses have time to settle … over five or six furlongs, they do
  not"; (b) worth "several lengths" in affected configurations; (c) **amplified by field size**; and
  (d) track-specific (a Randwick 1000m inside draw vs barrier 10+ is the canonical AU example). **None
  of that is representable by a linear or monotone function of a barrier *number*.**
- **Evidence.** raceadvisor.co.uk, thepuntlab.com (AU-specific), geegeez.co.uk, btxracing.com
  **[snippet-only], practitioner — no peer-reviewed AU draw-bias study was reachable; stated as a
  limitation.** The physical mechanism is quantified **inside STRIDE itself** as
  `trip_cost = (barrier_lane − 1) × 1.8 m × turns × 0.65` (`backfill_lambda_targeted.py:163`).
- **What STRIDE does today.** `barrier_draw` (raw integer, `RTP:2261`); `barrier_advantage` (**dead**);
  `empirical_barrier_advantage` (**dead**); `track_bias_score` (**dead**); `barrier_relevance_score` (a
  pure function of distance: 1.0/0.7/0.4/0.2 at ≤1200/≤1600/≤2000/else); `td_barrier_style_edge`; and
  `barrier_x_pace_inv` — **an interaction built on the dead `barrier_advantage`, therefore identically
  0 in training, and computed from a *different* formula at inference (`RTP:2312`)**, which SYSTEM_MAP
  §7b.10 already flags as a drift hazard. Separately `track_bias_points.py` produces a real per-track
  score that §7b.3 shows is **scale-mismatched at its consumer** (range −25…+45 into a `/100` map
  designed for 0–100 ⇒ near-constant ×0.95). `docs/04 §4` classifies barrier-bias tables as **Tier 2 —
  inference-safe but leaky if reused to backfill training rows** (constraint 25), which is why the
  empirical version is absent from training.
- **The gap.** Of six barrier-related features, **three are dead, one is distance-only, one is an
  interaction with a dead parent, and the live one is a raw integer whose meaning changes completely
  between Flemington 1200m and Randwick 2400m** — while the literature says this is a first-order sprint
  factor. **The Tier-2 constraint is real but surmountable: a barrier-bias table computed strictly from
  races before each training row's date is Tier 1 by construction** — the same expanding-as-of
  discipline the sectional LATERAL join already uses (F11). Aspirational refinement (rail position, true
  track configuration on the day) needs data the repo does not hold.

### R5-F9 — Pace / run-style is documented, implemented in two engines, and almost entirely absent from the trained model · **[HIT-RATE]** · *needs work — a training-side as-of-safe pace computation*

- **Finding.** Race-shape modelling — classify run style, project tempo, adjust each runner for how
  that tempo suits it — is standard practice with claimed predictive value ("EquinEdge Pace metric
  predicts 1st/2nd at first call 72.5%, including first-time starters"; Quirin Speed Points as the
  canonical early-pressure encoding).
- **Evidence.** equinedge.com, brisnet.com, geegeez.co.uk, globalracing.com **[snippet-only],
  practitioner — no peer-reviewed source reachable. The 72.5% figure is a vendor claim and should be
  treated as marketing until independently replicated.**
- **What STRIDE does today.** `docs/04 §2` lists 17 pace/speed-map features. **Of those, 11 are in F4's
  dead 41** (`running_style_score`, `pace_advantage`, `is_high_pace_expected`, `distance_style_match`,
  `predicted_settling_pos`, `settling_percentile`, `pace_pressure_index`, `settling_difficulty`,
  `settling_pace_interaction`, `is_congested_speed`, `speed_horse_ratio`). What survives is
  `_compute_pace_features` (`retrain_v2.py:~500-546`) → `pace_pressure_score`, `leader_advantage`,
  `closer_advantage`, derived from `form_string` and barrier — a thin proxy. Meanwhile two full pace
  engines (`pace_modeling.py`, `speed_mapping.py`) are imported by `mc_api` and feed the **MC side
  only**, and `race_context` produces `pace_clarity`, deliberately kept out of the contract and used
  only to *cap* confidence.
- **The gap.** **The two probability engines see different worlds: MC sees pace, the GBM effectively
  does not.** That asymmetry is invisible in every existing metric because **the ablation harness can
  only ablate what is populated.** Same root as F4, but worth separating because the producers already
  exist and are already called on the inference path — **the missing piece is a training-side as-of-safe
  pace computation, not new code.**

### R5-F10 — Jockey and trainer effects are real, significant, and *small* — and 3 of STRIDE's 4 jockey/trainer features are dead · **[HIT-RATE]**, medium size · *shrinkage implementable now; "residual to market" needs F2 fixed first*

- **Finding.** Variance-decomposition work consistently finds the **race** effect largest and the
  **jockey** effect the smallest of the modelled random effects, while still highly significant. Oki et
  al. (1995): race, jockey and weight carried all had highly significant (p < 0.01) effects on racing
  time across six distances, and "the skill of the jockey is an important source of variation …
  therefore it should be considered in deriving adjustment factors". Oda et al. (2024): across all
  **12 racecourse-distance categories**, "the race effect had the highest variance component, generally
  followed by the residual, permanent environmental effect, breeding value and **jockey effect**" —
  the rider is the *smallest* component. Corollary: **a raw jockey/trainer strike rate is dominated by
  mount quality** (good jockeys ride good horses), and small-sample rates need shrinkage — the
  empirical-Bayes beta-binomial result being that "low-sample observations exhibit significant
  shrinkage toward the overall mean".
- **Evidence.** Oki, Sasaki & Willham 1995, **J. Animal Breeding and Genetics** 112(1-6)
  **[snippet-only]**; Oda et al. 2024, **JABG** **[snippet-only]**; Robinson, *Understanding empirical
  Bayes estimation* **[snippet-only, practitioner but methodologically standard; the underlying
  beta-binomial result is textbook]**.
- **What STRIDE does today.** `is_elite_jockey`, `is_elite_trainer`, `is_jockey_upgrade` — **all three
  dead** (F4) — plus `trainer_momentum_score`, produced by `form_feature_builder` and set at inference
  with a **default of 50** (`RTP:2297`); `jockey_trainer_strike_rate` and `is_winning_combo` (both
  live); `jockey_booking_change`. `jockey_momentum_adjustment`'s context multiplier is **inert**
  (§7b.2 — never present in the result dict ⇒ `jockey_mult ≡ 1.00`), and
  `MC_ENABLE_JOCKEY_EFFICIENCY` is **force-disabled** by `RTP:92-100`. **No shrinkage is applied to any
  strike rate anywhere in the contract.**
- **The gap.** The honest reading is that this family is *worth having but easy to overfit*: a raw
  strike rate on small denominators is mostly noise plus mount-quality confounding, and the fix is
  (a) shrinkage toward the population rate and (b) measuring the effect **residual to the market
  price**, because the market already prices the booking. ⚠️ **Danger flagged:** `ml_model.py:250-252`
  instantiates a `TargetEncoder` over `['jockey','trainer','track','going','race_class']` with
  `smoothing=10.0, min_samples=5`; **if that is ever fitted over the full dataset rather than fold-wise
  it is Kaufman-et-al. target leakage. The agent could not establish which** — see the open question
  below.

### R5-F11 — Sectional-adjusted ratings out-predict raw ratings; STRIDE's join is genuinely leak-free but uses exactly one prior observation at ~47% coverage · **[HIT-RATE]**, ROI following · *(1) and (2) implementable now; (3) is a purchase decision*

- **Finding.** Sectional-adjusted ratings beat raw finishing-time ratings: "adjusted ratings based on
  sectional times correlated better with race results than original Racing Post ratings … notable
  because Racing Post ratings account for ease of victory and should have an advantage over automatic
  adjustments." Mechanism: raw time confounds the horse with the tempo it was run at.
- **Evidence.** rulesofsport.com, learnbetwin.com, horise.com, drawbias.com **[snippet-only],
  practitioner** — the drawbias.com analysis is the source `docs/12:70` already cites and could not be
  fetched. Coverage of the commercial alternative: **Punting Form sells 200 m-increment sectionals for
  all runners covering ~85% of AU TAB meetings, history to October 2012** (`research/report.md §2.6`,
  **verified 2-0 by the prior pass**) **[prior pass — extended]**.
- **What STRIDE does today — and it is a genuine strength.** The as-of join was verified by source read:
  `refresh_training_view_v2.py:252-270` is a `LEFT JOIN LATERAL (SELECT … FROM sectional_times st WHERE
  stride_norm_name(st.horse_name) = r.horse_name_norm **AND st.race_date < r.race_date** AND
  (st.lambda_decay IS NOT NULL OR st.z_200m IS NOT NULL) ORDER BY st.race_date DESC **LIMIT 1**) ls ON
  TRUE`. **The strict `<` is correct** — the docs/04 Tier-1 pattern working as advertised, and
  `research/report.md:121` is right to score STRIDE "ahead of most published work" on temporal hygiene.
- **The gap.** Three, in descending severity: **(1) production never receives them** — F5; the whole
  block is 0 at inference, and this dominates everything else here. **(2) `LIMIT 1`** — the feature is a
  *single* prior race's z-score, not a recency-weighted average over the last N; sectional z-scores are
  noisy (they depend on that day's tempo and going), so one observation is a high-variance estimator.
  **The repo already knows this pattern** — `avg_market_diff_3runs`, `speed_rating_trajectory` — it just
  was not applied here. **(3) ~47% coverage**, with QLD structurally blocked by Cloudflare (constraint
  20 — an access decision, not a code fix) and VIC/SA pending `RACING_COM_API_KEY`.

### R5-F12 — Weight, class and prize money: the two most-cited non-market fundamentals are weight and money, and STRIDE has weight raw and money not at all · **[HIT-RATE]** · *weight-relative/WFA implementable now; prize money depends on an unverified column*

- **Finding.** In the applied ML literature the recurring high-importance non-market features are
  `win_odds` and `declared_weight`, followed by **`preprize` (prize money won up to the last race)**,
  `horse_age` and `preorder`. Weight's physical effect is small but real and *systematically* applied by
  handicappers: roughly **0.02–0.03 s per pound per furlong at sprint distances** (≈0.12–0.18 s, about
  one length, for 5 lb over six furlongs), less per furlong but compounding over routes (1–1.5 lengths
  for 5 lb over nine furlongs). Australia's class structure is explicitly a **benchmark rating ladder**
  (BM64, BM78 …) with prize money mapped to it; average AU race prize money is **$53,797** and the
  country passed **$1 billion** in 2023/24.
- **Evidence.** Feature importances from LightGBM/SHAP write-ups **[snippet-only], practitioner blog
  posts, not peer-reviewed — the *ranking* is consistent across several independent write-ups, which is
  why it is reported, but no effect size is trustworthy.** Weight-per-pound figures **[snippet-only],
  practitioner**. One peer-reviewed anchor: body-weight change of ±20 kg vs ±5 kg increased racing time
  by **0.3 s** **[snippet-only]**; Oki et al. 1995 confirm weight carried is highly significant
  (p < 0.01). AU class/prize structure **[snippet-only], industry press**.
- **What STRIDE does today.** `weight_kg` (raw, parsed from a string), `weight_change`, `class_level`
  (a single numeric), `class_movement`/`is_class_drop`/`is_class_rise`/`is_first_time_stakes` (live),
  `class_x_spell` (**dead**), `market_efficiency_flag` (a step function of `class_level`). **There is no
  prize-money feature, no career-earnings feature, no benchmark rating, no weight-for-age adjustment,
  and no `career_starts` anywhere in the 113 columns** — `career_starts` is used *inside*
  `trial_x_experience` but never exposed.
- **The gap.** Three concrete misses: (a) weight is absolute, not WFA-adjusted and not field-relative —
  a 58 kg topweight in a BM64 and in a Group 1 mean opposite things (F6 again, applied); (b) prize
  money / career earnings is a *continuous, high-resolution* proxy for class that `class_level`'s single
  ordinal cannot carry, and it is third-ranked in the SHAP write-ups; (c) `career_starts` distinguishes
  a debutant from a 40-start veteran and is absent (F13). **Feasibility caveat: whether
  `race_results_history` carries prize money could not be verified** — the table has no `CREATE TABLE`
  in the repo (§9.17).

### R5-F13 — First starters, imports and thin-form horses get the same zero vector as a fully-formed veteran · **[HIT-RATE]**, modest but very cheap · *implementable now*

- **Finding.** Missing-data research is unanimous that **an explicit missingness indicator is
  required** alongside whatever imputation is used. In racing this matters more than in most domains
  because missingness is *not* random: first-starters, imports and horses returning from long spells
  have systematically absent form, **and the market prices that absence explicitly** (debutants of
  well-regarded stables shorten; unknown imports drift).
- **Evidence.** Perez-Lebel et al. 2022 GigaScience and Twala et al. 2008, as F5 **[snippet-only]**.
  First-up/spell structure of AU racing (an "x" in the form string = a spell of **84 days or more**;
  "1st Up"/"2nd Up" are first-class form categories) **[snippet-only], industry reference**. **No
  academic first-starter study was reachable.**
- **What STRIDE does today.** Live: `is_first_up`, `is_second_up`, `days_since_run`,
  `first_up_win_rate`, `second_up_win_rate`, `campaign_run_number`, `has_sectional_data` (the one good
  availability indicator), plus the five barrier-trial features which are precisely the debutant signal.
  **Dead: the entire freshness/staleness block** — `days_since_last_normalized`, `prep_run_x_days_since`,
  `run_spacing_quality`, `empirical_freshness_score`, `is_quick_backup`, `is_long_absence`,
  `distance_change_staleness`, `class_x_spell` (8 features, F4). **Absent:** `career_starts`, any "no
  prior form" flag, any imported-horse flag.
- **The gap.** A debutant and a veteran with an unlucky data gap are represented identically after
  zero-fill. **`has_sectional_data` proves the team already knows the pattern and applies it correctly —
  it just was not generalised.** The producers (`temporal_staleness.py`, `fitness_peak.py`) exist and are
  documented — **this is a wiring gap, not a research gap.** Fix: add `career_starts` and a
  `has_prior_form` indicator, both derivable with the existing as-of pattern, additive, retrain-gated.

### R5-F14 — Leakage taxonomy: STRIDE's *splits* are ahead of the literature, its *feature provenance* is behind it, and only the split half is measured · **[BOTH]** — a gap of attention allocation · *audit and gap-unification implementable now; the provenance fix is F2*

- **Finding.** The 2026 quantitative leakage landscape splits leakage into four causal classes with
  measured effect sizes across **2,047 tabular datasets / 29 experiments**: **Class I estimation
  leakage** (fitting scalers/encoders on full data) is *negligible* — nine conditions all
  |ΔAUC| ≤ 0.005; **Class II selection leakage** (peeking, seed cherry-picking, early stopping) is
  *substantial*, "consistent with approximately **90% noise-exploitation share** inflating reported
  scores"; **Class III memorisation** scales with model capacity (d_z = 0.37 for Naive Bayes to
  **1.11 for Decision Tree at 10% duplication**, 1.38 at 30%); and **Class IV boundary leakage is
  *invisible under random cross-validation*** — a boundary experiment across 129 temporal datasets showed
  random CV censors structural contamination entirely.
- **Evidence.** Roth 2026, *Which Leakage Types Matter?*, arXiv:2604.04199 — **[snippet-only]; all
  figures are from the search tool's extraction of a blocked PDF and should be re-verified.** Kapoor &
  Narayanan 2023 (F2) **[snippet-only]**. Purging and embargoing: López de Prado, *Advances in Financial
  Machine Learning*, 2018 **[snippet-only] + [recall — unverified]**. *Transfer note made explicitly by
  the agent:* both are non-IID time-ordered decision problems, **but a race outcome is a point event
  with no label horizon, so *purging* is less critical for racing than for finance, while the *embargo*
  still matters** because feature construction (rolling form, strike rates, bias tables) has a lookback
  that straddles the boundary.
- **What STRIDE does today.** Very well on the split axis: `DateWindowSplitter` = 60 d min train /
  **14 d purge gap** / 14 d test / 14 d step; `walk_forward_backtest.py` = min_train 3000, test 500,
  **gap 7 d**; the three-tier leakage catalogue in `docs/04 §4`; the verified as-of LATERAL join (F11);
  `retrain_v2.py:319` "Benchmark column only — **never a training feature**".
  `research/report.md:121` scores this "Ahead of most published work" — **correct.**
- **The gap.** Class IV is already handled. What is not handled is **feature-provenance leakage**, which
  the Roth taxonomy does not even index because it is domain-specific — **and that is precisely where
  STRIDE's real exposure sits (F2 SP-as-`market_odds`, F3 model-derived odds).** Three secondary items:
  (a) **the purge gap is inconsistent — 14 d in retrain, 7 d in walk-forward, 0 in `backtest.py` — so
  the same change measured on two harnesses is not comparable**; (b) there is **no embargo after** the
  test window in either splitter, only a gap before; (c) `backtest_v2_metro.py` — the source of the
  README's headline numbers — is a strategy-band evaluation on a saved pickle with no purge discipline
  of its own.

### R5-F15 — ~130 hand-tuned thresholds, six strategy bands, zero multiple-testing accounting, and nothing measuring the live wrapper · **[ROI]** · *reporting N-trials implementable now; CSCV/PBO needs work*

- **Finding.** The number-of-trials problem is the dominant failure mode of quantitative betting
  research: "when many trading rules or parameterizations are tried, the best in-sample performer is
  likely to be a false discovery unless multiple testing is accounted for"; "the more strategies you
  test, the greater the bias; the longer the backtest, the lower the bias." Standard corrections: White's
  Reality Check, Monte Carlo permutation, **PBO via combinatorially symmetric cross-validation**, and the
  **Deflated Sharpe Ratio**. Standard hold-out is described by that literature as "unreliable and
  inaccurate in the context of investment backtests."
- **Evidence.** White 2000, **Econometrica** 68(5) **[snippet-only] + [recall — unverified]**; Bailey,
  Borwein, López de Prado & Zhu, **Journal of Computational Finance** **[snippet-only]**; comparative
  evidence that "WRC and MCP perform best" **[snippet-only]**. *Transfer justification:* finance, not
  racing — **but the object is identical (selecting a decision rule by historical P&L over many candidate
  rules), and racing's per-bet variance is *higher* than most financial strategies, so the correction is
  if anything more necessary.**
- **What STRIDE does today.** SYSTEM_MAP §6 enumerates ~130 hard-coded numbers on the selection path;
  §9.14 records that the `mw` ladder, the band gates (4 / 2.5 / 3 and 30 / 15 / 10) and the crowd cutoffs
  (50 / 15 / 8 / 70 / 100) have **no fitting script, grid search or holdout evaluation anywhere** —
  "treat all of them as hand-tuned and unvalidated." `backtest_v2_metro.py` sweeps **6** bands,
  `backtest.py` **13**, plus `optimize_threshold`. The validated band is one selection from that sweep,
  reported without a multiplicity correction. And per §3, **the unit that actually decides bets has no
  live hit-rate and no live realised-ROI measurement at all.**
- **The gap.** The headline **+12.3% ROI on 9.9% strike** was selected as the best of ≥6 bands on one
  352-race window. At that strike rate the standard error over a few hundred bets is enormous; with 6+
  trials and no correction, **the honest posterior on "this band is genuinely +EV" is much weaker than
  the point estimate suggests.** Not a criticism of the number — a statement that it has never been given
  a multiplicity-aware confidence interval, and the promotion bar is therefore being applied to an
  uncertainty-free reading of a noisy quantity. `walk_forward_backtest.py` already computes ROI and hit
  rate at 5 thresholds with t-dist 95% CIs; **what is missing is (a) reporting the number of
  configurations tried alongside the winner, and (b) a permutation/bootstrap null.**

### R5-F16 — The training spine is finishers only, and nothing in the system models scratchings · **[ROI]** primarily · *needs work; a cheap first step is implementable now*

- **Finding.** Racing datasets carry three distinct selection effects: (a) non-finishers; (b) **late
  scratchings**, which materially reshape the market — when a horse is withdrawn "the chances of the
  remaining horses winning increases", and the bookmaking correction (Tattersalls **Rule 4(c)**, capped
  at 90p in the £) is a direct measure of how much probability mass is redistributed; and (c) horses that
  appear once or twice, whose form statistics are pure noise (F10's shrinkage problem).
- **Evidence.** Rule 4 mechanics from bookmaker documentation **[snippet-only] — authoritative for the
  *rule*, not academic. AU fixed-odds practice differs in detail from UK Rule 4 but the deduction
  principle is the same; the AU-specific deduction schedule could not be verified.** **No academic study
  of scratching-induced selection bias in racing datasets was reachable — stated as a dead end.**
- **What STRIDE does today.** The training spine filters to finishers:
  `refresh_training_view_v2.py:~197` — `FROM race_results_history r WHERE r.position IS NOT NULL`.
  `field_size` in training comes from `race_results_history` (actual starters); at inference from the
  post-`filter_active_runners` count. **No feature anywhere encodes "a runner was scratched from this
  race", "the field shrank after the odds were taken", or "this price predates a scratching".** The
  de-vig is proportional over whatever quotes are present and **returns 1.0 with no vig removal if fewer
  than 2 quotes exist.**
- **The gap.** Two mechanisms: (1) the pipeline publishes tips from morning prices, and a subsequent
  scratching shifts the true probabilities of everything left, **so the edge STRIDE reports is stale in a
  direction it cannot detect**; (2) **the `overround → 1.0` fallback means the thinnest,
  most scratching-affected races get the *most* optimistic edges — precisely backwards.** SYSTEM_MAP §9
  does not list this; it is new here. Cheap first step: **count how often `calculate_overround` hits the
  `< 2 quotes` branch, and refuse to publish an edge in that case.** Whether the Racing API exposes a
  same-day scratchings feed could not be established.

### R5-F17 — Going is a first-order AU interaction that the contract encodes as two scalars, one of which is dead at inference · **[HIT-RATE]** · *the `sectional_x_going` repair is the same one-line class of fix as F5*

- **Finding.** Track condition is one of the largest sources of AU form reversal — **the repo's own
  21-day autopsy classifies `going_miss` as a named failure mode** — and the effect is inherently an
  *interaction* (a wet-track specialist versus a firm-track horse), not a level.
- **Evidence.** STRIDE's own `research/performance_autopsy_last21days.py` failure taxonomy — repo-internal
  evidence, verified by source read. Going explicitly enters the MC engine's uncertainty: Dirichlet
  concentration scaled by going (**heavy ×0.72 / soft ×0.82 / synthetic ×1.06 / firm ×1.04**,
  `RS:1809-1815`) — the MC side already models "heavy going = more chaos". **External literature on going
  interactions was not reachable; only practitioner material was found and the agent declines to cite it
  as evidence.**
- **What STRIDE does today.** `going_suitability` (live both sides); `ground_suitability` (in
  `VIEW_TO_FEATURE_MAP` so **live in training, but never written into `feat` at inference** — one of F5's
  9 train-only features); and `sectional_x_going = z_200m × going_suitability` — **an interaction whose
  first term is hard 0 at inference, so the whole product is identically 0 in production.**
  `weather_api.py` is **dead** (zero importers) and finishing it is roadmap item 7.
- **The gap.** Going enters the MC engine as an uncertainty scaler and barely enters the GBM at all; the
  one going interaction in the contract is structurally zeroed in production. **Fixing F5 fixes
  `sectional_x_going` for free.** A proper going × run-style × distance interaction set is needs-work;
  live weather/track-condition-at-jump is aspirational.

### R5's highest-value unresolved question, carried forward verbatim

> **Whether `ml_model.py:250-252`'s `TargetEncoder` over `['jockey','trainer','track','going',
> 'race_class']` (`smoothing=10.0, min_samples=5`) is fitted fold-wise or on the full training set. If
> the latter it is classic target leakage (Kaufman et al.). ~20-minute source read. Phase 3 should do it
> first.**

---

# Where the five streams agree

Five agents worked in parallel with no knowledge of each other's findings. Where two or more reached
the same conclusion independently, that convergence is the strongest signal in this run — **and in
every case below the STRIDE half was source-verified separately by each agent.** Ranked by number of
independent arrivals, then by lever size.

### A1. Proportional de-vig is the wrong method and biases edge in a known direction — **4 of 5 streams** (R1-F7, R2-F13, R3-F9/F10/F13, R5-F16)

The most-converged finding in the run. All four agents independently: (i) read `calculate_overround`
(`RTP:432-442`) and `true_market = (100/odds)/overround` (`RTP:673-674`); (ii) grepped and confirmed
**zero** occurrences of `shin|power_method|odds_ratio_method|devig` anywhere in the repo; (iii) found
the same literature (Štrumbelj 2014; Clarke, Kovalchik & Ingram 2017 — the latter with direct
Swinburne/AU provenance); and (iv) independently flagged the `< 2 quotes → return 1.0` branch as **no
vig removal at all**. R3 additionally derived the compounding mechanism algebraically
(`modelEdge = mw·(raw − true_market)`, so the bias is multiplied by the *largest* `mw` at short
prices) and found the two shortest-price backtest cells are the two worst performers (−100.0%,
−28.0%). R5 independently noted the same `1.0` fallback gives **the thinnest, most
scratching-affected races the most optimistic edges.**

**One genuine disagreement inside the agreement** — see the Conflicts section: R1/R3 say proportional
de-vig **inflates** edge on favourites; R2 says it biases edge **toward longshots**.

**The convergence that survives regardless of that sign:** all four agree the method is wrong, that
the correct fix is a `method=` parameter on the **existing** `calculate_overround` behind a
`STRIDE_*` flag defaulting to byte-identical, that power is preferable to Shin on implementation
grounds, and that **it cannot move hit rate** because the power transform is monotone in odds.

### A2. Linear pooling is the structural error at the point where money is decided — **3 of 5** (R1-F5/F6/F17, R2-F1/F3/F17, R5-F1)

R1 and R2 independently found the same theorem (Ranjan & Gneiting 2010, JRSS-B) and the same
consequence: STRIDE's market anchor is a linear opinion pool with **no recalibration downstream**, and
the one live calibrator sits **upstream** of the step that creates the miscalibration. R2 went further
and counted **four** linear pools (`ml_model.py:594`, `RTP:667`, `RTP:692`, `mc_api.py:7393`), none of
which `docs/05 §5` recognises as part of the calibration stack. R1 independently noticed that
`mc_api` **already blends multiplicatively (= log space) while the wrapper blends linearly — the
pipeline contradicts itself.** R5 arrived at the same place from the feature side: the `mw` ladder is
"a *linear, hand-set* approximation of Benter's fitted log-space second stage."

All three name the same remedy family: **the log pool is already in the repo** as
`conditional_logit.py`, and the blocker is a provenance hold (constraint 14), not a design objection.

### A3. The edge gates and the `mw` ladder are hand-tuned, unvalidated, and now actively contradicted — **4 of 5** (R2-F8, R3-F1/F2/F3, R4-F2, R5-F15)

SYSTEM_MAP §9.14 already said "hand-tuned and unvalidated". Four agents independently upgraded that:
R3 showed the founding ROI number has **t = 0.432**, a 95% CI of **[−43.5%, +68.2%]**, and that **ROI
is non-monotonic in edge** within the same price band (+40.1% at edge 3–5% vs −14.8% at edge ≥5%).
R4 showed by arithmetic that **the live gate is not the validated band** — `backtest_v2_metro.py:217`
thresholds a *vig-inclusive* edge while `RTP:1816/1821/1825` threshold a *de-vigged* one, and the live
gate is looser by 3.3pp at $5/R=1.20. R2 showed the `mw` ladder is a hand-fitted **calibration slope**
correction for a quantity with a two-parameter estimator nobody has run. R5 counted ~130 hand-tuned
thresholds against **zero** multiple-testing accounting.

**Joint implication all four state independently:** every threshold in SYSTEM_MAP §6 is currently
unfalsifiable, and no A/B is meaningful until something measures the live wrapper.

### A4. STRIDE computes probability uncertainty and then discards it at the exact moment it matters — **3 of 5** (R1-F16, R3-F4, R4-F5/F8)

Three agents, three routes, one conclusion. R1: the MC win probability carries **1.02pp binomial SE at
N=2000** against a 3.0pp edge gate, and Rao-Blackwellising the Gumbel-max removes the dominant
variance term **and is cheaper**. R3: thresholding a noisy edge is the **winner's curse** — the
retained estimates are biased upward by construction — and the Wilson interval at `RS:334` is computed
and discarded before the gate. R4: Baker & McHale's shrinkage factor scales with **σ²(p̂)**, and
`compute_staking` never reads `ciLower`/`ciUpper` (`mc_api.py:7479-7481`) or the Dirichlet
concentration. R4 adds a second free uncertainty set: **the two engines disagree at `RTP:667` and both
numbers are in scope on that line.**

**Same correction, three application points:** shrink the probability (R1), shrink the edge before the
gate (R3), shrink the stake (R4).

### A5. Class-imbalance corrections are inflating `mlPredictedProb`, and the fitted antidote is switched off — **2 of 5, with a third corroborating** (R1-F3, R2-F4, R5-F7)

R1 and R2 independently read `retrain_v2.py:774/791/802`, independently found the per-model OOF
isotonic fitted at `:835/855/870` and deliberately not applied at `ml_model.py:565`, and independently
found the medical/tree-ensemble literature saying re-weighting destroys calibration with no AUC gain.
Both name it **the most concrete mechanism available for "top pick wins 33.7% but returns −4.2%"**.
R5 approached from the feature side and added that the three treatments are **mutually inconsistent
within one ensemble** and that a fixed `scale_pos_weight=9` implies a 10-runner field, wrong at both
tails (37.7% win rate in fields ≤7 vs 16.6% in ≥16).

Both R1 and R2 independently propose the same one-line diagnostic: **mean `mlPredictedProb` per card
vs observed win rate.** Zero behaviour change; confirms or kills it immediately.

### A6. Nothing measures the live system, and CLV is the way out of the sample-size trap — **3 of 5** (R3-F5/F6/F7, R4-F2/F15, R5-F15)

All three restate SYSTEM_MAP §3's measurement gap and add to it. R3 quantified the trap
(**~3,038 bets for t = 2 at 12.3% ROI; ~18,456 at 5%**; `MIN_BETS_REPORTABLE = 200` is ~15× too small
for ROI) and found the escape: **CLV needs ~400 bets instead of ~3,000, and both prices are already in
the same row of `stride_tip_results`** (`tipped_odds` at `:216`, `api_sp` at `:~305`) — yet
`shadow_pl_tracker.py:299` settles at SP, **the one price the punter did not take**, with an error
that correlates with pick quality. R4 independently found that **the overround is computed and stored
nowhere**, blocking its own largest claim. R5 independently found the wrapper unmeasured and the trial
count unreported.

**Every agent that touched measurement concluded the same sequencing: measure first, change second.**
R3 states it as an explicit prohibition — **do not enable Kelly yet.**

### A7. Two of the three feature families the literature ranks highest are advertised and not wired — **2 of 5** (R3-F8, R5-F4/F9)

R5 proved by source parse that **41 of 113 contract features are identically zero in both training and
inference**, including the *entire* 16-feature market-velocity block and 11 of 17 pace features, and
that no diagnostic in the repo can surface it. R3 independently found the market-movement half of the
same hole from the market side: the convergence pillar's `market_injection` is **pinned to 0** on the
live V3 path (`RTP:2697-2700`), and the two steam taxonomies coexist unadjudicated. Both note that
`research/report.md:125` scores STRIDE "Partially aligned" on market steam/drift **on the strength of
features that are constant zero.**

### A8. The market feature's provenance is broken, and it explains β = 0 — **2 of 5** (R1-F12, R5-F2)

R1 reached it from the literature (Sung & Johnson's one-step vs two-step; the fundamental arm must be
market-free for β to be identified). R5 reached it from the source and found something worse: the
training-time `market_odds` **is the starting price** (`retrain_v2.py:142-144, 557-574`) while
inference serves the ~8am racecard price. **These are the same diagnosis at two depths** — R1 says the
first stage is contaminated with market *features*; R5 says the market feature is contaminated with
*post-decision information*. Both independently conclude the CL fit's β = 0.000 is explained, and R5
adds that **the README's headline numbers and the LambdaRank H2H verdict were both produced on
SP-derived features.**

### A9. Staking is the free lever, and the promotion bar cannot evaluate it — **2 of 5** (R3-F14, R4-F3/F14/F16)

Both agents independently found all three Kelly surfaces to be decoys, both named `RS:309-326` (not
the dead `portfolio_risk.py`) as the module constraint 3 requires extending, and both concluded that
Kelly must **not** be enabled until the measurement work lands. R4 adds the process finding neither
SYSTEM_MAP nor any doc records: **the existing promotion bar cannot evaluate a staking change and will
reject the correct answer**, because Kelly moves neither hit rate nor Brier and typically *lowers*
headline ROI% (worked example: 10% → 7.3% while log-growth rises).

### A10. Small-sample regime: isotonic is the wrong family and STRIDE uses only isotonic — **2 of 5** (R1-F17, R2-F10/F11/F18)

Both found Kull et al. 2017 independently and both concluded beta calibration is the drop-in
improvement that **contains the identity map** and so cannot make a good input worse. R2 adds the
Niculescu-Mizil & Caruana ~2,000-case threshold against STRIDE's **794 `live_model` rows**, the
Venn–Abers path, and the STRIDE-specific consequence neither had seen stated: **an isotonic step
function shrinks `mc_spread` and can therefore trip the flat-MC breaker on its own.**

---

# Where they conflict

Four genuine disagreements. All are stated with both sides; none is resolved by fiat.

### C1. **The direction of the proportional-de-vig bias.** R1 + R3 vs R2 — *the most important conflict in this document*

- **R1-F7 and R3-F10 say:** proportional de-vig **understates the fair probability of favourites**, so
  `(raw − true_market)` is biased **high** at short prices, so STRIDE **over-states `modelEdge` on
  favourites and under-states it on longshots**. R3 derives the compounding: the bias is largest where
  `mw` is largest (0.80 at ≤$3), and offers a falsifiable prediction — short-price cells should
  underperform — which the backtest's two shortest-price strategies (−100.0%, −28.0%) are consistent
  with.
- **R2-F13 says:** proportional de-vig **overstates the longshot's fair probability and understates
  the favourite's**, which biases `modelEdge` **against favourites and toward longshots**, into the
  −41.55% zone; it reads the `odds > 15` ceiling and the rising `mw` as compensations for that.

**Both cannot be true of `modelEdge`.** Note the two agents actually agree on the *statement about
`true_market`* — R2's "overstates the longshot's fair probability and understates the favourite's" is
the same sentence as R1's "understates the fair probability of favourites". **The divergence is in the
sign carried through to `modelEdge`**, i.e. whether a *lower* `true_market` for a favourite inflates
`(raw − true_market)` (R1/R3's reading, arithmetically straightforward) or whether R2 is tracking the
*longshot* end of the same trade. R3's algebra (`modelEdge = mw·(raw − true_market)`) is explicit and
reproducible; R2's is stated in prose.

**Phase 3 must resolve this before any de-vig ticket is written**, because the two readings imply
opposite predictions about which price band currently carries false-positive edge, and therefore
opposite expectations for what a power/Shin de-vig will do to the bet population. **The resolution is
empirical and cheap:** compute `true_market` under proportional, power and Shin on the same stored
fields and tabulate the signed difference by price band. Nothing in this document settles it.

### C2. **Whether `mc_is_flat`/isotonic and the calibration chain are a hit-rate risk or a stakes risk.** R2 vs R4

- **R2-F10(c)** treats the flat-MC breaker as a *calibration artifact hazard*: a coarse isotonic step
  function shrinks `mc_spread`, trips `mc_is_flat`, and **hands ranking authority to the LLM** whose
  top pick bypasses every safety filter — framed primarily as a hit-rate and control-flow problem.
- **R4-F17** treats the same mechanism as a *staking boundary violation*: `low → 0u` makes the sizing
  function a covert **selection** rule, changing the bet denominator, which is exactly what F14 says
  staking must never do — framed primarily as a measurement-integrity problem.

These are **complementary, not contradictory** — same code path, two different harms — but they imply
different owners and different first tickets (R2: swap the calibrator family to something tie-free;
R4: separate "should we bet" from "how much"). **Both agree the firing rate is unmeasured and that a
one-line counter is the prerequisite.** Phase 3 should merge them into one ticket with two acceptance
criteria rather than pick a side.

### C3. **Whether the price band should be reconsidered.** R3-F11/F12 internal tension, echoed by R1-F11

- **R3-F11 and constraint 27 say:** the `$2–$15` band is **defensible on FLB grounds and should not be
  loosened**, and R3-F5 adds an independent statistical reason — **longshot returns are unmeasurable
  at realistic n, so a system that cannot measure a bet class should not bet it.**
- **R3-F12 says:** FLB is a **context effect**, not a stable function of price, so **a fixed price band
  is the wrong parameterisation** — the right conditioning variable is race context (field size, price
  dispersion, segment), and `market_efficiency.py` implements exactly that and is unwired.
- **R1-F11 says:** the *shape* of the filter may be wrong in a third way — Chapman's >20% returns came
  from a **model-probability floor (p̂ < 0.04)**, not an odds ceiling, and those select different sets.

**All three are defensible and they are not reconcilable a priori.** R3-F12 itself supplies the
tie-break protocol and it should be honoured: **get the (odds-decile × edge-decile) ROI surface running
first, re-cut it by field size and price dispersion, and only then decide.** R3 is explicit that
`market_efficiency.py` must **not** be wired on the strength of the literature alone — the repo's own
standard (constraint 36) is causal ablation, not plausibility.

### C4. **How much of the "more data" framing survives.** R1-F18 vs R5-F11/F17 and R3-F8

- **R1-F18 says:** *"we need more data" is not admissible* — STRIDE has 4.5× Benter's development
  sample and ~9× his stated minimum; the binding constraints are specification issues costing no new
  data.
- **R5-F11, R5-F17 and R3-F8 say:** the highest-value items include **~47% sectional coverage** (a
  purchase decision), **QLD structurally blocked**, a **live weather/track-condition feed**, and the
  **T−5-minute odds snapshot** (which R3 promotes for a second reason: it is the reference price CLV
  needs).

**These are reconcilable and R1 states the reconciliation itself:** *"The one genuine data gap the
evidence supports is a **different kind** of data, not more of the same."* The conflict is one of
emphasis rather than fact — but Phase 3 should carry R1's framing explicitly, because "collect more
data" is the failure mode this run is most likely to drift into. **Note also R3-F8's nuance, which
prevents the wrong sequencing:** for *CLV measurement* no new collection is needed (SP is already
stored); only *late-odds-as-a-feature* needs new infrastructure. **Do not let the harder half block
the easy half.**

### Non-conflicts worth recording (apparent disagreements that dissolve on reading)

- **Kelly: enable or not?** R4 spends 17 findings on how to size correctly; R3-F14 says **do not enable
  Kelly yet**. R4 agrees explicitly (F6: "Establishing live ECE must precede, not follow, any Kelly
  wiring"; F15: bankroll state is the prerequisite for seven of its own findings). **No conflict — both
  say measure first.**
- **LambdaRank.** Constraint 15 keeps the ranker evidence-only; R1-F9 does **not** propose overriding
  it — it proposes a *new evidence-only arm* (`CatBoostRanker(loss_function='QuerySoftMax')`) judged by
  the criterion already written at `docs/12:396`. R5-F7 independently arrives at the same "retest with
  CatBoost, after new information lands". **No conflict.**
- **Walsh & Joshi's magnitudes.** R4-F6 quotes them in detail; R2-F15 shows a corrigendum revised **all
  results**. R4 independently flagged the corrigendum's existence as a caveat on its own finding.
  **Not a conflict — a correction both agents reached.** ⚠️ **Phase 3 must treat the −75.9%,
  +34.69%/−35.17%, 4.46%/5.03% and 64.62%/64.27% figures as pre-corrigendum and withdrawn pending
  re-fetch. The direction survives; the magnitudes do not.**

---

# Master source table

Every source cited across all five streams, deduplicated. **Status is the honest one: three external
documents were fetched in the entire run.** "Cited by" gives stream-finding IDs. Where two streams
cited the same work, both are listed — that is itself evidence of convergence.

## Fetched (3)

| Source | What it is | Cited by | Value |
|---|---|---|---|
| `github.com/chris-alex-p/german-horse-racing` `notebooks/analysis_benter_methods.md` | Benter-method replication, German Ausgleich IV 2019-23: 5,625 runner-obs / 524 wins, **one-step** CL, odds coef −0.0835 (p<0.001), concordance 0.738, LR χ²=352.8/13 df, 914 bets → €54.90 (+6.0% ROI) vs 15% takeout, bootstrap p=0.0218 | R1-F4/F12, R5-F6 | **High** — the only independent CL replication with full numbers; also a *negative control* for within-race normalisation |
| `raw.githubusercontent.com/catboost/.../ranking_tutorial.ipynb` | CatBoost ranking tutorial; verbatim `QuerySoftMax` documentation | R1-F9 | **High** — proves the listwise top-1 conditional logit ships in a dependency STRIDE already has |
| `github.com/conorwalsh99/ml-for-sports-betting` | Walsh & Joshi replication repo README | R2-F15 | **High** — sole confirmation that a corrigendum revised *all* results |

## Racing methodology — foundational

| Source | Venue / year | Status | Cited by |
|---|---|---|---|
| Bolton & Chapman, *Searching for Positive Returns at the Track* | **Management Science** 32(8):1040-1060, 1986 | **[audit: venue/vol/pages/200-races/rank-ordered-explosion/longshot-side-constraint CONFIRMED; track-level split [unverified]]** | R1-F1/F18, R2-F5, R5-F1/F6/F7 |
| Benter, *Computer Based Horse Race Handicapping and Wagering Systems* | *Efficiency of Racetrack Betting Markets* ch.19, 1994 | **[audit: chapter CONFIRMED; the R² triple 0.1218/0.1245/0.1396 and the "500-1000 races" quote are [unverified] — not returned by any search]** | R1-F4/F18, R4-F11, R5-F1 |
| Chapman, *Still Searching for Positive Returns at the Track* (2,000 HK races) | same volume, ch.18, 1994 | **[audit: CONFIRMED near-verbatim — 20-variable MNL, 2,000 HK races, p̂<0.04 ⇒ ">20%" returns]** | R1-F11/F18 |
| Sung & Johnson, *Comparing One- and Two-Step Conditional Logit Models* | **Journal of Prediction Markets**, 2012 | [snippet-only] (403) | R1-F12 |
| Lessmann, Sung & Johnson, *Alternative methods of predicting competitive events* | **IJF** 26(3):518-536, 2010 | **[audit: venue/vol/pages + "competitive element" abstract CONFIRMED; the "1,000 HK races / 12,902 horses" and "RF 20.26% vs CL 8.84% over 500 races" figures are [unverified]]** | R1-F13/F18, R5-F6/F7 |
| Lessmann, Sung & Johnson, *Identifying winners … SVM-based classification* | **EJOR** 196(2):569-577, 2009 | [snippet-only] (403) | R1-F2/F13, R5 (context) |
| Edelman, *Adapting SVM methods for horserace odds prediction* (**AU data**, 200/100 races) | **Annals of OR** 151:325-336, 2007 | [snippet-only] (403) | R1-F13/F18 |
| Silverman, *A Hierarchical Bayesian Analysis of Horse Racing* | **JPM** 6(1):1-13, 2012 | [snippet-only] (403) | R1-F15 |
| Silverman & Suchard, *Regularized Conditional Logistic Regression with Frailty* | **JPM** 7(1):43-52, 2013 | [snippet-only] (403) ⚠️ method verified 3-0, **36.73% ROI claim refuted 0-3** [prior pass] | R1-F15 |
| Koker, *Beating the Odds: ML for Horse Racing* (938 HK races, softmax-over-race) | practitioner blog, 2019 | [snippet-only] (403), **not peer-reviewed** | R1-F14/F18 |
| Lo & Bacon-Shone, *Probability and Statistical Models for Racing*; NESSIS 2007 slides; Ali, *Probability models on horse-race outcomes* | survey, various | [snippet-only] (403) — α=0.89/0.80 agreed across 3 searches; ⚠️ the "7.34% / 15,000 races" figure **could not be attributed and is not relied on** | R1-F8, R2-F5 |
| Harville 1973 / Henery 1981 / Stern 1990 / Bacon-Shone, Lo & Busche 1992 | ordering-model family | [snippet-only] (403) | R1-F8, R2-F5 |
| West et al., *Classification in Horse Race Prediction Through PCA* | **Journal of Prediction Markets** | [snippet-only] (403) | R1-F2 |
| Peng & Uryasev, quantile running-time model | 2026 | [snippet-only] (403) — noted, not used | R1 |

## Learning-to-rank and model class

| Source | Venue / year | Status | Cited by |
|---|---|---|---|
| *Horse race rank prediction using learning-to-rank approaches* (Seoul) | **KJAS** 37(2):239-253, 2024 | **[audit: CONFIRMED — Chung, Shin, Hwang & Park; pairwise (RankNet/LambdaMART via XGB/LGB/CatBoost Rankers) generally beats pointwise; **CatBoost Ranker best**]**; verified 3-0/2-1 by prior pass | R1-F9, R5-F7 |
| KRA listwise LambdaRank study (**9,140 races**) | JKSCI, 2025 | [snippet-only] (403) | R1-F9/F18 |
| ⚠️ **`NDCG = 0.8895 / MAP = 0.4204`** | — | **Deliberately unattributed** — R1 could not determine which of the two papers above it belongs to | R1-F9 |
| Cao, Qin, Liu, Tsai & Li, *ListNet* | **ICML 2007**, pp.129-136 | [snippet-only] (403) | R1-F9 |
| Xia, Liu, Wang, Zhang & Li, *ListMLE* | **ICML 2008** | [snippet-only] (403) | R1-F9 |
| Salvadé & Hillel, *RUMBoost: Gradient boosted random utility models* | **Transportation Research Part C**, 2024 (arXiv 2401.11954) | [snippet-only] (403); repo `big-ucl/rumboost` reachable but **not read** | R1-F10 |
| Grinsztajn, Oyallon & Varoquaux, *Why do tree-based models still outperform deep learning on tabular data?* | **NeurIPS** 35:507-520, 2022 | [snippet-only] (403) | R1-F14 |

## Calibration

| Source | Venue / year | Status | Cited by |
|---|---|---|---|
| **Ranjan & Gneiting, *Combining probability forecasts*** | **JRSS-B** 72(1):71-91, 2010 | **[audit: CONFIRMED — venue/vol/pages, the theorem ("any non-trivial weighted average of two or more distinct, calibrated probability forecasts is necessarily uncalibrated and lacks sharpness") and the **beta-transformed linear opinion pool** remedy. DOI 10.1111/j.1467-9868.2009.00726.x. Strongest calibration citation in the document]** | R1-F5/F6, R2-F1 |
| Satopää, Baron, Foster, Mellers, Tetlock & Ungar, logit aggregator | **IJF**, 2014 | [snippet-only] (403) | R1-F5/F6, R2-F1 |
| *No-Regret Learning with Unbounded Losses: Logarithmic Pooling* | arXiv 2202.11219 | [snippet-only] (403) | R2-F1 |
| *Bayesian Ensembles of Binary-Event Forecasts* (when **not** to extremize) | arXiv 1705.02391 | [snippet-only] (403) | R2-F1 |
| Kull, Silva Filho & Flach, *Beta calibration* | **AISTATS 2017** (PMLR 54) + **EJS** 11(2):5052-5080 | [snippet-only] (403), **two independent streams** | R1-F17, R2-F11 |
| Guo, Pleiss, Sun & Weinberger, *On Calibration of Modern Neural Networks* (temperature scaling) | **ICML 2017** (>5,000 cites) | [snippet-only] (403) | R2-F12 |
| Minderer et al., *Revisiting the Calibration of Modern Neural Networks* | NeurIPS 2021 | [snippet-only] (403) | R2-F12 |
| Niculescu-Mizil & Caruana, *Predicting Good Probabilities* (the ~2,000-case threshold) | **ICML 2005** (>2,000 cites) + UAI 2005 | [snippet-only] (403) | R2-F2/F10 |
| Vaicenavicius, Widmann, Andersson, Lindsten, Roll & Schön, *Evaluating model calibration* | **AISTATS 2019** | [snippet-only] (403) | R2-F6 |
| Kumar, Liang & Ma, *Verified Uncertainty Calibration* | **NeurIPS 2019** (arXiv 1909.10155) | [snippet-only] (403) | R2-F6 |
| Roelofs, Cain, Shlens & Mozer, *Mitigating Bias in Calibration Error Estimation* | **AISTATS 2022** | [snippet-only] (403) | R2-F6 |
| Dimitriadis, Gneiting & Jordan, *Stable reliability diagrams* (**CORP**) | **PNAS** 118(8), 2021 | [snippet-only] (403) | R2-F6/F7 |
| Van Calster, McLernon, van Smeden, Wynants & Steyerberg, *Calibration: the Achilles heel* | **BMC Medicine** 17:230, 2019 (>2,000 cites) | [snippet-only] (403) | R2-F8 |
| Van Calster et al., *A calibration hierarchy for risk models*; Stephen/Riley et al. | **JCE** 2016 / 2019 | [snippet-only] (403) | R2-F8 |
| Perez-Lebel, Le Morvan & Varoquaux, *Beyond calibration: grouping loss* | **ICLR 2023** (arXiv 2210.16315) | [snippet-only] (403) | R2-F9 |
| Kull et al., *Dirichlet calibration / classwise-ECE* | NeurIPS 2019 (arXiv 1910.12656) | [snippet-only] (403) | R2-F9 |
| Vovk & Petej, *Venn–Abers predictors*; *Generalized Venn and Venn-Abers Calibration* | 2012/UAI 2014; **ICML 2025** | [snippet-only] (403) | R2-F18 |
| Davis et al., *Detection of calibration drift in clinical prediction models* | **J. Biomedical Informatics**, 2020 | [snippet-only] (403) | R2-F16 |
| Park et al., *Calibrated Prediction with Covariate Shift*; *Unsupervised Calibration under Covariate Shift* | **AISTATS 2020**; arXiv 2006.16405 | [snippet-only] (403) | R2-F16 |
| *Confidence-Aware Multi-Field Model Calibration* | arXiv 2402.17655 | [snippet-only] (403) | R2-F17 |
| Filho, Kull & Flach, *Classifier Calibration: A survey*; *Classifier Calibration at Scale* | arXiv 2112.10327; arXiv 2601.19944 | [snippet-only] (403) | R1-F17, R2-F11 |
| *Isotonic Recalibration under a Low Signal-to-Noise Ratio* | arXiv 2301.02692 | [snippet-only] (403) | R2-F10 |
| *Resolution Lost: The Deadweight Costs of Strict Isotonicity* | gojiberries.io | [snippet-only] (403), **blog** | R2-F10 |
| scikit-learn `CalibratedClassifierCV` docs (prefit disjointness) | — | [snippet-only] (403) | R2-F2/F17 |
| Murphy, *A New Vector Partition of the Probability Score* | J. Appl. Meteorology 12, 1973 | **[recall — unverified]**; existence corroborated [snippet-only] | R2-F7 |
| Kelly ↔ log-score / Bregman identity | arXiv 2607.06166 | **⚠️ [audit: MISATTRIBUTED. 2607.06166 resolves to *"When do prophets profit in prediction markets?"* (Gu, Wu et al.). The quoted KL/Bregman/Kelly passage was not located in it — treat the quotation as unsupported]** | R2-F7 |
| Oddacious, *Proper scoring rules* | practitioner survey | [snippet-only] (403), **not peer-reviewed** | R2-F7 |
| Spiegelhalter's z (1986, *Statistics in Medicine*) | — | **[recall — unverified]** via secondary sources only; **STRIDE does not use Hosmer–Lemeshow, so no gap was reported** | R2 (method note) |

## Class imbalance

| Source | Venue / year | Status | Cited by |
|---|---|---|---|
| van den Goorbergh, van Smeden, Timmerman & Van Calster, *The harm of class imbalance corrections* | **JAMIA** 29(9):1525-1534, 2022 (preprint arXiv 2202.09101) | **[audit: CONFIRMED — venue/vol/pages and "all imbalance correction methods led to poor calibration". Caveat: **logistic regression**, not tree ensembles]** | R1-F3 |
| Carriero, Luijken, de Hond, Moons, Van Calster & van Smeden, *The Harms of Class Imbalance Corrections … A Simulation Study* | **Statistics in Medicine** 44(3-4):e10320, 2025 | **[audit: paper CONFIRMED by title/venue/DOI 10.1002/sim.10320; the "all scenarios" and "not always able to be corrected with re-calibration" claims are [unverified]]** | R1-F3 |
| Dal Pozzolo, Caelen, Johnson & Bontempi, *Calibrating Probability with Undersampling* | **IEEE SSCI 2015** | [snippet-only] (403) | R2-F4 |
| *The Hidden Cost of Resampling* (tree ensembles) | arXiv 2606.29720 | **⚠️ [audit: resolves (Zewen Liu, single-author **preprint**, 5 datasets). PARTLY CONTRADICTS the use made of it: SMOTE cost is small (ECE +0.009) and post-hoc Platt/isotonic **eliminates** the damage (ECE −66%). Does not study `scale_pos_weight`]** | R2-F4 |
| MachineLearningMastery, *How to Calibrate Probabilities for Imbalanced Classification* | — | [snippet-only] (403), **practitioner** | R2-F4 |

## Odds → probability / de-vig

| Source | Venue / year | Status | Cited by |
|---|---|---|---|
| **Štrumbelj, *On determining probability forecasts from betting odds*** | **IJF** 30(4):934-943, 2014 — **83 citations** (SciSpace) | **[audit: CONFIRMED incl. the 83-citation count and "Shin > basic normalisation and regression". The "Shin's advantage shrinks as market size grows" claim in R2-F13 is [unverified] and struck]** | R1-F7, R2-F13 |
| Štrumbelj, *A Comment on the Bias of Probabilities Derived From Betting Odds* | **Journal of Sports Economics**, 2016 | [snippet-only] (403) | R2-F13 |
| **Clarke, Kovalchik & Ingram, *Adjusting Bookmaker's Odds to Allow for Overround*** | **American J. Sports Science** 5(6):45-49, 2017, DOI 10.11648/j.ajss.20170506.12 | **[audit: CONFIRMED verbatim — the power-method quote, the defect list for additive/normalisation/Shin, and the Vovk & Zhadanov 2009 / Clarke 2016 attribution]**. ⚠️ **The numerical comparison tables still could not be obtained — "the single most valuable missing number in this report"** | R1-F7, R3-F9 |
| Shin, *Measuring the Incidence of Insider Trading in a Market for State-Contingent Claims* | **Economic Journal**, 1993 | [snippet-only] (403); **[recall — unverified]** for exact title/venue | R1-F7, R2-F13, R3-F13 |
| *A Family of Solutions Related to Shin's Model* (closed-form variants) | 2024 | [snippet-only] (403) | R2-F13 |
| Vovk & Zhadanov, power method | 2009 | [snippet-only] (403) | R3-F9 |
| Whelan, *On Estimates of Insider Trading in Sports Betting* (critique of Shin's `z`) | working paper | [snippet-only] (403) | R3-F13 |
| Cain, Law & Peel, *The Favourite-Longshot Bias, Bookmaker Margins and Insider Trading* | — | [snippet-only] (403) | R2-F13, R3-F13 |

## Market efficiency, FLB and execution

| Source | Venue / year | Status | Cited by |
|---|---|---|---|
| ECU working paper — AU 2006 season, all 14,854 races (FLB −12.35% → −41.55%, coef −0.621, z = −11.29) | — | **[prior pass, verified 3-0]**; 403 this session | R2-F9/F13, R3-F10/F11, R4-F2 |
| JRA late-odds study, **894,127 runners 2004-2023** (coef −0.3386, SE 0.0392) | arXiv 2509.14645 | **[prior pass, verified 3-0]**; 403 | R3-F8, R5-F2 |
| Snowberg & Wolfers, *Explaining the Favorite-Longshot Bias* | **JPE** 118(4):723-746, 2010 | [snippet-only] (403) — conclusion only, **no tables** | R3-F11 |
| Whelan, *Risk aversion and favourite–longshot bias in a competitive fixed-odds betting market* | **Economica**, 2024 | [snippet-only] (403) | R3-F11 |
| **Meyer & Hundtofte, *The Longshot Bias Is a Context Effect*** | **Management Science** 69(11):6954-6968, 2023, DOI 10.1287/mnsc.2023.4684 | **[audit: CONFIRMED — both quoted sentences (contrast effects; the bias disappears in isolation / when probabilities are easier to compare) appear near-verbatim]** | R3-F12 |
| Green, Lee & Rothschild — informed bettors concentrate at the close | — | [prior pass, verified 3-0] | R5 (context) |
| *Inefficient Forecasts at the Sportsbook* (line-movement overreaction) | ResearchGate 372441761 | [snippet-only] (403) | R3-F8 |
| *Efficient Market Dynamics … Betfair UK Horse Racing* (**1,056,766 price-change signals, 50 ms**) | arXiv 2402.02623, **IJIT** 2024 | [snippet-only] (403) | R3-F8 |
| Kaunitz, Zhong & Kreiner, *Beating the bookies with their own numbers* (real-money campaign, account limiting) | arXiv 1710.02824, 2017 | [snippet-only] (403) — **exact real-money figures not obtained** | R3-F15 |
| **Racing NSW — MBL Schedule 1 + FAQ** (primary regulator documents) | — | **[audit: both PDFs exist on racingnsw.com.au; the $2,000/$800 metro, $1,000/$400 country-provincial figures and the "after 9am on raceday" rule are CONFIRMED. Night-meeting 2pm rule, jurisdiction rule, WA/NT carve-out, 2014/2016 commencement dates and the ~$250 Top Fluc cap remain [unverified]]** | R3-F15, R4-F12 |
| Racing Queensland MBL; WA CITS; Winning Edge state summary | — | [snippet-only] | R4-F12 |
| Australian Govt DSS, *Betting restrictions and online wagering in Australia* | — | [snippet-only] (403) | R3-F15 |
| Betfair Australia Hub *Commissions and Charges*; Betfair Automation Hub | — | **[audit: CONFIRMED — MBR 8% or 10% by state and code, on **net market winnings**; NSW/ACT racing is the 10% case]** | R4-F13 |
| Champion Bets — AU product types, Top Fluc ~$250 cap | — | [snippet-only] | R4-F12/F13 |
| horise.com / bets.com.au — **AU tote win takeout ~14.5%** | — | [snippet-only], **industry, load-bearing for R3-F11** | R3-F11 |
| Betfair AU liquidity (forum: "$904,000 → $372,000" at Flemington) | betangel forum, bonusbank | [snippet-only] — **practitioner forums, single unverified claim, indicative only** | R3-F16 |
| Racing Queensland FY2024-25 turnover **$5.6bn, down 9.3%** | — | **[prior pass, verified 3-0]** | R3-F16 |
| CLV explainers (strideodds.ai, boydsbets, oddsjam, joesaumarez) | — | [snippet-only] (403) — **all practitioner; R3 could not find a peer-reviewed CLV→profit quantification and says so** | R3-F6 |
| punter2pro, sample-size folklore ("300+ bets") | — | [snippet-only] (403) — **cited to reject** | R3-F5 |
| Ziemba, pari-mutuel survey (**Annual Review of Financial Economics**); Betwise feasibility paper | — | 403, **unobtained — both looked directly on-topic** | R3 (gaps) |

## Staking, Kelly and risk

| Source | Venue / year | Status | Cited by |
|---|---|---|---|
| Kelly, *A New Interpretation of Information Rate* | **Bell System Technical Journal** 35(4):917-926, 1956 | **[recall — unverified]**; formula independently present in STRIDE at `RS:319` | R4-F1 |
| **Baker & McHale, *Optimal Betting Under Parameter Uncertainty: Improving the Kelly Criterion*** | **Decision Analysis** 10(3):189-199, 2013, DOI 10.1287/deca.2013.0271 | **[audit: CONFIRMED — venue/vol/pages, the shrinkage conclusion, the "back of envelope" correction, and the tennis validation]**. ⚠️ **the closed form of `k` still could not be obtained and was deliberately not guessed** | R2-F14, R3-F14, R4-F5 |
| Metel, *Kelly betting on horse races with uncertainty in probability estimates* | arXiv 1701.02814, 2017 — **published *Decision Analysis* 15(1):47-52, 2018** | **[audit: CONFIRMED; MNL probabilities, mutually exclusive outcomes, stochastic optimisation. Cite the journal version, not only the preprint]** | R1-F15, R3-F14, R4-F5 |
| Chu, Wu & Swartz, *Modified Kelly Criteria* | SFU | [snippet-only] (403) | R2-F14, R4-F5 |
| MacLean, Ziemba & Blazenko, *Growth versus Security in Dynamic Investment Analysis* | **Management Science** 38(11):1562-1585, 1992 | **[audit: CONFIRMED — 1/3 vs 1/9 halving-before-doubling, and half-Kelly ≈75% of growth. The "30% Kelly / 1-in-213 / 51% growth" figure is [unverified]]** | R4-F4 |
| MacLean, Thorp & Ziemba, *Long-term capital growth*; Ziemba, *Using the Kelly Criterion for Investing* | **Quantitative Finance** 2010; chapter | [snippet-only] (403) | R4-F4 |
| Busseti, Ryu & Boyd, *Risk-Constrained Kelly Gambling* | arXiv 1603.06183, 2016 | [snippet-only] (403) | R4-F7 |
| Sun & Boyd, *Distributional Robust Kelly Gambling* (**horse-racing worked example**, ±15% → >1.5×) | arXiv 1812.10371, 2018 | **[audit: paper CONFIRMED (also `web.stanford.edu/~boyd/papers/robust_kelly.pdf`, 403). The ±15% and >1.5× figures remain [unverified] — a second, targeted search returned nothing. Do not size a ticket on them]** | R4-F8 |
| Smoczynski & Tomkins, *An explicit solution … allocations of a bettor's wealth* | **The Mathematical Scientist** 35:10-17, 2010 | [snippet-only] (403) | R4-F9 |
| Whitrow, *Algorithms for optimal allocation of bets on many simultaneous events* | **JRSS-C** 56(5):607-623, 2007 | [snippet-only] (403) | R4-F9/F10 |
| Whelan, *On optimal betting strategies with multiple mutually exclusive outcomes* | **Bulletin of Economic Research**, 2025 | [snippet-only] (403) | R4-F9 |
| *A Comparison of Simultaneous Kelly Betting Strategies* | **J. Gambling Business and Economics** | [snippet-only] (403) — the 20–25% cap is **practitioner heuristic, not a result** | R4-F10 |
| Isaacs 1953; Hausch, Ziemba & Rubinstein 1981; Ziemba & Hausch 1984/1987 (Dr Z) | — | [snippet-only] (403) | R4-F11 |
| Uhrín, Šír, Šourek & Železný, *Optimal sports betting strategies in practice* (**incl. horse racing**) | arXiv 2107.08827, 2021 | [snippet-only] (403); **[prior pass — extended]** | R4-F14 |
| **Walsh & Joshi, *ML for sports betting: accuracy or calibration?*** | **Machine Learning with Applications** 16:100539, June 2024 (preprint arXiv 2303.06021) | **[audit: CONFIRMED — venue/vol/article no. and the +34.69%/−35.17%, +36.93%/+5.56% pairs as published pre-corrigendum]** | R2-F15, R3-F14, R4-F6 |
| **Corrigendum to the above** | **MLwA Volume 19, March 2025** (**[audit: the doc said "Feb 2025" — corrected]**) | **[audit: CONFIRMED — "errors … in the feature engineering steps of the pipeline … affected all later results", conclusion unchanged]** ⚠️ **magnitudes withdrawn pending re-fetch** | R2-F15, R4-F6 |
| matthewdowney.github.io — fractional-Kelly-under-uncertainty simulations | — | [snippet-only] (403), **practitioner** | R4-F4 |

## Backtest overfitting and selection bias

| Source | Venue / year | Status | Cited by |
|---|---|---|---|
| White, *A Reality Check for Data Snooping* | **Econometrica** 68(5), 2000 | [snippet-only] (403) + [recall — unverified] | R3-F2/F17, R5-F15 |
| Hansen, Superior Predictive Ability (SPA) | 2005 | [snippet-only] (403) | R3-F2/F17 |
| Kuan et al., *Re-Examining the Profitability of Technical Analysis with White's Reality Check*; Neuhierl, *Data Snooping and Market-Timing Rule Performance* | — | [snippet-only] (403) | R3-F2 |
| Bailey & López de Prado, *The Deflated Sharpe Ratio* | **J. Portfolio Management** 40(5), 2014 | [snippet-only] (403) — ⚠️ **equations not obtained; R3-F17 is a protocol sketch, not a spec** | R3-F17, R5-F15 |
| Bailey, Borwein, López de Prado & Zhu, *The Probability of Backtest Overfitting* (CSCV) | **J. Computational Finance**, 2016 | [snippet-only] (403) | R3-F17, R5-F15 |
| López de Prado, *Advances in Financial Machine Learning* (purging/embargo) | Wiley, 2018 | [snippet-only] + [recall — unverified] | R5-F14 |
| EFMA working paper — "WRC and MCP perform best" | 2020 | [snippet-only] (403) | R5-F15 |
| *Breaking the Winner's Curse with Bayesian Hybrid Shrinkage*; Bigdeli et al. (winner's-curse correction, genetics) | arXiv 2511.06318; **Bioinformatics** 32(17):2598, 2016 | [snippet-only] (403) — **domain-free selection statistics; transfer justified** | R3-F4 |

## Data quality, leakage and features

| Source | Venue / year | Status | Cited by |
|---|---|---|---|
| Kaufman, Rosset, Perlich & Stitelman, *Leakage in Data Mining* | **ACM TKDD** 6(4):15, 2012 | [snippet-only] (403); ~700+ cites **[recall — unverified]** | R5-F2/F3/F10/F14 |
| Kapoor & Narayanan, *Leakage and the reproducibility crisis* (**294 papers / 17 fields / 8 types**) | **Patterns** 4(9), 2023 | [snippet-only] (403) | R5-F2/F14 |
| Roth, *Which Leakage Types Matter?* (**2,047 datasets**, 4-class taxonomy) | arXiv 2604.04199, 2026 (Simon Roth) | **[audit: CONFIRMED on a second independent search — 2,047 tabular datasets, 28 within-subject experiments + a boundary experiment on 129 temporal datasets, Class I \|ΔAUC\| ≤ 0.005, Class II ≈90% noise-exploitation share, Class III d_z 0.37→1.11, Class IV invisible under random CV. The "1.38 at 30% duplication" sub-figure is [unverified]]** | R5-F14 |
| Twala, Jones & Hand (origin of **MIA**) | **Pattern Recognition Letters** 29(7), 2008 | [snippet-only] (403) | R5-F5/F13 |
| Perez-Lebel, Varoquaux, Le Morvan, Josse & Poline, *Benchmarking missing-values approaches* | **GigaScience** 11, 2022 | [snippet-only] (403) — the missing-indicator result | R5-F5/F13 |
| Chen & Guestrin, XGBoost sparsity-aware split finding (default direction per split) | KDD 2016 | **[recall — unverified; kdd.org blocked]** | R5-F5 |
| Oki, Sasaki & Willham, *Influence of jockeys on racing time* | **J. Animal Breeding and Genetics** 112(1-6), 1995 | [snippet-only] (403) | R5-F10/F12 |
| Oda et al., *Assessing the predictability of racing performance … mixed-effects model* (**12 course-distance categories**) | **JABG**, 2024 | [snippet-only] (403) | R5-F10 |
| Robinson, *Understanding empirical Bayes estimation* (beta-binomial shrinkage) | varianceexplained.org | [snippet-only] (403), **practitioner but methodologically standard** | R5-F10 |
| Punting Form / sectionaltimes.com.au — **~85% AU TAB coverage, history to Oct 2012** | — | **[prior pass, verified 2-0]**; ⚠️ **QLD coverage still unverified** | R5-F11 |
| honestbettingreviews (field size: **37.7% ≤7 vs 16.6% ≥16**, 96k+ runners 2013-2026); raceadvisor | — | [snippet-only] (403), **practitioner, not peer-reviewed** | R5-F7/F8 |
| Draw bias: raceadvisor, thepuntlab (AU), geegeez, btxracing | — | [snippet-only] (403), **practitioner — no peer-reviewed AU draw-bias study was reachable** | R5-F8 |
| Pace: equinedge (**"72.5%" — vendor claim, treat as marketing**), brisnet, geegeez, globalracing | — | [snippet-only] (403), **practitioner** | R5-F9 |
| Sectional ratings: rulesofsport, learnbetwin, horise, drawbias.com | — | [snippet-only] (403), **practitioner** | R5-F11 |
| Weight / prize money: strideodds, flatstats, drawbias, LightGBM-SHAP write-ups, horise benchmark guide | — | [snippet-only] (403), **practitioner/industry — ranking reported, no effect size trustworthy** | R5-F12 |
| Rule 4 deduction mechanics (sportinglife, horseracing.net, olbg, 888sport) | — | [snippet-only] (403), **bookmaker documentation — authoritative for the rule, not academic; AU schedule unverified** | R5-F16 |
| r-bloggers, *Quantitative Horse Racing with R* | 2026 | [snippet-only] (403), **non-peer-reviewed** | R2-F5 |

## In-repo sources (read directly by every stream)

`docs/analysis/SYSTEM_MAP.md` · `docs/12-hit-rate-research.md` · `research/report.md` ·
`examples/backtest_summary.json` (**the arithmetic base for R3-F1/F2/F3/F5/F10**) ·
`server/python/run_tips_pipeline.py` · `mc_api.py` · `retrain_v2.py` · `ml_model.py` ·
`calibration_model.py` · `conditional_logit.py` · `rank_model.py` · `backtest_v2_metro.py` ·
`walk_forward_backtest.py` · `shadow_pl_tracker.py` · `portfolio_risk.py` · `market_efficiency.py` ·
`relative_market.py` · `form_feature_builder.py` · `refresh_training_view_v2.py` ·
`auto_results_collector.py` · `race_normaliser.py` · `mc_recalibration.py` ·
`racing_system_v8.3_mc.py` · `docs/01`, `docs/04`, `docs/05`, `docs/09`, `docs/10`, `docs/11`.
**Status: fetched (local reads). These carry every "What our system does today" claim in this
document and are the only evidence in it that is not subject to the egress caveat.**

---

## Standing corrections and re-verification queue for Phase 3

Carried forward verbatim from the streams that raised them. These are **not** optional.

1. **Walsh & Joshi magnitudes are withdrawn.** `research/report.md §2.5`'s +34.69% / −35.17%,
   −75.9% bankroll, +36.93%, 4.46%/5.03% classwise-ECE and 64.62%/64.27% accuracy pair are all
   **pre-corrigendum**. The corrigendum (MLwA, Feb 2025) states errors in feature engineering affected
   **all results** while leaving the conclusion unchanged. **Re-quote from the corrigendum or do not
   quote.** (R2-F15, R4-F6)
2. **`NDCG = 0.8895 / MAP = 0.4204` is unattributed** and must not be assigned to either the KJAS 2024
   Seoul paper or the JKSCI 2025 KRA paper. (R1-F9)
3. **Benter's R² triple (0.1396 / 0.1245 / 0.1218) has never been independently confirmed by anyone in
   this research run.** It is the most-cited number in STRIDE's own design rationale. (R1-F4, R5-F1)
4. **Clarke et al.'s effect-size tables were not obtained** — R3 names this the single most valuable
   missing number in its report, and it is what would size the de-vig ticket. (R1-F7, R3-F9)
5. **Baker & McHale's shrinkage formula was not obtained** and was deliberately not reconstructed.
   R4-F5 is directional, not implementable-as-specified. (R4-F5)
6. **The "7.34% / 15,000 races" Harville figure could not be attributed and is not relied on.** (R1-F8)
7. **Roth 2026's four class-level leakage effect sizes** come from one search extraction of a blocked
   PDF. Provisional. (R5-F14)
8. **Lessmann's "RF 20.26% vs CL 8.84% over 500 races"** is snippet-only, and
   `research/report.md §2.4`'s standing warning about published racing-ROI headlines applies with full
   force. (R5-F6)
9. **Highest-priority re-fetches when egress allows**, as nominated by the streams: Ranjan & Gneiting
   2010 · Štrumbelj 2014 · Clarke, Kovalchik & Ingram 2017 · the Walsh & Joshi corrigendum ·
   Baker & McHale 2013 · the Racing NSW MBL PDFs.

## Cheapest measurements the streams independently nominated

Each was named by its stream as the single highest-value next step. They are read-only, and together
they would settle the largest open questions in this document.

| Measurement | Settles | Nominated by |
|---|---|---|
| **The stored overround distribution** (computed at `RTP:432-442`, aggregated nowhere) | R4-F2's entire negative-EV claim; also C1 | R4 ("the single most valuable cheap measurement in this report") |
| **`Σ winPercentage` per race** | R2-F5(a) — whether every `modelEdge` on a card is biased by a common factor | R2 ("the single cheapest empirical check in the report") |
| **Mean `mlPredictedProb` per card vs observed win rate** | R1-F3 / R2-F4 — confirms or kills the class-imbalance inflation immediately | R1 and R2, independently |
| **Mean CLV and % positive CLV from `tipped_odds` vs `api_sp`** | R3-F6/F7 — a skill signal in ~400 bets instead of ~3,000 | R3 ("the single cheapest high-value change identified") |
| **(odds-decile × edge-decile) ROI surface with per-cell n and CI** | R3-F3 / R3-F12 — whether `modelEdge` is monotone at all | R3 ("worth more than any roadmap feature") |
| **Per-feature non-zero/non-NaN counts over the full 113-column contract** | R5-F4 — the 41 dead features, definitively | R5 ("the missing diagnostic") |
| **`mc_is_flat` firing rate, by field size** | R2-F10(c) / R4-F17 / R5-F7 — SYSTEM_MAP §9 Q10 | R2, R4, R5 |
| **Whether `models/isotonic_calibrator.pkl` exists on the production box** | SYSTEM_MAP §9 Q1 — on which R1-F3, R2-F2 and R4-F6 all depend | R1, R2, R4 ("the cheapest thing the operator could resolve") |
| **Whether `ml_model.py:250-252`'s `TargetEncoder` is fitted fold-wise** | Possible classic target leakage; ~20-minute source read | R5 ("Phase 3 should do it first") |

---

*End of Phase 2 deliverable. 87 findings across 5 streams. No `.py` or `.sql` file was modified by any
agent in this phase; `git status` shows only the untracked `docs/analysis/` directory.*

---

# Citation audit

**Run 2026-07-25, after the five research streams, by a sixth agent whose brief was to refute rather
than agree.** Method: (1) attempt direct fetches to test the standing egress claim; (2) re-check each
load-bearing source's *venue, volume, issue, page range and author list* against publisher metadata,
because that is where a fabricated citation fails first; (3) re-check whether the *quoted number*
actually appears; (4) re-read a sample of the "what STRIDE does today" claims against the real files.
No `.py` or `.sql` file was modified; only this document was edited.

## 1. Egress: the streams' caveat is honest

Direct fetching **is** still blocked. Confirmed 403 at CONNECT this session: `arxiv.org` (both
`/abs/` and `/pdf/`), `gwern.net`, `web.stanford.edu`, `actamachina.com`; `$HTTPS_PROXY/__agentproxy/status`
shows a standing `connect_rejected … policy denial` list including `openreview.net`,
`escholarship.org`, `en.m.wikipedia.org`, `oro.open.ac.uk`, `racingaustralia.horse`. **No stream
lied about the block.**

What the streams did *not* exploit is that `WebSearch` reliably returns publisher-abstract-level
metadata. That is enough to catch a fabricated venue, a wrong volume, a hallucinated author, or a
paper that does not exist — and it is enough to catch a number attached to the wrong paper. **That
check had not been run.** It has now.

## 2. Tally

| | count |
|---|---|
| Sources selected for checking (the most load-bearing) | **24** |
| **Resolved** — the paper exists, and venue/volume/issue/pages/authors match the citation | **24** |
| Failed to resolve / do not exist | **0** |
| Sources whose **claimed effect size was confirmed verbatim** | **11** |
| Sources that resolve but whose **quoted number could not be substantiated** | **9** |
| Sources **misattributed or misused** (right id, wrong paper, or paper says something materially different) | **2** |
| Distinct **claims downgraded to `[unverified]`** by this audit | **11** |
| **Claims about STRIDE's own code corrected** | **8** |
| STRIDE code claims **spot-checked and found correct** | **31** |

**No fabricated source was found.** Every DOI, arXiv id, journal, volume and page range checked
resolved to a real paper with matching authors — including the four post-2025 arXiv ids
(2604.04199, 2606.29720, 2607.06166, 2601.19944-adjacent) that were the strongest a-priori
fabrication candidates. That is a genuinely good result and it should be stated plainly: the five
streams did not invent literature. What they did do is **quote numbers they had not seen**, which
the `[snippet-only]` label warned about and which this pass has now separated into confirmed and
unconfirmed halves.

## 3. Claims now considered unsupported

Each is marked `[unverified]` inline at its finding. **None of these may enter a Phase-4 ticket as a
sizing input.**

1. **Bolton & Chapman's track-level race split** (Aqueduct 43 / Pimlico 52 / Garden State 42 /
   Keystone 32 / Suffolk Downs 31), R1-F1. Sums to 200, but appears in no reachable record.
2. **Benter's pseudo-R² triple 0.1218 / 0.1245 / 0.1396**, R1-F4, R5-F1. The document's own
   "triple-sourced" defence is circular — two of the three sources are STRIDE restating itself.
   *This is the single most load-bearing unverified number in the document*: it is the entire
   quantitative case for the two-stage architecture.
3. **Benter's "500 to 1000 races minimum" data-volume quote**, R1-F4, R1-F18. R1-F18's framing
   ("we need more data is not admissible") rests on it.
4. **Carriero 2025's "all simulated scenarios" and "not always able to be corrected with
   re-calibration"**, R1-F3.
5. **Štrumbelj's "Shin's advantage shrinks as market size grows"**, R2-F13. Struck.
6. **The Kelly ↔ KL ↔ dual-Bregman quotation**, R2-F7 — the arXiv id resolves to a different paper.
7. **MacLean/Ziemba's "30% Kelly cuts an 80% drawdown from 1-in-5 to 1-in-213 while keeping 51% of
   growth"**, R4-F4.
8. **Sun & Boyd's "±15% ⇒ worst-case growth >1.5×"**, R4-F8. A second targeted search returned
   nothing; the stream's own single-search warning is upheld.
9. **Lessmann's "RF 20.26% vs CL 8.84% over 500 races"** and **"1,000 HK races / 12,902 horses"**,
   R5-F6, R1-F13.
10. **Roth 2026's "d_z = 1.38 at 30% duplication"**, R5-F14 (the other three class-level figures
    are now confirmed).
11. **The MBL secondary conditions** — 2pm night meetings, jurisdiction-of-staging rule, WA/NT
    carve-out, the 2014/2016 commencement dates, the ~$250 Top Fluc cap — R3-F15, R4-F12. The
    *primary* figures are now confirmed.

**Sources misused rather than merely unverified — these are the two that matter most:**

- **arXiv 2606.29720** (*The Hidden Cost of Resampling*), cited at R2-F4 to support the claim that
  imbalance correction wrecks calibration. It resolves, but it is a single-author **preprint** on
  five datasets, it does **not** study `scale_pos_weight`/`is_unbalance`/`auto_class_weights`, it
  finds SMOTE's cost small (ECE +0.009), and its headline is that **post-hoc recalibration
  eliminates the damage (ECE −66%)**. It is evidence *for* switching STRIDE's OOF isotonic back on,
  and it should not sit next to R1-F3's "not always correctable" line.
- **arXiv 2607.06166**, cited at R2-F7 as the Kelly↔log-score identity. It is *"When do prophets
  profit in prediction markets?"* — related, but not the source of the quoted passage.

## 4. Claims about STRIDE's own code that were wrong

These matter more than any citation error, because Phase 4 builds on them.

| # | Claim as written | Reality | Where |
|---|---|---|---|
| 1 | **"41 of 113 contract features are identically zero in BOTH training and inference"** | Wrong on the inference half. `mc_api.extract_all_sophisticated_features` (`mc_api.py:5436` → `:908`, `:1556`) populates several of them and feeds them to `RacingMLModel.predict_adjustment` via `calculate_ml_probability_adjustment` (`mc_api.py:6448-6465`) — the 0.55-weighted `ml_adjustment` term at `mc_api.py:7379`. Verified populated: `running_style_score` `:1605`, `is_steam_move` `:2051/:2055`, `empirical_barrier_advantage` `:1153-1155`. The **training-side** half is re-verified and holds, and it is the half that determines behaviour. | R5-F4 |
| 2 | **`retrain_v2.py:219` is the `is_winner` target** | `:219` is `"svi",` inside `FEATURE_COLUMNS`. Target is at `retrain_v2.py:1433`; SQL selects it at `:299`, filters at `:336`. Inherited from SYSTEM_MAP §2 step 7 — **SYSTEM_MAP is wrong too**. | R1-F1, R5-F7 |
| 3 | **`conditional_logit` fits α, β freely; β = 0 proves market contamination** | `conditional_logit.py:134-135` box-constrains **both** parameters to `[0.0, 5.0]`. `β = 0.000` is a **corner solution on the lower bound** — equally consistent with an unconstrained optimum that is negative. Refitting with the bound relaxed is a one-argument precondition for the whole F12 ticket. **Neither R1 nor R5 read the optimiser call.** | R1-F12, R5-F2, §A8 |
| 4 | **`MIN_BETS_REPORTABLE = 200` at `shadow_pl_tracker.py:130`** | It is at `:323`, consumed at `:363`. | R3-F5 |
| 5 | **"Two independent MC scorings differ by ±2.0pp (95%)"** | ±2.0pp is the half-width for *one* scoring. The difference of two has SE 1.02·√2 = 1.45pp ⇒ **±2.84pp**. The finding gets stronger. | R1-F16 |
| 6 | **"Grep for `shin\|power_method\|odds_ratio_method\|devig` returns zero matches"** | Literally false — substring hits in `sectional_times_collector.py` and `nsw_xml_collector.py`. The *conclusion* (no alternative de-vig exists) is re-verified and correct. | R3-F9, R3-F13, §A1 |
| 7 | **`top3_probs` at `RS:1858`** | `:1858` is blank; `top3_probs` is `RS:1861`. | R1-F8 |
| 8 | **`market_efficiency.py:18-23`** | Dict opens at `:17`. Values correct. | R3-F12 |

**One thing the streams under-stated rather than over-stated**, added at R4-F1: the
`0.90 ≤ overround ≤ 1.60` validation at `race_normaliser.py:225` is guarded by
`if odds_count >= 3` at `:223`. A two-quote race is de-vigged with **no overround validation at
all**; a one-quote race gets neither validation nor de-vig. The unbounded case is not the 1.60
ceiling — it is the thin race.

## 5. STRIDE claims re-read and found correct

Spot-checked against source and confirmed exact, including every quoted constant: the six
`STRATEGIES` and `STAKE = 100` (`backtest_v2_metro.py:157-166`); `norm_prob` (`:174-176`) and the
vig-inclusive `implied = 1.0/sp; edge = prob − implied` (`:215-217`) — **R4-F2's "the live gate is
not the validated band" is correct**; all six strategy rows of `examples/backtest_summary.json`,
and R3-F1/F2/F3/F5's arithmetic on them reproduces to the decimal (σ = 3.396, SE = 0.285,
t = 0.432, CI [−43.5%, +68.2%], the 81-bet residual cell at +40.1%, n = 3,038 for t = 2);
`calculate_overround` incl. `valid < 2 → 1.0` (`RTP:432-442`); the full `mw` ladder and its
`RTP:676-678` Kelly-audit comment (`:679-697`); `mc_spread < 6.0` (`:703-705`); the bet gates
`odds > 15`, 4/2.5/3 and 30/15/10 (`:1812-1825`); `compute_confidence`'s `ev > 0 and edge > 1.0 →
high` (`:965`); `compute_staking` (`:1007-1015`); `mlPredictedProb` (`:2323`); the isotonic step
(`:657-661`) and the `ml_w` blend (`:667-668`); the seed (`:2340`); `_llm_top_pick` bypass (`:883`);
`market_injection = 0` (`:2697-2700`); `store_final_probs_in_audit` (`:1582`);
`scale_pos_weight: 9 / is_unbalance / auto_class_weights` (`retrain_v2.py:774/791/802`); the
"deliberately NOT applied" comment at **`ml_model.py:565`** — R2's correction of SYSTEM_MAP's `:566`
is right; the linear ensemble at `ml_model.py:594`; the unconditional `.fillna(0)` at
`ml_model.py:214-218` against `retrain_v2.py:680-684`'s deliberate NaN preservation — **R5-F5 is
fully verified**; the SP-as-`market_odds` chain (`retrain_v2.py:142-144`, `:557-563`, `:574`) —
**R5-F2 is fully verified and is the strongest source-read finding in the document**;
`calibration_model.py:30-32` and R2-F2's exhaustive grep (`ProbabilityCalibrator.fit()` has **zero
callers**; four sites total; nothing produces `models/isotonic_calibrator.pkl`) — **re-run and
confirmed exactly**; `walk_forward_backtest.py:100-117`'s ten equal-width bins;
`shadow_pl_tracker.py:283/290/299` settling at SP; the zero-match CLV grep;
`glicko2_elo.py` having zero importers; `portfolio_risk.py:235`'s variance computed at `q = 1/odds`
— **R4-F7's catch is correct and sharp**; `RS:35-41`, `:129-134`, `:145-153`; `kelly_stake`'s
inoperative cap (`RS:309-320`, binds only above 20% full Kelly); `mc_api.py:7483`'s `kellyStake`
and `:7636-7637`'s un-de-vigged edge; `RS:1855-1856` Gumbel-max; `mc_selection_score`'s
0.30/0.08/0.30/0.12/0.12/0.08 weights (`RS:1927-1933`); `RTP:774/776`'s 0.65/0.35 vs 0.50/0.50.

## 6. Verdict

**Confidence in the source layer: moderate, and materially higher than the document's own framing
implied.** The chief risk going in was fabrication; there is none. The residual risk is
**quoted-number drift** — nine of twenty-four checked sources resolve but do not visibly carry the
number attached to them, and two are used to support something adjacent to what they say. The
`[snippet-only]` labelling was honest but insufficiently granular: it conflated "I saw the abstract"
with "I saw the result", and those have very different consequences downstream.

**Confidence in the STRIDE-behaviour layer: high, with one important exception.** Thirty-one of
thirty-nine sampled code claims were exactly right including line anchors — an unusually good hit
rate for a document of this length, and the `[derived — this session]` arithmetic reproduces to the
decimal. But **R5-F4's headline is wrong in a way that would have misdirected Phase 4**, and
**R1-F12/R5-F2's β = 0 diagnosis rests on an optimiser bound nobody read**. Both are cases of a
stream stopping one call-site short.

**Practical instruction for Phase 3.** The three strongest things in this document are, in order:
(i) **R5-F2** — training fits SP, inference serves the racecard price; fully source-verified, no
literature dependency. (ii) **R3-F1/F2/F3 + R4-F2** — the founding ROI number has t = 0.43, ROI is
non-monotonic in edge, and the live gate thresholds a different quantity than the validated band;
all arithmetic reproduces from committed data. (iii) **R2-F2** — the one calibration layer that can
fire has no fitting code. **None of those three depends on a single external citation**, and none
was weakened by this audit. Build there. Everything that needs Benter's R², Sun & Boyd's 1.5×, or
MacLean's 1-in-213 to justify its size should wait for a real fetch.

### Additions to the re-verification queue (§ Standing corrections)

10. **Refit `conditional_logit` with `bounds=[(0.0, 5.0), (-5.0, 5.0)]` and report β's sign** before
    any work on R1-F12 / R5-F2 / §A8. One argument; settles whether β = 0 is contamination or a
    negative optimum.
11. **Re-run R5-F4's feature diff over BOTH inference paths** — `RTP:2258-2308` *and*
    `mc_api.extract_all_sophisticated_features` — before writing any "wire the dead features"
    ticket.
12. **Re-quote Benter's R² triple from the primary chapter, or delete it from `docs/12` and
    `conditional_logit.py:21-23`.** It is the most-cited number in STRIDE's design rationale and
    nobody in six agent-passes has seen it in the source.







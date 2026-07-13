# Hit-Rate Research & Improvement Roadmap

Goal: raise the probability that the horse STRIDE selects actually **wins** —
top-pick hit rate — without conflicting with the system's value/EV framework.
This document records the external research, the audit of STRIDE against it,
what was implemented, and the prioritized roadmap for what remains.

Related docs: [Scoring & output](09-scoring-and-output.md) ·
[ML training & calibration](05-ml-training-and-calibration.md) ·
[Backtesting & learning](10-backtesting-and-learning.md)

---

## 1. Framing: hit rate and value are different targets — sharpen the model and you serve both

The 2026-03→04 backtest shows the tension precisely: the model's top pick won
**33.7%** of races but lost 4.2% at the prices it was offered, while the
selective edge filter won only 9.9% of its bets at **+12.3% ROI**. Meanwhile
the long-run Australian baseline is that the **market favourite wins ~34.9%**
of races (second favourite ~19.8%, third ~13.5%).

Read together: STRIDE's top pick currently hits at roughly the *favourite
baseline* — the model adds little top-1 skill beyond the market. The honest
route to a higher hit rate is therefore **not** looser filters; it is a more
discriminative within-race probability model. That also mechanically improves
edge detection, so nothing here conflicts with the EV pillar — the BET gates,
convergence layer and staking are untouched.

## 2. What the research says

Five findings, in order of relevance:

1. **Winning is relative — model the race, not the runner.** The canonical
   form for race win probabilities is the conditional/multinomial logit
   estimated *within race* (Bolton & Chapman 1986, Management Science). The
   conditional logit "maintains connections between individual runners in a
   given race and can extract information from the composition of a race" —
   precisely what an independent binary classifier cannot do.
2. **The strongest validated architecture is a two-stage blend of model and
   market in log space.** Benter (1994) fitted his fundamental model first,
   then a *second* conditional logit over `ln(model prob)` and
   `ln(public prob)`. The combined model outperformed **both** inputs
   (R² 0.1396 vs 0.1245 fundamental-only vs 0.1218 market-only) and became
   the basis of the most successful betting operation on record.
3. **The market is the single strongest predictor, and its information is
   positional.** AU long-run samples: favourite 34.9%, 2nd favourite 19.8%,
   3rd 13.5%. A model that sees only a runner's raw price — but not where
   that price sits *within the field* — has to re-derive this ladder from a
   feature that isn't comparable across field sizes and overrounds.
4. **Learning-to-rank beats pointwise classification for picking race
   winners.** Recent work applying LambdaMART-family rankers
   (XGBoost/LightGBM/CatBoost rankers grouped by race) found pairwise ranking
   superior to pointwise learning for race outcome prediction, with CatBoost
   Ranker strongest; classic results (Lessmann, Sung & Johnson) similarly
   showed race-aware model forms outperform naive ones.
5. **Sectional-adjusted ratings out-predict raw ratings** — already a STRIDE
   strength (λ decay, SVI, z-scores, five quant engines), so the marginal
   gain lies in the areas above, not in more sectional engineering.

Sources: [Bolton & Chapman 1986](https://pubsonline.informs.org/doi/abs/10.1287/mnsc.32.8.1040)
([PDF](https://gwern.net/doc/statistics/decision/1986-bolton.pdf)) ·
[Benter 1994 (Semantic Scholar)](https://www.semanticscholar.org/paper/Computer-Based-Horse-Race-Handicapping-and-Wagering-Benter/2ea3ed4fa5ea9645614d76dd0a79201740949566)
· [annotated Benter analysis](https://actamachina.com/posts/annotated-benter-paper) ·
[two-stage replication notes](https://github.com/chris-alex-p/german-horse-racing/blob/main/notebooks/analysis_benter_methods.md)
· [Lessmann/Sung/Johnson SVM classification](https://www.sciencedirect.com/science/article/abs/pii/S0377221708003007)
· [Lessmann et al., alternative methods / random forest](https://www.sciencedirect.com/science/article/abs/pii/S0169207009002143)
· [LTR for horse race rank prediction (2024)](https://koreascience.kr/article/JAKO202414143309228.page)
· [AU favourite statistics](https://www.kruzey.com.au/australian-horse-racing-statistics-how-often-does-the-favourite-win/)
· [odds-rank win statistics](https://www.statfreaks.com.au/horse-racing-winning-odds-statistics-rank-analysis-7283/)
· [sectional-adjusted ratings analysis](https://www.drawbias.com/sectionaltimes2.html)

## 3. Audit: STRIDE vs the research

| Research finding | STRIDE today | Gap |
|---|---|---|
| Model within-race (conditional logit) | XGB/LGB/CatBoost are **pointwise binary** classifiers; field normalization happens only after the fact (mc_api renormalizes; backtests renormalize) | **Largest gap** — the ensemble never learns relative comparisons |
| Two-stage log-space model+market blend | A **linear** per-runner blend with hand-tuned odds-band weights (`calibrate_and_score`, mw 0.80→0.30) | Heuristic approximation of Benter's fitted blend |
| Positional market features | `market_odds`, steam/drift in the contract; `odds_rank`/`fair_implied_prob` computed in mc_api's adjustment layer but **absent from the trained contract** | Ensemble blind to the favourite ladder |
| Learning-to-rank | Not present | Roadmap |
| Sectional-adjusted ratings | Extensive (Phase 2 primitives, five quant engines) | Already strong |
| Race selectivity (bet where the top pick is most reliable) | **Wired (§4c):** predictability attached to every race entry; best-bets ordering behind `STRIDE_PREDICTABILITY_GATE` | Done — validate with shadow tracking |

## 4. Implemented in this change

Both changes follow the repo's rules: default behaviour is unchanged until
you opt in (env flag) or retrain (contract features), and each ships with a
runnable self-test.

### 4a. Phase-5 features: within-race relative market position (retrain-gated)

`server/python/relative_market.py` adds three features to the 110→113 column
contract, with names and semantics **identical to what
`mc_api.extract_ml_features` already computes** for its adjustment layer — so
both inference paths get train/serve parity and mc_api needed no edits:

- `fair_implied_prob` — overround-corrected implied win % within the field
- `odds_rank` — 1 = favourite; ties share the lower rank
- `odds_rank_pct` — rank / field size

Wired into `retrain_v2.build_feature_matrix` (grouped by the same
race key as the pace features) and the `run_tips_pipeline` ML block. Existing
model artifacts are unaffected — they carry their own saved `feature_columns`
list; the new features activate at the **next retrain**, where the existing
walk-forward CV output and the ablation harness measure their contribution.
As part of this, `ml_model.save()` now persists `feature_columns` (previously
only retrain_v2 artifacts did), so no future contract growth can
shape-mismatch a reloaded artifact.

### 4b. Conditional-logit blend — Benter's second stage (opt-in)

`server/python/conditional_logit.py` implements
`P_i ∝ exp(α·ln m_i + β·ln q_i)` per race, fitted by maximum likelihood on
your stored predictions:

```bash
python conditional_logit.py            # synthetic self-test (no DB)
python conditional_logit.py --fit      # fit α, β from training_view_v2;
                                       # prints holdout hit-rate/log-loss for
                                       # model-only vs market-only vs blend
STRIDE_CL_BLEND=true                   # enable in run_tips_pipeline
```

**Where the hook applies (train/serve consistency):** the fit consumes
`training_view_v2.predicted_win_prob`, which for full fields comes from
`prediction_audit` — logged by mc_api **before** the wrapper's isotonic /
ML-blend / market-anchor stages. The pipeline hook therefore applies the
transform at exactly that point: on entry to `calibrate_and_score`, to the
incoming MC `winPercentage` (replacing the early isotonic step when active).
The ML blend and the market anchor then run unchanged — so even a β=0
artifact keeps prices market-tethered (verified: MC 34.0% → CL 46.0% →
anchored 41.1% on the test fixture). Artifacts carry a `stage` tag and the
MC-stage hook refuses a mismatched artifact. With the flag off — or on but
with no fitted model — behaviour is verified **byte-identical** to before.

Synthetic self-test result (1,200 races, market sharper than model, holdout
400 races): model-only top-pick hit 34.5%, market-only 40.7%, **blend 42.0%**
with lower log-loss than both — the Benter phenomenon reproduced end-to-end.
The `--fit` report gives you the same three-way comparison on *your* data
before you flip the flag.

### 4c. Implemented in the follow-up pass (selectivity, instrumentation, CI)

- **Race-predictability selectivity** — `predictability_meta_model` is now
  wired into the tips output: every race entry carries a `predictability`
  block (score, category, confidence modifier, key factors), computed from
  **pre-race information only** (current market and card facts — leak-free
  by construction; heuristic fallback is deterministic without a fitted
  meta-model). With `STRIDE_PREDICTABILITY_GATE=true`, best-bets ordering
  weights the selection score by the race's confidence modifier (0.5–1.2)
  so best bets prefer races where the top pick is most likely to be right.
  Ordering only — BET/NO_BET decisions, edges and stakes are never touched;
  default off preserves the historical order exactly.
- **Final-probability instrumentation** — `store_final_probs_in_audit`
  records the published end-of-pipeline win % for *every* runner into
  `prediction_audit.final_win_prob` after each card (self-healing column;
  `migrations/final_prob_audit.sql`). This is the input the future
  final-stage blend fit needs (`conditional_logit.py --fit --stage final`,
  artifact kept separate and refused by the MC-stage hook).
- **CI** — `.github/workflows/ci.yml` compiles the whole repository and runs
  the four module self-tests on every push: the regression net that would
  have caught the silent stacking breakage years earlier.

### Verification performed

- `relative_market.py` and `conditional_logit.py` self-tests pass.
- `retrain_v2.build_feature_matrix` produces the 113-column matrix with
  correct per-race ranks and shares summing to 100 (synthetic race groups).
- `calibrate_and_score` verified byte-identical with the flag unset, and with
  the flag set but no fitted model; with a fitted model the blend engages,
  win% still sums to ~100 per field and edge = win − market holds.
- Full v1 train→save→load→predict roundtrip on the widened contract passes;
  the artifact now stores its 113 feature columns.

## 5. Roadmap (researched, not yet implemented)

Ordered by expected hit-rate return per unit of risk:

1. **Fit and enable the CL blend on real data** — run
   `conditional_logit.py --fit`, inspect the holdout table, then A/B a race
   day with `--output-suffix clblend --skip-db-store` against the canonical
   run before enabling in production. Three transports are supported: direct
   Postgres, automatic Neon SQL-over-HTTPS fallback (for networks that block
   port 5432), and `--csv <export.csv>` for a SQL-editor export. The
   `fit-conditional-logit` GitHub Action runs the fit on a hosted runner
   using the `DATABASE_URL` repository secret and prints the holdout report
   in the run log (never pass the connection string as a workflow input —
   the repository is public).

   **First real fit (2026-07-13, 1,227 races, 245-race temporal holdout):**
   model-only top-pick hit 42.9% / log-loss 1.7039; market-only (SP
   favourite) 40.4% / 1.7499; fitted blend α=1.296, β=0.000 → hit 42.9% /
   log-loss 1.6866. Interpretation: (a) the fitted quantity is the stored
   **MC-stage** probability (what mc_api logs to `prediction_audit`) — in
   the live window its top pick beats the SP favourite 42.9% vs 40.4%, and
   it is too flat at the top: the α≈1.3 race-conditional sharpening improves
   log-loss ~1% with the top pick unchanged (consistent with the README
   calibration table's under-predicted 0.20–0.30 bin). (b) With the hook
   now applied at the matching MC stage (see §4b), **this artifact is safe
   to enable**: it acts as a race-softmax sharpener of the MC probability
   and the downstream market anchor is untouched. Re-run the fit
   (`--stage mc`) and A/B a card before switching it on in production.
   (c) β=0 says only that the SP market adds nothing *conditional on the
   MC-stage probability, which already embeds market information* — the
   true independent market weight needs final-stage inputs, which the
   pipeline now records (`prediction_audit.final_win_prob`, §4c); once a
   few weeks accumulate, fit with `--stage final` for that estimate.
2. **Retrain with Phase-5 features** — the next `retrain_v2.py` run picks
   them up; check the printed feature importances and walk-forward AUC, and
   extend `run_ablation` to toggle the Phase-5 trio if a clean read is wanted.
3. **Race-selectivity gate ("bet the reliable races")** — **done, see §4c**
   (fields always on, ordering influence behind `STRIDE_PREDICTABILITY_GATE`).
   Remaining: once a few weeks of results accumulate, use shadow tracking to
   measure best-bet hit rate with the gate on vs off, and consider fitting
   the meta-model itself (`fav_won` target) to replace the heuristic.
4. **LambdaRank ensemble member** — add an `LGBMRanker`
   (`objective=lambdarank`, grouped by race, GroupKFold walk-forward) as a
   fourth model whose score enters the ensemble as a within-race ranking
   signal. Research (2024) finds pairwise rankers beat pointwise
   classification for this exact task; CatBoost's ranker is the strongest
   single reported.
5. **Capture a close-to-jump odds snapshot** — the current market pillar uses
   overnight/8am prices; late money is the sharpest public information. A
   T-5-minute snapshot upgrading `market_odds`/Phase-5 features at scoring
   time would strengthen the market input the CL blend leans on.
6. **Close the tipster-accuracy feedback loop** — `source_accuracy` is
   recorded but never read back into panel weights
   ([Consensus & market §2.4](08-consensus-and-market.md)).
7. **Finish `weather_api.py`** — going misclassification is a documented
   failure category in the 21-day autopsy; the stub already defines the
   uncertainty-widening interface.

## 6. Validation protocol

Every item above can be proven or rejected with harnesses already in the
repo — no new evaluation code needed:

- `retrain_v2.py` walk-forward CV + ablation → feature-level evidence.
- `backtest_v2_metro.py` confidence bands + `Top Pick` strategy → top-pick
  hit rate before/after.
- `walk_forward_backtest.py` → leakage-safe AUC/ECE deltas with CIs.
- `shadow_pl_tracker.py` → live-shadow comparison by convergence tier.
- `validate_tips.py` → output-contract invariants hold after any change.

The bar for enabling anything by default: it must raise top-pick hit rate on
the holdout **without** degrading the calibration Brier or the Value-Edge
band's ROI — hit rate paid for with calibration is how systems drift into
tipping favourites at a loss.

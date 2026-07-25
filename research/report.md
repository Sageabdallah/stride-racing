# Australian Horse Racing: Landscape, Research, and System Comparison
**Prepared for the stride-racing project · 2026-07-25**

*Method note: findings below were produced by a multi-agent research harness (5 search
angles, 22 sources fetched, 108 claims extracted, top 25 adversarially verified with
3 independent votes each). 15 claims survived verification, 2 were refuted, and 8
went unverified because the verification agents hit a session limit — those are
explicitly labelled **[unverified]** and should be treated as credible but unconfirmed.*

---

## 1. Current state of Australian turf racing

**The wagering pool that funds the industry is shrinking.** The one industry claim
that survived full verification: Racing Queensland's FY2024-25 annual report shows
wagering turnover of **$5.6 billion, down 9.3% from $6.1 billion** the prior year
([RQ annual report](https://www.racingqueensland.com.au/news/2025/09-september/rq-releases-2024-25-annual-report), verified 3-0).
Industry press fetched during research (The Straight, TTR AusNZ) frames this as a
national funding squeeze — declining turnover post the COVID-era wagering boom,
pressure on prize money, and the Rosehill sale debate as a symptom **[unverified —
these sources were fetched but their specific figures were not in the verified set]**.

**Data access is tightening, and it is undocumented.** Your own repo diagnosed
Racing Queensland's site moving behind a Cloudflare interactive challenge (docs/12).
Notably, RQ's public annual-report communication **makes no mention of data-access
policy, bots, or anti-scraping measures** (verified 3-0) — the wall is real but
publicly unacknowledged, which means there is no published route to ask for an
exemption and no signal it will be reversed. Practical implication: treat scraping
as a structurally declining data channel and commercial/official feeds as the
durable path (see §4, recommendation 2).

**Market structure favours the modeller in one specific way.** The Australian tote
exhibits a pronounced, quantified favourite-longshot bias (§2), and late money
demonstrably improves but does **not** fully correct prices — meaning a calibrated
model retains genuine room to add value over final odds, but far more room over
early odds (which is what your pipeline currently consumes).

---

## 2. Recent academic research

### 2.1 Late odds are the sharpest public signal (strongest verified cluster)

- **Japan (JRA), 894,127 runners, 2004–2023** ([arXiv 2509.14645](https://arxiv.org/html/2509.14645), verified 3-0):
  horses whose odds *shorten in the final five minutes* earn significantly higher
  realized returns than horses with identical final odds (coefficient on
  final-5-minute odds-change rate −0.3386, SE 0.0392). The effect size is large: at
  median odds, a 10% final-five-minute move implies a return difference **~14×
  larger** than a 10% cross-sectional difference in final odds. (A stronger claim —
  that *only* the last 5 minutes matter — was refuted 1-2; earlier moves carry some
  information too.)
- **Australia, all 14,854 races of the 2006 season** ([ECU working paper](https://economics.ecu.edu/wp-content/pv-uploads/sites/165/2019/07/ECU1202.pdf), verified 3-0):
  late money is "smart money." The late pool-share ratio (final pool % ÷ pool % at
  last tote click) predicts net returns with coefficient 4.124 (z = 9.79). Late
  money moves prices toward true probabilities **but final prices remain wrong in 5
  of 10 favourite ranks** — the market stays beatable at the close.
- **US parimutuel pools** ([Wharton, "The Favorite-Longshot Midas"](https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2018/08/The-Favorite-Longshot-Midas.pdf), verified 3-0):
  informed, often algorithmic bettors concentrate at the close of the window, when
  odds are most informative.

### 2.2 Favourite-longshot bias, quantified for Australia

The same AU study (verified 3-0): net-return regression coefficient on odds −0.621
(z = −11.29); rates of return decline monotonically from **−12.35% on top
favourites to −41.55% on 9th favourites**. The US study agrees in shape ($1 on an
even-money favourite loses ~15¢ vs 29¢ at 20/1 and 47¢ at 50/1). Consequence:
longshot value bets need a much larger modelled edge to clear the bias; favourite-
side value clears sooner.

### 2.3 Ranking objectives vs pointwise classification

A 2024 peer-reviewed Korean study on Seoul racing ([KJAS](https://www.kjas.or.kr/journal/view.html?doi=10.5351/KJAS.2024.37.2.239), verified 3-0)
found **pair-wise learning-to-rank generally outperforms point-wise approaches** for
race rank prediction, with **CatBoost Ranker the single best model tested**
(verified 2-1 — one dissent). This bears directly on your LambdaRank verdict (§3).

### 2.4 Conditional logit remains the market-blend workhorse

A regularized conditional-logit-with-frailty model on Hong Kong racing
([ResearchGate](https://www.researchgate.net/publication/343894424_PREDICTING_HORSE_RACE_WINNERS_THROUGH_A_REGULARIZED_CONDITIONAL_LOGISTIC_REGRESSION_WITH_FRAILTY), verified 3-0)
extends the same CL family your blend uses, with one instructive design choice: the
LASSO regularization was tuned to **maximize betting profit rather than likelihood**
(verified 3-0) — a decision-aligned objective. Cautionary note: the paper's headline
**36.73% ROI claim was refuted 0-3** by the verification panel — published ROI
claims in this literature routinely fail scrutiny; trust mechanisms, not headline
returns.

### 2.5 Calibration, staking, and metric choice **[unverified cluster — session-capped]**

These claims were extracted from credible peer-reviewed/preprint sources but their
verification votes never ran:

- Selecting a betting model by **calibration (classwise-ECE) rather than accuracy**
  produced +34.69% average ROI vs −35.17% for accuracy-selected models on NBA
  moneyline markets ([Machine Learning with Applications, 2024](https://www.sciencedirect.com/science/article/pii/S266682702400015X)).
- Under eighth-Kelly staking, the calibration-selected model returned +36.93% while
  an accuracy-selected model only ~0.8pp worse in ECE **lost 75.9% of bankroll** —
  Kelly amplifies miscalibration into ruin (same source).
- Accuracy and hit-rate are near-useless discriminators of profitability: two
  systems with near-identical accuracy (64.62% vs 64.27%) had wildly divergent ROI
  (same source).
- Staking strategy has a first-order effect on profit, on par with model quality
  ([arXiv 2107.08827](https://arxiv.org/abs/2107.08827)).
- Stride/biomechanical parameters carry information beyond velocity alone
  ([Equine Veterinary Journal](https://pubmed.ncbi.nlm.nih.gov/33098592/)).

### 2.6 Data availability (commercial)

**Punting Form sells 200m-increment sectional times for all runners covering ~85%
of AU TAB meetings, with history to October 2012** ([sectionaltimes.com.au](https://sectionaltimes.com.au/), verified 2-0).
Whether a QLD-specific pack exists went unverified (1-0 with abstentions).

---

## 3. Comparison with the stride-racing docs

### Where the system already matches or leads the literature

| Literature finding | Your implementation | Verdict |
|---|---|---|
| Leak-free temporal evaluation is non-negotiable | Walk-forward CV with 14-day purge (docs/05), 7-day purge backtest with leakage assert (docs/10), three-tier leakage catalogue (docs/04 §4) | **Ahead of most published work** |
| Favourite-longshot bias must be corrected | `mc_recalibration.py` targets FLB explicitly; value-play band capped at $15 (docs/09) | Aligned; the AU numbers (§2.2) validate the $4–$15 band — do not loosen it toward longshots |
| Calibration is central to betting systems | Five calibration layers incl. OOF isotonic that "never sees data the model was tuned on" (docs/05 §4) | Aligned in machinery; **misaligned in selection criteria** (below) |
| Conditional logit for market blending | CL blend built, held pending clean-provenance refit (docs/12) | Aligned — and the hold is exactly the discipline the refuted 36.73%-ROI claim argues for |
| Market steam/drift carries signal | 16 market features incl. `is_steam_move`, `late_move_indicator` (docs/04) | Partially aligned — see gap 1 |

### Where the system lags or diverges

**Gap 1 — your "late" odds are not late.** The convergence pillar snapshots odds at
~12:30 AM and ~8 AM (docs/08 §3.1), and the market pillar uses overnight/8am prices
(docs/12 §5.5). The verified literature is unambiguous that the sharpest information
arrives in the **final minutes** — with a JRA effect size ~14× the cross-sectional
odds signal. Your roadmap already lists a T-5-minute snapshot (§5.5) but ranks it
fifth; the evidence says it should be first.

**Gap 2 — model promotion is gated on AUC, not calibration or ROI.** The
ShadowTester promotes on shadow AUC > primary + 0.02 (docs/05 §7), and ablations are
read in AUC deltas. The unverified-but-credible calibration cluster (§2.5) argues
near-identical AUC/accuracy can hide catastrophic ROI differences — especially under
Kelly staking, which your system uses. Your own Phase-5 episode ("adds importance
but no AUC") was decided on AUC alone; an ECE/simulated-ROI reading might have
agreed, but the metric suite never asked.

**Gap 3 — the LambdaRank verdict may be implementation-specific.** Your H2H was
methodologically sound (identical races, stored-prob-covered fields) and the verdict
"evidence-only, no wiring" stands on that data. But the Seoul study (verified 3-0)
found pairwise rankers *generally* beat pointwise, with **CatBoost Ranker** — not
LightGBM LambdaRank — the top performer (2-1). The literature contradicts a blanket
"ranking doesn't work here" reading; it supports your narrower documented conclusion
("re-test after new information lands") and suggests the retest should try CatBoost
Ranker.

**Gap 4 — sectional coverage, not sectional value, is the open question.** Your
ablation found Phase-2 sectionals at −0.0005 AUC, which docs/12 itself attributes
plausibly to ~47% coverage. Punting Form's verified ~85% coverage back to 2012 makes
that hypothesis cheaply testable — and legitimately routes around the QLD Cloudflare
wall, consistent with your own "official access, no bypass" stance.

**What the literature confirms about your failures:** the Phase-5 verdict (odds
re-encodings are redundant) is exactly what market-efficiency theory predicts —
transforms of prices add no *information*; only genuinely new signals (late money,
new sensors) can. Your instinct to redirect feature work toward §5.5 is the
literature's recommendation too.

---

## 4. Actionable recommendations (ranked by expected return per unit effort/risk)

**1. Build the T-5-minute odds snapshot now (roadmap §5.5, promoted to #1).**
*Change:* extend `odds_movement.py` to capture a third snapshot ~5 minutes before
each race's jump; add `final_move_pct` (and pool-share ratio if tote data allows) as
scoring-time features. *Evidence:* the strongest verified cluster in this report —
JRA coefficient −0.3386 with ~14× economic magnitude; AU Crafts-ratio z = 9.79.
*Expected impact:* the single largest untapped signal available to the system;
directly strengthens the market input the CL blend needs. *Leak-free validation:*
this data can only be collected **prospectively** — never backfill "late odds" from
a vendor's final-odds field into historical training rows (final odds embed the
outcome-adjacent information you're trying to predict ahead of). Accumulate weeks of
timestamped snapshots, then run the retrain ablation with a late-odds arm and shadow-
track best-bet hit rate. The snapshot timestamp must be stored and asserted < jump time.

**2. Purchase Punting Form sectional data and re-run the sectional ablation.**
*Change:* one-off import (all runners, 200m increments, to Oct 2012) through the
existing `sectional_times` path with the temporal LATERAL join (docs/04 Tier 1).
*Evidence:* coverage ~85% verified 2-0; your own docs attribute the null sectional
result to 47% coverage. *Expected impact:* either sectionals finally show AUC/ROI
lift at real coverage, or you retire a whole feature block with confidence; also
fills the QLD hole legitimately. *Leak-free validation:* existing `race_date <`
join pattern + retrain ablation arm; no new leakage surface.

**3. Add calibration and simulated-ROI gates to model promotion.**
*Change:* extend ShadowTester and the ablation report to print ECE/Brier deltas and
simulated ROI under the *live* betting gate, alongside AUC; promotion requires
no-worse calibration, not just +0.02 AUC. *Evidence:* §2.5 **[unverified]** but
consistent with your own Phase-5 experience; cheap and risk-free. *Expected impact:*
prevents promoting a model that looks better on AUC but bets worse — the exact
failure mode Kelly staking amplifies. *Validation:* `walk_forward_backtest.py`
already computes ECE and ROI@thresholds — this is wiring, not new evaluation code.

**4. Cap Kelly at an explicit fraction and stress-test staking.**
*Change:* if not already fractional, cap stakes at ⅛–¼ Kelly; add a stress-test that
perturbs probabilities by your observed calibration error and reports bankroll
paths. *Evidence:* §2.5 **[unverified]** (−75.9% bankroll from 0.8pp of ECE under
eighth-Kelly) plus standard theory. *Expected impact:* ruin-risk insurance; costs
almost nothing. *Validation:* shadow P&L tracker already records level-stakes —
add a Kelly-path column.

**5. Re-test the ranker as CatBoost Ranker after recommendation 1 lands.**
*Change:* re-run the existing rank harness with a CatBoost Ranker arm, on
late-odds-enriched features, judged by the same three-way H2H criterion. *Evidence:*
Seoul study verified 3-0/2-1; your own criterion already defined. *Expected impact:*
moderate — the H2H bar (beat 39.7%) is high, but the literature says the objective
class deserves a second look with a better implementation and new information.
*Leak-free:* harness unchanged.

**6. When the CL blend refits on clean MC-stage rows, consider a profit-aligned
objective.** *Change:* keep the current hold (it is correct); at refit time, test
profit-weighted regularization à la the frailty paper alongside plain likelihood.
*Evidence:* method verified 3-0; its ROI headline refuted 0-3 — adopt the idea,
not the promised returns. *Validation:* the existing holdout table + one-day A/B
with `--skip-db-store`, exactly as your runbook prescribes.

### Standing leakage cautions for all of the above
- Late-odds features: prospective collection only; assert snapshot time < jump time.
- Any historical enrichment must use docs/04 Tier-1 patterns; Tier-2 modules
  (barrier-bias tables, `sectional_quant` engines, track profiler) must not be used
  to backfill training rows.
- Every new feature goes through the retrain ablation and the 7-day-purge
  walk-forward backtest before any production wiring — no exceptions, per your own
  validation protocol (docs/12 §6).

---

## Report limitations

Verification was truncated by a session cap: the calibration/Kelly cluster (§2.5),
the biomechanics claims, and Betfair-efficiency claims are unverified; the industry
section rests on one fully verified primary source plus unverified press. Two claims
were actively refuted and are reported as such. The verification pass can be resumed
after the cap resets to close these gaps.

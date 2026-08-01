# Feature provenance & liveness — what the model actually sees

*Audit of all 113 declared `FEATURE_COLUMNS` (2026-07-28, code = origin/main,
artifact = `racing_ensemble_v2.pkl` v2 trained 2026-04-15). Mechanical scan:
`server/python/feature_liveness_audit.py`; machine-readable results:
[`feature_liveness_report.json`](feature_liveness_report.json). Every static
verdict below was cross-checked against the artifact's stored
`feature_importance` — **zero contradictions**.*

## Headline findings

### 1. A quarter of the live model is silently disabled in production

**15 features carrying 25.45% of the model's total importance mass are
assigned at train time but never assigned at serve time.** `ml_model.
prepare_features` silently fills the missing columns with **constant 0**, so
every split the model learned on them fires on a fabricated value, every race,
every day.

Every train derivation below was traced to its inputs and is **leak-safe to
plumb at serve** — all inputs are knowable pre-race:

| feature | importance | train derivation (retrain_v2.py unless noted) | serve inputs needed |
|---|---|---|---|
| closer_advantage | 0.0563 | :639 via `_compute_pace_features` — field composition (each runner's pre-race form_string/barrier) | racecard field (already loaded) |
| pace_pressure_score | 0.0437 | :637 same — share of on-pace styles in the field | racecard field |
| field_size_context | 0.0396 | :646 `field_size/16` clipped — one line | field_size |
| leader_advantage | 0.0302 | :638 same pace-map computation | racecard field |
| z_800m | 0.0240 | view `prior_z_800m` — prior-start sectionals, `st.race_date < r.race_date` | prior z (enrich_with_db already queries it) |
| trip_cost_seconds | 0.0128 | view `prior_trip_cost_seconds` | prior sectionals |
| z_600m | 0.0074 | view `prior_z_600m` | prior sectionals |
| barrier_relevance_score | 0.0063 | :642 distance bucketing — one line | distance |
| market_efficiency_flag | 0.0058 | :649 **class_level bucketing** (misleading name — nothing market-derived) | class_level |
| rsi | 0.0058 | view `prior_rsi` | prior sectionals |
| lambda_decay | 0.0058 | view `prior_lambda_decay` | prior sectionals |
| svi | 0.0058 | view `prior_svi` | prior sectionals |
| z_400m | 0.0057 | view `prior_z_400m` | prior sectionals |
| z_200m | 0.0054 | view `prior_z_200m` | prior sectionals |
| ground_suitability | 0.0000 | view ← `selections.ground_suitability` (also zero-importance — dead weight both ways) | n/a |

Five of the fifteen are one-line formulas already written in retrain_v2 —
mirroring them in the serve builder is mechanical. The pace trio needs the
field-composition classifier (`_classify_running_style_from_form`) exposed to
serve, and the sectional set needs `enrich_with_db`'s existing prior-z query
moved before scoring. Nothing requires information unavailable at tip time.

Two aggravating details:

- **It is worse than "missing".** The sectional z-features are in
  `NAN_PRESERVE` at train — the model learned NaN = "no sectional history".
  Serve feeds **0**, which is a *real* z-score (exactly average), not
  "unknown". The model is not degrading gracefully; it is being lied to.
- **The data already exists at serve time.** `enrich_with_db`
  (run_tips_pipeline.py:1037-1050) already queries prior-start z-scores — but
  stores them as display-only keys (`prior_z200`/`prior_z400`) and runs at
  :2491, *after* ML scoring at :2339.

**Fix path (no retrain required):** plumb these 15 into the serve-time feature
dict *before* `prepare_features`, honouring the NaN contract (unknown → NaN,
never 0). This belongs in roi/03's shared `serve_features.py` builder — the
exact module built to kill train/serve skew. Expected effect: the live model
finally scores with the ~25% of its signal it already paid to learn.

### 2. The dominant feature family is SP-contaminated at train

`market_odds` alone carries **21.8%** of model importance — the single biggest
feature — and at train time it is mostly the **starting price of the race
being predicted**, which is unknowable at tip time:

- `retrain_v2.py:144` — `VIEW_TO_FEATURE_MAP` maps `"sp_odds": "market_odds"`
  ("market_odds is sparsely populated so we fill from sp_odds").
- `retrain_v2.py:557-563` — `_effective_odds = market_odds.fillna(sp_odds)`;
  tip-time odds exist only for rows that had live predictions, historical
  imports (the bulk) get SP.
- `retrain_v2.py:574,583` — the feature and its derivatives
  (`fair_implied_prob`, `odds_rank`, `odds_rank_pct`) all come from
  `_effective_odds`.

At serve time the same features are computed from pre-race racecard odds.
Train ≈ SP, serve = tip-time: the model's most important input has a
systematically different meaning in production than in training, and every
backtest on SP-derived features overstates what tip-time information can
achieve (the repo's own analysis docs flagged this; these are the exact lines).

**Fix path (task 12, data-gated):** roi/04's training view already adds
`tip_time_odds` + `odds_source` (`snapshot`/`racecard`/`sp_fallback`). Once
the odds clock accrues, retrain with odds features sourced from
`odds_source = 'snapshot'` rows (or explicitly ablate: SP-trained vs
snapshot-trained). Until then, treat every backtest ROI involving odds
features as optimistic.

### 3. Forty-one features are dead weight

41 of 113 declared features are **never assigned on either side** and carry
**0.000000 importance** in the artifact — confirmed independently by static
scan and by the pkl. The model spends training capacity, NaN-handling, and
serve-time column plumbing on them for nothing. Full list in the JSON report;
notable families: the entire market-movement set (`is_steam_move`, `is_drift`,
`steam_velocity`, `drift_velocity`, `odds_movement_pct`, `smart_money_score`,
`is_insider_signal`, `market_confidence`, `field_market_agreement`,
`market_trend_*`, `last_start_market_diff`, `avg_market_diff_3runs`), the
settling family (`predicted_settling_pos`, `settling_percentile`,
`settling_difficulty`, `settling_pace_interaction`), elite-connection flags
(`is_elite_jockey`, `is_elite_trainer`, `is_jockey_upgrade`), and the barrier
family (`barrier_advantage`, `empirical_barrier_advantage`,
`barrier_x_pace_inv`, `td_barrier_style_edge`).

**Fix path (task 12):** for each — either plumb it deliberately (the
market-movement family becomes computable from roi/04 + roi/14 snapshot
deltas; `barrier_advantage` has a designed formula that was simply never
wired) or drop it from `FEATURE_COLUMNS`. Keeping 41 phantom columns is how
the next liveness bug hides.

### 4. Three features exist only in code, not in the live artifact

`fair_implied_prob`, `odds_rank`, `odds_rank_pct` were added to
`FEATURE_COLUMNS` after the 2026-04-15 train: serve computes them, the pkl
(110 columns) ignores them. Inert today; they enter the model at the next
retrain — and since all three derive from `_effective_odds`, they inherit the
SP contamination above. Their task-12 treatment must also be snapshot-based.

### 5. The td_* aggregates leak at train — the open as-of question, RESOLVED

`docs/research/FEATURE_PROVENANCE.md` (finding notes) left one open question:
whether the track-distance aggregates behind `td_pace_bias`, `td_upset_rate`
and `td_closing_speed_bias` exclude the subject race's own result. **They do
not — this is a train-side RESULT_DERIVED_LEAK, diluted but systematic.**

The chain:

1. `track_profiler.build_profiles` aggregates **all** `race_results_history`
   rows in the trailing two years up to BUILD date — `WHERE race_date::date
   >= CURRENT_DATE - INTERVAL '2 years'` with **no upper bound**
   (track_profiler.py:69-77) — and writes a static snapshot
   (`intelligence/track_distance_profiles.json`, track_profiler.py:21,
   252-257). The same window feeds the winners' sectional profile
   (track_profiler.py:82-92) and the SP-ranked upset rate
   (track_profiler.py:174-204).
2. At train, `retrain_v2.build_feature_matrix` loads that snapshot and looks
   up each row's track/distance (retrain_v2.py:602-634,
   `lookup_profile(..., running_style=None)` — so `td_barrier_style_edge` is
   constant 0 at train; the three populated features are the aggregates).
3. The local snapshot was built **2026-04-13**; the artifact was trained
   **2026-04-15**. Therefore for every training row raced on or before
   2026-04-13 the aggregate contains (a) **the subject race's own result** —
   the row's winner feeds `pace_bias` (track_profiler.py:121-132), the row's
   race feeds `upset_rate` race groups (:174-204), and the winner's own
   last-200m feeds `closing_speed_bias` (:82-99, :164-172) — and (b) **every
   race run after the subject race up to the build date**, i.e. future
   information relative to that row.

Magnitude: diluted — 165 of 1,348 keys clear `MIN_RUNNERS = 100`
(track_profiler.py:22), median 152 runners per profile — so the own-race
contribution is ~1/n_races and the future window is shared across the whole
track-distance cell. But it is systematic, it touches every training row,
and `td_pace_bias`/`td_upset_rate` are served at 0.5/0.2 only in the
no-profile fallback (retrain_v2.py:630-634): the model trained on the
*leaked* aggregate distribution.

Serve side is safe by construction: the race being predicted has not run,
so a snapshot built from past results is legitimately pre-race (the staleness
between rebuilds is a freshness issue, not a leak).

**Fix path (task 12, pre-retrain — change no code now):** rebuild the
profiles with as-of discipline — either per training row (aggregate only
`race_date < row.race_date`, matching the form-feature builder's strict-`<`
rule, form_feature_builder.py:1085-1087) or, cheaper, freeze one snapshot
per retrain and exclude all races after the earliest training-row date in
each cell. Re-run the walk-forward after the rebuild: any td_* importance
drop is the leak's signature.

### 6. LIVE_BOTH semantic sweep — the remaining 53, classified

The remaining LIVE_BOTH features (everything except `market_odds`, already
SP_DERIVED in finding 2) trace clean: **41 PRIOR_STARTS_ONLY**, all computed
over the form-feature builder's strict prior-runs filter
(`prior = horse_hist[horse_hist["race_date"] < race_date]`,
form_feature_builder.py:1085-1087; identical rule at serve, :1276-1290),
the trials filter (`trial_date < race_date`, :639-641), the view's LATERAL
prior-sectional join (`st.race_date < r.race_date`,
refresh_training_view_v2.py:264-269), or monthly-bucket lookups with
`race_date::date < month-start` cutoffs (form_feature_builder.py:724, 821,
867 — "zero leakage from the row's own month forward", :970-973). **5
PRE_RACE_SAFE** card facts come straight off the subject race's own
results-history row (barrier_draw/weight_kg/distance/field_size/class_level
— view SQL refresh_training_view_v2.py:207-217). Full per-feature table with
file:line evidence lives in `feature_liveness_report.json` under
`semantic`. Three defects surfaced in the sweep (documented, no code
changed):

- **6a. `days_since_run` uses the wall clock, not the race date**
  (form_feature_builder.py:98: `days = max(0, (pd.Timestamp.now() -
  last_run_date).days)` — `race_date_str` is in scope at :40 and used for
  trials at :525 but not here). Every historical training row gets "days
  from last prior run to RETRAIN day", forcing `is_first_up=1`,
  `is_second_up=0`, `campaign_run_number=1`, `fresh_x_trajectory=0` (and
  skewing `fitness_x_distance`/`campaign_run_x_fitness`) on nearly all old
  rows, while serve (race ≈ today) computes correct values. Not future-data
  leakage, but it encodes row age and corrupts 7 features' train
distributions — `days_since_run` carries 2.6% importance on values that
  mean something different at train than at serve.
- **6b. `trainer_momentum_score` is a constant 50 at train**
  (form_feature_builder.py:496-497, "placeholder — populated by caller";
  no train-path caller populates it; run_tips_pipeline.py serves the same
  placeholder). Only mc_api computes it live (mc_api.py:5852-5853 →
  jockey_momentum.py:211-220, as-of safe SQL). Zero-variance at train,
  real signal on one serve path — skew in the opposite direction.
- **6c. Two more constants at train:** `is_first_time_stakes` is hardcoded
  0 (form_feature_builder.py:226, no other assignment), and
  `barrier_x_pace_inv` is identically 0 because `barrier_advantage` is
  never assigned at train (retrain_v2.py:659-664 consumes
  `barrier_advantage.fillna(0)`). Both are LIVE_BOTH by assignment
  evidence and dead by value — the artifact's importance table agrees
  (both ~0).

## Verdict summary (all 113 features)

| verdict | n | meaning | pkl cross-check |
|---|---|---|---|
| LIVE_BOTH | 54 | assigned train + serve | includes all top-importance live features |
| ZERO_AT_SERVE | 15 | trained signal, constant at serve | 14/15 carry importance (Σ 0.2545) |
| DEAD_BOTH_SIDES | 41 | never assigned anywhere | all 41 at 0.000000 importance |
| DEAD_AT_TRAIN | 3 | serve computes, model never learned | all 3 in-code-not-in-pkl |

Semantic summary (54 LIVE_BOTH, after the sweep in finding 6):

| semantic class | n | features |
|---|---|---|
| PRIOR_STARTS_ONLY | 41 | form-fitness, connections, class-distance, trial, bounce, sectional-derived families + 4 interactions |
| PRE_RACE_SAFE | 5 | barrier_draw, weight_kg, distance, field_size, class_level |
| SP_DERIVED | 1 | market_odds (finding 2) |
| RESULT_DERIVED_LEAK (train-side aggregate; serve-safe) | 3 | td_pace_bias, td_upset_rate, td_closing_speed_bias (finding 5) |
| CONSTANT_AT_TRAIN (assigned, zero-variance) | 4 | trainer_momentum_score, is_first_time_stakes, barrier_x_pace_inv, td_barrier_style_edge |

(5 of the 41 PRIOR_STARTS_ONLY features and 2 of the interactions
additionally inherit the finding-6a wall-clock defect: days_since_run,
is_first_up, is_second_up, campaign_run_number, fresh_x_trajectory,
fitness_x_distance, campaign_run_x_fitness. See the JSON for per-feature
records.)

## Method & honesty notes

- Static scan masks the declaration blocks (`FEATURE_COLUMNS`,
  `NAN_PRESERVE*`, `VIEW_TO_FEATURE_MAP`) so a name's own declaration never
  counts as liveness; assignment evidence = quoted subscript/dict-key
  assignment, view-column mapping, or bare SQL token in the training view.
  Scanned: retrain_v2.py, refresh_training_view_v2.py, run_tips_pipeline.py,
  ml_model.py, and form_feature_builder.py (imported lazily inside functions
  on both sides — a top-level import scan misses it entirely; an early draft
  of this audit did, and wrongly called 69 features dead. The pkl cross-check
  is what caught it).
- `ABSENT` verdicts are trustworthy (a real assignment of the exact name
  cannot escape the regex); `ASSIGNED` verdicts cite file:line for human
  spot-check.
- Semantic tip-time classification (SP_DERIVED vs PRIOR_STARTS_ONLY etc.) is
  agent-verified with file:line evidence for the sectional family and
  hand-verified for the odds family and all fifteen ZERO_AT_SERVE features
  (table above). The remaining families have mechanical verdicts only.
- Fallback branches can masquerade as constants: `td_pace_bias`/`td_upset_rate`
  are assigned 0.5/0.2 only in the no-data branch (retrain_v2.py:630-634); the
  real computation feeds from track-distance aggregates (:602-629). The open
  as-of question on those aggregates is RESOLVED in finding 5: they include
  the subject race's own result — a train-side leak.
- The finding-6 sweep was agent-traced with file:line evidence and the two
  load-bearing claims (wall-clock `days_since_run`, constant
  `trainer_momentum_score`) were independently re-verified by direct reads
  (form_feature_builder.py:98, :497).
- The masking step preserves newlines so every cited line number is real; an
  earlier draft blanked whole declaration blocks and shifted every citation
  after them by ~200 lines. Spot-check of regenerated citations: all true.

## Task-12 action list (machine-usable copy in the JSON report)

1. **Before any retrain:** land the ZERO_AT_SERVE plumbing (roi/03
   serve_features builder, NaN contract) — otherwise the retrain inherits the
   same 25% dead mass and every offline metric overstates live performance.
1b. **Before any retrain:** rebuild the td_* profiles with as-of discipline
   (finding 5 — currently a train-side aggregate leak) and fix the
   wall-clock `days_since_run` (finding 6a — use `race_date_str`, the
   one-line fix); both corrupt the training matrix the retrain would fit.
2. **Odds features** (`market_odds` + 3 derivatives): retrain on
   `odds_source='snapshot'` rows once roi/04 coverage accrues; ablate
   SP-vs-snapshot to quantify the historical contamination.
3. **41 dead features:** explicit keep-and-plumb / drop decision per family;
   default drop.
4. **Re-run this audit as a retrain gate:** `python
   feature_liveness_audit.py --pkl <new artifact>` must show
   ZERO_AT_SERVE = 0 and DEAD_BOTH_SIDES = 0 (or documented exceptions)
   before promotion.

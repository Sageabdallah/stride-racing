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

| feature | importance | train source (leak-safe) |
|---|---|---|
| closer_advantage | 0.0563 | train-side engineering |
| pace_pressure_score | 0.0437 | train-side engineering |
| field_size_context | 0.0396 | train-side engineering |
| leader_advantage | 0.0302 | train-side engineering |
| z_800m | 0.0240 | prior-start sectionals (view `prior_z_800m`, `st.race_date < r.race_date`) |
| trip_cost_seconds | 0.0128 | prior-start sectionals |
| z_600m | 0.0074 | prior-start sectionals |
| barrier_relevance_score | 0.0063 | train-side engineering |
| market_efficiency_flag | 0.0058 | train-side engineering |
| rsi | 0.0058 | prior-start sectionals |
| lambda_decay | 0.0058 | prior-start sectionals |
| svi | 0.0058 | prior-start sectionals |
| z_400m | 0.0057 | prior-start sectionals |
| z_200m | 0.0054 | prior-start sectionals |
| ground_suitability | 0.0000 | (also zero-importance — dead weight both ways) |

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

## Verdict summary (all 113 features)

| verdict | n | meaning | pkl cross-check |
|---|---|---|---|
| LIVE_BOTH | 54 | assigned train + serve | includes all top-importance live features |
| ZERO_AT_SERVE | 15 | trained signal, constant at serve | 14/15 carry importance (Σ 0.2545) |
| DEAD_BOTH_SIDES | 41 | never assigned anywhere | all 41 at 0.000000 importance |
| DEAD_AT_TRAIN | 3 | serve computes, model never learned | all 3 in-code-not-in-pkl |

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
  hand-verified for the odds family (above). The remaining families have
  mechanical verdicts only; their semantic pass is pending and matters mainly
  at task-12 time.

## Task-12 action list (machine-usable copy in the JSON report)

1. **Before any retrain:** land the ZERO_AT_SERVE plumbing (roi/03
   serve_features builder, NaN contract) — otherwise the retrain inherits the
   same 25% dead mass and every offline metric overstates live performance.
2. **Odds features** (`market_odds` + 3 derivatives): retrain on
   `odds_source='snapshot'` rows once roi/04 coverage accrues; ablate
   SP-vs-snapshot to quantify the historical contamination.
3. **41 dead features:** explicit keep-and-plumb / drop decision per family;
   default drop.
4. **Re-run this audit as a retrain gate:** `python
   feature_liveness_audit.py --pkl <new artifact>` must show
   ZERO_AT_SERVE = 0 and DEAD_BOTH_SIDES = 0 (or documented exceptions)
   before promotion.

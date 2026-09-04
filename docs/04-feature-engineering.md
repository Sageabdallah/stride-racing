# Feature Engineering

STRIDE's models consume a fixed contract of features — the `FEATURE_COLUMNS`
list in `server/python/ml_model.py` (the source of the README's "~110
engineered features"; originally exactly 110, now **113** with the Phase-5
relative-market trio). This document catalogues the features, the modules that
produce them, the maths behind the interesting ones, and the
leakage-prevention rules.

Related docs: [ML training & calibration](05-ml-training-and-calibration.md) ·
[Data & ingestion](03-data-and-ingestion.md)

---

## 1. Two parallel pipelines

1. `build_features.py` (repo root) was a standalone JSON→CSV extractor for
   backtesting over the old Racing API result files. It was removed in the 2026-09
   cleanup because nothing produces its input any more; only the production
   pipeline below remains.
2. **`server/python/*`** — the production pipeline. `retrain_v2.py` assembles the
   110-column matrix for training; `run_tips_pipeline.py`/`mc_api.py` assemble the
   same features per runner at inference. `form_feature_builder.py` produces the
   single largest block.

---

## 2. The 110-feature catalogue (by category)

**Form & class movement** (`form_feature_builder.py`, `advanced_features.py`):
`distance_strike_rate`, `course_strike_rate`, `weighted_form_score`, `is_first_up`,
`is_second_up`, `jockey_trainer_strike_rate`, `is_winning_combo`, `is_improving`,
`improvement_score`, `is_in_form_cycle`, `has_dominant_win`, `class_movement`,
`is_class_drop`, `is_class_rise`, `is_first_time_stakes`, `is_blinkers_first_time`,
`going_suitability`, `consistency_score`, `first_up_win_rate`, `second_up_win_rate`

**Trajectory (Phase 3)** (`form_feature_builder.py`): `form_direction_slope`,
`speed_rating_trajectory`, `sectional_trajectory`, `campaign_run_number`,
`weight_change`, `jockey_booking_change`, `fresh_x_trajectory`

**Fitness & staleness** (`temporal_staleness.py`, `fitness_peak.py`):
`days_since_run`, `days_since_last_normalized`, `prep_run_x_days_since`,
`run_spacing_quality`, `empirical_freshness_score`, `is_quick_backup`,
`is_long_absence`, `distance_change_staleness`, `class_x_spell`

**Trainer / jockey** (`jockey_momentum.py`): `is_elite_jockey`, `is_elite_trainer`,
`is_jockey_upgrade`, `trainer_momentum_score`

**Barrier, weight, race shape** (`enhanced_features.py`, `empirical_barriers.py`):
`barrier_draw`, `weight_kg`, `barrier_advantage`, `track_bias_score`,
`empirical_barrier_advantage`, `distance`, `field_size`, `class_level`

**Pace & speed map** (`speed_mapping.py`, `pace_modeling.py`, `race_context.py`):
`running_style_score`, `pace_advantage`, `is_high_pace_expected`,
`distance_style_match`, `predicted_settling_pos`, `settling_percentile`,
`pace_pressure_index`, `settling_difficulty`, `settling_pace_interaction`,
`is_congested_speed`, `speed_horse_ratio`, `pace_pressure_score`,
`leader_advantage`, `closer_advantage`, `barrier_relevance_score`,
`field_size_context`, `market_efficiency_flag`

**Market** (`market_velocity.py`, `market_analysis.py`): `market_odds`,
`is_steam_move`, `is_drift`, `odds_movement_pct`, `last_start_market_diff`,
`avg_market_diff_3runs`, `market_trend_shortening`, `market_trend_drifting`,
`steam_velocity`, `drift_velocity`, `late_move_indicator`, `market_confidence`,
`relative_move`, `smart_money_score`, `is_insider_signal`, `field_market_agreement`

**Sectionals — Phase 2 biomechanical** (`sectional_quant.py` +
`backfill_lambda_targeted.py`): `z_200m`, `z_400m`, `z_600m`, `z_800m`,
`lambda_decay`, `svi`, `rsi`, `trip_cost_seconds`

**Sectionals — Phase 4 distance intelligence** (`form_feature_builder.py`):
`distance_direction_flag`, `sectional_rank_at_distance`, `has_sectional_data`

**Bounce detection (Phase 4)** (`form_feature_builder.py`): `is_bounce_candidate`,
`bounce_severity`, `runs_since_peak`

**Barrier trials (Phase 2)** (`form_feature_builder.py`): `trial_recency`,
`trial_count_60d`, `trial_x_experience`, `trainer_trial_pattern`,
`trial_quality_score`

**Track-distance profiles** (`track_profiler.py`): `td_pace_bias`, `td_upset_rate`,
`td_barrier_style_edge`, `td_closing_speed_bias`

**Within-race relative market — Phase 5** (`relative_market.py`; activates at
the next retrain): `fair_implied_prob` (overround-corrected implied win %
within the field), `odds_rank` (1 = favourite), `odds_rank_pct` (rank / field
size). Names match `mc_api.extract_ml_features` for train/serve parity across
both inference paths — see [Hit-rate research](12-hit-rate-research.md).

**Interaction terms** (inline in `retrain_v2.py:627-649` and mirrored at inference in
`run_tips_pipeline.py:2222-2235`): `fitness_x_distance`, `barrier_x_pace_inv`,
`sectional_x_going`, `class_drop_x_trajectory`, `campaign_run_x_fitness`

Five sectional features are deliberately **excluded** from the contract for low
coverage (< 20%): `dist_sectional_slope`, `dist_sectional_recency_weighted`,
`sectional_result_divergence`, `first_at_distance_sectional_quality`,
`step_up_x_dist_slope` (`ml_model.py:168-170`). `form_feature_builder` still
computes them — computed-but-dropped outputs.

---

## 3. The maths behind the key features

### Form scoring (`form_feature_builder.py`)
- `weighted_form_score`: exponential recency weights **0.75^i** over the last 5 runs;
  non-linear position score (1st→10, 2nd→9, tapering); margin adjustments (dominant
  ≥ 3L win +1.5, scraped-in < 0.3L win −0.5).
- Strike rates: `win_rate × min(1, n/10)` confidence shrinkage, minimum 5 runs;
  distance band = ±100 m.
- `form_direction_slope`: `np.polyfit` slope of field-normalized position over the
  last 3 runs.
- `campaign_run_number`: counts back while gaps ≤ 60 days (60 days = spell threshold
  everywhere in the system).
- Bounce detection: performance score = position percentile × 0.6 + class × 0.4; a
  horse within 90% of its career-best in its last two runs is a bounce candidate
  (in-code note: regression follows ~60–70% of the time).
- Barrier trials: `trial_quality_score = quality × min(field_quality/0.10, 3.0) ×
  min(trials_60d/3, 1.0)`; `trainer_trial_pattern` = trainer's post-trial win rate
  relative to their first-up baseline (league average 0.10).

### Sectional biomechanics (`sectional_quant.py`, `backfill_lambda_targeted.py`)
- **λ decay** (Ward-Smith 1985): `λ = −ln(v_final / v_peak) / (decay_sections × 200)`
  — low λ = sustains velocity (stayer), high = fader.
- **SVI** (Sustained Velocity Index): mean of last-3-section speeds ÷ mean of all
  section speeds; > 1.05 = closer, < 0.95 = fader.
- **RSI** (Race Shape Index, Beyer/Quirin): first-half time ÷ second-half time.
- **Trip cost** (Thorograph/Brohamer): extra distance covered racing wide,
  `extra = (barrier_lane − 1) × 1.8 m × turns × 0.65`, converted to seconds.
- **Z-scores**: per-race per-section `z = (speed − field_mean) / field_std`
  (needs ≥ 3 runners), so `z_200m = +1.0` means one standard deviation faster than
  that field over the final 200 m.
- Five inference-time engines build on these: SASR upgrade (finishing-speed
  percentile vs par → mu shift ±0.15), pace-collapse probability (sigmoid of a
  weighted field-pressure score), race-shape fit, closing rank (elite closer =
  ≥ 3 of last 5 runs above the 80th percentile), and trip efficiency (unlucky-trip
  mu boost ≤ 0.06).

### Ratings & momentum
- **Glicko-2** (`glicko2_elo.py`): full Glickman implementation (scale 173.7178,
  τ=0.5, initial μ=1500 φ=350 σ=0.06) maintained **per surface** (firm/good/soft/
  heavy) plus an "all" bucket; pairwise scores blend finish order 70% with
  margin-decay 30%; win probability via Bradley-Terry. Self-test in `__main__`
  (passes — see [docs/README](README.md#what-was-verified-by-running-code)).
- **Jockey/trainer momentum** (`jockey_momentum.py`): strike rates over 7/14/30-day
  windows weighted 3/2/1, +10 course bonus, hot streak (≥ 3 wins/14d) +15, cold
  (≥ 10 rides, 0 wins) −15 → 0–100 score → multiplier 0.85–1.20. Properly as-of
  (windows end strictly before the race date).
- **Fitness peaks** (`fitness_peak.py`): preparations split at ≥ 60-day gaps;
  readiness = peak-run match 0.30 + trajectory 0.25 + spacing 0.15 + spell quality
  0.10 + placed-rate 0.20, with campaign-tempo modifiers (TIGHT/STANDARD/PATIENT/
  FRAGILE). `build_horse_prep_profiles.py` persists per-horse
  `distance_band × run_number` win rates and PvE (`expected_position =
  field_size × (1 − 1/SP)`; PvE = expected − actual).
- **Freshness** (`temporal_staleness.py`): cosine interpolation across bands (peak
  14–21 days = 1.0, 181–365 days = 0.30); run-spacing quality from the coefficient
  of variation of the last 6 gaps.

### Market & context
- Steam/drift for features: `(open − now) / open × 100` with ±10/±25 thresholds;
  smart-money score 0–100; MC sigma modifier 0.85–1.15 (see
  [Consensus & market §3.2](08-consensus-and-market.md)).
- `race_context.py` computes race-level context broadcast to all runners:
  `pace_pressure_score = min(1, (2·leaders + on_pace)/field)`, leader/closer
  advantage, barrier relevance (distance-scaled, ×1.3 on tight tracks), and an
  entropy-based `pace_clarity_score` (used to cap confidence, not in the
  feature contract).

---

## 4. Leakage prevention — three tiers

**Tier 1 — rigorously as-of (safe for training):**
- `form_feature_builder`: every history query is `race_date < current_race_date`;
  batch training uses per-month as-of caches so aggregates only see data strictly
  before each month boundary.
- `jockey_momentum`: windows bounded `race_date < ref_date`.
- `target_encoding.py`: leave-one-out encoding with Gaussian noise (σ=0.01),
  smoothing 10.0.
- `refresh_training_view_v2`: prior-sectionals via temporal LATERAL join.

**Tier 2 — inference-safe, leaky if reused for historical backfill:**
`advanced_features` hot-streak/collateral (anchored to `CURRENT_DATE`),
`enhanced_features`/`empirical_barriers` barrier-bias tables (no date filter — a
slow-moving track property), `sectional_quant` engines (`LIMIT n` without cutoff),
`track_condition_db` going records, `track_profiler` (rolling 2-year window from
today).

**Tier 3 — structural hazards worth knowing:**
`fitness_peak.build_fitness_profile` caches by horse name only (callers must clear
the cache per date); `days_since_run` falls back to `datetime.now()` for rows
missing dates, which is wrong for historical training rows.

---

## 5. Supporting infrastructure

- **`race_normaliser.py`** — runs before the MC engine: canonicalizes going/names/
  classes, validates the racecard (distance 400–5000 m, field ≥ 2, overround
  0.90–1.60, duplicate barriers/odds), builds the pace map, and emits candidate
  engineered features.
- **`normalize.py` / `horse_names.py` / `learning_track_map.py`** — name and track
  canonicalization (see [Data & ingestion §5](03-data-and-ingestion.md)).
- **`speed_ratings.py`** — synthetic par-time speed ratings
  (`100 + (par − time) × 5 + track_adj`, clamped [60, 130]); production role is the
  `speed_rating_trajectory` slope.

---

## 6. Known quirks (verified in source)

- **Four independent class scales** disagree: `advanced_features.CLASS_HIERARCHY`
  (0–100), `enhanced_features.CLASS_RANKINGS` (0–10),
  `temporal_staleness.CLASS_LEVEL_MAP` (1–10), `race_context._parse_class_level`
  (10–100). E.g. BM72 = 64 vs 4.6 vs 4 vs 70.
- **Five recency-weight schemes** coexist: 0.75^i (form), [0.35,0.25,0.20,0.12,0.08]
  (advanced form), [0.4,0.3,0.2,0.1] (settling), 3/2/1 windows (jockey momentum),
  1.0/0.7/0.4 date bands (distance sectionals).
- `build_features.enrich_with_sectional_features` previously keyed on a
  `horse_name` column the table builder never emits (it emits `horse`), so the
  join silently no-oped. Fixed: it now detects the `horse` column (still accepting
  `horse_name` for external frames). Note the function has no caller inside
  `build_features.main()` — it is an opt-in utility.
- Duplicate `RunningStyle`/`PaceScenario` enums and diverging tight-turn track lists
  exist in `pace_modeling.py` vs `speed_mapping.py`.
- `race_context`'s formulas are re-implemented inline in `retrain_v2.py` rather than
  imported; `pace_clarity_score` is produced but not in the contract.
- `temporal_staleness.classify_trainer_freshness` is a placeholder that always
  returns `'standard'`.

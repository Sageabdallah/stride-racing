# Task 12 — Feature Decision Matrix (dead/constant pruning)

**Inputs:** `docs/research/FEATURE_PROVENANCE.md` + `docs/research/feature_liveness_report.json`
(branches `docs/feature-provenance-sweep` / `ops/deploy-prep`; not yet on main at time of writing).

**Facts from the liveness audit:**
- 41 features `DEAD_BOTH_SIDES` — never assigned at train nor at serve; importance
  `0.000000` in `racing_ensemble_v2.pkl` (110 trained columns).
- 4 features `CONSTANT_AT_TRAIN` — assigned but zero-variance at train:
  - `trainer_momentum_score` hardcoded 50 (`form_feature_builder.py:496-497`, finding 6b)
  - `is_first_time_stakes` hardcoded 0 (`form_feature_builder.py:226`, finding 6c)
  - `barrier_x_pace_inv` identically 0 because `barrier_advantage` is never assigned
    (`retrain_v2.py:659-664`, finding 6c)
  - `td_barrier_style_edge` constant 0 at train (`retrain_v2.py:624` calls
    `lookup_profile(..., running_style=None)` → `track_profiler.py:294` returns 0)

**Decision classes:** DROP (gone for good) · DEFER-task-14 (dropped now, re-entry once
snapshot deltas exist — roi/04-as-of-odds-snapshot lineage) · KEEP-PLUMB-CANDIDATE
(designed formula never wired; dropped this pass unless Sage says plumb) · KEEP.

**All 45 rows below are removed from `FEATURE_COLUMNS` in both `retrain_v2.py` and
`ml_model.py` in this pass**, including the DEFER and KEEP-PLUMB-CANDIDATE rows.

Evidence citations refer to `feature_liveness_report.json`: `static` = train/serve
assignment sweep verdicts; `pkl` = importance in `racing_ensemble_v2.pkl`;
`semantic` = hand-verified class with code evidence.

## DROP — dead both sides (24)

| feature | class | importance | decision | rationale |
|---|---|---|---|---|
| running_style_score | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| pace_advantage | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| is_high_pace_expected | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| distance_style_match | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| is_blinkers_first_time | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| is_elite_jockey | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| is_elite_trainer | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| is_jockey_upgrade | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| track_bias_score | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| predicted_settling_pos | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| settling_percentile | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| pace_pressure_index | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| settling_difficulty | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| settling_pace_interaction | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| is_congested_speed | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| speed_horse_ratio | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| days_since_last_normalized | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| prep_run_x_days_since | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| run_spacing_quality | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| empirical_freshness_score | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| is_quick_backup | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| is_long_absence | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| distance_change_staleness | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |
| class_x_spell | DEAD_BOTH_SIDES | 0.000000 | DROP | static: train ABSENT / serve ABSENT; never computed anywhere |

## DROP — constant at train (3)

| feature | class | importance | decision | rationale |
|---|---|---|---|---|
| trainer_momentum_score | CONSTANT_AT_TRAIN | 0.000000 | DROP | semantic: `form_feature_builder.py:496-497` hardcoded 50 (finding 6b); zero variance at train. Live mc_api computes it as-of safe — re-entry only if train-side wiring lands |
| is_first_time_stakes | CONSTANT_AT_TRAIN | 0.000000 | DROP | semantic: `form_feature_builder.py:226` hardcoded 0 (finding 6c); zero variance |
| td_barrier_style_edge | CONSTANT_AT_TRAIN | 0.000000 | DROP | semantic: `retrain_v2.py:624` (`running_style=None`) + `track_profiler.py:294` → constant 0 at train; per-horse edge needs running style at train, which does not exist |

## DEFER-task-14 — market-movement family (15, dropped now, listed for re-entry)

Computable only once snapshot deltas exist (12P-4 / roi/04-as-of-odds-snapshot lineage).

| feature | class | importance | decision | rationale |
|---|---|---|---|---|
| is_steam_move | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| is_drift | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| steam_velocity | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| drift_velocity | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| odds_movement_pct | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| late_move_indicator | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| relative_move | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| smart_money_score | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| is_insider_signal | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| market_confidence | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| field_market_agreement | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| market_trend_shortening | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| market_trend_drifting | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| last_start_market_diff | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |
| avg_market_diff_3runs | DEAD_BOTH_SIDES | 0.000000 | DEFER-task-14 | static: train ABSENT / serve ABSENT; needs odds-snapshot deltas |

## KEEP-PLUMB-CANDIDATE — flagged for Sage (3, dropped this pass unless Sage says plumb)

| feature | class | importance | decision | rationale |
|---|---|---|---|---|
| barrier_advantage | DEAD_BOTH_SIDES | 0.000000 | KEEP-PLUMB-CANDIDATE | static: train REFERENCED_ONLY / serve REFERENCED_ONLY — referenced by `barrier_x_pace_inv` but never assigned; a designed formula that was never wired |
| empirical_barrier_advantage | DEAD_BOTH_SIDES | 0.000000 | KEEP-PLUMB-CANDIDATE | static: train ABSENT / serve ABSENT; empirical barrier priors exist (`empirical_barriers.py`) but were never wired into features |
| barrier_x_pace_inv | CONSTANT_AT_TRAIN | 0.000000 | KEEP-PLUMB-CANDIDATE | semantic: `retrain_v2.py:659-664` — product identically 0 because parent `barrier_advantage` is never assigned (finding 6c); plumb the parent and this revives |

## KEEP — dead at train today but wired for the next retrain (3, context only)

| feature | class | importance | decision | rationale |
|---|---|---|---|---|
| fair_implied_prob | DEAD_AT_TRAIN | NOT_IN_PKL | KEEP | static: assigned at serve (`relative_market.py`), absent at train; Phase 5 feature in code awaiting first retrain inclusion (`pkl.in_code_not_in_pkl`) |
| odds_rank | DEAD_AT_TRAIN | NOT_IN_PKL | KEEP | static: assigned at serve, absent at train; Phase 5 (`pkl.in_code_not_in_pkl`) |
| odds_rank_pct | DEAD_AT_TRAIN | NOT_IN_PKL | KEEP | static: assigned at serve, absent at train; Phase 5 (`pkl.in_code_not_in_pkl`) |

## Tally

| decision | count |
|---|---|
| DROP (dead both sides) | 24 |
| DROP (constant at train) | 3 |
| DEFER-task-14 (dropped now) | 15 |
| KEEP-PLUMB-CANDIDATE (dropped this pass) | 3 |
| **Removed from FEATURE_COLUMNS** | **45** |
| KEEP (Phase 5, context) | 3 |
| FEATURE_COLUMNS before → after | 113 → 68 |

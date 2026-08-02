# Agent 1 Findings

## Notes
- Historical rail-position data is not stored in the production DB, so rail-out analysis is skipped and documented as unavailable.
- Flemington straight lane-bias analysis is not emitted because there is no lateral lane/side-of-track column in the historical runner data.

## A-001
- **Category:** Sectional
- **Pattern Description:** At Eagle Farm in Middle 1400-1800 races on Good, runners closing their last 200m in 11.96s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Eagle Farm
- **Conditions:** Middle 1400-1800 | Good | Handicap/BM
- **Winner Win Rate:** 16.67
- **Field Average Win Rate:** 8.5
- **Edge Magnitude:** 8.17
- **Sample Size:** 78
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Eagle Farm AND distance_bucket=Middle 1400-1800 AND going=Good AND last_200m_time <= 11.96s THEN win rate is 8.17pp above base.
- **Priority:** 3
- **Window Stats:** [{"window": "early", "sample_size": 47, "win_rate": 10.64, "base_rate": 8.33, "edge_vs_base": 2.31, "implied_win_rate": null}, {"window": "mid", "sample_size": 12, "win_rate": 16.67, "base_rate": 9.71, "edge_vs_base": 6.96, "implied_win_rate": null}, {"window": "late", "sample_size": 19, "win_rate": 31.58, "base_rate": 9.07, "edge_vs_base": 22.51, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-002
- **Category:** Sectional
- **Pattern Description:** At Randwick in Middle 1400-1800 races on Good, horses sitting 2-3 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Randwick
- **Conditions:** Middle 1400-1800 | Good | 400m position 2-3
- **Winner Win Rate:** 16.38
- **Field Average Win Rate:** 8.48
- **Edge Magnitude:** 7.9
- **Sample Size:** 116
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Randwick AND distance_bucket=Middle 1400-1800 AND going=Good AND pos_400m_bucket=2-3 THEN win rate is 7.90pp above base.
- **Priority:** 3
- **Window Stats:** [{"window": "early", "sample_size": 42, "win_rate": 16.67, "base_rate": 8.89, "edge_vs_base": 7.78, "implied_win_rate": null}, {"window": "mid", "sample_size": 20, "win_rate": 0.0, "base_rate": 9.53, "edge_vs_base": -9.53, "implied_win_rate": null}, {"window": "late", "sample_size": 54, "win_rate": 22.22, "base_rate": 8.31, "edge_vs_base": 13.92, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## A-003
- **Category:** Barrier
- **Pattern Description:** At Eagle Farm in Sprint <=1200 races, barrier segment 5-8 wins above the local base rate when the surface is Dry and field size is 17+.
- **Query Used:** Runner-level win rates grouped by track, distance bucket, wet/dry group, barrier segment, and field-size bucket.
- **Tracks Affected:** Eagle Farm
- **Conditions:** Sprint <=1200 | surface=Dry | field_size=17+ | barrier=5-8
- **Winner Win Rate:** 12.4
- **Field Average Win Rate:** 5.01
- **Edge Magnitude:** 7.39
- **Sample Size:** 121
- **Sectionals Involved:** No
- **Data Quality Note:** Historical rail-position data is unavailable, so this is a barrier-only effect without rail interaction.
- **Model Gap Explanation:** Barrier features often get modelled too statically and miss the field-size x surface interaction.
- **Feature Engineering Fix:** Add barrier segment x field-size bucket x wet/dry interaction features by track.
- **Backtestable Rule:** IF track=Eagle Farm AND barrier_segment=5-8 AND field_size_bucket=17+ AND wet_group=Dry THEN win rate is 7.39pp above base.
- **Priority:** 3
- **Window Stats:** [{"window": "early", "sample_size": 77, "win_rate": 12.99, "base_rate": 9.39, "edge_vs_base": 3.6, "implied_win_rate": null}, {"window": "mid", "sample_size": 30, "win_rate": 10.0, "base_rate": 9.02, "edge_vs_base": 0.98, "implied_win_rate": null}, {"window": "late", "sample_size": 14, "win_rate": 14.29, "base_rate": 8.65, "edge_vs_base": 5.64, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-004
- **Category:** Sectional
- **Pattern Description:** At Eagle Farm in Middle 1400-1800 races on Soft, runners closing their last 200m in 12.29s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Eagle Farm
- **Conditions:** Middle 1400-1800 | Soft | Handicap/BM
- **Winner Win Rate:** 24.19
- **Field Average Win Rate:** 9.96
- **Edge Magnitude:** 14.24
- **Sample Size:** 62
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Eagle Farm AND distance_bucket=Middle 1400-1800 AND going=Soft AND last_200m_time <= 12.29s THEN win rate is 14.24pp above base.
- **Priority:** 4
- **Window Stats:** [{"window": "early", "sample_size": 50, "win_rate": 26.0, "base_rate": 8.33, "edge_vs_base": 17.67, "implied_win_rate": null}, {"window": "mid", "sample_size": 6, "win_rate": 16.67, "base_rate": 9.71, "edge_vs_base": 6.96, "implied_win_rate": null}, {"window": "late", "sample_size": 6, "win_rate": 16.67, "base_rate": 9.07, "edge_vs_base": 7.6, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-005
- **Category:** Sectional
- **Pattern Description:** At Eagle Farm in Sprint <=1200 races on Soft, runners closing their last 200m in 11.96s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Eagle Farm
- **Conditions:** Sprint <=1200 | Soft | Handicap/BM
- **Winner Win Rate:** 21.82
- **Field Average Win Rate:** 10.23
- **Edge Magnitude:** 11.59
- **Sample Size:** 55
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Eagle Farm AND distance_bucket=Sprint <=1200 AND going=Soft AND last_200m_time <= 11.96s THEN win rate is 11.59pp above base.
- **Priority:** 4
- **Window Stats:** [{"window": "early", "sample_size": 24, "win_rate": 16.67, "base_rate": 8.33, "edge_vs_base": 8.34, "implied_win_rate": null}, {"window": "mid", "sample_size": 19, "win_rate": 26.32, "base_rate": 9.71, "edge_vs_base": 16.61, "implied_win_rate": null}, {"window": "late", "sample_size": 12, "win_rate": 25.0, "base_rate": 9.07, "edge_vs_base": 15.93, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-006
- **Category:** Sectional
- **Pattern Description:** At Randwick in Middle 1400-1800 races on Soft, horses sitting 1 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Randwick
- **Conditions:** Middle 1400-1800 | Soft | 400m position 1
- **Winner Win Rate:** 19.3
- **Field Average Win Rate:** 8.52
- **Edge Magnitude:** 10.78
- **Sample Size:** 57
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Randwick AND distance_bucket=Middle 1400-1800 AND going=Soft AND pos_400m_bucket=1 THEN win rate is 10.78pp above base.
- **Priority:** 4
- **Window Stats:** [{"window": "early", "sample_size": 19, "win_rate": 21.05, "base_rate": 8.89, "edge_vs_base": 12.17, "implied_win_rate": null}, {"window": "mid", "sample_size": 21, "win_rate": 19.05, "base_rate": 9.53, "edge_vs_base": 9.52, "implied_win_rate": null}, {"window": "late", "sample_size": 17, "win_rate": 17.65, "base_rate": 8.31, "edge_vs_base": 9.34, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-007
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Middle 1400-1800 races on Soft, runners closing their last 200m in 11.17s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Rosehill
- **Conditions:** Middle 1400-1800 | Soft | Handicap/BM
- **Winner Win Rate:** 18.03
- **Field Average Win Rate:** 8.52
- **Edge Magnitude:** 9.51
- **Sample Size:** 61
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Middle 1400-1800 AND going=Soft AND last_200m_time <= 11.17s THEN win rate is 9.51pp above base.
- **Priority:** 4
- **Window Stats:** [{"window": "early", "sample_size": 5, "win_rate": 40.0, "base_rate": 8.33, "edge_vs_base": 31.67, "implied_win_rate": null}, {"window": "mid", "sample_size": 46, "win_rate": 19.57, "base_rate": 9.71, "edge_vs_base": 9.86, "implied_win_rate": null}, {"window": "late", "sample_size": 10, "win_rate": 0.0, "base_rate": 9.07, "edge_vs_base": -9.07, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## A-008
- **Category:** Sectional
- **Pattern Description:** At Randwick in Middle 1400-1800 races on Good, horses sitting 1 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Randwick
- **Conditions:** Middle 1400-1800 | Good | 400m position 1
- **Winner Win Rate:** 17.24
- **Field Average Win Rate:** 8.48
- **Edge Magnitude:** 8.76
- **Sample Size:** 58
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Randwick AND distance_bucket=Middle 1400-1800 AND going=Good AND pos_400m_bucket=1 THEN win rate is 8.76pp above base.
- **Priority:** 4
- **Window Stats:** [{"window": "early", "sample_size": 21, "win_rate": 19.05, "base_rate": 8.89, "edge_vs_base": 10.16, "implied_win_rate": null}, {"window": "mid", "sample_size": 10, "win_rate": 20.0, "base_rate": 9.53, "edge_vs_base": 10.47, "implied_win_rate": null}, {"window": "late", "sample_size": 27, "win_rate": 14.81, "base_rate": 8.31, "edge_vs_base": 6.51, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-009
- **Category:** Barrier
- **Pattern Description:** At Flemington in Middle 1400-1800 races, barrier segment 5-8 wins above the local base rate when the surface is Dry and field size is 17+.
- **Query Used:** Runner-level win rates grouped by track, distance bucket, wet/dry group, barrier segment, and field-size bucket.
- **Tracks Affected:** Flemington
- **Conditions:** Middle 1400-1800 | surface=Dry | field_size=17+ | barrier=5-8
- **Winner Win Rate:** 13.33
- **Field Average Win Rate:** 5.53
- **Edge Magnitude:** 7.8
- **Sample Size:** 60
- **Sectionals Involved:** No
- **Data Quality Note:** Historical rail-position data is unavailable, so this is a barrier-only effect without rail interaction.
- **Model Gap Explanation:** Barrier features often get modelled too statically and miss the field-size x surface interaction.
- **Feature Engineering Fix:** Add barrier segment x field-size bucket x wet/dry interaction features by track.
- **Backtestable Rule:** IF track=Flemington AND barrier_segment=5-8 AND field_size_bucket=17+ AND wet_group=Dry THEN win rate is 7.80pp above base.
- **Priority:** 4
- **Window Stats:** [{"window": "early", "sample_size": 32, "win_rate": 18.75, "base_rate": 9.39, "edge_vs_base": 9.36, "implied_win_rate": null}, {"window": "mid", "sample_size": 9, "win_rate": 0.0, "base_rate": 9.02, "edge_vs_base": -9.02, "implied_win_rate": null}, {"window": "late", "sample_size": 19, "win_rate": 10.53, "base_rate": 8.65, "edge_vs_base": 1.88, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## A-010
- **Category:** Barrier
- **Pattern Description:** At Randwick in Sprint <=1200 races, barrier segment 1-4 wins above the local base rate when the surface is Dry and field size is 17+.
- **Query Used:** Runner-level win rates grouped by track, distance bucket, wet/dry group, barrier segment, and field-size bucket.
- **Tracks Affected:** Randwick
- **Conditions:** Sprint <=1200 | surface=Dry | field_size=17+ | barrier=1-4
- **Winner Win Rate:** 12.12
- **Field Average Win Rate:** 5.23
- **Edge Magnitude:** 6.89
- **Sample Size:** 66
- **Sectionals Involved:** No
- **Data Quality Note:** Historical rail-position data is unavailable, so this is a barrier-only effect without rail interaction.
- **Model Gap Explanation:** Barrier features often get modelled too statically and miss the field-size x surface interaction.
- **Feature Engineering Fix:** Add barrier segment x field-size bucket x wet/dry interaction features by track.
- **Backtestable Rule:** IF track=Randwick AND barrier_segment=1-4 AND field_size_bucket=17+ AND wet_group=Dry THEN win rate is 6.89pp above base.
- **Priority:** 4
- **Window Stats:** [{"window": "early", "sample_size": 17, "win_rate": 11.76, "base_rate": 9.39, "edge_vs_base": 2.37, "implied_win_rate": null}, {"window": "mid", "sample_size": 4, "win_rate": 25.0, "base_rate": 9.02, "edge_vs_base": 15.98, "implied_win_rate": null}, {"window": "late", "sample_size": 45, "win_rate": 11.11, "base_rate": 8.65, "edge_vs_base": 2.47, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-011
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Middle 1400-1800 races on Good, runners closing their last 200m in 11.04s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Rosehill
- **Conditions:** Middle 1400-1800 | Good | Handicap/BM
- **Winner Win Rate:** 15.79
- **Field Average Win Rate:** 9.55
- **Edge Magnitude:** 6.24
- **Sample Size:** 57
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Middle 1400-1800 AND going=Good AND last_200m_time <= 11.04s THEN win rate is 6.24pp above base.
- **Priority:** 4
- **Window Stats:** [{"window": "early", "sample_size": 10, "win_rate": 10.0, "base_rate": 8.33, "edge_vs_base": 1.67, "implied_win_rate": null}, {"window": "mid", "sample_size": 5, "win_rate": 40.0, "base_rate": 9.71, "edge_vs_base": 30.29, "implied_win_rate": null}, {"window": "late", "sample_size": 42, "win_rate": 14.29, "base_rate": 9.07, "edge_vs_base": 5.21, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-012
- **Category:** Sectional
- **Pattern Description:** At Randwick in Sprint <=1200 races on Good, runners closing their last 200m in 10.71s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Randwick
- **Conditions:** Sprint <=1200 | Good | Group 2
- **Winner Win Rate:** 31.25
- **Field Average Win Rate:** 10.17
- **Edge Magnitude:** 21.08
- **Sample Size:** 32
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Randwick AND distance_bucket=Sprint <=1200 AND going=Good AND last_200m_time <= 10.71s THEN win rate is 21.08pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 24, "win_rate": 33.33, "base_rate": 8.33, "edge_vs_base": 25.0, "implied_win_rate": null}, {"window": "mid", "sample_size": 5, "win_rate": 20.0, "base_rate": 9.71, "edge_vs_base": 10.29, "implied_win_rate": null}, {"window": "late", "sample_size": 3, "win_rate": 33.33, "base_rate": 9.07, "edge_vs_base": 24.26, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-013
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Middle 1400-1800 races on Soft, horses sitting 1 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Rosehill
- **Conditions:** Middle 1400-1800 | Soft | 400m position 1
- **Winner Win Rate:** 25.0
- **Field Average Win Rate:** 7.81
- **Edge Magnitude:** 17.19
- **Sample Size:** 36
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Middle 1400-1800 AND going=Soft AND pos_400m_bucket=1 THEN win rate is 17.19pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 6, "win_rate": 33.33, "base_rate": 8.89, "edge_vs_base": 24.45, "implied_win_rate": null}, {"window": "mid", "sample_size": 22, "win_rate": 31.82, "base_rate": 9.53, "edge_vs_base": 22.29, "implied_win_rate": null}, {"window": "late", "sample_size": 8, "win_rate": 0.0, "base_rate": 8.31, "edge_vs_base": -8.31, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## A-014
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Middle 1400-1800 races on Heavy, horses sitting 1 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Rosehill
- **Conditions:** Middle 1400-1800 | Heavy | 400m position 1
- **Winner Win Rate:** 27.27
- **Field Average Win Rate:** 10.63
- **Edge Magnitude:** 16.64
- **Sample Size:** 22
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Middle 1400-1800 AND going=Heavy AND pos_400m_bucket=1 THEN win rate is 16.64pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 8, "win_rate": 37.5, "base_rate": 8.89, "edge_vs_base": 28.61, "implied_win_rate": null}, {"window": "mid", "sample_size": 11, "win_rate": 18.18, "base_rate": 9.53, "edge_vs_base": 8.65, "implied_win_rate": null}, {"window": "late", "sample_size": 3, "win_rate": 33.33, "base_rate": 8.31, "edge_vs_base": 25.03, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-015
- **Category:** Sectional
- **Pattern Description:** At Morphettville in Sprint <=1200 races on Unknown, runners closing their last 200m in 11.73s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Morphettville
- **Conditions:** Sprint <=1200 | Unknown | Unknown
- **Winner Win Rate:** 25.81
- **Field Average Win Rate:** 10.0
- **Edge Magnitude:** 15.81
- **Sample Size:** 31
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Morphettville AND distance_bucket=Sprint <=1200 AND going=Unknown AND last_200m_time <= 11.73s THEN win rate is 15.81pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "late", "sample_size": 31, "win_rate": 25.81, "base_rate": 9.07, "edge_vs_base": 16.73, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-016
- **Category:** Sectional
- **Pattern Description:** In slow early-pace races at Rosehill, contested runners in Middle 1400-1800 events outperform the race base rate.
- **Query Used:** Race-level opening_600m derived from splits_json segment ladder; hot/slow pace labels assigned by 25th/75th cohort quantiles, then runner win rates compared by pace_role_600m.
- **Tracks Affected:** Rosehill
- **Conditions:** Middle 1400-1800 | Good | pace=slow | role=contested
- **Winner Win Rate:** 20.0
- **Field Average Win Rate:** 6.71
- **Edge Magnitude:** 13.29
- **Sample Size:** 20
- **Sectionals Involved:** Yes - opening 600m derived from split ladder + 600m in-run position
- **Data Quality Note:** Opening 600m is approximated from segment times; off-pace/contested uses 600m position as a pace proxy.
- **Model Gap Explanation:** Static running-style features miss the interaction between race-level tempo and where the horse sat when pressure developed.
- **Feature Engineering Fix:** Add pace-scenario interaction features: hot/slow race flag x 600m pace-role bucket.
- **Backtestable Rule:** IF track=Rosehill AND pace_class=slow AND pace_role=contested THEN win rate is 13.29pp above same-race base.
- **Priority:** 5
- **Window Stats:** [{"window": "mid", "sample_size": 6, "win_rate": 16.67, "base_rate": 9.02, "edge_vs_base": 7.65, "implied_win_rate": null}, {"window": "late", "sample_size": 14, "win_rate": 21.43, "base_rate": 8.65, "edge_vs_base": 12.78, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-017
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Middle 1400-1800 races on Good, horses sitting 1 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Rosehill
- **Conditions:** Middle 1400-1800 | Good | 400m position 1
- **Winner Win Rate:** 21.62
- **Field Average Win Rate:** 8.57
- **Edge Magnitude:** 13.05
- **Sample Size:** 37
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Middle 1400-1800 AND going=Good AND pos_400m_bucket=1 THEN win rate is 13.05pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 4, "win_rate": 25.0, "base_rate": 8.89, "edge_vs_base": 16.11, "implied_win_rate": null}, {"window": "mid", "sample_size": 15, "win_rate": 20.0, "base_rate": 9.53, "edge_vs_base": 10.47, "implied_win_rate": null}, {"window": "late", "sample_size": 18, "win_rate": 22.22, "base_rate": 8.31, "edge_vs_base": 13.92, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-018
- **Category:** Sectional
- **Pattern Description:** At Eagle Farm in Staying 2000+ races on Soft, runners closing their last 200m in 12.40s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Eagle Farm
- **Conditions:** Staying 2000+ | Soft | Handicap/BM
- **Winner Win Rate:** 21.62
- **Field Average Win Rate:** 9.63
- **Edge Magnitude:** 11.99
- **Sample Size:** 37
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Eagle Farm AND distance_bucket=Staying 2000+ AND going=Soft AND last_200m_time <= 12.40s THEN win rate is 11.99pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 25, "win_rate": 20.0, "base_rate": 8.33, "edge_vs_base": 11.67, "implied_win_rate": null}, {"window": "mid", "sample_size": 3, "win_rate": 33.33, "base_rate": 9.71, "edge_vs_base": 23.63, "implied_win_rate": null}, {"window": "late", "sample_size": 9, "win_rate": 22.22, "base_rate": 9.07, "edge_vs_base": 13.15, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-019
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Sprint <=1200 races on Good, horses sitting 2-3 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Rosehill
- **Conditions:** Sprint <=1200 | Good | 400m position 2-3
- **Winner Win Rate:** 21.74
- **Field Average Win Rate:** 9.82
- **Edge Magnitude:** 11.92
- **Sample Size:** 46
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Sprint <=1200 AND going=Good AND pos_400m_bucket=2-3 THEN win rate is 11.92pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 10, "win_rate": 20.0, "base_rate": 8.89, "edge_vs_base": 11.11, "implied_win_rate": null}, {"window": "mid", "sample_size": 10, "win_rate": 20.0, "base_rate": 9.53, "edge_vs_base": 10.47, "implied_win_rate": null}, {"window": "late", "sample_size": 26, "win_rate": 23.08, "base_rate": 8.31, "edge_vs_base": 14.77, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-020
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Sprint <=1200 races on Good, horses sitting 1 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Rosehill
- **Conditions:** Sprint <=1200 | Good | 400m position 1
- **Winner Win Rate:** 21.74
- **Field Average Win Rate:** 9.82
- **Edge Magnitude:** 11.92
- **Sample Size:** 23
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Sprint <=1200 AND going=Good AND pos_400m_bucket=1 THEN win rate is 11.92pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 5, "win_rate": 40.0, "base_rate": 8.89, "edge_vs_base": 31.11, "implied_win_rate": null}, {"window": "mid", "sample_size": 5, "win_rate": 0.0, "base_rate": 9.53, "edge_vs_base": -9.53, "implied_win_rate": null}, {"window": "late", "sample_size": 13, "win_rate": 23.08, "base_rate": 8.31, "edge_vs_base": 14.77, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## A-021
- **Category:** Sectional
- **Pattern Description:** In slow early-pace races at Randwick, contested runners in Middle 1400-1800 events outperform the race base rate.
- **Query Used:** Race-level opening_600m derived from splits_json segment ladder; hot/slow pace labels assigned by 25th/75th cohort quantiles, then runner win rates compared by pace_role_600m.
- **Tracks Affected:** Randwick
- **Conditions:** Middle 1400-1800 | Good | pace=slow | role=contested
- **Winner Win Rate:** 20.0
- **Field Average Win Rate:** 8.15
- **Edge Magnitude:** 11.85
- **Sample Size:** 30
- **Sectionals Involved:** Yes - opening 600m derived from split ladder + 600m in-run position
- **Data Quality Note:** Opening 600m is approximated from segment times; off-pace/contested uses 600m position as a pace proxy.
- **Model Gap Explanation:** Static running-style features miss the interaction between race-level tempo and where the horse sat when pressure developed.
- **Feature Engineering Fix:** Add pace-scenario interaction features: hot/slow race flag x 600m pace-role bucket.
- **Backtestable Rule:** IF track=Randwick AND pace_class=slow AND pace_role=contested THEN win rate is 11.85pp above same-race base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 10, "win_rate": 20.0, "base_rate": 9.39, "edge_vs_base": 10.61, "implied_win_rate": null}, {"window": "late", "sample_size": 20, "win_rate": 20.0, "base_rate": 8.65, "edge_vs_base": 11.35, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-022
- **Category:** Barrier
- **Pattern Description:** At Randwick in Staying 2000+ races, barrier segment 1-4 wins above the local base rate when the surface is Dry and field size is 17+.
- **Query Used:** Runner-level win rates grouped by track, distance bucket, wet/dry group, barrier segment, and field-size bucket.
- **Tracks Affected:** Randwick
- **Conditions:** Staying 2000+ | surface=Dry | field_size=17+ | barrier=1-4
- **Winner Win Rate:** 15.0
- **Field Average Win Rate:** 4.72
- **Edge Magnitude:** 10.28
- **Sample Size:** 20
- **Sectionals Involved:** No
- **Data Quality Note:** Historical rail-position data is unavailable, so this is a barrier-only effect without rail interaction.
- **Model Gap Explanation:** Barrier features often get modelled too statically and miss the field-size x surface interaction.
- **Feature Engineering Fix:** Add barrier segment x field-size bucket x wet/dry interaction features by track.
- **Backtestable Rule:** IF track=Randwick AND barrier_segment=1-4 AND field_size_bucket=17+ AND wet_group=Dry THEN win rate is 10.28pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 12, "win_rate": 16.67, "base_rate": 9.39, "edge_vs_base": 7.28, "implied_win_rate": null}, {"window": "late", "sample_size": 8, "win_rate": 12.5, "base_rate": 8.65, "edge_vs_base": 3.85, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-023
- **Category:** Barrier
- **Pattern Description:** At Eagle Farm in Staying 2000+ races, barrier segment 1-4 wins above the local base rate when the surface is Dry and field size is 13-16.
- **Query Used:** Runner-level win rates grouped by track, distance bucket, wet/dry group, barrier segment, and field-size bucket.
- **Tracks Affected:** Eagle Farm
- **Conditions:** Staying 2000+ | surface=Dry | field_size=13-16 | barrier=1-4
- **Winner Win Rate:** 17.14
- **Field Average Win Rate:** 6.93
- **Edge Magnitude:** 10.21
- **Sample Size:** 35
- **Sectionals Involved:** No
- **Data Quality Note:** Historical rail-position data is unavailable, so this is a barrier-only effect without rail interaction.
- **Model Gap Explanation:** Barrier features often get modelled too statically and miss the field-size x surface interaction.
- **Feature Engineering Fix:** Add barrier segment x field-size bucket x wet/dry interaction features by track.
- **Backtestable Rule:** IF track=Eagle Farm AND barrier_segment=1-4 AND field_size_bucket=13-16 AND wet_group=Dry THEN win rate is 10.21pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 18, "win_rate": 16.67, "base_rate": 9.39, "edge_vs_base": 7.28, "implied_win_rate": null}, {"window": "mid", "sample_size": 4, "win_rate": 25.0, "base_rate": 9.02, "edge_vs_base": 15.98, "implied_win_rate": null}, {"window": "late", "sample_size": 13, "win_rate": 15.38, "base_rate": 8.65, "edge_vs_base": 6.74, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-024
- **Category:** Barrier
- **Pattern Description:** At Flemington in Staying 2000+ races, barrier segment 9+ wins above the local base rate when the surface is Dry and field size is 13-16.
- **Query Used:** Runner-level win rates grouped by track, distance bucket, wet/dry group, barrier segment, and field-size bucket.
- **Tracks Affected:** Flemington
- **Conditions:** Staying 2000+ | surface=Dry | field_size=13-16 | barrier=9+
- **Winner Win Rate:** 17.14
- **Field Average Win Rate:** 7.07
- **Edge Magnitude:** 10.07
- **Sample Size:** 35
- **Sectionals Involved:** No
- **Data Quality Note:** Historical rail-position data is unavailable, so this is a barrier-only effect without rail interaction.
- **Model Gap Explanation:** Barrier features often get modelled too statically and miss the field-size x surface interaction.
- **Feature Engineering Fix:** Add barrier segment x field-size bucket x wet/dry interaction features by track.
- **Backtestable Rule:** IF track=Flemington AND barrier_segment=9+ AND field_size_bucket=13-16 AND wet_group=Dry THEN win rate is 10.07pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 10, "win_rate": 10.0, "base_rate": 9.39, "edge_vs_base": 0.61, "implied_win_rate": null}, {"window": "mid", "sample_size": 14, "win_rate": 21.43, "base_rate": 9.02, "edge_vs_base": 12.41, "implied_win_rate": null}, {"window": "late", "sample_size": 11, "win_rate": 18.18, "base_rate": 8.65, "edge_vs_base": 9.54, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-025
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Middle 1400-1800 races on Heavy, horses sitting 2-3 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Rosehill
- **Conditions:** Middle 1400-1800 | Heavy | 400m position 2-3
- **Winner Win Rate:** 20.45
- **Field Average Win Rate:** 10.63
- **Edge Magnitude:** 9.83
- **Sample Size:** 44
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Middle 1400-1800 AND going=Heavy AND pos_400m_bucket=2-3 THEN win rate is 9.83pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 16, "win_rate": 12.5, "base_rate": 8.89, "edge_vs_base": 3.61, "implied_win_rate": null}, {"window": "mid", "sample_size": 22, "win_rate": 27.27, "base_rate": 9.53, "edge_vs_base": 17.74, "implied_win_rate": null}, {"window": "late", "sample_size": 6, "win_rate": 16.67, "base_rate": 8.31, "edge_vs_base": 8.36, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-026
- **Category:** Barrier
- **Pattern Description:** At Caulfield in Staying 2000+ races, barrier segment 1-4 wins above the local base rate when the surface is Dry and field size is 17+.
- **Query Used:** Runner-level win rates grouped by track, distance bucket, wet/dry group, barrier segment, and field-size bucket.
- **Tracks Affected:** Caulfield
- **Conditions:** Staying 2000+ | surface=Dry | field_size=17+ | barrier=1-4
- **Winner Win Rate:** 15.0
- **Field Average Win Rate:** 5.41
- **Edge Magnitude:** 9.59
- **Sample Size:** 20
- **Sectionals Involved:** No
- **Data Quality Note:** Historical rail-position data is unavailable, so this is a barrier-only effect without rail interaction.
- **Model Gap Explanation:** Barrier features often get modelled too statically and miss the field-size x surface interaction.
- **Feature Engineering Fix:** Add barrier segment x field-size bucket x wet/dry interaction features by track.
- **Backtestable Rule:** IF track=Caulfield AND barrier_segment=1-4 AND field_size_bucket=17+ AND wet_group=Dry THEN win rate is 9.59pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 5, "win_rate": 0.0, "base_rate": 9.39, "edge_vs_base": -9.39, "implied_win_rate": null}, {"window": "late", "sample_size": 15, "win_rate": 20.0, "base_rate": 8.65, "edge_vs_base": 11.35, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.5

## A-027
- **Category:** Barrier
- **Pattern Description:** At Flemington in Middle 1400-1800 races, barrier segment 5-8 wins above the local base rate when the surface is Wet and field size is 9-12.
- **Query Used:** Runner-level win rates grouped by track, distance bucket, wet/dry group, barrier segment, and field-size bucket.
- **Tracks Affected:** Flemington
- **Conditions:** Middle 1400-1800 | surface=Wet | field_size=9-12 | barrier=5-8
- **Winner Win Rate:** 18.42
- **Field Average Win Rate:** 9.09
- **Edge Magnitude:** 9.33
- **Sample Size:** 38
- **Sectionals Involved:** No
- **Data Quality Note:** Historical rail-position data is unavailable, so this is a barrier-only effect without rail interaction.
- **Model Gap Explanation:** Barrier features often get modelled too statically and miss the field-size x surface interaction.
- **Feature Engineering Fix:** Add barrier segment x field-size bucket x wet/dry interaction features by track.
- **Backtestable Rule:** IF track=Flemington AND barrier_segment=5-8 AND field_size_bucket=9-12 AND wet_group=Wet THEN win rate is 9.33pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 20, "win_rate": 15.0, "base_rate": 9.39, "edge_vs_base": 5.61, "implied_win_rate": null}, {"window": "mid", "sample_size": 13, "win_rate": 23.08, "base_rate": 9.02, "edge_vs_base": 14.06, "implied_win_rate": null}, {"window": "late", "sample_size": 5, "win_rate": 20.0, "base_rate": 8.65, "edge_vs_base": 11.35, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-028
- **Category:** Sectional
- **Pattern Description:** At Eagle Farm in Staying 2000+ races on Good, runners closing their last 200m in 11.97s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Eagle Farm
- **Conditions:** Staying 2000+ | Good | Handicap/BM
- **Winner Win Rate:** 17.5
- **Field Average Win Rate:** 8.23
- **Edge Magnitude:** 9.27
- **Sample Size:** 40
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Eagle Farm AND distance_bucket=Staying 2000+ AND going=Good AND last_200m_time <= 11.97s THEN win rate is 9.27pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 30, "win_rate": 10.0, "base_rate": 8.33, "edge_vs_base": 1.67, "implied_win_rate": null}, {"window": "mid", "sample_size": 7, "win_rate": 14.29, "base_rate": 9.71, "edge_vs_base": 4.58, "implied_win_rate": null}, {"window": "late", "sample_size": 3, "win_rate": 100.0, "base_rate": 9.07, "edge_vs_base": 90.93, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-029
- **Category:** Barrier
- **Pattern Description:** At Flemington in Staying 2000+ races, barrier segment 1-4 wins above the local base rate when the surface is Dry and field size is 9-12.
- **Query Used:** Runner-level win rates grouped by track, distance bucket, wet/dry group, barrier segment, and field-size bucket.
- **Tracks Affected:** Flemington
- **Conditions:** Staying 2000+ | surface=Dry | field_size=9-12 | barrier=1-4
- **Winner Win Rate:** 17.65
- **Field Average Win Rate:** 9.2
- **Edge Magnitude:** 8.45
- **Sample Size:** 34
- **Sectionals Involved:** No
- **Data Quality Note:** Historical rail-position data is unavailable, so this is a barrier-only effect without rail interaction.
- **Model Gap Explanation:** Barrier features often get modelled too statically and miss the field-size x surface interaction.
- **Feature Engineering Fix:** Add barrier segment x field-size bucket x wet/dry interaction features by track.
- **Backtestable Rule:** IF track=Flemington AND barrier_segment=1-4 AND field_size_bucket=9-12 AND wet_group=Dry THEN win rate is 8.45pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 21, "win_rate": 19.05, "base_rate": 9.39, "edge_vs_base": 9.66, "implied_win_rate": null}, {"window": "late", "sample_size": 13, "win_rate": 15.38, "base_rate": 8.65, "edge_vs_base": 6.74, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-030
- **Category:** Sectional
- **Pattern Description:** At Randwick in Sprint <=1200 races on Good, runners closing their last 200m in 10.90s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Randwick
- **Conditions:** Sprint <=1200 | Good | Handicap/BM
- **Winner Win Rate:** 17.95
- **Field Average Win Rate:** 9.52
- **Edge Magnitude:** 8.42
- **Sample Size:** 39
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Randwick AND distance_bucket=Sprint <=1200 AND going=Good AND last_200m_time <= 10.90s THEN win rate is 8.42pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 17, "win_rate": 17.65, "base_rate": 8.33, "edge_vs_base": 9.32, "implied_win_rate": null}, {"window": "mid", "sample_size": 5, "win_rate": 20.0, "base_rate": 9.71, "edge_vs_base": 10.29, "implied_win_rate": null}, {"window": "late", "sample_size": 17, "win_rate": 17.65, "base_rate": 9.07, "edge_vs_base": 8.58, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-031
- **Category:** Sectional
- **Pattern Description:** In slow early-pace races at Randwick, contested runners in Middle 1400-1800 events outperform the race base rate.
- **Query Used:** Race-level opening_600m derived from splits_json segment ladder; hot/slow pace labels assigned by 25th/75th cohort quantiles, then runner win rates compared by pace_role_600m.
- **Tracks Affected:** Randwick
- **Conditions:** Middle 1400-1800 | Soft | pace=slow | role=contested
- **Winner Win Rate:** 16.13
- **Field Average Win Rate:** 7.88
- **Edge Magnitude:** 8.25
- **Sample Size:** 31
- **Sectionals Involved:** Yes - opening 600m derived from split ladder + 600m in-run position
- **Data Quality Note:** Opening 600m is approximated from segment times; off-pace/contested uses 600m position as a pace proxy.
- **Model Gap Explanation:** Static running-style features miss the interaction between race-level tempo and where the horse sat when pressure developed.
- **Feature Engineering Fix:** Add pace-scenario interaction features: hot/slow race flag x 600m pace-role bucket.
- **Backtestable Rule:** IF track=Randwick AND pace_class=slow AND pace_role=contested THEN win rate is 8.25pp above same-race base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 10, "win_rate": 10.0, "base_rate": 9.39, "edge_vs_base": 0.61, "implied_win_rate": null}, {"window": "mid", "sample_size": 18, "win_rate": 16.67, "base_rate": 9.02, "edge_vs_base": 7.65, "implied_win_rate": null}, {"window": "late", "sample_size": 3, "win_rate": 33.33, "base_rate": 8.65, "edge_vs_base": 24.69, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-032
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Staying 2000+ races on Soft, horses sitting 2-3 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Rosehill
- **Conditions:** Staying 2000+ | Soft | 400m position 2-3
- **Winner Win Rate:** 16.67
- **Field Average Win Rate:** 8.7
- **Edge Magnitude:** 7.97
- **Sample Size:** 30
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Staying 2000+ AND going=Soft AND pos_400m_bucket=2-3 THEN win rate is 7.97pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 8, "win_rate": 25.0, "base_rate": 8.89, "edge_vs_base": 16.11, "implied_win_rate": null}, {"window": "mid", "sample_size": 12, "win_rate": 16.67, "base_rate": 9.53, "edge_vs_base": 7.14, "implied_win_rate": null}, {"window": "late", "sample_size": 10, "win_rate": 10.0, "base_rate": 8.31, "edge_vs_base": 1.69, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-033
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Sprint <=1200 races on Soft, horses sitting 1 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Rosehill
- **Conditions:** Sprint <=1200 | Soft | 400m position 1
- **Winner Win Rate:** 16.67
- **Field Average Win Rate:** 8.92
- **Edge Magnitude:** 7.75
- **Sample Size:** 30
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Sprint <=1200 AND going=Soft AND pos_400m_bucket=1 THEN win rate is 7.75pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 5, "win_rate": 20.0, "base_rate": 8.89, "edge_vs_base": 11.11, "implied_win_rate": null}, {"window": "mid", "sample_size": 19, "win_rate": 21.05, "base_rate": 9.53, "edge_vs_base": 11.52, "implied_win_rate": null}, {"window": "late", "sample_size": 6, "win_rate": 0.0, "base_rate": 8.31, "edge_vs_base": -8.31, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## A-034
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Staying 2000+ races on Good, horses sitting 2-3 at the 400m marker win above the local base rate.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times; 400m positions read from splits_json['400m'].position and grouped in pandas by track/distance_bucket/going_group/pos_400m_bucket.
- **Tracks Affected:** Rosehill
- **Conditions:** Staying 2000+ | Good | 400m position 2-3
- **Winner Win Rate:** 17.39
- **Field Average Win Rate:** 9.9
- **Edge Magnitude:** 7.49
- **Sample Size:** 23
- **Sectionals Involved:** Yes - 400m in-run position from splits_json
- **Data Quality Note:** Requires splits_json position fields; cohorts without 400m positions are excluded.
- **Model Gap Explanation:** A generic running-style label can miss track- and tempo-specific strike zones at the 400m marker.
- **Feature Engineering Fix:** Add 400m position bucket win-rate priors by track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Staying 2000+ AND going=Good AND pos_400m_bucket=2-3 THEN win rate is 7.49pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 4, "win_rate": 25.0, "base_rate": 8.89, "edge_vs_base": 16.11, "implied_win_rate": null}, {"window": "mid", "sample_size": 9, "win_rate": 22.22, "base_rate": 9.53, "edge_vs_base": 12.69, "implied_win_rate": null}, {"window": "late", "sample_size": 10, "win_rate": 10.0, "base_rate": 8.31, "edge_vs_base": 1.69, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-035
- **Category:** Barrier
- **Pattern Description:** At Caulfield in Middle 1400-1800 races, barrier segment 1-4 wins above the local base rate when the surface is Wet and field size is 9-12.
- **Query Used:** Runner-level win rates grouped by track, distance bucket, wet/dry group, barrier segment, and field-size bucket.
- **Tracks Affected:** Caulfield
- **Conditions:** Middle 1400-1800 | surface=Wet | field_size=9-12 | barrier=1-4
- **Winner Win Rate:** 16.67
- **Field Average Win Rate:** 9.59
- **Edge Magnitude:** 7.08
- **Sample Size:** 30
- **Sectionals Involved:** No
- **Data Quality Note:** Historical rail-position data is unavailable, so this is a barrier-only effect without rail interaction.
- **Model Gap Explanation:** Barrier features often get modelled too statically and miss the field-size x surface interaction.
- **Feature Engineering Fix:** Add barrier segment x field-size bucket x wet/dry interaction features by track.
- **Backtestable Rule:** IF track=Caulfield AND barrier_segment=1-4 AND field_size_bucket=9-12 AND wet_group=Wet THEN win rate is 7.08pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "mid", "sample_size": 20, "win_rate": 15.0, "base_rate": 9.02, "edge_vs_base": 5.98, "implied_win_rate": null}, {"window": "late", "sample_size": 10, "win_rate": 20.0, "base_rate": 8.65, "edge_vs_base": 11.35, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-036
- **Category:** Sectional
- **Pattern Description:** At Rosehill in Middle 1400-1800 races on Heavy, runners closing their last 200m in 11.49s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Rosehill
- **Conditions:** Middle 1400-1800 | Heavy | Handicap/BM
- **Winner Win Rate:** 18.18
- **Field Average Win Rate:** 11.45
- **Edge Magnitude:** 6.73
- **Sample Size:** 33
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Rosehill AND distance_bucket=Middle 1400-1800 AND going=Heavy AND last_200m_time <= 11.49s THEN win rate is 6.73pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 9, "win_rate": 11.11, "base_rate": 8.33, "edge_vs_base": 2.78, "implied_win_rate": null}, {"window": "mid", "sample_size": 20, "win_rate": 20.0, "base_rate": 9.71, "edge_vs_base": 10.29, "implied_win_rate": null}, {"window": "late", "sample_size": 4, "win_rate": 25.0, "base_rate": 9.07, "edge_vs_base": 15.93, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-037
- **Category:** Sectional
- **Pattern Description:** At Randwick in Middle 1400-1800 races on Good, runners closing their last 200m in 10.92s or faster win far more often than the field.
- **Query Used:** Runner-level SQL extract from race_results_history + sectional_times, then grouped in pandas by track/distance_bucket/going_group/class_bucket; threshold set at cohort 25th percentile of last_200m_time.
- **Tracks Affected:** Randwick
- **Conditions:** Middle 1400-1800 | Good | Group 2
- **Winner Win Rate:** 15.38
- **Field Average Win Rate:** 8.74
- **Edge Magnitude:** 6.65
- **Sample Size:** 26
- **Sectionals Involved:** Yes - last_200m_time
- **Data Quality Note:** Direct sectional timing from sectional_times; no lane-position data required.
- **Model Gap Explanation:** The model can underweight track- and going-specific late-speed thresholds when it only uses broad speed/pace features.
- **Feature Engineering Fix:** Add cohort-normalized fast-close flag: last_200m_time <= 25th percentile for track x distance bucket x going.
- **Backtestable Rule:** IF track=Randwick AND distance_bucket=Middle 1400-1800 AND going=Good AND last_200m_time <= 10.92s THEN win rate is 6.65pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 23, "win_rate": 13.04, "base_rate": 8.33, "edge_vs_base": 4.71, "implied_win_rate": null}, {"window": "late", "sample_size": 3, "win_rate": 33.33, "base_rate": 9.07, "edge_vs_base": 24.26, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-038
- **Category:** Sectional
- **Pattern Description:** At Rosehill, horses coming off a 2nd-5th finish with a personal-best last-200m split win next start above the cohort base rate.
- **Query Used:** Runner history ordered by horse_key/race_date; previous-run PB last_200m flagged in pandas, then next-start win rates compared against all runners coming off prior 2nd-5th finishes.
- **Tracks Affected:** Rosehill
- **Conditions:** class=Group 3 | prior finish 2nd-5th | prior PB last-200m
- **Winner Win Rate:** 15.38
- **Field Average Win Rate:** 10.26
- **Edge Magnitude:** 5.13
- **Sample Size:** 26
- **Sectionals Involved:** Yes - prior-run last_200m_time
- **Data Quality Note:** Uses horse_key continuity and available prior sectional history only.
- **Model Gap Explanation:** The current stack can miss latent fitness when a runner’s best closing split came in a losing run rather than a win.
- **Feature Engineering Fix:** Add previous-run PB closing-split flag plus prior finishing-position interaction.
- **Backtestable Rule:** IF prior_finish in 2-5 AND prior_run_last200_is_personal_best AND track=Rosehill THEN next-start win rate is 5.13pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 9, "win_rate": 11.11, "base_rate": 9.38, "edge_vs_base": 1.73, "implied_win_rate": null}, {"window": "mid", "sample_size": 3, "win_rate": 66.67, "base_rate": 9.04, "edge_vs_base": 57.63, "implied_win_rate": null}, {"window": "late", "sample_size": 14, "win_rate": 7.14, "base_rate": 8.63, "edge_vs_base": -1.49, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## A-039
- **Category:** Sectional
- **Pattern Description:** At Rosehill, horses coming off a 2nd-5th finish with a personal-best last-200m split win next start above the cohort base rate.
- **Query Used:** Runner history ordered by horse_key/race_date; previous-run PB last_200m flagged in pandas, then next-start win rates compared against all runners coming off prior 2nd-5th finishes.
- **Tracks Affected:** Rosehill
- **Conditions:** class=Group 2 | prior finish 2nd-5th | prior PB last-200m
- **Winner Win Rate:** 10.53
- **Field Average Win Rate:** 5.95
- **Edge Magnitude:** 4.57
- **Sample Size:** 19
- **Sectionals Involved:** Yes - prior-run last_200m_time
- **Data Quality Note:** Uses horse_key continuity and available prior sectional history only.
- **Model Gap Explanation:** The current stack can miss latent fitness when a runner’s best closing split came in a losing run rather than a win.
- **Feature Engineering Fix:** Add previous-run PB closing-split flag plus prior finishing-position interaction.
- **Backtestable Rule:** IF prior_finish in 2-5 AND prior_run_last200_is_personal_best AND track=Rosehill THEN next-start win rate is 4.57pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 9, "win_rate": 11.11, "base_rate": 9.38, "edge_vs_base": 1.73, "implied_win_rate": null}, {"window": "mid", "sample_size": 4, "win_rate": 0.0, "base_rate": 9.04, "edge_vs_base": -9.04, "implied_win_rate": null}, {"window": "late", "sample_size": 6, "win_rate": 16.67, "base_rate": 8.63, "edge_vs_base": 8.03, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## A-040
- **Category:** Sectional
- **Pattern Description:** In slow early-pace races at Randwick, contested runners in Sprint <=1200 events outperform the race base rate.
- **Query Used:** Race-level opening_600m derived from splits_json segment ladder; hot/slow pace labels assigned by 25th/75th cohort quantiles, then runner win rates compared by pace_role_600m.
- **Tracks Affected:** Randwick
- **Conditions:** Sprint <=1200 | Good | pace=slow | role=contested
- **Winner Win Rate:** 13.33
- **Field Average Win Rate:** 9.9
- **Edge Magnitude:** 3.44
- **Sample Size:** 30
- **Sectionals Involved:** Yes - opening 600m derived from split ladder + 600m in-run position
- **Data Quality Note:** Opening 600m is approximated from segment times; off-pace/contested uses 600m position as a pace proxy.
- **Model Gap Explanation:** Static running-style features miss the interaction between race-level tempo and where the horse sat when pressure developed.
- **Feature Engineering Fix:** Add pace-scenario interaction features: hot/slow race flag x 600m pace-role bucket.
- **Backtestable Rule:** IF track=Randwick AND pace_class=slow AND pace_role=contested THEN win rate is 3.44pp above same-race base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 20, "win_rate": 5.0, "base_rate": 9.39, "edge_vs_base": -4.39, "implied_win_rate": null}, {"window": "late", "sample_size": 10, "win_rate": 30.0, "base_rate": 8.65, "edge_vs_base": 21.35, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.5

## A-041
- **Category:** Sectional
- **Pattern Description:** At Eagle Farm, horses coming off a 2nd-5th finish with a personal-best last-200m split win next start above the cohort base rate.
- **Query Used:** Runner history ordered by horse_key/race_date; previous-run PB last_200m flagged in pandas, then next-start win rates compared against all runners coming off prior 2nd-5th finishes.
- **Tracks Affected:** Eagle Farm
- **Conditions:** class=3Y  HCP | prior finish 2nd-5th | prior PB last-200m
- **Winner Win Rate:** 12.0
- **Field Average Win Rate:** 9.38
- **Edge Magnitude:** 2.62
- **Sample Size:** 25
- **Sectionals Involved:** Yes - prior-run last_200m_time
- **Data Quality Note:** Uses horse_key continuity and available prior sectional history only.
- **Model Gap Explanation:** The current stack can miss latent fitness when a runner’s best closing split came in a losing run rather than a win.
- **Feature Engineering Fix:** Add previous-run PB closing-split flag plus prior finishing-position interaction.
- **Backtestable Rule:** IF prior_finish in 2-5 AND prior_run_last200_is_personal_best AND track=Eagle Farm THEN next-start win rate is 2.62pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 10, "win_rate": 10.0, "base_rate": 9.38, "edge_vs_base": 0.62, "implied_win_rate": null}, {"window": "mid", "sample_size": 8, "win_rate": 12.5, "base_rate": 9.04, "edge_vs_base": 3.46, "implied_win_rate": null}, {"window": "late", "sample_size": 7, "win_rate": 14.29, "base_rate": 8.63, "edge_vs_base": 5.65, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## A-042
- **Category:** Sectional
- **Pattern Description:** At Eagle Farm, horses coming off a 2nd-5th finish with a personal-best last-200m split win next start above the cohort base rate.
- **Query Used:** Runner history ordered by horse_key/race_date; previous-run PB last_200m flagged in pandas, then next-start win rates compared against all runners coming off prior 2nd-5th finishes.
- **Tracks Affected:** Eagle Farm
- **Conditions:** class=2Y  HCP | prior finish 2nd-5th | prior PB last-200m
- **Winner Win Rate:** 11.76
- **Field Average Win Rate:** 9.52
- **Edge Magnitude:** 2.24
- **Sample Size:** 17
- **Sectionals Involved:** Yes - prior-run last_200m_time
- **Data Quality Note:** Uses horse_key continuity and available prior sectional history only.
- **Model Gap Explanation:** The current stack can miss latent fitness when a runner’s best closing split came in a losing run rather than a win.
- **Feature Engineering Fix:** Add previous-run PB closing-split flag plus prior finishing-position interaction.
- **Backtestable Rule:** IF prior_finish in 2-5 AND prior_run_last200_is_personal_best AND track=Eagle Farm THEN next-start win rate is 2.24pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 12, "win_rate": 0.0, "base_rate": 9.38, "edge_vs_base": -9.38, "implied_win_rate": null}, {"window": "mid", "sample_size": 3, "win_rate": 0.0, "base_rate": 9.04, "edge_vs_base": -9.04, "implied_win_rate": null}, {"window": "late", "sample_size": 2, "win_rate": 100.0, "base_rate": 8.63, "edge_vs_base": 91.37, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.333

## A-043
- **Category:** Sectional
- **Pattern Description:** At Eagle Farm, horses coming off a 2nd-5th finish with a personal-best last-200m split win next start above the cohort base rate.
- **Query Used:** Runner history ordered by horse_key/race_date; previous-run PB last_200m flagged in pandas, then next-start win rates compared against all runners coming off prior 2nd-5th finishes.
- **Tracks Affected:** Eagle Farm
- **Conditions:** class=CL3 | prior finish 2nd-5th | prior PB last-200m
- **Winner Win Rate:** 10.53
- **Field Average Win Rate:** 8.33
- **Edge Magnitude:** 2.19
- **Sample Size:** 19
- **Sectionals Involved:** Yes - prior-run last_200m_time
- **Data Quality Note:** Uses horse_key continuity and available prior sectional history only.
- **Model Gap Explanation:** The current stack can miss latent fitness when a runner’s best closing split came in a losing run rather than a win.
- **Feature Engineering Fix:** Add previous-run PB closing-split flag plus prior finishing-position interaction.
- **Backtestable Rule:** IF prior_finish in 2-5 AND prior_run_last200_is_personal_best AND track=Eagle Farm THEN next-start win rate is 2.19pp above base.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 4, "win_rate": 25.0, "base_rate": 9.38, "edge_vs_base": 15.62, "implied_win_rate": null}, {"window": "mid", "sample_size": 4, "win_rate": 0.0, "base_rate": 9.04, "edge_vs_base": -9.04, "implied_win_rate": null}, {"window": "late", "sample_size": 11, "win_rate": 9.09, "base_rate": 8.63, "edge_vs_base": 0.46, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

# Agent 2 Findings

## Notes
- Usable T-10/SP-ish Betfair snapshot coverage query did not return rows in scope; advanced market-window analysis will be skipped or downgraded.
- Historical rail-position columns are not present in the production DB; rail-position analysis is conditional and currently skipped.
- Trainer is not stored in race_results_history or training_view_v2 at 18-month scale; trainer-specific findings are marked unavailable unless a broader source is added.
- Trainer-level first-up, trainer-jockey synergy, apprentice-claim, and stable-intent findings are limited because trainer and claim fields are not present in race_results_history/training_view_v2 at 18-month scale.
- No track-month cohort passed the 85% Betfair mapping gate. Market findings are downgraded and mapping should be expanded via server/python/build_betfair_mapping.py before production use.

## B-001
- **Category:** Jockey
- **Pattern Description:** D.Gibbons materially outperforms their own dry-track strike rate on wet going.
- **Query Used:** Compute jockey wet-track vs dry-track win rates from race_results_history, then compare wet actual win% to Betfair-implied wet win%.
- **Race Types Affected:** Wet-track races
- **Conditions:** Wet track rides for D.Gibbons
- **Winner Win Rate:** 17.82
- **Field Average Win Rate:** 9.39
- **Betfair Implied Win Rate:** 10.37
- **Edge Magnitude:** 7.45
- **Sample Size:** 101
- **Market Efficiency Note:** systematically wrong
- **Model Gap Explanation:** The model likely captures jockey quality generally, but not wet-track-specialist deltas relative to each jockey's baseline.
- **Feature Engineering Fix:** Add jockey wet-track residual performance feature using rolling wet vs dry delta.
- **Backtestable Rule:** IF jockey = D.Gibbons AND going is Soft/Heavy THEN actual win rate beats implied by 7.45 points.
- **Priority:** 3
- **Window Stats:** [{"window": "early", "sample_size": 22, "win_rate": 18.18, "base_rate": 8.97, "edge_vs_base": 9.21, "implied_win_rate": null}, {"window": "mid", "sample_size": 49, "win_rate": 14.29, "base_rate": 9.21, "edge_vs_base": 5.08, "implied_win_rate": null}, {"window": "late", "sample_size": 30, "win_rate": 23.33, "base_rate": 10.01, "edge_vs_base": 13.33, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## B-002
- **Category:** Jockey
- **Pattern Description:** A.Adkins materially outperforms their own dry-track strike rate on wet going.
- **Query Used:** Compute jockey wet-track vs dry-track win rates from race_results_history, then compare wet actual win% to Betfair-implied wet win%.
- **Race Types Affected:** Wet-track races
- **Conditions:** Wet track rides for A.Adkins
- **Winner Win Rate:** 13.16
- **Field Average Win Rate:** 9.39
- **Betfair Implied Win Rate:** 8.37
- **Edge Magnitude:** 4.79
- **Sample Size:** 76
- **Market Efficiency Note:** systematically wrong
- **Model Gap Explanation:** The model likely captures jockey quality generally, but not wet-track-specialist deltas relative to each jockey's baseline.
- **Feature Engineering Fix:** Add jockey wet-track residual performance feature using rolling wet vs dry delta.
- **Backtestable Rule:** IF jockey = A.Adkins AND going is Soft/Heavy THEN actual win rate beats implied by 4.79 points.
- **Priority:** 4
- **Window Stats:** [{"window": "early", "sample_size": 7, "win_rate": 0.0, "base_rate": 8.97, "edge_vs_base": -8.97, "implied_win_rate": null}, {"window": "mid", "sample_size": 44, "win_rate": 15.91, "base_rate": 9.21, "edge_vs_base": 6.7, "implied_win_rate": null}, {"window": "late", "sample_size": 25, "win_rate": 12.0, "base_rate": 10.01, "edge_vs_base": 1.99, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## B-003
- **Category:** Jockey
- **Pattern Description:** M.Harley materially outperforms their own dry-track strike rate on wet going.
- **Query Used:** Compute jockey wet-track vs dry-track win rates from race_results_history, then compare wet actual win% to Betfair-implied wet win%.
- **Race Types Affected:** Wet-track races
- **Conditions:** Wet track rides for M.Harley
- **Winner Win Rate:** 25.0
- **Field Average Win Rate:** 9.39
- **Betfair Implied Win Rate:** 14.88
- **Edge Magnitude:** 10.12
- **Sample Size:** 36
- **Market Efficiency Note:** systematically wrong
- **Model Gap Explanation:** The model likely captures jockey quality generally, but not wet-track-specialist deltas relative to each jockey's baseline.
- **Feature Engineering Fix:** Add jockey wet-track residual performance feature using rolling wet vs dry delta.
- **Backtestable Rule:** IF jockey = M.Harley AND going is Soft/Heavy THEN actual win rate beats implied by 10.12 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 17, "win_rate": 29.41, "base_rate": 8.97, "edge_vs_base": 20.44, "implied_win_rate": null}, {"window": "mid", "sample_size": 3, "win_rate": 0.0, "base_rate": 9.21, "edge_vs_base": -9.21, "implied_win_rate": null}, {"window": "late", "sample_size": 16, "win_rate": 25.0, "base_rate": 10.01, "edge_vs_base": 14.99, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## B-004
- **Category:** Jockey
- **Pattern Description:** A.J.Mallyon materially outperforms their own dry-track strike rate on wet going.
- **Query Used:** Compute jockey wet-track vs dry-track win rates from race_results_history, then compare wet actual win% to Betfair-implied wet win%.
- **Race Types Affected:** Wet-track races
- **Conditions:** Wet track rides for A.J.Mallyon
- **Winner Win Rate:** 22.45
- **Field Average Win Rate:** 9.39
- **Betfair Implied Win Rate:** 13.21
- **Edge Magnitude:** 9.23
- **Sample Size:** 49
- **Market Efficiency Note:** systematically wrong
- **Model Gap Explanation:** The model likely captures jockey quality generally, but not wet-track-specialist deltas relative to each jockey's baseline.
- **Feature Engineering Fix:** Add jockey wet-track residual performance feature using rolling wet vs dry delta.
- **Backtestable Rule:** IF jockey = A.J.Mallyon AND going is Soft/Heavy THEN actual win rate beats implied by 9.23 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 31, "win_rate": 29.03, "base_rate": 8.97, "edge_vs_base": 20.06, "implied_win_rate": null}, {"window": "mid", "sample_size": 5, "win_rate": 0.0, "base_rate": 9.21, "edge_vs_base": -9.21, "implied_win_rate": null}, {"window": "late", "sample_size": 13, "win_rate": 15.38, "base_rate": 10.01, "edge_vs_base": 5.38, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## B-005
- **Category:** Jockey
- **Pattern Description:** J.L.Melham materially outperforms their own dry-track strike rate on wet going.
- **Query Used:** Compute jockey wet-track vs dry-track win rates from race_results_history, then compare wet actual win% to Betfair-implied wet win%.
- **Race Types Affected:** Wet-track races
- **Conditions:** Wet track rides for J.L.Melham
- **Winner Win Rate:** 16.67
- **Field Average Win Rate:** 9.39
- **Betfair Implied Win Rate:** 14.84
- **Edge Magnitude:** 1.83
- **Sample Size:** 48
- **Market Efficiency Note:** partially right
- **Model Gap Explanation:** The model likely captures jockey quality generally, but not wet-track-specialist deltas relative to each jockey's baseline.
- **Feature Engineering Fix:** Add jockey wet-track residual performance feature using rolling wet vs dry delta.
- **Backtestable Rule:** IF jockey = J.L.Melham AND going is Soft/Heavy THEN actual win rate beats implied by 1.83 points.
- **Priority:** 5
- **Window Stats:** [{"window": "mid", "sample_size": 22, "win_rate": 18.18, "base_rate": 9.21, "edge_vs_base": 8.98, "implied_win_rate": null}, {"window": "late", "sample_size": 26, "win_rate": 15.38, "base_rate": 10.01, "edge_vs_base": 5.38, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## B-006
- **Category:** Market
- **Pattern Description:** The Betfair price bracket 6.01-9.00 is miscalibrated versus actual win frequency.
- **Query Used:** Bucket runners by Betfair price bracket and compare actual win% vs average implied win% within each bracket.
- **Race Types Affected:** All mapped race types
- **Conditions:** Betfair price bracket = 6.01-9.00
- **Winner Win Rate:** 10.79
- **Field Average Win Rate:** 9.47
- **Betfair Implied Win Rate:** 13.07
- **Edge Magnitude:** -2.28
- **Sample Size:** 1603
- **Market Efficiency Note:** largely correct
- **Model Gap Explanation:** If the model is calibrated too closely to market odds, it will inherit these bracket-level biases instead of exploiting them.
- **Feature Engineering Fix:** Add market price bracket residual calibration feature, track-specific where coverage is strong.
- **Backtestable Rule:** IF Betfair price bracket = 6.01-9.00 THEN actual win rate differs from implied by -2.28 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 522, "win_rate": 11.49, "base_rate": 9.42, "edge_vs_base": 2.08, "implied_win_rate": null}, {"window": "mid", "sample_size": 547, "win_rate": 10.24, "base_rate": 9.24, "edge_vs_base": 0.99, "implied_win_rate": null}, {"window": "late", "sample_size": 534, "win_rate": 10.67, "base_rate": 9.76, "edge_vs_base": 0.91, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## B-007
- **Category:** Market
- **Pattern Description:** The Betfair price bracket 9.01-14.00 is miscalibrated versus actual win frequency.
- **Query Used:** Bucket runners by Betfair price bracket and compare actual win% vs average implied win% within each bracket.
- **Race Types Affected:** All mapped race types
- **Conditions:** Betfair price bracket = 9.01-14.00
- **Winner Win Rate:** 6.33
- **Field Average Win Rate:** 9.47
- **Betfair Implied Win Rate:** 8.73
- **Edge Magnitude:** -2.4
- **Sample Size:** 1847
- **Market Efficiency Note:** largely correct
- **Model Gap Explanation:** If the model is calibrated too closely to market odds, it will inherit these bracket-level biases instead of exploiting them.
- **Feature Engineering Fix:** Add market price bracket residual calibration feature, track-specific where coverage is strong.
- **Backtestable Rule:** IF Betfair price bracket = 9.01-14.00 THEN actual win rate differs from implied by -2.40 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 638, "win_rate": 5.64, "base_rate": 9.42, "edge_vs_base": -3.77, "implied_win_rate": null}, {"window": "mid", "sample_size": 625, "win_rate": 6.88, "base_rate": 9.24, "edge_vs_base": -2.36, "implied_win_rate": null}, {"window": "late", "sample_size": 584, "win_rate": 6.51, "base_rate": 9.76, "edge_vs_base": -3.25, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.0

## B-008
- **Category:** Market
- **Pattern Description:** The Betfair price bracket 4.51-6.00 is miscalibrated versus actual win frequency.
- **Query Used:** Bucket runners by Betfair price bracket and compare actual win% vs average implied win% within each bracket.
- **Race Types Affected:** All mapped race types
- **Conditions:** Betfair price bracket = 4.51-6.00
- **Winner Win Rate:** 16.42
- **Field Average Win Rate:** 9.47
- **Betfair Implied Win Rate:** 18.93
- **Edge Magnitude:** -2.51
- **Sample Size:** 1145
- **Market Efficiency Note:** largely correct
- **Model Gap Explanation:** If the model is calibrated too closely to market odds, it will inherit these bracket-level biases instead of exploiting them.
- **Feature Engineering Fix:** Add market price bracket residual calibration feature, track-specific where coverage is strong.
- **Backtestable Rule:** IF Betfair price bracket = 4.51-6.00 THEN actual win rate differs from implied by -2.51 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 373, "win_rate": 14.75, "base_rate": 9.42, "edge_vs_base": 5.33, "implied_win_rate": null}, {"window": "mid", "sample_size": 397, "win_rate": 15.11, "base_rate": 9.24, "edge_vs_base": 5.87, "implied_win_rate": null}, {"window": "late", "sample_size": 375, "win_rate": 19.47, "base_rate": 9.76, "edge_vs_base": 9.71, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## B-009
- **Category:** Market
- **Pattern Description:** Short-priced favourites underperform materially at Eagle Farm on Good ground.
- **Query Used:** Filter Betfair favourites under 2.50 and compare actual vs implied win rates by track and going group.
- **Race Types Affected:** Favourite cohorts under 2.50
- **Conditions:** Eagle Farm, Good ground
- **Winner Win Rate:** 47.46
- **Field Average Win Rate:** 42.73
- **Betfair Implied Win Rate:** 51.28
- **Edge Magnitude:** -3.83
- **Sample Size:** 59
- **Market Efficiency Note:** systematically wrong
- **Model Gap Explanation:** If STRIDE anchors too close to Betfair in favourite-heavy scenarios, it will miss profitable favourite-opposition setups.
- **Feature Engineering Fix:** Add favourite vulnerability adjustment by track × going × distance bucket.
- **Backtestable Rule:** IF favourite < 2.50 at Eagle Farm on Good ground THEN actual win rate undershoots implied by 3.83 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 19, "win_rate": 36.84, "base_rate": 40.91, "edge_vs_base": -4.07, "implied_win_rate": null}, {"window": "mid", "sample_size": 16, "win_rate": 62.5, "base_rate": 43.64, "edge_vs_base": 18.86, "implied_win_rate": null}, {"window": "late", "sample_size": 24, "win_rate": 45.83, "base_rate": 43.64, "edge_vs_base": 2.2, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## B-010
- **Category:** Market
- **Pattern Description:** The Betfair price bracket 2.01-3.00 is miscalibrated versus actual win frequency.
- **Query Used:** Bucket runners by Betfair price bracket and compare actual win% vs average implied win% within each bracket.
- **Race Types Affected:** All mapped race types
- **Conditions:** Betfair price bracket = 2.01-3.00
- **Winner Win Rate:** 34.3
- **Field Average Win Rate:** 9.47
- **Betfair Implied Win Rate:** 38.73
- **Edge Magnitude:** -4.43
- **Sample Size:** 519
- **Market Efficiency Note:** overpricing this cohort materially
- **Model Gap Explanation:** If the model is calibrated too closely to market odds, it will inherit these bracket-level biases instead of exploiting them.
- **Feature Engineering Fix:** Add market price bracket residual calibration feature, track-specific where coverage is strong.
- **Backtestable Rule:** IF Betfair price bracket = 2.01-3.00 THEN actual win rate differs from implied by -4.43 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 144, "win_rate": 32.64, "base_rate": 9.42, "edge_vs_base": 23.22, "implied_win_rate": null}, {"window": "mid", "sample_size": 159, "win_rate": 33.96, "base_rate": 9.24, "edge_vs_base": 24.72, "implied_win_rate": null}, {"window": "late", "sample_size": 216, "win_rate": 35.65, "base_rate": 9.76, "edge_vs_base": 25.89, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## B-011
- **Category:** Market
- **Pattern Description:** The Betfair price bracket 1.51-2.00 is miscalibrated versus actual win frequency.
- **Query Used:** Bucket runners by Betfair price bracket and compare actual win% vs average implied win% within each bracket.
- **Race Types Affected:** All mapped race types
- **Conditions:** Betfair price bracket = 1.51-2.00
- **Winner Win Rate:** 51.16
- **Field Average Win Rate:** 9.47
- **Betfair Implied Win Rate:** 56.1
- **Edge Magnitude:** -4.93
- **Sample Size:** 129
- **Market Efficiency Note:** overpricing this cohort materially
- **Model Gap Explanation:** If the model is calibrated too closely to market odds, it will inherit these bracket-level biases instead of exploiting them.
- **Feature Engineering Fix:** Add market price bracket residual calibration feature, track-specific where coverage is strong.
- **Backtestable Rule:** IF Betfair price bracket = 1.51-2.00 THEN actual win rate differs from implied by -4.93 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 46, "win_rate": 60.87, "base_rate": 9.42, "edge_vs_base": 51.45, "implied_win_rate": null}, {"window": "mid", "sample_size": 30, "win_rate": 50.0, "base_rate": 9.24, "edge_vs_base": 40.76, "implied_win_rate": null}, {"window": "late", "sample_size": 53, "win_rate": 43.4, "base_rate": 9.76, "edge_vs_base": 33.64, "implied_win_rate": null}]
- **Temporal Stability Score:** 1.0

## B-012
- **Category:** Market
- **Pattern Description:** Short-priced favourites underperform materially at Randwick on Good ground.
- **Query Used:** Filter Betfair favourites under 2.50 and compare actual vs implied win rates by track and going group.
- **Race Types Affected:** Favourite cohorts under 2.50
- **Conditions:** Randwick, Good ground
- **Winner Win Rate:** 46.51
- **Field Average Win Rate:** 42.73
- **Betfair Implied Win Rate:** 51.7
- **Edge Magnitude:** -5.19
- **Sample Size:** 43
- **Market Efficiency Note:** systematically wrong
- **Model Gap Explanation:** If STRIDE anchors too close to Betfair in favourite-heavy scenarios, it will miss profitable favourite-opposition setups.
- **Feature Engineering Fix:** Add favourite vulnerability adjustment by track × going × distance bucket.
- **Backtestable Rule:** IF favourite < 2.50 at Randwick on Good ground THEN actual win rate undershoots implied by 5.19 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 25, "win_rate": 48.0, "base_rate": 40.91, "edge_vs_base": 7.09, "implied_win_rate": null}, {"window": "mid", "sample_size": 9, "win_rate": 22.22, "base_rate": 43.64, "edge_vs_base": -21.41, "implied_win_rate": null}, {"window": "late", "sample_size": 9, "win_rate": 66.67, "base_rate": 43.64, "edge_vs_base": 23.03, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.667

## B-013
- **Category:** Market
- **Pattern Description:** Short-priced favourites underperform materially at Rosehill on Good ground.
- **Query Used:** Filter Betfair favourites under 2.50 and compare actual vs implied win rates by track and going group.
- **Race Types Affected:** Favourite cohorts under 2.50
- **Conditions:** Rosehill, Good ground
- **Winner Win Rate:** 37.5
- **Field Average Win Rate:** 42.73
- **Betfair Implied Win Rate:** 49.64
- **Edge Magnitude:** -12.14
- **Sample Size:** 32
- **Market Efficiency Note:** systematically wrong
- **Model Gap Explanation:** If STRIDE anchors too close to Betfair in favourite-heavy scenarios, it will miss profitable favourite-opposition setups.
- **Feature Engineering Fix:** Add favourite vulnerability adjustment by track × going × distance bucket.
- **Backtestable Rule:** IF favourite < 2.50 at Rosehill on Good ground THEN actual win rate undershoots implied by 12.14 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 10, "win_rate": 30.0, "base_rate": 40.91, "edge_vs_base": -10.91, "implied_win_rate": null}, {"window": "mid", "sample_size": 1, "win_rate": 100.0, "base_rate": 43.64, "edge_vs_base": 56.36, "implied_win_rate": null}, {"window": "late", "sample_size": 21, "win_rate": 38.1, "base_rate": 43.64, "edge_vs_base": -5.54, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.333

## B-014
- **Category:** Market
- **Pattern Description:** Short-priced favourites underperform materially at Flemington on Good ground.
- **Query Used:** Filter Betfair favourites under 2.50 and compare actual vs implied win rates by track and going group.
- **Race Types Affected:** Favourite cohorts under 2.50
- **Conditions:** Flemington, Good ground
- **Winner Win Rate:** 32.43
- **Field Average Win Rate:** 42.73
- **Betfair Implied Win Rate:** 49.44
- **Edge Magnitude:** -17.01
- **Sample Size:** 37
- **Market Efficiency Note:** systematically wrong
- **Model Gap Explanation:** If STRIDE anchors too close to Betfair in favourite-heavy scenarios, it will miss profitable favourite-opposition setups.
- **Feature Engineering Fix:** Add favourite vulnerability adjustment by track × going × distance bucket.
- **Backtestable Rule:** IF favourite < 2.50 at Flemington on Good ground THEN actual win rate undershoots implied by 17.01 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 12, "win_rate": 41.67, "base_rate": 40.91, "edge_vs_base": 0.76, "implied_win_rate": null}, {"window": "mid", "sample_size": 13, "win_rate": 23.08, "base_rate": 43.64, "edge_vs_base": -20.56, "implied_win_rate": null}, {"window": "late", "sample_size": 12, "win_rate": 33.33, "base_rate": 43.64, "edge_vs_base": -10.3, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.333

## B-015
- **Category:** Market
- **Pattern Description:** Short-priced favourites underperform materially at Eagle Farm on Soft ground.
- **Query Used:** Filter Betfair favourites under 2.50 and compare actual vs implied win rates by track and going group.
- **Race Types Affected:** Favourite cohorts under 2.50
- **Conditions:** Eagle Farm, Soft ground
- **Winner Win Rate:** 32.56
- **Field Average Win Rate:** 42.73
- **Betfair Implied Win Rate:** 51.49
- **Edge Magnitude:** -18.93
- **Sample Size:** 43
- **Market Efficiency Note:** systematically wrong
- **Model Gap Explanation:** If STRIDE anchors too close to Betfair in favourite-heavy scenarios, it will miss profitable favourite-opposition setups.
- **Feature Engineering Fix:** Add favourite vulnerability adjustment by track × going × distance bucket.
- **Backtestable Rule:** IF favourite < 2.50 at Eagle Farm on Soft ground THEN actual win rate undershoots implied by 18.93 points.
- **Priority:** 5
- **Window Stats:** [{"window": "early", "sample_size": 21, "win_rate": 38.1, "base_rate": 40.91, "edge_vs_base": -2.81, "implied_win_rate": null}, {"window": "mid", "sample_size": 14, "win_rate": 28.57, "base_rate": 43.64, "edge_vs_base": -15.06, "implied_win_rate": null}, {"window": "late", "sample_size": 8, "win_rate": 25.0, "base_rate": 43.64, "edge_vs_base": -18.64, "implied_win_rate": null}]
- **Temporal Stability Score:** 0.0

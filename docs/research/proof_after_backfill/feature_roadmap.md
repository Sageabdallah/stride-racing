# STRIDE Feature Roadmap

## prior_pb_close_3to5_market_underreaction
- **Description:** Flag for runners whose prior start was a personal-best last-200m close, prior finish was 3rd-5th, and current market price sits in the 6.0-12.0 overlay band.
- **Derived From:** race_results_history + sectional_times.last_200m_time + training_view_v2.market_odds/model_probability
- **Expected Impact:** Directly addresses the sectional × market underreaction synthesis rule.
- **Implementation Complexity:** Medium
- **Priority Rank:** 1

## sectional_x_market_s_001
- **Description:** Prior run = personal-best last-200m close, prior finish 3rd-5th, current Betfair odds 6.0-12.0
- **Derived From:** See corresponding finding card query path.
- **Expected Impact:** Captures S-001 (synthesis) in the next STRIDE model sprint.
- **Implementation Complexity:** Medium
- **Priority Rank:** 2

## sectional_a_001
- **Description:** At Eagle Farm in Middle 1400-1800 races on Good, runners closing their last 200m in 11.96s or faster win far more often than the field.
- **Derived From:** See corresponding finding card query path.
- **Expected Impact:** Captures A-001 (agent1) in the next STRIDE model sprint.
- **Implementation Complexity:** Low
- **Priority Rank:** 3

## sectional_a_002
- **Description:** At Randwick in Middle 1400-1800 races on Good, horses sitting 2-3 at the 400m marker win above the local base rate.
- **Derived From:** See corresponding finding card query path.
- **Expected Impact:** Captures A-002 (agent1) in the next STRIDE model sprint.
- **Implementation Complexity:** Low
- **Priority Rank:** 4

## jockey_b_001
- **Description:** D.Gibbons materially outperforms their own dry-track strike rate on wet going.
- **Derived From:** See corresponding finding card query path.
- **Expected Impact:** Captures B-001 (agent2) in the next STRIDE model sprint.
- **Implementation Complexity:** Low
- **Priority Rank:** 5

## barrier_a_003
- **Description:** At Eagle Farm in Sprint <=1200 races, barrier segment 5-8 wins above the local base rate when the surface is Dry and field size is 17+.
- **Derived From:** See corresponding finding card query path.
- **Expected Impact:** Captures A-003 (agent1) in the next STRIDE model sprint.
- **Implementation Complexity:** Low
- **Priority Rank:** 6

## sectional_a_004
- **Description:** At Eagle Farm in Middle 1400-1800 races on Soft, runners closing their last 200m in 12.29s or faster win far more often than the field.
- **Derived From:** See corresponding finding card query path.
- **Expected Impact:** Captures A-004 (agent1) in the next STRIDE model sprint.
- **Implementation Complexity:** Low
- **Priority Rank:** 7

## sectional_a_005
- **Description:** At Eagle Farm in Sprint <=1200 races on Soft, runners closing their last 200m in 11.96s or faster win far more often than the field.
- **Derived From:** See corresponding finding card query path.
- **Expected Impact:** Captures A-005 (agent1) in the next STRIDE model sprint.
- **Implementation Complexity:** Low
- **Priority Rank:** 8

## sectional_a_006
- **Description:** At Randwick in Middle 1400-1800 races on Soft, horses sitting 1 at the 400m marker win above the local base rate.
- **Derived From:** See corresponding finding card query path.
- **Expected Impact:** Captures A-006 (agent1) in the next STRIDE model sprint.
- **Implementation Complexity:** Low
- **Priority Rank:** 9

## cross_signal_a-001_b-009
- **Description:** Composite feature combining A-001 and B-009 where track/condition overlap is strongest.
- **Derived From:** Agent 1 + Agent 2 source tables for the matched cohorts.
- **Expected Impact:** Elevates multi-angle patterns that the model is likely missing simultaneously.
- **Implementation Complexity:** Medium
- **Priority Rank:** 10

## cross_signal_a-002_b-012
- **Description:** Composite feature combining A-002 and B-012 where track/condition overlap is strongest.
- **Derived From:** Agent 1 + Agent 2 source tables for the matched cohorts.
- **Expected Impact:** Elevates multi-angle patterns that the model is likely missing simultaneously.
- **Implementation Complexity:** Medium
- **Priority Rank:** 11

## cross_signal_a-004_b-015
- **Description:** Composite feature combining A-004 and B-015 where track/condition overlap is strongest.
- **Derived From:** Agent 1 + Agent 2 source tables for the matched cohorts.
- **Expected Impact:** Elevates multi-angle patterns that the model is likely missing simultaneously.
- **Implementation Complexity:** Medium
- **Priority Rank:** 12

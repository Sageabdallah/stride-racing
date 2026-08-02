# Synthesis Report

## Cross-Agent Pattern Matches
- `A-001` + `B-009` | score=5 | tracks=Eagle Farm | conditions=good
- `A-002` + `B-012` | score=5 | tracks=Randwick | conditions=good
- `A-004` + `B-015` | score=5 | tracks=Eagle Farm | conditions=soft
- `A-005` + `B-015` | score=5 | tracks=Eagle Farm | conditions=soft
- `A-008` + `B-012` | score=5 | tracks=Randwick | conditions=good
- `A-011` + `B-013` | score=5 | tracks=Rosehill | conditions=good
- `A-012` + `B-012` | score=5 | tracks=Randwick | conditions=good
- `A-016` + `B-013` | score=5 | tracks=Rosehill | conditions=good
- `A-017` + `B-013` | score=5 | tracks=Rosehill | conditions=good
- `A-018` + `B-015` | score=5 | tracks=Eagle Farm | conditions=soft

## Sectional x Market Underreaction Test
- Sample size: 81
- Actual win rate: 13.58%
- Betfair implied win rate: 11.99%
- Mean model probability: 12.06%
- Edge vs market: 1.59pp

## Combined Priority Table
- `S-001` (synthesis) | priority=2 | edge=1.59 | sample=81 | Prior run = personal-best last-200m close, prior finish 3rd-5th, current Betfair odds 6.0-12.0
- `A-001` (agent1) | priority=3 | edge=8.17 | sample=78 | At Eagle Farm in Middle 1400-1800 races on Good, runners closing their last 200m in 11.96s or faster win far more often than the field.
- `A-002` (agent1) | priority=3 | edge=7.9 | sample=116 | At Randwick in Middle 1400-1800 races on Good, horses sitting 2-3 at the 400m marker win above the local base rate.
- `B-001` (agent2) | priority=3 | edge=7.45 | sample=101 | D.Gibbons materially outperforms their own dry-track strike rate on wet going.
- `A-003` (agent1) | priority=3 | edge=7.39 | sample=121 | At Eagle Farm in Sprint <=1200 races, barrier segment 5-8 wins above the local base rate when the surface is Dry and field size is 17+.
- `A-004` (agent1) | priority=4 | edge=14.24 | sample=62 | At Eagle Farm in Middle 1400-1800 races on Soft, runners closing their last 200m in 12.29s or faster win far more often than the field.
- `A-005` (agent1) | priority=4 | edge=11.59 | sample=55 | At Eagle Farm in Sprint <=1200 races on Soft, runners closing their last 200m in 11.96s or faster win far more often than the field.
- `A-006` (agent1) | priority=4 | edge=10.78 | sample=57 | At Randwick in Middle 1400-1800 races on Soft, horses sitting 1 at the 400m marker win above the local base rate.
- `A-007` (agent1) | priority=4 | edge=9.51 | sample=61 | At Rosehill in Middle 1400-1800 races on Soft, runners closing their last 200m in 11.17s or faster win far more often than the field.
- `A-008` (agent1) | priority=4 | edge=8.76 | sample=58 | At Randwick in Middle 1400-1800 races on Good, horses sitting 1 at the 400m marker win above the local base rate.
- `A-009` (agent1) | priority=4 | edge=7.8 | sample=60 | At Flemington in Middle 1400-1800 races, barrier segment 5-8 wins above the local base rate when the surface is Dry and field size is 17+.
- `A-010` (agent1) | priority=4 | edge=6.89 | sample=66 | At Randwick in Sprint <=1200 races, barrier segment 1-4 wins above the local base rate when the surface is Dry and field size is 17+.
- `A-011` (agent1) | priority=4 | edge=6.24 | sample=57 | At Rosehill in Middle 1400-1800 races on Good, runners closing their last 200m in 11.04s or faster win far more often than the field.
- `B-002` (agent2) | priority=4 | edge=4.79 | sample=76 | A.Adkins materially outperforms their own dry-track strike rate on wet going.
- `A-012` (agent1) | priority=5 | edge=21.08 | sample=32 | At Randwick in Sprint <=1200 races on Good, runners closing their last 200m in 10.71s or faster win far more often than the field.


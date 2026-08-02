# Forward validation registry (task 09)

Append-only pre-registration ledger. One hypothesis, one window B, never
re-tested on overlapping windows. Selection criteria use tip-time prices
only; SP appears in settlement and CLV columns. Corrections are new entries
referencing the old. A band may be quoted in outputs only with a PASS here
(enforced by ship_criteria.gate_registry_pass, wired 2026-08-02).

Success criterion for every entry unless stated otherwise: lower 95 percent
CI bound of net ROI above zero AND mean CLV above zero over at least 200
window-B bets, computed by validate_forward.py with no free parameters.

## Entries

### VR-001: current production band (registered 2026-08-02, before any window-B outcome existed)

| Field | Value |
|---|---|
| Hypothesis | The production selection band is net-profitable at tip-time prices |
| Exact rule | Tip-time price 2.00 to 15.00 (Betfair, price_source betfair*), edge >= 3 percentage points vs de-vigged market prob (STRIDE_DEVIG method at registration: proportional), flat 1u stake (STRIDE_FLAT_STAKING on), crowd gate-only on |
| Price source | runner_odds_snapshots tip_time rows (task 04); never SP |
| Window A (selection) | All data to 2026-08-01 (the rules above were fixed before window B opened) |
| Window B (validation) | 2026-08-02 to 2026-09-13 inclusive (first 6 weeks of tip-time capture; disjoint from and later than A) |
| n expected | ~150 to 250 settled bets (6 bets/day cap, ~5 race days/week) |
| Success criterion | lower 95 CI of net ROI > 0 AND mean CLV > 0 over >= 200 B bets; < 200 bets = INSUFFICIENT_SAMPLE, window extends day-for-day, never re-selected |
| Status | REGISTERED, window B open, no outcomes examined at registration |

## Graveyard

(none yet; FAILed rules land here, kept and documented, never re-tested on
overlapping windows)

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

### VR-001-C1: correction to VR-001 — INVALIDATED (appended 2026-08-02, before any window-B outcome was examined)

VR-001's text above stands unedited. This entry corrects its **status**.

| Field | Value |
|---|---|
| Corrects | VR-001 |
| New status | **INVALIDATED — window never validly ran. Not a FAIL. Not graveyarded.** |
| Reason | The consensus pillar was already non-functional at registration. `claude-sonnet-4-20250514` retired 2026-06-15; `git log -S` shows the id was introduced in `8f9a1f8` (2026-05-19) and never changed on `main`; every extraction call 404'd, the agent wrote all-zero-mention scores and exited 0. VR-001 was registered 2026-08-02, 48 days after retirement. With `crowd_score` all-zero, the crowd gate vetoed against an all-zero vector on every race, so the realised bet population was not the one the rule describes. |
| Evidence | [VR-001-invalidation.md](VR-001-invalidation.md) — self-contained, commands and outputs inline |
| Why not graveyarded | The graveyard holds rules tested and FAILed. This rule was never tested; filing it there would assert a result never obtained and bar a successor that has not been disproven. |
| Guard | VR-001's `REGISTRY` entry and its no-peek guard are left in place, unreadable until 2026-09-14 as registered. Nothing is relaxed. |
| Successor | VR-002 |
| Not amended | `docs/project_retrain_gate.md` — none of the five gates in `gate_status.py` reads VR-001 or `validate_forward`, so its dates remain true statements about the retrain decision |

### VR-002: successor to VR-001 (registered 2026-08-02, before any window-B outcome existed)

| Field | Value |
|---|---|
| Hypothesis | The production selection band is net-profitable at tip-time prices — VR-001's hypothesis, re-registered against a working consensus pillar |
| Exact rule | Unchanged from VR-001: tip-time price 2.00 to 15.00 (Betfair, price_source betfair*), edge >= 3 percentage points vs de-vigged market prob, flat 1u stake, crowd gate-only on |
| De-vig method | `proportional` at registration, unchanged from VR-001 and **deliberately not flipped** (the task-10 guardrail in `market_prob.devig_method` forbids ad-hoc changes). See [market-baseline-negative-control.md](market-baseline-negative-control.md) — this rule's edge term inherits the same favourite–longshot exposure, and the negative control must be run against this window before its result is quoted. |
| Price source | runner_odds_snapshots tip_time rows; never SP |
| Window A (selection) | All data to the window-B open date; the rule was fixed before B opened and is byte-identical to VR-001's |
| Window B (validation) | **opens: _to be filled at deploy_** — the date the repaired consensus agent is first executed by the scheduled task, **not** its commit date. The bet population changes when the deployed task picks the change up, which is a later and different instant. Close = open + 42 days inclusive. |
| System-health precondition | Consensus preflight green and a non-zero-yield consensus run on the open date. If the open date produces `zero_yield`, the window does not open that day. |
| Calendar precondition | **A 42-day window is not 42 observation days.** Roughly 37% of days carry no target-track racing at all — 28 of the 75 days to 2026-08-03, measured against `race_results_history` and recorded in the `download_racecards.py` module docstring — concentrated on Mondays, Tuesdays, Thursdays and Sundays. A 42-day window therefore yields **~26 observation days, clustered on Wednesdays and Saturdays**. Registered here as a precondition rather than discovered later as a revision: it is a fact about the Australian racing calendar, known before the window opens, and the honest place for it is the registration. Two consequences follow and neither is a change to the rule. First, the open date must itself be a day with target-track racing — a quiet day produces zero bets and cannot satisfy the system-health precondition above, so it cannot open the window. Second, the ~26 observation days are what must carry the n below; if they do not, that is INSUFFICIENT_SAMPLE under the existing criterion and the window extends day-for-day, which is already the registered behaviour. |
| n expected | ~150 to 250 settled bets |
| Success criterion | lower 95 CI of net ROI > 0 AND mean CLV > 0 over >= 200 B bets; < 200 = INSUFFICIENT_SAMPLE, window extends day-for-day, never re-selected |
| Status | **DRAFT — window B not yet open.** Becomes REGISTERED when the open date is filled at deploy. |

## Graveyard

(none yet; FAILed rules land here, kept and documented, never re-tested on
overlapping windows. Note VR-001 is **not** here — see VR-001-C1: it was
invalidated, not failed.)

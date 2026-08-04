# Forward validation registry (task 09)

Append-only pre-registration ledger. One hypothesis, one window B, never
re-tested on overlapping windows. Selection criteria use tip-time prices
only; SP appears in settlement and CLV columns. Corrections are new entries
referencing the old.

A band may be quoted in outputs only with a PASS here. **This is a rule
observed by the operator, not a mechanism.** `ship_criteria.gate_registry_pass`
exists and is unit-tested, but grep finds exactly two references to it in the
repository — its own definition at `ship_criteria.py:388` and
`tests/test_validate_forward.py:11`. No production path calls it, so nothing
in the code prevents a band being quoted without a registry PASS. The earlier
wording here claimed it was "enforced ... wired 2026-08-02"; that was untrue
when written and is corrected rather than deleted, because the gap it hid is
the point. It becomes a mechanism when a caller in the serve path consults it
before publishing a band; until then this line is a promise, and should be
read as one.

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
| Price source | **What the code delivers, not what was previously claimed.** `selection_ledger.persist_rows` writes `price_taken` from `pick["odds"]` (`selection_ledger.py:149`) and `price_source` as `pick.get("price_source") or ("betfair" if has_real_market_odds else "none")` (`:181`). Three consequences are registered here rather than discovered later. (a) The tip-time price is whatever priced the pick — `betfair_prices.fetch_price_map` tries the live Exchange and falls back to `runner_odds_snapshots` (`betfair_prices.py:163-182`), and `backfill_ledger_prices.py` fills any still-null price from tip_time snapshots, tagging `betfair_delayed_backfill`. So "runner_odds_snapshots tip_time rows" was the fallback, never the sole source: on 2026-08-02, 8 of 14 rows carried `betfair` and 6 carried `betfair_delayed_backfill`. (b) The literal tag `betfair` is **both** a genuine Exchange tag and the untagged default whenever `has_real_market_odds` is truthy, so a row tagged `betfair` is not by itself evidence of an Exchange price. (c) `validate_forward.rows_matching` filters only on the tag being in the allowlist (`validate_forward.py:70`); `none` is excluded and such a row leaves the sample entirely rather than merely lacking CLV. **Never SP** still holds and is enforced — `price_close`/`sp` are written only by settlement. This row is softened to the truth rather than the claim; the stronger claim becomes registrable when `price_source` distinguishes a genuine Exchange tag from the default, at which point that is a new entry, not an edit here. |
| Window A (selection) | All data to the window-B open date; the rule was fixed before B opened and is byte-identical to VR-001's |
| Window B (validation) | **opens: _to be filled at deploy_** — the date the repaired consensus agent is first executed by the scheduled task, **not** its commit date. The bet population changes when the deployed task picks the change up, which is a later and different instant. **Closes on sample, not on date** (amended 2026-08-04, before open — see the amendment note below): window B closes on the race day that carries the n-th qualifying settled bet, where n is the minimum below. A fixed 42-day close was registered on the assumption that 42 days could carry the n; the arithmetic in "n expected" shows it cannot, so the close condition is restated in the terms that actually bound it. **Hard stop 2026-12-31**: if the minimum has not accrued by then, the entry resolves INSUFFICIENT_SAMPLE, is reported as such, and is never re-selected or re-tested on any overlapping window. |
| System-health precondition | Consensus preflight green and a non-zero-yield consensus run on the open date. If the open date produces `zero_yield`, the window does not open that day. |
| Calendar precondition | **A 42-day window is not 42 observation days.** Roughly 37% of days carry no target-track racing at all — 28 of the 75 days to 2026-08-03, measured against `race_results_history` and recorded in the `download_racecards.py` module docstring — concentrated on Mondays, Tuesdays, Thursdays and Sundays. A 42-day window therefore yields **~26 observation days, clustered on Wednesdays and Saturdays**. Registered here as a precondition rather than discovered later as a revision: it is a fact about the Australian racing calendar, known before the window opens, and the honest place for it is the registration. Two consequences follow and neither is a change to the rule. First, the open date must itself be a day with target-track racing — a quiet day produces zero bets and cannot satisfy the system-health precondition above, so it cannot open the window. Second, the ~26 observation days are what must carry the n below; if they do not, that is INSUFFICIENT_SAMPLE under the existing criterion and the window extends day-for-day, which is already the registered behaviour. |
| n expected | **The arithmetic, shown, because the previous figure had none under it.** Exposure is capped at `STRIDE_MAX_BETS_PER_DAY=6` and `STRIDE_MAX_BETS_PER_TRACK=2` (`staking_controls.py:155-157`), so the per-day ceiling is `min(6, 2 × target_tracks_racing)`. Replaying the actual target-track counts of the trailing 30 days (2026-07-05..2026-08-03) against those caps gives **72 bets per 30 days = 2.40 bets per calendar day, as a ceiling** — it assumes every observation day fills its cap and every capped slot finds a qualifying edge, so realised n will be lower. The previously registered "~150 to 250 settled bets" over 42 days was unreachable under the entry's own calendar precondition: 26 observation days × 6 = 156 maximum, and 156 < 200. At the measured ceiling, 200 bets takes **~83 calendar days**, i.e. roughly 2026-10-27 from an 2026-08-05 open, and later in the realistic case. |
| Success criterion | lower 95 CI of net ROI > 0 AND mean CLV > 0 over **>= 200** settled qualifying B bets. The minimum is **deliberately not lowered**: it is what makes a positive result evidence rather than noise, and lowering it to fit a calendar would be fitting the test to the data-collection rate. The window length moves instead. < 200 at the hard stop = INSUFFICIENT_SAMPLE, never re-selected. |
| September readout | An interim readout may be published on or after 2026-09-13 stating **only**: the entry's registered rule, the open date, n accrued so far, the per-day accrual rate, and the projected resolution date. It may **not** state ROI, CLV, strike rate, or any function of outcomes, and it is not a stopping rule — the entry resolves on n or on the hard stop, never on the readout. This exists so the pre-registration is presentable in September without being read early; `validate_forward.window_read_allowed` is untouched and still refuses outcome reads. |
| Status | **DRAFT — window B not yet open.** Becomes REGISTERED when the open date is filled at deploy. |

**Amendment note (2026-08-04, before window B opened, before any window-B
outcome existed).** Three rows above — Price source, Window B, n expected —
were amended today. VR-002 was DRAFT and unopened, so this is a correction to
an unregistered draft rather than a revision of a live registration; no
outcome data existed to see, and none was examined. The amendment is recorded
here in full rather than applied silently because the defect being fixed is
exactly the one that would otherwise recur: **a registration claim with no
mechanism under it.** All three were of that kind — a sample target the
calendar could not deliver, a price-provenance claim the code did not enforce,
and (in the header) an enforcement claim for a function nothing calls. Fixing
them after data existed would have voided the entry as evidence, which is why
it is done tonight. The exact rule, the de-vig method, the price band, the
edge threshold and the success criterion's statistical bar are **unchanged**.

## Graveyard

(none yet; FAILed rules land here, kept and documented, never re-tested on
overlapping windows. Note VR-001 is **not** here — see VR-001-C1: it was
invalidated, not failed.)

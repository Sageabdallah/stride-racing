# 09 — Forward validation protocol: pre-registration, disjoint windows, honest bands

**Wave:** 3 · **Depends on:** [01](01-ledger-clv-net-settlement.md), [02](02-backtest-statistics.md), and ≥4 weeks of [04](04-as-of-odds-snapshot.md) capture · **Blocks:** [12](12-retrain-rebaseline.md) (promotion gates consume this protocol) · **Risk:** low · **Type:** governance/measurement

## Goal

End garden-of-forking-paths: every strategy band, threshold, or gate rule is
selected on window A and validated exactly once on a disjoint forward window B at
tip-time prices, net of commission, with pre-registered criteria.

## Why (evidence)

- The live gate (`run_tips_pipeline.py:1812-1826`: $15 cap, edge ≥ 4/2.5/3 across
  bands) is a transcription of the winning in-sample band from a 6-way sweep on one
  6-week window (`examples/backtest_summary.json`); `backtest.py:562-597` additionally
  tunes the prob threshold on validation ROI; 13 configs swept at `:600-622`.
  Under a zero-edge null, P(any of 6 ≥ +12.3%) ≈ 80–93%.
- The selection band filter itself uses SP (`backtest_v2_metro.py:212-218`) — the
  price is only knowable after the jump, and live selection happens at morning prices.
- The repo already has the right instincts: ≥200-bet reportability floor
  (`selection_ledger.py:226` shadow tracker), `ship_criteria.py` NOT_REPORTABLE
  verdicts. This task makes them a *protocol*, not just plumbing.

## Scope

**In:** a pre-registration file + validation runner + promotion gate logic.
**Out:** retraining (→ [12](12-retrain-rebaseline.md)); changing current thresholds
(this task *tests* them; changes follow from evidence).

## Steps for Kimi Code

1. **`docs/validation/registry.md`** (new): the pre-registration ledger. Columns:
   hypothesis, exact rule (price source, band, edge formula, stake), window A
   (selection dates), window B (validation dates, disjoint + later), n expected,
   success criterion (lower-95%-CI of net ROI > 0 AND mean CLV > 0 over ≥200 B bets),
   status. First entry: the current production band ($2–$15 tip-time price, edge ≥ 3%
   vs de-vigged market prob at **tip-time** odds from [04](04-as-of-odds-snapshot.md), flat 1u).
2. **Validation runner** `server/python/validate_forward.py`: given a registry entry,
   pulls settled ledger rows ([01](01-ledger-clv-net-settlement.md)) in window B matching
   the rule's *tip-time* criteria (not SP), computes net ROI/CLV/stats via
   `roi_stats.py` ([02](02-backtest-statistics.md)), and emits PASS/FAIL/INSUFFICIENT_SAMPLE
   against the pre-registered criterion. No free parameters — the script must not
   "search" for a passing variant.
3. **Promotion gate.** Wire PASS/FAIL into `ship_criteria.py`: a band may be quoted
   in outputs (and used for stake sizing in [06](06-staking-and-risk-controls.md)) only
   with a registry PASS. FAIL → rule retired to the registry's graveyard section
   (kept, documented, never re-tested on overlapping windows).
4. **Kill-switch for the current gate.** If the current band FAILS its window-B test,
   the fallback is: bet nothing (publish NO_BET) until [12](12-retrain-rebaseline.md)
   re-derives bands on honest features — not a re-sweep on window B.
5. **Cadence.** New hypotheses enter the registry only with window A/B defined in
   advance; B windows never reused across hypotheses.

## Acceptance criteria

- [ ] Registry exists with the current production band as entry #1 (window B = first
      ≥4 weeks of [04](04-as-of-odds-snapshot.md) data).
- [ ] `validate_forward.py` reproduces its verdict from raw ledger rows; unit test on
      a synthetic PASS and FAIL case.
- [ ] `ship_criteria.py` blocks quoting a band with no PASS (test).
- [ ] A FAIL path unit test shows the NO_BET fallback.

## Rollout & flags

- No model flags. The protocol activates the day window-B data is sufficient.

## Guardrails

- One hypothesis, one window B. Never re-run a failed hypothesis on a shifted window.
- Selection criteria use tip-time price only; SP appears in settlement/CLV columns.
- The registry is append-only; corrections are new entries referencing the old.

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §2 (A4), §6 (what not to do)
- Machinery: [01](01-ledger-clv-net-settlement.md) · [02](02-backtest-statistics.md) · [04](04-as-of-odds-snapshot.md)
- Consumed by: [12](12-retrain-rebaseline.md) promotion gates.

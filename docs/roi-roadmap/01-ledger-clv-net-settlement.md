# 01 — Selection ledger ON, CLV capture, net-of-commission settlement

**Wave:** 1 · **Depends on:** nothing (start immediately) · **Blocks:** [06](06-staking-and-risk-controls.md), [07](07-crowd-gate-gate-only.md), [09](09-forward-validation-protocol.md), [10](10-execution-and-pricing.md), [11](11-place-and-each-way.md) · **Risk:** low · **Type:** plumbing/measurement

## Goal

Every tipped selection — including refused/NO_BET sets — is persisted with **both**
the price at tip time and the final SP, settled at the price taken, with commission
applied, and CLV computed weekly. This is the measurement spine for the entire
program: CLV reaches statistical significance in ~400 bets vs ~3,000–5,000 for ROI.

## Why (evidence)

- The ledger schema is already correct: `build_ledger_row` records `price_taken`,
  `sp`, and `clv_pct` (`server/python/selection_ledger.py:156-173`), with the
  leakage rulebook and `assert_as_of` guards (`selection_ledger.py:53-73`).
- But `persist_rows` is a **no-op unless `STRIDE_LEDGER_WRITE=true`**
  (`selection_ledger.py:294-295`) — so CLV is likely not captured in production at all.
- Commission is defaulted away: `ev_at_price(..., commission_rate=0.0)` at every call
  site (`selection_ledger.py:118`). Gross settlement in backtests
  (`backtest.py:674`, `backtest_v2_metro.py:317`). The audit: +12.3% gross becomes
  ~+4.1% at 8% MBR, ~+2.1% at 10% — two-thirds to five-sixths of the edge is commission.
- Three conflicting P&L definitions coexist: ledger settles at `price_taken`
  (`selection_ledger.py:183`), both backtesters settle at SP, and
  `shadow_pl_tracker.py:290` settles at SP with a silent fallback to tipped odds.

## Scope

**In:** enable + harden ledger writes; thread a commission parameter through EV and
settlement; unify the settlement contract; weekly CLV report.
**Out:** changing any staking (→ [06](06-staking-and-risk-controls.md)); changing
what gets tipped.

## Steps for Kimi Code

1. **Enable writes.** Set `STRIDE_LEDGER_WRITE=true` in the production env and add it
   to `.env.example` with a comment. Verify `persist_rows` actually inserts (smoke:
   `server/python/audit_write_smoke.py` if wired, else a direct insert-count query).
2. **Migration.** Apply/extend `migrations/selection_ledger.sql` so the table has:
   `price_taken NUMERIC NOT NULL`, `sp NUMERIC`, `clv_pct NUMERIC`,
   `commission_rate NUMERIC NOT NULL DEFAULT 0.08`, `settled_pnl NUMERIC`,
   `refused BOOLEAN NOT NULL DEFAULT false` (for NO_BET sets the gate rejected).
   Existing rows: `sp` backfill allowed from results; `price_taken` stays NULL
   (never fabricate it — mark `clv_pct` NULL when `price_taken` is NULL).
3. **Single settlement contract.** In `selection_ledger.py` and `shadow_pl_tracker.py`:
   settle at `price_taken` when present, else SP **with an explicit
   `settled_at_sp_fallback=true` column** (remove the silent fallback at
   `shadow_pl_tracker.py:290`). Net P&L: win = `(price−1)×(1−commission)`, loss = `−1`.
4. **Commission plumbing.** Replace every `commission_rate=0.0` default call site with
   `commission_rate=float(os.environ.get("STRIDE_COMMISSION_RATE", "0.08"))`. EV
   functions must compute net EV. Document the NSW/ACT 10% MBR case in `.env.example`.
5. **Weekly CLV report.** Extend `weekly_metrics` (`selection_ledger.py:220-241`) to
   emit: n bets, mean CLV, % CLV>0, net ROI, gross ROI — **keeping the existing
   ≥200-bet reportability floor** (values below the floor print as
   `INSUFFICIENT_SAMPLE`, not numbers).
6. **Refused-set capture.** Where the EV gate returns NO_BET
   (`run_tips_pipeline.py:1812-1826`), persist a ledger row with `refused=true` and
   the would-be price. (This makes gate quality measurable — currently unfalsifiable.)

## Acceptance criteria

- [ ] After one live race day: one ledger row per race (bet or refused), both prices
      populated, no silent fallbacks (`settled_at_sp_fallback` explicit).
- [ ] `weekly_metrics` output shows mean CLV + % positive with the 200-bet floor
      respected (print `INSUFFICIENT_SAMPLE` below 200).
- [ ] Re-running the 2026-03-04→04-18 metro window at 8% and 10% commission produces
      net ROI columns alongside gross in the summary JSON (use [02](02-backtest-statistics.md)'s
      new reporting once merged; until then a plain column is fine).
- [ ] Unit test: dead-heat and scratched-runner rows never settle at full SP unless
      `won==1` with no "dh" marker (note current dead-heat gap: only the NSW
      collector parses "dh", `nsw_xml_collector.py:250-253`).

## Rollout & flags

- Flags: `STRIDE_LEDGER_WRITE=true` (on immediately — measurement only),
  `STRIDE_COMMISSION_RATE` (default 0.08).
- Rollback: set `STRIDE_LEDGER_WRITE=false`. No model behaviour changes in this task.

## Guardrails

- Do **not** change staking, tiers, or thresholds here.
- Do **not** backfill `price_taken` from SP — CLV computed on SP==price-taken is
  definitionally zero and poisons the metric.
- Do **not** introduce a third settlement path; the contract is: taken price settles,
  SP stored for CLV.

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §2 (A2, A3), §3 (B4)
- Next consumers: [06](06-staking-and-risk-controls.md) (stakes from net EV),
  [09](09-forward-validation-protocol.md) (pre-registration uses ledger rows),
  [11](11-place-and-each-way.md) (place shadow rows)

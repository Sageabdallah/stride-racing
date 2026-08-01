# 01 — Live-day smoke checklist (acceptance #1)

Run once, after the first race day with the ledger write flag on. Every check
is a positive assertion — "the script ran" proves nothing (this repo has twice
been bitten by failures that present as slightly worse output).

## 0. Deploy (before the race day)

```sql
-- both migrations, in order
\i migrations/selection_ledger.sql
\i migrations/selection_ledger_net_settlement.sql
```

```bash
STRIDE_LEDGER_WRITE=true
STRIDE_COMMISSION_RATE=0.08   # 0.10 when settling NSW/ACT tote prices
```

## 1. After the tips pipeline runs (pre-race)

```sql
SELECT count(*) AS rows_written,
       count(*) FILTER (WHERE refused) AS refused_rows,
       count(*) FILTER (WHERE NOT refused) AS bet_rows,
       count(*) FILTER (WHERE price_taken IS NULL AND has_real_market_odds) AS missing_taken_price
FROM selection_ledger
WHERE race_date = '<DATE>';
```

- [ ] `rows_written` > 0 and roughly one row per tipped race (bet or refused).
- [ ] `missing_taken_price` = 0 — the `chk_selection_ledger_price_taken_new_rows`
      constraint makes a violation an insert error, but assert it anyway.
- [ ] `settled` is FALSE for all rows at this point (they are pending).

## 2. After results collection runs (post-race)

Watch stderr of `stride_results_collector.py` Step 5c:

- [ ] NO `[LEDGER] *** WARNING: ... net_settlement ...` line — that warning
      means the net-settlement migration is missing and settlement was skipped.
- [ ] `Selection ledger: N rows settled` with **N > 0**. N = 0 on a resulted
      day is a failure, not a quiet day.

```sql
SELECT count(*) AS total,
       count(*) FILTER (WHERE settled) AS settled_rows,
       count(*) FILTER (WHERE sp IS NOT NULL) AS with_sp,
       count(*) FILTER (WHERE clv_pct IS NOT NULL) AS with_clv,
       count(*) FILTER (WHERE settled_at_sp_fallback) AS sp_fallbacks,
       count(*) FILTER (WHERE settled AND pnl IS NULL) AS settled_without_pnl
FROM selection_ledger
WHERE race_date = '<DATE>' AND NOT refused;
```

- [ ] `with_sp` = `total` for resulted races — **both prices populated**.
- [ ] `with_clv` = rows where `price_taken IS NOT NULL` (CLV is NULL only when
      no taken price existed — never fabricated from SP).
- [ ] `sp_fallbacks` = rows with `price_taken IS NULL` exactly (explicit, never silent).
- [ ] `settled_without_pnl` = 0.
- [ ] Spot-check one winner by hand: `pnl = stake × (price_taken − 1) × (1 − commission_rate)`,
      NOT settled at SP, and `settled_pnl = pnl`.

## 3. Edge cases (when they occur)

- [ ] Dead-heated tipped winner: `settled = FALSE`, `pnl IS NULL`, sp captured —
      never booked at full SP.
- [ ] Scratched tipped runner: `pnl = 0`, `settled_at_sp_fallback = FALSE`.
- [ ] Refused rows: `pnl = 0` always; `clv_pct` populated so gate quality is measurable.

## 4. Weekly report

- [ ] `weekly_metrics` over the week's rows shows `roi_net_pct` alongside
      `roi_gross_pct`, and headline metrics print `INSUFFICIENT_SAMPLE` until
      n ≥ 200 — not numbers.

If any box fails: do NOT trust downstream CLV/ROI reads until fixed. Rollback
is `STRIDE_LEDGER_WRITE=false` (measurement only; nothing else depends on it).

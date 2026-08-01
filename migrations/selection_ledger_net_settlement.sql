-- selection_ledger: CLV + net-of-commission settlement (roi-roadmap task 01).
--
-- Extends migrations/selection_ledger.sql with the columns the single
-- settlement contract needs:
--   * sp / settled_pnl — canonical names for the closing price and the net
--     settled P&L. Backfilled from the existing price_close / pnl columns,
--     which remain as legacy aliases (SP backfill from results is allowed).
--   * commission_rate default 0.0 -> 0.08 — the old default booked gross P&L;
--     the audit shows +12.3% gross is ~+4.1% net at 8% MBR. Existing rows keep
--     their recorded rate (historical truth); only the default changes.
--   * refused — one row per race even when the EV gate says NO_BET, so gate
--     quality becomes measurable (currently unfalsifiable).
--   * settled_at_sp_fallback — the taken price settles; SP is used only when
--     no taken price exists, and that is always marked, never silent.
--
-- price_taken is NEVER backfilled from SP: CLV computed on SP==price-taken is
-- definitionally zero and poisons the metric. clv_pct stays NULL wherever
-- price_taken is NULL. New rows must carry a real tip-time price whenever a
-- real market quote existed (enforced by the NOT VALID check below: it
-- constrains new/updated rows without auditing history).

ALTER TABLE selection_ledger
    ADD COLUMN IF NOT EXISTS sp                     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS settled_pnl            DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS refused                BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS settled_at_sp_fallback BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE selection_ledger
    ALTER COLUMN commission_rate SET DEFAULT 0.08;

-- Backfill the canonical SP / settled-P&L columns from the legacy aliases
-- (results-derived SP is allowed; price_taken is deliberately untouched).
UPDATE selection_ledger SET sp = price_close WHERE sp IS NULL AND price_close IS NOT NULL;
UPDATE selection_ledger SET settled_pnl = pnl WHERE settled_pnl IS NULL AND pnl IS NOT NULL;

-- New rows: a real market quote implies a real taken price. NOT VALID so
-- existing rows (which may legitimately predate price capture) are not
-- audited; the constraint is enforced on every new insert/update.
ALTER TABLE selection_ledger
    ADD CONSTRAINT chk_selection_ledger_price_taken_new_rows
    CHECK (price_taken IS NOT NULL OR has_real_market_odds IS NOT TRUE) NOT VALID;

CREATE INDEX IF NOT EXISTS ix_selection_ledger_refused
    ON selection_ledger (refused, race_date);

COMMENT ON COLUMN selection_ledger.sp IS
    'Starting price (closing line). Stored for CLV only — it never settles a bet that was struck at price_taken. Canonical name; price_close is the legacy alias.';

COMMENT ON COLUMN selection_ledger.settled_pnl IS
    'Net-of-commission P&L at the settlement price (price_taken, or SP only when settled_at_sp_fallback). Canonical name; pnl is the legacy alias.';

COMMENT ON COLUMN selection_ledger.refused IS
    'TRUE when the EV gate returned NO_BET: the row records the would-be price at 0u so gate quality is measurable. Refused rows never book P&L.';

COMMENT ON COLUMN selection_ledger.settled_at_sp_fallback IS
    'TRUE when the row settled at SP because no price_taken existed. Explicit replacement for the silent sp-or-tipped-odds fallback removed from shadow_pl_tracker.';

COMMENT ON COLUMN selection_ledger.commission_rate IS
    'Commission on net winnings applied at settlement (win = (price-1)*(1-rate), loss = -1). Default 0.08 (8% MBR); use 0.10 for NSW/ACT tote. Pre-migration rows may carry 0.0 (gross) — that is historical truth, do not rewrite.';

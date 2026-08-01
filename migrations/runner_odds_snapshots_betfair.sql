-- Betfair exchange depth columns for runner_odds_snapshots (additive only).
--
-- Prerequisite: migrations/runner_odds_snapshots.sql (roi/04) must have been
-- applied first — this file only extends that table, it never redefines or
-- alters the columns roi/04 created.
--
-- Why: roi/04 stores one bookmaker price per row (decimal_odds). The Betfair
-- exchange capture (server/python/betfair_odds_snapshot.py) also carries
-- market depth — best back/lay price+size, last traded price, matched volume
-- — and the exchange's own identity keys (market_id, selection_id). These
-- land in nullable columns so every existing writer keeps working unchanged;
-- non-Betfair rows simply leave them NULL. decimal_odds itself holds the
-- best available back price (falling back to last traded), so the training
-- view join works identically for bookmaker and exchange rows.
--
-- Provenance stays in the existing source_api column ('betfair_delayed' /
-- 'betfair_live' — the value follows the app key's delayData flag from
-- getDeveloperAppKeys, so activating the live key needs no code change).
--
-- Idempotent: safe to re-run.

ALTER TABLE runner_odds_snapshots
    ADD COLUMN IF NOT EXISTS market_id         TEXT,
    ADD COLUMN IF NOT EXISTS selection_id      BIGINT,
    ADD COLUMN IF NOT EXISTS back_size         NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS lay_price         NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS lay_size          NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS last_price_traded NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS total_matched     NUMERIC(16,2);

-- The exchange-side natural key. Partial so bookmaker rows (NULL market_id)
-- are untouched; the table PK still handles their dedup.
CREATE UNIQUE INDEX IF NOT EXISTS ux_runner_odds_snapshots_betfair_key
    ON runner_odds_snapshots (market_id, selection_id, snapshot_kind, captured_at)
    WHERE market_id IS NOT NULL;

COMMENT ON COLUMN runner_odds_snapshots.market_id IS
    'Betfair market id (e.g. 1.234567890); NULL on non-exchange rows.';
COMMENT ON COLUMN runner_odds_snapshots.selection_id IS
    'Betfair runner selection id; NULL on non-exchange rows.';
COMMENT ON COLUMN runner_odds_snapshots.last_price_traded IS
    'Betfair lastPriceTraded at capture; decimal_odds holds best back (or this, when no back offer).';
COMMENT ON COLUMN runner_odds_snapshots.total_matched IS
    'Betfair matched volume on the runner at capture (AUD).';

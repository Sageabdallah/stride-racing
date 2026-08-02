-- price_source: which API the ledger row's price actually came through, so
-- Betfair prices are distinguishable from anything legacy at settlement and
-- in every P&L cut. Additive and idempotent.
--   betfair / betfair_delayed / betfair_snapshot_db  - live Exchange pricing
--   betfair_delayed_backfill                         - filled after the fact
--                                                      from tip_time snapshots
--   racecard_legacy                                  - old bookmaker arrays
--   none                                             - no real price existed
ALTER TABLE selection_ledger
    ADD COLUMN IF NOT EXISTS price_source TEXT;

-- BSP settlement provenance (unblocks roi-roadmap task 11).
--
-- sp itself was added by selection_ledger_net_settlement.sql; these columns
-- say WHERE a filled sp came from and WHY an unfilled one is empty, so a
-- NULL is never silent: "not published yet" and "no SP exists for this
-- runner" are different facts and each gets its own marker.
--
-- Fully idempotent (columns + index only, no constraints), safe to re-run.

ALTER TABLE selection_ledger
    ADD COLUMN IF NOT EXISTS sp_source TEXT,
    ADD COLUMN IF NOT EXISTS sp_status TEXT;

CREATE INDEX IF NOT EXISTS ix_selection_ledger_sp_status
    ON selection_ledger (sp_status, race_date);

COMMENT ON COLUMN selection_ledger.sp_source IS
    'Origin of sp: betfair_bsp_file now; betfair_cleared_orders once real bets flow. NULL = sp never filled.';

COMMENT ON COLUMN selection_ledger.sp_status IS
    'BSP_MATCHED | ZERO_BSP | NO_MATCH_IN_FILE | FILE_NOT_PUBLISHED. Set by bsp_settlement.py; NULL = the BSP pass has not seen the row yet.';

-- Betfair BSP corpus: every runner in every race, not just the ones we bet.
--
-- selection_ledger already gets an sp filled by bsp_settlement.py, but that
-- pass only touches rows we took a position on. The horses we passed over are
-- the ones that make the data useful: calibration asks whether a stated 19.5%
-- win probability really occurs 19.5% of the time, and that question can only
-- be answered across a whole field. Keeping the numerator and discarding the
-- denominator is why the model has never been calibrated against the market.
--
-- Source is the same free daily file bsp_settlement.py already reads
-- (promo.betfair.com/betfairsp/prices/dwbfpricesauswin<DDMMYYYY>.csv), so this
-- table inherits its provenance and its publication-lag handling rather than
-- inventing a second ingest path.

CREATE TABLE IF NOT EXISTS betfair_bsp_history (
    id BIGSERIAL PRIMARY KEY,
    race_date DATE NOT NULL,
    track TEXT NOT NULL,
    track_norm TEXT NOT NULL,
    race_number INTEGER,
    horse_name TEXT NOT NULL,
    horse_name_norm TEXT NOT NULL,
    bsp DOUBLE PRECISION,              -- 0.0 means the file carried no SP
    win_lose INTEGER,                  -- 1 winner, 0 loser, NULL unparseable
    source_file TEXT NOT NULL,         -- the DDMMYYYY-stamped file it came from
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Last write wins, so re-ingesting a corrected file is a no-op-or-fix
    -- rather than a duplicate. race_number is in the key because a horse can
    -- legitimately appear twice on a card at different meetings, and
    -- track_norm rather than track because the file's menu_hint spelling is
    -- not stable across years.
    UNIQUE (race_date, track_norm, race_number, horse_name_norm)
);

CREATE INDEX IF NOT EXISTS idx_bsp_history_date
    ON betfair_bsp_history (race_date);
CREATE INDEX IF NOT EXISTS idx_bsp_history_horse
    ON betfair_bsp_history (horse_name_norm, race_date);

-- Per-file ingest record. A corpus is only trustworthy if you can tell a day
-- that genuinely had 600 runners from a day whose file published truncated,
-- and a row count alone cannot: both look like "we got some rows". Recording
-- what the file claimed alongside what we stored makes the substitution
-- visible, which is the failure this repo keeps rediscovering.
CREATE TABLE IF NOT EXISTS betfair_bsp_ingest_log (
    race_date DATE PRIMARY KEY,
    source_file TEXT NOT NULL,
    rows_in_file INTEGER NOT NULL,     -- parsed rows hinted with this date
    rows_written INTEGER NOT NULL,     -- rows that reached the table
    harness_skipped INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,              -- OK | FILE_NOT_PUBLISHED | EMPTY_FOR_DATE
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

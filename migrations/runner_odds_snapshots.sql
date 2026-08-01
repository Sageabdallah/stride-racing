-- runner_odds_snapshots: append-only store of the odds that were actually
-- knowable at prediction time (ROI roadmap task 04).
--
-- Why a new table: the model trains on SP mapped into market_odds while live
-- inference only ever sees the morning racecard price, so the strongest
-- feature is systematically flatter offline than live. The fix starts with
-- measurement: capture the real tip-time market (all bookmakers, not just the
-- first) and a late T-5min snapshot, prospectively, forever. This table is
-- purely additive — nothing existing reads or writes it.
--
-- Written by:
--   * server/python/run_tips_pipeline.py   (snapshot_kind = 'tip_time')
--   * server/python/capture_late_odds.py   (snapshot_kind = 'late_t5')
--   * server/python/odds_movement.py-style jobs may add 'morning' / 'baseline'
--
-- Guardrails honoured here:
--   * Append-only. No UPDATEs ever — a trigger rejects them (best-effort; see
--     the DO block comment). Re-captures create new rows via a new
--     captured_at, never overwrite.
--   * Never backfill: rows are created by live capture only. SP must never be
--     inserted as a 'tip_time' row.
--
-- It is safe to run this migration before any writer is enabled.

CREATE TABLE IF NOT EXISTS runner_odds_snapshots (
    race_id          TEXT          NOT NULL,
    runner_id        TEXT          NOT NULL,
    snapshot_kind    TEXT          NOT NULL
                       CHECK (snapshot_kind IN ('tip_time', 'late_t5', 'morning', 'baseline')),
    bookmaker        TEXT          NOT NULL,   -- or 'median' for derived rows
    captured_at      TIMESTAMPTZ   NOT NULL,   -- actual capture instant, never assumed
    decimal_odds     NUMERIC(10,2) NOT NULL
                       CHECK (decimal_odds > 1.0),
    source_api       TEXT,                     -- e.g. 'theracingapi'
    seconds_to_jump  INTEGER,                  -- captured_at minus scheduled jump, seconds

    -- Denormalised join keys. The rest of the schema keys runners on
    -- (race_date, track, race_number, horse_name_norm); keeping them on the
    -- row lets the training view join without a lookup table. race_id /
    -- runner_id remain the identity columns the writers key on.
    race_date        DATE,
    track            TEXT,
    race_number      INTEGER,
    horse_name       TEXT,
    horse_name_norm  TEXT,

    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    PRIMARY KEY (race_id, runner_id, snapshot_kind, bookmaker, captured_at)
);

-- Coverage monitor and training-view join paths.
CREATE INDEX IF NOT EXISTS ix_runner_odds_snapshots_date_kind
    ON runner_odds_snapshots (race_date, snapshot_kind);

CREATE INDEX IF NOT EXISTS ix_runner_odds_snapshots_runner_join
    ON runner_odds_snapshots (race_date, race_number, track, horse_name_norm, snapshot_kind);

-- Append-only enforcement. The project's Postgres role may not have TRIGGER
-- privilege on this table (it is not the table owner on some hosts), so
-- creation is wrapped: if the role lacks privilege the migration still
-- succeeds and the append-only rule is then enforced by convention only
-- (writers never UPDATE). Idempotent: safe to re-run.
CREATE OR REPLACE FUNCTION runner_odds_snapshots_reject_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'runner_odds_snapshots is append-only: UPDATEs are rejected (capture a new row instead)';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_runner_odds_snapshots_append_only'
          AND tgrelid = 'runner_odds_snapshots'::regclass
    ) THEN
        CREATE TRIGGER trg_runner_odds_snapshots_append_only
            BEFORE UPDATE ON runner_odds_snapshots
            FOR EACH ROW EXECUTE FUNCTION runner_odds_snapshots_reject_update();
    END IF;
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE 'skipping append-only trigger on runner_odds_snapshots: role lacks TRIGGER privilege (append-only enforced by convention)';
END
$$;

COMMENT ON TABLE runner_odds_snapshots IS
    'Append-only per-runner odds captured at knowable moments (tip_time, late_t5, morning, baseline). Prospective only — never backfilled from SP. Written by run_tips_pipeline.py and capture_late_odds.py.';

COMMENT ON COLUMN runner_odds_snapshots.snapshot_kind IS
    'tip_time = market at the moment the tips pipeline priced the field; late_t5 = capture near jump-5min (actual offset in seconds_to_jump); morning/baseline = overnight reference captures.';

COMMENT ON COLUMN runner_odds_snapshots.seconds_to_jump IS
    'Actual seconds between captured_at and the scheduled jump. Negative values are pre-jump; honesty over coverage — a T-15 capture records ~-900, not 300.';

COMMENT ON COLUMN runner_odds_snapshots.bookmaker IS
    'Bookmaker name as returned by the source API. One row per bookmaker per capture — the first-bookmaker choice in extract_odds is a derived view, not the stored data.';

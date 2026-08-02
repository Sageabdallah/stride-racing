-- race_schedule natural key (WP-3 finding). mc_api.log_race_schedule and
-- seed_race_schedule.py both write ON CONFLICT (track, race_number,
-- race_date), but no such constraint ever existed, so every one of those
-- inserts has errored and mc_api's bare try/except swallowed it. Same
-- failure class as the prediction_audit arbiter bug. Zero duplicate keys
-- exist at apply time (verified 2026-08-02). Idempotent.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_race_schedule_track_race_date'
    ) THEN
        ALTER TABLE race_schedule
            ADD CONSTRAINT uq_race_schedule_track_race_date
            UNIQUE (track, race_number, race_date);
    END IF;
END $$;

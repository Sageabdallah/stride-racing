-- prediction_audit: create the unique key its upsert has always required.
--
-- mc_api.log_prediction_audit inserts with
--   ON CONFLICT (track, race_number, race_date, horse_name) DO UPDATE ...
-- but the live table was created without any unique constraint on those
-- columns, and PostgreSQL rejects such an INSERT outright ("there is no
-- unique or exclusion constraint matching the ON CONFLICT specification").
-- Combined with debug-only error handling, that made EVERY audit insert
-- fail silently — the root cause of the near-empty prediction_audit table
-- found by the audit-smoke workflow on 2026-07-13 (docs/12 §4c).
--
-- log_prediction_audit now self-heals by creating this index once per
-- process; this migration is the explicit, reviewable version of the same
-- fix and also clears any pre-existing duplicates that would block it.

-- 1. Deduplicate on the upsert key (keeps one row per key — with the
--    current 260-row table this is immaterial; rows with NULL race_date
--    are untouched because NULL never equals NULL, and the index below
--    treats NULLs as distinct so they cannot block creation).
DELETE FROM prediction_audit a
USING prediction_audit b
WHERE a.ctid < b.ctid
  AND a.track = b.track
  AND a.race_number = b.race_number
  AND a.race_date = b.race_date
  AND a.horse_name = b.horse_name;

-- 2. The arbiter index the ON CONFLICT clause needs.
CREATE UNIQUE INDEX IF NOT EXISTS uq_prediction_audit_race_horse
    ON prediction_audit (track, race_number, race_date, horse_name);

COMMENT ON INDEX uq_prediction_audit_race_horse IS
    'Arbiter for mc_api.log_prediction_audit ON CONFLICT upsert; created 2026-07-13 after the audit-smoke workflow exposed that its absence failed every audit insert.';

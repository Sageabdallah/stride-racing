-- Phase 2: Biomechanical & Statistical Sectional Feature Columns
-- Run once against the production database before deploying the updated Python files.
--
-- Formulas implemented:
--   lambda_decay       : Ward-Smith (1985) velocity decay constant
--   svi                : Sustained Velocity Index (last 600m vs whole race)
--   rsi                : Beyer/Quirin Race Shape Index (first-half / second-half time)
--   z_200m .. z_800m   : Z-score vs race field per 200m section
--   trip_cost_seconds  : Thorograph trip cost formula (Brohamer 2000)
--   variant_adjusted_* : Beyer daily track variant adjustment

ALTER TABLE sectional_times
  ADD COLUMN IF NOT EXISTS lambda_decay            REAL,
  ADD COLUMN IF NOT EXISTS svi                     REAL,
  ADD COLUMN IF NOT EXISTS rsi                     REAL,
  ADD COLUMN IF NOT EXISTS z_200m                  REAL,
  ADD COLUMN IF NOT EXISTS z_400m                  REAL,
  ADD COLUMN IF NOT EXISTS z_600m                  REAL,
  ADD COLUMN IF NOT EXISTS z_800m                  REAL,
  ADD COLUMN IF NOT EXISTS trip_cost_seconds       REAL,
  ADD COLUMN IF NOT EXISTS variant_adjusted_200m   REAL,
  ADD COLUMN IF NOT EXISTS variant_adjusted_400m   REAL,
  ADD COLUMN IF NOT EXISTS variant_adjusted_600m   REAL,
  ADD COLUMN IF NOT EXISTS variant_adjusted_800m   REAL;

-- Fast horse lookup (used by all quant engines)
CREATE INDEX IF NOT EXISTS idx_sectional_horse_date
  ON sectional_times (LOWER(horse_name), race_date DESC);

-- Fast track/date/distance lookup (used by variant computation)
CREATE INDEX IF NOT EXISTS idx_sectional_track_date_dist
  ON sectional_times (track, race_date, distance_m, track_config);

-- Verify
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'sectional_times'
  AND column_name IN (
    'lambda_decay','svi','rsi',
    'z_200m','z_400m','z_600m','z_800m',
    'trip_cost_seconds',
    'variant_adjusted_200m','variant_adjusted_400m',
    'variant_adjusted_600m','variant_adjusted_800m'
  )
ORDER BY column_name;

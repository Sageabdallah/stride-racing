-- STRIDE Consensus Intelligence Layer — Database Migration
-- Creates 5 new tables + ALTER selections with convergence columns
-- Run against Neon PostgreSQL

-- ============================================================
-- Table 1: consensus_mentions (raw tipster picks from Tavily + Claude)
-- ============================================================
CREATE TABLE IF NOT EXISTS consensus_mentions (
    id SERIAL PRIMARY KEY,
    race_date DATE NOT NULL,
    track TEXT NOT NULL,
    race_number INTEGER NOT NULL,
    horse_name TEXT NOT NULL,
    horse_name_norm TEXT NOT NULL,
    source_url TEXT,
    source_name TEXT NOT NULL,
    source_bucket TEXT NOT NULL,           -- form_analyst | speed_analyst | stable_watcher | market_reader | retail_punter
    confidence_language TEXT,              -- HIGH | MEDIUM | LOW
    raw_snippet TEXT,
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_consensus_mentions_date_track
    ON consensus_mentions (race_date, track, race_number);
CREATE INDEX IF NOT EXISTS idx_consensus_mentions_horse
    ON consensus_mentions (horse_name_norm, race_date);

-- ============================================================
-- Table 2: consensus_scores (aggregated per horse per race)
-- ============================================================
CREATE TABLE IF NOT EXISTS consensus_scores (
    id SERIAL PRIMARY KEY,
    race_date DATE NOT NULL,
    track TEXT NOT NULL,
    race_number INTEGER NOT NULL,
    horse_name TEXT NOT NULL,
    horse_name_norm TEXT NOT NULL,
    total_mentions INTEGER DEFAULT 0,
    bucket_spread INTEGER DEFAULT 0,
    high_confidence_mentions INTEGER DEFAULT 0,
    consensus_score REAL DEFAULT 35.0,
    sources JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (race_date, track, race_number, horse_name_norm)
);

CREATE INDEX IF NOT EXISTS idx_consensus_scores_date
    ON consensus_scores (race_date, track);

-- ============================================================
-- Table 3: betfair_odds_snapshots (multi-timepoint price snapshots)
-- Phase 1 uses Racing API odds, not Betfair Exchange
-- ============================================================
CREATE TABLE IF NOT EXISTS betfair_odds_snapshots (
    id SERIAL PRIMARY KEY,
    race_date DATE NOT NULL,
    track TEXT NOT NULL,
    race_number INTEGER NOT NULL,
    horse_name TEXT NOT NULL,
    horse_name_norm TEXT NOT NULL,
    snapshot_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    snapshot_type TEXT NOT NULL,           -- BASELINE_NIGHT | MORNING_CHECK
    back_price REAL,
    lay_price REAL,
    matched_volume REAL,
    UNIQUE (race_date, track, race_number, horse_name_norm, snapshot_type)
);

CREATE INDEX IF NOT EXISTS idx_betfair_snapshots_lookup
    ON betfair_odds_snapshots (race_date, track, race_number, snapshot_type);

-- ============================================================
-- Table 4: market_signal_scores (steam/drift per horse)
-- ============================================================
CREATE TABLE IF NOT EXISTS market_signal_scores (
    id SERIAL PRIMARY KEY,
    race_date DATE NOT NULL,
    track TEXT NOT NULL,
    race_number INTEGER NOT NULL,
    horse_name TEXT NOT NULL,
    horse_name_norm TEXT NOT NULL,
    baseline_price REAL,
    morning_price REAL,
    price_movement_pct REAL DEFAULT 0.0,
    signal_type TEXT DEFAULT 'UNKNOWN',   -- STEAM | FIRMING | STABLE | DRIFT | STRONG_DRIFT | UNKNOWN
    market_signal_score REAL DEFAULT 50.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (race_date, track, race_number, horse_name_norm)
);

CREATE INDEX IF NOT EXISTS idx_market_signal_scores_date
    ON market_signal_scores (race_date, track);

-- ============================================================
-- Table 5: convergence_output (final blended result per horse)
-- ============================================================
CREATE TABLE IF NOT EXISTS convergence_output (
    id SERIAL PRIMARY KEY,
    race_date DATE NOT NULL,
    track TEXT NOT NULL,
    race_number INTEGER NOT NULL,
    horse_name TEXT NOT NULL,
    horse_name_norm TEXT NOT NULL,
    stride_score REAL,
    consensus_score REAL DEFAULT 35.0,
    market_signal_score REAL DEFAULT 50.0,
    final_convergence_score REAL NOT NULL,
    convergence_tier TEXT NOT NULL,        -- LOCK | CONFIRM | FLAG | CROWD_OVERRIDE | SKIP
    pillars_strong INTEGER DEFAULT 0,
    pillars_available JSONB,              -- {"stride": true, "consensus": true, "market": false}
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (race_date, track, race_number, horse_name_norm)
);

CREATE INDEX IF NOT EXISTS idx_convergence_output_date
    ON convergence_output (race_date, track);
CREATE INDEX IF NOT EXISTS idx_convergence_output_tier
    ON convergence_output (convergence_tier, race_date);

-- ============================================================
-- ALTER selections table: add convergence columns (all nullable)
-- ============================================================
ALTER TABLE selections
    ADD COLUMN IF NOT EXISTS consensus_score REAL DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS consensus_mention_count INTEGER DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS consensus_bucket_diversity INTEGER DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS market_signal_score REAL DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS market_signal_category TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS convergence_score REAL DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS convergence_tier TEXT DEFAULT NULL;

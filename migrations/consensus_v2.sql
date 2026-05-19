-- ============================================================
-- STRIDE Consensus Intelligence V2 — Database Migration
-- Run AFTER consensus_intelligence.sql (V1 tables must exist)
-- ============================================================

-- Table 6: tipster_panel_log
-- Tracks which panel members were fetched each day
CREATE TABLE IF NOT EXISTS tipster_panel_log (
    id SERIAL PRIMARY KEY,
    race_date DATE NOT NULL,
    tipster_id VARCHAR(50) NOT NULL,
    tipster_name VARCHAR(100) NOT NULL,
    fetch_url TEXT,
    fetch_status VARCHAR(20) NOT NULL,  -- SUCCESS | FAILED | SKIPPED | BLOCKED
    content_length INTEGER,
    picks_extracted INTEGER DEFAULT 0,
    fetch_duration_ms INTEGER,
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_panel_log_date ON tipster_panel_log(race_date);

-- Table 7: source_accuracy
-- Tracks tipster historical accuracy (nightly job)
CREATE TABLE IF NOT EXISTS source_accuracy (
    id SERIAL PRIMARY KEY,
    tipster_id VARCHAR(50) NOT NULL,
    tipster_name VARCHAR(100),
    source_bucket VARCHAR(50),
    race_date DATE NOT NULL,
    track VARCHAR(50),
    race_number INTEGER,
    horse_tipped VARCHAR(100) NOT NULL,
    finish_position INTEGER,
    was_winner BOOLEAN,
    starting_price NUMERIC(8,2),
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_source_accuracy_tipster ON source_accuracy(tipster_id, race_date);

-- ============================================================
-- ALTER selections: expand from 7 V1 columns to 19 total
-- All nullable — existing rows get NULL, frontend handles it
-- ============================================================
ALTER TABLE selections
  ADD COLUMN IF NOT EXISTS selection_score_raw REAL DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS consensus_vote_pct REAL DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS consensus_injection REAL DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS consensus_injection_reason TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS market_injection REAL DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS market_injection_reason TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS convergence_gate TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS market_confidence_score REAL DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS market_confidence_label TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS market_confidence_colour TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS tipsters_polled INTEGER DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS independent_source_rate REAL DEFAULT NULL;

-- ============================================================
-- ALTER convergence_output: add V2 columns
-- ============================================================
ALTER TABLE convergence_output
  ADD COLUMN IF NOT EXISTS field_size INTEGER,
  ADD COLUMN IF NOT EXISTS stride_score_raw NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS consensus_injection NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS market_injection NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS stride_score_final NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS market_confidence_score NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS market_confidence_label VARCHAR(20),
  ADD COLUMN IF NOT EXISTS vote_pct NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS tipsters_polled INTEGER;

-- ============================================================
-- ALTER consensus_mentions: add V2 columns for reasoning
-- ============================================================
ALTER TABLE consensus_mentions
  ADD COLUMN IF NOT EXISTS tipster_id VARCHAR(50),
  ADD COLUMN IF NOT EXISTS tipster_reasoning TEXT,
  ADD COLUMN IF NOT EXISTS reasoning_signals JSONB,
  ADD COLUMN IF NOT EXISTS is_independent BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS is_panel_source BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS quality_multiplier NUMERIC(3,2) DEFAULT 1.0;

-- ============================================================
-- ALTER consensus_scores: add V2 columns
-- ============================================================
ALTER TABLE consensus_scores
  ADD COLUMN IF NOT EXISTS field_size INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS tipsters_polled INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS vote_pct NUMERIC(5,2) DEFAULT 0,
  ADD COLUMN IF NOT EXISTS independent_source_rate NUMERIC(4,3) DEFAULT 1.0,
  ADD COLUMN IF NOT EXISTS reasoning_alignment VARCHAR(20);

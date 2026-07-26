-- selection_ledger: one settled-or-pending row per published selection.
--
-- Why a new table rather than columns on `selections`: guardrail 4 forbids
-- changing an existing schema, and `selections` is a contract with an unseen
-- consumer (the excluded TypeScript frontend). This table is purely additive —
-- nothing existing reads or writes it, so creating it cannot break any current
-- path, and dropping it cannot either.
--
-- What it exists for: SYSTEM_MAP §3 records that nothing measures the wrapper
-- end to end. `shadow_pl_tracker` measures convergence tiers; the backtests
-- measure the ML ensemble under strategy bands. The chain that actually
-- decides bets — calibrate_and_score -> apply_safety_filters ->
-- evaluate_bet_candidate -> crowd_bet_decision — has no realised hit-rate and
-- no realised ROI measurement, which is why every threshold in §6 is currently
-- unfalsifiable. These rows are the substrate that makes it falsifiable.
--
-- Two prices are stored, always. price_taken is the racecard price the tip was
-- published at; price_close is SP. Storing only one makes CLV uncomputable,
-- and CLV is the earliest available signal that an edge is real rather than
-- backtest luck. Settling on price_close alone is exactly the defect
-- Workstream A found in walk_forward_backtest.py.
--
-- Rows are written by server/python/selection_ledger.py. It is safe to run
-- this migration before that writer is ever enabled.

CREATE TABLE IF NOT EXISTS selection_ledger (
    id                  BIGSERIAL PRIMARY KEY,

    -- race identity, matching the (track, race_number, race_date) triple the
    -- rest of the schema keys on
    race_date           DATE          NOT NULL,
    track               TEXT          NOT NULL,
    race_number         INTEGER       NOT NULL,
    horse_name          TEXT          NOT NULL,

    -- how this selection came to be published
    selection_origin    TEXT,
    should_bet          BOOLEAN,
    confidence          TEXT,

    -- probability at each stage of the chain, so a regression can be located
    raw_model_prob      DOUBLE PRECISION,
    calibrated_prob     DOUBLE PRECISION,
    model_edge_pp       DOUBLE PRECISION,   -- percentage POINTS (calibrated - true_market)
    ev_at_taken         DOUBLE PRECISION,   -- ratio (calib/true_mkt - 1), NOT the same as edge
    fair_odds           DOUBLE PRECISION,

    -- both prices
    price_taken         DOUBLE PRECISION,
    price_close         DOUBLE PRECISION,
    clv_pct             DOUBLE PRECISION,
    has_real_market_odds BOOLEAN,

    -- staking as the live 2u/1u/0u ladder actually applied it
    stake_rule          TEXT,
    stake_units         DOUBLE PRECISION,
    stake               DOUBLE PRECISION,
    commission_rate     DOUBLE PRECISION DEFAULT 0.0,

    -- settlement
    settled             BOOLEAN       NOT NULL DEFAULT FALSE,
    won                 BOOLEAN,
    pnl                 DOUBLE PRECISION,

    -- shadow staking: computed for later evaluation, never applied
    shadow_kelly_json   JSONB,

    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Arbiter for the writer's ON CONFLICT upsert. prediction_audit was near-empty
-- for months because its upsert named an arbiter that did not exist and every
-- insert failed silently (see prediction_audit_unique_key.sql). Creating the
-- index in the same migration as the table avoids repeating that.
CREATE UNIQUE INDEX IF NOT EXISTS uq_selection_ledger_race_horse
    ON selection_ledger (track, race_number, race_date, horse_name);

CREATE INDEX IF NOT EXISTS ix_selection_ledger_race_date
    ON selection_ledger (race_date);

CREATE INDEX IF NOT EXISTS ix_selection_ledger_settled
    ON selection_ledger (settled, race_date);

COMMENT ON TABLE selection_ledger IS
    'One row per published selection with both prices, staged probabilities, applied stake and settled P&L. Additive: no existing code reads or writes it. Written by server/python/selection_ledger.py.';

COMMENT ON COLUMN selection_ledger.model_edge_pp IS
    'STRIDE modelEdge: calibrated - true_market, in percentage POINTS (run_tips_pipeline.py:697). Not interchangeable with ev_at_taken.';

COMMENT ON COLUMN selection_ledger.ev_at_taken IS
    'Expected profit per unit staked: calib/true_mkt - 1 (run_tips_pipeline.py:955), equivalently model_prob*decimal_odds - 1. Not interchangeable with model_edge_pp.';

COMMENT ON COLUMN selection_ledger.price_close IS
    'SP. Present so CLV is computable; never use it to settle a bet that was struck at price_taken.';

COMMENT ON COLUMN selection_ledger.shadow_kelly_json IS
    'Fractional-Kelly plan computed for evaluation only. applied is always false; the live stake is stake_units on the 2u/1u/0u ladder.';

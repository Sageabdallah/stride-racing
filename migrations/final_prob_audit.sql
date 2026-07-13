-- Adds the published end-of-pipeline win probability to the canonical
-- prediction audit. mc_api logs predicted_win_prob at the MC stage (before
-- the wrapper's isotonic / ML-blend / market-anchor stages); this column
-- records what the pipeline actually published for every runner, written by
-- run_tips_pipeline.store_final_probs_in_audit after each card.
--
-- Purpose (docs/12-hit-rate-research.md §5 item 1): give future
-- conditional-logit fits a genuinely final-stage input, so the market weight
-- (beta) can be estimated against the published probability rather than a
-- quantity that already embeds the market anchor.
--
-- The pipeline also self-heals this column at write time
-- (ADD COLUMN IF NOT EXISTS), so running this migration is optional but
-- recommended for explicitness.

ALTER TABLE prediction_audit
    ADD COLUMN IF NOT EXISTS final_win_prob NUMERIC;

COMMENT ON COLUMN prediction_audit.final_win_prob IS
    'Published end-of-pipeline win % for this runner (post market anchor), written by run_tips_pipeline.store_final_probs_in_audit; predicted_win_prob remains the MC-stage value logged by mc_api.';

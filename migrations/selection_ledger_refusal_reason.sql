-- refusal_reason (task 07): why a refused-set row was refused, so blocked
-- crowd promotions are queryable as their own cohort. Additive, idempotent.
ALTER TABLE selection_ledger
    ADD COLUMN IF NOT EXISTS refusal_reason TEXT;

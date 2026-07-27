# 04 — As-of-prediction-time odds snapshot (time-critical: start immediately)

**Wave:** 2 (start the capture job on day 1, in parallel with Wave 1) · **Depends on:** nothing · **Blocks:** [09](09-forward-validation-protocol.md), [12](12-retrain-rebaseline.md), [14](14-late-odds-features.md) · **Risk:** low · **Type:** data infrastructure

## Goal

Persist, per runner, the odds that were actually knowable at prediction time —
plus a T−5min late snapshot — so the model can be retrained on morning prices and
movement features can exist in training for the first time. **Prospective only:
never backfill.** Every day this isn't running pushes the re-baseline ([12](12-retrain-rebaseline.md)) out by a day.

## Why (evidence)

- The model trains/CVs/backtests with `sp_odds` mapped into `market_odds`
  (`retrain_v2.py:142-144`, `:560-565`; training view sources SP at
  `refresh_training_view_v2.py:216`; metro backtest repeats the mapping at
  `backtest_v2_metro.py:69,127-142`), while live inference serves the racecard/morning
  price (`run_tips_pipeline.py:2259`; `mc_api.py:1158`). The single most important
  feature (~0.16 importance) is stronger in every offline metric than live.
  The repo's own analysis: "106,193 of 119,577 view rows have no prediction join …
  the README's 33.7%/−4.2% and 9.9%/+12.3% were produced on SP-derived features"
  (`docs/analysis/IMPLEMENTATION_PLAN.md:1476-1483`).
- The planned fix already exists as ticket C6/T15 (`odds_source` /
  `has_real_market_odds` indicators, "strongest source-read finding") and X1/B2
  (T−5min snapshot — promoted to #1 by `research/report.md`; JRA 894k-runner study,
  ~14× cross-sectional effect for the late move).
- Current snapshot infra is wrong-window: `odds_movement.py` captures ~12:30am and
  ~8am only (`odds_movement.py:410-425`), stores medians in a table misnamed
  `betfair_odds_snapshots` (`odds_movement.py:260`), and misses the smart-money window.

## Scope

**In:** a new append-only odds-snapshot table + capture jobs at tip time and T−5min;
an `odds_source` column wherever odds enter features; training-view plumbing
(additive only — no retraining here).
**Out:** retraining (→ [12](12-retrain-rebaseline.md)); movement features
(→ [14](14-late-odds-features.md)); Betfair exchange integration (→ [10](10-execution-and-pricing.md)).

## Steps for Kimi Code

1. **Migration `migrations/odds_snapshots.sql`:** table `runner_odds_snapshots`
   (`race_id`, `runner_id`, `snapshot_kind` ENUM(`tip_time`,`late_t5`,`morning`,`baseline`),
   `captured_at timestamptz`, `bookmaker text` (or `median`), `decimal_odds numeric`,
   `source_api text`, PRIMARY KEY(`race_id`,`runner_id`,`snapshot_kind`,`bookmaker`,`captured_at`)).
   Append-only; no UPDATEs ever (add a trigger rejecting them if your Postgres role allows).
2. **Tip-time capture.** In `run_tips_pipeline.py`, at the point `extract_odds`
   (:2259, :445-471) prices the field, insert one `tip_time` row per runner
   (store **all** bookmakers returned, not just the first — the current
   first-bookmaker choice at :445-471 becomes a *derived* view, not the stored data).
3. **Late capture job.** New scheduler entry (alongside `download_racecards.py`):
   for each race today, at jump−5min pull the current market and insert `late_t5`
   rows. Reuse the existing Racing API client; respect rate limits; if the API can't
   do T−5 reliably, capture T−10/T−15 and store actual `captured_at` + seconds-to-jump
   (add `seconds_to_jump int` column).
4. **Feature plumbing (additive).** Add `tip_time_odds`, `odds_source`
   (`'racecard'|'snapshot'|'sp_fallback'`) and `seconds_to_jump` to the training view
   (`refresh_training_view_v2.py`) as **new columns only** — do not change the
   existing `market_odds`/`sp_odds` mapping until [12](12-retrain-rebaseline.md).
   `has_real_market_odds` indicator already exists at inference
   (`run_tips_pipeline.py:1807-1808`) — align semantics.
5. **Coverage monitor.** Daily audit query: % of today's runners with `tip_time`
   rows; % with `late_t5`. Alert (existing health-check pattern, cf.
   `results_health_check.py`) if tip_time coverage < 95%.

## Acceptance criteria

- [ ] Migration applies cleanly; append-only verified.
- [ ] After 3 race days: ≥95% of tipped runners have `tip_time` rows; late-capture
      coverage report exists (even if low at first — honesty over coverage).
- [ ] Training view exposes the new columns without touching the legacy mapping
      (row count unchanged; spot-check one date).
- [ ] Coverage monitor runs and reports daily.

## Rollout & flags

- No model flags. Scheduler additions only. Rollback: stop the capture jobs (data
  already captured is harmless and valuable).

## Guardrails

- **Never backfill historical `tip_time` odds from SP or any post-race source.**
  Rows must be created by live capture only.
- Do not change `market_odds` semantics anywhere in this task — additive columns only.
- Do not let the capture job delay the tips pipeline; wrap in try/except with
  async/fire-and-forget semantics (log failures, never block tipping).

## Related

- Evidence: [00-evidence-base.md](00-evidence-base.md) §2 (A1), §4 (C5)
- Consumers: [09](09-forward-validation-protocol.md) (validates bands at tip-time
  prices), [12](12-retrain-rebaseline.md) (retrains on `tip_time_odds` after
  4–6 weeks), [14](14-late-odds-features.md) (movement features from
  baseline→tip→late series).

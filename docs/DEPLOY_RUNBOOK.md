# STRIDE deploy-session runbook — restart the clocks, land the ROI stack

*One sitting, ordered, with a green/red gate at each step. The single command
that decides "done": `python server/python/deploy_preflight.py` exits 0.*

Two clocks have been stopped since 2026-04-19: the tips pipeline (audit rows,
calibrator coverage, shadow P/L all starve without it) and the odds-snapshot
clock (task-12 retrain and task-14 features starve without it). Everything in
this runbook exists to start both clocks on verified code and prove they tick.

---

## Phase 0 — before the session (safe merges, no behaviour change)

These change no live behaviour and can land any time. Order matters only
within the stack:

- [ ] Merge **PR #4** (roi/02 backtest statistics) — base of the stack,
      carries `roi_stats.py`. Verified: 4/4 acceptance + guardrails.
- [ ] Merge **PR #7** (ship-gate below-zero fix) — stacked on #4. Behavioral
      only for future promotions (SHIP→HOLD, stricter-only).
- [ ] Merge **PR #8** (reportability-floor single-source) — stacked on #4,
      zero-change refactor.
- [ ] Merge **PR #6** (`deploy_preflight.py`) — the gate tool itself.
- [ ] Merge the **deploy-prep PR** (this runbook + feature-liveness audit).

## Phase 1 — deploy-day code + schema

- [ ] Merge **PR #2** (roi/04 as-of odds snapshots). Confirm it contains the
      **invocation-path fix** (watcher launched from `run_tips_pipeline.py`
      itself — commit message "watcher launch moved into the tips pipeline").
      Without that fix the odds clock does NOT start: the Node scheduler
      (10 AM task → `pipeline.ts` → `mc_api.py`) and the manual `/stride-full`
      runbook both bypass `run_full_pipeline.py`, where the launch previously
      lived.
- [ ] Apply migration: `migrations/runner_odds_snapshots.sql` (append-only
      table + trigger; idempotent).
- [ ] Merge **PR #1** (roi/01 ledger + net settlement).
- [ ] Apply migrations: `migrations/selection_ledger.sql`, then
      `migrations/selection_ledger_net_settlement.sql` (settle fails loud if
      the second is missing — by design).
- [ ] Set environment (deployment env / `.env`):
      - `STRIDE_LEDGER_WRITE=true` — ledger rows on from day one
      - `STRIDE_COMMISSION_RATE=0.08` — net-of-commission settlement
      - `STRIDE_SHADOW_KELLY=true` — logs a Kelly counterfactual per ledger
        row, real stakes untouched (task-06's dataset accrues for free;
        plumbing verified at `selection_ledger.py:188`)
      - `STRIDE_ODDS_SNAPSHOT_WRITE` — leave unset (default ON)
- [ ] Run `python server/python/deploy_preflight.py` — SCHEMA board must be
      all green now (the two PEND items were PR #1/#2).

## Phase 2 — restart tips + prove the clocks tick

- [ ] Run the day's pipeline the way you actually run it (`/stride-full
      <date>` — consensus agent MUST run before tips or every pick gates to
      NO_BET).
- [ ] **Watcher smoke** (same minute as tips completing):
      - `ls server/python/logs/late_odds_<date>.pid` exists and
        `ps -p $(cat …pid)` shows a live process
      - `tail server/python/logs/late_odds_<date>.log` shows
        "[LATE_ODDS][watch] watching N races"
- [ ] **Snapshot smoke** (after the first race jumps):
      `runner_odds_snapshots` has `tip_time` rows for today and at least one
      `late_t5` row; `python server/python/odds_snapshot_coverage.py --date
      <date>` reports coverage.
- [ ] **Ledger smoke** (after results land): settled count > 0 for the day,
      `settled_at_sp_fallback` populated per the live-day checklist
      (`docs/roi-roadmap/01-live-day-smoke.md`).
- [ ] **The gate:** `python server/python/deploy_preflight.py` exits 0 — the
      LIVENESS board's "tips ran for the latest scheduled race day" is the
      check that catches the 2026-04-19 class of freeze.
- [ ] Scheduler note: if you re-enable the Node scheduler, verify which entry
      point actually produced today's selections (`pipeline_runs` vs
      `selections`) — the 10 AM task runs `mc_api.py` via `pipeline.ts`, not
      `run_tips_pipeline.py`. The watcher fix covers every path that runs
      `run_tips_pipeline.py`; a selections-producing path that bypasses it
      would need its own wiring (flag it if you see one).

## Phase 3 — the week after (data-gated follow-ons)

- [ ] **Tier/gate P&L attribution** (new analysis PR): one command against
      the prod DB, read-only —
      `python server/python/tier_pnl_attribution.py --to 2026-04-19`
      answers "what did FLAG (forced NO_BET) actually cost or save" from the
      frozen window. Needs your explicit prod-read approval; it SELECTs only.
- [ ] **Calibrator coverage** (roi/05): ~87 audit rows/day → the 500-row
      `STRIDE_CAL_MIN_COVERAGE` gate clears in ~1 week; then the roi/05
      shadow comparison starts. (Verified so far: 39/39 tests green,
      non-tautological; flags/math verification to finish before enabling.)
- [ ] **Serve-side liveness plumbing** (the big one): 25.45% of the live
      model's importance mass is fed constants at serve
      (`docs/research/FEATURE_PROVENANCE.md`). Plumb the 15 ZERO_AT_SERVE
      features through roi/03's `serve_features.py` honouring the NaN
      contract — no retrain required, direct live-scoring improvement.
      Gate any fix behind a flag + shadow comparison like roi/03.
- [ ] **Retrain hold stands** (task 12): not until roi/03+04+05 are live AND
      4-6 weeks of `odds_source='snapshot'` rows exist. At retrain time:
      odds features from snapshots only, dead-feature decisions from the
      liveness report, and `feature_liveness_audit.py --pkl <new>` as a
      promotion gate (ZERO_AT_SERVE must be 0).

## Rollback

- Model: `cp models/backups/<BACKUP>.pkl models/racing_ensemble_v2.pkl`
- Watcher: `STRIDE_SKIP_ODDS_WATCH=true` skips the launch;
  `kill $(cat server/python/logs/late_odds_<date>.pid)` stops a running one.
- Ledger: `STRIDE_LEDGER_WRITE=false` stops writes; settle passes are
  idempotent (sp/pnl predicate) so re-runs are safe.
- Migrations are append-only/idempotent; none drops or rewrites existing data.

# ATTENTION SAGE — your action list (nothing here can be done by an agent)

*Everything below needs your hands: GitHub merges, prod migrations, env flags,
or prod-DB approval. Full detail lives in
[docs/DEPLOY_RUNBOOK.md](docs/DEPLOY_RUNBOOK.md) — this is the short list in
the order to do it.*

## 1. Today, no deploy needed — safe merges (zero live-behaviour change)

Order matters only for the stack (#4 carries `roi_stats.py`):

```
gh pr merge 4  --merge     # roi/02 backtest statistics  (base of the stack)
gh pr merge 7  --merge     # ship-gate below-zero fix    (stacked on #4)
gh pr merge 8  --merge     # reportability floor         (stacked on #4)
gh pr merge 9  --merge     # tier P&L attribution        (stacked on #4)
gh pr merge 6  --merge     # deploy_preflight tool
gh pr merge 10 --merge     # runbook + liveness audit (this file)
```

All six verified; none changes what the live system does.

## 2. The deploy session (one sitting — runbook Phase 1-2)

- [ ] Merge **#2** (odds snapshots — includes the watcher invocation-path fix)
      and **#1** (ledger + net settlement)
- [ ] Apply migrations against prod:
      `migrations/runner_odds_snapshots.sql`,
      `migrations/selection_ledger.sql`,
      `migrations/selection_ledger_net_settlement.sql`
- [ ] Set env: `STRIDE_LEDGER_WRITE=true`, `STRIDE_COMMISSION_RATE=0.08`,
      `STRIDE_SHADOW_KELLY=true` (leave `STRIDE_ODDS_SNAPSHOT_WRITE` unset —
      default ON)
- [ ] Restart tips the way you actually run it (`/stride-full <date>` —
      consensus agent first or everything gates NO_BET)
- [ ] Smokes (detail in runbook Phase 2): watcher pid alive + log line,
      `tip_time` rows for today, ledger settled > 0 after results
- [ ] Gate: `python server/python/deploy_preflight.py` **exits 0**

## 3. One command, needs your prod access — the FLAG counterfactual

```
python server/python/tier_pnl_attribution.py --to 2026-04-19
```

Read-only. Answers what the FLAG tier (forced NO_BET) would have returned over
the frozen window — the number that decides whether the convergence gate is
earning its keep. (An agent could not run this: prod reads need your approval.)

## 4. The week after (calendar gates, check-ins only)

- [ ] Odds coverage: `python server/python/odds_snapshot_coverage.py --date <d>`
      trending to >= 95%
- [ ] Calibrator coverage: ~87 audit rows/day -> 500-row gate clears in ~1 week,
      then the roi/05 shadow comparison starts
- [ ] Shadow weeks run for #3 (calibrator) and #5 (serve parity) before either
      merges
- [ ] **Retrain hold stands** (task 12): not before roi/03+04+05 are live AND
      4-6 weeks of `odds_source='snapshot'` data exist. At retrain time the
      promotion gate includes
      `python server/python/feature_liveness_audit.py --pkl <new artifact>`
      showing ZERO_AT_SERVE = 0.

## Standing decisions already made (so future-you doesn't relitigate)

- Scheduler has NO auto-retrain (manual/API only) — restarting automation is
  safe on that front; but verify which entry point actually produced the day's
  selections (the 10 AM task runs `mc_api.py`, not `run_tips_pipeline.py`).
- The 25%-of-model serve-plumbing fix is designed and de-risked
  (docs/research/FEATURE_PROVENANCE.md) — it lands flag-gated via the roi/03
  builder, before the retrain, with its own shadow comparison.

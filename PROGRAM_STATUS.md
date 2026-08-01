# STRIDE ROI Program — Status & Playbook

*Last updated: 2026-07-29. Read this top to bottom and you are up to speed.
The short version: everything agent-side is built and verified. What remains
is your hands — merges, one deploy session, and calendar time.*

---

## 1. The mission in three sentences

The founding number (+12.3% ROI) is statistically unreportable — best-of-6 on
one window, 95% CI [−43.6%, +68.2%]. Two clocks have been dead since
**2026-04-19**: the tips pipeline stopped, and the odds-snapshot clock never
started. Everything below serves one arc: **restart the clocks on verified
code → accrue prospective data under a pre-registered protocol → retrain with
every known defect fixed → promote only through mechanical gates.**

## 2. The operating model

| Party | Role |
|---|---|
| **Sage** | Sole decider. Merges PRs, runs prod commands, sets env flags, resolves approval markers. Only human with prod access. |
| **Kimi** | The coder. Receives task prompts, returns branches/PRs + handover reports. |
| **Architect session** (Claude Code, this project dir) | Writes the task prompts, independently audits every Kimi handover against real pushed bytes before anything merges. |

House rules that never bend: nothing merges without independent verification;
no retrain without a staging artifact; the gate never promotes itself; prod
DB reads need Sage's explicit approval; commits are authored by Sage with no
generated-by attribution; consensus agent / convergence tiers / franking /
mw ladder are untouchable without explicit approval; consensus must run
before the tips pipeline or everything gates NO_BET.

## 3. The model defects found (why the retrain matters)

All artifact-validated against the live pkl
(`server/python/models/racing_ensemble_v2.pkl`, 110 columns, trained
2026-04-15). Full evidence: `docs/research/FEATURE_PROVENANCE.md`.

1. **25.45% of importance is dead at serve** — 15 features the model trained
   on are zero-filled at prediction time (and 0 is a real z-value, so it's
   misinformation, not missing data). Fix built: PR #11, flag-gated.
2. **`market_odds` (21.8%, the top feature) is SP-contaminated at train** —
   the model learned from starting prices it can never see pre-race. Fix
   built: snapshot-sourced training + ablation harness (PR #16).
3. **`td_*` aggregate leak (~7%)** — track-distance profiles had no upper
   date bound, so each training row's own result sat inside its aggregate.
   Fix built: as-of monthly buckets (PR #14).
4. **`days_since_run` wall-clock bug (2.6%)** — computed from "now" at train
   time, corrupting historical rows. Fixed: PR #13.
5. Plus 41 dead + 2 constant features pruned 113→68 (PR #15).

## 4. The board — every branch and its gate

**Nothing has merged yet. Main = `58b75fc`.** All PRs below are audited PASS
by independent verification.

### Merge today (no live-behaviour change)

| PR | What | Note |
|---|---|---|
| #4 | Backtest statistics (`roi_stats.py`) | Base of the stack — merge first |
| #7 | Ship-gate: below-zero CI → HOLD | Stacked on #4 |
| #8 | Reportability floor single-sourced | Stacked on #4 |
| #9 | Tier P&L attribution tool | Stacked on #4; live run = §5 step 3 |
| #6 | `deploy_preflight.py` | The deploy-session gate tool |
| #10 | Deploy runbook + feature liveness audit | |
| #12 | Feature provenance semantic sweep | After #10 |
| #13 | Wall-clock fix | |
| #14 | As-of td profiles | |
| #15 | Feature prune 113→68 | |
| #17 | `retrain_preflight.py` promotion gate | After #10; includes marker-count fix |
| #19 | Betfair snapshot coverage audit | Includes the join/normalisation fixes |

### Merge at the deploy session

| PR | What |
|---|---|
| #1 | Ledger CLV + net-of-commission settlement (+ migrations) |
| #2 | As-of odds snapshot capture (incl. watcher invocation fix) |
| #16 | Snapshot-odds training switch + ablation (stacked on #2) |

### Gated — do not merge until the condition passes

| PR | Condition |
|---|---|
| #18 | You resolve its 5 `[SAGE-APPROVAL:]` markers (each has a recommended default), then merge |
| #3 | Calibrator + renormalisation — after its shadow week |
| #5 | Serve-time probability fixes — after its shadow week |
| #11 | The 25.45% serve-liveness fix — after its shadow week |
| 12P-8 | Winner-pattern rescue: **hard-gated on #5 + #11 merged**. Kimi correctly aborted on 2026-07-29 (gate unmet). Re-dispatch the same prompt once they land. Source is safe on `backup/winner-pattern-d695894`. |

## 5. Your action list, in order

### Today (one sitting, no deploy)

1. Merge the "today" table above (stack order: #4 before #7/#8/#9; #10
   before #12/#17).
2. Resolve the 5 `[SAGE-APPROVAL:]` markers in PR #18's protocol docs, then
   merge it.
3. Two read-only prod one-liners (your access):
   ```bash
   python server/python/tier_pnl_attribution.py --to 2026-04-19
   python server/python/betfair_snapshot_coverage_audit.py
   ```
   The first answers whether the FLAG tier is earning its keep. The second
   will almost certainly print `NOT_FEASIBLE` (snapshots only exist from
   2026-04-06), officially closing the retro-ablation shortcut — the
   prospective window stands.

### The deploy session (one sitting — full detail in `docs/DEPLOY_RUNBOOK.md`)

**This is the calendar-critical action. The day it completes is day zero for
every wait below.**

- Merge #1, #2, #16.
- Apply migrations: `runner_odds_snapshots.sql`, `selection_ledger.sql`,
  `selection_ledger_net_settlement.sql`.
- Set env: `STRIDE_LEDGER_WRITE=true`, `STRIDE_COMMISSION_RATE=0.08`,
  `STRIDE_SHADOW_KELLY=true` (leave `STRIDE_ODDS_SNAPSHOT_WRITE` unset —
  default ON).
- **Find why tipping stopped 2026-04-19 and restart it** (`/stride-full
  <date>`, consensus first). Verify which entry point produces the day's
  selections — the 10 AM task runs `mc_api.py`, not `run_tips_pipeline.py`.
- Smokes: watcher pid alive, `tip_time` rows for today, ledger settled > 0
  after results.
- Gate: `python server/python/deploy_preflight.py` **exits 0**.

### Then the calendar runs itself (check-ins only)

- **Day zero + ~1 week:** calibrator hits its 500-row gate (~87 audit
  rows/day); shadow comparisons for #3 and #5 start. After **≥5 race days**
  of clean shadow data: merge #3, #5, #11.
- **The moment #5 + #11 land:** re-dispatch 12P-8 verbatim.
- **Day zero + 4–6 weeks** of `odds_source='snapshot'` data: the task-12
  retrain window opens.

## 6. What the retrain actually is (the payoff)

Not a script re-run — model v3, trained on clean data for the first time:
snapshot odds instead of SP, all 15 formerly-dead features live at serve,
as-of profiles, wall-clock fixed, 68 real features, winner-pattern features
if 12P-8 has landed. It goes live only through:

1. A **staging artifact** — never straight to live.
2. `retrain_preflight.py` all-green: `feature_liveness_audit.py --pkl <new>`
   showing **ZERO_AT_SERVE = 0**, lockstep + parity suites, live-model
   backup, zero unresolved approval markers, `ship_criteria` verdict (a
   below-zero ROI CI is HOLD, never SHIP).
3. The **pre-registered protocol** (PR #18) — comparison thresholds you
   signed before seeing results.
4. **Your click.** The gate never promotes itself.

If the deploy session happens the week of 2026-07-28: shadow-gated merges
clear ~mid-August, retrain window opens early-to-mid September. Every day the
deploy slips, the chain slips a day.

## 7. Where the detail lives

| Thing | Where |
|---|---|
| Deploy runbook (step-by-step) | `docs/DEPLOY_RUNBOOK.md` (PR #10) |
| Your short action list | `attentionsage.md` (PR #10; §5 above supersedes its PR list) |
| Feature defect evidence | `docs/research/FEATURE_PROVENANCE.md` (PR #10/#12) |
| Pre-registration + shadow-flip criteria | `docs/roi-roadmap/12-preregistration.md`, `shadow-flip-criteria.md` (PR #18) |
| Roadmap task specs 00–14 | `docs/roi-roadmap/` on any `roi/*` branch |
| Winner-pattern work (unmerged, rescued) | branch `backup/winner-pattern-d695894` |
| Live model artifact | `server/python/models/racing_ensemble_v2.pkl` |

## 8. Local-machine hygiene (small, but yours)

- The local Desktop repo has untracked `CLAUDE.md` and a `claude/` directory
  — never commit them; gitignore or delete.
- The local checkout of `analysis/betfair-coverage-audit` is behind the
  pushed fix — `git pull` before working there.
- Only one `DATABASE_URL` exists in the repo (`.env`); part of the deploy
  session is confirming whether prod is this machine or another.

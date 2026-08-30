# STRIDE Operations Prompt Pack (OP series)

*Issued 2026-07-29. This pack covers the execution phase of the program: the
agent-actionable tasks that orbit the operator's own checklist. Tasks are
split between the two executors by strength; the operator (Sage) remains the
sole merge, deploy, and prod authority for every task.*

## How this fits with the other documents

- `PROGRAM_STATUS.md` (stride-racing PR #20) — the operator's own action
  list: Phase-0 merges, the deploy session, approval markers, prod
  one-liners. **Nothing in that list can be done by an agent.**
- `CHATBOT_PROMPT_PACK.md` (local, Race-Analytics-Export/) — the CB series
  for the chat rebuild. CB-2/CB-3/CB-4 remain Kimi dispatches from that
  file; they are referenced here, not duplicated.
- This file — everything agents can usefully do around those two.

## Ownership

| Task | Owner | Why this owner |
|---|---|---|
| OP-1 root-cause the dead tips pipeline | Architect session | Pure forensics on the live machine: logs, schedulers, local artifacts. Needs direct machine access, read-only discipline, and program context. |
| OP-2 fix the scheduler's python interpreter | Kimi | A well-bounded app-code change in the private repo with mocked tests — the coder's lane. |
| OP-3 post-deploy verification battery | Architect session | Independent verification is the architect role; runs on the live machine right after the operator's deploy session. |
| OP-4 shadow-week check-ins | Architect session | Recurring evidence audits against gate criteria — verification work. |
| CB-2 → CB-4 chat rebuild | Kimi | Already specified in `CHATBOT_PROMPT_PACK.md`; dispatch order CB-2, CB-3, CB-4, each audited before the next. |
| 12P-8 winner-pattern rescue | Kimi | Hard-gated: dispatch ONLY after PRs #5 and #11 are merged to stride-racing main. The architect session reissues the archived prompt verbatim on request. |

Verification rule, unchanged: every Kimi handover is independently audited on
real pushed bytes before merge; architect-owned tasks ship with mechanical
self-verification evidence for the operator's review.

---

## UNIVERSAL RULES (bind every executor)

1. **Attribution.** Every commit: author AND committer
   `Sage Abdallah <sageabdallah10@gmail.com>`. No `Co-Authored-By`, no
   "Generated with", no AI/assistant authorship mention anywhere.
2. **Secrets.** Never commit or print `.env` contents, API keys, or
   connection strings. Secrets grep before every push:
   `git diff --cached | grep -cEi "sk-ant|pplx-|postgres(ql)?://[^\"']*@|whsec_"`
   must be 0.
3. **Prod discipline.** No prod DB writes ever. Prod DB READS require the
   operator's explicit approval per task — a task prompt saying so does not
   substitute for it. Automated tests make zero network calls.
4. **Blast radius.** Kimi tasks touch only the files named in the task.
   Nobody touches the consensus agent, convergence tiers, franking
   thresholds, or the mw ladder.
5. **Handover report:** branch + PR, commit SHAs, per-file diff summary,
   test count and command, every Definition-of-Done item addressed, all
   deviations flagged with reasons.

---

## OP-1 — Root-cause: why did tipping stop on 2026-04-19?

**Owner: Architect session** (read-only forensics on the live machine; runs
on the operator's go-ahead, ideally BEFORE the deploy session so the restart
step is informed rather than exploratory).

The tips pipeline last produced selections on 2026-04-19; the results side
kept running until ~mid-July. The schema is healthy — this is an
orchestration failure, and the deploy runbook's "find why tipping stopped
and restart it" step currently has no answer. Produce one.

Investigate, strictly read-only, using local artifacts first:

1. **Process managers:** launchd agents/daemons (`launchctl list`,
   `~/Library/LaunchAgents`), pm2 (`pm2 ls` if installed), cron
   (`crontab -l`), and whether the Express app (whose in-process scheduler
   drives daily tips) was even running across April–July. Uptime evidence:
   app logs, `last reboot` history, macOS sleep/wake patterns (a laptop that
   sleeps at 10 AM never fires a 10 AM task).
2. **App-side evidence:** the scheduler's own logs around 2026-04-17..21;
   any crash loops; the scheduler's catch-up-on-boot behavior (observed
   2026-07-29: it fires missed tasks at startup and `learn_from_results_v2`
   crashes on a broken python env — OP-2's subject).
3. **Change correlation:** git history and file mtimes around 2026-04-19 in
   both repos (model retrain was 2026-04-15; the chat model retirement was
   ~2026-06-15 — unrelated but rule it out); `.env` changes; racecard
   download artifacts stopping (the Wednesday 4 PM download feed).
4. **Machine identity:** confirm whether prod IS this machine — the single
   `DATABASE_URL` host in `.env` vs where the scheduler actually ran. If
   evidence points to another host having run the pipeline, say so — that
   changes the whole deploy session.
5. Only if local artifacts are inconclusive AND the operator grants a
   read: freshness SELECTs against prod tables to bracket the exact last
   writes per table.

**Deliverable:** a written root-cause verdict (or ranked hypotheses with the
discriminating evidence for each), plus a concrete restart recommendation
for the deploy session: what to fix, what to start, how to confirm it stays
alive (liveness check the operator can glance at daily).

**DoD:** verdict grounded in quoted evidence (file:line, log excerpts,
timestamps); zero writes performed anywhere; explicit statement of what was
NOT determinable locally.

---

## OP-2 — Scheduler python interpreter: stop spawning a broken python

**Owner: Kimi.** Repo: `github.com/Sageabdallah/stride-app`.
**Base:** `main` (after CB-1 merges, rebase onto it). **Branch:** `ops/01-python-interpreter`.

**Precondition gate:** clone stride-app; `npm ci && npm run check` green on
clean clone; else stop and report.

Observed 2026-07-29: on boot, the in-process scheduler fires catch-up tasks
and `learn_from_results_v2.py` crashes with `ModuleNotFoundError: dotenv`
(then `numpy`) — the server spawns the SYSTEM python, not the project's
virtualenv, so every python-backed scheduled task is running on luck.

1. Find every place the server spawns python (`runPythonScript` in
   `server/scheduler.ts` and any siblings in `server/*.ts` — enumerate them
   all in the handover). Centralize interpreter resolution into one exported
   helper: use `PYTHON_BIN` from env when set; else the project venv
   (`.venv/bin/python` relative to the repo root) when that file exists;
   else `python3` with a single startup `console.warn` naming the fallback.
2. On python-task failure, log ONE structured line (task name, exit code,
   first stderr line) — no behavior change beyond logging and interpreter
   selection. Do NOT modify any python file, task schedule, or catch-up
   semantics.
3. Add `PYTHON_BIN` to `.env.example` if that file exists; do not touch
   `.env`.
4. Tests (mocked spawn; zero real python execution): env override wins;
   venv path chosen when present; fallback warns once; failure line logged
   with task name.

**DoD:** all spawn sites route through the helper (grep-proof in handover:
zero remaining direct `"python3"` literals in server/*.ts outside the
helper); `npm run check` + `npm test` green; PR open against main.

---

## OP-3 — Post-deploy verification battery

**Owner: Architect session.** **Gate: runs immediately AFTER the operator's
deploy session** (stride-racing #1/#2/#16 merged, migrations applied, flags
set, tips restarted). Prod READS in this task are pre-authorized by the
operator by virtue of requesting the battery — writes remain forbidden.

Independently verify every deploy-session claim on real state:

1. `server/python/deploy_preflight.py` exits 0 — run it yourself, capture
   the board, true exit code (no pipes).
2. Odds-snapshot clock: `runner_odds_snapshots` (and
   `betfair_odds_snapshots` MORNING_CHECK) rows exist for today with
   `snapshot_type`/`odds_source` as designed; watcher pidfile behavior
   matches the roi/04 spec (acquire/release, dedupe).
3. Tips clock: today's `selections` and `prediction_audit` rows exist
   (~87/day expected); `tip_time` populated.
4. Ledger: `selection_ledger` rows for today; after the first results run,
   settled count > 0 and commission math spot-checked on 3 rows by hand.
5. Env flags: `STRIDE_LEDGER_WRITE`, `STRIDE_COMMISSION_RATE`,
   `STRIDE_SHADOW_KELLY` present; snapshot-write default ON confirmed
   behaviorally, not by reading the code.
6. Report GREEN / AMBER / RED per clock with evidence, plus the one-glance
   daily liveness check handed to the operator (from OP-1).

**DoD:** every number in the report traceable to a command output captured
during the battery; discrepancies between runbook expectation and observed
state flagged individually, never averaged into an overall "fine".

---

## OP-4 — Shadow-week check-in (repeat until gates clear)

**Owner: Architect session.** **Cadence:** after each race day post-deploy,
or every 2–3 days. Read-only; prod reads pre-authorized as in OP-3.

1. Count clean shadow race-days accrued for roi/05 (calibrator +
   renormalisation shadow JSONs) and roi/03 (NaN-contract / parity shadow),
   and serve-liveness shadow rows for #11. Verify field-sum invariant
   (|sum − 1| ≤ 1e-6) and tier-transition matrix sanity on the latest day.
2. Calibrator coverage: audit-row count vs the 500-row
   `STRIDE_CAL_MIN_COVERAGE` gate; report days-to-gate at observed rate.
3. Verdict per gated PR (#3, #5, #11): days accrued / days required, plus
   any anomaly that should pause the clock (missing days, invariant
   breaches, empty shadow files).
4. When a PR's gate clears (≥5 clean race days), say so explicitly and
   remind the operator of the merge order — and that **#5 + #11 merged is
   the trigger to re-dispatch 12P-8**.

**DoD per check-in:** counts, not impressions; a one-line trend ("3/5 days,
on pace for Friday"); anomalies raised the day they appear.

---

## OP-5 — Ordered, dependency-aware startup catch-up

**Owner: Kimi.** Repo: `github.com/Sageabdallah/stride-app`.
**Base:** `main` if PR #3 (`ops/01-python-interpreter`) has merged; otherwise
stack on `ops/01-python-interpreter` and say so in the PR (it edits the same
file). **Branch:** `ops/02-catchup-whitelist`.

**Why (OP-1 finding):** `runStartupCatchUps()` in `server/scheduler.ts`
hard-codes exactly two catch-up-eligible tasks (`stride_intelligence_daily`,
`stride_results_nightly`). `process_bets_daily` — the 10:00 task that
creates the day's selections — is not eligible, so any boot after 10:00
silently skips the day's tips. This is a primary cause of the dead-clock
incident.

**The dependency constraint that shapes the design:** the consensus agent
MUST run before tips generation, or every pick gates NO_BET (house rule).
A naive whitelist that fires `process_bets_daily` without first catching up
consensus would restart the clocks with junk data. Catch-up must therefore
be ORDERED and GATED.

1. **Refactor to a data-driven planner + executor** (behavior of the two
   existing catch-ups preserved exactly, test-pinned):
   - Pure function `planStartupCatchUps(now, tasks)` returning an ORDERED
     list of task names due for catch-up, driven by a config table:

     | order | task | scheduled | window |
     |---|---|---|---|
     | 1 | `stride_intelligence_daily` | 06:00 | same-day (existing semantics) |
     | 2 | `consensus_baseline_odds` | 00:30 | same-day |
     | 3 | `consensus_agent_daily` | 07:00 | same-day |
     | 4 | `consensus_morning_odds` | 08:00 | same-day |
     | 5 | `process_bets_daily` | 10:00 | same-day |
     | 6 | `stride_results_nightly` | 23:00 | 24h (existing semantics) |

     A task is due when `now` is past its scheduled time within its window
     and `lastRun` predates that occurrence — the same rule the two
     existing catch-ups implement today.
   - Executor runs the plan SEQUENTIALLY in order (await each before the
     next), logging one line per task started/completed/failed.
2. **Dependency gate:** if `consensus_agent_daily` was due and its catch-up
   FAILED, skip `process_bets_daily` with one loud log line naming the
   reason ("skipping tips catch-up: consensus failed — running tips now
   would gate every pick NO_BET"). Odds-snapshot failures do NOT gate tips
   (snapshots are additive), and nothing gates the results task.
3. **Do NOT change:** any task's schedule, cadence, or callback; any python
   file; the NO_BET/consensus logic itself. This task changes only which
   missed tasks are caught up at boot and in what order.
4. **Tests (mocked callbacks, fake timers/dates; zero python, zero network):**
   - Boot 12:56 → plan is exactly tasks 1–5 in order (plus 6 if due) —
     mirrors the observed incident.
   - Boot 06:30 → baseline-odds due; consensus/morning/bets not yet due.
   - Existing semantics pinned: intelligence same-day rule and nightly 24h
     window produce identical decisions to the current code for the same
     inputs (write these tests against the CURRENT behavior first).
   - Consensus-failure gate: bets skipped, loud log asserted; odds failure
     does not skip bets.
   - Sequential execution order asserted (no interleaving).

**DoD:** planner pure and unit-tested; the two pre-existing catch-ups
behaviorally identical (pinned by tests); dependency gate proven; one
structured log line per catch-up decision (ran / skipped+why);
`npm run check` + `npm test` green; PR open with the standard handover.

---

## Dispatch notes (operator)

- Order that makes sense: OP-1 now (informs your deploy session) → your
  deploy session → OP-3 same day → OP-4 recurring. OP-2 done (PR #3); OP-5 to Kimi any time
  after CB-1 merges (small, independent). CB-2 → CB-3 → CB-4 continue from
  the chatbot pack in parallel.
- Architect tasks run on your word in the architect session; Kimi tasks are
  pasted dispatches (Universal Rules + task text).
- Standing gates unchanged: #3/#5/#11 wait on shadow evidence via OP-4;
  12P-8 waits on #5+#11; the retrain waits on 4–6 weeks of snapshot data
  and an all-green `retrain_preflight.py`.

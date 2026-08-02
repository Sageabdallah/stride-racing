# What has actually executed, and what is still an assumption

Six latent defects in the WP-7 stack surfaced on first execution, none
findable by reading (missing exec bits; no Docker on the runner; Dockerfile
COPY paths valid only in the Mac's folder layout; an AL2 base whose glibc
rejects every modern ML wheel; a Lambda entrypoint that could never have
served Fargate; a --date script called positionally). The lesson is the
register below: a job is not "done" until it has run.

Status vocabulary:
- **RUN** — this exact job ran in its real runtime and exited 0. Claimed
  only with a run id.
- **QUEUED** — scheduled to be executed tonight by deploy smoke,
  post-deploy-verify or verify-jobs; not yet proven.
- **NEVER** — no execution anywhere, and none possible tonight without
  writing dishonest data.

Every underlying script in the table runs daily on the Mac or in CI. That
is not evidence: all six defects so far lived in the container, the task
role, the relay, or the CLI boundary — never in the scripts themselves.

## Register

| Job | Runtime | Slot (Sydney) | Status | If it breaks tomorrow like the entrypoint did |
|---|---|---|---|---|
| bsp-settle | Lambda | 12:00, 18:00 | RUN (deploy smoke 30741848527) | SP/CLV stay NULL with explicit markers; self-heals next run. No clock stops. |
| preflight | Fargate | 04:00 | RUN (30743011392) | Gate readout goes stale in run-state and the digest; no data lost. |
| intelligence-build | Fargate | 06:00 | RUN (sched 30743831537 + 30744702167) | **Tips cannot run.** Empty/absent intelligence → tips degrade or abort → gate-4 rows and shadow evidence stop for the day. |
| calibrator-coverage | Lambda | 02:00 | RUN (30743756889, Fargate) | Gate-3 calibrator day count stops accruing. Recoverable: the emitter is data-driven and backfills every settled day on the next good run. |
| late-odds-watch | Lambda | every 5 min, 11:00–18:30 | RUN (30742455791) | No late_t5 rows; ~90 alarm mails/day. Does not touch a registered gate. |
| weekly-digest | Lambda | Mon 07:00 | RUN (30743011392) | You stop receiving the Monday summary — the failure most likely to hide other failures. |
| racecard-collect | Fargate | 05:30 | RUN (30742455791) | **Sole path** since the duplicate pf-morning-racecards GitHub cron was retired (it ran the same script for the same date, on raw-UTC cron that drifts at DST, and doubled every alarm). |
| results-collect | Fargate | 22:30, 01:00 | RUN (30743450567) | **Covered:** pf-evening-results GitHub cron runs 20:30. |
| nightly-etl | Fargate | 00:45 | RUN (30743450567) | Sectionals/franking go stale; franking gate may trip at the next build. No registered clock stops. |
| gap-heal | Fargate | 03:00 | RUN (30743450567) | Self-healing stops — gaps stay open instead of closing. Silent until the digest. |
| **tips-pipeline** | Fargate | 10:00 | proof RUN (30744702167); live never | **Worst case in the stack.** No tips → no ledger rows (VR-001 sample), no audit rows (gate 4), no serve-liveness evidence (gate 3). Two of five gate clocks stop for the day. Unique unexercised paths: model staging from S3, the two-location card copy, the artifact-relay download, 8 GB MC memory, ~17 min runtime. |
| **consensus-agent** | Fargate | 07:00 | proof RUN (30743992792); live never | Tips still runs but every pick becomes NO_BET (documented hard gate). **Worse than a loud failure**: rows are produced, all refused, and the VR-001 sample gains nothing while looking healthy. |
| **morning-odds** | Fargate | 08:00 | **NEVER** | Market pillar dead → picks gate toward NO_BET. Key unknown: Betfair from a Fargate IP. Strong prior — the Lightsail Sydney runner (also AWS Sydney) transacts with Betfair fine. |
| **tip-time-snapshot** | Lambda | 10:45, 11:00 | **NEVER** | Gate 1's clock. **Covered:** the betfair-odds-snapshot GitHub cron also fires 10:45 on the syd runner and wrote 46 rows x3 today. |

## The honest summary

Twelve of fourteen jobs have now executed in their real runtime and exited 0, each with a run id above. tips and consensus have green non-committing proofs; only morning-odds and tip-time-snapshot have not run at all, both excluded for data honesty and both covered by a GitHub cron.
Four cannot run for real tonight without lying in the data: an evening
tips run would re-upsert day-1 ledger rows at post-race prices; a
post-jump tip_time write is what the WP-2 guard exists to prevent; a
"morning" snapshot written at night is false; consensus costs real spend
and overwrites today's file.

Redundancy, deliberately kept until AWS proves itself: racecards,
tip_time snapshots and results all still run on their GitHub crons, which
are already Mac-free. Both paths are idempotent (racecard overwrite +
upsert, snapshot ON CONFLICT DO NOTHING, results upsert), so tomorrow's
double execution is safe and is the intended belt-and-braces for day one.

Two of those four now have non-committing proof variants (`tips-proof`,
`consensus-proof`) that exercise the same container, secrets, model
staging, relay and card copy while writing nothing. A green proof does
not prove the live job — it leaves only the DB-write and LLM-call tails
unexercised — but it removes the failure mode that has produced all six
defects so far.

**The single points of failure tomorrow are exactly three: intelligence
(06:00), consensus (07:00), tips (10:00).** They are AWS-only, they are
the three never-executed jobs that matter, and they are chained: each
feeds the next. First live proof arrives 06:00–10:20; every failure alarms
to SNS, and the Mac can run the whole chain manually as fallback.

## Jobs that could exit 0 having done nothing (2026-08-02, closed)

Found by asking a different question than "does it run": *can this script
report failure at all?* Scanning the 15 scripts the handler shells out to
for any non-zero exit path returned four with none —
`run_tips_pipeline.py`, `stride_build.py`, `odds_movement.py`,
`stride_results_collector.py`. They still fail loudly on an uncaught
exception; what they cannot do is report a semantic no-op.

Two live consequences, both fixed:

1. `auto_results_collector.py` printed `success: true` and exited 0 with
   8 of 8 races failed. It is what settles `prediction_audit.actual_position`,
   the source of the calibrator's shadow evidence and gate 4. A missing
   `PUNTINGFORM_API_KEY` would have settled nothing every night while
   reporting success, and gate-3 evidence would silently never accrue.
   A failed *fetch* is now fatal; races merely not resulted yet stay
   non-fatal for the retry pass.
2. `job_tips_pipeline` staged the racecard `if os.path.exists(src)` and
   never checked; `job_intelligence_build` ignored `_prepare_racecard()`'s
   return. One failed 05:30 collect would have let all three morning jobs
   no-op and report success. `_require_racecard` is now wired into all
   three, and each asserts its own output afterwards.

Note the asymmetry deliberately kept: tips does **not** assert a bet
count. An all-NO_BET day is a legitimate outcome, not a failure.

Still unguarded by choice: `odds_movement.py` and
`stride_results_collector.py` have no post-condition. Both are covered by
GitHub crons and neither gates evidence accrual, but they remain the two
places a silent no-op could still hide.

## Not implemented, and why: the results-collector post-condition

`stride_results_collector.py` has no non-zero exit path, so in principle
it belongs with the morning-jobs post-conditions. It was designed, then
adversarially reviewed, and **the design was refuted on its load-bearing
assumption** — so it has not been implemented.

The proposed guard was "tips exist but nothing was scored → fatal", made
safe by the claim that an all-NO_BET day owes zero rows. Replaying
`find_tipped_races`' own rule over all 40 `racecards/tips_*.json` shows
`coverage_pick` present on **100%** of races in every file, and
`stride_results_collector.py:216-218` reads it *outside* the
`bet_status == "BET"` branch. So an all-NO_BET day owes the full race
count, not zero — the guard would fire hardest on precisely the day it
promised to exempt.

The corrected invariant (tips present AND results for the tipped tracks
landed AND still zero scored) is sound but needs tips-file parsing, track
normalisation and DynamoDB state, and by its author's own account fails
silent when normalisation misses. That is materially more machinery than
the defect it closes, aimed at the nightly job that currently settles
day-zero gate evidence.

Deferred deliberately. The higher-value defect in the same job — the
collector reporting `success: true` with 8 of 8 races failed — is fixed
and shipped, as is the `TimeoutExpired` bypass that made the
today/yesterday asymmetry a fiction.

## Betfair cert: blocked on one human step

The pair at `certs/betfair-client.crt` / `.key` is **verified good**:
`CN=stride-betfair-bot`, valid 31 Jul 2026 → 31 Jul 2027, and the key and
certificate moduli match. `scripts/betfair_cert_check.py` currently exits
3 with `CERT_AUTH_REQUIRED` — the pre-upload state.

Nothing cert-related is deployed to AWS, deliberately.
`stride-late-odds-watch` is a Lambda firing every 5 minutes across the
racing window, and its read-only `/var/task` means the session token
never persists, so every invocation logs in fresh. Shipping cert files to
AWS before the upload is registered would turn each of those into a
rejected cert login plus an interactive fallback — roughly 190 credential
submissions a day against an account with a lockout history. Prove it
locally first, then deploy the materialisation.

## Recorded decision: stay on the DELAYED Betfair app key (2026-08-02)

Measured, not assumed. The delayed key reports `isMarketDataDelayed=True`,
but the observed price-refresh cadence before jump was **median 2.3 s,
p90 4.6 s** — indistinguishable from live for a system that places nothing
in-play and reads its last prices well before the gate.

Decision: keep the delayed key. Revisit only if the strategy ever needs
in-play or sub-second pre-jump reaction, which nothing in the current
pipeline does. Reversal cost is small and known: update one Secrets
Manager value and run `deploy-infra` with `-f skip_image=true`, then the
smoke. No code depends on which key is in use.

## Known, not fixed: a hand-disabled schedule does not survive a deploy

`infra/06_schedules.sh:36` and `infra/07b_fargate_schedules.sh:34` both
call `aws scheduler update-schedule` without `--state`. That API is
full-replacement, so an omitted `State` reverts to its default of
`ENABLED`. "Disable X while we investigate" is therefore silently undone
by the next `deploy-infra`.

Verified 2026-08-02: all 16 schedules read `ENABLED`, which is the state
tomorrow wants, so nothing is currently wrong. Left unfixed deliberately
— editing the deploy path is the riskiest change available the night
before first live run, and the defect makes schedules more enabled, never
less. Fix is to read current state and pass `--state` explicitly, logging
loudly when preserving a `DISABLED` so a forgotten disable cannot hide.

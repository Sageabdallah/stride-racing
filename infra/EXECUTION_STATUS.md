# What has actually executed, and what is still an assumption

Six latent defects in the WP-7 stack surfaced on first execution, none
findable by reading (missing exec bits; no Docker on the runner; Dockerfile
COPY paths valid only in the Mac's folder layout; an AL2 base whose glibc
rejects every modern ML wheel; a Lambda entrypoint that could never have
served Fargate; a --date script called positionally). The lesson is the
register below: a job is not "done" until it has run.

Status vocabulary:
- **RUN** — this exact job ran in its real runtime and exited 0.
- **SCRIPT-ONLY** — the underlying script runs daily elsewhere (Mac or CI),
  but never inside the container, under the task role, with S3 relay.
- **NEVER** — no execution anywhere.

## Register

| Job | Runtime | Slot (Sydney) | Status | If it breaks tomorrow like the entrypoint did |
|---|---|---|---|---|
| bsp-settle | Lambda | 12:00, 18:00 | RUN (deploy smoke) | SP/CLV stay NULL with explicit markers; self-heals next run. No clock stops. |
| preflight | Fargate | 04:00 | RUN (deploy smoke) | Gate readout goes stale in run-state and the digest; no data lost. |
| intelligence-build | Fargate | 06:00 | RUN (scheduled proof) | **Tips cannot run.** Empty/absent intelligence → tips degrade or abort → gate-4 rows and shadow evidence stop for the day. |
| calibrator-coverage | Lambda | 02:00 | RUN (verify-jobs) | Gate-3 calibrator day count stops accruing. Recoverable: the emitter is data-driven and backfills every settled day on the next good run. |
| late-odds-watch | Lambda | every 5 min, 11:00–18:30 | RUN (verify-jobs) | No late_t5 rows; ~90 alarm mails/day. Does not touch a registered gate. |
| weekly-digest | Lambda | Mon 07:00 | RUN (verify-jobs) | You stop receiving the Monday summary — the failure most likely to hide other failures. |
| racecard-collect | Fargate | 05:30 | RUN (verify-jobs) | **Covered:** the pf-morning-racecards GitHub cron also runs 05:30 and is Mac-free. |
| results-collect | Fargate | 22:30, 01:00 | RUN (verify-jobs) | **Covered:** pf-evening-results GitHub cron runs 20:30. |
| nightly-etl | Fargate | 00:45 | RUN (verify-jobs) | Sectionals/franking go stale; franking gate may trip at the next build. No registered clock stops. |
| gap-heal | Fargate | 03:00 | RUN (verify-jobs) | Self-healing stops — gaps stay open instead of closing. Silent until the digest. |
| **tips-pipeline** | Fargate | 10:00 | **NEVER** | **Worst case in the stack.** No tips → no ledger rows (VR-001 sample), no audit rows (gate 4), no serve-liveness evidence (gate 3). Two of five gate clocks stop for the day. Unique unexercised paths: model staging from S3, the two-location card copy, the artifact-relay download, 8 GB MC memory, ~17 min runtime. |
| **consensus-agent** | Fargate | 07:00 | **NEVER** | Tips still runs but every pick becomes NO_BET (documented hard gate). **Worse than a loud failure**: rows are produced, all refused, and the VR-001 sample gains nothing while looking healthy. |
| **morning-odds** | Fargate | 08:00 | **NEVER** | Market pillar dead → picks gate toward NO_BET. Key unknown: Betfair from a Fargate IP. Strong prior — the Lightsail Sydney runner (also AWS Sydney) transacts with Betfair fine. |
| **tip-time-snapshot** | Lambda | 10:45, 11:00 | **NEVER** | Gate 1's clock. **Covered:** the betfair-odds-snapshot GitHub cron also fires 10:45 on the syd runner and wrote 46 rows x3 today. |

## The honest summary

Ten of fourteen jobs will have executed in their real runtime tonight.
Four have not, and cannot be tonight without lying in the data: an evening
tips run would re-upsert day-1 ledger rows at post-race prices; a
post-jump tip_time write is what the WP-2 guard exists to prevent; a
"morning" snapshot written at night is false; consensus costs real spend
and overwrites today's file.

Redundancy, deliberately kept until AWS proves itself: racecards,
tip_time snapshots and results all still run on their GitHub crons, which
are already Mac-free. Both paths are idempotent (racecard overwrite +
upsert, snapshot ON CONFLICT DO NOTHING, results upsert), so tomorrow's
double execution is safe and is the intended belt-and-braces for day one.

**The single points of failure tomorrow are exactly three: intelligence
(06:00), consensus (07:00), tips (10:00).** They are AWS-only, they are
the three never-executed jobs that matter, and they are chained: each
feeds the next. First live proof arrives 06:00–10:20; every failure alarms
to SNS, and the Mac can run the whole chain manually as fallback.

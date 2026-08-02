# STRIDE unattended operation on AWS (WP-7)

Goal: the Mac off for two months, everything collected and ingested, gaps
healed automatically, retrain gate status current, one query for health,
weekly digest by email.

## State at commit time (2026-08-02)

BLOCKED ON DEPLOY, NOT ON CODE. Two operator actions are required before
any script here can run:

1. `aws login` on this machine (session expired; interactive, operator
   only). Region is already ap-southeast-2.
2. Docker for the one-time image build (this Mac has none). Run
   `04_ecr_image.sh` on any machine with Docker, including the
   stride-syd-runner box.

Everything below is idempotent: safe to re-run top to bottom at any time.

## Order

    ./00_prereqs.sh                     sanity: identity, region
    ./01_secrets.sh                     .env -> Secrets Manager (stride/prod)
    ./02_state_table.sh                 DynamoDB stride_run_state
    ./03_notifications.sh you@mail      SNS topic + budget alarm + log retention policy
    ./04_ecr_image.sh                   build + push the job image (needs Docker)
    ./05_lambda_jobs.sh                 container Lambdas + per-function DLQs + alarms
    ./06_schedules.sh                   EventBridge Scheduler, Australia/Sydney timezone
    ./07_fargate_heavy.sh               ECS cluster + heavy task defs + their schedules
    ./08_digest.sh                      weekly digest schedule

## Job map, Sydney time (deltas from the work-order table, with reasons)

| Job | Time | Runs on | Notes |
|---|---|---|---|
| racecard_collect + schedule seed | 05:30 daily | Lambda | as ordered; PF cards are published overnight |
| intelligence build | 06:00 daily | Fargate | NOT in the work-order table but required: without it tips cannot run and nothing accrues toward the retrain gates. Time kept from the Mac scheduler |
| consensus agent | 07:00 daily | Fargate | same reason; LLM calls plus multi-minute runtime |
| morning odds snapshot | 08:00 daily | Lambda | odds_movement morning (Betfair since WP-1) |
| tips pipeline | 10:00 daily | Fargate | MC scoring runs ~17 minutes with a large model artifact: over Lambda's 15-minute and memory ceilings, hence Fargate |
| tip_time Betfair snapshot | 10:45 daily, retry 11:00 | Lambda | work order said "at defined tip_time"; tips are struck at 10:00, so tip time IS ~10:45. Retry 15 min later as ordered |
| late-odds watcher | every 5 min, 11:00 to 18:30 | Lambda | work order said "rolling 30 min before first jump through last jump"; EventBridge cannot read race_schedule, so the Lambda self-gates: it exits instantly unless now is inside [first_jump - 30min, last_jump + grace] |
| results collection | 22:30 daily, retry 01:00 | Lambda | as ordered (Mac ran 23:00; 22:30 is fine, night meetings caught by the 01:00 retry) |
| nightly ETL (import + sectionals + franking) | 00:45 daily | Fargate | after results settle, as ordered; franking recompute is heavy |
| gap scan + backfill | 03:00 daily | Lambda | the self-healing pass, as ordered |
| retrain_preflight | 04:00 daily | Lambda | result written to run-state, included in digest |
| calibrator coverage check | Mon 02:00 | Lambda | as ordered |
| weekly digest | Mon 07:00 | Lambda | rows, gaps found/healed, preflight, tip_time day count, gate-status |

All schedules use EventBridge Scheduler with
`--schedule-expression-timezone Australia/Sydney`: DST-proof by
construction, per the work order.

## Self-healing contract

Every job wrapper (infra/jobs/*.py) follows the same shape:
check its own history in stride_run_state, backfill missed days first
(within source limits), do today's work, write rows_written and
gaps_found/healed, raise loudly on failure (DLQ + alarm). Two honesty
rules: tip_time snapshots are live-only and can never be backfilled, so
the gap scan REPORTS those as permanent losses instead of pretending to
heal them; and the PF wall (~31 days) bounds racecard/results backfill,
so a gap older than the wall is also reported as permanent.

## Health in one query

    aws dynamodb scan --table-name stride_run_state --output table

## Acceptance drill (calendar-bound, run after deploy)

1. Turn the Mac off, disable its local scheduler. Seven days later:
   preflight green in run-state every day, digest arrived weekly.
2. Disable one EventBridge schedule for three days, re-enable, confirm the
   03:00 pass healed the healable gap with no manual input.

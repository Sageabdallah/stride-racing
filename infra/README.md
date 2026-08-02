# STRIDE unattended operation on AWS (WP-7)

Goal: the Mac off for two months, everything collected and ingested, gaps
healed automatically, retrain gate status current, one query for health,
weekly digest by email.

## State (updated 2026-08-02 pm)

Deploys from GitHub Actions, not a laptop. The one remaining operator
action: after `aws login`, run `./09_bootstrap_oidc.sh` once — it creates
the GitHub OIDC provider + a deploy role trusting only this repo, and
records the non-secret parameters as repo variables. From then on the
`deploy-infra` workflow (Actions tab, or `gh workflow run deploy-infra.yml`)
runs 00-08 end to end on the syd runner: Docker image build included, AWS
auth via OIDC, secrets sourced from GitHub Actions secrets (the store the
Betfair smoke test verifies) — never a local .env.

Everything below is idempotent: safe to re-run top to bottom at any time.

## Order (the deploy-infra workflow runs exactly this)

    ./00_prereqs.sh                     sanity: identity, region
    ./01_secrets.sh --from-env          GitHub secrets -> Secrets Manager (stride/prod)
    ./02_state_table.sh                 DynamoDB stride_run_state
    ./02b_evidence_bucket.sh            S3 gate-3 evidence store (versioned, private)
    ./03_notifications.sh you@mail      SNS topic + budget alarm + log retention policy
    ./04_ecr_image.sh                   build + push the job image (needs Docker)
    ./05_lambda_jobs.sh                 container Lambdas + per-function DLQs + alarms
    ./06_schedules.sh                   EventBridge Scheduler, Australia/Sydney timezone
    ./07_fargate_heavy.sh               ECS cluster + heavy task defs
    ./07b_fargate_schedules.sh SUB SG   the four heavy-job schedules
    ./08_digest.sh                      weekly digest schedule
    ./09_bootstrap_oidc.sh              (operator, once) OIDC provider + deploy role + repo vars

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
| retrain_preflight + gate status | 04:00 daily | Lambda | both written to run-state, included in digest |
| calibrator shadow evidence | 02:00 daily | Lambda | per-race-day gate-3 evidence to S3; day count is data-driven so missed runs self-backfill (was Mon-only before the gate-3 fix) |
| BSP settlement sweep | 05:00 daily | Lambda | sp/price_close/clv_pct from the free Betfair BSP files; gap-aware since day zero; a file missing past the grace window exits 4 -> alarm |
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

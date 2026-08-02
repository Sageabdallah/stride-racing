# STRIDE unattended operation on AWS (WP-7)

Goal: the Mac off for two months, everything collected and ingested, gaps
healed automatically, retrain gate status current, one query for health,
weekly digest by email.

## State (updated 2026-08-02 pm)

Deploys from GitHub Actions, not a laptop. The one remaining operator
sitting: after `aws login`, run `./09_bootstrap_oidc.sh` (OIDC provider +
repo-locked deploy role + repo variables) and `./09b_upload_models.sh`
(model artifacts -> private S3 bucket; the repo is PUBLIC, so the
proprietary pkl never enters git, releases, or the image — Fargate tasks
stage it at startup). From then on the `deploy-infra` workflow (Actions
tab, or `gh workflow run deploy-infra.yml`) runs 00-08 end to end on the
syd runner: Docker image build included, AWS auth via OIDC, secrets
sourced from GitHub Actions secrets (the store the Betfair smoke test
verifies) — never a local .env.

Runtime split rule: Lambda's filesystem is read-only and Fargate tasks
share no filesystem, so every job that writes repo paths (racecards/,
intelligence/) or reads models/ runs on Fargate, and the racecard ->
intelligence -> consensus -> tips chain relays its file artifacts through
s3://stride-evidence-<acct>/artifacts/. Only DB-only jobs stay on Lambda.

Everything below is idempotent: safe to re-run top to bottom at any time.

## Order (the deploy-infra workflow runs exactly this)

    ./00_prereqs.sh                     sanity: identity, region
    ./01_secrets.sh --from-env          GitHub secrets -> Secrets Manager (stride/prod)
    ./02_state_table.sh                 DynamoDB stride_run_state
    ./02b_evidence_bucket.sh            S3 gate-3 evidence store (versioned, private)
    ./03_notifications.sh you@mail      SNS topic + $20/mo cost tripwire (3 thresholds)
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
| racecard_collect + schedule seed | 05:30 daily | Fargate | writes racecards/ (read-only on Lambda); card relayed via S3 artifacts |
| intelligence build | 06:00 daily | Fargate | NOT in the work-order table but required: without it tips cannot run and nothing accrues toward the retrain gates. Uploads intelligence/ to the artifact relay |
| consensus agent | 07:00 daily | Fargate | LLM calls plus multi-minute runtime; downloads + re-uploads intelligence/ |
| morning odds snapshot | 08:00 daily | Fargate | odds_movement writes market_signals into intelligence/ |
| tips pipeline | 10:00 daily | Fargate | MC scoring ~17 minutes with the model artifact (staged from the models bucket at startup); downloads intelligence/ + racecard, performs the two-location card copy, uploads tips json |
| tip_time Betfair snapshot | 10:45 daily, retry 11:00 | Lambda | DB-only; work order said "at defined tip_time"; tips are struck at 10:00, so tip time IS ~10:45 |
| late-odds watcher | every 5 min, 11:00 to 18:30 | Lambda | DB-only; self-gates outside [first_jump - 30min, last_jump + grace] |
| results collection | 22:30 daily, retry 01:00 | Fargate | collectors write result files as well as DB rows |
| nightly ETL (import + sectionals + franking) | 00:45 daily | Fargate | franking recompute is heavy; re-uploads intelligence/ |
| gap scan + backfill | 03:00 daily | Fargate | calls the racecard/results jobs in-process, so it needs their writable fs |
| retrain_preflight + gate status | 04:00 daily | Fargate | gate 5 shells retrain_preflight, which reads models/racing_ensemble_v2.pkl; both lines written to run-state, digest reads them from there |
| calibrator shadow evidence | 02:00 daily | Lambda | per-race-day gate-3 evidence to S3; day count is data-driven so missed runs self-backfill (was Mon-only before the gate-3 fix) |
| BSP settlement sweep | 12:00 + 18:00 daily | Lambda | sp/price_close/clv_pct from the free Betfair BSP files. The file stamped D appears only after UK day D-1 closes (impossible before ~09:00 AEST, observed by 15:46 AEST), so the sweep runs midday+evening; gap-aware since day zero; a file missing past the grace window exits 4 -> alarm |
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

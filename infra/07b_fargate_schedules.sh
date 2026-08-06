#!/usr/bin/env bash
# ECS RunTask schedules for the heavy jobs. Needs a subnet and security
# group (default VPC values work): ./07b_fargate_schedules.sh subnet-xxx sg-xxx
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
source "$(dirname "$0")/lib_schedule.sh"
SUBNET="${1:?subnet id}"; SG="${2:?security group id}"
ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/stride-scheduler-role"
CLUSTER_ARN="arn:aws:ecs:$AWS_REGION:$ACCOUNT_ID:cluster/stride"
sched_ecs() {  # name cron family
  local NAME=$1 CRON=$2 FAMILY=$3
  local TD_ARN
  TD_ARN=$(aws ecs describe-task-definition --task-definition "$FAMILY" \
    --query taskDefinition.taskDefinitionArn --output text)
  local TARGET
  TARGET=$(cat <<JSON
{"Arn": "$CLUSTER_ARN", "RoleArn": "$ROLE_ARN",
 "EcsParameters": {"TaskDefinitionArn": "$TD_ARN", "LaunchType": "FARGATE",
   "NetworkConfiguration": {"awsvpcConfiguration": {"Subnets": ["$SUBNET"],
     "SecurityGroups": ["$SG"], "AssignPublicIp": "ENABLED"}}}}
JSON
)
  # Same IAM propagation retry as 06 — the ECS target's RoleArn is
  # validated at create time.
  local attempt
  for attempt in $(seq 1 10); do
    if aws scheduler create-schedule --name "$NAME" \
      --schedule-expression "cron($CRON)" \
      --schedule-expression-timezone "Australia/Sydney" \
      --flexible-time-window Mode=OFF --target "$TARGET" \
      >/dev/null 2>/tmp/sched_err; then
      break
    fi
    if grep -q "ConflictException\|already exists" /tmp/sched_err; then
      local KEEP
      KEEP=$(sched_state_to_keep "$NAME")
      aws scheduler update-schedule --name "$NAME" \
        --schedule-expression "cron($CRON)" \
        --schedule-expression-timezone "Australia/Sydney" \
        --flexible-time-window Mode=OFF --state "$KEEP" \
        --target "$TARGET" >/dev/null
      break
    fi
    echo "  $NAME: retrying (attempt $attempt): $(head -c 120 /tmp/sched_err)"
    sleep 10
    [ "$attempt" = "10" ] && { cat /tmp/sched_err >&2; exit 1; }
  done
  echo "schedule $NAME -> $FAMILY @ cron($CRON) Australia/Sydney"
}
retire_schedule() {  # name reason
  # A schedule name carries its fire time, so re-timing a job MUST rename it
  # AND delete the old name. Without this the create below just adds a second
  # schedule and the job runs twice a day — once at each time — with the
  # second run overwriting the first's output from a stale sync-down.
  #
  # Deliberately not `|| true`: a delete that fails for any reason other than
  # "already gone" leaves a duplicate firing tomorrow morning, which is worse
  # than a failed deploy. State is NOT preserved across a retire: if the old
  # schedule was DISABLED by an operator, that intent is lost and the new one
  # comes up ENABLED. Named here so it is a known cost, not a surprise.
  local NAME=$1 WHY=$2 ERR="${TMPDIR:-/tmp}/stride_retire_err.$$"
  if aws scheduler delete-schedule --name "$NAME" >/dev/null 2>"$ERR"; then
    echo "retired $NAME ($WHY)"
  elif grep -q ResourceNotFoundException "$ERR"; then
    echo "retired $NAME (already absent)"
  else
    cat "$ERR" >&2; rm -f "$ERR"; exit 1
  fi
  rm -f "$ERR"
}
# Superseded by the re-timed chain below. Keep these lines until the next
# deploy has run everywhere; they are no-ops once the names are gone.
retire_schedule stride-racecard-0530     "moved to 04:00"
retire_schedule stride-intelligence-0600 "moved to 04:20"
retire_schedule stride-consensus-0700    "moved to 05:30"
retire_schedule stride-morning-odds-0800 "moved to 07:30"
retire_schedule stride-tips-1000         "moved to 08:05"

# The morning chain, in dependency order. Re-timed 2026-08-06 so a SATURDAY
# card can finish before its own races. The cloud chain has never run one: it
# went live 2026-08-03 and every day since has been midweek.
#
# The problem this solves. A Saturday is ~2.6x a midweek card, and
# run_tips_pipeline costs 190-243s per race. The old chain put tips at 10:00,
# which on a real Saturday publishes between 13:15 and 14:20 — after most of
# the card has jumped. Tips after the race are not late, they are nothing.
#
# Why the whole chain moves and not just tips. tips reads consensus, consensus
# reads intelligence, and all three read the card. At ~90 races consensus alone
# costs ~110 minutes, so tips cannot start at 08:00 unless consensus starts at
# 05:30, which needs the card by ~04:00. Moving only the last job would have
# meant tips starting on a consensus file that was still being written.
#
# Why 04:00 is safe for the card. gap-heal at 03:00 already calls
# job_racecard_collect for today and has been succeeding — race_schedule for
# 2026-08-06 was created at 03:01. The provider publishes the next day's fields
# the evening before, so 04:00 is not an earlier ASK, only an earlier read.
# Nothing is lost against the old 05:30 either: final scratchings land after
# 07:00, so neither time was ever a "fresh" card in the sense that matters.
#
#   04:00 racecard-collect   ~6s            card + race_schedule for today
#   04:15 baseline-night     ~5m            needs the card AND race_schedule
#   04:20 intelligence-build ~55m -> 05:15  36.5s/race at 90 races
#   05:30 consensus-agent   ~110m -> 07:20  73.5s/race at 90 races
#   07:30 morning-odds        ~5m -> 07:35  independent of consensus; kept as
#                                           late as the chain allows, because
#                                           Betfair publishes provincial
#                                           markets only a few hours out
#   08:05 tips-pipeline    3.4-6.2h         reads BOTH files above
#
# Every gap is still wider than the MEASURED cost of the job before it at 90
# races, so JOB_TIMEOUTS in infra/jobs/handler.py needs no loosening — and
# must not be loosened past these gaps, or a timed-out job becomes the next
# job silently reading yesterday's file. The two must move together.
#
# What this does NOT fix: at ~90 races tips still costs 4.8-6.2h and lands
# after midday whatever time it starts. The remaining lever is per-meeting
# publication — run_tips_pipeline.py already accepts positional track filters
# and auto-merges them into the canonical tips_<date>.json (see its argparse
# block), so meetings can be published as they finish instead of all at the
# end. That is a handler change with its own rehearsal, not a schedule change.
#
# tip-time-snapshot stays at 10:45 (infra/06_schedules.sh): it shells out to
# betfair_odds_snapshot.py, which reads the Betfair markets for the date and
# never opens tips_<date>.json, so it does not need tips to exist first.
sched_ecs stride-racecard-0400     "0 4 * * ? *"  stride-racecard-collect
# AFTER racecard-collect, never before it. baseline-night maps the Betfair
# catalogue through race_schedule, which seed_race_schedule.py writes inside
# the racecard task, and it resolves the card at racecards/, which the same
# task uploads. scheduler.ts ran this at 00:30 because the Mac had a week of
# cards on disk from every Wednesday 4 PM; on Fargate 00:30 has neither the
# card nor the schedule and the job raises in _require_racecard every day.
sched_ecs stride-baseline-0415     "15 4 * * ? *" stride-baseline-night
sched_ecs stride-intelligence-0420 "20 4 * * ? *" stride-intelligence-build
sched_ecs stride-consensus-0530    "30 5 * * ? *" stride-consensus-agent
sched_ecs stride-morning-odds-0730 "30 7 * * ? *" stride-morning-odds
sched_ecs stride-tips-0805         "5 8 * * ? *"  stride-tips-pipeline
sched_ecs stride-results-2230      "30 22 * * ? *" stride-results-collect
sched_ecs stride-results-retry-0100 "0 1 * * ? *" stride-results-collect
sched_ecs stride-etl-0045          "45 0 * * ? *" stride-nightly-etl
sched_ecs stride-gapheal-0300      "0 3 * * ? *"  stride-gap-heal
sched_ecs stride-preflight-0400    "0 4 * * ? *"  stride-preflight
# Daily: the gate-3 calibrator evidence emitter. Data-driven day count,
# so a missed run backfills itself; on Fargate because its import chain
# writes to the repo tree.
sched_ecs stride-calibrator-0200   "0 2 * * ? *"  stride-calibrator-coverage

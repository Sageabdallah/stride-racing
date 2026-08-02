#!/usr/bin/env bash
# EventBridge Scheduler, all with an explicit Australia/Sydney timezone:
# DST-proof by construction (raw UTC cron drifts an hour twice a year).
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
ROLE_NAME=stride-scheduler-role
if ! aws iam get-role --role-name $ROLE_NAME >/dev/null 2>&1; then
  aws iam create-role --role-name $ROLE_NAME --assume-role-policy-document '{
    "Version": "2012-10-17", "Statement": [{"Effect": "Allow",
    "Principal": {"Service": "scheduler.amazonaws.com"},
    "Action": "sts:AssumeRole"}]}' >/dev/null
  aws iam put-role-policy --role-name $ROLE_NAME --policy-name invoke \
    --policy-document '{"Version": "2012-10-17", "Statement": [
      {"Effect": "Allow", "Action": ["lambda:InvokeFunction",
       "ecs:RunTask", "iam:PassRole"], "Resource": "*"}]}'
  sleep 10
fi
ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$ROLE_NAME"

schedule() {  # name cron function
  local NAME=$1 CRON=$2 FN=$3
  aws scheduler create-schedule --name "$NAME" \
    --schedule-expression "cron($CRON)" \
    --schedule-expression-timezone "Australia/Sydney" \
    --flexible-time-window Mode=OFF \
    --target "Arn=arn:aws:lambda:$AWS_REGION:$ACCOUNT_ID:function:$FN,RoleArn=$ROLE_ARN" \
    2>/dev/null || \
  aws scheduler update-schedule --name "$NAME" \
    --schedule-expression "cron($CRON)" \
    --schedule-expression-timezone "Australia/Sydney" \
    --flexible-time-window Mode=OFF \
    --target "Arn=arn:aws:lambda:$AWS_REGION:$ACCOUNT_ID:function:$FN,RoleArn=$ROLE_ARN" >/dev/null
  echo "schedule $NAME -> $FN @ cron($CRON) Australia/Sydney"
}

# File-writing / model-reading jobs schedule as ECS tasks in 07b, not here:
# racecard 05:30, intelligence 06:00, consensus 07:00, morning-odds 08:00,
# tips 10:00, results 22:30 + retry 01:00, ETL 00:45, gap-heal 03:00,
# preflight 04:00.
schedule stride-tiptime-1045       "45 10 * * ? *"   stride-tip-time-snapshot
schedule stride-tiptime-retry-1100 "0 11 * * ? *"    stride-tip-time-snapshot
schedule stride-lateodds-5min     "0/5 11-18 * * ? *" stride-late-odds-watch
# Daily since the gate-3 fix: the calibrator evidence day-count is data-
# driven (recomputed from settled audit rows), but the gate and digest read
# should never be more than a day stale.
schedule stride-calibrator-0200    "0 2 * * ? *"     stride-calibrator-coverage
# The BSP file stamped D appears only after UK day D-1 closes: structurally
# impossible before ~09:00 AEST on D, observed present by 15:46 AEST
# (2026-08-02 probes). A 05:00 sweep would therefore ALWAYS miss by a day.
# 12:00 catches early publication, 18:00 sweeps stragglers — cheap, the
# sweep touches unresolved rows only.
schedule stride-bsp-settle-1218    "0 12,18 * * ? *" stride-bsp-settle
schedule stride-digest-mon         "0 7 ? * MON *"   stride-weekly-digest

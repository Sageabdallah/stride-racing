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

schedule stride-racecard-0530      "30 5 * * ? *"    stride-racecard-collect
schedule stride-morning-odds-0800  "0 8 * * ? *"     stride-morning-odds
schedule stride-tiptime-1045       "45 10 * * ? *"   stride-tip-time-snapshot
schedule stride-tiptime-retry-1100 "0 11 * * ? *"    stride-tip-time-snapshot
schedule stride-lateodds-5min     "0/5 11-18 * * ? *" stride-late-odds-watch
schedule stride-results-2230       "30 22 * * ? *"   stride-results-collect
schedule stride-results-retry-0100 "0 1 * * ? *"     stride-results-collect
schedule stride-gapheal-0300       "0 3 * * ? *"     stride-gap-heal
schedule stride-preflight-0400     "0 4 * * ? *"     stride-preflight
# Daily since the gate-3 fix: the calibrator evidence day-count is data-
# driven (recomputed from settled audit rows), but the gate and digest read
# should never be more than a day stale.
schedule stride-calibrator-0200    "0 2 * * ? *"     stride-calibrator-coverage
# BSP files for AU race day D publish within hours of the last race (UK
# evening); 05:00 Sydney gives slack and the sweep self-heals stragglers.
schedule stride-bsp-settle-0500    "0 5 * * ? *"     stride-bsp-settle
schedule stride-digest-mon         "0 7 ? * MON *"   stride-weekly-digest

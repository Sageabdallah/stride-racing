#!/usr/bin/env bash
# ECS RunTask schedules for the heavy jobs. Needs a subnet and security
# group (default VPC values work): ./07b_fargate_schedules.sh subnet-xxx sg-xxx
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
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
  aws scheduler create-schedule --name "$NAME" \
    --schedule-expression "cron($CRON)" \
    --schedule-expression-timezone "Australia/Sydney" \
    --flexible-time-window Mode=OFF --target "$TARGET" 2>/dev/null || \
  aws scheduler update-schedule --name "$NAME" \
    --schedule-expression "cron($CRON)" \
    --schedule-expression-timezone "Australia/Sydney" \
    --flexible-time-window Mode=OFF --target "$TARGET" >/dev/null
  echo "schedule $NAME -> $FAMILY @ cron($CRON) Australia/Sydney"
}
sched_ecs stride-intelligence-0600 "0 6 * * ? *"  stride-intelligence-build
sched_ecs stride-consensus-0700    "0 7 * * ? *"  stride-consensus-agent
sched_ecs stride-tips-1000         "0 10 * * ? *" stride-tips-pipeline
sched_ecs stride-etl-0045          "45 0 * * ? *" stride-nightly-etl

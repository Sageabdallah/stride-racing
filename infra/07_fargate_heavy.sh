#!/usr/bin/env bash
# The jobs Lambda cannot hold: intelligence build (heavy DB + graph work),
# consensus agent (LLM calls, multi-minute), tips pipeline (MC scoring runs
# ~17 minutes with a large model artifact: over Lambda's 15-minute runtime
# ceiling), nightly ETL (franking recompute). Same image as the Lambdas.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
IMAGE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/stride-jobs:latest"
aws ecs describe-clusters --clusters stride --query 'clusters[0].status' \
  --output text 2>/dev/null | grep -q ACTIVE || \
  aws ecs create-cluster --cluster-name stride >/dev/null
EXEC_ROLE=stride-ecs-exec-role
if ! aws iam get-role --role-name $EXEC_ROLE >/dev/null 2>&1; then
  aws iam create-role --role-name $EXEC_ROLE --assume-role-policy-document '{
    "Version": "2012-10-17", "Statement": [{"Effect": "Allow",
    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
    "Action": "sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name $EXEC_ROLE \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
  sleep 10
fi
for spec in "intelligence-build 2048 4096" "consensus-agent 1024 2048" \
            "tips-pipeline 2048 8192" "nightly-etl 1024 4096"; do
  read -r NAME CPU MEM <<< "$spec"
  cat > /tmp/stride-task-$NAME.json <<JSON
{"family": "stride-$NAME", "networkMode": "awsvpc",
 "requiresCompatibilities": ["FARGATE"], "cpu": "$CPU", "memory": "$MEM",
 "executionRoleArn": "arn:aws:iam::$ACCOUNT_ID:role/$EXEC_ROLE",
 "taskRoleArn": "arn:aws:iam::$ACCOUNT_ID:role/stride-jobs-role",
 "containerDefinitions": [{"name": "job", "image": "$IMAGE",
   "command": ["jobs.handler.dispatch"],
   "environment": [{"name": "STRIDE_JOB", "value": "$NAME"},
                   {"name": "STRIDE_SECRET_ID", "value": "stride/prod"}],
   "logConfiguration": {"logDriver": "awslogs", "options": {
     "awslogs-group": "/ecs/stride-$NAME", "awslogs-region": "$AWS_REGION",
     "awslogs-stream-prefix": "job", "awslogs-create-group": "true"}}}]}
JSON
  aws ecs register-task-definition --cli-input-json file:///tmp/stride-task-$NAME.json >/dev/null
  aws logs put-retention-policy --log-group-name "/ecs/stride-$NAME" \
    --retention-in-days 14 2>/dev/null || true
  echo "task stride-$NAME registered"
done
echo "NOTE: wire the four ECS schedules (06:00 intelligence, 07:00 consensus,"
echo "10:00 tips, 00:45 ETL, Australia/Sydney) with scheduler targets of type"
echo "ECS RunTask against cluster stride; requires a subnet + security group"
echo "argument: ./07b_fargate_schedules.sh SUBNET_ID SG_ID (written next to this)"

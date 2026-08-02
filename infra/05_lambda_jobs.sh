#!/usr/bin/env bash
# Container Lambdas from the single stride-jobs image. Per function: an SQS
# DLQ, two async retries with backoff, a CloudWatch error alarm to SNS, and
# 14-day log retention (cost guardrail).
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
IMAGE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/stride-jobs:latest"
TOPIC_ARN=$(aws sns create-topic --name stride-alerts --query TopicArn --output text)
ROLE_NAME=stride-jobs-role
if ! aws iam get-role --role-name $ROLE_NAME >/dev/null 2>&1; then
  aws iam create-role --role-name $ROLE_NAME --assume-role-policy-document '{
    "Version": "2012-10-17", "Statement": [{"Effect": "Allow",
    "Principal": {"Service": "lambda.amazonaws.com"},
    "Action": "sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name $ROLE_NAME \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  aws iam put-role-policy --role-name $ROLE_NAME --policy-name stride-jobs-inline \
    --policy-document '{"Version": "2012-10-17", "Statement": [
      {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
       "Resource": "*"},
      {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem",
       "dynamodb:UpdateItem", "dynamodb:Scan"],
       "Resource": "*"},
      {"Effect": "Allow", "Action": ["sqs:SendMessage", "sns:Publish"],
       "Resource": "*"}]}'
  sleep 10
fi
ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$ROLE_NAME"

# name timeout_s memory_mb
JOBS=(
  "racecard-collect 600 1024"
  "morning-odds 300 512"
  "tip-time-snapshot 300 512"
  "late-odds-watch 300 512"
  "results-collect 900 1024"
  "gap-heal 900 1024"
  "preflight 300 512"
  "calibrator-coverage 300 512"
  "weekly-digest 300 512"
)
for spec in "${JOBS[@]}"; do
  read -r NAME TIMEOUT MEMORY <<< "$spec"
  FN="stride-$NAME"
  DLQ_URL=$(aws sqs create-queue --queue-name "$FN-dlq" --query QueueUrl --output text)
  DLQ_ARN=$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" \
    --attribute-names QueueArn --query Attributes.QueueArn --output text)
  if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
    aws lambda update-function-code --function-name "$FN" \
      --image-uri "$IMAGE" >/dev/null
  else
    aws lambda create-function --function-name "$FN" \
      --package-type Image --code ImageUri="$IMAGE" \
      --role "$ROLE_ARN" --timeout "$TIMEOUT" --memory-size "$MEMORY" \
      --environment "Variables={STRIDE_JOB=$NAME,STRIDE_SECRET_ID=stride/prod}" \
      --dead-letter-config TargetArn="$DLQ_ARN" >/dev/null
  fi
  aws lambda put-function-event-invoke-config --function-name "$FN" \
    --maximum-retry-attempts 2 >/dev/null
  aws logs put-retention-policy --log-group-name "/aws/lambda/$FN" \
    --retention-in-days 14 2>/dev/null || true
  aws cloudwatch put-metric-alarm --alarm-name "$FN-errors" \
    --namespace AWS/Lambda --metric-name Errors \
    --dimensions Name=FunctionName,Value="$FN" \
    --statistic Sum --period 3600 --evaluation-periods 1 --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --treat-missing-data notBreaching --alarm-actions "$TOPIC_ARN" >/dev/null
  echo "$FN ready"
done

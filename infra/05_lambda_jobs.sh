#!/usr/bin/env bash
# Container Lambdas from the single stride-jobs image. Per function: an SQS
# DLQ, two async retries with backoff, a CloudWatch error alarm to SNS, and
# 14-day log retention (cost guardrail).
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
IMAGE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/stride-jobs:latest"
TOPIC_ARN=$(aws sns create-topic --name stride-alerts --query TopicArn --output text)
ROLE_NAME=stride-jobs-role
# This role is BOTH the Lambda execution role and the ECS task role, so
# both services must be able to assume it. Lambda-only trust made every
# Fargate RunTask fail with "ECS was unable to assume the role" (run
# 30741594107) — i.e. all nine scheduled tasks would have died at startup.
# Asserted on every run, not just creation, so an existing role is repaired.
TRUST='{"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
  "Principal": {"Service": ["lambda.amazonaws.com", "ecs-tasks.amazonaws.com"]},
  "Action": "sts:AssumeRole"}]}'
if aws iam get-role --role-name $ROLE_NAME >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name $ROLE_NAME \
    --policy-document "$TRUST"
else
  aws iam create-role --role-name $ROLE_NAME \
    --assume-role-policy-document "$TRUST" >/dev/null
  sleep 10
fi
aws iam attach-role-policy --role-name $ROLE_NAME \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
# Inline policy is asserted on every run (not only at role creation) so a
# policy change here actually lands on re-deploy. s3 = the evidence store.
aws iam put-role-policy --role-name $ROLE_NAME --policy-name stride-jobs-inline \
  --policy-document '{"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
     "Resource": "*"},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem",
     "dynamodb:UpdateItem", "dynamodb:Scan"],
     "Resource": "*"},
    {"Effect": "Allow", "Action": ["sqs:SendMessage", "sns:Publish"],
     "Resource": "*"},
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject",
     "s3:ListBucket"],
     "Resource": "*"}]}'
ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$ROLE_NAME"
EVIDENCE_BUCKET="stride-evidence-$ACCOUNT_ID"

# name timeout_s memory_mb
# Only jobs that never write repo paths and never load the model live on
# Lambda (read-only filesystem). Everything that writes racecards/,
# intelligence/, or reads models/ runs as a Fargate task (07): racecard,
# morning-odds, results, gap-heal, preflight, intelligence, consensus,
# tips, ETL.
JOBS=(
  "tip-time-snapshot 300 512"
  "late-odds-watch 300 512"
  "calibrator-coverage 300 512"
  "bsp-settle 300 512"
  "weekly-digest 300 512"
)
for spec in "${JOBS[@]}"; do
  read -r NAME TIMEOUT MEMORY <<< "$spec"
  FN="stride-$NAME"
  DLQ_URL=$(aws sqs create-queue --queue-name "$FN-dlq" --query QueueUrl --output text)
  DLQ_ARN=$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" \
    --attribute-names QueueArn --query Attributes.QueueArn --output text)
  ENV="Variables={STRIDE_JOB=$NAME,STRIDE_SECRET_ID=stride/prod,STRIDE_EVIDENCE_BUCKET=$EVIDENCE_BUCKET,STRIDE_MODELS_BUCKET=stride-models-$ACCOUNT_ID,STRIDE_ALERT_TOPIC_ARN=arn:aws:sns:$AWS_REGION:$ACCOUNT_ID:stride-alerts}"
  if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
    aws lambda update-function-code --function-name "$FN" \
      --image-uri "$IMAGE" >/dev/null
    aws lambda wait function-updated --function-name "$FN"
    aws lambda update-function-configuration --function-name "$FN" \
      --timeout "$TIMEOUT" --memory-size "$MEMORY" \
      --environment "$ENV" >/dev/null
    aws lambda wait function-updated --function-name "$FN"
  else
    # IAM is eventually consistent and CreateFunction validates the role's
    # SQS/DLQ permission at call time: a freshly written inline policy is
    # not visible yet and the call fails with InvalidParameterValue
    # (run 30741287142). Retry until it propagates rather than sleeping a
    # guessed interval.
    for attempt in $(seq 1 12); do
      if aws lambda create-function --function-name "$FN" \
        --package-type Image --code ImageUri="$IMAGE" \
        --role "$ROLE_ARN" --timeout "$TIMEOUT" --memory-size "$MEMORY" \
        --environment "$ENV" \
        --dead-letter-config TargetArn="$DLQ_ARN" >/dev/null 2>/tmp/lambda_err; then
        break
      fi
      grep -q "does not have permissions\|InvalidParameterValue" /tmp/lambda_err || {
        cat /tmp/lambda_err >&2; exit 1; }
      echo "  $FN: waiting for IAM propagation (attempt $attempt)"
      sleep 10
      [ "$attempt" = "12" ] && { cat /tmp/lambda_err >&2; exit 1; }
    done
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

#!/usr/bin/env bash
# Container Lambdas from the single stride-jobs image. Per function: an SQS
# DLQ, two async retries with backoff, a CloudWatch error alarm to SNS, and
# 60-day log retention.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
IMAGE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/stride-jobs:latest"

# CloudWatch log retention, in days. One place to change it for every group
# this script owns. 60 rather than 14 because two windows outlast a fortnight:
# WP-7's disappear-for-two-months-and-return-to-a-healthy-system test (at 14
# days you come back to logs covering only the final fortnight, so any failure
# earlier in the absence is undiagnosable), and the 42-day VR-002 validation
# window, whose evidence trail has to stay intact for its full length.
# Retention moves storage only. Ingestion is billed on arrival however long the
# logs are then kept, so this multiplies the smaller line item: at roughly
# 200 KB/day across all fifteen groups, 14 -> 60 costs about a third of a cent
# per year at the Sydney rate of 0.033 USD per GB-month. Not a cost decision at
# this size.
LOG_RETENTION_DAYS=60

# Per-job retention exceptions, space-separated, each written as job:days.
# Empty, and that is a measured result rather than an oversight. late-odds-watch
# was the obvious candidate because it fires every five minutes through the
# racing window, but frequency turned out to be the wrong metric. Over the 24h
# to 2026-08-03 it ingested 16.4 KB, 8% of the estate's 203 KB/day, while the
# once-daily results-collect and preflight tasks ingested 36.5 KB and 33.1 KB.
# The expensive groups are the ones that print a lot per run, not the ones that
# run often. Measure IncomingBytes per group before adding an entry here.
LOG_RETENTION_EXCEPTIONS=""

# Days to keep $1's logs: its exception if it has one, otherwise the default.
retention_days() {
  local pair
  for pair in $LOG_RETENTION_EXCEPTIONS; do
    if [ "${pair%%:*}" = "$1" ]; then echo "${pair##*:}"; return; fi
  done
  echo "$LOG_RETENTION_DAYS"
}

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
# Lambda (read-only filesystem). NOTE: 'writes repo paths' includes writes
# at IMPORT time — walk_forward_backtest makedirs backtest_results on
# import, which is what moved calibrator-coverage to Fargate. Everything that writes racecards/,
# intelligence/, or reads models/ runs as a Fargate task (07): racecard,
# morning-odds, results, gap-heal, preflight, intelligence, consensus,
# tips, ETL.
JOBS=(
  "tip-time-snapshot 300 512"
  "late-odds-watch 300 512"
  "bsp-settle 300 512"
  "weekly-digest 300 512"
)
for spec in "${JOBS[@]}"; do
  read -r NAME TIMEOUT MEMORY <<< "$spec"
  FN="stride-$NAME"
  DLQ_URL=$(aws sqs create-queue --queue-name "$FN-dlq" --query QueueUrl --output text)
  DLQ_ARN=$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" \
    --attribute-names QueueArn --query Attributes.QueueArn --output text)
  ENV="Variables={STRIDE_JOB=$NAME,STRIDE_SECRET_ID=stride/prod,STRIDE_EVIDENCE_BUCKET=$EVIDENCE_BUCKET,STRIDE_MODELS_BUCKET=stride-models-$ACCOUNT_ID,STRIDE_ALERT_TOPIC_ARN=arn:aws:sns:$AWS_REGION:$ACCOUNT_ID:stride-alerts,MPLCONFIGDIR=/tmp/mpl}"
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
  # Same defect 07_fargate_heavy.sh already fixed, still live on this side:
  # Lambda creates its log group on first INVOCATION, not at create-function,
  # so on a fresh account put-retention-policy hit a group that did not exist
  # yet and `2>/dev/null || true` swallowed the failure. Functions that have
  # never run — tip-time-snapshot until today — therefore create their group
  # with unlimited retention and keep it until some later deploy happens to
  # land after their first invocation. Create the group first, and let the
  # retention call fail loudly if it fails.
  #
  # The `|| true` below is safe ONLY because put-retention-policy on the line
  # after it is unguarded. create-log-group returns ResourceAlreadyExistsException
  # on a re-run, so it has to be tolerated — but it would equally swallow
  # AccessDenied or an invalid name. What catches that is the retention call
  # failing loudly on a group that does not exist. Guard that call and this one
  # goes silent again. Do not add `|| true` to it.
  aws logs create-log-group --log-group-name "/aws/lambda/$FN" 2>/dev/null || true
  aws logs put-retention-policy --log-group-name "/aws/lambda/$FN" \
    --retention-in-days "$(retention_days "$NAME")"
  aws cloudwatch put-metric-alarm --alarm-name "$FN-errors" \
    --namespace AWS/Lambda --metric-name Errors \
    --dimensions Name=FunctionName,Value="$FN" \
    --statistic Sum --period 3600 --evaluation-periods 1 --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --treat-missing-data notBreaching --alarm-actions "$TOPIC_ARN" >/dev/null
  echo "$FN ready"
done

# Log groups that outlive their Lambda. calibrator-coverage moved to Fargate in
# 5606924 because its import chain writes to the repo tree; the Lambda itself is
# now deleted, but its log group is kept deliberately. It holds the four failed
# runs that diagnosed the move, and that is the evidence trail for 5606924 --
# the reason the job runs where it runs. Do not delete the group to tidy up.
#
# Nothing else sets retention on it. It is not in JOBS and must not go back:
# a recreated Lambda cannot work, and it would break scripts/classify_runtimes.py
# and verify-jobs.yml. Without the loop below this group is the one member of the
# estate no script owns, so it would drift to whatever it was last given while
# every other group tracked LOG_RETENTION_DAYS.
#
# create-log-group is here so a group deleted by hand comes back rather than
# silently losing retention. That means deleting the group is not enough to
# retire it -- remove this loop in the same change, or it returns empty forever.
for ORPHAN in calibrator-coverage; do
  aws logs create-log-group --log-group-name "/aws/lambda/stride-$ORPHAN" 2>/dev/null || true
  aws logs put-retention-policy --log-group-name "/aws/lambda/stride-$ORPHAN" \
    --retention-in-days "$(retention_days "$ORPHAN")"
  echo "stride-$ORPHAN log group retention asserted (Lambda retired, logs kept)"
done

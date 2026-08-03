#!/usr/bin/env bash
# SNS alert topic + email subscription + the monthly cost tripwire.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
EMAIL="${1:?usage: 03_notifications.sh you@example.com [budget_usd]}"
# $20, not a cap — the account is on the AWS Free Plan, where credits cover
# usage and access simply ends when they run out, so there is nothing to cap.
# This is a runaway-detector: steady state is ~$0.03/day, so $20 in a month
# means something is looping and you want to hear about it that day.
BUDGET_USD="${2:-20}"

TOPIC_ARN=$(aws sns create-topic --name stride-alerts --query TopicArn --output text)
# Unguarded: subscribe is idempotent for the same topic+protocol+endpoint (it
# returns the existing subscription), so `|| true` bought nothing and hid
# AccessDenied, a malformed address, and a missing topic.
aws sns subscribe --topic-arn "$TOPIC_ARN" --protocol email \
  --notification-endpoint "$EMAIL" >/dev/null
echo "topic=$TOPIC_ARN"

# Assert on the TOTAL, not just on how many are pending. Counting only pending
# was the wrong check: if subscribe had failed outright the topic would hold
# zero subscriptions, pending would be 0, no warning would fire, and silence
# would read as health — the same shape as the budget bug, in the code written
# to catch the budget bug.
#
# This topic carries the CloudWatch alarms from 05_lambda_jobs.sh. It does NOT
# carry the budget notifications: those are Budgets-native EMAIL subscribers
# that need no SNS confirmation, so they are unaffected by anything here.
SUBS=$(aws sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN" \
  --query "Subscriptions[].SubscriptionArn" --output text)
TOTAL=$(printf '%s\n' "$SUBS" | tr '\t' '\n' | grep -c . || true)
PENDING=$(printf '%s\n' "$SUBS" | tr '\t' '\n' | grep -c '^PendingConfirmation$' || true)

if [ "$TOTAL" = "0" ]; then
  echo "FATAL: subscribe reported success but stride-alerts has no subscriptions." \
       "Every CloudWatch alarm routed to this topic has nowhere to go." >&2
  exit 1
fi
if [ "$PENDING" = "$TOTAL" ]; then
  # Not fatal: a first deploy legitimately sits here until the human clicks.
  # Loud, because it stays in this state forever if nobody ever does.
  echo "  WARNING: all $TOTAL subscription(s) on stride-alerts are" \
       "PendingConfirmation — until $EMAIL clicks the AWS confirmation link," \
       "nothing published to this topic reaches anyone" >&2
else
  echo "  stride-alerts: $((TOTAL - PENDING)) of $TOTAL subscription(s) confirmed"
fi

# Budgets is a global service that answers only in us-east-1. Every script here
# runs with AWS_REGION=ap-southeast-2, so the unqualified call failed on every
# deploy since the account opened — and `|| echo "budget exists (left as is)"`
# reported that failure as success. The first successful deploy of a brand-new
# account (run 30741848527) printed "budget exists", which is impossible: no
# budget was ever created. Pin the region, and tell create-vs-already-there
# apart from create-genuinely-failed instead of collapsing both to one message.
BUDGETS=(aws budgets --region us-east-1)
ACCT=(--account-id "$ACCOUNT_ID")

SUBSCRIBERS="[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$EMAIL\"}]"
# 80% ACTUAL is the early warning; 100% ACTUAL is the one that matters and the
# old config did not have it — it alerted once at 80% and then went quiet no
# matter how far past the limit the month ran. FORECASTED catches a burn-rate
# change on day 3 rather than on day 30.
NOTIFS=(
  '{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE"}'
  '{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":100,"ThresholdType":"PERCENTAGE"}'
  '{"NotificationType":"FORECASTED","ComparisonOperator":"GREATER_THAN","Threshold":100,"ThresholdType":"PERCENTAGE"}'
)

BUDGET="{\"BudgetName\":\"stride-monthly\",\"BudgetLimit\":{\"Amount\":\"$BUDGET_USD\",\"Unit\":\"USD\"},\"BudgetType\":\"COST\",\"TimeUnit\":\"MONTHLY\"}"

NWS="["
for n in "${NOTIFS[@]}"; do
  NWS="$NWS{\"Notification\":$n,\"Subscribers\":$SUBSCRIBERS},"
done
NWS="${NWS%,}]"

if ERR=$("${BUDGETS[@]}" create-budget "${ACCT[@]}" --budget "$BUDGET" \
         --notifications-with-subscribers "$NWS" 2>&1); then
  echo "budget stride-monthly created at \$$BUDGET_USD/month"
else
  grep -q "DuplicateRecordException" <<<"$ERR" || { echo "$ERR" >&2; exit 1; }
  # Already there: update-budget carries the limit but not the notifications,
  # so reconcile those separately or a re-run silently keeps the old thresholds.
  "${BUDGETS[@]}" update-budget "${ACCT[@]}" --new-budget "$BUDGET" >/dev/null
  for n in "${NOTIFS[@]}"; do
    if NERR=$("${BUDGETS[@]}" create-notification "${ACCT[@]}" \
              --budget-name stride-monthly \
              --notification "$n" --subscribers "$SUBSCRIBERS" 2>&1); then :; else
      grep -q "DuplicateRecordException" <<<"$NERR" || { echo "$NERR" >&2; exit 1; }
    fi
  done
  echo "budget stride-monthly updated to \$$BUDGET_USD/month"
fi

"${BUDGETS[@]}" describe-budget "${ACCT[@]}" --budget-name stride-monthly \
  --query 'Budget.{Limit:BudgetLimit.Amount,Spent:CalculatedSpend.ActualSpend.Amount}' \
  --output table

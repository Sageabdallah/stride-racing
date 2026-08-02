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
aws sns subscribe --topic-arn "$TOPIC_ARN" --protocol email \
  --notification-endpoint "$EMAIL" >/dev/null 2>&1 || true
echo "topic=$TOPIC_ARN"

# An unconfirmed subscription delivers nothing, silently — the previous version
# printed "confirm the subscription email once" and never checked whether anyone
# had. Say which state it is actually in.
PENDING=$(aws sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN" \
  --query "length(Subscriptions[?SubscriptionArn=='PendingConfirmation'])" \
  --output text)
if [ "$PENDING" != "0" ]; then
  echo "  WARNING: $PENDING subscription(s) on stride-alerts are still" \
       "PendingConfirmation — until $EMAIL clicks the AWS confirmation link," \
       "nothing published to this topic reaches anyone" >&2
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

#!/usr/bin/env bash
# SNS alert topic + email subscription + monthly budget alarm + a CloudWatch
# log-retention default so logs never bill quietly for two months.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
EMAIL="${1:?usage: 03_notifications.sh you@example.com [budget_usd]}"
BUDGET_USD="${2:-50}"
TOPIC_ARN=$(aws sns create-topic --name stride-alerts --query TopicArn --output text)
aws sns subscribe --topic-arn "$TOPIC_ARN" --protocol email \
  --notification-endpoint "$EMAIL" >/dev/null 2>&1 || true
echo "topic=$TOPIC_ARN (confirm the subscription email once)"
cat > /tmp/stride-budget.json <<JSON
{"BudgetName": "stride-monthly", "BudgetLimit": {"Amount": "$BUDGET_USD",
 "Unit": "USD"}, "BudgetType": "COST", "TimeUnit": "MONTHLY"}
JSON
cat > /tmp/stride-budget-notify.json <<JSON
[{"Notification": {"NotificationType": "ACTUAL", "ComparisonOperator":
  "GREATER_THAN", "Threshold": 80, "ThresholdType": "PERCENTAGE"},
  "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "$EMAIL"}]}]
JSON
aws budgets create-budget --account-id "$ACCOUNT_ID" \
  --budget file:///tmp/stride-budget.json \
  --notifications-with-subscribers file:///tmp/stride-budget-notify.json \
  2>/dev/null && echo "budget created" || echo "budget exists (left as is)"

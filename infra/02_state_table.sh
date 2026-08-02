#!/usr/bin/env bash
# DynamoDB run-state: one row per job. On-demand billing (cost guardrail).
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
if ! aws dynamodb describe-table --table-name stride_run_state >/dev/null 2>&1; then
  aws dynamodb create-table \
    --table-name stride_run_state \
    --attribute-definitions AttributeName=job_name,AttributeType=S \
    --key-schema AttributeName=job_name,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST >/dev/null
  aws dynamodb wait table-exists --table-name stride_run_state
  echo "stride_run_state created"
else
  echo "stride_run_state exists"
fi

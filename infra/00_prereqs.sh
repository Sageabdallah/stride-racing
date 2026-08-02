#!/usr/bin/env bash
# WP-7: sanity gate. Every other script sources this.
set -euo pipefail
export AWS_REGION="${AWS_REGION:-ap-southeast-2}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ACCOUNT_ID
echo "account=$ACCOUNT_ID region=$AWS_REGION"

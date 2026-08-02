#!/usr/bin/env bash
# One image for every job: repo code + python deps on the Lambda python
# base, so container Lambdas and Fargate tasks run identical bytes.
# Needs Docker: run on the stride-syd-runner box if this machine has none.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
REPO=stride-jobs
aws ecr describe-repositories --repository-names $REPO >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name $REPO >/dev/null
URI="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO"
aws ecr get-login-password | docker login --username AWS --password-stdin "$URI"
docker build -t "$URI:latest" -f "$(dirname "$0")/Dockerfile" "$(dirname "$0")/.."
docker push "$URI:latest"
echo "pushed $URI:latest"

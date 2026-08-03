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
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# The commit these bytes come from, stamped into the image so the 04:00
# preflight can tell a current image from a stale one. GITHUB_SHA on Actions;
# HEAD otherwise. A dirty tree is stamped "<sha>-dirty" because the SHA no
# longer describes what was built, and the checker must report "cannot verify"
# rather than compare against a commit whose contents were not used.
GIT_SHA="${GIT_SHA:-${GITHUB_SHA:-$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo "")}}"
if [ -n "$GIT_SHA" ] && ! git -C "$ROOT" diff --quiet HEAD 2>/dev/null; then
  GIT_SHA="${GIT_SHA}-dirty"
fi
echo "stamping image with GIT_SHA=${GIT_SHA:-<none>}"

aws ecr get-login-password | docker login --username AWS --password-stdin "$URI"
docker build --build-arg GIT_SHA="$GIT_SHA" \
  -t "$URI:latest" -f "$ROOT/infra/Dockerfile" "$ROOT"
docker push "$URI:latest"
echo "pushed $URI:latest"

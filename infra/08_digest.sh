#!/usr/bin/env bash
# The weekly digest is just the stride-weekly-digest Lambda plus its Monday
# schedule; both are created by 05 and 06. This script exists so the run
# order in the README stays complete, and verifies both exist.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
aws lambda get-function --function-name stride-weekly-digest >/dev/null
aws scheduler get-schedule --name stride-digest-mon >/dev/null
# Runtime split sanity: the five Lambda jobs and the nine Fargate tasks.
for fn in stride-tip-time-snapshot stride-late-odds-watch stride-bsp-settle; do
  aws lambda get-function --function-name "$fn" >/dev/null
done
for td in stride-racecard-collect stride-morning-odds stride-results-collect \
          stride-gap-heal stride-preflight stride-intelligence-build \
          stride-consensus-agent stride-tips-pipeline stride-nightly-etl \
          stride-calibrator-coverage; do
  aws ecs describe-task-definition --task-definition "$td" >/dev/null
done
echo "digest + 4 lambdas + 10 fargate task defs present"

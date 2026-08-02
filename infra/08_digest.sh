#!/usr/bin/env bash
# The weekly digest is just the stride-weekly-digest Lambda plus its Monday
# schedule; both are created by 05 and 06. This script exists so the run
# order in the README stays complete, and verifies both exist.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
aws lambda get-function --function-name stride-weekly-digest >/dev/null
aws scheduler get-schedule --name stride-digest-mon >/dev/null
echo "digest function + schedule present"

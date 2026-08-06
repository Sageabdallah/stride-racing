#!/usr/bin/env bash
# Operator, once (and again whenever the panel is edited): upload the tipster
# panel from this machine to the private models bucket. Same reasoning as 09b
# and the same bucket, under config/ so _stage_models() skips it.
#
# The repo is PUBLIC and this file is gitignored (.gitignore:10). It holds
# which 16 of 37 sources are trusted, which weighting bucket each sits in and
# which carry the proofed-results boost — the vetting, not a description of it.
# historical_accuracy is null today and is designed not to stay null. Git is a
# one-way door: publishing it now while it is cheap keeps it published in every
# clone once it is not. Verified 2026-08-06 that it has never been committed —
# GitHub reports 0 commits touching the path, and the repo has 0 forks.
#
# Versioning is on the bucket already (09b), so every edit keeps history.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
BUCKET="stride-models-$ACCOUNT_ID"
SRC="$(dirname "$0")/../server/python/tipster_panel.json"
KEY="config/tipster_panel.json"

[ -f "$SRC" ] || {
  echo "FATAL: $SRC not found. This is the machine that HAS the panel; if it" >&2
  echo "is missing here it is not recoverable from the repository." >&2
  exit 1
}
# The file is the point, so a malformed one is worse than none: consensus
# would stage it, parse it, find zero usable sources and run panel-less
# looking exactly like a healthy day.
python3 -c "
import json, sys
d = json.load(open('$SRC'))
s = d.get('sources', [])
n = [x for x in s if x.get('active') and x.get('verified')]
print(f'  {len(s)} sources, {len(n)} active+verified')
sys.exit(0 if n else 1)
" || { echo "FATAL: no active+verified sources — refusing to upload" >&2; exit 1; }

# Liveness pre-flight. "verified": true is a claim someone made once, and it
# was wrong for 13 of 16 sources by the time anyone checked — four months after
# the file was last edited. Reporting it at upload time is the cheapest moment
# to notice, since this is the only step that runs when the panel changes.
#
# Reports, does not block, and the distinction matters: exit 1 means some
# sources are unreachable, which is a worse panel but still better than the
# none currently in the cloud. Exit 2 (nothing reachable, or no panel) is the
# one that stops the upload. A source that 403s here can still extract fine via
# Tavily, so blocking on this check would refuse good panels.
echo "checking source liveness (HEAD, no API keys, no cost)..."
set +e
python3 "$(dirname "$0")/../server/python/panel_liveness.py" --panel "$SRC"
LIVE_RC=$?
set -e
[ "$LIVE_RC" -ge 2 ] && { echo "FATAL: no reachable sources — refusing" >&2; exit 1; }
[ "$LIVE_RC" -eq 1 ] && echo "  (uploading anyway: a degraded panel beats no panel)"

aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1 || {
  echo "FATAL: s3://$BUCKET missing — run infra/09b_upload_models.sh first" >&2
  exit 1
}
aws s3 cp "$SRC" "s3://$BUCKET/$KEY"
echo "panel uploaded:"
aws s3 ls "s3://$BUCKET/config/"
echo
echo "Verify it reaches a task:  gh workflow run verify-jobs.yml -f jobs=panel-proof"

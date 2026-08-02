#!/usr/bin/env bash
# .env -> Secrets Manager as one JSON secret (stride/prod). Idempotent:
# create if absent, else put a new version. Nothing reads a local env file
# in production after this; the WP-0 inventory is the key list.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
ENV_FILE="${1:-$(dirname "$0")/../Race-Analytics/.env}"
[ -f "$ENV_FILE" ] || ENV_FILE="$(dirname "$0")/../.env"
[ -f "$ENV_FILE" ] || { echo "no .env found; pass its path as arg 1"; exit 1; }
JSON=$(python3 - "$ENV_FILE" <<'PY'
import json, sys
out = {}
for line in open(sys.argv[1]):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
print(json.dumps(out))
PY
)
if aws secretsmanager describe-secret --secret-id stride/prod >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --secret-id stride/prod --secret-string "$JSON" >/dev/null
  echo "stride/prod updated"
else
  aws secretsmanager create-secret --name stride/prod --secret-string "$JSON" >/dev/null
  echo "stride/prod created"
fi

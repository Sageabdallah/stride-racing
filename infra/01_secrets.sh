#!/usr/bin/env bash
# GitHub secrets / .env -> Secrets Manager as one JSON secret (stride/prod).
# Idempotent: create if absent, else put a new version. Nothing reads a
# local env file in production after this.
#
# Two modes:
#   --from-env   CI path: assemble from process env (GitHub Actions secrets —
#                the store the Betfair smoke test verifies). Absent keys are
#                listed loudly, never silently skipped.
#   <path>       Operator path: parse a .env file (legacy; the CI path is
#                canonical because only verified values live in GH secrets).
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"

if [ "${1:-}" = "--from-env" ]; then
  JSON=$(python3 <<'PY'
import json, os, sys
KEYS = ["BETFAIR_APP_KEY", "BETFAIR_USERNAME", "BETFAIR_PASSWORD",
        "BETFAIR_CERT_PEM", "BETFAIR_KEY_PEM", "DATABASE_URL",
        "PUNTINGFORM_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_CHAT_MODEL",
        "GROQ_API_KEY", "PERPLEXITY_API_KEY", "TAVILY_API_KEY",
        "LLM_ENABLED", "LLM_PROVIDER", "STRIDE_COMMISSION_RATE",
        "STRIDE_LEDGER_WRITE", "STRIDE_SHADOW_KELLY",
        "STRIDE_BOOK_COHERENCE",
        "STRIDE_SERVE_LIVE_FEATURES_SHADOW", "STRIDE_MODEL_WEIGHT",
        "CONSENSUS_WEIGHT", "MARKET_SIGNAL_WEIGHT",
        "CONSENSUS_CONFIRM_THRESHOLD", "CONSENSUS_LOCK_THRESHOLD",
        "MARKET_SIGNAL_THRESHOLD"]
out = {k: os.environ[k] for k in KEYS if os.environ.get(k)}
print(json.dumps(out))
print(f"shipping {len(out)}/{len(KEYS)} keys; absent: "
      f"{sorted(k for k in KEYS if k not in out)}", file=sys.stderr)
PY
)
else
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
fi

if aws secretsmanager describe-secret --secret-id stride/prod >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --secret-id stride/prod --secret-string "$JSON" >/dev/null
  echo "stride/prod updated"
else
  aws secretsmanager create-secret --name stride/prod --secret-string "$JSON" >/dev/null
  echo "stride/prod created"
fi

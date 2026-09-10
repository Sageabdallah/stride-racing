#!/usr/bin/env bash
# Rebuild the model guide from the repository's own records.
# Run from this directory. Requires node (docx) and python (matplotlib).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"

[ -d node_modules/docx ] || npm install --no-save --silent docx

python3 extract_facts.py > facts.json          # weights + constants, via AST
python3 extract_zas.py "$REPO/docs/research/feature_liveness_report.json"
python3 groups.py                              # themes, with a completeness check
python3 charts.py                              # the two figures
node assemble.js                               # the .docx itself
python3 verify_doc.py                          # every figure re-checked against source

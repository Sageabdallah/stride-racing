#!/usr/bin/env bash
# Operator, once (and once per future promotion): upload the trained model
# artifacts from this machine to the private models bucket. The repo is
# PUBLIC and the artifacts are proprietary IP (.gitignore:38-41), so they
# never enter git, releases, or the image — Fargate tasks stage them from
# S3 at startup. Versioning is on, so every promotion keeps history.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
BUCKET="stride-models-$ACCOUNT_ID"
SRC="$(dirname "$0")/../server/python/models"
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET" \
    --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
fi
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
for f in "$SRC"/*.pkl "$SRC"/*.json; do
  [ -e "$f" ] || continue
  aws s3 cp "$f" "s3://$BUCKET/$(basename "$f")"
done
echo "models uploaded:"
aws s3 ls "s3://$BUCKET/"

#!/usr/bin/env bash
# Durable evidence bucket for the retrain gates (gate 3). Lambda/Fargate
# filesystems are ephemeral, so shadow evidence written there is gone when
# the invocation ends — it must land where gate_status.py can count it.
# Versioned because evidence is append-only by intent; fully private.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
BUCKET="stride-evidence-$ACCOUNT_ID"
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET" \
    --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
fi
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
echo "evidence bucket $BUCKET ready"

# Private models bucket (proprietary IP; the repo is public so artifacts
# ride S3, never git or the image — tasks stage them at startup). Upload
# happens from the operator's machine via 09b_upload_models.sh.
MBUCKET="stride-models-$ACCOUNT_ID"
if ! aws s3api head-bucket --bucket "$MBUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$MBUCKET" \
    --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
fi
aws s3api put-bucket-versioning --bucket "$MBUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$MBUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
echo "models bucket $MBUCKET ready (upload via 09b_upload_models.sh)"

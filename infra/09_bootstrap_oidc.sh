#!/usr/bin/env bash
# The single operator bootstrap. Everything after this deploys from GitHub
# Actions via OIDC — no long-lived AWS keys anywhere, no interactive login
# in any scheduled path. Run once, right after `aws login`:
#
#   ./09_bootstrap_oidc.sh [alert-email]
#
# Creates the GitHub OIDC identity provider and a deploy role whose trust
# policy admits ONLY this repo's workflows, then records the non-secret
# deploy parameters as GitHub Actions variables so deploy-infra.yml needs
# no manual input. Idempotent.
set -euo pipefail
source "$(dirname "$0")/00_prereqs.sh"
EMAIL="${1:-sageabdallah10@gmail.com}"
REPO="Sageabdallah/stride-racing"
OIDC_HOST="token.actions.githubusercontent.com"
PROVIDER_ARN="arn:aws:iam::$ACCOUNT_ID:oidc-provider/$OIDC_HOST"

aws iam get-open-id-connect-provider \
  --open-id-connect-provider-arn "$PROVIDER_ARN" >/dev/null 2>&1 || \
  aws iam create-open-id-connect-provider --url "https://$OIDC_HOST" \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 >/dev/null

ROLE=stride-deploy-oidc
TRUST=$(cat <<JSON
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
 "Principal": {"Federated": "$PROVIDER_ARN"},
 "Action": "sts:AssumeRoleWithWebIdentity",
 "Condition": {"StringEquals": {"$OIDC_HOST:aud": "sts.amazonaws.com"},
   "StringLike": {"$OIDC_HOST:sub": "repo:$REPO:*"}}}]}
JSON
)
if aws iam get-role --role-name $ROLE >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name $ROLE --policy-document "$TRUST"
else
  aws iam create-role --role-name $ROLE \
    --assume-role-policy-document "$TRUST" >/dev/null
fi
# The deploy creates the whole stack (IAM roles, Lambdas, ECS, schedules,
# secrets, S3, SNS/SQS, DynamoDB, ECR, alarms) — broad by necessity. The
# trust policy above is the lock: only this repo's workflows can assume it.
# attach-role-policy is already idempotent — re-attaching an attached policy
# succeeds — so `2>/dev/null || true` bought nothing and hid NoSuchEntity,
# AccessDenied and LimitExceeded. A failure here leaves the deploy role with NO
# permissions while this script still prints "OIDC deploy role ready"; the next
# deploy then fails somewhere else entirely and you debug the wrong script.
# The one genuine transient is IAM propagation right after create-role above,
# so retry that specifically, the way 05_lambda_jobs.sh already does.
for attempt in $(seq 1 12); do
  if aws iam attach-role-policy --role-name $ROLE \
     --policy-arn arn:aws:iam::aws:policy/AdministratorAccess 2>/tmp/attach_err; then
    break
  fi
  grep -q "NoSuchEntity" /tmp/attach_err || { cat /tmp/attach_err >&2; exit 1; }
  echo "  $ROLE: waiting for IAM propagation (attempt $attempt)"
  sleep 5
  [ "$attempt" = "12" ] && { cat /tmp/attach_err >&2; exit 1; }
done
# Verify the attachment rather than trusting the call: this role is the only
# thing standing between the deploy workflow and a permission wall.
aws iam list-attached-role-policies --role-name $ROLE \
  --query 'AttachedPolicies[?PolicyName==`AdministratorAccess`]' --output text \
  | grep -q AdministratorAccess || {
    echo "FATAL: AdministratorAccess is not attached to $ROLE after attach reported success" >&2
    exit 1; }

# Default-VPC network for the Fargate schedules (07b) — discovered once here
# so the workflow never needs interactive input.
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)
SUBNET=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC" \
  --query 'Subnets[0].SubnetId' --output text)
SG=$(aws ec2 describe-security-groups --filters Name=vpc-id,Values="$VPC" \
  Name=group-name,Values=default \
  --query 'SecurityGroups[0].GroupId' --output text)

gh variable set AWS_ACCOUNT_ID --repo "$REPO" --body "$ACCOUNT_ID"
gh variable set AWS_SUBNET_ID  --repo "$REPO" --body "$SUBNET"
gh variable set AWS_SG_ID      --repo "$REPO" --body "$SG"
gh variable set ALERT_EMAIL    --repo "$REPO" --body "$EMAIL"

echo "OIDC deploy role ready: arn:aws:iam::$ACCOUNT_ID:role/$ROLE"
echo "vpc=$VPC subnet=$SUBNET sg=$SG alert=$EMAIL"
echo "Next: gh workflow run deploy-infra.yml -R $REPO   (or the Actions tab)"

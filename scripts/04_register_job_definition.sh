#!/usr/bin/env bash
# Render job-definition.json placeholders and register it with AWS Batch.
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${BUCKET:?}" ; : "${IMAGE_URI:?}" ; : "${JOB_ROLE_NAME:?}" ; : "${EXECUTION_ROLE_NAME:?}"

JOB_ROLE_ARN="$(aws iam get-role --role-name "$JOB_ROLE_NAME" --query 'Role.Arn' --output text)"
EXECUTION_ROLE_ARN="$(aws iam get-role --role-name "$EXECUTION_ROLE_NAME" --query 'Role.Arn' --output text)"

cd "$(dirname "$0")/.."
sed -e "s|REPLACE_BUCKET|${BUCKET}|g" \
    -e "s|REPLACE_IMAGE_URI|${IMAGE_URI}|g" \
    -e "s|REPLACE_JOB_ROLE_ARN|${JOB_ROLE_ARN}|g" \
    -e "s|REPLACE_EXECUTION_ROLE_ARN|${EXECUTION_ROLE_ARN}|g" \
    job-definition.json > job-definition.rendered.json

aws batch register-job-definition \
  --cli-input-json file://job-definition.rendered.json \
  --region "$AWS_REGION"
echo "Registered job definition (see job-definition.rendered.json)."

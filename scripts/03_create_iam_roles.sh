#!/usr/bin/env bash
# Create the IAM roles AWS Batch needs. Requires IAM admin permissions.
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${BUCKET:?source scripts/env.sh first}"
: "${JOB_ROLE_NAME:?}" ; : "${EXECUTION_ROLE_NAME:?}" ; : "${INSTANCE_ROLE_NAME:?}"

IAM_DIR="$(dirname "$0")/../iam"

create_role() {  # name trust-file
  aws iam get-role --role-name "$1" >/dev/null 2>&1 || \
    aws iam create-role --role-name "$1" --assume-role-policy-document "file://$2"
}

# --- ECS instance role (EC2 trust) + instance profile -------------------------
aws iam get-role --role-name "$INSTANCE_ROLE_NAME" >/dev/null 2>&1 || \
  aws iam create-role --role-name "$INSTANCE_ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name "$INSTANCE_ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role
aws iam get-instance-profile --instance-profile-name "$INSTANCE_ROLE_NAME" >/dev/null 2>&1 || \
  aws iam create-instance-profile --instance-profile-name "$INSTANCE_ROLE_NAME"
aws iam add-role-to-instance-profile --instance-profile-name "$INSTANCE_ROLE_NAME" \
  --role-name "$INSTANCE_ROLE_NAME" 2>/dev/null || true

# --- Job role (ecs-tasks trust) with scoped S3 access -------------------------
create_role "$JOB_ROLE_NAME" "$IAM_DIR/job-role-trust.json"
sed "s/REPLACE_BUCKET/${BUCKET}/g" "$IAM_DIR/job-role-s3-policy.json" > /tmp/job-role-s3-policy.rendered.json
aws iam put-role-policy --role-name "$JOB_ROLE_NAME" \
  --policy-name esmfold2-s3-access --policy-document file:///tmp/job-role-s3-policy.rendered.json

# --- Execution role (ecs-tasks trust) for ECR pull + CloudWatch logs ----------
create_role "$EXECUTION_ROLE_NAME" "$IAM_DIR/execution-role-trust.json"
aws iam attach-role-policy --role-name "$EXECUTION_ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

echo "Done. The AWSServiceRoleForBatch service-linked role is created automatically"
echo "when you create the first compute environment."

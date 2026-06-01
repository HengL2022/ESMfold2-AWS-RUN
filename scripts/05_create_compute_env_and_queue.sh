#!/usr/bin/env bash
# Create the GPU compute environment and job queue using the default VPC (fine for a first test).
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${COMPUTE_ENV:?}" ; : "${JOB_QUEUE:?}" ; : "${INSTANCE_ROLE_NAME:?}"
: "${INSTANCE_TYPE:?}" ; : "${MAX_VCPUS:?}"

INSTANCE_PROFILE_ARN="$(aws iam get-instance-profile --instance-profile-name "$INSTANCE_ROLE_NAME" \
  --query 'InstanceProfile.Arn' --output text)"

VPC_ID="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text --region "$AWS_REGION")"
SUBNETS="$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC_ID \
  --query 'Subnets[].SubnetId' --output text --region "$AWS_REGION" | tr '\t' ',')"
SG_ID="$(aws ec2 describe-security-groups --filters Name=vpc-id,Values=$VPC_ID Name=group-name,Values=default \
  --query 'SecurityGroups[0].GroupId' --output text --region "$AWS_REGION")"

echo "VPC=$VPC_ID  Subnets=$SUBNETS  SG=$SG_ID"

aws batch create-compute-environment \
  --compute-environment-name "$COMPUTE_ENV" \
  --type MANAGED --state ENABLED \
  --compute-resources "type=EC2,allocationStrategy=BEST_FIT_PROGRESSIVE,minvCpus=0,maxvCpus=${MAX_VCPUS},desiredvCpus=0,instanceTypes=${INSTANCE_TYPE},subnets=${SUBNETS},securityGroupIds=${SG_ID},instanceRole=${INSTANCE_PROFILE_ARN}" \
  --region "$AWS_REGION" || echo "(compute environment may already exist)"

echo "Waiting for compute environment to become VALID..."
until [ "$(aws batch describe-compute-environments --compute-environments "$COMPUTE_ENV" \
  --query 'computeEnvironments[0].status' --output text --region "$AWS_REGION")" = "VALID" ]; do
  sleep 5
done

aws batch create-job-queue \
  --job-queue-name "$JOB_QUEUE" --state ENABLED --priority 100 \
  --compute-environment-order "order=1,computeEnvironment=${COMPUTE_ENV}" \
  --region "$AWS_REGION" || echo "(job queue may already exist)"
echo "Compute environment and job queue ready."

#!/usr/bin/env bash
# Copy to scripts/env.sh and adjust, then `source scripts/env.sh` before running the other scripts.
# scripts/env.sh is gitignored because it ends up holding account-specific values.

# --- Region & project ---------------------------------------------------------
export AWS_REGION=ap-southeast-1
export PROJECT=esmfold2-batch

# Resolved from your *admin/PowerUser* credentials (NOT the Bedrock-only key).
export AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

# --- Names --------------------------------------------------------------------
export BUCKET="${PROJECT}-${AWS_ACCOUNT_ID}-${AWS_REGION}"
export ECR_REPO="${PROJECT}"
export IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:latest"

export COMPUTE_ENV=esmfold2-gpu-ce
export JOB_QUEUE=esmfold2-gpu-queue
export JOB_DEF=esmfold2-gpu-jobdef

export JOB_ROLE_NAME=esmfold2BatchJobRole
export EXECUTION_ROLE_NAME=esmfold2BatchExecutionRole
export INSTANCE_ROLE_NAME=ecsInstanceRole

# --- Model / fold defaults ----------------------------------------------------
export MODEL_ID=biohub/ESMFold2-Fast
export INSTANCE_TYPE=g5.2xlarge
export MAX_VCPUS=8   # ~1 concurrent g5.2xlarge job; raise (e.g. 32 = 4 jobs) only after a quota check.

echo "Loaded env for project=$PROJECT region=$AWS_REGION account=$AWS_ACCOUNT_ID"
echo "Bucket=$BUCKET"
echo "Image=$IMAGE_URI"

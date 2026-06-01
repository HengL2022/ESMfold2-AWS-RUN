#!/usr/bin/env bash
# Build the GPU image (linux/amd64) and push to ECR.
# Requires Docker with buildx. On Apple Silicon the --platform flag is mandatory.
# If you have no local Docker, see README "Option B: build in the cloud with CodeBuild".
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${AWS_ACCOUNT_ID:?source scripts/env.sh first}"
: "${ECR_REPO:?source scripts/env.sh first}"
: "${IMAGE_URI:?source scripts/env.sh first}"

aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "$ECR_REPO" --region "$AWS_REGION" \
    --image-scanning-configuration scanOnPush=true

# Authenticate Docker to the private project repo AND to the public DLC base-image registry.
aws ecr get-login-password --region "$AWS_REGION" | \
  docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
aws ecr-public get-login-password --region us-east-1 2>/dev/null | \
  docker login --username AWS --password-stdin public.ecr.aws || true

cd "$(dirname "$0")/.."
docker buildx build --platform linux/amd64 -t "$IMAGE_URI" --push .
echo "Pushed $IMAGE_URI"

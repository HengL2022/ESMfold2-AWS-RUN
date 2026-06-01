#!/usr/bin/env bash
# Build the image in the cloud with AWS CodeBuild — use this if you have NO local Docker.
# CodeBuild runs a linux/amd64 builder, so no cross-platform concerns.
# Requires: a CodeBuild service role with ECR push + CloudWatch Logs permissions.
# This is a convenience wrapper; for a first run you may find it simpler to install Docker Desktop
# and use 02_build_and_push.sh instead.
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${AWS_ACCOUNT_ID:?}" ; : "${ECR_REPO:?}" ; : "${PROJECT:?}"

cat <<EOF
This script is a placeholder/runbook, not a turnkey build, because CodeBuild needs:
  1. An ECR repo (create with: aws ecr create-repository --repository-name $ECR_REPO)
  2. A source location for the Dockerfile + run_esmfold2_batch.py (S3 zip or CodeCommit/GitHub)
  3. A CodeBuild project with environment privilegedMode=true (for Docker), image
     aws/codebuild/amazonlinux2-x86_64-standard:5.0, and a service role that can push to ECR.

Recommended buildspec.yml (commit alongside the source):

  version: 0.2
  phases:
    pre_build:
      commands:
        - aws ecr get-login-password --region \$AWS_REGION | docker login --username AWS --password-stdin \$AWS_ACCOUNT_ID.dkr.ecr.\$AWS_REGION.amazonaws.com
        - aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws
    build:
      commands:
        - docker build -t \$IMAGE_URI .
    post_build:
      commands:
        - docker push \$IMAGE_URI

Then: zip the project, upload to S3, create the CodeBuild project pointing at it, and run
  aws codebuild start-build --project-name $PROJECT-build --region $AWS_REGION
EOF

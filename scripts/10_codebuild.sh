#!/usr/bin/env bash
# Set up AWS CodeBuild to build the GPU image on native amd64 and push to ECR, then start a build.
# Idempotent: safe to re-run. Requires the esmfold2 admin profile.
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${AWS_ACCOUNT_ID:?}" ; : "${BUCKET:?}" ; : "${ECR_REPO:?}" ; : "${IMAGE_URI:?}"

CB_PROJECT="${PROJECT}-build"
CB_ROLE="esmfold2CodeBuildRole"
IAM_DIR="$(dirname "$0")/../iam"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "== 1. Ensure the deployer user can drive CodeBuild =="
DEPLOYER="$(aws sts get-caller-identity --query Arn --output text | sed 's#.*user/##')"
aws iam attach-user-policy --user-name "$DEPLOYER" \
  --policy-arn arn:aws:iam::aws:policy/AWSCodeBuildAdminAccess 2>/dev/null \
  && echo "Attached AWSCodeBuildAdminAccess to $DEPLOYER" || echo "(already attached or not permitted)"

echo "== 2. Ensure ECR repo exists =="
aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "$ECR_REPO" --region "$AWS_REGION" \
    --image-scanning-configuration scanOnPush=true >/dev/null
echo "ECR repo: $ECR_REPO"

echo "== 3. CodeBuild service role =="
aws iam get-role --role-name "$CB_ROLE" >/dev/null 2>&1 || \
  aws iam create-role --role-name "$CB_ROLE" \
    --assume-role-policy-document "file://$IAM_DIR/codebuild-trust.json" >/dev/null
sed -e "s/REPLACE_REGION/${AWS_REGION}/g" \
    -e "s/REPLACE_ACCOUNT/${AWS_ACCOUNT_ID}/g" \
    -e "s/REPLACE_BUCKET/${BUCKET}/g" \
    "$IAM_DIR/codebuild-policy.json" > /tmp/codebuild-policy.rendered.json
aws iam put-role-policy --role-name "$CB_ROLE" \
  --policy-name esmfold2-codebuild --policy-document file:///tmp/codebuild-policy.rendered.json
CB_ROLE_ARN="$(aws iam get-role --role-name "$CB_ROLE" --query 'Role.Arn' --output text)"
echo "Role: $CB_ROLE_ARN"

echo "== 4. Package source -> S3 =="
TMP_ZIP="$(mktemp -t cbsrc).zip"
( cd "$ROOT" && zip -q -r "$TMP_ZIP" Dockerfile run_esmfold2_batch.py buildspec.yml design/ )
aws s3 cp "$TMP_ZIP" "s3://${BUCKET}/codebuild/source.zip" >/dev/null
rm -f "$TMP_ZIP"
echo "Source: s3://${BUCKET}/codebuild/source.zip"

echo "== 5. Create/update CodeBuild project =="
ENV_VARS="environmentVariables=[{name=AWS_ACCOUNT_ID,value=${AWS_ACCOUNT_ID}},{name=IMAGE_URI,value=${IMAGE_URI}}]"
ENVIRONMENT="type=LINUX_CONTAINER,image=aws/codebuild/standard:7.0,computeType=BUILD_GENERAL1_LARGE,privilegedMode=true,${ENV_VARS}"
if aws codebuild batch-get-projects --names "$CB_PROJECT" --region "$AWS_REGION" \
     --query 'projects[0].name' --output text 2>/dev/null | grep -q "$CB_PROJECT"; then
  # Role may need a moment to propagate on first create; updates are fine immediately.
  aws codebuild update-project --name "$CB_PROJECT" --region "$AWS_REGION" \
    --source "type=S3,location=${BUCKET}/codebuild/source.zip" \
    --artifacts "type=NO_ARTIFACTS" \
    --environment "$ENVIRONMENT" \
    --service-role "$CB_ROLE_ARN" >/dev/null
  echo "Updated project $CB_PROJECT"
else
  for attempt in 1 2 3 4 5; do
    if aws codebuild create-project --name "$CB_PROJECT" --region "$AWS_REGION" \
        --source "type=S3,location=${BUCKET}/codebuild/source.zip" \
        --artifacts "type=NO_ARTIFACTS" \
        --environment "$ENVIRONMENT" \
        --service-role "$CB_ROLE_ARN" >/dev/null 2>/tmp/cb_create.err; then
      echo "Created project $CB_PROJECT"; break
    fi
    echo "  role not ready yet (attempt $attempt), waiting..."; sleep 6
    [ "$attempt" = 5 ] && { cat /tmp/cb_create.err >&2; exit 1; }
  done
fi

echo "== 6. Start build =="
BUILD_ID="$(aws codebuild start-build --project-name "$CB_PROJECT" --region "$AWS_REGION" \
  --query 'build.id' --output text)"
echo "BUILD_ID=$BUILD_ID"

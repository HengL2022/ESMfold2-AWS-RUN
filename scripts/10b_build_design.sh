#!/usr/bin/env bash
# Build the binder-DESIGN image (Dockerfile.design, esm@pin + ANARCI/HMMER) and push to ECR :design.
# Reuses the CodeBuild project created by scripts/10_codebuild.sh (run that once first), overriding the
# source package, the IMAGE_URI tag, and the DOCKERFILE — so the working fold image (:latest) is untouched.
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${AWS_ACCOUNT_ID:?}" ; : "${BUCKET:?}" ; : "${DESIGN_IMAGE_URI:?}"

CB_PROJECT="${PROJECT}-build"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "== Package design source -> S3 =="
TMP_ZIP="$(mktemp -t cbsrc-design).zip"
( cd "$ROOT" && zip -q -r "$TMP_ZIP" \
    Dockerfile.design buildspec.yml \
    run_esmfold2_batch.py run_esmfold2_design.py run_esmfold2_rank.py design/ )
aws s3 cp "$TMP_ZIP" "s3://${BUCKET}/codebuild/source-design.zip" >/dev/null
rm -f "$TMP_ZIP"
echo "Source: s3://${BUCKET}/codebuild/source-design.zip"

echo "== Start design build (override source + IMAGE_URI=:design + DOCKERFILE=Dockerfile.design) =="
BUILD_ID="$(aws codebuild start-build --project-name "$CB_PROJECT" --region "$AWS_REGION" \
  --source-location-override "${BUCKET}/codebuild/source-design.zip" \
  --environment-variables-override \
      "name=AWS_ACCOUNT_ID,value=${AWS_ACCOUNT_ID}" \
      "name=IMAGE_URI,value=${DESIGN_IMAGE_URI}" \
      "name=DOCKERFILE,value=Dockerfile.design" \
  --query 'build.id' --output text)"
echo "BUILD_ID=$BUILD_ID"

#!/usr/bin/env bash
# Create a private S3 bucket and upload the demo FASTA.
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${BUCKET:?source scripts/env.sh first}"

if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "Bucket $BUCKET already exists."
else
  if [ "$AWS_REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION"
  fi
fi

aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3 cp "$(dirname "$0")/../demo.fasta" "s3://${BUCKET}/inputs/demo.fasta"
echo "Uploaded demo FASTA to s3://${BUCKET}/inputs/demo.fasta"

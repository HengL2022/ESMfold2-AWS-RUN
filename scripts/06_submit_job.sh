#!/usr/bin/env bash
# Submit a single ESMFold2 job. Override input/output by passing args:
#   ./scripts/06_submit_job.sh [input_s3] [output_s3]
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${BUCKET:?}" ; : "${JOB_QUEUE:?}" ; : "${JOB_DEF:?}" ; : "${MODEL_ID:?}"

INPUT_S3="${1:-s3://${BUCKET}/inputs/demo.fasta}"
OUTPUT_S3="${2:-s3://${BUCKET}/results/demo/}"

JOB_ID="$(aws batch submit-job \
  --job-name "esmfold2-demo-$(date +%Y%m%d-%H%M%S)" \
  --job-queue "$JOB_QUEUE" \
  --job-definition "$JOB_DEF" \
  --parameters "input_s3=${INPUT_S3},output_s3=${OUTPUT_S3},model=${MODEL_ID},num_loops=3,num_sampling_steps=50" \
  --region "$AWS_REGION" --query jobId --output text)"
echo "Submitted job: $JOB_ID"
echo "$JOB_ID"

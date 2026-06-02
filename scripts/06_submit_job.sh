#!/usr/bin/env bash
# Submit a single ESMFold2 job. Override input/output by passing args:
#   ./scripts/06_submit_job.sh [input_s3] [output_s3]
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${BUCKET:?}" ; : "${JOB_QUEUE:?}" ; : "${JOB_DEF:?}" ; : "${MODEL_ID:?}"

INPUT_S3="${1:-s3://${BUCKET}/inputs/demo.fasta}"
OUTPUT_S3="${2:-s3://${BUCKET}/results/demo/}"

# Fold params are env-overridable; defaults preserve the original 3/50/1 behaviour.
NUM_LOOPS="${NUM_LOOPS:-3}"
NUM_SAMPLING_STEPS="${NUM_SAMPLING_STEPS:-50}"
NUM_DIFFUSION_SAMPLES="${NUM_DIFFUSION_SAMPLES:-1}"

JOB_ID="$(aws batch submit-job \
  --job-name "esmfold2-demo-$(date +%Y%m%d-%H%M%S)" \
  --job-queue "$JOB_QUEUE" \
  --job-definition "$JOB_DEF" \
  --parameters "input_s3=${INPUT_S3},output_s3=${OUTPUT_S3},model=${MODEL_ID},num_loops=${NUM_LOOPS},num_sampling_steps=${NUM_SAMPLING_STEPS},num_diffusion_samples=${NUM_DIFFUSION_SAMPLES}" \
  --region "$AWS_REGION" --query jobId --output text)"
echo "Submitted job: $JOB_ID"
echo "$JOB_ID"

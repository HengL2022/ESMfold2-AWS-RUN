#!/usr/bin/env bash
# Submit a binder-design array: one Batch child per seed (seed = seed_base + array index), so
# `size` seeds = `size` candidate binders. Uploads your campaign config, then submits.
# Usage:
#   ./scripts/11_submit_design_array.sh <config.json> <campaign_id> [size=12]
# Keep <config.json> out of version control (it holds your target/framework) — see .gitignore.
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${BUCKET:?}" ; : "${JOB_QUEUE:?}" ; : "${DESIGN_JOB_DEF:?}"

CONFIG="${1:?usage: 11_submit_design_array.sh <config.json> <campaign_id> [size]}"
CAMPAIGN="${2:?usage: 11_submit_design_array.sh <config.json> <campaign_id> [size]}"
SIZE="${3:-12}"
[ "$SIZE" -ge 2 ] || { echo "Array size must be >= 2." >&2; exit 1; }

CONFIG_S3="s3://${BUCKET}/design/${CAMPAIGN}/config.json"
OUTPUT_S3="s3://${BUCKET}/design/${CAMPAIGN}/stage1/"
aws s3 cp "$CONFIG" "$CONFIG_S3" >/dev/null
echo "Config -> $CONFIG_S3 ; array size $SIZE ; output $OUTPUT_S3"

aws batch submit-job \
  --job-name "design-${CAMPAIGN}-$(date +%Y%m%d-%H%M%S)" \
  --job-queue "$JOB_QUEUE" --job-definition "$DESIGN_JOB_DEF" \
  --array-properties "size=${SIZE}" \
  --parameters "config_s3=${CONFIG_S3},output_s3=${OUTPUT_S3}" \
  --region "$AWS_REGION" --query jobId --output text

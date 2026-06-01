#!/usr/bin/env bash
# Submit an array job: one child per input record. Each child folds the record at its
# AWS_BATCH_JOB_ARRAY_INDEX. Supports BOTH input formats:
#   - FASTA (.fasta/.fa): one monomer per record
#   - complexes manifest (.json): one multi-chain complex per record
# Usage:
#   ./scripts/07_submit_array_job.sh <local_or_s3_input> <run_name>
# A local file is uploaded to s3://$BUCKET/inputs/<run_name>.<ext> first.
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${BUCKET:?}" ; : "${JOB_QUEUE:?}" ; : "${JOB_DEF:?}" ; : "${MODEL_ID:?}"

INPUT="${1:?usage: 07_submit_array_job.sh <input> <run_name>}"
RUN_NAME="${2:?usage: 07_submit_array_job.sh <input> <run_name>}"

case "$INPUT" in
  *.json) EXT=json ;;
  *)      EXT=fasta ;;
esac

count_records() {  # $1 = local path
  if [ "$EXT" = json ]; then
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(len(d) if isinstance(d,list) else 1)' "$1"
  else
    grep -c '^>' "$1"
  fi
}

if [[ "$INPUT" == s3://* ]]; then
  INPUT_S3="$INPUT"
  TMP="$(mktemp)"; aws s3 cp "$INPUT_S3" "$TMP" >/dev/null; COUNT="$(count_records "$TMP")"; rm -f "$TMP"
else
  INPUT_S3="s3://${BUCKET}/inputs/${RUN_NAME}.${EXT}"
  aws s3 cp "$INPUT" "$INPUT_S3" >/dev/null
  COUNT="$(count_records "$INPUT")"
fi
OUTPUT_S3="s3://${BUCKET}/results/${RUN_NAME}/"
echo "Records: $COUNT ($EXT) -> array size $COUNT, output $OUTPUT_S3"

if [ "$COUNT" -lt 2 ]; then
  echo "Array jobs require size >= 2. Use 06_submit_job.sh for a single record." >&2; exit 1
fi

aws batch submit-job \
  --job-name "esmfold2-${RUN_NAME}-$(date +%Y%m%d-%H%M%S)" \
  --job-queue "$JOB_QUEUE" \
  --job-definition "$JOB_DEF" \
  --array-properties "size=${COUNT}" \
  --parameters "input_s3=${INPUT_S3},output_s3=${OUTPUT_S3},model=${MODEL_ID},num_loops=3,num_sampling_steps=50" \
  --region "$AWS_REGION" --query jobId --output text

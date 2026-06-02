#!/usr/bin/env bash
# Rank a finished design campaign by the critic-ensemble scores -> stage2/leaderboard.csv + top_k.fasta.
# Usage:
#   ./scripts/12_submit_rank.sh <campaign_id>
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${BUCKET:?}" ; : "${JOB_QUEUE:?}" ; : "${DESIGN_JOB_DEF:?}"

CAMPAIGN="${1:?usage: 12_submit_rank.sh <campaign_id>}"
STAGE1="s3://${BUCKET}/design/${CAMPAIGN}/stage1/"
STAGE2="s3://${BUCKET}/design/${CAMPAIGN}/stage2/"

aws batch submit-job \
  --job-name "rank-${CAMPAIGN}-$(date +%Y%m%d-%H%M%S)" \
  --job-queue "$JOB_QUEUE" --job-definition "$DESIGN_JOB_DEF" \
  --container-overrides "command=[\"/app/run_esmfold2_rank.py\",\"--stage1-s3\",\"${STAGE1}\",\"--output-s3\",\"${STAGE2}\"]" \
  --region "$AWS_REGION" --query jobId --output text
echo "Ranking ${CAMPAIGN}: ${STAGE1} -> ${STAGE2}"

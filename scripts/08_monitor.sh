#!/usr/bin/env bash
# Show job status and tail logs. Usage: ./scripts/08_monitor.sh <job_id>
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
JOB_ID="${1:?usage: 08_monitor.sh <job_id>}"

aws batch describe-jobs --jobs "$JOB_ID" --region "$AWS_REGION" \
  --query 'jobs[0].{status:status,statusReason:statusReason,startedAt:startedAt,stoppedAt:stoppedAt}'

echo "Tailing /aws/batch/job (Ctrl-C to stop)..."
aws logs tail /aws/batch/job --follow --region "$AWS_REGION"

---
name: preflight
description: Check AWS readiness for the ESMFold2 Batch project before submitting jobs — identity & permissions, GPU vCPU quota, IAM roles, ECR image, S3 bucket. Read-only; reports a go/no-go checklist.
disable-model-invocation: true
---

# Preflight — ESMFold2 AWS Batch readiness

Run read-only checks and report a clear go/no-go checklist. Do NOT create or modify any AWS
resources. Assume the user has `source scripts/env.sh` (use `$AWS_REGION`, `$BUCKET`, `$ECR_REPO`,
`$JOB_ROLE_NAME`, `$EXECUTION_ROLE_NAME`, `$INSTANCE_ROLE_NAME`, `$JOB_QUEUE`, `$COMPUTE_ENV`,
`$INSTANCE_TYPE`); if unset, tell the user to source it first.

Check each and mark ✅ / ❌ / ⚠️:

1. **Identity** — `aws sts get-caller-identity`. Report the ARN. ❌ if it's the Bedrock-only key
   `BedrockAPIKey-p8bu` (it cannot provision Batch/ECR/IAM/S3 — the user needs an admin profile).

2. **Permissions probe** — try `aws batch describe-job-queues`, `aws ecr describe-repositories`,
   `aws iam get-role --role-name "$INSTANCE_ROLE_NAME"`. AccessDenied on any → ❌ insufficient perms.

3. **GPU quota** — `aws service-quotas get-service-quota --service-code ec2 --quota-code L-DB2E81BA
   --region "$AWS_REGION"` (Running On-Demand G and VT instances). ⚠️ if the value is 0 or less than
   the vCPUs of one `$INSTANCE_TYPE` — jobs will sit in RUNNABLE. Tell them to request an increase.

4. **IAM roles** — `aws iam get-role` for the three roles. ❌ list any missing (run
   `scripts/03_create_iam_roles.sh`).

5. **ECR image** — `aws ecr describe-images --repository-name "$ECR_REPO" --region "$AWS_REGION"`.
   ❌ if repo/image absent (run `scripts/02_build_and_push.sh`).

6. **S3 bucket + input** — `aws s3api head-bucket --bucket "$BUCKET"` and
   `aws s3 ls "s3://$BUCKET/inputs/"`. ❌ if missing (run `scripts/01_create_bucket.sh`).

7. **Compute env / queue** — `aws batch describe-job-queues --job-queues "$JOB_QUEUE"` and
   `describe-compute-environments`. Report status; ⚠️ if not VALID/ENABLED.

End with a one-line verdict: **READY to submit** only if 1–7 all pass, otherwise list the exact
blocking steps to run, in order.

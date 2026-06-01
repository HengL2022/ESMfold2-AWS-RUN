#!/usr/bin/env bash
# Create/refresh an EC2 launch template that gives Batch GPU instances a larger root volume,
# then attach it to the compute environment. The default ~30GB root is too small for the 8GB
# image + ESMFold2/ESMC weights + HF download temp (causes "No space left on device").
set -euo pipefail
: "${AWS_REGION:?source scripts/env.sh first}"
: "${COMPUTE_ENV:?}"
LT_NAME="${LT_NAME:-esmfold2-gpu-lt}"
ROOT_GB="${ROOT_GB:-100}"

# /dev/xvda is the root device on the ECS-optimized Amazon Linux GPU AMI that Batch uses by default.
LT_DATA=$(cat <<JSON
{
  "BlockDeviceMappings": [
    {
      "DeviceName": "/dev/xvda",
      "Ebs": { "VolumeSize": ${ROOT_GB}, "VolumeType": "gp3", "DeleteOnTermination": true }
    }
  ]
}
JSON
)

if aws ec2 describe-launch-templates --launch-template-names "$LT_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  aws ec2 create-launch-template-version --launch-template-name "$LT_NAME" --region "$AWS_REGION" \
    --launch-template-data "$LT_DATA" --query 'LaunchTemplateVersion.VersionNumber' --output text
  echo "Added new version to launch template $LT_NAME (${ROOT_GB}GB root)."
else
  aws ec2 create-launch-template --launch-template-name "$LT_NAME" --region "$AWS_REGION" \
    --launch-template-data "$LT_DATA" >/dev/null
  echo "Created launch template $LT_NAME (${ROOT_GB}GB root)."
fi

echo "Attaching launch template to compute environment $COMPUTE_ENV ..."
aws batch update-compute-environment --compute-environment "$COMPUTE_ENV" --region "$AWS_REGION" \
  --compute-resources "launchTemplate={launchTemplateName=${LT_NAME},version=\$Latest}" >/dev/null

echo "Waiting for compute environment to return to VALID..."
until [ "$(aws batch describe-compute-environments --compute-environments "$COMPUTE_ENV" \
  --query 'computeEnvironments[0].status' --output text --region "$AWS_REGION")" = "VALID" ]; do
  sleep 5
done
echo "Done. New instances will boot with a ${ROOT_GB}GB root volume."

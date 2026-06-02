# ESMFold2 on AWS Batch (GPU) container image.
#
# Base: AWS PyTorch GPU Deep Learning Container (PyTorch 2.6.0, CUDA 12.4, Python 3.12, Ubuntu 22.04).
# NOTE: the "-v1.72" patch suffix advances over time and PyTorch 2.6 inference images are only
# patched through ~end of June 2026. Look up the current tag before a fresh build, e.g.:
#   curl -s https://api.us-east-1.gallery.ecr.aws/describeImageTags \
#     -H 'Content-Type: application/json' \
#     -d '{"registryAliasName":"deep-learning-containers","repositoryName":"pytorch-inference","maxResults":1000}' \
#     | python3 -c 'import sys,json;[print(t["imageTag"]) for t in json.load(sys.stdin)["imageTagDetails"]]' \
#     | grep "2.6.0-gpu-py312-cu124-ubuntu22.04-ec2" | sort -V | tail -1
ARG BASE_IMAGE=public.ecr.aws/deep-learning-containers/pytorch-inference:2.6.0-gpu-py312-cu124-ubuntu22.04-ec2-v1.72
FROM ${BASE_IMAGE}

# Pinned for reproducibility. This is the EvolutionaryScale ESM repo, which was renamed
# from github.com/evolutionaryscale/esm to github.com/Biohub/esm (the old URL 301-redirects).
# This revision matches the one used by Modal's official ESMFold2 example.
# Installing `esm` also pulls in EvolutionaryScale's custom `transformers` fork, which is what
# provides `transformers.models.esmfold2.modeling_esmfold2.ESMFold2Model` (NOT stock PyPI transformers).
ARG ESM_REVISION=81b3646c9429ea8458918415ad6a46178cb59833

RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir \
        "esm @ git+https://github.com/Biohub/esm.git@${ESM_REVISION}" \
        boto3

# HF cache lives on the (ephemeral) container disk under /tmp. For repeated runs you will want
# to mount a persistent cache (EFS) here instead so weights aren't re-downloaded every job.
ENV HF_HOME=/tmp/hf_cache
ENV HF_XET_HIGH_PERFORMANCE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY run_esmfold2_batch.py /app/run_esmfold2_batch.py
COPY design/ /app/design/

ENTRYPOINT ["python", "/app/run_esmfold2_batch.py"]

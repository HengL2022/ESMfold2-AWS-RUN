# ESMFold2 on AWS Batch (GPU)

Run **ESMFold2 / ESMFold2-Fast** protein structure prediction on GPU via AWS Batch:

```
FASTA in S3  →  AWS Batch GPU job  →  container loads ESMFold2  →  prediction on GPU
            →  .cif structure + metrics JSON written back to S3
```

First success target: **one FASTA in S3 → one `.cif` + one `.metrics.json` in S3.** Start with
full single-chain VHH/nanobody sequences; extend to nanobody–antigen complexes later (see end).

This scaffold was built from a setup guide that was **cross-checked against live sources first.**
Read [VERIFICATION.md](VERIFICATION.md) for what was confirmed and what was corrected.

---

## ⚠️ Two blockers before any of this can run

This machine cannot provision AWS infrastructure as-is. Resolve these first:

### 1. AWS credentials are Bedrock-only
The active CLI identity may be a Bedrock-scoped key (the default profile in this project's case). It is **denied**
IAM, Batch, ECR, and (almost certainly) S3 management actions — verified by probing:
`batch:DescribeComputeEnvironments`, `ecr:DescribeRepositories`, and `iam:List*` all return AccessDenied.

➡️ You need an **admin / PowerUser** IAM identity (or have an admin attach Batch+ECR+IAM+S3 policies
to a user/role you control). Configure it as a separate profile and `export AWS_PROFILE=...` before
sourcing `scripts/env.sh`.

### 2. No local Docker
`docker` is not installed, and this is an Apple Silicon (arm64) Mac while Batch GPU images must be
`linux/amd64`. Pick one:
- **Option A (simplest):** install Docker Desktop, then `scripts/02_build_and_push.sh` (it already
  passes `--platform linux/amd64`).
- **Option B (no local Docker):** build in the cloud with AWS CodeBuild — see
  `scripts/02b_build_with_codebuild.sh` (runbook).

---

## Recommended first-run defaults

| Setting | Value |
|---|---|
| Region | `ap-southeast-1` |
| Model | `biohub/ESMFold2-Fast` (inference-optimized, single-sequence, no MSA) |
| Instance | `g4dn.2xlarge` (1× T4, 16 GiB GPU, 8 vCPU, 32 GiB RAM) — `g5` is not offered by Batch in ap-southeast-1 |
| GPU / vCPU / Mem per job | 1 / 8 / 30000 MiB |
| Max vCPUs (compute env) | 8 (≈1 concurrent job) |

> **Quota check:** new accounts often have 0 GPU vCPU quota. Check Service Quotas for "Running
> On-Demand G and VT instances" in your region and request an increase before submitting.

---

## Files

```
Dockerfile                          GPU image: AWS PyTorch DLC + EvolutionaryScale esm pkg (pinned)
run_esmfold2_batch.py               folds FASTA from S3 → .cif + metrics to S3 (array-job aware)
job-definition.json                 Batch job def template (uses bare Ref::param substitution)
demo.fasta                          ubiquitin test sequence
iam/                                trust policies + scoped S3 policy for the job role
scripts/                            numbered runbook (source env.sh first)
  env.example.sh                    copy → env.sh, edit, then `source scripts/env.sh`
  01_create_bucket.sh               private S3 bucket + upload demo.fasta
  02_build_and_push.sh              docker buildx → ECR  (needs local Docker)
  10_codebuild.sh                   cloud build via CodeBuild (no local Docker) — USED for first run
  03_create_iam_roles.sh            instance role, job role (S3), execution role
  04_register_job_definition.sh     render placeholders + register job def
  05_create_compute_env_and_queue.sh  GPU compute env + job queue (default VPC)
  09_launch_template.sh             100GB root launch template + attach to compute env (disk fix)
  06_submit_job.sh                  submit one job
  07_submit_array_job.sh            submit array job (one child per FASTA record)
  08_monitor.sh                     status + CloudWatch logs
VERIFICATION.md                     what was checked against live sources, with corrections
```

---

## Runbook

```bash
# 0. Use an admin/PowerUser AWS identity (NOT the Bedrock key)
export AWS_PROFILE=your-admin-profile
cp scripts/env.example.sh scripts/env.sh   # edit region/names if desired
source scripts/env.sh

# 1. Bucket + demo input
./scripts/01_create_bucket.sh

# 2. Build & push the image — local Docker:  ./scripts/02_build_and_push.sh
#    OR cloud (no local Docker, used for first run):
./scripts/10_codebuild.sh        # needs AWSCodeBuildAdminAccess on the deployer (script attaches it)

# 3. IAM roles
./scripts/03_create_iam_roles.sh

# 4. Register the job definition
./scripts/04_register_job_definition.sh

# 5. Compute environment + queue
./scripts/05_create_compute_env_and_queue.sh

# 5b. Bigger root volume so weights fit (needs ec2:*LaunchTemplate* on the deployer — grant once)
./scripts/09_launch_template.sh

# 6. Submit the demo job and watch it
JOB_ID=$(./scripts/06_submit_job.sh | tail -1)
./scripts/08_monitor.sh "$JOB_ID"

# 7. Collect results
aws s3 ls "s3://${BUCKET}/results/demo/" --recursive
aws s3 cp "s3://${BUCKET}/results/demo/ubiquitin_demo.metrics.json" - | cat
```

### Many sequences (array job)
One FASTA, one record per candidate; each child folds its `AWS_BATCH_JOB_ARRAY_INDEX`:
```bash
./scripts/07_submit_array_job.sh vhh_candidates.fasta vhh_run_001
```
Concurrency ≈ `compute-env max vCPUs / job vCPUs`. With max=8, job=8 → ~1 at a time. For 4 concurrent
`g5.2xlarge`, set `MAX_VCPUS=32` in `env.sh` (only after confirming quota + budget).

---

## Fold parameters (passed to `ESMFold2InputBuilder.fold`)

| Use case | num_loops | num_sampling_steps | num_diffusion_samples |
|---|---|---|---|
| Fast screening | 1 | 20 | 1 |
| Default first run | 3 | 50 | 1 |
| Careful ranking | 3–5 | 50–100 | 2–8 |

Outputs per record: `<id>.cif`, `<id>.metrics.json` (pLDDT mean, pTM, iPTM), plus a run `summary-*.json`.

---

## Cost / performance notes
- First job downloads model weights inside the container (several minutes). For repeated runs, mount
  **EFS as the HF cache** (`HF_HOME`) or bake weights into the image/AMI to avoid re-downloading.
- Keep `minvCpus=0` so the environment scales to zero when idle.
- Use a **dedicated GPU queue** — non-GPU jobs can run on GPU instances but waste them.

## Nanobody–antigen complexes (implemented)
`run_esmfold2_batch.py` folds multi-chain complexes from a JSON manifest (`.json` input) as well as
single chains (FASTA). Each manifest record is `{id, chains:[{chain_id, sequence}, ...]}`; one chain
→ monomer, multiple → complex. The output `*.metrics.json` includes **iPTM** (inter-chain pTM), the
standard interface-confidence proxy for binding. Use the MSA-capable `biohub/ESMFold2` (set
`MODEL_ID=biohub/ESMFold2`) and a ≥24 GB GPU — ~470-residue complexes OOM on 16 GB (L40S 48 GB or
A100/H100 80 GB are comfortable).

`scripts/prepare_complex_input.py` builds the manifest from a FASTA of chains (one CD5-style antigen
record + VHH records), trimming the antigen to a defined range. See
`Test_input/raw_input.example.fasta` for the input format.

## Worked example: CD5 × anti-CD5 VHH complex screen
A real screen run on this pipeline — predicting interface confidence (iPTM, a binding proxy, **not**
an affinity/Kd) for anti-CD5 VHH nanobodies against the human CD5 ectodomain.

**Inputs** (real VHH sequences are internal and withheld from this repo — see `.gitignore`; the
`raw_input.example.fasta` shows the format):
- **Antigen:** human CD5 ectodomain, **Arg25–Asn371 (347 aa)** — signal peptide + TM/cytoplasmic
  tail trimmed from the full 495-aa sequence (endpoints validated by `prepare_complex_input.py`).
- **VHHs:** 1 positive control (`anti-CD5_P_control`, 122 aa) + 4 internal clones (`Clone_1..4`,
  127 aa). Each paired with CD5 as a 2-chain complex (chain A = VHH, B = CD5), ~469–474 aa total.
- **Model / settings:** `biohub/ESMFold2` (full), `num_loops=3, num_sampling_steps=50,
  num_diffusion_samples=1`. Hardware: `g6e.2xlarge` (NVIDIA L40S 48 GB), us-east-1, ~6.6 min/fold,
  all 5 run concurrently as a Batch array.

**Results — ranked by iPTM** (interpret *relative to the control*; absolute iPTM runs low for
antibody–antigen, single-sequence):

| Rank | Complex | iPTM | pTM | pLDDT(mean) | vs control |
|---|---|---|---|---|---|
| 1 | Clone_4 + CD5 | **0.544** | 0.447 | 0.685 | above control |
| 2 | anti-CD5_P (control) | 0.393 | 0.423 | 0.692 | baseline |
| 3 | Clone_1 + CD5 | 0.382 | 0.421 | 0.662 | ≈ control |
| 4 | Clone_3 + CD5 | 0.185 | 0.345 | 0.635 | below |
| 5 | Clone_2 + CD5 | 0.125 | 0.344 | 0.636 | below |

**Read:** Clone_4 is the standout (only clone scoring above the known binder); Clone_1 ≈ control;
Clone_2/3 deprioritized. Single-diffusion-sample iPTM is noisy — confirm rankings with
`num_diffusion_samples=8` + multiple seeds before acting.

**Reproduce:**
```bash
# 1. Provide your input FASTA (one CD5* record + VHH records); see raw_input.example.fasta
cp your_chains.fasta Test_input/raw_input.fasta
python3 scripts/prepare_complex_input.py            # -> Test_input/complexes.json (+ chains.fasta)

# 2. Env + infra (MODEL_ID=biohub/ESMFold2, INSTANCE_TYPE=g6e.2xlarge already set in env.sh)
source scripts/env.sh
./scripts/01_create_bucket.sh && ./scripts/03_create_iam_roles.sh && ./scripts/10_codebuild.sh
./scripts/05_create_compute_env_and_queue.sh && ./scripts/09_launch_template.sh
./scripts/04_register_job_definition.sh

# 3. Submit the complex array (07 sizes the array from the JSON automatically) and collect
./scripts/07_submit_array_job.sh Test_input/complexes.json cd5_screen_001
aws s3 cp "s3://${BUCKET}/results/cd5_screen_001/" results/cd5_screen_001/ --recursive
```

## Safety
ESMFold2 outputs are **computational hypotheses**, not validated structures — review and validate
experimentally. Don't put private sequence identifiers in Batch job names or logs.

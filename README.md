# ESMFold2 on AWS Batch — folding + binder design

Run two GPU workloads on AWS Batch from a single container pipeline:

1. **Fold** — predict structures (`.cif`) + confidence metrics for protein sequences and multi-chain
   complexes (FASTA / JSON in S3 → results in S3).
2. **Design binders** — generate de-novo minibinders or antibodies against a target, using the official
   ESMFold2 + ESMC binder-design method, and rank the candidates by an ensemble of confidence critics
   (campaign config in S3 → ranked `leaderboard.csv` + `top_k.fasta` in S3).

Everything runs as Batch GPU jobs; you submit work by dropping inputs in S3 and calling `aws batch
submit-job`. Inputs, outputs, and configs stay in your S3 bucket and are kept out of version control.

---

## Prerequisites

- An AWS account with an **admin / PowerUser** identity (Batch + ECR + IAM + S3 + CodeBuild). Configure
  it as a CLI profile.
- A **GPU vCPU quota** in your region (Service Quotas → "Running On-Demand G and VT instances"). The
  default is a 48 GB L40S instance (`g6e.2xlarge`).
- No local Docker needed — images build in the cloud via AWS CodeBuild.

```bash
cp scripts/env.example.sh scripts/env.sh      # set AWS_PROFILE, AWS_REGION, instance type, names
source scripts/env.sh
```

---

## One-time setup

```bash
source scripts/env.sh

./scripts/01_create_bucket.sh                  # private S3 bucket
./scripts/03_create_iam_roles.sh               # job / execution / instance roles
./scripts/05_create_compute_env_and_queue.sh   # GPU compute environment + job queue
./scripts/09_launch_template.sh                # 100 GB root volume (fits images + model weights)

# Build the container images (CodeBuild → ECR)
./scripts/10_codebuild.sh                      # folding image
./scripts/10b_build_design.sh                  # binder-design image (esm + ANARCI/HMMER)

# Register the Batch job definitions
./scripts/04_register_job_definition.sh        # folding job def
./scripts/13_register_design_jobdef.sh         # design job def
```

Re-run `10_codebuild.sh` / `10b_build_design.sh` whenever you change the code, and the matching
`register` script if you change a job definition.

---

## Fold proteins / complexes

Input is either a **FASTA** (one record = one monomer) or a **complexes manifest** (`.json`, a list of
`{ "id": ..., "chains": [ { "chain_id": "A", "sequence": ... }, ... ] }`; multiple chains = a complex).

```bash
# one sequence
./scripts/06_submit_job.sh  s3://$BUCKET/inputs/my.fasta  s3://$BUCKET/results/my_run/

# many records as an array (one Batch child per record; sizes the array automatically)
./scripts/07_submit_array_job.sh  my_inputs.fasta   my_run         # FASTA
./scripts/07_submit_array_job.sh  my_complexes.json my_run         # complexes

# watch + collect
./scripts/08_monitor.sh "<jobId>"
aws s3 cp "s3://$BUCKET/results/my_run/" results/my_run/ --recursive
```

Per record you get `<id>.cif` (structure), `<id>.metrics.json` (pLDDT, pTM, and **iPTM** for complexes),
and a run `summary-*.json`.

| Use case | num_loops | num_sampling_steps | num_diffusion_samples |
|---|---|---|---|
| Fast screening | 1 | 20 | 1 |
| Default | 3 | 50 | 1 |
| Careful ranking | 3–5 | 50–100 | 2–8 |

---

## Design binders

A **campaign config** (JSON in S3) defines the target and the binder type. Submit an array (one seed per
child → one candidate per seed), then rank.

**1. Write your campaign config** — see [`design_config.example.json`](design_config.example.json) for
the full format. Keep your real config **out of version control** (put it under `Test_input/` or any
path matched by `.gitignore`); it holds your target and, optionally, your antibody framework.

```json
{
  "campaign_id": "campaign_v1",
  "target_sequence": "<your target protein sequence>",
  "binder_name": "minibinder",
  "is_antibody": false,
  "seed_base": 0,
  "batch_size": 1,
  "use_scaling_critics": false
}
```

- **Target:** set `target_name` (a public built-in) **or** `target_sequence` (your own).
- **Binder:** set `binder_name` — `"minibinder"` (de-novo, `is_antibody: false`) or a public antibody
  framework — **or**, to design with your own framework, set `binder_template` (the framework string
  with `{hcdr1}`/`{hcdr2}`/`{hcdr3}` at the CDR positions) + `binder_cdr_lengths`, and `is_antibody:
  true`. Supplying the framework via the config keeps it in your private S3 config — never in the repo.
- `seed_base` + the array index set each child's seed (reproducible). `batch_size` designs that many
  candidates per job (the 48 GB L40S has headroom to raise it).

**2. Submit the design array** — third arg = how many candidate seeds:

```bash
source scripts/env.sh
./scripts/11_submit_design_array.sh  my_campaign.json  campaign_v1  12
./scripts/08_monitor.sh "<jobId>"
```

**3. Rank the candidates** → `leaderboard.csv` + `top_k.fasta`, then collect:

```bash
./scripts/12_submit_rank.sh  campaign_v1
aws s3 cp "s3://$BUCKET/design/campaign_v1/stage2/" results/campaign_v1/ --recursive
```

**Outputs**
- `stage1/<campaign>_seed<N>.fasta` — each designed binder sequence
- `stage1/<campaign>_seed<N>.metrics.json` — per-critic interface scores (iPTM + distogram / CDR proxies)
- `stage1/<campaign>_seed<N>.pdb` — the predicted target–binder complex
- `stage2/leaderboard.csv` + `stage2/top_k.fasta` — candidates ranked by the critic ensemble

Candidates are ranked by ESMFold2 interface confidence averaged over the critic checkpoints, exactly as
in the released method. Scale a campaign by raising the array `size` (more seeds) and/or `batch_size`.

---

## Configuration & cost

- Set region, instance type, and resource names in `scripts/env.sh` (gitignored). Default instance is
  `g6e.2xlarge` (1× L40S, 48 GB) in `us-east-1`.
- The compute environment scales to zero when idle (`minvCpus=0`) — no GPU billing between jobs.
- Concurrency ≈ compute-env `maxVcpus` ÷ job vCPUs. Raise `MAX_VCPUS` in `env.sh` for more parallel jobs
  (after confirming quota and budget).
- First job on a fresh instance downloads model weights (a few minutes). For very large campaigns,
  consider mounting EFS as the Hugging Face cache (`HF_HOME`).

## Safety

ESMFold2 outputs are **computational hypotheses** ranked by a model's own confidence — they are not
validated binders or structures. Confirm experimentally. Keep target/binder sequences and identifiers in
your gitignored config and S3, not in Batch job names or logs.

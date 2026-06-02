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

On a single H100, careful-ranking folds run **~13 s/complex** once the model is loaded — fold many records
as **one job** (model loads once) rather than an array. See [Measured throughput](#measured-throughput-single-h100--p54xlarge).

---

## Visualize results (HTML report)

Turn a folded screen into a **self-contained, offline HTML report** — a sortable leaderboard plus a
drill-down page per top candidate with the full sequence, structure-derived matrices, and an interactive
3D viewer. Runs locally (no AWS, no GPU); it only reads result files.

```bash
# 1. collect the screen's results locally (structures + metrics)
aws s3 cp "s3://$BUCKET/results/cd5_screen/" results/cd5_screen/ --recursive

# 2. build a leaderboard from the per-record metrics (if you don't already have one)
python3 scripts/aggregate_cd5_leaderboard.py results/cd5_screen

# 3. generate the report and open it
python3 scripts/build_cd5_report.py --results-dir results/cd5_screen --out results/cd5_screen/report
open results/cd5_screen/report/index.html
```

Input is any local directory holding `leaderboard.csv` + per-record `<id>.cif` + `<id>.metrics.json`
(exactly the fold outputs). Flags: `--top-n` (detail pages, default 20), `--contact-threshold` (Å,
default 8.0), `--out`. Needs only `numpy` + `jinja2` (`pip install numpy jinja2`); 3D viewer
([3Dmol.js](https://3dmol.org)) is vendored into the report so nothing loads from the network.

**What you get** under `<out>/`:
- `index.html` — leaderboard of all records (client-sortable; POS/NEG controls badged; a calibration-gate
  verdict comparing the positive vs negative controls; an iPTM strip-plot).
- `pages/<id>.html` — for each top-N record: the chain-A (binder) sequence with **CDRs highlighted** +
  the chain-B (target) sequence, the full `metrics.json`, three matrices derived from the `.cif`
  (**binder×target interface contact map**, **full Cα–Cα distance matrix**, **per-residue pLDDT track**),
  and a **3D structure viewer** with zoom / rotate / spin and toggles for style (cartoon / stick / sphere
  / surface), coloring (chain / pLDDT / spectrum), and interface highlighting.
- `assets/` — the vendored viewer + stylesheet (keep these alongside the HTML when sharing the folder).

The report opens by double-clicking `index.html` — no web server and no internet required (the 3D viewer
shows a fallback message only on browsers without WebGL). To regenerate and QA it in one step, use the
`/report-preflight` skill.

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

Each design takes **~16 min on a single H100** (~35 min on the L40S); array children run sequentially per
GPU. See [Measured throughput](#measured-throughput-single-h100--p54xlarge).

---

## Configuration & cost

- Set region, instance type, and resource names in `scripts/env.sh` (gitignored). Default instance is
  `g6e.2xlarge` (1× L40S, 48 GB) in `us-east-1`.
- The compute environment scales to zero when idle (`minvCpus=0`) — no GPU billing between jobs.
- Concurrency ≈ compute-env `maxVcpus` ÷ job vCPUs. Raise `MAX_VCPUS` in `env.sh` for more parallel jobs
  (after confirming quota and budget).
- First job on a fresh instance downloads model weights (a few minutes). For very large campaigns,
  consider mounting EFS as the Hugging Face cache (`HF_HOME`).

### Running in another AWS region

The only file you edit is **`scripts/env.sh`**: set `AWS_REGION` (and `AWS_DEFAULT_REGION`). `BUCKET` and
`IMAGE_URI` derive from the region automatically, so nothing else needs hand-editing.

ECR images and S3 buckets are **region-scoped**, so you must re-run the region-scoped setup in the new
region before submitting jobs (IAM roles are global and are reused):

```bash
# after editing AWS_REGION in scripts/env.sh:
source scripts/env.sh
./scripts/01_create_bucket.sh        # new bucket in the new region
./scripts/03_create_iam_roles.sh     # re-points the job role's S3 policy at the new bucket
./scripts/10_codebuild.sh            # rebuild the fold image into the new region's ECR
./scripts/10b_build_design.sh        # rebuild the design image into the new region's ECR
./scripts/05_create_compute_env_and_queue.sh
./scripts/09_launch_template.sh
./scripts/04_register_job_definition.sh
./scripts/13_register_design_jobdef.sh
```

Check your GPU vCPU quota in the new region (G-family → "Running On-Demand G and VT instances";
P-family such as H100 → "Running On-Demand P instances").

### Reserved capacity (Capacity Reservations / ODCR)

To pin jobs to a reserved instance (e.g. a reserved H100), set in **`scripts/env.sh`**:

- `INSTANCE_TYPE` — the reserved type (e.g. `p5.4xlarge`).
- `MAX_VCPUS` — one instance's vCPU count, so Batch launches exactly one and never an extra on-demand one
  (e.g. `16` for `p5.4xlarge`).
- `RESERVATION_SUBNET` — the subnet in the reservation's **Availability Zone** (a Capacity Reservation is
  AZ-scoped; Batch must launch there to consume it).
- `CAPACITY_RESERVATION_PREFERENCE=open` to auto-use any matching **open** reservation, **or**
  `CAPACITY_RESERVATION_ID=cr-…` to target a specific one.

`scripts/05_create_compute_env_and_queue.sh` pins the compute environment to `RESERVATION_SUBNET`, and
`scripts/09_launch_template.sh` injects the capacity-reservation block into the launch template. **Note:**
an ODCR bills hourly until you **cancel it** (EC2 → Capacity Reservations → Cancel), independent of
whether an instance is using it — disabling/scaling the compute env stops *instance* charges but not the
reservation.

### Measured throughput (single H100 / `p5.4xlarge`)

Numbers below are per sample on one H100 for a ~470 aa VHH–antigen complex; the L40S figures are the
prior baseline on `g6e.2xlarge`.

| Workload | Settings | H100 per sample | L40S per sample |
|---|---|---|---|
| **Fold (complex)** | careful ranking (3 / 100 / 4) | **~13 s warm** (model loaded once) | — |
| **Nanobody design** | 150 steps, 4 hero critics, `batch_size=1` | **~16 min** (incl. model load) | ~35 min |

- **Folding:** the ~13 s is the warm, in-process fold cost; the *first* job on a fresh instance also pays
  a one-time model download + cold-start (~6–7 min). On a single GPU, fold many records as **one job**
  (pass a multi-record manifest to `06_submit_job.sh` — the model loads once) rather than an array (each
  child reloads). Example: 53 complexes in one job ≈ **18 min** of compute (~33 min end-to-end incl.
  instance launch + image pull + first download).
- **Design:** ~16 min/design includes each job's model download/load; on one GPU array children run
  sequentially (6 designs ≈ 1.5–1.7 h). About **2× faster than the L40S**.

## Safety

ESMFold2 outputs are **computational hypotheses** ranked by a model's own confidence — they are not
validated binders or structures. Confirm experimentally. Keep target/binder sequences and identifiers in
your gitignored config and S3, not in Batch job names or logs.

# Guide verification (cross-checked 2026-06-01)

The original setup guide was checked against live sources before building this scaffold. Bottom line:
**the guide is substantially accurate.** A few things were corrected; those corrections are baked into
the files here.

## The one thing that looked wrong but isn't
"Biohub/ESMFold2" and `github.com/Biohub/esm` looked fabricated at first. They are real:
**EvolutionaryScale renamed its GitHub org/repo from `evolutionaryscale/esm` → `Biohub/esm`** (the old
URL 301-redirects to the new one), and **ESMFold2 was released ~May 2026**. It only looked fake because
it postdates the assistant's January 2026 knowledge cutoff. `esm` is v3.3.0, authored by the
EvolutionaryScale Team (Zeming Lin), and the HF models `biohub/ESMFold2` / `biohub/ESMFold2-Fast` exist.

## Confirmed
- HF models `biohub/ESMFold2` (MSA-conditioned) and `biohub/ESMFold2-Fast` (inference-optimized,
  single-sequence, no MSA) exist (created 2026-05).
- `pip install "esm @ git+https://github.com/Biohub/esm.git@<rev>"` — real, current official repo.
- Modal example (modal.com/docs/examples/esmfold2) exists and uses exactly these imports + the
  `ESMFold2InputBuilder().fold(model, spi, num_loops=, num_sampling_steps=, num_diffusion_samples=, seed=)`
  call; `result.complex.to_mmcif()`, `result.plddt/.ptm/.iptm` all exist in source.
- Base image `public.ecr.aws/deep-learning-containers/pytorch-inference:2.6.0-gpu-py312-cu124-ubuntu22.04-ec2-v1.72`
  is real and pullable.
- AWS Batch facts: g5.2xlarge specs, GPU-family requirement, Fargate has no GPU, `resourceRequirements`
  for GPU/VCPU/MEMORY, `AWS_BATCH_JOB_ARRAY_INDEX` on array children — all accurate.

## Corrected (fixes applied in this scaffold)
1. **Parameter substitution syntax.** The original guide used bare `"Ref::input_s3"`, which is
   CORRECT. (I briefly "corrected" it to double-brace `{{Ref::input_s3}}` based on the AWS docs
   page — but that form is NOT honored by the Batch runtime: a job submitted with `{{Ref::num_loops}}`
   passed the literal string to the container and crashed at argparse. Runtime-confirmed 2026-06-01:
   AWS Batch substitutes only bare, whole-element `Ref::name` tokens.) `job-definition.json` uses
   bare `Ref::`.
2. **esm pin.** The guide used `@c94ed8d` (not a real revision). → replaced with the real pinned
   revision `81b3646c9429ea8458918415ad6a46178cb59833` (matches Modal's example) in `Dockerfile`.
3. **`transformers` import.** `from transformers.models.esmfold2.modeling_esmfold2 import ESMFold2Model`
   works **only via EvolutionaryScale's `transformers` fork**, which `esm` installs as a dependency —
   NOT stock PyPI `transformers`. (No code change needed; installing `esm` handles it. Documented.)

## Runtime-discovered issues (fixed during first live run, 2026-06-01)
- **Instance type:** `g5.2xlarge` is NOT offered by Batch in `ap-southeast-1`. Switched to
  `g4dn.2xlarge` (Tesla T4, 16 GB GPU). Verify availability per region from the
  CreateComputeEnvironment error's allowed-types list.
- **Disk:** default ~30 GB instance root fills up (8 GB image + ESMFold2/ESMC weights + HF
  download temp) → `No space left on device`. Fixed with a launch template giving a 100 GB root
  (`scripts/09_launch_template.sh`), attached to the compute environment.
- **First successful fold:** ubiquitin (76 aa), pLDDT 0.827 / pTM 0.789, ~407 s on T4.

## Caveats to watch
- **Base image tag drifts.** The `-v1.x` suffix advances and PyTorch 2.6 inference images are patched
  only through ~end of June 2026. The `Dockerfile` exposes `BASE_IMAGE` as a build ARG and includes a
  one-liner to look up the current tag.
- **Execution role.** Not strictly required for a basic EC2 Batch job, but needed for ECR pull /
  CloudWatch logs in practice — so it's kept.
- **Model revisions differ per repo.** `6234905` pins `biohub/ESMFold2`; `-Fast` has its own. The run
  script takes an optional `--revision`.

---
name: design-preflight
description: Check readiness for a VHH binder-DESIGN run (the vendored official binder_design.py on AWS Batch) before submitting a design array — image deps, EFS HF-cache, experimental model repos, design job def, GPU/spot. Read-only go/no-go on top of /preflight.
disable-model-invocation: true
---

# Design-preflight — official binder_design.py on AWS Batch readiness

Read-only checks for the reuse-based design pipeline (`run_esmfold2_design.py` / `run_esmfold2_rank.py`
wrapping the vendored `design/binder_design.py`). Do NOT create or modify anything. Companion to
`/preflight` (run that first for identity, permissions, GPU quota, S3, base image). Spec:
`/Users/heng/.claude/plans/okay-so-right-now-sharded-clover.md`.

Assume `source scripts/env.sh` (use `$AWS_REGION`, `$BUCKET`, `$ECR_REPO`, `$JOB_QUEUE`,
`$COMPUTE_ENV`, `$INSTANCE_TYPE`, `$MAX_VCPUS`); if unset, tell the user to source it first.

Mark each ✅ / ❌ / ⚠️:

1. **Vendored code is faithful.** Suggest `/check-upstream-drift` — confirm `design/binder_design.py`
   diverges from upstream only in the sanctioned spots (Modal-strip, `REUSE_ESMC=True`) and that the
   gating/ranking is unchanged. ⚠️ if not recently checked. (The binder framework comes from the
   campaign config at runtime, not a committed prompt.)

2. **Image has the design deps.** The design image must include `esm@f652b471`, **ANARCI + HMMER**
   (conda/micromamba), `abnumber`, and `biotite`, plus `design/` + the new entrypoints. Confirm the
   latest ECR image postdates the design build:
   `aws ecr describe-images --repository-name "$ECR_REPO" --region "$AWS_REGION" --query 'sort_by(imageDetails,&imagePushedAt)[-1].imagePushedAt'`.
   ❌ if the image predates the design work (rebuild via `scripts/10_codebuild.sh`).

3. **EFS HF-cache populated.** The 7 large repos (2 inversion + 4 hero critics + `biohub/ESMC-6B`) must
   live on the EFS volume mounted at `HF_HOME`, populated by the one-time warm-up
   (`scripts/14_warm_efs_cache.sh`) — NOT pulled per job. ❌ if the EFS mount target / access point is
   missing or the cache wasn't warmed. (Check the EFS file system + that the design job def mounts it.)

4. **Experimental + ESMC-6B repos reachable.** Confirm the HF repos resolve (HTTP 200):
   `biohub/ESMFold2-Experimental-Fast`, `…-Fast-Cutoff2025`, `…-Experimental`, `…-Experimental-Cutoff2025`,
   `biohub/ESMC-6B`. ⚠️ if any 404 (gated/renamed) — the warm-up + design load will fail.

5. **Design + rank job defs registered.** `aws batch describe-job-definitions --job-definition-name
   esmfold2-design-jobdef --status ACTIVE --region "$AWS_REGION"` (and the rank def if separate). ❌ if
   missing (run `scripts/13_register_design_jobdef.sh`). Spot-check: GPU=1, bare `Ref::` params, EFS
   volume mounted at `HF_HOME`, `timeout` set.

6. **VRAM headroom.** Instance is L40S-class (g6e.2xlarge, 48 GB). `REUSE_ESMC=True` should keep the
   2 inversion + 4 hero critics + ESMC under ~27–48 GB. ⚠️ remind that CD5 (347 aa) is larger than the
   paper's targets, so P0 must confirm the actual peak; fallbacks = fewer/CPU hero critics or a target crop.

7. **Concurrency / spot.** `maxvCpus` (`$MAX_VCPUS`) implies the intended array concurrency and the
   instance family is GPU. ⚠️ if the design array isn't on spot (seeds are independent — ideal spot).

End with a one-line verdict: **READY to submit a design run** only if 1–5 pass (6–7 are
feasibility/cost), otherwise list the exact blocking steps in order. Remind that gating/ranking is the
official method used unchanged, and that a small seed array is a plumbing test, not a hit-finding campaign.

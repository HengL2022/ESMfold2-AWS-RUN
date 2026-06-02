"""Stage-1 GENERATE: run the official ESMFold2 binder-design loop on AWS Batch and write results to S3.

Thin harness around the vendored `design/binder_design.py` (`ESMFold2Design.load()/.design()`). The
design loop, losses, ESMC pseudo-perplexity, and the critic-ensemble gating/ranking are the official
implementation, used UNCHANGED. This file only does S3 I/O + seed/array bookkeeping.

One AWS Batch array child = one seed (`seed = seed_base + AWS_BATCH_JOB_ARRAY_INDEX`). Each design's
best sequence is folded by the hero critics inside `design()`, producing per-critic iPTM + distogram /
CDR proxies. We write, per design:
  <id>.fasta         the designed binder (VHH) sequence
  <id>.metrics.json  per-critic metrics (iptm, distogram_iptm_proxy, cdr_distogram_iptm_proxy, final_loss)
  <id>.cif           the predicted target|binder complex (first hero critic)

Campaign config (JSON on S3) — see design_config.example.json. Target = target_name OR target_sequence;
binder = binder_name (a public prompt) OR binder_template + binder_cdr_lengths (your private framework,
supplied via the gitignored config so it never enters the repo):
  {"campaign_id": "campaign_v1", "target_name": "pd-l1", "binder_name": "minibinder",
   "is_antibody": false, "seed_base": 0, "batch_size": 1, "use_scaling_critics": false}
"""

import argparse
import json
import os
import sys
import time

import torch

# Reuse the proven S3 helpers (single source of truth) from the folding entrypoint.
from run_esmfold2_batch import parse_s3_uri, s3_read_text, s3_write_text, sanitize_id

from design.binder_design import ESMFold2Design


def _structure_to_text(complex_obj):
    """ProteinComplex -> (text, extension). Prefer mmCIF; fall back to PDB."""
    for meth, ext, ctype in (("to_mmcif", "cif", "chemical/x-mmcif"),
                             ("to_pdb_string", "pdb", "chemical/x-pdb"),
                             ("to_pdb", "pdb", "chemical/x-pdb")):
        fn = getattr(complex_obj, meth, None)
        if fn is None:
            continue
        try:
            out = fn()
            if isinstance(out, str) and out.strip():
                return out, ext, ctype
        except Exception:
            continue
    return None, None, None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config-s3", required=True, help="s3://.../config.json campaign manifest")
    p.add_argument("--output-s3", required=True, help="s3://.../stage1/ output prefix")
    args = p.parse_args()

    started = time.time()
    cfg = json.loads(s3_read_text(args.config_s3))
    campaign_id = sanitize_id(str(cfg.get("campaign_id", "design")))
    target_name = cfg.get("target_name")            # a public built-in target, OR ...
    target_sequence = cfg.get("target_sequence")    # ... an explicit sequence (kept in your private config)
    binder_name = cfg.get("binder_name")
    binder_template = cfg.get("binder_template")    # optional private prompt (see below)
    is_antibody = cfg.get("is_antibody")            # None -> let the prompt decide
    seed_base = int(cfg.get("seed_base", 0))
    batch_size = int(cfg.get("batch_size", 1))
    use_scaling_critics = bool(cfg.get("use_scaling_critics", False))

    # Register an ad-hoc binder prompt from the config. This lets a private antibody framework be
    # supplied via the (gitignored) campaign config instead of being hard-coded in the repo — the
    # framework sequence then lives only in your S3 config, never in version control.
    if binder_template:
        from design.binder_design import BINDER_PROMPT_FACTORIES, PromptFactory
        lengths = {k: tuple(v) for k, v in (cfg.get("binder_cdr_lengths") or {}).items()}
        binder_name = binder_name or "custom"
        BINDER_PROMPT_FACTORIES[binder_name] = PromptFactory(
            name=binder_name, template=binder_template, length_ranges=lengths,
            is_antibody=bool(is_antibody) if is_antibody is not None else True,
        )

    # Resolve the target sequence locally for logging + metrics; design() also accepts target_name.
    if target_sequence is None and target_name:
        from design.binder_design import TARGET_SEQUENCES
        resolved_target = TARGET_SEQUENCES[target_name]
    else:
        resolved_target = target_sequence

    array_index = int(os.environ.get("AWS_BATCH_JOB_ARRAY_INDEX", "0"))
    seed = seed_base + array_index

    print(f"Campaign={campaign_id} seed={seed} (base {seed_base} + idx {array_index}) "
          f"binder={binder_name} target={target_name or 'seq'}({len(resolved_target)}aa) batch_size={batch_size}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"CUDA device: {torch.cuda.get_device_name(0)}")
        torch.cuda.reset_peak_memory_stats()

    print("Loading ESMFold2Design (inversion + hero critics + ESMC)...", flush=True)
    app = ESMFold2Design()
    app.load(use_scaling_critics=use_scaling_critics)
    if torch.cuda.is_available():
        print(f"Post-load peak GPU mem: {torch.cuda.max_memory_allocated() / 1e9:.2f} GB", flush=True)

    print("Running design()...", flush=True)
    best_sequences, trajectory, critic_results = app.design(
        target_name=target_name,
        target_sequence=target_sequence,
        binder_name=binder_name,
        is_antibody=is_antibody,
        seed=seed,
        batch_size=batch_size,
    )
    if torch.cuda.is_available():
        print(f"Post-design peak GPU mem: {torch.cuda.max_memory_allocated() / 1e9:.2f} GB", flush=True)

    out_prefix = args.output_s3.rstrip("/")

    # Group the per-(design, critic) rows by design (batch_idx).
    by_design: dict[int, list[dict]] = {}
    for r in critic_results:
        by_design.setdefault(int(r.get("batch_idx", 0)), []).append(r)

    summary = []
    for batch_idx, rows in sorted(by_design.items()):
        suffix = f"_seed{seed}" + (f"_b{batch_idx}" if batch_size > 1 else "")
        rid = sanitize_id(f"{campaign_id}{suffix}")
        designed_sequence = rows[0].get("designed_sequence", best_sequences[batch_idx])
        binder_seq = designed_sequence.split("|")[-1]
        is_ab = bool(rows[0].get("is_antibody", is_antibody))

        # Per-critic metrics (official, unchanged) — drop non-JSON objects (complex, logits).
        per_critic = [
            {
                "critic_name": r.get("critic_name"),
                "iptm": r.get("iptm"),
                "distogram_iptm_proxy": r.get("distogram_iptm_proxy"),
                "cdr_distogram_iptm_proxy": r.get("cdr_distogram_iptm_proxy"),
                "final_loss": r.get("final_loss"),
            }
            for r in rows
        ]
        metrics = {
            "id": rid,
            "campaign_id": campaign_id,
            "seed": seed,
            "binder_name": binder_name,
            "is_antibody": is_ab,
            "designed_sequence": designed_sequence,
            "binder_sequence": binder_seq,
            "binder_length": len(binder_seq),
            "target_length": len(resolved_target),
            "per_critic": per_critic,
            "runtime_seconds": round(time.time() - started, 1),
        }
        s3_write_text(f"{out_prefix}/{rid}.metrics.json",
                      json.dumps(metrics, indent=2), content_type="application/json")
        s3_write_text(f"{out_prefix}/{rid}.fasta",
                      f">{rid} seed={seed} binder={binder_name} len={len(binder_seq)}\n{binder_seq}\n")

        # Structure: first hero critic's predicted complex.
        complex_obj = next((r.get("complex") for r in rows if r.get("complex") is not None), None)
        if complex_obj is not None:
            text, ext, ctype = _structure_to_text(complex_obj)
            if text is not None:
                s3_write_text(f"{out_prefix}/{rid}.{ext}", text, content_type=ctype)
                print(f"Wrote {rid}.{ext}")
            else:
                print(f"WARNING: could not serialize complex for {rid}")
        print(f"Wrote {rid}.metrics.json / .fasta  (binder {len(binder_seq)}aa)")
        summary.append(metrics)

    s3_write_text(f"{out_prefix}/summary-seed{seed}.json",
                  json.dumps(summary, indent=2), content_type="application/json")
    print(f"Done in {round(time.time() - started, 1)}s; {len(summary)} design(s).")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

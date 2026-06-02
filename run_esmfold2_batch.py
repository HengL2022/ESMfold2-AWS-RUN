"""Run ESMFold2 / ESMFold2-Fast on a FASTA file pulled from S3 and write results back to S3.

Designed to run inside an AWS Batch GPU job. Supports array jobs: when AWS_BATCH_JOB_ARRAY_INDEX
is set, this process folds only the FASTA record at that index.

API verified against the official EvolutionaryScale `esm` package (github.com/Biohub/esm) and
Modal's ESMFold2 example.
"""

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import boto3
import torch

from esm.models.esmfold2 import (
    ESMFold2InputBuilder,
    ProteinInput,
    StructurePredictionInput,
)
from transformers.models.esmfold2.modeling_esmfold2 import ESMFold2Model


def parse_s3_uri(uri: str):
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Expected s3://bucket/key, got: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def s3_read_text(uri: str) -> str:
    bucket, key = parse_s3_uri(uri)
    obj = boto3.client("s3").get_object(Bucket=bucket, Key=key)
    return obj["Body"].read().decode("utf-8")


def s3_write_text(uri: str, text: str, content_type: str = "text/plain"):
    bucket, key = parse_s3_uri(uri)
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=text.encode("utf-8"),
        ContentType=content_type,
    )


def sanitize_id(raw_id: str) -> str:
    raw_id = raw_id.strip() or "sequence"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_id)
    return safe[:120]


def parse_fasta(text: str):
    records = []
    current_id = None
    current_seq = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_id is not None:
                records.append((sanitize_id(current_id), "".join(current_seq).upper()))
            current_id = line[1:].split()[0]
            current_seq = []
        else:
            current_seq.append(line)

    if current_id is not None:
        records.append((sanitize_id(current_id), "".join(current_seq).upper()))

    # Allow a plain sequence file with no FASTA header.
    if not records:
        seq = re.sub(r"\s+", "", text).upper()
        if seq:
            records.append(("sequence_0", seq))

    valid = []
    aa_pattern = re.compile(r"^[ACDEFGHIKLMNPQRSTVWYBXZUOJ*-]+$")
    for record_id, seq in records:
        seq = seq.replace("*", "")
        if not seq:
            continue
        if not aa_pattern.match(seq):
            raise ValueError(f"Sequence {record_id} contains unexpected characters.")
        valid.append((record_id, seq))

    if not valid:
        raise ValueError("No valid FASTA records found.")

    return valid


def parse_complexes(text: str):
    """Parse a complexes manifest: a JSON list of {id, chains:[{chain_id, sequence}, ...]}.
    Returns a list of (record_id, [(chain_id, sequence), ...])."""
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    aa_pattern = re.compile(r"^[ACDEFGHIKLMNPQRSTVWYBXZUOJ-]+$")
    records = []
    for i, entry in enumerate(data):
        rid = sanitize_id(str(entry.get("id", f"complex_{i}")))
        chains = []
        for j, ch in enumerate(entry.get("chains", [])):
            cid = str(ch.get("chain_id") or chr(ord("A") + j))
            seq = re.sub(r"\s+", "", str(ch["sequence"])).upper().replace("*", "")
            if not seq or not aa_pattern.match(seq):
                raise ValueError(f"Complex {rid} chain {cid} has an invalid/empty sequence.")
            chains.append((cid, seq))
        if not chains:
            raise ValueError(f"Complex {rid} has no chains.")
        records.append((rid, chains))
    if not records:
        raise ValueError("No complexes found in manifest.")
    return records


def fold_record(model, chains, num_loops: int, num_sampling_steps: int,
                num_diffusion_samples: int, seed: int):
    # chains: list of (chain_id, sequence). One entry = monomer; multiple = complex.
    spi = StructurePredictionInput(
        sequences=[ProteinInput(id=cid, sequence=seq.strip()) for cid, seq in chains]
    )

    result = ESMFold2InputBuilder().fold(
        model,
        spi,
        num_loops=num_loops,
        num_sampling_steps=num_sampling_steps,
        num_diffusion_samples=num_diffusion_samples,
        seed=seed,
    )

    # With num_diffusion_samples > 1 the builder returns a LIST of per-sample results (each with
    # .complex/.plddt/.ptm/.iptm); with 1 it returns a single result. Normalize to a list so the
    # single-sample path (used by the original CD5 screen) is byte-for-byte unchanged.
    samples = result if isinstance(result, list) else [result]

    def _score(r):
        # Rank samples for picking the representative structure: prefer interface confidence,
        # then pTM, then mean pLDDT. iptm is None for a single chain (the common VHH case).
        if getattr(r, "iptm", None) is not None:
            return float(r.iptm)
        if getattr(r, "ptm", None) is not None:
            return float(r.ptm)
        return float(r.plddt.mean()) if getattr(r, "plddt", None) is not None else -1.0

    best = max(samples, key=_score)
    iptms = [float(r.iptm) for r in samples if getattr(r, "iptm", None) is not None]
    ptms = [float(r.ptm) for r in samples if getattr(r, "ptm", None) is not None]

    cif_text = best.complex.to_mmcif()
    # ptm/plddt/iptm can be None depending on the model — guard all of them. Across multiple
    # diffusion samples we also report mean/max so a single noisy draw doesn't mislead.
    metrics = {
        "plddt_mean": float(best.plddt.mean()) if getattr(best, "plddt", None) is not None else None,
        "ptm": float(best.ptm) if getattr(best, "ptm", None) is not None else None,
        "iptm": float(best.iptm) if getattr(best, "iptm", None) is not None else None,
        "n_diffusion_samples_returned": len(samples),
        "iptm_mean": (sum(iptms) / len(iptms)) if iptms else None,
        "iptm_max": max(iptms) if iptms else None,
        "ptm_mean": (sum(ptms) / len(ptms)) if ptms else None,
    }
    return cif_text, metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-s3", required=True)
    parser.add_argument("--output-s3", required=True)
    parser.add_argument("--model", default="biohub/ESMFold2-Fast")
    parser.add_argument(
        "--revision",
        default=None,
        help="Optional HF model revision/commit to pin (e.g. 6234905 for biohub/ESMFold2). "
             "Omit to use the repo default branch.",
    )
    parser.add_argument("--num-loops", type=int, default=3)
    parser.add_argument("--num-sampling-steps", type=int, default=50)
    parser.add_argument("--num-diffusion-samples", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--record-index",
        type=int,
        default=None,
        help="Optional FASTA record index. Usually omitted; array jobs use AWS_BATCH_JOB_ARRAY_INDEX.",
    )
    args = parser.parse_args()

    started = time.time()

    print(f"Input: {args.input_s3}")
    print(f"Output prefix: {args.output_s3}")
    print(f"Model: {args.model} (revision={args.revision or 'default'})")
    print(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"CUDA device: {torch.cuda.get_device_name(0)}")
    else:
        print("WARNING: CUDA is not available. ESMFold2 expects a GPU; this will be very slow or fail.")

    input_text = s3_read_text(args.input_s3)
    # A .json input is a complexes manifest (multi-chain); anything else is FASTA (monomers).
    if args.input_s3.lower().endswith(".json"):
        records = parse_complexes(input_text)
        print(f"Found {len(records)} complex record(s).")
    else:
        records = [(rid, [("A", seq)]) for rid, seq in parse_fasta(input_text)]
        print(f"Found {len(records)} FASTA record(s).")

    array_index = os.environ.get("AWS_BATCH_JOB_ARRAY_INDEX")
    record_index = args.record_index
    if record_index is None and array_index is not None:
        record_index = int(array_index)

    if record_index is not None:
        if record_index < 0 or record_index >= len(records):
            raise IndexError(
                f"record_index={record_index} but input contains {len(records)} records."
            )
        records = [records[record_index]]
        print(f"Array/record mode: folding record index {record_index}.")

    print("Loading model onto GPU...")
    from_pretrained_kwargs = {}
    if args.revision:
        from_pretrained_kwargs["revision"] = args.revision
    model = ESMFold2Model.from_pretrained(args.model, **from_pretrained_kwargs).cuda().eval()

    output_prefix = args.output_s3.rstrip("/")

    summary = []
    for i, (record_id, chains) in enumerate(records):
        chain_desc = "+".join(f"{cid}:{len(seq)}" for cid, seq in chains)
        total_len = sum(len(seq) for _, seq in chains)
        print(f"Folding record {i}: id={record_id}, chains={chain_desc}, total={total_len}")

        with torch.inference_mode():
            cif_text, metrics = fold_record(
                model=model,
                chains=chains,
                num_loops=args.num_loops,
                num_sampling_steps=args.num_sampling_steps,
                num_diffusion_samples=args.num_diffusion_samples,
                seed=args.seed,
            )

        metrics.update(
            {
                "id": record_id,
                "num_chains": len(chains),
                "chains": {cid: len(seq) for cid, seq in chains},
                "total_length": total_len,
                "model": args.model,
                "revision": args.revision,
                "num_loops": args.num_loops,
                "num_sampling_steps": args.num_sampling_steps,
                "num_diffusion_samples": args.num_diffusion_samples,
                "seed": args.seed,
                "runtime_seconds_so_far": round(time.time() - started, 2),
            }
        )

        cif_uri = f"{output_prefix}/{record_id}.cif"
        metrics_uri = f"{output_prefix}/{record_id}.metrics.json"

        s3_write_text(cif_uri, cif_text, content_type="chemical/x-mmcif")
        s3_write_text(
            metrics_uri,
            json.dumps(metrics, indent=2),
            content_type="application/json",
        )

        print(f"Wrote {cif_uri}")
        print(f"Wrote {metrics_uri}")
        summary.append(metrics)

    summary_uri = f"{output_prefix}/summary-{os.environ.get('AWS_BATCH_JOB_ID', 'local')}.json"
    s3_write_text(
        summary_uri,
        json.dumps(summary, indent=2),
        content_type="application/json",
    )
    print(f"Wrote summary {summary_uri}")
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

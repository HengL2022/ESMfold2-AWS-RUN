"""Stage-2 RANK/SELECT: pool Stage-1 design metrics from S3 and rank by the OFFICIAL critic scores.

Faithful to the released gating/ranking: each design's metrics are the per-hero-critic `iptm`,
`distogram_iptm_proxy`, and `cdr_distogram_iptm_proxy` that `design_binder` already computed (we do
NOT invent weights or demote iPTM). We aggregate each design's score by the MEAN across its hero
critics (Algorithm 15 averages over the critic ensemble) and rank by the antibody selection proxy —
the CDR-restricted distogram-ipTM proxy — falling back to the whole-binder proxy when CDR is absent.

Outputs: stage2/leaderboard.csv (all designs, ranked) + stage2/top_k.fasta (top-K binders).
"""

import argparse
import csv
import io
import json
import math
import sys

import boto3

from run_esmfold2_batch import parse_s3_uri, s3_write_text


def _mean(values):
    """Mean of present, finite values; None if none."""
    xs = [float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return (sum(xs) / len(xs)) if xs else None


def _list_metrics(stage1_s3: str):
    bucket, prefix = parse_s3_uri(stage1_s3.rstrip("/") + "/")
    s3 = boto3.client("s3")
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".metrics.json"):
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
                yield json.loads(body)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage1-s3", required=True, help="s3://.../stage1/ prefix of Stage-1 outputs")
    p.add_argument("--output-s3", required=True, help="s3://.../stage2/ prefix for the leaderboard")
    p.add_argument("--top-k", type=int, default=84, help="how many to emit to top_k.fasta")
    args = p.parse_args()

    rows = []
    for m in _list_metrics(args.stage1_s3):
        critics = m.get("per_critic", [])
        mean_iptm = _mean([c.get("iptm") for c in critics])
        mean_dgram = _mean([c.get("distogram_iptm_proxy") for c in critics])
        mean_cdr = _mean([c.get("cdr_distogram_iptm_proxy") for c in critics])
        mean_loss = _mean([c.get("final_loss") for c in critics])
        # Official antibody selection metric = CDR-restricted proxy; fall back to whole-binder proxy.
        rank_score = mean_cdr if mean_cdr is not None else mean_dgram
        rows.append({
            "id": m.get("id"),
            "seed": m.get("seed"),
            "binder_length": m.get("binder_length"),
            "mean_iptm": mean_iptm,
            "mean_distogram_iptm_proxy": mean_dgram,
            "mean_cdr_distogram_iptm_proxy": mean_cdr,
            "mean_final_loss": mean_loss,
            "n_critics": len(critics),
            "rank_score": rank_score,
            "binder_sequence": m.get("binder_sequence", ""),
        })

    if not rows:
        raise SystemExit(f"No *.metrics.json found under {args.stage1_s3}")

    rows.sort(key=lambda r: (r["rank_score"] is not None, r["rank_score"] or -1.0), reverse=True)

    cols = ["rank", "id", "seed", "binder_length", "mean_cdr_distogram_iptm_proxy",
            "mean_distogram_iptm_proxy", "mean_iptm", "mean_final_loss", "n_critics", "binder_sequence"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for i, r in enumerate(rows, 1):
        w.writerow({"rank": i, **{k: r.get(k) for k in cols if k != "rank"}})
    out_prefix = args.output_s3.rstrip("/")
    s3_write_text(f"{out_prefix}/leaderboard.csv", buf.getvalue(), content_type="text/csv")

    fasta = "".join(
        f">rank{i}_{r['id']} cdr_proxy={r['mean_cdr_distogram_iptm_proxy']} iptm={r['mean_iptm']}\n"
        f"{r['binder_sequence']}\n"
        for i, r in enumerate(rows[: args.top_k], 1) if r["binder_sequence"]
    )
    s3_write_text(f"{out_prefix}/top_k.fasta", fasta)

    print(f"Ranked {len(rows)} designs -> {out_prefix}/leaderboard.csv (+ top_{args.top_k}.fasta)")
    for r in rows[:10]:
        print(f"  {r['id']:32s} cdr_proxy={r['mean_cdr_distogram_iptm_proxy']} "
              f"dgram={r['mean_distogram_iptm_proxy']} iptm={r['mean_iptm']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

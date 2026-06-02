#!/usr/bin/env python3
"""Aggregate the CD5 screen's per-record metrics into a ranked leaderboard.

Usage:
  python3 scripts/aggregate_cd5_leaderboard.py <results_dir> [out.csv]

<results_dir> holds the *.metrics.json files produced by run_esmfold2_batch.py
(download them first: aws s3 cp s3://$BUCKET/results/cd5_screen/ <dir> --recursive).

Ranks by iptm (interface confidence), tie-broken by iptm_mean then plddt_mean, and
flags the POS_/NEG_ controls so the ranking can be read relative to them. iptm is a
weak/non-specific oracle for CD5 (project Gate-0 note) — interpret as triage, not a
hard binding call, and check that POS out-scores the NEG controls.
"""
import csv
import json
import sys
from pathlib import Path


def num(x):
    return x if isinstance(x, (int, float)) else None


def sort_key(r):
    # Higher is better; None sorts last.
    def k(v):
        return v if isinstance(v, (int, float)) else float("-inf")
    return (k(r["iptm"]), k(r["iptm_mean"]), k(r["plddt_mean"]))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    results_dir = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else results_dir / "leaderboard.csv"

    rows = []
    for f in sorted(results_dir.glob("*.metrics.json")):
        m = json.loads(f.read_text())
        rid = m.get("id", f.stem)
        rows.append({
            "id": rid,
            "is_control": "POS" if rid.startswith("POS_") else ("NEG" if rid.startswith("NEG_") else ""),
            "iptm": num(m.get("iptm")),
            "iptm_mean": num(m.get("iptm_mean")),
            "iptm_max": num(m.get("iptm_max")),
            "ptm": num(m.get("ptm")),
            "plddt_mean": num(m.get("plddt_mean")),
            "n_diffusion_samples_returned": m.get("n_diffusion_samples_returned"),
            "total_length": m.get("total_length"),
        })

    rows.sort(key=sort_key, reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    fields = ["rank", "id", "is_control", "iptm", "iptm_mean", "iptm_max",
              "ptm", "plddt_mean", "n_diffusion_samples_returned", "total_length"]
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows -> {out}")
    pos = [r for r in rows if r["is_control"] == "POS"]
    neg = [r for r in rows if r["is_control"] == "NEG"]
    if pos and neg:
        pos_rank = pos[0]["rank"]
        neg_best = min(r["rank"] for r in neg)
        verdict = "OK (POS above NEG)" if pos_rank < neg_best else "WEAK: POS does not beat NEG — iptm not resolving CD5 binding"
        print(f"  POS rank={pos_rank}  best NEG rank={neg_best}  -> {verdict}")
    print("  Top 10:")
    for r in rows[:10]:
        tag = f" [{r['is_control']}]" if r["is_control"] else ""
        print(f"    {r['rank']:>2}. {r['id']}{tag}  iptm={r['iptm']}  iptm_mean={r['iptm_mean']}  plddt={r['plddt_mean']}")


if __name__ == "__main__":
    main()

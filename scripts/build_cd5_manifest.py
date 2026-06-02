#!/usr/bin/env python3
"""Build the CD5 complex-screening manifest for run_esmfold2_batch.py.

Emits a JSON list of {id, chains:[{chain_id:"A", VHH}, {chain_id:"B", CD5_ecto}]} —
one record per VHH candidate, plus calibration controls:
  - POS: anti-CD5_P_control (known CD5 binder)
  - NEG x2: CDR-scrambled VHHs (framework kept, CDR residues deterministically shuffled)

Pure stdlib so it runs locally without the model deps. Output matches the manifest
format parsed by run_esmfold2_batch.parse_complexes().
"""
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = ROOT / "Test_input" / "cd5_50_candidates.fasta"
CHAINS = ROOT / "Test_input" / "chains.fasta"
CONFIG = ROOT / "Test_input" / "cd5_vhh_v1_config.json"
OUT = ROOT / "Test_input" / "cd5_screen.json"


def compile_cdr_template(binder_template):
    """Build the CDR-anchor regex from a binder_template that marks the variable loops with
    {hcdr1}/{hcdr2}/{hcdr3}. The framework itself lives only in the (gitignored) campaign config,
    so it is never hardcoded in the repo. Groups 2,4,6 capture CDR1/CDR2/CDR3."""
    parts = re.split(r"\{hcdr[123]\}", binder_template)
    if len(parts) != 4:
        raise ValueError("binder_template must contain {hcdr1}, {hcdr2}, {hcdr3}")
    # Tolerate a single varying residue at the two FR→CDR boundaries (as the panel does).
    anchor = lambda fr: (re.escape(fr[:-1]) + ".") if fr else ""
    return re.compile(
        f"^({anchor(parts[0])})(.+?)({re.escape(parts[1])})(.+?)"
        f"({anchor(parts[2])})(.+?)({re.escape(parts[3])})$"
    )


def read_fasta(path):
    """Return list of (id, seq). id = first whitespace-delimited header token."""
    records, rid, seq = [], None, []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if rid is not None:
                records.append((rid, "".join(seq).upper()))
            rid = line[1:].split()[0]
            seq = []
        else:
            seq.append(line)
    if rid is not None:
        records.append((rid, "".join(seq).upper()))
    return records


def scramble_cdrs(seq, seed, template_re):
    """Deterministically shuffle the 3 CDR segments in place (length/composition kept)."""
    m = template_re.match(seq)
    if not m:
        raise ValueError(f"sequence does not match the VHH template: {seq}")
    g = list(m.groups())
    rng = random.Random(seed)
    for idx in (1, 3, 5):  # hcdr1, hcdr2, hcdr3 capture groups
        chars = list(g[idx])
        rng.shuffle(chars)
        g[idx] = "".join(chars)
    return "".join(g)


def main():
    candidates = read_fasta(CANDIDATES)
    chains = dict(read_fasta(CHAINS))
    cd5 = chains.get("CD5_ecto")
    pos = chains.get("anti-CD5_P_control")
    if not cd5 or not pos:
        sys.exit("CD5_ecto / anti-CD5_P_control not found in chains.fasta")
    if not CONFIG.exists():
        sys.exit(f"Campaign config not found: {CONFIG} (holds the private binder_template)")
    binder_template = json.loads(CONFIG.read_text())["binder_template"]
    template_re = compile_cdr_template(binder_template)

    records = []

    def add(rid, vhh):
        records.append({
            "id": rid,
            "chains": [
                {"chain_id": "A", "sequence": vhh},
                {"chain_id": "B", "sequence": cd5},
            ],
        })

    for rid, seq in candidates:
        add(f"{rid}__CD5ecto", seq)
    add("POS_antiCD5__CD5ecto", pos)
    # Two scrambled-CDR negatives derived from the most-supported candidate (VHHCluster_001).
    base = dict(candidates)["VHHCluster_001"]
    add("NEG_scramble1__CD5ecto", scramble_cdrs(base, 0, template_re))
    add("NEG_scramble2__CD5ecto", scramble_cdrs(base, 1, template_re))

    OUT.write_text(json.dumps(records, indent=2) + "\n")
    print(f"Wrote {len(records)} records -> {OUT}")
    print(f"  candidates={len(candidates)}  +1 positive  +2 negatives")
    print(f"  CD5_ecto chain B len={len(cd5)}")


if __name__ == "__main__":
    main()

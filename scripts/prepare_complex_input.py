"""Organize CD5 / anti-CD5 VHH inputs for ESMFold2 complex prediction.

Reads a FASTA of input chains (default Test_input/raw_input.fasta): exactly one CD5 record
(header id starting "CD5") plus one or more VHH records. Trims CD5 to its ectodomain
(Arg25-Asn371, 1-indexed inclusive; endpoints validated) and writes:
  Test_input/chains.fasta     clean reference (CD5 ectodomain + VHH chains)
  Test_input/complexes.json   one VHH+CD5 complex per record (chain A = VHH, B = CD5 ecto)

The real input FASTA and the generated files hold internal sequences and are gitignored.
See Test_input/raw_input.example.fasta for the expected format.

Usage: python3 scripts/prepare_complex_input.py [path/to/input.fasta]
"""
import json
import os
import sys

START, END = 25, 371  # CD5 ectodomain: Arg25-Asn371 (1-indexed, inclusive)


def read_fasta(path):
    recs, cur, seq = [], None, []
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(">"):
            if cur is not None:
                recs.append((cur, "".join(seq).upper()))
            cur = line[1:].split()[0]
            seq = []
        else:
            seq.append(line)
    if cur is not None:
        recs.append((cur, "".join(seq).upper()))
    return recs


def main():
    here = os.path.dirname(__file__)
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "Test_input", "raw_input.fasta")
    if not os.path.exists(src):
        sys.exit(f"Input not found: {src}\nProvide your own FASTA (see Test_input/raw_input.example.fasta).")

    recs = read_fasta(src)
    cd5 = [(i, s) for i, s in recs if i.upper().startswith("CD5")]
    vhhs = [(i, s) for i, s in recs if not i.upper().startswith("CD5")]
    if not cd5:
        sys.exit("No CD5 record (header id starting 'CD5') found in input.")
    if not vhhs:
        sys.exit("No VHH records found in input.")

    cd5_full = cd5[0][1]
    cd5_ecto = cd5_full[START - 1:END]
    r25, r371 = cd5_full[START - 1], cd5_full[END - 1]
    print(f"CD5 full: {len(cd5_full)} aa; residue {START}={r25} (expect R), {END}={r371} (expect N)")
    assert r25 == "R" and r371 == "N", "CD5 numbering mismatch — check the input sequence."
    print(f"CD5 ectodomain (Arg25-Asn371): {len(cd5_ecto)} aa  {cd5_ecto[:12]}...{cd5_ecto[-12:]}")

    out = os.path.join(here, "..", "Test_input")
    os.makedirs(out, exist_ok=True)

    def kind_of(name):
        return "control" if "control" in name.lower() else "internal"

    with open(os.path.join(out, "chains.fasta"), "w") as f:
        f.write(f">CD5_ecto Arg25-Asn371 len={len(cd5_ecto)}\n{cd5_ecto}\n")
        for i, s in vhhs:
            f.write(f">{i} {kind_of(i)} VHH len={len(s)}\n{s}\n")

    complexes = [
        {"id": f"{i}__CD5ecto", "kind": kind_of(i),
         "chains": [{"chain_id": "A", "sequence": s},
                    {"chain_id": "B", "sequence": cd5_ecto}]}
        for i, s in vhhs
    ]
    json.dump(complexes, open(os.path.join(out, "complexes.json"), "w"), indent=2)

    print(f"Wrote {len(complexes)} complexes -> Test_input/complexes.json")
    for c in complexes:
        tot = sum(len(ch["sequence"]) for ch in c["chains"])
        print(f"  {c['id']:30s} ({c['kind']:8s}) "
              f"{'+'.join(str(len(ch['sequence'])) for ch in c['chains'])} = {tot} aa")


if __name__ == "__main__":
    main()

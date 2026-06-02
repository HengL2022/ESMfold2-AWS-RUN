#!/usr/bin/env python3
"""Build a self-contained, offline HTML report for the CD5 nanobody folding screen.

Reads the finished local screen results in results/cd5_screen/ (leaderboard.csv,
<id>.metrics.json, <id>.cif) and emits a portable static site under
results/cd5_screen/report/:

  report/
    index.html              leaderboard (all records, client-sortable)
    pages/<id>.html         drill-down for the top-N records (default 20)
    assets/3Dmol-min.js     vendored 3D viewer (committed; offline)
    assets/report.css
    assets/report.js        table sort + 3Dmol viewer controls

Each detail page shows: the full chain-A (VHH) + chain-B (CD5) sequence with CDRs
highlighted, the full metrics.json, three structure-derived matrices (VHH x CD5
interface contact map, full CA-CA distance matrix, per-residue pLDDT track), and an
interactive 3Dmol.js viewer with the CIF inlined so the folder works on file:// with no
network and no local server.

Deps: numpy + jinja2 + stdlib only (PNGs are encoded via stdlib zlib/struct — no
matplotlib/Pillow). The report does NOT touch AWS Batch or the fold/design pipelines; it
only reads existing result files. See the plan at
~/.claude/plans/since-we-have-our-synchronous-sutherland.md
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
from jinja2 import Environment, select_autoescape

ROOT = Path(__file__).resolve().parent.parent

# --- residue 3->1 -----------------------------------------------------------------------------
THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
    "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
    "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
    "TYR": "Y", "VAL": "V", "MSE": "M", "SEC": "U", "PYL": "O",
}

# CDR-anchor regex for highlighting CDR1/2/3 (best-effort; ANARCI isn't available locally).
# Built at runtime from the binder_template in the (gitignored) campaign config, so the framework
# is never hardcoded in the repo. Stays None if no config is supplied → CDR highlighting is skipped.
TEMPLATE_RE = None


def compile_cdr_template(binder_template):
    """Build the CDR-anchor regex from a binder_template marking loops with {hcdr1}/{hcdr2}/{hcdr3}.
    Groups 2,4,6 capture CDR1/CDR2/CDR3."""
    parts = re.split(r"\{hcdr[123]\}", binder_template)
    if len(parts) != 4:
        raise ValueError("binder_template must contain {hcdr1}, {hcdr2}, {hcdr3}")
    anchor = lambda fr: (re.escape(fr[:-1]) + ".") if fr else ""
    return re.compile(
        f"^({anchor(parts[0])})(.+?)({re.escape(parts[1])})(.+?)"
        f"({anchor(parts[2])})(.+?)({re.escape(parts[3])})$"
    )

# viridis anchors (matplotlib), 0..1 -> RGB
_VIRIDIS_P = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
_VIRIDIS_C = np.array([
    [68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142],
    [31, 158, 137], [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37],
    [253, 231, 37],
], dtype=float)

# AlphaFold-style pLDDT bands (here pLDDT is 0..1)
PLDDT_BANDS = [
    (0.90, "#0053D6", "Very high (>0.90)"),
    (0.70, "#65CBF3", "Confident (0.70-0.90)"),
    (0.50, "#FFDB13", "Low (0.50-0.70)"),
    (0.00, "#FF7D45", "Very low (<0.50)"),
]


def plddt_color(v: float) -> str:
    for thr, color, _ in PLDDT_BANDS:
        if v >= thr:
            return color
    return PLDDT_BANDS[-1][1]


# --- CIF parsing ------------------------------------------------------------------------------
def parse_cif(path: Path):
    """Parse the ESMFold2 mmCIF _atom_site loop.

    Returns {chain_order: [..], chains: {cid: [residue,...]}} where each residue is a dict
    with seq_id, resname, aa (1-letter), plddt (0..1), ca (xyz|None), cb (xyz|None, CA for GLY).
    Column indices are fixed by the writer (19-col loop):
      2 label_atom_id, 4 label_comp_id, 5 label_asym_id, 7 label_seq_id,
      13 B_iso_or_equiv, 14/15/16 Cartn_x/y/z.
    """
    chains: dict[str, dict[int, dict]] = {}
    order: list[str] = []
    for line in path.read_text().splitlines():
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        t = line.split()
        if len(t) < 17:
            continue
        atom, resname, chain = t[2], t[4], t[5]
        try:
            seq_id = int(t[7])
            bfac = float(t[13])
            x, y, z = float(t[14]), float(t[15]), float(t[16])
        except ValueError:
            continue
        if chain not in chains:
            chains[chain] = {}
            order.append(chain)
        res = chains[chain].get(seq_id)
        if res is None:
            res = {
                "seq_id": seq_id, "resname": resname,
                "aa": THREE_TO_ONE.get(resname, "X"),
                "plddt": None, "ca": None, "cb": None,
            }
            chains[chain][seq_id] = res
        if atom == "CA":
            res["ca"] = (x, y, z)
            res["plddt"] = bfac / 100.0  # B-factor stores pLDDT on a 0..100 scale
        elif atom == "CB":
            res["cb"] = (x, y, z)

    out_chains = {}
    for cid in order:
        residues = [chains[cid][k] for k in sorted(chains[cid])]
        for r in residues:
            if r["cb"] is None:  # GLY (and any residue missing CB) -> fall back to CA
                r["cb"] = r["ca"]
        out_chains[cid] = residues
    return {"order": order, "chains": out_chains}


# --- PNG + colormap ---------------------------------------------------------------------------
def _png_bytes(rgb: np.ndarray) -> bytes:
    """Encode an (H, W, 3) uint8 array as a PNG (stdlib only)."""
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit, color type 2 (truecolor)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _colorize(norm: np.ndarray) -> np.ndarray:
    """norm in [0,1] (NaN allowed) -> (H, W, 3) uint8 via viridis. NaN -> light gray."""
    flat = norm.ravel()
    nan = np.isnan(flat)
    safe = np.nan_to_num(flat, nan=0.0)
    r = np.interp(safe, _VIRIDIS_P, _VIRIDIS_C[:, 0])
    g = np.interp(safe, _VIRIDIS_P, _VIRIDIS_C[:, 1])
    b = np.interp(safe, _VIRIDIS_P, _VIRIDIS_C[:, 2])
    rgb = np.stack([r, g, b], axis=1)
    rgb[nan] = [225, 225, 225]
    return rgb.reshape(*norm.shape, 3).astype(np.uint8)


def _gradient_css(invert: bool) -> str:
    stops = []
    for i in range(11):
        t = i / 10.0
        src = 1 - t if invert else t
        rr = int(np.interp(src, _VIRIDIS_P, _VIRIDIS_C[:, 0]))
        gg = int(np.interp(src, _VIRIDIS_P, _VIRIDIS_C[:, 1]))
        bb = int(np.interp(src, _VIRIDIS_P, _VIRIDIS_C[:, 2]))
        stops.append(f"rgb({rr},{gg},{bb}) {int(t*100)}%")
    return "linear-gradient(to right, " + ", ".join(stops) + ")"


def heatmap_data_uri(mat: np.ndarray, vmin: float, vmax: float,
                     invert: bool = False, boundary: int | None = None,
                     scale: int = 1) -> str:
    """Render a 2D matrix to a base64 PNG data URI. invert=True -> bright = small value."""
    norm = (mat - vmin) / (vmax - vmin) if vmax > vmin else np.zeros_like(mat)
    norm = np.clip(norm, 0.0, 1.0)
    if invert:
        norm = 1.0 - norm
    rgb = _colorize(norm)
    if boundary is not None and 0 < boundary < rgb.shape[0]:
        rgb[boundary, :, :] = [255, 255, 255]
    if boundary is not None and 0 < boundary < rgb.shape[1]:
        rgb[:, boundary, :] = [255, 255, 255]
    if scale > 1:
        rgb = np.repeat(np.repeat(rgb, scale, axis=0), scale, axis=1)
    return "data:image/png;base64," + base64.b64encode(_png_bytes(rgb)).decode("ascii")


# --- geometry / matrices ----------------------------------------------------------------------
def coords_array(residues, key):
    return np.array([r[key] if r[key] is not None else (np.nan, np.nan, np.nan)
                     for r in residues], dtype=float)


def pairwise_dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    diff = a[:, None, :] - b[None, :, :]
    return np.sqrt((diff * diff).sum(-1))


# --- sequence / CDR annotation ----------------------------------------------------------------
def cdr_spans(seq: str):
    """Return [(start, end_exclusive, label), ...] for CDR1/2/3, or [] if no template match."""
    if TEMPLATE_RE is None:
        return []
    m = TEMPLATE_RE.match(seq)
    if not m:
        return []
    spans, pos = [], 0
    for gi, label in [(1, None), (2, "CDR1"), (3, None), (4, "CDR2"), (5, None), (6, "CDR3")]:
        g = m.group(gi)
        if label:
            spans.append((pos, pos + len(g), label))
        pos += len(g)
    return spans


def seq_segments(seq: str, spans):
    """Split a sequence into [(text, css_class)] runs for templated rendering."""
    label_at = {}
    for s, e, lab in spans:
        for i in range(s, e):
            label_at[i] = lab
    segments, cur, cur_lab = [], [], label_at.get(0)
    for i, ch in enumerate(seq):
        lab = label_at.get(i)
        if lab != cur_lab:
            segments.append(("".join(cur), cur_lab))
            cur, cur_lab = [], lab
        cur.append(ch)
    if cur:
        segments.append(("".join(cur), cur_lab))
    return [{"text": t, "cls": ("cdr " + lab.lower()) if lab else ""} for t, lab in segments if t]


def read_fasta(path: Path):
    records, rid, seq, header = [], None, [], ""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if rid is not None:
                records.append((rid, "".join(seq).upper(), header))
            parts = line[1:].split(None, 1)
            rid, header, seq = parts[0], (parts[1] if len(parts) > 1 else ""), []
        else:
            seq.append(line)
    if rid is not None:
        records.append((rid, "".join(seq).upper(), header))
    return records


# --- templates --------------------------------------------------------------------------------
BASE_CSS = """
:root{--bg:#0f1320;--panel:#171c2e;--ink:#e8ecf5;--muted:#9aa4bf;--line:#2a3147;
--pos:#2ecc71;--neg:#e74c3c;--accent:#5b8cff;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header.top{padding:20px 28px;border-bottom:1px solid var(--line);background:var(--panel)}
header.top h1{margin:0 0 4px;font-size:20px}
.sub{color:var(--muted);font-size:13px}
.wrap{max-width:1200px;margin:0 auto;padding:24px 28px}
.verdict{display:inline-block;padding:6px 12px;border-radius:8px;font-weight:600;margin:8px 0}
.verdict.ok{background:rgba(46,204,113,.15);color:var(--pos);border:1px solid var(--pos)}
.verdict.weak{background:rgba(231,76,60,.15);color:var(--neg);border:1px solid var(--neg)}
table.lb{width:100%;border-collapse:collapse;margin-top:14px;font-size:13px}
table.lb th,table.lb td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
table.lb th:nth-child(2),table.lb td:nth-child(2){text-align:left}
table.lb th{cursor:pointer;user-select:none;color:var(--muted);position:sticky;top:0;background:var(--panel)}
table.lb th:hover{color:var(--ink)}
table.lb tr.pos{background:rgba(46,204,113,.08)}
table.lb tr.neg{background:rgba(231,76,60,.08)}
table.lb tr.top:hover{background:rgba(91,140,255,.12)}
.badge{display:inline-block;padding:1px 7px;border-radius:6px;font-size:11px;font-weight:700}
.badge.pos{background:var(--pos);color:#06251a}.badge.neg{background:var(--neg);color:#2a0a06}
.bar{position:relative;display:inline-block;width:90px;height:10px;background:var(--line);border-radius:5px;vertical-align:middle;margin-right:6px}
.bar>i{position:absolute;left:0;top:0;height:100%;border-radius:5px;background:var(--accent)}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 16px;min-width:120px}
.card .k{color:var(--muted);font-size:12px}.card .v{font-size:22px;font-weight:700;margin-top:2px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;margin:18px 0}
.panel h2{margin:0 0 12px;font-size:16px}
.seq{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;line-height:1.7;word-break:break-all}
.seq .cdr{border-radius:3px;padding:1px 0;font-weight:700}
.seq .cdr.cdr1{background:#3a6ea5;color:#fff}.seq .cdr.cdr2{background:#a55a3a;color:#fff}.seq .cdr.cdr3{background:#7d3aa5;color:#fff}
.legend{color:var(--muted);font-size:12px;margin-top:8px}
.legend .sw{display:inline-block;width:11px;height:11px;border-radius:3px;vertical-align:middle;margin:0 4px 0 12px}
.hm{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start}
.hm img{image-rendering:pixelated;border:1px solid var(--line);border-radius:6px;background:#000}
.cbar{width:160px;height:12px;border-radius:6px;border:1px solid var(--line)}
.cbar-row{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;width:160px;margin-top:3px}
.mtable{border-collapse:collapse;font-size:13px}
.mtable td{padding:4px 12px 4px 0;border-bottom:1px solid var(--line)}
.mtable td.k{color:var(--muted)}
#viewer{width:100%;height:460px;position:relative;border:1px solid var(--line);border-radius:10px;background:#0a0d16}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.controls button{background:#222a44;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 11px;cursor:pointer;font-size:12px}
.controls button:hover{background:#2c3760}
.controls .grp{display:flex;gap:6px;align-items:center;background:#10142340;border:1px solid var(--line);border-radius:8px;padding:4px 8px}
.controls .grp span{color:var(--muted);font-size:11px}
.nav{display:flex;justify-content:space-between;margin:18px 0}
.collapsible summary{cursor:pointer;color:var(--muted)}
.iflist{font-size:12px;color:var(--muted);max-height:120px;overflow:auto;font-family:ui-monospace,monospace}
"""

INDEX_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CD5 nanobody screen — leaderboard</title>
<link rel="stylesheet" href="assets/report.css"></head><body>
<header class="top"><h1>CD5 nanobody folding screen</h1>
<div class="sub">{{ n_records }} records · model {{ model }} ·
num_loops={{ params.num_loops }}, num_sampling_steps={{ params.num_sampling_steps }},
num_diffusion_samples={{ params.num_diffusion_samples }}, seed={{ params.seed }}</div></header>
<div class="wrap">
<div class="verdict {{ 'ok' if gate.ok else 'weak' }}">
Calibration gate: {{ 'PASS' if gate.ok else 'WEAK' }} —
POS iptm {{ '%.3f'|format(gate.pos) }} vs best NEG iptm {{ '%.3f'|format(gate.neg) }}</div>
<div class="panel"><h2>iPTM distribution</h2>{{ strip_svg|safe }}
<div class="legend"><span class="sw" style="background:var(--pos)"></span>positive control
<span class="sw" style="background:var(--neg)"></span>negative controls
<span class="sw" style="background:var(--accent)"></span>candidate</div></div>
<table class="lb" id="lb"><thead><tr>
<th data-k="rank" data-t="n">rank</th><th data-k="id" data-t="s">id</th>
<th data-k="ctrl" data-t="s">type</th><th data-k="iptm" data-t="n">iptm</th>
<th data-k="iptm_mean" data-t="n">iptm_mean</th><th data-k="iptm_max" data-t="n">iptm_max</th>
<th data-k="ptm" data-t="n">ptm</th><th data-k="plddt_mean" data-t="n">plddt_mean</th>
<th data-k="total_length" data-t="n">len</th></tr></thead><tbody>
{% for r in rows %}<tr class="{{ r.cls }}"
 data-rank="{{ r.rank }}" data-id="{{ r.id }}" data-ctrl="{{ r.ctrl }}"
 data-iptm="{{ r.iptm }}" data-iptm_mean="{{ r.iptm_mean }}" data-iptm_max="{{ r.iptm_max }}"
 data-ptm="{{ r.ptm }}" data-plddt_mean="{{ r.plddt_mean }}" data-total_length="{{ r.total_length }}">
<td>{{ r.rank }}</td>
<td>{% if r.href %}<a href="{{ r.href }}">{{ r.id }}</a>{% else %}{{ r.id }}{% endif %}</td>
<td>{% if r.ctrl %}<span class="badge {{ r.ctrl|lower }}">{{ r.ctrl }}</span>{% endif %}</td>
<td><span class="bar"><i style="width:{{ (r.iptm*100)|round(0,'floor') }}%"></i></span>{{ '%.3f'|format(r.iptm) }}</td>
<td>{{ '%.3f'|format(r.iptm_mean) }}</td><td>{{ '%.3f'|format(r.iptm_max) }}</td>
<td>{{ '%.3f'|format(r.ptm) }}</td><td>{{ '%.3f'|format(r.plddt_mean) }}</td>
<td>{{ r.total_length }}</td></tr>
{% endfor %}</tbody></table>
<p class="sub">Top {{ top_n }} (by iptm) have detail pages. Click a column header to sort.
iPTM is a confidence proxy, not a Kd — high iPTM on CD5 ≠ confirmed binder; wet-lab validate.</p>
</div><script src="assets/report.js"></script></body></html>
"""

PAGE_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ id }} — CD5 screen</title>
<link rel="stylesheet" href="../assets/report.css"></head><body>
<header class="top"><h1>#{{ rank }} · {{ id }}
{% if ctrl %}<span class="badge {{ ctrl|lower }}">{{ ctrl }}</span>{% endif %}</h1>
<div class="sub">VHH (chain A, {{ lenA }} aa) × CD5 ectodomain (chain B, {{ lenB }} aa) ·
model {{ model }}</div></header>
<div class="wrap">
<div class="nav"><a href="../index.html">← leaderboard</a><span>
{% if prev %}<a href="{{ prev }}">← prev</a>{% endif %}
{% if next %}&nbsp;&nbsp;<a href="{{ next }}">next →</a>{% endif %}</span></div>

<div class="cards">
<div class="card"><div class="k">rank (iptm)</div><div class="v">#{{ rank }}</div></div>
<div class="card"><div class="k">iptm</div><div class="v">{{ '%.3f'|format(meta.iptm) }}</div></div>
<div class="card"><div class="k">ptm</div><div class="v">{{ '%.3f'|format(meta.ptm) }}</div></div>
<div class="card"><div class="k">pLDDT (mean)</div><div class="v">{{ '%.3f'|format(meta.plddt_mean) }}</div></div>
<div class="card"><div class="k">iptm mean / max</div><div class="v" style="font-size:16px">{{ '%.3f'|format(meta.iptm_mean) }} / {{ '%.3f'|format(meta.iptm_max) }}</div></div>
</div>

<div class="panel"><h2>3D structure</h2>
<div class="controls">
<div class="grp"><span>style</span>
<button data-style="cartoon">cartoon</button><button data-style="stick">stick</button>
<button data-style="sphere">sphere</button><button data-style="surface">surface</button></div>
<div class="grp"><span>color</span>
<button data-color="chain">chain</button><button data-color="plddt">pLDDT</button>
<button data-color="spectrum">spectrum</button></div>
<div class="grp"><span>interface</span><button id="btn-iface">highlight</button></div>
<div class="grp"><span>view</span>
<button id="btn-zoomin">zoom +</button><button id="btn-zoomout">zoom −</button>
<button id="btn-spin">spin</button><button id="btn-reset">reset</button>
<button id="btn-bg">bg</button></div></div>
<div id="viewer" data-iface-a="{{ iface_a_json }}" data-iface-b="{{ iface_b_json }}"></div>
<div class="legend">Drag to rotate · scroll to zoom · pLDDT colors:
<span class="sw" style="background:#0053D6"></span>very high
<span class="sw" style="background:#65CBF3"></span>confident
<span class="sw" style="background:#FFDB13"></span>low
<span class="sw" style="background:#FF7D45"></span>very low</div></div>

<div class="panel"><h2>Sequence</h2>
<div class="sub" style="margin-bottom:6px">Chain A — VHH ({{ lenA }} aa){% if not has_cdr %} · CDR template not matched, CDRs unmarked{% endif %}</div>
<div class="seq">{% for s in seqA %}<span class="{{ s.cls }}">{{ s.text }}</span>{% endfor %}</div>
<div class="legend"><span class="sw" style="background:#3a6ea5"></span>CDR1
<span class="sw" style="background:#a55a3a"></span>CDR2
<span class="sw" style="background:#7d3aa5"></span>CDR3</div>
<details class="collapsible" style="margin-top:14px"><summary>Chain B — CD5 ectodomain ({{ lenB }} aa)</summary>
<div class="seq" style="margin-top:8px">{{ seqB }}</div></details></div>

<div class="panel"><h2>Interface contact map — VHH (rows) × CD5 (cols)</h2>
<div class="hm"><img src="{{ contact_png }}" width="{{ contact_w }}" alt="contact map">
<div><div class="cbar" style="background:{{ grad_inv }}"></div>
<div class="cbar-row"><span>{{ contact_vmax }} Å</span><span>0 Å</span></div>
<div class="legend" style="margin-top:10px">Bright = close (predicted contact, &lt; {{ thr }} Å).
{{ n_contacts }} residue pairs &lt; {{ thr }} Å.</div>
<div style="margin-top:10px" class="legend">VHH interface residues:</div>
<div class="iflist">{{ iface_a_txt }}</div>
<div style="margin-top:6px" class="legend">CD5 epitope residues:</div>
<div class="iflist">{{ iface_b_txt }}</div></div></div></div>

<div class="panel"><h2>Full distance matrix — CA–CA, all {{ total }} residues</h2>
<div class="hm"><img src="{{ dist_png }}" width="{{ dist_w }}" alt="distance matrix">
<div><div class="cbar" style="background:{{ grad_inv }}"></div>
<div class="cbar-row"><span>{{ dist_vmax }} Å</span><span>0 Å</span></div>
<div class="legend" style="margin-top:10px">White lines mark the chain A | B boundary.</div></div></div></div>

<div class="panel"><h2>Per-residue pLDDT</h2>{{ plddt_svg|safe }}
<div class="legend">Chain boundary dashed; CDR spans tinted.
<span class="sw" style="background:#0053D6"></span>&gt;0.90
<span class="sw" style="background:#65CBF3"></span>0.70–0.90
<span class="sw" style="background:#FFDB13"></span>0.50–0.70
<span class="sw" style="background:#FF7D45"></span>&lt;0.50</div></div>

<div class="panel"><h2>Metrics (metrics.json)</h2>
<table class="mtable">{% for k,v in meta_rows %}<tr><td class="k">{{ k }}</td><td>{{ v }}</td></tr>{% endfor %}</table></div>

<div class="nav"><a href="../index.html">← leaderboard</a><span>
{% if prev %}<a href="{{ prev }}">← prev</a>{% endif %}
{% if next %}&nbsp;&nbsp;<a href="{{ next }}">next →</a>{% endif %}</span></div>
</div>
<script src="../assets/3Dmol-min.js"></script>
<script>window.CIFDATA = {{ cif_text|tojson }};</script>
<script src="../assets/report.js"></script></body></html>
"""

REPORT_JS = r"""
// ---- leaderboard sort (index.html) ----
(function(){
  var tbl = document.getElementById('lb');
  if(!tbl) return;
  var dir = {};
  tbl.querySelectorAll('th').forEach(function(th){
    th.addEventListener('click', function(){
      var k = th.dataset.k, t = th.dataset.t;
      var tb = tbl.querySelector('tbody');
      var rows = Array.prototype.slice.call(tb.querySelectorAll('tr'));
      var d = dir[k] = !dir[k];
      rows.sort(function(a,b){
        var x = a.dataset[k], y = b.dataset[k];
        if(t==='n'){ x = parseFloat(x); y = parseFloat(y); }
        if(x<y) return d?-1:1; if(x>y) return d?1:-1; return 0;
      });
      rows.forEach(function(r){ tb.appendChild(r); });
    });
  });
})();

// ---- 3D viewer (detail pages) ----
(function(){
  var host = document.getElementById('viewer');
  if(!host || !window.CIFDATA) return;
  function notice(msg){ host.innerHTML = '<p style="padding:16px;color:#9aa4bf;font:14px sans-serif">'+msg+'</p>'; }
  var $3D = window.$3Dmol || window['3Dmol'];
  if(!$3D){ notice('3Dmol.js failed to load — check assets/3Dmol-min.js.'); return; }
  // WebGL is required; some headless/locked-down browsers lack it.
  try {
    var probe = document.createElement('canvas');
    if(!(probe.getContext('webgl') || probe.getContext('experimental-webgl'))){
      notice('This browser has no WebGL, so the 3D structure can\'t render here. Open the report in a normal desktop browser (Chrome/Firefox/Safari) to view it.');
      return;
    }
  } catch(e){ notice('WebGL unavailable — open in a desktop browser to view the 3D structure.'); return; }
  var viewer;
  try {
    viewer = $3D.createViewer(host, {backgroundColor:'#0a0d16'});
    viewer.addModel(window.CIFDATA, 'cif');
  } catch(e){ notice('3D viewer failed to initialise: '+e.message); return; }
  var ifaceA = JSON.parse(host.dataset.ifaceA || '[]');
  var ifaceB = JSON.parse(host.dataset.ifaceB || '[]');
  var bg = '#0a0d16';
  var state = {style:'cartoon', color:'chain', iface:false};

  function apply(){
    viewer.setStyle({}, {});
    var s = {};
    var base = {};
    if(state.color==='plddt'){ base = {colorscheme:{prop:'b',gradient:'roygb',min:50,max:95}}; }
    else if(state.color==='spectrum'){ base = {color:'spectrum'}; }
    if(state.style==='cartoon'){
      if(state.color==='chain'){
        viewer.setStyle({chain:'A'},{cartoon:{color:'#5b8cff'}});
        viewer.setStyle({chain:'B'},{cartoon:{color:'#9aa4bf'}});
      } else { viewer.setStyle({}, {cartoon:Object.assign({},base)}); }
    } else if(state.style==='stick'){
      viewer.setStyle({}, {stick:Object.assign({radius:0.15},base)});
    } else if(state.style==='sphere'){
      viewer.setStyle({}, {sphere:Object.assign({scale:0.3},base)});
    } else if(state.style==='surface'){
      viewer.setStyle({}, {cartoon:Object.assign({},base)});
    }
    viewer.removeAllSurfaces();
    if(state.style==='surface'){
      viewer.addSurface($3D.SurfaceType.VDW, {opacity:0.85,
        colorscheme: state.color==='plddt' ? {prop:'b',gradient:'roygb',min:50,max:95} : undefined,
        color: state.color==='chain' ? undefined : (state.color==='spectrum'? undefined : '#6f7aa0')}, {});
    }
    if(state.iface){
      viewer.addStyle({chain:'A', resi:ifaceA}, {stick:{radius:0.3, color:'#2ecc71'}});
      viewer.addStyle({chain:'B', resi:ifaceB}, {stick:{radius:0.3, color:'#f1c40f'}});
    }
    viewer.render();
  }
  document.querySelectorAll('button[data-style]').forEach(function(b){
    b.onclick=function(){ state.style=b.dataset.style; apply(); };
  });
  document.querySelectorAll('button[data-color]').forEach(function(b){
    b.onclick=function(){ state.color=b.dataset.color; apply(); };
  });
  document.getElementById('btn-iface').onclick=function(){ state.iface=!state.iface; apply(); };
  document.getElementById('btn-zoomin').onclick=function(){ viewer.zoom(1.2,300); };
  document.getElementById('btn-zoomout').onclick=function(){ viewer.zoom(0.8,300); };
  var spinning=false;
  document.getElementById('btn-spin').onclick=function(){ spinning=!spinning; viewer.spin(spinning?'y':false); };
  document.getElementById('btn-reset').onclick=function(){ viewer.zoomTo(); viewer.render(); };
  document.getElementById('btn-bg').onclick=function(){ bg = (bg==='#0a0d16')?'#ffffff':'#0a0d16'; viewer.setBackgroundColor(bg); };
  apply();
  viewer.zoomTo();
  viewer.render();
})();
"""


# --- pLDDT SVG --------------------------------------------------------------------------------
def plddt_svg(plddt, lenA, spans, width=900, height=70):
    n = len(plddt)
    if n == 0:
        return "<svg></svg>"
    bw = width / n
    pad_b = 16
    h = height - pad_b
    bars = []
    for i, v in enumerate(plddt):
        vv = 0.0 if v is None or np.isnan(v) else float(v)
        bh = max(1.0, vv * h)
        bars.append(
            f'<rect x="{i*bw:.2f}" y="{h-bh:.2f}" width="{bw+0.6:.2f}" height="{bh:.2f}" '
            f'fill="{plddt_color(vv)}"/>'
        )
    # CDR span tints (chain A coordinate == residue index)
    tints = []
    for s, e, lab in spans:
        col = {"CDR1": "#3a6ea5", "CDR2": "#a55a3a", "CDR3": "#7d3aa5"}[lab]
        tints.append(f'<rect x="{s*bw:.2f}" y="0" width="{(e-s)*bw:.2f}" height="{h:.2f}" '
                     f'fill="{col}" opacity="0.18"/>')
        tints.append(f'<text x="{((s+e)/2)*bw:.2f}" y="{h+12:.0f}" fill="#9aa4bf" '
                     f'font-size="10" text-anchor="middle">{lab}</text>')
    boundary = (f'<line x1="{lenA*bw:.2f}" y1="0" x2="{lenA*bw:.2f}" y2="{h:.2f}" '
                f'stroke="#e8ecf5" stroke-dasharray="3,3" stroke-width="1"/>'
                f'<text x="{lenA*bw:.2f}" y="{h+12:.0f}" fill="#9aa4bf" font-size="10" '
                f'text-anchor="middle">A|B</text>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'preserveAspectRatio="none" style="background:#0a0d16;border:1px solid #2a3147;'
            f'border-radius:6px">{"".join(tints)}{"".join(bars)}{boundary}</svg>')


def strip_svg(rows, width=1100, height=120):
    """iPTM strip plot: each record a dot positioned by iptm; controls highlighted."""
    vals = [r["iptm"] for r in rows]
    vmin, vmax = min(vals), max(vals)
    span = (vmax - vmin) or 1.0
    pad = 40
    w = width - 2 * pad
    dots = []
    for r in rows:
        x = pad + (r["iptm"] - vmin) / span * w
        if r["ctrl"] == "POS":
            col, rad = "#2ecc71", 6
        elif r["ctrl"] == "NEG":
            col, rad = "#e74c3c", 6
        else:
            col, rad = "#5b8cff", 4
        y = 60
        dots.append(f'<circle cx="{x:.1f}" cy="{y}" r="{rad}" fill="{col}" '
                    f'fill-opacity="0.8"><title>{r["id"]} iptm={r["iptm"]:.3f}</title></circle>')
    ticks = []
    for f in range(0, 6):
        v = vmin + span * f / 5
        x = pad + f / 5 * w
        ticks.append(f'<line x1="{x:.1f}" y1="80" x2="{x:.1f}" y2="86" stroke="#9aa4bf"/>'
                     f'<text x="{x:.1f}" y="100" fill="#9aa4bf" font-size="11" '
                     f'text-anchor="middle">{v:.2f}</text>')
    axis = f'<line x1="{pad}" y1="80" x2="{width-pad}" y2="80" stroke="#2a3147"/>'
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}">'
            f'{axis}{"".join(ticks)}{"".join(dots)}</svg>')


# --- main -------------------------------------------------------------------------------------
def fnum(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def build(results_dir: Path, out_dir: Path, top_n: int, thr: float, candidates_fasta: Path):
    lb_path = results_dir / "leaderboard.csv"
    if not lb_path.exists():
        sys.exit(f"leaderboard not found: {lb_path}")
    with lb_path.open() as fh:
        lb = list(csv.DictReader(fh))
    if not lb:
        sys.exit("leaderboard.csv is empty")

    # candidate FASTA metadata (optional)
    fasta_meta = {}
    if candidates_fasta.exists():
        for rid, _seq, header in read_fasta(candidates_fasta):
            fasta_meta[rid] = header

    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    env.filters  # touch

    # control-gate verdict
    pos = max((fnum(r["iptm"]) for r in lb if r.get("is_control") == "POS"), default=0.0)
    neg = max((fnum(r["iptm"]) for r in lb if r.get("is_control") == "NEG"), default=0.0)
    gate = {"pos": pos, "neg": neg, "ok": pos > neg}

    params = {}
    rows = []
    for r in lb:
        rank = int(r["rank"])
        ctrl = r.get("is_control") or ""
        rows.append({
            "rank": rank, "id": r["id"], "ctrl": ctrl,
            "iptm": fnum(r["iptm"]), "iptm_mean": fnum(r["iptm_mean"]),
            "iptm_max": fnum(r["iptm_max"]), "ptm": fnum(r["ptm"]),
            "plddt_mean": fnum(r["plddt_mean"]), "total_length": int(fnum(r["total_length"])),
            "cls": ("pos" if ctrl == "POS" else "neg" if ctrl == "NEG" else "")
                   + (" top" if rank <= top_n else ""),
            "href": f"pages/{r['id']}.html" if rank <= top_n else None,
        })

    top = sorted([r for r in rows if r["rank"] <= top_n], key=lambda r: r["rank"])
    top_ids = [r["id"] for r in top]

    # assets
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    (out_dir / "pages").mkdir(parents=True, exist_ok=True)
    (out_dir / "assets" / "report.css").write_text(BASE_CSS)
    (out_dir / "assets" / "report.js").write_text(REPORT_JS)
    if not (out_dir / "assets" / "3Dmol-min.js").exists():
        print("WARNING: assets/3Dmol-min.js missing — the 3D viewer won't load offline.",
              file=sys.stderr)

    # model/params from the first available metrics.json
    page_tmpl = env.from_string(PAGE_HTML)
    n_pages = 0
    for r in top:
        rid = r["id"]
        cif_path = results_dir / f"{rid}.cif"
        met_path = results_dir / f"{rid}.metrics.json"
        if not cif_path.exists() or not met_path.exists():
            print(f"WARNING: missing CIF/metrics for {rid} — skipping detail page.", file=sys.stderr)
            continue
        meta = json.loads(met_path.read_text())
        if not params:
            params = {k: meta.get(k) for k in
                      ("num_loops", "num_sampling_steps", "num_diffusion_samples", "seed")}

        cif_text = cif_path.read_text()
        parsed = parse_cif(cif_path)
        order = parsed["order"]
        chainA = parsed["chains"][order[0]]
        chainB = parsed["chains"][order[1]] if len(order) > 1 else []
        seqA = "".join(x["aa"] for x in chainA)
        seqB = "".join(x["aa"] for x in chainB)
        lenA, lenB = len(chainA), len(chainB)

        # sanity: CIF chain lengths vs metrics.json
        mchains = meta.get("chains", {})
        if mchains:
            exp_a = mchains.get(order[0])
            exp_b = mchains.get(order[1]) if len(order) > 1 else None
            assert exp_a in (None, lenA), f"{rid}: chain {order[0]} len {lenA} != metrics {exp_a}"
            assert exp_b in (None, lenB), f"{rid}: chain {order[1]} len {lenB} != metrics {exp_b}"

        # matrices
        cbA, cbB = coords_array(chainA, "cb"), coords_array(chainB, "cb")
        contact = pairwise_dist(cbA, cbB)  # (lenA, lenB)
        assert contact.shape == (lenA, lenB), f"{rid}: contact shape {contact.shape}"
        ca_all = coords_array(chainA + chainB, "ca")
        dist = pairwise_dist(ca_all, ca_all)  # (N, N)

        # interface residues
        a_min = np.nanmin(contact, axis=1) if lenB else np.full(lenA, np.inf)
        b_min = np.nanmin(contact, axis=0) if lenB else np.full(0, np.inf)
        iface_a_idx = [i for i in range(lenA) if a_min[i] < thr]
        iface_b_idx = [j for j in range(lenB) if b_min[j] < thr]
        n_contacts = int((contact < thr).sum())
        iface_a_resi = [chainA[i]["seq_id"] for i in iface_a_idx]
        iface_b_resi = [chainB[j]["seq_id"] for j in iface_b_idx]
        iface_a_txt = " ".join(f"{seqA[i]}{chainA[i]['seq_id']}" for i in iface_a_idx) or "(none < %.0f Å)" % thr
        iface_b_txt = " ".join(f"{seqB[j]}{chainB[j]['seq_id']}" for j in iface_b_idx) or "(none < %.0f Å)" % thr

        # heatmaps
        contact_vmax = 20.0
        sc = 2 if max(lenA, lenB) < 200 else 1
        contact_png = heatmap_data_uri(np.nan_to_num(contact, nan=contact_vmax),
                                       0.0, contact_vmax, invert=True, scale=sc)
        dist_vmax = float(min(64.0, np.nanmax(dist))) if dist.size else 1.0
        dist_png = heatmap_data_uri(np.nan_to_num(dist, nan=dist_vmax),
                                    0.0, dist_vmax, invert=True, boundary=lenA)

        # sequence segments / CDRs
        spans = cdr_spans(seqA)
        segA = seq_segments(seqA, spans)
        plddt = [x["plddt"] for x in (chainA + chainB)]

        # metrics table
        meta_rows = [(k, json.dumps(v) if isinstance(v, (dict, list)) else v)
                     for k, v in meta.items()]

        idx = top_ids.index(rid)
        prev_id = top_ids[idx - 1] if idx > 0 else None
        next_id = top_ids[idx + 1] if idx < len(top_ids) - 1 else None

        html = page_tmpl.render(
            id=rid, rank=r["rank"], ctrl=r["ctrl"], meta=r, model=meta.get("model"),
            lenA=lenA, lenB=lenB,
            total=lenA + lenB, meta_rows=meta_rows,
            seqA=segA, seqB=seqB, has_cdr=bool(spans),
            contact_png=contact_png, contact_w=contact.shape[1] * sc, contact_vmax=int(contact_vmax),
            dist_png=dist_png, dist_w=dist.shape[1], dist_vmax=int(dist_vmax),
            grad_inv=_gradient_css(invert=True), thr=int(thr), n_contacts=n_contacts,
            iface_a_txt=iface_a_txt, iface_b_txt=iface_b_txt,
            iface_a_json=json.dumps(iface_a_resi), iface_b_json=json.dumps(iface_b_resi),
            plddt_svg=plddt_svg(plddt, lenA, spans),
            prev=f"{prev_id}.html" if prev_id else None,
            next=f"{next_id}.html" if next_id else None,
            cif_text=cif_text,
        )
        (out_dir / "pages" / f"{rid}.html").write_text(html)
        n_pages += 1

    # index
    index_html = env.from_string(INDEX_HTML).render(
        rows=rows, n_records=len(rows), top_n=top_n,
        model=(json.loads((results_dir / f"{top_ids[0]}.metrics.json").read_text()).get("model")
               if top_ids else "?"),
        params={k: params.get(k, "?") for k in
                ("num_loops", "num_sampling_steps", "num_diffusion_samples", "seed")},
        gate=gate, strip_svg=strip_svg(rows),
    )
    (out_dir / "index.html").write_text(index_html)

    print(f"Report written -> {out_dir}")
    print(f"  records: {len(rows)} · detail pages: {n_pages}/{len(top)} (top {top_n})")
    print(f"  gate: {'PASS' if gate['ok'] else 'WEAK'} (POS {gate['pos']:.3f} vs NEG {gate['neg']:.3f})")
    print(f"  open: {out_dir / 'index.html'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-dir", default=str(ROOT / "results" / "cd5_screen"))
    ap.add_argument("--out", default=str(ROOT / "results" / "cd5_screen" / "report"))
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--contact-threshold", type=float, default=8.0)
    ap.add_argument("--candidates-fasta",
                    default=str(ROOT / "Test_input" / "cd5_50_candidates.fasta"))
    ap.add_argument("--binder-template-config",
                    default=str(ROOT / "Test_input" / "cd5_vhh_v1_config.json"),
                    help="Campaign config (gitignored) holding 'binder_template'; used only to locate "
                         "CDRs for highlighting. If absent, CDRs are simply not highlighted.")
    a = ap.parse_args()
    cfg = Path(a.binder_template_config)
    if cfg.exists():
        tmpl = json.loads(cfg.read_text()).get("binder_template")
        if tmpl:
            global TEMPLATE_RE
            TEMPLATE_RE = compile_cdr_template(tmpl)
    build(Path(a.results_dir), Path(a.out), a.top_n, a.contact_threshold,
          Path(a.candidates_fasta))


if __name__ == "__main__":
    main()

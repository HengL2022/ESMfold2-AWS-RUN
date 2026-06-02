---
name: report-preflight
description: Regenerate the CD5 screen HTML report and QA it — run scripts/build_cd5_report.py, check the sanity asserts and output structure, then drive /browse QA on the leaderboard + a detail page (3D viewer, sort, offline). Read-only except for regenerating results/cd5_screen/report/. Reports a go/no-go checklist.
disable-model-invocation: true
---

# Report preflight — CD5 screen HTML visualization

Regenerate the report and verify it end-to-end. The only writes allowed are regenerating
`results/cd5_screen/report/`; do NOT touch the screen inputs, the fold/design pipelines, or AWS.
Per global CLAUDE.md, all browser QA uses the **`/browse`** gstack skill — never a browser MCP.

Run each step and mark ✅ / ❌ / ⚠️:

1. **Generator runs clean** — `python3 scripts/build_cd5_report.py` (optionally
   `--results-dir results/cd5_screen --out results/cd5_screen/report --top-n 20`). ❌ on any
   traceback. Capture its printed summary (record count, top-N, output path) and its sanity
   asserts (53 leaderboard rows; per-page CIF chain lengths == `metrics.json.chains`; contact-map
   shape == (lenA, lenB)).

2. **Output structure** — confirm `results/cd5_screen/report/` has `index.html`, exactly
   `--top-n` files under `pages/`, and `assets/` with `3Dmol-min.js`, `report.css`, `report.js`.
   ❌ if any are missing or `pages/` count ≠ top-N.

3. **Offline portability** — `grep -rEni '(src|href)[[:space:]]*=[[:space:]]*["'"'"']https?://'
   results/cd5_screen/report` returns nothing (assets vendored, CIFs inlined). ⚠️/❌ on any
   off-host reference or absolute `/Users/...` path. Confirm `assets/3Dmol-min.js` is a real local
   file (non-trivial size), not a CDN redirect stub.

4. **Browser QA via `/browse`** — open `results/cd5_screen/report/index.html` on `file://`:
   - leaderboard renders all 53 rows; column sort works; POS (green) / NEG (red) badges correct;
     top-20 rows link to existing detail pages;
   - open a detail page (e.g. `pages/VHHCluster_022__CD5ecto.html`): full sequence with CDR
     highlights, all three matrices (interface contact map, full distance matrix, pLDDT track),
     and the **3D viewer loads the structure offline**;
   - exercise the controls: zoom ±, rotate (drag), style toggle (cartoon→surface), color-by-pLDDT,
     interface highlight, spin, reset — each should update the view. Screenshot before/after.

5. **No network needed** — the report opens and the 3D viewer works with no internet and no local
   server (file:// double-click). ⚠️ if anything silently requires a server.

End with a one-line verdict: **REPORT READY** only if 1–5 all pass, otherwise list the exact
blocking issues and the fix for each.

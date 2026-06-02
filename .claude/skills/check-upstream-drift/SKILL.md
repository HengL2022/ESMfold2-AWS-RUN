---
name: check-upstream-drift
description: Diff our vendored design/binder_design.py against the official upstream (Biohub/esm) at the pinned SHA and at main — to confirm our local diffs are only the sanctioned ones and to detect when upstream has moved. Read-only.
disable-model-invocation: true
---

# check-upstream-drift — keep the vendored binder_design.py honest

`design/binder_design.py` is VENDORED from the official `cookbook/tutorials/binder_design.py`
(Biohub/esm). This skill verifies two things, read-only: (a) our local copy diverges from the pinned
upstream ONLY in the sanctioned spots, and (b) whether upstream `main` has advanced past our pin.

Pinned SHA: **`f652b471d29da828b31e9b7a9cf7d0a7803240f5`** (the fork is `HengL2022/esm`; upstream is
`Biohub/esm`). Local file: `design/binder_design.py`.

Steps:

1. **Fetch the pinned upstream** and diff against our copy:
   ```bash
   PIN=f652b471d29da828b31e9b7a9cf7d0a7803240f5
   curl -fsSL "https://raw.githubusercontent.com/HengL2022/esm/$PIN/cookbook/tutorials/binder_design.py" -o /tmp/upstream_binder_design.py
   diff -u /tmp/upstream_binder_design.py "design/binder_design.py" | sed -n '1,400p'
   ```

2. **Classify each hunk.** Expected (sanctioned) diffs only:
   - Removed Modal bits: `import modal`, `get_base_image`, `app = modal.App(...)`, `ESMFold2DesignModal`,
     `@app.local_entrypoint() def main(...)`, the `if __name__ == "__main__"` block.
   - `REUSE_ESMC = True` (was `False`).
   (The binder framework is NOT a committed prompt — it is supplied at runtime from the campaign
   config via `run_esmfold2_design.py`, so `BINDER_PROMPT_FACTORIES` should match upstream.)
   Flag ANY diff touching `design_binder`, the loss functions, `compute_esmc_pseudoperplexity_nll`,
   `compute_distogram_iptm_proxy`, `_cdr_indices`, `normalized_gradient_tensor`, `fold_and_get_distogram`,
   or the design constants — that would change the gating/ranking, which must stay faithful.

3. **Check whether upstream moved:**
   ```bash
   gh api repos/HengL2022/esm/commits/main --jq '.sha'   # compare to the pin
   gh api "repos/HengL2022/esm/commits?path=cookbook/tutorials/binder_design.py&per_page=5" \
     --jq '.[] | .sha[0:8] + "  " + .commit.committer.date + "  " + (.commit.message|split("\n")[0])'
   ```
   If `binder_design.py` changed upstream since the pin, summarize what changed and whether it affects
   the design loop / gating (so the user can decide whether to re-vendor + re-pin).

End with a verdict: **CLEAN** (only sanctioned diffs, upstream unchanged for this file) or a list of
unexpected local diffs and/or upstream changes to review. Do not edit anything.

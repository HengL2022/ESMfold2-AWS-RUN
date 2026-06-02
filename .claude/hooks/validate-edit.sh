#!/usr/bin/env bash
# PostToolUse(Edit|Write) hook: syntax-check the edited file so a typo in a shell/python/json
# file is caught immediately (this project has no CI/linter). Exit 2 surfaces the error to Claude.
input="$(cat)"
file="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)"
[ -z "$file" ] && exit 0
[ -f "$file" ] || exit 0

case "$file" in
  *.sh)
    bash -n "$file" 2>/tmp/_vh.err || { echo "Shell syntax error in $file:"; cat /tmp/_vh.err >&2; exit 2; } ;;
  *.py)
    python3 -m py_compile "$file" 2>/tmp/_vh.err || { echo "Python syntax error in $file:"; cat /tmp/_vh.err >&2; exit 2; } ;;
  *.json)
    python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$file" 2>/tmp/_vh.err || { echo "Invalid JSON in $file:"; cat /tmp/_vh.err >&2; exit 2; } ;;
esac

# --- Guard 1: bare Ref:: in Batch job definitions (runtime bug #1) ---------------------------
# AWS Batch passes {{Ref::param}} double-braces LITERALLY -> argparse crash. Must be bare Ref::param.
case "$file" in
  *job-definition*.json)
    if grep -q '{{Ref::' "$file"; then
      echo "Batch job-def bug in $file: uses {{Ref::param}} double-braces." >&2
      echo "AWS Batch passes these literally (argparse crash). Use bare Ref::param — project runtime bug #1." >&2
      exit 2
    fi ;;
esac

# --- Guard 2: vendored-file fidelity (design/binder_design.py) -------------------------------
# This file is VENDORED from Biohub/esm @ f652b471. Sanctioned local edits ONLY: Modal-strip and
# REUSE_ESMC=True (the binder framework comes from the campaign config at runtime, not a committed
# prompt). The gating/ranking (design_binder, critic scoring, the loss functions) must stay faithful to
# upstream (explicit user constraint). Advisory reminder — the edit still applies; this just surfaces a
# note so the gating isn't changed by accident.
case "$file" in
  */design/binder_design.py|design/binder_design.py)
    echo "Reminder: $file is VENDORED from Biohub/esm@f652b471." >&2
    echo "Allowed local edits: Modal-strip and REUSE_ESMC=True (framework comes from the config, not the code)." >&2
    echo "Do NOT change gating/ranking (design_binder, critic scoring, losses). Run /check-upstream-drift to diff." >&2
    exit 2 ;;
esac
exit 0

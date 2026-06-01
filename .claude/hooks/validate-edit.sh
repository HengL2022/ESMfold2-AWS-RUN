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
exit 0

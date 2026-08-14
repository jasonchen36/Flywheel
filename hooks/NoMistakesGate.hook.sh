#!/bin/bash
# PreToolUse hook: block direct git push on feature branches without no-mistakes gate.
# Dual protocol support:
#   - Claude Code: {"continue":false,"stopReason":"..."}
#   - Grok Build: {"decision":"deny","reason":"..."} with exit code 2.

cmd=$(jq -r '.tool_input.command // .toolInput.command // ""' 2>/dev/null || echo "")

# Only intercept actual git push commands (not git commit -m "... push ...", git log, etc.)
if echo "$cmd" | grep -qE '(^|[[:space:]/;&|])(rtk[[:space:]]+)?git([[:space:]]+(-[a-zA-Z0-9_-]+|--[a-zA-Z0-9_-]+(=[^[:space:]]*)?|-c[[:space:]]*[^[:space:]]*))*[[:space:]]+push\b' && ! echo "$cmd" | grep -q 'no-mistakes'; then
  # Allow direct pushes to promotion branches (master, uat, prd)
  if echo "$cmd" | grep -qE '(\b|/)(master|uat|prd)(\b|:|\s)'; then
    exit 0
  fi

  jq -nc \
    --arg msg 'Direct git push blocked on feature branches. Run /no-mistakes instead: no-mistakes axi run --intent "<what the change achieves>"' \
    '{"continue": false, "stopReason": $msg, "decision": "deny", "reason": $msg}'
  exit 2
fi

exit 0

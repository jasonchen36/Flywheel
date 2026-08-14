#!/bin/bash
# PreToolUse hook: block consecutive PR approvals within a short window.
# Prevents rapid-fire approvals without adequate review time between them.
# Threshold: 10 seconds between approvals.
# Dual protocol support:
#   - Claude Code: {"continue":false,"stopReason":"..."}
#   - Grok Build: {"decision":"deny","reason":"..."} with exit code 2.

THRESHOLD=10
STATE_FILE="${HOME}/.claude/.pr_approval_last"

cmd=$(jq -r '.tool_input.command // .toolInput.command // ""' 2>/dev/null || echo "")

# Detect PR approval commands: gh/gh-axi pr review --approve
if echo "$cmd" | rg -q '(gh|gh-axi)\b.*\bpr\s+review\b.*--approve'; then
  now=$(date +%s)

  if [ -f "$STATE_FILE" ]; then
    last=$(cat "$STATE_FILE" 2>/dev/null)
    if [ -n "$last" ] && [ "$((now - last))" -lt "$THRESHOLD" ]; then
      remaining="$((THRESHOLD - (now - last)))"
      jq -nc \
        --arg msg "PR approval rate limit: last approval was $((now - last))s ago. Wait ${remaining}s before approving another PR. This prevents consecutive approvals without adequate review time." \
        '{"continue": false, "stopReason": $msg, "decision": "deny", "reason": $msg}'
      exit 2
    fi
  fi

  # Record this approval timestamp
  echo "$now" > "$STATE_FILE"
fi

exit 0

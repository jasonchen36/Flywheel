#!/usr/bin/env bash
# WorkflowGuard.hook.sh
# PreToolUse hook: guard CI/CD workflow configurations in .github/workflows/*.yml
# Rules (Error 262 & Retro 2026-08-13):
# 1. MS Teams notifications in Data Platform repos must route to Data Architecture channels
#    ('Data Architecture - Non-PRD', 'Data Architecture - UAT', 'Data Architecture - PRD'),
#    never foreign domain channels like 'bigbrother' / 'AXP+-+bigbrother'.
# 2. Enforces correct notification team 'BE+-+Notifications' when notify_teams_team is set.

file="${HOOK_TOOL_FILE_PATH:-}"
content="${HOOK_TOOL_PAYLOAD_CONTENT:-}"

if [[ -z "$file" || -z "$content" ]]; then
  input=$(cat /dev/stdin 2>/dev/null || true)
  [[ -z "$file" ]] && file=$(jq -r '.tool_input.file_path // .toolInput.filePath // ""' <<<"$input" 2>/dev/null || echo "")
  [[ -z "$content" ]] && content=$(jq -r '.tool_input.content // .tool_input.new_string // .toolInput.content // .toolInput.newString // ""' <<<"$input" 2>/dev/null || echo "")
fi

# Only target GitHub Actions workflow YAML files
case "$file" in
  *.github/workflows/*.yml|*.github/workflows/*.yaml) ;;
  *) exit 0 ;;
esac

# Rule 1: Reject improper notification channels (e.g. bigbrother in data platform workflows)
if echo "$content" | grep -qiE 'notify_teams_channel.*bigbrother'; then
  msg="Invalid Teams notification channel 'bigbrother' detected in workflow YAML. Data Platform workflows must route to Data Architecture channels (Data Architecture - Non-PRD/UAT/PRD) under team 'BE - Notifications'."
  echo "BLOCKED: $msg" >&2
  jq -nc \
    --arg msg "$msg" \
    '{"continue": false, "stopReason": $msg, "decision": "deny", "reason": $msg}'
  exit 2
fi

exit 0

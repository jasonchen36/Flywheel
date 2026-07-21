---
name: hooks
description: "Use when checking Claude Code hook activity, status, stats, or configurations, or troubleshooting empty/missing hooks."
---
# Hooks Skill

Displays a dashboard of safety guardrails and reminder hooks that have run in your sessions.

## Steps

1. **Verify hook execution log**:
   - Check if `~/.claude/logs/hooks.log` exists.
   - Run `rtk bash ~/.claude/tools/hooks-stats.sh --days 30` to show the stats over the last 30 days.

2. **Diagnose configuration issue**:
   - Check `~/.claude/settings.json` for hardcoded version strings.
   - Compare `the employer-engineering` plugin version (`0.12.0` in settings vs `0.29.0` installed).

3. **Offer fix**:
   - Suggest updating the hardcoded `0.12.0` version strings to `0.29.0` in `~/.claude/settings.json`.

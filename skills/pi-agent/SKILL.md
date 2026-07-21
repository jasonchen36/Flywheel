---
name: pi-agent
description: >
  Default pi coding-agent behavioral surface for PAI self-improvement.
  Attributed on unscoped pi ratings (skill=pi-agent). skill_autofix may append
  AUTO-LEARNED-GUARDRAILS only. Always injected into learning context when present.
user-invocable: false
---

# Pi agent default skill

Default attribution target when a pi session has no `/skill:name` or known slash command.

## Standing rules (shared with Claude/Grok)

- Never claim done without a paper trace (CLI output, path, URL, diff).
- Prefer `cli` for Jira; never jira-context MCP.
- Never post to GitHub without user approval (draft → show → ask).
- Use graphiti/bungraph before broad manual research when available.
- Evidence first: dry-run SQL, verify schemas, no unverified system claims.

## Auto-learned section

The self-improvement loop rewrites only between the markers below.
Do not remove the markers.

<!-- AUTO-LEARNED-GUARDRAILS:start -->
## Auto-learned guardrails (self-improvement loop)
<!-- pattern:unverified_completion updated:2026-07-09 — evolved after subj Δ=+0.134 regression -->
- Never say done/fixed/complete/picture-is-complete without STRONG evidence: fenced CLI/test output, pass counts, exit codes, or a live URL. Bare paths do not count.
- If ticket or request scope is not fully verified, say what remains open — do not rephrase partial work as complete.
- When a prior tool call failed, diagnose root cause before retrying the same action.
- Before "full picture" summaries, list what was checked (ticket, diff, paired systems); if anything is unread, do not claim completeness.
<!-- AUTO-LEARNED-GUARDRAILS:end -->

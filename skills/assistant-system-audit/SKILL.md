---
name: assistant-system-audit
description: "unified assistant-system audit for router sync, skill parity, hook references, and Codex MCP configuration. Use when this workflow is needed."
---
# Assistant System Audit Skill

Use this when you want one compact audit across the assistant stack instead of checking skills, hooks, and MCP wiring separately.

## What It Checks

- assistant router sync (`CLAUDE.md` -> `AGENTS.md` / `GEMINI.md`)
- parity-map workflows against actual Codex skills and Claude commands/skills
- Claude hook config references
- Codex MCP config absolute paths
- essential local MCP health (`rtfmcp`, `ai-agents-review` bootstrap)

## Command

```bash
~/.claude/tools/assistant-system-audit.sh
```

## When To Run

- after adding or renaming Codex skills
- after editing `ASSISTANT_SKILL_PARITY_MAP.md`
- after changing `.codex/config.toml`
- after hook additions or removals
- before or after a failover drill when you want a narrower config audit

## Follow-Up

If the audit fails:
- fix the specific drift
- re-run the audit
- if the issue involves cross-assistant behavior, run `/failover-drill` next

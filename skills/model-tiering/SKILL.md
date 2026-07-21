---
name: model-tiering
description: "Routes work to cheap, mid, or high model tiers by phase and blast radius. Use when choosing a model, planning AIDD phases, controlling AI cost, or when the user asks about Sonnet/Opus spend or default model selection."
---

# Model tiering

Read and follow: `~/.claude/MEMORY/STATE/model_tiering.md`

## Quick decision

| Situation | Tier |
|---|---|
| Summarize scrum / extract transcript / classify | cheap |
| Single-repo fix with clear tests | mid |
| Multi-repo warehouse / cascade / security design | high |
| Background harness / ratings / reclass | cheap only |

## When invoked

1. Classify task phase and blast radius.
2. State chosen tier and why (one line).
3. Escalate only after a failed mid attempt or explicit high-risk need.
4. Prefer verification tools over spending another high-tier turn.

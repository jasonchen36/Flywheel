# Retrieval SOP (graph-first)

Use this order every investigation. Do not open with broad web/code trawl.

1. **Graph** — `graphiti-memory` (group `main`) and/or `bungraph`
   - prior decisions, entities, tribal facts, ACE-adjacent memory
2. **Local truth** — repo files, `bq show` / schema tools, rtfmcp / Confluence
3. **Scrum / meetings** — `~/.claude/scrum-recordings/*.txt` or summaries only if graph miss
4. **Broad search** — web / large greps only after 1–3

## Hard gate

`graphiti_bypassed` is **BLOCK** if ≥2 research/search tools run without graphiti-memory or bungraph.

## Write-back

Durable findings (decisions, owner, runbook facts) → `add_memory` / bungraph episode before session end.

## Anti-patterns

- mem0-only when Graphiti is healthy
- Full scrum archive trawl for one ticket (use `/scrum-context` narrow query)
- Claiming schema/CI/state without a tool result after retrieval

---
name: token-retro
description: "Use when you need to analyze recent Codex sessions with the local token-retro helper to find high-cost patterns, rank costly workflows, and turn them into concrete prompt or workflow improvements"
---
# Token Retro Skill

Use this skill for weekly or ad hoc token-efficiency retrospectives across recent Codex sessions.

It wraps the local helper:
- `~/.claude/tools/codex-token-retro.py`

## Load First

- `${HOME}/CODEX_TOKEN_SAVING_PLAYBOOK.md`

## Workflow

### 1. Pick the Window

Defaults:
- `--days 7`
- `--limit 10`

Useful variants:

```bash
~/.claude/tools/codex-token-retro.py --days 7 --limit 10
~/.claude/tools/codex-token-retro.py --days 14 --limit 15 --include-archived
~/.claude/tools/codex-token-retro.py --days 7 --limit 10 --min-score 30000
~/.claude/tools/codex-token-retro.py --days 7 --json
```

### 2. Read the Output as Relative Cost Signal

The script reports a cost proxy, not actual billable tokens.

Use it to answer:
- which sessions were most expensive
- which task classes are trending expensive
- whether prompt volume, tool volume, or long debug/review loops dominate
- whether bootstrap/context overhead is still too high

### 3. Extract the Top 3 Fixes

Turn the results into specific actions such as:
- tighten prompt scope
- cap findings and tool output
- split unrelated work into new threads
- use `/context-pack` or `/session-brief` before handoff
- prune noisy mem0 retrieval patterns
- create or refine skills for repeated costly workflows

### 3b. Hallucination Audit (run alongside cost analysis)

Scan the same sessions for hallucination patterns — claims made without evidence, later corrected:

```bash
# Look for correction signals in session logs
~/.claude/tools/codex-token-retro.py --days 7 --json | \
  python3 -c "
import json, sys
sessions = json.load(sys.stdin)
for s in sessions:
    title = s.get('title', '')
    # Proxy: sessions with high tool-call density relative to message count
    # suggest over-asserting (many verifications needed after the fact)
    msgs = s.get('message_count', 0)
    tools = s.get('tool_use_count', 0)
    ratio = tools / max(msgs, 1)
    if ratio > 3:
        print(f'High verify-after-assert ratio: {title} ({tools} tools / {msgs} msgs = {ratio:.1f}x)')
"
```

Review flagged sessions for these patterns:
- Claims made before any tool call on that topic (assertion without read/run)
- Cross-env claims ("in PRD / UAT") without per-env tool evidence
- Contract claims (nullability, ranges) without a BQ query result
- "Done" or "fixed" without attached artifact (test output, dry-run bytes, run link)
- Multiple correction cycles on the same claim (compounding errors)

Add any recurring pattern to `${HOME}/errors-and-lessons.md`.

### 4. Store or Share the Outcome

Produce a compact retro summary:

```text
TOKEN RETRO

Window:
- <days>

Most Expensive Pattern:
- <pattern>

Top Cost Drivers:
- <driver>

Recommended Changes:
- <change 1>
- <change 2>
- <change 3>
```

If the retro reveals a stable workflow improvement, update the relevant playbook or skill in the same session.

## Guardrails

- Treat the score as a relative ranking, not a billing source of truth.
- Do not optimize away required verification for high-risk tasks.
- Prefer 2-3 concrete actions over a long list of observations.

## Integration

- Run weekly as referenced in `CODEX_TOKEN_SAVING_PLAYBOOK.md`.
- Pair with `/mem0-cleanup` when repeated mem0 lookups are a cost driver.
- Pair with `/context-pack` when prompt bloat is the main issue.

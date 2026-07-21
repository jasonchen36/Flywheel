---
name: session-brief
description: "Use when you need to generate a compact local session brief artifact with repo, JIRA, PR, commit, and context-pack signal for fast context restoration or handoff"
---
# Session Brief Skill

Use this skill when you want a compact local session artifact without running the full `/session-start` workflow.

Good use cases:
- before switching topics
- before handing work to another assistant
- after a long debugging session
- before a failover drill

## Workflow

### 1. Decide Brief Shape

Defaults:
- output: `~/SESSION_BRIEF_CURRENT.md`
- include context pack: yes
- max items: `10`

If the user wants a lighter brief:
- reduce `--max-items`
- pass `--no-context-pack`

### 2. Run the Helper Script

Default:

```bash
~/.claude/tools/assistant-session-brief.sh
```

Useful variants:

```bash
~/.claude/tools/assistant-session-brief.sh --max-items 5
~/.claude/tools/assistant-session-brief.sh --no-context-pack
~/.claude/tools/assistant-session-brief.sh --output /tmp/session-brief.md
```

### 3. Inspect the Output

Check that the brief includes the expected sections:
- repo context
- in-progress JIRA
- open PRs
- recent commits
- optional compact context pack

If the current directory is not a repo, note that repo sections are intentionally reduced.

### 4. Summarize the Useful Signal

Do not paste the entire artifact back unless asked.
Extract only the parts needed for the current task:
- active branch and dirty files
- open PRs
- in-progress ticket references
- next actions

## Guardrails

- Use `/session-start` for a full morning bootstrap; use this skill for a lighter artifact-oriented refresh.
- Treat the generated markdown as a local artifact, not a source of truth for external posting.
- If JIRA or GitHub auth is unavailable, report the missing section rather than failing the whole brief.

## Integration

- Run before `/failover-drill` to confirm the session-brief path is healthy.
- Run before starting a new assistant thread when you want a compact handoff artifact.
- Pair with `/context-pack` when you need a repo-focused prompt seed rather than a broader session snapshot.

---
name: transcript-extract
description: "use for scrum calls, transcripts, or pasted meeting text; extract a bounded summary artifact with action items, decisions, blockers, lessons, and dates before deeper reasoning. Use when this workflow is needed."
---
# Transcript Extract Skill

Use this skill before doing deep reasoning on a long transcript.

Best use cases:
- scrum transcript files
- translated meeting transcripts
- long pasted meeting notes copied into a file

It wraps:
- `~/.claude/tools/transcript-extract.sh`

## Workflow

### 1. Point It at a Transcript File

Default:

```bash
bash ~/.claude/tools/transcript-extract.sh /path/to/transcript.txt --your-name jason
```

For scrum transcripts, prefer:

```bash
bash ~/.claude/tools/transcript-extract.sh /path/to/transcript.txt --your-name jason --scrum-mode
```

Optional bounded outputs:

```bash
bash ~/.claude/tools/transcript-extract.sh \
  /path/to/transcript.txt \
  --your-name jason \
  --scrum-mode \
  --max-items 6 \
  --output /tmp/transcript-summary.md \
  --json-output /tmp/transcript-summary.json
```

### 2. Read the Summary Artifact, Not the Raw Transcript First

The extractor writes a bounded markdown summary with:
- action items for you
- other action items
- blockers
- dates and deadlines
- direct mentions

Without `--scrum-mode`, it also includes decisions and lessons.

Use that artifact for the first reasoning pass.
Only go back to the raw transcript if a specific unresolved question remains.

### 3. Route the Next Step

- use `/scrum-context` if the goal is ticket/PR context
- update `errors-and-lessons.md` if a durable lesson clearly emerges
- draft a scrum update if the point is status communication

## Guardrails

- Do not reason directly from a giant transcript when the bounded summary artifact is available.
- Treat extraction results as heuristics; verify important claims against the source transcript before posting or escalating.
- Keep transcript work bounded with `--max-items`.
- Prefer `--scrum-mode` for noisy translated scrum transcripts.

## Integration

- Pair with `/scrum-context` for ticket- or PR-specific context recovery.
- Pair with `/scrum-update` for standup summaries.
- Use before storing transcript-derived summaries in mem0.

---
name: diarize
description: "Use when you need to read a large body of content — session signals, error logs, NPS comments, meeting notes, support tickets, scrum transcripts, or any multi-document set — and distill it into a structured one-page intelligence brief. Not a summary. A judgment profile that captures patterns, contradictions, trajectory, and what to act on. Use before /skill-patch when the source material needs sense-making first."
---
# Diarize

Reads everything about a subject and writes a structured profile — a single page of judgment distilled from many documents. The output answers: "If I had 5 minutes to understand this, what would I need to know and do?"

**Diarization ≠ summarization.** Summarization extracts what was said. Diarization surfaces what it means: recurring patterns, contradictions between claim and reality, trajectory over time, and gaps that shouldn't be there.

## Input

Accept any of:
- A file or directory path: `/diarize ~/.claude/MEMORY/LEARNING/SIGNALS/`
- A topic with optional date filter: `/diarize recent session errors last 14 days`
- A named source: `/diarize errors-and-lessons.md errors 120-141`
- Content in the current conversation: `/diarize` with text already in context

## Step 1: Collect source material

Read every relevant document. Do not sample — read all of it.

**For explicit paths**: read the file or all files in the directory.

**For topics**, search in parallel:
- `~/.claude/MEMORY/LEARNING/SIGNALS/ratings.jsonl` — filter by date and rating threshold if specified
- `~/.claude/MEMORY/LEARNING/FAILURES/` — list and read all files in date range
- `~/.claude/MEMORY/LEARNING/REFLECTIONS/algorithm-reflections.jsonl` — read all entries in range
- `~/errors-and-lessons.md` — read the Quick Reference Index first, then the full entries for the date range
- mem0 search on the topic (3–5 result limit)

Record: total document count, date range, and source breakdown before proceeding.

## Step 2: Read for signal, not content

While reading, track four things only:

**Patterns** — what keeps coming up across multiple documents, even in different words.

**Contradictions** — where what was claimed diverges from what actually happened. The canonical form: "Says X, actually Y." Flag these explicitly; they are usually the most actionable finding.

**Trajectory** — is the subject getting better, worse, or cycling? Look for the same issue appearing in Jan, then Feb, then Apr — that's a cycle, not progress.

**Gaps** — what's conspicuously absent that should be there. A skill that never gets updated despite being used daily. An error category that keeps appearing but has no corresponding guardrail.

Ignore content that doesn't contribute to one of these four. Do not extract every data point — extract judgment.

## Step 3: Produce the brief

```
DIARIZATION: <subject>
Period: <from> → <to>
Sources: <n> documents (<breakdown by source>)

PATTERNS:
- [pattern]: [1-2 sentence evidence]

CONTRADICTIONS:
- [claim] vs [reality]: [evidence with date or source]

TRAJECTORY:
- [better / worse / cycling / stable]: [evidence]

TOP INSIGHTS (actionable only):
1. [insight] → [specific recommended action]
2. [insight] → [specific recommended action]
3. [insight] → [specific recommended action]

GAPS:
- [what's missing that should be there]
```

Keep the brief under 40 lines. Omit sections with nothing to report. Do not pad with observations that don't lead to an action.

## Step 4: Route to action

After presenting the brief, offer next steps based on the content:

| Finding type | Offer |
|---|---|
| Patterns that map to a skill domain | `/skill-patch` for each pattern |
| Errors with no corresponding skill guardrail | `/skill-patch Error <n>` for each |
| Stale or contradicted mem0 entries | `/mem0-cleanup` |
| Gaps that suggest a missing skill | Draft the skill (name, description, first 3 steps) |
| Trajectory showing a worsening pattern | Escalate to `errors-and-lessons.md` update |

Do not act on any of these automatically — present the options and wait.

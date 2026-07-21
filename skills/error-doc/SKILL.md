---
name: error-doc
description: "capture new workflow lessons or errors, update the documentation, and persist the distilled knowledge in mem0. Use when this workflow is needed."
---
# Error Documentation Skill

This skill captures new lessons and errors from your workflow, updates the documentation, and persists the knowledge in mem0.

## Workflow
1. Take a compact summary of the lesson.
2. Update the durable documentation source (`errors-and-lessons.md`, `CLAUDE.md`, or equivalent).
3. Persist one distilled mem0 memory only if a mem0 write tool is actually available in the current runtime.

## Mem0 Rule

When storing to mem0, store one canonical summary:
- decision
- rationale
- impact
- prevention rule

Do not store:
- raw transcript text
- long doc excerpts
- one-line URL indexes
- multiple near-duplicate memories for the same lesson

If the current runtime does not expose a mem0 write tool, stop after the documentation update and report that mem0 sync is unavailable in this session. Do not claim the memory was persisted.

## Usage
```bash
# Log a new lesson
./tools/log-lesson.sh "Summary of lesson"
```

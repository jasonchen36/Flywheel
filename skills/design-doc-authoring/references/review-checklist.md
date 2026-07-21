# Design Doc — Review Checklist & Conciseness Workflow

Read this before sharing/committing a design doc, or when asked to make one concise.

## Pre-publish checklist

- [ ] No specific implementation-ticket IDs in the body (only historical/parent/context tickets)
- [ ] No "we will not use X" unless X was a genuinely close alternative (then it's one line)
- [ ] No implementation minutiae (API calls, flags, exit codes, function names, runbook steps)
- [ ] Every removed/deferred thing is simply absent, not eulogized
- [ ] No fact stated twice; notes don't repeat prose or tables
- [ ] Each Decision = bold one-liner + brief rationale, not a wall of text
- [ ] Diagrams/tables aren't re-narrated in prose
- [ ] Would a senior engineer call any section "too much detail for a design doc"? If yes, cut it.

## Editing an existing doc for conciseness

1. **Read the current file fully first.** It may have changed since you last saw it; never edit
   from cached/quoted content.
2. **Attack the longest lines/paragraphs first.** They are usually the worst offenders —
   credential blocks, override/merge semantics, auto-merge mechanics, multi-warning notes.
   Collapse each to a design-level statement; push the detail to a ticket.
3. **Genericize ticket-coupling.** Replace impl ticket IDs with "the implementation team" /
   "an implementation decision". Keep parent/spike/context tickets.
4. **Delete exclusion-essays.** Remove "X must not be used / is not required / do not implement
   Y" unless it documents a genuinely close rejected alternative.
5. **De-duplicate.** If a note and prose (or a table) say the same thing, keep one.
6. **Verify before commit:**
   - `rg "<IMPL-TICKET-PREFIX>-(<the impl ticket numbers>)" <file>` → expect 0 matches
   - `rg "<removed-component-name>" <file>` → expect 0 matches
   - Re-read the diff: does each surviving line state *what/why*, not *how*?

## Why these rules (from real review feedback)

- Design docs tied to impl tickets go outdated the moment tickets are renamed/re-scoped.
- "There can be a lot of things we will not use — that is self-explanatory." Listing them is
  noise; stating what we *will* use is the clarity reviewers want.
- A design doc at implementation-level detail reads as a spec, not a design — reviewers can't
  see the decisions through the mechanics.
- When you remove something per feedback, leaving a tombstone ("we no longer use X") puts the
  noise right back.

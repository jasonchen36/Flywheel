---
name: design-doc-authoring
description: "Use when writing, drafting, reviewing, or editing a design doc, RFC, or technical design (e.g. files under design_docs/ or rtfm/docs/**/Design_Docs/) — especially at the employer. Keeps docs at design level and prevents verbosity, implementation minutiae, ticket-coupling, and exclusion-essays. Triggers: 'write a design doc', 'draft a design', 'design doc for', 'make this design concise', 'RFC'."
---
# Design Doc Authoring

A design doc states **what** we will build and **why**, at design level. It is not an
implementation spec, a runbook, or a ticket tracker. Reviewers want clarity, not coverage.

## Files in this skill
- `assets/design-doc-template.md` — copy this as the starting skeleton for a new design doc.
- `references/review-checklist.md` — the pre-publish checklist and the edit-for-conciseness
  workflow. Read it before sharing/committing a doc, or when asked to make a doc concise.

## The five rules (the judgment core — apply always)

1. **Design level, not implementation level.** State the decision and rationale; do not
   specify HOW. Cut on sight: API call names/args, exit codes, CLI/`pip` flags, function
   signatures, pagination mechanics, library names, step-by-step runbooks. Those go in
   tickets/code. *Smell test:* if a sentence says **how** (a function, API, flag, error code)
   rather than **what** is decided or **why** — move it to a ticket or delete it.

2. **No implementation-ticket references.** Impl ticket IDs change and go stale. Use generic
   language: "the implementation team", "an implementation decision", "before implementation
   begins". Reference only **historical/parent/context** tickets (the spike, the parent epic) —
   those are stable facts. Tickets are created **after** the design is finalized.

3. **Say what you WILL use, not what you won't.** List the repos/components/approaches the
   design uses; everything unmentioned is implicitly out of scope. No essays of "we will not
   use A, B, C". *Exception:* name a rejected option only when two choices were genuinely
   close — then one line: "Chose X over Y because…".

4. **When feedback removes something, remove it cleanly.** Delete every mention. Do not leave
   "note: we no longer use X" — that reintroduces the noise you were asked to cut.

5. **State each fact once.** No redundant restatements. If a table or diagram conveys it,
   don't repeat it in prose.

## Workflow
- **New doc:** copy `assets/design-doc-template.md`, fill it, then run the checklist in
  `references/review-checklist.md`.
- **Editing for conciseness / addressing review feedback:** follow the workflow in
  `references/review-checklist.md` (read current file first → attack longest paragraphs →
  grep for stray ticket IDs and removed-component names before committing).

This skill is "code quality and review" type: it enforces a doc standard the model does not
hold by default. Start small; add gotchas here as new edge cases appear.

---
name: skill-patch
description: "Use when a lesson, error, or friction point from a session should be written back into an existing skill file as a guardrail or rule. Closes the self-improvement loop — takes a \"when X, do Y\" lesson and patches it directly into the skill that covers that domain. Also used by the nightly enrichment cron to surface patch proposals."
---
# Skill Patch

Closes the self-improvement loop: takes a lesson from the current session, a specific error number, or a signal from `ratings.jsonl` or `FAILURES/`, finds the skill file where it belongs, and writes the rule back in.

## Input

The lesson can come from:
- A direct argument: `/skill-patch "when reviewing SQL, always run dry-run before posting"`
- An error number: `/skill-patch Error 110`
- No argument: derive from the most recent low-rating signal or the current session's friction points

## Step 1: Formulate the rule

Convert the lesson into canonical "when X, do Y" format:

```
When: <triggering condition — what situation activates this>
Do:   <required action — what the rule demands>
Why:  <one sentence on what went wrong without it — cite error# or signal date>
```

If the input is a raw friction point ("the model hallucinated column names"), extract the trigger and action from it before proceeding. Do not proceed with a vague rule — the output must be specific enough to paste directly into a skill.

## Step 2: Find the right skill file

Search `~/.claude/commands/` and `~/.claude/skills/` for skill files whose content matches the domain of the lesson:

```bash
grep -rl "<keyword1>" ~/.claude/commands/ ~/.claude/skills/
grep -rl "<keyword2>" ~/.claude/commands/ ~/.claude/skills/
```

Run 2–3 keyword variations. List candidate files with a one-line rationale for each. Pick the most specific match. If multiple skills are equally relevant, patch all of them. If no skill matches, note that a new skill may be needed and suggest a name and location — but do not create it automatically.

## Step 3: Read the skill file

Read the full content of each candidate skill file before drafting anything.

## Step 4: Draft the patch

Identify the best insertion point in order of preference:
1. **Existing `## Guardrails` section** — append a bullet
2. **The specific step the rule applies to** — add a blockquote note below it
3. **No natural home** — add a new `## Guardrails` section before the final heading

Format for a guardrail bullet:
```markdown
- **[trigger condition]**: [required action]. (Evidence: [Error# or YYYY-MM-DD signal])
```

Format for a step-level note:
```markdown
> **Guardrail**: [one sentence rule]. ([Error# or date])
```

Show a before/after diff: the 3–5 lines before the insertion point, then the inserted text, then the 2–3 lines after.

## Step 5: Write on approval

Wait for explicit user approval. Do not write any file without it.

On approval:
- Write the patched skill file
- Confirm: "Patched `<path>`. The rule is now in the skill."

If the lesson applies to a marketplace skill under `~/.claude/plugins/marketplaces/the employer-ai-tools/`, note that a PR to `example-org/ai-tools` is also needed — the local cache will be overwritten on the next plugin sync.

## Batch mode (nightly enrichment)

When invoked by the nightly cron without a specific lesson argument:

1. Read the last 7 days of `~/.claude/MEMORY/LEARNING/SIGNALS/ratings.jsonl` — filter for `rating <= 4`
2. Read the last 7 days of `~/.claude/MEMORY/LEARNING/FAILURES/` — list file names and read each
3. For each signal/failure, formulate a candidate rule (Step 1)
4. Deduplicate rules that map to the same skill and insertion point
5. Present all proposals as a numbered list — rule, target skill, insertion point, draft text
6. Do not write anything until the user selects which proposals to act on

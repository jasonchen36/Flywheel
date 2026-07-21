---
name: sync-check
description: "Use when you need to check AGENTS.md, CLAUDE.md, and GEMINI.md for rule drift and sync gaps"
---
# Sync Check Skill

Verifies that AGENTS.md, CLAUDE.md, and GEMINI.md are consistent with each other and with the current canonical rules. The cross-assistant sync rule requires all three to be updated together when guardrails change.

## When to Run

- After updating any of the three files
- When you notice inconsistent behavior across sessions (may indicate a rule diverged)

Prerequisite:
- `~/AGENTS.md`, `~/CLAUDE.md`, and `~/.gemini/GEMINI.md` are local workstation files, not versioned in this repository.
- If one or more files do not exist yet, report that explicitly and continue with available files instead of returning an empty report.

## Step 1: Load All Three Files

Read in parallel:
- `~/AGENTS.md`
- `~/CLAUDE.md`
- `~/.gemini/GEMINI.md` (if it exists)

If any file is missing, record it explicitly and continue the comparison with available files.

Note the "Last Updated" date on each. Any file that hasn't been updated within 30 days of the others is a drift candidate.

## Step 2: Compare Absolute Safety Rules

Use `~/.claude/commands/shared-policies.md#canonical-safety-rules-baseline` as the runtime canonical baseline.
Source fallback path for comparisons:
- `${CLAUDE_SKILLS_SOURCE_DIR:-~/claude-skills}/skills/shared-policies.md`
- If that source file is missing, report setup drift and continue without source fallback comparison.
If the installed file is missing, fall back to the source path above and flag the missing installed file as setup drift.
When both copies exist, compare installed vs source and flag any drift in the installed local copy separately.

Process:
- Load that baseline list first.
- Check whether each baseline rule appears in all three local files (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`) with equivalent meaning.
- If a strict rule exists in a local file but is missing from the shared baseline, flag it as "baseline drift" and propose updating `shared-policies.md` first.

For each rule, check whether it appears in all three files with equivalent meaning. Flag:
- **Missing** — rule exists in AGENTS.md/CLAUDE.md but not in GEMINI.md (or vice versa)
- **Weakened** — rule is present but softer ("consider" vs "never")
- **Contradictory** — different files give different guidance on the same topic

## Step 3: Compare Workflow Rules

Check these workflow-level rules for consistency:

- airflow-image PRD tag PR requires 2 human reviewers; assistants do not auto-approve/merge
- PR label requirement (`ai-review:latest-models`)
- Post-PR scrub command (no Codex/Claude/GPT/assistant in title/body)
- ADF JSON format for JIRA updates (never `--description-file`)
- evidence-first rules (read before asserting, dry-run before claiming SQL works)
- Scrum transcript search for JIRA/PR investigations

## Step 4: Check Skills Inventory Consistency

The skills listed in CLAUDE.md and AGENTS.md should reference the actual files in `~/.claude/commands/`:

```bash
ls ~/.claude/commands/*.md | xargs -I{} basename {} .md | sort
```

Compare against any skill lists mentioned in the reference docs. Flag any skills that:
- Exist in `~/.claude/commands/` but aren't mentioned in the routing docs
- Are mentioned in the docs but the file doesn't exist

## Step 5: Check Operational References

Both AGENTS.md and CLAUDE.md have a table of operational references (data-warehouse.md, deployment-workflows.md, etc.). Verify:
- The same files are listed in both
- Files referenced actually exist on disk

```bash
python3 - <<'PY'
from pathlib import Path
import re

home = str(Path.home())
docs = [
    Path.home() / "AGENTS.md",
    Path.home() / "CLAUDE.md",
    Path.home() / ".gemini" / "GEMINI.md",
]
paths = set()

for doc in docs:
    if not doc.exists():
        print(f"MISSING_SYNC_DOC: {doc}")
        continue
    text = doc.read_text()
    for raw in re.findall(rf"(?:{re.escape(home)}|~)/[^\s`\"']+\.md", text):
        paths.add(str(Path(raw).expanduser()))

for path in sorted(paths):
    if not Path(path).is_file():
        print(f"MISSING: {path}")
PY
```

## Step 6: Present Sync Report

```
Sync Check Report — <date>
===========================

Last Updated:
- AGENTS.md:  <date>
- CLAUDE.md:  <date>
- GEMINI.md:  <date> (or "not found")

Safety Rule Coverage:
- All rules present in all files: <n>/<total>
- Missing rules: <list with which file is missing>
- Weakened rules: <list>
- Contradictions: <list>

Workflow Rules:
- Consistent: <n>/<total>
- Gaps: <list>

Skills:
- In commands/ but undocumented: <list>
- Documented but missing file: <list>

Reference files:
- All present: YES / <n missing>

Overall: IN SYNC / DRIFT DETECTED (<n issues>)
```

## Step 7: Offer to Fix Drift

If drift is detected, for each gap:
- Show the canonical version (from AGENTS.md or CLAUDE.md, whichever is more recent)
- Show what the other file(s) have
- Ask: "Should I update [file] to match?"

Always show the diff before making changes. Do not update files without approval.

After updates:
- Update the "Last Updated" date in all modified files

## Guardrails

- Never weaken a safety rule during sync — if AGENTS.md is stricter, propagate the stricter version.
- If GEMINI.md doesn't exist, note it but do not create it — that's a separate decision.
- Do not remove rules unless the user explicitly says they're obsolete.

## Integration

- Run after any significant workflow change that updates one of the three files

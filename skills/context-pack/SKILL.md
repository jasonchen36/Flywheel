---
name: context-pack
description: "Use when you need to generate a bounded repo-focused context pack for a new Codex thread or handoff, including changed files, recent commits, optional risky files, PR checks, and errors-and-lessons anchors"
---
# Context Pack Skill

Use this skill when you want a compact, bounded context bundle for the current repo before starting a new thread, asking for review, or handing work to another assistant.

## Workflow

### 1. Choose the Pack Depth

Defaults are intentionally bounded.

Typical light run:

```bash
~/.claude/tools/codex-context-pack.sh --max-files 10 --max-lines 10
```

For riskier changes:

```bash
~/.claude/tools/codex-context-pack.sh \
  --max-files 15 \
  --max-lines 20 \
  --diff-stat \
  --risky-files \
  --errors-pattern "review|approval|dry_run"
```

For PR-linked work:

```bash
~/.claude/tools/codex-context-pack.sh --pr-checks --risky-files
```

### 2. Inspect the Bounded Sections

Focus on:
- changed files
- recent commits
- risky changed files
- PR check failures or pending checks
- relevant `errors-and-lessons.md` anchors

Do not paste the entire output into chat unless the user asks. Extract only the pieces needed.

### 3. Use It as a Prompt Seed or Handoff Base

The script already emits a compact prompt seed.
Use that to start a new thread with:
- exact scope
- changed-file focus
- capped findings
- verification-first expectations

## Guardrails

- This is a bounded snapshot, not a substitute for reading the actual files.
- Prefer targeted flags over enabling every section by default.
- If not in a git repo, report that and stop instead of forcing a meaningless pack.

## Integration

- Run before `/review` when you want a compact repo snapshot.
- Run before `/session-brief` with `--no-context-pack` if you want the repo pack separately.
- Run before opening a fresh Codex thread on the same worktree.

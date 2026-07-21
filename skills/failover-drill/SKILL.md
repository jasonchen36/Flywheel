---
name: failover-drill
description: "assistant failover readiness drill: run the shared drill script, capture friction, and verify cross-assistant guardrails and session-brief readiness. Use when working on assistant failover readiness drill."
---
# Failover Drill Skill

Use this skill for the monthly assistant failover drill or whenever you want to verify that Codex, Claude, and Gemini workflows are still aligned enough to swap assistants safely.

## Load First

- `${HOME}/ASSISTANT_FAILOVER_DRILL_TEMPLATE.md`
- `${HOME}/ASSISTANT_SKILL_PARITY_MAP.md`

## Workflow

### 1. Confirm Drill Scope

Decide:
- assistant target (`claude`, `codex`, or another label)
- task under test
- workspace
- optional friction items already known

If the user did not specify these, use the script defaults and state them.

### 2. Run the Shared Drill Script

Default command:

```bash
~/.claude/tools/run-assistant-failover-drill.sh --assistant codex
```

Useful options:

```bash
~/.claude/tools/run-assistant-failover-drill.sh \
  --assistant codex \
  --task "Run one real PR review workflow" \
  --workspace /path/to/repo \
  --friction "Confluence auth failed in Codex" \
  --friction "Needed manual repo argument for github-pr-context"
```

If the user wants a temporary smoke test instead of a saved drill report, pass `--report /tmp/...`.

### 3. Review the Generated Report

Inspect:
- shared policy sync status
- session brief generation status
- approval gate probe
- SQL dry-run proof gate
- friction items captured

Also confirm the report points to the expected artifacts:
- `SESSION_BRIEF_CURRENT.md`
- `ASSISTANT_PROMPT_TEMPLATES.md`
- `ASSISTANT_SKILL_PARITY_MAP.md`

### 4. Extract Actionable Follow-Ups

Summarize:
- what passed
- what failed
- whether the failure is config drift, auth drift, tool drift, or doc drift
- which script or doc should be patched first

### 5. Patch the Friction in the Same Session When Feasible

If the drill reveals a straightforward gap:
- update the relevant script or docs
- re-run the drill
- confirm the report improved

### 6. Produce a Compact Drill Summary

Use:

```text
FAILOVER DRILL

Target:
- <assistant>

Passed:
- <checks>

Failed:
- <checks>

Friction:
- <item>

Next Fix:
- <highest-value patch>

Artifacts:
- <report path>
```

## Guardrails

- Do not treat a successful script run as proof that a real workflow is healthy; note what the drill did and did not validate.
- Keep friction items concrete and patchable.
- If the drill reveals rule drift, run `/sync-check` next.
- If the drill reveals skill routing drift, update `${HOME}/ASSISTANT_SKILL_PARITY_MAP.md`.

## Integration

- Run monthly as documented in `AGENTS.md` and `CLAUDE.md`.
- Run after major router, hook, or MCP changes.
- Pair with `/sync-check` when the report shows cross-assistant drift.

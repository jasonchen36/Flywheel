---
name: self-improve
description: >
  Status and control surface for the autonomous self-improvement loop.
  Use when asked about self-learning, autonomy, lesson queue, skill autofix,
  review_queue, /self-improve, or pi harness parity.
user-invocable: true
---

# Self-improve loop status (pi + Claude + Grok)

Lil'Log map: [Harness Engineering for Self-Improvement](https://lilianweng.github.io/posts/2026-07-04-harness/)
— ACE playbook, Self-Harness mine/propose/validate, editable surfaces outside the loop.

**Full parity agents:** Claude Code, Grok Build, **pi** (`@earendil-works/pi-coding-agent`).

Shared harness root: `~/.claude/MEMORY/LEARNING/`

| Agent | Enforcement | Learning inject | SessionEnd |
|---|---|---|---|
| Claude | `EnforcementGate.hook.ts` (Stop) | `FailurePatternReminder` + `RatingCapture` | settings hooks |
| Grok | Claude hooks via `[compat.claude]` | same via hooks compat | same |
| pi | `pai-enforcement-gate.ts` (agent_end) | `pai-learning-harness.ts` | `claude-bridge.ts` → SessionEnd |

**Graphiti freshness:** SessionEnd runs `session_graphiti_autoseed.py` → `sync_graph_memory.py` → `flush_graphiti_pending.py` (Neo4j via MCP `:8000/mcp`). Mid-session: `graphiti_bypassed` **block** (read) + `graphiti_writeback_skipped` **warn** (write).

## Steps

1. Read `~/.claude/MEMORY/LEARNING/AUTONOMY.md` (includes pi parity table).
2. Run:

```bash
cd ~/.claude/MEMORY/LEARNING && pyenv exec python3 harness_healthcheck.py
pyenv exec python3 held_out_suite.py --gate
pyenv exec python3 agent_rollouts.py --gate
pyenv exec python3 self_harness.py --gate
pyenv exec python3 skill_autofix.py --dry-run 2>&1 | head -30
pyenv exec python3 ace_playbook.py --dry-run
pyenv exec python3 review_queue.py --stats
pyenv exec python3 skill_autofix.py --status
pyenv exec python3 flush_graphiti_pending.py --dry-run
pyenv exec python3 harness_changelog.py          # visible digest of harness mutations since last run (DoorDash Flux lesson: make the work visible)
pyenv exec python3 surface_gate.py --help        # deterministic pre-apply surface check against editable_surfaces.json (Flux gateway lesson)
```

3. Summarize: healthcheck OK, fixture D_in/D_out, agent_rollouts pass_rate, skill_autofix,
   ACE bullets, regressed/escalate, graph pending=0, gate_pass, pi ratings (`"agent":"pi"`).
4. Pi-native: `/self-improve` command (pai-learning-harness) for quick status.
5. Force loop now:

```bash
bash ~/.claude/hooks/claude-session-end
```

6. Drain queue now:

```bash
cd ~/.claude/MEMORY/LEARNING && pyenv exec python3 review_queue.py --auto-drain --min-age 0
```

7. Never auto-post to GitHub, never edit prod, never disable EnforcementGate safety.
   Never mutate paths listed under `editable_surfaces.json` → deny.
   Never edit `~/.pi/agent/extensions/**` via skill_autofix (deny list).

## Pi parity surfaces

| Surface | Path |
|---|---|
| Ratings + skill attribution | `pai-learning-harness.ts` → `SIGNALS/ratings.jsonl` |
| ACE / FailurePatternReminder every turn | `pai-learning-harness.ts` before_agent_start |
| EnforcementGate (block → follow-up) | `pai-enforcement-gate.ts` agent_end |
| SessionEnd full loop | `claude-bridge.ts` → `claude-session-end` |
| skill_autofix targets | `~/.claude/commands/*.md` + `~/.pi/agent/skills/**` |
| Default pi skill | `~/.pi/agent/skills/pi-agent/SKILL.md` |

## Grok Build parity check

When asked if pi matches Grok Build harness:

1. Run `bash ~/.grok/tools/claude-harness-parity-audit.sh` (Grok side; Claude SSOT).
2. Diff detectors: Claude `EnforcementGate.hook.ts` vs pi `pai-enforcement-gate.ts` (DETECTORS + ALWAYS_ON must match).
3. Confirm shared MEMORY/* health via `harness_healthcheck.py`.

---
name: self-improve
description: >
  Status and control surface for the autonomous self-improvement loop.
  Use when asked about self-learning, autonomy, lesson queue, skill autofix,
  review_queue, or /self-improve.
user-invocable: true
---

# Self-improve loop status

Lil'Log map: [Harness Engineering for Self-Improvement](https://lilianweng.github.io/posts/2026-07-04-harness/)
— ACE playbook, Self-Harness mine/propose/validate, editable surfaces outside the loop.

**Full parity agents:** Claude Code, Grok Build, **pi**, **Codex CLI**.
Pi: `pai-learning-harness.ts` (ACE every turn + ratings), `pai-enforcement-gate.ts` (block follow-up),
`claude-bridge.ts` (SessionEnd loop), skills under `~/.pi/agent/skills/`.
Codex: `~/.codex/hooks/pai_*.py` + `~/.codex/bin/codex-harness` (EXIT → SessionEnd). See `~/.codex/PARITY.md`.

**Graphiti freshness:** SessionEnd runs `session_graphiti_autoseed.py` (durable transcript
excerpts → pending) → `sync_graph_memory.py` → `flush_graphiti_pending.py` (→ Neo4j via
MCP HTTP `:8000/mcp`). Mid-session: `graphiti_bypassed` **block** (read) +
`graphiti_writeback_skipped` **warn** (write).

## Steps

1. Read `~/.claude/MEMORY/LEARNING/AUTONOMY.md` for architecture (includes pi parity table).
2. Run:

```bash
cd ~/.claude/MEMORY/LEARNING && pyenv exec python3 harness_healthcheck.py
pyenv exec python3 skill_burnin.py --status
pyenv exec python3 skill_burnin.py --resolve-stall --apply   # park edits with 0 post-apply sessions
pyenv exec python3 chronic_failures.py                        # recidivism report for ladder-top patterns
pyenv exec python3 held_out_suite.py --gate
pyenv exec python3 agent_rollouts.py --gate
pyenv exec python3 self_harness.py --gate
pyenv exec python3 skill_autofix.py --dry-run 2>&1 | head -30
pyenv exec python3 ace_playbook.py --dry-run
pyenv exec python3 review_queue.py --stats
pyenv exec python3 skill_autofix.py --status
pyenv exec python3 flush_graphiti_pending.py --dry-run
pyenv exec python3 harness_changelog.py          # visible digest of harness mutations since last run
pyenv exec python3 surface_gate.py --help        # deterministic pre-apply surface check against editable_surfaces.json
```

3. Summarize: healthcheck OK, fixture D_in/D_out, agent_rollouts pass_rate, skill_autofix,
   ACE bullets, regressed/escalate, graph pending=0, gate_pass.
   Also flag: `taxonomy_integrity` (no `other` in PR_PATTERN_KEYWORDS; promote line-anchor),
   `review_recidivism` (same pair approved ≥10× = re-queue bug).
4. If user asks to "run the loop now":

```bash
bash ~/.claude/hooks/claude-session-end
```

5. If user asks to "drain the queue now":

```bash
cd ~/.claude/MEMORY/LEARNING && pyenv exec python3 review_queue.py --auto-drain --min-age 0
```

6. Never auto-post to GitHub, never edit prod, never disable EnforcementGate safety.
   Never mutate paths listed under `editable_surfaces.json` → deny.


## Phase A–D control surface (2026-07-17)

```bash
cd ~/.claude/MEMORY/LEARNING
pyenv exec python3 self_harness_status.py          # single dashboard
pyenv exec python3 skill_burnin.py --status
pyenv exec python3 skill_burnin.py --provisional-measure --apply
pyenv exec python3 agent_attribution.py --stats
pyenv exec python3 reclass_other.py --apply
pyenv exec python3 escalation_ladder.py --apply
pyenv exec python3 agent_effectiveness.py
pyenv exec python3 propose_held_out_fixtures.py
```


## Phases E–I

```bash
pyenv exec python3 weekly_harness_digest.py
# multi-host tags: host_agent_tag.jsonl
# SessionEnd runs reclass_other, escalation_ladder, agent_effectiveness, status
```


## Phases J–O
```bash
pyenv exec python3 harness_smoke_test.py
pyenv exec python3 skill_guardrail_refresh.py --apply
pyenv exec python3 agent_rollouts.py --gate --golden-only
```

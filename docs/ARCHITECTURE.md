# Architecture

## Loop (every SessionEnd)

```
ratings / FAILURES
  → self_improve.py          # pattern → lesson_autogen_*.md
  → evals / judge_outcomes
  → pattern_promotion
  → measure_effectiveness    # held-in verdicts
  → skill_autofix --apply    # bounded AUTO-LEARNED-GUARDRAILS only
  → enforcement_promotion
  → held_out_regression
  → lesson_dedup / lesson_evolve
  → review_queue --auto-drain
  → held_out_suite --gate
  → agent_rollouts --gate
  → self_harness --apply
  → graphiti autoseed / sync / flush (optional)
```

## Editable surface (meta-harness)

`editable_surfaces.json` is **outside** the mutation loop. Auto-edits may only touch allowlisted paths (lessons, skill EVOLVE blocks, ACE bullets, signals). Hooks, settings, and this policy file are deny-listed.

## Enforcement

`EnforcementGate.hook.ts` (Stop) arms detectors from:

- ALWAYS_ON structural gates
- `effectiveness_scores.json` escalate list
- `enforcement_config.json` overrides

## Eval gates

- **D_in**: known weaknesses must still fail bad fixtures
- **D_out**: good behaviors must not regress
- Fixtures are human-owned under `held_out_suite/fixtures/`

## Paths

`learning/harness_paths.py` centralizes `HARNESS_HOME`. Older scripts still use `~/.claude/MEMORY/...` defaults so a standard install works without edits.

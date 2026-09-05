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

`learning/harness_paths.py` is the single source of truth for runtime directories. Every Python stage derives mutable state from `HARNESS_HOME`; TypeScript hooks and pi extensions honor `HARNESS_HOME` before the legacy `PAI_DIR` fallback. Optional overrides include `HARNESS_LESSONS_DIR`, `HARNESS_MEETING_DIR`, `HARNESS_SCRUM_DIR`, `HARNESS_PROJECTS_DIR`, `HARNESS_PI_SKILLS`, and `BUNGRAPH_DB`.

## SessionEnd observability

The SessionEnd hook remains non-blocking, but each stage writes a dedicated log and appends an explicit status row under `MEMORY/LEARNING/DIAGNOSTICS/session-end/`. The latest aggregate status is recorded in `latest.tsv`, avoiding collisions between multiple harness installations and making swallowed stage failures visible.

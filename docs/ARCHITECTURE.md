# Architecture

## Loop (every SessionEnd)

The hook returns immediately, while one background worker acquires a per-install lock and executes the stages in dependency order. A second SessionEnd invocation records `already-running` in `skipped.tsv` instead of overlapping mutable state work.

```
ratings / FAILURES
  → ratings_hygiene / meeting_ingest / intent_how_audit
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
  → harness_changelog / surface-gate self-test
```

Stages are intentionally sequential. The small loss in wall-clock parallelism prevents related lesson, review, ledger, and graph queue files from being read and rewritten concurrently.

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

## Durable state I/O

`learning/state_io.py` owns reusable state operations. Whole-file updates use same-directory temporary files, `fsync`, and atomic replacement; JSONL readers isolate malformed rows; JSONL appenders coordinate through stable sidecar locks. Queue producers that update both Graphiti pending work and an ingestion ledger acquire both locks in sorted order and re-check the ledger inside the transaction.

## SessionEnd observability

The SessionEnd hook remains non-blocking, but each stage writes a dedicated log and appends an explicit status row under `MEMORY/LEARNING/DIAGNOSTICS/session-end/`. The latest aggregate status is recorded in `latest.tsv`; overlapping invocations are recorded in `skipped.tsv`. The native lock uses `flock` when available and an atomic directory lock with stale-PID recovery otherwise.

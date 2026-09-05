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
- validated `enforcement_config.json` overrides

`learning/harness_config.py` defines the accepted boolean and `off|warn|block` schema. Python health checks reject unknown keys or modes. Claude and pi hooks independently normalize the same known key set, ignore invalid values fail-safely, and never overwrite an existing malformed configuration.

## Eval gates

- **D_in**: known weaknesses must still fail bad fixtures
- **D_out**: good behaviors must not regress
- Fixtures are human-owned under `held_out_suite/fixtures/`

## Paths

`learning/harness_paths.py` is the single source of truth for runtime directories. Every Python stage derives mutable state from `HARNESS_HOME`; TypeScript hooks and pi extensions honor `HARNESS_HOME` before the legacy `PAI_DIR` fallback. Optional overrides include `HARNESS_LESSONS_DIR`, `HARNESS_MEETING_DIR`, `HARNESS_SCRUM_DIR`, `HARNESS_PROJECTS_DIR`, `HARNESS_PI_SKILLS`, and `BUNGRAPH_DB`.

## Durable state I/O

`learning/state_io.py` owns reusable state operations. Whole-file updates use same-directory temporary files, `fsync`, and atomic replacement; JSONL readers isolate malformed rows; JSONL appenders coordinate through stable sidecar locks. Queue producers that update both Graphiti pending work and an ingestion ledger acquire both locks in sorted order and re-check the ledger inside the transaction.

## Transactional review workflow

`learning/review_store.py` owns `pending_human_review.jsonl`. Producers enqueue semantic `(pattern, source)` keys under one lock and cannot replace records written by another process. Approval first changes a record from `pending` to `processing`, runs the source-specific side effect outside the queue lock, and then finalizes it as `approved` or `action_failed`. Failed actions retain an error and attempt count and require explicit `--retry-failed`; stale processing claims recover to `action_failed`. Rejections are atomic and never run approval side effects. Pending, processing, and failed actions remain gated from escalation.

## SessionEnd observability

The SessionEnd hook remains non-blocking, but each stage writes a dedicated log and appends a five-column status row—timestamp, stage, status, exit code, and duration in milliseconds—under `MEMORY/LEARNING/DIAGNOSTICS/session-end/`. Atomic `latest.json` summarizes total duration, stage count, failure count, and failed stage names. `skipped.json` records overlapping invocations without replacing the active run summary. The native lock uses `flock` when available and an atomic directory lock with stale-PID recovery otherwise.

## Quality thresholds

The complete test command enforces an 18% branch-aware repository coverage floor, up from the third-pass baseline of 11%. Shared durable state, transactional review, and validated configuration modules independently require 100% statement and branch coverage.

`held_out_suite.py --gate` is the deterministic behavioral gate. `agent_rollouts.py --gate` is a live-provider semantic supplement: empty responses or provider exceptions are recorded as skipped infrastructure checks, persisted in the latest diagnostic report, and never misclassified as harness behavior failures or written into performance history.

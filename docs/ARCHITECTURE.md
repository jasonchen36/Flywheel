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

`learning/harness_paths.py` is the single source of truth for runtime directories. Every Python stage derives mutable state from `HARNESS_HOME`; TypeScript hooks and pi extensions honor `HARNESS_HOME` before the legacy `PAI_DIR` fallback. Optional overrides include `HARNESS_LESSONS_DIR`, `HARNESS_MEETING_DIR`, `HARNESS_SCRUM_DIR`, `HARNESS_PROJECTS_DIR`, `HARNESS_PI_SKILLS`, and `BUNGRAPH_DB`. The surface gate accepts the shipped exact-`path` and `glob` rule forms, maps default Claude and pi skill prefixes onto these custom roots, validates policy structure, and fails closed on missing or malformed policy data.

## Durable state I/O

`learning/state_io.py` owns reusable state operations. Whole-file updates use same-directory temporary files, `fsync`, and atomic replacement; JSONL readers isolate malformed rows; single and batched JSONL appenders coordinate through stable ownership directories. `runtime/state-io.ts` implements the same token-and-PID protocol for Claude and pi, so ratings, pending judge work, and enforcement events cannot race Python rewrites. Dead owners recover immediately, malformed owner state ages out, bounded waits fail visibly, and a live owner is never evicted solely because of lock age. Queue producers that update both Graphiti pending work and an ingestion ledger acquire both locks in sorted order and re-check the ledger inside the transaction. Held-out state, ACE outputs, evaluation registries, judge queues, skill-autofix state, lessons, reports, and changelog snapshots use these same durable boundaries rather than direct mutable-state writes.

## Transactional summary ingestion

`learning/summary_ingest.py` owns meeting and scrum candidate discovery, signal filtering, provenance text, archive and ledger normalization, multi-file transactional commits, and optional Graphiti flushing. The host-specific scripts provide only paths, filename conventions, names, and group IDs. Flush subprocesses use the active Python interpreter rather than an undeclared `pyenv` dependency and run only when the current process actually committed rows after its lock-scoped ledger recheck.

## Transactional review workflow

`learning/review_store.py` owns `pending_human_review.jsonl`. Producers enqueue semantic `(pattern, source)` keys under one lock and cannot replace records written by another process. Approval first changes a record from `pending` to `processing`, runs the source-specific side effect outside the queue lock, and then finalizes it as `approved` or `action_failed`. Failed actions retain an error and attempt count and require explicit `--retry-failed`; stale processing claims recover to `action_failed`. Rejections are atomic and never run approval side effects. Pending, processing, and failed actions remain gated from escalation.

## SessionEnd observability

The SessionEnd hook remains non-blocking, but each stage writes a dedicated log and appends a five-column status row—timestamp, stage, status, exit code, and duration in milliseconds—under `MEMORY/LEARNING/DIAGNOSTICS/session-end/`. Atomic `latest.json` summarizes total duration, stage count, failure count, and failed stage names. `skipped.json` records overlapping invocations without replacing the active run summary. The native lock uses `flock` when available and an atomic directory lock with stale-PID recovery otherwise.

## Quality thresholds

The complete test command enforces a 49% branch-aware repository coverage floor, up from the original baseline of approximately 11%. Nine critical modules independently require 100% statement and branch coverage: shared durable state, transactional review, validated configuration, surface permissions, ratings hygiene, config-only enforcement promotion, shared summary ingestion, lesson deduplication, and pattern promotion. Direct lifecycle suites additionally cover held-out fixtures, self-harness orchestration, session auto-seeding, graph synchronization, lesson retirement, and skill burn-in; healthcheck's operational decision paths remain regression-tested across healthy, stale, corrupt, missing, and degraded installations.

Lesson retirement normalizes malformed effectiveness records, writes backfilled baselines and diagnostics atomically, and archives with collision-safe atomic renames. Lesson deduplication derives mutation targets from validated filenames rather than mutable frontmatter, uses semantic review-store deduplication, creates collision-safe backups, and makes partially completed merges idempotently recoverable. Pattern promotion rejects malformed model labels and thresholds and writes JSON-escaped, normalized, duplicate-safe taxonomy entries. Skill burn-in normalizes malformed edit ledgers and baseline rates while retaining explicit stall, reactivation, hold, and confirmation states.

`held_out_suite.py --gate` is the deterministic behavioral gate. Fixture documents are shape-validated and an existing malformed baseline fails closed. Self-harness reuses the held-out and rollout results produced during its validation stage instead of rerunning deterministic fixtures and live-model scenarios during the same command. Reverted skill edits are archived idempotently. Session auto-seed stores an explicit content hash and deduplicates pending and archived episodes inside one transaction; graph synchronization normalizes malformed score and ACE state and counts only successfully spawned BunGraph commands. `agent_rollouts.py --gate` is a live-provider semantic supplement: empty responses or provider exceptions are recorded as skipped infrastructure checks, persisted in the latest diagnostic report, and never misclassified as harness behavior failures or written into performance history.

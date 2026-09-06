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

`learning/state_io.py` owns reusable state operations. Whole-file updates use same-directory temporary files, `fsync`, and atomic replacement; JSONL readers isolate malformed rows; single and batched JSONL appenders coordinate through stable ownership directories. `runtime/state-io.ts` implements the same token-and-PID protocol for Claude and pi, so ratings, pending judge work, and enforcement events cannot race Python rewrites. Dead-owner cleanup is serialized by a companion recovery directory, disappearing lock paths are treated as contention rather than stale state, malformed owner state ages out, bounded waits fail visibly, and a live owner is never evicted solely because of lock age. Queue producers that update both Graphiti pending work and an ingestion ledger acquire both locks in sorted order and re-check the ledger inside the transaction. Held-out state, ACE outputs, evaluation registries, judge queues, skill-autofix state, lessons, reports, and changelog snapshots use these same durable boundaries rather than direct mutable-state writes.

## Transactional summary ingestion

`learning/summary_ingest.py` owns meeting and scrum candidate discovery, signal filtering, provenance text, archive and ledger normalization, multi-file transactional commits, and optional Graphiti flushing. The host-specific scripts provide only paths, filename conventions, names, and group IDs. Flush subprocesses use the active Python interpreter rather than an undeclared `pyenv` dependency and run only when the current process actually committed rows after its lock-scoped ledger recheck.

## Transactional review workflow

`learning/review_store.py` owns `pending_human_review.jsonl`. Producers enqueue semantic `(pattern, source)` keys under one lock and cannot replace records written by another process. Approval first changes a record from `pending` to `processing`, runs the source-specific side effect outside the queue lock, and then finalizes it as `approved` or `action_failed`. Failed actions retain an error and attempt count and require explicit `--retry-failed`; stale processing claims recover to `action_failed`. Rejections are atomic and never run approval side effects. Pending, processing, and failed actions remain gated from escalation.

## Feedback-loop decision integrity

`judge_outcomes.py` distinguishes a valid clean verdict (`{"failures": []}`) from provider exceptions, empty responses, and malformed JSON. Unavailable or invalid provider output leaves the turn queued for retry without a second probe request. Valid result rows are deduplicated by a collision-resistant turn identity and committed with queue draining under one multi-file lock; malformed queue rows are quarantined in `invalid_judge.jsonl` so they remain inspectable without blocking later turns. Effectiveness scoring normalizes prior score and review state, validates sample thresholds, applies explicit objective → judge → subjective precedence, and preserves first-time soft regressions behind transactional human review. Held-out regression uses validated dates and sample sizes and relies on semantic `(pattern, source)` review-store deduplication.

`harness_changelog.py` stores SHA-256 fingerprints rather than relying on modification time and size. This detects same-size edits even when timestamps are preserved, upgrades older snapshots without breaking installation state, and excludes external symlinks, portable lock-owner directories, and transient atomic-write files from the watched surfaces.

## Self-improvement mutation integrity

`self_improve.py` routes background models through an explicit provider allowlist; unknown names and unconfigured cloud projects fail closed rather than falling through to another vendor. Structured lesson output is normalized into bounded string fields, and eval candidates require a snake-case ID, a meaningful predicate, and an exact pattern match. Durable reclassification uses `session_id|timestamp` turn identities, so multiple turns in one session cannot overwrite each other; pattern promotion stores those exact turns but applies thresholds to distinct sessions. Eval candidates and reclassification records are semantically deduplicated under locks, and lesson plus `MEMORY.md` mutations are lock-scoped. A rejected lesson write cannot be logged, indexed, or reported as successful.

`evals.py` validates the code catalog for duplicate or unsafe IDs, checks captured hook results against code-owned patterns and boolean verdicts, and persists exact-turn objective rows while accepting legacy timestamp state. Result replacement and registry reconciliation run under one multi-file lock; malformed registry entries are quarantined and pattern, source, version, orphan, and reactivation drift is logged. `lesson_evolve.py` validates score and candidate schemas, assigns stable proposal and batch identities, pauses generation while a review is pending, and limits approval to the latest batch. The chosen variant and all stale siblings transition together under lesson-and-ledger locks; a failed ledger commit restores the live lesson, and collision-safe backups preserve prior evidence.

`skill_autofix.py` treats the live skill, private Git snapshot, deterministic validation, and autofix ledger as a fail-closed mutation transaction. Missing snapshots, failed restores, and failed audit commits retain explicit `rollback-failed` or audit-failed states rather than erasing files or claiming success. Apply cycles and `skill_burnin.py` transitions share the ledger lock, generated validation commands remain allowlisted and shell-free, and path resolution rejects symlinks, directories, and root escapes. Healthcheck reports critical rollback or invalid states as errors and audit failures or quarantined ledger rows as warnings.

## SessionEnd observability

The SessionEnd hook remains non-blocking, but each stage writes a dedicated log and appends a five-column status row—timestamp, stage, status, exit code, and duration in milliseconds—under `MEMORY/LEARNING/DIAGNOSTICS/session-end/`. Atomic `latest.json` summarizes total duration, stage count, failure count, and failed stage names. `skipped.json` records overlapping invocations without replacing the active run summary. The native lock uses `flock` when available and an atomic directory lock with stale-PID recovery otherwise.

## Quality thresholds

The complete test command enforces an 81% branch-aware repository coverage floor, up from the original baseline of approximately 11%. Eighteen critical modules independently require 100% statement and branch coverage: shared durable state, transactional review, validated configuration, surface permissions, ratings hygiene, config-only enforcement promotion, shared summary ingestion, lesson deduplication, lesson evolution, pattern promotion, strict outcome judging, the binary evaluation registry, effectiveness measurement, held-out regression decisions, changelog integrity, the self-improvement engine, skill autofix, and skill burn-in. Direct lifecycle suites additionally cover held-out fixtures, self-harness orchestration, session auto-seeding, graph synchronization, lesson retirement, exact-turn pattern promotion, Git-backed autofix confirmation and rollback, and the end-to-end judge-to-effectiveness-to-review flow; healthcheck's operational decision paths remain regression-tested across healthy, stale, corrupt, missing, and degraded installations.

Lesson retirement normalizes malformed effectiveness records, writes backfilled baselines and diagnostics atomically, and archives with collision-safe atomic renames. Lesson deduplication derives mutation targets from validated filenames rather than mutable frontmatter, uses semantic review-store deduplication, creates collision-safe backups, and makes partially completed merges idempotently recoverable. Pattern promotion rejects malformed model labels and thresholds and writes JSON-escaped, normalized, duplicate-safe taxonomy entries. Skill burn-in normalizes malformed edit ledgers and baseline rates while retaining explicit stall, reactivation, hold, and confirmation states.

`held_out_suite.py --gate` is the deterministic behavioral gate. Fixture documents are shape-validated and an existing malformed baseline fails closed. Self-harness reuses the held-out and rollout results produced during its validation stage instead of rerunning deterministic fixtures and live-model scenarios during the same command. Reverted skill edits are archived idempotently. Session auto-seed stores an explicit content hash and deduplicates pending and archived episodes inside one transaction; graph synchronization normalizes malformed score and ACE state and counts only successfully spawned BunGraph commands. `agent_rollouts.py --gate` is a live-provider semantic supplement: empty responses or provider exceptions are recorded as skipped infrastructure checks, persisted in the latest diagnostic report, and never misclassified as harness behavior failures or written into performance history.

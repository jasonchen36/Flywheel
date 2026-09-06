# Self-Learning Harness (Flywheel)

Portable **agent self-improvement harness** for coding agents (Claude Code, Grok Build, pi, and similar).

Inspired by [Lilian Weng — Harness Engineering for Self-Improvement](https://lilianweng.github.io/posts/2026-07-04-harness/), ACE-style playbooks, and the **Flywheel Brake** memory lifecycle architecture.

This is the **employer-agnostic core**: ratings → lessons → effectiveness → skill guardrails → enforcement → held-out gates → optional Graphiti memory. It does **not** ship company-specific workflows, repos, or tribal process.

---

## Architecture & Capabilities

| Layer | Purpose |
|---|---|
| **SessionEnd Loop** | Mines failures, updates lessons, measures effectiveness, autofixes skills, promotes enforcement |
| **Flywheel Brake & Expiry** | Classifies rules (`compensation`, `boundary`, `context`), revalidates on model upgrades, prunes unearned rules via `lesson_retire.py` |
| **Rationale-Aware Evolution** | `lesson_evolve.py` mutates flat/regressed rules into structured `Rule \| Rationale \| Applicability` instructions |
| **EnforcementGate (Stop Hook)** | Hard deterministic stop hook that blocks unverified completion claims and weak paper traces |
| **Held-Out Side-Effect Check** | `held_out_regression.py` ensures fixing pattern $A$ does not silently regress unrelated pattern $B$ |
| **Precondition Enumeration** | Enforces explicit precondition enumeration before action execution (2.83x gain over generic CoT) |
| **Deterministic Controls** | Wraps mutating operations in hard code checks and tool wrappers rather than prose system prompts |
| **Independent Observer Verification** | Stop hooks and verification loops use independent, specialized tools (linters, AST checks, schema dry-runs) |
| **$pass^k$ Reliability Benchmark** | Evaluates multi-step agent success across all $k$ attempts ($pass^k$) rather than $pass@k$ |
| **RatingCapture** | Explicit 1–10 + optional implicit sentiment → `ratings.jsonl` |
| **Human Policy Ratification** | `review_queue.py` gates candidate rules so a human approves input policy once before hard enforcement |
| **Meeting / Scrum → Graphiti** | Thin host commands share one transactional ingestion foundation, preserve speaker authority, and tag `[TENTATIVE_PROPOSAL]` vs `[RATIFIED_DECISION]` |

---

## Empty Graphiti + Personal Skills

| Path | Contents |
|---|---|
| [`graphiti/`](graphiti/) | Empty Neo4j volume + MCP bootstrap (no employer data) |
| [`skills/`](skills/) | Personal skills: self-improve, model-tiering, instincts, caveman, … |

```bash
# Graphiti (fresh DB)
cd graphiti && cp .env.example .env   # add GOOGLE_API_KEY
./scripts/bootstrap.sh && ./scripts/start-mcp.sh
```

---

## Quick Install

Flywheel requires **Python 3.10+** and **Bun** for TypeScript hooks. The installer uses `rsync` when available and falls back to `tar` automatically.

```bash
git clone git@github.com:jasonchen36/Flywheel.git
cd Flywheel
python3 -m pip install -r requirements.txt
./install.sh
# Custom location: HARNESS_HOME=~/my-flywheel ./install.sh
# Explicit pi destination: HARNESS_PI_EXTENSIONS=~/.pi/agent/extensions ./install.sh
```

The installer enforces the Python version floor, seeds state atomically, honors an explicit pi-extension destination even when its parents do not exist, and creates collision-free backups of existing hooks before replacement. Then wire hooks from `templates/settings.hooks.snippet.json` into your Claude Code `settings.json`. When using a custom location, export the same `HARNESS_HOME` for the host process; Python stages, hooks, and pi extensions all honor it.

---

## Layout After Install

```
$HARNESS_HOME/   # default ~/.claude
  MEMORY/
    LEARNING/          # Python loop + SIGNALS + fixtures
      evals.py                 # Binary pass/fail eval suite
      measure_effectiveness.py # Held-in before/after verdict engine
      held_out_regression.py   # Held-out side-effect regression detector
      lesson_evolve.py         # Rationale-aware evolutionary lesson mutation
      lesson_retire.py         # Flywheel brake zombie lesson retirement
      review_queue.py          # Human policy ratification review queue
      scrum_graphiti_ingest.py # Provenance-aware scrum transcript ingest
      meeting_summary_ingest.py# Provenance-aware meeting summary ingest
      summary_ingest.py        # Shared transactional ingestion foundation
    STATE/             # scores, ACE playbook, enforcement_config, lesson_archive
    lessons/           # lesson_autogen_*.md
  hooks/               # TypeScript / bash hooks (EnforcementGate, StopHooks, hook-io)
  runtime/             # Bun/Python-compatible portable state-lock protocol
  meeting-summaries/   # drop *.summary.md for Graphiti ingest
  skills/
```

---

## Runtime Requirements

- Python 3.10+ and the packages in `requirements.txt`
- Bun for Claude-compatible TypeScript hooks
- `rsync` or `tar` for installation; `tar` is the automatic fallback
- Optional: OpenCode (`deepseek-v4-flash`), Vertex Gemini, or Anthropic for background LLM labels (`PAI_BACKGROUND_LLM_PROVIDER=opencode`)
- Optional: [Graphiti MCP](https://github.com/getzep/graphiti) on an HTTP(S) `/mcp` endpoint configured through `GRAPHITI_MCP_URL`
- Claude Code hooks host (or pi extensions under `pi/`)

---

## Healthcheck & Diagnostics

```bash
cd ~/.claude/MEMORY/LEARNING

# Check harness health, background LLM, and active skill autofixes
python3 harness_healthcheck.py

# Run binary eval suite dry-run
python3 evals.py --dry-run

# Run held-out regression check
python3 held_out_regression.py

# Check zombie lesson retirement status
python3 lesson_retire.py

# Run held-out suite gate
python3 held_out_suite.py --gate
```

---

## Multi-Agent Compatibility

| Host | How Harness Attaches |
|---|---|
| Claude Code | Native hooks + SessionEnd script |
| Grok Build | `[compat.claude] hooks` reading same Claude paths |
| pi | `pi/*.ts` extensions + SessionEnd via Claude-compatible bridge |

Tag ratings with `agent: claude|grok|pi` (hooks set this when possible). Claude and pi append ratings, pending judge work, and enforcement events through the same ownership-directory lock protocol used by Python rewriters, preventing cross-runtime lost updates. Dead-owner recovery is serialized across contenders, disappearing paths are never misclassified as stale, and long-running live owners are never evicted solely because of lock age.

---

## Runtime Diagnostics and Review Recovery

SessionEnd writes stage logs plus `latest.tsv` and an atomic `latest.json` under `MEMORY/LEARNING/DIAGNOSTICS/session-end/`. The JSON summary includes total duration, stage counts, failed stage names, and whether the run completed cleanly or with failures. Overlapping runs are recorded separately in `skipped.tsv` and `skipped.json`.

Review approvals use `pending → processing → approved|action_failed`. If a source-specific side effect fails, inspect the stored `action_error`, correct the cause, and retry explicitly:

```bash
python3 "$HARNESS_HOME/MEMORY/LEARNING/review_queue.py" \
  --approve PATTERN --source SOURCE --retry-failed
```

`harness_healthcheck.py --json` reports malformed review rows, unknown statuses, processing claims, failed actions, and invalid enforcement configuration. Outcome judging treats empty or malformed provider output as unavailable and leaves the turn queued for retry; malformed queue rows are moved to `SIGNALS/invalid_judge.jsonl` so they remain inspectable without blocking later work. Valid judge results, queue draining, and result deduplication share one transaction boundary.

`self_improve.py` applies durable reclassification by exact `session_id|timestamp` identity, validates structured lesson and eval-candidate schemas, and fails closed on unknown background provider names or missing cloud projects. Candidate, reclassification, lesson, and index mutations are semantically deduplicated or lock-scoped; rejected lesson content cannot be reported or indexed as written. Pattern promotion retains exact turns for evidence while counting distinct sessions toward promotion thresholds.

`harness_changelog.py` fingerprints watched files with SHA-256, so same-size edits are detected even when timestamps are preserved. It excludes external symlinks, transient atomic-write files, and portable lock-owner directories while accepting and upgrading older size-and-mtime snapshots.

---

## Development and Validation

Install the development tools and run the complete repository quality gate:

```bash
make install-dev
make check
```

The gate runs Ruff, mypy with Python 3.10 compatibility, Python compilation, the complete branch-aware pytest suite with a 71% repository non-regression floor, a Bun build of all hooks and pi extensions, Bandit, Python and JavaScript dependency audits, and ShellCheck. Fourteen critical modules independently require 100% statement and branch coverage: durable state I/O, transactional review storage, validated configuration, surface permissions, ratings hygiene, config-only enforcement promotion, shared summary ingestion, lesson deduplication, pattern promotion, strict outcome judging, effectiveness measurement, held-out regression decisions, hashed changelog integrity, and the self-improvement engine. Direct lifecycle tests additionally cover held-out fixture validation, self-harness result reuse, session auto-seeding, graph synchronization, lesson retirement, skill burn-in, exact-turn pattern promotion, and the judge-to-effectiveness-to-human-review flow without live network or model dependencies. Individual targets include `make test`, `make coverage-foundations`, `make lint`, `make hooks`, `make security`, and `make shellcheck`.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for change and test expectations and [`SECURITY.md`](SECURITY.md) for responsible reporting guidance.

---

## Portable Knowledge Pack

| File | Contents |
|---|---|
| [`docs/principles.md`](docs/principles.md) | Sanitized engineering principles distilled from personal errors log (~195 portable rules + Standing Rules 1–23) |
| [`docs/principles.json`](docs/principles.json) | Machine-readable same set |
| [`docs/memory.md`](docs/memory.md) | Harness **logic-only** memory (SessionEnd order, enforcement detectors, ACE bullets, pattern ids) |

No employer product data, tickets, or meeting transcripts.

---

## What is Intentionally Out of Scope

- Employer-specific skills (Jira projects, warehouse YAML, Airflow runbooks)
- Company secrets, MCP server credentials, production project IDs
- Full PAI product / identity system (hooks that import PAI Tools need that stack or stubs)

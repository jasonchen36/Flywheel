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
| **Meeting / Scrum → Graphiti** | `scrum_graphiti_ingest.py` & `meeting_summary_ingest.py` preserve speaker authority & tag `[TENTATIVE]` vs `[RATIFIED]` |

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

```bash
git clone git@github.com:jasonchen36/Flywheel.git
cd Flywheel
./install.sh
# or: HARNESS_HOME=~/.claude ./install.sh
```

Then wire hooks (see `templates/settings.hooks.snippet.json`) into your Claude Code `settings.json`.

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
    STATE/             # scores, ACE playbook, enforcement_config, lesson_archive
    lessons/           # lesson_autogen_*.md
  hooks/               # TypeScript / bash hooks (EnforcementGate, StopHooks, hook-io)
  meeting-summaries/   # drop *.summary.md for Graphiti ingest
  skills/
```

---

## Runtime Requirements

- Python 3.11+ (pyenv recommended)
- Optional: OpenCode (`deepseek-v4-flash`), Vertex Gemini, or Anthropic for background LLM labels (`PAI_BACKGROUND_LLM_PROVIDER=opencode`)
- Optional: [Graphiti MCP](https://github.com/getzep/graphiti) on `GRAPHITI_MCP_URL` (default `http://127.0.0.1:8000/mcp`)
- Claude Code hooks host (or pi extensions under `pi/`)

---

## Healthcheck & Diagnostics

```bash
cd ~/.claude/MEMORY/LEARNING

# Check harness health, background LLM, and active skill autofixes
python3 self_harness_status.py

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

Tag ratings with `agent: claude|grok|pi` (hooks set this when possible).

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

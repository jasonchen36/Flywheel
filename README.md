# Self-Learning Harness

Portable **agent self-improvement harness** for coding agents (Claude Code, Grok Build, pi, and similar).

Inspired by [Lilian Weng — Harness Engineering for Self-Improvement](https://lilianweng.github.io/posts/2026-07-04-harness/) and ACE-style playbooks.

This is the **employer-agnostic core**: ratings → lessons → effectiveness → skill guardrails → enforcement → held-out gates → optional Graphiti memory. It does **not** ship company-specific workflows, repos, or tribal process.

## What you get

| Layer | Purpose |
|---|---|
| **SessionEnd loop** | Mine failures, update lessons, measure, autofix skills, promote enforcement |
| **ACE playbook** | Itemized bullets injected every turn (not a blob rewrite) |
| **EnforcementGate** | Block/warn on unverified completion, weak claims, tool misuse patterns |
| **RatingCapture** | Explicit 1–10 + optional implicit sentiment → `ratings.jsonl` |
| **Held-out suite + agent rollouts** | D_in / D_out gates so harness edits cannot silently regress |
| **Blast-radius patterns** | Confirm/block dangerous bash (customize for your cloud) |
| **Model tiering + retrieval SOP** | Default cheap models; graph-first research |
| **Meeting summary → Graphiti** | Continuous tribal knowledge ingest (optional MCP) |
| **Intent vs HOW audit** | Flag bitter-lesson HOW scaffolding for human deprecation |

## Empty Graphiti + personal skills

| Path | Contents |
|---|---|
| [`graphiti/`](graphiti/) | Empty Neo4j volume + MCP bootstrap (no employer data) |
| [`skills/`](skills/) | Personal skills: self-improve, model-tiering, instincts, caveman, … |

```bash
# Graphiti (fresh DB)
cd graphiti && cp .env.example .env   # add GOOGLE_API_KEY
./scripts/bootstrap.sh && ./scripts/start-mcp.sh
```

## Quick install

```bash
git clone git@github.com:jasonchen36/Flywheel.git
cd Flywheel
./install.sh
# or: HARNESS_HOME=~/.claude ./install.sh
```

Then wire hooks (see `templates/settings.hooks.snippet.json`) into your Claude Code `settings.json`.

## Layout after install

```
$HARNESS_HOME/   # default ~/.claude
  MEMORY/
    LEARNING/          # Python loop + SIGNALS + fixtures
    STATE/             # scores, ACE playbook, enforcement_config
    lessons/           # lesson_autogen_*.md
  hooks/               # TypeScript / bash hooks
  meeting-summaries/   # drop *.summary.md for Graphiti ingest
  skills/
```

## Runtime requirements

- Python 3.11+ (pyenv recommended)
- Optional: Vertex Gemini / Anthropic for background LLM labels (`PAI_BACKGROUND_LLM_PROVIDER=gemini`)
- Optional: [Graphiti MCP](https://github.com/getzep/graphiti) on `GRAPHITI_MCP_URL` (default `http://127.0.0.1:8000/mcp`)
- Claude Code hooks host (or pi extensions under `pi/`)

## Healthcheck

```bash
cd ~/.claude/MEMORY/LEARNING
python3 harness_healthcheck.py
python3 held_out_suite.py --gate
```

## Multi-agent

| Host | How harness attaches |
|---|---|
| Claude Code | native hooks + SessionEnd script |
| Grok Build | `[compat.claude] hooks` reading same Claude paths |
| pi | `pi/*.ts` extensions + SessionEnd via Claude-compatible bridge |

Tag ratings with `agent: claude|grok|pi` (hooks set this when possible).

## Portable knowledge pack

| File | Contents |
|---|---|
| [`docs/principles.md`](docs/principles.md) | Sanitized engineering principles distilled from a personal errors log (~195 portable rules) |
| [`docs/principles.json`](docs/principles.json) | Machine-readable same set |
| [`docs/memory.md`](docs/memory.md) | Harness **logic-only** memory (SessionEnd order, enforcement detectors, ACE bullets, pattern ids) |

No employer product data, tickets, or meeting transcripts.

## What is intentionally out of scope

- Employer-specific skills (Jira projects, warehouse YAML, Airflow runbooks)
- Company secrets, MCP server credentials, production project IDs
- Full PAI product / identity system (hooks that import PAI Tools need that stack or stubs)

RatingCapture / some hooks import optional PAI helpers. If those are missing, prefer explicit ratings and EnforcementGate still run with local fs paths.

## Customize for a new employer

See [docs/MIGRATE_EMPLOYER.md](docs/MIGRATE_EMPLOYER.md).

## License

Private repository. All rights reserved unless you add a LICENSE file.

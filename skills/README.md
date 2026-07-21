# Personal portable skills

Employer-agnostic skills for Claude Code / Grok / pi.

## Install

```bash
# via harness installer
./install.sh

# or manual
rsync -a --exclude README.md skills/ ~/.agents/skills/
rsync -a skills/self-improve skills/model-tiering skills/pi-agent skills/instincts ~/.pi/agent/skills/
```

## Included (high level)

- **self-improve**, **model-tiering** — harness control + cost/model routing
- **instincts**, **caveman**, **no-mistakes**, **evidence-check** — behavior
- **graphify**, **Agents**, **gnhf**, **hooks** — agent tooling
- **transcript-extract**, **diarize**, **skill-patch** — capture lessons
- **small-model-*** , **token-retro** — efficiency
- **playwright**, **spreadsheet**, **design-doc-authoring** — general craft
- **grok-create-skill**, **grok-check-work** — Grok utilities
- plus session/help/verify/sync helpers

## Not included

Data-platform / employer marketplace skills (Airflow, warehouse YAML, Streamverse, etc.).

# Migrating to a new employer

## Do copy

- This repo (`learning/`, `hooks/`, `pi/`, `config/`, `templates/`)
- Your **generic** lessons that are not company-secret
- Held-out fixtures that encode universal agent hygiene (completion artifacts, no force-push, etc.)

## Do not copy

- Company secrets, tokens, service account keys
- Internal MCP endpoints, VPN-only URLs
- Employer-specific skills (ticket systems, data warehouse runbooks, internal tool names)
- Production project IDs and customer data in ratings/transcripts
- `SIGNALS/ratings.jsonl` if it contains confidential incident text (export redacted subset only)

## Checklist on day 1 at new job

1. `./install.sh` with clean `HARNESS_HOME`
2. Edit `PAI/USER/PAISECURITYSYSTEM/patterns.yaml` for new cloud projects / tools
3. Set `GCP_PROJECT` / Vertex location if using Gemini background LLM
4. Point Graphiti at a **new** Neo4j (empty group_id or new `GRAPHITI_GROUP_ID`)
5. Replace meeting corpus path: `HARNESS_MEETING_DIR`
6. Wire hooks; run `harness_healthcheck.py`
7. Seed 3–5 explicit ratings after first week so skill_autofix has signal
8. Run `intent_how_audit.py` monthly to delete HOW scaffolding that SOTA models no longer need

## Separating “your OS” from “employer skills”

| Layer | Lives where | Portable? |
|---|---|---|
| Self-learning loop | this harness | yes |
| Enforcement detectors | hooks + config | yes (tune strings) |
| Employer skills / RTFM | separate skills repo | no — rewrite |
| Tribal meeting memory | Graphiti group | new instance |

Keep employer skills in a **different** git repo you can leave behind. Keep this harness as your personal agent OS.

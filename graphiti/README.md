# Empty Graphiti stack (personal)

Fresh Graphiti + Neo4j with a **new Docker volume**. No employer graph data.

Upstream: [getzep/graphiti](https://github.com/getzep/graphiti)

## Quick start

```bash
cd graphiti
cp .env.example .env   # edit passwords + GOOGLE_API_KEY (or Vertex)
./scripts/bootstrap.sh
./scripts/start-mcp.sh   # HTTP MCP on :8000
```

Merge `mcp.json.example` into `~/.mcp.json` / Claude MCP config:

```json
"graphiti-memory": { "type": "http", "url": "http://localhost:8000/mcp" }
```

Harness env:

```bash
export GRAPHITI_MCP_URL=http://127.0.0.1:8000/mcp
export GRAPHITI_GROUP_ID=personal
```

## Empty by design

| Component | Volume / path | Reuse employer data? |
|---|---|---|
| Neo4j | `graphiti_neo4j_personal_data` | **No** — different volume name |
| MCP | clone under `~/graphiti-memory-personal` | No |
| group_id | `personal` | New group |

## Optional macOS always-on

1. Edit `launchd/com.graphiti.memory.plist.example` paths
2. Copy to `~/Library/LaunchAgents/com.graphiti.memory.plist`
3. `launchctl load ~/Library/LaunchAgents/com.graphiti.memory.plist`

## Do not bring from work

- Existing Neo4j volume `graphiti_neo4j_data`
- Employer Vertex project IDs / company LLM proxies
- Flushed episode archives full of company content

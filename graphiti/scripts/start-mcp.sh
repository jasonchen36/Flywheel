#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${GRAPHITI_INSTALL_DIR:-$HOME/graphiti-memory-personal}"
VENV_DIR="${GRAPHITI_VENV:-$HOME/.local/share/graphiti-memory-venv}"
# shellcheck source=/dev/null
if [ -f "$ROOT/.env" ]; then set -a; source "$ROOT/.env"; set +a; fi
export CONFIG_PATH="${CONFIG_PATH:-$INSTALL_DIR/mcp_server/config/config.yaml}"
export NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
export NEO4J_USER="${NEO4J_USER:-neo4j}"
export NEO4J_PASSWORD="${NEO4J_PASSWORD:-changeme-graphiti}"
export GRAPHITI_TELEMETRY_ENABLED="${GRAPHITI_TELEMETRY_ENABLED:-false}"
exec "$VENV_DIR/bin/python3" "$INSTALL_DIR/mcp_server/main.py" \
  --llm-provider "${GRAPHITI_LLM_PROVIDER:-gemini}" \
  --database-provider neo4j \
  --model "${GRAPHITI_LLM_MODEL:-gemini-2.5-flash}" \
  --embedder-provider "${GRAPHITI_EMBEDDER_PROVIDER:-gemini}" \
  --embedder-model "${GRAPHITI_EMBEDDER_MODEL:-text-embedding-004}" \
  --temperature 0.0

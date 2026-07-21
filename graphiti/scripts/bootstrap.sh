#!/usr/bin/env bash
# Bootstrap EMPTY Graphiti (Neo4j + MCP) for personal use.
# Does NOT copy any existing graph data.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
INSTALL_DIR="${GRAPHITI_INSTALL_DIR:-$HOME/graphiti-memory-personal}"
VENV_DIR="${GRAPHITI_VENV:-$HOME/.local/share/graphiti-memory-venv}"

echo "==> Empty Neo4j (volume: graphiti_neo4j_personal_data)"
cd "$ROOT"
if [ -f .env ]; then set -a; source .env; set +a; fi
docker compose up -d

echo "==> Clone getzep/graphiti (if missing)"
if [ ! -d "$INSTALL_DIR/.git" ]; then
  git clone --depth 1 https://github.com/getzep/graphiti.git "$INSTALL_DIR"
else
  echo "    already present: $INSTALL_DIR"
fi

echo "==> Config"
mkdir -p "$INSTALL_DIR/mcp_server/config"
cp "$ROOT/config/config.yaml.example" "$INSTALL_DIR/mcp_server/config/config.yaml"
if [ -f "$ROOT/.env" ]; then
  # shellcheck disable=SC1091
  set -a; source "$ROOT/.env"; set +a
fi

echo "==> Python venv + install mcp_server"
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install -U pip wheel
if [ -f "$INSTALL_DIR/mcp_server/pyproject.toml" ]; then
  pip install -e "$INSTALL_DIR/mcp_server"
elif [ -f "$INSTALL_DIR/pyproject.toml" ]; then
  pip install -e "$INSTALL_DIR"
fi

echo "==> MCP config snippet → $REPO_ROOT/graphiti/mcp.json.example (merge into ~/.mcp.json)"
echo "==> Start MCP: $ROOT/scripts/start-mcp.sh"
echo "==> Use a NEW group_id (e.g. personal). Do not reuse employer Neo4j volumes."

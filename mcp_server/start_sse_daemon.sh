#!/bin/bash
# Graphiti MCP Server - SSE Shared Daemon on Darwin
# Serves multiple CC sessions over HTTP, per-request group_id scoping

set -euo pipefail

SERVER_DIR="$(dirname "$(readlink -f "$0")")"

# Source secrets for OpenAI API key
if [ -f "$HOME/.secrets/load-all.sh" ]; then
    set -a
    source "$HOME/.secrets/load-all.sh"
    set +a
fi

# Source server .env (FalkorDB, embeddings, model config)
if [ -f "$SERVER_DIR/.env" ]; then
    set -a
    source "$SERVER_DIR/.env"
    set +a
fi

# Activate venv
source "$SERVER_DIR/.venv/bin/activate"

exec python3 "$SERVER_DIR/graphiti_mcp_server.py" \
    --transport sse \
    --host 192.168.4.1 \
    --port "${GRAPHITI_SSE_PORT:-8500}" \
    --use-custom-entities

#!/usr/bin/env bash
# Start the full swarm stack (Anti-Gravity + Ollama).
# Run from the repo root: bash swarm/start.sh

set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Load vendor API keys (DIGIKEY_CLIENT_ID, MOUSER_API_KEY, etc.) so the agent
# processes inherit them even when started from a terminal that hasn't sourced
# the shell profile. Credentials are user configuration, never repository data.
for ENVFILE in "$HOME/.config/designstudio/env"; do
    if [ -f "$ENVFILE" ]; then
        set -a; . "$ENVFILE"; set +a
        break
    fi
done

echo "[swarm] Starting Ollama..."
OLLAMA_BIN="${OLLAMA_BIN:-$(command -v ollama || true)}"
if [ -z "$OLLAMA_BIN" ]; then
  echo "[swarm] ERROR: ollama is not on PATH; set OLLAMA_BIN" >&2
  exit 1
fi
OLLAMA_MODELS="${OLLAMA_MODELS:-${XDG_DATA_HOME:-$HOME/.local/share}/ollama/models}"
export OLLAMA_MODELS
pgrep -x ollama > /dev/null 2>&1 || \
  "$OLLAMA_BIN" serve >> /tmp/ollama.log 2>&1 &

echo "[swarm] Waiting for Ollama..."
for i in $(seq 1 10); do
  python3 -c "
import urllib.request, sys
try:
    urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=2)
    sys.exit(0)
except:
    sys.exit(1)
" && break
  sleep 2
done

echo "[swarm] Starting Anti-Gravity MCP server..."
cd "$REPO/swarm/antigravity"
python3 server.py >> /tmp/antigravity.log 2>&1 &
AG_PID=$!

sleep 2
if kill -0 $AG_PID 2>/dev/null; then
  echo "[swarm] Anti-Gravity running (PID $AG_PID) on http://127.0.0.1:8765/mcp"
else
  echo "[swarm] ERROR: Anti-Gravity failed to start — check /tmp/antigravity.log"
  exit 1
fi

echo ""
echo "Stack ready:"
echo "  Ollama:        http://127.0.0.1:11434"
echo "  Anti-Gravity:  http://127.0.0.1:8765/mcp"
echo ""
echo "Verify: python3 $REPO/swarm/verify_setup.py"

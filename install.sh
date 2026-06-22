#!/usr/bin/env bash
# OpenHealth Installer — macOS (Bash)
# Run from the project root: ./install.sh
#
# Docker Desktop must be installed manually first (it usually needs a restart).
# Safe to run more than once: it installs Ollama if missing, starts Docker/Ollama
# if they're stopped, and re-pulls the models (Ollama fetches only changed layers),
# so a re-run doubles as an "update" pass.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "========================================"
echo "  OpenHealth Installer"
echo "========================================"
echo ""

# Models that fail to pull are collected and reported at the end.
FAILURES=()

# ── Helper functions ──────────────────────────────────────────────────────────

command_exists() {
    command -v "$1" &>/dev/null
}

# Wait up to 30 seconds for Ollama to respond on localhost:11434.
wait_for_ollama() {
    local waited=0
    printf "Waiting for Ollama to be ready"
    while [ $waited -lt 30 ]; do
        if curl -sf http://localhost:11434 &>/dev/null; then
            echo ""
            return 0
        fi
        printf "."
        sleep 2
        waited=$((waited + 2))
    done
    echo ""
    return 1
}

# Ensure the Ollama server is up before pulling models. A host install (brew or the
# .app) doesn't guarantee the server is currently running, and "ollama pull" needs it.
start_ollama_server() {
    if curl -sf http://localhost:11434 &>/dev/null; then
        return 0
    fi
    echo "Starting Ollama service..."
    if command_exists brew && brew services list 2>/dev/null | grep -q '^ollama'; then
        brew services start ollama &>/dev/null || true
    else
        # Background server (used when Ollama was installed as the .app/CLI directly).
        ollama serve &>/dev/null &
    fi
    wait_for_ollama
}

# Wait for the Docker daemon to accept commands (Docker Desktop is slow to start).
wait_for_docker() {
    local waited=0
    printf "Waiting for Docker to be ready"
    while [ $waited -lt 180 ]; do
        if docker info &>/dev/null; then
            echo ""
            return 0
        fi
        printf "."
        sleep 3
        waited=$((waited + 3))
    done
    echo ""
    return 1
}

# Pull one model into host Ollama, recording a failure instead of aborting (set -e).
pull_host_model() {
    local model="$1"
    echo ""
    echo "  Pulling: $model"
    ollama pull "$model" || FAILURES+=("$model")
}

# ── 1. Check / install Docker ─────────────────────────────────────────────────

if ! command_exists docker; then
    echo "Docker Desktop is not installed."
    echo "Docker Desktop is required to run OpenHealth and must be installed manually."
    echo ""
    echo "  1. Download and install Docker Desktop:"
    echo "       https://www.docker.com/products/docker-desktop/"
    echo "  2. Restart your computer if prompted (installation usually requires it)."
    echo "  3. Launch Docker Desktop and wait for it to finish starting."
    echo "  4. Re-run ./install.sh."
    echo ""
    exit 1
fi

# ── 2. Ensure the Docker daemon is running ────────────────────────────────────

echo "Checking Docker daemon..."
if ! docker info &>/dev/null; then
    echo "Docker is installed but not running. Attempting to start Docker Desktop..."
    open -a Docker &>/dev/null || true
    if ! wait_for_docker; then
        echo ""
        echo "Docker did not become ready in time."
        echo "Open Docker Desktop, wait for it to fully start, then re-run ./install.sh."
        exit 1
    fi
fi
echo "Docker is running."
echo ""

# ── 3. Load required models from config.defaults.json ────────────────────────

CONFIG_PATH="$SCRIPT_DIR/backend/config.defaults.json"
if [ ! -f "$CONFIG_PATH" ]; then
    echo "ERROR: Could not find backend/config.defaults.json"
    exit 1
fi

if ! command_exists python3; then
    echo "ERROR: python3 is required to read the model list from config.defaults.json."
    exit 1
fi

# Parse JSON and deduplicate. vision_model is INCLUDED — the backend preflight
# requires every model below, so omitting it makes a "successful" install fail.
MODELS=$(python3 - <<'EOF'
import json
with open("backend/config.defaults.json") as f:
    c = json.load(f)
seen = set()
for key in ["chat_model", "clinical_model", "verification_model", "vision_model", "embedding_model"]:
    v = c.get(key)
    if v and v not in seen:
        seen.add(v)
        print(v)
EOF
)

# ── 4. Check / install Ollama ─────────────────────────────────────────────────
# Ollama runs natively on the host (not in a container) so it can use the GPU. A
# container on macOS is CPU-only (no Metal access), far too slow for these models.

if ! command_exists ollama; then
    echo "Ollama is not installed."
    echo "Ollama provides the AI models that power OpenHealth."
    echo ""
    read -rp "Would you like to install Ollama now? (Y/N): " choice
    case "$choice" in
        [Yy]*)
            if command_exists brew; then
                echo ""
                echo "Installing Ollama via Homebrew..."
                brew install ollama

                if ! command_exists ollama; then
                    echo ""
                    echo "Ollama was installed but could not be found in PATH."
                    echo "Please open a new terminal and re-run ./install.sh"
                    exit 1
                fi
                echo "Ollama installed successfully."
            else
                echo ""
                echo "Homebrew is not available on this machine."
                echo "Please install Ollama manually and re-run this installer:"
                echo "  https://ollama.com/download/mac"
                exit 1
            fi
            ;;
        *)
            echo ""
            echo "Ollama is required. Install it and re-run ./install.sh:"
            echo "  https://ollama.com/download/mac"
            exit 1
            ;;
    esac
else
    echo "Ollama is already installed."
fi

echo ""
echo "Copying compose configuration (host Ollama)..."
cp "docker/docker-compose.ollama-host.yml" "docker-compose.yml"

# A fresh install (or a host where Ollama isn't up yet) won't have the server
# running, and every "ollama pull" below needs it.
if ! start_ollama_server; then
    echo ""
    echo "Ollama is installed but its server did not respond on localhost:11434."
    echo "Start Ollama and re-run ./install.sh."
    exit 1
fi

echo ""
echo "Pulling required AI models (this may take a while)..."
while IFS= read -r model; do
    [ -n "$model" ] && pull_host_model "$model"
done <<< "$MODELS"

# ── 5. Create data directory ──────────────────────────────────────────────────

if [ ! -d "data" ]; then
    echo ""
    echo "Creating ./data directory..."
    mkdir -p data
fi

# ── 6. Done ───────────────────────────────────────────────────────────────────

if [ ${#FAILURES[@]} -gt 0 ]; then
    echo ""
    echo "========================================"
    echo "  Setup incomplete"
    echo "========================================"
    echo ""
    echo "The following models failed to download:"
    for model in "${FAILURES[@]}"; do
        echo "  $model"
    done
    echo ""
    echo "Re-run ./install.sh to retry, or pull them manually with 'ollama pull <model>'."
    exit 1
fi

echo ""
echo "========================================"
echo "  Setup complete!"
echo "========================================"
echo ""
echo "Start OpenHealth with:"
echo ""
echo "  docker compose up"
echo ""
echo "Then open:"
echo "  http://localhost:3000"
echo ""

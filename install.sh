#!/usr/bin/env bash
# OpenHealth Installer — macOS (Bash)
# Run from the project root: ./install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "========================================"
echo "  OpenHealth Installer"
echo "========================================"
echo ""

# ── Helper functions ──────────────────────────────────────────────────────────

command_exists() {
    command -v "$1" &>/dev/null
}

# Wait up to 30 seconds for Ollama to respond on localhost:11434
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

# ── 1. Check / install Docker ─────────────────────────────────────────────────

if ! command_exists docker; then
    echo "Docker Desktop is not installed."
    echo "Docker Desktop is required to run OpenHealth."
    echo ""
    echo "Please install Docker Desktop manually:"
    echo "  https://www.docker.com/products/docker-desktop/"
    echo ""
    read -rp "Press Enter after installing Docker Desktop (or Ctrl+C to cancel)..."

    if ! command_exists docker; then
        echo ""
        echo "Docker CLI is still not available in this terminal."
        echo "Open a new terminal and re-run ./install.sh after Docker Desktop finishes installing."
        exit 1
    fi
fi

# ── 2. Check Docker daemon is running ─────────────────────────────────────────

echo "Checking Docker daemon..."
if ! docker info &>/dev/null; then
    echo ""
    echo "Docker is installed but not running."
    echo "Please open Docker Desktop from your Applications folder and wait for it to fully start."
    exit 1
fi
echo "Docker is running."
echo ""

# ── Helper: load required models from config.defaults.json ───────────────────

CONFIG_PATH="$SCRIPT_DIR/backend/config.defaults.json"
if [ ! -f "$CONFIG_PATH" ]; then
    echo "ERROR: Could not find backend/config.defaults.json"
    exit 1
fi

# Use python3 (always present on modern macOS) to parse JSON and deduplicate
MODELS=$(python3 - <<'EOF'
import json, sys
with open("backend/config.defaults.json") as f:
    c = json.load(f)
seen = set()
for key in ["chat_model", "clinical_model", "summary_model", "verification_model", "embedding_model"]:
    v = c.get(key)
    if v and v not in seen:
        seen.add(v)
        print(v)
EOF
)

# ── 3. Check / install Ollama ─────────────────────────────────────────────────

if command_exists ollama; then
    echo "Ollama is already installed."
    echo ""
    echo "Copying compose configuration (host Ollama)..."
    cp "docker/docker-compose.mac-ollama-host.yml" "docker-compose.yml"

    echo ""
    echo "Pulling required AI models (this may take a while)..."
    while IFS= read -r model; do
        echo ""
        echo "  Pulling: $model"
        ollama pull "$model"
    done <<< "$MODELS"
else
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

                # Start the Ollama service so we can pull models
                echo "Starting Ollama service..."
                brew services start ollama
                sleep 3

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

            echo ""
            echo "Copying compose configuration (host Ollama)..."
            cp "docker/docker-compose.mac-ollama-host.yml" "docker-compose.yml"

            echo ""
            echo "Pulling required AI models (this may take a while)..."
            while IFS= read -r model; do
                echo ""
                echo "  Pulling: $model"
                ollama pull "$model"
            done <<< "$MODELS"
            ;;
        *)
            echo ""
            echo "Using the bundled Ollama Docker container instead."
            echo "Copying compose configuration (Docker Ollama)..."
            cp "docker/docker-compose.mac-ollama-docker.yml" "docker-compose.yml"

            echo ""
            echo "Starting Ollama container to pull models..."
            docker compose up -d ollama

            if ! wait_for_ollama; then
                echo ""
                echo "Ollama container did not respond in time."
                echo "You can pull models manually once the container is running:"
                while IFS= read -r model; do
                    echo "  docker exec ollama ollama pull $model"
                done <<< "$MODELS"
                exit 1
            fi

            echo "Ollama is ready."
            echo ""
            echo "Pulling required AI models (this may take a while)..."
            while IFS= read -r model; do
                echo ""
                echo "  Pulling: $model"
                docker exec ollama ollama pull "$model"
            done <<< "$MODELS"

            echo ""
            echo "Stopping Ollama container (it will start again with the full stack)..."
            docker compose stop ollama
            ;;
    esac
fi

# ── 4. Create data directory ──────────────────────────────────────────────────

if [ ! -d "data" ]; then
    echo ""
    echo "Creating ./data directory..."
    mkdir -p data
fi

# ── 5. Done ───────────────────────────────────────────────────────────────────

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

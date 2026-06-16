# OpenHealth Prototype

A local-first medical record workspace with grounded chat over patient records.

## Table of Contents
- [Machine Requirements](#machine-requirements)
- [Getting Started](#getting-started)
- [Developers](#developers)

## Machine Requirements

### Operating system
- Windows 11 (or recent Windows 10) or macOS

### RAM
- Minimum: 16 GB RAM
- Recommended: 32 GB RAM for smoother local model usage

### Storage
You need free disk space for Docker Desktop, Ollama, and model downloads.

Approximate model sizes used by this project:
- nomic-embed-text: ~275 MB
- gemma4:e2b: ~7.2 GB
- dcarrascosa/medgemma-1.5-4b-it:Q4_K_M: ~3.3 GB

Model total: about 10.8 GB

Also account for:
- Docker Desktop install + runtime data: typically several GB and grows over time
- Ollama install and cache: typically around 1+ GB plus model storage
- Project data, logs, and future model updates

Recommended free space before install: at least 30 GB (40+ GB preferred for headroom).

## Getting Started
This path is designed for non-technical users.

### 1. Download the project
Choose one:
- Download ZIP from GitHub and extract it.
- If you already use Git: `git clone https://github.com/RaganaCorp/openhealth-prototype-1.git`

### 2. Install Docker Desktop
Install Docker Desktop directly from the official website, then launch it and wait for it to fully start before continuing.

- Windows: https://www.docker.com/products/docker-desktop/ (download the Windows installer, run it, then start Docker Desktop)
- macOS: https://www.docker.com/products/docker-desktop/ (choose the Apple Silicon or Intel chip build that matches your Mac, install it, then start Docker Desktop)

The installer in the next step will detect Docker and skip its own setup if Docker is already installed and running.

### 3. Run the installer
The installer checks Docker, helps with Ollama setup, copies the right Docker configuration, and pulls required AI models.

Windows (PowerShell, copy/paste):
```powershell
Set-Location "$HOME\Downloads\openhealth-prototype-1-main"
powershell -ExecutionPolicy Bypass -File .\install.ps1
```
Note: depending on how it is extracted, you might have another folder named `openhealth-prototype-1-main` within

macOS (Terminal, copy/paste):
```bash
cd "$HOME/Downloads/openhealth-prototype-1-main"
chmod +x ./install.sh
./install.sh
```

### 4. Start the app
From the project root:
```bash
docker compose up
```

### 5. Open in browser
- App: http://localhost:3000
- API docs (optional check): http://localhost:8000/docs

### 6. Stop the app later
In the terminal running Docker Compose, press Ctrl+C.

If you want to fully stop containers in the background:
```bash
docker compose down
```

### If you already have Docker and Ollama
You can still run the installer. It will skip installs, ensure the correct Docker config is in place, and pull any missing models.

## Developers
If you want to run this from source without Docker (hot reload for frontend/backend), use the developer guide:

- [DEVELOPERS.md](DEVELOPERS.md)

That guide covers local Python + Node setup, Ollama model pulls, and the no-Docker run commands.

# OpenHealth Prototype

A local-first medical record workspace with summary, and grounded chat.

## Table of Contents
- [Getting Started](#getting-started)
- [Developers](#developers)

## Getting Started
This path is designed for non-technical users.

### 1. Download the project
Choose one:
- Download ZIP from GitHub and extract it.
- If you already use Git: `git clone https://github.com/RaganaCorp/openhealth-prototype-1.git`

### 2. Run the installer
The installer checks Docker, helps with Ollama setup, copies the right Docker configuration, and pulls required AI models.

Windows (File Explorer):
1. Open the project folder in File Explorer.
2. Right-click `install.ps1`.
3. Click **Run with PowerShell**.

If Windows blocks script execution, open PowerShell and run:
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

macOS (Terminal):
```bash
cd /path/to/prototype-adam-1
chmod +x ./install.sh
./install.sh
```

### 3. Start the app
From the project root:
```bash
docker compose up
```

### 4. Open in browser
- App: http://localhost:3000
- API docs (optional check): http://localhost:8000/docs

### 5. Stop the app later
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

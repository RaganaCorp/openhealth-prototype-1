# OpenHealth Installer - Windows (PowerShell)
# Run from the project root: .\install.ps1
#
# Docker Desktop must be installed manually first (it usually needs a restart).
# Safe to run more than once: it installs Ollama if missing, starts Docker/Ollama
# if they're stopped, and re-pulls the models (Ollama fetches only changed layers),
# so a re-run doubles as an "update" pass.

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  OpenHealth Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# -- Helpers ------------------------------------------------------------------

function Test-Command($name) {
    return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

# Pull Machine + User PATH into the current session so a freshly-installed CLI
# (winget puts it on PATH for *new* shells) is usable without reopening the terminal.
function Update-SessionPath {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# Wait up to $TimeoutSec for Ollama to respond on localhost:11434.
function Wait-ForOllama {
    param([int]$TimeoutSec = 30)
    $waited = 0
    Write-Host "Waiting for Ollama to be ready" -NoNewline
    while ($waited -lt $TimeoutSec) {
        try {
            $null = Invoke-WebRequest -Uri "http://localhost:11434" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
            Write-Host ""
            return $true
        } catch {
            Write-Host "." -NoNewline
        }
        Start-Sleep -Seconds 2
        $waited += 2
    }
    Write-Host ""
    return $false
}

# Ensure the Ollama server is running before pulling models (a fresh install,
# or a host where the tray app isn't running yet, won't have it up).
function Start-OllamaServer {
    try {
        $null = Invoke-WebRequest -Uri "http://localhost:11434" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        return $true
    } catch {
        Write-Host "Starting Ollama service..." -ForegroundColor Cyan
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
        return (Wait-ForOllama)
    }
}

# Launch Docker Desktop from its usual install locations. Returns $true if found.
function Start-DockerDesktop {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) {
            Start-Process -FilePath $p | Out-Null
            return $true
        }
    }
    return $false
}

# Wait for the Docker daemon to accept commands (Docker Desktop is slow to start).
function Wait-ForDocker {
    param([int]$TimeoutSec = 180)
    $waited = 0
    Write-Host "Waiting for Docker to be ready" -NoNewline
    while ($waited -lt $TimeoutSec) {
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { Write-Host ""; return $true }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 3
        $waited += 3
    }
    Write-Host ""
    return $false
}

# Models that fail to pull are collected and reported at the end.
$pullFailures = @()

# -- 1. Check / install Docker ------------------------------------------------

if (-not (Test-Command "docker")) {
    Write-Host "Docker Desktop is not installed." -ForegroundColor Yellow
    Write-Host "Docker Desktop is required to run OpenHealth and must be installed manually."
    Write-Host ""
    Write-Host "  1. Download and install Docker Desktop:"
    Write-Host "       https://www.docker.com/products/docker-desktop/" -ForegroundColor Cyan
    Write-Host "  2. Restart your computer if prompted (installation usually requires it)."
    Write-Host "  3. Launch Docker Desktop and wait for it to finish starting."
    Write-Host "  4. Re-run install.ps1."
    Write-Host ""
    exit 1
}

# -- 2. Ensure the Docker daemon is running -----------------------------------

Write-Host "Checking Docker daemon..." -ForegroundColor Cyan
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker is installed but not running. Attempting to start Docker Desktop..." -ForegroundColor Yellow
    if (Start-DockerDesktop) {
        if (-not (Wait-ForDocker)) {
            Write-Host ""
            Write-Host "Docker did not become ready in time." -ForegroundColor Red
            Write-Host "Launch Docker Desktop, wait for it to fully start, then re-run install.ps1."
            Write-Host "(If you just installed Docker Desktop, you may need to log out and back in first.)"
            exit 1
        }
    } else {
        Write-Host ""
        Write-Host "Could not locate Docker Desktop to start it automatically." -ForegroundColor Red
        Write-Host "Launch Docker Desktop from the Start menu, wait for it to start, then re-run install.ps1."
        exit 1
    }
}
Write-Host "Docker is running." -ForegroundColor Green
Write-Host ""

# -- 3. Load required models from config.defaults.json ------------------------

$configPath = Join-Path $ScriptDir "backend\config.defaults.json"
if (-not (Test-Path $configPath)) {
    Write-Host "ERROR: Could not find backend\config.defaults.json" -ForegroundColor Red
    exit 1
}
$cfg = Get-Content $configPath -Raw | ConvertFrom-Json
# Include EVERY model the backend requires at startup — vision_model included, or
# the backend preflight (ensure_ollama_available) fails on a "successful" install.
$models = @(
    $cfg.chat_model
    $cfg.clinical_model
    $cfg.verification_model
    $cfg.vision_model
    $cfg.embedding_model
) | Where-Object { $_ } | Sort-Object -Unique

# -- 4. Check / install Ollama ------------------------------------------------
# Ollama runs natively on the host (not in a container) so it can use the GPU. A
# container on Windows is CPU-only here, which is far too slow for these models.

if (-not (Test-Command "ollama")) {
    Write-Host "Ollama is not installed." -ForegroundColor Yellow
    Write-Host "Ollama provides the AI models that power OpenHealth."
    Write-Host ""
    $choice = Read-Host "Would you like to install Ollama now? (Y/N)"

    if ($choice -notmatch "^[Yy]") {
        Write-Host ""
        Write-Host "Ollama is required. Install it and re-run install.ps1:" -ForegroundColor Yellow
        Write-Host "  https://ollama.com/download/windows"
        exit 1
    }

    if (-not (Test-Command "winget")) {
        Write-Host ""
        Write-Host "winget is not available on this machine." -ForegroundColor Yellow
        Write-Host "Please install Ollama manually and re-run this installer:"
        Write-Host "  https://ollama.com/download/windows"
        exit 1
    }

    Write-Host ""
    Write-Host "Installing Ollama via winget..." -ForegroundColor Cyan
    winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements

    # Refresh PATH in the current session so ollama is immediately available.
    Update-SessionPath

    if (-not (Test-Command "ollama")) {
        Write-Host ""
        Write-Host "Ollama was installed but could not be found in PATH." -ForegroundColor Yellow
        Write-Host "Please close this terminal, open a new one, and re-run install.ps1."
        exit 1
    }
    Write-Host "Ollama installed successfully." -ForegroundColor Green
} else {
    Write-Host "Ollama is already installed." -ForegroundColor Green
}

Write-Host ""
Write-Host "Copying compose configuration (host Ollama)..."
Copy-Item "docker\docker-compose.ollama-host.yml" "docker-compose.yml" -Force

# A fresh install (or a host where the tray app isn't up yet) won't have the
# server running, and every "ollama pull" below needs it.
if (-not (Start-OllamaServer)) {
    Write-Host ""
    Write-Host "Ollama is installed but its server did not respond on localhost:11434." -ForegroundColor Red
    Write-Host "Start Ollama (e.g. from the Start menu) and re-run install.ps1."
    exit 1
}

Write-Host ""
Write-Host "Pulling required AI models (this may take a while)..." -ForegroundColor Cyan
foreach ($model in $models) {
    Write-Host ""
    Write-Host "  Pulling: $model" -ForegroundColor White
    ollama pull $model
    if ($LASTEXITCODE -ne 0) { $pullFailures += $model }
}

# -- 5. Create data directory --------------------------------------------------

if (-not (Test-Path "data")) {
    Write-Host ""
    Write-Host "Creating ./data directory..."
    New-Item -ItemType Directory -Path "data" | Out-Null
}

# -- 6. Done -------------------------------------------------------------------

if ($pullFailures.Count -gt 0) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "  Setup incomplete" -ForegroundColor Red
    Write-Host "========================================"
    Write-Host ""
    Write-Host "The following models failed to download:" -ForegroundColor Yellow
    foreach ($model in $pullFailures) {
        Write-Host "  $model"
    }
    Write-Host ""
    Write-Host "Re-run install.ps1 to retry, or pull them manually with 'ollama pull <model>'."
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "========================================"
Write-Host ""
Write-Host "Start OpenHealth with:"
Write-Host ""
Write-Host "  docker compose up" -ForegroundColor Cyan
Write-Host ""
Write-Host "Then open:" -ForegroundColor White
Write-Host "  http://localhost:3000" -ForegroundColor Cyan
Write-Host ""

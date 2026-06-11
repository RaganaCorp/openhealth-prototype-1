# OpenHealth Installer - Windows (PowerShell)
# Run from the project root: .\install.ps1

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  OpenHealth Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# -- 1. Check / install Docker ------------------------------------------------

function Test-Command($name) {
    return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

# Wait up to 30 seconds for Ollama to respond on localhost:11434
function Wait-ForOllama {
    $waited = 0
    Write-Host "Waiting for Ollama to be ready" -NoNewline
    while ($waited -lt 30) {
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

# Ensure the Ollama server is running before pulling models (a fresh install
# does not start it automatically).
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

# Models that fail to pull are collected and reported at the end.
$pullFailures = @()

if (-not (Test-Command "docker")) {
    Write-Host "Docker Desktop is not installed." -ForegroundColor Yellow
    Write-Host "Docker Desktop is required to run OpenHealth."
    Write-Host ""
    Write-Host "Please install Docker Desktop manually:"
    Write-Host "  https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
    Write-Host ""
    $null = Read-Host "Press Enter after installing Docker Desktop (or Ctrl+C to cancel)"

    if (-not (Test-Command "docker")) {
        Write-Host ""
        Write-Host "Docker CLI is still not available in this terminal." -ForegroundColor Yellow
        Write-Host "Open a new terminal and re-run install.ps1 after Docker Desktop finishes installing."
        exit 1
    }
}

# -- 2. Check Docker daemon is running ----------------------------------------

Write-Host "Checking Docker daemon..." -ForegroundColor Cyan
$null = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Docker is installed but not running." -ForegroundColor Yellow
    Write-Host "Please launch Docker Desktop from the Start menu and wait for it to fully start."
    Write-Host "(If you just installed Docker Desktop, you may need to log out and back in first.)"
    exit 1
}
Write-Host "Docker is running." -ForegroundColor Green
Write-Host ""

# -- Helper: load required models from config.defaults.json -------------------

$configPath = Join-Path $ScriptDir "backend\config.defaults.json"
if (-not (Test-Path $configPath)) {
    Write-Host "ERROR: Could not find backend\config.defaults.json" -ForegroundColor Red
    exit 1
}
$cfg = Get-Content $configPath -Raw | ConvertFrom-Json
$models = @(
    $cfg.chat_model
    $cfg.clinical_model
    $cfg.summary_model
    $cfg.verification_model
    $cfg.embedding_model
) | Where-Object { $_ } | Sort-Object -Unique

# -- 3. Check / install Ollama ------------------------------------------------

if (Test-Command "ollama") {
    Write-Host "Ollama is already installed." -ForegroundColor Green
    Write-Host ""
    Write-Host "Copying compose configuration (host Ollama)..."
    Copy-Item "docker\docker-compose.windows-ollama-host.yml" "docker-compose.yml" -Force

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
} else {
    Write-Host "Ollama is not installed." -ForegroundColor Yellow
    Write-Host "Ollama provides the AI models that power OpenHealth."
    Write-Host ""
    $choice = Read-Host "Would you like to install Ollama now? (Y/N)"

    if ($choice -match "^[Yy]") {
        if (Test-Command "winget") {
            Write-Host ""
            Write-Host "Installing Ollama via winget..." -ForegroundColor Cyan
            winget install Ollama.Ollama

            # Refresh PATH in the current session so ollama is immediately available
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("Path", "User")

            if (-not (Test-Command "ollama")) {
                Write-Host ""
                Write-Host "Ollama was installed but could not be found in PATH." -ForegroundColor Yellow
                Write-Host "Please close this terminal, open a new one, and re-run install.ps1."
                exit 1
            }
            Write-Host "Ollama installed successfully." -ForegroundColor Green

            # A fresh winget install does not start the Ollama server, and every
            # "ollama pull" below would fail against an unreachable localhost:11434.
            if (-not (Start-OllamaServer)) {
                Write-Host ""
                Write-Host "Ollama was installed but its server did not start." -ForegroundColor Red
                Write-Host "Start Ollama from the Start menu, then re-run install.ps1."
                exit 1
            }
        } else {
            Write-Host ""
            Write-Host "winget is not available on this machine." -ForegroundColor Yellow
            Write-Host "Please install Ollama manually and re-run this installer:"
            Write-Host "  https://ollama.com/download/windows"
            exit 1
        }

        Write-Host ""
        Write-Host "Copying compose configuration (host Ollama)..."
        Copy-Item "docker\docker-compose.windows-ollama-host.yml" "docker-compose.yml" -Force

        Write-Host ""
        Write-Host "Pulling required AI models (this may take a while)..." -ForegroundColor Cyan
        foreach ($model in $models) {
            Write-Host ""
            Write-Host "  Pulling: $model" -ForegroundColor White
            ollama pull $model
            if ($LASTEXITCODE -ne 0) { $pullFailures += $model }
        }
    } else {
        Write-Host ""
        Write-Host "Using the bundled Ollama Docker container instead." -ForegroundColor Cyan
        Write-Host "Copying compose configuration (Docker Ollama)..."
        Copy-Item "docker\docker-compose.windows-ollama-docker.yml" "docker-compose.yml" -Force

        Write-Host ""
        Write-Host "Starting Ollama container to pull models..." -ForegroundColor Cyan
        docker compose up -d ollama
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "Failed to start the Ollama container." -ForegroundColor Red
            exit 1
        }

        $ready = Wait-ForOllama

        if (-not $ready) {
            Write-Host ""
            Write-Host "Ollama container did not respond in time." -ForegroundColor Red
            Write-Host "You can pull models manually once the container is running:"
            foreach ($model in $models) {
                Write-Host "  docker exec ollama ollama pull $model"
            }
            exit 1
        }

        Write-Host "Ollama is ready." -ForegroundColor Green
        Write-Host ""
        Write-Host "Pulling required AI models (this may take a while)..." -ForegroundColor Cyan
        foreach ($model in $models) {
            Write-Host ""
            Write-Host "  Pulling: $model" -ForegroundColor White
            docker exec ollama ollama pull $model
            if ($LASTEXITCODE -ne 0) { $pullFailures += $model }
        }

        Write-Host ""
        Write-Host "Stopping Ollama container (it will start again with the full stack)..."
        docker compose stop ollama
    }
}

# -- 4. Create data directory --------------------------------------------------

if (-not (Test-Path "data")) {
    Write-Host ""
    Write-Host "Creating ./data directory..."
    New-Item -ItemType Directory -Path "data" | Out-Null
}

# -- 5. Done -------------------------------------------------------------------

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

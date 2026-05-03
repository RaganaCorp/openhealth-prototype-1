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

if (-not (Test-Command "docker")) {
    Write-Host "Docker Desktop is not installed." -ForegroundColor Yellow
    Write-Host "Docker Desktop is required to run OpenHealth."
    Write-Host ""
    $choice = Read-Host "Would you like to install Docker Desktop now? (Y/N)"
    if ($choice -notmatch "^[Yy]") {
        Write-Host ""
        Write-Host "Please install Docker Desktop manually and re-run this installer:"
        Write-Host "  https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
        exit 1
    }

    if (Test-Command "winget") {
        Write-Host ""
        Write-Host "Installing Docker Desktop via winget..." -ForegroundColor Cyan
        Write-Host "(A UAC prompt will appear - please allow it to continue.)"
        Write-Host ""
        winget install Docker.DockerDesktop
        Write-Host ""
        Write-Host "Docker Desktop installed." -ForegroundColor Green
        Write-Host ""
        Write-Host "ACTION REQUIRED:" -ForegroundColor Yellow
        Write-Host "  1. Launch Docker Desktop from the Start menu."
        Write-Host "  2. Wait for it to fully start (whale icon in the taskbar stops animating)."
        Write-Host "  3. If 'docker' is still not found, log out and back in to refresh your PATH."
        Write-Host ""
        Read-Host "Press Enter once Docker Desktop is running to continue"
    } else {
        Write-Host ""
        Write-Host "winget is not available on this machine." -ForegroundColor Yellow
        Write-Host "Please install Docker Desktop manually and re-run this installer:"
        Write-Host "  https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
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

    Write-Host ""
    Write-Host "Pulling required AI models (this may take a while)..." -ForegroundColor Cyan
    foreach ($model in $models) {
        Write-Host ""
        Write-Host "  Pulling: $model" -ForegroundColor White
        ollama pull $model
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
        }
    } else {
        Write-Host ""
        Write-Host "Using the bundled Ollama Docker container instead." -ForegroundColor Cyan
        Write-Host "Copying compose configuration (Docker Ollama)..."
        Copy-Item "docker\docker-compose.windows-ollama-docker.yml" "docker-compose.yml" -Force

        Write-Host ""
        Write-Host "Starting Ollama container to pull models..." -ForegroundColor Cyan
        docker compose up -d ollama

        # Wait up to 30 seconds for Ollama to be responsive
        $ready = $false
        $waited = 0
        Write-Host "Waiting for Ollama to be ready" -NoNewline
        while (-not $ready -and $waited -lt 30) {
            Start-Sleep -Seconds 2
            $waited += 2
            try {
                $null = Invoke-WebRequest -Uri "http://localhost:11434" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
                $ready = $true
            } catch {
                Write-Host "." -NoNewline
            }
        }
        Write-Host ""

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

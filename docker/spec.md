# OpenHealth — Docker Specification

Source of truth for `docker-compose.yml` and the overall runtime environment. See `spec-backend.md` and `spec-frontend.md` for service-level details.

---

## Goals

- Single command to run the entire stack for non-technical users.
- No Python, Node, or any other runtime required on the host.
- All runtime data (config, patient records, vector store) lives in a single `./data/` bind-mount on the host machine.
- Ollama is **optional** — users with Ollama already running on the host OS can skip the bundled service.
- Platform-specific installers handle pre-flight checks and select the correct compose variant.

---

## Services

| Service | Image | Port | Notes |
|---|---|---|---|
| `frontend` | `ghcr.io/ragana/openhealth-prototype1-frontend:${OPENHEALTH_PROTOTYPE1_IMAGE_TAG:-latest}` | `3000:3000` | Next.js, served via `next start` |
| `backend` | `ghcr.io/ragana/openhealth-prototype1-backend:${OPENHEALTH_PROTOTYPE1_IMAGE_TAG:-latest}` | `8000:8000` | FastAPI + Uvicorn |
| `ollama` | `ollama/ollama:latest` | `11434:11434` | Only present in "ollama in Docker" variants |

ChromaDB runs **in-process** inside the backend container (Python library, no separate service). The vector store is persisted to `./data/memory_db/` via the bind mount.

Frontend and backend are pulled as prebuilt Ragana images by default for fast setup on non-developer machines.

---

## Volumes

A single bind mount covers all runtime data:

```
./data  →  /data  (inside backend container)
```

The frontend container does not need a data mount — all file I/O goes through the backend API.

The `./data/` directory is **git-ignored**. It is created automatically on first run if absent.

---

## Networking

All services communicate on an internal Docker network (`openhealth_net` or equivalent). Only the frontend and backend ports are exposed to the host.

- Frontend calls backend at `http://backend:8000` (internal) — configured via `NEXT_PUBLIC_API_URL` environment variable.
- Backend calls Ollama at the URL specified in `config.json` `ollama_base_url`. Default: `http://ollama:11434` (internal, when Ollama service is running). Users pointing to host Ollama set this to `http://host.docker.internal:11434` via `POST /config` or by editing `config.json` directly.

---

## Docker Compose Variants

Four runtime compose files live in the `docker/` directory. The installer copies the correct runtime file to the project root as `docker-compose.yml` (git-ignored).

| File | Platform | Ollama |
|---|---|---|
| `docker/docker-compose.windows-ollama-docker.yml` | Windows | Ollama container included |
| `docker/docker-compose.windows-ollama-host.yml` | Windows | Points to host Ollama (`host.docker.internal:11434`) |
| `docker/docker-compose.mac-ollama-docker.yml` | macOS | Ollama container included |
| `docker/docker-compose.mac-ollama-host.yml` | macOS | Points to host Ollama (`host.docker.internal:11434`) |

Optional developer override:

| File | Purpose |
|---|---|
| `docker/docker-compose.local-build.override.yml` | Build frontend/backend from local source (`docker compose -f docker-compose.yml -f docker/docker-compose.local-build.override.yml up --build`) |

### Differences between variants

- **Published app images**: all runtime variants use `ghcr.io/ragana/openhealth-prototype1-frontend:${OPENHEALTH_PROTOTYPE1_IMAGE_TAG:-latest}` and `ghcr.io/ragana/openhealth-prototype1-backend:${OPENHEALTH_PROTOTYPE1_IMAGE_TAG:-latest}`.
- **Ollama-in-Docker variants**: include the `ollama` service with `image: ollama/ollama:latest` and expose port `11434`. `OLLAMA_BASE_URL` is set to `http://ollama:11434` (internal network).
- **Ollama-on-host variants**: no `ollama` service. `OLLAMA_BASE_URL` is set to `http://host.docker.internal:11434`. No `11434` port exposure.
- **Windows vs macOS**: differences are limited to `extra_hosts` entries and any platform-specific volume path handling required by Docker Desktop.

---

## Installer

Two installer scripts live in the project root. They perform pre-flight checks, select the correct compose variant, and copy it to `docker-compose.yml`.

### Model List Resolution

Both installers derive the required Ollama models from `backend/config.defaults.json` at install time. They read the fields `chat_model`, `clinical_model`, `summary_model`, `verification_model`, and `embedding_model`, deduplicate, and use the resulting list for `ollama pull` calls.

### Windows — `install.ps1` (PowerShell)

**Steps:**
1. Check Docker is installed (`docker --version`). If not found:
   - Explain to the user that Docker Desktop is required and ask if they would like to install it now (Y/N prompt).
   - If **No**: print the manual download URL (`https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe`) and exit.
   - If **Yes**: check whether `winget` is available.
     - If **winget available**: run `winget install Docker.DockerDesktop` (a UAC prompt will appear). After install completes, tell the user to launch Docker Desktop from the Start menu and wait for it to finish starting, then prompt the user to press Enter to continue.
     - If **winget not available**: print the manual download URL (`https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe`) and exit.
2. Check Docker daemon is running (`docker info`). If not running, prompt user to start Docker Desktop and exit (they may need to log out and back in after a fresh install for the PATH to update; note this in the message).
3. Check whether Ollama is already installed on this machine (`ollama --version`).
   - If **found** (Ollama on host):
     - Copy `docker/docker-compose.windows-ollama-host.yml` → `docker-compose.yml`.
     - Pull each required model (`ollama pull <model>`). Print progress for each.
   - If **not found**: explain that Ollama is required and ask the user whether they would like to install it now (Y/N prompt).
     - If **Yes**: check whether `winget` is available.
       - If **winget available**: run `winget install Ollama.Ollama`. After install, refresh the PATH in the current session and verify `ollama --version` succeeds.
       - If **winget not available**: print the manual download URL (`https://ollama.com/download/windows`) and exit.
       - Copy `docker/docker-compose.windows-ollama-host.yml` → `docker-compose.yml`.
       - Pull each required model (`ollama pull <model>`). Print progress for each.
     - If **No** (use bundled Ollama container):
       - Copy `docker/docker-compose.windows-ollama-docker.yml` → `docker-compose.yml`.
       - Run `docker compose up -d ollama` to start just the Ollama container.
       - Wait up to 30 seconds for the Ollama service to become responsive (poll `http://localhost:11434` or use `docker compose ps`).
       - Pull each required model via `docker exec ollama ollama pull <model>`. Print progress for each.
       - Run `docker compose stop ollama` so the full `docker compose up` starts everything cleanly together.
4. Create `./data/` directory if it does not exist.
5. Print next steps: `docker compose up` and open `http://localhost:3000`.

### macOS — `install.sh` (Bash)

**Steps:**
1. Check Docker is installed (`docker --version`). If not found:
   - Explain to the user that Docker Desktop is required and ask if they would like to install it now (Y/N prompt).
   - If **No**: print the manual download URL (`https://www.docker.com/products/docker-desktop/`) and exit.
   - If **Yes**: check whether `brew` is available.
     - If **Homebrew available**: run `brew install --cask docker`. After install completes, tell the user to launch Docker Desktop from Applications and wait for it to finish starting, then prompt the user to press Enter to continue.
     - If **Homebrew not available**: print the manual download URL (`https://www.docker.com/products/docker-desktop/`) and exit.
2. Check Docker daemon is running (`docker info`). If not running, prompt user to launch Docker Desktop from Applications and press Enter to retry.
3. Check whether Ollama is already installed on this machine (`ollama --version`).
   - If **found** (Ollama on host):
     - Copy `docker/docker-compose.mac-ollama-host.yml` → `docker-compose.yml`.
     - Pull each required model (`ollama pull <model>`). Print progress for each.
   - If **not found**: explain that Ollama is required and ask the user whether they would like to install it now (Y/N prompt).
     - If **Yes**: check whether `brew` is available.
       - If **Homebrew available**: run `brew install ollama`. After install, verify `ollama --version` succeeds.
       - If **Homebrew not available**: print the manual download URL (`https://ollama.com/download/mac`) and exit.
       - Copy `docker/docker-compose.mac-ollama-host.yml` → `docker-compose.yml`.
       - Pull each required model (`ollama pull <model>`). Print progress for each.
     - If **No** (use bundled Ollama container):
       - Copy `docker/docker-compose.mac-ollama-docker.yml` → `docker-compose.yml`.
       - Run `docker compose up -d ollama` to start just the Ollama container.
       - Wait up to 30 seconds for the Ollama service to become responsive.
       - Pull each required model via `docker exec ollama ollama pull <model>`. Print progress for each.
       - Run `docker compose stop ollama` so the full `docker compose up` starts everything cleanly together.
4. Create `./data/` directory if it does not exist.
5. Print next steps: `docker compose up` and open `http://localhost:3000`.

### Notes
- `docker-compose.yml` in the project root is **git-ignored**. It is always generated by the installer, never committed.
- The runtime compose files in `docker/` and the local build override file are committed to the repo.
- Users who re-run the installer will have `docker-compose.yml` overwritten.

---

## Environment Variables

| Variable | Service | Default | Description |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | frontend | `http://localhost:8000` | Backend URL as seen from the browser |
| `BACKEND_API_URL` | frontend (SSR) | `http://backend:8000` | Backend URL for server-side rendering calls |
| `DATA_PATH` | backend | `/data` | Root data directory inside container |
| `OLLAMA_BASE_URL` | backend | `http://ollama:11434` | Ollama endpoint (overridden by `config.json` at runtime) |
| `OPENHEALTH_PROTOTYPE1_IMAGE_TAG` | frontend + backend | `latest` | Optional override for the Ragana prototype image tag |

---

## Data Directory Layout

All runtime data lives under `./data/` in the project root, bind-mounted into the backend container at `/data`.

```
./data/
├── config/
│   └── config.json                      # created with defaults on first run if absent
├── patients.json                         # thin patient index
├── memory_db/                            # ChromaDB persistent vector store
│   ├── {patient_id}_docs/               # document chunks per patient
│   └── {patient_id}_chat_{session_id}/  # semantic chat memory per session
└── patients/
    └── mary-johnson/                    # auto-created from patient name slug
        ├── patient.json                 # per-patient record (documents, timeline, summary, sessions, states)
        ├── patient.md                   # generated — full concatenated record
        ├── uploads/                     # source files (PDFs, TIFFs, TXTs, HTMLs, JSONs)
        │   ├── discharge_summary_jan2026.pdf
        │   └── discharge_summary_jan2026.pdf.extracted  # cached plain-text sidecar
        └── chats/
            └── {session_id}.json        # ordered message log per chat session
```

`patient.md`, `patient.json`, `.extracted` files, and the `memory_db/` directory are excluded from git (contain personal medical data).

---

## Project Root Layout

```
openhealth/
├── install.ps1                  # Windows installer (PowerShell)
├── install.sh                   # macOS installer (Bash)
├── docker-compose.yml           # git-ignored; copied here by installer
├── README.md
├── backend/
│   ├── spec.md
│   └── ...                      # source files
├── frontend/
│   ├── spec.md
│   └── ...                      # source files
├── docker/
│   ├── spec.md
│   ├── docker-compose.windows-ollama-docker.yml
│   ├── docker-compose.windows-ollama-host.yml
│   ├── docker-compose.mac-ollama-docker.yml
│   ├── docker-compose.mac-ollama-host.yml
│   └── docker-compose.local-build.override.yml
└── data/                        ← bind-mounted volume (git-ignored)
```

---

## Dockerfile Notes

### Backend (`backend/Dockerfile`)
- Base: `python:3.12-slim`
- Install system dependencies: `tesseract-ocr`, `libgl1` (for PyMuPDF)
- Install Python dependencies from `requirements.txt`
- Copy source; run `uvicorn main:app --host 0.0.0.0 --port 8000`
- Working directory: `/app`
- Data volume mount point: `/data`

### Frontend (`frontend/Dockerfile`)
- Base: `node:22-slim`
- Build stage: `npm ci`, `npm run build`
- Runtime stage: `next start` on port 3000
- No data volume needed

---

## First-Run Behavior

1. User runs `install.ps1` (Windows) or `install.sh` (macOS).
2. Installer checks Docker, selects compose variant, copies to `docker-compose.yml`.
3. Installer pulls required Ollama models (from `backend/config.defaults.json`) — either via the host `ollama` CLI or by briefly starting the bundled Ollama container. This is the longest step; the installer prints per-model progress.
4. User runs `docker compose up`.
5. Backend starts, checks for `./data/config/config.json` — creates it with defaults if absent.
6. Backend starts `watchdog` watcher for any existing patient folders.
7. Frontend starts; home page loads patients from `GET /patients`.
8. All required models are already present in Ollama; no model download happens on first inference.

---

## README

The `README.md` at the project root should cover:

1. Prerequisites: Docker Desktop only.
2. Clone the repo.
3. **Windows**: run `install.ps1` in PowerShell. **macOS**: run `./install.sh` in Terminal.
4. Follow the installer prompts (Docker check, Ollama choice).
5. `docker compose up`
6. Open `http://localhost:3000`
7. Note on data: all patient data lives in `./data/` — back it up as needed.
8. Note on changing Ollama choice: re-run the installer or edit `ollama_base_url` in Settings.
9. Optional for developers: local source build with `docker compose -f docker-compose.yml -f docker/docker-compose.local-build.override.yml up --build`.

# OpenHealth — Technical Specification

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Next.js  (localhost:3000)                      │
│  Pages: Home · Patient                          │
└───────────────────┬─────────────────────────────┘
                    │ HTTP / JSON
┌───────────────────▼─────────────────────────────┐
│  FastAPI  (localhost:8000) example URLs         │
│                                                 │
│  POST /ingest/{id}  →  Background job           │
│  POST /chat         →  turn assembly + Ollama   │
│  GET  /timeline     →  patients/{slug}/patient.json │
│  GET  /summary      →  patients/{slug}/patient.json │
└──────────┬──────────────────────┬───────────────┘
           │                      │
┌──────────▼──────┐    ┌──────────▼───────────────┐
│  Ollama         │    │  ChromaDB (persistent)   │
│  dcarrascosa/...│    │  /data/memory_db/        │
│  nomic-embed    │    │  memory_db/              │
└─────────────────┘    └──────────────────────────┘
```

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js (App Router) |
| Styling | Tailwind CSS v4 |
| Backend | Python 3.12, FastAPI, Uvicorn |
| AI inference | Ollama (local) |
| Default chat model | `dcarrascosa/medgemma-1.5-4b-it:Q8_0` |
| Default embedding model | `nomic-embed-text` (768-dim) |
| Vector store | ChromaDB (persistent, cosine similarity) |
| PDF extraction | PyMuPDF (fitz) |
| OCR | Tesseract 5 via PyMuPDF `get_textpage_ocr()` |
| Data persistence | JSON files (`patients.json` thin index + per-patient `patients/{slug}/patient.json`) |

This solution needs to be easily copied and run locally by people who likely don't have python or node knowledge (or the rest). Docker Compose handles all components and wires them together.

**Ollama** is handled by the platform installer (`install.ps1` on Windows, `install.sh` on macOS). The installer detects whether Ollama is already present; if not, it offers to install it via `winget` (Windows) or `brew` (macOS), or falls back to a bundled Ollama Docker container if the user declines. After resolving Ollama, the installer pulls all required models from `backend/config.defaults.json` before the user ever runs `docker compose up`. Four compose variants live in `docker/` — the installer copies the correct one to `docker-compose.yml` (git-ignored) based on platform and Ollama choice.

**Patient creation** — the user provides a name in the UI. The backend slugifies the name (e.g. "Mary Johnson" → `mary-johnson`) and creates `./data/patients/mary-johnson/` automatically. No folder path is entered by the user. If a folder with that slug already exists (e.g. a second patient named "John Smith" when `john-smith` is taken), a numeric counter is appended: `john-smith-2`, `john-smith-3`, etc. After the patient is created, the user is immediately presented with a file upload interface to drop or select files from their computer. Uploaded files are written by the backend into the patient's folder inside the Docker-mounted volume. Upload completion automatically triggers ingestion.

---

## Supported File Types

| Type | Extraction method |
|---|---|
| `.pdf` (text-based) | PyMuPDF embedded text |
| `.pdf` (scanned/image) | PyMuPDF → Tesseract OCR fallback |
| `.tif` / `.tiff` | Pillow + pytesseract |
| `.txt` | direct read |
| `.html` | tag stripping + html.unescape |
| `.json` | LLM-assisted extraction (flexible, structure-agnostic) |

Scanned PDFs that produce no text via PyMuPDF automatically fall back to Tesseract. Files where OCR also yields nothing are recorded as processed (so they don't re-appear as "new") but excluded from `patient.md`.

**JSON ingestion**: Because provider-exported JSON files have no standard structure, the raw JSON is serialised to a human-readable string and passed to the LLM with a prompt asking it to extract all clinically relevant information as plain text. The resulting text is then chunked and embedded like any other document. No assumptions are made about field names or nesting.

---

## Ingestion Pipeline

```
PDFs / TIFFs / TXTs / HTMLs
        │
        ▼
  extract_text()
  ├── embedded text (PyMuPDF)
  └── OCR fallback (tesseract via PyMuPDF) ← handles scanned PDFs
        │
        ▼
  chunk_text()  →  embed chunks  →  ChromaDB
        │
        ▼
  write_patient.md  (all docs concatenated with document boundaries)
        │
        ├──▶  generate_full_timeline()  →  one LLM call  →  timeline events
        │
        └──▶  _regenerate_summary()    →  two LLM calls →  structured summary
                                           ├── Pass 1: structured extraction
                                           │   (conditions / medications / procedures / labs → JSON)
                                           ├── Deterministic fallback merge
                                           │   (regex-based section extraction from patient.md
                                           │    merged with pass-1 results; used when pass-1 sparse)
                                           └── Pass 2: prose generation from pre-extracted facts
                                               (fallback: inject pass-1 items directly if LLM drops a section)
```

**Summary generation — two-pass approach**: because patient records arrive in wildly different formats (CCD exports, portal HTML, PDFs, JSON), deterministic extraction is not reliable across all formats. Summary generation therefore uses two LLM calls. **Pass 1** sends the full `patient.md` (up to 30,000 characters) to the LLM with a strict extraction-only prompt and receives a JSON object containing conditions, medications, procedures, key labs, and demographics. After pass-1, a deterministic fallback extracts section blobs from `patient.md` by regex-matching common section headers (e.g. "Active Problems", "Medications", "Procedures") and parses list items as a safety net. The pass-1 LLM output and the deterministic fallback are then merged (LLM results take precedence; deterministic fills any empty arrays). **Pass 2** formats those extracted facts as explicit pre-verified bullet lists and sends them to the LLM for prose generation. If the LLM drops a section in Pass 2 that had items in the merged structured data, those items are injected directly as a fallback, bypassing the LLM for that section.

Ingestion runs as a background job. Frontend polls `/status/{job_id}` every 2 seconds and displays live progress.

Ingestion is triggered automatically after file upload completes (incremental). Advanced users who manually copy files into `uploads/` are also supported via a **directory watcher**: the backend watches each patient's `uploads/` folder using `watchdog` and triggers an incremental job whenever new files are detected.

**Incremental ingestion** (default for upload and watcher): only files not yet recorded in `patient.json` or files detected as changed are processed. Change detection uses file modification timestamp and file size (`mtime`, `size`) tracked per document record. `patient.md` is appended/updated for those files and only corresponding chunks are upserted in the `_docs` ChromaDB collection. Existing chat history is untouched.

**Full rebuild** (`POST /rebuild/{patient_id}`): clears `patient.md`, drops and re-creates the `_docs` ChromaDB collection, and re-processes every file in `uploads/` from scratch. Timeline and summary are also regenerated. Chat history in `_chat` is never cleared on any rebuild. This is exposed in the UI as a "Rebuild from scratch" action for cases where document order or extraction quality needs to be reset.

**File scan rules**: uploaded files are stored in the `uploads/` subfolder of each patient folder. Ingestion scans only the top level of `uploads/` — subdirectories within it are ignored. The only exclusion applied is `*.extracted` files (they are cached extraction outputs, not source documents). All other files matching supported extensions (`.pdf`, `.tif`, `.tiff`, `.txt`, `.html`, `.json`) are processed. App-generated files (`patient.json`, `patient.md`) live outside `uploads/` in the patient root and are never scanned.

Patients can have multiple independent chat sessions over the same document set. Each session has isolated memory and isolated conversation state.

---

## patient.md Structure

`patient.md` is a single concatenated file containing the full text of every ingested document. Each document is wrapped in explicit boundary markers so the model can identify the source and scope of any given section.

Format:

```
================================================================================
DOCUMENT: discharge_summary_jan2026.pdf
TYPE: discharge summary
DATE DETECTED: 2026-01-15
================================================================================

[full extracted text of document]

================ END: discharge_summary_jan2026.pdf ================


================================================================================
DOCUMENT: lab_results_mar2026.pdf
TYPE: lab result
DATE DETECTED: 2026-03-04
================================================================================

[full extracted text of document]

================ END: lab_results_mar2026.pdf ================
```

Documents are ordered chronologically by detected date. This structure ensures the model can attribute any piece of information to its source document without ambiguity, and citations in chat responses can reference the correct filename.

---

## Chat Pipeline

```
User selects or creates Chat (chat_session_id)
  │
  ▼
User question
      │
      ▼
classify_query()               ← detects document_type of the query (e.g. lab, medication, procedure) to optimise retrieval strategy
      │
      ▼
retrieve_chat_history()        ← ChromaDB semantic memory (top-K) filtered by patient_id + chat_session_id
  │
  ▼
load_conversation_state()      ← persisted rolling summary + open threads + facts for chat_session_id
      │
      ▼
read_patient_md()              ← full record with document boundaries
  │
  ▼
assemble_turn_context()        ← stateless packet for this turn only
  - patient.md (or docs fallback if too large)
  - relevant prior exchanges
  - conversation_state capsule
  - current user question
      │
      ▼
Ollama (dcarrascosa/medgemma-1.5-4b-it:Q8_0)
  │
  ▼
verify_grounding()             ← citation + factual support check
      │
      ▼
finalize_response()            ← answer + citations + uncertainty notes
      │
      ▼
store_exchange()               ← embed/store Q&A in ChromaDB
  │
  ▼
update_conversation_state()    ← refresh rolling summary + unresolved questions for chat_session_id
```

This pipeline is intentionally **stateless at inference time**. Every turn is rebuilt from persisted memory and patient data, rather than relying on model-internal multi-turn state.

Memory isolation rule: retrieval and state updates are always filtered by both `patient_id` and `chat_session_id`. Cross-session retrieval is never allowed.

---

## ChromaDB — Dual Role

ChromaDB serves two distinct purposes:

**1. Document chunks (ingestion)**
- Each document is chunked and embedded during ingestion
- Used as fallback if `patient.md` exceeds the model's context window
- Metadata: `{ patient_id, document_id, filename, chunk_index, date, document_type }`
- Collection name: `patient_{patient_id}_docs`

**2. Chat history (conversation memory)**
- Every chat exchange (question + response) is embedded and stored after each turn
- Retrieved semantically before each new message — surfaces relevant past exchanges, not just the most recent ones
- Gives the model awareness of what has been discussed previously without passing the entire chat history on every call
- Metadata: `{ patient_id, chat_session_id, role, timestamp }`
- Collection name: `patient_{patient_id}_chat_{chat_session_id}`
- Guardrail: all memory queries include an explicit metadata filter on both patient and session IDs

---

## Data Model

### Patient Index entry (`patients.json`)
```json
[
    {
    "id": "uuid",
    "name": "Mary Johnson",
    "folder_slug": "mary-johnson",
    "folder_path": "/data/patients/mary-johnson",
    "document_count": 12,
    "last_ingested_at": "ISO timestamp",
    "created_at": "ISO timestamp"
    }
]
```

`patients.json` is a flat array of these index entries — the only file read when listing patients. `document_count` and `last_ingested_at` are denormalized here so the home page can display them without loading each `patient.json`. Both are updated at the end of every ingestion job.

### Patient Record (`/patients/{patient_id}/patient.json`)
```json
{
  "id": "uuid",
  "last_ingested_at": "ISO timestamp",
  "document_count": 12,
  "documents": [],
  "timeline": [],
  "summary": "string",
  "chat_sessions": [],
  "conversation_states": {},
  "memory_results_override": null,
  "context_window_tokens_override": null
}
```

`memory_results_override` and `context_window_tokens_override` are per-patient overrides of the corresponding global config values. When non-null, they take precedence over config for that patient only. When `null`, global config is used.

### Document
```json
{
  "id": "uuid",
  "patient_id": "uuid",
  "filename": "discharge_summary_jan2026.pdf",
  "file_path": "./uploads/{patient-provided-file-name}",
  "extracted_file_path": "./uploads/{patient-provided-file-name}.extracted",
  "date_detected": "2026-01-15",
  "document_type": "discharge summary | lab result | imaging | prescription | clinical note | unknown",
  "ingested_at": "ISO timestamp",
  "mtime": "float (Unix timestamp, from os.path.getmtime)",
  "size": "int (bytes, from os.path.getsize)"
}
```

### Timeline Event
```json
{
  "id": "uuid",
  "patient_id": "uuid",
  "document_id": "uuid",
  "date": "2026-01-15",
  "title": "string",
  "summary": "string",
  "document_type": "string",
  "source_filename": "string"
}
```

### Ingestion Job
```json
{
  "job_id": "uuid",
  "patient_id": "uuid",
  "status": "running | complete | failed",
  "total": 12,
  "processed": 7,
  "current_file": "string",
  "started_at": "ISO timestamp",
  "completed_at": "ISO timestamp"
}
```

### Conversation State
```json
{
  "patient_id": "uuid",
  "session_id": "uuid",
  "title": "Follow-up questions after discharge",
  "rolling_summary": "string",
  "active_topics": ["medication side effects", "follow-up imaging"],
  "open_questions": ["Is lisinopril still current?"],
  "created_at": "ISO timestamp",
  "last_updated_at": "ISO timestamp"
}
```

This object is maintained server-side and refreshed each turn. It is a compact, structured memory capsule that compensates for MedGemma not being optimized for long multi-turn dialog.

### Chat Session
```json
{
  "id": "uuid",
  "patient_id": "uuid",
  "title": "Medication review",
  "title_auto_generated": true,
  "created_at": "ISO timestamp",
  "last_message_at": "ISO timestamp",
  "message_count": 23
}
```

Each patient can have many chat sessions. Sessions are independent memory scopes over the same underlying documents.

The `title` is set to "New Chat" on creation. After the first exchange completes, the backend generates a descriptive title using the LLM and updates the session. Once a user manually renames the session, `title_auto_generated` is set to `false` and automatic titling is suppressed.

### Chat Message Log (`patients/{slug}/chats/{session_id}.json`)
```json
{
  "session_id": "uuid",
  "messages": [
    {
      "id": "uuid",
      "role": "user | assistant",
      "content": "string",
      "citations": [
        { "filename": "string", "excerpt": "string" }
      ],
      "timestamp": "ISO timestamp"
    }
  ]
}
```

This flat ordered log is the source of truth for rendering chat history in the UI. It is separate from the ChromaDB semantic memory, which is used only for retrieval during turn assembly.

---

## API Endpoints

### Patients
- `GET /patients` — list all patients
- `POST /patients` — create patient `{ name }`. Backend slugifies name, creates folder, returns `{ id, name, folder_slug, folder_path, document_count, last_ingested_at, created_at }` (same thin shape as `GET /patients/{id}`).
- `POST /patients/{id}/upload` — upload one or more files (multipart/form-data). Files are written to `./data/patients/{slug}/uploads/`. Returns `{ filenames[], job_id }`. Triggers ingestion automatically upon completion; `job_id` can be used immediately to poll `/status/{job_id}`.
- `GET /patients/{id}` — get thin patient detail only: `{ id, name, folder_slug, folder_path, document_count, last_ingested_at, created_at }`. Summary, timeline, chat sessions, and messages are fetched from their dedicated endpoints.
- `PATCH /patients/{id}` — update patient fields. Accepts any subset of `{ name, memory_results_override, context_window_tokens_override }`. Renaming updates the `patients.json` index entry name; the folder slug is never changed. Returns the updated thin patient shape.
- `DELETE /patients/{id}` — remove a patient from the list. The frontend shows a confirmation modal with checkboxes (all defaulted to on) before calling this endpoint:
  - Remove uploaded source files (`uploads/` folder)
  - Remove chat message logs (`chats/` folder)
  - Remove patient record (`patient.json`, `patient.md`, `.extracted` files)
  - Remove ChromaDB collections
  The endpoint accepts `{ delete_uploads: bool, delete_chats: bool, delete_record_files: bool, delete_vector_data: bool }` and always removes the `patients.json` index entry.
  If all delete flags are `false`, the behavior is still valid: remove only the `patients.json` entry and keep all patient data on disk/vector store.

### Ingestion
- `POST /ingest/{patient_id}` — start incremental ingestion (new/changed files only), returns `{ job_id }` immediately
- `POST /rebuild/{patient_id}` — force a full rebuild: clears `patient.md`, all `_docs` ChromaDB chunks, and re-processes every file in `uploads/`. Returns `{ job_id }`. Exposed in the UI as a "Rebuild from scratch" action on the patient page.
- `POST /refresh/{patient_id}` — alias for incremental ingest; kept for internal use by the watcher
- `GET /status/{job_id}` — returns job status and progress
- `GET /documents/{patient_id}` — list all ingested documents
- `DELETE /documents/{patient_id}/{document_id}` — delete one uploaded document by ID, remove its cached `.extracted` sidecar, and trigger a rebuild. Returns `{ job_id }`.
- `GET /patients/{id}/active-job` — returns the currently running ingestion job for a patient (or `null`). Used by the frontend to discover watcher-triggered jobs and display progress without a client-initiated `job_id`.

### Summary & Timeline
- `GET /summary/{patient_id}` — return stored summary as `{ "summary": "markdown string" }`
- `POST /summary/{patient_id}` — regenerate summary, returns `{ "summary": "markdown string" }`
- `GET /timeline/{patient_id}` — return timeline events sorted by date
- `GET /timeline/{patient_id}/{event_id}` — return a single timeline event detail

### Chat
- `POST /chat`
  ```json
  Request:
  {
    "patient_id": "uuid",
    "chat_session_id": "uuid",
    "message": "string"
  }

  Response:
  {
    "response": "string",
    "grounding_retried": false,
    "retry_count": 0,
    "citations": [
      {
        "filename": "string",
        "excerpt": "string"
      }
    ]
  }
  ```

Note: conversation history is managed server-side via ChromaDB. The frontend does not need to pass history on each request.

- `GET /patients/{patient_id}/chat-sessions` — list sessions for patient
- `POST /patients/{patient_id}/chat-sessions` — create session `{ title? }`, returns `{ chat_session_id }`. Title defaults to "New Chat"; LLM-generated title is applied after the first exchange.
- `PATCH /patients/{patient_id}/chat-sessions/{chat_session_id}` — rename session `{ title }` (sets `title_auto_generated: false`)
- `DELETE /patients/{patient_id}/chat-sessions/{chat_session_id}` — delete one session and only its memory/state/messages
- `GET /chat/messages/{patient_id}/{chat_session_id}` — return ordered message log for display (from `./data/patients/{slug}/chats/`)
- `GET /chat/state/{patient_id}/{chat_session_id}` — return conversation state capsule for one session
- `POST /chat/reset/{patient_id}/{chat_session_id}` — clear memory + reset state for one session
- `POST /chat/rebuild-state/{patient_id}/{chat_session_id}` — recompute state from stored exchanges for one session

### Config
- `GET /config` — get current model settings
- `POST /config` — update any subset of config fields
- `GET /models` — list available Ollama models

---

## File Structure Example

```
openhealth/
├── install.ps1              # Windows installer (PowerShell)
├── install.sh               # macOS installer (Bash)
├── docker-compose.yml       # git-ignored; copied here by installer
├── README.md
├── backend/
│   ├── ai.py           # Ollama HTTP client (embeddings + chat)
│   ├── config.py       # config.json read/write with defaults
│   ├── config.defaults.json  # default model names and settings
│   ├── documents.py    # text extraction, OCR, chunking, patient.md I/O, JSON extraction; scans uploads/ subfolder, excludes *.extracted
│   ├── jobs.py         # async ingestion pipeline, background job tracker
│   ├── main.py         # FastAPI routes
│   ├── memory.py       # ChromaDB wrapper (doc chunks + chat history collections)
│   ├── patients.py     # JSON persistence for patient index, patient.json, chat message logs
│   ├── timeline.py     # LLM-based timeline extraction
│   ├── watcher.py      # watchdog-based watcher; monitors uploads/ subfolder per patient; surfaces jobs via /status/{job_id}
│   ├── Dockerfile
│   └── requirements.txt  # includes: fastapi, uvicorn, pymupdf, pytesseract, chromadb, watchdog
├── frontend/
│   ├── app/
│   │   ├── page.tsx             # Home
│   │   ├── patient/[id]/
│   │   │   ├── page.tsx         # Patient
│   │   │   └── settings/
│   │   │       └── page.tsx     # Patient Settings
│   │   └── layout.tsx           # Root layout with header
│   ├── components/
│   │   ├── Header.tsx
│   │   ├── SettingsModal.tsx
│   │   ├── UploadArea.tsx        # drag-and-drop / file picker, calls POST /patients/{id}/upload
│   │   ├── Chat.tsx
│   │   ├── Citation.tsx
│   │   ├── DeletePatientModal.tsx  # confirmation modal with per-item checkboxes before delete
│   │   ├── IngestionProgress.tsx
│   │   ├── PatientSettings.tsx   # patient settings form (rename, overrides, document upload/delete, delete patient)
│   │   ├── SummaryPanel.tsx      # renders summary markdown string as HTML
│   │   └── Timeline.tsx
│   ├── lib/
│   │   └── api.ts
│   ├── Dockerfile
│   ├── package.json
│   └── tailwind.config.ts
├── docker/
│   ├── docker-compose.windows-ollama-docker.yml
│   ├── docker-compose.windows-ollama-host.yml
│   ├── docker-compose.mac-ollama-docker.yml
│   └── docker-compose.mac-ollama-host.yml
└── data/                        # bind-mounted volume (git-ignored)
    ├── config/
    │   └── config.json
    ├── patients.json             # thin patient index (includes document_count)
    ├── memory_db/
    └── patients/
        └── mary-johnson/
            ├── patient.json
            ├── patient.md
            ├── uploads/
            └── chats/
```

**Project layout** keeps the root clean. All runtime files (data, config, vector store) live inside a single `./data/` directory which is bind-mounted into the containers and git-ignored. The root contains the installer scripts, `README.md`, `backend/`, `frontend/`, and `docker/` source folders. `docker-compose.yml` is git-ignored and generated by the installer.

```
openhealth/          ← cloned repo root
├── install.ps1      ← Windows installer
├── install.sh       ← macOS installer
├── docker-compose.yml  ← git-ignored; generated by installer
├── README.md
├── backend/
├── frontend/
├── docker/          ← compose variant source files
└── data/            ← bind-mounted volume (git-ignored)
    ├── config/
    │   └── config.json
    ├── patients.json    ← thin patient index
    ├── memory_db/
    └── patients/
        └── mary-johnson/
            ├── patient.json   ← per-patient data
            ├── patient.md     ← generated record
            ├── uploads/       ← source files go here
            └── chats/         ← session message logs
```


---

## Data Storage

All runtime data lives under `./data/` in the project root, which is bind-mounted into the containers and git-ignored. This keeps the repo root clean for non-developer users.

`patients.json` is a thin index (id, name, folder_slug, folder_path, document_count, last_ingested_at, created_at). All patient-specific data lives in a `patient.json` file inside each patient's own folder under `./data/patients/`, preventing any cross-patient data from ever occupying the same structure.

```
./data/
├── config/
│   └── config.json                      # editable before or after startup
├── patients.json                         # index: id, name, folder_slug, folder_path, document_count, last_ingested_at, created_at
├── memory_db/                            # ChromaDB vector store
│   ├── {patient_id}_docs/               # document chunks per patient
│   └── {patient_id}_chat_{session_id}/  # semantic chat memory per session
└── patients/
    └── mary-johnson/                    # auto-created from patient name slug
        ├── patient.json                 # per-patient data: documents, timeline, summary, sessions, states
        ├── patient.md                   # generated — full concatenated record
        ├── uploads/                     # source files uploaded via UI or placed manually
        │   ├── discharge_summary_jan2026.pdf
        │   └── discharge_summary_jan2026.pdf.extracted  # cached extracted plain text
        └── chats/
            └── {session_id}.json        # ordered message log for UI display
```

**`.extracted` files** are written alongside each source file inside `uploads/` during ingestion. They cache the plain-text extraction (PyMuPDF, OCR, or LLM output) so that `patient.md` can be regenerated instantly on refresh without re-running OCR or LLM extraction passes. If a source file is detected as changed (mtime or size differs from the stored document record), its `.extracted` sidecar is overwritten in place before re-processing.

`patient.md`, `patient.json`, and `.extracted` files are excluded from git as they contain personal medical data. Patient folders are created automatically by the backend when a patient is added via the UI. Advanced users can also drop files directly into a patient's `uploads/` folder; the background watcher will detect them.

---

## Configuration

Config is stored at `./data/config/config.json` (inside the mounted volume). It is created with defaults on first run if absent, and is editable directly or via the Settings UI.

```json
{
  "chat_model": "gemma4:e2b",
  "clinical_model": "dcarrascosa/medgemma-1.5-4b-it:Q4_K_M",
  "summary_model": "dcarrascosa/medgemma-1.5-4b-it:Q4_K_M",
  "verification_model": "dcarrascosa/medgemma-1.5-4b-it:Q4_K_M",
  "embedding_model": "nomic-embed-text",
  "embed_timeout_seconds": 60.0,
  "chat_timeout_seconds": 600.0,
  "meta_timeout_seconds": 15.0,
  "chunk_size": 750,
  "chunk_overlap": 80,
  "memory_results": 8,
  "context_window_tokens": 32000,
  "data_path": "/data",
  "ollama_base_url": "http://ollama:11434",
  "routing_mode": "balanced",
  "medgemma_verification_enabled": true,
  "grounding_enabled": true,
  "grounding_max_retries": 1
}
```

Defaults are defined in `backend/config.defaults.json` and merged with any user-saved `config.json` at startup. The installer reads `config.defaults.json` to determine which Ollama models to pull during setup.

Model names are used exactly as configured. There is no model aliasing/normalization layer.

---

## Design Decisions

**Why `patient.md` instead of pure RAG?**
Chunk retrieval picks the most *similar* chunks, not necessarily the most *relevant* ones. A question about medication interactions might miss a dosage buried in a discharge note that used different wording. Sending the full record as context eliminates retrieval failure entirely.

**Why document boundaries in `patient.md`?**
Without explicit start/end markers, the model cannot reliably attribute information to its source when documents share similar language or when the same condition appears across multiple records. Boundaries make citations precise and prevent context from bleeding between documents.

**Why ChromaDB for chat history instead of passing full history?**
Passing the entire chat history on every call grows unbounded and eventually crowds out the patient record in the context window. Semantic retrieval surfaces the most relevant past exchanges for the current question — not just the most recent ones — without sacrificing context window space.

**Why two separate ChromaDB collections per patient?**
Document chunks and chat history have different retrieval semantics, different metadata schemas, and different lifecycle needs (docs are cleared on re-ingest; chat history persists independently). Separating them avoids retrieval interference.

**Why generate the timeline in one LLM call over `patient.md`?**
Per-document timeline calls can only see one document at a time, missing context that spans documents. One call over the full record produces a coherent, deduplicated event list.

**Why FastAPI over direct React → Ollama calls?**
Document processing (PyMuPDF, OCR, chunking) requires Python. ChromaDB's Python client is the most mature interface. Async ingestion requires a server-side job runner.

**Why a server-managed conversation state capsule?**
MedGemma 1.5 is not optimized for multi-turn chat. Persisting a compact rolling summary, active topics, and open questions allows each turn to be reconstructed deterministically without depending on latent model memory.

**Why session-scoped memory for a single patient?**
Users often need separate threads (for example, medication questions vs billing/admin notes) over the same records. Session-scoped storage prevents conceptual bleed and ensures each chat remains coherent to its own intent.

**Why verify responses after generation?**
Multi-turn drift is more likely when the model must carry context across many turns. A post-generation grounding check enforces that key claims are supported by `patient.md` or retrieved snippets, and can trigger a constrained retry when unsupported claims are detected.

**Why avoid sending raw full chat logs every turn?**
Large raw logs increase noise and context collisions. Structured state + semantic retrieval provides continuity with less token overhead and more stable behavior.

**Why per-patient JSON record files instead of one `patients.json`?**
A single file accumulates all patients' documents, timelines, and summaries, growing unbounded and requiring full rewrites on every change. Separate files keep I/O scoped per patient, prevent cross-patient data from ever occupying the same structure, and make corruption recovery easier.

**Why incremental ingestion by default?**
Re-processing all documents on every file addition is wasteful when a patient already has many files. Incremental ingestion appends only the new documents, keeping ingestion time proportional to the number of new files rather than the total. Full rebuild remains available for cases where document order, extraction quality, or embedding consistency needs resetting.

**Why token-count before inference rather than catching Ollama errors?**
Catching a context-length error from Ollama means a full LLM round-trip was wasted. A pre-flight character-based approximation (`len(text) / 4`) is fast, free, and deterministic. It may occasionally underestimate token count for unusual scripts, so the `context_window_tokens` default is set conservatively at 120,000 (below the model's 128K limit). Users can adjust this in Settings if needed.

**Why `.extracted` sidecar files?**
Re-running OCR or LLM extraction on every `patient.md` rebuild is slow and expensive. Caching extracted text alongside the source file makes rebuilds near-instant and lets the system detect changes by comparing file modification times.

**Why uploads/ subfolder instead of patient root?**
Separating source files from app-generated files (`patient.json`, `patient.md`) eliminates the need for an exclusion list. The watcher monitors `uploads/` only, so writes to app-generated files never trigger spurious re-ingestion. The only exclusion still needed is `*.extracted` within `uploads/`, since those are cached extraction outputs placed alongside source files during ingestion.

**Why no authentication?**
This is a local, single-user-trusted application. Docker Compose binds to localhost only and the data volume is on the user's own machine. A separate SaaS project will handle multi-user auth and access control.

---

## System Prompts

### Chat
```
You are OpenHealth, a knowledgeable and compassionate medical AI assistant.
You have been given the full medical record for this patient as context.
Use the documents to ground your responses — interpret, explain, and connect information across them.
When referencing specific information, cite the source document.
Be direct, warm, and clear. Write in paragraphs, not bullet points.
You are not limited to only what is in the documents — use your medical knowledge
to help the user understand, interpret, and act on what the records contain.
```

### Conversation state update
```
You are maintaining a compact clinical conversation state for a medical assistant.
Given the latest user message, assistant response, and prior state:
1) Update rolling_summary in 4-8 sentences
2) Update active_topics as short phrases
3) Update open_questions with unresolved items only
Keep the state factual, concise, and grounded in the conversation and records.
Do not invent patient facts.
```

### Grounding verifier
```
You are a strict grounding verifier.
Given a draft assistant answer and source context (patient.md plus retrieved snippets),
label each major claim as SUPPORTED, PARTIAL, or UNSUPPORTED.
Return:
- a corrected answer that removes or qualifies unsupported claims
- citations for every supported claim
- an uncertainty note where evidence is insufficient
Do not add new clinical facts that are not in evidence.
```

### Summary generation — Pass 1 (extraction)
```
You are a clinical data extractor. Extract structured information from the following
medical records. Return ONLY valid JSON in this exact format — no prose, no explanation:

{
  "demographics": { "age": "", "sex": "", "dob": "" },
  "conditions": ["..."],
  "medications": ["..."],
  "procedures": ["..."],
  "key_labs": ["..."],
  "allergies": ["..."]
}

Rules:
- Extract only information that is explicitly stated in the records.
- Do not invent or infer. Leave arrays empty if nothing is found.
- Medications: include drug name and dosage if present.
- Labs: include test name and value/date if present.
- Conditions: use the clinical terminology from the records.
```

### Summary generation — Pass 2 (prose)
```
You are a medical summarization assistant. Be precise and factual.
The following pre-extracted clinical facts have been verified from the patient's records.
Use them to produce a markdown-formatted summary using the exact section headers below.
Do not invent facts not present in the provided data. Keep each section concise.

## Overview
3-4 sentences summarizing the patient's overall medical picture based on the facts below.

## Active Conditions
[pre-extracted conditions list injected here]

## Current Medications
[pre-extracted medications list injected here]

## Recent Procedures
[pre-extracted procedures list injected here]

## Key Concerns
Note patterns, gaps, or items that warrant attention based on the above facts.
```

### Timeline generation
```
You are a medical timeline extractor.
Extract every medical event from the following records as a chronological list.
For each event include: exact date, one-line title, document type, source filename.
Do not infer or summarize — only extract what is explicitly stated.
```

### JSON extraction
```
You are a medical data extractor. You have been given the raw contents of a JSON file
exported from a healthcare system. The structure and field names may be unfamiliar.
Extract all clinically relevant information — diagnoses, medications, lab results,
procedures, dates, provider notes, and any other health-related data — and present it
as clear, readable plain text. Do not include technical metadata, IDs, or system fields
unless they carry clinical meaning. Preserve all dates and values exactly as they appear.
```

---

## Key Behaviors

1. **Async ingestion** — ingestion never blocks the UI. Always runs as a background job. Upload returns a `job_id` directly; watcher-triggered jobs are discovered via `GET /patients/{id}/active-job`. Frontend polls every 2 seconds.
2. **New file detection** — the `watchdog` watcher monitors each patient's `uploads/` subfolder. When new files appear (from manual copy, not upload), an incremental ingestion job starts automatically and is surfaced to the UI via `GET /patients/{id}/active-job` polling.
3. **Citation format** — always at the bottom of every AI response, never inline.
4. **Context window fallback** — before each inference call the backend estimates `patient.md` token count using `len(text) / 4` (character-based approximation). If the estimate exceeds `context_window_tokens` from config (default `120000`, a safe margin below the model's 128K limit), it falls back to ChromaDB document chunk retrieval. This is deterministic and avoids wasted round-trips from Ollama context-length errors.
5. **Chat history persistence** — every exchange is embedded and stored in ChromaDB. Relevant history is retrieved semantically on each new message.
6. **Offline** — no network calls except to localhost Ollama.
7. **Stateless turn reconstruction** — every answer is generated from an explicit context packet (patient record + retrieved memory + state capsule), never by trusting model-native multi-turn continuity.
8. **Grounding gate** — when `grounding_enabled: true`, each response is verified. If unsupported claims are found, the backend retries up to `grounding_max_retries` times (default 2). If retries are exhausted, the best available response is returned with uncertainty markers. The response includes `grounding_retried: true` and `retry_count` when retries occurred; the UI displays a subtle "Answer was refined" indicator attached to the assistant message bubble when `grounding_retried` is true.
9. **Session isolation** — all chat memory/state reads and writes are scoped by `{ patient_id, chat_session_id }`; no cross-session retrieval.
10. **Session reset control** — users can reset one chat session without affecting other sessions for the same patient.
11. **Auto-titled sessions** — session title is set to "New Chat" on creation; after the first exchange the backend generates a descriptive title via LLM. User renames lock the title permanently.
12. **Message log for display** — ordered messages are written to `./data/patients/{slug}/chats/` on every turn and read back directly for UI rendering; ChromaDB is used only for semantic retrieval, not display.
13. **No authentication** — local single-user app; no login required.
14. **Optimistic chat send** — when the user sends a message, it is displayed immediately with a "Sending..." status indicator. On success the message is replaced by the server-confirmed exchange. On failure the message remains visible with a "Failed to send. Please retry." notice rather than disappearing; the error is also logged to the browser console.
15. **Markdown rendering in chat** — assistant responses are rendered as sanitized HTML using `marked` + `DOMPurify` with the `.markdown-body` stylesheet. User messages remain plain text. This mirrors the rendering behavior of the Summary panel.
16. **Backend observability** — every LLM call logs the full request payload and response to the `uvicorn.error` logger. Transport and HTTP errors log status codes and response bodies. The `/chat` endpoint logs lifecycle events (request received, context prepared, response sent) and catches all unhandled exceptions for structured logging.

---

## UI Layout

### Global Header

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  OpenHealth v1                                                          ⚙  │
└─────────────────────────────────────────────────────────────────────────────┘
```

Present on every page. The cog icon (⚙) opens the Settings modal as an overlay. No separate settings page or route.

### Settings Modal

Slides in as a right-side panel or centered modal when the cog is clicked. Displays and saves all values from `./data/config/config.json` via `GET /config` and `POST /config`.

- Model selector (dropdown populated from `GET /models`)
- Embedding model selector (dropdown populated from `GET /models`)
- Chunk size input (number)
- Chunk overlap input (number)
- Grounding enabled toggle (on by default)
- Grounding max retries input (number, default 2)
- Context window tokens input (number, default 120000)
- Ollama base URL override (text input)

Changes take effect immediately on save. The modal closes on save or on click-outside / Escape.

### Patient Page

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  OpenHealth v1                                                            ⚙   │
└────────────────────────────────────────────────────────────────────────────────┘
┌──────────────────┬──────────────────────────────────────┬──────────────────────┐
│  Left Sidebar    │  Main — Chat                         │  Right Sidebar       │
│──────────────────│──────────────────────────────────────│──────────────────────│
│ Patients         │  "Medication review"  [rename]       │  Summary             │
│  ▸ Mom      ←    │  ─────────────────────────────────── │  ──────────────────  │
│    Dad           │                                      │  [structured text]   │
│    + Add Patient │  [message bubbles, scrollable]       │                      │
│                  │                                      │  Timeline            │
│ Chats            │                                      │  ──────────────────  │
│  + New Chat      │                                      │  ● 2026-01-15        │
│  ───────────     │                                      │    Discharge         │
│  ▸ Medication    │                                      │  ● 2026-03-04        │
│    review        │                                      │    Lab results       │
│    Follow-up     │                                      │  ● 2026-04-10        │
│    questions     │                                      │    Cardiology appt   │
│                  │  [text input]             [Send]     │                      │
└──────────────────┴──────────────────────────────────────┴──────────────────────┘
```

- **Left sidebar**: patient selector (all patients listed, active highlighted) followed by the chat/session list for the selected patient. "New Chat" button at the top of the list. A settings icon next to the active patient name links to `/patient/[id]/settings`.
- **Main area**: active chat session. Shows session title with inline rename on click. Message bubbles for user and assistant. Citations rendered below each assistant message. Text input fixed at the bottom.
- **Right sidebar**: summary rendered as markdown-to-HTML in a scrollable panel (`SummaryPanel.tsx`). The summary LLM output uses `##` markdown headers for each section — Overview, Active Conditions, Medications, Recent Procedures, Key Concerns. Frontend converts the stored markdown string to HTML for display and must sanitize the rendered HTML before injection. Vertical timeline below the summary panel (scrollable, chronological, oldest at top). Timeline is informational display only — events are not clickable.

### Home Page

Simple patient list with name, document count, last ingested date, and an "Open" button per patient. "Add Patient" opens a modal: user enters name, backend creates the folder slug automatically, then the modal transitions to a file upload step (drag-and-drop or file picker). The user can skip the upload step and upload files later from the Patient page. Upload completion triggers ingestion and redirects to the Patient page.

### Settings Page

Settings are accessed via the cog icon (\u2699) in the global header and open as an overlay panel. There is no dedicated settings route. See **Settings Modal** above for field details.

### Patient Settings Page (`/patient/[id]/settings`)

A dedicated page for per-patient configuration, accessible via a settings icon or link on the Patient page. Contains:

- **Rename patient** — editable name field. On save, calls `PATCH /patients/{id}` with `{ name }`. Backend updates the `patients.json` entry; the folder slug is not renamed (slug is permanent once created).
- **Multi-turn memory threshold** — `memory_results` override for this patient (number of past exchanges retrieved per turn). Defaults to the global `memory_results` from config if not set.
- **Context window override** — `context_window_tokens` override for this patient. Defaults to global config value if not set.
- **Documents loaded panel** — shows all currently ingested documents for the patient from `GET /documents/{patient_id}`.
- **Upload documents** — uses the same upload flow as the patient workspace (`POST /patients/{id}/upload`) and shows ingestion progress.
- **Delete document** — per-document delete action calls `DELETE /documents/{patient_id}/{document_id}` and triggers rebuild job progress.
- **Delete patient** — opens the same confirmation modal with per-item checkboxes as the home-page delete flow.

Per-patient overrides (`memory_results_override`, `context_window_tokens_override`) are stored in the patient's `patient.json`. When present, they take precedence over global config values for that patient only. When absent or `null`, global config is used.

# UX Principles

- Calm tone
- No alarmist language
- Always cite source
- Clear distinction between facts vs interpretation
- Plain-English default

# Branding, Styling, Theme

Create styling guidelines.

Prototype implementation rule:
- Even for early mock screens and prototypes, use a shared `app.css` file as the primary global stylesheet entry point
- Define all theme colors as CSS variables in `app.css`
- Do not hardcode hex values directly in components or one-off mock styles
- Color changes during prototyping should be handled by updating variables first, so visual direction can pivot quickly without refactoring screens

## Visual Direction (Light Theme)
- Tone: calm, supportive, confident
- Theme baseline: light-first with high readability
- Visual style: clean clinical utility with warm accents (not sterile, not playful)

## Color Tokens (Initial)

| Token | CSS Variable | Value | Usage |
|---|---|---|---|
| Background | `--color-background` | `#F8F9FA` | Page background |
| Surface | `--color-surface` | `#FFFFFF` | Cards, panels |
| Surface Elevated | `--color-surface-elevated` | `#F1F3F5` | Hover states, sidebars |
| Border | `--color-border` | `#E5E7EB` | Dividers, card outlines |
| Text Primary | `--color-text-primary` | `#1A1D21` | Body text, headings |
| Text Secondary | `--color-text-secondary` | `#6B7280` | Labels, metadata, captions |
| Text Muted | `--color-text-muted` | `#9CA3AF` | Placeholder, disabled |
| Primary | `--color-primary` | `#2E7D6B` | Buttons, active states, links |
| Primary Hover | `--color-primary-hover` | `#245F54` | Button hover |
| Primary Light | `--color-primary-light` | `#E6F2EF` | Highlight backgrounds |
| Success | `--color-success` | `#16A34A` | Completion states |
| Warning | `--color-warning` | `#D97706` | Needs attention |
| Error | `--color-error` | `#DC2626` | Errors, failures |

All values are starting points — adjust by updating CSS variables in `app.css` only.

Implementation requirement:
- Map these tokens to CSS custom properties in `app.css` (example naming: `--color-background`, `--color-surface`, `--color-text-primary`, `--color-primary`)
- Screens, components, and mock layouts must consume the variables rather than raw color literals

Accessibility targets:
- WCAG 2.1 AA contrast for text and controls
- Do not use color alone to communicate risk or urgency

## Typography

| Role | Family | Weight | Size |
|---|---|---|---|
| UI base | Inter, system-ui, sans-serif | 400 | 16px |
| Heading H1 | Inter | 700 | 28px |
| Heading H2 | Inter | 600 | 22px |
| Heading H3 | Inter | 600 | 18px |
| Label / Caption | Inter | 500 | 13px |
| Code / mono | JetBrains Mono, ui-monospace, monospace | 400 | 14px |

Inter is loaded via `next/font` (no external CDN call — consistent with offline-first requirement). Line height: 1.6 for body, 1.2 for headings.

## Layout and Components
- 12-column desktop grid, 4-column mobile grid
- Card-based information hierarchy for Daily Brief, What Changed, and Timeline modules
- Sticky top navigation with patient context and quick upload action
- Timeline entries must prioritize date, event type, and source link visibility

Core component states:
- Upload status chips: `Processing`, `Ready`, `Needs Review`
- Confidence indicator: sentence/document confidence shown with neutral UI, not alarmist color spikes
- Citation anchors: sentence-level references with hover/focus highlight in document preview

## Motion and Interaction
- Use subtle, purposeful motion only:
  - Staggered card reveal on dashboard load (100-180ms offsets)
  - Smooth expand/collapse for timeline details
  - Gentle highlight transition for newly detected changes
- Respect `prefers-reduced-motion`

## Trust and Safety UI Cues
- Separate "Facts from source" and "AI interpretation" in all summary views
- Show uncertainty explicitly (e.g., "Possible medication change - please confirm")
- Require user confirmation for ambiguous medication matches and multi-date selection
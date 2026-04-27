# OpenHealth — Backend Specification

Source of truth for the FastAPI backend service. See `spec-docker.md` for how the service is wired into the compose stack and `spec-frontend.md` for the UI that consumes these APIs.

---

## Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12 |
| Framework | FastAPI + Uvicorn |
| AI inference | Ollama (local) via HTTP |
| Default chat model | `MedAIBase/MedGemma1.5:4b` (128K context window) |
| Default embedding model | `nomic-embed-text` (768-dim) |
| Vector store | ChromaDB (persistent, cosine similarity) |
| PDF extraction | PyMuPDF (fitz) |
| OCR | Tesseract 5 via PyMuPDF `get_textpage_ocr()` |
| TIFF extraction | Pillow + pytesseract |
| Directory watching | watchdog |
| Data persistence | JSON files (`patients.json` thin index + per-patient `patient.json`) |

---

## File Structure

```
backend/
├── ai.py           # Ollama HTTP client (embeddings + chat completions)
├── config.py       # config.json read/write with defaults
├── documents.py    # text extraction, OCR, chunking, patient.md I/O, JSON extraction
                    # scans uploads/ subfolder only; excludes *.extracted
├── jobs.py         # async ingestion pipeline, background job tracker
├── main.py         # FastAPI routes (all endpoints)
├── memory.py       # ChromaDB wrapper (doc chunks + chat history collections)
├── patients.py     # JSON persistence for patient index, patient.json, chat message logs
├── timeline.py     # LLM-based timeline extraction
├── watcher.py      # watchdog-based watcher; monitors uploads/ per patient
├── Dockerfile
└── requirements.txt  # fastapi, uvicorn, pymupdf, pytesseract, chromadb, watchdog, pillow
```

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

**JSON ingestion**: the raw JSON is serialised to a human-readable string and passed to the LLM with a prompt asking it to extract all clinically relevant information as plain text. The resulting text is then chunked and embedded like any other document. No assumptions are made about field names or nesting.

---

## File Scan Rules

- Ingestion scans only the **top level** of `uploads/` — subdirectories within it are ignored.
- Exclusion: `*.extracted` files (cached extraction outputs, not source documents).
- All other files matching supported extensions (`.pdf`, `.tif`, `.tiff`, `.txt`, `.html`, `.json`) are processed.
- App-generated files (`patient.json`, `patient.md`) live outside `uploads/` in the patient root and are never scanned.

---

## Ingestion Pipeline

```
PDFs / TIFFs / TXTs / HTMLs / JSONs
        │
        ▼
  extract_text()
  ├── embedded text (PyMuPDF)
  └── OCR fallback (Tesseract via PyMuPDF) ← handles scanned PDFs
        │
        ▼
  chunk_text()  →  embed chunks  →  ChromaDB (_docs collection)
        │
        ▼
  write_patient.md  (all docs concatenated with document boundaries)
        │
        ├──▶  generate_full_timeline()  →  one LLM call  →  timeline events
        │
        └──▶  _regenerate_summary()    →  one LLM call  →  structured summary
```

Ingestion always runs as a **background job**. The endpoint returns `{ job_id }` immediately. Frontend polls `/status/{job_id}` every 2 seconds for progress.

### Triggers

- **Upload**: `POST /patients/{id}/upload` writes files and starts an incremental job; returns `job_id` directly.
- **Directory watcher**: `watchdog` monitors each patient's `uploads/` subfolder. Detects new files (from manual copy) and triggers an incremental job. Job is surfaced to the UI via `GET /patients/{id}/active-job` polling — there is no client-initiated `job_id` in this path.

### Incremental ingestion (default)

Only files not yet in `patient.json` or files detected as changed are processed. Change detection compares `mtime` (Unix timestamp) and `size` (bytes) against stored document record values. `patient.md` is appended/updated for changed files only. Corresponding chunks are upserted in the `_docs` ChromaDB collection. Existing chat history is never touched.

If a source file is detected as changed, its `.extracted` sidecar is **overwritten in place** before re-processing.

### Full rebuild (`POST /rebuild/{patient_id}`)

Clears `patient.md`, drops and re-creates the `_docs` ChromaDB collection, and re-processes every file in `uploads/` from scratch. Timeline and summary are also regenerated. Chat history in `_chat` collections is **never cleared** on any rebuild.

---

## patient.md Structure

Single concatenated file containing the full text of every ingested document. Each document is wrapped in explicit boundary markers.

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

Documents are ordered **chronologically by detected date**. Boundaries allow the model to attribute any piece of information to its source document, enabling precise citations.

---

## Chat Pipeline

```
User question + patient_id + chat_session_id
      │
      ▼
classify_query()               ← detects document_type of query (lab, medication, procedure)
      │
      ▼
retrieve_chat_history()        ← ChromaDB semantic memory (top-K) filtered by patient_id + chat_session_id
      │
      ▼
load_conversation_state()      ← persisted rolling summary + open threads + facts for session
      │
      ▼
read_patient_md()              ← full record with document boundaries
      │
      ▼
assemble_turn_context()        ← stateless packet:
  - patient.md (or ChromaDB docs fallback if too large)
  - relevant prior exchanges
  - conversation_state capsule
  - current user question
      │
      ▼
Ollama (MedAIBase/MedGemma1.5:4b)
      │
      ▼
verify_grounding()             ← citation + factual support check (if grounding_enabled)
      │
      ▼
finalize_response()            ← answer + citations + uncertainty notes
      │
      ▼
store_exchange()               ← embed/store Q&A in ChromaDB (_chat collection)
      │
      ▼
update_conversation_state()    ← refresh rolling summary + unresolved questions
```

**Context window pre-flight**: before inference, estimate token count with `len(patient_md_text) / 4`. If estimate exceeds `context_window_tokens` (per-patient override → global config, default `120000`), fall back to ChromaDB document chunk retrieval instead of passing `patient.md` directly.

**Grounding gate**: when `grounding_enabled: true`, each response is verified after generation. If unsupported claims are found, retry up to `grounding_max_retries` times (default 2). If retries exhausted, return best available response with uncertainty markers. Response includes `grounding_retried: true` and `retry_count` when retries occurred.

**Session isolation**: all retrieval and state updates are filtered by both `patient_id` and `chat_session_id`. Cross-session retrieval is never allowed.

**Session auto-title**: on creation, title is "New Chat". After the first exchange completes, generate a descriptive title via LLM and update the session. If `title_auto_generated` is `false` (user renamed), skip auto-titling permanently.

---

## ChromaDB — Dual Role

### 1. Document chunks
- Collection name: `patient_{patient_id}_docs`
- Populated during ingestion; cleared on full rebuild only
- Metadata: `{ patient_id, document_id, filename, chunk_index, date, document_type }`
- Used as context fallback when `patient.md` exceeds context window

### 2. Chat history / semantic memory
- Collection name: `patient_{patient_id}_chat_{chat_session_id}`
- Every exchange (Q+A pair) is embedded and stored after each turn
- Retrieved semantically before each new message — surfaces relevant past exchanges
- Metadata: `{ patient_id, chat_session_id, role, timestamp }`
- All queries filtered by both `patient_id` and `chat_session_id`

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

`patients.json` is a flat array — the only file read when listing patients. `document_count` and `last_ingested_at` are denormalized here so the home page can display them without loading each `patient.json`. Both are updated at the end of every ingestion job. Patient name is also updated here when renamed via `PATCH /patients/{id}`.

### Patient Record (`/data/patients/{slug}/patient.json`)
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
  "file_path": "./uploads/{filename}",
  "extracted_file_path": "./uploads/{filename}.extracted",
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

Maintained server-side; refreshed each turn. Compact structured memory capsule that compensates for MedGemma not being optimized for long multi-turn dialog.

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

### Chat Message Log (`/data/patients/{slug}/chats/{session_id}.json`)
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

Flat ordered log; source of truth for rendering chat history in the UI. Separate from ChromaDB semantic memory (which is used only for retrieval during turn assembly).

---

## API Endpoints

### Patients

- `GET /patients` — list all patients (reads `patients.json`)
- `POST /patients` — create patient `{ name }`. Slugify name (e.g. "Mary Johnson" → `mary-johnson`). If slug exists, append counter (`mary-johnson-2`). Create `./data/patients/{slug}/` and `uploads/` subfolder. Write entry to `patients.json`. Returns `{ id, name, folder_slug, folder_path, document_count, last_ingested_at, created_at }`.
- `GET /patients/{id}` — returns thin shape: `{ id, name, folder_slug, folder_path, document_count, last_ingested_at, created_at }`.
- `PATCH /patients/{id}` — accepts any subset of `{ name, memory_results_override, context_window_tokens_override }`. Renaming updates `patients.json` entry; folder slug is never changed. Returns updated thin shape.
- `DELETE /patients/{id}` — always removes `patients.json` index entry. Accepts `{ delete_uploads: bool, delete_chats: bool, delete_record_files: bool, delete_vector_data: bool }`. All flags optional and independent. If all flags are `false`, only the index entry is removed (all data stays on disk).

### Ingestion

- `POST /ingest/{patient_id}` — start incremental ingestion; returns `{ job_id }` immediately.
- `POST /rebuild/{patient_id}` — full rebuild: clears `patient.md`, drops/re-creates `_docs` ChromaDB collection, reprocesses all files. Returns `{ job_id }`.
- `POST /refresh/{patient_id}` — alias for incremental ingest; kept for internal use by the watcher.
- `GET /status/{job_id}` — returns job status and progress (`{ job_id, patient_id, status, total, processed, current_file, started_at, completed_at }`).
- `GET /documents/{patient_id}` — list all ingested documents for a patient.
- `GET /patients/{id}/active-job` — returns the currently running ingestion job for a patient, or `null`. Used to surface watcher-triggered jobs to the frontend.

### Upload

- `POST /patients/{id}/upload` — multipart/form-data; one or more files. Writes files to `./data/patients/{slug}/uploads/`. Triggers incremental ingestion automatically. Returns `{ filenames: [], job_id }`.

### Summary & Timeline

- `GET /summary/{patient_id}` — returns `{ "summary": "markdown string" }`.
- `POST /summary/{patient_id}` — regenerates summary via LLM; returns `{ "summary": "markdown string" }`.
- `GET /timeline/{patient_id}` — returns timeline events sorted by date.
- `GET /timeline/{patient_id}/{event_id}` — returns a single timeline event.

### Chat

- `POST /chat`

  Request:
  ```json
  {
    "patient_id": "uuid",
    "chat_session_id": "uuid",
    "message": "string"
  }
  ```
  Response:
  ```json
  {
    "response": "string",
    "grounding_retried": false,
    "retry_count": 0,
    "citations": [
      { "filename": "string", "excerpt": "string" }
    ]
  }
  ```

  Conversation history is managed server-side via ChromaDB. The frontend does not pass history on each request.

- `GET /patients/{patient_id}/chat-sessions` — list sessions for patient.
- `POST /patients/{patient_id}/chat-sessions` — create session `{ title? }`. Title defaults to "New Chat"; LLM-generated title applied after first exchange. Returns `{ chat_session_id }`.
- `PATCH /patients/{patient_id}/chat-sessions/{chat_session_id}` — rename session `{ title }`. Sets `title_auto_generated: false`.
- `DELETE /patients/{patient_id}/chat-sessions/{chat_session_id}` — delete one session (memory, state, messages only).
- `GET /chat/messages/{patient_id}/{chat_session_id}` — return ordered message log from `./data/patients/{slug}/chats/`.
- `GET /chat/state/{patient_id}/{chat_session_id}` — return conversation state capsule.
- `POST /chat/reset/{patient_id}/{chat_session_id}` — clear memory + reset state for one session.
- `POST /chat/rebuild-state/{patient_id}/{chat_session_id}` — recompute state from stored exchanges.

### Config

- `GET /config` — get current model settings.
- `POST /config` — update any subset of config fields.
- `GET /models` — list available Ollama models.

---

## Configuration

Stored at `./data/config/config.json`. Created with defaults on first run if absent.

```json
{
  "model": "MedAIBase/MedGemma1.5:4b",
  "embedding_model": "nomic-embed-text",
  "chunk_size": 900,
  "chunk_overlap": 100,
  "memory_results": 15,
  "context_window_tokens": 120000,
  "data_path": "/data",
  "ollama_base_url": "http://ollama:11434",
  "grounding_enabled": true,
  "grounding_max_retries": 2
}
```

Per-patient overrides in `patient.json` (`memory_results_override`, `context_window_tokens_override`) take precedence over these global values when non-null.

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

### Summary generation
```
You are a medical summarization assistant. Be precise and factual.
Based on the following medical documents, produce a markdown-formatted summary
using the exact section headers below. Keep each section concise.

## Overview
3-4 sentences summarizing the patient's overall medical history.

## Active Conditions
List current diagnosed conditions.

## Current Medications
List medications with dosage where available.

## Recent Procedures
List recent procedures or hospitalizations.

## Key Concerns
Note patterns, gaps, or items that warrant attention.
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

1. **Async ingestion** — ingestion never blocks. Always runs as a background job. Upload returns `job_id` directly; watcher jobs are discovered via `GET /patients/{id}/active-job`.
2. **Incremental change detection** — uses `mtime + size` per document record. Changed `.extracted` sidecars are overwritten in place.
3. **Context window fallback** — pre-flight `len(text) / 4` estimate. Falls back to ChromaDB chunk retrieval if estimate exceeds `context_window_tokens`. Default `120000` is a safe margin below model's 128K limit.
4. **Grounding gate** — when enabled, verifies each response; retries up to `grounding_max_retries` (default 2). Returns `grounding_retried` and `retry_count` in response.
5. **Session isolation** — all memory/state reads/writes scoped by `{ patient_id, chat_session_id }`. No cross-session retrieval.
6. **Stateless turn reconstruction** — every answer rebuilt from persisted memory + patient data. No reliance on model-native multi-turn state.
7. **Auto-titled sessions** — "New Chat" on creation; LLM generates title after first exchange. User renames lock title permanently (`title_auto_generated: false`).
8. **Message log for display** — ordered messages written to `./data/patients/{slug}/chats/{session_id}.json` on every turn and read back directly for UI rendering. ChromaDB used only for semantic retrieval.
9. **No authentication** — local single-user app; no login required.
10. **Offline** — no network calls except to localhost Ollama.

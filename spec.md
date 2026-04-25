# OpenHealth — Technical Specification

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│  React + Vite  (localhost:5173)                 │
│  Pages: Home · Patient · Settings              │
└───────────────────┬─────────────────────────────┘
                    │ HTTP / JSON
┌───────────────────▼─────────────────────────────┐
│  FastAPI  (localhost:8000)                      │
│                                                 │
│  POST /ingest/{id}  →  Background job           │
│  POST /chat         →  patient.md + Ollama      │
│  GET  /timeline     →  patients.json            │
│  GET  /summary      →  patients.json            │
└──────────┬──────────────────────┬───────────────┘
           │                      │
┌──────────▼──────┐    ┌──────────▼──────────────┐
│  Ollama         │    │  ChromaDB (persistent)   │
│  medgemma1.5    │    │  ~/openhealth_data/      │
│  nomic-embed    │    │  memory_db/              │
└─────────────────┘    └─────────────────────────┘
```

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, Vite, React Router v6 |
| Backend | Python 3.12, FastAPI, Uvicorn |
| AI inference | Ollama (local) |
| Default chat model | `medgemma1.5:latest` |
| Default embedding model | `nomic-embed-text` (768-dim) |
| Vector store | ChromaDB (persistent, cosine similarity) |
| PDF extraction | PyMuPDF (fitz) |
| OCR | Tesseract 5 via PyMuPDF `get_textpage_ocr()` |
| Data persistence | JSON file (`~/openhealth_data/patients.json`) |

---

## Supported File Types

| Type | Extraction method |
|---|---|
| `.pdf` (text-based) | PyMuPDF embedded text |
| `.pdf` (scanned/image) | PyMuPDF → Tesseract OCR fallback |
| `.tif` / `.tiff` | Pillow + pytesseract |
| `.txt` | direct read |
| `.html` | tag stripping + html.unescape |

Scanned PDFs that produce no text via PyMuPDF automatically fall back to Tesseract. Files where OCR also yields nothing are recorded as processed (so they don't re-appear as "new") but excluded from `patient.md`.

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
        └──▶  _regenerate_summary()    →  one LLM call  →  structured summary
```

Ingestion runs as a background job. Frontend polls `/status/{job_id}` every 2 seconds and displays live progress.

On refresh (new files detected): `patient.md` is fully rebuilt from scratch and the `_docs` ChromaDB collection is cleared and re-embedded. This ensures document order, boundaries, and chunk indexes are always consistent. Chat history in `_chat` is never cleared on refresh.

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
User question
      │
      ▼
read_patient_md()              ← full record with document boundaries
      │
      ▼
retrieve_chat_history()        ← ChromaDB: last N relevant exchanges for this patient
      │
      ▼
CHAT_SYSTEM + patient.md       ← embedded in system prompt
      + retrieved chat history  ← injected as prior context
      │
      ▼
Ollama (medgemma1.5:latest)    ← sees full record + relevant history
      │
      ▼
Response + citation parsing
      │
      ▼
store_exchange()               ← embed and store Q&A pair in ChromaDB
```

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
- Metadata: `{ patient_id, role, timestamp }`
- Collection name: `patient_{patient_id}_chat`

---

## Data Model

### Patient
```json
{
  "id": "uuid",
  "name": "string",
  "folder_path": "/absolute/path/to/folder",
  "created_at": "ISO timestamp",
  "last_ingested_at": "ISO timestamp",
  "document_count": 12
}
```

### Document
```json
{
  "id": "uuid",
  "patient_id": "uuid",
  "filename": "discharge_summary_jan2026.pdf",
  "file_path": "/absolute/path/to/file",
  "extracted_text": "string",
  "date_detected": "2026-01-15",
  "document_type": "discharge summary | lab result | imaging | prescription | clinical note | unknown",
  "ingested_at": "ISO timestamp"
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

---

## API Endpoints

### Patients
- `GET /patients` — list all patients
- `POST /patients` — create patient `{ name, folder_path }`
- `GET /patients/{id}` — get patient detail
- `DELETE /patients/{id}` — delete patient and all ChromaDB data

### Ingestion
- `POST /ingest/{patient_id}` — start background ingestion, returns `{ job_id }` immediately
- `POST /refresh/{patient_id}` — start background refresh (new files only), returns `{ job_id }`
- `GET /status/{job_id}` — returns job status and progress
- `GET /documents/{patient_id}` — list all ingested documents

### Summary & Timeline
- `GET /summary/{patient_id}` — return stored summary
- `POST /summary/{patient_id}` — regenerate summary
- `GET /timeline/{patient_id}` — return timeline events sorted by date

### Chat
- `POST /chat`
  ```json
  Request:
  {
    "patient_id": "uuid",
    "message": "string"
  }

  Response:
  {
    "response": "string",
    "citations": [
      {
        "filename": "string",
        "excerpt": "string"
      }
    ]
  }
  ```

Note: conversation history is managed server-side via ChromaDB. The frontend does not need to pass history on each request.

### Config
- `GET /config` — get current model settings
- `POST /config` — update `{ model_name, embedding_model }`
- `GET /models` — list available Ollama models

---

## File Structure

```
openhealth/
├── backend/
│   ├── ai.py           # Ollama HTTP client (embeddings + chat)
│   ├── config.py       # config.json read/write with defaults
│   ├── documents.py    # text extraction, OCR, chunking, patient.md I/O
│   ├── jobs.py         # async ingestion pipeline, background job tracker
│   ├── main.py         # FastAPI routes
│   ├── memory.py       # ChromaDB wrapper (doc chunks + chat history collections)
│   ├── patients.py     # JSON persistence for patients, docs, timeline, summaries
│   ├── timeline.py     # LLM-based timeline extraction
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── api.js
│       ├── pages/
│       │   ├── Home.jsx
│       │   ├── Patient.jsx
│       │   └── Settings.jsx
│       └── components/
│           ├── Chat.jsx
│           ├── Citation.jsx
│           ├── IngestionProgress.jsx
│           ├── SummaryModal.jsx
│           └── Timeline.jsx
├── config.json
└── start.sh / start.ps1
```

---

## Data Storage

```
~/openhealth_data/
├── patients.json               # all patient records, documents, timeline, summaries
└── memory_db/                  # ChromaDB vector store
    ├── patient_{id}_docs/      # document chunks per patient
    └── patient_{id}_chat/      # chat history per patient

<patient folder>/
└── patient.md                  # generated — full concatenated record with document boundaries
```

`patient.md` is written to the same folder as the source documents and excluded from `.gitignore` as it contains personal medical data.

---

## Configuration

```json
{
  "model": "medgemma1.5:latest",
  "embedding_model": "nomic-embed-text",
  "chunk_size": 900,
  "chunk_overlap": 100,
  "memory_results": 15,
  "data_path": "~/openhealth_data"
}
```

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

### Summary generation
```
You are a medical summarization assistant. Be precise and factual.
Based on the following medical documents, provide:
1. A 3-4 sentence overview of the patient's medical history
2. Current active conditions
3. Current medications
4. Recent procedures or hospitalizations
5. Key concerns or patterns
```

### Timeline generation
```
You are a medical timeline extractor.
Extract every medical event from the following records as a chronological list.
For each event include: exact date, one-line title, document type, source filename.
Do not infer or summarize — only extract what is explicitly stated.
```

---

## Key Behaviors

1. **Async ingestion** — ingestion never blocks the UI. Always runs as a background job. Frontend polls every 2 seconds.
2. **New file detection** — on patient view load, check folder for new files and show a banner if found.
3. **Citation format** — always at the bottom of every AI response, never inline.
4. **Context window fallback** — if `patient.md` exceeds the model's context window, fall back to ChromaDB document chunk retrieval.
5. **Chat history persistence** — every exchange is embedded and stored in ChromaDB. Relevant history is retrieved semantically on each new message.
6. **Offline** — no network calls except to localhost Ollama.
# Developers

This guide is for running OpenHealth locally without Docker.

## What this gives you
- Frontend and backend run directly on your machine.
- Faster iteration with hot reload.
- Uses your local Ollama instance for inference.

## Prerequisites
- **16 GB RAM minimum.** Inference runs entirely on-device, and the pipeline keeps
  several local models resident at once (chat + clinical/verification + vision +
  embeddings). With less than 16 GB, Ollama evicts and reloads model weights between
  steps, making every chat turn slow.
- Node.js 22+
- npm 10+
- Python 3.12
- Ollama installed and running (default: http://127.0.0.1:11434)

Recommended for OCR-heavy documents:
- Tesseract OCR installed and available on PATH

## 1. Install dependencies
From the project root:

```bash
npm install
```

If Python packages did not install during npm setup, run:

```bash
pip install -r backend/requirements.txt
```

## 2. Pull required Ollama models
Use the defaults in backend/config.defaults.json:

```bash
ollama pull gemma4:e2b-it-q4_K_M                  # chat_model
ollama pull dcarrascosa/medgemma-1.5-4b-it:Q4_K_M # clinical_model + verification_model
ollama pull dcarrascosa/medgemma-1.5-4b-it:Q8_0   # vision_model
ollama pull nomic-embed-text:latest               # embedding_model
```

## 3. Run locally (no Docker)
From the project root:

```bash
npm run dev
```

This starts:
- Backend on http://127.0.0.1:8000
- Frontend on http://127.0.0.1:3000

Open http://127.0.0.1:3000 in your browser.

## Optional: run services separately
Backend only:

```bash
npm run dev:backend
```

Frontend only:

```bash
npm run dev:frontend
```

## Data and config
- Data root defaults to ./data
- App config is stored under data/config/config.json
- Patient files and vector data are persisted under ./data

### Performance tuning
Ingestion runs entirely on your local Ollama, so throughput is bound by your
hardware. The slowest step is clinical fact extraction (several model calls per
document).
- **GPU (NVIDIA/AMD/Apple):** set `OLLAMA_NUM_PARALLEL=4` (or higher) in Ollama's
  environment, then raise `extraction_concurrency` in `config.json` to match. This
  overlaps the per-category extraction calls and shortens ingest noticeably.
- **CPU-only (incl. Snapdragon/ARM, where Ollama has no GPU backend):** leave
  `extraction_concurrency` at its default of `1`. Overlapping calls only contend
  for the same cores and can make a call exceed `chat_timeout_seconds`, surfacing
  as a "Needs Review" failure. Expect multi-minute ingests for large documents;
  a smaller/faster `clinical_model` is the main lever here.
- Check what Ollama is actually using with `ollama ps` (the `PROCESSOR` column
  shows the CPU/GPU split).

### Logging and PHI
To protect patient health information, full LLM request/response payloads (which
contain record content), chunk text, generated chat titles, and upload filenames
are **redacted from the logs by default** — even at debug log level. To see them
while debugging locally, set `OPENHEALTH_LOG_PAYLOADS=1` before starting the
backend. Leave it unset in any shared or persisted-log environment.

## Common issues
- Ollama not reachable: make sure Ollama is running on port 11434.
- Python not found: ensure python/pip point to Python 3.12.
- OCR not working on scanned PDFs/TIFFs: install Tesseract and verify it is on PATH.

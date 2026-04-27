"""
Ingestion pipeline + in-memory job tracker.

Jobs run as asyncio background tasks.
The job store lives in memory — job history is lost on restart, which is acceptable
for a local single-user app (the UI polls while the process is running).
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ai
import documents as docs_module
import memory
import patients as pt
import timeline as tl
from config import load_config

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
# patient_id -> job_id for currently running jobs
_patient_active: dict[str, str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_job(job_id: str) -> Optional[dict]:
    return _jobs.get(job_id)


def get_active_job(patient_id: str) -> Optional[dict]:
    job_id = _patient_active.get(patient_id)
    if not job_id:
        return None
    job = _jobs.get(job_id)
    if job and job["status"] == "running":
        return job
    _patient_active.pop(patient_id, None)
    return None


def _create_job(patient_id: str) -> dict:
    job_id = str(uuid.uuid4())
    started_at = _now()
    job = {
        "job_id": job_id,
        "patient_id": patient_id,
        "status": "running",
        "total": 0,
        "processed": 0,
        "current_file": None,
        "phase": "queued",
        "phase_started_at": started_at,
        "started_at": started_at,
        "completed_at": None,
    }
    _jobs[job_id] = job
    _patient_active[patient_id] = job_id
    return job


def _set_phase(job: dict, phase: str, current_file: Optional[str] = None) -> None:
    if job.get("phase") != phase:
        job["phase"] = phase
        job["phase_started_at"] = _now()
    if current_file is not None:
        job["current_file"] = current_file


def _complete_job(job: dict) -> None:
    job["status"] = "complete"
    _set_phase(job, "complete")
    job["completed_at"] = _now()
    job["current_file"] = None
    _patient_active.pop(job["patient_id"], None)


def _fail_job(job: dict, error: str = "") -> None:
    job["status"] = "failed"
    _set_phase(job, "failed")
    job["completed_at"] = _now()
    job["current_file"] = None
    if error:
        job["error"] = error
    _patient_active.pop(job["patient_id"], None)


# ---------------------------------------------------------------------------
# Ingestion helpers
# ---------------------------------------------------------------------------

async def _embed_and_upsert_chunks(
    patient_id: str,
    doc_record: dict,
    text: str,
) -> None:
    """Chunk text, embed each chunk, upsert into ChromaDB _docs collection."""
    cfg = load_config()
    chunks = docs_module.chunk_text(text, cfg.chunk_size, cfg.chunk_overlap)
    if not chunks:
        return

    chunk_ids: list[str] = []
    embeddings: list[list[float]] = []
    metadatas: list[dict] = []

    for i, chunk in enumerate(chunks):
        embedding = await ai.embed(chunk)
        chunk_id = f"{doc_record['id']}_chunk_{i}"
        chunk_ids.append(chunk_id)
        embeddings.append(embedding)
        metadatas.append({
            "patient_id": patient_id,
            "document_id": doc_record["id"],
            "filename": doc_record["filename"],
            "chunk_index": i,
            "date": doc_record.get("date_detected") or "unknown",
            "document_type": doc_record.get("document_type", "unknown"),
        })

    await memory.upsert_doc_chunks(
        patient_id=patient_id,
        chunks=chunks,
        embeddings=embeddings,
        metadatas=metadatas,
        ids=chunk_ids,
    )


def _file_changed(file_path: Path, doc_record: dict) -> bool:
    """Return True if mtime or size differs from the stored record."""
    try:
        current_mtime = os.path.getmtime(file_path)
        current_size = os.path.getsize(file_path)
    except OSError:
        return False
    return (
        current_mtime != doc_record.get("mtime")
        or current_size != doc_record.get("size")
    )


async def _make_llm_json_fn():
    """Return a sync callable that uses the event loop to run LLM JSON extraction."""
    loop = asyncio.get_event_loop()

    def llm_json_fn(json_content: str) -> str:
        future = asyncio.run_coroutine_threadsafe(
            tl.extract_json_document(json_content), loop
        )
        return future.result(timeout=300)

    return llm_json_fn


async def _process_file(
    patient_id: str,
    folder_slug: str,
    file_path: Path,
    existing_doc: Optional[dict],
    job: dict,
    is_rebuild: bool,
) -> dict:
    """
    Extract, embed, and return an updated document record for one file.
    Overwrites .extracted sidecar if file has changed or is being rebuilt.
    """
    extracted_path = file_path.with_suffix(file_path.suffix + ".extracted")
    llm_json_fn = await _make_llm_json_fn()

    # Determine if we need to re-extract.
    if is_rebuild or (existing_doc and _file_changed(file_path, existing_doc)):
        text = await asyncio.to_thread(
            docs_module.overwrite_extracted_sidecar,
            file_path,
            extracted_path,
            llm_json_fn,
        )
    else:
        text = await asyncio.to_thread(
            docs_module.extract_text_sync,
            file_path,
            extracted_path,
            llm_json_fn,
        )

    doc_type = docs_module.detect_document_type(file_path.name, text)
    date_detected = docs_module.detect_date(text)

    doc_record = existing_doc.copy() if existing_doc else {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
    }
    doc_record.update({
        "filename": file_path.name,
        "file_path": f"./uploads/{file_path.name}",
        "extracted_file_path": f"./uploads/{file_path.name}.extracted",
        "date_detected": date_detected or "unknown",
        "document_type": doc_type,
        "ingested_at": _now(),
        "mtime": os.path.getmtime(file_path),
        "size": os.path.getsize(file_path),
    })

    await _embed_and_upsert_chunks(patient_id, doc_record, text)
    return doc_record


def _rebuild_patient_md(patient_folder: Path, doc_records: list[dict]) -> None:
    """Read all .extracted sidecars and write patient.md sorted by date_detected."""
    uploads_dir = patient_folder / "uploads"
    docs_with_text: list[dict] = []

    for record in doc_records:
        extracted = uploads_dir / (record["filename"] + ".extracted")
        if extracted.exists():
            text = extracted.read_text(encoding="utf-8")
        else:
            text = ""
        docs_with_text.append({
            "filename": record["filename"],
            "document_type": record.get("document_type", "unknown"),
            "date_detected": record.get("date_detected", "unknown"),
            "text": text,
        })

    # Sort chronologically; "unknown" dates last.
    docs_with_text.sort(
        key=lambda d: (d["date_detected"] == "unknown", d["date_detected"])
    )
    docs_module.write_patient_md(patient_folder, docs_with_text)


# ---------------------------------------------------------------------------
# Public ingestion entry points
# ---------------------------------------------------------------------------

async def start_incremental_ingestion(patient_id: str) -> dict:
    """Create job, fire background task, return job immediately."""
    job = _create_job(patient_id)
    asyncio.create_task(_run_incremental(patient_id, job))
    return job


async def start_full_rebuild(patient_id: str) -> dict:
    """Create job, fire background task, return job immediately."""
    job = _create_job(patient_id)
    asyncio.create_task(_run_rebuild(patient_id, job))
    return job


# ---------------------------------------------------------------------------
# Background task implementations
# ---------------------------------------------------------------------------

async def _run_incremental(patient_id: str, job: dict) -> None:
    try:
        _set_phase(job, "loading")
        entry = await pt.find_patient_by_id(patient_id)
        if entry is None:
            _fail_job(job, "Patient not found")
            return

        folder_slug = entry["folder_slug"]
        patient_folder = Path(entry["folder_path"])
        uploads_dir = patient_folder / "uploads"

        # Load existing document records for change detection.
        record = await pt.load_patient_record(patient_id)
        if record is None:
            _fail_job(job, "patient.json not found")
            return

        existing_docs: dict[str, dict] = {
            d["filename"]: d for d in record.get("documents", [])
        }

        all_files = await asyncio.to_thread(docs_module.scan_uploads, uploads_dir)

        # Determine which files need processing.
        files_to_process = []
        for fp in all_files:
            ed = existing_docs.get(fp.name)
            if ed is None or _file_changed(fp, ed):
                files_to_process.append((fp, ed))

        job["total"] = len(files_to_process)
        _set_phase(job, "processing_files")

        updated_docs: dict[str, dict] = {**existing_docs}

        for file_path, existing_doc in files_to_process:
            job["current_file"] = file_path.name
            doc_record = await _process_file(
                patient_id, folder_slug, file_path, existing_doc, job, is_rebuild=False
            )
            updated_docs[doc_record["filename"]] = doc_record
            job["processed"] += 1

        # Rebuild patient.md from all documents.
        _set_phase(job, "rebuilding_patient_md", "patient.md")
        all_doc_records = list(updated_docs.values())
        await asyncio.to_thread(_rebuild_patient_md, patient_folder, all_doc_records)

        # Update patient.json.
        record["documents"] = all_doc_records
        patient_md_text = await asyncio.to_thread(
            docs_module.read_patient_md, patient_folder
        )

        # Regenerate timeline and summary only if files were processed.
        if files_to_process:
            _set_phase(job, "generating_timeline", "timeline")
            record["timeline"] = await tl.generate_timeline(patient_id, patient_md_text)
            _set_phase(job, "generating_summary", "summary")
            record["summary"] = await tl.generate_summary(patient_md_text)

        _set_phase(job, "saving", "patient.json")
        await pt.save_patient_record(record)
        await pt.update_ingestion_stats(patient_id, len(all_doc_records))

        _complete_job(job)

    except Exception as e:
        _fail_job(job, str(e))
        return


async def _run_rebuild(patient_id: str, job: dict) -> None:
    try:
        _set_phase(job, "loading")
        entry = await pt.find_patient_by_id(patient_id)
        if entry is None:
            _fail_job(job, "Patient not found")
            return

        folder_slug = entry["folder_slug"]
        patient_folder = Path(entry["folder_path"])
        uploads_dir = patient_folder / "uploads"

        # Clear patient.md.
        patient_md_file = patient_folder / "patient.md"
        if patient_md_file.exists():
            patient_md_file.unlink()

        # Drop and recreate _docs ChromaDB collection.
        await memory.drop_docs_collection(patient_id)

        record = await pt.load_patient_record(patient_id)
        if record is None:
            _fail_job(job, "patient.json not found")
            return

        all_files = await asyncio.to_thread(docs_module.scan_uploads, uploads_dir)
        job["total"] = len(all_files)
        _set_phase(job, "processing_files")

        new_docs: list[dict] = []
        for file_path in all_files:
            job["current_file"] = file_path.name
            doc_record = await _process_file(
                patient_id, folder_slug, file_path, None, job, is_rebuild=True
            )
            new_docs.append(doc_record)
            job["processed"] += 1

        _set_phase(job, "rebuilding_patient_md", "patient.md")
        await asyncio.to_thread(_rebuild_patient_md, patient_folder, new_docs)

        patient_md_text = await asyncio.to_thread(
            docs_module.read_patient_md, patient_folder
        )

        _set_phase(job, "generating_timeline", "timeline")
        record["documents"] = new_docs
        record["timeline"] = await tl.generate_timeline(patient_id, patient_md_text)
        _set_phase(job, "generating_summary", "summary")
        record["summary"] = await tl.generate_summary(patient_md_text)

        _set_phase(job, "saving", "patient.json")
        await pt.save_patient_record(record)
        await pt.update_ingestion_stats(patient_id, len(new_docs))

        _complete_job(job)

    except Exception as e:
        _fail_job(job, str(e))
        return

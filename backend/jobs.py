"""
Ingestion pipeline + in-memory job tracker.

Jobs run as asyncio background tasks.
The job store lives in memory — job history is lost on restart, which is acceptable
for a local single-user app (the UI polls while the process is running).
"""

import asyncio
import logging
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

_logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
# patient_id -> job_id for currently running jobs
_patient_active: dict[str, str] = {}
# patient_id -> "incremental" | "rebuild": a follow-up run requested while a job
# was already in flight. Coalesces bursts (e.g. several files dropped into
# uploads/ at once, each firing the watcher) into a single follow-up run so two
# ingestion jobs never mutate the same patient.json concurrently.
_patient_pending: dict[str, str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_job(job_id: str) -> Optional[dict]:
    return _jobs.get(job_id)


def discard_patient_jobs(patient_id: str) -> None:
    """Forget a deleted patient's in-memory job bookkeeping (pending followup,
    active-job pointer, and finished job records) so these tables don't grow
    without bound across patient create/delete cycles."""
    _patient_pending.pop(patient_id, None)
    _patient_active.pop(patient_id, None)
    stale = [jid for jid, job in _jobs.items() if job.get("patient_id") == patient_id]
    for jid in stale:
        _jobs.pop(jid, None)


def get_active_job(patient_id: str) -> Optional[dict]:
    job_id = _patient_active.get(patient_id)
    if not job_id:
        return None
    job = _jobs.get(job_id)
    if job and job["status"] == "running":
        return job
    _patient_active.pop(patient_id, None)
    return None


# Keep at most this many finished job records in memory; older ones are pruned
# so long-running sessions (e.g. many watcher-triggered ingests) don't grow the
# table without bound. Running jobs are never pruned.
_MAX_FINISHED_JOBS = 50


def _prune_finished_jobs() -> None:
    finished = [jid for jid, job in _jobs.items() if job.get("status") != "running"]
    excess = len(finished) - _MAX_FINISHED_JOBS
    if excess <= 0:
        return
    # Dict insertion order ≈ creation order, so the front of the list is oldest.
    for jid in finished[:excess]:
        _jobs.pop(jid, None)


def _create_job(patient_id: str) -> dict:
    _prune_finished_jobs()
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
        # List of {phase, elapsed_ms} for all completed phases, in order.
        "phase_history": [],
    }
    _jobs[job_id] = job
    _patient_active[patient_id] = job_id
    return job


def _set_phase(job: dict, phase: str, current_file: Optional[str] = None) -> None:
    if job.get("phase") != phase:
        # Record elapsed time for the phase that just ended.
        prev_phase = job.get("phase")
        prev_started = job.get("phase_started_at")
        if prev_phase and prev_started:
            try:
                start_dt = datetime.fromisoformat(prev_started)
                elapsed_ms = int((datetime.now(timezone.utc) - start_dt).total_seconds() * 1000)
            except ValueError:
                elapsed_ms = 0
            job["phase_history"].append({"phase": prev_phase, "elapsed_ms": elapsed_ms})
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

# Bound on concurrent embedding requests per document. Embedding each chunk is an
# independent Ollama round-trip, so we issue them in parallel instead of strictly
# one-at-a-time; the cap keeps us from overwhelming a local Ollama instance.
_EMBED_CONCURRENCY = 8


async def _embed_and_upsert_chunks(
    patient_id: str,
    doc_record: dict,
    text: str,
) -> None:
    """Chunk text, embed the chunks concurrently, upsert into the ChromaDB _docs collection."""
    cfg = load_config()
    chunks = docs_module.chunk_text(text, cfg.chunk_size, cfg.chunk_overlap)

    # Re-extraction can yield fewer chunks than before; upsert alone would leave
    # the old tail chunks (stale medical content) retrievable forever. Clear this
    # document's chunks first (no-op for new documents).
    await memory.delete_doc_chunks(patient_id, doc_record["id"])
    if not chunks:
        return

    semaphore = asyncio.Semaphore(_EMBED_CONCURRENCY)

    async def _embed_one(chunk: str) -> list[float]:
        async with semaphore:
            return await ai.embed(chunk)

    # gather preserves order, so embeddings[i] corresponds to chunks[i].
    embeddings: list[list[float]] = await asyncio.gather(
        *(_embed_one(chunk) for chunk in chunks)
    )

    chunk_ids = [f"{doc_record['id']}_chunk_{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "patient_id": patient_id,
            "document_id": doc_record["id"],
            "filename": doc_record["filename"],
            "chunk_index": i,
            "date": doc_record.get("date_detected") or "unknown",
            "document_type": doc_record.get("document_type", "unknown"),
        }
        for i in range(len(chunks))
    ]

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
    except OSError as exc:
        # File became inaccessible between the directory scan and now (e.g. a
        # transient lock or a concurrent delete). Skip it rather than failing the
        # whole batch, but surface it instead of skipping silently.
        _logger.warning("could not stat %s during change detection: %s; skipping", file_path, exc)
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

    # Stat BEFORE extraction. If the file is still being copied in while we
    # extract (the watcher fires on creation), recording the pre-extraction
    # size/mtime means the next scan sees it as changed and re-extracts —
    # stat'ing afterwards would permanently freeze the truncated extraction.
    pre_stat = await asyncio.to_thread(file_path.stat)

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
        "mtime": pre_stat.st_mtime,
        "size": pre_stat.st_size,
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

def _request_followup(patient_id: str, kind: str) -> None:
    """Record that another run is needed once the in-flight job finishes.
    A rebuild supersedes a queued incremental refresh."""
    if _patient_pending.get(patient_id) == "rebuild":
        return
    _patient_pending[patient_id] = kind


def _schedule_followup_if_pending(patient_id: str) -> None:
    """Kick off a single coalesced follow-up run, if one was requested while busy.
    Called once an ingestion job finishes (after _patient_active is cleared)."""
    kind = _patient_pending.pop(patient_id, None)
    if kind is None:
        return
    job = _create_job(patient_id)
    if kind == "rebuild":
        asyncio.create_task(_run_rebuild(patient_id, job))
    else:
        # A plain incremental rescans all uploads, so any files that arrived
        # while the previous job ran are picked up — no need to track filenames.
        asyncio.create_task(_run_incremental(patient_id, job, None))


async def start_incremental_ingestion(
    patient_id: str,
    target_filenames: Optional[list[str]] = None,
) -> dict:
    """Create job, fire background task, return job immediately.
    If a job is already running for this patient, coalesce into a single
    follow-up run instead of starting a competing job that would race on
    patient.json."""
    active = get_active_job(patient_id)
    if active is not None:
        _request_followup(patient_id, "incremental")
        return active
    job = _create_job(patient_id)
    asyncio.create_task(_run_incremental(patient_id, job, target_filenames))
    return job


async def start_full_rebuild(patient_id: str) -> dict:
    """Create job, fire background task, return job immediately.
    If a job is already running for this patient, queue the rebuild to run after
    it finishes rather than mutating patient.json concurrently."""
    active = get_active_job(patient_id)
    if active is not None:
        _request_followup(patient_id, "rebuild")
        return active
    job = _create_job(patient_id)
    asyncio.create_task(_run_rebuild(patient_id, job))
    return job


async def start_document_deletion(patient_id: str, document_id: str) -> Optional[dict]:
    """Incrementally delete one document: remove its files and vector chunks,
    rebuild patient.md from the remaining documents, and regenerate the summary —
    without re-extracting or re-embedding the rest of the record.

    Returns None if a job is already running for this patient (the caller should
    surface a conflict); the deletion targets a specific document and cannot be
    safely coalesced like a refresh."""
    if get_active_job(patient_id) is not None:
        return None
    job = _create_job(patient_id)
    asyncio.create_task(_run_document_deletion(patient_id, job, document_id))
    return job


# ---------------------------------------------------------------------------
# Background task implementations
# ---------------------------------------------------------------------------

async def _run_incremental(
    patient_id: str,
    job: dict,
    target_filenames: Optional[list[str]] = None,
) -> None:
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
        target_file_set = set(target_filenames or [])

        # Determine which files need processing.
        files_to_process = []
        for fp in all_files:
            if target_file_set and fp.name not in target_file_set:
                continue
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

        # Rebuild patient.md from all documents, under the per-patient lock so
        # a concurrent summary-overrides edit can't interleave with the rebuild.
        # Overrides are re-read fresh inside the lock so an edit made while files
        # were processing isn't reverted by this job's stale record snapshot.
        _set_phase(job, "rebuilding_patient_md", "patient.md")
        all_doc_records = list(updated_docs.values())
        async with pt.record_lock(patient_id):
            await asyncio.to_thread(_rebuild_patient_md, patient_folder, all_doc_records)
            fresh_record = await asyncio.to_thread(pt._load_patient_record_sync, folder_slug)
            overrides = (fresh_record or record).get("summary_overrides")
            await asyncio.to_thread(
                docs_module.upsert_summary_overrides_section,
                patient_folder,
                overrides,
            )

        # Update patient.json. Compute owned fields first, then persist only
        # those via mutate_patient_record so a concurrent chat write (e.g.
        # conversation_states) on the same record is not clobbered.
        patient_md_text = await asyncio.to_thread(
            docs_module.read_patient_md, patient_folder
        )

        new_summary: Optional[str] = None
        # Regenerate the summary only if files were processed.
        if files_to_process:
            _set_phase(job, "generating_summary", "summary")
            cfg = load_config()
            new_summary = await tl.generate_summary(
                patient_md_text,
                overrides,
                model=cfg.summary_model,
            )

        def _apply(r: dict) -> None:
            r["documents"] = all_doc_records
            if new_summary is not None:
                r["summary"] = new_summary

        _set_phase(job, "saving", "patient.json")
        await pt.mutate_patient_record(patient_id, _apply)
        await pt.update_ingestion_stats(patient_id, len(all_doc_records))

        _complete_job(job)

    except Exception as e:
        _fail_job(job, str(e))
    finally:
        _schedule_followup_if_pending(patient_id)


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
        async with pt.record_lock(patient_id):
            await asyncio.to_thread(_rebuild_patient_md, patient_folder, new_docs)
            fresh_record = await asyncio.to_thread(pt._load_patient_record_sync, folder_slug)
            overrides = (fresh_record or record).get("summary_overrides")
            await asyncio.to_thread(
                docs_module.upsert_summary_overrides_section,
                patient_folder,
                overrides,
            )

        patient_md_text = await asyncio.to_thread(
            docs_module.read_patient_md, patient_folder
        )

        _set_phase(job, "generating_summary", "summary")
        cfg = load_config()
        new_summary = await tl.generate_summary(
            patient_md_text,
            overrides,
            model=cfg.summary_model,
        )

        def _apply(r: dict) -> None:
            r["documents"] = new_docs
            r["summary"] = new_summary

        _set_phase(job, "saving", "patient.json")
        await pt.mutate_patient_record(patient_id, _apply)
        await pt.update_ingestion_stats(patient_id, len(new_docs))

        _complete_job(job)

    except Exception as e:
        _fail_job(job, str(e))
    finally:
        _schedule_followup_if_pending(patient_id)


async def _run_document_deletion(patient_id: str, job: dict, document_id: str) -> None:
    try:
        _set_phase(job, "loading")
        entry = await pt.find_patient_by_id(patient_id)
        if entry is None:
            _fail_job(job, "Patient not found")
            return

        record = await pt.load_patient_record(patient_id)
        if record is None:
            _fail_job(job, "patient.json not found")
            return

        doc = next(
            (d for d in record.get("documents", []) if d.get("id") == document_id), None
        )
        if doc is None:
            _fail_job(job, "Document not found")
            return

        patient_folder = Path(entry["folder_path"])
        uploads_dir = patient_folder / "uploads"
        job["total"] = 1

        # 1. Delete the source file and its cached extraction sidecar.
        filename = doc.get("filename")
        _set_phase(job, "deleting_files", filename)
        if isinstance(filename, str) and filename:
            (uploads_dir / filename).unlink(missing_ok=True)
            (uploads_dir / f"{filename}.extracted").unlink(missing_ok=True)

        # 2. Drop only this document's chunks from the vector store.
        _set_phase(job, "removing_chunks")
        await memory.delete_doc_chunks(patient_id, document_id)

        # 3. Rebuild patient.md from the remaining documents (sidecar reads only —
        #    no re-extraction or re-embedding).
        remaining = [d for d in record.get("documents", []) if d.get("id") != document_id]
        _set_phase(job, "rebuilding_patient_md", "patient.md")
        async with pt.record_lock(patient_id):
            await asyncio.to_thread(_rebuild_patient_md, patient_folder, remaining)
            fresh_record = await asyncio.to_thread(
                pt._load_patient_record_sync, entry["folder_slug"]
            )
            overrides = (fresh_record or record).get("summary_overrides")
            await asyncio.to_thread(
                docs_module.upsert_summary_overrides_section,
                patient_folder,
                overrides,
            )

        # 4. Regenerate the summary so it no longer reflects the removed document.
        new_summary = ""
        if remaining:
            patient_md_text = await asyncio.to_thread(
                docs_module.read_patient_md, patient_folder
            )
            _set_phase(job, "generating_summary", "summary")
            cfg = load_config()
            new_summary = await tl.generate_summary(
                patient_md_text,
                overrides,
                model=cfg.summary_model,
            )

        def _apply(r: dict) -> None:
            r["documents"] = remaining
            r["summary"] = new_summary

        _set_phase(job, "saving", "patient.json")
        await pt.mutate_patient_record(patient_id, _apply)
        await pt.update_ingestion_stats(patient_id, len(remaining))
        job["processed"] = 1

        _complete_job(job)

    except Exception as e:
        _fail_job(job, str(e))
    finally:
        _schedule_followup_if_pending(patient_id)

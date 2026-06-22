"""
Ingestion pipeline + in-memory job tracker.

Jobs run as asyncio background tasks.
The job store lives in memory — job history is lost on restart, which is acceptable
for a local single-user app (the UI polls while the process is running).
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ai
import documents as docs_module
import extraction
import llm
import memory
import patients as pt
import prompts
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

# Strong references to in-flight background tasks. asyncio.create_task only keeps
# a WEAK reference to the task, so a fire-and-forget task whose return value is
# discarded can be garbage-collected mid-run — silently aborting an ingestion and
# leaving the job stuck "running" forever. Holding the reference here (and clearing
# it on completion) prevents that.
_background_tasks: set = set()


def _spawn(coro) -> "asyncio.Task":
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


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


def _create_job(patient_id: str, operation: str = "ingestion") -> dict:
    _prune_finished_jobs()
    job_id = str(uuid.uuid4())
    started_at = _now()
    job = {
        "job_id": job_id,
        "patient_id": patient_id,
        # What kind of work this job does — drives how the UI presents it
        # ("ingestion" / "rebuild" show full progress; "deletion" shows a slim chip).
        "operation": operation,
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
            # The per-file sub-phases (extracting_text/extracting_facts/embedding)
            # repeat once per document. Accumulate into the existing entry for this
            # phase rather than appending a row per file, so the breakdown reads as a
            # clean pipeline (one line per stage) while still totaling its real cost.
            # First-seen order is preserved, which matches the pipeline order.
            history = job["phase_history"]
            existing = next((e for e in history if e["phase"] == prev_phase), None)
            if existing is not None:
                existing["elapsed_ms"] += elapsed_ms
            else:
                history.append({"phase": prev_phase, "elapsed_ms": elapsed_ms})
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
    """Chunk text, embed all chunks in one batched call, upsert into the ChromaDB _docs collection."""
    cfg = load_config()
    chunks = docs_module.chunk_text(text, cfg.chunk_size, cfg.chunk_overlap)

    # Re-extraction can yield fewer chunks than before; upsert alone would leave
    # the old tail chunks (stale medical content) retrievable forever. Clear this
    # document's chunks first (no-op for new documents).
    await memory.delete_doc_chunks(patient_id, doc_record["id"])
    if not chunks:
        return

    # Prepend a provenance header to each chunk so the vector itself encodes the
    # source and date (not just the Chroma metadata), and so a retrieved chunk
    # carries its origin into the chat context. The header is stored too — it's the
    # text the model sees as context.
    header = (
        f"[{doc_record['filename']} · "
        f"{doc_record.get('document_type', 'unknown')} · "
        f"{doc_record.get('date_detected') or 'unknown'}]"
    )
    tagged_chunks = [f"{header}\n{chunk}" for chunk in chunks]

    # embed_many returns vectors in input order, so embeddings[i] matches chunks[i].
    # These are stored content, so embed them with the "document" retrieval prefix.
    embeddings: list[list[float]] = await ai.embed_many(tagged_chunks, task="document")

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
        chunks=tagged_chunks,
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


# Slack added on top of the model HTTP timeout when waiting on a threaded->async
# call. The HTTP request itself times out at chat_timeout_seconds, so the bridge
# must wait a little longer — otherwise it abandons the call early with a bare
# TimeoutError (and leaves the request running, orphaned, on the event loop).
_BRIDGE_TIMEOUT_SLACK_SECONDS = 30


async def _make_llm_json_fn():
    """Return a sync callable that uses the event loop to run LLM JSON extraction."""
    loop = asyncio.get_event_loop()
    timeout = load_config().chat_timeout_seconds + _BRIDGE_TIMEOUT_SLACK_SECONDS

    def llm_json_fn(json_content: str) -> str:
        future = asyncio.run_coroutine_threadsafe(
            llm.extract_json_document(json_content), loop
        )
        return future.result(timeout=timeout)

    return llm_json_fn


async def _make_vision_fn():
    """Return a sync callable that transcribes an image (PNG/JPEG bytes) to text via
    the multimodal model, bridging the threaded extraction code back to the event loop."""
    loop = asyncio.get_event_loop()
    cfg = load_config()
    timeout = cfg.chat_timeout_seconds + _BRIDGE_TIMEOUT_SLACK_SECONDS

    def vision_fn(image_bytes: bytes) -> str:
        future = asyncio.run_coroutine_threadsafe(
            ai.chat_vision(
                prompts.VISION_TRANSCRIPTION_PROMPT, [image_bytes], model=cfg.vision_model
            ),
            loop,
        )
        return future.result(timeout=timeout)

    return vision_fn


async def _load_or_extract_facts(
    text: str,
    facts_path: Path,
    regenerate: bool,
    patient_dob: Optional[str],
) -> dict:
    """Return structured clinical facts for a document, reusing the .facts.json
    sidecar unless regeneration is forced (i.e. the text was just re-extracted)."""
    if not regenerate and facts_path.exists():
        try:
            raw = await asyncio.to_thread(facts_path.read_text, encoding="utf-8")
            return json.loads(raw)
        except (OSError, json.JSONDecodeError):
            _logger.warning("could not read facts sidecar %s; re-extracting", facts_path)
    facts = await extraction.extract_facts(text, patient_dob)
    await asyncio.to_thread(
        facts_path.write_text, json.dumps(facts, indent=2), encoding="utf-8"
    )
    return facts


async def _process_file(
    patient_id: str,
    file_path: Path,
    existing_doc: Optional[dict],
    is_rebuild: bool,
    job: dict,
    patient_dob: Optional[str] = None,
) -> dict:
    """
    Extract text + structured facts, embed, and return an updated document record.
    Overwrites the .extracted / .facts.json sidecars if the file changed or is being rebuilt.

    Drives the job's sub-phase as it goes (reading → extracting facts → embedding)
    so the progress UI shows where time is actually spent, not one opaque block.
    """
    extracted_path = file_path.with_suffix(file_path.suffix + ".extracted")
    facts_path = file_path.with_suffix(file_path.suffix + ".facts.json")
    llm_json_fn = await _make_llm_json_fn()
    vision_fn = await _make_vision_fn()

    # Stat BEFORE extraction. If the file is still being copied in while we
    # extract (the watcher fires on creation), recording the pre-extraction
    # size/mtime means the next scan sees it as changed and re-extracts —
    # stat'ing afterwards would permanently freeze the truncated extraction.
    pre_stat = await asyncio.to_thread(file_path.stat)

    # Determine if we need to re-extract; the same decision gates fact regeneration.
    needs_reextract = is_rebuild or bool(existing_doc and _file_changed(file_path, existing_doc))
    _set_phase(job, "extracting_text", file_path.name)
    if needs_reextract:
        text = await asyncio.to_thread(
            docs_module.overwrite_extracted_sidecar,
            file_path,
            extracted_path,
            llm_json_fn,
            vision_fn,
        )
    else:
        text = await asyncio.to_thread(
            docs_module.extract_text_sync,
            file_path,
            extracted_path,
            llm_json_fn,
            vision_fn,
        )

    _set_phase(job, "extracting_facts", file_path.name)
    facts = await _load_or_extract_facts(
        text, facts_path, regenerate=needs_reextract, patient_dob=patient_dob
    )

    doc_type = docs_module.detect_document_type(file_path.name, text)
    # Prefer the extractor's clinically-typed document_date (which is told not to
    # use the patient's DOB) over the regex date scan; the scan is now also given
    # the DOB so its fallback can't latch onto it either.
    date_detected = facts.get("document_date") or docs_module.detect_date(text, patient_dob)

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
        "status": "ok",
        # Clear any error from a prior failed attempt now that this succeeded.
        "error": None,
        "facts": facts,
    })

    _set_phase(job, "embedding", file_path.name)
    await _embed_and_upsert_chunks(patient_id, doc_record, text)
    return doc_record


def _errored_doc_record(
    patient_id: str, existing_doc: Optional[dict], file_path: Path, error: str
) -> dict:
    """Build a placeholder record for a file whose ingestion failed, so the file
    stays visible in the UI (instead of silently vanishing) with its error.

    Deliberately omits mtime/size: leaving them unset makes the next scan treat
    the file as changed and re-attempt it, so a transient failure (e.g. the model
    was briefly unavailable) self-heals on the next ingest."""
    record = existing_doc.copy() if existing_doc else {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
    }
    record.update({
        "filename": file_path.name,
        "file_path": f"./uploads/{file_path.name}",
        "date_detected": record.get("date_detected", "unknown"),
        "document_type": record.get("document_type", "unknown"),
        "ingested_at": _now(),
        "status": "error",
        "error": error[:500],
    })
    record.pop("mtime", None)
    record.pop("size", None)
    return record


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
    job = _create_job(patient_id, "rebuild" if kind == "rebuild" else "ingestion")
    if kind == "rebuild":
        _spawn(_run_rebuild(patient_id, job))
    else:
        # A plain incremental rescans all uploads, so any files that arrived
        # while the previous job ran are picked up — no need to track filenames.
        _spawn(_run_incremental(patient_id, job, None))


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
    job = _create_job(patient_id, "ingestion")
    _spawn(_run_incremental(patient_id, job, target_filenames))
    return job


async def start_full_rebuild(patient_id: str) -> dict:
    """Create job, fire background task, return job immediately.
    If a job is already running for this patient, queue the rebuild to run after
    it finishes rather than mutating patient.json concurrently."""
    active = get_active_job(patient_id)
    if active is not None:
        _request_followup(patient_id, "rebuild")
        return active
    job = _create_job(patient_id, "rebuild")
    _spawn(_run_rebuild(patient_id, job))
    return job


async def start_document_deletion(patient_id: str, document_id: str) -> Optional[dict]:
    """Incrementally delete one document: remove its files and vector chunks and
    rebuild patient.md from the remaining documents — without re-extracting or
    re-embedding the rest of the record.

    Returns None if a job is already running for this patient (the caller should
    surface a conflict); the deletion targets a specific document and cannot be
    safely coalesced like a refresh."""
    if get_active_job(patient_id) is not None:
        return None
    job = _create_job(patient_id, "deletion")
    _spawn(_run_document_deletion(patient_id, job, document_id))
    return job


# ---------------------------------------------------------------------------
# Background task implementations
# ---------------------------------------------------------------------------

async def _persist_documents(
    patient_id: str, patient_folder: Path, job: dict, all_docs: list[dict]
) -> None:
    """Persist document records (including any errored placeholders) to patient.json
    and rebuild patient.md from only the successfully-processed documents. Called
    even when some files failed, so successful work is never discarded."""
    good_docs = [d for d in all_docs if d.get("status") != "error"]

    _set_phase(job, "writing_export", "patient.md")
    async with pt.record_lock(patient_id):
        await asyncio.to_thread(_rebuild_patient_md, patient_folder, good_docs)

    # Persist only owned fields via mutate_patient_record so a concurrent chat write
    # (e.g. session conversation_state updates) on the same record is not clobbered.
    def _apply(r: dict) -> None:
        r["documents"] = all_docs

    _set_phase(job, "saving", "patient.json")
    await pt.mutate_patient_record(patient_id, _apply)
    await pt.update_ingestion_stats(patient_id, len(good_docs))


def _finalize_job(job: dict, failed_files: list[str]) -> None:
    """Mark the job complete, or failed if any file could not be processed. Either
    way the successful documents have already been persisted by _persist_documents,
    and each failed file is recorded as an errored document the user can see."""
    if failed_files:
        _fail_job(
            job,
            f"{len(failed_files)} file(s) could not be processed: {', '.join(failed_files)}",
        )
    else:
        _complete_job(job)


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
        patient_dob = (record.get("profile") or {}).get("dob")

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

        updated_docs: dict[str, dict] = {**existing_docs}
        failed_files: list[str] = []

        for file_path, existing_doc in files_to_process:
            job["current_file"] = file_path.name
            # Process each file independently: one bad file must not discard the
            # whole batch (and with it every already-processed file). Record the
            # failure as an errored document so it stays visible and retries later.
            try:
                doc_record = await _process_file(
                    patient_id, file_path, existing_doc, is_rebuild=False,
                    job=job, patient_dob=patient_dob
                )
            except Exception as exc:
                _logger.exception(
                    "ingestion failed for file %s patient_id=%s", file_path.name, patient_id
                )
                failed_files.append(file_path.name)
                doc_record = _errored_doc_record(patient_id, existing_doc, file_path, str(exc))
            updated_docs[doc_record["filename"]] = doc_record
            job["processed"] += 1

        await _persist_documents(patient_id, patient_folder, job, list(updated_docs.values()))
        _finalize_job(job, failed_files)

    except asyncio.CancelledError:
        # CancelledError is BaseException, not Exception, so it would otherwise
        # skip _fail_job and leave _patient_active pointing at this job forever —
        # permanently 409-blocking deletes and coalescing all future ingests into
        # a follow-up that never fires. Mark it failed, then re-raise to honor
        # the cancellation.
        _fail_job(job, "cancelled")
        raise
    except Exception as e:
        _logger.exception("ingestion job failed patient_id=%s job_id=%s", patient_id, job["job_id"])
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
        patient_dob = (record.get("profile") or {}).get("dob")

        all_files = await asyncio.to_thread(docs_module.scan_uploads, uploads_dir)
        job["total"] = len(all_files)

        new_docs: list[dict] = []
        failed_files: list[str] = []
        for file_path in all_files:
            job["current_file"] = file_path.name
            try:
                doc_record = await _process_file(
                    patient_id, file_path, None, is_rebuild=True,
                    job=job, patient_dob=patient_dob
                )
            except Exception as exc:
                _logger.exception(
                    "ingestion failed for file %s patient_id=%s", file_path.name, patient_id
                )
                failed_files.append(file_path.name)
                doc_record = _errored_doc_record(patient_id, None, file_path, str(exc))
            new_docs.append(doc_record)
            job["processed"] += 1

        await _persist_documents(patient_id, patient_folder, job, new_docs)
        _finalize_job(job, failed_files)

    except asyncio.CancelledError:
        # CancelledError is BaseException, not Exception, so it would otherwise
        # skip _fail_job and leave _patient_active pointing at this job forever —
        # permanently 409-blocking deletes and coalescing all future ingests into
        # a follow-up that never fires. Mark it failed, then re-raise to honor
        # the cancellation.
        _fail_job(job, "cancelled")
        raise
    except Exception as e:
        _logger.exception("ingestion job failed patient_id=%s job_id=%s", patient_id, job["job_id"])
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

        # 1. Delete the source file and its cached extraction + facts sidecars.
        filename = doc.get("filename")
        _set_phase(job, "deleting_files", filename)
        if isinstance(filename, str) and filename:
            (uploads_dir / filename).unlink(missing_ok=True)
            (uploads_dir / f"{filename}.extracted").unlink(missing_ok=True)
            (uploads_dir / f"{filename}.facts.json").unlink(missing_ok=True)

        # 2. Drop only this document's chunks from the vector store.
        _set_phase(job, "removing_chunks")
        await memory.delete_doc_chunks(patient_id, document_id)

        # 3. Rebuild patient.md from the remaining documents (sidecar reads only —
        #    no re-extraction or re-embedding).
        remaining = [d for d in record.get("documents", []) if d.get("id") != document_id]
        _set_phase(job, "writing_export", "patient.md")
        async with pt.record_lock(patient_id):
            await asyncio.to_thread(_rebuild_patient_md, patient_folder, remaining)

        def _apply(r: dict) -> None:
            r["documents"] = remaining

        _set_phase(job, "saving", "patient.json")
        await pt.mutate_patient_record(patient_id, _apply)
        await pt.update_ingestion_stats(patient_id, len(remaining))
        job["processed"] = 1

        _complete_job(job)

    except asyncio.CancelledError:
        # CancelledError is BaseException, not Exception, so it would otherwise
        # skip _fail_job and leave _patient_active pointing at this job forever —
        # permanently 409-blocking deletes and coalescing all future ingests into
        # a follow-up that never fires. Mark it failed, then re-raise to honor
        # the cancellation.
        _fail_job(job, "cancelled")
        raise
    except Exception as e:
        _logger.exception("ingestion job failed patient_id=%s job_id=%s", patient_id, job["job_id"])
        _fail_job(job, str(e))
    finally:
        _schedule_followup_if_pending(patient_id)

"""
Persistence layer for all JSON-backed data:
  - patients.json        thin patient index
  - patients/{slug}/patient.json   per-patient record
  - patients/{slug}/chats/{session_id}.json  message log
"""

import asyncio
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from config import DATA_PATH

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PATIENTS_INDEX_FILE = DATA_PATH / "patients.json"
PATIENTS_DIR = DATA_PATH / "patients"

# Asyncio lock serialises writes to patients.json.
_index_lock = asyncio.Lock()

# Per-patient locks serialise read-modify-write cycles on patient.json and that
# patient's chat logs, so concurrent writers (e.g. an ingestion job and a chat
# request) cannot clobber each other's updates. Created lazily; creation is safe
# without its own lock because it runs synchronously on the single event loop.
_record_locks: dict[str, asyncio.Lock] = {}


def _record_lock(patient_id: str) -> asyncio.Lock:
    lock = _record_locks.get(patient_id)
    if lock is None:
        lock = asyncio.Lock()
        _record_locks[patient_id] = lock
    return lock


def record_lock(patient_id: str) -> asyncio.Lock:
    """Public accessor for the per-patient lock — also used by callers that
    write patient.md, so file rebuilds and override edits serialise with each
    other and with record mutations."""
    return _record_lock(patient_id)


def discard_record_lock(patient_id: str) -> None:
    """Drop a deleted patient's lock so the table doesn't grow without bound.
    Safe even if the lock is momentarily held: an in-flight holder keeps its own
    reference, and a deleted patient has no further operations to serialise."""
    _record_locks.pop(patient_id, None)


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON via a unique temp file + atomic rename so readers never observe
    a partially-written file (removes the need to lock reads). A unique temp name
    means concurrent writers to the same target can't collide on the temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        # Don't leave a stray temp file behind if writing/rename failed.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _patient_dir(folder_slug: str) -> Path:
    return PATIENTS_DIR / folder_slug


def _patient_record_file(folder_slug: str) -> Path:
    return _patient_dir(folder_slug) / "patient.json"


def _chat_log_file(folder_slug: str, session_id: str) -> Path:
    return _patient_dir(folder_slug) / "chats" / f"{session_id}.json"


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-") or "patient"


def unique_slug(name: str) -> str:
    base = slugify(name)
    slug = base
    counter = 2
    while (PATIENTS_DIR / slug).exists():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


# ---------------------------------------------------------------------------
# Patient index (patients.json)
# ---------------------------------------------------------------------------

def _load_index_sync() -> list[dict]:
    if PATIENTS_INDEX_FILE.exists():
        with open(PATIENTS_INDEX_FILE) as f:
            return json.load(f)
    return []


def _save_index_sync(index: list[dict]) -> None:
    _atomic_write_json(PATIENTS_INDEX_FILE, index)


async def load_patients_index() -> list[dict]:
    async with _index_lock:
        return await asyncio.to_thread(_load_index_sync)


async def save_patients_index(index: list[dict]) -> None:
    async with _index_lock:
        await asyncio.to_thread(_save_index_sync, index)


async def mutate_patients_index(mutator: Callable[[list[dict]], Any]) -> Any:
    """Atomically load → mutate → save patients.json. Holding the lock across the
    whole read-modify-write cycle prevents two concurrent writers (e.g. a rename
    and a background job's ingestion-stats update) from losing each other's
    changes. The mutator mutates the index list in place; its return value is
    passed through."""
    async with _index_lock:
        index = await asyncio.to_thread(_load_index_sync)
        result = mutator(index)
        await asyncio.to_thread(_save_index_sync, index)
        return result


async def find_patient_by_id(patient_id: str) -> Optional[dict]:
    index = await load_patients_index()
    return next((p for p in index if p["id"] == patient_id), None)


async def find_patient_by_slug(folder_slug: str) -> Optional[dict]:
    index = await load_patients_index()
    return next((p for p in index if p["folder_slug"] == folder_slug), None)


def _thin_shape(entry: dict) -> dict:
    return {
        "id": entry["id"],
        "name": entry["name"],
        "folder_slug": entry["folder_slug"],
        "folder_path": entry["folder_path"],
        "document_count": entry.get("document_count", 0),
        "last_ingested_at": entry.get("last_ingested_at"),
        "created_at": entry["created_at"],
    }


async def create_patient(name: str) -> dict:
    slug = unique_slug(name)
    patient_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    folder_path = str(_patient_dir(slug))

    # Create directories.
    uploads_dir = _patient_dir(slug) / "uploads"
    chats_dir = _patient_dir(slug) / "chats"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    chats_dir.mkdir(parents=True, exist_ok=True)

    # Initial patient.json record.
    record: dict[str, Any] = {
        "id": patient_id,
        "name": name,
        "folder_slug": slug,
        "folder_path": folder_path,
        "last_ingested_at": None,
        "document_count": 0,
        "documents": [],
        "summary": "",
        "chat_sessions": [],
        "conversation_states": {},
        "summary_overrides": {
            "active_conditions": "",
            "current_medications": "",
            "recent_procedures": "",
            "key_concerns": "",
        },
        "memory_results_override": None,
        "context_window_tokens_override": None,
    }
    await asyncio.to_thread(_save_patient_record_sync, slug, record)

    # Index entry.
    entry = {
        "id": patient_id,
        "name": name,
        "folder_slug": slug,
        "folder_path": folder_path,
        "document_count": 0,
        "last_ingested_at": None,
        "created_at": now,
    }
    await mutate_patients_index(lambda index: index.append(entry))

    return _thin_shape(entry)


async def patch_patient(patient_id: str, updates: dict) -> Optional[dict]:
    # Apply the name update to the index atomically under the index lock.
    matched: list[dict] = []

    def _apply_index(index: list[dict]) -> None:
        entry = next((p for p in index if p["id"] == patient_id), None)
        if entry is None:
            return
        if "name" in updates:
            entry["name"] = updates["name"]
        matched.append(entry)

    await mutate_patients_index(_apply_index)
    if not matched:
        return None
    entry = matched[0]

    # Apply overrides (and name) to patient.json.
    override_fields = {"memory_results_override", "context_window_tokens_override"}
    record_updates = {k: v for k, v in updates.items() if k in override_fields}
    if "name" in updates:
        record_updates["name"] = updates["name"]
    record: Optional[dict] = None
    if record_updates:
        record = await mutate_patient_record(patient_id, lambda r: r.update(record_updates))

    # Include the saved override values so the client sees the applied state.
    shape = _thin_shape(entry)
    if record is not None:
        shape["memory_results_override"] = record.get("memory_results_override")
        shape["context_window_tokens_override"] = record.get("context_window_tokens_override")
    return shape


async def delete_patient_index_entry(patient_id: str) -> Optional[dict]:
    removed: list[dict] = []

    def _apply(index: list[dict]) -> None:
        entry = next((p for p in index if p["id"] == patient_id), None)
        if entry is not None:
            removed.append(entry)
            index.remove(entry)

    await mutate_patients_index(_apply)
    return removed[0] if removed else None


# ---------------------------------------------------------------------------
# Patient record (patient.json)
# ---------------------------------------------------------------------------

def _load_patient_record_sync(folder_slug: str) -> Optional[dict]:
    f = _patient_record_file(folder_slug)
    if not f.exists():
        return None
    with open(f) as fh:
        return json.load(fh)


def _save_patient_record_sync(folder_slug: str, record: dict) -> None:
    _atomic_write_json(_patient_record_file(folder_slug), record)


async def load_patient_record(patient_id: str) -> Optional[dict]:
    entry = await find_patient_by_id(patient_id)
    if entry is None:
        return None
    return await asyncio.to_thread(_load_patient_record_sync, entry["folder_slug"])


async def save_patient_record(record: dict) -> None:
    entry = await find_patient_by_id(record["id"])
    if entry is None:
        return
    async with _record_lock(record["id"]):
        await asyncio.to_thread(_save_patient_record_sync, entry["folder_slug"], record)


async def mutate_patient_record(
    patient_id: str, mutator: Callable[[dict], Any]
) -> Optional[dict]:
    """Atomically load → mutate → save a patient record under its per-patient lock.

    `mutator` receives the freshly-loaded record and mutates it in place. It must
    be fast and synchronous (field assignments only) — the lock is held for its
    duration. Do any slow work (LLM calls, embeddings) BEFORE calling this and
    apply only the resulting fields inside the mutator, so that writers touching
    other fields concurrently are not clobbered.

    Returns the saved record, or None if the patient no longer exists.
    """
    entry = await find_patient_by_id(patient_id)
    if entry is None:
        return None
    slug = entry["folder_slug"]
    async with _record_lock(patient_id):
        record = await asyncio.to_thread(_load_patient_record_sync, slug)
        if record is None:
            return None
        mutator(record)
        await asyncio.to_thread(_save_patient_record_sync, slug, record)
        return record


async def update_ingestion_stats(patient_id: str, document_count: int) -> None:
    """Denormalize document_count and last_ingested_at into patients.json index."""
    now = datetime.now(timezone.utc).isoformat()

    def _apply_index(index: list[dict]) -> None:
        for entry in index:
            if entry["id"] == patient_id:
                entry["document_count"] = document_count
                entry["last_ingested_at"] = now
                break

    await mutate_patients_index(_apply_index)

    def _apply(record: dict) -> None:
        record["document_count"] = document_count
        record["last_ingested_at"] = now

    await mutate_patient_record(patient_id, _apply)


# ---------------------------------------------------------------------------
# Chat message log (chats/{session_id}.json)
# ---------------------------------------------------------------------------

def _load_message_log_sync(folder_slug: str, session_id: str) -> dict:
    f = _chat_log_file(folder_slug, session_id)
    if f.exists():
        with open(f) as fh:
            return json.load(fh)
    return {"session_id": session_id, "messages": []}


def _save_message_log_sync(folder_slug: str, session_id: str, log: dict) -> None:
    _atomic_write_json(_chat_log_file(folder_slug, session_id), log)


def _delete_message_log_sync(folder_slug: str, session_id: str) -> None:
    f = _chat_log_file(folder_slug, session_id)
    if f.exists():
        f.unlink()


async def load_message_log(patient_id: str, session_id: str) -> Optional[dict]:
    entry = await find_patient_by_id(patient_id)
    if entry is None:
        return None
    return await asyncio.to_thread(
        _load_message_log_sync, entry["folder_slug"], session_id
    )


async def save_message_log(patient_id: str, session_id: str, log: dict) -> None:
    entry = await find_patient_by_id(patient_id)
    if entry is None:
        return
    async with _record_lock(patient_id):
        await asyncio.to_thread(
            _save_message_log_sync, entry["folder_slug"], session_id, log
        )


async def append_message(patient_id: str, session_id: str, message: dict) -> None:
    entry = await find_patient_by_id(patient_id)
    if entry is None:
        return
    async with _record_lock(patient_id):
        log = await asyncio.to_thread(
            _load_message_log_sync, entry["folder_slug"], session_id
        )
        log["messages"].append(message)
        await asyncio.to_thread(
            _save_message_log_sync, entry["folder_slug"], session_id, log
        )


async def delete_message_log(patient_id: str, session_id: str) -> None:
    entry = await find_patient_by_id(patient_id)
    if entry is None:
        return
    await asyncio.to_thread(
        _delete_message_log_sync, entry["folder_slug"], session_id
    )


# ---------------------------------------------------------------------------
# Chat session helpers (stored in patient.json)
# ---------------------------------------------------------------------------

async def add_chat_session(patient_id: str, session: dict) -> None:
    await mutate_patient_record(
        patient_id, lambda r: r.setdefault("chat_sessions", []).append(session)
    )


async def update_chat_session(patient_id: str, session_id: str, updates: dict) -> Optional[dict]:
    matched: list[dict] = []

    def _apply(record: dict) -> None:
        for s in record.get("chat_sessions", []):
            if s["id"] == session_id:
                s.update(updates)
                matched.append(s)
                break

    record = await mutate_patient_record(patient_id, _apply)
    if record is None:
        return None
    return matched[0] if matched else None


async def delete_chat_session(patient_id: str, session_id: str) -> bool:
    deleted = False

    def _apply(record: dict) -> None:
        nonlocal deleted
        sessions = record.get("chat_sessions", [])
        before = len(sessions)
        record["chat_sessions"] = [s for s in sessions if s["id"] != session_id]
        record.get("conversation_states", {}).pop(session_id, None)
        deleted = len(record["chat_sessions"]) < before

    record = await mutate_patient_record(patient_id, _apply)
    return bool(record is not None and deleted)


# ---------------------------------------------------------------------------
# Conversation state helpers
# ---------------------------------------------------------------------------

async def load_conversation_state(patient_id: str, session_id: str) -> Optional[dict]:
    record = await load_patient_record(patient_id)
    if record is None:
        return None
    return record.get("conversation_states", {}).get(session_id)


async def save_conversation_state(patient_id: str, session_id: str, state: dict) -> None:
    await mutate_patient_record(
        patient_id,
        lambda r: r.setdefault("conversation_states", {}).__setitem__(session_id, state),
    )


# ---------------------------------------------------------------------------
# Upload file helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    """Remove path separators and dangerous characters from an uploaded filename."""
    name = Path(name).name  # strip any directory components
    name = re.sub(r"[^\w\-_\. ]", "", name)
    name = name.strip(". ")
    return name or "unnamed_file"


def safe_upload_path(uploads_dir: Path, filename: str) -> Path:
    """Return a safe absolute path inside uploads_dir, raising on traversal attempt."""
    base = uploads_dir.resolve()
    target = (base / filename).resolve()
    # Component-wise containment check — unlike a string prefix test, this is not
    # fooled by a sibling directory that shares a prefix (e.g. ".../uploads-evil").
    if not target.is_relative_to(base):
        raise ValueError("Path traversal detected in filename")
    return target

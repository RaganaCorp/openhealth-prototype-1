"""
Persistence layer for all JSON-backed data:
  - patients.json        thin patient index
  - patients/{slug}/patient.json   per-patient record
  - patients/{slug}/chats/{session_id}.json  message log
"""

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import DATA_PATH

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PATIENTS_INDEX_FILE = DATA_PATH / "patients.json"
PATIENTS_DIR = DATA_PATH / "patients"

# Asyncio lock serialises writes to patients.json.
_index_lock = asyncio.Lock()


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
    PATIENTS_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PATIENTS_INDEX_FILE, "w") as f:
        json.dump(index, f, indent=2)


async def load_patients_index() -> list[dict]:
    async with _index_lock:
        return await asyncio.to_thread(_load_index_sync)


async def save_patients_index(index: list[dict]) -> None:
    async with _index_lock:
        await asyncio.to_thread(_save_index_sync, index)


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
        "timeline": [],
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
    index = await load_patients_index()
    index.append(entry)
    await save_patients_index(index)

    return _thin_shape(entry)


async def patch_patient(patient_id: str, updates: dict) -> Optional[dict]:
    index = await load_patients_index()
    entry = next((p for p in index if p["id"] == patient_id), None)
    if entry is None:
        return None

    # Apply name update to index.
    if "name" in updates:
        entry["name"] = updates["name"]
        await save_patients_index(index)

    # Apply overrides to patient.json.
    override_fields = {"memory_results_override", "context_window_tokens_override"}
    record_updates = {k: v for k, v in updates.items() if k in override_fields}
    if "name" in updates:
        record_updates["name"] = updates["name"]
    if record_updates:
        record = await load_patient_record(patient_id)
        if record:
            record.update(record_updates)
            await asyncio.to_thread(_save_patient_record_sync, entry["folder_slug"], record)

    return _thin_shape(entry)


async def delete_patient_index_entry(patient_id: str) -> Optional[dict]:
    index = await load_patients_index()
    entry = next((p for p in index if p["id"] == patient_id), None)
    if entry is None:
        return None
    await save_patients_index([p for p in index if p["id"] != patient_id])
    return entry


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
    f = _patient_record_file(folder_slug)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w") as fh:
        json.dump(record, fh, indent=2)


async def load_patient_record(patient_id: str) -> Optional[dict]:
    entry = await find_patient_by_id(patient_id)
    if entry is None:
        return None
    return await asyncio.to_thread(_load_patient_record_sync, entry["folder_slug"])


async def save_patient_record(record: dict) -> None:
    entry = await find_patient_by_id(record["id"])
    if entry is None:
        return
    await asyncio.to_thread(_save_patient_record_sync, entry["folder_slug"], record)


async def update_ingestion_stats(patient_id: str, document_count: int) -> None:
    """Denormalize document_count and last_ingested_at into patients.json index."""
    now = datetime.now(timezone.utc).isoformat()
    index = await load_patients_index()
    for entry in index:
        if entry["id"] == patient_id:
            entry["document_count"] = document_count
            entry["last_ingested_at"] = now
            break
    await save_patients_index(index)

    record = await load_patient_record(patient_id)
    if record:
        record["document_count"] = document_count
        record["last_ingested_at"] = now
        await save_patient_record(record)


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
    f = _chat_log_file(folder_slug, session_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w") as fh:
        json.dump(log, fh, indent=2)


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
    await asyncio.to_thread(
        _save_message_log_sync, entry["folder_slug"], session_id, log
    )


async def append_message(patient_id: str, session_id: str, message: dict) -> None:
    entry = await find_patient_by_id(patient_id)
    if entry is None:
        return
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
    record = await load_patient_record(patient_id)
    if record is None:
        return
    record["chat_sessions"].append(session)
    await save_patient_record(record)


async def update_chat_session(patient_id: str, session_id: str, updates: dict) -> Optional[dict]:
    record = await load_patient_record(patient_id)
    if record is None:
        return None
    for s in record["chat_sessions"]:
        if s["id"] == session_id:
            s.update(updates)
            await save_patient_record(record)
            return s
    return None


async def delete_chat_session(patient_id: str, session_id: str) -> bool:
    record = await load_patient_record(patient_id)
    if record is None:
        return False
    before = len(record["chat_sessions"])
    record["chat_sessions"] = [
        s for s in record["chat_sessions"] if s["id"] != session_id
    ]
    record["conversation_states"].pop(session_id, None)
    if len(record["chat_sessions"]) < before:
        await save_patient_record(record)
        return True
    return False


# ---------------------------------------------------------------------------
# Conversation state helpers
# ---------------------------------------------------------------------------

async def load_conversation_state(patient_id: str, session_id: str) -> Optional[dict]:
    record = await load_patient_record(patient_id)
    if record is None:
        return None
    return record.get("conversation_states", {}).get(session_id)


async def save_conversation_state(patient_id: str, session_id: str, state: dict) -> None:
    record = await load_patient_record(patient_id)
    if record is None:
        return
    record.setdefault("conversation_states", {})[session_id] = state
    await save_patient_record(record)


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
    target = (uploads_dir / filename).resolve()
    if not str(target).startswith(str(uploads_dir.resolve())):
        raise ValueError("Path traversal detected in filename")
    return target

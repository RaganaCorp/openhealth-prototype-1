"""
FastAPI application — all routes.
"""

import asyncio
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

import ai
import jobs
import memory
import patients as pt
import timeline as tl
import watcher
from config import LOG_PAYLOADS, load_config, patch_config, save_config, Config


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await ai.ensure_ollama_available()
    await watcher.start_watcher()
    yield
    watcher.stop_watcher()


app = FastAPI(title="OpenHealth API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://frontend:3000",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


import logging as _logging
_logger = _logging.getLogger("uvicorn.error")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_HIGH_RISK_QUERY_TERMS = (
    "dose",
    "dosage",
    "contraindication",
    "interaction",
    "allergy",
    "adverse",
    "side effect",
    "lab",
    "abnormal",
    "critical",
    "risk",
    "emergency",
    "chest pain",
    "stroke",
    "suicidal",
)


def _is_high_risk_query(user_message: str) -> bool:
    lowered = user_message.lower()
    return any(term in lowered for term in _HIGH_RISK_QUERY_TERMS)


def _should_run_clinical_verification(
    routing_mode: str,
    high_risk_query: bool,
) -> bool:
    mode = routing_mode.lower().strip()
    if mode == "strict":
        return True
    if mode == "fast":
        return False
    return high_risk_query


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _logger.exception(
        "unhandled exception method=%s path=%s error=%s",
        request.method,
        request.url.path,
        exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": _safe_error_detail(exc, "Internal server error")},
    )


def _http_404(detail: str) -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


def _http_409(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _safe_error_detail(exc: Exception, fallback: str) -> str:
    """Client-safe error message. Our own operational failures (e.g. Ollama
    unreachable, missing model) are raised as RuntimeError with curated,
    non-sensitive text and are safe to surface. Any other exception may embed
    file paths or record content, so return a generic message and rely on the
    server logs (which capture the full detail) for diagnosis."""
    if isinstance(exc, RuntimeError):
        return str(exc)
    return fallback


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

_SEX_VALUES = {"male", "female", "intersex", "undisclosed"}
_CONDITION_SOURCES = {"preset", "custom"}
# Allowed values for each enum-style social-history field.
_SOCIAL_ENUMS = {
    "tobacco_status": {"never", "former", "current"},
    "alcohol_status": {"never", "occasional", "moderate", "heavy"},
    "drug_status": {"never", "former", "current"},
    "sexual_activity_status": {"not_active", "active"},
    "sexual_protection": {"always", "sometimes", "never"},
}


def _blank_str_to_none(value):
    """Coerce empty/whitespace strings to None and strip the rest; pass non-strings through."""
    if isinstance(value, str):
        return value.strip() or None
    return value


class ConditionIn(BaseModel):
    category: str
    code: str
    label: str
    source: str = "preset"

    @field_validator("category", "code", "label")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Condition fields cannot be empty")
        return value.strip()

    @field_validator("source")
    @classmethod
    def _valid_source(cls, value: str) -> str:
        if value not in _CONDITION_SOURCES:
            raise ValueError(f"source must be one of {sorted(_CONDITION_SOURCES)}")
        return value


class SocialHistoryIn(BaseModel):
    tobacco_status: Optional[str] = None
    tobacco_details: Optional[str] = None
    alcohol_status: Optional[str] = None
    alcohol_drinks_per_week: Optional[float] = None
    drug_status: Optional[str] = None
    drug_substances: Optional[str] = None
    sexual_activity_status: Optional[str] = None
    sexual_partners: Optional[str] = None
    sexual_partner_genders: Optional[str] = None
    sexual_protection: Optional[str] = None

    @field_validator(
        "tobacco_details",
        "drug_substances",
        "sexual_partners",
        "sexual_partner_genders",
        mode="before",
    )
    @classmethod
    def _free_text(cls, value):
        return _blank_str_to_none(value)

    @field_validator(
        "tobacco_status",
        "alcohol_status",
        "drug_status",
        "sexual_activity_status",
        "sexual_protection",
        mode="before",
    )
    @classmethod
    def _enum_fields(cls, value, info):
        value = _blank_str_to_none(value)
        if value is None:
            return None
        allowed = _SOCIAL_ENUMS[info.field_name]
        if value not in allowed:
            raise ValueError(f"{info.field_name} must be one of {sorted(allowed)}")
        return value

    @field_validator("alcohol_drinks_per_week")
    @classmethod
    def _non_negative(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if value < 0:
            raise ValueError("alcohol_drinks_per_week cannot be negative")
        return value


class ProfileIn(BaseModel):
    dob: Optional[str] = None
    sex_assigned_at_birth: Optional[str] = None
    gender_identity: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    social_history: Optional[SocialHistoryIn] = None
    conditions: list[ConditionIn] = []

    @field_validator("gender_identity", "dob", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        return _blank_str_to_none(value)

    @field_validator("dob")
    @classmethod
    def _valid_dob(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("dob must be a valid YYYY-MM-DD date")
        if parsed > datetime.now(timezone.utc).date():
            raise ValueError("dob cannot be in the future")
        return value

    @field_validator("sex_assigned_at_birth", mode="before")
    @classmethod
    def _valid_sex(cls, value):
        value = _blank_str_to_none(value)
        if value is None:
            return None
        if value not in _SEX_VALUES:
            raise ValueError(f"sex_assigned_at_birth must be one of {sorted(_SEX_VALUES)}")
        return value

    @field_validator("height_cm", "weight_kg")
    @classmethod
    def _positive_measure(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("measurements must be positive")
        return value


class CreatePatientRequest(BaseModel):
    name: str
    profile: Optional[ProfileIn] = None


class PatchPatientRequest(BaseModel):
    name: Optional[str] = None
    memory_results_override: Optional[int] = None
    context_window_tokens_override: Optional[int] = None


class DeletePatientRequest(BaseModel):
    delete_uploads: bool = True
    delete_chats: bool = True
    delete_record_files: bool = True
    delete_vector_data: bool = True


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


class RenameSessionRequest(BaseModel):
    title: str


class ChatRequest(BaseModel):
    patient_id: str
    chat_session_id: str
    message: str


class ConfigUpdateRequest(BaseModel):
    chat_model: Optional[str] = None
    clinical_model: Optional[str] = None
    verification_model: Optional[str] = None
    embedding_model: Optional[str] = None
    embed_timeout_seconds: Optional[float] = None
    chat_timeout_seconds: Optional[float] = None
    meta_timeout_seconds: Optional[float] = None
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    memory_results: Optional[int] = None
    context_window_tokens: Optional[int] = None
    data_path: Optional[str] = None
    ollama_base_url: Optional[str] = None
    routing_mode: Optional[str] = None
    medgemma_verification_enabled: Optional[bool] = None
    grounding_enabled: Optional[bool] = None
    grounding_max_retries: Optional[int] = None


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------

@app.get("/patients")
async def list_patients():
    return await pt.load_patients_index()


@app.post("/patients", status_code=201)
async def create_patient(body: CreatePatientRequest):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Patient name cannot be empty")
    profile = body.profile.model_dump() if body.profile is not None else None
    patient = await pt.create_patient(body.name.strip(), profile=profile)
    # Start watching the new patient's uploads/ folder.
    entry = await pt.find_patient_by_id(patient["id"])
    if entry:
        uploads_dir = Path(entry["folder_path"]) / "uploads"
        watcher.add_patient_watch(patient["id"], uploads_dir)
    return patient


def _patient_view(entry: dict, record: Optional[dict]) -> dict:
    """Full patient shape returned by GET and profile-update endpoints."""
    return {
        "id": entry["id"],
        "name": entry["name"],
        "folder_slug": entry["folder_slug"],
        "folder_path": entry["folder_path"],
        "document_count": entry.get("document_count", 0),
        "last_ingested_at": entry.get("last_ingested_at"),
        "created_at": entry["created_at"],
        "memory_results_override": None if record is None else record.get("memory_results_override"),
        "context_window_tokens_override": None if record is None else record.get("context_window_tokens_override"),
        "profile": None if record is None else record.get("profile"),
    }


_SEX_DISPLAY = {
    "male": "Male",
    "female": "Female",
    "intersex": "Intersex",
    "undisclosed": "Prefer not to say",
}


def _profile_age(dob: Optional[str]) -> Optional[int]:
    if not dob:
        return None
    try:
        birth = datetime.strptime(dob, "%Y-%m-%d").date()
    except ValueError:
        return None
    today = datetime.now(timezone.utc).date()
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    return age if age >= 0 else None


def serialize_profile(profile: Optional[dict]) -> str:
    """Render the structured intake profile (demographics, social history,
    conditions) from patient.json as a readable block for the prompt. Returns ""
    when there is nothing to show so callers can skip adding an empty part."""
    if not profile:
        return ""

    lines: list[str] = []

    dob = profile.get("dob")
    if dob:
        age = _profile_age(dob)
        lines.append(f"Date of birth: {dob}" + (f" (age {age})" if age is not None else ""))
    sex = profile.get("sex_assigned_at_birth")
    if sex:
        lines.append(f"Sex assigned at birth: {_SEX_DISPLAY.get(sex, sex)}")
    gender = profile.get("gender_identity")
    if gender:
        lines.append(f"Gender identity: {gender}")
    height_cm = profile.get("height_cm")
    if isinstance(height_cm, (int, float)):
        lines.append(f"Height: {height_cm:g} cm")
    weight_kg = profile.get("weight_kg")
    if isinstance(weight_kg, (int, float)):
        lines.append(f"Weight: {weight_kg:g} kg")

    social = profile.get("social_history") or {}
    social_lines: list[str] = []
    tobacco_status = social.get("tobacco_status")
    if tobacco_status:
        detail = social.get("tobacco_details")
        social_lines.append("Tobacco: " + tobacco_status + (f" — {detail}" if detail else ""))
    alcohol_status = social.get("alcohol_status")
    if alcohol_status:
        dpw = social.get("alcohol_drinks_per_week")
        extra = f" — {dpw:g} drinks/week" if isinstance(dpw, (int, float)) else ""
        social_lines.append("Alcohol: " + alcohol_status + extra)
    drug_status = social.get("drug_status")
    if drug_status:
        subs = social.get("drug_substances")
        social_lines.append("Recreational drugs: " + drug_status + (f" — {subs}" if subs else ""))
    sexual_status = social.get("sexual_activity_status")
    if sexual_status == "active":
        bits = []
        if social.get("sexual_partners"):
            bits.append(f"partners: {social['sexual_partners']}")
        if social.get("sexual_partner_genders"):
            bits.append(f"gender(s): {social['sexual_partner_genders']}")
        if social.get("sexual_protection"):
            bits.append(f"protection: {social['sexual_protection']}")
        social_lines.append("Sexual activity: active" + (f" — {'; '.join(bits)}" if bits else ""))
    elif sexual_status == "not_active":
        social_lines.append("Sexual activity: not active")
    if social_lines:
        lines.append("")
        lines.append("Social history:")
        lines.extend(f"- {s}" for s in social_lines)

    conditions = profile.get("conditions") or []
    grouped: dict[str, list[str]] = {}
    for c in conditions:
        category = c.get("category") or "Other"
        label = c.get("label") or c.get("code")
        if label:
            grouped.setdefault(category, []).append(label)
    if grouped:
        lines.append("")
        lines.append("Conditions:")
        for category, labels in grouped.items():
            lines.append(f"- {category}: {', '.join(labels)}")

    if not lines:
        return ""

    return "PATIENT PROFILE\n" + "\n".join(lines)


@app.get("/patients/{patient_id}")
async def get_patient(patient_id: str):
    entry = await pt.find_patient_by_id(patient_id)
    if entry is None:
        raise _http_404("Patient not found")
    record = await pt.load_patient_record(patient_id)
    return _patient_view(entry, record)


@app.put("/patients/{patient_id}/profile")
async def update_patient_profile(patient_id: str, body: ProfileIn):
    entry = await pt.find_patient_by_id(patient_id)
    if entry is None:
        raise _http_404("Patient not found")
    record = await pt.update_patient_profile(patient_id, body.model_dump())
    if record is None:
        raise _http_404("Patient not found")
    return _patient_view(entry, record)


@app.patch("/patients/{patient_id}")
async def patch_patient(patient_id: str, body: PatchPatientRequest):
    # exclude_unset (not exclude_none) so an override explicitly sent as null is
    # applied (clears it), while fields the client omitted are left untouched.
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    if "name" in updates:
        name = updates["name"]
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=422, detail="Patient name cannot be empty")
        updates["name"] = name.strip()
    result = await pt.patch_patient(patient_id, updates)
    if result is None:
        raise _http_404("Patient not found")
    return result


@app.delete("/patients/{patient_id}", status_code=204)
async def delete_patient(patient_id: str, body: DeletePatientRequest):
    # Refuse while an ingestion job is running: the job would otherwise re-create
    # the patient's ChromaDB collection (get_or_create) and re-upsert deleted PHI
    # after the cleanup below has already run.
    if jobs.get_active_job(patient_id) is not None:
        raise _http_409("An ingestion job is running for this patient; wait for it to finish and retry.")

    entry = await pt.delete_patient_index_entry(patient_id)
    if entry is None:
        raise _http_404("Patient not found")

    # Stop watching the uploads folder so a recreated same-slug patient doesn't
    # stack duplicate watch handlers on the same path.
    watcher.remove_patient_watch(patient_id)

    folder_path = Path(entry["folder_path"])

    if body.delete_vector_data:
        try:
            await memory.delete_all_patient_collections(patient_id)
        except Exception:
            # Best-effort cleanup: do not block patient deletion if vector
            # storage deletion fails due to backend-specific compatibility.
            pass

    if body.delete_uploads:
        uploads = folder_path / "uploads"
        if uploads.exists():
            await asyncio.to_thread(shutil.rmtree, uploads, True)

    if body.delete_chats:
        chats = folder_path / "chats"
        if chats.exists():
            await asyncio.to_thread(shutil.rmtree, chats, True)

    if body.delete_record_files:
        for name in ("patient.json", "patient.md"):
            f = folder_path / name
            if f.exists():
                f.unlink(missing_ok=True)
        # Remove .extracted files.
        uploads = folder_path / "uploads"
        if uploads.exists():
            for ef in uploads.glob("*.extracted"):
                ef.unlink(missing_ok=True)

    # If all patient content has been removed, delete the now-empty folder.
    if body.delete_uploads and body.delete_chats and body.delete_record_files:
        await asyncio.to_thread(shutil.rmtree, folder_path, True)

    # Drop the patient's in-memory bookkeeping so these tables don't accumulate.
    pt.discard_record_lock(patient_id)
    jobs.discard_patient_jobs(patient_id)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@app.post("/patients/{patient_id}/upload")
async def upload_files(
    patient_id: str,
    files: list[UploadFile] = File(...),
):
    entry = await pt.find_patient_by_id(patient_id)
    if entry is None:
        raise _http_404("Patient not found")

    uploads_dir = Path(entry["folder_path"]) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    saved_names: list[str] = []
    for file in files:
        safe_name = pt.sanitize_filename(file.filename or "upload")
        dest = pt.safe_upload_path(uploads_dir, safe_name)
        # Stream to disk in chunks and offload the blocking write to a thread, so
        # a large upload neither buffers the whole file in RAM nor blocks the event
        # loop (which would freeze chat and status polling for all requests).
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                await asyncio.to_thread(out.write, chunk)
        saved_names.append(safe_name)

    job = await jobs.start_incremental_ingestion(patient_id, saved_names)
    return {"filenames": saved_names, "job_id": job["job_id"]}


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

@app.post("/ingest/{patient_id}")
async def ingest(patient_id: str):
    if await pt.find_patient_by_id(patient_id) is None:
        raise _http_404("Patient not found")
    job = await jobs.start_incremental_ingestion(patient_id)
    return {"job_id": job["job_id"]}


@app.post("/rebuild/{patient_id}")
async def rebuild(patient_id: str):
    if await pt.find_patient_by_id(patient_id) is None:
        raise _http_404("Patient not found")
    job = await jobs.start_full_rebuild(patient_id)
    return {"job_id": job["job_id"]}


@app.post("/refresh/{patient_id}")
async def refresh(patient_id: str):
    """Alias for incremental ingest — used internally by the watcher."""
    if await pt.find_patient_by_id(patient_id) is None:
        raise _http_404("Patient not found")
    job = await jobs.start_incremental_ingestion(patient_id)
    return {"job_id": job["job_id"]}


@app.get("/status/{job_id}")
async def job_status(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise _http_404("Job not found")
    return job


@app.get("/documents/{patient_id}")
async def list_documents(patient_id: str):
    record = await pt.load_patient_record(patient_id)
    if record is None:
        raise _http_404("Patient not found")
    return record.get("documents", [])


@app.delete("/documents/{patient_id}/{document_id}")
async def delete_document(patient_id: str, document_id: str):
    entry = await pt.find_patient_by_id(patient_id)
    if entry is None:
        raise _http_404("Patient not found")

    record = await pt.load_patient_record(patient_id)
    if record is None:
        raise _http_404("Patient not found")

    doc = next((d for d in record.get("documents", []) if d.get("id") == document_id), None)
    if doc is None:
        raise _http_404("Document not found")

    job = await jobs.start_document_deletion(patient_id, document_id)
    if job is None:
        raise _http_409("An ingestion or rebuild job is in progress; try again once it finishes.")
    return {"job_id": job["job_id"]}


@app.get("/patients/{patient_id}/active-job")
async def active_job(patient_id: str):
    return jobs.get_active_job(patient_id)


# ---------------------------------------------------------------------------
# Chat sessions
# ---------------------------------------------------------------------------

@app.get("/patients/{patient_id}/chat-sessions")
async def list_sessions(patient_id: str):
    record = await pt.load_patient_record(patient_id)
    if record is None:
        raise _http_404("Patient not found")
    return record.get("chat_sessions", [])


@app.post("/patients/{patient_id}/chat-sessions", status_code=201)
async def create_session(patient_id: str, body: CreateSessionRequest):
    if await pt.find_patient_by_id(patient_id) is None:
        raise _http_404("Patient not found")

    session_id = str(uuid.uuid4())
    now = _now()
    session = {
        "id": session_id,
        "patient_id": patient_id,
        "title": body.title or "New Chat",
        "title_auto_generated": True,
        "created_at": now,
        "last_message_at": None,
        "message_count": 0,
        "conversation_state": {
            "rolling_summary": "",
            "active_topics": [],
            "open_questions": [],
            "created_at": now,
            "last_updated_at": now,
        },
    }
    await pt.add_chat_session(patient_id, session)

    return {"chat_session_id": session_id}


@app.patch("/patients/{patient_id}/chat-sessions/{session_id}")
async def rename_session(
    patient_id: str, session_id: str, body: RenameSessionRequest
):
    result = await pt.update_chat_session(
        patient_id, session_id, {"title": body.title, "title_auto_generated": False}
    )
    if result is None:
        raise _http_404("Session not found")
    return result


@app.delete("/patients/{patient_id}/chat-sessions/{session_id}", status_code=204)
async def delete_session(patient_id: str, session_id: str):
    deleted = await pt.delete_chat_session(patient_id, session_id)
    if not deleted:
        raise _http_404("Session not found")
    await memory.delete_chat_collection(patient_id, session_id)
    await pt.delete_message_log(patient_id, session_id)


# ---------------------------------------------------------------------------
# Chat messages & state
# ---------------------------------------------------------------------------

@app.get("/chat/messages/{patient_id}/{session_id}")
async def get_messages(patient_id: str, session_id: str):
    log = await pt.load_message_log(patient_id, session_id)
    if log is None:
        raise _http_404("Patient not found")
    return log


@app.get("/chat/state/{patient_id}/{session_id}")
async def get_chat_state(patient_id: str, session_id: str):
    state = await pt.load_conversation_state(patient_id, session_id)
    if state is None:
        raise _http_404("Conversation state not found")
    return state


@app.post("/chat/reset/{patient_id}/{session_id}", status_code=204)
async def reset_chat(patient_id: str, session_id: str):
    await memory.delete_chat_collection(patient_id, session_id)
    now = _now()
    state = {
        "rolling_summary": "",
        "active_topics": [],
        "open_questions": [],
        "created_at": now,
        "last_updated_at": now,
    }
    await pt.save_conversation_state(patient_id, session_id, state)
    # Reset message log.
    await pt.save_message_log(
        patient_id, session_id, {"session_id": session_id, "messages": []}
    )


@app.post("/chat/rebuild-state/{patient_id}/{session_id}", status_code=204)
async def rebuild_state(patient_id: str, session_id: str):
    """Recompute conversation state from the stored message log."""
    log = await pt.load_message_log(patient_id, session_id)
    if log is None:
        raise _http_404("Patient not found")

    messages = log.get("messages", [])
    if not messages:
        return

    # Reconstruct by feeding each exchange to the state updater.
    state: Optional[dict] = None
    for i in range(0, len(messages) - 1, 2):
        user_msg = messages[i] if messages[i]["role"] == "user" else None
        asst_msg = messages[i + 1] if len(messages) > i + 1 and messages[i + 1]["role"] == "assistant" else None
        if user_msg and asst_msg:
            updates = await tl.update_conversation_state(
                state, user_msg["content"], asst_msg["content"]
            )
            if state is None:
                now = _now()
                state = {
                    "created_at": now,
                }
            state.update(updates)

    if state:
        await pt.save_conversation_state(patient_id, session_id, state)


# ---------------------------------------------------------------------------
# Chat inference
# ---------------------------------------------------------------------------

@app.post("/chat")
async def chat(body: ChatRequest):
    from documents import read_patient_md  # noqa: PLC0415 — deferred to avoid circular import at module level

    _logger.info(
        "chat request received patient_id=%s session_id=%s message_len=%d",
        body.patient_id,
        body.chat_session_id,
        len(body.message or ""),
    )

    cfg = load_config()
    patient_id = body.patient_id
    session_id = body.chat_session_id
    user_message = body.message

    try:
        entry = await pt.find_patient_by_id(patient_id)
        if entry is None:
            raise _http_404("Patient not found")

        # Refuse to answer while an ingestion/rebuild/delete job is mutating the
        # record. Otherwise chat would read a half-rebuilt vector store / patient.md
        # and ground its answer in an inconsistent medical record.
        if jobs.get_active_job(patient_id) is not None:
            raise HTTPException(
                status_code=409,
                detail="The record is being updated. Please wait for ingestion to finish before sending a message.",
            )

        record = await pt.load_patient_record(patient_id)
        if record is None:
            raise _http_404("Patient record not found")

        # Per-patient overrides. Use an explicit None check so a deliberately
        # stored 0 (e.g. disable semantic history retrieval) isn't silently
        # treated as "unset" and overridden by the global default.
        memory_results_override = record.get("memory_results_override")
        effective_memory_results = (
            memory_results_override if memory_results_override is not None else cfg.memory_results
        )
        ctx_tokens_override = record.get("context_window_tokens_override")
        effective_ctx_tokens = (
            ctx_tokens_override if ctx_tokens_override is not None else cfg.context_window_tokens
        )

        # 1. Classify query for optimised chunk retrieval fallback.
        doc_type = tl.classify_query(user_message)

        # 2. Retrieve relevant chat history (semantic memory).
        query_embedding = await ai.embed(user_message)
        history_chunks = await memory.query_chat(
            patient_id, session_id, query_embedding, effective_memory_results
        )
        history_text = "\n\n".join(c["text"] for c in history_chunks)

        # 3. Load conversation state.
        conv_state = await pt.load_conversation_state(patient_id, session_id)
        state_capsule = ""
        if conv_state:
            state_capsule = (
                f"Rolling summary: {conv_state.get('rolling_summary', '')}\n"
                f"Active topics: {', '.join(conv_state.get('active_topics', []))}\n"
                f"Open questions: {', '.join(conv_state.get('open_questions', []))}"
            )

        # 4. Patient context — full patient.md or chunk fallback.
        # Use a conservative ~3 chars/token estimate (not the optimistic 4): dense
        # clinical text — codes, numbers, dates — tokenizes closer to 3 chars/token,
        # and patient.md is date-ordered, so an underestimate makes Ollama silently
        # truncate the TAIL (the most recent labs/meds). Falling back to chunk
        # retrieval a little earlier is far safer than dropping the newest records.
        patient_md = await asyncio.to_thread(read_patient_md, Path(entry["folder_path"]))
        token_estimate = len(patient_md) // 3
        use_chunks = token_estimate > effective_ctx_tokens

        if use_chunks:
            chunk_results = max(6, min(20, effective_ctx_tokens // 3000))
            doc_chunks = await memory.query_docs(
                patient_id, query_embedding, n_results=chunk_results, document_type=doc_type
            )
            patient_context = "\n\n".join(c["text"] for c in doc_chunks)
        else:
            patient_context = patient_md

        _logger.info(
            "chat context prepared patient_id=%s session_id=%s use_chunks=%s token_estimate=%d",
            patient_id,
            session_id,
            use_chunks,
            token_estimate,
        )

        # 5. Assemble prompt.
        # Patient records go in the SYSTEM message so that small instruction-tuned
        # models treat them as pre-loaded background knowledge rather than something
        # the user is asking them to process. Only the conversational elements
        # (state, history, question) go in the USER turn.
        system_parts = [
            "You are OpenHealth, a knowledgeable and compassionate medical AI assistant.\n"
            "The patient's medical record is provided below.\n"
            "Use it to ground your responses — interpret, explain, and connect information across the records.\n"
            "When referencing specific information, cite the source document.\n"
            "Be direct, warm, and clear. Write in paragraphs, not bullet points.\n"
            "You are not limited to only what is in the documents — use your medical knowledge\n"
            "to help the user understand, interpret, and act on what the records contain."
        ]
        if patient_context:
            system_parts.append(f"\n\n--- PATIENT RECORDS ---\n{patient_context}")

        system_prompt = "\n".join(system_parts)

        # Structured intake profile (demographics, social history, conditions) the
        # user supplied about the patient. Sent as a user content part so it frames
        # the question as user-provided context rather than ingested record text.
        profile_text = serialize_profile(record.get("profile"))

        user_content_parts = []
        if profile_text:
            user_content_parts.append(profile_text)
        if state_capsule:
            user_content_parts.append(f"CONVERSATION CONTEXT:\n{state_capsule}")
        if history_text:
            user_content_parts.append(f"RELEVANT PRIOR EXCHANGES:\n{history_text}")
        user_content_parts.append(user_message)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n\n---\n\n".join(user_content_parts)},
        ]

        high_risk_query = _is_high_risk_query(user_message)
        draft_model = cfg.clinical_model if cfg.routing_mode.lower().strip() == "strict" else cfg.chat_model

        # 6. Generate response (capturing any model reasoning separately).
        draft, draft_thinking = await ai.chat_complete_with_thinking(
            messages, model=draft_model, num_ctx=effective_ctx_tokens
        )

        # 7. Grounding verification.
        grounding_retried = False
        retry_count = 0
        final_answer = draft
        citations: list[dict] = []

        if cfg.grounding_enabled and cfg.medgemma_verification_enabled:
            should_verify = _should_run_clinical_verification(cfg.routing_mode, high_risk_query)
        else:
            should_verify = False

        if should_verify:
            context_for_grounding = patient_context
            grounding_context_limit = max(8000, min(30000, effective_ctx_tokens // 2))
            grounding_uncertainty = ""
            for attempt in range(cfg.grounding_max_retries + 1):
                result = await tl.verify_grounding(
                    final_answer,
                    context_for_grounding,
                    context_limit=grounding_context_limit,
                    model=cfg.verification_model,
                )
                citations = result.get("citations", [])
                if attempt > 0:
                    grounding_retried = True
                    retry_count = attempt
                # Accept if uncertainty_note is empty or answer hasn't changed materially.
                final_answer = result["corrected_answer"]
                grounding_uncertainty = result.get("uncertainty_note") or ""
                if not grounding_uncertainty or attempt >= cfg.grounding_max_retries:
                    break
                # Retry with corrected answer as the new draft.

            # Surface a residual uncertainty (incl. a verification that could not
            # be completed — verify_grounding fails closed with a non-empty note)
            # so the user is never shown an unverified answer as if it were clean.
            if grounding_uncertainty:
                final_answer = f"{final_answer}\n\n_{grounding_uncertainty.strip()}_"

        # 8. Persist exchange.
        now = _now()
        user_msg_record = {
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": user_message,
            "citations": [],
            "timestamp": now,
        }
        asst_msg_record = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": final_answer,
            "citations": citations,
            "thinking": draft_thinking,
            "timestamp": now,
        }
        await pt.append_message(patient_id, session_id, user_msg_record)
        await pt.append_message(patient_id, session_id, asst_msg_record)

        # Embed and store the exchange in ChromaDB chat memory.
        exchange_text = f"User: {user_message}\nAssistant: {final_answer}"
        exchange_embedding = await ai.embed(exchange_text)
        await memory.upsert_chat_exchange(
            patient_id=patient_id,
            session_id=session_id,
            exchange_id=str(uuid.uuid4()),
            text=exchange_text,
            embedding=exchange_embedding,
            metadata={
                "patient_id": patient_id,
                "chat_session_id": session_id,
                "role": "exchange",
                "timestamp": now,
            },
        )

        # 9. Update conversation state (fire-and-forget style but awaited since
        #    we're still in the request — acceptable latency for a local app).
        state_updates = await tl.update_conversation_state(conv_state, user_message, final_answer)
        if conv_state is None:
            conv_state = {
                "created_at": now,
            }
        conv_state.update(state_updates)
        await pt.save_conversation_state(patient_id, session_id, conv_state)

        # 10. Update session metadata.
        log = await pt.load_message_log(patient_id, session_id)
        msg_count = len(log["messages"]) if log else 0
        session_updates: dict[str, Any] = {
            "last_message_at": now,
            "message_count": msg_count,
        }

        # Auto-title on first completed exchange.
        session_record = next(
            (s for s in record.get("chat_sessions", []) if s["id"] == session_id), None
        )
        _logger.info(
            "auto-title check session_id=%s session_found=%s title_auto_generated=%s msg_count=%d",
            session_id,
            session_record is not None,
            session_record.get("title_auto_generated") if session_record else None,
            msg_count,
        )
        if session_record and session_record.get("title_auto_generated") and session_record.get("title") == "New Chat":
            try:
                auto_title = await tl.generate_session_title(
                    user_message,
                    final_answer,
                    model=cfg.chat_model,
                )
                _logger.info(
                    "auto-title generated session_id=%s title=%s",
                    session_id,
                    repr(auto_title) if LOG_PAYLOADS else f"<redacted {len(auto_title or '')} chars>",
                )
                if auto_title:
                    session_updates["title"] = auto_title
                    session_updates["title_auto_generated"] = False
            except Exception:
                _logger.exception("auto-title failed session_id=%s — skipping", session_id)

        await pt.update_chat_session(patient_id, session_id, session_updates)

        _logger.info(
            "chat request completed patient_id=%s session_id=%s message_len=%d response_len=%d",
            patient_id,
            session_id,
            len(user_message or ""),
            len(final_answer or ""),
        )

        return {
            "response": final_answer,
            "grounding_retried": grounding_retried,
            "retry_count": retry_count,
            "citations": citations,
            "thinking": draft_thinking,
        }
    except HTTPException:
        raise
    except Exception as exc:
        _logger.exception(
            "chat request failed patient_id=%s session_id=%s message_len=%d",
            patient_id,
            session_id,
            len(user_message or ""),
        )
        raise HTTPException(
            status_code=500,
            detail=_safe_error_detail(
                exc, "Chat failed due to an internal error. See server logs for details."
            ),
        )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@app.get("/config")
async def get_config():
    return load_config().model_dump()


@app.post("/config")
async def update_config(body: ConfigUpdateRequest):
    updates = body.model_dump(exclude_none=True)
    try:
        updated = patch_config(updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return updated.model_dump()


@app.get("/models")
async def list_models():
    try:
        models = await ai.list_models()
        return {"models": models}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Ollama: {e}")

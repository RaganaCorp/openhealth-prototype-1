"""
FastAPI application — all routes.
"""

import asyncio
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

import ai
import documents
import jobs
import llm
import memory
import patients as pt
import prompts
import watcher
from config import load_config, patch_config, save_config, Config


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await watcher.start_watcher()
    yield
    watcher.stop_watcher()
    await ai.aclose_client()


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


# ---------------------------------------------------------------------------
# Chat conversation memory
# ---------------------------------------------------------------------------

# Strong refs to fire-and-forget background tasks (post-chat bookkeeping), so the
# event loop can't garbage-collect them mid-run. Mirrors jobs._spawn.
_background_tasks: set = set()


def _spawn_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# Most-recent exchange pairs kept verbatim in the prompt. Older exchanges are folded
# into the rolling summary one at a time as they leave this window. Keeping recent
# turns verbatim preserves pronoun / "that" references that semantic retrieval alone
# can miss, and lets the common short conversation run with NO summary LLM call.
RECENT_TURN_PAIRS = 4
# Cap each verbatim message so one long answer can't dominate the context window.
_RECENT_TURN_CHAR_CAP = 1500


def _format_recent_turns(messages: list[dict], max_pairs: int) -> str:
    """Render the last ``max_pairs`` user/assistant exchanges verbatim."""
    if max_pairs <= 0:
        return ""
    lines: list[str] = []
    for m in messages[-max_pairs * 2:]:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if len(content) > _RECENT_TURN_CHAR_CAP:
            content = content[:_RECENT_TURN_CHAR_CAP] + " […]"
        speaker = "User" if m.get("role") == "user" else "Assistant"
        lines.append(f"{speaker}: {content}")
    return "\n\n".join(lines)


def _overflow_exchange(messages: list[dict], window_pairs: int) -> Optional[tuple[str, str]]:
    """The (user, assistant) exchange that just left the verbatim window this turn,
    or None while the whole conversation still fits in the window. Returns each
    exchange exactly once — the turn after it drops out of the recent window — so
    folding it into the rolling summary captures all older history without ever
    re-summarizing (and re-distorting) the recent turns."""
    num_pairs = len(messages) // 2
    idx = num_pairs - 1 - window_pairs
    if idx < 0:
        return None
    user_msg = messages[2 * idx]
    asst_msg = messages[2 * idx + 1]
    if user_msg.get("role") != "user" or asst_msg.get("role") != "assistant":
        return None
    return (user_msg.get("content") or "", asst_msg.get("content") or "")


async def _post_chat_followup(
    cfg: Config,
    patient_id: str,
    session_id: str,
    user_message: str,
    final_answer: str,
    now: str,
    record: dict,
) -> None:
    """Model-bound bookkeeping that doesn't need to block the user's answer: store
    the exchange in semantic memory, fold the exchange leaving the verbatim window
    into the rolling summary, and update session metadata / auto-title. Runs as a
    background task; each persistence step takes the per-patient record lock, so it
    serialises safely with the next turn."""
    try:
        # Store the exchange in ChromaDB chat memory for later semantic retrieval.
        exchange_text = f"User: {user_message}\nAssistant: {final_answer}"
        exchange_embedding = await ai.embed(exchange_text, task="document")
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

        log = await pt.load_message_log(patient_id, session_id)
        messages = log.get("messages", []) if log else []

        # Rolling summary: only fold the exchange that just left the verbatim window
        # (none while the conversation still fits). Reload state fresh here rather
        # than holding it across the answer, so a prior turn's fold isn't lost.
        overflow = _overflow_exchange(messages, RECENT_TURN_PAIRS)
        if overflow is not None:
            conv_state = await pt.load_conversation_state(patient_id, session_id)
            state_updates = await llm.update_conversation_state(conv_state, overflow[0], overflow[1])
            new_state = dict(conv_state) if conv_state else {"created_at": now}
            new_state.update(state_updates)
            await pt.save_conversation_state(patient_id, session_id, new_state)

        # Session metadata + auto-title on the first completed exchange.
        session_updates: dict[str, Any] = {
            "last_message_at": now,
            "message_count": len(messages),
        }
        session_record = next(
            (s for s in record.get("chat_sessions", []) if s["id"] == session_id), None
        )
        if session_record and session_record.get("title_auto_generated") and session_record.get("title") == "New Chat":
            try:
                auto_title = await llm.generate_session_title(user_message, final_answer, model=cfg.chat_model)
                if auto_title:
                    session_updates["title"] = auto_title
                    session_updates["title_auto_generated"] = False
            except Exception:
                _logger.exception("auto-title failed session_id=%s — skipping", session_id)

        await pt.update_chat_session(patient_id, session_id, session_updates)
    except Exception:
        _logger.exception(
            "post-chat followup failed patient_id=%s session_id=%s", patient_id, session_id
        )


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
    vision_model: Optional[str] = None
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
    ollama_keep_alive: Optional[str] = None
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


def _uniq_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        normalized = (item or "").strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            out.append(normalized)
    return out


def _latest_by_name(entries: list[tuple]) -> list[tuple]:
    """From (name, value, unit, date) tuples keep the most recent per name, newest first.
    Dates are mixed-format/unknown, so compare on a normalized ISO key rather than the
    raw string (see documents.date_sort_key) — otherwise an older record can be picked
    as 'most recent' and unknown-dated entries sort as newest."""
    best: dict[str, tuple] = {}
    for name, value, unit, date in entries:
        key = name.strip().lower()
        cur = best.get(key)
        if cur is None or documents.date_sort_key(date) > documents.date_sort_key(cur[3]):
            best[key] = (name.strip(), value, unit, date)
    return sorted(best.values(), key=lambda e: documents.date_sort_key(e[3]), reverse=True)


def build_facts_summary(record: dict) -> str:
    """Aggregate the per-document extracted facts into a compact structured snapshot
    for chat context: current conditions, medications, allergies, and the most recent
    value of each lab and vital. Returns '' when there are no facts yet (so chat
    falls back to the retrieved narrative excerpts alone)."""
    problems: list[str] = []
    medications: list[str] = []
    allergies: list[str] = []
    labs: list[tuple] = []
    vitals: list[tuple] = []

    for doc in record.get("documents", []):
        facts = doc.get("facts") or {}
        doc_date = doc.get("date_detected") or ""
        problems += [p["name"] for p in facts.get("problems", []) if p.get("name")]
        medications += [m["name"] for m in facts.get("medications", []) if m.get("name")]
        allergies += [a["substance"] for a in facts.get("allergies", []) if a.get("substance")]
        for lab in facts.get("lab_results", []):
            name = lab.get("canonical_name") or lab.get("test")
            if name and lab.get("value"):
                labs.append((name, lab["value"], lab.get("unit") or "", lab.get("date") or doc_date))
        for vital in facts.get("vitals", []):
            if vital.get("name") and vital.get("value"):
                vitals.append((vital["name"], vital["value"], vital.get("unit") or "", vital.get("date") or doc_date))

    problems = _uniq_keep_order(problems)
    medications = _uniq_keep_order(medications)
    allergies = _uniq_keep_order(allergies)
    latest_labs = _latest_by_name(labs)[:25]
    latest_vitals = _latest_by_name(vitals)

    def _measure_line(name: str, value: str, unit: str, date: str) -> str:
        unit_part = f" {unit}".rstrip()
        date_part = f" ({date})" if date else ""
        return f"- {name}: {value}{unit_part}{date_part}"

    sections: list[str] = []
    if problems:
        sections.append("Conditions: " + ", ".join(problems))
    if medications:
        sections.append("Medications: " + ", ".join(medications))
    if allergies:
        sections.append("Allergies: " + ", ".join(allergies))
    if latest_labs:
        sections.append("Most recent labs:\n" + "\n".join(_measure_line(*lab) for lab in latest_labs))
    if latest_vitals:
        sections.append("Most recent vitals:\n" + "\n".join(_measure_line(*v) for v in latest_vitals))

    if not sections:
        return ""
    return "STRUCTURED RECORD SUMMARY (most recent values; see excerpts for detail)\n" + "\n".join(sections)


def _citations_from_chunks(chunks: list[dict]) -> list[dict]:
    """Build citations from the chunks actually retrieved into context, so a citation
    always points at a real source rather than something the model invented."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for chunk in chunks:
        md = chunk.get("metadata") or {}
        filename = md.get("filename")
        if not filename:
            continue
        key = (filename, md.get("date"))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "filename": filename,
            "date": md.get("date"),
            "excerpt": (chunk.get("text") or "")[:240],
        })
    return out


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
        # Remove cached extraction + facts sidecars.
        uploads = folder_path / "uploads"
        if uploads.exists():
            for pattern in ("*.extracted", "*.facts.json"):
                for ef in uploads.glob(pattern):
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
            updates = await llm.update_conversation_state(
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

        # 1. Retrieve relevant chat history (semantic memory) + recent turns verbatim.
        query_embedding = await ai.embed(user_message, task="query")
        history_chunks = await memory.query_chat(
            patient_id, session_id, query_embedding, effective_memory_results
        )
        history_text = "\n\n".join(c["text"] for c in history_chunks)

        # Recent turns verbatim: load the log BEFORE this turn's exchange is appended,
        # so it holds only prior turns. Verbatim recent history keeps pronoun / "that"
        # references intact that the semantic retrieval above can miss.
        prior_log = await pt.load_message_log(patient_id, session_id)
        prior_messages = prior_log.get("messages", []) if prior_log else []
        recent_turns_text = _format_recent_turns(prior_messages, RECENT_TURN_PAIRS)

        # 2. Load conversation state — the rolling summary of turns OLDER than the
        # verbatim window. Include only non-empty parts so a short conversation (whose
        # full history is already in the verbatim window) adds no empty-summary noise.
        conv_state = await pt.load_conversation_state(patient_id, session_id)
        state_bits: list[str] = []
        if conv_state:
            if conv_state.get("rolling_summary"):
                state_bits.append(f"Summary of earlier conversation: {conv_state['rolling_summary']}")
            if conv_state.get("active_topics"):
                state_bits.append(f"Active topics: {', '.join(conv_state['active_topics'])}")
            if conv_state.get("open_questions"):
                state_bits.append(f"Open questions: {', '.join(conv_state['open_questions'])}")
        state_capsule = "\n".join(state_bits)

        # 3. Patient context = structured facts summary + retrieved, provenance-tagged
        # excerpts. The facts summary gives a clean current snapshot (and degrades to
        # "" when extraction hasn't produced facts yet); the excerpts carry the
        # narrative. A larger n_results means a small record retrieves ~all of its
        # chunks. (patient.md is no longer read into the prompt — it's an export only.)
        facts_summary = build_facts_summary(record)
        chunk_results = max(8, min(30, effective_ctx_tokens // 2000))
        doc_chunks = await memory.query_docs(
            patient_id, query_embedding, n_results=chunk_results, document_type=None
        )
        excerpt_text = "\n\n".join(c["text"] for c in doc_chunks)

        context_blocks: list[str] = []
        if facts_summary:
            context_blocks.append(facts_summary)
        if excerpt_text:
            context_blocks.append("RELEVANT EXCERPTS FROM THE RECORD:\n" + excerpt_text)
        patient_context = "\n\n".join(context_blocks)

        # Citations come from the chunks actually retrieved, not the model.
        citations = _citations_from_chunks(doc_chunks)

        _logger.info(
            "chat context prepared patient_id=%s session_id=%s facts=%s chunks=%d",
            patient_id,
            session_id,
            bool(facts_summary),
            len(doc_chunks),
        )

        # 4. Assemble prompt.
        # Patient records go in the SYSTEM message so that small instruction-tuned
        # models treat them as pre-loaded background knowledge rather than something
        # the user is asking them to process. Only the conversational elements
        # (state, history, question) go in the USER turn.
        system_parts = [prompts.CHAT_SYSTEM_PROMPT]
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
        if recent_turns_text:
            user_content_parts.append(f"RECENT CONVERSATION:\n{recent_turns_text}")
        if history_text:
            user_content_parts.append(f"OTHER RELEVANT EXCHANGES:\n{history_text}")
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

        # 7. Grounding verification. Citations are already set from the retrieved
        # chunks above; the verifier only corrects/qualifies the answer text.
        grounding_retried = False
        retry_count = 0
        final_answer = draft

        if cfg.grounding_enabled and cfg.medgemma_verification_enabled:
            should_verify = _should_run_clinical_verification(cfg.routing_mode, high_risk_query)
        else:
            should_verify = False

        if should_verify:
            context_for_grounding = patient_context
            grounding_context_limit = max(8000, min(30000, effective_ctx_tokens // 2))
            grounding_uncertainty = ""
            for attempt in range(cfg.grounding_max_retries + 1):
                result = await llm.verify_grounding(
                    final_answer,
                    context_for_grounding,
                    context_limit=grounding_context_limit,
                    model=cfg.verification_model,
                )
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
            # Gate on citations: a general-knowledge answer draws on no record chunks
            # (so citations is empty) and makes no record claims, so a "could not be
            # verified against the record" note there is misleading — suppress it.
            if grounding_uncertainty and citations:
                final_answer = f"{final_answer}\n\n_{grounding_uncertainty.strip()}_"

        # 8. Persist the exchange to the message log NOW — it's the source of truth
        #    the UI reloads from, so it stays on the response path (local file writes
        #    are fast; a reload right after sending must never lose the turn).
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

        # 9. Hand the model-bound bookkeeping (chat-memory embedding, rolling-summary
        #    fold, session metadata, auto-title) to a background task so the user gets
        #    the answer without waiting on extra Ollama round-trips afterwards.
        _spawn_background(
            _post_chat_followup(
                cfg, patient_id, session_id, user_message, final_answer, now, record
            )
        )

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


async def _startup_preflight() -> None:
    try:
        await ai.ensure_ollama_available()
    finally:
        # The preflight runs on a temporary asyncio.run() loop that is torn down
        # immediately after. Close the shared HTTP client here so its connection
        # pool isn't carried — bound to this now-dead loop — into uvicorn's server
        # loop, where reusing it fails with "Event loop is closed".
        await ai.aclose_client()


def main() -> int:
    try:
        asyncio.run(_startup_preflight())
    except RuntimeError as exc:
        _logger.error(str(exc))
        return 1

    # Hot reload for development: uvicorn can only watch + reload when given the
    # app as an import string ("main:app"), not the app instance. Enabled via
    # BACKEND_RELOAD so production (which runs this same entrypoint) stays single
    # process. reload_dirs scopes the watcher to the backend package so writes to
    # the data folder don't trigger restarts.
    reload_enabled = os.getenv("BACKEND_RELOAD", "").strip().lower() in ("1", "true", "yes", "on")
    uvicorn.run(
        "main:app" if reload_enabled else app,
        host=os.getenv("BACKEND_HOST", "127.0.0.1"),
        port=int(os.getenv("BACKEND_PORT", "8000")),
        log_level=os.getenv("BACKEND_LOG_LEVEL", "debug"),
        reload=reload_enabled,
        reload_dirs=[str(Path(__file__).parent)] if reload_enabled else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

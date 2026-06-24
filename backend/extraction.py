"""
Structured clinical fact extraction.

Extraction runs eagerly during ingest over a document's cleaned text and returns
a normalized DocumentFacts record. To stay reliable on a small local model it is
**decomposed**: one focused call per category (problems, medications, labs, …),
each constrained to a single-array schema and fed only the relevant document
section when one is detected. A small model fills one focused bucket far more
accurately — and is far less prone to the repeating-array loop — than a single
call asked to fill every category at once. The caller caches the result to a
.facts.json sidecar so re-ingest is free.

canonical_name on lab results is LLM-emitted here; Phase G layers a curated
table on top (table first, this as fallback).
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel, Field

import ai
import prompts
from config import load_config

_logger = logging.getLogger("uvicorn.error")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class LabResult(BaseModel):
    test: str
    canonical_name: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    date: Optional[str] = None
    date_type: Optional[str] = None


class Medication(BaseModel):
    name: str
    dose: Optional[str] = None
    frequency: Optional[str] = None
    status: Optional[str] = None


class Problem(BaseModel):
    name: str
    status: Optional[str] = None
    date: Optional[str] = None


class Vital(BaseModel):
    name: str
    value: Optional[str] = None
    unit: Optional[str] = None
    date: Optional[str] = None


class Allergy(BaseModel):
    substance: str
    reaction: Optional[str] = None
    severity: Optional[str] = None


class Procedure(BaseModel):
    name: str
    date: Optional[str] = None


class FamilyHistory(BaseModel):
    # A relative's condition. relationship is optional because notes sometimes record
    # "family history of X" without naming who.
    condition: str
    relationship: Optional[str] = None
    member: Optional[str] = None


class DocumentFacts(BaseModel):
    # The clinically relevant date of the document itself (never the patient DOB).
    document_date: Optional[str] = None
    document_date_type: Optional[str] = None
    problems: list[Problem] = Field(default_factory=list)
    medications: list[Medication] = Field(default_factory=list)
    lab_results: list[LabResult] = Field(default_factory=list)
    vitals: list[Vital] = Field(default_factory=list)
    allergies: list[Allergy] = Field(default_factory=list)
    procedures: list[Procedure] = Field(default_factory=list)
    family_history: list[FamilyHistory] = Field(default_factory=list)


# Single-array wrapper schemas — one focused shape per extraction call.
class _Problems(BaseModel):
    problems: list[Problem] = Field(default_factory=list)


class _Medications(BaseModel):
    medications: list[Medication] = Field(default_factory=list)


class _LabResults(BaseModel):
    lab_results: list[LabResult] = Field(default_factory=list)


class _Vitals(BaseModel):
    vitals: list[Vital] = Field(default_factory=list)


class _Allergies(BaseModel):
    allergies: list[Allergy] = Field(default_factory=list)


class _Procedures(BaseModel):
    procedures: list[Procedure] = Field(default_factory=list)


class _FamilyHistory(BaseModel):
    family_history: list[FamilyHistory] = Field(default_factory=list)


class _DocumentDate(BaseModel):
    document_date: Optional[str] = None
    document_date_type: Optional[str] = None


@dataclass(frozen=True)
class _Category:
    key: str
    label: str
    model: type[BaseModel]
    instructions: str
    # Substrings that mark a section header for this category (lowercased).
    section_keywords: tuple[str, ...]


_CATEGORIES: tuple[_Category, ...] = (
    _Category(
        "problems", "problem or diagnosis", _Problems,
        "A problem is a diagnosis or active condition; include the name and the ICD code if shown.",
        ("problem", "assessment", "diagnos", "impression"),
    ),
    _Category(
        "medications", "medication", _Medications,
        "Include the drug name, dose, frequency, and status (active or discontinued) if shown.",
        ("medication", "meds", "prescription"),
    ),
    _Category(
        "lab_results", "lab result", _LabResults,
        "Each has a test name, value, unit, and reference range. Also give canonical_name: a "
        "standardized lowercase test name (e.g. \"ldl cholesterol\" for \"LDL\" or \"LDL-C\").",
        ("result", "laborator", "lab"),
    ),
    _Category(
        "vitals", "vital sign", _Vitals,
        "Vitals include blood pressure, weight, height, BMI, temperature, pulse, respiration, and O2 saturation.",
        ("vital",),
    ),
    _Category(
        "allergies", "allergy", _Allergies,
        "Include the substance and the reaction or severity if shown. \"No known allergies\" means there are none.",
        ("allerg",),
    ),
    _Category(
        "procedures", "procedure or surgery", _Procedures,
        "Procedures and surgeries the patient has had. Exclude lab tests and lab orders.",
        ("procedure", "surg"),
    ),
    _Category(
        "family_history", "family medical history entry", _FamilyHistory,
        "Each entry pairs a relative's relationship (e.g. father, mother, sibling) with a "
        "condition they have or had; include the relative's name in member if shown. Only "
        "the patient's relatives — never the patient's own conditions.",
        ("family history", "family"),
    ),
)

# Cap text handed to any one extraction call so it can't blow the model's context.
_MAX_EXTRACTION_CHARS = 24000
# Documents at/below this size have no detectable sections scanned per-category over
# the whole text; larger documents only run a category when its section is found.
_SMALL_DOC_CHARS = 6000
# Where document dates almost always live — the header region.
_DATE_SCAN_CHARS = 2500
# Per-call output caps: focused outputs are small; this just bounds a degenerate run.
_CATEGORY_NUM_PREDICT = 2048
_DATE_NUM_PREDICT = 128
# A header line is short; a content line that merely mentions a section word is long.
_MAX_HEADER_LEN = 45


def empty_facts() -> dict:
    return DocumentFacts().model_dump()


def _dob_hint(patient_dob: Optional[str]) -> str:
    if patient_dob:
        return (
            f"The patient's date of birth is {patient_dob}. Never use this date as a "
            "document or result date."
        )
    return "If the text contains a date of birth, never use it as a document or result date."


# ---------------------------------------------------------------------------
# Truncated-JSON salvage (a capped/looping generation can cut off mid-array)
# ---------------------------------------------------------------------------

def _close_truncated_json(s: str) -> Optional[str]:
    """Rewind a cut-off JSON string to the last complete element and close any
    still-open arrays/objects, so completed entries before the truncation survive."""
    stack: list[str] = []
    in_str = False
    esc = False
    safe_len: Optional[int] = None
    safe_stack: Optional[list[str]] = None
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
        elif ch in "]}":
            if not stack:
                break
            stack.pop()
            safe_len, safe_stack = i + 1, list(stack)
        elif ch == ",":
            safe_len, safe_stack = i, list(stack)
    if safe_len is None or safe_stack is None:
        return None
    prefix = s[:safe_len].rstrip().rstrip(",").rstrip()
    return prefix + "".join(reversed(safe_stack))


def _parse(response: str, model: type[BaseModel]) -> Optional[dict]:
    """Parse a structured response into the model dict, salvaging truncation."""
    for candidate in (response, _close_truncated_json(response)):
        if not candidate:
            continue
        try:
            return model.model_validate(json.loads(candidate)).model_dump()
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _dedup(items: list[dict]) -> list[dict]:
    """Drop duplicate items (e.g. from a model that looped on the same entry),
    preserving first-seen order."""
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        key = json.dumps(item, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------

def _header_category(line: str) -> Optional[str]:
    """Classify a line as a section header for one category, or None. Only short
    lines qualify, so a sentence that merely mentions 'allergies' isn't a header."""
    s = line.strip().lower()
    if s.startswith("top "):  # CCDA back-to-top anchor prefix
        s = s[4:].strip()
    s = s.rstrip(":").strip()
    if not s or len(s) > _MAX_HEADER_LEN:
        return None
    for cat in _CATEGORIES:
        if any(kw in s for kw in cat.section_keywords):
            return cat.key
    return None


# A real section needs more than a few words of content; the document's
# table-of-contents lists section names back-to-back with little/no body, so this
# threshold drops those phantom sections.
_MIN_SECTION_BODY = 30


def _detect_sections(text: str) -> dict[str, str]:
    """Split text into per-category section bodies by detecting header lines, with
    the body running until the next header. Many HTML medical exports prefix real
    section headers with a "top" back-to-top anchor; when those are present we
    trust only them, which cleanly skips the table-of-contents and inline phrases
    that merely mention a section word. Same-category sections are concatenated."""
    lines = text.split("\n")
    candidates = [
        (i, cat, line.strip().lower().startswith("top "))
        for i, line in enumerate(lines)
        if (cat := _header_category(line))
    ]
    has_top = any(is_top for *_, is_top in candidates)
    headers = [(i, cat) for i, cat, is_top in candidates if is_top or not has_top]

    bodies: dict[str, list[str]] = {}
    for n, (idx, cat) in enumerate(headers):
        end = headers[n + 1][0] if n + 1 < len(headers) else len(lines)
        body = "\n".join(lines[idx + 1:end]).strip()
        if len(body) >= _MIN_SECTION_BODY:
            bodies.setdefault(cat, []).append(body)
    return {cat: "\n".join(parts) for cat, parts in bodies.items()}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

async def _run_structured(prompt: str, model: type[BaseModel], num_predict: int) -> Optional[dict]:
    cfg = load_config()
    try:
        response = await ai.chat_json(
            [{"role": "user", "content": prompt}],
            schema=model.model_json_schema(),
            model=cfg.clinical_model,
            num_predict=num_predict,
        )
    except Exception:
        _logger.warning("extraction call failed", exc_info=True)
        return None
    return _parse(response, model)


async def _extract_category(scope: str, cat: _Category, patient_dob: Optional[str]) -> list[dict]:
    prompt = prompts.CATEGORY_EXTRACTION_PROMPT.format(
        category_label=cat.label,
        category_key=cat.key,
        category_instructions=cat.instructions,
        dob_hint=_dob_hint(patient_dob),
        document_text=scope[:_MAX_EXTRACTION_CHARS],
    )
    start = time.monotonic()
    result = await _run_structured(prompt, cat.model, _CATEGORY_NUM_PREDICT)
    items = _dedup(result.get(cat.key) or []) if result else []
    # Per-category timing. The UI shows extraction as one combined phase (and the
    # calls may overlap when extraction_concurrency > 1), so this log is how we see
    # which category dominates the cost.
    _logger.info(
        "extraction[%s]: %d item(s) in %.1fs", cat.key, len(items), time.monotonic() - start
    )
    return items


async def _extract_date(text: str, patient_dob: Optional[str]) -> dict:
    prompt = prompts.DOCUMENT_DATE_PROMPT.format(
        dob_hint=_dob_hint(patient_dob),
        document_text=text[:_DATE_SCAN_CHARS],
    )
    result = await _run_structured(prompt, _DocumentDate, _DATE_NUM_PREDICT)
    return result or {}


async def extract_facts(text: str, patient_dob: Optional[str] = None) -> dict:
    """Extract structured clinical facts from one document's text via per-category,
    section-scoped calls. Returns a DocumentFacts dict; never raises — a failed
    category just yields an empty list for that category."""
    if not text or not text.strip():
        return empty_facts()

    text = text[:_MAX_EXTRACTION_CHARS]
    facts = empty_facts()

    # Section-scope only large documents. On a short note a data-bearing line like
    # "Vitals: Wt 315lb" would be misread as a header (excluding its data from the
    # body), so small docs are simply scanned whole-text per category — cheap, and
    # avoids that trap. A large document with no detectable sections also falls
    # back to whole-text per category (correctness over cost; unavoidable).
    sections = {} if len(text) <= _SMALL_DOC_CHARS else _detect_sections(text)

    # Decide which categories to run. A sectioned document with no section for a
    # category simply has none of it, so skip its call entirely.
    pending: list[_Category] = []
    for cat in _CATEGORIES:
        if sections:
            if sections.get(cat.key) is None:
                continue
        pending.append(cat)

    def _scope_for(cat: _Category) -> str:
        return sections[cat.key] if sections else text

    # Surface which path this document took, so we can tell whether the call count
    # is already minimal (one per detected section) or hitting the redundant
    # whole-text scan (every category re-reading the same text).
    _logger.info(
        "extraction plan: %d category call(s) [%s] — %s",
        len(pending),
        ", ".join(c.key for c in pending),
        "sectioned" if sections else f"whole-text scan, {len(text)} chars",
    )

    # The date call and every category call are independent (different category,
    # different section scope), so they *can* overlap. Whether overlapping helps is
    # entirely a function of the host's Ollama: it speeds things up on a GPU with
    # parallel slots, but on a CPU-only Ollama it just thrashes the shared cores and
    # can push a queued call past chat_timeout_seconds. So concurrency is config-gated
    # and defaults to 1 (sequential) — safe on every install — bounded by a semaphore
    # so a call's timeout clock starts only when it actually begins (no queue pile-up).
    concurrency = max(1, load_config().extraction_concurrency)
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(coro):
        async with sem:
            return await coro

    results = await asyncio.gather(
        _bounded(_extract_date(text, patient_dob)),
        *(_bounded(_extract_category(_scope_for(cat), cat, patient_dob)) for cat in pending),
    )

    date = results[0]
    if date.get("document_date"):
        facts["document_date"] = date["document_date"]
        facts["document_date_type"] = date.get("document_date_type")
    for cat, items in zip(pending, results[1:]):
        facts[cat.key] = items

    return facts

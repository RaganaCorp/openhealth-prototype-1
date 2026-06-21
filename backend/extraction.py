"""
Structured clinical fact extraction.

Runs one LLM pass per document (eager, during ingest) over its cleaned text and
returns a normalized DocumentFacts record — problems, medications, labs, vitals,
allergies, procedures, plus the document's clinically-relevant date. Output is
constrained to the schema via Ollama structured outputs. The caller caches the
result to a .facts.json sidecar so re-ingest is free.

canonical_name on lab results is LLM-emitted here; Phase G layers a curated
table on top (table first, this as fallback).
"""

import json
import logging
from typing import Optional

from pydantic import BaseModel, Field

import ai
import prompts
from config import load_config

_logger = logging.getLogger("uvicorn.error")


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


_FACTS_SCHEMA = DocumentFacts.model_json_schema()

# Cap the text handed to the extractor so a very large document can't blow the
# model's context. Per-document chunked extraction can come later if needed.
_MAX_EXTRACTION_CHARS = 24000

# Cap the extractor's output. Legitimate facts for one document are well under
# this; the ceiling exists so a model that degenerates into a repeating-array loop
# fails fast (and gets truncated to something salvageable) instead of generating
# until the request times out.
_EXTRACTION_NUM_PREDICT = 2048


def empty_facts() -> dict:
    return DocumentFacts().model_dump()


def _close_truncated_json(s: str) -> Optional[str]:
    """Best-effort repair of JSON that was cut off mid-generation (e.g. the output
    hit num_predict inside an array). Rewinds to the last point where a complete
    element ended and closes any still-open arrays/objects, so the complete
    entries before the truncation can still be recovered."""
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
            # A comma outside a string always follows a complete element.
            safe_len, safe_stack = i, list(stack)
    if safe_len is None or safe_stack is None:
        return None
    prefix = s[:safe_len].rstrip().rstrip(",").rstrip()
    return prefix + "".join(reversed(safe_stack))


def _parse_facts(response: str) -> dict:
    """Parse a structured-extraction response into DocumentFacts, salvaging a
    truncated response where possible. Returns empty facts if nothing parses."""
    for candidate in (response, _close_truncated_json(response)):
        if not candidate:
            continue
        try:
            return DocumentFacts.model_validate(json.loads(candidate)).model_dump()
        except (json.JSONDecodeError, ValueError):
            continue
    return empty_facts()


async def extract_facts(text: str, patient_dob: Optional[str] = None) -> dict:
    """Extract structured clinical facts from one document's text. Returns a
    DocumentFacts dict; on any failure returns empty facts so ingestion never
    fails on a single document."""
    if not text or not text.strip():
        return empty_facts()

    if patient_dob:
        dob_hint = (
            f"The patient's date of birth is {patient_dob}. Never use this date as "
            "document_date or any result date."
        )
    else:
        dob_hint = (
            "If the document contains a date of birth, never use it as document_date "
            "or any result date."
        )

    prompt = prompts.STRUCTURED_EXTRACTION_PROMPT.format(
        dob_hint=dob_hint,
        document_text=text[:_MAX_EXTRACTION_CHARS],
    )

    cfg = load_config()
    try:
        response = await ai.chat_json(
            [{"role": "user", "content": prompt}],
            schema=_FACTS_SCHEMA,
            model=cfg.clinical_model,
            num_predict=_EXTRACTION_NUM_PREDICT,
        )
    except Exception:
        _logger.warning(
            "structured extraction call failed; storing empty facts for this document",
            exc_info=True,
        )
        return empty_facts()

    facts = _parse_facts(response)
    if facts == empty_facts() and response.strip():
        _logger.warning(
            "structured extraction returned unparseable output (len=%d); storing empty facts",
            len(response),
        )
    return facts

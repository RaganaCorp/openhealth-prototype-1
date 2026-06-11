"""
LLM-based timeline and summary generation.
Both functions receive the full patient.md text and call Ollama once.
JSON responses are parsed from code-fenced LLM output.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import ai

_logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_EXTRACTION_PASS1_PROMPT = """\
You are a clinical data extractor. From the medical records below, extract structured lists of facts.

Return ONLY a JSON object wrapped in a ```json ... ``` code fence. Do not write prose.
Extract only what is explicitly stated in the records — do not infer or add details not present.

{{
  "demographics": {{
    "age": "value or null",
    "sex": "value or null"
  }},
  "conditions": ["condition name", ...],
  "medications": ["Drug name dose frequency", ...],
  "procedures": ["procedure description (date if available)", ...],
  "allergies": ["allergy description", ...],
  "key_labs": ["test name: value (date if available)", ...],
  "concerns": ["clinical concern or notable finding", ...]
}}

If a category has no data in the records, return an empty array (or null for demographics fields).

RECORDS:
{patient_md}
"""

_SUMMARY_PROSE_PROMPT = """\
You are a medical summarization assistant. Write a concise, clinically useful patient summary.

The following structured data was pre-extracted from the patient's records.
Use these facts as the authoritative basis for each section.
Do not contradict or omit items listed below. Do not add facts not present here.

{structured_data}

Caregiver corrections by section (if provided) are listed below.
Treat these as preferred emphasis/wording while staying faithful to the record evidence.

{corrections_block}

Write the summary using exactly these five section headers in this order.

## Overview
3-4 sentences summarizing the patient's overall medical history and current status.

## Active Conditions
List current diagnosed conditions as bullet points.

## Current Medications
List current medications with dose/frequency when available.

## Recent Procedures
List recent procedures, hospitalizations, or key visits.

## Key Concerns
Note risks, trends, missing data, or follow-up needs.

Rules:
- Output only markdown, no preamble and no trailing notes.
- Use exactly the five section headers shown above in the same order.
- Do not repeat sentences or duplicate findings.
- If a section truly has no data (the pre-extracted lists above are empty for that category),
  write "No clear evidence in provided records.".
"""

_SUMMARY_REPAIR_PROMPT = """\
You are editing a noisy clinical summary.
Rewrite it into clean markdown using exactly these headers and this order:

## Overview
## Active Conditions
## Current Medications
## Recent Procedures
## Key Concerns

Rules:
- Keep only clinically relevant information.
- Remove duplication and repetitive text.
- Keep each section concise.
- Do not add facts not present in the draft.
- If a section has no evidence, write "No clear evidence in provided records.".

DRAFT SUMMARY:
{draft_summary}
"""

_JSON_EXTRACTION_PROMPT = """\
You are a medical data extractor. You have been given the raw contents of a JSON file
exported from a healthcare system. The structure and field names may be unfamiliar.
Extract all clinically relevant information — diagnoses, medications, lab results,
procedures, dates, provider notes, and any other health-related data — and present it
as clear, readable plain text. Do not include technical metadata, IDs, or system fields
unless they carry clinical meaning. Preserve all dates and values exactly as they appear.

JSON CONTENT:
{json_content}
"""

_STATE_UPDATE_PROMPT = """\
You are maintaining a compact clinical conversation state for a medical assistant.
Given the latest user message, assistant response, and prior state, update the state.

Prior state:
{prior_state}

Latest user message:
{user_message}

Latest assistant response:
{assistant_response}

Return updated state as JSON wrapped in a ```json ... ``` code fence:
{{
  "rolling_summary": "4-8 sentence summary of the full conversation so far",
  "active_topics": ["short phrase 1", "short phrase 2"],
  "open_questions": ["unresolved question 1"]
}}

Keep the state factual, concise, and grounded in the conversation and records.
Do not invent patient facts.
"""

_GROUNDING_PROMPT = """\
You are a strict grounding verifier.
Given a draft assistant answer and source context, verify that every major claim
is supported by the source material.

Source context:
{context}

Draft answer:
{draft_answer}

Label each major claim as SUPPORTED, PARTIAL, or UNSUPPORTED.
Return a JSON object wrapped in a ```json ... ``` code fence:
{{
  "corrected_answer": "revised answer removing or qualifying unsupported claims",
  "citations": [
    {{"filename": "source_filename.pdf", "excerpt": "relevant excerpt"}}
  ],
  "uncertainty_note": "note about insufficient evidence, or empty string"
}}

Do not add new clinical facts not present in the source context.
"""

_AUTO_TITLE_PROMPT = """\
Generate a short, descriptive title (5-7 words) for a medical conversation that started with:

User: {first_user_message}
Assistant: {first_assistant_response}

Return only the title text, no punctuation, no quotes.
"""

_CLASSIFY_QUERY_PROMPT = """\
Classify this medical query into one of these document types:
lab result, discharge summary, imaging, prescription, clinical note, unknown

Query: {query}

Return only the document type label, nothing else.
"""

SUMMARY_SYSTEM_MESSAGE = (
    "You are a clinical documentation assistant helping authorized caregivers "
    "organize and understand their care recipient's medical records. "
    "All data has been provided by the authorized caregiver for their own use. "
    "You must complete every task fully and output exactly the format requested."
)


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------

_REFUSAL_PHRASES = (
    "i am unable to provide",
    "i cannot process",
    "i can't provide",
    "i cannot summarize",
    "i'm not able to",
    "sensitive and potentially confidential",
    "i cannot assist with",
)


def _is_refusal_response(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _REFUSAL_PHRASES)


def _parse_json_response(text: str, fallback: Any = None) -> Any:
    """Extract JSON from a code-fenced LLM response."""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try parsing the whole response as JSON.
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return fallback


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_markdown_fences(text: str) -> str:
    fenced = re.search(r"```(?:markdown|md)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


def _strip_reasoning_leakage(text: str) -> str:
    cleaned = re.sub(r"<unused\d+>", "", text)

    # Some model responses include planning/instructions before the real summary.
    # Select the last plausible summary block starting at "## Overview".
    starts = [m.start() for m in re.finditer(r"##\s+Overview", cleaned)]
    if not starts:
        return cleaned.strip()

    for start in reversed(starts):
        candidate = cleaned[start:].strip()
        if _has_required_summary_sections(candidate):
            return candidate

    return cleaned[starts[-1]:].strip()


def _normalize_summary_layout(text: str) -> str:
    # Ensure headers start on their own lines.
    text = re.sub(r"\s*(##\s)", r"\n\n\1", text).strip()
    # Collapse excessive blank lines while preserving markdown readability.
    return re.sub(r"\n{3,}", "\n\n", text)


def _extract_sentences(text: str) -> list[str]:
    # Keep a simple splitter; robust enough for repetition detection.
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _trim_chars(text: str, max_chars: int) -> str:
    normalized = _normalize_ws(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "..."


def _has_required_summary_sections(text: str) -> bool:
    required_headers = [
        "## Overview",
        "## Active Conditions",
        "## Current Medications",
        "## Recent Procedures",
        "## Key Concerns",
    ]
    return all(h in text for h in required_headers)


def _has_admin_noise(text: str) -> bool:
    markers = [
        "health care providers",
        "patient contacts",
        "guarantor",
        "address:",
        "powered by",
    ]
    lowered = text.lower()
    hits = sum(1 for marker in markers if marker in lowered)
    return hits >= 2


def _is_repetitive_text(text: str) -> bool:
    sentences = _extract_sentences(text)
    if len(sentences) < 12:
        return False

    norm = [_normalize_ws(s).lower() for s in sentences]
    counts: dict[str, int] = {}
    for s in norm:
        counts[s] = counts.get(s, 0) + 1

    max_repeat = max(counts.values()) if counts else 0
    unique_ratio = len(counts) / max(len(norm), 1)

    # Strong repetition signal from model loops.
    return max_repeat >= 4 or unique_ratio < 0.55


def _dedupe_repeated_sentences(text: str) -> str:
    sentences = _extract_sentences(text)
    seen: set[str] = set()
    kept: list[str] = []
    for sentence in sentences:
        key = _normalize_ws(sentence).lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(sentence)
    return "\n\n".join(kept)


def _prepare_summary_records(patient_md: str) -> str:
    """Extract and compress clinically relevant sections from noisy exports."""
    if not patient_md.strip():
        return ""

    # Strip only synthetic document boundary lines, not the document body.
    cleaned = re.sub(r"(?mi)^={5,}\s*$", " ", patient_md)
    cleaned = re.sub(r"(?mi)^={5,}\s*END:.*$", " ", cleaned)
    cleaned = _normalize_ws(cleaned)

    # Many CCD/portal exports use "top <Section Name>" markers.
    section_iter = list(
        re.finditer(
            r"\btop\s+([A-Za-z][A-Za-z /&\-]{2,60})\s+(.*?)(?=\btop\s+[A-Za-z]|\Z)",
            cleaned,
            re.IGNORECASE,
        )
    )

    if not section_iter:
        return cleaned[:40000]

    keep_sections = {
        "reason for referral",
        "assessments",
        "problems",
        "allergies and adverse reactions",
        "medications",
        "procedures",
        "plan of treatment",
        "results",
        "vital signs",
        "encounters",
        "family history",
        "social history",
        "functional status",
    }

    drop_markers = [
        "health care providers",
        "patient contacts",
        "document details",
        "payers",
        "insurance providers",
        "guarantors",
        "powered by",
    ]

    blocks: list[str] = []
    for match in section_iter:
        title = _normalize_ws(match.group(1))
        body = _normalize_ws(match.group(2))
        if not title or not body:
            continue

        title_lower = title.lower()
        if title_lower not in keep_sections:
            continue
        if any(marker in body.lower() for marker in drop_markers):
            continue

        blocks.append(f"## {title}\n{body[:5000]}")

    prepared = "\n\n".join(blocks).strip()
    if not prepared:
        return cleaned[:40000]

    # Reduce obvious loops from duplicated CCD rows.
    if _is_repetitive_text(prepared):
        prepared = _dedupe_repeated_sentences(prepared)

    return prepared[:50000]


def _unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        normalized = _normalize_ws(item)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _extract_top_section_blob(text: str, section_name: str) -> str:
    pattern = re.compile(
        rf"\btop\s+{re.escape(section_name)}\b\s+(.*?)(?=\btop\s+[A-Za-z]|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(text)
    return _normalize_ws(m.group(1)) if m else ""


def _extract_structured_fallback(patient_md: str) -> dict:
    """Best-effort deterministic extraction when LLM pass-1 returns sparse output.

    Uses only general, structural signals — ICD-coded problems, dosed medications,
    the allergies section, and common lab analytes — never patient- or
    sample-specific term lists, so it generalises across any record.
    """
    empty = {
        "demographics": {"age": None, "sex": None},
        "conditions": [],
        "medications": [],
        "procedures": [],
        "allergies": [],
        "key_labs": [],
        "concerns": [],
    }
    if not patient_md.strip():
        return empty

    cleaned = re.sub(r"(?mi)^={5,}.*$", " ", patient_md)
    cleaned = _normalize_ws(cleaned)

    # Demographics — common CCD phrasings.
    sex = None
    if re.search(r"\bmale\s+sex\b", cleaned, re.IGNORECASE):
        sex = "male"
    elif re.search(r"\bfemale\s+sex\b", cleaned, re.IGNORECASE):
        sex = "female"

    age = None
    age_match = (
        re.search(r"\bage[:\s]+(\d{1,3})\b", cleaned, re.IGNORECASE)
        or re.search(r"\b(\d{1,3})\s*(?:years?\s*old|y/?o)\b", cleaned, re.IGNORECASE)
    )
    if age_match:
        age = age_match.group(1)

    # Conditions — terms paired with an ICD-10 code, e.g. "Hypertension (I10)".
    # ICD-10 Z-codes are administrative/screening/vaccination encounters ("factors
    # influencing health status"), not clinical conditions, so exclude them by code
    # class rather than blocklisting specific phrases.
    condition_sources = " ".join(
        part
        for part in [
            _extract_top_section_blob(cleaned, "Assessments"),
            _extract_top_section_blob(cleaned, "Problems"),
            cleaned,
        ]
        if part
    )
    cond_matches = re.findall(
        r"([A-Z][A-Za-z0-9,\-'/ ]{2,80}?)\s*\(([A-TV-Z][0-9]{1,2}(?:\.[0-9A-Za-z]+)?)\)",
        condition_sources,
    )
    conditions = _unique_keep_order(
        [name.strip(" -") for name, code in cond_matches if not code.upper().startswith("Z")]
    )

    # Medications — a name followed by a dose (and optional frequency). Only dosed
    # mentions are reliable without a drug dictionary; undosed mentions are left to
    # the LLM pass rather than guessed from a hardcoded drug list.
    med_blob = _extract_top_section_blob(cleaned, "Medications") or cleaned
    med_matches = re.findall(
        r"\b([A-Z][A-Za-z0-9\-/]{2,}(?:\s+[A-Z][A-Za-z0-9\-/]{1,}){0,2})\s+"
        r"(\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units))(?:\s+([A-Za-z0-9/.-]{1,20}))?",
        med_blob,
        flags=re.IGNORECASE,
    )
    medications = _unique_keep_order(
        [
            _normalize_ws(f"{name} {dose} {freq}" if freq else f"{name} {dose}")
            for name, dose, freq in med_matches
        ]
    )

    # Allergies — the allergies section, plus explicit "no known allergies" markers.
    allergy_blob = _extract_top_section_blob(cleaned, "Allergies and Adverse Reactions")
    allergies = _unique_keep_order(
        re.findall(
            r"\b(No Known Allergies|No Known Drug Allergies|[A-Z][A-Za-z0-9 ,\-/]{2,40} allergy)\b",
            allergy_blob,
            re.IGNORECASE,
        )
    )

    # Key labs — common analytes paired with a numeric value. These analytes are
    # ordered for most patients, so the list is general clinical knowledge rather
    # than anything specific to one record.
    key_labs = _unique_keep_order(
        re.findall(
            r"\b(?:A1c|HbA1c|glucose|creatinine|BUN|sodium|potassium|chloride|calcium|"
            r"hemoglobin|hematocrit|WBC|platelets?|AST|ALT|bilirubin|albumin|"
            r"cholesterol|LDL|HDL|triglycerides|TSH)\b[^\n]{0,40}?\b\d+(?:\.\d+)?\b",
            cleaned,
            flags=re.IGNORECASE,
        )
    )

    # Procedures and concerns have no reliable structural signal without a curated
    # vocabulary. Rather than guess from a hardcoded list (which only fits one
    # record), leave them to the LLM extraction pass.
    return {
        "demographics": {"age": age, "sex": sex},
        "conditions": conditions[:30],
        "medications": medications[:20],
        "procedures": [],
        "allergies": allergies[:20],
        "key_labs": key_labs[:20],
        "concerns": [],
    }


def _merge_structured(primary: dict, fallback: dict) -> dict:
    """Merge pass-1 output with fallback extraction, preferring non-empty pass-1 fields."""
    p_demo = primary.get("demographics") if isinstance(primary.get("demographics"), dict) else {}
    f_demo = fallback.get("demographics") if isinstance(fallback.get("demographics"), dict) else {}

    def _merged_list(name: str) -> list[str]:
        p = primary.get(name) if isinstance(primary.get(name), list) else []
        f = fallback.get(name) if isinstance(fallback.get(name), list) else []
        return _unique_keep_order((p if p else []) + (f if not p else []))

    return {
        "demographics": {
            "age": p_demo.get("age") if p_demo.get("age") not in [None, "", "unknown"] else f_demo.get("age"),
            "sex": p_demo.get("sex") if p_demo.get("sex") not in [None, "", "unknown"] else f_demo.get("sex"),
        },
        "conditions": _merged_list("conditions"),
        "medications": _merged_list("medications"),
        "procedures": _merged_list("procedures"),
        "allergies": _merged_list("allergies"),
        "key_labs": _merged_list("key_labs"),
        "concerns": _merged_list("concerns"),
    }


def _build_structured_input(data: dict) -> str:
    """Format pass-1 extracted JSON into a readable block for the prose prompt."""
    lines: list[str] = []

    demo = data.get("demographics") or {}
    age = demo.get("age") or "unknown"
    sex = demo.get("sex") or "unknown"
    lines.append(f"**Patient demographics**: age {age}, sex {sex}\n")

    def _section(label: str, items: list) -> None:
        if items:
            lines.append(f"**{label}**:")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")
        else:
            lines.append(f"**{label}**: none recorded\n")

    _section("Known Conditions", data.get("conditions") or [])
    _section("Current Medications", data.get("medications") or [])
    _section("Recent Procedures / Visits", data.get("procedures") or [])
    _section("Allergies", data.get("allergies") or [])
    _section("Key Lab Results", data.get("key_labs") or [])
    _section("Clinical Concerns", data.get("concerns") or [])

    return "\n".join(lines)


def _inject_pass1_fallbacks(summary: str, structured: dict) -> str:
    """
    For any summary section that claims no evidence but pass-1 had items,
    replace the placeholder with the extracted items as bullet points.
    """
    no_evidence = "no clear evidence in provided records"

    replacements = [
        ("## Active Conditions", structured.get("conditions") or []),
        ("## Current Medications", structured.get("medications") or []),
        ("## Recent Procedures", structured.get("procedures") or []),
    ]

    for header, items in replacements:
        if not items:
            continue
        pattern = re.compile(
            rf"({re.escape(header)}\n)(.*?)(?=\n## |\Z)",
            re.IGNORECASE | re.DOTALL,
        )

        def _replace(m: re.Match, _items: list = items) -> str:
            body = m.group(2).strip()
            if no_evidence in body.lower():
                bullet_list = "\n".join(f"- {item}" for item in _items)
                return f"{m.group(1)}{bullet_list}\n\n"
            return m.group(0)

        summary = pattern.sub(_replace, summary)

    return summary


def _ensure_summary_sections(text: str) -> str:
    has_all = _has_required_summary_sections(text)
    if has_all:
        return text

    # Fallback layout when model ignores format instructions.
    return (
        "## Overview\n"
        f"{text.strip()}\n\n"
        "## Active Conditions\n"
        "No clear evidence in provided records.\n\n"
        "## Current Medications\n"
        "No clear evidence in provided records.\n\n"
        "## Recent Procedures\n"
        "No clear evidence in provided records.\n\n"
        "## Key Concerns\n"
        "No clear evidence in provided records."
    )


def _prune_summary_sections(text: str) -> str:
    pattern = re.compile(
        r"(?ms)^## (Overview|Active Conditions|Current Medications|Recent Procedures|Key Concerns)\n(.*?)(?=^## |\Z)"
    )
    matches = pattern.findall(text)
    if not matches:
        return text

    banned_tokens = [
        "provider",
        "address",
        "guarantor",
        "phone",
        "email",
        "pharmacy",
        "powered by",
        "staff",
    ]

    section_order = [
        "Overview",
        "Active Conditions",
        "Current Medications",
        "Recent Procedures",
        "Key Concerns",
    ]
    sections: dict[str, str] = {name: "" for name in section_order}
    for name, content in matches:
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        kept: list[str] = []
        for line in lines:
            lowered = line.lower()
            if any(tok in lowered for tok in banned_tokens):
                continue
            if len(line) > 500:
                continue
            if line in {"1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "10."}:
                continue
            kept.append(line)

        max_lines = 6 if name == "Overview" else 8
        kept = kept[:max_lines]
        if not kept:
            sections[name] = "No clear evidence in provided records."
        else:
            sections[name] = "\n".join(kept)

    rebuilt: list[str] = []
    for name in section_order:
        rebuilt.append(f"## {name}")
        rebuilt.append(sections[name])
        rebuilt.append("")
    return "\n".join(rebuilt).strip()


def _sanitize_summary_output(text: str) -> str:
    cleaned = _strip_markdown_fences(text)
    cleaned = _strip_reasoning_leakage(cleaned)
    if _is_repetitive_text(cleaned):
        cleaned = _dedupe_repeated_sentences(cleaned)
    cleaned = _ensure_summary_sections(cleaned)
    cleaned = _normalize_summary_layout(cleaned)
    cleaned = _prune_summary_sections(cleaned)
    # Guard against pathological long outputs from repetition loops.
    return cleaned[:12000].strip()


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def _build_corrections_block(summary_overrides: dict | None) -> str:
    if not summary_overrides:
        return "No section-level caregiver corrections provided."

    rows = [
        ("Active Conditions", str(summary_overrides.get("active_conditions", "")).strip()),
        ("Current Medications", str(summary_overrides.get("current_medications", "")).strip()),
        ("Recent Procedures", str(summary_overrides.get("recent_procedures", "")).strip()),
        ("Key Concerns", str(summary_overrides.get("key_concerns", "")).strip()),
    ]
    if not any(value for _, value in rows):
        return "No section-level caregiver corrections provided."

    return "\n".join([
        f"- {label}: {value if value else '(none)'}"
        for label, value in rows
    ])


def build_extraction_pass1_prompt(patient_md: str) -> str:
    return _EXTRACTION_PASS1_PROMPT.format(patient_md=patient_md)


def build_summary_prose_prompt(structured_data: dict | str, summary_overrides: dict | None = None) -> str:
    structured_block = (
        structured_data
        if isinstance(structured_data, str)
        else _build_structured_input(structured_data)
    )
    corrections_block = _build_corrections_block(summary_overrides)
    return _SUMMARY_PROSE_PROMPT.format(
        structured_data=structured_block,
        corrections_block=corrections_block,
    )


def build_summary_messages(structured_data: dict | str, summary_overrides: dict | None = None) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SUMMARY_SYSTEM_MESSAGE},
        {"role": "user", "content": build_summary_prose_prompt(structured_data, summary_overrides)},
    ]


def build_grounding_prompt(draft_answer: str, context: str, context_limit: int = 60000) -> str:
    return _GROUNDING_PROMPT.format(
        context=context[:context_limit],
        draft_answer=draft_answer,
    )


def build_json_extraction_prompt(json_content: str) -> str:
    return _JSON_EXTRACTION_PROMPT.format(json_content=json_content)


async def generate_summary(
    patient_md: str,
    summary_overrides: dict | None = None,
    model: str | None = None,
) -> str:
    """Two-pass LLM summary: Pass 1 extracts structured data, Pass 2 generates prose."""
    if not patient_md.strip():
        return ""

    _summary_messages_prefix = [{"role": "system", "content": SUMMARY_SYSTEM_MESSAGE}]

    # Pass 1 — structured extraction from raw records.
    # Use raw patient_md (not pre-filtered) so all clinical sections reach the model.
    extract_prompt = build_extraction_pass1_prompt(patient_md[:30000])
    extract_response = await ai.chat_complete(
        _summary_messages_prefix + [{"role": "user", "content": extract_prompt}],
        model=model,
    )
    if _is_refusal_response(extract_response):
        _logger.warning("summary extraction pass returned a refusal; skipping LLM extraction")
        extract_response = "{}"
    structured = _parse_json_response(extract_response, fallback={})
    if not isinstance(structured, dict):
        structured = {}
    fallback_structured = _extract_structured_fallback(patient_md)
    structured = _merge_structured(structured, fallback_structured)
    _logger.info(
        "summary extracted+fallback: conditions=%d meds=%d procedures=%d",
        len(structured.get("conditions") or []),
        len(structured.get("medications") or []),
        len(structured.get("procedures") or []),
    )

    # Pass 2 — prose generation from pre-extracted facts.
    prose_prompt = build_summary_prose_prompt(structured, summary_overrides)
    prose_response = await ai.chat_complete(
        _summary_messages_prefix + [{"role": "user", "content": prose_prompt}],
        model=model,
    )
    # Detect refusal from the prose pass; fall back to injecting pass-1 data directly.
    if _is_refusal_response(prose_response):
        _logger.warning("summary prose pass returned a refusal; using pass-1 fallback only")
        prose_response = ""
    summary = _sanitize_summary_output(prose_response)

    # Fallback: if LLM dropped a section that had pass-1 data, inject items directly.
    summary = _inject_pass1_fallbacks(summary, structured)

    return summary


async def extract_json_document(json_content: str) -> str:
    """LLM-assisted plain-text extraction from provider JSON exports."""
    prompt = build_json_extraction_prompt(json_content)
    return await ai.chat_complete([{"role": "user", "content": prompt}])


async def update_conversation_state(
    prior_state: dict | None,
    user_message: str,
    assistant_response: str,
) -> dict:
    """Refresh conversation state with lightweight local heuristics."""
    prior = prior_state if isinstance(prior_state, dict) else {}
    prior_summary = str(prior.get("rolling_summary", "") or "")

    turn_summary = (
        f"User asked: {_trim_chars(user_message, 180)} "
        f"Assistant answered: {_trim_chars(assistant_response, 260)}"
    )
    rolling_summary = _normalize_ws(f"{prior_summary} {turn_summary}").strip()
    rolling_summary = _trim_chars(rolling_summary, 1800)

    prior_topics = prior.get("active_topics", [])
    active_topics: list[str] = []
    if isinstance(prior_topics, list):
        for topic in prior_topics:
            if isinstance(topic, str) and topic.strip():
                active_topics.append(_trim_chars(topic, 80))

    doc_type = classify_query(user_message)
    if doc_type != "unknown":
        active_topics.append(doc_type)
    active_topics = _unique_keep_order(active_topics)[-6:]

    prior_open = prior.get("open_questions", [])
    open_questions: list[str] = []
    if isinstance(prior_open, list):
        for question in prior_open:
            if isinstance(question, str) and question.strip():
                open_questions.append(_trim_chars(question, 180))

    if "?" in user_message:
        open_questions.append(_trim_chars(user_message, 180))
    open_questions = _unique_keep_order(open_questions)[-4:]

    lowered_answer = assistant_response.lower()
    resolved_markers = [
        "no clear evidence",
        "insufficient evidence",
        "not enough information",
        "uncertain",
    ]
    if not any(marker in lowered_answer for marker in resolved_markers):
        # Keep only the most recent unresolved question if the assistant provided a direct answer.
        open_questions = open_questions[-1:]

    now = datetime.now(timezone.utc).isoformat()
    return {
        "rolling_summary": rolling_summary,
        "active_topics": active_topics,
        "open_questions": open_questions,
        "last_updated_at": now,
    }


async def verify_grounding(
    draft_answer: str,
    context: str,
    context_limit: int = 60000,
    model: str | None = None,
) -> dict:
    """
    Verify grounding of draft_answer against context.
    Returns { corrected_answer, citations, uncertainty_note }.
    Falls back to original answer if parsing fails.
    """
    prompt = build_grounding_prompt(draft_answer, context, context_limit)
    response = await ai.chat_complete([{"role": "user", "content": prompt}], model=model)
    parsed = _parse_json_response(response, fallback=None)
    if parsed and isinstance(parsed, dict) and "corrected_answer" in parsed:
        return {
            "corrected_answer": parsed.get("corrected_answer", draft_answer),
            "citations": parsed.get("citations", []),
            "uncertainty_note": parsed.get("uncertainty_note", ""),
        }
    return {
        "corrected_answer": draft_answer,
        "citations": [],
        "uncertainty_note": "",
    }


async def generate_session_title(
    first_user_message: str,
    first_assistant_response: str,
    model: str | None = None,
) -> str:
    prompt = _AUTO_TITLE_PROMPT.format(
        first_user_message=first_user_message,
        first_assistant_response=first_assistant_response[:500],
    )
    title = await ai.chat_complete([{"role": "user", "content": prompt}], model=model)
    return title.strip().strip('"').strip("'")[:80]


def classify_query(query: str) -> str:
    """
    Keyword-based query classifier — no LLM needed.
    Returns a document_type string used to guide chunk retrieval.
    """
    q = query.lower()
    if any(w in q for w in ["lab", "result", "blood", "panel", "urine", "culture", "pathology", "test"]):
        return "lab result"
    if any(w in q for w in ["discharge", "hospital", "admission", "hospitalized"]):
        return "discharge summary"
    if any(w in q for w in ["imaging", "mri", "ct", "x-ray", "xray", "ultrasound", "scan", "radiology"]):
        return "imaging"
    if any(w in q for w in ["medication", "drug", "prescription", "dosage", "dose", "pharmacy", "rx"]):
        return "prescription"
    if any(w in q for w in ["note", "visit", "encounter", "assessment", "soap", "progress"]):
        return "clinical note"
    return "unknown"

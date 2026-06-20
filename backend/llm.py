"""
Higher-level LLM helpers built on top of the ai.py provider client: provider
JSON document extraction, grounding verification, conversation-state updates,
session titles, and query classification. Functions that call the model do so
via ai.chat_complete; JSON responses are parsed from code-fenced LLM output.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import ai
import prompts

_logger = logging.getLogger("uvicorn.error")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _trim_chars(text: str, max_chars: int) -> str:
    normalized = _normalize_ws(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "..."


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


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def build_grounding_prompt(draft_answer: str, context: str, context_limit: int = 60000) -> str:
    return prompts.GROUNDING_PROMPT.format(
        context=context[:context_limit],
        draft_answer=draft_answer,
    )


def build_json_extraction_prompt(json_content: str) -> str:
    return prompts.JSON_EXTRACTION_PROMPT.format(json_content=json_content)


async def extract_json_document(json_content: str) -> str:
    """LLM-assisted plain-text extraction from provider JSON exports."""
    extraction_prompt = build_json_extraction_prompt(json_content)
    return await ai.chat_complete([{"role": "user", "content": extraction_prompt}])


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

    current_question = _trim_chars(user_message, 180) if "?" in user_message else None
    if current_question:
        open_questions.append(current_question)
    open_questions = _unique_keep_order(open_questions)

    lowered_answer = assistant_response.lower()
    unresolved_markers = [
        "no clear evidence",
        "insufficient evidence",
        "not enough information",
        "uncertain",
    ]
    answer_is_direct = not any(marker in lowered_answer for marker in unresolved_markers)
    if answer_is_direct and current_question:
        # A direct answer resolves only THIS turn's question. Drop just that one
        # and keep the rest — previously the code truncated to the single most
        # recent question, silently discarding genuinely unresolved ones whenever
        # the model phrased its hedge differently than the marker list expects.
        open_questions = [q for q in open_questions if q != current_question]
    open_questions = open_questions[-4:]

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

    Fails CLOSED: if the verifier output can't be parsed, or comes back with an
    empty corrected answer, we keep the draft but attach a non-empty
    uncertainty_note. This matters for a clinical tool — a verification step that
    silently passes the unverified draft through (empty note) is indistinguishable
    from one that actually confirmed grounding, so callers must be able to tell
    that the check did not complete.
    """
    grounding_prompt = build_grounding_prompt(draft_answer, context, context_limit)
    response = await ai.chat_complete([{"role": "user", "content": grounding_prompt}], model=model)
    parsed = _parse_json_response(response, fallback=None)
    if parsed and isinstance(parsed, dict) and "corrected_answer" in parsed:
        corrected = parsed.get("corrected_answer", draft_answer)
        uncertainty_note = parsed.get("uncertainty_note", "")
        if not (isinstance(corrected, str) and corrected.strip()):
            # The verifier returned an empty answer — don't surface a blank
            # response to the user; keep the draft and flag that it's unverified.
            corrected = draft_answer
            uncertainty_note = (
                uncertainty_note
                or "Automated grounding check returned no answer; response could not be verified against the record."
            )
        return {
            "corrected_answer": corrected,
            "citations": parsed.get("citations", []),
            "uncertainty_note": uncertainty_note,
        }
    return {
        "corrected_answer": draft_answer,
        "citations": [],
        "uncertainty_note": "Automated grounding check could not be completed; this response was not verified against the record.",
    }


async def generate_session_title(
    first_user_message: str,
    first_assistant_response: str,
    model: str | None = None,
) -> str:
    title_prompt = prompts.AUTO_TITLE_PROMPT.format(
        first_user_message=first_user_message,
        first_assistant_response=first_assistant_response[:500],
    )
    title = await ai.chat_complete([{"role": "user", "content": title_prompt}], model=model)
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

"""
Higher-level LLM helpers built on top of the ai.py provider client: provider
JSON document extraction, grounding verification, rolling conversation
summarization, and session titles.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

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


class _ConversationState(BaseModel):
    rolling_summary: str = ""
    active_topics: list[str] = []
    open_questions: list[str] = []


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
    """Refresh the rolling conversation summary via a small structured LLM call (on
    the fast chat model). Falls back to the prior state on any failure so a chat
    turn never breaks on the summary step."""
    prior = prior_state if isinstance(prior_state, dict) else {}
    prompt = prompts.CONVERSATION_SUMMARY_PROMPT.format(
        prior_summary=(prior.get("rolling_summary") or "(none)"),
        prior_open_questions="; ".join(prior.get("open_questions") or []) or "(none)",
        user_message=user_message,
        assistant_response=assistant_response[:1200],
    )
    state = {
        "rolling_summary": prior.get("rolling_summary") or "",
        "active_topics": list(prior.get("active_topics") or []),
        "open_questions": list(prior.get("open_questions") or []),
    }
    try:
        response = await ai.chat_json(
            [{"role": "user", "content": prompt}],
            schema=_ConversationState.model_json_schema(),
            num_predict=512,
        )
        state = _ConversationState.model_validate(json.loads(response)).model_dump()
    except Exception:
        _logger.warning("conversation summary update failed; keeping prior state", exc_info=True)

    state["active_topics"] = [t for t in state.get("active_topics", []) if isinstance(t, str) and t.strip()][:8]
    state["open_questions"] = [q for q in state.get("open_questions", []) if isinstance(q, str) and q.strip()][:6]
    state["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    return state


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

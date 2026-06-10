"""
Ollama HTTP client — embeddings, chat completions, model listing.
All calls use httpx.AsyncClient with appropriate timeouts.
"""

import logging
import json
import re
from time import perf_counter
from typing import Any

import httpx

from config import load_config

_logger = logging.getLogger("uvicorn.error")


def _log_ollama_call_start(endpoint: str, model: str | None = None) -> float:
    if model:
        _logger.info("starting ollama call endpoint=%s model=%s", endpoint, model)
    else:
        _logger.info("starting ollama call endpoint=%s", endpoint)
    return perf_counter()


def _log_ollama_call_end(started_at: float, endpoint: str, status_code: int) -> None:
    duration_ms = int((perf_counter() - started_at) * 1000)
    _logger.info(
        "got response, duration %d ms endpoint=%s status=%d",
        duration_ms,
        endpoint,
        status_code,
    )


def _truncate_for_log(value: Any, max_chars: int = 12000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=True)
    except Exception:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... [truncated {len(text) - max_chars} chars]"


def _estimate_tokens_from_text(text: str) -> int:
    # Rough fallback when provider token usage is unavailable.
    return max(1, len(text) // 4)


def _estimate_prompt_tokens(messages: list[dict[str, str]]) -> int:
    joined = "\n".join(m.get("content", "") for m in messages)
    return _estimate_tokens_from_text(joined)


def _extract_chat_token_usage(
    response_payload: dict[str, Any], messages: list[dict[str, str]], content: str
) -> tuple[int, int, int, bool]:
    prompt_eval_count = response_payload.get("prompt_eval_count")
    eval_count = response_payload.get("eval_count")
    if isinstance(prompt_eval_count, int) and isinstance(eval_count, int):
        total = prompt_eval_count + eval_count
        return prompt_eval_count, eval_count, total, False

    prompt_tokens = _estimate_prompt_tokens(messages)
    completion_tokens = _estimate_tokens_from_text(content)
    return prompt_tokens, completion_tokens, prompt_tokens + completion_tokens, True


def _model_variants(name: str) -> set[str]:
    base = name.split(":", 1)[0]
    return {name, base}


_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.IGNORECASE | re.DOTALL)


def _split_thinking(content: str, native_thinking: str | None) -> tuple[str, str | None]:
    """Separate model reasoning from the answer body.

    Captures Ollama's native ``message.thinking`` field (returned by
    thinking-capable models) and any inline ``<think>...</think>`` blocks, and
    returns ``(clean_answer, thinking_text_or_None)`` with the reasoning removed
    from the answer.
    """
    parts: list[str] = []
    if native_thinking and native_thinking.strip():
        parts.append(native_thinking.strip())
    for block in _THINK_BLOCK_RE.findall(content):
        if block.strip():
            parts.append(block.strip())

    answer = _THINK_BLOCK_RE.sub("", content)
    # Drop gemma-style reasoning sentinel tokens that occasionally leak.
    answer = re.sub(r"<unused\d+>", "", answer).strip()
    thinking = "\n\n".join(parts).strip() or None
    return answer, thinking


def _extract_ollama_error(exc: httpx.HTTPStatusError) -> str:
    try:
        data = exc.response.json()
        detail = data.get("error") if isinstance(data, dict) else None
        if isinstance(detail, str) and detail.strip():
            return detail
    except Exception:
        pass
    return exc.response.text.strip() or str(exc)


async def ensure_ollama_available() -> None:
    cfg = load_config()
    endpoint = "/api/tags"
    try:
        async with httpx.AsyncClient() as client:
            started_at = _log_ollama_call_start(endpoint)
            resp = await client.get(
                f"{cfg.ollama_base_url}/api/tags",
                timeout=cfg.meta_timeout_seconds,
            )
            resp.raise_for_status()
            _log_ollama_call_end(started_at, endpoint, resp.status_code)
            payload = resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Unable to reach Ollama at {cfg.ollama_base_url}. "
            "Start Ollama before launching the backend."
        ) from exc

    models = payload.get("models", []) if isinstance(payload, dict) else []
    installed: set[str] = set()
    for model in models:
        if isinstance(model, dict):
            name = model.get("name")
            if isinstance(name, str):
                installed.update(_model_variants(name))

    required_models = {
        cfg.chat_model,
        cfg.clinical_model,
        cfg.summary_model,
        cfg.verification_model,
        cfg.embedding_model,
    }
    missing: list[str] = [name for name in sorted(required_models) if name not in installed]

    if missing:
        missing_cmds = "\n".join(f"ollama pull {m}" for m in missing)
        raise RuntimeError(
            "Ollama is running but required models are missing: "
            f"{', '.join(missing)}.\n"
            f"Install them with:\n{missing_cmds}"
        )


async def embed(text: str) -> list[float]:
    cfg = load_config()
    endpoint = "/api/embeddings"
    async with httpx.AsyncClient() as client:
        try:
            started_at = _log_ollama_call_start(endpoint, cfg.embedding_model)
            resp = await client.post(
                f"{cfg.ollama_base_url}/api/embeddings",
                json={"model": cfg.embedding_model, "prompt": text},
                timeout=cfg.embed_timeout_seconds,
            )
            resp.raise_for_status()
            _log_ollama_call_end(started_at, endpoint, resp.status_code)
        except httpx.HTTPStatusError as exc:
            detail = _extract_ollama_error(exc)
            _logger.error(
                "ollama embeddings http error endpoint=%s model=%s detail=%s payload=%s",
                endpoint,
                cfg.embedding_model,
                detail,
                _truncate_for_log({"model": cfg.embedding_model, "prompt": text}),
            )
            raise RuntimeError(
                f"Ollama embeddings request failed for model '{cfg.embedding_model}': {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            _logger.exception(
                "ollama embeddings transport error endpoint=%s model=%s payload=%s",
                endpoint,
                cfg.embedding_model,
                _truncate_for_log({"model": cfg.embedding_model, "prompt": text}),
            )
            raise RuntimeError(
                f"Ollama embeddings transport failure for model '{cfg.embedding_model}': {exc}"
            ) from exc
        return resp.json()["embedding"]


async def _chat_request(
    messages: list[dict[str, str]],
    model: str | None,
    num_ctx: int | None,
    think: bool,
) -> dict[str, Any]:
    """POST to Ollama /api/chat and return the raw response payload.

    When ``think`` is set we ask Ollama for separate reasoning output; models or
    Ollama builds that don't support the flag reject it, so we transparently
    retry once without it rather than failing the chat.
    """
    cfg = load_config()
    effective_model = model or cfg.chat_model
    endpoint = "/api/chat"
    request_payload: dict[str, Any] = {"model": effective_model, "messages": messages, "stream": False}
    if num_ctx and num_ctx > 0:
        request_payload["options"] = {"num_ctx": num_ctx}
    if think:
        request_payload["think"] = True
    _logger.info(
        "ollama chat request endpoint=%s model=%s payload=%s",
        endpoint,
        effective_model,
        _truncate_for_log(request_payload),
    )
    async with httpx.AsyncClient() as client:
        while True:
            try:
                started_at = _log_ollama_call_start(endpoint, effective_model)
                resp = await client.post(
                    f"{cfg.ollama_base_url}/api/chat",
                    json=request_payload,
                    timeout=cfg.chat_timeout_seconds,
                )
                resp.raise_for_status()
                _log_ollama_call_end(started_at, endpoint, resp.status_code)
            except httpx.HTTPStatusError as exc:
                detail = _extract_ollama_error(exc)
                # If the model can't do separate thinking, drop the flag and retry.
                if request_payload.pop("think", None) and "think" in detail.lower():
                    _logger.info(
                        "ollama chat: model does not support think flag, retrying without it model=%s",
                        effective_model,
                    )
                    continue
                _logger.error(
                    "ollama chat http error endpoint=%s model=%s detail=%s payload=%s",
                    endpoint,
                    effective_model,
                    detail,
                    _truncate_for_log(request_payload),
                )
                raise RuntimeError(
                    f"Ollama chat request failed for model '{effective_model}': {detail}"
                ) from exc
            except httpx.HTTPError as exc:
                _logger.exception(
                    "ollama chat transport error endpoint=%s model=%s payload=%s",
                    endpoint,
                    effective_model,
                    _truncate_for_log(request_payload),
                )
                raise RuntimeError(
                    f"Ollama chat transport failure for model '{effective_model}': {exc}"
                ) from exc

            response_payload = resp.json()
            content = response_payload.get("message", {}).get("content", "")
            prompt_tokens, completion_tokens, total_tokens, estimated = _extract_chat_token_usage(
                response_payload,
                messages,
                content,
            )
            _logger.info(
                "ollama chat response endpoint=%s model=%s prompt_tokens=%d completion_tokens=%d total_tokens=%d usage_estimated=%s payload=%s",
                endpoint,
                effective_model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                estimated,
                _truncate_for_log(response_payload),
            )
            return response_payload


async def chat_complete(
    messages: list[dict[str, str]],
    model: str | None = None,
    num_ctx: int | None = None,
) -> str:
    response_payload = await _chat_request(messages, model, num_ctx, think=False)
    return response_payload.get("message", {}).get("content", "")


async def chat_complete_with_thinking(
    messages: list[dict[str, str]],
    model: str | None = None,
    num_ctx: int | None = None,
) -> tuple[str, str | None]:
    """Like chat_complete, but returns ``(answer, thinking)`` with the model's
    reasoning separated out of the answer body."""
    response_payload = await _chat_request(messages, model, num_ctx, think=True)
    message = response_payload.get("message", {}) or {}
    return _split_thinking(message.get("content", ""), message.get("thinking"))


async def list_models() -> list[str]:
    cfg = load_config()
    endpoint = "/api/tags"
    async with httpx.AsyncClient() as client:
        started_at = _log_ollama_call_start(endpoint)
        resp = await client.get(
            f"{cfg.ollama_base_url}/api/tags",
            timeout=cfg.meta_timeout_seconds,
        )
        resp.raise_for_status()
        _log_ollama_call_end(started_at, endpoint, resp.status_code)
        return [m["name"] for m in resp.json().get("models", [])]

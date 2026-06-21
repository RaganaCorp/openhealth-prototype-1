"""
Ollama HTTP client — embeddings, chat completions, model listing.
All calls use httpx.AsyncClient with appropriate timeouts.
"""

import asyncio
import base64
import logging
import json
import re
from time import perf_counter
from typing import Any, Literal

import httpx

from config import LOG_PAYLOADS, load_config

_logger = logging.getLogger("uvicorn.error")


# A single shared AsyncClient is reused across all Ollama calls so HTTP
# connections are pooled and kept alive, instead of opening (and tearing down) a
# fresh connection on every embed/chat request. Per-request timeouts are still
# passed explicitly, so the shared client carries no default timeout of its own.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=None)
    return _client


async def aclose_client() -> None:
    """Close the shared client. Call on application shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


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


def _loggable_payload(value: Any) -> str:
    """Render a payload for logging, redacting PHI-bearing content unless payload
    logging is explicitly enabled. Reports the size so logs stay useful for
    debugging without exposing patient data."""
    if LOG_PAYLOADS:
        return _truncate_for_log(value)
    try:
        size = len(json.dumps(value, ensure_ascii=True))
    except Exception:
        size = -1
    return f"<redacted {size} chars; set OPENHEALTH_LOG_PAYLOADS=1 to log payloads>"


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
        client = _get_client()
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
        cfg.verification_model,
        cfg.vision_model,
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


def _validate_embedding(vector: Any, model: str) -> None:
    """Reject a malformed embedding before it reaches ChromaDB. A bad vector would
    otherwise corrupt the collection and break every later query for that patient."""
    if not isinstance(vector, list) or not vector or not all(
        isinstance(v, (int, float)) for v in vector
    ):
        raise RuntimeError(
            f"Ollama returned an invalid embedding for model '{model}': "
            f"expected a non-empty numeric vector, got {type(vector).__name__}"
        )


EmbedTask = Literal["query", "document"]

# Per-model retrieval instruction prefixes. nomic-embed-text is trained to be
# prompted with these task prefixes, and using them improves query↔document
# retrieval. The prefix is model-specific (bge/mxbai/qwen want different formats),
# so we only prefix models we have a known mapping for and pass others through raw
# rather than risk applying the wrong instruction.
_EMBED_PREFIXES: dict[str, dict[EmbedTask, str]] = {
    "nomic-embed-text": {"query": "search_query: ", "document": "search_document: "},
}


def _embed_prefix(model: str, task: EmbedTask) -> str:
    base = model.split(":", 1)[0]  # drop any :tag (e.g. ":latest")
    return _EMBED_PREFIXES.get(base, {}).get(task, "")


async def embed_many(texts: list[str], task: EmbedTask) -> list[list[float]]:
    """Embed multiple texts in a single Ollama call. Returns one vector per input,
    in order. Batching avoids a separate HTTP round-trip per chunk during ingestion.

    ``task`` selects the retrieval instruction prefix ("query" for search inputs,
    "document" for stored content); the prefix is applied to the embedding input
    only and never to the text the caller stores."""
    if not texts:
        return []
    cfg = load_config()
    endpoint = "/api/embed"
    prefix = _embed_prefix(cfg.embedding_model, task)
    inputs = [prefix + t for t in texts] if prefix else texts
    request_payload = {
        "model": cfg.embedding_model,
        "input": inputs,
        "keep_alive": cfg.ollama_keep_alive,
    }
    try:
        client = _get_client()
        started_at = _log_ollama_call_start(endpoint, cfg.embedding_model)
        resp = await client.post(
            f"{cfg.ollama_base_url}/api/embed",
            json=request_payload,
            timeout=cfg.embed_timeout_seconds,
        )
        resp.raise_for_status()
        _log_ollama_call_end(started_at, endpoint, resp.status_code)
    except httpx.HTTPStatusError as exc:
        detail = _extract_ollama_error(exc)
        _logger.error(
            "ollama embed http error endpoint=%s model=%s count=%d detail=%s",
            endpoint,
            cfg.embedding_model,
            len(texts),
            detail,
        )
        raise RuntimeError(
            f"Ollama embed request failed for model '{cfg.embedding_model}': {detail}"
        ) from exc
    except httpx.HTTPError as exc:
        _logger.exception(
            "ollama embed transport error endpoint=%s model=%s count=%d",
            endpoint,
            cfg.embedding_model,
            len(texts),
        )
        raise RuntimeError(
            f"Ollama embed transport failure for model '{cfg.embedding_model}': {exc}"
        ) from exc

    payload = resp.json()
    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise RuntimeError(
            f"Ollama returned {len(embeddings) if isinstance(embeddings, list) else 'no'} "
            f"embeddings for {len(texts)} inputs (model '{cfg.embedding_model}')"
        )
    for vector in embeddings:
        _validate_embedding(vector, cfg.embedding_model)
    return embeddings


async def embed(text: str, task: EmbedTask) -> list[float]:
    """Embed a single text. ``task`` is "query" for a search input or "document"
    for stored content (see embed_many)."""
    vectors = await embed_many([text], task)
    return vectors[0]


async def _chat_request(
    messages: list[dict[str, str]],
    model: str | None,
    num_ctx: int | None,
    think: bool,
    fmt: dict | str | None = None,
    temperature: float | None = None,
    num_predict: int | None = None,
) -> dict[str, Any]:
    """POST to Ollama /api/chat and return the raw response payload.

    When ``think`` is set we ask Ollama for separate reasoning output; models or
    Ollama builds that don't support the flag reject it, so we transparently
    retry once without it rather than failing the chat.

    ``fmt`` constrains the output via Ollama's structured-output support — pass a
    JSON schema dict (or the string ``"json"``) to get schema-valid JSON back in a
    single call instead of parsing it out of free text. ``temperature`` sets the
    sampling temperature (e.g. 0 for deterministic extraction/verification).
    """
    cfg = load_config()
    effective_model = model or cfg.chat_model
    endpoint = "/api/chat"
    request_payload: dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "stream": False,
        "keep_alive": cfg.ollama_keep_alive,
    }
    options: dict[str, Any] = {}
    if num_ctx and num_ctx > 0:
        options["num_ctx"] = num_ctx
    if temperature is not None:
        options["temperature"] = temperature
    if num_predict is not None:
        options["num_predict"] = num_predict
    if options:
        request_payload["options"] = options
    if fmt is not None:
        request_payload["format"] = fmt
    if think:
        request_payload["think"] = True
    _logger.info(
        "ollama chat request endpoint=%s model=%s payload=%s",
        endpoint,
        effective_model,
        _loggable_payload(request_payload),
    )
    client = _get_client()
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
                _loggable_payload(request_payload),
            )
            raise RuntimeError(
                f"Ollama chat request failed for model '{effective_model}': {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            _logger.exception(
                "ollama chat transport error endpoint=%s model=%s payload=%s",
                endpoint,
                effective_model,
                _loggable_payload(request_payload),
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
            _loggable_payload(response_payload),
        )
        return response_payload


async def chat_complete(
    messages: list[dict[str, str]],
    model: str | None = None,
    num_ctx: int | None = None,
) -> str:
    response_payload = await _chat_request(messages, model, num_ctx, think=False)
    return response_payload.get("message", {}).get("content", "")


async def chat_json(
    messages: list[dict[str, str]],
    schema: dict | None = None,
    model: str | None = None,
    num_ctx: int | None = None,
    temperature: float = 0.0,
    num_predict: int | None = None,
) -> str:
    """Chat with structured output. Returns the raw JSON string produced by the
    model; pass a JSON schema to enforce a shape, or omit it for free-form JSON.
    Defaults to temperature 0 since callers want deterministic, parseable output.
    ``num_predict`` caps output length so a degenerate (looping) generation fails
    fast instead of running until the request times out."""
    response_payload = await _chat_request(
        messages,
        model,
        num_ctx,
        think=False,
        fmt=schema if schema is not None else "json",
        temperature=temperature,
        num_predict=num_predict,
    )
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


# Upper bound on tokens for a single page transcription. Generous for a dense
# page, but caps a model that degenerates into a repetition loop so it fails fast
# instead of generating until the request times out.
_VISION_NUM_PREDICT = 4096


async def chat_vision(
    prompt: str,
    images: list[bytes],
    model: str | None = None,
    num_ctx: int | None = None,
    num_predict: int | None = _VISION_NUM_PREDICT,
) -> str:
    """Send a prompt plus one or more images to a multimodal model and return the
    text response. Used to transcribe scanned/image medical documents."""
    encoded = [base64.b64encode(img).decode("ascii") for img in images]
    messages = [{"role": "user", "content": prompt, "images": encoded}]
    response_payload = await _chat_request(
        messages, model, num_ctx, think=False, num_predict=num_predict
    )
    return response_payload.get("message", {}).get("content", "")


_EMBEDDING_NAME_RE = re.compile(r"embed|bge|minilm|gte", re.IGNORECASE)


def _infer_capabilities(name: str) -> list[str]:
    """Fallback classification when Ollama doesn't report capabilities (older
    builds). Vision can't be inferred from a name, so name-only models are treated
    as embedding or completion."""
    return ["embedding"] if _EMBEDDING_NAME_RE.search(name) else ["completion"]


async def _model_capabilities(name: str) -> list[str]:
    """Capabilities for one model from /api/show (e.g. ["completion", "vision"],
    ["embedding"]). Falls back to a name heuristic if the call fails or the build
    doesn't report capabilities."""
    cfg = load_config()
    try:
        client = _get_client()
        resp = await client.post(
            f"{cfg.ollama_base_url}/api/show",
            json={"model": name},
            timeout=cfg.meta_timeout_seconds,
        )
        resp.raise_for_status()
        caps = resp.json().get("capabilities")
        if isinstance(caps, list):
            caps = [c for c in caps if isinstance(c, str)]
            if caps:
                return caps
    except httpx.HTTPError:
        _logger.warning("could not fetch capabilities for model %s; inferring from name", name)
    return _infer_capabilities(name)


async def list_models() -> list[dict[str, Any]]:
    """Return installed models with their capabilities:
    ``[{"name": str, "capabilities": [str, ...]}, ...]``."""
    cfg = load_config()
    endpoint = "/api/tags"
    client = _get_client()
    started_at = _log_ollama_call_start(endpoint)
    resp = await client.get(
        f"{cfg.ollama_base_url}/api/tags",
        timeout=cfg.meta_timeout_seconds,
    )
    resp.raise_for_status()
    _log_ollama_call_end(started_at, endpoint, resp.status_code)
    names = [m["name"] for m in resp.json().get("models", [])]

    # Fetch each model's capabilities concurrently; /api/show is local metadata.
    capabilities = await asyncio.gather(*(_model_capabilities(name) for name in names))
    return [{"name": name, "capabilities": caps} for name, caps in zip(names, capabilities)]

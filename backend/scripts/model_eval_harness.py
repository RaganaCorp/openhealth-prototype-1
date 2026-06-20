"""
Evaluate prompt scenarios across installed non-embedding Ollama models.

Outputs:
  - data-test/model_eval_matrix.csv   (rows=scenarios, columns=models)
  - data-test/model_eval_details.csv  (one row per scenario x model)
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any
from urllib import error, request

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[2]
DATA_TEST_DIR = ROOT / "data-test"
OUT_MATRIX = DATA_TEST_DIR / "model_eval_matrix.csv"
OUT_DETAILS = DATA_TEST_DIR / "model_eval_details.csv"
OUT_ERRORS = DATA_TEST_DIR / "model_eval_errors.jsonl"
EXPECTED_DIR = DATA_TEST_DIR / "expected"
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "16384"))

# Ollama model tags to evaluate — must match `ollama list` names exactly.
REQUESTED_MODELS = [
    "dcarrascosa/medgemma-1.5-4b-it:Q4_K_M",
    "dcarrascosa/medgemma-1.5-4b-it:Q8_0",
    "medgemma:4b-it-q4_K_M",
    "medgemma:4b-it-q8_0",
    "medgemma1.5:4b-it-q4_K_M",
    "medgemma1.5:4b-it-q8_0",
    "gemma4:e2b-it-q4_K_M",
    "gemma4:e2b-it-q8_0",
    "gemma4:e4b-it-q4_K_M",
    "gemma4:e4b-it-q8_0"
]

# Ollama structured-output schemas for scenarios that require exact JSON structure.
# Passed as the `format` field in /api/chat — Ollama constrains token sampling to match.
_VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["corrected_answer", "citations", "uncertainty_note"],
    "properties": {
        "corrected_answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "excerpt":  {"type": "string"},
                },
            },
        },
        "uncertainty_note": {"type": "string"},
    },
}



sys.path.insert(0, str(ROOT / "backend"))
import llm  # noqa: E402


SCENARIO_RUBRICS = {
    "verification": (
        "Score factual grounding. 100 means the candidate correctly removes or qualifies unsupported claims, "
        "uses evidence from the context, and returns the requested JSON structure."
    ),
    "summary_generation": (
        "Score whether the candidate produces a clinically useful summary with the required sections and accurate facts."
    ),
    "extraction_pass1": (
        "Score whether the candidate extracts only explicit facts, avoids hallucinations, de-duplicates repeated facts, "
        "and fits the requested schema."
    ),
    "json_document_extraction": (
        "Score whether the candidate accurately extracts clinically relevant facts from the JSON document input."
    ),
}


@dataclass
class ExternalLLMConfig:
    provider: str
    model: str
    api_key: str
    base_url: str | None = None


def _get_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _load_external_llm_config(prefix: str) -> ExternalLLMConfig | None:
    provider = _get_env(f"EVAL_{prefix}_PROVIDER")
    model = _get_env(f"EVAL_{prefix}_MODEL")
    api_key = _get_env(f"EVAL_{prefix}_API_KEY")
    base_url = _get_env(f"EVAL_{prefix}_BASE_URL") or None
    if not provider or not model or not api_key:
        return None
    return ExternalLLMConfig(
        provider=provider.lower(),
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


def _render_messages(messages: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role", "user")).upper()
        content = str(message.get("content", ""))
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _http_json(
    method: str,
    url: str,
    payload: dict | None = None,
    timeout: int = 900,
    extra_headers: dict[str, str] | None = None,
) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, method=method, data=data, headers=headers)
    with request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def _call_external_llm(
    config: ExternalLLMConfig,
    messages: list[dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> str:
    provider = config.provider

    if provider == "openai":
        url = config.base_url or "https://api.openai.com/v1/chat/completions"
        data = _http_json(
            "POST",
            url,
            payload={
                "model": config.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=300,
            extra_headers={"Authorization": f"Bearer {config.api_key}"},
        )
        return str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))

    if provider == "anthropic":
        system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
        chat_messages = [
            {"role": "assistant" if m.get("role") == "assistant" else "user", "content": m.get("content", "")}
            for m in messages
            if m.get("role") in {"user", "assistant"}
        ]
        data = _http_json(
            "POST",
            config.base_url or "https://api.anthropic.com/v1/messages",
            payload={
                "model": config.model,
                "system": "\n\n".join(system_parts),
                "messages": chat_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=300,
            extra_headers={
                "x-api-key": config.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        blocks = data.get("content", [])
        return "\n".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict))

    if provider == "gemini":
        system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
        contents = []
        for message in messages:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": [{"text": str(message.get("content", ""))}],
                }
            )
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        data = _http_json(
            "POST",
            config.base_url
            or f"https://generativelanguage.googleapis.com/v1beta/models/{config.model}:generateContent?key={config.api_key}",
            payload=payload,
            timeout=300,
        )
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))

    raise RuntimeError(f"Unsupported evaluation provider: {config.provider}")


def list_non_embedding_models() -> list[str]:
    tags = _http_json("GET", f"{OLLAMA_BASE_URL}/api/tags", timeout=60)
    models = tags.get("models", []) if isinstance(tags, dict) else []
    names: list[str] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = m.get("name")
        if not isinstance(name, str):
            continue
        lowered = name.lower()
        if "embed" in lowered or "embedding" in lowered:
            continue
        names.append(name)
    return sorted(set(names))


def resolve_requested_models(installed: list[str], requested_models: list[str]) -> dict[str, str]:
    installed_set = set(installed)
    missing = [m for m in requested_models if m not in installed_set]
    if missing:
        raise RuntimeError(
            "Requested models are not installed in Ollama: "
            + ", ".join(missing)
            + ".\nInstall them with: ollama pull <name>"
        )
    return {m: m for m in requested_models}


def _extract_pdf_text(path: Path) -> str:
    doc = fitz.open(str(path))
    parts: list[str] = []
    for page in doc:
        txt = page.get_text().strip()
        if txt:
            parts.append(txt)
    doc.close()
    return "\n\n".join(parts)


def _extract_html_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<!--([\s\S]*?)-->", " ", raw)
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_data_test_context() -> tuple[str, str]:
    files = [
        f
        for f in DATA_TEST_DIR.iterdir()
        if f.is_file() and "model_eval" not in f.name.lower()
    ]
    text_chunks: list[str] = []
    json_source = ""

    for f in sorted(files, key=lambda p: p.name.lower()):
        suffix = f.suffix.lower()
        extracted = ""
        if suffix == ".pdf":
            extracted = _extract_pdf_text(f)
        elif suffix in {".html", ".htm"}:
            extracted = _extract_html_text(f)
        elif suffix in {".txt", ".md"}:
            extracted = f.read_text(encoding="utf-8", errors="replace")
        elif suffix == ".json":
            extracted = f.read_text(encoding="utf-8", errors="replace")
            if not json_source:
                json_source = extracted

        if extracted.strip():
            text_chunks.append(f"DOCUMENT: {f.name}\n\n{extracted.strip()}")

    if not text_chunks:
        raise RuntimeError("No readable documents found in data-test directory.")

    patient_context = "\n\n".join(text_chunks)[:70000]

    if not json_source:
        synthetic = {
            "source": "data-test documents",
            "notes": patient_context[:2000],
            "query": "Extract clinically relevant facts including diagnoses, medications, labs, and dates.",
        }
        json_source = json.dumps(synthetic, indent=2)

    return patient_context, json_source[:12000]


def parse_json_response(text: str) -> Any:
    # Strip the full chain-of-thought block emitted by MedGemma variants.
    # Pattern: <unusedN>thought ... <unusedM>  (everything from the opening token
    # to the closing token that precedes the real answer).
    text = re.sub(r"<unused\d+>thought[\s\S]*?(?=<unused\d+>)", "", text, flags=re.IGNORECASE)
    # Strip any remaining bare <unusedN> tokens.
    text = re.sub(r"<unused\d+>", "", text)

    candidates: list[str] = []

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())

    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    # Fallback: extract first likely JSON object/array from mixed output.
    object_match = re.search(r"\{[\s\S]*\}", text)
    if object_match:
        candidates.append(object_match.group(0).strip())
    array_match = re.search(r"\[[\s\S]*\]", text)
    if array_match:
        candidates.append(array_match.group(0).strip())

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _expected_file(scenario_name: str) -> Path:
    return EXPECTED_DIR / f"{scenario_name}.txt"


def _expected_meta_file() -> Path:
    return EXPECTED_DIR / ".meta.json"


def _load_expected_outputs() -> dict[str, Any]:
    """Load per-scenario expected outputs from individual .txt files."""
    if not EXPECTED_DIR.exists():
        return {}
    meta: dict[str, Any] = {}
    meta_path = _expected_meta_file()
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    result: dict[str, Any] = {}
    for txt_file in EXPECTED_DIR.glob("*.txt"):
        name = txt_file.stem
        content = txt_file.read_text(encoding="utf-8").strip()
        if content:
            result[name] = {
                "expected_output": content,
                **meta.get(name, {}),
            }
    return result


def _write_expected_output(scenario_name: str, output: str, generator: str) -> None:
    """Write one scenario's expected output to its own .txt file."""
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    _expected_file(scenario_name).write_text(output.strip(), encoding="utf-8")
    meta_path = _expected_meta_file()
    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    meta[scenario_name] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": generator,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8")


def _generate_expected_output(
    scenario: dict[str, Any],
    reference_config: ExternalLLMConfig,
) -> str:
    schema = scenario.get("output_format")
    schema_text = ""
    if isinstance(schema, dict):
        schema_text = "\nRequired JSON schema:\n" + json.dumps(schema, indent=2, ensure_ascii=True)

    messages = [
        {
            "role": "system",
            "content": (
                "You are generating a gold-standard expected answer for an evaluation harness. "
                "Follow the scenario instructions exactly and produce the ideal answer only."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Scenario: {scenario['name']}\n"
                f"Rubric: {SCENARIO_RUBRICS.get(scenario['name'], '')}\n"
                f"Original scenario messages:\n{_render_messages(scenario['messages'])}"
                f"{schema_text}\n\n"
                "Return only the expected answer for this scenario."
            ),
        },
    ]
    return _call_external_llm(reference_config, messages, temperature=0.0, max_tokens=2500).strip()


def _ensure_expected_outputs(
    scenarios: list[dict[str, Any]],
    reference_config: ExternalLLMConfig | None,
) -> dict[str, Any]:
    cache = _load_expected_outputs()
    if not reference_config:
        return cache

    for scenario in scenarios:
        if cache.get(scenario["name"], {}).get("expected_output"):
            continue
        expected_output = _generate_expected_output(scenario, reference_config)
        generator = f"{reference_config.provider}:{reference_config.model}"
        _write_expected_output(scenario["name"], expected_output, generator)
        cache[scenario["name"]] = {
            "expected_output": expected_output,
            "generator": generator,
        }

    return cache


def _judge_candidate_output(
    scenario_name: str,
    candidate_output: str,
    expected_output: str,
    judge_config: ExternalLLMConfig,
) -> tuple[float | None, str]:
    messages = [
        {
            "role": "system",
            "content": (
                "You are an evaluation judge. Score the candidate output against the expected output. "
                "Return only JSON with keys correctness_score and rationale."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Scenario: {scenario_name}\n"
                f"Rubric: {SCENARIO_RUBRICS.get(scenario_name, '')}\n\n"
                f"Expected output:\n{expected_output}\n\n"
                f"Candidate output:\n{candidate_output}\n\n"
                "Return JSON exactly like:\n"
                '{"correctness_score": 0-100, "rationale": "brief explanation"}'
            ),
        },
    ]
    raw = _call_external_llm(judge_config, messages, temperature=0.0, max_tokens=700)
    parsed = parse_json_response(raw)
    if not isinstance(parsed, dict):
        return None, raw.strip()

    score = parsed.get("correctness_score")
    rationale = str(parsed.get("rationale", "")).strip()
    try:
        numeric = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        return None, rationale or raw.strip()
    return round(numeric, 2), rationale


def _prepare_verification_context(patient_context: str) -> str:
    """Reduce noisy chart text while preserving evidence-rich sections for grounding checks."""
    text = re.sub(r"\s+", " ", patient_context)

    priority_markers = [
        "top Reason for Referral",
        "top Assessments",
        "top Problems",
        "top Medications",
        "top Results",
        "top Allergies and Adverse Reactions",
    ]

    chunks: list[str] = []
    for marker in priority_markers:
        pattern = re.compile(
            rf"({re.escape(marker)}.*?)(?=top [A-Za-z]|DOCUMENT:|\Z)",
            re.IGNORECASE,
        )
        m = pattern.search(text)
        if m:
            chunks.append(m.group(1).strip())

    if not chunks:
        return patient_context[:9000]

    reduced = "\n\n".join(chunks)
    return reduced[:9000]


def build_scenarios(patient_context: str, json_source: str) -> list[dict[str, Any]]:
    verification_context = _prepare_verification_context(patient_context)

    scenarios: list[dict[str, Any]] = [
        {
            "name": "verification",
            "output_format": _VERIFICATION_SCHEMA,
            "options": {"temperature": 0, "num_predict": 1024},
            "messages": [
                {
                    "role": "user",
                    "content": llm.build_grounding_prompt(
                        draft_answer="The patient has stage IV renal failure and is currently on warfarin daily.",
                        context=verification_context,
                    ),
                }
            ],
            "validator": lambda output: isinstance(parse_json_response(output), dict),
        },
        {
            "name": "json_document_extraction",
            "messages": [
                {
                    "role": "user",
                    "content": llm.build_json_extraction_prompt(json_source),
                }
            ],
            "validator": lambda output: len(output.strip()) > 120,
        },
    ]
    return scenarios


def call_model(
    model: str,
    messages: list[dict[str, str]],
    output_format: str | dict | None = None,
    options: dict[str, Any] | None = None,
    num_ctx: int | None = None,
) -> tuple[str, int]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if output_format is not None:
        payload["format"] = output_format
    merged_options: dict[str, Any] = dict(options or {})
    if num_ctx and num_ctx > 0:
        merged_options["num_ctx"] = int(num_ctx)
    if merged_options:
        payload["options"] = merged_options
    start = time.perf_counter()
    data = _http_json("POST", f"{OLLAMA_BASE_URL}/api/chat", payload=payload, timeout=1800)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    content = data.get("message", {}).get("content", "")
    return str(content), elapsed_ms


def correctness_score(valid: bool, judge_score: float | None = None) -> float:
    """Prefer semantic judge score when available; otherwise fall back to binary validation."""
    if judge_score is not None:
        return judge_score
    return 100.0 if valid else 0.0


def speed_score(latency_ms: int, has_output: bool) -> float:
    """0–100 based on latency; empty/error outputs get no speed credit."""
    if not has_output or latency_ms < 0:
        return 0.0
    return round(max(0.0, 100.0 * (1.0 - min(latency_ms, 120000) / 120000.0)), 2)


def performance_score(correctness: float, speed: float) -> float:
    """Combined score: 60% semantic correctness + 40% speed."""
    return round((0.6 * correctness) + (0.4 * speed), 2)


def _clip(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [truncated {len(text) - max_chars} chars]"


def _format_elapsed(seconds: float) -> str:
    total = int(max(0, seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_duration_mmss(latency_ms: int) -> str:
    if latency_ms < 0:
        return ""
    total_seconds = max(0, int(latency_ms // 1000))
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def _progress_reporter(
    stop_event: threading.Event,
    state: dict[str, Any],
    state_lock: threading.Lock,
    interval_seconds: float = 2.0,
) -> None:
    while not stop_event.is_set():
        with state_lock:
            done = int(state["done"])
            total = int(state["total"])
            current = str(state["current"])
            started_at = float(state["started_at"])
            scenario_started_at = float(state["scenario_started_at"])
        elapsed = _format_elapsed(time.perf_counter() - started_at)
        scenario_elapsed = _format_elapsed(time.perf_counter() - scenario_started_at)
        print(
            f"\rProgress {done}/{total} | overall {elapsed} | scenario {scenario_elapsed} | {current}",
            end="",
            flush=True,
        )
        stop_event.wait(interval_seconds)

    # Final line update on stop.
    with state_lock:
        done = int(state["done"])
        total = int(state["total"])
        current = str(state["current"])
        started_at = float(state["started_at"])
        scenario_started_at = float(state["scenario_started_at"])
    elapsed = _format_elapsed(time.perf_counter() - started_at)
    scenario_elapsed = _format_elapsed(time.perf_counter() - scenario_started_at)
    print(
        f"\rProgress {done}/{total} | overall {elapsed} | scenario {scenario_elapsed} | {current}",
        end="",
        flush=True,
    )


def run(
    selected_scenarios: list[str] | None = None,
    selected_models: list[str] | None = None,
    num_ctx: int = DEFAULT_NUM_CTX,
    append: bool = False,
) -> None:
    if not DATA_TEST_DIR.exists():
        raise RuntimeError(f"data-test directory not found at: {DATA_TEST_DIR}")

    patient_context, json_source = load_data_test_context()
    all_scenarios = build_scenarios(patient_context, json_source)

    if selected_scenarios:
        wanted = set(selected_scenarios)
        known = {s["name"] for s in all_scenarios}
        unknown = sorted(wanted - known)
        if unknown:
            raise RuntimeError(
                "Unknown scenario name(s): "
                + ", ".join(unknown)
                + ". Available: "
                + ", ".join(sorted(known))
            )
        scenarios = [s for s in all_scenarios if s["name"] in wanted]
    else:
        scenarios = all_scenarios

    judge_config = _load_external_llm_config("JUDGE")
    reference_config = _load_external_llm_config("REFERENCE") or judge_config
    expected_outputs = _ensure_expected_outputs(scenarios, reference_config)

    installed = list_non_embedding_models()
    model_columns = selected_models if selected_models else REQUESTED_MODELS
    model_resolution = resolve_requested_models(installed, model_columns)
    total_evals = len(scenarios) * len(model_columns)

    progress_state: dict[str, Any] = {
        "done": 0,
        "total": total_evals,
        "current": "initializing",
        "started_at": time.perf_counter(),
        "scenario_started_at": time.perf_counter(),
    }
    progress_lock = threading.Lock()
    progress_stop = threading.Event()
    reporter = threading.Thread(
        target=_progress_reporter,
        args=(progress_stop, progress_state, progress_lock),
        daemon=True,
    )
    reporter.start()

    details_rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []
    matrix: dict[str, dict[str, float]] = {s["name"]: {} for s in scenarios}
    try:
        for scenario in scenarios:
            scenario_name = scenario["name"]
            validator: Callable[[str], bool] = scenario["validator"]
            messages = scenario["messages"]

            with progress_lock:
                progress_state["scenario_started_at"] = time.perf_counter()

            for requested_model in model_columns:
                runtime_model = model_resolution[requested_model]
                with progress_lock:
                    progress_state["current"] = f"{scenario_name} -> {requested_model}"

                error_text = ""
                output = ""
                latency_ms = -1
                valid = False
                judge_score: float | None = None
                judge_rationale = ""
                expected_output = str(expected_outputs.get(scenario_name, {}).get("expected_output", ""))
                expected_source = str(expected_outputs.get(scenario_name, {}).get("generator", ""))

                try:
                    output, latency_ms = call_model(
                        runtime_model,
                        messages,
                        output_format=scenario.get("output_format"),
                        options=scenario.get("options"),
                        num_ctx=num_ctx,
                    )

                    # Some models return an empty body when constrained by full JSON schema.
                    # Retry once with plain JSON mode before failing validation.
                    if not output.strip() and isinstance(scenario.get("output_format"), dict):
                        output, latency_ms = call_model(
                            runtime_model,
                            messages,
                            output_format="json",
                            options=scenario.get("options"),
                            num_ctx=num_ctx,
                        )

                    valid = bool(validator(output))
                except Exception as exc:  # noqa: BLE001
                    error_text = str(exc)

                if output.strip() and expected_output and judge_config:
                    try:
                        judge_score, judge_rationale = _judge_candidate_output(
                            scenario_name,
                            output,
                            expected_output,
                            judge_config,
                        )
                    except Exception as exc:  # noqa: BLE001
                        judge_rationale = f"judge_error: {exc}"

                _lat = latency_ms if latency_ms >= 0 else 120000
                c_score = correctness_score(valid, judge_score)
                s_score = speed_score(_lat, bool(output.strip()))
                score = performance_score(c_score, s_score)
                matrix[scenario_name][requested_model] = score

                details_rows.append(
                    {
                        "scenario": scenario_name,
                        "model": requested_model,
                        "runtime_model": runtime_model,
                        "duration": _format_duration_mmss(latency_ms),
                        "valid_output": valid,
                        "correctness_score": c_score,
                        "speed_score": s_score,
                        "combined_score": score,
                        "judge_score": "" if judge_score is None else judge_score,
                        "judge_rationale": judge_rationale,
                        "expected_source": expected_source,
                        "output_chars": len(output),
                        "error": error_text,
                    }
                )

                if error_text or not valid:
                    error_rows.append(
                        {
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "scenario": scenario_name,
                            "model": requested_model,
                            "runtime_model": runtime_model,
                            "duration": _format_duration_mmss(latency_ms),
                            "valid_output": valid,
                            "error": error_text or "validator_failed",
                            "judge_score": judge_score,
                            "judge_rationale": judge_rationale,
                            "expected_source": expected_source,
                            "messages": [
                                {
                                    "role": m.get("role", ""),
                                    "content": _clip(str(m.get("content", "")), 3000),
                                }
                                for m in messages
                            ],
                            "output_preview": _clip(output, 6000),
                        }
                    )

                with progress_lock:
                    progress_state["done"] = int(progress_state["done"]) + 1

        with progress_lock:
            progress_state["current"] = "writing_csv"

        OUT_DETAILS.parent.mkdir(parents=True, exist_ok=True)

        details_exists = OUT_DETAILS.exists()
        details_mode = "a" if append else "w"
        with OUT_DETAILS.open(details_mode, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "scenario",
                    "model",
                    "runtime_model",
                    "duration",
                    "valid_output",
                    "correctness_score",
                    "speed_score",
                    "combined_score",
                    "judge_score",
                    "judge_rationale",
                    "expected_source",
                    "output_chars",
                    "error",
                ],
            )
            if not append or not details_exists:
                writer.writeheader()
            writer.writerows(details_rows)

        matrix_exists = OUT_MATRIX.exists()
        matrix_mode = "a" if append else "w"
        with OUT_MATRIX.open(matrix_mode, newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not append or not matrix_exists:
                writer.writerow(["scenario", *model_columns])
            for scenario in [s["name"] for s in scenarios]:
                row = [scenario]
                for model in model_columns:
                    row.append(matrix[scenario].get(model, ""))
                writer.writerow(row)

        errors_exists = OUT_ERRORS.exists()
        errors_mode = "a" if append else "w"
        with OUT_ERRORS.open(errors_mode, encoding="utf-8") as f:
            if not append and errors_exists:
                # File is overwritten in write mode; no separator needed.
                pass
            for row in error_rows:
                f.write(json.dumps(row, ensure_ascii=True) + "\n")
    finally:
        with progress_lock:
            progress_state["current"] = "done"
        progress_stop.set()
        reporter.join(timeout=3)
        print()

    print(f"Wrote matrix CSV: {OUT_MATRIX}")
    print(f"Wrote details CSV: {OUT_DETAILS}")
    print(f"Wrote error log: {OUT_ERRORS}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate prompt scenarios against selected Ollama models.")
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="Scenario name to run. Repeat for multiple scenarios.",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="Model tag to run. Repeat for multiple models.",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=DEFAULT_NUM_CTX,
        help="Override Ollama num_ctx for each request (default from OLLAMA_NUM_CTX or 16384).",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append results to existing CSVs instead of overwriting.",
    )
    args = parser.parse_args()
    run(
        selected_scenarios=args.scenario or None,
        selected_models=args.model or None,
        num_ctx=args.num_ctx,
        append=args.append,
    )

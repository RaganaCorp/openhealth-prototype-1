import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_DEFAULT_CONFIG_FILE = Path(__file__).with_name("config.defaults.json")

# DATA_PATH is set by the Docker environment; default to /data for container runtime.
DATA_PATH = Path(os.environ.get("DATA_PATH", "/data"))
_CONFIG_FILE = DATA_PATH / "config" / "config.json"

# Operational debug toggle. When enabled, full LLM request/response payloads
# (which embed patient record content), chunk text, generated titles, and upload
# filenames are written to the logs. OFF by default to keep protected health
# information out of logs; set OPENHEALTH_LOG_PAYLOADS=1 for local debugging.
LOG_PAYLOADS = os.environ.get("OPENHEALTH_LOG_PAYLOADS", "").strip().lower() in ("1", "true", "yes", "on")


def _apply_env_overrides(values: dict[str, Any]) -> dict[str, Any]:
    ollama_base_url = os.environ.get("OLLAMA_BASE_URL")
    if ollama_base_url:
        values["ollama_base_url"] = ollama_base_url
    elif values.get("ollama_base_url") == "http://ollama:11434":
        # Default to local Ollama when no explicit env override is provided.
        values["ollama_base_url"] = "http://127.0.0.1:11434"
    return values


def _load_default_values() -> dict[str, Any]:
    with open(_DEFAULT_CONFIG_FILE) as f:
        defaults = json.load(f)

    # Keep the checked-in defaults as the source of truth, but let the runtime
    # environment define where data lives.
    defaults["data_path"] = str(DATA_PATH)
    return defaults


class Config(BaseModel):
    chat_model: str
    clinical_model: str
    verification_model: str
    # Multimodal model used to transcribe scanned/image documents. A higher-quality
    # (less-quantized) model here loops far less on the hard vision task, while text
    # extraction stays on the faster clinical_model.
    vision_model: str
    embedding_model: str
    embed_timeout_seconds: float
    chat_timeout_seconds: float
    meta_timeout_seconds: float
    chunk_size: int
    chunk_overlap: int
    # How many per-category clinical-extraction calls to run concurrently per
    # document. Default 1 (sequential) is the safe choice for every install: on a
    # CPU-only Ollama, overlapping calls only thrash the same cores and can push a
    # queued call past chat_timeout_seconds (a spurious failure). Installs whose
    # Ollama runs on a GPU with parallel slots (OLLAMA_NUM_PARALLEL > 1) can raise
    # this (e.g. to 4) to overlap the calls and shorten extraction wall-clock.
    extraction_concurrency: int = 1
    memory_results: int
    context_window_tokens: int
    data_path: str = str(DATA_PATH)
    ollama_base_url: str
    # How long Ollama keeps a model resident after a request. Keeping models warm
    # avoids reloading weights on every call (model "thrash") when the pipeline
    # rotates between the chat/extraction model, the embedder, and the verifier.
    ollama_keep_alive: str = "30m"
    routing_mode: str
    medgemma_verification_enabled: bool
    grounding_enabled: bool
    grounding_max_retries: int


def load_config() -> Config:
    defaults = _load_default_values()
    if _CONFIG_FILE.exists():
        with open(_CONFIG_FILE) as f:
            data = json.load(f)
        defaults.update(data)
        _apply_env_overrides(defaults)
        return Config(**defaults)

    _apply_env_overrides(defaults)
    cfg = Config(**defaults)
    save_config(cfg)
    return cfg


def save_config(cfg: Config) -> None:
    # Atomic write (temp file + rename): a crash mid-write must not corrupt
    # config.json, since load_config would then fail on every request.
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(_CONFIG_FILE.parent), prefix=f"{_CONFIG_FILE.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg.model_dump(), f, indent=2)
        os.replace(tmp_name, _CONFIG_FILE)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def patch_config(updates: dict[str, Any]) -> Config:
    cfg = load_config()
    data = cfg.model_dump()
    data.update(updates)
    if data["chunk_overlap"] >= data["chunk_size"]:
        # Otherwise chunk_text degenerates to a step of 1 and produces roughly
        # one chunk per character — an embedding explosion on the next ingest.
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    updated = Config(**data)
    save_config(updated)
    return updated

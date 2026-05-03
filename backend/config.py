import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_DEFAULT_CONFIG_FILE = Path(__file__).with_name("config.defaults.json")

# DATA_PATH is set by the Docker environment; default to /data for container runtime.
DATA_PATH = Path(os.environ.get("DATA_PATH", "/data"))
_CONFIG_FILE = DATA_PATH / "config" / "config.json"


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
    summary_model: str
    verification_model: str
    embedding_model: str
    embed_timeout_seconds: float
    chat_timeout_seconds: float
    meta_timeout_seconds: float
    chunk_size: int
    chunk_overlap: int
    memory_results: int
    context_window_tokens: int
    data_path: str = str(DATA_PATH)
    ollama_base_url: str
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
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_FILE, "w") as f:
        json.dump(cfg.model_dump(), f, indent=2)


def patch_config(updates: dict[str, Any]) -> Config:
    cfg = load_config()
    data = cfg.model_dump()
    data.update(updates)
    updated = Config(**data)
    save_config(updated)
    return updated

"""Settings: defaults overridable by %APPDATA%\\contextual-intelligence\\config.toml,
then by environment variables (a repo-root .env is loaded if present).

Precedence: env var > config.toml > default.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

# env var -> Settings field
_ENV_OVERRIDES = {
    "LMSTUDIO_API_KEY": "api_key",
    "LMSTUDIO_BASE_URL": "base_url",
    "CI_MODEL": "model",
}


def load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader; existing environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


class Settings(BaseModel):
    # LM Studio OpenAI-compatible endpoint
    base_url: str = "http://localhost:1234/v1"
    api_key: str = "lm-studio"
    # Exact id as LM Studio reports it (`ci-lookup smoke` lists loaded models).
    model: str = "google/gemma-4-e4b"
    # Context captured either side of the selection, and the cap sent to the model.
    context_chars_per_side: int = 1500
    max_prompt_context_chars: int = 1500
    max_answer_tokens: int = 300
    request_timeout_s: float = 30.0
    log_level: str = "INFO"


def default_config_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "contextual-intelligence" / "config.toml"


def load_settings(path: Path | None = None, dotenv: Path | None = None) -> Settings:
    load_dotenv(dotenv or Path.cwd() / ".env")
    path = path or default_config_path()
    data: dict = {}
    if path.is_file():
        with path.open("rb") as f:
            data = tomllib.load(f)
    for env_name, field in _ENV_OVERRIDES.items():
        if os.environ.get(env_name):
            data[field] = os.environ[env_name]
    return Settings(**data)

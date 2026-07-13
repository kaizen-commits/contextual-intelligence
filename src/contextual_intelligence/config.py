"""Settings: defaults overridable by %APPDATA%\\contextual-intelligence\\config.toml,
then by environment variables (loaded only via explicit --env-file).

Precedence: env var > config.toml > default.
"""

from __future__ import annotations

import os
import tomllib
from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator, ConfigDict, StrictBool

_LOCAL_HTTP_NETWORKS = tuple(
    ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)

# env var -> Settings field
_ENV_OVERRIDES = {
    "LMSTUDIO_API_KEY": "api_key",
    "LMSTUDIO_BASE_URL": "base_url",
    "CI_MODEL": "model",
    "CI_PASTE_HOTKEY_VK": "paste_hotkey_vk",
    "CI_MAX_PASTE_OUTPUT_TOKENS": "max_paste_output_tokens",
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
    max_answer_tokens: int = 1024
    paste_hotkey_vk: int = 0x56  # ord('V')
    max_paste_output_tokens: int = 4096
    request_timeout_s: float = 30.0
    log_level: str = "INFO"
    enable_clipboard_fallback: StrictBool = False

    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
    )

    @field_validator("base_url")
    @classmethod
    def _validate_endpoint_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Endpoint URL must use http:// or https://")
        if not parsed.hostname:
            raise ValueError("Endpoint URL must include a host")
        if parsed.scheme == "https":
            return value
        host = parsed.hostname.lower()
        if host == "localhost":
            return value
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address is not None and any(address in network for network in _LOCAL_HTTP_NETWORKS):
            return value
        raise ValueError(
            "HTTPS is required for non-local endpoints; use localhost, a private/local "
            "network IP for HTTP LM Studio, or configure an https:// endpoint."
        )

    @field_validator("request_timeout_s")
    @classmethod
    def _validate_timeout(cls, value: float) -> float:
        if not (0 < value <= 300):
            raise ValueError("request_timeout_s must be in range (0, 300]")
        return value

    @field_validator("max_answer_tokens", "max_paste_output_tokens")
    @classmethod
    def _validate_tokens(cls, value: int) -> int:
        if not (1 <= value <= 32768):
            raise ValueError("Token fields must be in range [1, 32768]")
        return value

    @field_validator("context_chars_per_side", "max_prompt_context_chars")
    @classmethod
    def _validate_context(cls, value: int) -> int:
        if not (0 <= value <= 20000):
            raise ValueError("Context fields must be in range [0, 20000]")
        return value

    @field_validator("paste_hotkey_vk")
    @classmethod
    def _validate_hotkey(cls, value: int) -> int:
        if not (0x01 <= value <= 0xFE):
            raise ValueError("paste_hotkey_vk must be in range [0x01, 0xFE]")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        val_upper = value.upper()
        if val_upper not in levels:
            raise ValueError(f"log_level must be one of {levels}")
        return val_upper


def default_config_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "contextual-intelligence" / "config.toml"


def load_settings(path: Path | None = None, dotenv: Path | None = None) -> Settings:
    # Migration detection for CI_MAX_PASTE_INPUT_CHARS env var
    import logging
    logger = logging.getLogger(__name__)
    if "CI_MAX_PASTE_INPUT_CHARS" in os.environ:
        logger.warning(
            "CI_MAX_PASTE_INPUT_CHARS environment variable is deprecated and no longer supported. "
            "The input character limit is fixed at 8000. Please remove it from your environment."
        )

    if dotenv is not None:
        load_dotenv(dotenv)
    path = path or default_config_path()
    data: dict = {}
    if path.is_file():
        with path.open("rb") as f:
            data = tomllib.load(f)
    for env_name, field in _ENV_OVERRIDES.items():
        if os.environ.get(env_name):
            data[field] = os.environ[env_name]
    return Settings(**data)

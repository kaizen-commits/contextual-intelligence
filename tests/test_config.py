import pytest
from pydantic import ValidationError

from contextual_intelligence.config import Settings, load_dotenv, load_settings


def test_defaults():
    s = Settings()
    assert s.base_url == "http://localhost:1234/v1"
    assert s.context_chars_per_side == 1500
    assert s.max_answer_tokens == 1024
    assert s.paste_hotkey_vk == 0x56
    assert s.max_paste_output_tokens == 4096
    assert s.enable_clipboard_fallback is False


def test_load_dotenv_parses_and_respects_existing(tmp_path, monkeypatch):
    monkeypatch.delenv("SOME_NEW_KEY", raising=False)
    monkeypatch.setenv("ALREADY_SET", "original")
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "SOME_NEW_KEY=value-123\n"
        "ALREADY_SET=overwritten\n"
        "QUOTED='q-value'\n"
        "malformed line\n"
    )
    load_dotenv(env)
    import os

    assert os.environ["SOME_NEW_KEY"] == "value-123"
    assert os.environ["ALREADY_SET"] == "original"
    assert os.environ["QUOTED"] == "q-value"


def test_env_overrides_toml(tmp_path, monkeypatch):
    toml = tmp_path / "config.toml"
    toml.write_text('api_key = "from-toml"\nmodel = "toml-model"\n')
    monkeypatch.setenv("LMSTUDIO_API_KEY", "from-env")
    monkeypatch.delenv("CI_MODEL", raising=False)
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)
    s = load_settings(path=toml, dotenv=tmp_path / "no-dotenv-here")
    assert s.api_key == "from-env"  # env beats toml
    assert s.model == "toml-model"  # toml beats default


def test_dotenv_feeds_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)
    monkeypatch.delenv("CI_MODEL", raising=False)
    env = tmp_path / ".env"
    env.write_text("LMSTUDIO_API_KEY=sk-test-key\n")
    s = load_settings(path=tmp_path / "no-toml", dotenv=env)
    assert s.api_key == "sk-test-key"


def test_paste_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("CI_PASTE_HOTKEY_VK", "88")
    monkeypatch.setenv("CI_MAX_PASTE_OUTPUT_TOKENS", "250")
    s = load_settings(path=tmp_path / "no-toml", dotenv=tmp_path / "no-env")
    assert s.paste_hotkey_vk == 88
    assert s.max_paste_output_tokens == 250


def test_no_implicit_cwd_dotenv_loading(tmp_path, monkeypatch):
    # If we change CWD to tmp_path and write a .env there, load_settings()
    # must NOT load it when dotenv=None.
    monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)
    monkeypatch.delenv("CI_MODEL", raising=False)

    bad_env = tmp_path / ".env"
    bad_env.write_text("LMSTUDIO_API_KEY=untrusted-key\n")

    monkeypatch.chdir(tmp_path)
    s = load_settings(path=tmp_path / "no-toml")
    assert s.api_key == "lm-studio"  # Default survives, untrusted key not loaded


def test_deprecation_warning_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CI_MAX_PASTE_INPUT_CHARS", "10000")
    import logging
    with caplog.at_level(logging.WARNING):
        s = load_settings(path=tmp_path / "no-toml")
    assert any("CI_MAX_PASTE_INPUT_CHARS" in r.message for r in caplog.records)


def test_forbid_extra_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Settings(unknown_field="unwanted-value")


def test_numeric_bounds_validation():
    # request_timeout_s bounds (0, 300]
    with pytest.raises(ValidationError, match="request_timeout_s"):
        Settings(request_timeout_s=0.0)
    with pytest.raises(ValidationError, match="request_timeout_s"):
        Settings(request_timeout_s=300.1)

    # token bounds [1, 32768]
    with pytest.raises(ValidationError, match="Token fields must be in range"):
        Settings(max_answer_tokens=0)
    with pytest.raises(ValidationError, match="Token fields must be in range"):
        Settings(max_answer_tokens=32769)

    # context bounds [0, 20000]
    with pytest.raises(ValidationError, match="Context fields must be in range"):
        Settings(context_chars_per_side=-1)
    with pytest.raises(ValidationError, match="Context fields must be in range"):
        Settings(context_chars_per_side=20001)

    # paste_hotkey_vk bounds [0x01, 0xFE]
    with pytest.raises(ValidationError, match="paste_hotkey_vk must be in range"):
        Settings(paste_hotkey_vk=0x00)
    with pytest.raises(ValidationError, match="paste_hotkey_vk must be in range"):
        Settings(paste_hotkey_vk=0xFF)


def test_log_level_validation():
    assert Settings(log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValidationError, match="log_level must be one of"):
        Settings(log_level="INVALID")


def test_strict_bool_coercion():
    assert Settings(enable_clipboard_fallback=True).enable_clipboard_fallback is True
    # Pydantic StrictBool rejects 1, "yes", "true", etc.
    with pytest.raises(ValidationError, match="Input should be a valid boolean"):
        Settings(enable_clipboard_fallback=1)


def test_allows_local_http_endpoints():
    assert Settings(base_url="http://localhost:1234/v1").base_url == "http://localhost:1234/v1"
    assert Settings(base_url="http://127.0.0.1:1234/v1").base_url == "http://127.0.0.1:1234/v1"


def test_allows_private_lan_http_endpoint():
    endpoint = "http://192.168.1.50:1234/v1"
    assert Settings(base_url=endpoint).base_url == endpoint


def test_rejects_remote_http_endpoint():
    with pytest.raises(ValidationError, match="HTTPS is required for non-local endpoints"):
        Settings(base_url="http://llm.example.test/v1")


def test_rejects_public_ip_http_endpoint():
    with pytest.raises(ValidationError, match="HTTPS is required for non-local endpoints"):
        Settings(base_url="http://8.8.8.8:1234/v1")


def test_allows_remote_https_endpoint():
    assert Settings(base_url="https://llm.example.test/v1").base_url == "https://llm.example.test/v1"


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://192.168.1.50/v1",
        "file:///tmp/lm-studio",
        "192.168.1.50:1234/v1",
    ],
)
def test_rejects_unsupported_or_missing_endpoint_scheme(endpoint):
    with pytest.raises(ValidationError, match="must use http:// or https://"):
        Settings(base_url=endpoint)


def test_rejects_endpoint_without_host():
    with pytest.raises(ValidationError, match="must include a host"):
        Settings(base_url="https:///v1")

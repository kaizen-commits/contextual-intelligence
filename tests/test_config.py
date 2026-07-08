import pytest

from contextual_intelligence.config import Settings, load_dotenv, load_settings


def test_defaults():
    s = Settings()
    assert s.base_url == "http://localhost:1234/v1"
    assert s.context_chars_per_side == 1500
    assert s.max_answer_tokens == 1024
    assert s.paste_hotkey_vk == 0x56
    assert s.max_paste_input_chars == 8000
    assert s.max_paste_output_tokens == 4096


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
    monkeypatch.setenv("CI_MAX_PASTE_INPUT_CHARS", "5000")
    monkeypatch.setenv("CI_MAX_PASTE_OUTPUT_TOKENS", "250")
    s = load_settings(path=tmp_path / "no-toml", dotenv=tmp_path / "no-env")
    assert s.paste_hotkey_vk == 88
    assert s.max_paste_input_chars == 5000
    assert s.max_paste_output_tokens == 250


def test_allows_local_http_endpoints():
    assert Settings(base_url="http://localhost:1234/v1").base_url == "http://localhost:1234/v1"
    assert Settings(base_url="http://127.0.0.1:1234/v1").base_url == "http://127.0.0.1:1234/v1"


def test_rejects_remote_http_endpoint():
    with pytest.raises(ValueError, match="HTTPS is required for non-local endpoints"):
        Settings(base_url="http://llm.example.test/v1")


def test_allows_remote_https_endpoint():
    assert Settings(base_url="https://llm.example.test/v1").base_url == "https://llm.example.test/v1"

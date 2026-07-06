from contextual_intelligence.config import Settings, load_dotenv, load_settings


def test_defaults():
    s = Settings()
    assert s.base_url == "http://localhost:1234/v1"
    assert s.context_chars_per_side == 1500


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

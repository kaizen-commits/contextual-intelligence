import sys
import pytest
from unittest.mock import patch
from contextual_intelligence.cli import main


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "ci-lookup" in captured.out


def test_cli_platform_guard(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    code = main(["smoke"])
    assert code == 1
    captured = capsys.readouterr()
    assert "Contextual Intelligence requires Windows" in captured.err


def test_cli_missing_env_file(capsys):
    code = main(["--env-file", "nonexistent_file_path_123.env", "smoke"])
    assert code == 1
    captured = capsys.readouterr()
    assert "config error: env file not found" in captured.err


def test_cli_malformed_toml(tmp_path, monkeypatch, capsys):
    toml = tmp_path / "config.toml"
    toml.write_text("invalid = { = }")
    monkeypatch.setattr("contextual_intelligence.config.default_config_path", lambda: toml)
    
    code = main(["smoke"])
    assert code == 1
    captured = capsys.readouterr()
    assert "config error" in captured.err


def test_cli_invalid_endpoint(tmp_path, monkeypatch, capsys):
    toml = tmp_path / "config.toml"
    toml.write_text('base_url = "http://llm.example.test/v1"')
    monkeypatch.setattr("contextual_intelligence.config.default_config_path", lambda: toml)
    
    code = main(["smoke"])
    assert code == 1
    captured = capsys.readouterr()
    assert "HTTPS is required for non-local endpoints" in captured.err


def test_cli_env_file_dispatch_and_precedence(tmp_path, monkeypatch):
    # Test --env-file loading settings
    env = tmp_path / ".env"
    env.write_text("LMSTUDIO_API_KEY=env-file-key\n")
    monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
    monkeypatch.setattr("contextual_intelligence.config.default_config_path", lambda: tmp_path / "no-config-toml")
    
    # Mock cmd_smoke to inspect loaded settings
    with patch("contextual_intelligence.cli.cmd_smoke") as mock_smoke:
        mock_smoke.return_value = 0
        code = main(["--env-file", str(env), "smoke"])
        assert code == 0
        mock_smoke.assert_called_once()
        settings = mock_smoke.call_args[0][0]
        assert settings.api_key == "env-file-key"


def test_cli_no_cwd_dotenv_loading(tmp_path, monkeypatch):
    bad_env = tmp_path / ".env"
    bad_env.write_text("LMSTUDIO_API_KEY=untrusted-key\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
    monkeypatch.setattr("contextual_intelligence.config.default_config_path", lambda: tmp_path / "no-config-toml")
    
    with patch("contextual_intelligence.cli.cmd_smoke") as mock_smoke:
        mock_smoke.return_value = 0
        code = main(["smoke"])
        assert code == 0
        settings = mock_smoke.call_args[0][0]
        assert settings.api_key == "lm-studio"  # Default survives

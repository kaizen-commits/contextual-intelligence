"""Slice D tests: single-instance guard and visible hotkey-failure status."""

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

import contextual_intelligence.instance as instance_mod
from contextual_intelligence.config import Settings
from contextual_intelligence.hotkey import LOOKUP_HOTKEY_ID, PASTE_HOTKEY_ID
from contextual_intelligence.instance import (
    acquire_single_instance_lock,
    release_instance_lock,
)
from contextual_intelligence.ui.tray import TrayApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _reset_mutex_handle():
    yield
    instance_mod._mutex_handle = None


class FakeKernel32:
    def __init__(self, handle=1234, last_error=0):
        self.handle = handle
        self.last_error = last_error
        self.closed = []

    def CreateMutexW(self, security, initial_owner, name):
        return self.handle

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def test_lock_acquired_and_released(monkeypatch):
    fake = FakeKernel32(handle=1234, last_error=0)
    monkeypatch.setattr(instance_mod, "_kernel32", fake)
    monkeypatch.setattr(instance_mod.ctypes, "get_last_error", lambda: fake.last_error)

    assert acquire_single_instance_lock() is True
    assert instance_mod._mutex_handle == 1234

    release_instance_lock()
    assert fake.closed == [1234]
    assert instance_mod._mutex_handle is None
    release_instance_lock()  # idempotent
    assert fake.closed == [1234]


def test_lock_held_by_other_instance_closes_duplicate(monkeypatch):
    fake = FakeKernel32(handle=5678, last_error=183)  # ERROR_ALREADY_EXISTS
    monkeypatch.setattr(instance_mod, "_kernel32", fake)
    monkeypatch.setattr(instance_mod.ctypes, "get_last_error", lambda: fake.last_error)

    assert acquire_single_instance_lock() is False
    assert fake.closed == [5678]  # duplicate handle must not leak
    assert instance_mod._mutex_handle is None


def test_null_handle_fails_open_with_warning(monkeypatch, caplog):
    """P7: an exotic CreateMutexW failure must not block startup."""
    fake = FakeKernel32(handle=0, last_error=6)
    monkeypatch.setattr(instance_mod, "_kernel32", fake)
    monkeypatch.setattr(instance_mod.ctypes, "get_last_error", lambda: fake.last_error)

    import logging

    with caplog.at_level(logging.WARNING):
        assert acquire_single_instance_lock() is True
    assert any("single-instance guard unavailable" in r.message for r in caplog.records)
    assert instance_mod._mutex_handle is None


def test_cmd_tray_exits_before_qt_when_lock_held(monkeypatch, capsys):
    from contextual_intelligence import cli

    # cmd_tray imports the guard at call time — patch the source module.
    monkeypatch.setattr(instance_mod, "acquire_single_instance_lock", lambda: False)
    constructed = []
    monkeypatch.setattr(
        cli, "build_orchestrator", lambda settings: constructed.append("orchestrator")
    )

    rc = cli.cmd_tray(Settings())

    assert rc == 1
    assert constructed == []  # nothing Qt/capture constructed
    assert "already running" in capsys.readouterr().err


# --- visible hotkey status -------------------------------------------------


def _tray(monkeypatch):
    monkeypatch.setattr(
        "contextual_intelligence.ui.tray.HotkeyBridge.start", lambda self, hm: None
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.tray.HotkeyBridge.stop", lambda self: True
    )
    tray = TrayApplication(Settings(), MagicMock(), MagicMock())
    tray.tray_icon = MagicMock()
    return tray


def test_hotkey_failure_shows_tray_message_and_tooltip(qapp, monkeypatch):
    tray = _tray(monkeypatch)

    tray._on_hotkey_failed(PASTE_HOTKEY_ID, 0x56)

    tray.tray_icon.showMessage.assert_called_once()
    args = tray.tray_icon.showMessage.call_args[0]
    assert args[0] == "Shortcut unavailable"
    assert "Ctrl+Alt+V" in args[1]
    assert "Smart Paste" in args[1]
    assert args[2] == QSystemTrayIcon.MessageIcon.Warning
    tooltip = tray.tray_icon.setToolTip.call_args[0][0]
    assert "Ctrl+Alt+D" in tooltip and "Ctrl+Alt+V" not in tooltip

    tray.quit()


def test_both_hotkeys_failed_escalates(qapp, monkeypatch):
    tray = _tray(monkeypatch)

    tray._on_hotkey_failed(LOOKUP_HOTKEY_ID, 0x44)
    tray._on_hotkey_failed(PASTE_HOTKEY_ID, 0x56)

    assert tray.tray_icon.showMessage.call_count == 2
    second = tray.tray_icon.showMessage.call_args[0]
    assert "No shortcuts could be registered" in second[1]
    tooltip = tray.tray_icon.setToolTip.call_args[0][0]
    assert "hotkeys unavailable" in tooltip

    tray.quit()

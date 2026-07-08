from unittest.mock import MagicMock
import pytest
from PySide6.QtWidgets import QApplication

from contextual_intelligence.config import Settings
from contextual_intelligence.hotkey import LOOKUP_HOTKEY_ID, PASTE_HOTKEY_ID
from contextual_intelligence.ui.tray import TrayApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_tray_application_init_and_triggers(qapp, monkeypatch):
    # Mock hotkey bridge so we don't spawn real win32 threads in tests
    monkeypatch.setattr("contextual_intelligence.ui.tray.HotkeyBridge.start", lambda self, hm: None)
    monkeypatch.setattr("contextual_intelligence.ui.tray.HotkeyBridge.stop", lambda self: None)

    settings = Settings()
    orchestrator = MagicMock()
    llm = MagicMock()

    tray = TrayApplication(settings, orchestrator, llm)
    assert tray.popup is not None
    assert tray.paste_palette is not None

    # Test trigger_lookup
    tray.trigger_lookup()
    assert tray.popup.isVisible() or not tray.popup.isHidden()

    # Test trigger_paste
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format",
        lambda: False,
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.read_text_clipboard",
        lambda: "test text",
    )
    tray.trigger_paste("test_app.exe")
    assert tray.paste_palette._source_app == "test_app.exe"

    # Test hotkey failure logging
    tray._on_hotkey_failed(LOOKUP_HOTKEY_ID, 0x44)
    tray._on_hotkey_failed(PASTE_HOTKEY_ID, 0x56)

    # Test quit
    tray.quit()


def test_tray_application_mutual_exclusion_scope_23(qapp, monkeypatch):
    monkeypatch.setattr("contextual_intelligence.ui.tray.HotkeyBridge.start", lambda self, hm: None)
    monkeypatch.setattr("contextual_intelligence.ui.tray.HotkeyBridge.stop", lambda self: None)
    monkeypatch.setattr("PySide6.QtCore.QTimer.singleShot", lambda delay, cb: cb())
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format",
        lambda: False,
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.read_text_clipboard",
        lambda: "test text",
    )

    settings = Settings()
    orchestrator = MagicMock()
    llm = MagicMock()

    tray = TrayApplication(settings, orchestrator, llm)

    # 1. When palette is visible and lookup is triggered, palette should close
    tray.paste_palette.show()
    assert tray.paste_palette.isVisible()
    tray.trigger_lookup()
    assert not tray.paste_palette.isVisible()

    # 2. When popup is visible and paste is triggered, popup should close
    tray.popup.show()
    assert tray.popup.isVisible()
    tray.trigger_paste("test_app.exe")
    assert not tray.popup.isVisible()
    assert tray.paste_palette.isVisible()

    tray.quit()


def test_tray_records_only_short_palette_copies(qapp, monkeypatch):
    """Only lookup-sized palette copies are recorded for the handoff (SCOPE-30)."""
    monkeypatch.setattr("contextual_intelligence.ui.tray.HotkeyBridge.start", lambda self, hm: None)
    monkeypatch.setattr("contextual_intelligence.ui.tray.HotkeyBridge.stop", lambda self: None)

    tray = TrayApplication(Settings(), MagicMock(), MagicMock())
    assert tray._recent_app_copy is None

    tray.paste_palette.copied_from_palette.emit("gadget")
    assert tray._recent_app_copy is not None
    assert tray._recent_app_copy.text == "gadget"
    assert tray._recent_app_copy.source == "smart_paste"

    # Oversized copies are ignored; the previous record is kept as-is
    tray.paste_palette.copied_from_palette.emit("x" * 500)
    assert tray._recent_app_copy.text == "gadget"

    tray.quit()


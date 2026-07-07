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

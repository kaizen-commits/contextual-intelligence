"""Shared Qt lifetime hygiene for the test suite.

Without this, widgets created in a test are destroyed whenever Python's
cyclic GC happens to run — which can be in the middle of Qt event delivery
in a *later* test. On Windows destroying a QComboBox that way crashes the
process with an access violation. Dispose of top-level widgets
deterministically at a safe point after each test instead, so GC never has
a live C++ widget to tear down.
"""

import gc

import pytest


@pytest.fixture(autouse=True)
def _no_uia_warmup(monkeypatch):
    """Disable TrayApplication's UIA warm-up thread during tests.

    The warm-up daemon thread initializes COM and calls GetFocusedControl(),
    which can invoke in-process UIA providers on this process's own Qt
    windows while a test (or the disposal fixture below) is destroying them —
    an intermittent access violation. The warm-up only exists to hide cold
    startup latency in the real app; it has no value in tests.
    """
    try:
        from contextual_intelligence.ui import tray
    except ImportError:
        return
    monkeypatch.setattr(tray.TrayApplication, "_warmup_uia", lambda self: None)


@pytest.fixture(autouse=True)
def _dispose_qt_widgets_between_tests():
    yield
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is not None:
        for widget in app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        # processEvents() alone does not guarantee delivery of DeferredDelete
        # events when pytest is driving Qt without the main event loop running.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    gc.collect()  # only dead Python wrappers left; no C++ dtors run here

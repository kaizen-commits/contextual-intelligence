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

# Note: the UIA warm-up thread (and the fixture that suppressed it here) was
# removed in the hardening pass — production no longer starts an unowned COM
# thread that could race Qt widget destruction.


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

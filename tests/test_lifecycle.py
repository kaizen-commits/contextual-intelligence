"""Slice C lifecycle tests: cancellation checkpoints, retiring-worker retention,
shutdown gating/ordering, the watchdog, and the hotkey stop handshake."""

import threading
import time
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

import contextual_intelligence.ui.tray as tray_mod
from contextual_intelligence.config import Settings
from contextual_intelligence.llm import LlmClient
from contextual_intelligence.models import PastePayload
from contextual_intelligence.ui.palette import PastePaletteWindow
from contextual_intelligence.ui.paste_worker import PasteWorker
from contextual_intelligence.ui.tray import HotkeyBridge, TrayApplication
from contextual_intelligence.ui.worker import LookupWorker


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# --- cancellation checkpoints -------------------------------------------------


def test_lookup_worker_pre_cancel_makes_zero_calls(qapp):
    """A cancel issued before run() must stick: zero orchestrator calls, zero
    LLM calls, zero signals (the Event is never reset inside run())."""
    orch = MagicMock()
    llm = MagicMock()
    worker = LookupWorker(orch, llm)

    signals = []
    worker.started_capture.connect(lambda: signals.append("started"))
    worker.capture_succeeded.connect(lambda p: signals.append("captured"))
    worker.error_occurred.connect(lambda m: signals.append("error"))

    worker.cancel()
    worker.run()

    orch.capture.assert_not_called()
    llm.stream_lookup.assert_not_called()
    assert signals == []


def test_paste_worker_pre_cancel_makes_zero_calls(qapp):
    llm = MagicMock()
    worker = PasteWorker(PastePayload(text="hello", instruction="upper"), llm)

    signals = []
    worker.started_transform.connect(lambda: signals.append("started"))
    worker.error_occurred.connect(lambda m: signals.append("error"))

    worker.cancel()
    worker.run()

    llm.stream_transform.assert_not_called()
    assert signals == []


def test_lookup_worker_cancel_between_capture_and_stream(qapp):
    """Checkpoint before stream creation: cancel during capture means no LLM call."""
    llm = MagicMock()

    class CancellingOrchestrator:
        def __init__(self, worker_ref):
            self.worker_ref = worker_ref

        def capture(self):
            self.worker_ref[0].cancel()
            from contextual_intelligence.models import CaptureTier, ContextPayload

            return ContextPayload(selected_text="word", tier=CaptureTier.UIA, app_name="t")

    ref = [None]
    worker = LookupWorker(CancellingOrchestrator(ref), llm)
    ref[0] = worker
    worker.run()

    llm.stream_lookup.assert_not_called()


# --- palette: retiring worker, no replacement while one lives ------------------


class BlockingLlm:
    """Streams one chunk, then blocks until released — a stand-in for a stalled
    network read that cooperative cancellation cannot interrupt."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()

    def stream_transform(self, payload):
        self.started.set()
        yield "partial "
        self.release.wait(5.0)
        yield "rest"


def _open_palette(monkeypatch, llm):
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.has_high_value_non_text_format", lambda: False
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.palette.read_text_clipboard", lambda: "hello world"
    )
    palette = PastePaletteWindow(Settings(), llm)
    palette.open_palette("notepad.exe")
    return palette


def test_palette_retires_stalled_worker_and_rejects_new_work(qapp, monkeypatch):
    llm = BlockingLlm()
    palette = _open_palette(monkeypatch, llm)

    palette.instruction_input.setText("summarize")
    palette._on_submit()
    stalled = palette._worker
    assert stalled is not None
    assert llm.started.wait(2.0)

    # Bounded wait expires -> worker is retired (reference retained), not dropped.
    assert palette.cancel_worker(timeout_ms=50) is False
    assert palette._worker is None
    assert palette._retiring is stalled
    assert palette.live_workers() == [stalled]

    # New work is rejected while the retiring worker lives.
    palette._on_submit()
    assert palette._worker is None
    assert "Finishing previous request" in palette.status_label.text()

    # Release the stall; the retiring slot clears itself via QThread.finished.
    llm.release.set()
    assert stalled.wait(2000)
    deadline = time.monotonic() + 2.0
    while palette._retiring is not None and time.monotonic() < deadline:
        qapp.processEvents()
    assert palette._retiring is None
    assert palette.live_workers() == []

    palette.close()


# --- tray: idempotent, ordered, gated shutdown ---------------------------------


def _mocked_tray(monkeypatch):
    # Defuse the hard-exit: if an assertion fails while a real watchdog is
    # armed, os._exit must not kill the pytest process 10 seconds later. The
    # watchdog test installs its own recorder over this.
    monkeypatch.setattr(tray_mod.os, "_exit", lambda code: None)
    monkeypatch.setattr(
        "contextual_intelligence.ui.tray.HotkeyBridge.start", lambda self, hm: None
    )
    monkeypatch.setattr(
        "contextual_intelligence.ui.tray.HotkeyBridge.stop", lambda self: True
    )
    tray = TrayApplication(Settings(), MagicMock(), MagicMock())
    # Dispose the real windows; the shutdown units are exercised through mocks.
    tray.popup.close()
    tray.paste_palette.close()
    m = MagicMock()
    m.popup.live_workers.return_value = []
    m.popup.cancel_lookup.return_value = True
    m.palette.live_workers.return_value = []
    m.palette.cancel_worker.return_value = True
    m.bridge.stop.return_value = True
    tray.popup = m.popup
    tray.paste_palette = m.palette
    tray.hotkey_bridge = m.bridge
    tray.llm_client = m.llm
    tray.tray_icon = m.tray_icon
    tray.app = m.app
    return tray, m


def test_tray_quit_is_idempotent_and_ordered(qapp, monkeypatch):
    tray, m = _mocked_tray(monkeypatch)

    tray.quit()

    names = [c[0] for c in m.mock_calls]
    # Contract: hotkeys stop first; cancel is signalled before close() aborts
    # I/O; bounded waits come after close() so aborts make them fast.
    assert names.index("bridge.stop") < names.index("llm.close")
    assert names.index("popup.request_cancel") < names.index("llm.close")
    assert names.index("palette.request_cancel") < names.index("llm.close")
    assert names.index("llm.close") < names.index("popup.cancel_lookup")
    assert "tray_icon.hide" in names
    assert "app.quit" in names
    assert tray._teardown_done
    assert tray._watchdog.finished.is_set()  # cancelled after clean shutdown

    m.llm.close.reset_mock()
    tray.quit()  # idempotent: a second call is a no-op
    m.llm.close.assert_not_called()


def test_tray_quit_gates_on_running_worker(qapp, monkeypatch):
    tray, m = _mocked_tray(monkeypatch)

    blocker = MagicMock()
    blocker.isRunning.return_value = True
    m.popup.live_workers.return_value = [blocker]

    tray.quit()

    # Teardown must not proceed while the worker lives.
    assert not tray._teardown_done
    m.app.quit.assert_not_called()
    blocker.finished.connect.assert_called_once()
    resume = blocker.finished.connect.call_args[0][0]

    # Worker exits -> the gated continuation completes teardown.
    m.popup.live_workers.return_value = []
    resume()
    assert tray._teardown_done
    m.app.quit.assert_called_once()
    assert tray._watchdog.finished.is_set()


def test_tray_watchdog_hard_exits_when_gate_never_clears(qapp, monkeypatch):
    tray, m = _mocked_tray(monkeypatch)
    monkeypatch.setattr(tray_mod, "SHUTDOWN_GRACE_S", 0.05)
    exited = threading.Event()
    calls = []
    monkeypatch.setattr(
        tray_mod.os, "_exit", lambda code: (calls.append(code), exited.set())
    )

    blocker = MagicMock()
    blocker.isRunning.return_value = True
    m.popup.live_workers.return_value = [blocker]

    tray.quit()
    assert exited.wait(2.0)
    assert calls == [1]
    assert not tray._teardown_done
    tray._watchdog.cancel()


# --- hotkey bridge stop handshake ----------------------------------------------


def test_hotkey_bridge_stop_reports_termination(qapp, monkeypatch):
    def fake_loop(
        hotkey_map=None,
        on_thread_id=None,
        on_registration_failure=None,
        on_ready=None,
        stopping=None,
    ):
        on_thread_id(0xDEAD)
        on_ready()
        while not stopping.is_set():
            time.sleep(0.005)

    monkeypatch.setattr(tray_mod, "run_hotkey_loop", fake_loop)

    # stop() before start(): trivially terminated.
    assert HotkeyBridge().stop() is True

    bridge = HotkeyBridge()
    bridge.start({})
    assert bridge._ready.wait(2.0)
    assert bridge.stop() is True
    assert bridge._done.is_set()
    assert not bridge.thread.is_alive()


# --- llm client shutdown surface -----------------------------------------------


def test_llm_client_zero_retries_and_idempotent_close():
    client = LlmClient(Settings())
    assert client._client.max_retries == 0
    client.close()
    client.close()  # idempotent; never raises


def test_llm_client_close_swallows_exceptions(monkeypatch):
    client = LlmClient(Settings())
    monkeypatch.setattr(
        client._client, "close", MagicMock(side_effect=RuntimeError("boom"))
    )
    client.close()  # must not raise

"""The lifecycle contract these tests pin down is the fix for the bug that
killed the original app (listeners staying alive after popup close)."""

import pytest

from contextual_intelligence.capture.clipboard_fallback import (
    ArmedClipboardCapture,
    ClipboardFallbackProvider,
    ListenerState,
)
from contextual_intelligence.models import CaptureError, CaptureTier, ContextPayload


def test_starts_disarmed():
    assert ClipboardFallbackProvider().state is ListenerState.DISARMED


def test_capture_while_disarmed_is_illegal():
    p = ClipboardFallbackProvider()
    with pytest.raises(CaptureError, match="requires armed state"):
        p.capture()
    assert p.state is ListenerState.DISARMED


def test_double_arm_is_illegal():
    p = ClipboardFallbackProvider()
    p.arm()
    with pytest.raises(CaptureError, match="cannot arm"):
        p.arm()


def test_disarm_is_idempotent_and_legal_from_any_state():
    p = ClipboardFallbackProvider()
    p.disarm()
    p.arm()
    p.disarm()
    p.disarm()
    assert p.state is ListenerState.DISARMED


def test_capture_always_disarms_even_on_failure(monkeypatch):
    p = ClipboardFallbackProvider()
    p.arm()
    monkeypatch.setattr(
        p,
        "_do_capture",
        lambda: (_ for _ in ()).throw(CaptureError("mock failure", CaptureTier.CLIPBOARD)),
    )
    with pytest.raises(CaptureError):
        p.capture()
    # No lingering listener after a failed capture — the original app's bug.
    assert p.state is ListenerState.DISARMED


def test_rearm_after_capture_cycle_works(monkeypatch):
    p = ClipboardFallbackProvider()
    p.arm()
    monkeypatch.setattr(
        p,
        "_do_capture",
        lambda: (_ for _ in ()).throw(CaptureError("mock failure", CaptureTier.CLIPBOARD)),
    )
    with pytest.raises(CaptureError):
        p.capture()
    p.arm()
    assert p.state is ListenerState.ARMED


def test_adapter_runs_full_cycle_and_leaves_disarmed(monkeypatch):
    adapter = ArmedClipboardCapture()
    monkeypatch.setattr(
        adapter.provider,
        "_do_capture",
        lambda: ContextPayload(selected_text="word", tier=CaptureTier.CLIPBOARD),
    )
    payload = adapter.capture()
    assert payload.selected_text == "word"
    assert adapter.provider.state is ListenerState.DISARMED


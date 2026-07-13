"""Privacy regressions for the primary UI Automation capture tier."""

from __future__ import annotations

import logging

from contextual_intelligence.capture import uia


_SENTINEL = "PRIVATE_CONTROL_TEXT"


def _raise_private(*args, **kwargs):
    raise RuntimeError(_SENTINEL)


def test_process_image_lookup_does_not_log_foreign_exception_text(monkeypatch, caplog):
    """The clipboard fallback calls this helper during target attribution."""
    monkeypatch.setattr(uia.ctypes.windll.kernel32, "OpenProcess", _raise_private)

    with caplog.at_level(logging.DEBUG):
        assert uia.get_process_image_name(1234) == ""

    assert _SENTINEL not in caplog.text
    assert "RuntimeError" in caplog.text


def test_surrounding_context_failures_do_not_log_foreign_exception_text(caplog):
    class _Range:
        def Clone(self):
            raise RuntimeError(_SENTINEL)

    provider = uia.UiaCaptureProvider()

    with caplog.at_level(logging.DEBUG):
        assert provider._surrounding(_Range(), "selected", object()) == ("", "")

    assert _SENTINEL not in caplog.text
    assert "RuntimeError" in caplog.text
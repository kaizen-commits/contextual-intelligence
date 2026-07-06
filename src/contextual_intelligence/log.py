"""Logging setup. Capture telemetry (tier, timings, validation results) goes
through here so real-world coverage is measurable before tier polish."""

from __future__ import annotations

import logging
import sys

FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(FORMAT))
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers[:] = [handler]

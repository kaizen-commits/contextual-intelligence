"""Screen-aware positioning utilities for popup overlays and palettes."""

from __future__ import annotations

import logging

from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)


def position_near_cursor(
    widget: QWidget, offset_x: int = 15, offset_y: int = 15, margin: int = 10
) -> None:
    """Position a widget near the mouse cursor on the screen containing the cursor.

    Using screenAt rather than primaryScreen ensures that on multi-monitor layouts
    where secondary monitors sit at negative virtual coordinates, the widget is
    positioned and clamped against the correct screen boundaries.
    """
    pos = QCursor.pos()
    screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
    if screen:
        geom = screen.availableGeometry()
        x = min(pos.x() + offset_x, geom.right() - widget.width() - margin)
        y = min(pos.y() + offset_y, geom.bottom() - widget.height() - margin)
        widget.move(max(geom.left() + margin, x), max(geom.top() + margin, y))
    else:
        widget.move(pos.x() + offset_x, pos.y() + offset_y)


def clamp_to_screen(widget: QWidget, margin: int = 10) -> None:
    """Keep a widget fully within its current screen's available geometry.

    Useful after adjustSize() or dynamic content growth.
    """
    screen = (
        QGuiApplication.screenAt(widget.frameGeometry().center())
        or QGuiApplication.primaryScreen()
    )
    if screen is None:
        return
    geom = screen.availableGeometry()
    x = min(widget.x(), geom.right() - widget.width() - margin)
    y = min(widget.y(), geom.bottom() - widget.height() - margin)
    widget.move(max(geom.left() + margin, x), max(geom.top() + margin, y))

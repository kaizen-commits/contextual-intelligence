"""Screen-aware positioning utilities for popup overlays and palettes."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint
from PySide6.QtGui import QCursor, QGuiApplication, QScreen
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)


def _get_screen_for_point(pos: QPoint) -> QScreen | None:
    """Find the screen containing `pos`, falling back to the closest screen if `screenAt` returns None."""
    screen = QGuiApplication.screenAt(pos)
    if screen is not None:
        return screen

    screens = QGuiApplication.screens()
    if not screens:
        return QGuiApplication.primaryScreen()

    # In multi-monitor Windows setups with mixed DPI scaling or negative coordinates,
    # screenAt(pos) can return None due to coordinate rounding gaps or boundary mismatches.
    # Find the closest screen by distance instead of blindly jumping to primaryScreen().
    best_screen = screens[0]
    min_dist_sq = float("inf")
    for s in screens:
        geom = s.geometry()
        dx = max(geom.left() - pos.x(), 0, pos.x() - geom.right())
        dy = max(geom.top() - pos.y(), 0, pos.y() - geom.bottom())
        dist_sq = dx * dx + dy * dy
        if dist_sq < min_dist_sq:
            min_dist_sq = dist_sq
            best_screen = s
    return best_screen


def position_near_cursor(
    widget: QWidget, offset_x: int = 15, offset_y: int = 15, margin: int = 10
) -> None:
    """Position a widget near the mouse cursor on the screen containing the cursor.

    Using _get_screen_for_point ensures that on multi-monitor layouts where secondary
    monitors sit at negative virtual coordinates or use mixed DPI scaling, the widget is
    positioned and clamped against the correct screen boundaries instead of jumping to the main monitor.
    """
    pos = QCursor.pos()
    screen = _get_screen_for_point(pos)
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
    screen = _get_screen_for_point(widget.frameGeometry().center())
    if screen is None:
        return
    geom = screen.availableGeometry()
    x = min(widget.x(), geom.right() - widget.width() - margin)
    y = min(widget.y(), geom.bottom() - widget.height() - margin)
    widget.move(max(geom.left() + margin, x), max(geom.top() + margin, y))

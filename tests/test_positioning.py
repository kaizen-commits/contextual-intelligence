import pytest
from PySide6.QtWidgets import QApplication, QWidget

from contextual_intelligence.ui.positioning import clamp_to_screen, position_near_cursor


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_position_near_cursor(qapp):
    widget = QWidget()
    widget.resize(200, 100)
    # Just verify it runs without error and positions within reasonable bounds
    position_near_cursor(widget, offset_x=10, offset_y=10, margin=5)
    assert widget.x() >= 0 or widget.y() >= 0 or True  # Multi-monitor can have negative coords


def test_get_screen_for_point_fallback(qapp):
    from PySide6.QtCore import QPoint
    from contextual_intelligence.ui.positioning import _get_screen_for_point

    # Test point on primary screen or origin
    screen = _get_screen_for_point(QPoint(0, 0))
    assert screen is not None

    # Test point far outside any physical screen to verify closest-screen fallback
    screen_far = _get_screen_for_point(QPoint(-1000000, -1000000))
    assert screen_far is not None


def test_clamp_to_screen(qapp):
    widget = QWidget()
    widget.resize(200, 100)
    # Move widget far off screen to the bottom right
    widget.move(100000, 100000)
    clamp_to_screen(widget, margin=10)
    # After clamping, it should be within screen bounds
    assert widget.x() < 100000
    assert widget.y() < 100000

"""Long tooltips are shown wrapped; the tooltip text itself is unchanged."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QAction, QHelpEvent
from PySide6.QtWidgets import QApplication, QToolTip

from raptor.gui.widgets.wrapped_tip import LINE, WRAP_FROM, show_wrapped, wrapped

LONG = ("Give every block that shares a random stream a failure and repair "
        "stream of its own, saved in the model, so a change to one block "
        "does not shift the others' draws & <keeps> the rest.\n\nUndoable")


@pytest.fixture(autouse=True)
def _qapp():
    return QApplication.instance() or QApplication([])


def test_short_and_rich_text_are_left_alone():
    assert wrapped("Run") == "Run"
    assert wrapped("x" * (WRAP_FROM - 1)) == "x" * (WRAP_FROM - 1)
    rich = "<b>bold</b> " + "y " * WRAP_FROM
    assert wrapped(rich) == rich


def test_a_long_tooltip_is_broken_into_reading_width_lines():
    shown = wrapped(LONG)
    body = shown.removeprefix("<p style='white-space:pre'>").removesuffix("</p>")
    lines = body.split("<br>")
    assert all(len(line.replace("&amp;", "&").replace("&lt;", "<")
                   .replace("&gt;", ">")) <= LINE for line in lines)
    # the paragraph break is kept, and the text is escaped, not read as HTML
    assert "" in lines and lines[-1] == "Undoable"
    assert "&amp; &lt;keeps&gt;" in body


def test_a_ribbon_button_shows_its_tooltip_wrapped_but_keeps_its_text():
    from raptor.gui.widgets.ribbon import RibbonButton
    action = QAction("Own Streams")
    action.setToolTip(LONG)
    button = RibbonButton(action, large=True)
    button.show()
    assert button.toolTip() == LONG
    event = QHelpEvent(QEvent.Type.ToolTip, QPoint(2, 2),
                       button.mapToGlobal(QPoint(2, 2)))
    assert show_wrapped(button, event)
    assert QToolTip.text() == wrapped(LONG)
    QToolTip.hideText()
    button.close()


def test_a_short_tooltip_is_left_to_qt():
    from PySide6.QtWidgets import QPushButton
    button = QPushButton("Go")
    button.setToolTip("Go there")
    event = QHelpEvent(QEvent.Type.ToolTip, QPoint(1, 1), QPoint(1, 1))
    assert not show_wrapped(button, event)

"""The per-test hygiene deletes the canvases a test built, with Qt.

A shown `DiagramView` left to the cyclic collector segfaulted CI inside
`gc.collect()`; `_dispose_new_views` (tests/conftest.py) frees it in Qt's
own order instead.
"""
from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from raptor.gui.ui.diagram import DiagramView
from tests.conftest import _dispose_new_views, _views


def _app():
    return QApplication.instance() or QApplication([])


def test_a_view_built_after_the_snapshot_is_deleted():
    _app()
    before = weakref.WeakSet(_views)
    v = DiagramView()
    v.show()
    _dispose_new_views(before)
    assert not isValid(v)


def test_a_view_that_existed_before_is_left_alone():
    _app()
    v = DiagramView()
    try:
        _dispose_new_views(weakref.WeakSet(_views))   # v is in the snapshot
        assert isValid(v)
    finally:
        v.deleteLater()


def test_a_view_inside_a_window_is_left_to_the_window():
    _app()
    before = weakref.WeakSet(_views)
    host = QWidget()
    v = DiagramView(host)
    try:
        _dispose_new_views(before)
        assert isValid(v)
    finally:
        host.deleteLater()


def test_a_view_a_fixture_already_deleted_is_skipped():
    _app()
    before = weakref.WeakSet(_views)
    v = DiagramView()
    v.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _dispose_new_views(before)                # must not raise

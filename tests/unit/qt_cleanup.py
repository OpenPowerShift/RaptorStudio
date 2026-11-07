"""Delete a test's canvases with Qt, not with the cyclic collector.

A shown `DiagramView` left to `gc.collect()` has its scene, items and custom
cursors torn down in whatever order the collector reaches them, which
segfaulted CI in the test hygiene. Closing it and running its queued
`DeferredDelete` frees it in Qt's own (safe) order.
"""
from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget


def dispose(widgets: Iterable[QWidget]) -> None:
    for w in widgets:
        try:
            w.close()
            w.deleteLater()
        except RuntimeError:        # already deleted on the C++ side
            pass
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

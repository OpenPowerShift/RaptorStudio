"""A second window must not reset the application style: with the theme's
stylesheet on, the style's name is empty, every new window called
`setStyle(Fusion)`, and re-styling every widget crashed Qt (segfault)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_fusion_is_set_once_and_never_after_the_stylesheet(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from raptor.gui.ui.main_window import ensure_fusion
    app = QApplication.instance() or QApplication([])
    calls = []
    real = QApplication.setStyle
    monkeypatch.setattr(QApplication, "setStyle",
                        lambda self, style: (calls.append(style), real(self, style))[1])
    app.setProperty("raptorFusion", None)
    sheet = app.styleSheet()
    try:
        app.setStyleSheet("QWidget { color: black; }")
        assert app.style().objectName() == ""       # what fooled the name check
        ensure_fusion(app)
        ensure_fusion(app)
        assert calls == []
    finally:
        app.setStyleSheet(sheet)
        app.setProperty("raptorFusion", True)


def test_two_windows_do_not_restyle_the_application(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from raptor.gui.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    first = MainWindow()
    calls = []
    monkeypatch.setattr(QApplication, "setStyle",
                        lambda self, style: calls.append(style))
    second = MainWindow()
    try:
        assert calls == []
        assert app.style() is not None
    finally:
        for window in (second, first):
            window._persist_layout = False
            window.deleteLater()
        app.processEvents()

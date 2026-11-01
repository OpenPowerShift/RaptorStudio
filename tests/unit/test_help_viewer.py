"""The in-app help viewer must never spin up QtWebEngine.

GitHub issue #75: opening Help ▸ User Guide loaded a QtWebEngine view
and segfaulted the whole application inside Qt/PySide, losing unsaved
work. The help pages are local static HTML/AsciiDoc, so the viewer now
renders them with QTextBrowser. These tests pin that down, and check the
features that had to survive the replacement: page title/text, F1-style
anchor jumps, internal link following, find, and opsmodel:// dispatch.
"""
from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QTextBrowser

pytestmark = pytest.mark.gui

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def viewer(qapp):
    from raptor.gui.ui.help_viewer import HelpViewer
    dlg = HelpViewer()
    try:
        yield dlg
    finally:
        dlg.close()
        dlg.deleteLater()
        qapp.processEvents()


def _doc_url(dlg, fragment: str) -> QUrl:
    url = QUrl(dlg._view.source())
    url.setFragment(fragment)
    return url


def test_importing_the_viewer_never_loads_qtwebengine(monkeypatch, qapp):
    """Importing the help viewer with WebEngine made unavailable must work.

    This is the regression guard for the crash itself: if a future change
    reintroduces a `PySide6.QtWebEngine*` import, this fails before any
    window is shown."""
    real_import = builtins.__import__

    def deny_webengine(name, *args, **kwargs):
        if "QtWebEngine" in name:
            raise AssertionError(f"help viewer imported {name}")
        return real_import(name, *args, **kwargs)

    sys.modules.pop("raptor.gui.ui.help_viewer", None)
    monkeypatch.setattr(builtins, "__import__", deny_webengine)
    from raptor.gui.ui import help_viewer

    assert isinstance(help_viewer.HelpViewer(None)._view, QTextBrowser)
    assert not [m for m in sys.modules if m.startswith("PySide6.QtWebEngine")]


def test_viewer_opens_the_bundled_manual(viewer, qapp):
    viewer.show_doc("manual.html")
    qapp.processEvents()

    assert viewer.isVisible()
    assert "RAPTOR Studio Reference Manual" in viewer.windowTitle()
    text = viewer._view.toPlainText()
    assert "Model tab" in text
    assert "Debug tab: stepping debugger" in text


def test_show_doc_scrolls_to_an_f1_anchor(viewer, qapp):
    viewer.show_doc("manual.html", "stepping")
    qapp.processEvents()

    assert viewer._view.source().fragment() == "stepping"
    assert viewer._view.verticalScrollBar().value() > 0
    assert not viewer._view.document().isEmpty()


def test_viewer_follows_an_internal_anchor(viewer, qapp):
    viewer.show_doc("manual.html")
    qapp.processEvents()
    assert viewer._view.verticalScrollBar().value() == 0

    # What QTextBrowser emits when a TOC/link anchor is clicked.
    viewer._view.anchorClicked.emit(_doc_url(viewer, "results"))
    qapp.processEvents()

    assert viewer._view.source().fragment() == "results"
    assert viewer._view.verticalScrollBar().value() > 0


def test_opsmodel_links_open_the_referenced_model(qapp):
    opened: list[str] = []
    from raptor.gui.ui.help_viewer import HelpViewer
    dlg = HelpViewer(None, open_model=opened.append)
    try:
        dlg._follow_link(QUrl("opsmodel://models/Lantern1.rbds"))
    finally:
        dlg.close()
        dlg.deleteLater()
        qapp.processEvents()

    assert len(opened) == 1
    assert Path(opened[0]).name == "Lantern1.rbds"
    assert Path(opened[0]).is_file()


def test_external_links_go_to_the_system_browser(viewer, monkeypatch):
    opened: list[QUrl] = []
    monkeypatch.setattr(
        "raptor.gui.ui.help_viewer.QDesktopServices.openUrl",
        lambda url: opened.append(url))

    viewer._follow_link(QUrl("https://example.com/help"))

    assert [u.toString() for u in opened] == ["https://example.com/help"]


def test_find_selects_the_next_match(viewer, qapp):
    viewer.show_doc("manual.html")
    qapp.processEvents()

    viewer._find_edit.setText("Debug tab")
    qapp.processEvents()

    assert viewer._find_status.text() == ""
    assert "Debug tab" in viewer._view.textCursor().selectedText()

    viewer._find_edit.setText("no such text in the manual")
    qapp.processEvents()
    assert viewer._find_status.text() == "no matches"


def test_ai_assistant_help_pane_also_avoids_webengine(qapp):
    """Help ▸ AI Assistant shares `_open_doc` with the User Guide.

    Issue #75 also crashed this entry point, so the regression test opens
    it the way the ribbon command does rather than only constructing the
    viewer directly."""
    from raptor.gui.ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    try:
        win._open_doc("manual.html", "ai-assistant")
        qapp.processEvents()

        dlg = win._help_viewer
        assert isinstance(dlg._view, QTextBrowser)
        assert dlg._view.source().fragment() == "ai-assistant"
        assert "AI assistant (MCP)" in dlg._view.toPlainText()
        assert not [m for m in sys.modules
                    if m.startswith("PySide6.QtWebEngine")]
    finally:
        win._dirty = False
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_a_missing_page_is_shown_as_a_message(viewer, qapp):
    viewer.show_doc("this-page-does-not-exist.html")
    qapp.processEvents()

    assert viewer.isVisible()
    assert "was not found" in viewer._view.toPlainText()
    assert "RAPTOR Studio Help" in viewer.windowTitle()


def test_an_unreadable_page_does_not_escape_the_viewer(viewer, qapp, tmp_path):
    missing = tmp_path / "broken.html"
    viewer._load(QUrl.fromLocalFile(str(missing)))
    qapp.processEvents()

    assert "could not be opened" in viewer._view.toPlainText()
    # A later theme toggle must not resurrect the previously loaded page.
    viewer.refresh_theme(True)
    qapp.processEvents()
    assert "could not be opened" in viewer._view.toPlainText()

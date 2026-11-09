"""Help belongs to the process, not to one model window (#206).

Two MainWindows: A opens Help, closing A leaves the same viewer open for
B, closing the last window closes it, and an ``opsmodel://`` link follows
the current owner. The document-navigation rules from #207 are unit-tested
here too, with a fake web view, so they run without QtWebEngine.
"""
from __future__ import annotations

import os
import weakref

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from raptor.gui import open_models
from raptor.gui.ui import help_service as help_service_module
from raptor.gui.ui import help_viewer

pytestmark = pytest.mark.gui


class _FakeHelpViewer:
    """Enough of HelpViewer for the lifecycle service: no WebEngine."""

    def __init__(self, open_model=None, switch_to_app=None):
        self.open_model = open_model
        self.switch_to_app = switch_to_app
        self._owner_ref = None
        self.docs: list[tuple[str, str | None]] = []
        self.loads: list[QUrl] = []
        self.closed = False
        self.visible = False
        self.theme = None

    def set_owner(self, owner):
        self._owner_ref = weakref.ref(owner) if owner is not None else None

    @property
    def owner(self):
        return self._owner_ref() if self._owner_ref is not None else None

    def show_doc(self, name, anchor=None):
        self.docs.append((name, anchor))
        self.visible = True

    def refresh_theme(self, dark):
        self.theme = dark

    def close(self):
        self.closed = True
        self.visible = False

    def isVisible(self):
        return self.visible


@pytest.fixture
def two_windows(monkeypatch):
    """Two real MainWindows and a fake-viewer Help service for this test."""
    # Do not inherit windows/kept references from another test.
    monkeypatch.setattr(open_models, "_WINDOWS", [])
    monkeypatch.setattr(open_models, "_KEPT", [])
    monkeypatch.setattr(open_models, "_SERIAL", [0])

    app = QApplication.instance() or QApplication([])
    service = help_service_module.HelpService(viewer_factory=_FakeHelpViewer)
    monkeypatch.setattr(help_service_module, "_SERVICE", service)

    from raptor.gui.ui.main_window import MainWindow
    w1, w2 = MainWindow(), MainWindow()
    w1.show()
    w2.show()
    app.processEvents()
    try:
        yield w1, w2, service
    finally:
        for w in (w1, w2):
            w._dirty = False
            try:
                w.close()
            except RuntimeError:
                pass
        app.processEvents()
        service.close()


def _viewer_of(window):
    return window._show_help_viewer("book.html")


def test_closing_one_window_leaves_help_open_and_the_next_reuses_it(two_windows):
    w1, w2, service = two_windows
    viewer = _viewer_of(w1)
    assert viewer.isVisible()

    w1._dirty = False
    assert w1.close()
    QApplication.processEvents()

    assert w1 not in open_models.windows()
    assert service.viewer is viewer
    assert viewer.isVisible()
    assert not viewer.closed

    reused = w2._show_help_viewer("manual.html", "results")
    assert reused is viewer
    assert viewer.docs[-1] == ("manual.html", "results")
    assert service.owner is w2

    w2._dirty = False
    assert w2.close()
    QApplication.processEvents()

    assert service.viewer is None
    assert viewer.closed
    assert not viewer.isVisible()


def test_the_window_keeps_only_a_weak_compatibility_reference(two_windows):
    w1, _w2, service = two_windows
    viewer = _viewer_of(w1)

    assert isinstance(w1._help_viewer, weakref.ProxyTypes)
    assert w1._help_viewer is not viewer
    assert w1._help_viewer.isVisible()
    assert service.viewer is viewer


def test_ops_model_callback_goes_to_the_current_owner(two_windows, monkeypatch):
    w1, w2, service = two_windows
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        w1, "open_model_from_help", lambda path: calls.append(("w1", path)))
    monkeypatch.setattr(
        w2, "open_model_from_help", lambda path: calls.append(("w2", path)))

    viewer = _viewer_of(w1)
    viewer.open_model("first.rbds")
    assert calls[-1] == ("w1", "first.rbds")

    # B's Help action transfers ownership; the page callback now reaches B.
    w2._show_help_viewer("manual.html")
    assert service.owner is w2
    viewer.open_model("second.rbds")
    assert calls[-1] == ("w2", "second.rbds")

    # Closing the owner hands Help to the live window without losing it.
    w1.close()
    QApplication.processEvents()
    assert service.owner is w2
    viewer.open_model("third.rbds")
    assert calls[-1] == ("w2", "third.rbds")


# --------------------------------------------------------------- #207 rules
class _FakePage:
    def __init__(self):
        self.scripts: list[str] = []

    def runJavaScript(self, script):
        self.scripts.append(script)


class _FakeView:
    def __init__(self, url):
        self._url = QUrl(url)
        self.loaded: list[QUrl] = []
        self._page = _FakePage()

    def url(self):
        return self._url

    def load(self, url):
        self.loaded.append(QUrl(url))
        self._url = QUrl(url)

    def page(self):
        return self._page


class _Navigation(help_viewer._HelpDocumentNavigation):
    """The show_doc rules without a QDialog/WebEngine."""

    def __init__(self, url):
        self._view = _FakeView(url)
        self.presented = False

    def show(self):
        self.presented = True

    def raise_(self):
        pass

    def activateWindow(self):
        pass


def test_show_doc_does_not_reload_the_document_already_loaded(tmp_path, monkeypatch):
    book = tmp_path / "book.html"
    book.write_text("<html><body>book</body></html>", encoding="utf-8")
    monkeypatch.setattr(help_viewer, "_resolve_doc", lambda name: book)

    nav = _Navigation(QUrl.fromLocalFile(str(book)))
    nav.show_doc("book.html")

    assert nav._view.loaded == []
    assert nav.presented


def test_show_doc_scrolls_to_a_new_anchor_without_reloading(tmp_path, monkeypatch):
    book = tmp_path / "book.html"
    book.write_text("<html><body><h2 id='results'>Results</h2></body></html>",
                    encoding="utf-8")
    monkeypatch.setattr(help_viewer, "_resolve_doc", lambda name: book)

    nav = _Navigation(QUrl.fromLocalFile(str(book)))
    nav.show_doc("book.html", "results")

    assert nav._view.loaded == []
    assert len(nav._view.page().scripts) == 1
    assert "results" in nav._view.page().scripts[0]
    assert "scrollIntoView" in nav._view.page().scripts[0]


def test_show_doc_loads_a_different_document(tmp_path, monkeypatch):
    book = tmp_path / "book.html"
    manual = tmp_path / "manual.html"
    book.write_text("<html><body>book</body></html>", encoding="utf-8")
    manual.write_text("<html><body>manual</body></html>", encoding="utf-8")
    monkeypatch.setattr(
        help_viewer, "_resolve_doc",
        lambda name: manual if name == "manual.html" else book)

    nav = _Navigation(QUrl.fromLocalFile(str(book)))
    nav.show_doc("manual.html")

    assert len(nav._view.loaded) == 1
    assert nav._view.loaded[0].toLocalFile() == str(manual)

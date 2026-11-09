"""Help belongs to the process, not to one model window (#206).

Two MainWindows: A opens Help, closing A leaves the same viewer open for
B, closing the last window closes it, and an ``opsmodel://`` link follows
the current owner. The document-navigation rules from #207 are unit-tested
here too, with a fake web view, so they run without QtWebEngine.
"""
from __future__ import annotations

import os
import weakref
from pathlib import Path

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
        self.shutdown_called = False

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

    def shutdown(self):
        self.shutdown_called = True
        self.close()

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
    assert viewer.shutdown_called
    assert not viewer.isVisible()


def test_the_window_keeps_only_a_weak_compatibility_reference(two_windows):
    w1, w2, service = two_windows
    viewer = _viewer_of(w1)

    ref = w1._help_viewer
    assert isinstance(ref, weakref.ReferenceType)
    assert ref() is viewer
    assert service.viewer is viewer

    # Closing the window clears the attribute instead of leaving a dead
    # proxy that raises ReferenceError when read.
    w1._dirty = False
    assert w1.close()
    QApplication.processEvents()
    assert w1._help_viewer is None
    assert ref() is viewer          # Help is still open for w2

    w2._dirty = False
    assert w2.close()
    QApplication.processEvents()
    del viewer
    assert ref() is None


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


def test_activating_a_window_makes_it_the_help_owner(two_windows, monkeypatch):
    w1, w2, service = two_windows
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        w2, "open_model_from_help", lambda path: calls.append(("w2", path)))

    viewer = _viewer_of(w1)
    help_service_module.note_window_activated(w2)

    assert service.owner is w2
    viewer.open_model("from-b.rbds")
    assert calls == [("w2", "from-b.rbds")]


def test_closing_a_non_owner_window_leaves_help_alone(two_windows):
    w1, w2, service = two_windows
    viewer = _viewer_of(w1)
    w2._show_help_viewer("manual.html")
    assert service.owner is w2

    w1._dirty = False
    assert w1.close()
    QApplication.processEvents()

    assert service.viewer is viewer
    assert not viewer.closed
    assert service.owner is w2


def test_switch_to_app_routes_to_the_current_owner(two_windows, monkeypatch):
    w1, w2, _service = two_windows
    calls: list[str] = []
    monkeypatch.setattr(w2, "_switch_from_help", lambda: calls.append("w2"))

    viewer = _viewer_of(w1)
    w2._show_help_viewer("manual.html")
    viewer.switch_to_app()

    assert calls == ["w2"]


def test_quit_all_closes_help_and_stops_the_page(two_windows):
    w1, _w2, service = two_windows
    viewer = _viewer_of(w1)

    w1._quit_all()
    QApplication.processEvents()

    assert service.viewer is None
    assert viewer.closed
    assert viewer.shutdown_called


def test_about_to_quit_closes_help(two_windows):
    w1, _w2, service = two_windows
    viewer = _viewer_of(w1)

    QApplication.instance().aboutToQuit.emit()

    assert service.viewer is None
    assert viewer.closed
    assert viewer.shutdown_called


def test_route_ops_model_falls_back_to_the_last_live_window(
        two_windows, monkeypatch):
    _w1, w2, _service = two_windows
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        w2, "open_model_from_help", lambda path: calls.append(("w2", path)))
    monkeypatch.setattr(help_service_module, "_SERVICE", None)

    help_service_module.route_ops_model("fallback.rbds")

    assert calls == [("w2", "fallback.rbds")]


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
        self.loading = False

    def url(self):
        return self._url

    def isLoading(self):
        return self.loading

    def load(self, url):
        self.loaded.append(QUrl(url))
        self._url = QUrl(url)
        self.loading = True

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
    assert "history.replaceState" in nav._view.page().scripts[0]


def test_show_doc_reapplies_an_anchor_already_in_the_url(tmp_path, monkeypatch):
    book = tmp_path / "book.html"
    book.write_text("<html><body><h2 id='results'>Results</h2></body></html>",
                    encoding="utf-8")
    monkeypatch.setattr(help_viewer, "_resolve_doc", lambda name: book)
    url = QUrl.fromLocalFile(str(book))
    url.setFragment("results")

    nav = _Navigation(url)
    nav.show_doc("book.html", "results")     # user scrolled away since

    assert nav._view.loaded == []
    assert len(nav._view.page().scripts) == 1
    assert "scrollIntoView" in nav._view.page().scripts[0]


def test_show_doc_queues_an_anchor_while_the_page_is_loading(
        tmp_path, monkeypatch):
    book = tmp_path / "book.html"
    book.write_text("<html><body><h2 id='results'>Results</h2></body></html>",
                    encoding="utf-8")
    monkeypatch.setattr(help_viewer, "_resolve_doc", lambda name: book)

    nav = _Navigation(QUrl.fromLocalFile(str(book)))
    nav._view.loading = True
    nav.show_doc("book.html", "results")
    nav.show_doc("book.html", "results")     # rapid second Help action

    assert nav._view.loaded == []
    assert nav._view.page().scripts == []
    assert nav._pending_anchor == "results"

    nav._flush_pending_anchor(True)

    assert len(nav._view.page().scripts) == 1
    assert "results" in nav._view.page().scripts[0]
    assert nav._pending_anchor is None


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


# --------------------------------------------------------------- CSS copies
ROOT = Path(__file__).resolve().parents[2]


def test_the_two_css_copies_are_identical():
    """render_docs.sh copies new_docs/ops_asciidoctor.css to docs/; the
    viewer keys its stable TOC on rules that must ship in both."""
    assert (ROOT / "new_docs" / "ops_asciidoctor.css").read_bytes() == \
        (ROOT / "docs" / "ops_asciidoctor.css").read_bytes()

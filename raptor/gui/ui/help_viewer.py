"""In-app help viewer for RAPTOR Studio.

Renders the Asciidoctor-built help docs (new_docs/book.html, the tutorial
book; docs/manual.html, the reference manual) inside a QtWebEngine view
instead of shelling out to the system browser, so Help behaves like part
of the app:

* Ctrl+F finds text in the page (highlighted, next/previous).
* Links BETWEEN our help pages navigate in-app; anything external
  (http/https, or a file outside the docs tree) opens in the system
  browser instead.
* ``opsmodel://`` links (emitted by the opsmodel:: Asciidoctor macro,
  new_docs/tools/ops_adoc_ext.rb) open the referenced .rbds/.rbd model
  in the main window.
* The page colour scheme follows the app's light/dark theme toggle
  (the docs CSS adapts via prefers-color-scheme).

Kept self-contained so it degrades gracefully: when QtWebEngine is not
importable the helpers here simply don't load and MainWindow falls back
to QDesktopServices.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QUrl, QTimer, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QToolBar, QVBoxLayout, QWidget)

from raptor.bundled import candidate_roots
try:                                    # heavy optional dependency
    # Availability check only -- the names are re-imported locally at
    # their use sites (F401 is intentional here).
    from PySide6.QtWebEngineCore import (  # noqa: F401
        QWebEnginePage, QWebEngineProfile, QWebEngineSettings,
        QWebEngineUrlScheme, QWebEngineUrlSchemeHandler)
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_OK = True
except ImportError:                     # pragma: no cover - env dependent
    WEBENGINE_OK = False


#: Custom scheme emitted by the opsmodel:: AsciiDoc macro; clicking it
#: in the help viewer opens the model in RAPTOR Studio (see
#: MainWindow.open_model_from_help). Must be registered before the
#: QApplication (and therefore the WebEngine runtime) is constructed.
OPS_MODEL_SCHEME = "opsmodel"

#: Directory that holds this file: raptor/gui/ui/
_UI_DIR = Path(__file__).resolve().parent


def _doc_roots():
    """Local doc roots, nearest-first: the checkout, then the install's data
    directories (see `raptor.bundled`; when frozen, sys.prefix is
    PyInstaller's bundle root, where the spec datas put new_docs/ and docs/)."""
    return tuple(candidate_roots())


#: The help documents we treat as "ours" (in-app navigation). Anything
#: outside these roots goes to the system browser.
_DOC_ROOTS = _doc_roots()


_ops_scheme_registered = False


def _register_ops_scheme() -> None:
    """Register the opsmodel scheme with Chromium. Idempotent, and must
    run before the first QWebEngineProfile is created.

    `LocalScheme | LocalAccessAllowed` lets the file:// help pages click
    through to it. `NoAccessAllowed` is deliberately not set: it makes
    the renderer kill the navigation before anything sees it, so the
    click silently does nothing."""
    global _ops_scheme_registered
    if not WEBENGINE_OK or _ops_scheme_registered:
        return
    from PySide6.QtWebEngineCore import QWebEngineUrlScheme
    scheme = QWebEngineUrlScheme(OPS_MODEL_SCHEME.encode())
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.LocalScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed)
    QWebEngineUrlScheme.registerScheme(scheme)
    _ops_scheme_registered = True


def _under_docs_tree(url: QUrl) -> bool:
    """True when url is a local file inside one of the doc roots."""
    if not url.isLocalFile():
        return False
    path = Path(url.toLocalFile()).resolve()
    return any(_is_relative_to(path, root) for root in _DOC_ROOTS)


def _resolve_model_rel(rel: str) -> Path | None:
    """opsmodel:// target (e.g. models/Lantern1.rbds, as written in the
    docs, relative to the doc tree) -> absolute path, trying the docs
    subdirs; None when nothing on disk matches."""
    for root in _DOC_ROOTS:
        for sub in ("new_docs", "docs", ""):
            candidate = root / sub / rel
            if candidate.is_file():
                return candidate
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


if WEBENGINE_OK:

    #: Process-wide opsmodel:// handler reference (a profile keeps only
    #: a C++ pointer; the Python wrapper must not be GC'd).
    _OPS_HANDLER = None

    class _HelpPage(QWebEnginePage):
        """Page with the help link policy:

        * file:// inside the docs tree (book <-> manual, anchors,
          images) -> navigate in-app;
        * opsmodel://... -> open the model in the main window;
        * everything else -> system browser, navigation blocked.
        """

        open_model_requested = Signal(str)      # absolute model path

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if url.scheme() == OPS_MODEL_SCHEME:
                self._emit_model(url)
                return False
            if url.scheme() in ("http", "https"):
                QDesktopServices.openUrl(url)
                return False
            if _under_docs_tree(url):
                return True
            if url.isLocalFile():
                # A local file that is NOT our help: still ours to
                # open, just not in this viewer.
                QDesktopServices.openUrl(url)
                return False
            return False

        def _emit_model(self, url: QUrl) -> None:
            # opsmodel://models/Lantern1.rbds -> host-relative path
            rel = (url.host() + url.path()).lstrip("/")
            resolved = _resolve_model_rel(rel)
            self.open_model_requested.emit(str(resolved or rel))

    class _OpsModelSchemeHandler(QWebEngineUrlSchemeHandler):
        """The SUPPORTED dispatch path for opsmodel:// links: Chromium
        hands every request for the registered scheme to this handler
        (installed on the default profile), so a click always reaches
        us regardless of renderer navigation quirks. acceptNavigation-
        Request's opsmodel branch stays as a second net."""

        def __init__(self, on_open, parent=None):
            super().__init__(parent)
            self._on_open = on_open

        def requestReceived(self, job, request):
            from PySide6.QtWebEngineCore import (
                QWebEngineUrlRequestJob as J)
            url = request.url()
            rel = (url.host() + url.path()).lstrip("/")
            resolved = _resolve_model_rel(rel)
            QTimer.singleShot(
                0, lambda: self._on_open(str(resolved or rel)))
            job.fail(J.Error.RequestDenied)

    class HelpViewer(QDialog):
        """Modeless dialog hosting one help document with find + nav."""

        def __init__(self, parent=None, open_model=None, switch_to_app=None):
            super().__init__(parent)
            self.setWindowTitle("RAPTOR Studio Help")
            self.setModal(False)
            self.resize(1100, 800)
            self._view: QWebEngineView
            self._open_model = open_model   # callable(str) or None
            self._switch_to_app = switch_to_app   # callable() or None

            # opsmodel:// dispatch (process-wide profile; the handler
            # object must outlive this dialog, hence the module ref).
            global _OPS_HANDLER
            if _OPS_HANDLER is None:
                from PySide6.QtWebEngineCore import QWebEngineProfile
                profile = QWebEngineProfile.defaultProfile()
                _OPS_HANDLER = _OpsModelSchemeHandler(
                    self._dispatch_model, profile)
                profile.installUrlSchemeHandler(
                    OPS_MODEL_SCHEME.encode(), _OPS_HANDLER)

            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)

            bar = QToolBar()
            bar.setMovable(False)
            lay.addWidget(bar)

            def act(text, tip, slot):
                from PySide6.QtGui import QAction
                a = QAction(text, self)
                a.setToolTip(tip)
                a.triggered.connect(slot)
                bar.addAction(a)
                return a

            # Main-topic buttons, mirroring the Help ribbon page, so any help
            # page is one click away.
            self._guide_btn = act("User Guide",
                                  "The User Guide: reliability simulation from scratch",
                                  lambda: self.show_doc("book.html"))
            self._manual_btn = act("Reference Manual",
                                   "The field-by-field reference "
                                   "(F1 jumps to the focused panel's section)",
                                   lambda: self.show_doc("manual.html"))
            self._formulas_btn = act("Formulas",
                                     "Reliability formulas quick reference "
                                     "— every reported metric as an "
                                     "equation, one page",
                                     lambda: self.show_doc("formulas.html"))

            bar.addSeparator()
            self._back = act("←", "Back", lambda: self._view.back())
            self._fwd = act("→", "Forward", lambda: self._view.forward())
            self._reload = act("⟳", "Reload", lambda: self._view.reload())
            self._home = act("⌂", "Back to the User Guide (book)",
                             lambda: self.show_doc("book.html"))

            spacer = QWidget()
            from PySide6.QtWidgets import QSizePolicy
            spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            bar.addWidget(spacer)

            # Switch to App: bring the main application window forward without
            # closing the help browser.
            if self._switch_to_app is not None:
                self._switch_btn = act(
                    "Switch to App",
                    "Bring the main application window forward",
                    self._switch_to_app)

            self._browser_btn = act(
                "Open in browser", "Show this page in the system browser",
                self._open_in_system_browser)

            # ---- find bar -------------------------------------------
            frow = QHBoxLayout()
            frow.setContentsMargins(6, 4, 6, 4)
            self._find_edit = QLineEdit()
            self._find_edit.setPlaceholderText(
                "Find in page  (Enter=next, Shift+Enter=previous)")
            self._find_edit.returnPressed.connect(self._find_next)
            self._find_edit.textChanged.connect(self._find_changed)
            frow.addWidget(QLabel("Find:"))
            frow.addWidget(self._find_edit, 1)

            from PySide6.QtGui import QAction
            from PySide6.QtWidgets import QToolButton
            prev_b, next_b = QToolButton(), QToolButton()
            prev_b.setDefaultAction(QAction("▲", self))
            prev_b.setToolTip("Previous match (Shift+Enter)")
            prev_b.triggered.connect(self._find_prev)
            next_b.setDefaultAction(QAction("▼", self))
            next_b.setToolTip("Next match (Enter)")
            next_b.triggered.connect(self._find_next)
            frow.addWidget(prev_b)
            frow.addWidget(next_b)
            self._find_status = QLabel("")
            self._find_status.setStyleSheet("color: gray;")
            frow.addWidget(self._find_status)
            lay.addLayout(frow)

            self._page = _HelpPage(self)
            self._page.open_model_requested.connect(self._on_open_model)
            self._view = QWebEngineView(self)
            self._view.setPage(self._page)
            self._view.titleChanged.connect(self._on_title)
            lay.addWidget(self._view, 1)

            cast(Any, self._page).view = self._view   # toolbar home target

            # shortcuts
            QShortcut(QKeySequence.StandardKey.Find, self).activated.connect(self._focus_find)
            QShortcut(QKeySequence("Escape"), self).activated.connect(self._escape)
            QShortcut(QKeySequence("Shift+Return"), self._find_edit).activated.connect(self._find_prev)

        # ------------------------------------------------------------ doc
        def show_doc(self, name: str, anchor: str | None = None) -> None:
            """Load one of our bundled docs (book.html / manual.html),
            optionally scrolled to an anchor (F1 context help)."""
            path = _resolve_doc(name)
            if path is None:
                return
            url = QUrl.fromLocalFile(str(path))
            if anchor:
                url.setFragment(anchor)
            self._view.load(url)
            self.show()
            self.raise_()
            self.activateWindow()

        # ------------------------------------------------------------ nav
        def _on_title(self, title: str) -> None:
            self.setWindowTitle(f"{title} — RAPTOR Studio Help")

        def _open_in_system_browser(self) -> None:
            url = self._view.url()
            if url.isValid():
                QDesktopServices.openUrl(url)

        def _on_open_model(self, path: str) -> None:
            if self._open_model:
                self._open_model(path)

        def _dispatch_model(self, path: str) -> None:
            """Scheme-handler callback (also used by the page signal)."""
            self._on_open_model(path)

        # ------------------------------------------------------------ find
        def _focus_find(self) -> None:
            self._find_edit.setFocus()
            self._find_edit.selectAll()

        def _flags(self, backwards: bool = False):
            flags = QWebEnginePage.FindFlag(0)
            if backwards:
                flags |= QWebEnginePage.FindFlag.FindBackward
            return flags

        def _find_changed(self, text: str) -> None:
            if not text:
                self._page.findText("")
                self._find_status.clear()
                return
            self._page.findText(
                text, self._page.FindFlag(0), self._find_status_cb)

        def _find_next(self) -> None:
            text = self._find_edit.text()
            if text:
                self._page.findText(text)

        def _find_prev(self) -> None:
            text = self._find_edit.text()
            if text:
                self._page.findText(text, self._flags(backwards=True))

        def _find_status_cb(self, found: bool) -> None:
            self._find_status.setText(
                "" if found else "no matches")

        def _escape(self) -> None:
            if self._find_edit.hasFocus() or self._find_edit.text():
                self._page.findText("")
                self._find_status.clear()
                self._view.setFocus()
                return
            self.close()

        # ------------------------------------------------------------ theme
        #: Mirrors the @media (prefers-color-scheme: dark) block in
        #: ops_asciidoctor.css. WebEngine's Chromium build reports light
        #: regardless of the app theme, so the dark palette is injected
        #: as inline :root variables (inline always wins the cascade).
        _DARK_VARS_JS = """
(function(){
  const v = document.documentElement.style;
  const set = (k, val) => v.setProperty(k, val);
  set('--color-text', '#e2e8f0');
  set('--color-background', '#1e293b');
  set('--color-link', '#fb923c');
  set('--color-heading', '#f1f5f9');
  set('--color-border', '#535e68');
  set('--color-code-bg', '#23282c');
  set('--color-code-text', '#cbd5e1');
  set('--color-table-stripe', '#394046');
  set('--color-admonition-bg', '#394046');
})();
"""
        _LIGHT_VARS_JS = """
(function(){
  const v = document.documentElement.style;
  ['--color-text','--color-background','--color-link','--color-heading',
   '--color-border','--color-code-bg','--color-code-text',
   '--color-table-stripe','--color-admonition-bg'
  ].forEach(k => v.removeProperty(k));
})();
"""

        def refresh_theme(self, dark: bool) -> None:
            """Follow the app's light/dark toggle (see _DARK_VARS_JS)."""
            self._view.page().runJavaScript(
                self._DARK_VARS_JS if dark else self._LIGHT_VARS_JS)


def _resolve_doc(name: str) -> Path | None:
    """Find a bundled doc HTML (book.html / manual.html), nearest first.
    Mirrors MainWindow._doc_candidates."""
    for root in _DOC_ROOTS:
        for sub in ("docs", "new_docs"):
            candidate = root / sub / name
            if candidate.is_file():
                return candidate
    return None

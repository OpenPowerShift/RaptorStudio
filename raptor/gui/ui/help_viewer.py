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

import json
import weakref
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
        QWebEngineLoadingInfo, QWebEnginePage, QWebEngineProfile,
        QWebEngineScript, QWebEngineSettings, QWebEngineUrlScheme,
        QWebEngineUrlSchemeHandler)
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


def _same_document(current: QUrl, target: QUrl) -> bool:
    """True when both URLs point at the same local help file (fragments
    ignored). Used to avoid a full reload when Help is invoked again."""
    if not current.isValid() or not current.isLocalFile():
        return False
    if not target.isValid() or not target.isLocalFile():
        return False
    try:
        return Path(current.toLocalFile()).resolve() == \
            Path(target.toLocalFile()).resolve()
    except OSError:                         # pragma: no cover - exotic paths
        return current.toLocalFile() == target.toLocalFile()


class _HelpDocumentNavigation:
    """Document loading/scroll policy for the help viewer.

    Kept separate from the QtWebEngine widget so a unit test can drive it
    with a fake `_view`: the rules below are what the lifecycle and
    same-document tests care about, and they do not need Chromium.
    """

    #: Filled in by HelpViewer; the fake in tests provides the same API.
    _view: Any
    #: Anchor requested while the page was still loading; flushed after the
    #: matching navigation succeeds. The target URL plus an increasing load
    #: serial identify the in-flight navigation that owns it (Qt may strip
    #: fragments from loading-event URLs, so the serial matters).
    _pending_anchor: str | None = None
    _pending_document: QUrl | None = None
    _pending_load_url: QUrl | None = None
    _pending_generation: int = 0
    _navigation_generation: int = 0
    _inflight_url: QUrl | None = None
    _load_serial: int = 0
    _pending_load_serial: int = 0

    def show_doc(self, name: str, anchor: str | None = None) -> None:
        """Load one of our bundled docs (book.html / manual.html),
        optionally scrolled to an anchor (F1 context help).

        Re-invoking Help for the document already on screen must not
        reload it: a reload resets the scroll position (and, before #207,
        the TOC reflow moved it to a different section anyway). A new
        anchor in the same document scrolls in place instead.
        """
        path = _resolve_doc(name)
        if path is None:
            return
        url = QUrl.fromLocalFile(str(path))
        if anchor:
            url.setFragment(anchor)
        self._navigation_generation += 1
        generation = self._navigation_generation
        current = self._view.url()
        if _same_document(current, url):
            if anchor:
                if self._document_loading():
                    # A second Help action while the first load is still
                    # running: the DOM is not ready, so scroll on load.
                    self._queue_anchor(
                        anchor, url, generation,
                        bind_serial=(self._inflight_url is None
                                     or _same_document(self._inflight_url,
                                                      url)))
                else:
                    self._clear_pending_anchor()
                    self._scroll_to_anchor(anchor)
            else:
                # Re-opening the same document at no particular section
                # keeps the reading position, and cancels an older queued
                # anchor so it cannot fire later.
                self._clear_pending_anchor()
        else:
            self._clear_pending_anchor()
            if anchor:
                self._queue_anchor(anchor, url, generation)
            self._view.load(url)
        self._present()

    def _queue_anchor(self, anchor: str, url: QUrl, generation: int,
                      *, bind_serial: bool = False) -> None:
        self._pending_anchor = anchor
        self._pending_document = QUrl(url)
        self._pending_load_url = QUrl(url)
        self._pending_generation = generation
        self._pending_load_serial = self._load_serial if bind_serial else 0

    def _clear_pending_anchor(self) -> None:
        self._pending_anchor = None
        self._pending_document = None
        self._pending_load_url = None
        self._pending_generation = 0
        self._pending_load_serial = 0

    def _document_loading(self) -> bool:
        try:
            page = self._view.page()
            check = getattr(page, "isLoading", None)
            return bool(check()) if callable(check) else False
        except RuntimeError:                 # page already gone
            return False

    def _load_started(self, url: QUrl) -> None:
        """Record the latest in-flight navigation.

        Qt may strip the fragment from `url`; the serial is what
        distinguishes two same-document navigations. Only adopt the serial
        when the started document is the pending anchor's target, so a
        late start for a superseded navigation cannot invalidate it."""
        self._load_serial += 1
        self._inflight_url = QUrl(url)
        if self._pending_anchor is not None \
                and self._pending_document is not None \
                and _same_document(url, self._pending_document):
            self._pending_load_serial = self._load_serial

    def _document_loaded(self, ok: bool, url: QUrl | None = None) -> None:
        """A page navigation reported its result.

        Apply a queued anchor only if this is the navigation that owns it.
        `url` is the event's own URL (QWebEngineLoadingInfo), not the
        view's current one. While another navigation is still loading,
        the event belongs to an older one; any completed navigation for
        an unrelated document cancels the queued anchor rather than
        leaving it to replay later."""
        if not self._document_loading():
            self._inflight_url = None
        anchor = self._pending_anchor
        if anchor is None:
            return
        if self._document_loading():
            # A newer navigation is in flight: this result is from an
            # older load (possibly the same file with a different
            # fragment), so it must not consume the newest anchor.
            return
        if url is not None and self._pending_load_url is not None \
                and not _same_document(url, self._pending_load_url):
            # Navigated away (or an older navigation completed): the
            # queued anchor belongs to a document no longer being loaded.
            self._clear_pending_anchor()
            return
        if self._pending_generation != self._navigation_generation:
            # A newer Help request superseded this queued anchor.
            return
        if not ok:
            if self._pending_load_serial and \
                    self._pending_load_serial != self._load_serial:
                # An older navigation stopped/failed; keep waiting for
                # the one that queued this anchor.
                return
            self._clear_pending_anchor()
            return
        self._clear_pending_anchor()
        self._scroll_to_anchor(anchor)

    def _flush_pending_anchor(self, ok: bool = True) -> None:
        """Apply the queued anchor using the view's current URL (tests)."""
        self._document_loaded(ok, self._view.url())

    def _scroll_to_anchor(self, anchor: str) -> None:
        """Scroll within the loaded document (no reload, no history entry)."""
        script = f"""
(function() {{
  const el = document.getElementById({json.dumps(anchor)});
  if (!el) return false;
  const root = document.documentElement;
  const previous = root.style.scrollBehavior;
  root.style.scrollBehavior = 'auto';
  el.scrollIntoView({{block: 'start'}});
  root.style.scrollBehavior = previous;
  try {{ history.replaceState(null, '', '#' + {json.dumps(anchor)}); }}
  catch (e) {{ /* file:// may forbid it on some builds */ }}
  return true;
}})();
"""
        self._view.page().runJavaScript(script)

    def _present(self) -> None:
        window = cast(Any, self)
        window.show()
        window.raise_()
        window.activateWindow()


#: Injected at DocumentReady into every help page (QWebEngineScript, below).
#:
#: 1. Adds `raptor-viewer`, which ops_asciidoctor.css uses to keep the TOC
#:    as a fixed sidebar at every window width. The viewer starts at 1100px
#:    (below the 80rem breakpoint); maximising used to move the inline TOC
#:    out of flow and change the document height under a fixed scrollTop.
#: 2. Tracks the nearest heading at the top of the viewport and its offset,
#:    and restores that pair after a resize. Width changes still re-wrap
#:    prose (so a numeric scrollTop lands on a different section); keeping
#:    heading+offset makes maximise/restore read the same place.
_VIEWER_PAGE_JS = r"""
(function () {
  const body = document.body;
  if (body) body.classList.add('raptor-viewer');

  const state = {
    id: '',
    index: 0,
    offset: 0,
    saved: false
  };
  window.__raptorReading = state;

  function headings() {
    return Array.from(document.querySelectorAll(
      '#content h1, #content h2, #content h3, #content h4, ' +
      '#content h5, #content h6'));
  }

  function capture() {
    const hs = headings();
    if (!hs.length) return null;
    // The last heading at or above the viewport's top edge is the section
    // being read: the same "nearest visible heading" a person would name.
    let current = hs[0];
    for (const h of hs) {
      if (h.getBoundingClientRect().top <= 8) current = h;
      else break;
    }
    state.id = current.id || '';
    state.index = hs.indexOf(current);
    state.offset = current.getBoundingClientRect().top;
    state.saved = true;
    return state;
  }

  function restore() {
    if (!state.saved) return;
    const hs = headings();
    const heading = (state.id && document.getElementById(state.id)) ||
                    hs[state.index];
    if (!heading) return;
    const delta = heading.getBoundingClientRect().top - state.offset;
    if (Math.abs(delta) <= 0.5) return;
    // The stylesheet asks for smooth scrolling; that would still be
    // animating when the user lets go of the maximise button.
    const root = document.documentElement;
    const previous = root.style.scrollBehavior;
    root.style.scrollBehavior = 'auto';
    window.scrollBy(0, delta);
    root.style.scrollBehavior = previous;
  }

  window.__raptorCaptureReading = capture;
  window.__raptorRestoreReading = restore;

  let pending = false;
  window.addEventListener('scroll', function () {
    if (window.__raptorRestoring || pending) return;
    pending = true;
    requestAnimationFrame(function () {
      pending = false;
      capture();
    });
  }, {passive: true});

  window.addEventListener('resize', function () {
    window.__raptorRestoring = true;
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        restore();
        window.__raptorRestoreCount =
          (window.__raptorRestoreCount || 0) + 1;
        window.__raptorRestoring = false;
      });
    });
  });

  capture();
})();
"""


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
        Request's opsmodel branch stays as a second net.

        The handler is process-wide and stores no viewer reference: it
        routes through `help_service.route_ops_model`, so closing Help
        can destroy the WebEngine viewer without leaving this handler
        (and the profile) pinning it."""

        def requestReceived(self, job, request):
            from PySide6.QtWebEngineCore import (
                QWebEngineUrlRequestJob as J)
            from raptor.gui.ui.help_service import route_ops_model
            url = request.url()
            rel = (url.host() + url.path()).lstrip("/")
            resolved = _resolve_model_rel(rel)
            QTimer.singleShot(
                0, lambda: route_ops_model(str(resolved or rel)))
            job.fail(J.Error.RequestDenied)

    class HelpViewer(_HelpDocumentNavigation, QDialog):
        """Modeless dialog hosting one help document with find + nav.

        The process-wide `HelpService` owns the single instance; a window
        only borrows it, and `set_owner` decides which window a click in
        the page acts on.
        """

        def __init__(self, parent=None, open_model=None, switch_to_app=None):
            super().__init__(parent)
            self.setWindowTitle("RAPTOR Studio Help")
            self.setModal(False)
            self.resize(1100, 800)
            self._view: QWebEngineView
            self._open_model = open_model   # callable(str) or None
            self._switch_to_app = switch_to_app   # callable() or None
            self._owner_ref: weakref.ref | None = None

            # opsmodel:// dispatch (process-wide profile; the handler
            # object must outlive every viewer, hence the module ref).
            # It carries nothing viewer-specific after construction.
            global _OPS_HANDLER
            if _OPS_HANDLER is None:
                from PySide6.QtWebEngineCore import QWebEngineProfile
                profile = QWebEngineProfile.defaultProfile()
                _OPS_HANDLER = _OpsModelSchemeHandler(profile)
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
            self._switch_btn = act(
                "Switch to App",
                "Bring the main application window forward",
                self._switch_to_app_clicked)

            self._browser_btn = act(
                "Open in browser", "Show this page in the system browser",
                self._open_in_system_browser)

            # ---- find bar -------------------------------------------
            frow = QHBoxLayout()
            frow.setSpacing(8)
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
            self._page.loadingChanged.connect(self._on_loading_changed)
            lay.addWidget(self._view, 1)
            self._install_page_scripts()

            cast(Any, self._page).view = self._view   # toolbar home target

            # shortcuts
            QShortcut(QKeySequence.StandardKey.Find, self).activated.connect(self._focus_find)
            QShortcut(QKeySequence("Escape"), self).activated.connect(self._escape)
            QShortcut(QKeySequence("Shift+Return"), self._find_edit).activated.connect(self._find_prev)

        def _install_page_scripts(self) -> None:
            """Add the stable-layout + reading-position script to every
            page this viewer loads (in-app cross-links included)."""
            script = QWebEngineScript()
            script.setName("raptor-help-viewer")
            script.setSourceCode(_VIEWER_PAGE_JS)
            script.setInjectionPoint(
                QWebEngineScript.InjectionPoint.DocumentReady)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            self._page.scripts().insert(script)

        # ----------------------------------------------------------- owner
        def set_owner(self, owner: Any | None) -> None:
            """Remember which MainWindow Help actions and callbacks act on."""
            self._owner_ref = weakref.ref(owner) if owner is not None else None

        @property
        def owner(self) -> Any | None:
            ref = self._owner_ref
            owner = ref() if ref is not None else None
            if owner is None:
                return None
            try:
                from shiboken6 import isValid
                return owner if isValid(owner) else None
            except (ImportError, RuntimeError, TypeError):
                return owner

        def _target_owner(self) -> Any | None:
            """Current owner, else the newest live window (direct use)."""
            owner = self.owner
            if owner is not None:
                return owner
            from raptor.gui import open_models
            live = open_models.windows()
            return live[-1] if live else None

        # ------------------------------------------------------------ nav
        def _on_loading_changed(self, info: QWebEngineLoadingInfo) -> None:
            """Feed per-navigation start/result to the pending-anchor queue."""
            status = info.status()
            if status == QWebEngineLoadingInfo.LoadStatus.LoadStartedStatus:
                self._load_started(info.url())
            elif status == QWebEngineLoadingInfo.LoadStatus.LoadSucceededStatus:
                self._document_loaded(True, info.url())
            elif status in (
                    QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus,
                    QWebEngineLoadingInfo.LoadStatus.LoadStoppedStatus):
                self._document_loaded(False, info.url())

        def _on_title(self, title: str) -> None:
            self.setWindowTitle(f"{title} — RAPTOR Studio Help")

        def _open_in_system_browser(self) -> None:
            url = self._view.url()
            if url.isValid():
                QDesktopServices.openUrl(url)

        def _on_open_model(self, path: str) -> None:
            if self._open_model is not None:
                self._open_model(path)
                return
            owner = self._target_owner()
            if owner is not None:
                owner.open_model_from_help(path)

        def _switch_to_app_clicked(self) -> None:
            if self._switch_to_app is not None:
                self._switch_to_app()
                return
            owner = self._target_owner()
            if owner is not None:
                owner._switch_from_help()

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

        def shutdown(self) -> None:
            """Stop the page and schedule deletion before the app goes away.

            `HelpService.close` calls this for the last window and on
            aboutToQuit. Stopping first avoids a load completing while the
            view is being destroyed; `deleteLater` lets Qt free the page in
            its own order (a Python `del` here is the #75 crash shape).
            """
            try:
                self._view.stop()
            except RuntimeError:             # C++ side already gone
                pass
            try:
                self._page.triggerAction(
                    QWebEnginePage.WebAction.Stop)
            except RuntimeError:
                pass
            try:
                self.close()
                self.deleteLater()
            except RuntimeError:
                pass

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

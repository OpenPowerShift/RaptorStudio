"""In-app help viewer for RAPTOR Studio.

Renders the Asciidoctor-built help docs (new_docs/book.html, the tutorial
book; docs/manual.html, the reference manual) in a plain ``QTextBrowser``.

The viewer used to host them in a QtWebEngine view. That engine crashed
the whole process on some systems while the help window was being shown
(GitHub issue #75: a segfault inside Qt/PySide's QObject property
delivery, before any application code ran). The docs are local static
HTML/AsciiDoc; they need no JavaScript and no browser engine, so Qt's
own rich-text renderer is both sufficient and far safer. Keeping
QtWebEngine out of the import path means merely opening Help can never
start Chromium.

Behaviour kept from the WebEngine viewer:

* Ctrl+F finds text in the page (next/previous).
* Links BETWEEN our help pages navigate in-app; anything external
  (http/https, or a file outside the docs tree) opens in the system
  browser instead.
* ``opsmodel://`` links (emitted by the opsmodel:: Asciidoctor macro,
  new_docs/tools/ops_adoc_ext.rb) open the referenced .rbds/.rbd model
  in the main window.
* The page colour scheme follows the app's light/dark theme toggle.

A missing or unreadable page is shown as a short message inside the
browser, never as an exception.
"""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import (QDesktopServices, QKeySequence, QShortcut,
                           QTextDocument)
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QSizePolicy, QTextBrowser, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

from raptor.bundled import candidate_roots
from raptor.gui.resources.design_tokens import DARK_COLOUR, LIGHT_COLOUR


#: Custom scheme emitted by the opsmodel:: AsciiDoc macro; clicking it
#: in the help viewer opens the model in RAPTOR Studio (see
#: MainWindow.open_model_from_help).
OPS_MODEL_SCHEME = "opsmodel"


def _doc_roots():
    """Local doc roots, nearest-first: the checkout, then the install's data
    directories (see `raptor.bundled`; when frozen, sys.prefix is
    PyInstaller's bundle root, where the spec datas put new_docs/ and docs/)."""
    return tuple(candidate_roots())


#: The help documents we treat as "ours" (in-app navigation). Anything
#: outside these roots goes to the system browser.
_DOC_ROOTS = _doc_roots()


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


def _document_css(colour: dict) -> str:
    """Qt rich-text stylesheet for the help pages.

    The Asciidoctor CSS uses CSS features Qt's renderer does not
    implement, so the generated pages are restyled with the app's own
    tokens instead of loading ops_asciidoctor.css. Only properties in
    Qt's supported CSS subset are used."""
    return f"""
body {{ color: {colour['text']}; background-color: {colour['surface']};
       font-family: "Inter", "Segoe UI", "Noto Sans", sans-serif;
       font-size: 14px; }}
h1, h2, h3, h4, h5 {{ color: {colour['text']}; }}
a {{ color: {colour['accent']}; }}
p, li, td, th, dt, dd {{ color: {colour['text']}; }}
code, pre {{ background-color: {colour['input_bg']};
             color: {colour['text']};
             font-family: "DejaVu Sans Mono", "Consolas", monospace; }}
pre {{ border: 1px solid {colour['border']}; }}
table {{ border: 1px solid {colour['border']}; }}
th {{ background-color: {colour['group_header']}; }}
td, th {{ border: 1px solid {colour['border']}; }}
"""


class HelpViewer(QDialog):
    """Modeless dialog hosting one help document with find + nav."""

    def __init__(self, parent=None, open_model=None, switch_to_app=None):
        super().__init__(parent)
        self.setWindowTitle("RAPTOR Studio Help")
        self.setModal(False)
        self.resize(1100, 800)
        self._open_model = open_model              # callable(str) or None
        self._switch_to_app = switch_to_app        # callable() or None
        self._dark = False
        #: True while an error message (not a bundled page) is displayed.
        self._error_shown = False

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
        self._guide_btn = act(
            "User Guide",
            "The User Guide: reliability simulation from scratch",
            lambda: self.show_doc("book.html"))
        self._manual_btn = act(
            "Reference Manual",
            "The field-by-field reference "
            "(F1 jumps to the focused panel's section)",
            lambda: self.show_doc("manual.html"))
        self._formulas_btn = act(
            "Formulas",
            "Reliability formulas quick reference — every reported "
            "metric as an equation, one page",
            lambda: self.show_doc("formulas.html"))

        bar.addSeparator()
        self._back = act("←", "Back", self._go_back)
        self._back.setEnabled(False)
        self._fwd = act("→", "Forward", self._go_forward)
        self._fwd.setEnabled(False)
        self._reload = act("⟳", "Reload", self._reload_page)
        self._home = act("⌂", "Back to the User Guide (book)",
                         lambda: self.show_doc("book.html"))

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)
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

        self._view = QTextBrowser(self)
        # Intercept anchors ourselves: in-app links navigate here, external
        # ones open in the system browser, opsmodel:// opens a model.
        self._view.setOpenLinks(False)
        self._view.setOpenExternalLinks(False)
        self._view.anchorClicked.connect(self._follow_link)
        self._view.sourceChanged.connect(self._on_source_changed)
        self._view.backwardAvailable.connect(self._back.setEnabled)
        self._view.forwardAvailable.connect(self._fwd.setEnabled)
        lay.addWidget(self._view, 1)

        self.refresh_theme(False)

        # shortcuts
        QShortcut(QKeySequence.StandardKey.Find,
                  self).activated.connect(self._focus_find)
        QShortcut(QKeySequence("Escape"),
                  self).activated.connect(self._escape)
        QShortcut(QKeySequence("Shift+Return"),
                  self._find_edit).activated.connect(self._find_prev)

    # ------------------------------------------------------------ doc
    def show_doc(self, name: str, anchor: str | None = None) -> None:
        """Load one of our bundled docs (book.html / manual.html),
        optionally scrolled to an anchor (F1 context help)."""
        path = _resolve_doc(name)
        if path is None:
            self._show_error(
                f"{html.escape(name)} was not found next to the application.",
                "The documentation ships separately — see the repository's "
                "new_docs/ (book) and docs/ (manual) folders.")
        else:
            url = QUrl.fromLocalFile(str(path))
            if anchor:
                url.setFragment(anchor)
            self._load(url)
        self.show()
        self.raise_()
        self.activateWindow()

    def _load(self, url: QUrl) -> None:
        """Load url into the text browser. A bad page shows a message
        inside the browser instead of propagating an error."""
        if url.isLocalFile():
            path = Path(url.toLocalFile())
            try:
                # Read once up front: QTextBrowser.setSource() reports an
                # unreadable file only by logging and leaving the old page
                # up, which would look like the link did nothing.
                path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                self._show_error("This help page could not be opened.",
                                 html.escape(str(exc)))
                return
        try:
            self._view.setSource(url)
        except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
            self._show_error("This help page could not be opened.",
                             html.escape(str(exc)))
            return
        self._error_shown = False

    def _show_error(self, headline: str, detail: str = "") -> None:
        body = f"<h2>{headline}</h2>"
        if detail:
            body += f"<p>{detail}</p>"
        self._view.setHtml(body)
        self._error_shown = True
        self.setWindowTitle("RAPTOR Studio Help")

    # ------------------------------------------------------------ nav
    def _on_source_changed(self, _url: QUrl) -> None:
        title = self._view.document().metaInformation(
            QTextDocument.MetaInformation.DocumentTitle)
        if not title and self._view.source().isLocalFile():
            title = Path(self._view.source().toLocalFile()).stem
        self.setWindowTitle(f"{title or 'Help'} — RAPTOR Studio Help")

    def _follow_link(self, url: QUrl) -> None:
        """The browser's anchorClicked policy (setOpenLinks is off):

        * file:// inside the docs tree (book <-> manual, anchors,
          images) -> navigate in-app;
        * opsmodel://... -> open the model in the main window;
        * everything else -> system browser.
        """
        if url.scheme() == OPS_MODEL_SCHEME:
            rel = (url.host() + url.path()).lstrip("/")
            resolved = _resolve_model_rel(rel)
            self._on_open_model(str(resolved or rel))
            return
        if url.isLocalFile():
            if _under_docs_tree(url):
                self._load(url)
                return
            # A local file that is NOT our help: open it somewhere else.
            QDesktopServices.openUrl(url)
            return
        if url.scheme() in ("http", "https", "mailto", "ftp"):
            QDesktopServices.openUrl(url)
            return
        # A fragment-only link that Qt could not resolve against the
        # current source: scroll within the page we already have.
        if url.hasFragment():
            self._view.scrollToAnchor(url.fragment())
            return
        if url.isValid():
            QDesktopServices.openUrl(url)

    def _go_back(self) -> None:
        self._view.backward()

    def _go_forward(self) -> None:
        self._view.forward()

    def _reload_page(self) -> None:
        self._view.reload()

    def _open_in_system_browser(self) -> None:
        if self._error_shown:
            return
        url = self._view.source()
        if url.isLocalFile() and Path(url.toLocalFile()).is_file():
            QDesktopServices.openUrl(url)

    def _on_open_model(self, path: str) -> None:
        if self._open_model:
            self._open_model(path)

    # ------------------------------------------------------------ find
    def _focus_find(self) -> None:
        self._find_edit.setFocus()
        self._find_edit.selectAll()

    def _flags(self, backwards: bool = False) -> QTextDocument.FindFlag:
        flags = QTextDocument.FindFlag(0)
        if backwards:
            flags |= QTextDocument.FindFlag.FindBackward
        return flags

    def _find(self, backwards: bool = False) -> bool:
        text = self._find_edit.text()
        if not text:
            return False
        found = self._view.find(text, self._flags(backwards))
        self._find_status.setText("" if found else "no matches")
        return found

    def _find_changed(self, text: str) -> None:
        if not text:
            self._view.textCursor().clearSelection()
            self._find_status.clear()
            return
        self._find()

    def _find_next(self) -> None:
        self._find()

    def _find_prev(self) -> None:
        self._find(backwards=True)

    def _escape(self) -> None:
        if self._find_edit.hasFocus() or self._find_edit.text():
            self._view.textCursor().clearSelection()
            self._find_status.clear()
            self._find_edit.clear()
            self._view.setFocus()
            return
        self.close()

    # ------------------------------------------------------------ theme
    def refresh_theme(self, dark: bool) -> None:
        """Follow the app's light/dark toggle."""
        self._dark = bool(dark)
        colour = DARK_COLOUR if self._dark else LIGHT_COLOUR
        self._view.setStyleSheet(
            f"QTextBrowser {{ background: {colour['surface']};"
            f" color: {colour['text']}; border: none;"
            f" selection-background-color: {colour['accent']}; }}")
        self._view.document().setDefaultStyleSheet(_document_css(colour))
        # setDefaultStyleSheet only affects subsequently loaded content;
        # reload the current page so the restyle is visible.
        source = self._view.source()
        if source.isLocalFile() and not self._error_shown:
            scroll = self._view.verticalScrollBar().value()
            self._view.setSource(source)
            self._view.verticalScrollBar().setValue(scroll)


def _resolve_doc(name: str) -> Path | None:
    """Find a bundled doc HTML (book.html / manual.html), nearest first.
    Mirrors MainWindow._doc_candidates."""
    for root in _DOC_ROOTS:
        for sub in ("docs", "new_docs"):
            candidate = root / sub / name
            if candidate.is_file():
                return candidate
    return None

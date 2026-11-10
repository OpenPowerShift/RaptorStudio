"""Help: shortcuts, the manual viewer, context help, About and issue reporting.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations


from raptor.gui.ui import msgbox
from raptor.bundled import candidate_roots
import os
import platform
import weakref
from typing import TYPE_CHECKING, Any
from pathlib import Path
from PySide6.QtCore import (QObject, QUrl)
from PySide6.QtGui import (QDesktopServices)
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QMessageBox,
    QDialog,
)





if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class HelpMixin(_Base):
    """Help: shortcuts, the manual viewer, context help, About and issue reporting."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        GITHUB_REPO_URL: Any
        _HELP_ANCHORS: Any
        _current_file: str | None
        _help_viewer: Any
        def _open_path(self, path: str) -> Any: ...
        _ribbon: Any
        _shortcuts_window: Any
        _statusbar: Any

    def _open_shortcuts(self):
        """Help > Keyboard Shortcuts: the shortcut list in a themed window
        of the app's own. It used to write a crude HTML file (tables shown
        as raw "| a | b |" lines) and open it in the system browser."""
        from raptor.gui.ui.shortcuts_window import show_shortcuts
        for path in self._doc_candidates("KEYBOARD_SHORTCUTS.md"):
            if os.path.isfile(path):
                self._shortcuts_window = show_shortcuts(
                    self, Path(path).read_text(encoding="utf-8"),
                    getattr(self, "_shortcuts_window", None))
                return
        self._statusbar.showMessage("KEYBOARD_SHORTCUTS.md not found",
                                    6000)

    def _doc_candidates(self, name: str):
        """Possible on-disk locations of a bundled doc, nearest first.
        `raptor.bundled.candidate_roots` covers a checkout, a wheel's
        data-files placement (<venv>/docs, <user base>/new_docs) and, when
        frozen, the PyInstaller bundle root (_internal, where the spec
        puts new_docs/ and docs/)."""
        here = os.path.dirname(os.path.abspath(__file__))
        roots = [str(root / sub) for root in candidate_roots()
                 for sub in ("docs", "new_docs")]
        roots += [
            os.path.join(here, "..", "..", "docs"),
            os.path.join(here, "..", "..", "new_docs"),
            os.path.dirname(self._current_file or "") or here,
        ]
        for root in roots:
            yield os.path.normpath(os.path.join(root, name))

    def _open_doc(self, name: str, anchor: str | None = None) -> None:
        """Open a bundled HTML doc (book / manual) in the in-app help
        viewer (QtWebEngine) with find-in-page, in-app cross-links and
        opsmodel:// links handled natively. Falls back to the system
        browser when QtWebEngine is unavailable or the doc is missing."""
        try:
            from raptor.gui.ui import help_viewer
        except ImportError:
            help_viewer = None  # type: ignore[assignment]
        if help_viewer is not None and help_viewer.WEBENGINE_OK:
            path = help_viewer._resolve_doc(name)
            if path is not None:
                self._show_help_viewer(name, anchor)
                return
        for path in self._doc_candidates(name):
            if os.path.isfile(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                self._statusbar.showMessage(f"Opened {name}")
                return
        # Non-modal on purpose: a modal dialog pumping events after
        # WebEngine has spun up can crash, so a status note and a
        # non-blocking box avoid that path.
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Documentation not found")
        msg.setText(
            f"{name} was not found next to the application.\n\n"
            "The documentation ships separately — see the repository's "
            "new_docs/ (book) and docs/ (manual) folders.")
        msg.setModal(False)
        msg.show()
        self._statusbar.showMessage(f"{name} not found", 6000)

    def _show_help_viewer(self, name: str, anchor: str | None = None):
        """Show (or reuse) the process-wide in-app help viewer.

        Help belongs to the process, not this window: the same viewer
        follows the window that invoked it, and closing one model window
        leaves it open for the others (see `ui/help_service.py`)."""
        from raptor.gui.ui.help_viewer import _register_ops_scheme
        _register_ops_scheme()
        from raptor.gui.ui.help_service import get_help_service
        viewer = get_help_service().show(self, name, anchor)
        # Compatibility for code (and older tests) that looked for the
        # viewer on the window. A weak reference on purpose: Help is no
        # longer owned by this window, and a closing window must not keep
        # it alive. Access it with `()`; it is empty after Help closes.
        self._help_viewer = weakref.ref(viewer)
        return viewer

    def open_model_from_help(self, path: str) -> None:
        """opsmodel:// link clicked in the help viewer: load the model
        like File → Open (discard-confirm included), then refocus."""
        self.raise_()
        self._open_path(path)

    def _switch_from_help(self) -> None:
        """Help viewer's "Switch to App" button: bring the main
        application window forward."""
        self.show()
        self.raise_()
        self.activateWindow()

    def _report_issue(self) -> None:
        """Help > Report Issue: open the GitHub new-issue page, prefilled
        with the app version so bug reports start with the basics."""
        import raptor
        version = getattr(raptor, "__version__", "dev")
        from urllib.parse import quote
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        body = (
            "**RAPTOR Studio version:** " + version + "\n"
            "**Platform:** " + platform.platform() + "\n"
            "**Python:** " + platform.python_version() + "\n\n"
            "**What happened?**\n\n\n"
            "**Steps to reproduce:**\n\n\n"
            "(Attach the `.rbds`/`.rbd` model if possible — it makes "
            "reproducing much faster.)\n")
        url = QUrl(self.GITHUB_REPO_URL + "/issues/new?body="
                   + quote(body))
        QDesktopServices.openUrl(url)
        self._statusbar.showMessage("Opening the GitHub issue tracker", 4000)

    def _show_about(self) -> None:
        import raptor
        version = getattr(raptor, "__version__", "dev")
        msgbox.about(
            self, "About RAPTOR Studio",
            f"<h3>RAPTOR Studio {version}</h3>"
            "<p>RAPTOR Studio is an open-source reliability simulation "
            "tool for modelling systems as reliability block diagrams "
            "and simulating thousands of mission trials to produce "
            "Reliability, Availability, Maintainability and cost, "
            "capacity, sparing and weak-link results.</p>"
            "<p><b>Credits.</b> RAPTOR was originally created at ARINC "
            "by Chuck Carter, Steve Brown, Tony Malerich and Elizabeth "
            "Grimes. Our thanks go to <b>Booz Allen Hamilton</b> for "
            "releasing RAPTOR as open source, without which this port "
            "would not exist.</p>"
            f"<p><b>Licence.</b> The Raptor 7-derived material "
            f"is licensed under the <b>Booz Allen Public License v1.0</b> "
            f"(© ARINC / Booz Allen Hamilton); the new code in this port "
            f"is licensed under the <b>Apache License 2.0</b> "
            f"(© 2026 OpenPowerShift). Built with Qt for Python "
            f"(LGPL-3.0). See LICENSE, NOTICE.md and "
            f"THIRD_PARTY_NOTICES.md in the distribution.</p>"
            f"<p><a href='{self.GITHUB_REPO_URL}'>{self.GITHUB_REPO_URL}</a></p>")

    def _open_context_help(self) -> None:
        """F1: open the reference manual at the section covering the
        focused dock or dialog (falls back to the manual's front page;
        the Simulate ribbon tab maps to the stepping-debugger section)."""
        anchor = None
        widget: QObject | None = QApplication.focusWidget()
        while widget is not None:
            if isinstance(widget, (QDockWidget, QDialog)):
                title = (widget.windowTitle() or "").strip()
                if title in self._HELP_ANCHORS:
                    anchor = self._HELP_ANCHORS[title]
                    break
            widget = widget.parent()
        if anchor is None and self._ribbon.tab_bar.currentIndex() \
                == getattr(self, "_simulate_page_index", -1):
            anchor = self._HELP_ANCHORS["Simulate"]
        self._open_doc("manual.html", anchor)

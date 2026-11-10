"""Start page, examples, new windows and the recent-files list.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations

from raptor.gui.ui.window_support import _OverlayKeeper


from raptor.gui.ui import msgbox
import os
from typing import TYPE_CHECKING, Any, cast
from PySide6.QtCore import (Qt, QPoint)
from PySide6.QtWidgets import (
    QApplication,
)

from raptor.gui.ui import settings




if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base

    from raptor.gui.ui.main_window import MainWindow
else:
    _Base = object


class RecentFilesMixin(_Base):
    """Start page, examples, new windows and the recent-files list."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _backstage: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    def _show_backstage(self, page: str = "Home") -> None:
        """The full-page File view (`ui/backstage.py`), on `page`."""
        if getattr(self, "_backstage", None) is None:
            from PySide6.QtWidgets import QDockWidget
            from raptor.gui.ui.backstage import Backstage
            self._backstage = Backstage(cast("MainWindow", self))
            keeper = _OverlayKeeper(self)
            for w in (*self.findChildren(QDockWidget), self.centralWidget()):
                if w is not None:
                    w.installEventFilter(keeper)
        self._backstage.open_view(page)

    def show_start_page(self) -> None:
        """At start-up with nothing to open: the File view's Home page, so
        the first thing on screen is what to do -- new, recent, examples,
        help -- rather than an empty canvas (unless turned off there)."""
        from raptor.gui.ui.backstage import START_KEY
        if self._rbd is None and settings.get_bool(START_KEY, True):
            self._show_backstage("Home")

    def _open_example(self, file_name: str) -> None:
        """Open a User Guide example as a new, UNSAVED model: Save would
        otherwise write over the bundled copy (read-only once installed),
        and an example is a starting point, as a template is."""
        target = self._window_for_open()
        if target is not self:
            target._open_example(file_name)
            return
        if self._refuse_during_run("open an example"):
            return
        self._inspector.flush_pending_comment()
        if not self._confirm_discard_unsaved(closing=True):
            return
        path = next((p for p in self._doc_candidates(f"models/{file_name}")
                     if os.path.isfile(p)), None)
        if path is None:
            msgbox.critical(self, "Example Not Found",
                            f"The example {file_name} is not installed.")
            return
        try:
            rbd = self._open_model_any(path)
        except Exception as e:
            msgbox.critical(self, "Open Failed", f"Could not open {file_name}:\n\n{e}")
            return
        self._load_into_app(rbd, None)
        self._statusbar.showMessage(
            f"Opened the example {file_name} as a new model — Save As to keep "
            f"your changes", 8000)

    def _window_for_open(self, path: str | None = None):
        """Which window a model being opened goes to: this one while it
        holds nothing worth keeping (no model, or an untouched one never
        saved -- a fresh New or an unedited example), else a new window,
        so opening a second model never closes the first. A file already
        open in some window brings that window forward instead (None).
        Preferences can keep everything in this window
        (`files/open_in` = "this")."""
        from raptor.gui import open_models
        holder = open_models.window_for_file(path)
        if holder is not None:
            holder.show()
            holder.raise_()
            holder.activateWindow()
            holder._statusbar.showMessage(
                f"{os.path.basename(path or '')} is open in this window", 6000)
            return None
        if self._rbd is None or (self._current_file is None
                                 and not self._dirty):
            return self
        if settings.get("files/open_in", "new") == "this":
            return self
        return self._spawn_window()

    def _spawn_window(self):
        """A new, empty window beside this one, for another model."""
        from raptor.gui import open_models
        # Building a window takes most of a second: say so at once, in the
        # window that was clicked, before anything is built.
        self._statusbar.showMessage("Opening a new window…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            win = type(self)()
            win.resize(self.size())
            win.move(self.pos() + QPoint(36, 36))
            open_models.keep(win)
            win.show()
            win.raise_()
            win.activateWindow()
            QApplication.processEvents()
        finally:
            QApplication.restoreOverrideCursor()
            self._statusbar.clearMessage()
        return win

    def _recent_reload(self):
        """The recent list changed: refresh the File view if it is open."""
        backstage = getattr(self, "_backstage", None)
        if backstage is not None and backstage.isVisible():
            backstage.reload()

    def _recent_clear(self):
        settings.set_value(self._RECENT_KEY, [])
        self._recent_reload()

    def _forget_recent(self, path: str) -> None:
        settings.set_value(self._RECENT_KEY,
                           [p for p in settings.get_list(self._RECENT_KEY)
                            if p != path])
        self._recent_reload()

    def _recent_list(self) -> list[str]:
        return [p for p in settings.get_list(self._RECENT_KEY)
                if os.path.exists(p)]

    def _remember_recent(self, path: str):
        if not path:
            return
        paths = [p for p in self._recent_list() if p != path]
        paths.insert(0, path)
        settings.set_value(self._RECENT_KEY, paths[:self._RECENT_MAX])
        self._recent_reload()

    def _open_path(self, path: str):
        """Shared loader for the Open dialog and the recent-files list."""
        target = self._window_for_open(path)
        if target is not self:
            if target is not None:
                target._open_path(path)
            return
        if self._refuse_during_run("open another file"):
            return
        self._inspector.flush_pending_comment()
        if not self._confirm_discard_unsaved(closing=True):
            return
        try:
            rbd = self._open_model_any(path)
        except Exception as e:
            msgbox.critical(
                self, "Open Failed", f"Could not open {path}:\n\n{e}")
            return
        self._load_into_app(rbd, path)
        self._remember_recent(path)

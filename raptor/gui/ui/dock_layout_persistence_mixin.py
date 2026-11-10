"""Saving, restoring and resetting the window and dock layout.

Expects on `self`: the docks built by `_setup_docks` and `_statusbar`."""
from __future__ import annotations


from typing import Any, TYPE_CHECKING
from PySide6.QtCore import QSize, Qt, QTimer

from raptor.gui.ui import settings

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class DockLayoutPersistenceMixin(_Base):
    """Saving, restoring and resetting the window and dock layout."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        _DEFAULT_WINDOW_SIZE: Any
        _LAYOUT_VERSION: Any
        _canvas_room_defaults_done: Any
        _compact_minimized: set[str]
        _data_tables_dock: Any
        _dock_chrome: dict
        _dock_resize_generation: Any
        _dock_state_sync: dict
        _dock_user_sized: Any
        def _ensure_status_bar_visible(self) -> Any: ...
        _explorer_dock: Any
        def _give_canvas_room(self, *, force: bool = ...) -> Any: ...
        _inspector_dock: Any
        _live_log_dock: Any
        _persist_layout: Any
        def _reset_right_docks(self) -> Any: ...
        _restoring_dock_chrome: Any
        _results_dock: Any
        _run_dock: Any
        @staticmethod
        def _set_dock_min_width(dock: Any, width: int) -> None: ...
        _statusbar: Any
        def _sync_dock_tab_bars(self) -> Any: ...
        def _update_dock_max_heights(self) -> None: ...

    def _initial_window_size(self):
        """1600x1000, unless the screen cannot hold that.

        A screen below 1024x640 is not a supported size, and the headless
        test platform reports 800x600, so those keep the default rather
        than shrinking every window a test builds."""
        from PySide6.QtGui import QGuiApplication
        w, h = self._DEFAULT_WINDOW_SIZE
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            if avail.width() >= 1024 and avail.height() >= 640:
                w, h = min(w, avail.width()), min(h, avail.height())
        return QSize(w, h)

    def enable_layout_persistence(self) -> None:
        """Restore the saved window geometry and keep saving it on close.

        Called by `app.main()` only. `MainWindow` itself never persists:
        a test that builds and closes a window would otherwise hand its
        geometry to the next test's window."""
        self._persist_layout = True
        saved = settings.get("layout/window_geometry")
        if saved:
            try:
                self.restoreGeometry(saved)
            except (TypeError, RuntimeError):
                pass
        self._clamp_to_screens()
        # The dock layout comes back once the window is shown and laid out: a stub or a minimized slot pins its size
        # from the dock's current size, which is meaningless before the first layout pass.
        raw = settings.get("layout/docks")
        if raw:
            import json
            try:
                blob = json.loads(raw)
            except (TypeError, ValueError):
                blob = None
            QTimer.singleShot(0, lambda b=blob: self._restore_saved_layout(b))

    def _restore_saved_layout(self, blob) -> None:
        """Apply the startup layout once, and forget it if it is unusable.

        A rejected blob used to stay in settings, so every launch paid for
        the same failed restore and logged the same warning. Removing it
        makes the next start fall back cleanly to the default layout.
        """
        if not self._apply_layout_blob(blob):
            settings.remove("layout/docks")

    def _layout_blob(self) -> dict:
        """The dock layout as plain data: Qt's `saveState()` plus each slot's chrome (closed / minimized) and the
        user's normal extent, which `saveState()` only records as the collapsed strip while a dock is minimised."""
        import base64
        chrome = {}
        for title, c in (self._dock_chrome or {}).items():
            state = dict(c["get"]())
            dock = c.get("dock")
            entry = (getattr(self, "_dock_chrome_entries", None)
                     or {}).get(dock) or {}
            for key in ("docked_width", "docked_height"):
                value = entry.get(key)
                if isinstance(value, int) and value > 0:
                    state[key] = value
            if state.get("minimized") and dock is not None and dock.isFloating():
                # A collapsed floating dock saves as a 36px empty window.
                # Persist it expanded; the next start is then usable.
                state["minimized"] = False
            chrome[title] = state
        return {"v": self._LAYOUT_VERSION,
                "state": base64.b64encode(self.saveState().data()).decode("ascii"),
                "chrome": chrome}

    def _apply_layout_blob(self, blob) -> bool:
        """Restore a saved layout. False, with the default layout untouched, for anything that is not a blob this
        version wrote (missing keys, wrong types, a state Qt rejects)."""
        import base64
        from PySide6.QtCore import QByteArray
        try:
            if not isinstance(blob, dict) or blob.get("v") != self._LAYOUT_VERSION:
                return False
            chrome = blob.get("chrome")
            if not isinstance(chrome, dict):
                return False
            state = QByteArray(base64.b64decode(blob["state"], validate=True))
            if not self.restoreState(state):
                return False
        except (KeyError, TypeError, ValueError, RuntimeError):
            return False
        # Park the right docks before applying saved chrome. Doing it
        # afterwards forced a saved-closed Inspector back open.
        self._reset_right_docks()
        self._restoring_dock_chrome = True
        try:
            for title, c in (self._dock_chrome or {}).items():
                want = chrome.get(title)
                if not isinstance(want, dict):
                    continue
                entry = (getattr(self, "_dock_chrome_entries", None)
                         or {}).get(c.get("dock")) or {}
                for key in ("docked_width", "docked_height"):
                    value = want.get(key)
                    if isinstance(value, int) and value > 0:
                        entry[key] = value
                if want.get("closed") is True and c["set_closed"] is not None:
                    c["set_closed"](True)
                elif want.get("minimized") is True:
                    dock = c.get("dock")
                    # Repair blobs written by older builds: a collapsed
                    # floating dock restores as an empty titlebar-height
                    # window, so show its content instead.
                    if dock is not None and dock.isFloating():
                        c["set_minimized"](False)
                    else:
                        c["set_minimized"](True)
        finally:
            self._restoring_dock_chrome = False
        # The user's saved widths/heights are deliberate; a later model
        # load must not replace them with the designed defaults.
        self._canvas_room_defaults_done = True
        self._dock_user_sized = True
        return True

    def _reset_dock_chrome(self) -> None:
        for c in (self._dock_chrome or {}).values():
            if c["get"]()["closed"] and c["set_closed"] is not None:
                c["set_closed"](False)
            if c["get"]()["minimized"]:
                c["set_minimized"](False)

    def reset_layout(self) -> None:
        """View > Reset Layout: every dock back in its first-run place, open and full size; the saved layout is
        forgotten so the next start is the default too."""
        self._reset_dock_chrome()
        # Reset Layout takes over from the narrow-window compact policy:
        # widening must not re-minimize/re-expand or restore old widths.
        self._compact_minimized.clear()
        # Cancel the width/height restores that un-collapsing just
        # scheduled, and any older one: Reset Layout's designed sizes
        # must win, not the pre-reset 520 px extent.
        self._dock_resize_generation += 1
        # Repeated tabifyDockWidget calls leave orphan QTabBars behind
        # (same geometry, still visible), which is why a reset could show
        # two bottom strips and "clicking Run does nothing". Drop the old
        # bottom bars; Qt recreates one when the group is rebuilt.
        from PySide6.QtWidgets import QTabBar
        for bar in self.findChildren(QTabBar):
            if bar.objectName() == "qt_tabwidget_tabbar":
                continue
            if bar.parent() is self and bar.count() <= 3:
                bar.hide()
                bar.setParent(None)
                bar.deleteLater()
        explorer, inspector = self._explorer_dock, self._inspector_dock
        bottom = [self._data_tables_dock, self._results_dock, self._run_dock]
        default_state = getattr(self, "_default_dock_state", None)
        if default_state is not None and self.restoreState(default_state):
            # Restore the exact first-run arrangement. Re-adding and
            # re-tabifying docks created orphan QTabBars (two visible
            # bottom strips), and could leave Data Tables in front.
            for d in [explorer, inspector, self._live_log_dock, *bottom]:
                if d.isFloating():
                    d.setFloating(False)
                if d is self._results_dock:
                    d.setWindowTitle("Results")
        else:
            for d in [explorer, inspector, self._live_log_dock, *bottom]:
                if d.isFloating():
                    d.setFloating(False)
                if d is self._results_dock:
                    d.setWindowTitle("Results")
            self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, explorer)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, inspector)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,
                               self._live_log_dock)
            for d in bottom:
                self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, d)
            self.tabifyDockWidget(self._data_tables_dock, self._results_dock)
            self.tabifyDockWidget(self._data_tables_dock, self._run_dock)
        for d in (explorer, inspector, *bottom):
            d.show()
        self._live_log_dock.hide()
        self._sync_dock_tab_bars()
        for fn in (self._dock_state_sync or {}).values():
            fn(True)
        self._reset_right_docks()
        settings.remove("layout/docks")
        # Layout settles after the event loop; raise/size then, not
        # before the re-tabification has been applied.
        QTimer.singleShot(0, self._finish_reset_layout)
        self._statusbar.showMessage("Layout reset", 4000)

    def _finish_reset_layout(self) -> None:
        """Complete Reset Layout after Qt has applied the dock changes."""
        try:
            for d in (self._results_dock, self._data_tables_dock,
                      self._run_dock):
                self._set_dock_min_width(d, 0)
                d.setMaximumWidth(16777215)
                d.setMinimumHeight(120)
            self._update_dock_max_heights()
            self._results_dock.show()
            self._results_dock.raise_()
            self._give_canvas_room(force=True)
            self._sync_dock_tab_bars()
            # Reset must also restore the fixed chrome, not just the docks.
            QTimer.singleShot(0, self._ensure_status_bar_visible)
        except RuntimeError:
            pass

    def _clamp_to_screens(self) -> None:
        """A restored geometry from a monitor that is no longer there (or a
        smaller one) must not leave the window off-screen or oversized."""
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QGuiApplication
        screens = QGuiApplication.screens()
        if not screens:
            return
        union = QRect()
        for sc in screens:
            union = union.united(sc.availableGeometry())
        frame = self.frameGeometry()
        visible = frame.intersected(union)
        if (visible.width() < min(frame.width(), 200)
                or visible.height() < min(frame.height(), 120)
                or frame.width() > union.width()
                or frame.height() > union.height()):
            self.resize(self._initial_window_size())
            primary = QGuiApplication.primaryScreen()
            if primary is not None:
                self.move(primary.availableGeometry().center()
                          - self.rect().center())

    def _save_layout(self) -> None:
        if not getattr(self, "_persist_layout", False):
            return
        try:
            settings.set_value("layout/window_geometry",
                               self.saveGeometry())
            import json
            settings.set_value("layout/docks", json.dumps(self._layout_blob()))
        except RuntimeError:
            pass

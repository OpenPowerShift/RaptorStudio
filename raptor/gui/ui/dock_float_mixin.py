"""Floating, redocking and dragging docks, and the floating Results window.

Expects on `self`: the docks built by `_setup_docks` and `_statusbar`."""
from __future__ import annotations


from typing import Any, TYPE_CHECKING
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDockWidget,
)


if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class DockFloatMixin(_Base):
    """Floating, redocking and dragging docks, and the floating Results window."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _bottom_active_dock: Any
        _results_dock_height_before_float: Any
        _results_float_geom: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    def _results_full_target(self) -> int:
        """Height for the near-full Results stretch: everything except
        the ribbon/status-bar chrome and the canvas's own minimum,
        floored at a usable strip."""
        return self._clamp_dock_height(
            max(280, self.height() - self._vertical_chrome_reserve()))

    def _results_window_title(self) -> str:
        """"Results — <model>" for the floated window, so it is told
        apart from the main window in the taskbar and Alt+Tab."""
        return self.windowTitle().replace("RAPTOR Studio", "Results", 1)

    def _float_results(self, checked: bool):
        self._float_results_properly(self._results_dock, checked)

    def _float_results_properly(self, dock: QDockWidget, floating: bool):
        """One float path for every entry point (ribbon Float, the
        dock's own title-bar float button, ...): plain `setFloating(True)`
        leaves the dock as a docked-sized sliver floating mid-screen,
        and re-docking then keeps the floated size. Floating now sizes
        and positions the window; docking restores the standard
        bottom-strip geometry."""
        if floating:
            # Never carry a titlebar collapse into the floating window:
            # set_minimized pins the dock to its titlebar height, so the
            # new window would be an empty 36px strip. The un-collapse
            # is deferred, so seed the remembered height from the saved
            # extent instead of reading the strip that is still on screen.
            entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                     .get(dock)) or {}
            chrome = (self._dock_chrome or {}).get("Results")
            was_minimized = bool(chrome and chrome["get"]()["minimized"])
            self._restore_results_chrome()
            if was_minimized and entry.get("docked_height"):
                self._results_dock_height_before_float = int(
                    entry["docked_height"])
            else:
                self._results_dock_height_before_float = dock.height()
            if was_minimized:
                # Qt restores the previous floating geometry before the
                # topLevelChanged handler; do not remember that as the
                # docked height.
                entry["_skip_extent_remember"] = True
        dock.setFloating(floating)
        if floating:
            # A floated dock is a frameless tool window: no taskbar entry,
            # no maximise, and it hides behind the main window. Make it
            # an ordinary window the window manager decorates. Docking
            # again (setFloating(False)) puts Qt's own flags back.
            dock.setWindowFlags(Qt.WindowType.Window)
            dock.setWindowTitle(self._results_window_title())
            # a floating window owns its own size: lift the docked
            # ceiling so resize(1080, 760) isn't clamped by it
            dock.setMaximumHeight(16777215)
            host = self.window()
            geometry = getattr(self, "_results_float_geom", None)
            if geometry is not None and geometry.width() > 200 and geometry.height() > 120:
                dock.setGeometry(geometry)
            else:
                dock.resize(1080, 760)
                # Top-right of the main window, inside the screen: a
                # sensible cascade position, not Qt's centre default.
                geo = host.geometry()
                x = geo.x() + max(0, geo.width() - 1080 - 40)
                y = geo.y() + 120
                dock.move(x, y)
            dock.show()
            dock.raise_()
            dock.activateWindow()
        else:
            # Remember where the user left the floating window before
            # re-docking, so the next Float restores it.
            if dock.geometry().isValid():
                self._results_float_geom = dock.geometry()
            dock.setWindowState(Qt.WindowState.WindowNoState)
            dock.setWindowTitle("Results")      # its tab's text again
            dock.raise_()       # back in front of its tabbed neighbours
            # Re-docked: drop the leftover floated geometry, give the
            # strip a usable height and let the splitter control it
            # from there (not 50-60% of the screen, not a 260px sliver).
            try:
                self._update_dock_max_heights()
                target = getattr(self, "_results_dock_height_before_float",
                                 None) or 480
                self._resize_docks([dock],
                                 [self._clamp_dock_height(target)],
                                 Qt.Orientation.Vertical)
            except Exception:
                pass

    def _show_dock_back_button(self, dock, entry, show: bool) -> None:
        """A detached panel gets "back to the dock" instead of collapse.

        Floating hides the collapse/expand arrow (meaningless on a free
        window -- reported), and leaves the user with no obvious way home
        except the window menu. This is that way: one click re-docks it
        where it belonged and restores the docked size.
        """
        btn = entry.get("dock_btn")
        if btn is None and show:
            from PySide6.QtWidgets import QToolButton

            btn = QToolButton(entry["titlebar"])
            btn.setText("Redock")
            btn.setAutoRaise(True)
            btn.setToolTip(f"Redock {dock.windowTitle()}")
            btn.setAccessibleName(f"Return {dock.windowTitle()} to the dock")
            btn.clicked.connect(lambda _c=False, d=dock: self._redock(d))
            lay = entry["titlebar"].layout()
            if lay is not None:
                lay.addWidget(btn)
            entry["dock_btn"] = btn
        if btn is not None:
            btn.setVisible(show)

    def _edge_for_area(self, area):
        """Translate a Qt dock area into the edge that names its sizing
        dimension: left/right docks are sized by width, top/bottom by
        height."""
        from PySide6.QtCore import Qt as _Qt
        if area == _Qt.DockWidgetArea.LeftDockWidgetArea:
            return "left"
        if area == _Qt.DockWidgetArea.RightDockWidgetArea:
            return "right"
        if area in (_Qt.DockWidgetArea.TopDockWidgetArea,
                    _Qt.DockWidgetArea.BottomDockWidgetArea):
            return "bottom"
        return None

    def _current_dock_edge(self, dock, fallback=None):
        """The edge a dock is on *now*.

        The edge passed to `_make_dock_minimizable` is only the
        construction-time default: a user can drag Explorer to the
        bottom, and a minimised/redocked dock must then restore a height
        rather than its old width minimum."""
        try:
            area = self.dockWidgetArea(dock)
        except RuntimeError:
            return fallback
        return self._edge_for_area(area) or fallback

    @staticmethod
    def _dock_min_dimension(entry: dict, edge: str) -> int:
        """The dock's own minimum along `edge`'s dimension.

        Store width and height minima separately: a side panel sent to
        the bottom must not inherit its 340 px width as a 340 px height
        floor."""
        if edge in ("left", "right"):
            return max(1, int(entry.get("normal_min_width") or 0))
        return max(1, int(entry.get("normal_min_height") or 0))

    def _apply_docked_dock_constraints(self, dock, entry, edge) -> None:
        """Release every stale min/max lock, then apply the one minimum
        the dock needs on the edge it now occupies. A dock that was
        floated had 160x90/uncapped; one collapsed earlier may still
        carry equal minimum/maximum values."""
        try:
            dock.setMaximumSize(16777215, 16777215)
            if edge in ("left", "right"):
                dock.setMinimumHeight(0)
                dock.setMaximumHeight(16777215)
                self._set_dock_min_width(
                    dock, self._dock_min_dimension(entry, edge))
            else:
                dock.setMinimumWidth(0)
                dock.setMaximumWidth(16777215)
                dock.setMinimumHeight(
                    self._dock_min_dimension(entry, "bottom"))
        except RuntimeError:
            pass

    def _remember_docked_extent(self, dock, entry) -> None:
        """Remember the docked size before a float so Redock restores the
        user's width/height, not a hard-coded one."""
        if entry is None:
            return
        if entry.get("minimized") or entry.get("closed"):
            return
        if entry.pop("_skip_extent_remember", False):
            return
        try:
            width, height = max(dock.width(), 1), max(dock.height(), 1)
        except RuntimeError:
            return
        edge = self._current_dock_edge(dock, entry.get("edge"))
        if edge in ("left", "right"):
            entry["docked_width"] = width
        else:
            entry["docked_height"] = height

    def _unminimize_before_float(self, dock, entry) -> None:
        """A minimised dock floated as-is is an empty 36 px window.
        Restore its content before it becomes a free window."""
        if entry is not None and entry.get("minimized"):
            setter = entry.get("set_minimized")
            if setter is not None:
                setter(False)
            # Floating is the user's next explicit state; compact must
            # not later widen/restore this dock behind their back.
            try:
                self._compact_minimized.discard(dock.windowTitle())
            except AttributeError:
                pass
            # The un-minimise restore is deferred; the titlebar strip
            # geometry on this turn is not the user's docked extent.
            entry["_skip_extent_remember"] = True

    @staticmethod
    def _system_move_supported() -> bool:
        """Wayland forbids client-side top-level positioning."""
        from PySide6.QtGui import QGuiApplication
        return "wayland" in QGuiApplication.platformName().casefold()

    def _start_dock_system_move(self, dock: QDockWidget) -> bool:
        """Ask the compositor to move a floating dock (works on Wayland)."""
        try:
            handle = dock.windowHandle()
            return bool(handle is not None and handle.startSystemMove())
        except RuntimeError:
            return False

    def _begin_dock_titlebar_drag(self, dock: QDockWidget,
                                  press_global) -> None:
        """A custom title bar must float the dock itself: replacing Qt's
        title bar also removes its built-in drag gesture."""
        entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                 .get(dock))
        self._remember_docked_extent(dock, entry)
        if dock is not getattr(self, "_results_dock", None):
            self._unminimize_before_float(dock, entry)
        if not dock.isFloating():
            if dock is getattr(self, "_results_dock", None):
                self._float_results_properly(dock, True)
            else:
                dock.setFloating(True)
                dock.show()
                dock.raise_()
        if dock.isFloating():
            # Put the new window under the pointer instead of leaving it
            # at Qt's default position, which can be far from the drag.
            dock.move(max(0, press_global.x() - 40),
                      max(0, press_global.y() - 12))

    def _finish_dock_titlebar_drag(self, dock: QDockWidget,
                                   global_pos) -> None:
        """A release over the main window docks to its nearest edge.

        Without this, dragging a custom-titled panel out was one-way:
        the window followed the mouse but could never be put back except
        with the Redock button."""
        if not dock.isFloating():
            return
        if not self.frameGeometry().contains(global_pos):
            return
        local = self.mapFromGlobal(global_pos)
        if local.y() < self.height() // 2:
            # A release in the top half, and especially the top band,
            # must never join the bottom strip even on a short, wide
            # window where the bottom edge is geometrically nearer.
            area = (Qt.DockWidgetArea.LeftDockWidgetArea
                    if local.x() < self.width() // 2
                    else Qt.DockWidgetArea.RightDockWidgetArea)
        else:
            distances = {
                Qt.DockWidgetArea.LeftDockWidgetArea: local.x(),
                Qt.DockWidgetArea.RightDockWidgetArea:
                    self.width() - local.x(),
                Qt.DockWidgetArea.BottomDockWidgetArea:
                    self.height() - local.y(),
            }
            area = min(distances, key=lambda a: distances[a])
        self._dock_to_area(dock, area)

    def _dock_to_area(self, dock: QDockWidget, area) -> None:
        """Dock a dragged floating panel into `area`, restoring its own
        remembered size."""
        entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                 .get(dock)) or {}
        dock.setFloating(False)
        self.addDockWidget(area, dock)
        if area == Qt.DockWidgetArea.BottomDockWidgetArea:
            if dock is not getattr(self, "_data_tables_dock", None):
                self.tabifyDockWidget(self._data_tables_dock, dock)
            self._bottom_active_dock = dock
        elif area == Qt.DockWidgetArea.RightDockWidgetArea and dock in (
                getattr(self, "_live_log_dock", None),
                getattr(self, "_fev_inspector_dock", None)):
            self.tabifyDockWidget(self._inspector_dock, dock)
        edge = self._edge_for_area(area) or entry.get("edge")
        entry["edge"] = edge
        if not dock.isFloating():
            self._apply_docked_dock_constraints(dock, entry, edge)
        self._restore_dock_extent(dock, entry, edge)
        if dock is getattr(self, "_results_dock", None):
            dock.setWindowTitle("Results")
        dock.show()
        dock.raise_()

    def _toggle_dock_floating(self, dock: QDockWidget) -> None:
        """Double-clicking a custom title bar toggles float, like the
        native one."""
        if dock.isFloating():
            self._redock(dock)
            return
        entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                 .get(dock))
        self._remember_docked_extent(dock, entry)
        if dock is not getattr(self, "_results_dock", None):
            self._unminimize_before_float(dock, entry)
        if dock is getattr(self, "_results_dock", None):
            self._float_results_properly(dock, True)
        else:
            dock.setFloating(True)
            dock.show()
            dock.raise_()

    def _on_dock_location_changed(self, dock, area) -> None:
        """Update the entry's edge after a dock moves between areas and
        re-apply the minimum for the new dimension."""
        entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                 .get(dock))
        if entry is not None:
            edge = self._edge_for_area(area) or entry.get("edge")
            entry["edge"] = edge
            if not dock.isFloating():
                self._apply_docked_dock_constraints(dock, entry, edge)
            stub = entry.get("stub")
            if stub is not None and edge not in ("left", "right") \
                    and entry.get("closed"):
                # A side-stub closed dock moved to the bottom cannot use
                # the rotated title bar there: restore its normal chrome
                # first, then it closes for real.
                restore = (self._dock_stubs or {}).get(dock)
                if restore is not None:
                    restore(False)
        QTimer.singleShot(0, self._sync_dock_tab_bars)
        QTimer.singleShot(0, self._update_dock_max_heights)

    def _restore_dock_extent(self, dock, entry, edge) -> None:
        """Reassert the user's docked width/height after a float/redock.

        Deferred: on the same turn as setFloating(False) the main layout
        has not re-adopted the dock yet, and a synchronous resizeDocks
        request is discarded. Release the shared strip's tab ceiling
        first or the smallest maximumHeight in the group wins. One
        correction pass compensates for the tab-bar allowance Qt leaves
        outside the dock's own height.
        """
        if edge in ("left", "right"):
            width = entry.get("docked_width") \
                or entry.get("normal_min_width") or 320
            width = max(int(width), self._dock_min_dimension(entry, edge))
            generation = self._dock_resize_generation

            def restore_width(d=dock, w=width, attempt=0):
                if generation != self._dock_resize_generation:
                    return
                try:
                    actual = d.width()
                    if attempt and actual >= w - 4:
                        return
                    requested = w + (w - actual) if actual < w - 4 else w
                    self._resize_docks([d], [requested],
                                       Qt.Orientation.Horizontal)
                except (RuntimeError, TypeError):
                    return
                if attempt < 1:
                    QTimer.singleShot(
                        0, lambda: restore_width(d, w, attempt + 1))
            QTimer.singleShot(0, restore_width)
            return

        target = entry.get("docked_height")
        if dock is getattr(self, "_results_dock", None):
            target = self._results_dock_height_before_float or target
        height = self._clamp_dock_height(int(target or 480))
        generation = self._dock_resize_generation

        def restore_height(d=dock, h=height, attempt=0):
            if generation != self._dock_resize_generation:
                return
            try:
                self._bottom_active_dock = d
                self._release_bottom_strip_ceiling()
                self._update_dock_max_heights()
                actual = d.height()
                if attempt and actual >= h - 4:
                    return
                requested = h + (h - actual) if actual < h - 4 else h
                self._resize_docks([d], [requested],
                                   Qt.Orientation.Vertical)
            except (RuntimeError, TypeError):
                return
            if attempt < 2:
                QTimer.singleShot(
                    0, lambda: restore_height(d, h, attempt + 1))
        QTimer.singleShot(0, restore_height)
        if target is None and hasattr(dock, "fit_height"):
            QTimer.singleShot(0, dock.fit_height)

    def _redock(self, dock: QDockWidget) -> None:
        """Put a floating panel back where it was docked, at its own size."""
        entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                 .get(dock)) or {}
        edge = self._current_dock_edge(dock, entry.get("edge"))
        entry["edge"] = edge
        dock.setFloating(False)
        self._restore_dock_extent(dock, entry, edge)
        if dock is getattr(self, "_results_dock", None):
            dock.setWindowTitle("Results")
        dock.show()
        dock.raise_()

    def _on_dock_top_level_changed(self, dock, floating: bool) -> None:
        """A floating dock is the user's own window: no caps.

        Docked, each panel is held to a size that suits the layout (a
        minimised strip, the Run panel's content height, the Explorer's
        normal width). Detached, those caps made the window unable to grow
        or shrink -- the reported "when it's detached I should be able to
        expand it and resize it too". Release them here, and put them back
        when the dock is docked again.
        """
        if dock is None:
            return
        try:
            dock.objectName()          # liveness: the deferred call can
        except RuntimeError:           # outlive a closed window
            return
        entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                 .get(dock))
        if floating:
            try:
                self._compact_minimized.discard(dock.windowTitle())
            except AttributeError:
                pass
            if entry is not None and entry.get("closed"):
                restore = (self._dock_stubs or {}).get(dock)
                if restore is not None:
                    restore(False)
                # set_closed() pinned the stub to 24 px; keep the real
                # docked width and do not let the float transition
                # remember the stub instead.
                entry["_skip_extent_remember"] = True
                width = int(entry.get("docked_width") or 0)
                if width > 160:
                    try:
                        dock.resize(width, max(dock.height(), 120))
                    except RuntimeError:
                        pass
            self._unminimize_before_float(dock, entry)
            self._remember_docked_extent(dock, entry)
            dock.setMinimumSize(160, 90)
            dock.setMaximumSize(16777215, 16777215)
            # A floating window keeps its minimize/restore control, so
            # the panel can still be expanded/collapsed anywhere. The
            # float button is meaningless once detached; Close and Back
            # stay.
            if entry is not None:
                min_btn = entry.get("min_btn")
                if min_btn is not None:
                    min_btn.setVisible(True)
                float_btn = entry.get("float_btn")
                if float_btn is not None:
                    float_btn.setVisible(False)
                self._show_dock_back_button(dock, entry, True)
            return
        if entry is not None:
            for key in ("min_btn", "float_btn"):
                widget = entry.get(key)
                if widget is not None:
                    widget.setVisible(True)
            self._show_dock_back_button(dock, entry, False)
            edge = self._current_dock_edge(dock, entry.get("edge"))
            entry["edge"] = edge
            self._apply_docked_dock_constraints(dock, entry, edge)
        else:
            dock.setMaximumSize(16777215, 16777215)
        self._update_dock_max_heights()
        # The bottom strip sizes itself from the panel that is showing.
        # Run hugs its content; Results keeps the height the user had
        # before floating, which `_redock` restores immediately after
        # this signal.
        for attr in ("_run_dock", "_results_dock"):
            panel_dock = getattr(self, attr, None)
            fit = getattr(dock, "fit_height", None)
            remembered = (entry or {}).get("docked_height")
            if panel_dock is dock and fit is not None \
                    and (dock is getattr(self, "_run_dock", None)
                         or remembered is None):
                QTimer.singleShot(0, fit)

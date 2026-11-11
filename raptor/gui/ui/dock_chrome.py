"""Per-slot dock chrome: the custom title bar, minimise-to-titlebar and the closed stub.

One `DockSlotChrome` owns one physical dock slot (a single dock, or several tabified
docks that collapse together as one unit). It used to be a 400-line closure factory
inside `DockLayoutMixin._make_dock_minimizable`; the closures shared mutable state
(`entries`, the glyphs, `restore_max`), which is now explicit instance state.

Each dock gets an *entry*, a plain dict that other parts of the window read
(`_dock_chrome_entries`, `_redock`, layout persistence). The dict shape is therefore
part of this module's contract; see `_build_entry` for the keys.

`window` is the main window. Everything the chrome needs from it is a method or
attribute already on `DockLayoutMixin` (`_current_dock_edge`, `_dock_min_dimension`,
`_set_dock_min_width`, `_resize_docks`, `_dock_resize_generation`, ...).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QLabel, QToolButton

from raptor.gui.resources import icons
from raptor.gui.resources.design_tokens import active_colour
from raptor.gui.ui.dock_title_widgets import _DockStub, _DockTitleBar
from raptor.gui.ui.qtcompat import req

#: Qt's QWIDGETSIZE_MAX: "no maximum". Passing it to setMaximumSize undoes a fixed size.
_NO_MAX = 16777215

_GLYPH_COLLAPSE = {"left": "◂", "right": "▸", "bottom": "▾"}
_GLYPH_EXPAND = {"left": "▸", "right": "◂", "bottom": "▴"}
_SIDE = ("left", "right")


class DockSlotChrome:
    """Title bars, minimise/restore and the closed stub for one dock slot."""

    def __init__(self, window: Any, docks: list[QDockWidget], edge: str,
                 restore_max: int | Callable[[], int] | None = None):
        self.win = window
        self.edge = edge
        #: int or zero-arg callable giving the maximum size to restore a bottom
        #: dock to. Bottom docks have a session-mutable cap, so this is read
        #: fresh at restore time rather than baked in.
        self.restore_max = restore_max
        self.glyph_collapse = _GLYPH_COLLAPSE[edge]
        self.glyph_expand = _GLYPH_EXPAND[edge]
        self.entries: list[dict[str, Any]] = [self._build_entry(d) for d in docks]

    # ---------------------------------------------------------------- build

    def _build_entry(self, dock: QDockWidget) -> dict[str, Any]:
        """Create one dock's title bar (and stub, for a side dock) and install it.

        LEFT/RIGHT title bars are always laid out horizontally (Qt never rotates one
        for a side dock) and there is no room for the label plus all three buttons at
        a genuinely thin stub width, so collapsing there hides everything but the
        toggle. The bottom dock only squeezes height, so every button stays visible.
        """
        win = self.win
        content = dock.widget()
        titlebar = _DockTitleBar(dock, win)
        tb = QHBoxLayout(titlebar)
        tb.setContentsMargins(8, 4, 4, 4)
        tb.setSpacing(2)
        # The tab bar gets its icon from `windowIcon`; a custom title bar has to
        # paint the same one itself so the dock is recognisable once floated too.
        icon_label = QLabel()
        icon_label.setFixedSize(16, 16)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = dock.windowIcon()
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(16, 16))
        tb.addWidget(icon_label)
        label = QLabel(dock.windowTitle())
        tb.addWidget(label)
        tb.addStretch(1)

        min_btn = QToolButton()
        min_btn.setAutoRaise(True)
        min_btn.setText(self.glyph_collapse)
        min_btn.setToolTip(f"Minimize {dock.windowTitle()}")
        min_btn.setAccessibleName(f"Minimize {dock.windowTitle()}")
        tb.addWidget(min_btn)

        features = dock.features()
        float_btn = close_btn = None
        if features & QDockWidget.DockWidgetFeature.DockWidgetFloatable:
            float_btn = QToolButton()
            float_btn.setAutoRaise(True)
            float_btn.setIcon(icons.float_window_icon(active_colour()["text"]))
            float_btn.setProperty("titleIcon", "float_window_icon")
            float_btn.setToolTip("Float")
            float_btn.setAccessibleName(
                f"Float {dock.windowTitle()} in its own window")
            float_btn.clicked.connect(
                lambda _checked=False, d=dock: self._toggle_float(d))
            tb.addWidget(float_btn)
        if features & QDockWidget.DockWidgetFeature.DockWidgetClosable:
            close_btn = QToolButton()
            close_btn.setAutoRaise(True)
            close_btn.setIcon(icons.close_x_icon(active_colour()["text"]))
            close_btn.setProperty("titleIcon", "close_x_icon")
            close_btn.setToolTip("Close")
            close_btn.setAccessibleName(f"Close {dock.windowTitle()}")
            close_btn.clicked.connect(
                lambda _checked=False, d=dock: self._close_clicked(d))
            tb.addWidget(close_btn)

        # The rotated closed-dock stub (left/right only). Closing the dock swaps this
        # in as the title bar and pins the dock to a ~24px strip, so the panel's slot
        # stays findable and clickable instead of the dock vanishing entirely.
        stub = None
        if self.edge in _SIDE:
            word = {"Model Explorer": "Model"}.get(
                dock.windowTitle(), dock.windowTitle())
            stub = _DockStub(word, -90.0 if self.edge == "left" else 90.0)
            stub.setFixedWidth(24)

        dock.setTitleBarWidget(titlebar)
        # Keys are read by _redock, the floating handler and layout persistence.
        return {"dock": dock, "content": content,
                "titlebar": titlebar, "icon_label": icon_label,
                "label": label,
                "min_btn": min_btn, "float_btn": float_btn,
                "close_btn": close_btn, "stub": stub,
                "edge": self.edge,
                "normal_min_width": max(dock.minimumWidth(), 0),
                "normal_min_height": max(dock.minimumHeight(), 0),
                "docked_width": None, "docked_height": None,
                "minimized": False, "closed": False}

    def _toggle_float(self, dock: QDockWidget) -> None:
        # The Results dock floats through its own path (it must re-apply its size caps).
        if dock is self.win._results_dock:
            self.win._float_results_properly(dock, not dock.isFloating())
        else:
            dock.setFloating(not dock.isFloating())

    def _close_clicked(self, dock: QDockWidget) -> None:
        """left/right docks (rotated stub available) collapse to the stub instead of
        vanishing; a dock moved to the bottom, and any floated window, close for real."""
        if (dock.isFloating()
                or self.win._current_dock_edge(dock) not in _SIDE):
            dock.close()
        else:
            self.set_closed(True)

    def wire(self) -> Callable[[bool], None]:
        """Register this slot with the window and connect its buttons.

        Returns `set_minimized` (callers keep it to drive the slot from the ribbon).
        """
        win = self.win
        if win._dock_titlebar_rethemers is None:
            win._dock_titlebar_rethemers = []
        if win._dock_stubs is None:
            win._dock_stubs = {}
        if win._dock_state_sync is None:
            win._dock_state_sync = {}
        # Every titled dock's own entry, not just the group built last: this runs once
        # per dock slot, and replacing the map left `_redock` and the floating handler
        # with no entry at all for Explorer/Inspector.
        if getattr(win, "_dock_chrome_entries", None) is None:
            win._dock_chrome_entries = {}
        win._dock_chrome_entries.update({e["dock"]: e for e in self.entries})

        req(win._dock_titlebar_rethemers).append(self.retheme)
        self.retheme()
        for e in self.entries:
            e["min_btn"].clicked.connect(self.toggle_minimized)
            e["set_minimized"] = self.set_minimized
            if e["stub"] is not None:
                e["stub"].clicked.connect(
                    lambda _c=False: self.set_closed(False))
                win._dock_stubs[e["dock"]] = self.set_closed

        # Layout persistence: `saveState()` cannot see the chrome (a closed dock is
        # pinned to a 24 px stub, a minimized one to its title bar), so each slot says
        # how to read and set its own.
        if win._dock_chrome is None:
            win._dock_chrome = {}
        first = self.entries[0]
        win._dock_chrome[first["dock"].windowTitle()] = {
            "dock": first["dock"],
            "get": lambda: {"closed": bool(first["closed"]),
                            "minimized": bool(first["minimized"])},
            "set_closed": self.set_closed if first["stub"] is not None else None,
            "set_minimized": self.set_minimized,
        }
        return self.set_minimized

    # --------------------------------------------------------------- theming

    def retheme(self) -> None:
        c = active_colour()
        for e in self.entries:
            collapsed = e["minimized"]
            bg = c["group_header"] if collapsed else c["surface"]
            e["titlebar"].setStyleSheet(
                f"background: {bg}; border-bottom: 1px solid {c['border']};")
            icon = e["dock"].windowIcon()
            if not icon.isNull():
                e["icon_label"].setPixmap(icon.pixmap(16, 16))
            e["label"].setStyleSheet(
                f"font-weight: 600; color: {c['accent'] if collapsed else c['text']};")
            if e["stub"] is not None:
                # Custom-painted: must be told to repaint on a theme flip like the
                # title bars themselves.
                e["stub"].update()

    def sync_action(self, dock: QDockWidget) -> None:
        """Push a dock's open/closed state to its ribbon action.

        `set_closed` hides the dock's *content* and swaps the title bar for the stub,
        so the QDockWidget never becomes hidden and `visibilityChanged` never fires;
        without this the Docks list keeps showing a closed panel as if it were there.
        Only the close path syncs: a *minimized* dock is still present and the ribbon
        toggle cannot restore it, so greying its action out would strand it.
        """
        fn = (self.win._dock_state_sync or {}).get(dock)
        if fn is not None:
            fn(dock.isVisible())

    # ------------------------------------------------------------- minimise

    def toggle_minimized(self, _checked: bool = False) -> None:
        # A user minimize/restore takes ownership back from the narrow-window compact
        # policy. (`_compact_minimized` is absent on a window that never set it up.)
        try:
            self.win._compact_minimized.discard(
                self.entries[0]["dock"].windowTitle())
        except AttributeError:
            pass
        self.set_minimized(not self.entries[0]["minimized"])

    def set_minimized(self, minimized: bool) -> None:
        for e in self.entries:
            dock = e["dock"]
            edge_now = self.win._current_dock_edge(dock, e.get("edge"))
            e["edge"] = edge_now
            e["minimized"] = minimized
            e["content"].setVisible(not minimized)
            self._label_min_button(e, minimized)
            if edge_now in _SIDE:
                self._hide_side_extras(e, minimized)
                if minimized:
                    self._minimize_side(e)
                else:
                    self._restore_side(e)
            elif minimized:
                self._minimize_bottom(e)
            else:
                self._restore_bottom(e)
        self.retheme()
        # Minimising hides content without hiding the dock, so `visibilityChanged`
        # never fires; sync the ribbon toggle or it still reads "open" and the next
        # click closes instead of restoring.
        for e in self.entries:
            self.sync_action(e["dock"])

    def _label_min_button(self, e: dict[str, Any], minimized: bool) -> None:
        dock = e["dock"]
        text = (f"Restore {dock.windowTitle()}" if minimized
                else f"Minimize {dock.windowTitle()}")
        e["min_btn"].setText(self.glyph_expand if minimized else self.glyph_collapse)
        e["min_btn"].setToolTip(text)
        e["min_btn"].setAccessibleName(text)

    @staticmethod
    def _hide_side_extras(e: dict[str, Any], minimized: bool) -> None:
        # A minimised side dock is only a few pixels wide: keep just the toggle.
        e["label"].setVisible(not minimized)
        for key in ("float_btn", "close_btn"):
            if e[key] is not None:
                e[key].setVisible(not minimized)

    def _restoring_chrome(self) -> bool:
        """True while a saved layout is being re-applied: the extents it sets must not
        be overwritten by the transient squeezed size we would otherwise remember."""
        return bool(getattr(self.win, "_restoring_dock_chrome", False))

    def _minimize_side(self, e: dict[str, Any]) -> None:
        dock = e["dock"]
        if not self._restoring_chrome():
            e["docked_width"] = max(dock.width(), 1)
        e["titlebar"].adjustSize()
        # Qt logs "Negative sizes" when setFixedWidth pairs the new width with a -1
        # height (dock not yet laid out), so clamp to the current height.
        dock.setFixedHeight(max(dock.height(), 1))
        dock.setFixedWidth(e["titlebar"].sizeHint().width())

    def _restore_side(self, e: dict[str, Any]) -> None:
        dock = e["dock"]
        min_w = self.win._dock_min_dimension(e, "left")
        dock.setMaximumSize(_NO_MAX, _NO_MAX)
        self.win._set_dock_min_width(dock, min_w)
        dock.setMinimumHeight(0)
        dock.setMaximumHeight(_NO_MAX)
        self.restore_side_extent(e, dock)

    def _minimize_bottom(self, e: dict[str, Any]) -> None:
        dock = e["dock"]
        # Remember the pre-collapse height so restore comes back to the same size
        # rather than leaving it to the layout.
        if not self._restoring_chrome():
            e["saved_height"] = dock.height()
            e["docked_height"] = max(dock.height(), 1)
        e["titlebar"].adjustSize()
        dock.setFixedWidth(max(dock.width(), 1))
        dock.setFixedHeight(e["titlebar"].sizeHint().height())

    def _restore_bottom(self, e: dict[str, Any]) -> None:
        dock = e["dock"]
        # Release the width lock that collapsing applied: setFixedWidth(w) also set
        # maximumWidth(w), so a bottom dock stayed 271 px wide forever after one
        # minimise.
        dock.setMaximumSize(_NO_MAX, _NO_MAX)
        dock.setMinimumWidth(0)
        dock.setMaximumWidth(_NO_MAX)
        min_h = self.win._dock_min_dimension(e, "bottom")
        dock.setMinimumHeight(min_h)
        cap = (_NO_MAX if dock.isFloating() else
               (self.restore_max() if callable(self.restore_max)
                else self.restore_max))
        cap = cap or _NO_MAX
        dock.setMaximumHeight(cap)
        saved = e.get("saved_height") or e.get("docked_height")
        if not saved:
            return
        # Apply the remembered height first (fixed), then release the fix on the next
        # event-loop pass so the splitter stays draggable.
        dock.setFixedHeight(max(saved, min_h))

        def release(d=dock, cap_=cap, nm=min_h):
            # Guarded: the dock may already be torn down when the timer fires
            # (window close).
            try:
                d.setMinimumHeight(nm)
                d.setMaximumHeight(cap_)
            except RuntimeError:
                pass  # C++ object already deleted
        QTimer.singleShot(0, release)

    def restore_side_extent(self, e: dict[str, Any], dock: QDockWidget) -> None:
        """Re-apply the user's width once Qt has released the minimise/close lock.

        Deferred (and retried once): the dock's min/max are still being changed on
        this turn, so the first resize can be swallowed by the layout.
        """
        width = int(e.get("docked_width") or 0)
        if not width:
            return
        win = self.win
        generation = win._dock_resize_generation

        def apply(attempt: int = 0) -> None:
            if generation != win._dock_resize_generation:
                return      # Reset Layout cancelled this restore
            try:
                if dock.isFloating() or e.get("minimized") or e.get("closed"):
                    return
                if win._current_dock_edge(dock, e.get("edge")) not in _SIDE:
                    return
                actual = dock.width()
                if attempt and actual >= width - 4:
                    return
                requested = width + (width - actual) if actual < width - 4 else width
                win._resize_docks([dock], [requested], Qt.Orientation.Horizontal)
            except (RuntimeError, TypeError):
                return      # dock deleted, or a stub-only dock with no size yet
            if attempt < 1:
                QTimer.singleShot(0, lambda: apply(attempt + 1))
        QTimer.singleShot(0, apply)

    # ----------------------------------------------------------------- close

    def set_closed(self, closed: bool) -> None:
        """Swap each dock between its normal chrome and the rotated closed stub.

        A closed Model/Inspector shows its name rotated to face the user, and stays
        restorable by clicking. Docks without a stub (the bottom group) are skipped.
        """
        for e in self.entries:
            if e["stub"] is None:
                continue
            try:
                floating = e["dock"].isFloating()
            except RuntimeError:
                continue    # dock already deleted
            if not closed:
                # Restoring chrome (stub click, Properties, _show_inspector_dock)
                # takes the dock back from the compact policy.
                try:
                    self.win._compact_minimized.discard(e["dock"].windowTitle())
                except AttributeError:
                    pass
            if floating:
                self._close_floating(e, closed)
            elif closed:
                self._close_docked(e)
            else:
                self._reopen_docked(e)
        # Sync every entry (the original synced only the loop's last one, which for
        # the single-dock side slots that have stubs is the same dock).
        for e in self.entries:
            self.sync_action(e["dock"])
        self.retheme()

    def _close_floating(self, e: dict[str, Any], closed: bool) -> None:
        """The rotated side stub cannot exist on a free window: restore normal
        chrome/content; a close request really closes the floating window."""
        dock = e["dock"]
        e["closed"] = False
        e["minimized"] = False
        dock.setTitleBarWidget(e["titlebar"])
        e["content"].setVisible(True)
        # Release the stub's fixed-width lock, but keep the floating window's own
        # 160x90 floor so reopening it cannot collapse below a usable panel.
        dock.setMinimumSize(160, 90)
        dock.setMaximumSize(_NO_MAX, _NO_MAX)
        if closed:
            dock.close()

    def _close_docked(self, e: dict[str, Any]) -> None:
        dock, stub = e["dock"], e["stub"]
        edge_now = self.win._current_dock_edge(dock, e.get("edge"))
        if edge_now not in _SIDE:
            # A dock moved to the bottom has no usable rotated side stub: close it for
            # real, and leave the chrome flags consistent so View > Panels can
            # restore it.
            e["closed"] = False
            e["minimized"] = False
            dock.setTitleBarWidget(e["titlebar"])
            dock.close()
            return
        e["closed"] = True
        if not e["minimized"] and not self._restoring_chrome():
            # Remember the width the user had before the stub pins the dock to 24 px.
            # Do not overwrite it when a minimised dock is already squeezed.
            e["docked_width"] = max(dock.width(), int(e.get("docked_width") or 0))
        e["minimized"] = False
        e["content"].setVisible(False)
        dock.setTitleBarWidget(stub)
        dock.setFixedHeight(max(dock.height(), 1))
        dock.setFixedWidth(stub.width() or 24)

    def _reopen_docked(self, e: dict[str, Any]) -> None:
        dock = e["dock"]
        win = self.win
        e["closed"] = False
        edge_now = win._current_dock_edge(dock, e.get("edge"))
        e["edge"] = edge_now
        dock.setTitleBarWidget(e["titlebar"])
        if e["minimized"]:
            # Restoring the *chrome* of a minimised dock must not release its collapse
            # locks or reveal its content: `set_minimized(False)` owns that.
            e["content"].setVisible(False)
            return
        dock.setMaximumSize(_NO_MAX, _NO_MAX)
        if edge_now in _SIDE:
            win._set_dock_min_width(dock, win._dock_min_dimension(e, edge_now))
            dock.setMinimumHeight(0)
            self.restore_side_extent(e, dock)
        else:
            dock.setMinimumWidth(0)
            dock.setMinimumHeight(win._dock_min_dimension(e, "bottom"))
        e["content"].setVisible(True)

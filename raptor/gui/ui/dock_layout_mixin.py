"""Dock chrome and window layout: minimise/float/redock, saved layout, canvas room.

Expects on `self`: the docks built by `_setup_docks`, `_diagram_dock`, `_results_dock`
and `_statusbar`.
"""
from __future__ import annotations

from raptor.gui.ui.qtcompat import req

import logging
from typing import Any, TYPE_CHECKING, cast
from collections.abc import Callable
from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QHBoxLayout, QLabel, QToolButton, QWidget,
)

from raptor.gui.resources import icons
from raptor.gui.resources.design_tokens import (
    active_colour,
)
from raptor.gui.ui.dock_layout_persistence_mixin import DockLayoutPersistenceMixin
from raptor.gui.ui.dock_float_mixin import DockFloatMixin


class _DockTitleBar(QWidget):
    """The custom dock title bar, which also carries the drag gesture.

    `QDockWidget.setTitleBarWidget()` replaces Qt's own title bar
    outright, and with it the built-in drag-to-move/float handling:
    accepting mouse presses here without forwarding them left the
    panel impossible to detach by dragging. Reproduce that one gesture
    (drag = float, then move the floating window; double-click = toggle
    float) and leave the title-bar buttons to their own widgets.
    """

    def __init__(self, dock: QDockWidget, window):
        super().__init__()
        self._dock = dock
        self._window = window
        self._press_global = None
        self._dragging = False
        self._drag_offset = None

    def _clear_drag(self) -> None:
        self._press_global = None
        self._dragging = False
        self._drag_offset = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._dragging = False
            self._drag_offset = None
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is None:
            super().mouseMoveEvent(event)
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            # A lost release (window switch, popup, remote desktop) used
            # to leave the title bar stuck in drag state: the next hover
            # without a button moved the window.
            self._clear_drag()
            super().mouseMoveEvent(event)
            return
        pos = event.globalPosition().toPoint()
        if not self._dragging:
            if (pos - self._press_global).manhattanLength() \
                    < QApplication.startDragDistance():
                return
            self._dragging = True
            self._window._begin_dock_titlebar_drag(
                self._dock, self._press_global)
            # Wayland does not let a client position a top-level window
            # with move(); ask the compositor for a system move instead,
            # so the dock can cross screens and be placed freely.
            if self._window._system_move_supported():
                if self._window._start_dock_system_move(self._dock):
                    self._clear_drag()
                    event.accept()
                    return
            # The dock may have just been re-parented into its own
            # floating window. Anchor further movement to where that
            # window now sits so it does not jump on the next event.
            self._drag_offset = self._press_global - self._dock.pos()
        if self._drag_offset is not None and self._dock.isFloating():
            self._dock.move(pos - self._drag_offset)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._window._finish_dock_titlebar_drag(
                self._dock, event.globalPosition().toPoint())
        self._clear_drag()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._clear_drag()
            self._window._toggle_dock_floating(self._dock)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event) -> None:
        # While the button is held the pointer must be allowed to leave
        # the title bar (that is the drag). Without the button it is a
        # stale press state.
        if not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            self._clear_drag()
        super().leaveEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate:
            self._clear_drag()
        return super().event(event)


class _DockStub(QWidget):
    """Vertical 'closed dock' stub: a ~24px strip in the dock's old slot
    showing the dock's name rotated to read facing the user. Clicking
    it restores the dock."""

    clicked = Signal()

    def __init__(self, text: str, angle: float, parent=None):
        super().__init__(parent)
        self._text = text
        self._angle = angle
        self.setToolTip(f"{text} — click to restore this panel")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()

    def paintEvent(self, event) -> None:
        c = active_colour()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(c["group_header"]))
        p.setPen(QColor(c["text"]))
        f = self.font()
        f.setBold(True)
        f.setPointSize(10)
        p.setFont(f)
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(self._angle)
        fm = p.fontMetrics()
        p.drawText(QRectF(-fm.horizontalAdvance(self._text) / 2, -fm.height() / 2,
                          fm.horizontalAdvance(self._text), fm.height()),
                   Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


if TYPE_CHECKING:
    # Mixin methods call Qt on `self`; type-check them as a main window.
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class DockLayoutMixin(DockFloatMixin, DockLayoutPersistenceMixin, _Base):
    """Dock chrome and window layout: minimise/float/redock, saved layout, canvas room."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _dock_state_sync: Any
        _compact_minimized: Any
        _dock_stubs: Any
        _dock_chrome: Any
        _dock_titlebar_rethemers: Any
        _dock_resize_generation: Any
        _dock_startup_snapshot_done: Any
        _dock_startup_window_size: Any
        _results_float_geom: Any
        _results_dock_height_before_float: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by `MainWindow`; typed loosely here."""
            ...

    def _can_stub_dock(self, dock) -> bool:
        """True only while the dock is on a side edge and not floating.

        `_dock_stubs` membership outlives a move to the bottom: the
        rotated stub is meaningless there, so close/toggle must consult
        the live edge and chrome state, not the map."""
        entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                 .get(dock))
        if entry is None or entry.get("stub") is None:
            return False
        try:
            if dock.isFloating():
                return False
        except RuntimeError:
            return False
        return self._current_dock_edge(
            dock, entry.get("edge")) in ("left", "right")

    def _is_closed_side_dock(self, dock) -> bool:
        entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                 .get(dock))
        return bool(entry and entry.get("closed")) \
            and self._can_stub_dock(dock)

    def _setup_dock_toggle(self, action, dock, *, manage_enabled: bool = True,
                           on_show=None, on_hide=None):
        """Checkable ribbon action <-> dock visibility, both directions.
        LEFT/RIGHT docks with a rotated stub (`self._dock_stubs`) don't
        close: unchecking collapses them to the rotated "Model"/
        "Inspector" word, and clicking the stub or re-checking restores
        them. Floated docks still close for real -- a stub in a floating
        window makes no sense.

        `manage_enabled=False` leaves the action's enabled state to the
        command table's own predicate. The Failure Effects panel uses this:
        it is only enabled while Failure Effects View is open, but it must
        stay clickable after the user hides it during that mode.
        `on_show`/`on_hide` let a dock with its own swap policy (the Live
        Log returns to the Inspector tab) run it around the generic
        show/close."""
        content = dock.widget()

        def is_open():
            # A stubbed dock is still a visible QDockWidget -- it hides
            # its *content* and swaps its titlebar for the rotated
            # label -- so `visibilityChanged` never fires and the
            # ribbon button stayed lit over a closed panel. The stub
            # reads as open iff its content does.
            return bool(dock.isVisible() and not content.isHidden())

        def sync(visible):
            open_ = is_open() if visible else False
            action.blockSignals(True)
            action.setChecked(open_)
            # Closed-and-stubbed docks read as disabled rather than
            # merely unchecked, so the Docks list shows at a glance
            # that the Inspector/Explorer is not there. Only for docks
            # with a stub: those are recoverable by clicking the stub
            # itself, whereas a real close (a floated dock) must stay
            # clickable or the panel could never be brought back from
            # the ribbon. The tooltip is deliberately left alone --
            # `new_docs/tools/gen_help_reference.py` reads these live,
            # and a state-dependent tooltip would document whichever
            # dock state the generator happened to be looking at.
            if manage_enabled:
                # A closed side dock is recoverable, so its ribbon action
                # must stay clickable. Disabling it left the Inspector
                # impossible to bring back from View > Panels.
                action.setEnabled(True)
            action.blockSignals(False)

        if self._dock_state_sync is None:
            self._dock_state_sync = {}
        self._dock_state_sync[dock] = sync

        def toggle(checked):
            try:
                self._compact_minimized.discard(dock.windowTitle())
            except AttributeError:
                pass
            stub = (self._dock_stubs or {}).get(dock)
            entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                     .get(dock)) or {}
            if checked:
                if stub is not None and entry.get("closed"):
                    # A closed dock may be floating by now: _can_stub_dock
                    # is false there, but its content still has to come
                    # back before the dock is shown.
                    stub(False)
                # A collapsed bottom slot is still "visible"; un-collapse
                # it or the checkbox appears to do nothing.
                chrome = (self._dock_chrome or {}).get(dock.windowTitle())
                if chrome is None and dock in (
                        getattr(self, "_run_dock", None),
                        getattr(self, "_results_dock", None),
                        getattr(self, "_data_tables_dock", None)):
                    chrome = (self._dock_chrome or {}).get("Results")
                if chrome is not None and chrome["get"]()["minimized"]:
                    chrome["set_minimized"](False)
                if on_show is not None:
                    on_show()
                dock.setVisible(True)
                dock.raise_()
                sync(True)
            elif dock.isFloating():
                dock.close()
                if on_hide is not None:
                    on_hide()
            elif self._can_stub_dock(dock):
                cast(Callable[[bool], None], stub)(True)
                sync(False)
            else:
                dock.close()
                if on_hide is not None:
                    on_hide()
            # A hidden bottom dock can change which tab sizes the strip;
            # keep the status bar from being squeezed out (#231).
            QTimer.singleShot(0, self._ensure_status_bar_visible)
        action.toggled.connect(toggle)
        dock.visibilityChanged.connect(sync)
        sync(dock.isVisible())

        self.setMenuWidget(self._ribbon)

    def _make_dock_minimizable(self, docks: list[QDockWidget], edge: str,
                                restore_max=None):
        """Simple pin-open minimize (not VS-style auto-hide). The toggle
        lives in the dock's own titlebar next to its float/close buttons.
        Collapsing hides the content and squeezes the dock down to just
        its titlebar, which gets a distinctly shaded background while
        collapsed so it isn't mistaken for a rendering glitch. `docks`
        can be several tabified widgets sharing one physical slot (Data
        Tables + Results collapse together as one unit): each keeps its
        own titlebar (whichever tab is active is the one visible) but
        they are driven by the same shared toggle.

        Qt has no native "collapse to stub" mode, hence
        `setTitleBarWidget` instead of the default one -- this
        reimplements float/close so the dock loses no functionality.

        `restore_max`: optional int or zero-arg callable giving the
        maximum size to restore to (bottom docks have their own,
        session-mutable cap -- `self._results_dock_max` -- so this is
        read fresh at restore time via a callable, not baked in here).
        """
        if self._dock_titlebar_rethemers is None:
            self._dock_titlebar_rethemers = []
        if self._dock_stubs is None:
            self._dock_stubs = {}
        if self._dock_state_sync is None:
            self._dock_state_sync = {}
        glyph_collapse = {"left": "◂", "right": "▸", "bottom": "▾"}[edge]
        glyph_expand = {"left": "▸", "right": "◂", "bottom": "▴"}[edge]
        # LEFT/RIGHT titlebars are always laid out horizontally (Qt
        # never rotates one for a side dock) -- there's no room for the
        # label + all three buttons at a genuinely thin stub width, so
        # collapsing there hides everything but the toggle itself. The
        # bottom dock only squeezes height, so its titlebar keeps its
        # full width and every button stays visible either way.
        entries: list[dict[str, Any]] = []
        for dock in docks:
            content = dock.widget()

            titlebar = _DockTitleBar(dock, self)
            tb = QHBoxLayout(titlebar)
            tb.setContentsMargins(8, 4, 4, 4)
            tb.setSpacing(2)
            # The tab bar gets its icon from `windowIcon`; a custom
            # titlebar has to paint the same one itself so the dock is
            # recognisable once floated as well.
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
            min_btn.setText(glyph_collapse)
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
                    lambda _checked=False, d=dock: (
                        self._float_results_properly(d, not d.isFloating())
                        if d is self._results_dock
                        else d.setFloating(not d.isFloating())))
                tb.addWidget(float_btn)
            if features & QDockWidget.DockWidgetFeature.DockWidgetClosable:
                close_btn = QToolButton()
                close_btn.setAutoRaise(True)
                close_btn.setIcon(icons.close_x_icon(active_colour()["text"]))
                close_btn.setProperty("titleIcon", "close_x_icon")
                close_btn.setToolTip("Close")
                close_btn.setAccessibleName(f"Close {dock.windowTitle()}")
                # left/right docks (rotated stub available) collapse to
                # the stub instead of vanishing; a dock moved to the
                # bottom, and any floated window, close for real.
                close_btn.clicked.connect(
                    lambda _checked=False, d=dock: (
                        d.close()
                        if (d.isFloating()
                            or self._current_dock_edge(d) not in
                            ("left", "right"))
                        else set_closed(True)))
                tb.addWidget(close_btn)

            # The rotated closed-dock stub (left/right only). Closing the
            # dock swaps this in as the titlebar and pins the dock to a
            # ~24px strip, so the panel's slot stays findable and
            # clickable instead of the dock vanishing entirely.
            stub = None
            if edge in ("left", "right"):
                word = {"Model Explorer": "Model"}.get(
                    dock.windowTitle(), dock.windowTitle())
                stub = _DockStub(word, -90.0 if edge == "left" else 90.0)
                stub.setFixedWidth(24)

            dock.setTitleBarWidget(titlebar)

            normal_min_width = max(dock.minimumWidth(), 0)
            normal_min_height = max(dock.minimumHeight(), 0)
            entries.append({"dock": dock, "content": content,
                            "titlebar": titlebar, "icon_label": icon_label,
                            "label": label,
                            "min_btn": min_btn, "float_btn": float_btn,
                            "close_btn": close_btn, "stub": stub,
                            "edge": edge,
                            "normal_min_width": normal_min_width,
                            "normal_min_height": normal_min_height,
                            "docked_width": None, "docked_height": None,
                            "minimized": False, "closed": False})

        # Every titled dock's own chrome entry (its title-bar buttons and
        # the last edge it was docked on), not just the group built last:
        # the loop runs once per dock slot, and replacing the map here
        # left `_redock` and the floating handler with no entry at all
        # for Explorer/Inspector.
        if getattr(self, "_dock_chrome_entries", None) is None:
            self._dock_chrome_entries = {}
        self._dock_chrome_entries.update({e["dock"]: e for e in entries})

        def retheme():
            c = active_colour()
            for e in entries:
                collapsed = e["minimized"]
                bg = c["group_header"] if collapsed else c["surface"]
                e["titlebar"].setStyleSheet(
                    f"background: {bg}; border-bottom: 1px solid "
                    f"{c['border']};")
                icon = e["dock"].windowIcon()
                if not icon.isNull():
                    e["icon_label"].setPixmap(icon.pixmap(16, 16))
                e["label"].setStyleSheet(
                    f"font-weight: 600; color: {c['accent'] if collapsed else c['text']};")
                if e["stub"] is not None:
                    # Custom-painted: must be told to repaint on a theme
                    # flip like the titlebars themselves.
                    e["stub"].update()
        req(self._dock_titlebar_rethemers).append(retheme)
        retheme()

        def sync_action(dock):
            """Push a dock's open/closed state to its ribbon action.
            `set_closed` hides the dock's *content* and swaps the
            titlebar for the stub, so the QDockWidget never becomes
            hidden and `visibilityChanged` never fires -- without this
            the Docks list keeps showing a closed panel as if it were
            there. Only the close path syncs: a *minimized* dock is
            still present and the ribbon toggle cannot restore it, so
            greying its action out would strand it."""
            fn = (self._dock_state_sync or {}).get(dock)
            if fn is not None:
                fn(dock.isVisible())

        def restore_side_extent(e, dock):
            """Re-apply the user's width once Qt has released the
            minimise/close lock. Deferred: the dock's min/max are still
            being changed on this turn."""
            width = int(e.get("docked_width") or 0)
            if not width:
                return
            generation = self._dock_resize_generation

            def apply(attempt=0):
                if generation != self._dock_resize_generation:
                    return      # Reset Layout cancelled this restore
                try:
                    if dock.isFloating() or e.get("minimized") \
                            or e.get("closed"):
                        return
                    if self._current_dock_edge(
                            dock, e.get("edge")) not in ("left", "right"):
                        return
                    actual = dock.width()
                    if attempt and actual >= width - 4:
                        return
                    requested = width + (width - actual) \
                        if actual < width - 4 else width
                    self._resize_docks([dock], [requested],
                                       Qt.Orientation.Horizontal)
                except (RuntimeError, TypeError):
                    return
                if attempt < 1:
                    QTimer.singleShot(
                        0, lambda: apply(attempt + 1))
            QTimer.singleShot(0, apply)

        def set_minimized(minimized: bool):
            for e in entries:
                dock, content = e["dock"], e["content"]
                edge_now = self._current_dock_edge(dock, e.get("edge"))
                e["edge"] = edge_now
                e["minimized"] = minimized
                content.setVisible(not minimized)
                e["min_btn"].setText(glyph_expand if minimized
                                     else glyph_collapse)
                e["min_btn"].setToolTip(
                    f"Restore {dock.windowTitle()}" if minimized
                     else f"Minimize {dock.windowTitle()}")
                e["min_btn"].setAccessibleName(
                    f"Restore {dock.windowTitle()}" if minimized
                    else f"Minimize {dock.windowTitle()}")
                if edge_now in ("left", "right"):
                    e["label"].setVisible(not minimized)
                    if e["float_btn"] is not None:
                        e["float_btn"].setVisible(not minimized)
                    if e["close_btn"] is not None:
                        e["close_btn"].setVisible(not minimized)
                if edge_now in ("left", "right"):
                    if minimized:
                        if not getattr(self, "_restoring_dock_chrome", False):
                            e["docked_width"] = max(dock.width(), 1)
                        e["titlebar"].adjustSize()
                        # Qt logs "Negative sizes" when setFixedWidth
                        # pairs the new width with a -1 height (dock
                        # not yet laid out) — clamp to the current
                        # height.
                        h = max(dock.height(), 1)
                        dock.setFixedHeight(h)
                        dock.setFixedWidth(
                            e["titlebar"].sizeHint().width())
                    else:
                        min_w = self._dock_min_dimension(e, "left")
                        dock.setMaximumSize(16777215, 16777215)
                        self._set_dock_min_width(dock, min_w)
                        dock.setMinimumHeight(0)
                        dock.setMaximumHeight(16777215)
                        restore_side_extent(e, dock)
                else:
                    if minimized:
                        # Remember the pre-collapse height so restore
                        # comes back to the same size rather than
                        # leaving it to the layout.
                        if not getattr(self, "_restoring_dock_chrome", False):
                            e["saved_height"] = dock.height()
                            e["docked_height"] = max(dock.height(), 1)
                        e["titlebar"].adjustSize()
                        w = max(dock.width(), 1)
                        dock.setFixedWidth(w)
                        dock.setFixedHeight(
                            e["titlebar"].sizeHint().height())
                    else:
                        # Release the width lock that collapsing applied:
                        # setFixedWidth(w) also set maximumWidth(w), so a
                        # bottom dock stayed 271 px wide forever after one
                        # minimise.
                        dock.setMaximumSize(16777215, 16777215)
                        dock.setMinimumWidth(0)
                        dock.setMaximumWidth(16777215)
                        min_h = self._dock_min_dimension(e, "bottom")
                        dock.setMinimumHeight(min_h)
                        cap = (16777215 if dock.isFloating() else
                               (restore_max() if callable(restore_max)
                                else restore_max))
                        dock.setMaximumHeight(cap or 16777215)
                        saved = e.get("saved_height") or e.get("docked_height")
                        if saved:
                            # Apply the remembered height first (fixed),
                            # then release the fix on the next event-
                            # loop pass so the splitter stays draggable.
                            # Guarded: the dock may already be torn down
                            # when the timer fires (window close).
                            dock.setFixedHeight(max(saved, min_h))
                            def _release(d=dock, cap_=cap or 16777215,
                                         nm=min_h):
                                try:
                                    d.setMinimumHeight(nm)
                                    d.setMaximumHeight(cap_)
                                except RuntimeError:
                                    pass  # C++ object already deleted
                            QTimer.singleShot(0, _release)
            retheme()
            # Minimising hides content without hiding the dock, so
            # `visibilityChanged` never fires; sync the ribbon toggle or
            # it still reads "open" and the next click closes instead of
            # restoring.
            for e in entries:
                sync_action(e["dock"])

        def _toggle_minimized(_checked=False):
            # A user minimize/restore takes ownership back from the
            # narrow-window compact policy.
            try:
                self._compact_minimized.discard(
                    entries[0]["dock"].windowTitle())
            except AttributeError:
                pass
            set_minimized(not entries[0]["minimized"])

        for e in entries:
            e["min_btn"].clicked.connect(_toggle_minimized)

        def set_closed(closed: bool):
            """Swap the dock between its normal chrome and the rotated
            closed-stub: a closed Model/Inspector shows its name rotated
            to face the user, and stays restorable by clicking."""
            for e in entries:
                dock, stub = e["dock"], e["stub"]
                if stub is None:
                    continue
                try:
                    floating = dock.isFloating()
                except RuntimeError:
                    continue
                if not closed:
                    # Restoring chrome (stub click, Properties,
                    # _show_inspector_dock) takes the dock back from the
                    # compact policy.
                    try:
                        self._compact_minimized.discard(dock.windowTitle())
                    except AttributeError:
                        pass
                if floating:
                    # The rotated side stub cannot exist on a free
                    # window. Restore normal chrome/content; a close
                    # request really closes the floating window.
                    e["closed"] = False
                    e["minimized"] = False
                    dock.setTitleBarWidget(e["titlebar"])
                    e["content"].setVisible(True)
                    # Release the stub's fixed-width lock, but keep the
                    # floating window's own 160x90 floor so reopening it
                    # cannot collapse below a usable panel.
                    dock.setMinimumSize(160, 90)
                    dock.setMaximumSize(16777215, 16777215)
                    if closed:
                        dock.close()
                    continue
                edge_now = self._current_dock_edge(dock, e.get("edge"))
                if closed and edge_now not in ("left", "right"):
                    # A dock moved to the bottom has no usable rotated
                    # side stub: close it for real, and leave the chrome
                    # flags consistent so View > Panels can restore it.
                    e["closed"] = False
                    e["minimized"] = False
                    dock.setTitleBarWidget(e["titlebar"])
                    dock.close()
                    continue
                e["closed"] = closed
                if closed:
                    if not e["minimized"] and not getattr(
                            self, "_restoring_dock_chrome", False):
                        # Remember the width the user had before the stub
                        # pins the dock to 24 px. Do not overwrite it when
                        # a minimised dock is already squeezed.
                        e["docked_width"] = max(
                            dock.width(), int(e.get("docked_width") or 0))
                    e["minimized"] = False
                    e["content"].setVisible(False)
                    dock.setTitleBarWidget(stub)
                    dock.setFixedHeight(max(dock.height(), 1))
                    dock.setFixedWidth(stub.width() or 24)
                else:
                    edge_now = self._current_dock_edge(dock, e.get("edge"))
                    e["edge"] = edge_now
                    dock.setTitleBarWidget(e["titlebar"])
                    if e["minimized"]:
                        # Restoring the *chrome* of a minimised dock must
                        # not release its collapse locks or reveal its
                        # content: `set_minimized(False)` owns that.
                        e["content"].setVisible(False)
                    else:
                        dock.setMaximumSize(16777215, 16777215)
                        if edge_now in ("left", "right"):
                            self._set_dock_min_width(
                                dock, self._dock_min_dimension(e, edge_now))
                            dock.setMinimumHeight(0)
                            restore_side_extent(e, dock)
                        else:
                            dock.setMinimumWidth(0)
                            dock.setMinimumHeight(
                                self._dock_min_dimension(e, "bottom"))
                        e["content"].setVisible(True)
            sync_action(e["dock"])
            retheme()

        for e in entries:
            e["set_minimized"] = set_minimized
            if e["stub"] is not None:
                e["stub"].clicked.connect(
                    lambda _c=False, fn=set_closed: fn(False))
                self._dock_stubs[e["dock"]] = set_closed
        # Layout persistence: `saveState()` cannot see the chrome (a closed dock is pinned to a 24 px stub, a
        # minimized one to its titlebar), so each slot says how to read and set its own.
        if self._dock_chrome is None:
            self._dock_chrome = {}
        self._dock_chrome[entries[0]["dock"].windowTitle()] = {
            "dock": entries[0]["dock"],
            "get": lambda: {"closed": bool(entries[0]["closed"]), "minimized": bool(entries[0]["minimized"])},
            "set_closed": set_closed if entries[0]["stub"] is not None else None,
            "set_minimized": set_minimized,
        }
        return set_minimized

    def _set_dock_icon(self, dock, factory_name: str,
                       token: str = "text") -> None:
        """Give one dock the painted glyph of its ribbon command."""
        from raptor.gui.resources import icons as _ic
        from raptor.gui.resources.design_tokens import active_colour
        dock.setWindowIcon(getattr(_ic, factory_name)(active_colour()[token]))

    def _apply_dock_icons(self) -> None:
        """Give every dock the same glyph as its ribbon command.

        `QDockWidget.setWindowIcon` is what the tabified QTabBar uses for
        its tab icon; the custom minimize titlebars draw it from the same
        window icon (see `_make_dock_minimizable`), so both stay in step.
        The tab bar copies the icon when the dock is tabified, so a theme
        change also updates the live tabs directly.
        """
        for attr, factory_name, token in self._DOCK_ICONS:
            dock = getattr(self, attr, None)
            if dock is not None:
                self._set_dock_icon(dock, factory_name, token)
        self._sync_dock_tab_icons()

    def _sync_dock_tab_icons(self) -> None:
        """Push changed window icons into already-built dock tab bars."""
        from PySide6.QtWidgets import QTabBar
        docks = [getattr(self, attr, None)
                 for attr, _factory, _token in self._DOCK_ICONS]
        try:
            bars = self.findChildren(QTabBar)
        except RuntimeError:
            return
        for bar in bars:
            if bar.objectName() == "qt_tabwidget_tabbar":
                continue
            # The ribbon's page strip has a tab titled "Results" too, and
            # matching by title alone put the Results dock glyph on that
            # page tab while every other page stayed text-only.
            if bar is getattr(getattr(self, "_ribbon", None), "tab_bar", None):
                continue
            for index in range(bar.count()):
                for dock in docks:
                    if dock is not None and dock.windowTitle() == \
                            bar.tabText(index):
                        bar.setTabIcon(index, dock.windowIcon())
                        break

    def _sync_dock_toggles(self) -> None:
        """Refresh every ribbon dock checkbox from the live dock state."""
        for fn in (getattr(self, "_dock_state_sync", None) or {}).values():
            try:
                fn(True)
            except RuntimeError:
                pass

    def _sync_results_float_action(self, floating: bool):
        """The ribbon's Float Window stays ticked exactly while the
        Results are in their own window, however they got there."""
        self._results_float_action.blockSignals(True)
        self._results_float_action.setChecked(floating)
        self._results_float_action.blockSignals(False)

    def _on_bottom_dock_visibility_changed(self, dock, visible: bool) -> None:
        """Apply the height policy of the bottom tab that came to front.

        `visibilityChanged` fires for a tabified dock when its tab is
        raised (True) or tucked behind a sibling (False), which is the
        only signal Qt gives for a tab click. Run keeps its content
        height; the other two release the group so the splitter can
        make Results smaller or larger. Deferred, so Qt's own tab swap
        has finished before the caps change.
        """
        if dock is None:
            return
        # Hiding Results can make Run the only bottom tab and let its
        # content height squeeze the status bar out (#231). Self-heal on
        # both show and hide, after Qt has applied the tab change.
        QTimer.singleShot(0, self._ensure_status_bar_visible)
        if not visible:
            return
        try:
            if dock.isFloating():
                return
            if self.dockWidgetArea(dock) not in (
                    Qt.DockWidgetArea.BottomDockWidgetArea,
                    Qt.DockWidgetArea.TopDockWidgetArea):
                return
        except RuntimeError:           # C++ object already torn down
            return
        self._bottom_active_dock = dock
        run = getattr(self, "_run_dock", None)
        if run is not None and dock is run and hasattr(run, "fit_height"):
            QTimer.singleShot(0, run.fit_height)
        else:
            QTimer.singleShot(0, self._release_bottom_strip_ceiling)
            if dock is getattr(self, "_data_tables_dock", None):
                QTimer.singleShot(0, self._ensure_data_tables_height)

    def _ensure_data_tables_height(self) -> None:
        dock = getattr(self, "_data_tables_dock", None)
        if dock is None:
            return
        try:
            if dock.isFloating() or not dock.isVisible():
                return
            if dock.height() < self.DATA_TABLES_OPEN_HEIGHT:
                self._release_bottom_strip_ceiling()
                self._resize_docks([dock], [self.DATA_TABLES_OPEN_HEIGHT],
                                 Qt.Orientation.Vertical)
        except RuntimeError:
            pass

    def _release_bottom_strip_ceiling(self) -> None:
        """Lift the height cap on every tab in the shared bottom slot.

        Results, Data Tables and Run are tabified into one physical
        strip, so the strip can only grow to the *smallest*
        `maximumHeight()` among its tabs. The Run panel deliberately caps
        itself to its content height (`fit_height`) so opening Simulate
        does not leave a mostly-empty dock -- but that cap then stranded
        Results at ~330 px, so Show Results could not stretch it any
        more. Lift all three here; the Run panel re-applies its own fit
        the next time Simulate is opened.
        """
        for d in (getattr(self, "_results_dock", None),
                  getattr(self, "_data_tables_dock", None),
                  getattr(self, "_run_dock", None)):
            if d is None:
                continue
            try:
                if d.isFloating():
                    continue
                d.setMaximumHeight(16777215)  # Qt's QWIDGETSIZE_MAX
            except RuntimeError:
                pass          # C++ object already torn down

    def _vertical_chrome_reserve(self) -> int:
        """Space the dock areas must leave for the fixed chrome: the
        ribbon plus the status bar plus separator slack. A smaller
        reserve would let a stretched Results squeeze the status bar out
        of the window entirely: Qt honours resizeDocks' request by
        shrinking whatever is left, and the status bar is last."""
        ribbon_h = self._ribbon.height() or \
            self._ribbon.sizeHint().height()
        sb_h = max(self.statusBar().height(),
                   self.statusBar().sizeHint().height())
        # separator slack: up to a few 5px separators + layout margins
        return ribbon_h + sb_h + 24

    def _clamp_dock_height(self, target: int) -> int:
        """Clamp a bottom-dock height request to what actually fits:
        the canvas has its own hard minimum height, and a request
        beyond `chrome + canvas_min` makes the QMainWindow layout
        overflow -- the status bar, the window's last item, is what Qt
        then pushes off-screen."""
        return max(160, min(target, self._max_bottom_dock_height()))

    def _max_bottom_dock_height(self) -> int:
        """The largest height the shared bottom dock strip can have
        without pushing the status bar out of the window."""
        try:
            canvas_min = max(
                self._diagram_dock.minimumSizeHint().height(),
                self._diagram_dock.minimumHeight(),
                260)
        except Exception:
            canvas_min = 280
        avail = self.height() - self._vertical_chrome_reserve() \
            - canvas_min
        return max(160, avail)

    def _update_dock_max_heights(self) -> None:
        """Hard ceiling on the bottom dock strip that tracks the
        window: a stale oversized strip can survive maximize untouched,
        because nothing re-clamps the dock's geometry then. Setting
        maximumHeight bounds every path at once: resizeDocks requests,
        splitter drags, and stale carried-over geometry. Floating docks
        are exempt (a floating window owns its own size)."""
        try:
            self.objectName()      # liveness: a deferred call can outlive
        except RuntimeError:       # the window's C++ object
            return
        limit = self._max_bottom_dock_height()
        docks = [d for d in self.findChildren(QDockWidget)
                 if d.parent() is self]
        entries = getattr(self, "_dock_chrome_entries", None) or {}
        bottom: list[QDockWidget] = []
        for dock in docks:
            try:
                if dock.isFloating():
                    entry = entries.get(dock) or {}
                    if not entry.get("minimized") and \
                            dock.maximumHeight() != 16777215:
                        dock.setMaximumHeight(16777215)
                    continue
                area = self.dockWidgetArea(dock)
            except RuntimeError:
                continue
            entry = entries.get(dock) or {}
            if entry.get("minimized"):
                continue
            if area in (Qt.DockWidgetArea.BottomDockWidgetArea,
                        Qt.DockWidgetArea.TopDockWidgetArea):
                bottom.append(dock)
            elif dock.maximumHeight() != 16777215:
                # No longer in a vertical dock area: a stale bottom cap
                # must not follow the dock to the right/left.
                dock.setMaximumHeight(16777215)
        # The active dock may have been moved out of the bottom area.
        # Recompute from the docks still there rather than trusting an
        # identity remembered before the move.
        active = getattr(self, "_bottom_active_dock", None)
        available = [d for d in bottom
                     if not (entries.get(d) or {}).get("minimized")]
        if active not in available:
            preferred = (getattr(self, "_results_dock", None),
                         getattr(self, "_data_tables_dock", None),
                         getattr(self, "_run_dock", None),
                         getattr(self, "_inspector_dock", None),
                         getattr(self, "_explorer_dock", None),
                         getattr(self, "_live_log_dock", None),
                         getattr(self, "_fev_inspector_dock", None))
            active = next((d for d in preferred if d in available), None)
        self._bottom_active_dock = active
        for dock in bottom:
            # A tab that is tucked behind does not size the strip;
            # leaving its cap in place would let the hidden Run tab
            # squeeze Results to its content height again.
            cap = limit
            if dock is active and dock is getattr(self, "_run_dock", None):
                # Run keeps its own content cap (never raise a lower cap
                # here, or the Simulate tab re-opens the empty field
                # under its last section). Every other active tab follows
                # the window limit both ways, so a shrink-then-grow
                # resize restores its ceiling.
                current = int(dock.maximumHeight())
                if current < cap:
                    cap = current
            if dock.maximumHeight() != cap:
                dock.setMaximumHeight(cap)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._dock_startup_snapshot_done:
            self._dock_startup_snapshot_done = True
            QTimer.singleShot(0, self._snapshot_startup_dock_sizes)
        # The startup clamp is queued when a model loads, which can be
        # BEFORE the window is shown. Qt's first post-show layout pass
        # then gives the tabified Results dock its sizeHint (a tall
        # strip) and leaves the canvas at its minimum. A single deferred
        # call is undone by that pass; the second event-loop turn lands
        # after it and sticks.
        if not getattr(self, "_canvas_room_shown", False):
            self._canvas_room_shown = True
            # A window with no model has no cramped canvas to fix (and
            # most test windows are shown empty); skipping it avoids two
            # extra layout passes for every window.
            if self._rbd is None:
                return
            self._canvas_room_stage = 0
            self._canvas_room_timer.start()

    def _canvas_room_tick(self) -> None:
        if self._canvas_room_stage == 0:
            self._canvas_room_stage = 1
            self._canvas_room_timer.start()
            return
        self._apply_startup_canvas_room()

    def _apply_startup_canvas_room(self) -> None:
        """The second-turn half of `showEvent`'s layout clamp.

        Skip it once the Simulate ribbon is open: the Run panel sizes the
        shared strip to its own content (`fit_height`), and a startup
        clamp after that would squeeze it back."""
        try:
            if self._ribbon.tab_bar.currentIndex() == \
                    getattr(self, "_simulate_page_index", -1):
                return
            self._give_canvas_room()
        except RuntimeError:
            pass

    def resizeEvent(self, event):
        if not self._canvas_room_defaults_done:
            # Check BEFORE Qt lays out the resized window: a size that
            # already differs from the startup snapshot is a splitter
            # drag made before this resize, and must survive it.
            if self._dock_sizes_changed_since_startup():
                self._dock_splitter_moved = True
            self._dock_rebase_pending = True
            QTimer.singleShot(0, self._rebase_startup_bottom_extents)
        super().resizeEvent(event)
        # Maximise/restore/resize: re-apply the bottom dock ceiling so
        # a strip sized for a previous window state can't clip the
        # status bar in the new one. Deferred one event-loop turn: the
        # dock areas settle after the window resize lands.
        self._update_dock_max_heights()
        QTimer.singleShot(0, self._ensure_status_bar_visible)
        self._fit_status_readouts()
        QTimer.singleShot(0, self._apply_compact_layout)

    def _apply_compact_layout(self) -> None:
        """Collapse side docks on very narrow windows.

        At 800×600 the fixed 320/400 px side panels left a 70 px canvas.
        Collapse them to their titlebar stubs while the window is that
        narrow, and restore only the ones this policy collapsed.
        """
        try:
            compact = self.width() < 1000
            forced = bool(getattr(self, "_compact_forced", False))
            chrome = self._dock_chrome or {}
            if compact and not forced:
                self._compact_minimized = set()
                for title in ("Model Explorer", "Inspector"):
                    c = chrome.get(title)
                    if c is None:
                        continue
                    state = c["get"]()
                    dock = c.get("dock")
                    # A closed stub must stay closed (and keep its saved
                    # width), a dock the user already minimized is their
                    # choice, and a floating window is not part of the
                    # narrow main-window side layout.
                    if state["closed"] or state["minimized"]:
                        continue
                    if dock is not None:
                        try:
                            if dock.isFloating():
                                continue
                        except RuntimeError:
                            continue
                    c["set_minimized"](True)
                    self._compact_minimized.add(title)
                self._compact_forced = True
            elif not compact and forced:
                for title in getattr(self, "_compact_minimized", set()):
                    c = chrome.get(title)
                    if c is not None and not c["get"]()["closed"]:
                        c["set_minimized"](False)
                self._compact_minimized = set()
                self._compact_forced = False
        except Exception:
            pass

    def _fit_status_readouts(self) -> None:
        """Show the status-bar run readouts only when there is room."""
        ro = getattr(self, "_readouts", None)
        if ro is not None:
            ro.setVisible(self.width() >= 1200)

    def resizeDocks(self, docks, sizes, orientation) -> None:
        """Mark a size choice made outside this window's own layout code.

        QMainWindow has no "user dragged the splitter" signal, so the
        public `resizeDocks()` is the one observable entry point. The
        window's own startup/fit/restore calls go through
        `_resize_docks()` with the internal guard set; anything else is
        treated as a deliberate size that the first model load must not
        overwrite.
        """
        if not getattr(self, "_internal_dock_resize", False) and docks \
                and all(isinstance(d, QDockWidget) for d in docks):
            self._dock_user_sized = True
        super().resizeDocks(docks, sizes, orientation)

    def _resize_docks(self, docks, sizes, orientation) -> None:
        """Internal `resizeDocks()`: never marks the docks user-sized."""
        previous = getattr(self, "_internal_dock_resize", False)
        self._internal_dock_resize = True
        try:
            super().resizeDocks(docks, sizes, orientation)
        finally:
            self._internal_dock_resize = previous

    @staticmethod
    def _set_dock_min_width(dock, width: int) -> None:
        """Set a dock's minimum WIDTH without tripping Qt's -1 sentinel.

        `QWidget.setMinimumWidth(w)` routes through
        `setMinimumSize(w, minimumHeight())`; a freshly built or restored
        QDockWidget can report the unset `-1` height, which Qt logs as
        `Negative sizes (340,-1) are not possible`. Pair the width with a
        real height instead.
        """
        try:
            dock.setMinimumSize(int(width), max(1, dock.minimumHeight()))
        except RuntimeError:
            pass          # C++ object already torn down (window closing)

    def _ensure_status_bar_visible(self):
        """Self-heal the status bar: a dock layout that overflows can
        clip it (and the window may then refuse to resize). Detect the
        actual clipped geometry rather than trusting the requested
        sizes: if the status bar sits below the window's bottom edge,
        shrink the bottom dock strip to the ceiling and log a full
        geometry dump (RAPTOR_GUI_DEBUG) to diagnose the cause."""
        try:
            sb = self.statusBar()
            geo = sb.geometry() if sb is not None else None
            if geo is None:
                return
            _ = self.height()   # raises RuntimeError if the C++ object
            # was already deleted (tests and teardown close the window while
            # the deferred self-heal singleShot is still queued).
        except RuntimeError:
            return
        # The status bar is fixed chrome: always keep its floor, even when
        # no model is open or the current geometry happens to look fine.
        try:
            sb.setMinimumHeight(sb.sizeHint().height())
            sb.show()
        except RuntimeError:
            return
        clipped = (geo.y() + geo.height() > self.height()) or \
            (sb.isVisibleTo(self) is False and not sb.isHidden()) or \
            geo.y() >= self.height()
        if not clipped:
            return
        limit = self._max_bottom_dock_height()
        log = logging.getLogger("raptor.gui")
        # Debug, not warning: this is the self-heal working (it runs on
        # every resize that overflows), and it printed to the terminal
        # each time.
        log.debug(
            "status bar clipped (sb y=%s h=%s, window h=%s) -- "
            "shrinking bottom docks to %s. State: results=%s "
            "(min=%s max=%s) tables=%s (min=%s max=%s) canvas=%s "
            "(min=%s) ribbon=%s winMin=%s",
            geo.y(), geo.height(), self.height(), limit,
            self._results_dock.height(),
            self._results_dock.minimumHeight(),
            self._results_dock.maximumHeight(),
            self._data_tables_dock.height(),
            self._data_tables_dock.minimumHeight(),
            self._data_tables_dock.maximumHeight(),
            self._diagram_dock.height(),
            self._diagram_dock.minimumSizeHint().height(),
            self._ribbon.height(),
            self.minimumSizeHint().height())
        try:
            self._update_dock_max_heights()
            docks = [d for d in (self._results_dock,
                                 self._data_tables_dock,
                                 self._run_dock)
                     if d is not None and not d.isFloating()
                     and d.isVisible()]
            if docks:
                self._resize_docks(docks, [limit] * len(docks), Qt.Orientation.Vertical)
            for d in (self._results_dock, self._data_tables_dock,
                      self._run_dock):
                if d is not None and not d.isFloating():
                    # A content-sized Run panel can leave a minimum that
                    # makes the window unable to shrink; release it before
                    # applying the ceiling.
                    d.setMinimumHeight(min(120, max(1, limit)))
                    d.setMaximumHeight(limit)
            self._statusbar.setMinimumHeight(
                self._statusbar.sizeHint().height())
        except Exception:
            pass

    def _sync_dock_tab_bars(self):
        """QMainWindow's tabified-dock tab bars default to
        ElideRight, so "Data Tables"/"Results"/"Live Log"/"Inspector"
        collapse to an ellipsis even with the whole window width free.
        ElideNone fixes that. The tabs stay non-expanding, packed at the
        left, rather than stretched across the window;
        idempotent, re-run whenever a dock is first tabified (the Live
        Log joins the right-side strip on its first show, after
        startup)."""
        from PySide6.QtWidgets import QTabBar
        from PySide6.QtCore import Qt as _Qt
        try:
            bars = self.findChildren(QTabBar)
        except RuntimeError:
            # A deferred topLevelChanged/dockLocationChanged callback can
            # land after the window's C++ object is gone (tests close
            # windows aggressively).
            return
        for bar in bars:
            # QTabWidget-owned bars carry a marker objectName and manage
            # their own eliding; only QMainWindow's dock-tab bars need
            # this (the Inspector's own 7-tab strip is a QTabWidget bar
            # and must keep its scroller).
            if bar.objectName() == "qt_tabwidget_tabbar":
                continue
            bar.setElideMode(_Qt.TextElideMode.ElideNone)
            bar.setExpanding(False)

    def _snapshot_startup_dock_sizes(self) -> None:
        """Remember the first laid-out sizes before any model load.

        A real splitter drag does not call Python `resizeDocks()`, so the
        first-load canvas clamp compares against this untouched snapshot
        to tell a user's size from Qt's default strip.
        """
        try:
            self._dock_startup_extents = self._current_dock_extents()
            self._dock_startup_window_size = (self.width(), self.height())
        except RuntimeError:
            pass

    def _current_dock_extents(self) -> dict | None:
        docks = (getattr(self, "_explorer_dock", None),
                 getattr(self, "_inspector_dock", None),
                 getattr(self, "_results_dock", None),
                 getattr(self, "_data_tables_dock", None))
        extents = {}
        for dock in docks:
            if dock is None or dock.isFloating():
                continue
            entry = (getattr(self, "_dock_chrome_entries", None)
                     or {}).get(dock) or {}
            if entry.get("minimized") or entry.get("closed"):
                continue
            extents[dock] = (dock.width(), dock.height())
        return extents or None

    def _rebase_startup_bottom_extents(self) -> None:
        """Rebase the bottom baseline after a window resize.

        Qt legitimately changes the bottom strip's height when the window
        grows, so comparing a post-resize height with the original
        snapshot would look like a splitter drag. Re-snapshot the bottom
        docks once the resize layout has settled; a later splitter drag
        still differs from that baseline even though the window is now a
        different size.
        """
        try:
            if self._canvas_room_defaults_done:
                return
            self._dock_rebase_pending = False
            if getattr(self, "_dock_startup_window_size", None) == (
                    self.width(), self.height()):
                return      # not a window resize; a splitter changed a dock
            snapshot = getattr(self, "_dock_startup_extents", None)
            current = self._current_dock_extents()
            if not snapshot or not current:
                return
            for dock in list(snapshot):
                if dock not in current:
                    # Minimised/closed between snapshot and resize: keep
                    # its old baseline; the chrome itself is the user's
                    # choice and first-load sizing must not override it.
                    continue
                entry = (getattr(self, "_dock_chrome_entries", None)
                         or {}).get(dock) or {}
                if self._current_dock_edge(
                        dock, entry.get("edge")) not in ("left", "right"):
                    snapshot[dock] = current[dock]
            self._dock_startup_window_size = (self.width(), self.height())
        except RuntimeError:
            # Resize event queued a callback that outlived the window.
            return

    def _dock_sizes_changed_since_startup(self) -> bool:
        snapshot = getattr(self, "_dock_startup_extents", None)
        if not snapshot:
            return False
        # Compare the sizes at the START of the load, not after the load
        # rebuilt the panels: Qt may legitimately change the strip while
        # the model populates, and that is not a user size.
        current = getattr(self, "_dock_extents_at_load_start", None) \
            or self._current_dock_extents() or {}
        for dock, (width, height) in snapshot.items():
            try:
                if dock.isFloating():
                    continue
                entry = (getattr(self, "_dock_chrome_entries", None)
                         or {}).get(dock) or {}
                if entry.get("minimized") or entry.get("closed"):
                    # A collapsed dock is not resized by the first-load
                    # defaults anyway. Ignore only this dock; the other
                    # docks (notably the bottom strip) still need their
                    # designed size.
                    continue
                if dock not in current:
                    continue
                cw, ch = current[dock]
                edge = self._current_dock_edge(
                    dock, entry.get("edge"))
                if edge in ("left", "right"):
                    changed = abs(cw - width) > 2
                else:
                    # A pending resize rebase means the bottom height may
                    # legitimately have changed with the window. A
                    # recorded splitter drag overrides that.
                    changed = (not self._dock_rebase_pending
                               and abs(ch - height) > 2)
                if changed:
                    return True
            except RuntimeError:
                continue
        return False

    def _give_canvas_room(self, *, force: bool = False):
        """Apply the designed first-load dock sizes exactly once.

        `QMainWindow` gives the tabified bottom strip a tall default
        size, so the first model load still needs the 150/320/400 clamp.
        But a size the user (or a restored layout) chose before that
        load is deliberate: `_dock_user_sized` records any public
        `resizeDocks()` call, and later loads never touch sizes again.
        `force=True` (Reset Layout) reapplies the designed sizes even if
        that means shrinking.
        """
        if not force:
            if self._canvas_room_defaults_done:
                self._update_dock_max_heights()
                return
            if self._dock_user_sized or self._dock_splitter_moved \
                    or self._dock_sizes_changed_since_startup():
                # Chosen before the first load (or restored from a saved
                # layout): keep it, and do not try again later.
                self._canvas_room_defaults_done = True
                self._update_dock_max_heights()
                return
        try:
            bottom = [d for d in (getattr(self, "_data_tables_dock", None),
                                  getattr(self, "_results_dock", None),
                                  getattr(self, "_run_dock", None))
                      if d is not None and not d.isFloating()]
            active = getattr(self, "_bottom_active_dock", None) \
                or getattr(self, "_results_dock", None)
            if active is not None and active in bottom:
                entry = (getattr(self, "_dock_chrome_entries", None)
                         or {}).get(active) or {}
                if not entry.get("minimized"):
                    self._resize_docks([active], [150],
                                       Qt.Orientation.Vertical)
            # Startup widths for the side docks: Explorer keeps its
            # designed 320, Inspector gets 400 so its tab strip doesn't
            # scroll. Minimised/closed slots are not touched.
            for dock, target in ((getattr(self, "_explorer_dock", None), 320),
                                 (getattr(self, "_inspector_dock", None), 400)):
                if dock is None or dock.isFloating():
                    continue
                entry = (getattr(self, "_dock_chrome_entries", None)
                         or {}).get(dock) or {}
                if entry.get("minimized") or entry.get("closed"):
                    continue
                self._resize_docks([dock], [target],
                                   Qt.Orientation.Horizontal)
            self._canvas_room_defaults_done = True
            if force:
                self._dock_user_sized = False
        except Exception:
            pass

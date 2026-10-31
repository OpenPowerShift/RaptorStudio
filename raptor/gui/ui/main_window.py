"""RAPTOR Studio main window — QMainWindow shell with ribbon, docks, and diagram.

Assembles the full application shell: ribbon bar, model explorer dock (left),
diagram canvas (center), inspector dock (right). Status-bar readouts and the
stepping debugger follow Raptor 7.
"""
from __future__ import annotations
from raptor.gui.ui.modality import exec_dialog

from raptor.gui.ui.qtcompat import req

from raptor.gui.ui import msgbox
from raptor.bundled import candidate_roots
import logging
import os
import platform
import re
import time
from typing import Any, cast
from pathlib import Path
from PySide6.QtCore import (QSize, Qt, QTimer, QObject, QPoint, QUrl, Signal)
from PySide6.QtGui import (QCursor, QKeySequence, QPainter, QColor, QPixmap, QDesktopServices,
                           QShortcut)
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (
    QApplication, QToolTip, QDoubleSpinBox, QMainWindow, QDockWidget, QStatusBar, QFileDialog, QMessageBox,
    QDialog, QFormLayout, QPushButton, QLabel, QHBoxLayout, QVBoxLayout, QWidget,
    QToolButton, QTreeWidget, QTreeWidgetItem,
)

from raptor.gui.ui import filedialogs as fdg
from raptor.gui.ui.fev_mixin import FevMixin
from raptor.gui.ui.isolate_mixin import IsolateMixin
from raptor.gui.ui import settings
from raptor.core import run_experiment, run_manifest
from raptor.gui.ui.run_experiment_mixin import (
    RunExperimentMixin,
)
from raptor.core.fileio.rbd import load_rbd, save_rbd, RbdFile
from raptor.core.fileio import rbds as rbds_io
from raptor.core.fileio.legacy_layout import GUI_REFINE_BUDGET_S, refine_budget
from raptor.core.engine.weak_metric import (
    canonical as canonical_analysis, cross_run_event_metric,
    cross_run_node_metric, cross_run_weak_metric)
from raptor.core.fileio.typed import system_config_from_row
from raptor.core.model import BlockStatus
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.engine.step_session import StepSession
from raptor.core.engine.load import load_model
from raptor.core.engine.errors import RunAborted
from raptor.core.engine.reports import (
    build_end_of_run_report,
    build_final_results_report,
)
from raptor import __version__ as _APP_VERSION
from raptor.gui.new_model import build_new_model
from raptor.gui.widgets.ribbon import Ribbon, RibbonButton
from raptor.gui.command_table import COMMANDS, LAYOUT
from raptor.gui.commands import (PREDICATES, AppState, CommandRegistry, build_pages)
from raptor.gui.ui.ribbon_builders import RibbonBuilders


from raptor.gui.ui.diagram import (
    ANNO_APPLICABLE_PROPS, DiagramDock, RowCellCmd, TableRowCmd,
)
from raptor.gui.ui.diagram_commands import apply_row_edits
from raptor.gui.ui.inspector import InspectorDock
from raptor.gui.ui.explorer import ExplorerDock
from raptor.gui.ui.results import ResultsDock
from raptor.gui.ui.data_tables import DataTablesDock
from raptor.api.tables import parse_event_log_line
from raptor.gui.resources import icons
from raptor.gui.resources.design_tokens import (
    APPENDIX_D_STATE_COLOURS, ThemeManager, active_colour,
    global_stylesheet,
)


def _engine_version() -> str:
    """The version string recorded in a run manifest, so a result can be
    traced back to the engine that produced it."""
    return f"raptor-studio {_APP_VERSION}"


#: Alt key tip for the File button. Not F: that is Finish's, and a
#: shipped letter is never reassigned (`test_key_tips.py`).
FILE_KEY_TIP = "E"

#: The Simulate tab's analysis toggles (built in `ribbon_builders`).
_ANALYSIS_ICONS = {
    "weakAnalysis": "chain_link_icon",
    "costAnalysis": "dollar_icon",
    "capacityAnalysis": "gauge_icon",
}

#: Canvas selection clicks are coalesced: a burst of clicks (rubber-band
#: drag, arrow-key walk) rebuilds the Inspector once, for the element the
#: user actually landed on. 80 ms is below the threshold where a click
#: still feels immediate, and well under the ~9 ms rebuild on a large
#: model, so it removes the per-click cost without adding perceived lag.
INSPECTOR_SELECT_DEBOUNCE_MS = 80


class _KeyTipFilter(QObject):
    """While key tips show: hand every key press, whichever widget it was
    sent to, to the window's key-tip handler (see `_show_key_tips`)."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress and self._win.isActiveWindow():
            return self._win._key_press_event_filter(self._win, event)
        return False


class _NoMinimumWidth(QWidget):
    """A container that asks for its natural width but sets no minimum, so
    what it holds does not stop the window getting narrower."""

    def minimumSizeHint(self):                            # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint


class _OverlayKeeper(QObject):
    """Keeps the File view on top while it is open. Docks raise themselves
    in many places (a run finishing raises Results), and a raised dock sat
    over the File view; this puts the view back above any dock or the
    canvas that comes forward or appears."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.Type.ZOrderChange, QEvent.Type.Show):
            bs = getattr(self._win, "_backstage", None)
            if bs is not None and bs.isVisible() and obj is not bs:
                bs.raise_()
        return False


class _FloatedResultsCloser(QObject):
    """Closing the floated Results window puts it back in the main
    window rather than hiding the results: the window is a convenience
    view, not somewhere the results can be lost."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.Close and obj.isFloating():
            event.ignore()
            self._win._float_results_properly(obj, False)
            return True
        return False


class _TooltipKeeper(QObject):
    """Qt auto-hides tooltips after a few seconds even when the cursor
    holds still, which is too short for the long glossary and field
    hints in this app. While a tooltip is on screen, re-show the
    current text every 3 s so it stays up for as long as the cursor
    rests on the same widget.

    Polls QToolTip's static state rather than installing an
    application-wide event filter: an app-wide filter that receives
    events from QtWebEngine's internal objects crashes inside PySide's
    QObject wrapper resolution. Polling needs no filter and covers both
    static widget tooltips and item-view tooltips resolved from the
    model's Qt.ToolTipRole."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._reshow)
        self._last_text = ""
        self._last_pos = QPoint(-1, -1)

    def install(self, app):
        # `app` kept for signature compatibility; the poll is global by
        # nature (QToolTip state is process-wide).
        self._timer.start()

    def _reshow(self):
        if not QToolTip.isVisible():
            self._last_text = ""
            return
        text = QToolTip.text()
        if not text:
            self._last_text = ""
            return
        cur = QCursor.pos()
        if text == self._last_text and cur == self._last_pos:
            # Same tooltip, cursor parked: re-show to defeat the
            # auto-hide timer.
            QToolTip.showText(cur, text)
            return
        self._last_text = text
        self._last_pos = QPoint(cur)


#: (canonical TerminationType code, human wording). The file and engine
#: key off the codes; every dialog shows what the run actually
#: finishes on instead of the TimeSim/FailSim/CycleSim acronyms.
_TERMINATION_CHOICES = [
    ("TimeSim", "Time simulation — finish at the run length"),
    ("FailSim", "Failure simulation — finish after N system failures"),
    ("CycleSim", "Phase cycle simulation — finish after N phase cycles"),
]


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


def _live_state_signature(blocks, cap_data, model, system_state=None) -> tuple:
    """Fingerprint of everything the live canvas paints.

    The animated run must repaint when something actually changed, but a
    3,470-block model spends its first ~6,900 scheduler steps at t=0
    (block kickoff) with an identical picture. Repainting those frames
    looked like a frozen run and cost a full frame each. Compare this
    first; skip only the canvas repaint (status text and event-loop
    pumping still run every tick).

    Only painted state goes in: block op/active status and phasing,
    node/hierarchy/event status, and the live flow/capacity dicts.
    Link states are derived from block/node changes in the same event,
    so they do not need their own 5,184-entry scan.
    """
    if isinstance(blocks, dict):
        blocks = blocks.values()
    sig: list = [getattr(system_state, "color", None),
                 getattr(system_state, "current_phase", None)]
    for b in blocks:
        run_state = getattr(b, "run_state", None)
        sig.append((getattr(b, "op_status", None),
                    getattr(b, "active_status", None),
                    getattr(run_state, "fev_induced_by_dependency", False),
                    (getattr(run_state, "phase_stress", None)
                     if getattr(b, "uses_phasing", False) else None)))
    if model is not None:
        if isinstance(model, dict):
            # Plain per-status maps from the background worker.
            for key in ("nodes", "hiers", "events"):
                sig.append(tuple(sorted((model.get(key) or {}).items())))
        else:
            sig.append(tuple(n.status for n in model.nodes_by_id.values()))
            sig.append(tuple(
                (h.status, h.dsi_state, h.num_currently_failed,
                 h.dsi_yellows)
                for h in model.hiers_by_id.values()))
            sig.append(tuple((e.op_status, e.active_status)
                             for e in model.events_by_id.values()))
    if cap_data is not None:
        sig.append(tuple(cap_data[0].items()))
        sig.append(tuple(cap_data[1].items()))
    return tuple(sig)


class MainWindow(RibbonBuilders, RunExperimentMixin, FevMixin, IsolateMixin, QMainWindow):
    """The application shell.

    `RunExperimentMixin` supplies every read and write of the run
    experiment (trials, termination, analyses, recording) plus the
    readiness line and the panel wiring. It is a mixin rather than a
    collaborator because those methods reach a dozen pieces of window
    state; as a separate file the coupling is at least explicit and
    greppable, and the file reads on its own. See
    `ui/run_experiment_mixin.py` for what it expects on `self`."""
    #: Default background-run poll interval (ms) -- the poll samples
    #: worker-thread state onto the canvas; see `_sync_bg_poll_interval`
    #: for why this shrinks at speeds faster than 10 events/sec.
    _BG_POLL_INTERVAL_DEFAULT = 100
    #: Never poll faster than this regardless of configured speed --
    #: smooth enough for human perception, without hammering the Qt
    #: event loop at Max speed.
    _BG_POLL_INTERVAL_FLOOR = 30
    #: the stepping session, the background-run thread and its last progress/bar keys (None when idle)
    _step_session: Any = None
    _bg_thread: Any = None
    _bg_last_progress: Any = None
    _bg_last_bar: Any = None

    def __init__(self):
        super().__init__()
        self._rbd: RbdFile | None = None
        #: Templates of the library last imported (Data ▸ Import Library); the Block Defaults dialog offers them.
        self._block_library: list[dict] = []
        self._current_file: str | None = None
        self._dirty = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave_tick)
        # Background-run poller (no cross-thread Qt calls: the worker
        # only writes a plain dict; this timer reads it on the GUI
        # thread).
        self._bg_poll = QTimer(self)
        self._bg_poll.setInterval(self._BG_POLL_INTERVAL_DEFAULT)
        self._bg_poll.timeout.connect(self._bg_poll_tick)
        # Canvas selection -> Inspector rebuild is debounced (see
        # `_on_canvas_block_selected`): one rebuild for a burst of clicks.
        self._pending_block_selection: int | None = None
        #: Any other selected kind (node/event/hier/annotation), queued on
        #: the same timer. Their Inspector rebuilds are heavier than a
        #: block's on a big model (a node's Dependency tab lists every
        #: block), so the click must not pay for them either.
        self._pending_inspector_selection: tuple[str, int] | None = None
        self._inspector_select_timer = QTimer(self)
        self._inspector_select_timer.setSingleShot(True)
        self._inspector_select_timer.setInterval(INSPECTOR_SELECT_DEBOUNCE_MS)
        self._inspector_select_timer.timeout.connect(
            self._flush_canvas_block_selection)
        # Layout settles only after two event-loop turns; a parented
        # timer (not QTimer.singleShot with a lambda) cannot fire after
        # this window's C++ object is gone.
        self._canvas_room_stage = 0
        self._canvas_room_timer = QTimer(self)
        self._canvas_room_timer.setSingleShot(True)
        self._canvas_room_timer.setInterval(0)
        self._canvas_room_timer.timeout.connect(self._canvas_room_tick)
        # One model per window; the registry is what the windows share.
        from raptor.gui import open_models
        self._window_serial = open_models.register(self)
        # Recovery notice (auto-save of never-saved models): surface a
        # leftover recovery copy shortly after startup.
        QTimer.singleShot(600, self._check_recovery)
        self.setWindowTitle("RAPTOR Studio")
        from raptor.gui.ui.splash import app_icon
        self.setWindowIcon(app_icon())
        self.resize(self._initial_window_size())
        self._persist_layout = False
        self.setDockNestingEnabled(True)
        self.setStyleSheet(global_stylesheet())
        # Safety net for entry points that build a MainWindow without
        # app.main() (test harnesses, embedding): same rationale as
        # app.py's own setStyle call just above.
        from PySide6.QtWidgets import QStyleFactory
        qapp = cast(QApplication | None, QApplication.instance())
        if qapp is not None and qapp.style().objectName().lower() != "fusion":
            qapp.setStyle(QStyleFactory.create("Fusion"))
        # File dialogs: never ask the desktop MIME database for a per-file
        # icon (see ui/filedialogs.py -- the missing `asciidoctor.svg`).
        from raptor.gui.ui.filedialogs import install_plain_icons
        install_plain_icons(qapp)

        self._setup_ribbon()
        self._setup_central()
        self._setup_docks()
        self._setup_statusbar()
        self._wire_signals()
        self._apply_action_icons()
        # Every command's shortcut works whatever ribbon tab is showing. Qt
        # honours an action's shortcut only while a widget it belongs to is
        # visible, and these belonged only to ribbon buttons -- so F5 did
        # nothing unless the Simulate tab was open, and Ctrl+O / Ctrl+N /
        # Ctrl+P (File view buttons) never worked at all.
        self.addActions([a for a in self._commands.actions.values()
                         if not a.shortcut().isEmpty()])

        # F1 = context help: jump from the focused dock/dialog to the
        # matching section of the reference manual (see _open_context_help).
        self._context_help_anchor = {}
        help_sc = QShortcut(QKeySequence("F1"), self)
        # Ctrl+S/Z/Y "anywhere in this window" regardless of which child
        # has focus: one QAction each, at WindowShortcut context (the Qt
        # default). ApplicationShortcut made a second open model window a
        # second global Ctrl+Z, which Qt reports as "QAction::event:
        # Ambiguous shortcut overload"; a window context only competes
        # with itself, and the focused window's undo is the right one.
        help_sc.activated.connect(self._open_context_help)

        # Esc = pause/stop during a run. A WindowShortcut fires no matter
        # which child of the window has focus; when no run is active it
        # re-dispatches Esc to the focused widget so the canvas's own
        # Esc semantics (cancel tool/annotation edit) keep working.
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        # Re-entrancy latch for the re-dispatch below: a synthetic Escape
        # sent to the focused widget propagates back up to this window
        # shortcut, and without the latch it re-sends it forever.
        self._esc_redispatching = False
        self._esc_shortcut.activated.connect(self._handle_escape_activated)

        # Alt = show key tips, then Alt+<letter> to fire. A single
        # QShortcut on Alt alone, caught on release: the spec requires
        # "key tips shown after Alt" (`blueprint:146`), and with the
        # Simulate ribbon down to 15 controls the run settings now live in
        # a panel, so a keyboard route that does not require finding a
        # dock matters. Letters are `key_tip` on the command's table entry
        # (command_table.py), never derived from the label, so renaming
        # a button cannot reassign a key somebody already learned.
        self._key_tips_active = False
        # (Alt itself is caught in keyPressEvent/keyReleaseEvent: Qt does
        # not match a shortcut made of a modifier alone.)
        # Alt+<letter> arrives as an ordinary key press (a lone Alt
        # QShortcut does not compose in Qt), delivered to whichever widget
        # has focus -- usually the canvas, not the window. So while the
        # tips show, an application-wide filter catches it (installed then
        # and removed after: a PERMANENT app-wide filter was taken out to
        # avoid a WebEngine crash). The window filtering itself, as it did,
        # never saw the key: the tips showed and no letter did anything.
        self._key_tip_filter = _KeyTipFilter(self)

        self._tooltip_keeper = _TooltipKeeper(self)
        self._tooltip_keeper.install(QApplication.instance())

        ThemeManager.instance().on_changed(self._on_theme_changed)
        # One-shot settings migration BEFORE any read, so a user
        # upgrading gets their preferences moved into the namespaced
        # tree (and told if the gap was too big to interpret) rather
        # than silently reset. See `ui/settings.py`.
        settings.ensure_migrated()
        # Remember the theme across launches. Applied before the window
        # is shown, so startup doesn't flash light first; the checkable
        # ribbon action follows via the _on_theme_changed callback.
        ThemeManager.instance().set_dark(
            settings.get_bool("appearance/dark_mode"))

    @property
    def _step_session(self):
        return getattr(self, "_step_session_obj", None)

    @_step_session.setter
    def _step_session(self, session) -> None:
        """A session starting or ending changes what Terminate / Finish /
        Restart / Jump can do; it is set in many places, so they follow it
        here rather than at each one."""
        changed = (session is None) != (getattr(self, "_step_session_obj", None) is None)
        self._step_session_obj = session
        if changed and getattr(self, "_commands", None) is not None:
            self._refresh_commands(only={"stepping", "jump_available"})

    def _show_key_tips(self) -> None:
        """Alt: reveal the key tips on every ribbon button of the current
        page, and arm Alt+<letter>.

        The spec wants "Alt key tips and keyboard focus [to] traverse all
        commands" (`blueprint:675`); the visible part is a tip on each
        button, and the invisible part is that the next key press runs
        the matching action. Toggled by Alt again, or by any other key.
        """
        if self._key_tips_active:
            self._dismiss_key_tips()
            return
        self._key_tips_active = True
        for btn in self._ribbon.findChildren(RibbonButton):
            text = btn.property("ribbonActionText") or ""
            tip = btn.property("ribbonKeyTip")
            if tip:
                btn.setText(f"{text}  ({tip})")
        # Large buttons already sit text-under-icon; small ones flip to
        # it so the tip has room without truncating the label.
        for btn in self._ribbon.findChildren(RibbonButton):
            if btn.property("ribbonKeyTip"):
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self._ribbon.file_button.setText(f"File  ({FILE_KEY_TIP})")
        QApplication.instance().installEventFilter(self._key_tip_filter)
        self._statusbar.showMessage(
            "Key tips on — press a letter, or Alt again to hide", 4000)

    def _restore_ribbon_button_styles(self) -> None:
        for btn in self._ribbon.findChildren(RibbonButton):
            btn.reapply_mode()
        self._ribbon.file_button.setText("File")

    def _key_press_event_filter(self, obj, event):
        """Catch Alt+<letter> while key tips are showing.

        Needed because a single `QShortcut` on Alt alone does not
        compose into a two-key sequence in Qt; the second key arrives as
        an ordinary key press. Without this the tips would be visible
        decoration and the promised keyboard route would not exist."""
        if not self._key_tips_active or obj is not self:
            return False
        from PySide6.QtCore import QEvent
        if event.type() != QEvent.Type.KeyPress:
            return False
        if event.key() in (Qt.Key.Key_Alt, Qt.Key.Key_Shift,
                           Qt.Key.Key_Control, Qt.Key.Key_Meta,
                           Qt.Key.Key_AltGr):
            return False      # a modifier on its own is not a choice
        text = event.text().upper()
        if not text or not text.isalpha():
            # Any other key dismisses the tips, as in every other app.
            self._dismiss_key_tips()
            return False
        if text == FILE_KEY_TIP:
            self._dismiss_key_tips()
            self._show_backstage()
            return True
        for btn in self._ribbon.findChildren(RibbonButton):
            label = btn.property("ribbonActionText") or ""
            if btn.property("ribbonKeyTip") == text:
                self._dismiss_key_tips()
                if btn.isEnabled():
                    btn.click()
                else:
                    self._statusbar.showMessage(
                        f"{label} is not available right now", 3000)
                return True
        # A tipped command that lives in the File view, not on a ribbon
        # page (Open, Save, New, Copy View): its letter still works from
        # any tab, as it did when they were on the File tab.
        for spec in self._commands.specs.values():
            if spec.key_tip == text:
                self._dismiss_key_tips()
                action = self._commands.action(spec.id)
                if action.isEnabled():
                    action.trigger()
                else:
                    self._statusbar.showMessage(
                        f"{action.text()} is not available right now", 3000)
                return True
        self._dismiss_key_tips()
        return False

    def _dismiss_key_tips(self) -> None:
        """Turn the tips off WITHOUT re-toggling, so a key press that
        consumed a tip cannot leave the labels showing."""
        if not self._key_tips_active:
            return
        self._key_tips_active = False
        QApplication.instance().removeEventFilter(self._key_tip_filter)
        for btn in self._ribbon.findChildren(RibbonButton):
            text = btn.property("ribbonActionText") or ""
            btn.setText(text)
        self._restore_ribbon_button_styles()
        self._statusbar.showMessage("Key tips off", 2000)

    def _apply_action_icons(self):
        """Repaint every ribbon icon in the active theme's colours.

        Every plain command's icon is declared in `command_table.py`
        (`icon="fn:token"`) and repainted by the registry; what is left
        here is the controls the builders in `ribbon_builders.py` make.
        No stock QStyle icon is used anywhere on the ribbon: those follow
        the desktop's icon theme, not ours, and can vanish in one of the
        themes."""
        icons.clear_cache()
        c = active_colour()
        self._commands.repaint_icons()
        self._pool_actions["SPARE_POOL_DATA"].setIcon(
            icons.spare_pool_icon(c["text"]))
        self._pool_actions["RESOURCE_DATA"].setIcon(
            icons.resource_icon(c["text"]))
        self._pool_actions["TRIGGERS"].setIcon(icons.trigger_icon(c["text"]))
        self._rates_action.setIcon(icons.rates_icon(c["text"]))
        self._step_multi_action.setIcon(icons.step_multi_icon(c["accent"]))
        self._step_change_action.setIcon(icons.step_change_icon(c["accent"]))
        self._step_menu_action.setIcon(icons.step_multi_icon(c["accent"]))
        self._speed_action.setIcon(icons.stopwatch_icon(c["text"]))
        for _col, _icon_fn in _ANALYSIS_ICONS.items():
            _act = self._analysis_actions.get(_col)
            if _act is not None:
                _act.setIcon(getattr(icons, _icon_fn)(c["accent"]))
        self._fev_log_action.setIcon(icons.log_icon(c["text"]))
        self._align_action.setIcon(icons.align_hcenter_icon(c["accent"]))
        for icon_fn, act in self._align_menu_actions:
            act.setIcon(getattr(icons, icon_fn)(c["accent"]))
        # The docks' own title-bar Float / Close buttons.
        for btn in self.findChildren(QToolButton):
            fn = btn.property("titleIcon")
            if fn:
                btn.setIcon(getattr(icons, fn)(c["text"]))

    def _setup_ribbon(self):
        """Build the ribbon from `command_table.py`.

        `COMMANDS` says what each plain command is and `LAYOUT` says where
        it goes; `CommandRegistry` builds the actions under the attribute
        names the rest of the window uses and owns their availability. The
        controls that are more than a button (menus, combos, spin boxes)
        are built by the methods in `ribbon_builders.py` that `LAYOUT`
        names. This function used to be ~1,400 lines of inline `QAction`
        setup."""
        self._ribbon = Ribbon()
        self._init_edit_and_run_state()
        self._commands = CommandRegistry(self, COMMANDS, factories={
            # Qt creates these itself: they follow the undo stack.
            "undo": lambda win, spec: win._undo_stack.createUndoAction(
                win, spec.text),
            "redo": lambda win, spec: win._undo_stack.createRedoAction(
                win, spec.text),
        })
        self._commands.create_all()
        self._undo_stack.cleanChanged.connect(self._on_undo_clean_changed)
        # Spread applies to the ACTIVE overlay, and only Weak and Cost
        # have quartile bands. Enable it from the checked state, not from
        # data availability; pressing it with Spares/Capacity showing must
        # never switch overlays.
        def sync_spread() -> None:
            self._weak_spread_action.setEnabled(
                self._weak_color_action.isChecked()
                or self._cost_overlay_action.isChecked())
        for overlay in (self._weak_color_action, self._cost_overlay_action,
                        self._capacity_overlay_action,
                        self._spares_overlay_action,
                        self._importance_overlay_action):
            overlay.toggled.connect(lambda _on: sync_spread())
        sync_spread()
        build_pages(self, self._commands, LAYOUT)
        # Analyses are edited only in the Run panel now. Keep checked
        # QActions as the internal mirror/state API (tests and sync paths
        # use them) without putting a second set of toggles on the ribbon.
        if not self._analysis_actions:
            self._build_hidden_analysis_actions()
        # The z-order commands follow the annotation selection as a set.
        self._anno_order_actions = {
            "front": self._anno_order_front_action,
            "forward": self._anno_order_forward_action,
            "backward": self._anno_order_backward_action,
            "back": self._anno_order_back_action,
        }
        self._ribbon.file_requested.connect(self._show_backstage)
        self._apply_autosave_timer()
        # Animate is application state, seeded from the same key the
        # Preferences dialog writes. Set with signals blocked: it used to
        # be set before the handler was connected, and applying the saved
        # value must not run the handler (it touches the canvas).
        self._live_sim_action.blockSignals(True)
        self._live_sim_action.setChecked(
            settings.get_bool("execution/default_animate", False))
        self._live_sim_action.blockSignals(False)
        # ...but the speed dial still follows it (off by default now, so a
        # launch with Animate off left the dial looking usable).
        self._set_speed_state_enabled(self._live_sim_action.isChecked())
        # Controls that exist but are not on the ribbon.
        self._init_find_box()
        self._init_report_stream_actions()
        # Save / Undo / Redo stay visible beside the tab strip on every
        # tab: the same QAction objects, so their state stays in sync.
        self._ribbon.set_quick_access_actions(
            [self._save_action, self._undo_action, self._redo_action])
        # Leaving the Simulate tab ends the "live" visual mode.
        self._ribbon.tab_bar.currentChanged.connect(
            self._on_ribbon_tab_changed)
        # Apply the starting state now (no model open), so a command that
        # is unavailable at launch already says why in its tooltip.
        self._refresh_commands()

    def _command_state(self, *, reuse_validity: bool = False) -> AppState:
        """What command availability depends on, right now.

        Every field is DERIVED from a live object, not remembered in a
        flag (the same rule as `_sync_run_settings_lock`): a background
        run is "the poll timer is running", a synchronous one is
        "`_run_in_progress` without it". A flag paired with each start and
        end is how a control ends up disabled forever after a run that
        ended oddly."""
        model_open = self._rbd is not None
        if not getattr(self, "_run_in_progress", False):
            run = "idle"
        elif getattr(self, "_bg_poll", None) is not None \
                and self._bg_poll.isActive():
            run = "background"
        else:
            run = "sync"
        dock = getattr(self, "_diagram_dock", None)
        view = dock.view if dock is not None else None
        selected = view.selected_elements() if view else ([], [], [], [])
        # A selection cannot change validity, so selection-driven refreshes
        # reuse the last applied answer instead of re-walking the model.
        # An edit since that answer was computed makes it a claim about a
        # model that no longer exists: report "not valid" for the short
        # debounce window rather than enabling Run on a stale yes -- the
        # validation pass that follows restores the real answer. Layout
        # edits never set this flag (they cannot change validity).
        reuse = (reuse_validity
                 and getattr(self, "_applied_command_state", None) is not None)
        if reuse:
            valid = (self._applied_command_state.valid
                     and not getattr(self, "_validity_dirty_since_check",
                                     False))
        else:
            valid = model_open and self._model_is_valid()
        return AppState(
            model_open=model_open,
            has_file=self._current_file is not None,
            valid=valid,
            run=run,
            selected_movable=view.selected_movable_count() if view else 0,
            selected_elements=view.selected_element_count() if view else 0,
            selected_structural=sum(len(ids) for ids in selected[:3]),
            hierarchies_here=view.hierarchy_count() if view else 0,
            selected_hierarchies=len(selected[3]),
            fev_active=getattr(self, "_fev", None) is not None,
            fev_phased=bool(getattr(getattr(self, "_fev", None), "phased", False)),
            step_next_run=bool(getattr(getattr(self, "_step_session", None), "has_next_run", False)),
            bg_parallel=(getattr(self, "_bg_state", None) or {}).get("workers", 1) > 1,
            stepping=getattr(self, "_step_session", None) is not None,
            has_results=bool(getattr(self, "_results", None)
                             and self._results.has_results()),
            run_page=bool(getattr(self, "_ribbon", None)
                          and getattr(self, "_simulate_page_index", -1) >= 0
                          and self._ribbon.tab_bar.currentIndex()
                          == self._simulate_page_index))

    def _bump_model_revision(self) -> None:
        """Declare that what the model VALIDATES to may have changed.

        Called from every edit (`_mark_dirty`) and from every load or
        close, which are the only things that can move a model between
        valid and invalid. Selection, scrolling and hovering cannot, and
        that is the whole point: see `_model_is_valid`.
        """
        self._model_revision = getattr(self, "_model_revision", 0) + 1

    def _model_is_valid(self) -> bool:
        """Whole-model validity, memoised per model revision.

        `_command_state` ran `_validation_issues` every time it was
        asked, and it is asked on every selection change -- twice per
        click, once from `block_selected` and once from
        `selection_changed`. That validation is not cheap: it hands the
        model to the engine (`api.validate` -> `load_model`) and runs the
        Raptor 7 input rules over every link, so clicking a block cost
        about 100 ms on a 44-block model and grows with the model, while
        a click can only change the SELECTION.

        Keyed on the revision that `_mark_dirty` and the load/close paths
        bump, so a genuine edit still re-validates before Run is
        re-enabled -- including an undo that fixes the model, since undo
        goes through the same `model_changed` -> `_mark_dirty` path.
        """
        rev = getattr(self, "_model_revision", 0)
        cached = getattr(self, "_validity_cache", None)
        if cached is not None and cached[0] == rev:
            return cached[1]
        # Mid-load, the window is being rebuilt around a model that has
        # not finished loading: answering "valid" would require the
        # expensive whole-model pass, and the answer is recomputed at the
        # end of the load anyway. Commands are greyed for those moments,
        # under the load indicator, rather than freezing the spinner to
        # ask a question the load is about to answer itself.
        if getattr(self, "_loading_model", False):
            return False
        valid = not self._validation_issues()
        self._validity_cache = (rev, valid)
        self._validity_dirty_since_check = False
        return valid

    def _carry_validity_across_run_setting_edit(self) -> None:
        """Keep the memoised validity across a run-setting edit.

        The run panel and ribbon write `SYSTEM_DATA` (trials, termination,
        start-statistics, the analysis flags). That marks the model dirty
        and, through the normal edit path, schedules a whole-model
        re-validation -- about two seconds on a 3,500-block file, with no
        change to the answer: a run setting cannot break a link or a block.
        The one rule set an edit here CAN newly break is the SYSTEM_DATA /
        experiment rules themselves, so re-check only those (cheap, no
        model walk) and, when they still pass, carry the previous "valid"
        forward and cancel the debounced pass.

        An invalid model is left alone: it may be invalid for a reason the
        cheap check cannot see, so the full pass still runs.
        """
        cached = getattr(self, "_validity_cache", None)
        if cached is None or not cached[1] or self._rbd is None:
            return
        # A structural edit since the last full pass means the cached
        # "valid" is not an answer about this model any more.
        if getattr(self, "_validity_dirty_since_check", False):
            return
        from raptor.core.input_rules import check_run_settings
        if any(v.severity == "error" for v in check_run_settings(self._rbd)):
            return
        self._validity_cache = (getattr(self, "_model_revision", 0), True)
        timer = getattr(self, "_validation_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()

    def _refresh_commands(self, state: AppState | None = None,
                          only: set[str] | None = None) -> None:
        """Apply the current state to every command the table manages.

        Safe to call at ANY time: run state is part of the state, so this
        no longer re-enables Run in the middle of a run. Call it wherever
        one of the inputs changes -- a model opening or closing, a run
        starting or ending, a file being saved, validation changing."""
        state = state or self._command_state()
        # Only a FULL refresh is "the state now applied". A partial one
        # (`only=...`, e.g. after a selection change) leaves every other
        # command as it was; recording its state as applied made the
        # validation timer see "nothing changed" and skip the refresh that
        # re-enables Run -- so undoing the edit that broke a model left
        # Run, Step and Failure Effects disabled for good.
        if only is None:
            self._applied_command_state = state
        self._commands.refresh(state, only=only)
        # The breakpoint combo is the Run panel's own widget (aliased
        # here in _setup_docks), so the table cannot hold it.
        combo = getattr(self, "_breakpoint_combo", None)
        if combo is not None:
            combo.setEnabled(PREDICATES["can_simulate"](state))
        backstage = getattr(self, "_backstage", None)
        if backstage is not None:
            backstage.sync_state()
        if hasattr(self, "_cores_spin"):
            self._sync_cores_box()             # locked while a run uses it
        spin = getattr(self, "_run_count_spin", None)
        if spin is not None:                   # like the Run panel: idle only
            spin.setEnabled(state.model_open and state.run == "idle"
                            and not state.stepping)
        badge = getattr(self, "_validation_badge", None)
        if badge is not None:                  # no model: no "Model OK"
            badge.setVisible(state.model_open)

    def _open_print_dialog(self) -> None:
        from raptor.gui.ui import printing
        printing.open_print_dialog(self)

    def _clear_results_clicked(self) -> None:
        self._discard_results()
        self._sync_results_notice()
        self._statusbar.showMessage("Results cleared: the model can be edited", 4000)

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
        """Show (or reuse) the shared in-app help viewer window."""
        from raptor.gui.ui.help_viewer import HelpViewer, _register_ops_scheme
        _register_ops_scheme()
        if getattr(self, "_help_viewer", None) is None:
            self._help_viewer = HelpViewer(
                parent=None, open_model=self.open_model_from_help,
                switch_to_app=self._switch_from_help)
            from raptor.gui.resources.design_tokens import ThemeManager
            ThemeManager.instance().on_changed(
                self._help_viewer.refresh_theme)
            self._help_viewer.refresh_theme(
                ThemeManager.instance().is_dark)
        self._help_viewer.show_doc(name, anchor)
        return self._help_viewer

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

    #: GitHub repo of this port (pyproject.toml's project.urls).
    GITHUB_REPO_URL = "https://github.com/openpowershift/RaptorStudio"

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

    #: focused dock/dialog title (or ribbon tab name) -> manual anchor
    _HELP_ANCHORS = {
        "Project Settings": "system-settings",
        "Results": "results",
        "Run": "run",
        "Data Tables": "data-tables",
        "Model Explorer": "data-tables",
        "Simulate": "stepping",
        "Inspector": "system-settings",
    }

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

    def _setup_dock_toggle(self, action, dock):
        """Checkable ribbon action <-> dock visibility, both directions.
        LEFT/RIGHT docks with a rotated stub (`self._dock_stubs`) don't
        close: unchecking collapses them to the rotated "Model"/
        "Inspector" word, and clicking the stub or re-checking restores
        them. Floated docks still close for real -- a stub in a floating
        window makes no sense."""
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
            recoverable = dock in (self._dock_stubs or {})
            action.setEnabled(open_ or not recoverable)
            action.blockSignals(False)

        if self._dock_state_sync is None:
            self._dock_state_sync = {}
        self._dock_state_sync[dock] = sync

        def toggle(checked):
            stub = (self._dock_stubs or {}).get(dock)
            if checked:
                if stub is not None:
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
                dock.setVisible(True)
                dock.raise_()
                sync(True)
            elif dock.isFloating():
                dock.close()
            elif stub is not None:
                stub(True)
                sync(False)
            else:
                dock.close()
        action.toggled.connect(toggle)
        dock.visibilityChanged.connect(sync)
        sync(dock.isVisible())

        self.setMenuWidget(self._ribbon)

    #: Tracks every dock titlebar's own theme refresh -- the minimize
    #: control lives in a custom titlebar styled from `active_colour()`
    #: rather than the global stylesheet's `QDockWidget::title` rule, so
    #: a theme toggle must repaint it explicitly. Appended to by
    #: `_make_dock_minimizable`, called from `_on_theme_changed`.
    _dock_titlebar_rethemers: list | None = None
    #: dock -> set_closed(bool) for the left/right docks that collapse
    #: to a rotated-label stub instead of disappearing when closed.
    #: Filled in by `_make_dock_minimizable`; read by
    #: `_setup_dock_toggle` and the docks' own close buttons.
    _dock_stubs: dict = None  # type: ignore[assignment]
    #: dock -> sync(bool), the ribbon-action state refresher registered
    #: by `_setup_dock_toggle`. `_make_dock_minimizable` calls it from
    #: `set_closed`/`set_minimized`, because those hide the dock's
    #: *content* and never emit `visibilityChanged` -- without this the
    #: Docks list and the actual panel state drift apart.
    _dock_state_sync: dict = None  # type: ignore[assignment]
    #: slot title -> {"get", "set_closed", "set_minimized"}, filled by `_make_dock_minimizable`; read by the layout
    #: save/restore and Reset Layout.
    _dock_chrome: dict = None  # type: ignore[assignment]

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
        squeeze_chrome = edge in ("left", "right")
        entries: list[dict[str, Any]] = []
        for dock in docks:
            content = dock.widget()

            titlebar = QWidget()
            tb = QHBoxLayout(titlebar)
            tb.setContentsMargins(8, 4, 4, 4)
            tb.setSpacing(2)
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
                # the stub instead of vanishing; bottom docks and any
                # floated window close for real.
                has_stub = edge in ("left", "right")
                close_btn.clicked.connect(
                    lambda _checked=False, d=dock, stubbed=has_stub: (
                        d.close()
                        if (d.isFloating() or not stubbed)
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

            normal_min = (dock.minimumWidth() if edge in ("left", "right")
                          else dock.minimumHeight())
            entries.append({"dock": dock, "content": content,
                            "titlebar": titlebar, "label": label,
                            "min_btn": min_btn, "float_btn": float_btn,
                            "close_btn": close_btn, "stub": stub,
                            "normal_min": normal_min, "minimized": False,
                            "closed": False})

        # The floating handler needs each dock's own chrome entry (its
        # title-bar buttons). The `_dock_chrome` map below is keyed by
        # TITLE for the bottom strip's state only.
        self._dock_chrome_entries = {e["dock"]: e for e in entries}

        def retheme():
            c = active_colour()
            for e in entries:
                collapsed = e["minimized"]
                bg = c["group_header"] if collapsed else c["surface"]
                e["titlebar"].setStyleSheet(
                    f"background: {bg}; border-bottom: 1px solid "
                    f"{c['border']};")
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

        def set_minimized(minimized: bool):
            for e in entries:
                dock, content = e["dock"], e["content"]
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
                if squeeze_chrome:
                    e["label"].setVisible(not minimized)
                    if e["float_btn"] is not None:
                        e["float_btn"].setVisible(not minimized)
                    if e["close_btn"] is not None:
                        e["close_btn"].setVisible(not minimized)
                if edge in ("left", "right"):
                    if minimized:
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
                        self._set_dock_min_width(dock, e["normal_min"])
                        dock.setMaximumWidth(16777215)
                        dock.setMinimumHeight(max(dock.minimumHeight(), 1))
                else:
                    if minimized:
                        # Remember the pre-collapse height so restore
                        # comes back to the same size rather than
                        # leaving it to the layout.
                        e["saved_height"] = dock.height()
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
                        self._set_dock_min_width(dock, 0)
                        dock.setMaximumWidth(16777215)
                        dock.setMinimumHeight(e["normal_min"])
                        cap = (16777215 if dock.isFloating() else
                               (restore_max() if callable(restore_max)
                                else restore_max))
                        dock.setMaximumHeight(cap or 16777215)
                        saved = e.get("saved_height")
                        if saved:
                            # Apply the remembered height first (fixed),
                            # then release the fix on the next event-
                            # loop pass so the splitter stays draggable.
                            # Guarded: the dock may already be torn down
                            # when the timer fires (window close).
                            dock.setFixedHeight(max(saved, e["normal_min"]))
                            def _release(d=dock, cap_=cap or 16777215,
                                         nm=e["normal_min"]):
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

        for e in entries:
            e["min_btn"].clicked.connect(
                lambda _checked=False: set_minimized(not entries[0]["minimized"]))

        def set_closed(closed: bool):
            """Swap the dock between its normal chrome and the rotated
            closed-stub: a closed Model/Inspector shows its name rotated
            to face the user, and stays restorable by clicking."""
            for e in entries:
                dock, stub = e["dock"], e["stub"]
                if stub is None:
                    continue
                e["closed"] = closed
                if closed:
                    e["minimized"] = False
                    e["content"].setVisible(False)
                    dock.setTitleBarWidget(stub)
                    dock.setFixedHeight(max(dock.height(), 1))
                    dock.setFixedWidth(stub.width() or 24)
                else:
                    dock.setTitleBarWidget(e["titlebar"])
                    self._set_dock_min_width(dock, e["normal_min"])
                    dock.setMaximumWidth(16777215)
                    # release the stub's fixed height
                    dock.setMinimumHeight(0)
                    dock.setMaximumHeight(16777215)
                    e["content"].setVisible(True)
            sync_action(e["dock"])
            retheme()

        for e in entries:
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

    def _setup_central(self):
        self._diagram_dock = DiagramDock()
        self._diagram_dock.view.status_message_sink = \
            lambda msg, mw=self: mw.statusBar().showMessage(msg, 6000)
        self._diagram_dock.view.set_undo_stack(self._undo_stack)
        self._refresh_dev_hooks()
        self._diagram_dock.view.block_selected.connect(self._on_canvas_block_selected)
        self._diagram_dock.view.selection_cleared.connect(
            self._on_canvas_selection_cleared)
        self._diagram_dock.view.node_selected.connect(self._on_canvas_node_selected)
        self._diagram_dock.view.hier_selected.connect(self._on_hier_selected)
        self._diagram_dock.view.annotation_selected.connect(
            self._on_annotation_selected)
        self._diagram_dock.view.level_changed.connect(self._on_level_changed)
        self._diagram_dock.view.level_changed.connect(
            lambda _pid: self._explorer.refresh())
        # Data Tables are per-hierarchy too: refresh them on a level
        # change so they don't keep showing the parent level's rows.
        self._diagram_dock.view.level_changed.connect(
            lambda _pid: self._data_tables.refresh())
        self._diagram_dock.view.model_changed.connect(self._mark_dirty)
        # Position/size/z-order changes are real edits (the file is dirty
        # and Save must write them) but they cannot change what the engine
        # simulates: no whole-model validation, no revision bump, and no
        # Inspector/Explorer/Data Tables rebuild. On a 3,470-block model
        # the old `model_changed` path cost seconds per drag -- including
        # on the selection click that followed it.
        self._diagram_dock.view.layout_changed.connect(
            self._mark_layout_dirty)
        # Any model mutation (including undo/redo) refreshes the
        # Inspector, Data Tables and Explorer from the current model.
        # Deferred/coalesced -- see `_schedule_view_refresh`.
        self._diagram_dock.view.model_changed.connect(
            self._schedule_view_refresh)
        self._diagram_dock.view.fev_target_clicked.connect(
            self._on_fev_target_clicked)
        self._diagram_dock.view.fev_menu_requested.connect(self._on_fev_menu)
        self._diagram_dock.view.element_created.connect(
            lambda kind, _oid: self._refresh_goto_combo()
            if kind == "hier" else None)
        self._diagram_dock.view.annotations_may_be_invisible.connect(
            self._on_annotations_may_be_invisible)
        self._diagram_dock.view.properties_requested.connect(
            self._show_inspector_dock)
        # Any selection change can enable/disable the z-order buttons and
        # Align & Distribute, neither of which depends on WHICH element
        # is selected.
        self._diagram_dock.view.selection_changed.connect(
            self._sync_annotation_order_actions)
        # Selection-dependent commands (Delete/Collapse/Align/Mass Edit/
        # Zoom to Hierarchy/Extract) must change state on the click that
        # changes the selection, not on the next validation timer tick.
        self._diagram_dock.view.selection_changed.connect(
            lambda: self._refresh_commands(
                self._command_state(reuse_validity=True),
                only={"has_selection", "has_structural_selection",
                      "has_movable_selection", "can_align", "can_mass_edit",
                      "can_zoom_hierarchy", "can_extract_hierarchy"}))
        # The view disarms its own tools (Esc, and the end of a
        # one-shot annotation placement). Mirror that back onto the
        # ribbon so a button does not stay lit while the tool it arms
        # is already spent.
        self._diagram_dock.view.tool_mode_changed.connect(
            self._on_tool_mode_changed)
        # Saved "style for new <Kind>s" defaults. The view applies them
        # to every shape it places; MainWindow owns the QSettings.
        self._diagram_dock.view.annotation_style_default_changed.connect(
            self._on_annotation_style_default)
        self._diagram_dock.view.default_annotation_props = (
            self._annotation_style_defaults())
        self.setCentralWidget(self._diagram_dock)

    def _setup_docks(self):
        self._explorer = ExplorerDock()
        self._explorer.hier_activated.connect(
            lambda hid: self._diagram_dock.view.enter_level(hid))
        self._explorer.ascend_requested.connect(
            self._diagram_dock.view.go_up)
        # Re-parents the ribbon-built Find combo into this dock's own
        # layout (see _setup_ribbon's own comment on it).
        self._explorer.set_search_widget(self._find_edit)
        dock_left = QDockWidget("Model Explorer", self)
        dock_left.setWidget(self._explorer)
        # `setMinimumWidth` is not a standalone setter in Qt: it is routed
        # through `setMinimumSize(w, currentMinimumHeight())`, and a
        # freshly-built dock still holds Qt's unset `-1` height
        # sentinel. That produced the console noise
        # `QWidget::setMinimumSize: (/QDockWidget) Negative sizes
        # (260,-1) are not possible`. Pair it with a 0 height instead.
        dock_left.setMinimumSize(260, 0)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock_left)
        self._explorer_dock = dock_left

        self._inspector = InspectorDock()
        self._inspector.set_undo_stack(self._undo_stack)
        # Animation is application state, so the Inspector cannot read it
        # off the model; hand it the live value instead of letting it show
        # the legacy `graphicsUse` column, which nothing writes.
        self._inspector.set_animate_state_provider(
            lambda: self._live_sim_action.isChecked())
        dock_right = QDockWidget("Inspector", self)
        dock_right.setWidget(self._inspector)
        # Wider than the default so all Inspector details show by
        # default -- 300px was not enough for the 7-tab strip without
        # its scroll arrows.
        dock_right.setMinimumSize(340, 0)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock_right)
        self._inspector_dock = dock_right

        # Live Log dock: while stepping / live-running, the right dock
        # shows the streaming event log (like the Results Event Log)
        # instead of the Inspector; the Inspector returns after.
        from PySide6.QtWidgets import QTreeWidget as _QTree
        live_page = QWidget()
        lv = QVBoxLayout(live_page)
        lv.setContentsMargins(0, 0, 0, 0)
        self._live_log_tree = _QTree()
        self._live_log_tree.setHeaderLabels(
            ["Time", "Type", "Element", "State", "Detail"])
        self._live_log_tree.setRootIsDecorated(False)
        self._live_log_tree.setUniformRowHeights(True)
        self._live_log_tree.setAlternatingRowColors(True)
        # Same affordances as the Results dock's Event Log: the State
        # column coloured green/amber/red, and a click on a Block/Node row
        # locating that element on the canvas. The live log was a plain
        # grey tree, so watching a run meant reading it column by column
        # and then hunting for the element by eye.
        self._live_log_tree.itemSelectionChanged.connect(
            self._on_live_log_row_clicked)
        # Caption above the log.
        self._live_log_caption = QLabel(
            "Live event log — stepping / animated run")
        self._live_log_caption.setObjectName("SectionNote")
        self._live_log_caption.setWordWrap(True)
        lv.addWidget(self._live_log_caption)
        lv.addWidget(self._live_log_tree)
        self._live_log_dock = QDockWidget("Live Log", self)
        self._live_log_dock.setWidget(live_page)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._live_log_dock)
        self._live_log_dock.hide()

        self._results = ResultsDock()
        dock_bottom = QDockWidget("Results", self)
        dock_bottom.setWidget(self._results)
        # Low enough that the empty state (a one-line "No results yet"
        # placeholder, or Data Tables before a model is loaded) doesn't
        # force a tall, mostly-blank strip under the canvas --
        # `_give_canvas_room`'s startup height and `_expand_results`'s
        # post-run height both grow it back up for real content; this is
        # only the floor.
        dock_bottom.setMinimumHeight(120)
        # Closable so a floated Results window gets the X close button --
        # with the default feature set there was no way to dismiss it.
        dock_bottom.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self._results_dock = dock_bottom
        dock_bottom.topLevelChanged.connect(self._sync_results_float_action)
        self._floated_results_closer = _FloatedResultsCloser(self)
        dock_bottom.installEventFilter(self._floated_results_closer)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock_bottom)

        self._data_tables = DataTablesDock()
        self._data_tables.model_edited.connect(self._mark_dirty)
        self._data_tables.export_tables_requested.connect(self._export_tables)
        self._data_tables.element_locate_requested.connect(
            self._on_result_element_clicked)
        self._data_tables.validation_failed.connect(
            lambda msg: self._statusbar.showMessage(f"Invalid value — {msg}", 8000))
        dock_tables = QDockWidget("Data Tables", self)
        dock_tables.setWidget(self._data_tables)
        self._data_tables_dock = dock_tables
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock_tables)
        # Data Tables and Results share one bottom slot as tabs, so a
        # single tabbed strip keeps one sane height the splitter
        # controls.
        self.tabifyDockWidget(dock_tables, dock_bottom)
        dock_bottom.raise_()
        dock_tables.setMinimumHeight(120)

        # Run panel: the third tab in the same bottom strip. Deliberately
        # NOT a new right-side panel -- at 1280px, Explorer (260) +
        # Inspector (340) + a third 320px panel leaves a 360px canvas
        # against the spec's own 700px minimum
        # (the internal UI blueprint). Tabifying costs no new
        # width and reuses the existing splitter.
        from raptor.gui.ui.run_panel import RunPanelDock
        self._run_dock = RunPanelDock(self)
        self._run_panel = self._run_dock.panel
        # The stepping debugger's parameters moved to the panel; point the
        # attributes the existing code (and tests) read at the panel's
        # real widgets, so there is ONE of each rather than a ribbon
        # spinner and a panel spinner that could disagree.
        self._step_count_spin = self._run_panel._step_count
        self._run_to_time_spin = self._run_panel._run_to
        self._breakpoint_combo = self._run_panel._breakpoint
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._run_dock)
        self.tabifyDockWidget(dock_tables, self._run_dock)
        # Results, Data Tables and Run share one physical strip, and the
        # strip can only grow to the smallest maximumHeight() among the
        # three. Tie that ceiling to whichever tab is on show: Run hugs
        # its own content, Results/Data Tables may use the window.
        self._bottom_active_dock = dock_bottom
        for _tab in (dock_bottom, dock_tables, self._run_dock):
            _tab.visibilityChanged.connect(
                lambda visible, d=_tab:
                self._on_bottom_dock_visibility_changed(d, visible))
        # Adding Run last made it the active tab on every startup, even
        # on the Model tab. Results is the default front tab; Simulate
        # raises Run explicitly.
        dock_bottom.raise_()
        self._setup_dock_toggle(self._run_toggle_action, self._run_dock)
        self._run_panel.experiment_changed.connect(self._on_panel_experiment)
        self._run_panel.dependency_blocked.connect(self._on_panel_dependency)
        self._run_panel.cores_changed.connect(self._on_cores_changed)
        self._sync_cores_box()
        self._run_dock.setMinimumHeight(120)
        self._sync_run_panel_units()
        # There is no model at construction; leaving the editor enabled
        # let a user change Trials and lose it on the first Open.
        if self._rbd is None:
            self._run_panel.set_enabled(False)
        # No maximum-height cap: `_show_results_full` and the minimizer
        # reference `_results_dock_max`, so it holds Qt's own "no cap"
        # value.
        self._results_dock_max = 16777215
        #: Last user-owned geometry of the floated Results window. A
        #: re-float restores it instead of hard-coding 1080x760.
        self._results_float_geom = None
        self._results_dock_height_before_float = None

        # Ribbon View > Docks toggles, kept in sync with the docks' own X
        # buttons in both directions.
        self._setup_dock_toggle(self._explorer_toggle_action, dock_left)
        self._setup_dock_toggle(self._inspector_toggle_action, dock_right)
        self._setup_dock_toggle(self._results_toggle_action, dock_bottom)
        self._setup_dock_toggle(self._data_tables_action, dock_tables)
        self._sync_dock_tab_bars()
        # Floating/re-docking can create a fresh QTabBar whose default
        # elide mode is ElideRight, so tabs show "Data Tab...". Re-apply
        # the no-elide rule whenever the dock placement changes.
        for d in (dock_left, dock_right, self._live_log_dock,
                  dock_bottom, dock_tables, self._run_dock):
            d.dockLocationChanged.connect(
                lambda _area: QTimer.singleShot(0, self._sync_dock_tab_bars))
            # Floating/re-docking can create a fresh QTabBar whose default
            # elide mode is ElideRight, so tabs show "Data Tab...". Re-apply
            # the no-elide rule whenever the dock placement changes. The
            # same signal releases the constraints a DOCKED panel needs
            # (reported: "when it's detached I should be able to expand it
            # and resize it too").
            d.topLevelChanged.connect(
                lambda floating, dock=d:
                (QTimer.singleShot(0, self._sync_dock_tab_bars),
                 self._on_dock_top_level_changed(dock, floating)))
        # Setup snapshots visibility before the window is shown, so
        # tabified panels looked unchecked although present. Re-sync once
        # the layout is live (also after reset).
        QTimer.singleShot(0, self._sync_dock_toggles)

        # `saveState()` keys each dock by object name; without one Qt drops it from the saved layout.
        for dock, name in ((dock_left, "ExplorerDock"), (dock_right, "InspectorDock"),
                           (self._live_log_dock, "LiveLogDock"), (dock_bottom, "ResultsDock"),
                           (dock_tables, "DataTablesDock"), (self._run_dock, "RunPanelDock")):
            dock.setObjectName(name)

        # Minimize-to-stub: Data Tables + Results collapse together as
        # one bottom stub since they're tabified into one physical slot
        # -- minimizing either one collapses both.
        self._make_dock_minimizable([dock_left], "left")
        self._make_dock_minimizable([dock_right], "right")
        self._make_dock_minimizable(
            [dock_bottom, dock_tables, self._run_dock], "bottom",
            restore_max=lambda: self._max_bottom_dock_height())
        # Snapshot the first-run arrangement. Reset Layout restores this
        # instead of re-tabifying repeatedly (which created orphan tab
        # bars and could leave Data Tables in front).
        self._default_dock_state = self.saveState()

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

    def _on_hier_selected(self, hier_id: int):
        self._queue_inspector_selection("hier", hier_id)

    def _on_annotation_selected(self, anno_id: int):
        self._queue_inspector_selection("annotation", anno_id)

    def _on_level_changed(self, parent_id: int):
        # A queued Inspector rebuild belongs to the level we just left;
        # letting it fire would show an element the user can no longer see.
        self._cancel_pending_block_selection()
        inside = parent_id != 0
        name = self._diagram_dock.view.hiers.get(parent_id)
        self._hier_up_action.setEnabled(inside)
        self._hier_home_action.setEnabled(inside)
        # A level switch rebuilds the annotation items, so a selection
        # held in the old level's notes is gone: re-gate the z-order
        # buttons and Align against whatever is selected now.
        # The Inspector must follow: a block/hierarchy from the old
        # level is not editable in the new level's scoped dicts.
        self._inspector.clear_selection()
        self._sync_annotation_order_actions()
        # Narrow Sparing/Capacity result rows to the drilled-into subtree
        # (Raptor 7's per-hierarchy scoping).
        self._results.set_level_filter(self._level_allowed_names(parent_id))
        if hasattr(self._results, "set_hierarchy_options"):
            v3 = self._diagram_dock.view
            self._results.set_hierarchy_options(
                {h: hh.name for h, hh in v3.hiers.items()}, parent_id)
        self._refresh_goto_combo()
        if inside and name is not None:
            self._statusbar.showMessage(
                f"Viewing hierarchy level: {name.name} (double-click an "
                f"Out marker or use Up/Home to return)", 6000)

    def _push_domains(self) -> None:
        """Compute Weak Link Raptor 7 domains from the loaded model and
        hand them to the results dock (structural — safe pre-run).

        Computed on demand (the Domain Diagram dialog), not on every
        load: it rebuilds the engine model, which cost about a second on
        a 3,500-block file for a dialog most sessions never open. Cached
        per model revision and shared by every open of the dialog.
        """
        rev = getattr(self, "_model_revision", 0)
        if getattr(self, "_domains_revision", None) == rev:
            return
        try:
            from raptor.core.engine.graph import upstream_domains
            v = self._diagram_dock.view
            rbd = getattr(v, "_rbd_ref", None)
            if rbd is None:
                return
            m = load_model(rbd)
            self._results.set_domains(upstream_domains(
                m.nodes_by_id, m.blocks_by_id, m.events_by_id,
                m.links_by_id, m.hiers_by_id))
            self._domains_revision = rev
        except Exception:
            pass

    def _level_allowed_names(self, parent_id: int) -> set[str] | None:
        """Element names inside `parent_id`'s subtree (the hierarchy plus
        everything nested under it); None = home level = no filtering.

        Scans the raw file rows across the whole model rather than the
        view's `blocks`/`nodes`: those are level-scoped (only the level
        currently on screen), so they would miss elements on other
        levels and in nested sub-hierarchies. This is the same source
        `DiagramView.locate_element`/`all_element_names` use.
        """
        if parent_id == 0:
            return None
        v = self._diagram_dock.view
        ids = {parent_id}
        changed = True
        while changed:
            changed = False
            for hid, h in v.hiers.items():
                if h.parent_id in ids and hid not in ids:
                    ids.add(hid)
                    changed = True
        rbd = getattr(v, "_rbd_ref", None)
        names: set[str] = set()
        if rbd is None:
            return names
        from raptor.core.fileio.typed import block_from_row, node_from_row
        for section, from_row, name_of in (
                ("BLOCK_DATA", block_from_row, lambda o: o.name),
                ("NODE_DATA", node_from_row, lambda o: o.node_name)):
            sec = rbd.sections.get(section)
            if sec is None:
                continue
            for row in sec.rows:
                try:
                    obj = from_row(row)
                except (ValueError, KeyError):
                    continue
                if obj.parent_id in ids:
                    names.add(name_of(obj))
        return names

    def _on_weak_type_changed(self, new_type: str):
        """Raptor 7 Analysis Type switch from the Weak Link tab: persist to
        the model's nodeAnalType and re-colour the canvas on the new
        basis (Availability vs Dependability).

        `nodeAnalType` is a presentation/analysis setting, not structure:
        it goes through the ungated stack and keeps the memoised validity,
        exactly like a run-setting edit (the rule gate's two whole-model
        passes and the debounced validation made this feel like a freeze).
        """
        sec = self._rbd.sections.get("SYSTEM_DATA") if self._rbd else None
        if sec and sec.rows:
            row = sec.rows[0]
            old = row.get("nodeAnalType", "Availability")
            if old != new_type:
                from raptor.gui.ui.diagram import RowCellCmd
                v = self._diagram_dock.view
                self._in_run_setting_edit = True
                try:
                    with v._stack.ungated():
                        v._stack.beginMacro("Weak Link analysis type")
                        v._stack.push(RowCellCmd(v, "SYSTEM_DATA", 0,
                                                 "nodeAnalType", old, new_type))
                        v._stack.endMacro()
                    self._mark_dirty()
                finally:
                    self._in_run_setting_edit = False
                self._carry_validity_across_run_setting_edit()
        # recolour with the new basis if colouring is currently on
        if getattr(self._diagram_dock.view, "_weak_colors_on", False):
            self._toggle_weak_colors()   # off
            self._toggle_weak_colors()   # back on, new basis
        # Overview card (Availability/Dependability) must switch basis
        # too. `_push_weak_settings` re-reads the model's own
        # (just-edited) nodeAnalType; refresh_overview_metric repaints
        # the card from it against the already-held report data.
        self._push_weak_settings()
        if self._inspector.current_target() == ("system", None):
            self._inspector.show_system_config()
        self._results.refresh_overview_metric()

    def _apply_threshold_edits(self, edits, macro_label: str) -> bool:
        """Write one or two band thresholds as ONE undo step.

        The Spread command and the ribbon's paired GY/YR editor each used
        to call the one-column writer twice, which opened two macros: one
        Ctrl+Z left the bands half-changed. Return False when nothing
        actually changed, so callers do not create an empty macro.
        """
        sec = self._rbd.sections.get("SYSTEM_DATA") if self._rbd else None
        if not sec or not sec.rows:
            return False
        v = self._diagram_dock.view
        row = sec.rows[0]
        pending = []
        for column, value in edits:
            new = f"{value:.6f}"
            old = row.get(column, "")
            if old != new:
                pending.append((column, old, new, value))
        if not pending:
            return False
        self._in_run_setting_edit = True
        try:
            with v._stack.ungated():
                v._stack.beginMacro(macro_label)
                try:
                    for column, old, new, _value in pending:
                        v._stack.push(
                            RowCellCmd(v, "SYSTEM_DATA", 0, column, old, new))
                finally:
                    v._stack.endMacro()
            self._mark_dirty()
        finally:
            self._in_run_setting_edit = False
        self._carry_validity_across_run_setting_edit()
        # Keep both ribbon spins reflecting the row (one may have fired
        # this edit; the other is the paired bound).
        for column, _old, _new, value in pending:
            sp = self._gy_spin if column == "GYthreshold" else self._yr_spin
            if abs(sp.value() - value) > 1e-9:
                sp.blockSignals(True)
                sp.setValue(value)
                sp.blockSignals(False)
        # live re-band: re-run apply with same metric, new thresholds
        if getattr(v, "_weak_colors_on", False):
            self._toggle_weak_colors()
            self._toggle_weak_colors()
        self._push_weak_settings()
        if self._results.has_results():
            # The hierarchy dots use the same bands.
            self._apply_hier_status(self._results._report_data)
        if self._inspector.current_target() == ("system", None):
            # Keep the Inspector's read-only band echoes current.
            self._inspector.show_system_config()
        if len(pending) == 1:
            column, _old, _new, value = pending[0]
            label = ("Green/Yellow" if column == "GYthreshold"
                     else "Yellow/Red")
            self._statusbar.showMessage(
                f"{label} threshold set to {value:.2f} — bands updated",
                6000)
        else:
            self._statusbar.showMessage(
                "Green/Yellow and Yellow/Red bands updated", 6000)
        return True

    def _on_system_threshold_changed(self, column: str, value: float):
        """Raptor 7 Colour Thresholds (GY/YR) edited in the Inspector's
        Analysis group: persist + refresh the dock's band settings and
        recolour if weak colouring is on."""
        self._apply_threshold_edits([(column, value)], f"Set {column}")

    def _on_bands_edited(self, gy: float, yr: float):
        """The ribbon's paired GY/YR editor: one undo step for both."""
        self._apply_threshold_edits(
            [("GYthreshold", gy), ("YRthreshold", yr)],
            "Set Weak Link bands")

    def _on_system_config_changed(self, updates: dict) -> None:
        """Apply Inspector Project Settings edits through the undoable
        row writer, then refresh every dependent readout."""
        if not updates or self._rbd is None:
            return
        sec = self._rbd.sections.get("SYSTEM_DATA")
        if not sec or not sec.rows:
            return
        row = sec.rows[0]
        v = self._diagram_dock.view
        apply_row_edits(v, "SYSTEM_DATA", 0, row, list(updates.items()),
                        "System settings")
        # The edit came from the Inspector page itself: do not rebuild it
        # (that would reset scroll and focus on every stream change).
        self._sync_settings_from_model(refresh_inspector=False)
        units = self._current_units()
        if hasattr(self, "_units_chip"):
            self._units_chip.setText(f" units: {units} " if units else "")
        # Keep every unit-aware surface in step with the model: the
        # Inspector's field labels and the Data Tables' time columns.
        self._inspector.set_rbd(self._rbd)
        self._data_tables.set_units(units)
        self._statusbar.showMessage("System settings updated", 4000)

    def _on_system_comment_changed(self, text: str) -> None:
        """Write the system comment through the undoable row writer."""
        if self._rbd is None:
            return
        sec = self._rbd.sections.get("COMMENTS")
        if sec is None:
            return
        for i, row in enumerate(sec.rows):
            if row.get("Type", "") == "System":
                if row.get("Comment", "") != text:
                    apply_row_edits(self._diagram_dock.view, "COMMENTS",
                                    i, row, [("Comment", text)],
                                    "System comment")
                    self._mark_dirty()
                return
        # No system comment yet: add one through the undoable row command.
        self._diagram_dock.view._stack.push(TableRowCmd(
            self._diagram_dock.view, "COMMENTS",
            {"Type": "System", "ID": "", "Name": "", "Comment": text},
            len(sec.rows), True))
        self._mark_dirty()

    def _on_result_element_clicked(self, kind: str, name: str):
        if kind not in ("block", "node"):
            return
        v = self._diagram_dock.view
        if not v.locate_element(kind, name):
            self._statusbar.showMessage(
                f"Couldn't find {kind} '{name}' in the model", 6000)

    def keyPressEvent(self, event):
        # Esc is handled by the `_esc_shortcut` above (WindowShortcut --
        # fires no matter which child has focus). This override handles
        # the no-run case when the main window itself has focus (the
        # shortcut re-dispatches here).
        from PySide6.QtCore import Qt as _Qt
        # A lone Alt (pressed and released with nothing between) shows the
        # key tips. Qt never matches a shortcut made of a modifier alone,
        # so the QShortcut on Alt this replaced could not fire from a real
        # keyboard. A child that does not use Alt passes it up to here.
        import time
        self._alt_alone = event.key() == _Qt.Key.Key_Alt
        self._alt_down_at = time.monotonic()
        if event.key() == _Qt.Key.Key_Escape:
            if self._handle_escape():
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        import time
        from PySide6.QtCore import Qt as _Qt
        # Quick, too: a key a child handled while Alt was held (Alt+arrow
        # on the canvas) never reaches here to clear the flag.
        quick = time.monotonic() - getattr(self, "_alt_down_at", 0.0) < 1.0
        if event.key() == _Qt.Key.Key_Alt and quick and \
                getattr(self, "_alt_alone", False):
            self._alt_alone = False
            self._show_key_tips()
            return
        super().keyReleaseEvent(event)

    def _handle_escape(self) -> bool:
        """Esc routing: first press pauses, second stops. Returns True
        when consumed. During a run: background runs pause on the first
        Esc and stop on the second (only the background path has a pause
        concept -- a synchronous run's `_sync_stop` is a one-way abort,
        so Esc stops directly); stepped sessions stop too. With no run
        active: False -- the focused widget's own Esc semantics (canvas
        tool-cancel, annotation-edit exit) take over via the re-dispatch;
        a stepped session is left alone (Terminate ends it)."""
        if getattr(self, "_run_action", None) is not None and \
                not self._run_action.isEnabled():
            bg_state = getattr(self, "_bg_state", None)
            bg_running = bool(bg_state and not bg_state.get("done"))
            if bg_running:
                resume_ev = req(bg_state).get("resume_ev")
                already_paused = (resume_ev is not None
                                  and not resume_ev.is_set())
                if already_paused:
                    self._stop_bg_run()
                    self._statusbar.showMessage(
                        "Escape — stop requested")
                else:
                    self._toggle_bg_pause()
                    self._statusbar.showMessage(
                        "Escape — paused (Esc again to stop, or "
                        "Pause/Resume to continue)")
            else:
                self._stop_bg_run()
                self._statusbar.showMessage("Escape — stop requested")
            return True
        # A stepped session is NOT ended by Esc: it is also the key that
        # cancels an armed tool, Fail or Repair, so one press threw away a
        # long stepped position without asking. Terminate ends a session.
        return False

    def _handle_escape_activated(self) -> None:
        """Shortcut entry point: consume Esc only when a run/step is
        active; otherwise re-send the key to the focused widget so the
        canvas's own Esc semantics (cancel armed tool, exit annotation
        editing, cancel connect/arrow/shape ghosts) keep working."""
        # The File view first. It used to bind its own Esc as well, and two
        # window shortcuts on one key are ambiguous: Qt fired neither, so
        # Esc never closed it.
        bs = getattr(self, "_backstage", None)
        if bs is not None and bs.isVisible():
            bs.close_view()
            return
        if self._esc_redispatching:
            # Already re-dispatching once. The synthetic Escape we sent
            # has walked back up the parent chain and reached this
            # window shortcut again -- letting it through would hand
            # the same key to `sendEvent` forever, which is the
            # `RecursionError: Error calling Python override of
            # QGraphicsView::eventFilter(): maximum recursion depth
            # exceeded` the console was reporting on every Esc press
            # with no run active.
            return
        if self._handle_escape():
            return
        focus = QApplication.focusWidget()
        # An armed canvas tool is cancelled directly, whatever has focus.
        # Forwarding the key press (below) never reached the canvas: Qt
        # matches this same window shortcut on the forwarded event first.
        v = self._diagram_dock.view
        if v.has_armed():
            v.cancel_armed()
            return
        if focus is None:
            return
        from PySide6.QtCore import QEvent, Qt as _Qt, QCoreApplication
        from PySide6.QtGui import QKeyEvent
        self._esc_redispatching = True
        try:
            QCoreApplication.sendEvent(focus, QKeyEvent(
                QEvent.Type.KeyPress, _Qt.Key.Key_Escape, _Qt.KeyboardModifier.NoModifier))
        finally:
            self._esc_redispatching = False

    def _setup_statusbar(self):
        self._statusbar = QStatusBar()
        self._statusbar.showMessage("Ready — Open an .rbd or .rbds file to begin (Ctrl+O)")
        self.setStatusBar(self._statusbar)
        # Never let a resizeDocks request shrink the status bar away
        # entirely: the dock areas can be asked for more than fits, and
        # the status bar, last in the layout, is what gets squeezed out.
        # The main fix is reserving chrome space in
        # `_vertical_chrome_reserve`; this floor is the backstop if any
        # other path overcommits.
        self._statusbar.setMinimumHeight(
            self._statusbar.sizeHint().height())
        # Run progress: a thin Material-style bar tracking
        # completed/total runs, alongside the "run N of M (X%)" text --
        # hidden at idle, shown for the run's duration.
        from PySide6.QtWidgets import QProgressBar, QLabel
        self._run_progress = QProgressBar()
        self._run_progress.setFixedHeight(6)
        self._run_progress.setFixedWidth(120)
        self._run_progress.setTextVisible(False)
        self._run_progress.setRange(0, 100)
        c = active_colour()
        self._run_progress.setStyleSheet(
            "QProgressBar { border: none; border-radius: 3px; "
            f"background: {c['border']}; }}"
            "QProgressBar::chunk { border-radius: 3px; "
            f"background: {c['accent']}; }}")
        self._run_progress.setVisible(False)
        self._statusbar.addPermanentWidget(self._run_progress)
        # % complete, right by the progress line. The bar itself is only
        # 6px tall (too thin for legible overlaid text), hence a separate
        # label instead of QProgressBar's own setTextVisible/setFormat.
        self._run_progress_label = QLabel("")
        # SCOPED QLabel rule: unscoped `color:` on a status-bar label
        # leaks into its tooltip window (see the summary labels below) --
        # scope everything on the navy bar.
        self._run_progress_label.setStyleSheet(
            f"QLabel {{ color: {c['accent']}; padding: 0 4px; "
            f"font-weight: 600; }}")
        # Fixed cell so status items don't shift as the text changes --
        # "100%" is the widest rendering
        self._run_progress_label.setFixedWidth(
            self._run_progress_label.fontMetrics()
            .horizontalAdvance("100%") + 12)
        self._run_progress_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._run_progress_label.setVisible(False)
        self._statusbar.addPermanentWidget(self._run_progress_label)
        # The "approximately N left" estimate rides in the run status
        # message itself (see `_run_eta_text`): that is the bar's wide,
        # flexible area, and a permanent widget carrying the full sentence
        # would crowd out the run position on a smaller window.
        # live Ao/MTBDE/MDT/Red% readouts exist from startup so they're
        # visible (and updating) from the first completed run
        self._ensure_summary_labels()
        # badge exists from startup so timers/clicks can never race it
        self._set_validation_badge(True, [])
        # Which result overlay is on the canvas, permanently visible so a
        # coloured diagram is not mistaken for the model's own state when
        # the ribbon is on another tab.
        self._overlay_chip = QLabel("")
        self._overlay_chip.setObjectName("overlay_chip")
        self._overlay_chip.setStyleSheet(
            "QLabel { color: #EAF2FB; padding: 0 6px; font-weight: 600; }")
        self._overlay_chip.setVisible(False)
        self._statusbar.addPermanentWidget(self._overlay_chip)
        self._units_chip = QLabel("")
        self._units_chip.setToolTip(
            "Time units for THIS model (Project Settings)")
        # Scoped bright rule: the bar is dark navy in both themes, and an
        # app-level `QLabel { color: {text} }` rule would paint this chip
        # in the theme text colour (near-black on navy, invisible in the
        # light theme). Scoped so the rule stays off the tooltip window.
        self._units_chip.setStyleSheet(
            "QLabel { color: #EAF2FB; padding: 0 6px; font-weight: 600; }")
        self._statusbar.addPermanentWidget(self._units_chip)
        # How many cores the next run uses, always on screen (the ribbon's
        # Cores box sets it; `_sync_cores_box` keeps this in step).
        self._cores_chip = QLabel("")
        self._cores_chip.setObjectName("cores_chip")
        self._cores_chip.setStyleSheet(
            "QLabel { color: #EAF2FB; padding: 0 6px; font-weight: 600; }")
        self._statusbar.addPermanentWidget(self._cores_chip)
        self._sync_cores_box()

    def _wire_signals(self):
        self._explorer.block_selected.connect(self._on_explorer_block_selected)
        self._explorer.node_selected.connect(self._on_explorer_node_selected)
        self._explorer.event_selected.connect(self._on_explorer_event_selected)
        self._explorer.system_config_selected.connect(self._on_explorer_sysconfig_selected)
        self._inspector.block_renamed.connect(self._on_block_renamed)
        self._inspector.block_comment_changed.connect(
            lambda bid, txt: self._diagram_dock.view
            .set_element_comment("block", bid, txt)
            or self._mark_dirty())
        self._inspector.element_comment_changed.connect(
            lambda etype, oid, txt: self._diagram_dock.view
            .set_element_comment(etype, oid, txt)
            or self._mark_dirty())
        self._inspector.node_renamed.connect(self._on_node_renamed)
        self._inspector.hier_renamed.connect(self._on_hier_renamed)
        self._inspector.phasing_changed.connect(self._on_phasing_changed)
        self._inspector.block_pm_stagger_changed.connect(
            self._on_block_pm_stagger_changed)
        self._inspector.block_dep_changed.connect(
            lambda bid, vals: self._diagram_dock.view.set_block_dep_fields(
                bid, vals))
        self._inspector.block_init_stock_changed.connect(
            lambda bid, v: self._diagram_dock.view.set_block_init_stock(
                bid, v))
        self._diagram_dock.view.event_selected.connect(
            self._on_canvas_event_selected)
        self._inspector.event_renamed.connect(self._on_event_renamed)
        self._inspector.event_field_changed.connect(
            self._on_event_field_changed)
        self._inspector.block_costs_changed.connect(
            lambda bid, vals: self._diagram_dock.view.set_block_costs(
                bid, vals))
        self._inspector.block_pm_dist_changed.connect(
            lambda bid, code: (
                self._diagram_dock.view.set_block_pm_dist(bid, int(code)),
                self._inspector.show_block(
                    bid, self._diagram_dock.view.blocks)))
        self._inspector.block_pm_params_changed.connect(
            lambda bid, vals: self._diagram_dock.view.set_block_pm_params(
                bid, vals))
        self._inspector.block_pre_ldt_params_changed.connect(
            lambda bid, vals: self._diagram_dock.view.set_block_ldt_params(
                bid, "pre", vals))
        self._inspector.block_always_done_changed.connect(
            lambda bid, flag: self._diagram_dock.view.set_block_always_done(
                bid, flag))
        self._inspector.block_post_ldt_params_changed.connect(
            lambda bid, vals: self._diagram_dock.view.set_block_ldt_params(
                bid, "post", vals))
        self._inspector.block_fail_params_changed.connect(self._on_block_fail_params_changed)
        self._inspector.block_repair_params_changed.connect(self._on_block_repair_params_changed)
        # Re-populate after a Type change: _distribution_params_row builds the param
        # rows from the distribution current when the tab was populated,
        # so a new repair distribution would keep showing the previous
        # distribution's field labels/values until some later
        # re-populate.
        self._inspector.block_fail_type_changed.connect(
            lambda bid, code: self._on_block_dist_type_changed(bid, "F", code))
        self._inspector.block_repair_type_changed.connect(
            lambda bid, code: self._on_block_dist_type_changed(bid, "R", code))
        self._inspector.block_fail_stream_changed.connect(
            self._on_block_stream_changed)
        self._inspector.block_repair_stream_changed.connect(
            self._on_block_stream_changed)
        self._inspector.block_pm_freq_changed.connect(self._on_block_pm_freq_changed)
        self._inspector.node_good_paths_changed.connect(self._on_node_good_paths_changed)
        self._inspector.clear_results_requested.connect(self._clear_results_clicked)
        self._inspector.validation_failed.connect(
            lambda msg: self._statusbar.showMessage(f"Invalid value — {msg}", 8000))
        self._inspector.node_anal_type_changed.connect(
            self._on_weak_type_changed)
        self._inspector.sparing_type_changed.connect(
            lambda bid, name: self._diagram_dock.view
            .set_block_sparing_type(bid, name))
        self._inspector.pool_name_changed.connect(
            lambda bid, name: self._diagram_dock.view
            .set_block_pool_name(bid, name))
        self._inspector.block_pm_trig_changed.connect(
            lambda bid, on, name: self._diagram_dock.view
            .set_block_pm_trigger(bid, on, name))
        self._inspector.block_pre_ldt_dist_changed.connect(
            lambda bid, code: (
                self._diagram_dock.view.set_block_ldt_dist(bid, "pre", code),
                self._inspector.show_block(
                    bid, self._diagram_dock.view.blocks)))
        self._inspector.block_post_ldt_dist_changed.connect(
            lambda bid, code: (
                self._diagram_dock.view.set_block_ldt_dist(bid, "post", code),
                self._inspector.show_block(
                    bid, self._diagram_dock.view.blocks)))
        # Re-populate on toggle: the PM-enable checkbox gates whether
        # the rest of the tab (frequency/trigger/distribution/options)
        # exists at all, same reason node_full_flow_changed below
        # re-populates.
        self._inspector.block_pm_enabled_changed.connect(
            lambda bid, v: (
                self._diagram_dock.view.set_block_pm_enabled(bid, v),
                self._inspector.show_block(
                    bid, self._diagram_dock.view.blocks)))
        self._inspector.block_pm_option_changed.connect(
            lambda bid, key, v: self._diagram_dock.view
            .set_block_pm_option(bid, key, v))
        self._inspector.block_dep_master_changed.connect(
            lambda bid, dn, dt: self._diagram_dock.view
            .set_block_dep_master(bid, dn, dt))
        self._inspector.block_dep_default_idle_changed.connect(
            lambda bid, v: self._diagram_dock.view
            .set_block_dep_default_idle(bid, v))
        self._inspector.block_resource_pool_changed.connect(
            lambda bid, name: self._diagram_dock.view
            .set_block_resource_pool(bid, name))
        self._inspector.block_resource_counts_changed.connect(
            lambda bid, vals: self._diagram_dock.view
            .set_block_resource_counts(bid, vals[0], vals[1]))
        self._inspector.block_new_spares_changed.connect(
            lambda bid, vals: self._diagram_dock.view
            .set_block_new_spares(bid, vals[0], vals[1]))
        self._inspector.block_standby_stress_changed.connect(
            lambda bid, v: self._diagram_dock.view
            .set_block_standby_stress(bid, v))
        # Re-populate on toggle: enabling/disabling growth-decay changes
        # whether the Type/Rate/Limit fields exist at all.
        self._inspector.block_gd_enabled_changed.connect(
            lambda bid, v: (
                self._diagram_dock.view.set_block_gd_enabled(bid, v),
                self._inspector.show_block(
                    bid, self._diagram_dock.view.blocks)))
        self._inspector.block_gd_type_changed.connect(
            lambda bid, t: self._diagram_dock.view
            .set_block_gd_type(bid, t))
        self._inspector.block_gd_rate_limit_changed.connect(
            lambda bid, vals: self._diagram_dock.view
            .set_block_gd_rate_limit(bid, vals[0], vals[1]))
        self._inspector.manage_pool_requested.connect(
            self._on_manage_pool_requested)
        # Re-populate on toggle (not just mark-dirty) so the Flow In/Out
        # link table appears/disappears immediately, matching Raptor 7's
        # own checkbox-gated table.
        self._inspector.node_full_flow_changed.connect(
            lambda nid, v: (
                self._diagram_dock.view.set_node_full_flow(nid, v),
                self._inspector.show_node(nid, self._diagram_dock.view.nodes)))
        self._inspector.node_any_path_changed.connect(
            lambda nid, v: (
                self._diagram_dock.view.set_node_any_path(nid, v),
                self._inspector.show_node(nid, self._diagram_dock.view.nodes)))
        # Capacity flow-assignment cells: undoable
        # single-cell LINK_DATA edits. The command updates the row; the
        # live Link object the canvas reads holds a parsed copy, so
        # re-parse it to keep open views in sync.
        self._inspector.flow_cell_changed.connect(
            lambda lid, col, old, new: (
                self._diagram_dock.view.set_link_flow_field(
                    lid, col, old, new),
                self._diagram_dock.view.resync_link(lid)))
        # Node Standby tab's per-incoming-link switchover table (same
        # LINK_DATA/undo plumbing as the flow cells above).
        self._inspector.link_standby_cell_changed.connect(
            lambda lid, col, old, new: (
                self._diagram_dock.view.set_link_standby_field(
                    lid, col, old, new),
                self._diagram_dock.view.resync_link(lid)))
        self._inspector.node_report_anal_changed.connect(
            lambda nid, v: self._diagram_dock.view.set_node_report_anal(nid, v))
        self._inspector.node_k_star_changed.connect(
            lambda nid, v: self._diagram_dock.view.set_node_k_star(nid, v))
        self._inspector.node_priority_return_changed.connect(
            lambda nid, v: self._diagram_dock.view.set_node_priority_return(
                nid, v))
        self._inspector.node_check_autos_first_changed.connect(
            lambda nid, v: self._diagram_dock.view
            .set_node_check_autos_first(nid, v))
        # cold_standby also gates the Priority-Return/Auto-switch
        # checkboxes' enabled state (manual: "the first checkbox...
        # engages the remainder of the tab") -- re-populate so toggling
        # it updates their greyed-out state immediately, not just on
        # the next selection change.
        self._inspector.node_cold_standby_changed.connect(
            lambda nid, v: (
                self._diagram_dock.view.set_node_cold_standby(nid, v),
                self._inspector.show_node(nid, self._diagram_dock.view.nodes)))
        self._inspector.node_dep_changed.connect(
            lambda nid, dn, dt: self._diagram_dock.view.set_node_dep(
                nid, dn, dt))
        self._inspector.event_costs_changed.connect(
            lambda eid, vals: self._diagram_dock.view.set_event_costs(
                eid, vals))
        self._inspector.annotation_prop_changed.connect(
            lambda aid, changes: self._diagram_dock.view
            .set_annotation_props(aid, changes))
        self._inspector.rates_changed.connect(self._mark_dirty)
        self._inspector.system_threshold_changed.connect(
            self._on_system_threshold_changed)
        self._inspector.system_config_changed.connect(
            self._on_system_config_changed)
        self._inspector.system_comment_changed.connect(
            self._on_system_comment_changed)
        # Event-log rows carry element names; resolve them against the
        # loaded model and select on the canvas (which also populates the
        # Inspector via the normal selection path).
        self._results.element_clicked.connect(self._on_result_element_clicked)
        self._results.export_tables_requested.connect(self._export_tables)
        self._results.contents_changed.connect(self._sync_results_notice)
        self._results.set_status_hint_fn(
            lambda msg: self._statusbar.showMessage(msg, 6000))
        self._results.hierarchy_filter_changed.connect(
            self._on_results_hier_filter)
        self._results.set_hierarchy_options({})
        self._results.set_selected_block_names_fn(
            lambda: {self._diagram_dock.view.blocks[b].name
                     for b in self._diagram_dock.view.blocks
                     if b in self._diagram_dock.view._block_items
                     and self._diagram_dock.view._block_items[b]
                     .isSelected()})
        self._diagram_dock.view.selection_changed.connect(
            self._results.refresh_weak_selection)
        self._results.weak_analysis_type_changed.connect(
            self._on_weak_type_changed)
        self._results.importance_metric_changed.connect(
            self._on_importance_metric_changed)
        self._results.y_axis_min_changed.connect(self._on_y_axis_min_changed)

    def _on_theme_changed(self, dark: bool):
        self._theme_action.blockSignals(True)
        self._theme_action.setChecked(dark)
        self._theme_action.setText("Light Theme" if dark else "Dark Theme")
        self._theme_action.blockSignals(False)
        self.setStyleSheet(global_stylesheet())
        c = active_colour()
        self._ribbon._refresh_theme(c)
        self._apply_action_icons()
        for rethemer in (self._dock_titlebar_rethemers or []):
            rethemer()

    def _on_annotations_may_be_invisible(self, count: int) -> None:
        """The view has already run the contrast check (`DiagramView.
        find_low_contrast_annotations`); this asks before touching
        anything the user deliberately coloured."""
        from raptor.gui.ui.dialogs import question_box
        plural = "s" if count != 1 else ""
        if question_box(
                self, "Annotations may be hard to see",
                f"{count} annotation{plural} may be hard to see against "
                f"the new theme.\n\nAdjust their text/border colour to fit?",
                yes_text="Adjust colours", no_text="Leave as is",
                default_yes=True):
            view = self._diagram_dock.view
            view.fix_annotation_contrast(view.find_low_contrast_annotations())

    def _toggle_theme(self, checked: bool):
        ThemeManager.instance().set_dark(checked)
        # Persist so the next launch opens in the same mode.
        settings.set_value("appearance/dark_mode", checked)

    def _on_undo_clean_changed(self, clean: bool) -> None:
        """Undo back to the opened/saved state is clean again."""
        self._dirty = not clean
        self._update_title()

    def _mark_layout_dirty(self):
        """A layout-only edit (move/resize/reorder): dirty, nothing more.

        Geometry is not an input to validation, the engine graph, the
        Inspector, the Explorer, the Data Tables or the results hash, so
        none of those may be rebuilt for it. This is the light twin of
        `_mark_dirty`, wired from `DiagramView.layout_changed`; keeping
        the revision untouched also keeps the memoised validity good, so
        the click that follows a drag no longer re-validates the whole
        model."""
        self._dirty = True
        self._update_title()
        # The canvas moved but the Inspector is not rebuilt on the light
        # path; keep its Position rows in step (notably after undo/redo).
        self._inspector.refresh_position_display()

    def _mark_dirty(self):
        # First, before any early return: whatever the run guard does
        # about a mid-run edit, the model has changed, so the memoised
        # validity (`_model_is_valid`) is stale from this point on.
        self._bump_model_revision()
        # Run-setting writes deliberately keep the memoised answer (see
        # `_carry_validity_across_run_setting_edit`); every other edit
        # makes it a claim about a model that no longer exists.
        if not getattr(self, "_in_run_setting_edit", False):
            self._validity_dirty_since_check = True
        self._dirty = True
        self._update_title()
        # Edit-during-run guard: a sync run pumps the event loop and a
        # step session sits idle between clicks, so the UI stays fully
        # clickable mid-simulation -- the user could edit the model with
        # no indication that the run holds live engine objects built from
        # the old model. Intercept the first edit while a run/step is
        # active and let them choose; see `_confirm_edit_during_run`.
        #
        # Deferred on purpose: this slot fires from inside a
        # QUndoStack.push (the command's own _done() -> model_changed),
        # and a modal dialog plus stack.undo() re-entering an in-progress
        # redo() corrupts the stack. singleShot(0) runs the dialog after
        # the push has fully completed, when the edit is the top of the
        # stack. The step-session invalidation below is also skipped
        # while the choice is pending: tearing the session down before
        # the user answers would make the dialog's "keep simulation"
        # option a lie.
        if getattr(self, "_run_own_change", False):
            return      # the run adjusting its own settings, not an edit
        if getattr(self, "_edit_during_run_guard", False) or \
                self._run_or_step_active():
            if not getattr(self, "_edit_during_run_guard", False):
                self._edit_during_run_guard = True
                self._arm_edit_guard()
            self._find_combo_refresh()
            return
        # Any model edit invalidates an in-progress stepped run -- the
        # session holds live engine objects built from the old model.
        self._invalidate_step_session()
        self._find_combo_refresh()
        # Results on screen describe the model as it WAS. See
        # `_arm_results_guard`.
        if self._results_are_stale():
            self._arm_results_guard()

    # -- displayed results vs. edits ------------------------------------
    #
    # Editing is not locked by tab. What needs protecting is a result set
    # that is on screen: once the model changes, every number in it
    # describes a model that no longer exists, and nothing on the screen
    # would say so. So the FIRST edit that changes what the engine would
    # simulate asks whether to discard the results. Selecting, panning,
    # zooming, moving things around and changing run settings never ask:
    # they cannot change a simulated number, and the check below is made
    # on a hash that ignores layout (`run_manifest.model_results_hash`).
    #
    # It works like the edit-during-run guard above and for the same
    # reason: the prompt is deferred until the command has finished
    # pushing, and "keep the results" is implemented as undoing the edit.
    # The hash comparison is what makes that safe -- the undo re-enters
    # `_mark_dirty`, finds the model equal to the results' basis again, and
    # stays quiet, so the prompt cannot loop.

    def _results_basis_hash(self) -> str | None:
        """Hash of the model as the DIAGRAM currently holds it."""
        if self._rbd is None:
            return None
        try:
            self._diagram_dock.view.write_back_to_rbd(self._rbd)
            return run_manifest.model_results_hash(self._rbd)
        except Exception:
            return None

    def _note_results_basis(self) -> None:
        """Remember which model the results now on screen came from.
        Called wherever results are handed to the Results dock."""
        self._results_basis = self._results_basis_hash()

    def _results_are_displayed(self) -> bool:
        return self._results.has_results()

    def _results_are_stale(self) -> bool:
        """Results are on screen and the model no longer matches them."""
        if getattr(self, "_results_guard_reentrancy", False):
            return False
        if not self._results_are_displayed():
            return False
        basis = getattr(self, "_results_basis", None)
        if basis is None:
            return False        # unknown origin: never nag on a guess
        now = self._results_basis_hash()
        return now is not None and now != basis

    def _arm_results_guard(self) -> None:
        if getattr(self, "_results_guard_pending", False):
            return
        self._results_guard_pending = True
        QTimer.singleShot(0, self._confirm_edit_discarding_results)

    def _confirm_edit_discarding_results(self) -> None:
        self._results_guard_pending = False
        if getattr(self, "_results_guard_reentrancy", False):
            return
        if not self._results_are_stale():
            return              # undone, or a burst that netted out
        self._results_guard_reentrancy = True
        try:
            stack = getattr(self._diagram_dock.view, "_stack", None)
            can_revert = bool(stack and stack.canUndo())
            box = QMessageBox(self)
            box.setWindowTitle("Results on screen")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(
                "These results were produced from the model as it was "
                "before this edit.\n\nKeeping the edit means discarding "
                "them; they would no longer match the model.")
            keep_btn = None
            if can_revert:
                keep_btn = box.addButton(
                    "Keep results (undo this edit)",
                    QMessageBox.ButtonRole.NoRole)
            discard_btn = box.addButton(
                "Discard results && edit", QMessageBox.ButtonRole.YesRole)
            box.setDefaultButton(keep_btn or discard_btn)
            box.exec()
            if box.clickedButton() is discard_btn:
                self._discard_results()
                self._statusbar.showMessage(
                    "Results discarded — the model was edited", 6000)
            elif keep_btn is not None and stack is not None:
                stack.undo()
                self._statusbar.showMessage(
                    "Edit undone — results kept", 6000)
            else:
                # Nothing to undo and the box was dismissed: the edit
                # already stands, so the results must go.
                self._discard_results()
        finally:
            self._results_guard_reentrancy = False

    def _discard_results(self) -> None:
        """Every surface that shows the old model's numbers."""
        self._results.clear()
        self._results_basis = None
        self._reset_status_summary()
        self._update_system_swatch(None)
        self._diagram_dock.view.clear_live_states(keep_result_visuals=False)
        self._diagram_dock.view._weak_colors_on = False
        self._sync_overlay_actions([])

    def _apply_hier_status(self, report_data) -> None:
        """Each hierarchy's status dot after a run: its share of time
        working, degraded and down over EVERY run, banded like Weak Links
        (`weak_metric.hier_time_shares`). It showed how the last trial
        ended, which after many trials is nearly always "all up". The
        per-run times come from Weak Link analysis; without it, no dot --
        rather than one that misleads."""
        from raptor.core.engine.weak_metric import hier_time_shares
        runs = [d.weak_hiers for d in report_data or []
                if getattr(d, "weak_hiers", None)]
        shares = hier_time_shares(runs) if runs else {}
        # Read the model, not the ribbon widgets: after a Project
        # Settings/undo refresh the widgets can be stale, and the dots
        # must use the same thresholds everything else does.
        try:
            from raptor.core.fileio.typed import system_config_from_row
            cfg = system_config_from_row(
                req(self._rbd).sections["SYSTEM_DATA"].rows[0])
            gy, yr = cfg.gy_threshold, cfg.yr_threshold
        except (ValueError, KeyError, IndexError, AttributeError):
            gy, yr = self._gy_spin.value(), self._yr_spin.value()
        dsi = {}
        for hid, sh in shares.items():
            a = sh["availability"]
            band = "AllUp" if a >= gy else "Degraded" if a >= yr else "Down"
            dsi[hid] = {"kind": "average", "state": band, **sh}
        self._diagram_dock.view.apply_hier_dsi(dsi)

    def _sync_overlay_actions(self, report_data) -> None:
        """Results > Show on Canvas: each overlay is offered when the run
        produced its data. One left on from data that is gone goes off."""
        have = {
            "_weak_color_action": any(d.weak_stats for d in report_data),
            "_capacity_overlay_action": any(d.capacity is not None
                                            for d in report_data),
            "_cost_overlay_action": any(d.cost is not None
                                        for d in report_data),
            "_spares_overlay_action": any(d.weak_stats for d in report_data),
            # Per run, not per model: importance only exists when the
            # experiment asked for it, so the button appears with the run.
            "_importance_overlay_action": bool(
                getattr(self._results, "_importance", None)),
        }
        # A new result set invalidates every previous overlay: clear the
        # canvas before re-enabling/disabling actions, never leave old
        # gauges/fills/tooltips behind.
        self._diagram_dock.view.clear_result_overlays()
        self._cost_spread = False
        for attr, ok in have.items():
            action = getattr(self, attr)
            if not ok:
                self._uncheck_overlay(attr)
            action.setEnabled(ok)
        spread = getattr(self, "_weak_spread_action", None)
        if spread is not None:
            spread.setEnabled(
                self._weak_color_action.isChecked()
                or self._cost_overlay_action.isChecked())
        self._update_overlay_chip()

    def _uncheck_overlay(self, attr: str) -> None:
        """Untick an overlay's ribbon button without running its handler."""
        action = getattr(self, attr)
        action.blockSignals(True)
        action.setChecked(False)
        action.blockSignals(False)

    def _sync_results_notice(self) -> None:
        """Tell the Inspector whether editing will cost the results."""
        try:
            self._inspector.set_results_notice(self._results_are_displayed())
        except AttributeError:
            pass
        # Clear Results is meaningless without a result set; update only
        # that predicate (a full refresh would re-enable Run mid-run).
        try:
            self._refresh_commands(only={"has_results"})
        except AttributeError:
            pass

    def _run_or_step_active(self) -> bool:
        """A foreground/background run is in progress, or a step-mode
        session exists (stepped runs live on between clicks)."""
        if getattr(self, "_edit_run_abandoned", False):
            # The user already answered "cancel the simulation" for
            # this run -- every further edit just lands, no re-prompt.
            return False
        if getattr(self, "_run_in_progress", False):
            return True
        return getattr(self, "_step_session", None) is not None

    def _refuse_during_run(self, what: str) -> bool:
        """True (and warns) if `what` (Open/New/Close/Quit) must be
        refused because a simulation is running OR a print/PDF export is
        in progress.

        Both run paths pump the event loop (`QApplication.processEvents`
        in the synchronous callbacks; a free main thread for the
        background path), and the PDF export (`report_pdf.py`) pumps it too
        -- so a File-menu action can fire in either while `self._rbd` is
        being read/reassigned. The model-edit case is guarded by
        `_mark_dirty`; this covers the file/lifecycle cases."""
        if getattr(self, "_export_in_progress", False):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("Export in progress")
            box.setText(f"Cannot {what} while a print/PDF export is "
                        f"running.\n\nWait for the export to finish.")
            box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
            box.exec()
            return True
        if not getattr(self, "_run_in_progress", False):
            return False
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Simulation in progress")
        box.setText(f"Cannot {what} while a simulation is running.\n\n"
                    "Stop the run first.")
        box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        return True

    def _schedule_view_refresh(self):
        """Coalesced, deferred refresh of the model-derived views
        (Inspector, Data Tables, Explorer) after a model mutation.

        `model_changed` fires per undo command -- including undo/redo,
        whose apply runs from inside the command -- so refreshing
        synchronously from this slot would tear down widgets during a
        signal they themselves emitted. Deferring to singleShot(0) lets
        the command finish first; the flag coalesces a burst into one
        repaint."""
        if getattr(self, "_view_refresh_pending", False):
            return
        self._view_refresh_pending = True
        QTimer.singleShot(0, self._do_view_refresh)

    def _do_view_refresh(self):
        self._view_refresh_pending = False
        if getattr(self, "_view_refresh_busy", False):
            return
        self._view_refresh_busy = True
        try:
            try:
                self._refresh_inspector()
            except Exception:
                pass
            try:
                self._explorer.refresh()
            except Exception:
                pass
            # Data Tables are model-derived too. This was missing, so a
            # rename/parameter edit made in the Inspector or undone from
            # the canvas left the grid showing the old value.
            self._refresh_data_tables()
            try:
                self._populate_breakpoints()   # renames, adds, deletes
            except Exception:
                pass
            # Whatever the change was -- a hierarchy added, deleted,
            # dissolved, collapsed, or any of those undone -- the commands
            # that depend on what is on the canvas may have changed too.
            try:
                self._refresh_commands(only={"can_zoom_hierarchy",
                                             "can_align", "can_mass_edit",
                                             "can_extract_hierarchy"})
            except Exception:
                pass
        finally:
            self._view_refresh_busy = False

    def _refresh_data_tables(self):
        """Refresh the Data Tables grids after a model change.

        Deferred so the undo command or cell commit finishes first, and
        coalesced so a burst of signals is one repopulate. DataTables.
        refresh() preserves the current row/cell, so this no longer drops
        the user's place after every edit.
        """
        if getattr(self, "_data_refresh_pending", False):
            return
        # `_apply_loaded_model` already repopulates every grid through
        # `set_view` (and has just set the units/display units). A signal
        # raised during that load used to schedule a SECOND full rebuild
        # of every table -- on a 500-block model that was half the Open
        # time, for a grid that had just been built from the same model.
        if getattr(self, "_loading_model", False):
            return
        self._data_refresh_pending = True

        def _do():
            self._data_refresh_pending = False
            try:
                self._data_tables.refresh(force=False)
            except Exception:
                pass
        QTimer.singleShot(0, _do)

    def _on_canvas_selection_cleared(self) -> None:
        """Clicking empty canvas deselects: the Inspector stops showing the
        element it had (nothing listened to this, so a deselected node
        stayed in the Inspector, editable). Not while the canvas rebuilds
        itself (a level change, a Rates update) -- that clears the scene's
        selection too, and must not empty the Inspector mid-edit -- and not
        for Project Settings, which is not a canvas selection."""
        v = self._diagram_dock.view
        if getattr(v, "_loading", False):
            return
        self._cancel_pending_block_selection()
        target = self._inspector.current_target()
        if target and target[0] in ("block", "node", "event", "hier", "annotation"):
            self._inspector.clear_selection()
            self._inspector._refresh_empty_text()

    def _refresh_inspector(self):
        """Re-populate whatever element the Inspector is showing from
        the CURRENT model. This is what keeps the Inspector from
        displaying (and then re-applying) a stale populate-time value
        after Ctrl+Z, and what keeps it in step with Data Tables edits.

        This is the ONLY refresh path (review E-16). It previously had a
        second, narrower twin, `_refresh_inspector_current_element`, which
        re-dispatched the same job from `_current_block_id` /
        `_current_node_id` -- **private attributes of another class**, for
        which `InspectorDock.current_target()` exists precisely to avoid
        that. The twin handled only 2 of the 6 kinds, so `event`, `hier`,
        `system` and `annotation` were silently skipped on exactly the
        flows that used it: the pool-dialog return, the ribbon pool path,
        and the display-unit switch. Same screen, two dispatch tables, and
        the narrower one was the one with the holes.
        """
        target = self._inspector.current_target()
        if not target:
            # Nothing selected: the empty state's model counts move with
            # every edit ("This model: 1 blocks" after adding three).
            self._inspector._refresh_empty_text()
            return
        kind, oid = target
        v = self._diagram_dock.view
        gone = {"block": v.blocks, "node": v.nodes, "event": v.events,
                "hier": v.hiers}
        if kind in gone and oid not in gone[kind]:
            # Deleted (or undone): it used to stay on show, with editable
            # fields for an element that no longer exists.
            self._inspector.clear_selection()
            return
        if kind == "block" and oid in v.blocks:
            self._inspector.show_block(oid, v.blocks)
        elif kind == "node" and oid in v.nodes:
            self._inspector.show_node(oid, v.nodes)
        elif kind == "event" and oid in v.events:
            self._inspector.show_event(v.events[oid])
        elif kind == "hier" and oid in v.hiers:
            self._inspector.show_hier(v.hiers[oid])
        elif kind == "system":
            self._inspector.show_system_config()
        elif kind == "annotation":
            # Without this the panel was never re-populated for an
            # annotation, so any prop change made from anywhere OTHER
            # than the panel itself -- an undo, the "these annotations
            # may be hard to see" contrast fix, a model edited
            # externally -- left the on-screen colour/font values stale
            # until the user re-selected the note. Every other element
            # kind refreshed here already.
            item = v._anno_items.get(oid)
            if item is not None:
                self._inspector.show_annotation(item)

    def _on_block_pm_stagger_changed(self, bid: int, value: float):
        """Original rule: PM stagger must be less than PM frequency.
        Reject the write (and say why) instead of silently storing an
        invalid combination."""
        block = self._diagram_dock.view.blocks.get(bid)
        if block is not None and block.pm_freq and value >= block.pm_freq:
            self._statusbar.showMessage(
                "PM stagger must be less than PM frequency", 8000)
            return
        self._diagram_dock.view.set_block_pm_stagger(bid, value)

    #: How many times the edit-during-run prompt may appear in
    #: `_EDIT_GUARD_WINDOW` before this class of dialog is considered
    #: broken and the guard switches to stopping the run silently.
    #:
    #: This exists because the prompt is MODAL and the code that raises it
    #: runs from inside `QUndoStack.push` (a command's `_done()` ->
    #: `model_changed`). Any re-entrancy on that path -- the dialog's own
    #: button handler, a nested command, an operation that pumps the event
    #: loop between commands -- can arm the guard again while the first
    #: dialog is still being handled. The latch coalesced the common case
    #: (one prompt per burst) but nothing bounded the pathological one,
    #: and the user-visible symptom was a pile of dialogs flashing
    #: transiently over copy/paste, Animate and the analysis toggles.
    #:
    #: Two hard rules follow, and together they make a stack impossible
    #: rather than merely unlikely:
    #:
    #:  1. `_edit_guard_reentrancy` makes the prompt body non-re-entrant,
    #:     so nothing the dialog does can re-arm it.
    #:  2. The cap makes the Nth prompt impossible. Past the cap the guard
    #:     stops the run without asking -- the correct outcome anyway
    #:     (a model edit must not leave a run on stale engine objects), and
    #:     far better than a fourth dialog.
    _EDIT_GUARD_MAX_PROMPTS = 2
    _EDIT_GUARD_WINDOW = 30.0      # seconds

    def _arm_edit_guard(self) -> None:
        """Queue the edit-during-run prompt, coalescing a burst of edits
        into one dialog.

        The zero-timer is what does the coalescing: a multi-element paste
        calls `_mark_dirty` once per command, and the queued callback runs
        only once, after the whole burst has been pushed and the last
        command is the top of the undo stack. That is also why the dialog
        cannot be shown inline here.
        """
        QTimer.singleShot(0, self._confirm_edit_during_run)

    def _edit_guard_prompt_budget(self) -> int:
        """Prompts left before the guard stops asking.

        Prunes expired timestamps as it goes, so the window is applied to
        the recorded prompts themselves rather than through a separate
        "last prompt" field that could disagree with the list.
        """
        now = time.monotonic()
        recent = [t for t in getattr(self, "_edit_guard_prompt_times", [])
                  if now - t <= self._EDIT_GUARD_WINDOW]
        self._edit_guard_prompt_times = recent
        return self._EDIT_GUARD_MAX_PROMPTS - len(recent)

    def _note_edit_guard_prompt(self) -> None:
        self._edit_guard_prompt_times.append(time.monotonic())

    def _confirm_edit_during_run(self):
        """Model edit while a simulation is running/stepping: confirm
        BEFORE anything consumes the edit. Scheduled by `_arm_edit_guard`
        via singleShot(0) so this runs AFTER the push completed (the edit
        is then the top of the undo stack); the re-entrant model_changed a
        revert emits re-enters `_mark_dirty`, which sees the guard flag
        and stays out of the way.

        Two protections, both about the *general* problem rather than any
        one operation:

        * **Re-entrancy.** `_edit_guard_reentrancy` makes the body
          non-re-entrant. Without it, anything the dialog's own handlers
          do -- `stack.undo()`, `_stop_run_for_edit()` -- can schedule
          another copy of this method while the first is still running,
          and since `box.exec()` pumps the event loop that second copy
          runs *inside* the first.
        * **A cap.** After `_EDIT_GUARD_MAX_PROMPTS` prompts in the
          window, the guard stops asking and simply stops the run. The
          prompt is a courtesy about a consequence the app has to enforce
          anyway (a model edit cannot leave a run executing against stale
          engine objects), so silently doing the enforced thing is right,
          and a bounded number of dialogs is a promise the UI can keep.
        """
        if getattr(self, "_edit_guard_reentrancy", False):
            return          # already inside a prompt; never stack
        if not getattr(self, "_edit_during_run_guard", False):
            # Direct call (not via the deferred scheduling path).
            self._edit_during_run_guard = True
        if not self._run_or_step_active():
            # The run finished between arming and firing. Nothing to ask.
            self._edit_during_run_guard = False
            return
        if self._edit_guard_prompt_budget() <= 0:
            # Out of budget: do the enforced thing without asking.
            self._edit_during_run_guard = False
            self._stop_run_for_edit()
            self._statusbar.showMessage(
                "Simulation stopped — the model was edited", 6000)
            return
        self._edit_guard_reentrancy = True
        self._note_edit_guard_prompt()
        try:
            stack = getattr(self._diagram_dock.view, "_stack", None)
            can_revert = bool(stack and stack.canUndo())
            box = QMessageBox(self)
            box.setWindowTitle("Simulation in progress")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(
                "A simulation is running (or being stepped), and it was "
                "built from the model as it was. Keeping this edit stops it.")
            # Two choices, each saying what it does. There was a third,
            # plain "Cancel" -- the default, and what Esc chose -- which
            # read as "never mind" but stopped the run and kept the edit.
            keep_btn = None
            if can_revert:
                keep_btn = box.addButton(
                    "Keep running (undo this edit)",
                    QMessageBox.ButtonRole.RejectRole)
                box.setDefaultButton(keep_btn)
                box.setEscapeButton(keep_btn)
            stop_btn = box.addButton("Stop the run, keep the edit",
                                     QMessageBox.ButtonRole.AcceptRole)
            if keep_btn is None:
                box.setDefaultButton(stop_btn)
                box.setEscapeButton(stop_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is keep_btn and keep_btn is not None:
                # Undo the just-pushed edit command; the re-entrant
                # model_changed this emits hits the guard flag above
                # and returns immediately. The run keeps going on the
                # unmodified model.
                req(stack).undo()
                self._statusbar.showMessage(
                    "Model edit reverted — simulation continues", 6000)
                return
            # "Stop the run, keep the edit": the run dies through the same
            # paths the Stop button uses.
            self._stop_run_for_edit()
        finally:
            # The re-entrancy latch is released last, in its own step, so
            # it is still held while `_stop_run_for_edit` and
            # `stack.undo()` above run their own `model_changed` cascade.
            # Releasing both together would let the last of those calls
            # schedule a fresh prompt.
            self._edit_during_run_guard = False
            self._edit_guard_reentrancy = False

    def _stop_run_for_edit(self):
        """The edit won: stop whatever run/step is active, using the
        Stop button's own mechanisms (sync flag / bg event / step
        session teardown)."""
        self._edit_run_abandoned = True
        if getattr(self, "_step_session", None) is not None:
            self._invalidate_step_session()
            self._statusbar.showMessage(
                "Simulation cancelled — model edited", 6000)
            return
        self._stop_bg_run()

    def _reset_live_log(self):
        """Clear the stepping Live Log dock and its fed-counter together:
        the counter reset alone would leave the previous run's rows above
        the new session's lines."""
        if hasattr(self, "_live_log_fed"):
            self._live_log_fed = 0
        self._live_log_tree.clear()
        self._live_log_dock.setWindowTitle("Live Log")

    def _invalidate_step_session(self):
        self._leave_fev()
        self._on_fev_log_close()
        if getattr(self, "_fev_status_dialog", None) is not None:
            cast(Any, self)._fev_status_dialog.close()
        self._reset_live_log()
        if getattr(self, "_step_session", None) is not None:
            self._step_session = None
            self._step_sink = []
            self._diagram_dock.view.clear_live_states()
            # The run is over, so the run settings unlock. Derived, not
            # remembered -- see `_sync_run_settings_lock`.
            self._sync_run_settings_lock()
            self._statusbar.showMessage("Stepped run discarded — model changed")

    def _update_title(self):
        star = "*" if self._dirty else ""
        if self._rbd is None:
            self.setWindowTitle("RAPTOR Studio")
        elif self._current_file is None:
            # Name a never-saved model too, with its unsaved mark: it used
            # to show plain "RAPTOR Studio", the one model state with no
            # cue that Save As is needed.
            stem = Path(str(getattr(self._rbd, "file_name", "") or "")).stem
            name = (f"{stem} (not saved)"
                    if stem and stem not in ("NewModel", "Untitled")
                    else "Untitled")
            self.setWindowTitle(f"RAPTOR Studio — {name}{star}")
        else:
            filename = Path(self._current_file).name
            self.setWindowTitle(f"RAPTOR Studio — {filename}{star}")
        results = getattr(self, "_results_dock", None)
        if results is not None and results.isFloating():
            results.setWindowTitle(self._results_window_title())
        # Save's hover tooltip says what plain Save will do.
        if self._current_file:
            ext = Path(self._current_file).suffix.lstrip(".")
            tip = f"Save as .{ext} — the open file's format (Ctrl+S)"
        else:
            tip = "Save — not saved yet, so choose where (Ctrl+S)"
        self._commands.set_tooltip(self._save_action, tip)

    def _confirm_discard_unsaved(self, *, closing: bool = False) -> bool:
        """Returns True if it's OK to proceed (no unsaved changes, or the
        user chose to Save/proceed anyway); False if the caller should
        abort (user chose Cancel, or a requested Save itself failed/was
        cancelled).

        `closing=True` (window/file close, the only case where declining
        to save genuinely loses the edits) keeps the standard Save/
        Discard/Cancel wording. The default (`closing=False`, e.g. before
        a Run) uses "Save"/"Run Without Saving"/"Cancel" instead: running
        doesn't touch the in-memory model or close anything, so "Discard"
        would be misleading there."""
        if not self._dirty:
            return True
        # This text is a sentence's own opening word, so it needs a
        # capital regardless of any surrounding context.
        name = Path(self._current_file).name if self._current_file else "This model"
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved Changes")
        box.setIcon(QMessageBox.Icon.Question)
        if closing:
            box.setText(f"{name} has unsaved changes. Save before continuing?")
            save_btn = box.addButton(QMessageBox.StandardButton.Save)
            _proceed_btn = box.addButton(QMessageBox.StandardButton.Discard)
        else:
            box.setText(
                f"{name} has unsaved changes. The simulation will use "
                "the current in-memory model either way — this only "
                "affects what's on disk.")
            save_btn = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
            _proceed_btn = box.addButton("Run Without Saving", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(save_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel_btn:
            return False
        if clicked is save_btn:
            self._save_file()
            return not self._dirty  # False if the save failed or was cancelled.
        return True  # proceed_btn (Discard / Run Without Saving).

    def _maybe_save_before_run(self) -> bool:
        """A preference decides what to do with unsaved changes before
        Run, since results otherwise silently reflect a different model
        than what's on disk. Returns True if it's OK to proceed."""
        if not self._dirty:
            return True
        mode = self._prefs().get("save_before_run", "ask")
        if mode == "never":
            return True
        if mode == "always":
            self._save_file()
            return not self._dirty  # False if the save itself failed/was cancelled.
        return self._confirm_discard_unsaved()  # "ask" -- closing=False







    def _export_block_library(self):
        from raptor.gui.ui import dialogs
        dialogs.export_block_library(self)

    def _import_block_library(self):
        from raptor.gui.ui import dialogs
        dialogs.import_block_library(self)

    def _show_zoom_percent(self):
        """Manual 20-12 Zoom Percent dialog: absolute zoom entry."""
        if self._rbd is None:
            return
        from PySide6.QtWidgets import (QDialog, QPushButton, QHBoxLayout)
        v = self._diagram_dock.view
        dlg = QDialog(self)
        dlg.setWindowTitle("Zoom Percent")
        form = QFormLayout(dlg)
        spin = QDoubleSpinBox()
        spin.setRange(2.0, 400000.0)   # ZOOM_MIN..ZOOM_MAX as percent
        spin.setDecimals(1)
        spin.setSuffix("%")
        spin.setValue(v.zoom_percent())
        spin.setToolTip("100 = model units at native scale")
        form.addRow("Zoom:", spin)
        buttons = QHBoxLayout()
        ok = QPushButton("OK")
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dlg.reject)

        def accept():
            if v.set_zoom_percent(spin.value()):
                self._statusbar.showMessage(
                    f"Zoom set to {v.zoom_percent()}%", 4000)
            dlg.accept()
        ok.clicked.connect(accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        form.addRow(buttons)
        exec_dialog(dlg)

    def _export_pdf(self):
        if self._rbd is None:
            return
        from raptor.gui.ui.printing import _prompt_export_options
        # The report (`report_pdf`, laid out by `raptor.api.report_html`);
        # Print sends the same report to the printer. Same section picker.
        from raptor.gui.ui.report_pdf import export_pdf
        opts = _prompt_export_options(self, "Export PDF")
        if opts is None:
            return
        default = (Path(self._current_file).stem + ".pdf"
                   if self._current_file else "report.pdf")
        path, _ = fdg.getSaveFileName(
            self, "Export PDF", default, "PDF files (*.pdf)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._export_in_progress = True
        try:
            self._diagram_dock.view.write_back_to_rbd(self._rbd)
            export_pdf(self, path, opts)
        except Exception as e:
            # Export drives weasyprint + file I/O; without this
            # handler a failure produced no dialog and, crossing the Qt
            # slot boundary, could take the process (and the unsaved
            # model) down with no trace.
            msgbox.critical(
                self, "Export PDF Failed",
                f"Could not export the PDF:\n\n{e}")
            self._statusbar.showMessage("PDF export failed", 8000)
            return
        finally:
            self._export_in_progress = False
            QApplication.restoreOverrideCursor()
        self._statusbar.showMessage(f"PDF saved: {Path(path).name}",
                                    6000)

    def _export_tables(self):
        """File > Export > Export Tables...: the model's tables and a run's
        result tables as CSV, text or a spreadsheet."""
        from raptor.gui.ui.tables_dialog import open_export_tables_dialog
        open_export_tables_dialog(self)

    def _export_diagram_svg(self):
        """Export the CURRENTLY DISPLAYED hierarchy level as a scalable
        vector file -- reuses the exact same content-rect measurement
        and QPainter render call the PDF report uses for its diagram
        pages (`report_pdf.py`), just targeting a `QSvgGenerator` paint
        device instead of an image. No
        options dialog (unlike Print/Export PDF): a diagram export has
        exactly one sensible output, the view as it stands, no
        section-picker/title-block choices to make."""
        if self._rbd is None:
            return
        from PySide6.QtSvg import QSvgGenerator
        from PySide6.QtGui import QPainter
        default = (Path(self._current_file).stem + ".svg"
                  if self._current_file else "diagram.svg")
        path, _ = fdg.getSaveFileName(
            self, "Export Diagram SVG", default, "SVG files (*.svg)")
        if not path:
            return
        v = self._diagram_dock.view
        # Measure the content rect live from the scene's own items: the
        # cached `_content_rect` is only refreshed on a full scene rebuild
        # and goes stale after ad hoc drags (its own docstring), which
        # would render exported elements off-centre inside the viewBox
        # and could crop links.
        content = QRectF()
        from raptor.gui.ui.diagram import _GridBackground
        for it in v.scene().items():
            if isinstance(it, _GridBackground):
                continue
            content = content.united(it.sceneBoundingRect())
        if content.isEmpty():
            content = getattr(v, "_content_rect", None) or v.sceneRect()
        src = content.adjusted(-30, -30, 30, 30)
        w = max(int(src.width()), 1)
        h = max(int(src.height()), 1)
        gen = QSvgGenerator()
        gen.setFileName(path)
        gen.setSize(QSize(w, h))
        gen.setViewBox(QRectF(0, 0, w, h))
        gen.setTitle(Path(self._current_file).stem
                    if self._current_file else "RAPTOR Studio diagram")
        painter = QPainter()
        if not painter.begin(gen):
            # Report the failure instead of silently producing no file.
            msgbox.critical(
                self, "Export Diagram Failed",
                f"Could not write the SVG to:\n\n{path}")
            self._statusbar.showMessage("Diagram SVG export failed", 8000)
            return
        try:
            # No grid: `QGraphicsScene.render` paints everything in the
            # scene and has no item filter, so the world-sized
            # `_GridBackground` -- an on-screen editing aid -- was
            # baked into every exported SVG.
            with v.labels_forced(), v.grid_hidden():
                v.scene().render(painter, QRectF(0, 0, w, h), QRectF(src),
                                 Qt.AspectRatioMode.IgnoreAspectRatio)
        except Exception as e:
            msgbox.critical(
                self, "Export Diagram Failed",
                f"Could not render the diagram:\n\n{e}")
            self._statusbar.showMessage("Diagram SVG export failed", 8000)
            return
        finally:
            painter.end()
        self._statusbar.showMessage(
            f"Diagram SVG saved: {Path(path).name}", 6000)

    def _open_phasing_dialog(self):
        from raptor.gui.ui.phasing_dialog import open_phasing_dialog
        open_phasing_dialog(self)

    def _open_pool_dialog(self, section: str):
        from raptor.gui.ui.pool_dialogs import open_pool_dialog
        open_pool_dialog(self, section)
        # The dialog is modal, so on return any Inspector tab whose
        # fields list these rows (PM's "Trigger Name" combo, a Spares
        # tab's "Pool:") is showing a stale list unless it repopulates
        # -- the Inspector's own Manage… button already refreshed via
        # manage_pool_requested; the ribbon path didn't.
        self._refresh_inspector()

    def _open_rates_dialog(self):
        from raptor.gui.ui.rates_dialog import open_rates_dialog
        open_rates_dialog(self)

    def _on_manage_pool_requested(self, section: str):
        """Contextual 'Manage…' button from the Inspector (Spares/PM/
        General tabs). Opens the same dialog as the ribbon, then
        re-populates the currently-shown element so a newly added pool/
        trigger/phase shows up immediately instead of only after the
        user re-clicks the element."""
        self._open_pool_dialog(section)
        self._refresh_inspector()

    def _push_weak_settings(self):
        """Hand the model's Raptor 7 settings (analysis type + thresholds)
        to the results dock before any Weak tab is built."""
        try:
            from raptor.core.fileio.typed import system_config_from_row
            cfg = system_config_from_row(
                req(self._rbd).sections["SYSTEM_DATA"].rows[0])
            self._results.set_weak_settings(cfg.node_anal_type,
                                            cfg.gy_threshold,
                                            cfg.yr_threshold)
        except (ValueError, KeyError, IndexError, AttributeError):
            pass

    def _push_hierarchy_options(self):
        """Feed the results filter combo with {id: name} for every
        hierarchy; keep the combo on the CURRENT level."""
        v = self._diagram_dock.view
        hiers = {hid: h.name for hid, h in v.hiers.items()}
        self._results.set_hierarchy_options(hiers,
                                            v._current_parent)

    def _on_results_hier_filter(self, hid: int):
        """User picked a hierarchy in the results filter: navigate the
        canvas to it, which runs the standard level-filter + visuals
        path."""
        if hid and hid in self._diagram_dock.view.hiers:
            self._diagram_dock.view.enter_level(hid)
        elif not hid:
            self._diagram_dock.view.go_home()

    def _show_domains_dialog(self):
        """Weak Link Domain Diagram as a ribbon dialog:
        per Regular/End node, every element whose failure propagates."""
        if self._rbd is None:
            return
        # Built on demand (see `_push_domains`): this is the only reader.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._push_domains()
        finally:
            QApplication.restoreOverrideCursor()
        dlg = QDialog(self)
        dlg.setWindowTitle("Domain Diagram")
        lay = QVBoxLayout(dlg)
        head = QLabel(
            "For each node: every element whose failure can propagate "
            "to it. Structural — reflects the model as loaded.")
        head.setObjectName("SectionNote")
        head.setWordWrap(True)
        lay.addWidget(head)
        tree = QTreeWidget()
        tree.setColumnCount(1)
        tree.setHeaderHidden(True)
        tree.setAlternatingRowColors(True)
        for nid in sorted(self._results._domains_data or {}):
            info = self._results._domains_data[nid]
            role = "End" if info.get("end") else "Regular"
            root = QTreeWidgetItem(
                [f"{info['name']} ({role}) — {len(info['sources'])} "
                 f"source(s), path via: {', '.join(info['paths'])}"])
            for kind, name in info["sources"]:
                QTreeWidgetItem(root, [f"{name}  ({kind})"])
            tree.addTopLevelItem(root)
        lay.addWidget(tree, 1)
        close = QPushButton("Close")
        close.clicked.connect(dlg.reject)
        lay.addWidget(close)
        dlg.resize(520, 460)
        # Non-modal (`.show()`, not `.exec()`): a modal dialog blocks with
        # nothing able to dismiss it under headless/offscreen test
        # automation. `self._domains_dialog` keeps a reference so it
        # isn't garbage-collected the instant this method returns.
        self._domains_dialog = dlg
        dlg.show()

    def _show_spof_dialog(self):
        from raptor.gui.ui import dialogs
        dialogs.show_spof_dialog(self)

    def _show_preferences(self):
        from raptor.gui.ui import dialogs
        dialogs.show_preferences(self)
        self._refresh_dev_hooks()

    def _refresh_dev_hooks(self) -> None:
        """Apply the developer options that act on the canvas (`inspector_ids`: Raptor 7's
        `/dev` hover text in the status bar)."""
        view = self._diagram_dock.view
        if self._dev_options().inspector_ids:
            from raptor.gui.dev_inspector import inspector_text
            view.hover_inspector = (
                lambda kind, ident: inspector_text(
                    self._rbd, kind, ident, view=view)
                if self._rbd is not None else None)
        else:
            view.hover_inspector = None

    def _show_system_config(self):
        """Legacy modal entry point (kept for old test harnesses).

        The GUI command goes to `_open_project_settings`, which uses the
        Inspector. Nothing in the ribbon/menu should call this.
        """
        from raptor.gui.ui import dialogs
        dialogs.show_system_config(self)

    def _open_project_settings(self):
        """Project Settings is the Inspector page, not a modal.

        The old modal duplicated run settings, analyses and thresholds
        and applied an all-or-nothing set of edits. The Inspector is
        already bound to the same model and stays open beside the canvas.
        """
        self._inspector_dock.show()
        self._inspector_dock.raise_()
        self._inspector.show_system_config()
        self._statusbar.showMessage(
            "Project Settings opened in the Inspector", 4000)

    def _edit_block_defaults(self):
        from raptor.gui.ui import dialogs
        dialogs.edit_block_defaults(self)

    def _on_mass_edit(self):
        from raptor.gui.ui import dialogs
        dialogs.on_mass_edit(self)

    def _make_file_dialog(self, caption: str, mode: QFileDialog.AcceptMode) -> QFileDialog:
        """A pre-configured non-native file dialog. The native/stock one
        rendered its toolbar icons (back/forward, view-mode buttons) at a
        few pixels -- our global QToolButton stylesheet starves them.
        Building the dialog ourselves lets us restore sane icon sizes and
        keep the theme's stylesheet."""
        dlg = QFileDialog(self, caption)
        dlg.setAcceptMode(mode)
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        fdg.theme_file_dialog(dlg)
        if mode == QFileDialog.AcceptMode.AcceptOpen:
            # Open shows both formats by default, not just .rbds with
            # .rbd hidden behind the filter dropdown -- the combined
            # filter is listed first (Qt's default-active-filter
            # convention) so nothing is hidden on open; the two
            # single-format filters stay available for narrowing.
            dlg.setNameFilter(
                "All RAPTOR models (*.rbds *.rbd);;"
                "RAPTOR Studio models (*.rbds);;Classic RAPTOR (*.rbd)")
        else:
            dlg.setNameFilter(
                "RAPTOR Studio models (*.rbds);;Classic RAPTOR (*.rbd)")
            # The Save filter's active default matches the open file's
            # own extension. Plain Save keeps the open format; only
            # Save As deliberately suggests .rbds (its own selectFile
            # below).
            if str(self._current_file or "").lower().endswith(".rbd"):
                dlg.selectNameFilter("Classic RAPTOR (*.rbd)")
        return dlg

    def _close_current_file(self):
        """File ▸ Close: back to the no-model state (save prompt)."""
        if self._refuse_during_run("close the model"):
            return
        if not self._confirm_discard_unsaved(closing=True):
            return
        self._invalidate_step_session()
        # Reset the view's per-file state, then leave the canvas EMPTY. It
        # used to keep showing that placeholder model -- a diagram, a full
        # Explorer and "Model OK" over a ribbon with everything disabled.
        view = self._diagram_dock.view
        view.load_rbd(build_new_model("Closed"))
        view.clear_model()
        view.hiers.clear()
        self._bump_model_revision()      # no model: nothing to reuse
        self._rbd = None
        self._current_file = None
        self._dirty = False
        self._results.clear()
        self._results_show_action.setEnabled(False)
        self._results_float_action.setEnabled(False)
        self._sync_overlay_actions([])
        self._reset_status_summary()
        self._explorer.refresh()
        # The placeholder model above must not leave a Data Tables row
        # or a live Run panel behind with no model underneath.
        self._data_tables.set_view(None)
        self._run_panel.set_enabled(False)
        self._run_panel.set_units("", None)
        self._inspector.set_rbd(None)
        self._inspector.clear_selection()
        if hasattr(self, "_validation_badge"):
            self._validation_badge.setText("")
        self._refresh_commands()
        self._update_readiness()
        self._update_title()
        self._statusbar.showMessage("Model closed", 4000)
        self.show_start_page()

    def _quit_all(self):
        """Quit the application, not just the active window.

        Each window gets its normal close event (and dirty prompt). If a
        user cancels one, stop closing the rest and stay open.
        """
        from raptor.gui import open_models
        for win in list(open_models.windows()):
            if not win.close():
                return
        QApplication.quit()

    def _new_file(self):
        target = self._window_for_open()
        if target is not self:
            target._new_file()
            return
        if self._refuse_during_run("create a new model"):
            return
        if not self._confirm_discard_unsaved(closing=True):
            return
        # A new model is seeded from the Preferences dialog's "New models"
        # tab -- the time unit AND the starting experiment profile -- so
        # "what a new model starts with" is one setting the user controls
        # rather than a constant buried in a template string.
        try:
            rbd = build_new_model(
                "NewModel",
                units=settings.get("new_model/units", "hours"),
                profile=settings.get("experiments/last_used",
                                    run_experiment.DEFAULT_PROFILE))
        except Exception as e:
            msgbox.critical(self, "New Model Failed", f"Could not create a new model:\n{e}")
            return
        self._load_into_app(rbd, None)
        self._statusbar.showMessage("New model created — click Add Block, "
                                    "then the canvas, to build your diagram")

    # --- Preferences -------------------
    #
    # Thin compatibility shim over `ui/settings.py`. Kept because a
    # number of call sites read several keys at once, and because the
    # GUI's own dialogs/tests speak in terms of "the preferences". Every
    # value now lives under one namespaced key with a documented default
    # (see settings.DEFAULTS) rather than in a serialised blob plus an
    # orphan scalar.

    _PREFS_KEY = "preferences"   # legacy key, retained for tests

    def _prefs(self) -> dict:
        return {
            "units": settings.get("new_model/units", "hours"),
            "autosave": settings.get_bool("files/autosave"),
            "autosave_min": settings.get_int("files/autosave_min", 5),
            "exp_mode": settings.get("editing/exp_mode", "mean"),
            "ln_mode": settings.get("editing/ln_mode", "mean_sd"),
            # "never" | "ask" | "always"
            "save_before_run": settings.get("files/save_before_run",
                                            "ask"),
            # Sampling intervals are PER RUN, not application settings --
            # two models open side by side were fighting over these, and
            # they were in no file at all. They now live on the run panel
            # and default from the engine's own values.
            "ao_plot_interval": self._effective_run_interval("ao_interval"),
            "cost_plot_interval": self._effective_run_interval(
                "cost_interval"),
        }

    # --- Run experiment (the "Experiment" scope) ------------------------
    #
    # Trials, termination, statistics start, analyses and which plots to
    # record. Travels with the model, edited in the run panel, and
    # FROZEN into a manifest at run start so a result set can always
    # describe its own origin. See `raptor/core/run_experiment.py`.

    def _set_pref(self, key: str, value) -> None:
        """Write ONE preference, by its short name. The single write
        path for the preferences dialog, replacing the old
        write-the-whole-blob-at-once call that silently dropped
        `parallel_workers` (it was never in the blob)."""
        mapping = {
            "units": "new_model/units",
            "autosave": "files/autosave",
            "autosave_min": "files/autosave_min",
            "exp_mode": "editing/exp_mode",
            "ln_mode": "editing/ln_mode",
            "save_before_run": "files/save_before_run",
        }
        target = mapping.get(key)
        if target:
            settings.set_value(target, value)



    def _on_goto_hier(self, index: int):
        hid = self._goto_combo.itemData(index)
        if hid:
            self._diagram_dock.view.enter_level(int(hid))

    def _refresh_goto_combo(self):
        """(Re)build the Goto list from the view's parsed hierarchies,
        indented one step per nesting level (Raptor 7's tree)."""
        combo = self._goto_combo
        combo.blockSignals(True)
        combo.clear()
        v = self._diagram_dock.view
        hiers = getattr(v, "hiers", {}) or {}
        for hid in sorted(hiers, key=lambda h: (hiers[h].level,
                                                hiers[h].name.lower())):
            h = hiers[hid]
            combo.addItem(f"{'    ' * max(h.level - 1, 0)}{h.name}", hid)
        combo.setEnabled(bool(hiers))
        # Keep the combo in sync with where we are: on a hierarchy, that
        # one; at the top level, none (so the placeholder shows).
        combo.setCurrentIndex(-1)
        cur = getattr(v, "_current_parent", 0)
        if cur:
            i = combo.findData(cur)
            if i >= 0:
                combo.setCurrentIndex(i)
        combo.blockSignals(False)

    def _import_rbd(self):
        if self._rbd is None:
            return
        dlg = self._make_file_dialog("Import RBD as Hierarchy",
                                     QFileDialog.AcceptMode.AcceptOpen)
        if not dlg.exec() or not dlg.selectedFiles():
            return
        path = dlg.selectedFiles()[0]
        if not path:
            return
        try:
            hid = self._diagram_dock.view.import_rbd_as_hierarchy(path)
        except Exception as e:
            msgbox.critical(
                self, "Import Failed",
                f"Could not import {path}:\n\n{e}")
            return
        name = self._diagram_dock.view.hiers[hid].name
        self._statusbar.showMessage(
            f"Imported '{name}' as a hierarchy — link it in with the "
            f"Connect tool (its Start/End became the In/Out markers)")
        self._explorer.refresh()
        self._refresh_goto_combo()

    def _on_collapse_selection(self):
        if self._rbd is None:
            return
        hid = self._diagram_dock.view.collapse_selection()
        if hid is None:
            self._statusbar.showMessage(
                "Collapse: select at least one block, node, or event first")
        else:
            self._explorer.refresh()
            self._refresh_goto_combo()

    def _on_annotation_order(self, command: str):
        """Ribbon z-order button. The view owns the rule (what a command
        does to the current selection) and returns False when it had
        nothing to do; say so rather than leaving the press silent."""
        if not self._diagram_dock.view.set_annotation_order(command):
            self._statusbar.showMessage(
                "Select an annotation first — the z-order commands only "
                "apply to canvas annotations")
        self._sync_annotation_order_actions()

    def _sync_annotation_order_actions(self):
        """Enable each z-order button only when it would actually do
        something: there has to be a selected annotation, and a further
        step has to exist in that direction. Driven by the view's
        `selection_changed` signal so it tracks the canvas, the ribbon
        tab's lock state and undo/redo alike.

        Also gates Align & Distribute on a 2+ selection, which it needs
        and never had: it was enabled as soon as a model was open, and
        did nothing at all with zero or one item selected.
        """
        v = self._diagram_dock.view
        if getattr(self, "_rbd", None) is None:
            return
        for key, act in self._anno_order_actions.items():
            act.setEnabled(v.annotation_order_target(key) is not None)
        # Align and Mass Edit follow the selection through the same
        # derived state as every other managed command.
        # Selection changes cannot change whether the model validates, and validating loads the whole model, so
        # reuse the last answer (it was most of the lag when clicking elements).
        self._refresh_commands(
            self._command_state(reuse_validity=True),
            only={"can_align", "can_mass_edit", "can_zoom_hierarchy",
                  "can_extract_hierarchy"})

    def _on_tool_mode_changed(self, mode: str):
        """Keep the ribbon's tool buttons in step with the view.

        `_set_tool` is the user-facing path and already leaves the other
        tool buttons unchecked. This is the other direction: the VIEW
        disarming itself (Esc, or a one-shot annotation placement
        committing) has to clear the button, or it stays lit while the
        tool is spent and the next canvas click does something the user
        no longer asked for.
        """
        for action, m in self._tool_actions():
            if m != mode and action.isChecked():
                action.blockSignals(True)
                action.setChecked(False)
                action.blockSignals(False)

    def _tool_actions(self):
        """(action, tool-mode) for every mutually-exclusive build tool."""
        return ((self._add_block_tool, "add_block"),
                (self._add_node_tool, "add_node"),
                (self._add_event_tool, "add_event"),
                (self._add_hier_tool, "add_hierarchy"),
                (self._connect_tool, "connect"),
                (self._anno_text_tool, "anno_text"),
                (self._anno_rect_tool, "anno_rect"),
                (self._anno_ellipse_tool, "anno_ellipse"),
                (self._anno_arrow_tool, "anno_arrow"))

    def _set_tool(self, mode: str, on: bool):
        """Exclusive build-tool selection; 'select' is the fallback."""
        v = self._diagram_dock.view
        if not on:
            if v.tool_mode == mode:
                v.tool_mode = "select"
            return
        # `_on_tool_mode_changed` does the unchecking now that the view
        # broadcasts every mode change, so the two can never disagree
        # about which tool is armed.
        v.tool_mode = mode
        self._on_tool_mode_changed(mode)

    # ------------------------------------------------------------------
    # Busy indicator for known-slow, main-thread work (Open / New)
    # ------------------------------------------------------------------

    def _begin_busy(self, message: str) -> bool:
        """Show the modal "working…" indicator. True if this call created
        it (and so must close it).

        Deliberately a QProgressDialog with an indeterminate range: Qt
        animates its busy bar while events are processed, and its
        application modality blocks a second Open from landing in the
        middle of the first one's rebuild.
        """
        if getattr(self, "_busy_dialog", None) is not None:
            return False
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog(self)
        dlg.setWindowTitle("RAPTOR Studio")
        dlg.setLabelText(message)
        dlg.setRange(0, 0)
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.setMinimumWidth(380)
        dlg.show()
        self._busy_dialog = dlg
        QApplication.processEvents()
        return True

    def _end_busy(self, created: bool) -> None:
        if not created:
            return
        dlg = getattr(self, "_busy_dialog", None)
        self._busy_dialog = None
        if dlg is not None:
            dlg.close()
            dlg.deleteLater()
        QApplication.processEvents()

    def _load_into_app(self, rbd: RbdFile, path: str | None):
        """Put a freshly read model into the window, with an indicator.

        Reading the file is only part of an Open: rebuilding the canvas,
        the Explorer, the Inspector and the run mirrors, then validating
        the whole model, takes seconds on a large file. The window is
        single-threaded for all of it, so the previous behaviour was an
        unexplained white screen. The indicator is application-modal, so
        a click that lands during the rebuild cannot start a second Open.
        """
        self._loading_model = True
        busy = False
        try:
            busy = self._begin_busy(
                f"Opening {Path(path).name}…" if path else "Creating model…")
            self._apply_loaded_model(rbd, path)
        finally:
            self._loading_model = False
            self._end_busy(busy)

    def _apply_loaded_model(self, rbd: RbdFile, path: str | None):
        # Give the canvas dock priority height once the docks have real
        # layouts — without this, Results/Data Tables crush the diagram
        # to a ~316px strip and fit-zoom shrinks blocks to ant-scale.
        QTimer.singleShot(0, self._give_canvas_room)
        self._bump_model_revision()      # a different model entirely
        self._cancel_pending_block_selection()
        self._rbd = rbd
        # a stepped session belongs to the previous model -- stepping
        # after an Open must never touch the old engine context
        self._invalidate_step_session()
        self._current_file = path
        # Retention rule: a file opened as legacy .rbd keeps saving as
        # .rbd; new models and .rbds files default to .rbds.
        self._opened_legacy_format = bool(path) and \
            not str(path).lower().endswith(".rbds")
        self._undo_stack.setClean()
        self._dirty = False
        # units chip
        cap_on = False
        try:
            cfg = system_config_from_row(
                self._rbd.sections["SYSTEM_DATA"].rows[0])
            u = cfg.units
            cap_on = bool(cfg.capacity_analysis)
        except (ValueError, KeyError, IndexError, AttributeError):
            u = ""
        if hasattr(self, "_units_chip"):
            self._units_chip.setText(f" units: {u} " if u else "")
        try:
            self._results.set_y_axis_min(cfg.graph_ymin)
        except (NameError, AttributeError, UnboundLocalError):
            pass
        # Freeze the MTBDE/MDT readouts to the new units now: if the
        # frozen width only adapted at the first run's first update, the
        # run start would visibly reflow the bar.
        if hasattr(self, "_summary_labels"):
            self._refresh_summary_freeze(u)
        # Capacity plot availability follows the model's own Capacity
        # Grey the capacity mode out when the model can't produce
        # capacity data.
        if hasattr(self, "_results"):
            self._results.set_capacity_available(cap_on)
        if hasattr(self, "_run_to_label"):
            # "Run To" is a real sim-time target, so its label/entry
            # follow the same display-unit override as every other time
            # field, not just the model's native unit.
            shown_u = getattr(self._rbd, "display_unit", None) or u
            self._run_to_label.setText(
                f"{shown_u.capitalize()}:" if shown_u else "Time:")
        v = self._diagram_dock.view
        defaults = self._block_defaults()
        v.default_block_fdist = defaults["fdist"]
        v.default_block_fparams = defaults["fparams"]
        v.default_block_rdist = defaults["rdist"]
        v.default_block_rparams = defaults["rparams"]
        v.default_block_sparing = defaults["sparing"]
        v.load_rbd(rbd)
        # The canvas rebuild is the one long block the indicator can be
        # repainted after: without a yield it stays as the first frame
        # (or, on some platforms, an unpainted window) until the end.
        QApplication.processEvents()
        self._sync_block_label_controls()
        self._update_overlay_chip()
        self._push_hierarchy_options()
        v.model_changed.connect(self._schedule_validation)
        # The timer exists from the first load, with both of its slots
        # wired, even though the load validates explicitly at its end.
        self._ensure_validation_timer()
        self._sync_analysis_checkboxes()
        self._explorer.set_view(v)
        self._data_tables.set_view(v)
        # The Data Tables dock's own Failure/Repair Mean/Std Dev/PM
        # Frequency columns are sim-time quantities, so they carry the
        # model's configured unit (results.py's equivalent columns
        # already append it).
        cfg_sec = rbd.sections.get("SYSTEM_DATA")
        self._data_tables.set_units(
            system_config_from_row(cfg_sec.rows[0]).units
            if cfg_sec and cfg_sec.rows else "")
        self._inspector.set_rbd(rbd)
        self._inspector.clear_selection()
        self._results.clear()
        # A new model has no result data: clear the old model's canvas
        # overlay and uncheck its button. Otherwise Weak Links stayed
        # "on" over a diagram that painted nothing.
        self._sync_overlay_actions([])
        # Opening a file clears the old model's results: the Results
        # dock's clear() above empties the cards/tables, but the status
        # bar's cross-run readouts, live system swatch, run-progress
        # widgets and phase/mission panels are all owned here and would
        # otherwise survive an Open as stale numbers from the previous
        # model. Reset the whole set.
        self._reset_status_summary()
        self._update_system_swatch(None)
        self._hide_phase_labels()
        self._run_progress.setVisible(False)
        self._run_progress_label.setVisible(False)
        # Availability of every "needs a model" command follows ONE rule
        # (`enabled_when` in command_table.py). It used to be ~50
        # hand-written lines here, mirrored by a shorter list in
        # `_close_current_file` -- which is how 37 commands stayed enabled
        # after File > Close.
        self._refresh_commands()
        # _align_btn is NOT force-enabled here: it needs a 2+ selection,
        # so it comes up disabled and `_sync_annotation_order_actions`
        # (via selection_changed) enables it the moment that holds.
        #
        # One whole-model validation, inside the load indicator. The
        # debounced timer started above used to be the FIRST of four
        # passes this load cost (the command refresh, the readiness line
        # and the badge each asked again); now they all read this one
        # answer, and the timer's later fire is a cache hit. It runs
        # BEFORE the command refresh below so that refresh sees the real
        # answer, not the "still loading" placeholder.
        self._validate_model(quiet=True)
        self._sync_simulate_enabled_state()
        self._populate_breakpoints()
        self._refresh_goto_combo()
        self._find_combo_refresh()
        # Seed the run experiment and every mirror from the freshly
        # loaded model. Before that, carry an upgrading user's
        # `reports/*` recording choices onto it, once -- see
        # `_migrate_report_stream_settings` for why this is a silent
        # regression if it is missing.
        self._migrate_report_stream_settings()
        # `_current_experiment` derives one from the
        # SYSTEM_DATA columns when the file has no experiment block (a
        # legacy .rbd, or a .rbds written before the block existed), so
        # a loaded model never silently reports defaults that contradict
        # its own row.
        self._sync_run_mirrors()
        self._sync_run_panel_units()
        self._update_readiness()
        name = Path(path).name if path else "NewModel"
        nb, nn, ne, nl = self._model_totals()
        msg = (f"Loaded: {name} — "
               f"{nb} blocks, {nn} nodes, {ne} events, {nl} links")
        self._statusbar.showMessage(msg)
        self._update_title()

    def _model_totals(self) -> tuple[int, int, int, int]:
        """Whole-model counts, not the current hierarchy level.

        The status bar used the VIEW's dicts, which only hold the level
        on screen; a model with hierarchy levels was reported as
        "8 blocks, 8 nodes" while its own Inspector said 17/18.
        """
        if self._rbd is None:
            return (0, 0, 0, 0)

        def n(section: str, skip_default_block: bool = False) -> int:
            sec = self._rbd.sections.get(section)
            if sec is None:
                return 0
            if not skip_default_block:
                return len(sec.rows)
            return sum(1 for r in sec.rows
                       if str(r.get("ID", "")).strip() not in
                       ("0", "0.0", "0.000000"))
        return (n("BLOCK_DATA", True), n("NODE_DATA"),
                n("EVENT_DATA"), n("LINK_DATA"))

    def _open_file(self):
        # No unsaved-changes question here: `_open_path` asks it once a
        # file is actually chosen. Asking first as well put the question
        # twice, and before the user had even picked a file.
        dlg = self._make_file_dialog("Open RBD File", QFileDialog.AcceptMode.AcceptOpen)
        if not dlg.exec() or not dlg.selectedFiles():
            return
        path = dlg.selectedFiles()[0]
        if not path:
            return
        self._open_path(path)

    def _write_model(self, path: str) -> bool:
        """Write the model in the format its EXTENSION asks for
        (.rbds JSON vs legacy .rbd). One place so every save path
        behaves identically. Returns False if the user CANCELLED at a
        legacy-format data-loss confirmation (rates/formulas -- see
        below) -- callers must not treat that as a completed save
        (clearing the dirty flag, updating the title, ...)."""
        if getattr(self._rbd, "protected_file", False):
            msgbox.warning(
                self, "Protected File",
                "This model is marked as overwrite-protected: it cannot be "
                "saved. Copy "
                "the file outside the app if you need an editable copy.")
            return False
        self._diagram_dock.view.write_back_to_rbd(self._rbd)
        # Resync annotation geometry/text/props (and deletions --
        # delete_selected_annotations reassigns view.annotations to a
        # fresh list rather than mutating in place, which breaks its
        # aliasing with self._rbd.annotations) into the model before
        # serializing, so deleted/edited annotations don't survive in
        # the saved file.
        self._diagram_dock.view.write_back_annotations()
        if str(path).lower().endswith(".rbds"):
            # Saving can re-lay out a crammed model, on the UI thread.
            with refine_budget(GUI_REFINE_BUDGET_S):
                rbds_io.save_rbds(req(self._rbd), path)
            return True
        n_ann = len(getattr(self._rbd, "annotations", []) or [])
        if n_ann:
            msgbox.warning(
                self, "Annotations not supported in the .rbd format",
                f"{n_ann} annotation(s) (text/shapes) cannot be "
                f"stored in the .rbd format and will be "
                f"DROPPED. Save as .rbds to keep them.")
        if getattr(self._rbd, "display_unit", None):
            msgbox.warning(
                self, "Display unit not supported in the .rbd format",
                "The chosen display unit (" + str(req(self._rbd).display_unit) +
                ") cannot be stored in the .rbd format and will "
                "be DROPPED -- reopening this .rbd file will show "
                "values in the model's own native unit again. The "
                "underlying numbers are unaffected either way. Save as "
                ".rbds to keep the preference.")
        if getattr(self._rbd, "project_meta", None):
            msgbox.warning(
                self, "Print title block not supported in the .rbd format",
                "The print title block (project / revision / date) "
                "cannot be stored in the .rbd format and will be "
                "DROPPED. Printouts from this file will fall back to the "
                "file's own name. Save as .rbds to keep it.")
        # Rates/formulas: unlike annotations, the field's own numeric
        # value is already a plain float in `sections` regardless --
        # what legacy can't hold is the formula/rate record explaining
        # where that number came from. Ask with a blocking Yes/No rather
        # than a fire-and-forget warning, since declining cancels the
        # save outright instead of silently dropping the basis.
        n_forms = len(getattr(self._rbd, "formulas", {}) or {})
        n_rates = len(getattr(self._rbd, "rates", []) or [])
        if n_forms or n_rates:
            parts = []
            if n_forms:
                parts.append(f"{n_forms} field formula(s)/rate link(s)")
            if n_rates:
                parts.append(f"{n_rates} saved rate(s)")
            from raptor.gui.ui.dialogs import question_box
            if not question_box(
                    self, "Formulas not supported in the .rbd format",
                    "The .rbd format has no way to store "
                    + " and ".join(parts) + " -- only the plain computed "
                    "numbers they produced. Continuing will save those "
                    "plain values and PERMANENTLY drop the formulas/rates "
                    "themselves (this model's own copy is untouched; "
                    "only the .rbd FILE loses them). Save as .rbds instead to "
                    "keep them.\n\nContinue and save plain values only?",
                    yes_text="Continue, save plain values",
                    no_text="Cancel"):
                return False
        with refine_budget(GUI_REFINE_BUDGET_S):
            save_rbd(req(self._rbd), path)
        return True

    def _open_model_any(self, path: str):
        """Read .rbds or legacy .rbd by extension."""
        if str(path).lower().endswith(".rbds"):
            return rbds_io.load_rbds(path)
        return load_rbd(path)

    def _save_file(self):
        if self._rbd is None:
            return
        if self._current_file is None:
            # New, never-saved model: Save means "choose a location".
            self._save_file_as()
            return
        try:
            if not self._write_model(self._current_file):
                self._statusbar.showMessage("Save cancelled")
                return
            self._undo_stack.setClean()
            self._dirty = False
            self._update_title()
            self._statusbar.showMessage(f"Saved: {Path(self._current_file).name}")
        except Exception as e:
            msgbox.critical(self, "Save Failed", f"Failed to save:\n{e}")

    def _untitled_save_path(self) -> str:
        """Where Save As starts for a model with no file: its title (or
        "Untitled") in the folder of the most recent file, else Documents."""
        import re
        meta = getattr(self._rbd, "project_meta", None) or {}
        name = re.sub(r'[\\/:*?"<>|]+', "_", meta.get("name") or "") \
            or "Untitled"
        recent = [p for p in settings.get_list("files/recent") if p]
        folder = (os.path.dirname(recent[0]) if recent
                  else os.path.join(os.path.expanduser("~"), "Documents"))
        if not os.path.isdir(folder):
            folder = os.path.expanduser("~")
        return os.path.join(folder, name + ".rbds")

    def _save_file_as(self):
        if self._rbd is None:
            return
        dlg = self._make_file_dialog("Save RBD File As", QFileDialog.AcceptMode.AcceptSave)
        # Save As suggests .rbds even for legacy files, nudging users to
        # the native format; plain Save keeps the file's own format
        # unchanged.
        suggested = self._current_file or self._untitled_save_path()
        stem = suggested.rsplit(".", 1)[0] if "." in suggested \
            else suggested
        suggested = stem + ".rbds"
        dlg.selectFile(suggested)
        if not dlg.exec() or not dlg.selectedFiles():
            return
        path = dlg.selectedFiles()[0]
        if not path:
            return
        if not str(path).lower().endswith((".rbds", ".rbd")):
            path += ".rbds"     # no extension -> new default format
        from raptor.gui import open_models
        other = open_models.window_for_file(path)
        if other is not None and other is not self:
            msgbox.warning(
                self, "File Already Open",
                f"{Path(path).name} is open in another RAPTOR Studio "
                "window. Close that window or save to a different file.")
            return
        try:
            if not self._write_model(path):
                self._statusbar.showMessage("Save cancelled")
                return
        except Exception as e:
            msgbox.critical(self, "Save Failed", f"Failed to save:\n{e}")
            return
        self._current_file = path
        self._undo_stack.setClean()
        self._dirty = False
        # A model saved for the first time now has a file: Save becomes
        # available.
        self._refresh_commands()
        self._update_title()
        self._remember_recent(path)
        self._statusbar.showMessage(f"Saved: {Path(path).name}")

    def _open_report_streams(self) -> dict:
        """Create the disk-backed ResultStreams for this batch's ticked
        Files & Reports checkboxes (None = the sink is not collected and
        is not handed to the engine at all). Closes the previous batch's
        temp files first: plot rows must never accumulate in RAM across
        runs."""
        from raptor.gui.ui.result_streams import ResultStream
        self._close_report_streams()
        streams: dict = {}
        acts = self._report_stream_actions
        if acts["ao_plot"].isChecked():
            # The engine writes its own file header once on first sight
            # of an empty sink (`if _ao_sink is not None and not _ao_-
            # sink`) — ResultStream's list-like truthiness keeps that
            # working, so no GUI-side header here.
            streams["ao"] = ResultStream("ao")
        # The Capacity and Cost sinks are DEPENDENT on their analysis
        # flags, and the dependency used to live only in tooltip prose
        # ("Requires Capacity Analysis" / "Requires Cost Analysis") in a
        # group 600px away from the toggles that set it. Ticking the plot
        # on a model without the analysis handed the engine a sink that
        # nothing could ever write to, and the user got an empty plot
        # and no message.
        #
        # Enforced HERE, in the command layer, rather than only in the
        # widget: a legacy .rbd import, a profile load, or any other
        # programmatic route to the flag can set the plot without going
        # through the checkbox, and a widget-only gate would be bypassed.
        # When it bites we say so, once, in the status bar, naming what to
        # turn on.
        analyses = self._analysis_flags()
        for key, flag, label in (
                ("capacity_plot", "capacityAnalysis", "Capacity"),
                ("cost_plot", "costAnalysis", "Cost")):
            if not acts[key].isChecked():
                continue
            if analyses.get(flag):
                streams["capacity" if key == "capacity_plot" else "cost"] \
                    = ResultStream(
                        "capacity" if key == "capacity_plot" else "cost")
            else:
                # The run is already active here, so this untick would
                # look like a user edit to the edit-during-run guard and
                # prompt to cancel the very run that made it.
                self._run_own_change = True
                try:
                    acts[key].setChecked(False)
                finally:
                    self._run_own_change = False
                self._statusbar.showMessage(
                    f"{label} plot not collected — turn on "
                    f"Analyses ▸ {label} to record it", 8000)
        return streams

    def _analysis_flags(self) -> dict:
        """The three SYSTEM_DATA analysis flags as booleans, read
        straight from the row. Empty dict when no model is open, so
        callers treat every dependent feature as unavailable rather than
        assuming it is on."""
        if self._rbd is None:
            return {}
        sec = self._rbd.sections.get("SYSTEM_DATA")
        if not sec or not sec.rows:
            return {}
        row = sec.rows[0]

        def flag(col: str) -> bool:
            v = str(row.get(col, "")).strip().upper()
            return v.startswith(("1", "T", "TRUE"))
        return {"weakAnalysis": flag("weakAnalysis"),
                "costAnalysis": flag("costAnalysis"),
                "capacityAnalysis": flag("capacityAnalysis")}

    def _remember_report_streams(self, streams: dict) -> None:
        """Adopt this batch's streams (the Results dock reads them lazily
        from disk); they stay alive until the NEXT batch opens them."""
        self._active_report_streams = [s for s in streams.values()
                                       if s is not None]

    def _close_report_streams(self) -> None:
        for s in getattr(self, "_active_report_streams", None) or []:
            s.close()
        self._active_report_streams = []

    # -- window geometry ----------------------------------------------
    #
    # The window used to open at a hard-coded 1600x1000 every time: larger
    # than a 1366x768 laptop screen, and it forgot where the user had put
    # it. Geometry is remembered; the DOCK layout is not, deliberately:
    # the docks' custom minimize / closed-stub chrome works by pinning
    # fixed widths, which `saveState()` does not capture, so restoring it
    # blindly could bring a dock back at stub width with its content
    # showing. That needs those states serialised first.

    _DEFAULT_WINDOW_SIZE = (1600, 1000)

    def _initial_window_size(self):
        """1600x1000, unless the screen cannot hold that.

        A screen below 1024x640 is not a supported size, and the headless
        test platform reports 800x600, so those keep the default rather
        than shrinking every window a test builds."""
        from PySide6.QtCore import QSize
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

    _LAYOUT_VERSION = 1

    def _restore_saved_layout(self, blob) -> None:
        """Apply the startup layout once, and forget it if it is unusable.

        A rejected blob used to stay in settings, so every launch paid for
        the same failed restore and logged the same warning. Removing it
        makes the next start fall back cleanly to the default layout.
        """
        if not self._apply_layout_blob(blob):
            settings.remove("layout/docks")

    def _layout_blob(self) -> dict:
        """The dock layout as plain data: Qt's `saveState()` plus each slot's chrome (closed / minimized)."""
        import base64
        chrome = {}
        for title, c in (self._dock_chrome or {}).items():
            state = dict(c["get"]())
            dock = c.get("dock")
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
        for title, c in (self._dock_chrome or {}).items():
            want = chrome.get(title)
            if not isinstance(want, dict):
                continue
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
            self._give_canvas_room()
            self._sync_dock_tab_bars()
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

    def closeEvent(self, event):
        """Confirm unsaved changes before closing."""
        if getattr(self, "_export_in_progress", False):
            self._statusbar.showMessage(
                "Wait for the export to finish before closing", 6000)
            event.ignore()
            return
        if getattr(self, "_run_in_progress", False) and \
                not self._stop_run_for_close():
            event.ignore()
            return
        if self._confirm_discard_unsaved(closing=True):
            # `_tooltip_keeper` is a plain QTimer poller (no app-wide
            # event filter, see its docstring), parented to this window:
            # it dies with it, so no teardown is needed here.
            self._tooltip_keeper._timer.stop()
            # A pending layout/selection callback must not run against a
            # window whose C++ side is about to go (that is a hard Qt
            # abort, not a Python exception, if it lands after teardown).
            self._canvas_room_timer.stop()
            self._inspector_select_timer.stop()
            self._close_report_streams()
            # Clean exit: drop the crash-recovery copy so it isn't
            # offered as a "crash" on the next launch.
            self._clear_recovery()
            self._save_layout()
            # The Help viewer is a top-level window of its own (so a modal dialog cannot block it); it must not
            # outlive the application.
            if getattr(self, "_help_viewer", None) is not None:
                self._help_viewer.close()
            from raptor.gui import open_models
            open_models.unregister(self)
            event.accept()
        else:
            event.ignore()

    def _stop_run_for_close(self) -> bool:
        """Closing during a run. A background run works on its own copy of
        the model in a worker thread: ask, stop it, wait for the worker,
        then let the close go on. An animated run is on this thread (its
        frame holds live canvas objects), so it has to be stopped first;
        say so plainly. Closing used to be refused with only a status-bar
        note, which read as the window ignoring the click (and in Qt 6 it
        also cancels Quit)."""
        from raptor.gui.ui.dialogs import question_box
        bg_state = getattr(self, "_bg_state", None) or {}
        if not bg_state or bg_state.get("done"):
            msgbox.information(
                self, "Simulation running",
                "An animated run is in progress. Stop it (Simulate > Stop, "
                "or Esc), then close.")
            return False
        if not question_box(self, "Simulation running",
                            "A simulation is running in the background. "
                            "Stop it and close? Its results so far are not "
                            "kept.", yes_text="Stop and close",
                            no_text="Keep running"):
            return False
        self._stop_bg_run()
        thread = getattr(self, "_bg_thread", None)
        deadline = time.monotonic() + 15.0
        while thread is not None and thread.is_alive() \
                and time.monotonic() < deadline:
            QApplication.processEvents()
            thread.join(0.05)
        self._run_in_progress = False
        return True

    def _model_for_engine(self, copy: bool = False):
        """The model as the user sees it, for the engine.

        Edits made on the canvas or in the Inspector live in the view's
        element objects until something writes them back to the file rows
        -- and the engine reads the rows. Nothing did before a run, so the
        first run after an edit simulated the PREVIOUS model (a PM interval
        changed to 300 ran at 400; the next run was right). Every engine
        entry point takes its model from here.

        `copy=True` (the background run) hands the engine a private copy: a
        worker thread reading the live model raced any edit made while it
        ran and failed mid-run on a half-edited diagram."""
        import copy as _copy
        rbd = req(self._rbd)
        view = self._diagram_dock.view
        view.flush_pending_moves()     # before the run, not during it
        view.write_back_to_rbd(rbd)
        return _copy.deepcopy(rbd) if copy else rbd

    def _open_diff_dialog(self, other_path: str | None = None,
                          open_model=None):
        """Model > Differences: this model against its saved file, another
        model file, or a model open in another window
        (`ui/diff_dialog.py`)."""
        from raptor.gui.ui.diff_dialog import open_diff
        return open_diff(self, other_path, open_model)

    def _open_sweep_dialog(self):
        """Simulate > Batch > Sweep: one number over a range, at full speed."""
        return self._open_batch_dialog("_sweep_dialog", "SweepDialog")

    def _open_compare_dialog(self):
        """Simulate > Batch > Compare: this model beside other model files."""
        return self._open_batch_dialog("_compare_dialog", "CompareDialog")

    def _open_batch_dialog(self, attr: str, cls_name: str):
        # One window of each kind. It is kept -- with its results and
        # inputs -- unless the model has changed since it was built (another
        # file, or an edit), when its element list would be stale. A
        # running one is always kept.
        from raptor.gui.ui import batch_dialogs
        dlg = getattr(self, attr, None)
        key = (id(self._rbd), self._undo_stack.index(), self._current_file)
        running = dlg is not None and dlg._job is not None \
            and dlg._job.is_running()
        if dlg is not None and not running and dlg._model_key != key:
            dlg.close()
            dlg.deleteLater()
            dlg = None
        if dlg is None:
            self._model_for_engine()          # the rows match the canvas
            dlg = getattr(batch_dialogs, cls_name)(self)
            dlg._model_key = key
            setattr(self, attr, dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _on_run(self):
        """Run (F5). Animate on: the one-core run you watch, here on the
        GUI thread with every event drawn. Animate off: full speed, in the
        background on the cores in the Cores box (Pause, Stop and Jump all
        work). There is no separate "Run in Background" switch any more:
        whether a run is in the background follows from how it runs."""
        self._run_full_speed = not self._live_sim_action.isChecked()
        try:
            self._run_simulation()
        finally:
            self._run_full_speed = False

    def _start_run_eta(self) -> None:
        """Begin timing a batch for the "approximately N left" readout."""
        from raptor.gui.ui.run_eta import RunEstimate
        self._run_eta = RunEstimate(
            extra_batches=self._importance_extra_batches())
        self._bg_last_paused = False

    def _importance_extra_batches(self) -> int:
        """Batches importance measures add after the baseline (0 when off)."""
        try:
            mode = str(self._current_experiment().get("importance", "none")
                       or "none")
            if mode in ("", "none") or self._rbd is None:
                return 0
            from raptor.api import importance as _imp
            return max(0, _imp.batch_count(
                len(_imp.block_rows(self._rbd)), mode) - 1)
        except (ImportError, KeyError, ValueError, TypeError, AttributeError):
            return 0

    def _note_run_profile(self, done_runs: int) -> None:
        """Remember how long this batch actually took, for the next
        long-run confirmation's order-of-magnitude reference."""
        est = getattr(self, "_run_eta", None)
        if est is None or done_runs <= 0:
            return
        try:
            horizon = float(self._current_experiment().get("term_condition")
                            or 0.0)
        except (TypeError, ValueError, AttributeError):
            return
        if horizon <= 0:
            return
        from raptor.gui import cores
        batches = 1
        try:
            mode = str(self._current_experiment().get("importance", "none")
                       or "none")
            if mode not in ("", "none") and self._rbd is not None:
                from raptor.api import importance as _imp
                batches = _imp.batch_count(
                    len(_imp.block_rows(self._rbd)), mode)
        except (ImportError, KeyError, ValueError, TypeError):
            batches = 1
        self._last_run_profile = (horizon, int(done_runs),
                                  float(est.active_seconds()),
                                  int(cores.chosen()), int(batches))

    def _run_eta_text(self, done, total) -> str:
        """The estimate sentence for a run status message ("" while the
        average is not yet meaningful, and once the batch is finished)."""
        est = getattr(self, "_run_eta", None)
        if est is None:
            return ""
        return est.text(done, total) or ""

    def _run_status_text(self, prefix: str, done, total) -> str:
        """`prefix`, plus the estimate when there is one."""
        eta = self._run_eta_text(done, total)
        return f"{prefix} · {eta}" if eta else prefix

    def _clear_run_eta(self) -> None:
        self._run_eta = None

    def _track_paused_eta(self, paused: bool) -> None:
        """Paused time must not inflate the remaining estimate (a long
        pause would otherwise claim the batch is slower than it is)."""
        est = getattr(self, "_run_eta", None)
        if est is None:
            return
        was = bool(getattr(self, "_bg_last_paused", False))
        if paused and not was:
            est.pause()
        elif not paused and was:
            est.resume()
        self._bg_last_paused = paused

    def _update_importance_progress(self, done, total, label="") -> None:
        """Show importance progress on the run progress bar and in the bar.

        The same widgets the simulation uses: with Animate off (the default)
        there is no canvas activity at all, so without this a run that then
        spends minutes on variants looks hung.
        """
        total = max(int(total), 1)
        pct = 100 * int(done) // total
        self._run_progress.setVisible(True)
        self._run_progress.setValue(pct)
        self._run_progress_label.setVisible(True)
        self._run_progress_label.setText(f"{pct}%")
        self._clear_run_eta()      # the baseline batch is over
        # `done` counts batches finished with the baseline as the first, so
        # the variants finished are done - 1 of total - 1. Timed from the
        # first report: the estimate was dropped here before, which left the
        # slowest part of a run (one batch per block per measure) with no
        # time at all.
        imp = getattr(self, "_imp_eta", None)
        if imp is None:
            from raptor.gui.ui.run_eta import RunEstimate
            imp = RunEstimate()
            self._imp_eta: RunEstimate | None = imp
        eta = imp.text(int(done) - 1, int(total) - 1)
        detail = f" [{label}]" if label else ""
        self._statusbar.showMessage(
            f"Importance measures — variant {int(done)} of {int(total)}"
            f"{detail} ({pct}%)" + (f" · {eta}" if eta else ""), 0)
        QApplication.processEvents()

    def _end_importance_progress(self, message: str) -> None:
        self._imp_eta = None
        self._run_progress.setVisible(False)
        self._run_progress_label.setVisible(False)
        self._statusbar.showMessage(message, 8000)

    def _apply_importance_progress(self, state) -> None:
        """The background worker's importance progress, applied on the GUI
        thread. Kept as its own method so the poll path is testable without
        standing up a whole worker run."""
        progress = (state or {}).get("importance_progress")
        if progress:
            self._update_importance_progress(*progress)

    def _importance_for(self, run_model, report_data):
        """Importance measures for a finished batch, or None.

        Called by BOTH run paths. Only the background worker had it wired,
        so ticking the box and pressing Run (the synchronous default) did
        nothing at all -- the setting was silently dropped. Returns None
        when the experiment did not ask for it, and records a note rather
        than losing the batch if the analysis itself fails.
        """
        mode = str((self._current_experiment() or {}).get("importance",
                                                          "none"))
        if mode in ("none", "") or not report_data:
            return None
        from raptor.api import importance as _imp
        batches = _imp.batch_count(len(_imp.block_rows(run_model)), mode)
        self._update_importance_progress(1, batches, "starting")
        try:
            data = _imp.analyse(
                run_model, report_data, mode=mode, runs=len(report_data),
                workers=max(1, int(
                    self._current_experiment().get("workers", 1) or 1)),
                progress=self._update_importance_progress,
                # Stop must stop this too. It is the same button and the same
                # wait; without it an "aborted" run ground through every
                # remaining batch with no way out.
                stop_check=lambda: bool(getattr(self, "_sync_stop", False)))
        except RuntimeError:
            self._end_importance_progress(
                "Importance measures cancelled — the simulation results are "
                "unaffected")
            return None
        except Exception as exc:            # noqa: BLE001
            self._end_importance_progress(f"Importance measures failed: {exc}")
            return None
        self._end_importance_progress(
            f"Importance measures complete — Results ▸ Final Results ▸ "
            f"Importance ({batches} batches)")
        return data

    def _confirm_long_batch(self, num_runs: int) -> bool:
        """Ask before a batch that is likely to run for tens of minutes.

        The default `ProjectX`-class model is 10 trials x 175,200 h; at four
        cores that is roughly an hour, and the old UI only said "Ready".
        This is deliberately a coarse run-hours guard, not a pilot: a pilot
        on such a model costs as much as the short run it would protect.
        A previous run's measured wall time, when one is known, is shown as
        a scale reference.
        """
        from raptor.gui import cores
        try:
            experiment = self._current_experiment()
            horizon = float(experiment.get("term_condition") or 0.0)
        except (TypeError, ValueError, AttributeError):
            return True
        if horizon <= 0:
            return True
        # Importance measures are extra batches after the baseline; the
        # time until completion includes them, so the guard must too.
        importance_batches = 0
        importance_mode = "none"
        try:
            exp_mode = str(experiment.get("importance", "none") or "none")
            if exp_mode not in ("", "none") and self._rbd is not None:
                from raptor.api import importance as _imp
                importance_mode = exp_mode
                importance_batches = max(
                    0, _imp.batch_count(
                        len(_imp.block_rows(self._rbd)), exp_mode) - 1)
        except (ImportError, KeyError, ValueError, TypeError):
            importance_batches = 0
        batches = 1 + importance_batches
        run_hours = horizon * max(1, int(num_runs)) * batches
        if run_hours < 50_000:
            return True
        previous = getattr(self, "_last_run_profile", None)
        reference = ""
        if previous:
            try:
                ph, pn, pwall, pworkers, pbatches = previous
            except ValueError:
                ph, pn, pwall, pworkers = previous
                pbatches = 1
            if ph and pn and pwall:
                scale = ((horizon / ph) * (num_runs / pn)
                         * (batches / max(1, pbatches))
                         * (max(1, pworkers) / max(1, cores.chosen())))
                reference = (
                    f"\n\nA previous {pn} × {ph:g} h run took "
                    f"~{pwall:.0f} s; at this size expect roughly "
                    f"{pwall * scale / 60:.0f} min (order of magnitude).")
        extra = (f"\n\nImportance measures are on: {importance_mode}, "
                 f"{importance_batches} extra batch(es), {batches} total. "
                 f"The time until completion includes them."
                 if importance_batches else "")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Long simulation")
        box.setText(
            f"This run is {num_runs} trial(s) × {horizon:,.0f} hours "
            f"({run_hours:,.0f} run-hours including every batch)."
            f"{extra}{reference}\n\n"
            "For an interactive check, lower Run length or Trials. "
            "Run the full operation anyway?")
        run_btn = box.addButton("Run anyway", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is run_btn

    def _run_simulation(self):
        # Synchronous/blocking: the engine runs on the GUI thread, and
        # the progress/event callbacks below pump the event loop so the
        # window stays responsive and Stop can land mid-run.
        if self._rbd is None:
            return
        # A spin box commits on editingFinished, which F5 does not cause.
        # Clear focus so the number on screen is the number the run uses.
        focused = QApplication.focusWidget()
        if focused is not None:
            focused.clearFocus()
            QApplication.processEvents()
        # Failure Effects View holds live engine objects of its own. A
        # new run supersedes it; leave it explicitly instead of making
        # Run unavailable because a toggle on another tab is on.
        if getattr(self, "_fev", None) is not None:
            self._leave_fev()
            self._statusbar.showMessage(
                "Left Failure Effects View to start the run", 5000)
        if not self._maybe_save_before_run():
            self._statusbar.showMessage("Run cancelled")
            return
        run_model = self._model_for_engine()
        # Read the experiment, not the ribbon mirror: the mirror can be
        # stale after undo/redo or an external model edit, and the next
        # run must use the same value the panel shows and the manifest
        # records.
        num_runs = int(self._current_experiment()["trials"])
        self._write_dev_trees()
        from raptor.core import diagnostics as _dg
        self._diag(*_dg.pre_sim_lines(), _dg.start_simulation_line())
        mission_path = self._ask_mission_path()
        cumulative_trial_path = self._ask_cumulative_trial_path()
        cumulative_time_path = self._ask_cumulative_time_path()
        self._invalidate_step_session()
        # A new run resets the edit-during-run acknowledgement (the
        # previous run's "cancel" choice must not silence this run's
        # own guard).
        self._edit_run_abandoned = False
        # Warn before the run about features the engine will reject
        # mid-run with NotImplementedError.
        preflight = self._preflight_issues()
        if preflight:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Unsupported Model Features")
            box.setText(
                "This model uses features the simulation engine doesn't "
                "support yet:")
            box.setDetailedText("\n".join(preflight))
            box.setText("\n".join(preflight[:6])
                        + ("\n…" if len(preflight) > 6 else ""))
            run_btn = box.addButton("Run Anyway", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not run_btn:
                self._statusbar.showMessage("Run cancelled — unsupported "
                                            "features present")
                return
        if getattr(self, "_run_full_speed", False):
            if not self._confirm_long_batch(num_runs):
                return
            self._start_background_run(num_runs)
            return
        if not self._confirm_long_batch(num_runs):
            return
        self._statusbar.showMessage(f"Running {num_runs} run(s)...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        # Units are needed by the per-run status refresh, so parse them
        # before the batch starts.
        units = None
        try:
            cfg_section = self._rbd.sections.get("SYSTEM_DATA")
            if cfg_section and cfg_section.rows:
                units = system_config_from_row(cfg_section.rows[0]).units
        except (ValueError, KeyError):
            pass
        # Keep the UI responsive during the synchronous batch: the
        # engine's progress hook fires after every completed run, and the
        # loop is pumped there; without it, long runs would freeze the
        # window into "Not Responding". Re-entrancy guarded by disabling
        # Run for the duration.
        self._run_in_progress = True
        self._refresh_commands()
        self._disable_run_panel_for_run()
        # Fresh run: wipe stale results so old numbers can't be mistaken
        # for the new batch.
        self._results.clear()
        # The live-frame fingerprint belongs to the previous run (a
        # stopped run left its last state in it).
        self._live_sig = None
        # A new run invalidates the old Weak Link labels, including on a
        # Start/End node where they would collide with the live capacity
        # readout.
        self._diagram_dock.view.clear_result_overlays()
        self._reset_status_summary()
        self._start_run_eta()
        # ...and the stepping Live Log dock.
        self._reset_live_log()
        # Sync runs pump the event loop between runs, so a Stop press
        # can land: flag checked in _progress, engine aborts cleanly.
        self._sync_stop = False
        runs_done = [0]
        live_logs: list[list[str]] = []
        status_updates = True   # readouts update per-run, always
        live_view = self._live_sim_action.isChecked()

        # Live intra-run view (Raptor 7's Simulation View behaviour):
        # every processed event re-colours the blocks by their current
        # state (manual Appendix D colours). Throttled to ~30 UI
        # updates/second so huge models don't drown in repaints.
        # Note: the status bar does not show within-run values -- Raptor 7
        #reads cross-run monitors there, refreshed as each run
        # finishes, so the averages update per run via _progress below,
        # not per event.
        import time as _time
        last_paint = [_time.monotonic()]

        # Long single runs (or a big model generating lots of events)
        # would freeze the window for the run's entire duration if only
        # `_progress` (fired between whole runs) pumped the event loop,
        # leaving paint/input/OS keepalive pings blocked while a run was
        # actually in progress. Both event callbacks below pump the loop
        # themselves, throttled so the extra per-event cost stays
        # negligible next to the engine work; this also lets a Stop press
        # land mid-run instead of only between runs.
        # There is one status-bar text format, always both the run number
        # and the current time: while Live Sim is on, _on_event is the
        # sole writer (it is far more frequent and has the live time
        # _progress never does) -- _progress only writes text when Live
        # Sim is off, the one case _on_event never fires.
        def _on_event(scheduler, system_state, blocks, cap_data=None,
                      model=None):
            now = _time.monotonic()
            if now - last_paint[0] < 1 / 30:
                return
            last_paint[0] = now
            # System-status square in the status bar: the live
            # SystemColor straight from the running cascade.
            self._update_system_swatch(getattr(system_state, "color", None))
            # Raptor 7's status panels 2/3/4: cycles completed, the
            # current phase name, and MISSION/Nonmission (phased models).
            self._update_phase_labels(system_state)
            # cap_data (when capacity analysis is enabled): the engine's
            # live per-node flow dicts, painted under the nodes Raptor 7
            # `ShowFlow` style. model (engine's on_event 5th arg): the
            # live engine model -- node/link/event/hierarchy colours are
            # painted from it (the canvas's own objects are a stale
            # separate parse).
            #
            # Repaint only when something painted actually changed
            # (`_live_state_signature`). The kickoff burst at t=0 is
            # thousands of scheduler steps with an identical picture;
            # repainting each one looked frozen and dominated the run.
            sig = _live_state_signature(blocks, cap_data, model, system_state)
            if sig != getattr(self, "_live_sig", None):
                self._live_sig = sig
                self._diagram_dock.view.update_live_states(
                    blocks, cap_data=cap_data, model=model,
                    phase=getattr(system_state, "current_phase", None))
            if status_updates:
                self._statusbar.showMessage(self._run_status_text(
                    f"Running ({self._threads_label(1)}) — run "
                    f"{runs_done[0] + 1} of {num_runs}, "
                    f"t = {scheduler.time:.3f}",
                    runs_done[0], num_runs))
            QApplication.processEvents()
            if getattr(self, "_sync_stop", False):
                raise RunAborted()

        def _on_event_keepalive(scheduler, system_state, blocks,
                                cap_data=None, model=None):
            # Animate off: the run is at full speed, so skip the
            # repaint/status-text work _on_event does, but still pump the
            # loop occasionally so a long run at max speed doesn't go
            # fully unresponsive either.
            now = _time.monotonic()
            if now - last_paint[0] < 0.5:
                return
            last_paint[0] = now
            QApplication.processEvents()
            if getattr(self, "_sync_stop", False):
                raise RunAborted()

        def _throttle() -> None:
            """Animation pacing for the SYNCHRONOUS path.

            The speed dial used to be read only inside the background
            worker, so in the default (foreground) run mode all four
            buttons did nothing but move a label. This applies the same
            per-event delay here, via `_effective_event_delay` (which
            returns 0 whenever Animate is off, matching Raptor 7's
            "no graphics = full speed" rule)."""
            d = self._effective_event_delay()
            if d:
                _time.sleep(d)

        runaway_asked = [False]

        def _progress(completed: int, total: int):
            runs_done[0] = completed
            if run_metadata.get("runaway_runs") and not runaway_asked[0]:
                runaway_asked[0] = True
                if completed < total and not self._ask_keep_going_after_runaway(completed, total):
                    from raptor.core.engine.errors import RunAborted
                    raise RunAborted()
            if getattr(self, "_sync_stop", False):
                # mimic the background path's abort: raise through the
                # engine so it stops between draws with results kept
                from raptor.core.engine.errors import RunAborted
                raise RunAborted()
            pct = 100 * completed // max(total, 1)
            if status_updates and not live_view:
                self._statusbar.showMessage(self._run_status_text(
                    f"Running ({self._threads_label(1)}) — run "
                    f"{completed} of {total}", completed, total))
            self._run_progress.setValue(pct)
            self._run_progress.setVisible(True)
            self._run_progress_label.setText(f"{pct}%")
            self._run_progress_label.setVisible(True)
            # Cross-run averages (AvailMon.Mean semantics): refresh the
            # permanent readouts from every completed run's data,
            # independent of any toggle.
            self._update_status_summary(report_data, units)
            if live_view and live_logs:
                # Stream the completed runs' logs into the Event Log tab
                # so the user watches the simulation arrive run-by-run.
                if not self._results_dock.isVisible():
                    self._results_dock.show()
                self._results.show_live_logs(live_logs, total)
            if cap_lines:
                # Capacity analysis enabled: stream the capacity
                # snapshots completed so far into the Graphs tab's
                # Capacity plot, so the capacity is visible during the
                # run.
                self._results.update_live_capacity(cap_lines)
                if not self._results_dock.isVisible():
                    self._results_dock.show()
            if ao_lines:
                # Live Ao plot (Raptor 7 displays it during a run).
                self._results.update_live_ao(ao_lines)
                if not self._results_dock.isVisible():
                    self._results_dock.show()
            if status_updates or live_view:
                QApplication.processEvents()

        report_data: list = []
        report_notes: list[str] = []
        # Files & Reports opt-ins: plot sinks are only created -- and
        # only handed to the engine -- when their Simulate ▸ Files &
        # Reports checkbox is ticked; each one is a disk-backed
        # ResultStream, so the engine's rows never accumulate in RAM.
        # Empty lists stay for the disabled ones so the code below can
        # treat them uniformly.
        streams = self._open_report_streams()
        # Note: keep the raw stream objects (a fresh stream is falsy —
        # never let `or []` substitute a list, or set_results loses the
        # disk-backed sink).
        ao_lines = streams.get("ao")
        cap_lines = streams.get("capacity")
        cost_lines = streams.get("cost")
        run_metadata: dict = {}
        # Freeze the manifest BEFORE the engine is handed anything, so it
        # describes the run that actually happened even if the model is
        # edited while the engine works.
        run_metadata["manifest"] = self._build_run_manifest()
        # Wrap the per-event hook so the animation delay is applied on
        # EVERY event, not just the ones that pass the 30 Hz paint
        # throttle inside `_on_event`. `_effective_event_delay` returns
        # 0.0 while Animate is off, so a non-animated run pays nothing.
        _raw_on_event = _on_event if live_view else _on_event_keepalive

        def _on_event_throttled(*a, **kw):
            _throttle()
            return _raw_on_event(*a, **kw)
        try:
            event_logs, end_of_run_lines, final_results_lines = run_simulation_with_reports(
                run_model, num_runs=num_runs, report_sink=report_data,
                progress=_progress, notes_sink=report_notes,
                on_event=_on_event_throttled,
                # Enabled sinks pass their ResultStream (never truthiness
                # tests — a fresh empty stream must still collect);
                # disabled ones pass None so the engine skips them.
                ao_sink=streams.get("ao"),
                capacity_sink=streams.get("capacity"),
                cost_sink=streams.get("cost"),
                collect_logs=self._report_stream_actions[
                    "event_log"].isChecked(),
                metadata_sink=run_metadata,
                ao_plot_interval=self._effective_run_interval("ao_interval"),
                cost_plot_interval=self._effective_run_interval(
                    "cost_interval"),
                count_draws=self._dev_options().random_draw_count,
                sim_details_path=self._sim_details_path(),
                sim_details_append=True,
                mission_path=mission_path,
                defer_trigger=self._dev_options().defer_trigger,
                cumulative_trial_path=cumulative_trial_path,
                cumulative_trial_interval=self._dev_options().cumulative_by_trial,
                cumulative_time_path=cumulative_time_path,
                cumulative_time_mesh=self._dev_options().cumulative_by_time,
                keep_partial_logs=True,
            )
        except RunAborted:
            # Stop pressed (between runs via _progress, or now mid-run
            # via _on_event/_on_event_keepalive): keep completed results
            # and fall through to the normal results path (flag already
            # set).
            pass
        except NotImplementedError as e:
            QApplication.restoreOverrideCursor()
            self._run_in_progress = False
            self._refresh_commands()
            # A failed run is over too: without this the run settings
            # stayed locked after an error.
            self._reenable_run_panel()
            self._run_progress.setVisible(False)
            self._run_progress_label.setVisible(False)
            self._clear_run_eta()
            self._hide_phase_labels()
            msgbox.warning(
                self, "Unsupported Model Feature",
                f"This model uses a feature the simulation engine doesn't support yet:\n\n{e}",
            )
            self._statusbar.showMessage("Run failed — unsupported feature")
            return
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self._run_in_progress = False
            self._refresh_commands()
            # A failed run is over too: without this the run settings
            # stayed locked after an error.
            self._reenable_run_panel()
            self._run_progress.setVisible(False)
            self._run_progress_label.setVisible(False)
            self._clear_run_eta()
            self._hide_phase_labels()
            msgbox.critical(self, "Simulation Failed", f"The run failed:\n\n{e}")
            self._statusbar.showMessage("Run failed")
            return
        QApplication.restoreOverrideCursor()
        self._run_in_progress = False
        self._refresh_commands()
        self._reenable_run_panel()
        self._run_progress.setVisible(False)
        self._run_progress_label.setVisible(False)
        self._clear_run_eta()
        # Raptor 7 clears its cycle/phase panels at batch end; same
        # here. Clear the live fills/Linked/Cut badges too: the
        # synchronous (Animate) path used to leave them on the canvas, so
        # a finished run still showed white Linked squares.
        self._hide_phase_labels()
        self._diagram_dock.view.clear_live_states()
        # Status bar says "Processing results" between the run(s)
        # finishing and the results actually being parsed/rendered;
        # without this, the bar would show a stale "Running — run N of
        # N" message for however long set_results/_present_results took
        # on a large model, looking frozen.
        self._statusbar.showMessage("Processing results…")
        QApplication.processEvents()
        units = None
        try:
            cfg_section = self._rbd.sections.get("SYSTEM_DATA")
            if cfg_section and cfg_section.rows:
                units = system_config_from_row(cfg_section.rows[0]).units
        except (ValueError, KeyError):
            pass
        self._push_weak_settings()
        # FailSim/CycleSim anti-runaway bound hit: surface it loudly
        # instead of silently reporting numbers from a truncated
        # simulation.
        self._notify_negative_times(run_metadata.get("negative_draws", []), report_notes)
        if run_metadata.get("hit_safety_bound"):
            report_notes.append(
                "Note: the run reached the simulation safety bound "
                "(t = 10,000,000) WITHOUT meeting its termination "
                "condition — results cover only the simulated portion. "
                "Check the model's termination condition; see Help ▸ "
                "Report Issue if this model should have ended earlier.")
            self._statusbar.showMessage(
                "WARNING: run stopped at the t=10,000,000 safety bound — "
                "termination condition never met", 12000)
        self._results._source_file_name = self._current_file or ""
        event_logs = self._dev_filtered_event_logs(event_logs)
        importance = self._importance_for(run_model, report_data)
        self._results.set_results(event_logs, end_of_run_lines, final_results_lines,
                                  report_data=report_data, units=units,
                                  notes=report_notes, ao_lines=ao_lines,
                                  cap_lines=cap_lines, cost_lines=cost_lines,
                                  manifest=run_metadata.get("manifest"),
                                  importance=importance)
        self._note_results_basis()
        self._remember_report_streams(streams)
        self._results_show_action.setEnabled(bool(report_data))
        self._results_float_action.setEnabled(bool(report_data))
        self._sync_overlay_actions(report_data)
        # Do NOT auto-enable a canvas overlay here. A run should not
        # silently repaint the model (and must never override the
        # overlay the user explicitly chose). The Results ribbon offers
        # Weak Links/Capacity/Cost/Spares once their data exists.
        self._apply_hier_status(report_data)
        self._present_results()
        self._note_run_profile(len(report_data))
        self._update_status_summary(report_data, units)
        from raptor.core import diagnostics as _dg
        self._diag(_dg.end_simulation_line())
        total_lines = sum(len(lines) for lines in event_logs)
        if getattr(self, "_sync_stop", False):
            completed = len(report_data)
            if completed < len(event_logs):
                self._statusbar.showMessage(
                    "Stopped — partial event log kept for the interrupted "
                    f"run, {total_lines} event-log lines", 10000)
            else:
                self._statusbar.showMessage(
                    f"Stopped after {completed} run(s), "
                    f"{total_lines} event-log lines")
        else:
            # State success explicitly so a finished run is never
            # mistaken for an aborted one.
            self._statusbar.showMessage(
                f"Completed {len(event_logs)} run(s) successfully, "
                f"{total_lines} event-log lines")
        self._sync_stop = False

    def _current_units(self):
        try:
            cfg_section = req(self._rbd).sections.get("SYSTEM_DATA")
            if cfg_section and cfg_section.rows:
                return system_config_from_row(cfg_section.rows[0]).units
        except (ValueError, KeyError):
            pass
        return None

    def _ensure_step_session(self):
        """Lazily build the interactive single-run session. Full kickoff
        (resets + synchronous initialization) happens HERE, synchronously,
        without advancing the clock."""
        if self._step_session is None or self._step_sink is None:
            self._step_sink = []
            self._step_ao: list[str] = []
            self._results.clear()   # new debug session: clear stale results
            self._diagram_dock.view.clear_result_overlays()
            self._edit_run_abandoned = False
            # The session spans the configured runs, as Raptor 7's does: Jump goes on to the next run
            # and Finish completes every run that is left.
            self._step_session = StepSession(
                self._model_for_engine(), report_sink=self._step_sink,
                ao_sink=self._step_ao,
                num_runs=int(self._current_experiment()["trials"]))
            # A stepped session is a run for as long as it is open -- it
            # holds live engine objects and sits idle between clicks. The
            # run settings lock with it, exactly as for a batch; without
            # this the Simulate tab's Analyses toggles stay live, and each one
            # writes the model and so trips the edit-during-run guard's
            # modal.
            self._disable_run_panel_for_run()
        return self._step_session

    _NO_BREAKPOINT = "No breakpoint"

    def _bp_name(self):
        """The chosen breakpoint block name, from whichever control holds
        it. The combo now lives in the Run panel (the ribbon has no room
        for it), and `_breakpoint_combo` is kept as an alias so the
        existing reads keep working."""
        bid = self._breakpoint_combo.currentData()
        if bid is None or self._rbd is None:
            return None
        live = self._diagram_dock.view.blocks.get(bid)
        if live is not None:
            return live.name
        row = next((r for r in self._rbd.sections["BLOCK_DATA"].rows
                    if str(r.get("ID")) == str(bid)), None)
        return row.get("blockName") if row is not None else None

    def _breakpoint_hit(self, session, prev_status) -> bool:
        """True when the selected block newly entered Repairing during
        the last step (a block "fails" = Running→Repairing transition;
        BlockStatus has no FAILED state of its own)."""
        bid = self._breakpoint_combo.currentData()
        if bid is None:
            return False
        for b in session.blocks:
            if b.id == bid:          # by id: names need not be unique
                return (b.op_status == BlockStatus.REPAIRING
                        and prev_status.get(b.id) != BlockStatus.REPAIRING)
        return False

    def _show_inspector_dock(self):
        """Bring the Inspector dock to front -- same show()+raise_()
        pattern used elsewhere for a tabbed dock (`_show_live_log_dock`).

        `show()` alone is not enough: a *closed* Inspector is collapsed
        to its rotated stub by hiding its content, and the QDockWidget
        stays visible, so `show()` was a no-op and right-click >
        Properties appeared to do nothing. Go through the stub's own
        restore first."""
        stub = (self._dock_stubs or {}).get(self._inspector_dock)
        if stub is not None:
            stub(False)
        self._inspector_dock.show()
        self._inspector_dock.raise_()

    def _show_live_log_dock(self, show: bool):
        """Swap right-side docks: Live Log during stepping, Inspector
        otherwise. Tabified on the same area so the user's layout is
        preserved."""
        if show:
            self.tabifyDockWidget(self._live_log_dock,
                                  self._inspector_dock)
            self._sync_dock_tab_bars()
            self._live_log_dock.show()
            self._live_log_dock.raise_()
        else:
            self._live_log_dock.hide()
            self._inspector_dock.show()

    def _feed_live_log(self, logs: list[str]):
        """Append new log lines, coloured like the Results Event Log.

        Five columns (Time/Type/Element/State/Detail) from the engine's own
        tab-separated line -- `parse_event_log_line` is shared with the
        export path, so the live log and the spreadsheet can never disagree
        about a line's shape. The State cell is coloured green/amber/red by
        `ResultsDock._state_colour`, the same mapping the Event Log tab
        uses, so a run's health is readable at a glance instead of by
        reading the text."""
        from PySide6.QtWidgets import QTreeWidgetItem
        from raptor.gui.resources.design_tokens import active_colour
        from raptor.gui.ui.results import ResultsDock
        c = active_colour()
        for line in logs:
            item = QTreeWidgetItem(self._live_log_tree,
                                   list(parse_event_log_line(line)))
            colour = ResultsDock._state_colour(item.text(3), c)
            if colour is not None:
                item.setForeground(3, colour)
                # The element name gets the same tint so a state change is
                # visible when the columns are narrow.
                item.setForeground(2, colour)

    def _on_live_log_row_clicked(self):
        """Focus the canvas on the selected live-log row's element."""
        items = self._live_log_tree.selectedItems()
        if not items:
            return
        name = items[0].text(2).strip()
        if not name:
            return
        # `_on_result_element_clicked` locates a block or node by name and
        # says so in the status bar when there is no match; a system-level
        # row ("System") simply has nothing to locate.
        for kind in ("block", "node"):
            if self._diagram_dock.view.locate_element(kind, name):
                return
        self._statusbar.showMessage(
            f"Live log row has no canvas element named '{name}'", 4000)

    def _refresh_live_step_view(self, session):
        # Refresh after every step (no throttle -- stepping is user-paced,
        # the whole point is watching each event land).
        if self._live_sim_action.isChecked():
            if not self._diagram_dock.isVisible():
                self._diagram_dock.show()
            self._diagram_dock.view.update_live_states(
                session.blocks, model=session.model,
                phase=getattr(getattr(session.cascade, "system_state", None), "current_phase", None))
            # Status-bar system square tracks stepping too.
            self._update_system_swatch(
                getattr(getattr(session.cascade, "system_state", None),
                        "color", None))
            self._show_live_log_dock(True)
            new_logs = getattr(session, "logs", [])
            prev_n = getattr(self, "_live_log_fed", 0)
            if len(new_logs) > prev_n:
                self._feed_live_log(new_logs[prev_n:])
                self._live_log_fed = len(new_logs)
                self._live_log_tree.scrollToBottom()
        logs = session.logs
        self._statusbar.showMessage(
            f"Stepping — t = {session.time:.3f} "
            f"({len(logs)} event-log lines)"
            f"{self._phase_status_suffix(session)}")
        # Live progress in the Live Log dock/tab title -- the tabified
        # label doubles as the tab text. The Live Log dock uses the plain
        # Qt titlebar, which re-reads windowTitle() on show, so there is
        # no cached custom-titlebar label to keep in sync.
        self._live_log_dock.setWindowTitle(
            f"Live Log — t = {session.time:.3f} ({len(logs)} events)")
        if self._results_dock.isVisible() and self._live_sim_action.isChecked():
            self._results.show_live_logs([logs], 1)
        QApplication.processEvents()

    def _describe_and_focus_step(self, session, prev_statuses,
                                 prev_line_count):
        """After one step: status-bar wording for WHAT changed + optional
        canvas centring on that element (Raptor 7's step semantics —
        'advance to the next event that results in a color change')."""
        new_lines = session.logs[prev_line_count:]
        v = self._diagram_dock.view
        target_item = None
        message = None
        # Prefer the last new log line mentioning a real element
        # (skip System bookkeeping) for both wording and focus.
        parsed = []
        for line in new_lines:
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) >= 4:
                parsed.append(parts)  # time, Kind, Name, Status, [comment]
        for parts in reversed(parsed):
            kind, name, status = parts[1], parts[2], parts[3]
            if kind == "System" or name in ("--",):
                continue
            message = f"{kind} {name} → {status}  (t = {parts[0]})"
            target_item = (
                next((it for bid, it in v._block_items.items()
                      if v.blocks[bid].name == name), None)
                or next((it for nid, it in v._node_items.items()
                         if v.nodes[nid].node_name == name), None)
                or next((it for eid, it in v._event_items.items()
                         if v.events[eid].name == name), None)
                or v._hier_items.get(next(
                    (h for h in v.hiers if v.hiers[h].name == name),
                    None)))
            if target_item is not None:
                break
        if message is None and parsed:
            parts = parsed[-1]
            message = f"{parts[1]} {parts[2]} → {parts[3]}  (t = {parts[0]})"
        # Fall back to block-status diff when log parsing finds nothing.
        if message is None:
            for b in session.blocks:
                if prev_statuses.get(b.id) != b.op_status:
                    message = (f"{b.name} → "
                               f"{getattr(b.op_status, 'value', b.op_status)}"
                               f"  (t = {session.time:.3f})")
                    target_item = v._block_items.get(b.id)
                    break
        if self._focus_event_action.isChecked() and target_item is not None:
            v.centerOn(target_item)
        self._statusbar.showMessage(
            (message or f"Step — t = {session.time:.3f}")
            + self._phase_status_suffix(session))

    def _phase_status_suffix(self, session) -> str:
        """Raptor 7 status-bar readout during step sim: current
        phase name + Mission/Non-Mission flag (`SystemState.current_
        phase`/`mission_status`, both live-updated by the engine). Empty
        for a non-phased model -- it's always implicitly "phase 1",
        nothing worth surfacing."""
        phases = getattr(session.model, "phases", None)
        if not phases:
            return ""
        ss = session.cascade.system_state
        idx = ss.current_phase
        name = (phases[idx - 1].phase_name
                if 1 <= idx <= len(phases) else "")
        mission = getattr(ss.mission_status, "value", ss.mission_status)
        label = f'Phase {idx}/{len(phases)}'
        if name:
            label += f' "{name}"'
        return f" — {label} ({mission})"

    def _on_step(self):
        if self._rbd is None:
            return
        try:
            session = self._ensure_step_session()
            prev = {b.id: b.op_status for b in session.blocks}
            prev_lines = len(session.logs)
            # Step pauses only when system state actually changed (Raptor 7's
            # TimeAdvance `IF SystemStateChange`): silent
            # calendar wakes (GenerateAoPlotData's AoPlotInterval timer,
            # CalculateInterimCost's CostPlotInterval timer) produce no
            # log lines and no pause. Skip them, or Next lands on an
            # AoPlot wake with nothing in the live log.
            while True:
                advanced = session.step()
                if not advanced or len(session.logs) > prev_lines:
                    break
            self._refresh_live_step_view(session)
            if not advanced:
                self._end_of_stepped_run(session)
                return
            self._describe_and_focus_step(session, prev, prev_lines)
            if self._breakpoint_hit(session, prev):
                self._statusbar.showMessage(
                    f"BREAKPOINT — {self._bp_name()} entered Repairing at "
                    f"t = {session.time:.3f}")
        except Exception:
            import traceback
            QApplication.restoreOverrideCursor()
            msgbox.critical(
                self, "Step Failed",
                "The step could not be executed:\n\n"
                + traceback.format_exc(limit=4))
            self._step_session = None
            self._sync_run_settings_lock()

    def _on_step_change(self):
        """Step to the next system-state change (Raptor 7's Step; `StepSession.step_to_change`)."""
        if self._rbd is None:
            return
        try:
            session = self._ensure_step_session()
            prev = {b.id: b.op_status for b in session.blocks}
            prev_lines = len(session.logs)
            advanced = session.step_to_change()
            self._refresh_live_step_view(session)
            if not advanced:
                self._end_of_stepped_run(session)
                return
            self._describe_and_focus_step(session, prev, prev_lines)
            if self._breakpoint_hit(session, prev):
                self._statusbar.showMessage(
                    f"BREAKPOINT — {self._bp_name()} entered Repairing at "
                    f"t = {session.time:.3f}")
        except Exception:
            import traceback
            QApplication.restoreOverrideCursor()
            msgbox.critical(
                self, "Step Failed",
                "The step could not be executed:\n\n" + traceback.format_exc(limit=4))
            self._step_session = None
            self._sync_run_settings_lock()

    def _advance_until(self, *, max_steps=None, target_time=None,
                       until_time_changes=False):
        """Shared Multi/Run-To/Next-Time driver: steps until whichever
        comes first -- step budget exhausted, breakpoint hit, target
        time reached, sim time advances past its starting value
        (`until_time_changes`), or the run ends. Focuses/describes only
        the LAST change (per-event centring during a multi-step would
        be seizure-inducing). Returns True if a breakpoint fired."""
        session = self._ensure_step_session()
        start_time = session.time if until_time_changes else None
        fired = False
        done = False
        prev_statuses = {b.id: b.op_status for b in session.blocks}
        prev_lines = len(session.logs)
        while not done:
            prev = {b.id: b.op_status for b in session.blocks}
            advanced = session.step()
            if advanced and len(session.logs) == prev_lines \
                    and target_time is None and max_steps is None:
                # Step pauses only on a system state change; a silent
                # calendar wake (AoPlot/InterimCost interval timers, no
                # log lines) is sailed through -- skipping it keeps Next
                # Time/stepping off nonexistent times. Run-To and
                # budgeted Multi keep their exact one-entry semantics.
                continue
            self._refresh_live_step_view(session)
            if not advanced:
                done = True          # run out of events -> auto-finish below
                break
            if self._breakpoint_hit(session, prev):
                fired = True
                break
            if max_steps is not None:
                max_steps -= 1
                if max_steps <= 0:
                    break
            if target_time is not None and session.time >= target_time:
                break
            if until_time_changes and session.time > start_time:
                break
        if done:
            self._end_of_stepped_run(session)
        else:
            self._describe_and_focus_step(session, prev_statuses,
                                          prev_lines)
        if fired:
            # After the description: said before it, the narration of the
            # last change overwrote it and the stop looked unexplained.
            self._statusbar.showMessage(
                f"BREAKPOINT — {self._bp_name()} entered Repairing at "
                f"t = {session.time:.3f}")
        return fired

    def _on_step_next_time(self):
        """"Next Time": several events often share the exact same SimTime
        (simultaneous transitions), so stepping one event at a time
        means clicking through all of them before anything visually new
        happens. This advances through every event at the current time
        and stops as soon as sim time actually moves forward, or a
        breakpoint/the run's end intervenes first."""
        if self._rbd is None:
            return
        self._advance_until(until_time_changes=True)

    def _on_step_multi(self):
        if self._rbd is None:
            return
        n = self._run_panel.step_count() if self._run_panel is not None \
            else self._step_count_spin.value()
        self._advance_until(max_steps=n)

    def _on_run_to(self):
        if self._rbd is None:
            return
        from raptor.gui.ui.units_conv import display_to_native
        try:
            native_units = system_config_from_row(
                self._rbd.sections["SYSTEM_DATA"].rows[0]).units
        except (ValueError, KeyError, IndexError, AttributeError):
            native_units = ""
        raw = self._run_panel.run_to_time() if self._run_panel is not None \
            else self._run_to_time_spin.value()
        target = display_to_native(
            raw, native_units,
            getattr(self._rbd, "display_unit", None))
        self._advance_until(target_time=target)

    def _populate_breakpoints(self):
        """(Re)build the breakpoint list from EVERY block in the model.

        It read the view's blocks -- the current level only -- and only on
        load, so a model whose blocks all sit inside hierarchies (the data
        centre) offered nothing to stop on, and a renamed block kept its
        old name. Blocks inside a hierarchy are labelled with their path
        ("IT_sub / Server_Row_1"); the choice is kept by block id, so it
        survives a rename and is refreshed after every edit."""
        rbd = self._rbd
        hier_name: dict[int, tuple[str, int]] = {}
        for r in (rbd.sections.get("HIERARCHY_DATA").rows
                  if rbd is not None and rbd.sections.get("HIERARCHY_DATA")
                  else []):
            try:
                hier_name[int(float(r["ID"]))] = (
                    r.get("hierarchyName", ""), int(float(r.get("Parent", 0) or 0)))
            except (KeyError, ValueError):
                continue

        def path(parent: int) -> str:
            parts, seen = [], set()
            while parent and parent in hier_name and parent not in seen:
                seen.add(parent)
                name, parent = hier_name[parent]
                parts.append(name)
            return " / ".join(reversed(parts))

        options: list[tuple[int, str]] = []
        for r in (rbd.sections["BLOCK_DATA"].rows
                  if rbd is not None and "BLOCK_DATA" in rbd.sections else []):
            try:
                bid = int(float(r["ID"]))
                parent = int(float(r.get("ParentId", 0) or 0))
            except (KeyError, ValueError):
                continue
            if bid == 0:
                continue
            where = path(parent)
            live = self._diagram_dock.view.blocks.get(bid)
            # The canvas holds this level's edits (a rename) until they
            # are written back to the rows.
            name = live.name if live is not None else r.get("blockName", "")
            options.append((bid, f"{where} / {name}" if where else name))
        options.sort(key=lambda o: o[1].lower())
        combo = self._breakpoint_combo
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(self._NO_BREAKPOINT, None)
        for bid, label in options:
            combo.addItem(label, bid)
        idx = combo.findData(current) if current is not None else -1
        combo.setCurrentIndex(idx if idx > 0 else 0)
        combo.blockSignals(False)
        panel = getattr(self, "_run_panel", None)
        if panel is not None and panel._breakpoint is not combo:
            panel.set_breakpoint_options(options)

    def _end_of_stepped_run(self, session) -> None:
        """A stepped run has no events left. Raptor 7 stays in step mode
        across trials: the next trial starts and pauses at its first
        change (what Jump does). Only after the last trial do the results
        come up. This used to call Finish, which ran every remaining trial
        at full speed -- one stepped run, then hundreds unseen."""
        if not session.has_next_run:
            self._on_step_finish()
            return
        prev = {b.id: b.op_status for b in session.blocks}
        prev_lines = len(session.logs)
        session.jump_to_next_run_start()
        session.step_to_change()
        self._refresh_live_step_view(session)
        self._describe_and_focus_step(session, prev, prev_lines)
        self._statusbar.showMessage(
            f"Run {session.run_index} finished — now stepping run "
            f"{session.run_index + 1} of {session.num_runs} "
            f"(Finish runs the rest without stopping)", 8000)
        self._refresh_commands()

    def _on_step_jump(self):
        """Jump: the rest of this run goes by, and the next run stops at its first
        change (`StepSession.jump_to_next_run_start`)."""
        session = self._step_session
        if session is None or not session.has_next_run:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                session.jump_to_next_run_start()
                prev = {b.id: b.op_status for b in session.blocks}
                prev_lines = len(session.logs)
                session.step_to_change()
            finally:
                QApplication.restoreOverrideCursor()
            self._refresh_live_step_view(session)
            self._describe_and_focus_step(session, prev, prev_lines)
            self._statusbar.showMessage(
                f"Jump — run {session.run_index + 1} of {session.num_runs}, "
                f"t = {session.time:.3f}")
            self._refresh_commands()
        except Exception:
            import traceback
            msgbox.critical(
                self, "Jump Failed",
                "The jump could not be executed:\n\n" + traceback.format_exc(limit=4))
            self._step_session = None
            self._sync_run_settings_lock()

    def _on_step_finish(self):
        """Complete the stepped run (remaining events + closing passes),
        then present results exactly like a normal batch run."""
        if self._step_session is None:
            return
        session = self._step_session
        self._on_fev_log_close()
        self._step_session = None  # single-use; next Step starts fresh
        self._sync_run_settings_lock()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            event_logs, _ = session.finish_all()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            msgbox.critical(self, "Simulation Failed",
                                 f"The run failed:\n\n{e}")
            self._statusbar.showMessage("Run failed")
            return
        eor_lines: list[str] = []
        final_lines: list[str] = []
        report_data = list(self._step_sink)
        if report_data:
            model = session.model
            date = time.ctime()
            file_name = session.rbd_file.file_name
            eor_lines = [f"End_Of_Run Report for {file_name}  {date}", ""]
            for report in report_data:
                eor_lines.extend(build_end_of_run_report(report, model.blocks_by_id))
            final_lines = build_final_results_report(
                report_data, model.blocks_by_id, file_name,
                model.run_length, date,
                termination_type=model.termination_type,
            )
        QApplication.restoreOverrideCursor()
        self._diagram_dock.view.clear_live_states()
        # The manifest says how many runs the numbers on screen come from.
        step_manifest = self._build_run_manifest()
        if step_manifest:
            step_manifest["trials"] = len(event_logs)
        self._present_run_results(event_logs, eor_lines, final_lines,
                                  report_data,
                                  ao_lines=list(self._step_ao or []),
                                  cap_lines=session.cap_lines,
                                  manifest=step_manifest)

    def _diag(self, *lines: str) -> None:
        """Append to the `/d` diagnostics file beside the model when `diagnostics_log` is on."""
        if not self._dev_options().diagnostics_log or not self._current_file:
            return
        import time
        from raptor.core import diagnostics
        folder = Path(self._current_file).parent
        if getattr(self, "_diag_name", None) is None:
            now = time.ctime()
            self._diag_name = diagnostics.diagnostics_file_name(now)
            first = diagnostics.startup_lines(now, install_path=str(folder),
                                              file_to_open=self._current_file)
        else:
            first = []
        try:
            with open(folder / self._diag_name, "ab") as f:
                for ln in [*first, *lines]:
                    f.write(ln.encode("latin-1", "replace") + b"\r\n")
        except OSError as exc:
            self._statusbar.showMessage(f"Diagnostics file could not be written: {exc}", 8000)

    def _write_dev_trees(self) -> None:
        """`reports/DomainTree.TXT` and `reports/CoreFile.TXT` beside the model when the
        `domain_tree` / `core_file` options are on (Raptor 7 writes them at run start,
        relative to its working directory)."""
        opts = self._dev_options()
        if not (opts.domain_tree or opts.core_file) or not self._current_file \
                or self._rbd is None:
            return
        import time
        from raptor.core.engine.domain_tree import domain_tree_lines
        folder = Path(self._current_file).parent / "reports"
        today = time.ctime()
        name = Path(self._current_file).name
        try:
            folder.mkdir(exist_ok=True)
            for want, file_name, core in ((opts.domain_tree, "DomainTree.TXT", False),
                                      (opts.core_file, "CoreFile.TXT", True)):
                if want:
                    text = "\r\n".join(domain_tree_lines(self._rbd, name, today, core=core))
                    (folder / file_name).write_bytes((text + "\r\n").encode("latin-1", "replace"))
        except OSError as exc:
            self._statusbar.showMessage(f"Domain tree file could not be written: {exc}", 8000)

    def _ask_mission_path(self) -> str | None:
        """The `mission_file` option's Save-As prompt (the original asks at run start; Cancel
        means no mission file for this run)."""
        if not self._dev_options().mission_file:
            return None
        start = (str(Path(self._current_file).with_name(Path(self._current_file).stem
                                                       + "_mission.txt"))
                 if self._current_file else "mission.txt")
        path, _ = fdg.getSaveFileName(self, "Mission File", start, "Text files (*.txt)")
        return path or None

    def _on_y_axis_min_changed(self, value: float) -> None:
        """The Ao graphs' Y-axis minimum was set: it is the model's `graphYmin` (saved, so the
        model is changed -- as in Raptor 7, whose Set Y-Axis edits the same variable)."""
        if self._rbd is None:
            return
        self._rbd.section("SYSTEM_DATA").rows[0]["graphYmin"] = f"{value:.6f}"
        self._mark_dirty()

    def _notify_negative_times(self, per_run, report_notes: list[str]) -> None:
        """After a batch that drew negative times (clamped to 0.000001): a note in the results and,
        unless the model ignores the warning, Raptor 7's dialog with its "Don't show this message
        again" box -- ticking it sets the model's `ShowNegErrMsg` (and marks it changed)."""
        from raptor.core.engine import negative_draws as neg
        text = neg.note(per_run)
        if text is None or self._rbd is None:
            return
        if neg.ignoring(self._rbd):
            return
        report_notes.append("Note: " + text)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(neg.DIALOG_TITLE)
        box.setText(neg.DIALOG_BODY)
        from PySide6.QtWidgets import QCheckBox
        again = QCheckBox(neg.DIALOG_DONT_SHOW)
        box.setCheckBox(again)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if again.isChecked():
            self._rbd.section("SYSTEM_DATA").rows[0]["ShowNegErrMsg"] = "1"
            self._mark_dirty()

    def _ask_cumulative_trial_path(self) -> str | None:
        """The `cumulative_by_trial` option's Save-As prompt ("Cumulative By Trial File"; Cancel
        means none for this run)."""
        if self._dev_options().cumulative_by_trial <= 0:
            return None
        start = (str(Path(self._current_file).with_name(Path(self._current_file).stem
                                                       + "_cumulative_by_trial.txt"))
                 if self._current_file else "cumulative_by_trial.txt")
        path, _ = fdg.getSaveFileName(self, "Cumulative By Trial File", start,
                                      "Text files (*.txt)")
        return path or None

    def _ask_cumulative_time_path(self) -> str | None:
        """The `cumulative_by_time` option's Save-As prompt ("Cumulative By Time File")."""
        if self._dev_options().cumulative_by_time <= 0:
            return None
        start = (str(Path(self._current_file).with_name(Path(self._current_file).stem
                                                       + "_cumulative_by_time.txt"))
                 if self._current_file else "cumulative_by_time.txt")
        path, _ = fdg.getSaveFileName(self, "Cumulative By Time File", start,
                                      "Text files (*.txt)")
        return path or None

    def _sim_details_path(self) -> str | None:
        """`simDetails.TXT` beside the model when the `sim_details` option is on. The file
        grows across runs and launches, as Raptor 7's does."""
        if not self._dev_options().sim_details or not self._current_file:
            return None
        return str(Path(self._current_file).parent / "simDetails.TXT")

    @staticmethod
    def _dev_options():
        from raptor.gui import dev_settings
        return dev_settings.current_options()

    @staticmethod
    def _dev_filtered_event_logs(event_logs):
        """The event logs through the Developer tab's category filter and one-component
        detail (Raptor 7's Avant-garde boxes); unchanged when neither is set."""
        from raptor.core.engine.eventlog import filter_event_log
        opts = MainWindow._dev_options()
        if not (opts.event_log_filter or opts.event_log_component):
            return event_logs
        cats = opts.event_log_filter
        return [filter_event_log(
            run, blocks="block" in cats, nodes="node" in cats, system="system" in cats,
            phases="phase" in cats, component=opts.event_log_component)
            for run in event_logs]

    def _present_run_results(self, event_logs, eor_lines, final_lines,
                             report_data, ao_lines: list[str] | None = None,
                             cap_lines: list[str] | None = None,
                             manifest=None):
        units = self._current_units()
        self._push_weak_settings()
        self._results._source_file_name = self._current_file or ""
        event_logs = self._dev_filtered_event_logs(event_logs)
        self._results.set_results(event_logs, eor_lines, final_lines,
                                  report_data=report_data, units=units,
                                  ao_lines=ao_lines, cap_lines=cap_lines,
                                  manifest=manifest)
        self._note_results_basis()
        self._results_show_action.setEnabled(bool(report_data))
        self._results_float_action.setEnabled(bool(report_data))
        self._apply_hier_status(report_data)
        self._present_results()
        self._note_run_profile(len(report_data))
        self._update_status_summary(report_data, units)
        total_lines = sum(len(lines) for lines in event_logs)
        self._statusbar.showMessage(
            f"Completed {len(event_logs)} run(s) successfully, "
            f"{total_lines} event-log lines")

    def _ensure_summary_labels(self):
        """Create the permanent status-bar stat readouts once."""
        if hasattr(self, "_summary_labels"):
            return
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy
        self._summary_labels = {}
        # The readouts share one container that does not count toward the
        # window's minimum width (they made it 916 px) and is hidden when
        # the window is too narrow for it (`_fit_status_readouts`); the
        # Results panel has the same numbers.
        self._readouts = _NoMinimumWidth()
        self._readouts.setSizePolicy(QSizePolicy.Policy.Preferred,
                                     QSizePolicy.Policy.Preferred)
        _ro = QHBoxLayout(self._readouts)
        _ro.setContentsMargins(0, 0, 0, 0)
        _ro.setSpacing(0)
        self._statusbar.addPermanentWidget(self._readouts)
        # System-status square: one small colour swatch replacing the
        # original's per-view status graphics -- any time Show Simulation
        # is on, the current system state is visible at a glance in the
        # status bar. Updated from the engine's live SystemColor (see
        # _update_system_swatch).
        self._system_swatch = QLabel("      ")
        self._system_swatch.setToolTip(
            "Current system status (live):\nGreen = fully working · "
            "Yellow = degraded · Red = fully down")
        # Selector-scoped rule: an unscoped `background: ...` declaration
        # applies to the widget and its logical descendants in Qt's
        # stylesheet engine, and the tooltip window painted for this
        # widget picks that background up -- the swatch's tooltip would
        # be tinted with the current system colour (green/yellow/red box
        # instead of the themed tooltip every other status-bar widget
        # shows). Scoping it to `QLabel` keeps the rule off the tip
        # window.
        self._system_swatch.setStyleSheet(
            "QLabel { background: "
            f"{APPENDIX_D_STATE_COLOURS['running']}; "
            "border: 1px solid #0b1220; "
            "border-radius: 3px; min-width: 14px; }")
        self._system_swatch.hide()
        _ro.addWidget(self._system_swatch)
        # Fixed widths for every live-updating readout: each label keeps
        # the width of its longest plausible rendering, centred inside
        # its cell, so updates don't shift the neighbouring items
        # horizontally. `_freeze_status_label` recomputes on demand
        # (units change per model).
        self._summary_units_cache = None
        for key in ("Ao", "MTBDE", "MDT", "R", "G/Y/R"):
            lbl = QLabel("—")
            lbl.setToolTip(
                {"Ao": "Availability — fraction of time at least partially working",
                 "MTBDE": "Mean Time Between Downing Events",
                 "MDT": "Mean Down Time per downing event",
                 "R": "Tracked average Reliability — missions completed "
                      "without failure / missions attempted, averaged "
                      "across runs",
                 "Red%": "Percent of time fully down",
                 "G/Y/R": "Average time split: fully working / "
                          "degraded / fully down"}.get(
                    key, "Simulation statistic"))
            _ro.addWidget(lbl)
            self._summary_labels[key] = lbl
            # The bold status stylesheet first (fixed width must be
            # measured with the font the label will actually render
            # -- freezing with the default weight and then bolding in
            # _update_status_summary would let the bold glyphs overflow
            # the frozen cell).
            if key != "G/Y/R":
                lbl.setStyleSheet(
                    # Scoped QLabel rule: an unscoped `color: #EAF2FB`
                    # here leaks into the label's tooltip window, painting
                    # the tip text the same bright colour as the navy
                    # bar's readouts (invisible in the light theme).
                    # Scoping to QLabel keeps the rule off the tip window,
                    # same as the system swatch above.
                    "QLabel { color: #EAF2FB; padding: 0 6px; "
                    "font-weight: 600; }")
            self._freeze_status_label(
                lbl, self._summary_sample(key, ""))
        # Raptor 7's status-bar panels 2/3/4: "<N> cycles completed"
        # for CycleSim models, the current phase name, and the
        # MISSION/Nonmission flag -- shown only while a phased model is
        # simulating, hidden otherwise (Raptor 7 clears panels 2/3 at
        # batch end and only writes 3/4 when activePhases>0).
        self._cycles_label = QLabel("")
        self._cycles_label.setToolTip(
            "Completed phase cycles this run (CycleSim termination "
            "condition)")
        # Scoped bright rule: plain QLabels on the navy bar matched the
        # app-level `QLabel { color: {text} }` rule -- theme text on
        # navy, invisible in the light theme (same bug as the units
        # chip). Scoped so the rule stays off the tooltip window.
        self._cycles_label.setStyleSheet(
            "QLabel { color: #EAF2FB; padding: 0 6px; }")
        self._freeze_status_label(self._cycles_label,
                                  "999999 cycles completed")
        self._cycles_label.hide()
        self._statusbar.addPermanentWidget(self._cycles_label)
        self._phase_label = QLabel("")
        self._phase_label.setToolTip(
            "Current phase name")
        self._phase_label.setStyleSheet(
            "QLabel { color: #EAF2FB; padding: 0 6px; }")
        # phase names cap at 20 characters (Raptor's own rule); the
        # label prefix is "Phase: "
        self._freeze_status_label(self._phase_label, "Phase: " + "M" * 20)
        self._phase_label.hide()
        self._statusbar.addPermanentWidget(self._phase_label)
        self._mission_label = QLabel("")
        self._mission_label.setToolTip(
            "Whether the current phase is a mission (time counts toward "
            "mission statistics) or non-mission")
        self._mission_label.setStyleSheet(
            "QLabel { color: #EAF2FB; padding: 0 6px; }")
        self._freeze_status_label(self._mission_label, "Nonmission")
        self._mission_label.hide()
        self._statusbar.addPermanentWidget(self._mission_label)

    #: Longest plausible rendering per summary readout -- the fixed
    #: width every label keeps regardless of the live value.
    _SUMMARY_SAMPLES = {
        "Ao": "Ao 0.999999999",
        "MTBDE": "MTBDE 999999.999",
        "MDT": "MDT 999999.999",
        "R": "R 0.999",
        "G/Y/R": "● 100.0%  ● 100.0%  ● 100.0%",
    }

    def _summary_sample(self, key: str, units: str = "") -> str:
        base = self._SUMMARY_SAMPLES.get(key, "—")
        if key in ("MTBDE", "MDT") and units:
            # the units suffix is appended verbatim to the number
            base = f"{base} {units}"
        return base

    def _freeze_status_label(self, lbl, sample: str, pad: int = 16):
        """Pin a status-bar readout to the width of its longest
        plausible rendering so status items don't shift as the live
        values change. Right-aligned inside the frozen cell: with
        centred text a growing number still slides around within its
        own slot, while right-aligned digits stay put, the way every
        right-justified numeric column behaves."""
        fm = lbl.fontMetrics()
        lbl.setFixedWidth(fm.horizontalAdvance(sample) + pad)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _refresh_summary_freeze(self, units: str | None = None) -> None:
        """Re-freeze the units-suffixed readouts (MTBDE/MDT) for a new
        units string; called at model open so the widths are already
        final before any run starts."""
        self._summary_units_cache = units
        for key in ("MTBDE", "MDT"):
            self._freeze_status_label(
                self._summary_labels[key],
                self._summary_sample(key, units or ""))


    def _update_phase_labels(self, system_state) -> None:
        """Refresh the cycles / phase-name / MISSION-Nonmission status
        readouts from the engine's live SystemState (Raptor 7's
        status-bar panels 2/3/4). Shown only while a phased model is
        actually simulating; hidden when the model has no phases, when
        idle, or at run end (Raptor 7 clears those panels at batch
        end)."""
        self._ensure_summary_labels()
        rbd = getattr(self, "_rbd", None)
        phase_rows = (rbd.sections.get("PHASES").rows
                      if rbd is not None
                      and rbd.sections.get("PHASES") is not None else [])
        if not phase_rows or system_state is None:
            self._cycles_label.hide()
            self._phase_label.hide()
            self._mission_label.hide()
            return
        try:
            from raptor.core.fileio.typed import system_config_from_row
            cfg = system_config_from_row(
                req(self._rbd).sections["SYSTEM_DATA"].rows[0])
        except (ValueError, KeyError, IndexError, AttributeError):
            cfg = None
        term_type = getattr(cfg, "termination_type", "") if cfg else ""
        if term_type == "CycleSim":
            n = int(getattr(system_state, "term_counter", 0) or 0)
            self._cycles_label.setText(
                f"{n} cycle{'s' if n != 1 else ''} completed")
            self._cycles_label.show()
        else:
            self._cycles_label.hide()
        phase_idx = int(getattr(system_state, "current_phase", 1) or 1)
        if 1 <= phase_idx <= len(phase_rows):
            from raptor.core.fileio.typed import phase_from_row
            self._phase_label.setText(
                f"Phase: {phase_from_row(phase_rows[phase_idx - 1]).phase_name}")
            self._phase_label.show()
        else:
            self._phase_label.hide()
        from raptor.core.engine.system_status import MissionStatus
        self._mission_label.setText(
            "MISSION" if getattr(system_state, "mission_status",
                                 None) is MissionStatus.MISSION
            else "Nonmission")
        self._mission_label.show()

    def _hide_phase_labels(self) -> None:
        """Batch end / model closed: Raptor 7 clears its cycle and
        phase panels."""
        if getattr(self, "_cycles_label", None) is not None:
            self._cycles_label.hide()
            self._phase_label.hide()
            self._mission_label.hide()

    def _update_system_swatch(self, colour, force: bool = False) -> None:
        """Repaint the status-bar system-status square from a live
        `SystemColor`. Hidden when Show Simulation is off, unless `force`
        (Failure Effects View always shows it)."""
        if colour is None or not (force or self._live_sim_action.isChecked()):
            self._system_swatch.hide()
            return
        from raptor.core.engine.system_status import SystemColor
        c = active_colour()
        fill = {SystemColor.GREEN: APPENDIX_D_STATE_COLOURS["running"],
                SystemColor.YELLOW: APPENDIX_D_STATE_COLOURS["maintenance"],
                SystemColor.RED: APPENDIX_D_STATE_COLOURS["repairing"],
                SystemColor.DONE: c["muted"]}.get(colour, c["muted"])
        self._system_swatch.setStyleSheet(
            f"QLabel {{ background: {fill}; border: 1px solid #0b1220; "
            f"border-radius: 3px; min-width: 14px; }}")
        self._system_swatch.show()

    def _on_ribbon_tab_changed(self, index: int):
        if index == getattr(self, "_simulate_page_index", -1) \
                and not self._model_is_valid():
            self._statusbar.showMessage(
                "Model errors require fixing before simulation can "
                "occur — see the ✗ badge (Model tab)", 8000)
        # A tab is a place to find commands, not a mode. Editing used to
        # lock on Simulate/Results, even on a freshly opened model with
        # nothing to protect. What needs protecting is DISPLAYED RESULTS,
        # and that is handled where the edit lands: see
        # `_arm_results_guard`. So switching tabs never changes
        # editability. An armed drawing tool is still dropped on the two
        # run-facing tabs, as before, so a stray click there cannot place
        # an element.
        v = self._diagram_dock.view
        if index in (getattr(self, "_simulate_page_index", -1),
                     getattr(self, "_results_page_index", -1)) \
                and v.tool_mode != "select":
            for t in (self._add_block_tool, self._add_node_tool,
                      self._add_event_tool, self._add_hier_tool,
                      self._connect_tool, self._anno_text_tool,
                      self._anno_rect_tool,
                      self._anno_ellipse_tool,
                      self._anno_arrow_tool):
                t.blockSignals(True)
                t.setChecked(False)
                t.blockSignals(False)
            v.tool_mode = "select"
        self._sync_annotation_order_actions()
        # Data Tables and Results are tabified into one bottom dock slot
        # (see the setup comment near `tabifyDockWidget`) -- raise
        # Results when the ribbon context is Simulate/Results, without
        # forcing the dock open if the user closed it.
        if index in (getattr(self, "_simulate_page_index", -1),
                     getattr(self, "_results_page_index", -1)):
            dock = getattr(self, "_results_dock", None)
            if dock is not None and dock.isVisible():
                dock.raise_()
        # The Run panel is the Simulate tab's settings home. Switching to
        # Simulate must show it, exactly as View ▸ Panels ▸ Run Panel does:
        # restore a minimised/closed slot and raise the Run tab. (It used
        # to raise only an already-visible tab, so a minimised strip left
        # the user looking at the ribbon with no settings.)
        if index == getattr(self, "_simulate_page_index", -1):
            run = getattr(self, "_run_dock", None)
            if run is not None:
                chrome = (getattr(self, "_dock_chrome", None) or {}).get(
                    "Results")
                if chrome is not None and chrome["get"]()["minimized"]:
                    chrome["set_minimized"](False)
                run.show()
                run.raise_()
                # `raise_()` does not fire a show event on a tab that was
                # already visible, so the dock never re-measured itself and
                # arrived clipped to the 150 px startup strip (reported:
                # "ok when I click the Run panel, not when I open the
                # Simulate ribbon"). Measure it here, the way the tab click
                # does.
                if hasattr(run, "fit_height"):
                    run.fit_height()
                sync = (getattr(self, "_dock_state_sync", None) or {}).get(run)
                if sync is not None:
                    sync(True)
            self._update_readiness()
        # Run Settings is redundant while its own page is showing the Run
        # panel; re-evaluate it after every ribbon switch.
        try:
            self._refresh_commands(only={"run_settings_available"})
        except AttributeError:
            pass

    def _sync_simulate_enabled_state(self):
        """Bring the command state in line with the model's validity.

        Runs off `_validation_timer` and after a load. Command availability
        itself is `_refresh_commands`; this only decides WHEN, and applies
        the one side effect an invalid model has: a stepped session built
        from it is discarded.

        The state includes the run, so refreshing during a run is safe
        (this used to be skipped entirely while running, because its tail
        would otherwise re-enable Run and disable Pause / Stop).
        """
        state = self._command_state()
        if state != getattr(self, "_applied_command_state", None):
            self._refresh_commands(state)
        if state.run != "idle":
            return
        if not state.valid:
            self._step_session = None
            self._sync_run_settings_lock()

    def _reset_status_summary(self):
        """Run starts with the readouts cleared -- no stale values from
        the previous batch."""
        self._ensure_summary_labels()
        self._clear_run_eta()
        for key in ("Ao", "MTBDE", "MDT", "R", "G/Y/R"):
            lbl = self._summary_labels.get(key)
            if lbl is not None:
                base = "G/Y/R" if key == "G/Y/R" else key
                lbl.setText(f"{base} —")

    def _update_status_summary(self, report_data: list, units: str | None):
        """Raptor-style cumulative key statistics, permanently visible in
        the status bar after a run (like the real Simulation view's own
        status readout): Availability, MTBDE, MDT, and % fully down."""
        self._ensure_summary_labels()
        if not report_data:
            return

        def mean(getter):
            vals = [v for v in (getter(d) for d in report_data) if v is not None]
            return sum(vals) / len(vals) if vals else None

        c = active_colour()
        u = f" {units}" if units else ""
        # Re-freeze the time-dimensioned readouts when the model's own
        # units change (the suffix is part of the frozen sample).
        if units != self._summary_units_cache:
            self._refresh_summary_freeze(units)
        ao = mean(lambda d: d.availability)
        mtbde = mean(lambda d: d.mtbde)
        mdt = mean(lambda d: d.mdt)
        red = mean(lambda d: d.red_percent)
        green = mean(lambda d: d.green_percent)
        yellow = mean(lambda d: d.yellow_percent)
        self._summary_labels["Ao"].setText(
            f"Ao {ao:.9f}" if ao is not None else "Ao —")
        # Fixed decimal places (not %g's adaptive significant-digit
        # count) so the number's width and decimal-point position don't
        # jump between updates as the running mean shifts during a live
        # run.
        self._summary_labels["MTBDE"].setText(
            f"MTBDE {mtbde:.3f}{u}" if mtbde is not None else "MTBDE —")
        self._summary_labels["MDT"].setText(
            f"MDT {mdt:.3f}{u}" if mdt is not None else "MDT —")
        # Tracked average reliability: the mission-success fraction
        # averaged across completed runs, same number the report's
        # Reliability row averages.
        rel = mean(lambda d: d.reliability)
        self._summary_labels["R"].setText(
            f"R {rel:.3f}" if rel is not None else "R —")

        def pct(v):
            return f"{v:.1f}%" if v is not None else "—"
        colour_lbl = self._summary_labels.setdefault("G/Y/R", QLabel("—"))
        colour_lbl.setToolTip(
            "Average percent of time fully working / degraded / "
            "fully down, across completed runs")
        # G/Y/R as three coloured dots with their averages.
        gy = self._summary_labels["G/Y/R"]
        gy.setTextFormat(Qt.TextFormat.RichText)
        gy.setText(
            f"<span style='color:{c['result_green']};'>●</span>"
            f"<span style='color:#EAF2FB;'>{pct(green)}&nbsp;&nbsp;</span>"
            f"<span style='color:{c['result_yellow']};'>●</span>"
            f"<span style='color:#EAF2FB;'>{pct(yellow)}&nbsp;&nbsp;</span>"
            f"<span style='color:{c['result_red']};'>●</span>"
            f"<span style='color:#EAF2FB;'>{pct(red)}</span>")
        gy.show()
        # The status bar is dark navy in both themes (global stylesheet),
        # so the readouts must be a fixed bright colour -- the theme's
        # text colour is near-black on navy (invisible) in the light
        # theme and too dim in dark.
        # Note: no `background: transparent` here: an unscoped background
        # declaration on a widget also paints the tooltip window shown
        # for it. Labels don't paint a background anyway, so dropping the
        # declaration fixes the tooltip while looking identical on the
        # bar.
        for key, lbl in self._summary_labels.items():
            if key == "G/Y/R":
                continue   # rich text already carries its own colours
            lbl.setStyleSheet(
                "QLabel { color: #EAF2FB; padding: 0 6px;"
                "font-weight: 600; }")
        gy.setStyleSheet(
            "QLabel { padding: 0 6px; font-weight: 600; }")

    def _restore_results_chrome(self) -> None:
        """Expand Results if its titlebar collapsed it.

        A collapsed *floating* Results is an empty 36px tool window (the
        collapse pins the dock to its titlebar height). Every path that
        means "show me the results" -- Show Results, a finishing run,
        floating the dock -- has to clear that first.
        """
        chrome = (self._dock_chrome or {}).get("Results")
        if chrome is not None and chrome["get"]()["minimized"]:
            chrome["set_minimized"](False)

    def _show_results_full(self):
        """Show/raise Results once. Idempotent: never hides.

        The command used to be a toggle, so pressing “Show Results” while
        Results was already visible closed it (and on a minimised slot it
        removed the tab). A floating Results is raised/maximised as its
        own window instead of being re-docked. Use the dock’s Close/X or
        View ▸ Panels to hide it.
        """
        dock = self._results_dock
        # Clear the collapsed chrome before the floating branch: a
        # minimized floating Results is a 36px empty window, and Show
        # Results must bring its content back, not maximise the empty
        # shell.
        self._restore_results_chrome()
        if dock.isFloating():
            dock.showNormal()
            dock.showMaximized()
            dock.raise_()
            dock.activateWindow()
            return
        if not dock.isVisible():
            dock.show()
        # A minimised/closed tab can come back outside the group; put it
        # back beside Data Tables and Run before raising it.
        if dock not in self.tabifiedDockWidgets(self._data_tables_dock):
            self.tabifyDockWidget(self._data_tables_dock, dock)
        run = getattr(self, "_run_dock", None)
        if run is not None and run not in self.tabifiedDockWidgets(
                self._data_tables_dock):
            self.tabifyDockWidget(self._data_tables_dock, run)
        dock.raise_()
        # Only stretch when there is content; never close an open panel.
        has_results = bool(getattr(self._results, "_report_data", None))
        if has_results:
            self._release_bottom_strip_ceiling()
            self.resizeDocks([dock], [self._results_full_target()],
                             Qt.Orientation.Vertical)
        else:
            self._release_bottom_strip_ceiling()
            self.resizeDocks([dock], [260], Qt.Orientation.Vertical)

    def _on_bottom_dock_visibility_changed(self, dock, visible: bool) -> None:
        """Apply the height policy of the bottom tab that came to front.

        `visibilityChanged` fires for a tabified dock when its tab is
        raised (True) or tucked behind a sibling (False), which is the
        only signal Qt gives for a tab click. Run keeps its content
        height; the other two release the group so the splitter can
        make Results smaller or larger. Deferred, so Qt's own tab swap
        has finished before the caps change.
        """
        if not visible or dock is None:
            return
        try:
            if dock.isFloating():
                return
        except RuntimeError:           # C++ object already torn down
            return
        self._bottom_active_dock = dock
        run = getattr(self, "_run_dock", None)
        if run is not None and dock is run and hasattr(run, "fit_height"):
            QTimer.singleShot(0, run.fit_height)
        else:
            QTimer.singleShot(0, self._release_bottom_strip_ceiling)

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
        limit = self._max_bottom_dock_height()
        active = getattr(self, "_bottom_active_dock", None) \
            or getattr(self, "_results_dock", None)
        for d in (getattr(self, "_results_dock", None),
                  getattr(self, "_data_tables_dock", None),
                  getattr(self, "_run_dock", None)):
            dock: Any = d
            if dock is None:
                continue
            if dock.isFloating():
                continue
            # A tab that is tucked behind does not size the strip;
            # leaving its cap in place would let the hidden Run tab
            # squeeze Results to its content height again.
            cap: int = 16777215
            if dock is active:
                # Run keeps its own content cap (never raise a lower cap
                # here, or the Simulate tab re-opens the empty field
                # under its last section).
                cap = int(dock.maximumHeight())  # type: ignore[union-attr]
                if cap > limit:
                    cap = limit
            if dock.maximumHeight() != cap:
                dock.setMaximumHeight(cap)

    def showEvent(self, event):
        super().showEvent(event)
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
                for title in ("Model Explorer", "Inspector"):
                    c = chrome.get(title)
                    if c is not None and not c["get"]()["minimized"]:
                        c["set_minimized"](True)
                self._compact_forced = True
            elif not compact and forced:
                for title in ("Model Explorer", "Inspector"):
                    c = chrome.get(title)
                    if c is not None:
                        c["set_minimized"](False)
                self._compact_forced = False
        except Exception:
            pass

    def _fit_status_readouts(self) -> None:
        """Show the status-bar run readouts only when there is room."""
        ro = getattr(self, "_readouts", None)
        if ro is not None:
            ro.setVisible(self.width() >= 1200)

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
            dock.setMinimumSize(int(width), max(0, dock.minimumHeight()))
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
            if geo is None or self._rbd is None:
                return
            _ = self.height()   # raises RuntimeError if the C++ object
            # was already deleted (tests and teardown close the window while
            # the deferred self-heal singleShot is still queued).
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
                self.resizeDocks(docks, [limit] * len(docks), Qt.Orientation.Vertical)
            for d in (self._results_dock, self._data_tables_dock,
                      self._run_dock):
                if d is not None and not d.isFloating():
                    d.setMaximumHeight(limit)
            self._statusbar.setMinimumHeight(
                self._statusbar.sizeHint().height())
        except Exception:
            pass

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
            # new window would be an empty 36px strip.
            self._restore_results_chrome()
            self._results_dock_height_before_float = dock.height()
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
                self.resizeDocks([dock],
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

    def _redock(self, dock) -> None:
        """Put a floating panel back where it was docked, at its own size."""
        entry = ((getattr(self, "_dock_chrome_entries", None) or {})
                 .get(dock)) or {}
        edge = entry.get("edge")
        dock.setFloating(False)
        if edge in ("left", "right"):
            width = entry.get("normal_min") or 320
            try:
                self.resizeDocks([dock], [width], Qt.Orientation.Horizontal)
            except Exception:                          # noqa: BLE001
                pass
        elif hasattr(dock, "fit_height"):
            QTimer.singleShot(0, dock.fit_height)
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
            dock.setMinimumSize(160, 90)
            dock.setMaximumSize(16777215, 16777215)
            # No collapse/expand (or re-float) controls on a detached
            # window: its own title bar and frame already do that job, and
            # an "expand up" arrow on a free-floating window is meaningless
            # (reported). Close stays.
            if entry is not None:
                for key in ("min_btn", "float_btn"):
                    widget = entry.get(key)
                    if widget is not None:
                        widget.setVisible(False)
                self._show_dock_back_button(dock, entry, True)
            return
        if entry is not None:
            for key in ("min_btn", "float_btn"):
                widget = entry.get(key)
                if widget is not None:
                    widget.setVisible(True)
            self._show_dock_back_button(dock, entry, False)
        if entry is not None:
            edge = entry.get("edge")
            if edge in ("left", "right"):
                self._set_dock_min_width(dock, entry.get("normal_min", 240))
            else:
                dock.setMinimumHeight(entry.get("normal_min", 0))
        dock.setMaximumSize(16777215, 16777215)
        # The bottom strip sizes itself from the panel that is showing.
        for attr in ("_run_dock", "_results_dock"):
            panel_dock = getattr(self, attr, None)
            if panel_dock is dock and hasattr(dock, "fit_height"):
                QTimer.singleShot(0, dock.fit_height)

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

    def _give_canvas_room(self):
        """Clamp the shared bottom dock strip so the central canvas
        keeps the majority of the window (PySide6's resizeDocks trips a
        generic-isinstance warning with dock subclasses, so constraints
        are set directly). Data Tables and Results are tabified into
        one strip; the user can still drag the splitter.

        Runs once at startup, before any model is loaded or run -- both
        tabs are at their emptiest here, so this only needs to fit that
        placeholder state, not a populated table. `_expand_results`
        grows it back up once there's real content to show."""
        try:
            docks = [d for d in (getattr(self, "_data_tables_dock", None),
                                 getattr(self, "_results_dock", None),
                                 getattr(self, "_run_dock", None))
                     if d is not None and not d.isFloating()]
            if docks:
                self.resizeDocks(docks, [150] * len(docks), Qt.Orientation.Vertical)
            # Startup widths for the side docks: Explorer keeps its
            # designed 320, Inspector gets 400 so its tab strip doesn't
            # scroll.
            side = [d for d in (getattr(self, "_explorer_dock", None),
                                getattr(self, "_inspector_dock", None))
                    if d is not None and not d.isFloating()]
            if side:
                self.resizeDocks(side, [320, 400][:len(side)], Qt.Orientation.Horizontal)
        except Exception:
            pass

    def _expand_results(self):
        """Give Results real estate when a run finishes -- the metric
        cards need ~460px; squashed into the 280px shared strip they
        wrap into unreadable mush. The target is clamped to what the
        window can actually give so the layout never overflows and
        clips the status bar off short screens. Only when docked and
        visible."""
        try:
            d = getattr(self, "_results_dock", None)
            if d is None or d.isFloating() or not d.isVisible():
                return
            # Clamped by what actually fits: an unclamped 480 request
            # overflows short maximized windows and Qt clips the status
            # bar, the window's last item.
            target = self._clamp_dock_height(
                min(480, max(280, self.height() - 300)))
            # Do not overwrite a height the user deliberately set. A
            # small/default strip still grows so the result cards fit;
            # a user-expanded strip keeps its size.
            if d.height() >= 280:
                return
            self.resizeDocks([d], [target], Qt.Orientation.Vertical)
        except Exception:
            pass

    def _present_results(self):
        """After a run: switch the ribbon to the Results tab, surface
        the dock and expand it -- results are what the user came for."""
        try:
            d = getattr(self, "_results_dock", None)
            if d is None:
                return
            # The ceiling must be current before any dock resize below
            # (the run itself doesn't resize the window, so resizeEvent
            # won't have fired).
            self._update_dock_max_heights()
            if hasattr(self, "_results_page_index") and \
                    self._ribbon.tab_bar.currentIndex() \
                    != self._results_page_index:
                self._ribbon.tab_bar.setCurrentIndex(
                    self._results_page_index)
                self._results_dock.show()
            self._restore_results_chrome()
            if not d.isVisible():
                # Re-surfacing after the user closed it: a previously
                # floated dock re-shows as its old sliver/bar geometry,
                # so re-float properly (sized+positioned) if it was
                # floating, plain-show otherwise.
                if d.isFloating():
                    self._float_results_properly(d, True)
                else:
                    d.setVisible(True)
            d.raise_()
            # Keep a user's larger height, but do not leave fresh results
            # in a 168 px strip where the overview cards are cut off. Only
            # grow a strip that is too small to show the first row.
            if d.height() < 300:
                self._expand_results()
        except Exception:
            pass
        # Self-heal after any layout change settles.
        QTimer.singleShot(0, self._ensure_status_bar_visible)



    def _preflight_issues(self) -> list[str]:
        """Model features the engine will reject, found before Run is
        pressed. The checks themselves are a pure function of the model
        and live in `ui/preflight.py` -- this is the window-facing
        wrapper, because the review's "why is Run greyed out?" finding
        could not be fixed while the answer lived inside the widget
        that has the question."""
        from raptor.gui.ui.preflight import preflight_issues
        return preflight_issues(self._rbd, self._diagram_dock.view,
                                self._preflight_elem_name)

    @staticmethod
    def _preflight_elem_name(kind: str, oid: int) -> str:
        """Best display name for an element id during preflight."""
        from PySide6.QtWidgets import QApplication  # noqa: F401
        return f"{kind} {oid}"

    def _validation_issues(self) -> list[str]:
        """Whole-model validation (not per-level): counting the visible
        level's view dicts would report 'No blocks defined' for a model
        whose blocks all live inside a hierarchy.

        Memoised per model revision, TOGETHER with the warnings: both
        halves used to run the API validator (which rebuilds the engine
        graph) independently, and `_validate_model` ran both, so one
        validation of a 3,500-block model paid for that build four times.
        Every caller of issues and warnings now shares one pass.
        """
        if self._rbd is None:
            return ["No model loaded"]
        rev = getattr(self, "_model_revision", 0)
        cached = getattr(self, "_validation_cache", None)
        if cached is not None and cached[0] == rev:
            return list(cached[1])
        from raptor.api.validate import issues as api_validate
        api_issues = api_validate(self._rbd)
        issues = self._compute_validation_issues(api_issues)
        warnings = self._warnings_from_api(api_issues)
        self._validation_cache = (rev, issues, warnings)
        return list(issues)

    def _compute_validation_issues(self, api_issues) -> list[str]:
        """The GUI's own findings, plus every ERROR from `api_issues`."""
        issues: list[str] = []
        if "SYSTEM_DATA" not in self._rbd.sections or \
                not self._rbd.sections["SYSTEM_DATA"].rows:
            issues.append("Missing SYSTEM_DATA section")
        try:
            block_rows = [r for r in self._rbd.sections["BLOCK_DATA"].rows
                        if int(float(r.get("ID", "0"))) != 0]
        except (KeyError, ValueError):
            block_rows = []
        # An events-only model (manual: RBDEvents fire once at kickoff,
        # no scheduler process) is fully supported and simulatable with
        # zero blocks -- tests/golden/Events is exactly this shape.
        # Flagging "No blocks defined" for it is a false positive.
        try:
            event_rows = self._rbd.sections["EVENT_DATA"].rows
        except KeyError:
            event_rows = []
        if not block_rows and not event_rows:
            issues.append("No blocks or events defined (in any "
                          "hierarchy level)")
        # Start->End connectivity is DELEGATED. This used to be a local
        # reachability walk here, wrapped in `except Exception: pass` --
        # which is how a whole capability ended up reachable only through
        # the GUI: an MCP or script user got no Start->End check at all,
        # so a model with broken hierarchy wiring was caught in the window
        # and silently accepted through the API.
        #
        # The local version also called `load_model`, so it was skipped
        # whenever the engine could not build the graph -- exactly the
        # models most likely to have broken wiring. The validator reads the
        # model's own LINK_DATA/NODE_DATA rows instead, so it needs no
        # engine import and cannot raise.
        from raptor.api.validate import _check_start_to_end_reachable
        # The rule is a WARNING: Raptor 7 runs these models, and
        # `preflight_warnings` already lists it. Extending every finding
        # here turned the same warning into a blocking issue and showed
        # it twice (once under problems, once under warnings).
        issues.extend(i.message for i in _check_start_to_end_reachable(
            self._rbd) if i.severity == "error")
        node_rows = self._rbd.sections.get("NODE_DATA")
        n_nodes = len(node_rows.rows) if node_rows else 0
        types = {r.get("Type") for r in node_rows.rows} if node_rows \
            else set()
        if n_nodes < 2 or not {"1", "3"} & types:
            issues.append("Model needs Start and End markers")
        # The view-based preflight rules are NOT in the API validator, so
        # they still run; the delegated ones are skipped because the
        # caller already holds their answer (see `preflight_issues`).
        from raptor.gui.ui.preflight import preflight_issues
        issues.extend(preflight_issues(
            self._rbd, self._diagram_dock.view,
            self._preflight_elem_name, api_issues=api_issues))
        # The API validator owns distribution-parameter and other
        # domain checks the GUI's own input rules do not cover. Without
        # this, "Model OK" could sit over a model `raptor validate`
        # rejects and Run would happily simulate it.
        for issue in api_issues:
            if issue.severity != "error":
                continue
            msg = issue.message.split(" [RAPTOR Studio, ", 1)[0].rstrip()
            text = f"{issue.element}: {msg}"
            if text not in issues:
                issues.append(text)
        return issues

    @staticmethod
    def _warnings_from_api(api_issues) -> list[str]:
        """Non-blocking findings from an already-computed API result."""
        def user_text(msg: str) -> str:
            """Drop the developer source citation from a user-facing warning.

            The API/MCP scrub the citation too, so dialogs and tool replies match;
            the Issues list must not quote source code at a modeller.
            """
            return msg.split(" [RAPTOR Studio, ", 1)[0].rstrip()

        return [f"{i.element}: {user_text(i.message)}" for i in api_issues
                if i.severity == "warning"]

    def _validate_model(self, quiet: bool = False):
        """Compute issues, refresh the badge; clicking the badge (or an
        explicit Validate press when invalid) opens the issues list."""
        if self._rbd is None:
            return
        # This pass supersedes any debounced one: stopping it here also
        # keeps the `processEvents` below from re-entering this method if
        # the timer happened to come due at the same moment.
        timer = getattr(self, "_validation_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        # A full pass over a large model takes seconds, and `quiet` runs
        # happen by themselves after an edit. Say so and show the wait
        # cursor: the window is busy, and an unexplained freeze reads as
        # a hang. (The load path has its own indicator, so skip this
        # there -- it would replace the load dialog's cursor.)
        announce = quiet and not getattr(self, "_loading_model", False)
        if announce:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._statusbar.showMessage("Checking the model…")
            QApplication.processEvents()
        try:
            issues = self._validation_issues()
            # This is the one place the whole model is validated anyway, so
            # hand the answer to `_model_is_valid` rather than making the next
            # `_command_state` repeat the work.
            self._validity_cache = (getattr(self, "_model_revision", 0),
                                    not issues)
            self._validity_dirty_since_check = False
            warnings = self._validation_warnings()
        finally:
            if announce:
                QApplication.restoreOverrideCursor()
        self._set_validation_badge(not issues, issues, warnings)
        # The Run panel's "Not ready — N problem(s)" line is computed
        # from the same issue list, so it is refreshed from here -- the
        # one chokepoint every validation passes through. It used to be
        # wired only to model load and to a Simulate ribbon tab change,
        # so fixing a problem cleared the badge but left the panel still
        # saying "Not ready" until you happened to switch tabs.
        # No-ops when the panel has not been built yet.
        self._update_readiness()
        if not issues and not warnings:
            if announce:
                # Replace the "Checking the model…" message: it has no
                # timeout, so leaving it would keep claiming work that has
                # finished -- e.g. while the user waits on a Weak Link
                # overlay and blames the wrong operation.
                self._statusbar.showMessage("Model checked — no issues", 3000)
            if not quiet:
                self._statusbar.showMessage("No issues: the model is ready to run", 4000)
            return
        if issues:
            self._statusbar.showMessage(
                f"Model has {len(issues)} issue(s) — click Issues for the list")
        elif announce:
            self._statusbar.showMessage(
                f"Model checked — {len(warnings)} warning"
                f"{'s' if len(warnings) != 1 else ''}", 4000)
        if not quiet:
            self._show_issues_dialog(issues, warnings)

    def _locate_issue(self, text: str) -> bool:
        """Select the element an issue is about, on whatever hierarchy
        level it lives. Issues name it as "Block 4 'Pump'" or "node 'split'
        (id=3)"; it is found by id. This used to take the text before the
        first colon as a NAME ("Block 4 'Pump'"), match nothing, and report
        the block as being on another hierarchy level."""
        import re
        v = self._diagram_dock.view
        kinds = {"block": "block", "node": "node", "event": "event",
                 "hier": "hier", "hierarchy": "hier"}
        # Links are the one element the diagram draws as a connector, not as
        # an item in the collections `locate_id` searches -- a capacity
        # warning ("Link 29 ...: Nominal flow must be a whole number!")
        # therefore navigated nowhere.
        m = re.search(r"\bLink\s+(\d+)\b", text, re.IGNORECASE)
        if m:
            return self._locate_link(int(m.group(1)))
        m = re.search(r"\b(Block|Node|Event|Hier(?:archy)?)\s+(\d+)\b",
                      text, re.IGNORECASE) or re.search(
            r"\b(block|node|event|hier(?:archy)?)\s+'[^']*'\s*\(id=(\d+)\)",
            text, re.IGNORECASE)
        if m:
            return v.locate_id(kinds[m.group(1).lower()], int(m.group(2)))
        m = re.search(r"\b(block|node)\s+'([^']+)'", text, re.IGNORECASE)
        return bool(m) and v.locate_element(m.group(1).lower(), m.group(2))

    def _locate_link(self, link_id: int) -> bool:
        """Navigate to a link: its source element, then the connector.

        A link has no row in the block/node/event/hierarchy collections the
        other locators use, so an Issues entry about one ("Nominal flow must
        be a whole number!") could not select anything and the click looked
        broken. Select the source element on its own level, then the
        connector itself, because the flow fields the warning is about are
        edited on the link.
        """
        rbd = self._rbd
        if rbd is None:
            return False
        rows = getattr(rbd.sections.get("LINK_DATA"), "rows", [])
        row = next((r for r in rows if str(r.get("ID")) == str(link_id)), None)
        if row is None:
            return False
        kind = {"Block": "block", "Node": "node", "Event": "event",
                "Hier": "hier", "RBDBlock": "block", "RBDNode": "node",
                "RBDEvent": "event", "RBDHier": "hier"}.get(
                    str(row.get("FromType", "")))
        try:
            from_id = int(float(row.get("FromID", 0)))
        except (TypeError, ValueError):
            from_id = 0
        found = bool(kind) and from_id != 0 and \
            self._diagram_dock.view.locate_id(kind, from_id)
        if self._diagram_dock.view.locate_link(link_id):
            return True
        return found

    def _validation_warnings(self) -> list[str]:
        """Non-blocking findings for the Issues list. Header-count drift is
        left out: it is a property of a FILE, re-synced on every save, so
        a model built in the app always has it until its first save, and
        there is nothing the user could do about it.

        Served from the same per-revision pass as the issues: the warning
        side is just the API's `warning` rows, which that pass already
        computed.
        """
        if self._rbd is None:
            return []
        rev = getattr(self, "_model_revision", 0)
        cached = getattr(self, "_validation_cache", None)
        if cached is None or cached[0] != rev:
            self._validation_issues()          # fills the cache
            cached = getattr(self, "_validation_cache", None)
        if cached is None or cached[0] != rev:
            return []
        return [w for w in cached[2] if "WARN: header " not in w]

    def _show_issues_dialog(self, issues: list[str],
                            warnings: list[str] | None = None):
        """Clickable issue list -- problems that stop a run, then warnings
        that do not; picking one locates the element when its name
        appears before the leading colon."""
        warnings = warnings or []
        from PySide6.QtWidgets import (QDialog, QVBoxLayout, QListWidget,
                                       QLabel)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Issues — {len(issues)} problem(s), "
                           f"{len(warnings)} warning(s)")
        lay = QVBoxLayout(dlg)
        head = QLabel("Click an issue to locate the element "
                      "(when named).")
        head.setObjectName("SectionNote")
        lay.addWidget(head)
        items = QListWidget()
        # Validation messages are often a full sentence with values and a
        # fix. A single elided line hid the actionable half.
        items.setWordWrap(True)
        items.setTextElideMode(Qt.TextElideMode.ElideNone)
        items.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        items.setUniformItemSizes(False)
        items.setSpacing(3)
        for msg in issues:
            items.addItem(msg)
        for msg in warnings:
            items.addItem(f"Warning — {msg}")
        def go(item):
            found = self._locate_issue(item.text())
            if found:
                dlg.accept()
            else:
                self._statusbar.showMessage(
                    "This issue is about the model as a whole, not one "
                    "element", 6000)
        items.itemClicked.connect(go)
        lay.addWidget(items, 1)
        dlg.resize(720, 420)
        exec_dialog(dlg)

    def _schedule_validation(self):
        """Debounced re-validation after edits (model_changed fires per
        keystroke on some editors).

        A load validates once at its end, inside the load indicator, so
        scheduling another pass here would only repeat that work (and can
        fire midway through the rebuild, while the indicator is up).
        """
        if getattr(self, "_loading_model", False):
            return
        self._ensure_validation_timer().start()

    def _ensure_validation_timer(self):
        """Create the debounce timer once, with both slots it must run.

        Both `_validate_model` (refresh the badge and the issue list) and
        `_sync_simulate_enabled_state` (re-enable Run when the fix landed)
        have to fire together; connecting them here keeps the load path and
        the edit path on the same timer instead of each adding a connection.
        """
        if not hasattr(self, "_validation_timer"):
            from PySide6.QtCore import QTimer
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(600)
            timer.timeout.connect(lambda: self._validate_model(quiet=True))
            timer.timeout.connect(self._sync_simulate_enabled_state)
            self._validation_timer = timer
        return self._validation_timer

    def _set_validation_badge(self, ok: bool, issues: list[str] | None = None,
                              warnings: list[str] | None = None):
        """Persistent clickable right-side badge: green tick or red
        cross with issue count; hover lists the first issues. The ribbon's
        Issues button shows the same state and opens the same list."""
        from PySide6.QtWidgets import QPushButton
        if not hasattr(self, "_validation_badge"):
            self._validation_badge = QPushButton()
            self._validation_badge.setFlat(True)
            self._validation_badge.setCursor(Qt.CursorShape.PointingHandCursor)
            self._validation_badge.clicked.connect(
                lambda: self._validate_model(quiet=False))
            self._statusbar.addPermanentWidget(self._validation_badge)
        # No model, nothing to vouch for: no "Model OK".
        self._validation_badge.setVisible(self._rbd is not None)
        c = active_colour()
        n_warn = len(warnings or [])
        if ok and n_warn:
            self._validation_badge.setText(
                f"✓ Model OK · {n_warn} warning{'s' if n_warn > 1 else ''}")
            tip = "\n".join((warnings or [])[:6])
        elif ok:
            self._validation_badge.setText("✓ Model OK")
            tip = "Model validates cleanly"
        else:
            n = len(issues or [])
            self._validation_badge.setText(f"✗ Invalid ({n})")
            tip = "\n".join((issues or [])[:6])
            if n > 6:
                tip += f"\n… click for all {n}"
        self._validation_badge.setToolTip(tip)
        colour = c["go"] if ok else c["stop"]
        self._validation_badge.setStyleSheet(
            f"QPushButton {{ color: {colour}; font-weight: 700;"
            f" padding: 0 8px; background: transparent; border: none;"
            f" text-align: right; }}")
        # The ribbon's Issues button carries the same state: a tick when
        # the model can run, a red cross when it cannot.
        act = getattr(self, "_validate_action", None)
        if act is not None:
            from raptor.gui.resources import icons
            act.setIcon(icons.check_badge_icon(c["go"]) if ok
                        else icons.issues_icon(c["stop"]))
            # Through the registry, which keeps any "Unavailable" reason.
            self._commands.set_tooltip(
                act,
                ("No problems stop a run" if ok else
                 f"{len(issues or [])} problem(s) stop a run")
                + (f", {n_warn} warning(s)" if n_warn else "")
                + ". Click for the list (F7)")

    #: Legacy alias names, kept so existing tests and sweeps that poke
    #: settings by key keep working. They now point at the namespaced
    #: locations in `ui/settings.py`.
    _BLOCK_DEF_KEY = "editing/block_defaults"

    #: Raptor factory defaults: "It will have infinite
    #: spares, no logistics delays, and no advanced features are
    #: activated... The default failure distribution is the exponential
    #: distribution... the lognormal distribution was chosen as the
    #: default repair distribution."
    _BLOCK_FACTORY_DEFAULTS: dict[str, Any] = {
        "fdist": 4, "fparams": [100.0, 0.0, 0.0],
        "rdist": 7, "rparams": [10.0, 2.0, 0.0],
        "sparing": "Infinite",
    }

    def _block_defaults(self) -> dict:
        """The saved defaults, cleaned. A file saved before all three parameters were kept holds a single
        `fparam` / `rparam`; it becomes the first parameter and the rest come from the factory values."""
        saved = settings.get_dict("editing/block_defaults")
        f = self._BLOCK_FACTORY_DEFAULTS

        def params(key: str, legacy: str, fallback: list[float]) -> list[float]:
            got = saved.get(key)
            if isinstance(got, (list, tuple)) and len(got) == 3:
                return [float(x) for x in got]
            if legacy in saved:
                return [float(saved[legacy]), *fallback[1:]]
            return list(fallback)
        return {"fdist": int(saved.get("fdist", f["fdist"])),
                "fparams": params("fparams", "fparam", f["fparams"]),
                "rdist": int(saved.get("rdist", f["rdist"])),
                "rparams": params("rparams", "rparam", f["rparams"]),
                "sparing": str(saved.get("sparing", f["sparing"]))}

    # ------------------------------------------------------------------
    # Annotation style defaults ("Set as Default for New Rectangles")
    # ------------------------------------------------------------------
    #: QSettings key, same pattern as `_BLOCK_DEF_KEY` above: a user
    #: preference, not model data, so it lives with the app's settings
    #: rather than in the .rbds (which must stay portable between Raptor
    #: Studio installs and plain Raptor).
    _ANNOTATION_DEF_KEY = "editing/annotation_style_defaults"

    def _annotation_style_defaults(self) -> dict[str, dict]:
        """Saved per-kind annotation styles, cleaned. A None value is
        dropped rather than kept as `{"fill_colour": None}`, so "no
        default saved" and "saved, with no fill" stay distinguishable.

        The per-kind allow-list is `diagram.ANNO_APPLICABLE_PROPS`, the
        same one the view filters with, so what lands in QSettings is
        exactly what that kind can actually render -- and a stale or
        hand-edited settings entry cannot inject an unknown key.
        """
        raw = settings.get_dict("editing/annotation_style_defaults")
        if not isinstance(raw, dict):
            return {}
        out: dict[str, dict] = {}
        for kind, props in raw.items():
            if not isinstance(props, dict):
                continue
            clean = {k: props[k] for k in ANNO_APPLICABLE_PROPS.get(
                str(kind), ()) if k in props and props[k] is not None}
            if clean:
                out[str(kind)] = clean
        return out

    def _on_annotation_style_default(self, kind: str, props):
        """Persist (or clear) the saved style for `kind`. `props` is None
        when the user reset it."""
        stored = self._annotation_style_defaults()
        if props is None:
            stored.pop(kind, None)
        else:
            clean = {k: v for k, v in dict(props).items()
                     if k in ANNO_APPLICABLE_PROPS.get(kind, ())
                     and v is not None}
            if clean:
                stored[kind] = clean
            else:
                stored.pop(kind, None)
        if stored:
            settings.set_value("editing/annotation_style_defaults", stored)
        else:
            settings.remove("editing/annotation_style_defaults")






    def _apply_autosave_timer(self):
        prefs = self._prefs()
        if prefs["autosave"]:
            self._autosave_timer.start(prefs["autosave_min"] * 60_000)
        else:
            self._autosave_timer.stop()

    def _recovery_path(self):
        import tempfile
        # `.rbds` (not the old `.rbd`): autosave must never silently drop
        # annotations, formulas/rates or the display-unit preference the
        # legacy format cannot carry. The extension also selects
        # `rbds_io` in `_open_model_any`.
        # One copy per window, or two windows would overwrite each
        # other's, and closing one would delete the other's.
        n = getattr(self, "_window_serial", 1)
        name = ("unsaved_recovery.rbds" if n <= 1
                else f"unsaved_recovery-{n}.rbds")
        return str(Path(tempfile.gettempdir())
                   / "raptor_studio_recovery" / name)

    def _clear_recovery(self):
        """Delete the rolling recovery copy on a clean exit (the original
        deletes its crash file on clean shutdown; a surviving copy is the
        signal that the previous session crashed)."""
        try:
            os.remove(self._recovery_path())
        except OSError:
            pass

    def _check_recovery(self):
        """Offer to open the recovery copies left by a previous session's
        crash. One is written per window for every dirty model (saved or
        not) and deleted when that window closes cleanly, so a surviving
        copy means a crash. The first window asks once for all of them;
        each opens in a window of its own."""
        if getattr(self, "_window_serial", 1) > 1 or \
                getattr(self, "_rbd", None) is not None:
            return
        # A load is in progress (its indicator pumps the event loop, so
        # this 600 ms startup timer can come due mid-load, before the new
        # model is assigned): the user is already opening a model, and
        # asking about crash copies on top of it would be noise.
        if getattr(self, "_loading_model", False):
            return
        folder = Path(self._recovery_path()).parent
        try:
            paths = sorted(str(p) for p in folder.glob("unsaved_recovery*.rbds"))
        except OSError:
            return
        if not paths:
            return
        from raptor.gui.ui.dialogs import question_box
        many = len(paths) > 1
        if not question_box(
                self, "Recovery Copy Found",
                (f"{len(paths)} auto-saved recovery copies from a previous "
                 f"session exist.\n\nOpen them now?\n\n" if many else
                 "An auto-saved recovery copy from a previous session "
                 "exists.\n\nOpen it now?\n\n") + "\n".join(paths),
                yes_text="Open recovery cop" + ("ies" if many else "y"),
                no_text="Not now", default_yes=True):
            return
        for path in paths:
            try:
                rbd = self._open_model_any(path)
            except Exception:
                continue
            target = self if self._rbd is None else self._spawn_window()
            target._load_into_app(rbd, None)
            target._statusbar.showMessage(
                "Recovered unsaved model — Save As to keep it")

    def _autosave_tick(self):
        """Write a separate rolling recovery copy -- only when dirty.
        Deliberately never touches the user's own file, matching the
        Raptor 7, whose autosave went to a dedicated crash file so
        "close without saving" always worked. Routing through the .rbds
        writer and a recovery path avoids overwriting an open .rbds with
        legacy .rbd text, mutating the live model in place, and dropping
        annotations."""
        if not (self._dirty and self._rbd is not None):
            return
        if getattr(self, "_loading_model", False):
            # A load is replacing the model; the old one's recovery copy
            # is not this session's unsaved work.
            return
        if getattr(self, "_run_in_progress", False):
            # Never write back into a model a live engine is reading.
            return
        try:
            self._diagram_dock.view.write_back_to_rbd(self._rbd)
            # Mandatory or deleted annotations would survive in the
            # recovery file (same rule as `_write_model`).
            self._diagram_dock.view.write_back_annotations()
            path = self._recovery_path()
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with refine_budget(GUI_REFINE_BUDGET_S):
                rbds_io.save_rbds(self._rbd, path)
            self._statusbar.showMessage(
                f"Auto-saved recovery copy — {path} "
                f"(restored automatically after a crash)", 8000)
        except Exception:
            pass  # auto-save must never crash the session

    def _sync_analysis_checkboxes(self):
        """Reflect SYSTEM_DATA flags onto the Simulate ribbon checks +
        sync the Weak Link metric/band widgets."""
        if self._rbd is None:
            return
        try:
            from raptor.core.fileio.typed import system_config_from_row
            cfg = system_config_from_row(
                self._rbd.sections["SYSTEM_DATA"].rows[0])
        except (ValueError, KeyError, IndexError, AttributeError):
            return
        mapping = {"weakAnalysis": cfg.weak_analysis,
                   "costAnalysis": cfg.cost_analysis,
                   "capacityAnalysis": cfg.capacity_analysis}
        for col, checked in mapping.items():
            act = self._analysis_actions.get(col)
            if act is not None and act.isChecked() != bool(checked):
                act.blockSignals(True)
                act.setChecked(bool(checked))
                act.blockSignals(False)
        # weak band widgets
        want_type = (cfg.node_anal_type or "Availability").strip()
        i = self._weak_type_combo.findText(want_type.capitalize())
        if i >= 0 and self._weak_type_combo.currentIndex() != i:
            self._weak_type_combo.blockSignals(True)
            self._weak_type_combo.setCurrentIndex(i)
            self._weak_type_combo.blockSignals(False)
        self._gy_spin.blockSignals(True)
        self._gy_spin.setValue(cfg.gy_threshold)
        self._gy_spin.blockSignals(False)
        self._yr_spin.blockSignals(True)
        self._yr_spin.setValue(cfg.yr_threshold)
        self._yr_spin.blockSignals(False)
        self._results.set_weak_settings(want_type, cfg.gy_threshold,
                                        cfg.yr_threshold)

    def _weak_metric(self):
        """All Weak Link metrics from the last result set.

        Returns `(blocks, nodes, events, hierarchies, metric_name, cfg)`,
        or None with the reason on the status bar. Blocks, nodes, events
        and hierarchies are each `{id: mean metric}`; Raptor 7 colours
        all four and prints the value under each.

        Memoised per result set and metric/thresholds: on a large model
        the cross-run average walks every element in every run (about a
        second at 3,500 blocks x 100 runs). Toggling the overlay off and
        on again, or re-banding after a threshold edit, used to pay that
        every time; a new batch (or a different metric) is a new key.
        """
        data = self._results._report_data or []
        runs = [d.weak_stats for d in data if d.weak_stats]
        node_runs = [d.weak_nodes for d in data if d.weak_nodes]
        event_runs = [d.weak_events for d in data if d.weak_events]
        hier_runs = [d.weak_hiers for d in data if d.weak_hiers]
        if not runs and not node_runs and not event_runs:
            self._statusbar.showMessage(
                "No weak-link data — enable Weak Link in the Run panel's "
                "Analysis section and run again")
            return None
        from raptor.core.fileio.typed import system_config_from_row
        try:
            cfg = system_config_from_row(
                req(self._rbd).sections["SYSTEM_DATA"].rows[0])
        except (ValueError, KeyError, IndexError):
            self._statusbar.showMessage("No SYSTEM_DATA row — cannot "
                                        "read weak-link settings")
            return None
        metric_name = canonical_analysis(cfg.node_anal_type)
        key = (id(data), len(data), metric_name,
               cfg.gy_threshold, cfg.yr_threshold)
        cached = getattr(self, "_weak_metric_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        cold = not getattr(self, "_run_in_progress", False)
        if cold:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._statusbar.showMessage("Computing Weak Link values…")
            # Paint ONLY the status bar: `processEvents()` here would let a
            # second click re-enter the toggle while the metric is half
            # computed.
            self._statusbar.repaint()
        try:
            # Per Raptor 7: thresholds are fractions of the METRIC itself
            # (e.g. Availability >= GY -> Green). The bucket sets live in
            # `core.engine.weak_metric` -- they used to be spelled out here,
            # and this copy kept `idle` in the Dependability denominator while
            # the results tab dropped it, so the same block could be coloured
            # green in the table and red on the canvas.
            def ids_of(run_list):
                return set().union(*(set(r) for r in run_list)) \
                    if run_list else set()

            block_ids = ids_of(runs)
            node_ids = ids_of(node_runs)
            event_ids = ids_of(event_runs)
            hier_ids = ids_of(hier_runs)
            block_missions = [d.missions_attempted for d in data if d.weak_stats]
            node_missions = [d.missions_attempted for d in data if d.weak_nodes]
            event_missions = [d.missions_attempted for d in data if d.weak_events]
            hier_missions = [d.missions_attempted for d in data if d.weak_hiers]
            result = (
                cross_run_weak_metric(runs, block_ids, metric_name,
                                      missions=block_missions),
                cross_run_node_metric(node_runs, node_ids, metric_name,
                                      missions=node_missions),
                cross_run_event_metric(event_runs, event_ids, metric_name,
                                       missions=event_missions),
                cross_run_node_metric(hier_runs, hier_ids, metric_name,
                                      missions=hier_missions),
                metric_name, cfg,
            )
        finally:
            if cold:
                QApplication.restoreOverrideCursor()
        self._weak_metric_cache = (key, result)
        return result

    def _spread_overlay(self):
        """Band the ACTIVE overlay by its own quartiles.

        Spread used to fall through to Weak Links whenever Cost was not
        current, so pressing it with Spares showing silently turned Weak
        Links on and rewrote the model thresholds. Capacity and Spares do
        not have a meaningful quartile band here; say so instead of
        switching the user's overlay.
        """
        if self._capacity_overlay_action.isChecked():
            self._statusbar.showMessage(
                "Capacity bands are fixed at Green < 70%, Yellow < 90%, "
                "Red >= 90%; there are no spread thresholds to set", 8000)
            return
        if self._spares_overlay_action.isChecked():
            self._statusbar.showMessage(
                "Spares is a flag (any exhausted time is Red), not a "
                "quartile band; there is nothing to spread", 8000)
            return
        if self._importance_overlay_action.isChecked():
            self._statusbar.showMessage(
                "Importance bands are already relative to the largest "
                "value in this run (Red >= 2/3, Yellow >= 1/3); a quartile "
                "split would not add information", 8000)
            return
        if self._cost_overlay_action.isChecked():
            self._cost_spread = True
            self._on_cost_overlay_toggled(True)
            return
        if self._weak_color_action.isChecked():
            self._spread_weak_thresholds()
            return
        self._statusbar.showMessage(
            "Choose an overlay first: Spread applies to Weak Links or Cost",
            8000)

    def _spread_weak_thresholds(self):
        """Results > Weak Link > Spread: Green and Red at the quartiles of
        this run's per-block metric, so the colours separate the blocks.
        A highly available model put every block above 0.95, all Green,
        which said nothing about which one was weakest."""
        import math
        import statistics
        found = self._weak_metric()
        if found is None:
            return
        metric, _nodes, _events, _hiers, metric_name, _cfg = found
        vals = sorted(v for v in metric.values() if math.isfinite(v))
        if len(vals) < 2 or vals[0] == vals[-1]:
            self._statusbar.showMessage(
                f"Every block has the same {metric_name.lower()} — there is "
                f"nothing to spread", 6000)
            return
        q1, _median, q3 = statistics.quantiles(vals, n=4, method="inclusive")
        if q3 == q1:                 # most blocks tie: split at the ends
            q1, q3 = vals[0] + 1e-9, vals[-1]
        # Red is strictly below Y/R, Green at or above G/Y. The model's own
        # rule refuses Y/R above G/Y at any moment, so move whichever keeps
        # that true first.
        order = [("YRthreshold", q1), ("GYthreshold", q3)]
        if q1 > self._gy_spin.value():
            order.reverse()
        self._apply_threshold_edits(order, "Spread Weak Link bands")
        red = sum(v < q1 for v in vals)
        green = sum(v >= q3 for v in vals)
        self._statusbar.showMessage(
            f"Weak Link bands spread by quartile: Green ≥ {q3:.6g} "
            f"({green} blocks), Red < {q1:.6g} ({red} blocks)", 8000)

    def _toggle_weak_colors(self):
        # Explicit `setChecked` throughout (never relying on Qt's own
        # auto-toggle-on-click) -- several existing call sites invoke
        # this as a plain Python method, not through the QAction's own
        # `.trigger()`, which wouldn't flip its checked state at all;
        # and a checkable action clicked for Raptor 7 would auto-toggle
        # before this even runs, so an early "no data" return must
        # explicitly correct it back to unchecked or the ribbon shows
        # a stuck pressed-looking button with nothing actually shown.
        v = self._diagram_dock.view
        if getattr(v, "_weak_colors_on", False):
            v.clear_result_overlays()
            self._weak_color_action.setChecked(False)
            self._statusbar.showMessage("Weak Link colouring off")
            self._update_overlay_chip()
            return
        found = self._weak_metric()
        if found is None:
            self._weak_color_action.setChecked(False)
            return
        (metric, node_metric, event_metric, hier_metric,
         metric_name, cfg) = found
        gy, yr = cfg.gy_threshold, cfg.yr_threshold
        from raptor.gui.ui import settings
        digits = settings.get_int("appearance/weak_link_digits", 3)
        self._deactivate_other_canvas_overlays(except_kind="weak")
        v.clear_result_overlays()
        v._weak_colors_on = True
        self._weak_color_action.setChecked(True)
        v.apply_weak_colors(
            metric, gy, yr, metric_name=metric_name,
            node_metric=node_metric, event_metric=event_metric,
            hier_metric=hier_metric, digits=digits)
        all_values = [*metric.values(), *node_metric.values(),
                      *event_metric.values(), *hier_metric.values()]
        worst = min(all_values) if all_values else 1.0
        self._statusbar.showMessage(
            f"Weak Link colouring on ({metric_name}) — lowest value "
            f"{worst:.6g} (Green ≥ {gy:.6g} ≥ Yellow ≥ {yr:.6g} > Red)")
        self._update_overlay_chip()

    def _update_overlay_chip(self) -> None:
        """Status-bar readout of the one active canvas overlay."""
        chip = getattr(self, "_overlay_chip", None)
        if chip is None:
            return
        v = self._diagram_dock.view
        vis = getattr(v, "_last_result_visuals", None) or {}
        kind = "weak" if getattr(v, "_weak_colors_on", False) \
            else vis.get("kind")
        names = {"weak": "Weak Links", "capacity": "Capacity",
                 "cost": "Cost", "spares": "Spares",
                 "importance": "Importance"}
        if kind:
            chip.setText(f"Canvas: {names.get(kind, kind)}")
            chip.show()
        else:
            chip.clear()
            chip.hide()

    def _on_importance_overlay_toggled(self, on: bool):
        v = self._diagram_dock.view
        if not on:
            v.clear_result_overlays()
            self._statusbar.showMessage("Importance colouring off")
            self._update_overlay_chip()
            return
        data = getattr(self._results, "_importance", None) or {}
        metric = getattr(self._results, "_importance_metric", "Availability")
        entry = (data.get("metrics") or {}).get(metric)
        if not entry or not entry.get("rows"):
            self._statusbar.showMessage(
                "No importance measures in this run. Switch on Run ▸ Analysis "
                "▸ Importance measures and run again — it costs one extra "
                "batch per block (two for Both).", 12000)
            self._uncheck_overlay("_importance_overlay_action")
            return
        # Colour by the SAME measure the results tab ranks by, so the canvas
        # and the table cannot disagree. `ranking_measure` prefers
        # Vesely-Fussell because Birnbaum saturates in a series model.
        from raptor.api.importance import RANKING_LABELS, ranking_measure
        rows = entry["rows"]
        key = ranking_measure(rows)
        measure = RANKING_LABELS[key]
        values = {int(r["block_id"]): float(r[key]) for r in rows
                  if r.get(key) is not None}
        if not values:
            self._statusbar.showMessage(
                f"No {measure} values to colour: every block is undefined "
                f"for this run")
            self._uncheck_overlay("_importance_overlay_action")
            return
        self._deactivate_other_canvas_overlays(except_kind="importance")
        v.clear_result_overlays()
        v.apply_importance_colors(values, measure=measure,
                                  metric_name=metric)
        top_bid = max(values, key=values.get)
        top_name = next((r["block"] for r in rows
                         if int(r["block_id"]) == top_bid), str(top_bid))
        self._statusbar.showMessage(
            f"Importance colouring on ({measure}, on {metric}) — most "
            f"influential block: {top_name} ({values[top_bid]:.6g}). "
            f"Red means 'fix this first', not 'this is failing'.")
        self._update_overlay_chip()

    def _on_importance_metric_changed(self, label: str) -> None:
        """Keep the canvas on the metric the Importance tab is showing.

        The overlay colours by the selected metric's rows, so switching
        Availability -> Reliability left the diagram coloured by a metric
        the table no longer displayed, with a status line still naming it.
        Only re-applies when the overlay is actually on: turning it on
        later reads the current metric anyway.
        """
        action = getattr(self, "_importance_overlay_action", None)
        if action is None or not action.isChecked():
            return
        self._on_importance_overlay_toggled(True)

    def _deactivate_other_canvas_overlays(self, except_kind: str):
        """The canvas has exactly one active colour overlay at a time
        (`DiagramView._last_result_visuals`'s own single-slot design):
        Weak Link, Capacity, Cost and Spares, all on the Results ribbon's
        Show on Canvas group. Called by whichever one is about to turn on, so the
        other three's own UI (checked state) doesn't go stale. Doesn't
        itself clear the canvas -- the caller immediately overwrites with
        its own colours, so an intermediate clear would just flicker."""
        v = self._diagram_dock.view
        if except_kind != "weak" and getattr(v, "_weak_colors_on", False):
            v._weak_colors_on = False
            self._weak_color_action.setChecked(False)
        for kind in ("capacity", "cost", "spares", "importance"):
            if kind != except_kind:
                self._uncheck_overlay(f"_{kind}_overlay_action")

    def _on_capacity_overlay_toggled(self, on: bool):
        v = self._diagram_dock.view
        if not on:
            v.clear_result_overlays()
            self._statusbar.showMessage("Capacity colouring off")
            self._update_overlay_chip()
            return
        caps = [d.capacity for d in (self._results._report_data or [])
                if d.capacity is not None]
        if not caps:
            self._statusbar.showMessage(
                "No capacity data — enable Capacity in the Run panel's "
                "Analysis section and run again")
            self._uncheck_overlay("_capacity_overlay_action")
            return
        usage: dict[int, float] = {}
        capability: dict[int, float] = {}
        node_ids = {nid for cap in caps for nid in cap.nodes}
        for nid in node_ids:
            per_run = [cap.nodes[nid] for cap in caps if nid in cap.nodes]
            # Only capacity nodes ("Unrestricted flow" off) have a
            # utilisation: an unrestricted node is an infinite pipe, which
            # Raptor 7 reports as N/A. Colouring those 0 % green made a
            # model with no capacity nodes read "highest utilization 0.0 %".
            if not per_run or any(s.full_flow for s in per_run):
                continue
            usage[nid] = sum(s.usage for s in per_run) / len(per_run)
            capability[nid] = (sum(s.capability for s in per_run)
                               / len(per_run))
        if not usage:
            self._statusbar.showMessage(
                "No capacity nodes to colour: every node has unrestricted "
                "flow (an infinite pipe). Untick \"Unrestricted flow\" on a "
                "node in the Inspector and give its incoming links a "
                "maximum flow, then run again.", 12000)
            self._uncheck_overlay("_capacity_overlay_action")
            return
        self._deactivate_other_canvas_overlays(except_kind="capacity")
        v.clear_result_overlays()
        v.apply_capacity_colors(usage, capability)
        worst = max(usage.values()) if usage else 0.0
        self._statusbar.showMessage(
            f"Capacity colouring on — highest utilization "
            f"{worst * 100:.1f}% (Green < 70% · Yellow < 90% · Red ≥ 90%)")
        self._update_overlay_chip()

    def _on_cost_overlay_toggled(self, on: bool):
        v = self._diagram_dock.view
        if not on:
            v.clear_result_overlays()
            self._statusbar.showMessage("Cost colouring off")
            self._update_overlay_chip()
            return
        costs = [d.cost for d in (self._results._report_data or [])
                if d.cost is not None]
        if not costs:
            self._statusbar.showMessage(
                "No cost data — enable Cost Analysis in Project "
                "Settings and run again")
            self._uncheck_overlay("_cost_overlay_action")
            return
        cost_by_block: dict[int, float] = {}
        block_ids = {bid for c in costs for bid in c.blocks}
        for bid in block_ids:
            per_run = [c.blocks[bid].total for c in costs if bid in c.blocks]
            if per_run:
                cost_by_block[bid] = sum(per_run) / len(per_run)
        self._deactivate_other_canvas_overlays(except_kind="cost")
        v.clear_result_overlays()
        spread = getattr(self, "_cost_spread", False)
        v.apply_cost_colors(cost_by_block, spread=spread)
        top = max(cost_by_block.values()) if cost_by_block else 0.0
        self._statusbar.showMessage(
            f"Cost colouring on{' (spread by quartile)' if spread else ''}"
            f" — most expensive block ${top:,.2f} total life-cycle cost")
        self._update_overlay_chip()

    def _on_spares_overlay_toggled(self, on: bool):
        v = self._diagram_dock.view
        if not on:
            v.clear_result_overlays()
            self._statusbar.showMessage("Spares colouring off")
            self._update_overlay_chip()
            return
        runs = [d.weak_stats for d in (self._results._report_data or [])
               if d.weak_stats]
        costs = [d.cost for d in (self._results._report_data or [])
                if d.cost is not None]
        if not runs:
            self._statusbar.showMessage(
                "No weak-link data — enable Weak Link in the Run panel's "
                "Analysis section and run again (Spares reads the "
                "same per-block state-time data)")
            self._uncheck_overlay("_spares_overlay_action")
            return
        exhausted_frac: dict[int, float] = {}
        for bid in runs[0]:
            fracs = []
            for ws in runs:
                if bid not in ws:
                    continue
                total = sum(ws[bid][k] for k in
                           ("run", "standby", "idle", "pm", "pm_hold",
                            "repair", "rep_hold", "done"))
                fracs.append((ws[bid]["done"] / total) if total else 0.0)
            if fracs:
                exhausted_frac[bid] = sum(fracs) / len(fracs)
        sparing_cost: dict[int, float] = {}
        block_ids = {bid for c in costs for bid in c.blocks}
        for bid in block_ids:
            per_run = [c.blocks[bid].sparing for c in costs
                      if bid in c.blocks]
            if per_run:
                sparing_cost[bid] = sum(per_run) / len(per_run)
        self._deactivate_other_canvas_overlays(except_kind="spares")
        v.clear_result_overlays()
        v.apply_spares_colors(exhausted_frac, sparing_cost)
        flagged = sum(1 for f in exhausted_frac.values() if f > 0.0)
        self._statusbar.showMessage(
            f"Spares colouring on — {flagged} of {len(exhausted_frac)} "
            f"block(s) had spares/life-exhausted time this run")
        self._update_overlay_chip()

    def _start_background_run(self, num_runs: int):
        import threading
        if self._bg_thread is not None and self._bg_thread.is_alive():
            return
        # Taken on the GUI thread, before the worker starts.
        bg_model = self._model_for_engine(copy=True)
        main_manifest = self._build_run_manifest()
        state: dict = {"done": False, "progress": (0, num_runs),
                       "snapshot": None, "paused": False}
        # `resume_ev` cleared = paused (Event.wait blocks while clear);
        # Resume sets it, Pause clears it again.
        resume_ev = threading.Event()
        resume_ev.set()
        stop_ev = threading.Event()
        skip_ev = threading.Event()
        self._results.clear()   # fresh batch: no stale numbers
        self._diagram_dock.view.clear_result_overlays()
        self._reset_live_log()
        state["resume_ev"] = resume_ev
        state["stop_ev"] = stop_ev
        state["skip_ev"] = skip_ev
        self._reset_status_summary()
        self._start_run_eta()
        # Fresh batch: reset the "what did we last paint" trackers so the
        # first poll shows the new run's progress.
        self._bg_last_progress = None
        self._bg_last_bar = None
        self._bg_last_report_count = -1
        self._bg_state = state

        units = self._current_units()
        live_view = self._live_sim_action.isChecked()
        # Parallel workers: only when the user asked for them and no live
        # stepping view (the on_event hook cannot cross process
        # boundaries). Independent stream segments make replications
        # statistically independent; results are not bit-identical to a
        # sequential batch by design.
        from raptor.gui import cores
        workers = 1 if live_view else cores.chosen()
        # What the engine will actually use: `run_simulation` only takes
        # the parallel path when num_workers>1 and runs>1, so a
        # single-replication batch is shown as 1 thread even if the
        # preference is higher.
        state["workers"] = workers if (workers > 1 and num_runs > 1) else 1

        def worker():
            try:
                report_data: list = []
                report_notes: list[str] = []
                # Same Files & Reports opt-in + disk-streaming as the
                # synchronous path (created on the main thread via the
                # signal-connected helper, appended to from this worker
                # thread — file appends are GIL-atomic, same rule as the
                # list sinks below).
                streams = self._open_report_streams()
                # keep the raw stream objects (fresh stream is falsy —
                # never `or []` it, or the sink is lost)
                ao_lines = streams.get("ao")
                cap_lines = streams.get("capacity")
                cost_lines = streams.get("cost")
                run_metadata: dict = {}
                # Frozen on the MAIN thread before the worker starts --
                # the manifest must describe the model as it was when the
                # run was launched, not as it is when the worker gets
                # scheduled.
                run_metadata["manifest"] = main_manifest
                # Same "only plain data crosses the thread boundary" rule
                # as `state["snapshot"]`/`state["progress"]` below --
                # `report_sink` is appended to in place by the engine as
                # each run finishes, so exposing this same list reference
                # lets `_bg_poll_tick` (main thread) read the latest
                # cumulative stats without any cross-thread Qt calls.
                # `list.append()` is GIL-atomic; a poll landing mid-append
                # just reads one run behind, same as `progress` already can.
                state["report_data"] = report_data
                state["cap_lines"] = cap_lines
                state["ao_lines"] = ao_lines

                def progress(done, total):
                    state["progress"] = (done, total)
                    if (run_metadata.get("runaway_runs") and done < total
                            and not state.get("runaway_asked")):
                        # Pause the worker; the poll on the GUI thread asks (a thread cannot open a modal).
                        state["runaway_ask"] = (done, total)
                        resume_ev.clear()

                def on_event(sched, system_state, blocks, cap_data=None,
                             model=None):
                    # Only plain data crosses the thread boundary.
                    state["snapshot"] = (
                        sched.time,
                        [(b.id, getattr(b.op_status, "value",
                                        str(b.op_status)))
                         for b in blocks],
                        str(getattr(system_state, "color", "")))
                    # Raptor 7's status panels 2/3/4 (cycles /
                    # phase name / MISSION flag), plain tuples -- same
                    # thread rule as `snapshot`.
                    from raptor.core.engine.system_status import MissionStatus
                    state["phase_snapshot"] = (
                        int(getattr(system_state, "current_phase", 1) or 1),
                        getattr(system_state, "mission_status",
                                None) is MissionStatus.MISSION,
                        int(getattr(system_state, "term_counter", 0) or 0))
                    # Node/link/event/hier live statuses, plain maps --
                    # the canvas's own objects are a stale separate
                    # parse, so without these the background live view
                    # could only animate blocks.
                    if model is not None:
                        def _val(v):
                            return getattr(v, "value", str(v))

                        state["element_snapshot"] = (
                            {nid: _val(getattr(n, "status", None))
                             for nid, n in model.nodes_by_id.items()},
                            {link.object_num: _val(getattr(link, "status", None))
                             for link in model.links_by_id.values()},
                            {eid: _val(getattr(e, "op_status", None))
                             for eid, e in model.events_by_id.items()},
                            {hid: _val(getattr(h, "status", None))
                             for hid, h in model.hiers_by_id.items()})
                    # Live capacity for the diagram's node readouts
                    # (plain dict copies -- same thread rule).
                    if cap_data is not None:
                        state["cap_snapshot"] = cap_data
                    # Animation pacing: delay per processed event
                    # (manual B-11 Increase/Decrease Sim Speed). Uses the
                    # SAME helper as the synchronous path, which returns
                    # 0.0 while Animate is off — Raptor 7's rule
                    # (Raptor 7 deactivates its time slice
                    # without graphics forces max speed). Reading
                    # `_speed_state` directly here is what made the dial
                    # apply to background runs but not to the default
                    # foreground mode.
                    d = self._effective_event_delay()
                    if d:
                        time.sleep(d)

                def stop_check():
                    if not resume_ev.is_set():
                        state["paused"] = True
                        resume_ev.wait()
                        state["paused"] = False
                    return stop_ev.is_set()

                def skip_check():
                    # Clear-once: exactly one run is abandoned per Jump
                    # press (otherwise every later run would be skipped
                    # too, since the flag stays latched).
                    if skip_ev.is_set():
                        skip_ev.clear()
                        return True
                    return False

                logs, eor, fin = run_simulation_with_reports(
                    bg_model, num_runs=num_runs,
                    report_sink=report_data, notes_sink=report_notes,
                    progress=progress,
                    on_event=on_event if live_view else None,
                    ao_sink=streams.get("ao"),
                    capacity_sink=streams.get("capacity"),
                    cost_sink=streams.get("cost"),
                    collect_logs=self._report_stream_actions[
                        "event_log"].isChecked(),
                    metadata_sink=run_metadata,
                    # `workers` is already forced to 1 when live_view is
                    # on (above).
                    num_workers=workers,
                    stop_check=stop_check,
                    skip_check=skip_check,
                    ao_plot_interval=self._effective_run_interval(
                        "ao_interval"),
                    cost_plot_interval=self._effective_run_interval(
                        "cost_interval"),
                    keep_partial_logs=True)
                state["result"] = (logs, eor, fin, report_data,
                                   report_notes, ao_lines, cap_lines,
                                   cost_lines, units)
                # Importance measures run here, in the worker, after the
                # ordinary batch -- which is their baseline. Same rule as
                # `api.run`: the user's own results exist before the extra
                # batches are spent.
                _mode = str((getattr(bg_model, "experiment", None) or {})
                            .get("importance", "none"))
                if _mode not in ("none", "") and report_data:
                    from raptor.api import importance as _imp
                    try:
                        state["importance"] = _imp.analyse(
                            bg_model, report_data, mode=_mode,
                            runs=len(report_data), workers=workers,
                            stop_check=stop_ev.is_set,
                            progress=lambda d, t, lab: state.__setitem__(
                                "importance_progress", (d, t, lab)))
                    except Exception as exc:      # noqa: BLE001
                        # A failed importance pass must not lose the batch
                        # the user actually asked for.
                        report_notes.append(
                            f"importance measures were not calculated: {exc}")
                        state["importance"] = None
                state["streams"] = streams
                state["run_metadata"] = run_metadata
            except Exception as e:
                import traceback
                traceback.print_exc()
                state["error"] = "".join(
                    traceback.format_exception_only(type(e), e)).strip() \
                    + "\n" + traceback.format_exc(limit=8)
            finally:
                state["done"] = True

        self._bg_thread = threading.Thread(target=worker, daemon=True)
        self._bg_thread.start()
        self._bg_poll.start()
        self._run_in_progress = True
        self._refresh_commands()
        # Lock AFTER the flag exists: `_disable_run_panel_for_run` derives
        # the lock from `_run_or_step_active`, which is false until this
        # point. Calling it first left the whole panel editable during a
        # background batch.
        self._disable_run_panel_for_run()
        self._run_progress.setValue(0)
        self._run_progress.setVisible(True)
        self._run_progress_label.setText("0%")
        self._run_progress_label.setVisible(True)
        self._statusbar.showMessage(
            f"Background run started — {num_runs} run(s). Partial results "
            f"appear as runs complete; full results when the batch finishes.",
            10000)

    @staticmethod
    def _threads_label(n: int) -> str:
        """Status-bar worker count: "1 thread" / "N threads" (the parallel
        batch's processes; the label follows the UI's own "worker thread"
        wording)."""
        return f"{n} core" if n == 1 else f"{n} cores"

    def _ask_keep_going_after_runaway(self, done: int, total: int) -> bool:
        """A run ended on the anti-runaway bound: the model's termination condition may never be met, and every
        later run will take as long. True to go on with the remaining runs."""
        from raptor.gui.ui.dialogs import question_box
        # On several cores a worker reports the run-away run before the runs
        # around it are merged, so "run 0 of 40" is all `done` can say.
        which = (f"Run {done} of {total}" if done >= 1
                 else f"A run (of {total})")
        rest = (f"the other {total - done} run(s)" if done >= 1
                else "the remaining runs")
        return question_box(
            self, "Run did not finish normally",
            f"{which} did not meet its termination condition and was ended by the safety limit, so "
            f"its figures are not those of a completed run. Every remaining run is likely to take as long.\n\n"
            f"Keep going with {rest}?",
            yes_text="Keep going", no_text="Stop here")

    def _bg_poll_tick(self):
        state = self._bg_state
        if not state:
            return
        # The worker records importance progress; nothing displayed it, so a
        # background run spent its last minutes silently.
        self._apply_importance_progress(state)
        if state.get("runaway_ask") and not state.get("runaway_asked"):
            state["runaway_asked"] = True
            done, total = state["runaway_ask"]
            if not self._ask_keep_going_after_runaway(done, total):
                state["stop_ev"].set()
            state["resume_ev"].set()
        live = self._live_sim_action.isChecked()
        if state.get("snapshot"):
            snapshot = state["snapshot"]
            # Third slot (str(SystemColor)) feeds the status-bar square;
            # tolerate older 2-tuples (tests).
            if len(snapshot) == 3:
                t, statuses, sys_color = snapshot
            else:
                t, statuses = snapshot
                sys_color = None
            # System-status square: the snapshot's third slot carries
            # str(SystemColor) from the worker thread.
            from raptor.core.engine.system_status import SystemColor
            try:
                self._update_system_swatch(
                    SystemColor(sys_color) if sys_color else None)
            except ValueError:
                self._update_system_swatch(None)
            # Raptor 7's status panels 2/3/4 during a backgrounded
            # live run: plain tuple -> shim object, same reader the
            # synchronous path uses.
            phase_snap = state.get("phase_snapshot")
            if phase_snap is not None:
                class _Ph:
                    def __init__(self, idx, is_mission, cycles):
                        self.current_phase = idx
                        self.term_counter = cycles
                    mission_status = None

                ph = _Ph(*phase_snap)
                from raptor.core.engine.system_status import MissionStatus
                ph.mission_status = (MissionStatus.MISSION
                                     if phase_snap[1]
                                     else MissionStatus.NON_MISSION)
                self._update_phase_labels(ph)
            # Remembered across ticks (not just used on this one) so the
            # text-format decision below never depends on whether a
            # fresh snapshot happens to exist on this particular poll,
            # which would flip the status bar between two message formats
            # within the same run.
            self._bg_last_t = t
            if live:
                by_id = dict(statuses)

                class _Val:
                    def __init__(self, value):
                        self.value = value

                class _Shim:
                    def __init__(self, value):
                        self.op_status = _Val(value)

                shim = {bid: _Shim(by_id.get(bid, "Running"))
                        for bid in self._diagram_dock.view.blocks}
                if not self._diagram_dock.isVisible():
                    self._diagram_dock.show()
                # element_snapshot (plain maps) -> shim objects with the
                # same .status/.op_status attribute shape update_live_
                # states reads engine model objects with, so node/link/
                # event/hier colours animate in the background live view
                # too.
                elem = state.get("element_snapshot")
                dict_model = None
                if elem is not None:
                    nstat, lstat, estat, hstat = elem

                    class _S:
                        def __init__(self, v):
                            self.status = _Val(v)
                            self.op_status = _Val(v)

                    dict_model = {
                        "nodes": {nid: _S(v) for nid, v in nstat.items()},
                        "links": {num: _S(v) for num, v in lstat.items()},
                        "events": {eid: _S(v) for eid, v in estat.items()},
                        "hiers": {hid: _S(v) for hid, v in hstat.items()},
                    }
                self._diagram_dock.view.update_live_states(
                    shim, cap_data=state.get("cap_snapshot"),
                    model=dict_model)
        if state.get("progress"):
            done, total = state.get("progress", (0, 0))
            paused = bool(state.get("paused"))
            threads = self._threads_label(state.get("workers", 1))
            self._track_paused_eta(paused)
            # Only repaint when the count (or paused state) actually
            # changed -- the poll runs at 10 Hz and would otherwise
            # rewrite the same status text many times per batch.
            key = (done, total, paused)
            if live:
                # Live time advances every tick, so this one still
                # refreshes each poll.
                t = getattr(self, "_bg_last_t", 0.0)
                self._statusbar.showMessage(self._run_status_text(
                    f"Background ({threads}) — run {min(done + 1, total)} "
                    f"of {total}, t = {t:.3f}", done, total))
                self._bg_last_progress = key
            elif key != getattr(self, "_bg_last_progress", None):
                self._bg_last_progress = key
                self._statusbar.showMessage(self._run_status_text(
                    f"Background ({threads}) — {done} of {total} run(s) "
                    f"complete"
                    + (" (PAUSED)" if paused else ""), done, total))
            if (done, total) != getattr(self, "_bg_last_bar", None):
                self._bg_last_bar = (done, total)
                pct = 100 * done // max(total, 1)
                self._run_progress.setValue(pct)
                self._run_progress.setVisible(True)
                self._run_progress_label.setText(f"{pct}%")
                self._run_progress_label.setVisible(True)

        # Cross-run permanent readouts (Ao/MTBDE/MDT/G-Y-R) -- same "every
        # completed run's data, independent of any toggle" rule the
        # synchronous run path's own `_progress` callback already applies
        # (see that callback's comment: "readouts appeared frozen during
        # runs when this was gated on the checkbox"). The background path
        # had the identical bug: `report_data` grows inside the worker
        # thread, but nothing here ever read it before `state["done"]`,
        # so backgrounded runs left the status bar's Ao/MTBDE/MDT/G-Y-R
        # readouts frozen at
        # their pre-run values for the whole run, only jumping to the
        # final numbers at completion -- looked like "doesn't update".
        # Refreshed only when another run lands (report_data grows); no
        # point recomputing against unchanged data every 100 ms.
        report_data = state.get("report_data")
        if report_data and len(report_data) != \
                getattr(self, "_bg_last_report_count", -1):
            self._bg_last_report_count = len(report_data)
            self._update_status_summary(report_data, self._current_units())
            cap_lines = state.get("cap_lines")
            if cap_lines:
                # Live capacity in the background path too. Gated on
                # "a run landed", not every 100 ms poll.
                self._results.update_live_capacity(cap_lines)
                if not self._results_dock.isVisible():
                    self._results_dock.show()
            ao_lines = state.get("ao_lines")
            if ao_lines:
                # Live Ao too (Raptor 7 shows it; only Capacity had a
                # live update before).
                self._results.update_live_ao(ao_lines)
                if not self._results_dock.isVisible():
                    self._results_dock.show()

        if not state.get("done"):
            return
        self._bg_poll.stop()
        self._run_in_progress = False
        self._refresh_commands()
        self._run_progress.setVisible(False)
        self._run_progress_label.setVisible(False)
        self._clear_run_eta()
        self._hide_phase_labels()
        self._diagram_dock.view.clear_live_states()
        # Unlock the run settings: whether the run finished, failed or was
        # stopped, it is over, and leaving the panel locked would read as
        # "still running".
        self._reenable_run_panel()
        err = state.get("error")
        if err:
            msgbox.critical(self, "Simulation Failed",
                                 f"The background run failed:\n\n{err}")
            self._statusbar.showMessage("Run failed")
            return
        stopped = state["stop_ev"].is_set()
        # Same gap as the synchronous run path (see its own comment):
        # results still need parsing/rendering below, which can take a
        # visible moment on a large model -- without this the bar was
        # left showing the last "Background — ..." progress line.
        self._statusbar.showMessage("Processing results…")
        QApplication.processEvents()
        (logs, eor, fin, report_data, report_notes, ao_lines, cap_lines,
         cost_lines, units) = state["result"]
        self._push_weak_settings()
        # Same safety-bound surfacing as the synchronous path.
        if state.get("run_metadata", {}).get("hit_safety_bound"):
            report_notes.append(
                "Note: the run reached the simulation safety bound "
                "(t = 10,000,000) WITHOUT meeting its termination "
                "condition — results cover only the simulated portion. "
                "Check the model's termination condition; see Help ▸ "
                "Report Issue if this model should have ended earlier.")
            self._statusbar.showMessage(
                "WARNING: run stopped at the t=10,000,000 safety bound — "
                "termination condition never met", 12000)
        self._results._source_file_name = self._current_file or ""
        self._results.set_results(logs, eor, fin, report_data=report_data,
                                  units=units, notes=report_notes,
                                  ao_lines=ao_lines, cap_lines=cap_lines,
                                  cost_lines=cost_lines,
                                  manifest=(state.get("run_metadata") or
                                            {}).get("manifest"),
                                  importance=state.get("importance"))
        self._note_results_basis()
        self._remember_report_streams(state.get("streams") or {})
        self._results_show_action.setEnabled(bool(report_data))
        self._results_float_action.setEnabled(bool(report_data))
        self._sync_overlay_actions(report_data)
        # Do NOT auto-enable a canvas overlay here. A run should not
        # silently repaint the model (and must never override the
        # overlay the user explicitly chose). The Results ribbon offers
        # Weak Links/Capacity/Cost/Spares once their data exists.
        self._apply_hier_status(report_data)
        self._present_results()
        self._note_run_profile(len(report_data))
        self._update_status_summary(report_data, units)
        if stopped:
            if not report_data:
                self._statusbar.showMessage(
                    "Stopped before any run completed — no results", 10000)
            elif len(report_data) < len(logs):
                self._statusbar.showMessage(
                    "Stopped — partial event log kept for the interrupted "
                    "run", 10000)
            else:
                self._statusbar.showMessage(
                    f"Stopped after {len(logs)} run(s) — results ready")
        else:
            self._statusbar.showMessage(
                f"Completed {len(logs)} run(s) successfully — results "
                f"ready")

    def _toggle_bg_pause(self):
        # Pre-arm: Pause before any Run kicks off a stepped session
        # paused at t = 0, ready for Step/Multi/Run-To.
        bg_active = bool(getattr(self, "_bg_state", None))
        if (getattr(self, "_step_session", None) is None
                and not bg_active
                and self._rbd is not None):
            self._ensure_step_session()
            self._show_live_log_dock(True)
            self._refresh_live_step_view(self._step_session)
            return
        state = getattr(self, "_bg_state", {})
        event = state.get("resume_ev")
        if event is None:
            return
        if event.is_set():
            event.clear()          # pause
            self._pause_action.setText("Resume")
        else:
            event.set()            # resume
            self._pause_action.setText("Pause")
        self._update_readiness()

    def _stop_bg_run(self):
        # Sync (in-thread) runs pump the event loop and check this flag
        # between runs; threaded runs use their own event below.
        #
        # "This is a sync run, not a background one" is decided from
        # `_run_in_progress` (True for the whole lifetime of either run
        # kind) combined with a genuinely live (not-yet-`done`)
        # `_bg_state` check -- the same "bg_running" test
        # `keyPressEvent`'s own Esc handler uses.
        bg_state = getattr(self, "_bg_state", None)
        bg_running = bool(bg_state and not bg_state.get("done"))
        if not bg_running:
            if getattr(self, "_sync_stop", False) or \
                    getattr(self, "_run_in_progress", False):
                self._sync_stop = True
                self._statusbar.showMessage("Stop requested — finishing "
                                            "the current run…", 6000)
            return
        event = req(bg_state).get("resume_ev")
        if event is not None:
            event.set()            # wake a paused worker so it sees the stop
        stop = req(bg_state).get("stop_ev")
        if stop is not None:
            stop.set()

    def _jump_bg_run(self):
        if getattr(self, "_step_session", None) is not None:
            self._on_step_jump()
            return
        state = getattr(self, "_bg_state", {})
        event = state.get("skip_ev")
        if event is not None:
            res = state.get("resume_ev")
            if res is not None:
                res.set()       # wake a paused worker so it sees the jump
            event.set()
            self._statusbar.showMessage(
                "Jump — abandoning current run, starting next")

    def _sync_bg_poll_interval(self, delay):
        """`_bg_poll` (which samples the worker thread's latest event
        onto the canvas) would otherwise be fixed at 100ms regardless of
        the configured per-event delay, so any speed faster than 10
        events/sec (delay < 0.1s, reachable via Faster or Max) would
        have multiple real events overwrite `state["snapshot"]` between
        polls, dropping every one but the last and updating the canvas at
        a flat ~10Hz. Shrinks the poll interval to track the configured
        speed (never below the floor, to avoid hammering the event loop
        at Max)."""
        interval = (self._BG_POLL_INTERVAL_FLOOR if delay is None
                   else max(self._BG_POLL_INTERVAL_FLOOR,
                           min(self._BG_POLL_INTERVAL_DEFAULT,
                               int(delay * 1000))))
        self._bg_poll.setInterval(interval)

    def _set_animate(self, on: bool) -> None:
        """Animate toggle -> the engine's `live_view` and the speed dial's
        enabled state.

        This is APPLICATION state, and deliberately not a model edit.

        An earlier version wrote `SYSTEM_DATA/graphicsUse` here, to keep
        that column meaningful for a legacy Raptor opening our file. It
        was wrong, in a way only real use exposed: writing the model
        dirties the file and trips the edit-during-run guard, so with a
        step session live, clicking Animate threw a modal
        "Simulation in progress -- editing now will cancel it" every
        single time. A display toggle that interrupts you is not a
        display toggle.

        Raptor 7 is on the same side of this line. Its graphics
        checkbox lives in the Display module, and the setting it gates --
        the time slice -- is stored in `graphTimeSlice`, a VIEW column,
        alongside `zoomVal` and `xOrigin`. Panning the canvas and animating the run are the same
        category of decision: how to show the model, not what it is.

        So the state lives in the settings tree, under the same key the
        Preferences dialog's "Animate by default" row writes. `graphicsUse`
        stays in the file as the read-but-ignored legacy column the
        formatter round-trips, and nothing offers a control for it.
        """
        # With animation off the speed dial is meaningless (the original
        # deactivates its time slice on the same condition), so it goes
        # with it.
        self._set_speed_state_enabled(on)
        if not on and self._speed_state.get("delay") is not None:
            # Don't silently discard a rate the user dialled in; just
            # stop applying it. Re-enabling Animate restores it.
            self._sync_speed_label()
        settings.set_value("execution/default_animate", bool(on))
        # The Inspector's Display group shows the live state, which is
        # not on the model, so it has to be told.
        self._inspector.refresh_animate_row()
        self._sync_cores_box()
        self._update_readiness()
        from raptor.gui import cores
        self._statusbar.showMessage(cores.describe(on), 4000)

    def _set_speed_state_enabled(self, on: bool) -> None:
        """Enable/disable the speed dial with the Animate toggle.

        The disabled tooltip has to say WHY (the spec requires every
        disabled action to explain its unmet condition; see the internal
        UI blueprint), and re-enabling has to
        restore the button's OWN tooltip -- so the originals are captured
        once, not re-read from the action after being overwritten."""
        for act in (self._speed_slower, self._speed_reset_1,
                    self._speed_faster, self._speed_reset_max):
            if not hasattr(act, "_speed_tip"):
                act._speed_tip = act.toolTip()   # type: ignore[attr-defined]
            act.setEnabled(bool(on))
            if not on:
                act.setToolTip(
                    "Animation is off — the run goes at full speed. "
                    "Turn Animate on to set an animation speed.")
            else:
                act.setToolTip(act._speed_tip)   # type: ignore[attr-defined]

    def _effective_event_delay(self) -> float:
        """Seconds of real time to sleep per processed event, for BOTH
        run paths.

        Returns 0.0 (no throttle) whenever animation is off, matching the
        original's rule that a non-graphics run always goes at full
        speed (sets `timeSlice := 0.0000001` when
        graphics are off, having deactivated the field).

        Previously `_speed_state` was consulted ONLY inside the
        background worker, so the dial did nothing at all in the default
        foreground run mode.
        """
        if not self._live_sim_action.isChecked():
            return 0.0
        d = self._speed_state.get("delay")
        return float(d) if d else 0.0

    def _set_speed(self, delay):
        """`delay` = seconds per processed event; None = as fast as
        possible. Log-scale steps via _adjust_speed."""
        self._speed_state["delay"] = delay
        if self._bg_state.get("event_delay") is not None or delay:
            self._bg_state["event_delay"] = delay
        self._sync_bg_poll_interval(delay)
        self._sync_speed_label()

    def _sync_speed_label(self) -> None:
        lbl = getattr(self, "_speed_label", None)
        if not self._live_sim_action.isChecked():
            if lbl:
                lbl.setText("Speed: max (no animation)")
            return
        delay = self._speed_state.get("delay")
        if delay is None:
            self._statusbar.showMessage("Sim speed: maximum")
            if lbl:
                lbl.setText("Speed: max")
        else:
            self._statusbar.showMessage(
                f"Sim speed: ~{1.0 / delay:g} events/sec")
            if lbl:
                lbl.setText(f"Speed: {1.0 / delay:g} ev/s "
                            f"({delay:g}s/ev)")

    def _adjust_speed(self, direction: int):
        if not self._live_sim_action.isChecked():
            self._statusbar.showMessage(
                "Animation is off — the run goes at full speed")
            return
        d = self._speed_state.get("delay")
        if d is None:
            d = 0.1 if direction > 0 else None
            if d is None:
                self._set_speed(None)
                return
        else:
            d = d * (10.0 ** direction)
        d = max(0.001, min(d, 2.0))
        self._set_speed(d)

    def _select_all(self):
        v = self._diagram_dock.view
        if self._rbd is None:
            return
        # The view owns the list (blocks/nodes/events/hierarchies AND
        # annotations) so Ctrl+A and the canvas's own background-menu
        # "Select All" can never disagree about what "all" means.
        v.select_all()

    #: Supersampling for bitmap export / clipboard -- text must stay
    #: legible at 100%.
    _BITMAP_SCALE = 4

    def _render_scene_png(self, rect: QRectF, scale: int | None = None) \
            -> QPixmap:
        """Render `rect` of the diagram scene into a supersampled QPixmap.

        DELEGATED to `raptor.render.scene_pixmap`, which the headless
        render worker also calls. The two copies had diverged in three
        ways that all changed the output image, most importantly semantic
        zoom: this version forced labels, the worker's did not, so the same
        diagram could export with or without element labels depending on
        which door rendered it.

        The shared helper also applies the worker's edge cap, which this
        version lacked -- that cap is what stops a large `itemsBoundingRect()`
        (the canvas is infinite) from trying to allocate a multi-gigabyte
        pixmap.

        Background prefers the view's `backgroundBrush()`, which is how the
        grid and themed background are actually installed, falling back to
        the viewport palette."""
        from raptor.render.scene_pixmap import render_scene_to_pixmap
        return render_scene_to_pixmap(
            self._diagram_dock.view, rect,
            scale=(scale or self._BITMAP_SCALE))

    def _export_bitmap(self):
        if self._rbd is None:
            return
        dlg = QFileDialog(self, "Save Diagram as PNG")
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        fdg.theme_file_dialog(dlg)
        dlg.setNameFilter("PNG Images (*.png)")
        dlg.selectFile("diagram.png")
        if not dlg.exec() or not dlg.selectedFiles():
            return
        path = dlg.selectedFiles()[0]
        if not path:
            return
        # Full model rect (content), supersampled — never the
        # infinite-canvas world sceneRect (multi-GB pixmap -> OOM).
        view = self._diagram_dock.view
        rect = getattr(view, "_content_rect", None) or             QRectF(view._scene.itemsBoundingRect())
        pm = self._render_scene_png(QRectF(rect))
        if not pm.save(path, "PNG"):
            msgbox.critical(self, "Export Failed",
                                 f"Could not write {path}")
            return
        self._statusbar.showMessage(
            f"Saved diagram bitmap ({pm.width()}×{pm.height()}) — {path}")

    def _copy_view_to_clipboard(self):
        """Copy the CURRENT VIEWPORT (what you see) to the clipboard as
        a high-resolution PNG."""
        if self._rbd is None:
            return
        view = self._diagram_dock.view
        source = view.mapToScene(view.viewport().rect()).boundingRect()
        pm = self._render_scene_png(source)
        QApplication.clipboard().setPixmap(pm)
        self._statusbar.showMessage(
            f"Copied view to clipboard ({pm.width()}×{pm.height()} px)")

    _RECENT_KEY = "files/recent"
    #: The File view lists and searches them, so the list can be long.
    _RECENT_MAX = 50

    def _show_backstage(self, page: str = "Home") -> None:
        """The full-page File view (`ui/backstage.py`), on `page`."""
        if getattr(self, "_backstage", None) is None:
            from PySide6.QtWidgets import QDockWidget
            from raptor.gui.ui.backstage import Backstage
            self._backstage = Backstage(self)
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
                f"{os.path.basename(path)} is open in this window", 6000)
            return None
        if self._rbd is None or (self._current_file is None
                                 and not self._dirty):
            return self
        if settings.get("files/open_in", "new") == "this":
            return self
        return self._spawn_window()

    def _spawn_window(self):
        """A new, empty window beside this one, for another model."""
        from PySide6.QtCore import QPoint
        from raptor.gui import open_models
        win = type(self)()
        win.resize(self.size())
        win.move(self.pos() + QPoint(36, 36))
        open_models.keep(win)
        win.show()
        win.raise_()
        win.activateWindow()
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

    def _find_combo_refresh(self, _filter_text: str = ""):
        """Rebuild the Find Item backing list: every block, node, event
        and hierarchy, prefixed by kind and carrying its id, so Enter can
        resolve either a name or an id. Called whenever the model changes
        (load, edit, undo/redo) -- not on every keystroke; typing is
        handled by the Model Explorer's own filter, which is the only
        place the results are shown."""
        entries = []
        v = getattr(self, "_diagram_dock", None)
        if v is not None:
            v = v.view
            def add(prefix, items):
                for name, eid in items:
                    entries.append(f"{prefix}  {name} ({eid})")
            add("Block", sorted(((b.name, i) for i, b in v.blocks.items()),
                                key=lambda t: t[0].lower()))
            add("Node", sorted(((n.node_name, i) for i, n in v.nodes.items()),
                               key=lambda t: t[0].lower()))
            add("Event", sorted(((e.name, i)
                                 for i, e in v.events.items()),
                                key=lambda t: t[0].lower()))
            add("Hier", sorted(((h.name, i) for i, h in v.hiers.items()),
                               key=lambda t: t[0].lower()))
        self._find_model.setStringList(entries)
        self._find_edit.setEnabled(bool(entries))

    def _on_find_text_changed(self, text: str) -> None:
        """Live-refine the Model Explorer tree from the Find box
        (refine, don't replace; match hierarchy + children). This is
        where the search results appear -- the box itself deliberately
        shows no dropdown, so the list of names is not drawn twice."""
        dock = getattr(self, "_explorer", None)
        if dock is not None:
            dock.filter_items(text)

    #: "Block  ", "Node  ", ... -- the prefixed form the (now
    #: popup-less) completer still produces, kept so an old entry, a
    #: pasted string, or a test's text still resolves.
    _FIND_PREFIXES = ("Block", "Node", "Event", "Hier")

    def _resolve_find_text(self, text: str) -> tuple[str, str] | None:
        """What the Find box holds -> `(kind, name)`, or None.

        Accepts the prefixed form (`Block  Pump (12)`), a bare name
        (`Pump`), a bare id (`12`), and a name carrying its id
        (`Pump (12)`) -- which is what the Explorer rows now show, so a
        row the user clicked through from is a valid thing to press
        Enter on."""
        text = text.strip()
        if not text:
            return None
        kind = None
        for p in self._FIND_PREFIXES:
            if text.startswith(p + "  "):
                kind, text = p, text[len(p) + 2:].strip()
                break
        if not text:
            return None
        # A bare id first: `12`, or the Explorer row form `Pump (12)`.
        eid = None
        name = text
        if text.isdigit():
            eid, name = int(text), ""
        else:
            m = re.fullmatch(r"(.*?)\s*\((\d+)\)\s*", text)
            if m:
                name, eid = m.group(1).strip(), int(m.group(2))
        v = self._diagram_dock.view
        if eid is not None and not name:
            # An id alone identifies exactly one element, if it exists.
            for k, table, attr in (("Block", v.blocks, "name"),
                                   ("Node", v.nodes, "node_name"),
                                   ("Event", v.events, "name"),
                                   ("Hier", v.hiers, "name")):
                if eid in table:
                    return k, getattr(table[eid], attr)
            return None
        if kind is not None:
            return kind, name
        if eid is not None:
            # Name AND id: the id is authoritative when it names something.
            for k, table, attr in (("Block", v.blocks, "name"),
                                   ("Node", v.nodes, "node_name"),
                                   ("Event", v.events, "name"),
                                   ("Hier", v.hiers, "name")):
                if eid in table and \
                        getattr(table[eid], attr).lower() == name.lower():
                    return k, name
            return None
        # A bare name: first match wins, blocks before nodes (a name is far
        # more often a block's) -- same order the completer listed.
        for k, table, attr in (("Block", v.blocks, "name"),
                               ("Node", v.nodes, "node_name"),
                               ("Event", v.events, "name"),
                               ("Hier", v.hiers, "name")):
            if any(getattr(o, attr).lower() == name.lower()
                   for o in table.values()):
                return k, name
        return None

    def _on_find_submit(self, text: str):
        resolved = self._resolve_find_text(text)
        if resolved is None:
            self._statusbar.showMessage(
                f"No element matches '{text.strip()}'", 4000)
            return
        kind, name = resolved
        v = self._diagram_dock.view

        # Block/Node go through locate_element (navigates into whatever
        # hierarchy level the element actually lives on first, if not
        # the one currently shown -- v.blocks/v.nodes are level-scoped,
        # so a same-level-only lookup would silently do nothing for an
        # element on another level).
        if kind in ("Block", "Node"):
            v.locate_element(kind.lower(), name)
            self._find_edit.clear()
            return

        target = None
        if kind == "Event":
            target = next((it for eid, it in v._event_items.items()
                           if v.events[eid].name == name), None)
        elif kind == "Hier":
            hid = next((h for h in v.hiers if v.hiers[h].name == name),
                       None)
            target = v._hier_items.get(hid)
        if target is None:
            return
        for it in list(v._scene.selectedItems()):
            it.setSelected(False)
        target.setSelected(True)
        v.centerOn(target)
        self._find_edit.clear()

    def _set_zoom_percent(self):
        """Navigate ▸ Zoom Percent: absolute zoom entry."""
        from PySide6.QtWidgets import QInputDialog
        cur = round(self._diagram_dock.view.transform().m11() * 100)
        pct, ok = QInputDialog.getInt(
            self, "Zoom Percent", "Zoom (%):", max(cur, 1), 1, 100000)
        if not ok:
            return
        v = self._diagram_dock.view
        target = pct / 100.0
        curm = v.transform().m11()
        if curm > 0:
            v.scale(target / curm, target / curm)
            v._user_zoomed = True
            v._apply_lod()

    def _fit_all(self):
        self._diagram_dock.view.fit_all()

    def _zoom_to_selection(self):
        """Fit the current selection in the diagram window."""
        n = self._diagram_dock.view.zoom_to_selection()
        if n:
            self.statusBar().showMessage(
                f"Zoomed to {n} selected element(s)", 4000)
        else:
            self.statusBar().showMessage(
                "Select one or more elements first, then Zoom to Selection",
                4000)

    def _zoom_to_hierarchy(self):
        """Descend into the selected hierarchy oval."""
        if not self._diagram_dock.view.zoom_to_hierarchy():
            self.statusBar().showMessage(
                "Select a hierarchy oval first, then Zoom to Hierarchy", 4000)

    def _queue_inspector_selection(self, kind: str, oid: int) -> None:
        """Queue one Inspector rebuild on the debounce timer (see
        `_on_canvas_block_selected`)."""
        if kind == "block":
            self._pending_block_selection = int(oid)
            self._pending_inspector_selection = None
        else:
            self._pending_block_selection = None
            self._pending_inspector_selection = (kind, int(oid))
        self._inspector_select_timer.start()

    def _on_canvas_block_selected(self, block_id: int, _name: str):
        """Queue the Inspector rebuild instead of doing it in the click.

        A click costs ~9 ms of Inspector rebuild on a large model, and a
        rubber-band drag or arrow-key walk emits a selection for every
        element it passes. Coalescing means the rebuild happens once, for
        the element the user stopped on; the click itself returns at once
        and the canvas repaint is not queued behind seven tab builders.
        """
        self._queue_inspector_selection("block", block_id)

    def _flush_canvas_block_selection(self) -> None:
        bid = self._pending_block_selection
        other = self._pending_inspector_selection
        self._pending_block_selection = None
        self._pending_inspector_selection = None
        v = self._diagram_dock.view
        # The model may have been rebuilt (level change, reopen) while the
        # click was queued; a stale id must not show another model's block.
        if bid is not None:
            if bid in v.blocks:
                self._inspector.show_block(bid, v.blocks)
            return
        if other is None:
            return
        kind, oid = other
        if kind == "node" and oid in v.nodes:
            self._inspector.show_node(oid, v.nodes)
        elif kind == "event" and oid in v.events:
            self._inspector.show_event(v.events[oid])
        elif kind == "hier" and oid in v.hiers:
            self._inspector.show_hier(v.hiers[oid])
        elif kind == "annotation":
            item = v._anno_items.get(oid)
            if item is not None:
                self._inspector.show_annotation(item)

    def _cancel_pending_block_selection(self) -> None:
        self._pending_block_selection = None
        self._pending_inspector_selection = None
        self._inspector_select_timer.stop()

    def _on_canvas_node_selected(self, node_id: int, _name: str):
        # Same coalescing as a block: a node's Inspector rebuild is the
        # heavier of the two (its Dependency tab lists every block).
        self._queue_inspector_selection("node", node_id)

    def _on_canvas_event_selected(self, event_id: int):
        self._queue_inspector_selection("event", event_id)

    def _select_canvas_item(self, item) -> None:
        """Select `item` alone and centre the view on it -- shared by
        every Explorer selection handler, which otherwise only updates
        the Inspector's content without touching the canvas selection or
        scroll position. Safe to call with `None` (nothing found -- a
        no-op, not an error)."""
        if item is None:
            return
        v = self._diagram_dock.view
        for it in list(v._scene.selectedItems()):
            it.setSelected(False)
        item.setSelected(True)
        v.centerOn(item)

    def _on_explorer_block_selected(self, block_id: int):
        self._cancel_pending_block_selection()
        v = self._diagram_dock.view
        self._inspector.show_block(block_id, v.blocks)
        self._select_canvas_item(v._block_items.get(block_id))

    def _on_explorer_node_selected(self, node_id: int):
        self._cancel_pending_block_selection()
        v = self._diagram_dock.view
        self._inspector.show_node(node_id, v.nodes)
        self._select_canvas_item(v._node_items.get(node_id))

    def _on_explorer_event_selected(self, event_id: int):
        v = self._diagram_dock.view
        event = v.events.get(event_id)
        if event is not None:
            self._inspector.show_event(event)
        self._select_canvas_item(v._event_items.get(event_id))

    def _on_explorer_sysconfig_selected(self):
        self._inspector.show_system_config()

    def _note_shared_name(self, kind: str, name: str, source: tuple) -> None:
        """Elements may share a name (Raptor 7's own FactoryDemo has two `CMD_5000`), so a rename is never refused;
        but a selector, a report row or a search by that name then matches both, so say so."""
        items, attr = source
        n = sum(1 for o in items.values() if getattr(o, attr, None) == name)
        if n > 1:
            self._statusbar.showMessage(
                f"{n} {kind}s are now named '{name}'. That is allowed, but selectors, searches and reports that "
                f"use the name will match all of them.", 9000)

    def _on_block_renamed(self, block_id: int, new_name: str):
        v = self._diagram_dock.view
        v.rename_block(block_id, new_name)  # Also marks the model dirty (model_changed).
        self._note_shared_name('block', new_name, (v.blocks, 'name'))
        self._explorer.set_view(v)
        self._inspector.show_block(block_id, v.blocks)

    def _on_node_renamed(self, node_id: int, new_name: str):
        v = self._diagram_dock.view
        v.rename_node(node_id, new_name)  # Also marks the model dirty (model_changed).
        self._note_shared_name('node', new_name, (v.nodes, 'node_name'))
        self._explorer.set_view(v)
        self._inspector.show_node(node_id, v.nodes)

    def _on_step_restart(self):
        """B-11 Restart: tear down the current stepped session and start
        an identical fresh one (kickoff resets everything; RNG streams
        reseed from the table, matching a brand-new session)."""
        if self._rbd is None:
            return
        self._invalidate_step_session()
        self._ensure_step_session()
        self._refresh_live_step_view(self._step_session)
        self._statusbar.showMessage(
            "Stepped run restarted — t = 0", 6000)

    def _on_step_terminate(self):
        """Manual B-11 Early Terminate on the stepped session."""
        if self._step_session is None:
            return
        session = self._step_session
        t = session.time
        self._reset_live_log()
        self._show_live_log_dock(False)
        self._on_fev_log_close()
        self._step_session = None   # single-use from here
        self._sync_run_settings_lock()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            logs, report = session.early_terminate()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            msgbox.critical(self, "Terminate Failed",
                                 f"Early terminate failed:\n\n{e}")
            return
        event_logs = [logs]
        eor_lines: list[str] = []
        final_lines: list[str] = []
        report_data = list(self._step_sink)
        if report is not None:
            model = session.model
            date = time.ctime()
            file_name = session.rbd_file.file_name
            eor_lines = [f"End_Of_Run Report for {file_name}  {date}",
                         "(Early Terminate)", ""]
            eor_lines.extend(build_end_of_run_report(
                report, model.blocks_by_id))
            final_lines = build_final_results_report(
                [report], model.blocks_by_id, file_name,
                model.run_length, date,
                termination_type=model.termination_type)
        QApplication.restoreOverrideCursor()
        self._diagram_dock.view.clear_live_states()
        self._present_run_results(event_logs, eor_lines, final_lines,
                                  report_data,
                                  ao_lines=list(self._step_ao or []),
                                  cap_lines=session.cap_lines)
        self._statusbar.showMessage(
            f"Early Terminate at t = {t:.3f} — partial results kept")

    def _on_event_renamed(self, eid: int, new_name: str):
        v = self._diagram_dock.view
        v.rename_event(eid, new_name)
        self._note_shared_name('event', new_name, (v.events, 'name'))
        self._explorer.refresh()
        if eid in v.events:
            self._inspector.show_event(v.events[eid])

    def _on_event_field_changed(self, eid: int, field: str, value: float):
        self._diagram_dock.view.set_event_field(eid, field, value)

    def _on_phasing_changed(self, kind: str, oid: int, phased: bool,
                            entries: list):
        self._diagram_dock.view.set_element_phasing(
            kind, oid, phased, entries)
        self._statusbar.showMessage(
            f"Phasing {'set' if phased else 'cleared'} for "
            f"{kind} {oid}")

    def _on_hier_renamed(self, hier_id: int, new_name: str):
        v = self._diagram_dock.view
        v.rename_hier(hier_id, new_name)
        self._note_shared_name('hierarchy', new_name, (v.hiers, 'name'))
        self._explorer.refresh()
        self._inspector.show_hier(v.hiers[hier_id])

    def _on_block_dist_type_changed(self, bid: int, which: str, code: int):
        """Inspector distribution change. The parameters used to stay as
        they were, so Exponential(100) became Weibull(shape 100, scale 0),
        and the first edit was refused for the field the user had not
        touched yet. When the old values are not valid for the new
        distribution, it gets valid ones with the same mean -- in the same
        undo step (`DiagramView.change_block_dist`, shared with the Data
        Tables grid)."""
        v = self._diagram_dock.view
        if bid not in v.blocks:
            return
        v.change_block_dist(bid, which, code)
        self._inspector.show_block(bid, v.blocks)

    def _on_block_fail_params_changed(self, block_id: int, values: list):
        self._diagram_dock.view.set_block_fail_params(block_id, values)

    def _on_block_stream_changed(self, block_id: int, value: int):
        """Inspector stream edit. A SYSTEM pick (201..210) arrives from
        both the failure and repair signals in quick succession -- the
        dialog writes both sides from one combo -- so route each side's
        own signal to its own 'which'."""
        v = self._diagram_dock.view
        sender = self.sender()
        which = ("F" if sender is self._inspector.block_fail_stream_changed
                 else "R")
        if not v.set_block_stream(block_id, which, value):
            self._statusbar.showMessage(
                "Invalid stream — independent 1..110, system A..J "
                "(antithetic stored as negative)", 8000)

    def _on_block_repair_params_changed(self, block_id: int, values: list):
        self._diagram_dock.view.set_block_repair_params(block_id, values)

    def _on_block_pm_freq_changed(self, block_id: int, value: float):
        self._diagram_dock.view.set_block_pm_freq(block_id, value)

    def _on_node_good_paths_changed(self, node_id: int, value: int):
        self._diagram_dock.view.set_node_good_paths(node_id, value)

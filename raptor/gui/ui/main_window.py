"""RAPTOR Studio main window — QMainWindow shell with ribbon, docks, and diagram.

Assembles the full application shell: ribbon bar, model explorer dock (left),
diagram canvas (center), inspector dock (right). Status-bar readouts and the
stepping debugger follow Raptor 7.
"""
from __future__ import annotations
from raptor.gui.ui.modality import exec_dialog

from raptor.gui.ui.qtcompat import req

from raptor.gui.ui import msgbox
from raptor.gui import text_editing
import time
import weakref
from typing import Any, cast
from pathlib import Path
from PySide6.QtCore import (Qt, QTimer, QObject, QPoint, QEvent, QEventLoop)
from PySide6.QtGui import (QCursor, QKeySequence, QAction, QShortcut)
from PySide6.QtWidgets import (
    QApplication, QToolTip, QDoubleSpinBox, QMainWindow, QDockWidget, QStatusBar, QFileDialog, QMessageBox,
    QDialog, QFormLayout, QPushButton, QLabel, QHBoxLayout, QVBoxLayout, QToolButton, QTreeWidget, QTreeWidgetItem,
)

from raptor.gui.ui.fev_mixin import FevMixin
from raptor.gui.ui.isolate_mixin import IsolateMixin
from raptor.gui.ui.background_run_mixin import BackgroundRunMixin
from raptor.gui.ui.overlay_mixin import OverlayMixin
from raptor.gui.ui.status_summary_mixin import StatusSummaryMixin
from raptor.gui.ui.autosave_mixin import AutosaveMixin
from raptor.gui.ui.run_progress_mixin import RunProgressMixin
from raptor.gui.ui.file_io_mixin import FileIoMixin
from raptor.gui.ui.step_mixin import StepMixin
from raptor.gui.ui.dock_layout_mixin import DockLayoutMixin
from raptor.gui.ui.validation_mixin import ValidationMixin
from raptor.gui.ui import settings
from raptor.gui.ui.run_experiment_mixin import (
    RunExperimentMixin,
)
from raptor.core.fileio.rbd import RbdFile
from raptor.core.engine.load import load_model
from raptor import __version__ as _APP_VERSION
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
from raptor.gui.resources import icons
from raptor.gui.resources.design_tokens import (
    ThemeManager, active_colour,
    global_stylesheet,
)
from raptor.gui.ui.help_mixin import HelpMixin
from raptor.gui.ui.export_mixin import ExportMixin
from raptor.gui.ui.recent_files_mixin import RecentFilesMixin
from raptor.gui.ui.find_zoom_mixin import FindZoomMixin
from raptor.gui.ui.selection_mixin import SelectionMixin
from raptor.gui.ui.step_terminate_mixin import StepTerminateMixin
from raptor.gui.ui.report_streams_mixin import ReportStreamsMixin
from raptor.gui.ui.run_mixin import RunMixin
from raptor.gui.ui.edit_guard_mixin import EditGuardMixin


#: Auto Layout's tooltip when it arranges the whole level (no selection,
#: or everything selected); the selection variant is built per count.
_AUTO_LAYOUT_TIP = next(c.tooltip for c in COMMANDS
                        if c.id == "model.auto_layout")


def _items_phrase(count: int, *, selected: bool = False) -> str:
    """'1 item', '3 selected items'."""
    noun = "item" if count == 1 else "items"
    return f"{count} selected {noun}" if selected else f"{count} {noun}"


def _engine_version() -> str:
    """The version string recorded in a run manifest, so a result can be
    traced back to the engine that produced it."""
    return f"raptor-studio {_APP_VERSION}"


from raptor.gui.key_tips import FILE_KEY_TIP, TAB_KEY_TIPS  # noqa: E402

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



class MainWindow(EditGuardMixin, RunMixin, ReportStreamsMixin, StepTerminateMixin, SelectionMixin, FindZoomMixin, RecentFilesMixin, ExportMixin, HelpMixin, RibbonBuilders, RunExperimentMixin, FevMixin, IsolateMixin,
                 ValidationMixin, DockLayoutMixin, StepMixin, FileIoMixin,
                 RunProgressMixin, AutosaveMixin, StatusSummaryMixin, OverlayMixin,
                 BackgroundRunMixin, QMainWindow):
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
    #: Terminate finalize worker poll (ms). Short enough that results
    #: appear as soon as the worker finishes, long enough not to spin.
    _STEP_FINALIZE_POLL_MS = 50
    #: the stepping session, the background-run thread and its last progress/bar keys (None when idle)
    _bg_thread: Any = None
    _bg_last_progress: Any = None
    _bg_last_bar: Any = None
    #: A Terminate finalize running on a worker thread: plain state dict
    #: shared with the thread and polled by `_step_finalize_timer` (no
    #: cross-thread Qt calls), plus the thread itself.
    _step_finalize_state: Any = None
    _step_finalize_thread: Any = None
    #: The background worker has finished its baseline and is now spending
    #: the user's time on importance variants. The baseline has already
    #: been put on screen once (`_bg_baseline_presented`), so the poll must
    #: not re-populate it and must ignore the baseline's stale
    #: stage/progress fields while this is true.
    _bg_baseline_presented: bool = False
    #: True only for the (re-entrant-safe) baseline population itself.
    _bg_baseline_presenting: bool = False
    #: Last importance progress tuple written to the status bar -- the poll
    #: runs at 10 Hz and would otherwise rewrite the same sentence.
    _imp_last_key: Any = None
    #: The canvas-overlay ribbon buttons, in the order the Results page
    #: offers them. Used to carry the user's chosen overlay across a
    #: same-run refresh (baseline -> baseline + Importance).
    _OVERLAY_ACTION_ATTRS = (
        "_weak_color_action", "_capacity_overlay_action",
        "_cost_overlay_action", "_spares_overlay_action",
        "_importance_overlay_action")
    #: True only while the extra importance batches are being measured.
    #: Run/Stop/settings stay locked, but the edit guard's wording must not
    #: claim a live simulation when the baseline has already finished.
    _importance_running: bool = False
    #: True while ResultsDock.set_results is populating; its progress
    #: callback pumps the event loop, which must not re-enter the poll
    #: tick or start a second population.
    _results_populating: bool = False
    #: A populated baseline whose importance phase follows. Caps the
    #: results-population bar at 99% so it cannot read as finished.
    _results_follow_up: bool = False
    #: Canvas level to return to when the Run panel's hierarchy scope is
    #: cleared; None while whole-model scope is active.
    _run_scope_return_level: int | None = None
    #: The model handed to the last run (an extract when a hierarchy is
    #: selected), for ETA/importance reporting.
    _last_run_model: RbdFile | None = None
    #: Isolation record of the stepped session (None for the whole model).
    _step_isolation = None

    def __init__(self):
        super().__init__()
        self._rbd: RbdFile | None = None
        #: Weak compatibility reference to the process-wide Help viewer
        #: (see `_show_help_viewer`); None before Help / after this window
        #: closes.
        self._help_viewer: weakref.ref | None = None
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
        # Terminate finalize worker poll: the worker only fills a plain
        # dict; this timer presents the results on the GUI thread.
        self._step_finalize_timer = QTimer(self)
        self._step_finalize_timer.setInterval(self._STEP_FINALIZE_POLL_MS)
        self._step_finalize_timer.timeout.connect(
            self._step_finalize_poll_tick)
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
        #: The designed startup dock widths/heights are applied once.
        #: Later model loads must not resize docks the user has chosen.
        self._canvas_room_defaults_done = False
        #: Set when any resizeDocks() call did not come from the window's
        #: own startup/clamp code -- i.e. the user (or a test) chose a
        #: size. First-load defaults must then be skipped.
        self._dock_user_sized = False
        self._internal_dock_resize = False
        self._dock_startup_extents = None
        self._dock_startup_snapshot_done = False
        self._dock_startup_window_size = None
        self._dock_resize_generation = 0
        self._dock_splitter_moved = False
        self._dock_rebase_pending = False
        self._compact_minimized: set[str] = set()
        self._live_log_session_shown = False
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

        # Alt+Up is the canvas fine-nudge chord. It cannot live on the
        # ribbon's Up button: a ribbon click must ALWAYS ascend, while the
        # key chord nudges a focused selection and falls back to ascending
        # when there is nothing to move.
        self._fine_nudge_up_action = QAction("Fine Move Up", self)
        self._fine_nudge_up_action.setShortcut(QKeySequence("Alt+Up"))
        self._fine_nudge_up_action.setShortcutContext(
            Qt.ShortcutContext.WindowShortcut)
        self._fine_nudge_up_action.setToolTip(
            "Fine-nudge the canvas selection by a tenth of a snap step; "
            "with nothing selected, ascend one hierarchy level")
        self._fine_nudge_up_action.triggered.connect(
            self._fine_nudge_or_hierarchy)
        self.addAction(self._fine_nudge_up_action)

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
        # dock matters. Letters are in `key_tips.py`, never derived from the label, so renaming
        # a button cannot reassign a key somebody already learned.
        self._key_tips_active = False
        self._key_tip_page: str | None = None
        self._key_tip_badges: list = []
        # (Alt itself is caught in keyPressEvent/keyReleaseEvent: Qt does
        # not match a shortcut made of a modifier alone.)
        # Alt+<letter> arrives as an ordinary key press (a lone Alt
        # QShortcut does not compose in Qt), delivered to whichever widget
        # has focus -- usually the canvas, not the window. So while the
        # tips show, the window grabs the keyboard and every key comes to
        # its own keyPressEvent. NOT an application-wide event filter: a
        # Python filter on the QApplication also sees QtWebEngine's
        # internal objects and PySide segfaults on them (#142).

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
        Restart / Jump / View > Live Log can do; it is set in many places,
        so they follow it here rather than at each one."""
        previous = getattr(self, "_step_session_obj", None)
        changed = (session is None) != (previous is None)
        self._step_session_obj = session
        if session is not None and previous is None:
            self._live_log_session_shown = False
        if changed and getattr(self, "_commands", None) is not None:
            self._refresh_commands(
                only={"stepping", "jump_available", "live_session"})

    def _show_key_tips(self) -> None:
        """Alt: put a letter over the File button and every ribbon tab, and
        arm the keyboard. That letter opens the tab and shows a letter over
        each of its buttons (`_show_page_tips`); the next one runs the
        command (Alt, R, S = Results > Show Results). Toggled by Alt again,
        and ended by any key that is not a tip (`key_tips.py`)."""
        if self._key_tips_active:
            self._dismiss_key_tips()
            return
        self._key_tips_active = True
        self._key_tip_page = None
        self._show_tab_tips()
        self.grabKeyboard()
        self._statusbar.showMessage(
            "Key tips on — press a letter, or Alt again to hide", 4000)

    def _clear_key_tip_badges(self) -> None:
        for badge in getattr(self, "_key_tip_badges", []):
            badge.hide()
            badge.deleteLater()
        self._key_tip_badges = []

    def _show_tab_tips(self) -> None:
        """Level 1: File and the tabs."""
        from PySide6.QtCore import QPoint
        from raptor.gui.widgets.key_tip_badge import KeyTipBadge
        self._clear_key_tip_badges()
        self._key_tip_page = None
        ribbon = self._ribbon
        badges = [KeyTipBadge(FILE_KEY_TIP, ribbon.file_button, self)]
        bar = ribbon.tab_bar
        for i in range(bar.count()):
            tip = TAB_KEY_TIPS.get(bar.tabText(i))
            if tip:
                r = bar.tabRect(i)
                badges.append(KeyTipBadge(
                    tip, bar, self, QPoint(r.center().x(), r.bottom() + 2)))
        self._key_tip_badges = badges

    def _page_buttons(self):
        page = self._ribbon._current_page()
        return page.findChildren(RibbonButton) if page is not None else []

    @staticmethod
    def _icon_centre(btn):
        """Where a button's badge sits: on its icon, so the label stays
        readable (large buttons: icon over label; small: icon at left)."""
        from PySide6.QtCore import QPoint
        from raptor.gui.widgets.ribbon import LARGE
        icon = btn.iconSize()
        if btn.mode() == LARGE:
            return QPoint(btn.width() // 2, 6 + icon.height() // 2)
        return QPoint(4 + icon.width() // 2, btn.height() // 2)

    def _show_page_tips(self, title: str) -> None:
        """Level 2: open the tab and letter its buttons."""
        from PySide6.QtCore import QEvent
        from raptor.gui.widgets.key_tip_badge import KeyTipBadge
        self._ribbon.select_page(title)
        # The page's geometry settles on the next layout pass; badges are
        # placed from it, so run that pass now.
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self._clear_key_tip_badges()
        self._key_tip_page = title
        self._key_tip_badges = [
            KeyTipBadge(tip, btn, self, self._icon_centre(btn))
            for btn in self._page_buttons()
            if (tip := btn.property("ribbonKeyTip")) and btn.isVisible()]

    def _key_press_event_filter(self, obj, event):
        """Handle a key press while key tips are showing.

        Needed because a single `QShortcut` on Alt alone does not
        compose into a two-key sequence in Qt; the second key arrives as
        an ordinary key press. Without this the tips would be visible
        decoration and the promised keyboard route would not exist."""
        if not self._key_tips_active or obj is not self:
            return False
        from PySide6.QtCore import QEvent
        if event.type() != QEvent.Type.KeyPress:
            return False
        key = event.key()
        if key in (Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Control,
                   Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            return False      # a modifier on its own is not a choice
        if key == Qt.Key.Key_Backspace and self._key_tip_page is not None:
            self._show_tab_tips()             # back up one level
            return True
        text = event.text().upper()
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            text = chr(key)                   # Alt held: text may be empty
        if not text or not text.isalpha():
            # Any other key dismisses the tips, as in every other app.
            self._dismiss_key_tips()
            return False
        if self._key_tip_page is None:
            return self._key_tip_tab_letter(text)
        for btn in self._page_buttons():
            if btn.property("ribbonKeyTip") == text:
                label = btn.property("ribbonActionText") or ""
                self._dismiss_key_tips()
                if btn.isEnabled():
                    btn.click()
                else:
                    self._statusbar.showMessage(
                        f"{label} is not available right now", 3000)
                return True
        self._statusbar.showMessage(
            f"No key tip {text} on {self._key_tip_page} — "
            "Backspace goes back", 3000)
        return True

    def _key_tip_tab_letter(self, text: str) -> bool:
        if text == FILE_KEY_TIP:
            self._dismiss_key_tips()
            self._show_backstage()
            return True
        for title, tip in TAB_KEY_TIPS.items():
            if tip == text:
                self._show_page_tips(title)
                return True
        self._statusbar.showMessage(f"No key tip {text}", 3000)
        return True

    def _dismiss_key_tips(self) -> None:
        """Turn the tips off WITHOUT re-toggling, so a key press that
        consumed a tip cannot leave the badges showing."""
        if not self._key_tips_active:
            return
        self._key_tips_active = False
        self._key_tip_page = None
        self.releaseKeyboard()
        self._clear_key_tip_badges()
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
        self._sync_link_routing()
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
        editor = text_editing.active_editor()
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
            selected_copyable=view.copyable_count() if view else 0,
            text_editing=editor is not None,
            text_can_copy=editor is not None and text_editing.can_copy(editor),
            clipboard_has_text=text_editing.clipboard_has_text(),
            clipboard_pastable=view.can_paste() if view else False,
            # `selected` is the same walk `selected_element_count()` would
            # do again -- twice per click, over every item map on the
            # level. Sum the answer we already have.
            selected_elements=sum(len(ids) for ids in selected),
            # Collapse moves blocks, regular nodes, events and whole
            # hierarchies: everything `selected_elements` returns.
            selected_structural=sum(len(ids) for ids in selected),
            hierarchies_here=view.hierarchy_count() if view else 0,
            level_depth=view._current_depth() if view else 0,
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
        # Hierarchy edits change the Run panel's scope list. Guarded
        # because reselecting a scope can rebuild the scene, which calls
        # back into this method.
        if not getattr(self, "_refreshing_scope_options", False):
            self._refreshing_scope_options = True
            try:
                self._refresh_run_scope_options()
            finally:
                self._refreshing_scope_options = False

    def _open_print_dialog(self) -> None:
        from raptor.gui.ui import printing
        printing.open_print_dialog(self)

    def _clear_results_clicked(self) -> None:
        self._discard_results()
        self._sync_results_notice()
        self._statusbar.showMessage("Results cleared: the model can be edited", 4000)


    #: GitHub repo of this port (pyproject.toml's project.urls).
    GITHUB_REPO_URL = "https://github.com/openpowershift/RaptorStudio"


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

    def _link_cell_edit(self, setter, lid, col, old, new) -> None:
        """One undoable LINK_DATA cell edit, then re-parse the live link the canvas
        reads (a separate step: `resync_link` returns nothing, so it cannot sit in a
        value-yielding lambda)."""
        setter(lid, col, old, new)
        self._diagram_dock.view.resync_link(lid)

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
        # The Explorer lists the whole model, so a level change only opens
        # the folders down to the new level; nothing is rebuilt (which also
        # keeps the row being clicked alive when a click changes level).
        # (A lambda: the Explorer is built later, in `_setup_docks`.)
        self._diagram_dock.view.level_changed.connect(
            lambda pid: self._explorer.reveal_level(pid))
        # Data Tables are per-hierarchy too: refresh them on a level
        # change so they don't keep showing the parent level's rows.
        self._diagram_dock.view.level_changed.connect(
            lambda _pid: self._data_tables.refresh())
        self._diagram_dock.view.model_changed.connect(self._mark_dirty)
        self._diagram_dock.view.comment_changed.connect(
            self._on_comment_changed)
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
            lambda: self._show_inspector_dock(expand_minimized=True))
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
                      "can_copy", "can_paste", "has_deletable",
                      "can_collapse",
                      "has_movable_selection", "can_align", "can_mass_edit",
                      "can_zoom_hierarchy", "can_extract_hierarchy"}))
        # Paste follows the clipboard, which changes outside any selection
        # (another model, another window, any other application).
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)
        if isinstance(qapp := QApplication.instance(), QApplication):
            qapp.focusChanged.connect(self._on_focus_changed)
        self._diagram_dock.view.paste_state_changed.connect(
            self._on_clipboard_changed)
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

    def _edit_copy(self) -> None:
        editor = text_editing.active_editor()
        if editor is not None:
            text_editing.copy(editor)
        else:
            self._diagram_dock.view.copy_selection()

    def _edit_cut(self) -> None:
        editor = text_editing.active_editor()
        if editor is not None:
            text_editing.cut(editor)
        else:
            self._diagram_dock.view.cut_selection()

    def _edit_paste(self) -> None:
        editor = text_editing.active_editor()
        if editor is not None:
            text_editing.paste(editor)
            return
        # A Data Tables grid takes one pasted value for its selected rows.
        grid_paste = getattr(QApplication.focusWidget(), "paste_text", None)
        if callable(grid_paste) and grid_paste(
                QApplication.clipboard().text()):
            return
        self._diagram_dock.view.paste()

    def _on_focus_changed(self, _old, new) -> None:
        """Cut/Copy/Paste follow the text editor that has the keyboard,
        including its selection."""
        for signal in ("selectionChanged", "copyAvailable"):
            sig = getattr(new, signal, None)
            # Only a real signal: on an item view `selectionChanged` is a
            # protected method, which has no `connect`.
            if (sig is not None and hasattr(sig, "connect")
                    and not new.property("_clip_hooked")):
                new.setProperty("_clip_hooked", True)
                sig.connect(self._on_clipboard_changed)
        self._on_clipboard_changed()

    def _focus_find(self) -> None:
        """Ctrl+F: put the cursor in the Find item box."""
        if not self._find_edit.isEnabled():
            self._statusbar.showMessage(
                "Open a model to find items in it.", 4000)
            return
        self._find_edit.setFocus()
        self._find_edit.selectAll()

    def _on_clipboard_changed(self) -> None:
        self._refresh_commands(self._command_state(reuse_validity=True),
                               only={"can_copy", "can_paste", "has_deletable"})

    def _setup_docks(self):
        self._build_explorer_dock()
        self._build_inspector_dock()
        self._build_fev_inspector_dock()
        self._build_live_log_dock()
        self._build_results_dock()
        self._build_data_tables_dock()
        self._build_run_dock_and_arrange()

    def _build_explorer_dock(self):
        self._explorer = ExplorerDock()
        self._explorer.hier_activated.connect(
            lambda hid: self._diagram_dock.view.enter_level(hid))
        self._explorer.ascend_requested.connect(
            self._diagram_dock.view.go_up)
        # Re-parents the ribbon-built Find combo into this dock's own
        # layout (see _setup_ribbon's own comment on it).
        self._explorer.set_search_widget(self._find_edit)
        # Ctrl+F = Find item, from the canvas or the Explorer only. Tables
        # keep their own Ctrl+F (the filter bar); two shortcuts with the
        # same keys active at once would cancel each other out.
        for owner in (self._diagram_dock, self._explorer):
            find_sc = QShortcut(QKeySequence.StandardKey.Find, owner)
            find_sc.setContext(
                Qt.ShortcutContext.WidgetWithChildrenShortcut)
            find_sc.activated.connect(self._focus_find)
        dock_left = QDockWidget("Model Explorer", self)
        self._set_dock_icon(dock_left, "explorer_dock_icon")
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

    def _build_inspector_dock(self):
        self._inspector = InspectorDock()
        self._inspector.set_undo_stack(self._undo_stack)
        # Animation is application state, so the Inspector cannot read it
        # off the model; hand it the live value instead of letting it show
        # the legacy `graphicsUse` column, which nothing writes.
        self._inspector.set_animate_state_provider(
            lambda: self._live_sim_action.isChecked())
        dock_right = QDockWidget("Inspector", self)
        self._set_dock_icon(dock_right, "inspector_dock_icon")
        dock_right.setWidget(self._inspector)
        # Wider than the default so all Inspector details show by
        # default -- 300px was not enough for the 7-tab strip without
        # its scroll arrows.
        dock_right.setMinimumSize(340, 1)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock_right)
        self._inspector_dock = dock_right

        # Failure Effects inspector: the structured state beside the canvas
        # while Failure Effects View is enabled. It shares the Inspector's
        # tab group, so no new width is reserved for a mode the user is
        # usually not in, and raises itself only for that mode.

    def _build_fev_inspector_dock(self):
        from raptor.gui.ui.fev_inspector import FevInspectorDock
        self._fev_inspector = FevInspectorDock(
            summary_provider=self._fev_text)
        self._fev_inspector.locate_requested.connect(
            self._on_fev_element_locate)
        fev_dock = QDockWidget("Failure Effects", self)
        self._set_dock_icon(fev_dock, "fev_panel_icon", "accent")
        fev_dock.setWidget(self._fev_inspector)
        fev_dock.setMinimumSize(340, 1)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, fev_dock)
        self.tabifyDockWidget(self._inspector_dock, fev_dock)
        fev_dock.hide()
        self._fev_inspector_dock = fev_dock

        # Live Log dock: while stepping / live-running, the right dock
        # shows the streaming event log (like the Results Event Log)
        # instead of the Inspector; the Inspector returns after.
        # The panel carries a run selector, a position line and the tree.

    def _build_live_log_dock(self):
        from raptor.gui.ui.live_log_panel import LiveLogPanel
        self._live_log_panel = LiveLogPanel()
        live_page = self._live_log_panel
        self._live_log_tree = self._live_log_panel.tree
        self._live_log_caption = self._live_log_panel.caption
        # Focus Event controls whether new log rows are followed to the
        # bottom; with it off the user's selection/scroll stays put.
        self._live_log_panel.set_follow_latest(
            self._focus_event_action.isChecked())
        self._focus_event_action.toggled.connect(
            self._live_log_panel.set_follow_latest)
        # Same affordances as the Results dock's Event Log: the State
        # column coloured green/amber/red, and a click on a Block/Node row
        # locating that element on the canvas.
        self._live_log_tree.itemSelectionChanged.connect(
            self._on_live_log_row_clicked)
        self._live_log_dock = QDockWidget("Live Log", self)
        self._set_dock_icon(self._live_log_dock, "log_icon")
        self._live_log_dock.setWidget(live_page)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._live_log_dock)
        self._live_log_dock.hide()

    def _build_results_dock(self):
        self._results = ResultsDock()
        dock_bottom = QDockWidget("Results", self)
        self._set_dock_icon(dock_bottom, "results_dock_icon")
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

    def _build_data_tables_dock(self):
        self._data_tables = DataTablesDock()
        self._data_tables.model_edited.connect(self._mark_dirty)
        self._data_tables.export_tables_requested.connect(self._export_tables)
        self._data_tables.element_locate_requested.connect(
            self._on_result_element_clicked)
        self._data_tables.element_locate_id_requested.connect(
            self._on_fev_element_locate)
        self._data_tables.status_message.connect(
            lambda text: self._statusbar.showMessage(text, 8000))
        self._data_tables.validation_failed.connect(
            lambda msg: self._statusbar.showMessage(f"Invalid value — {msg}", 8000))
        dock_tables = QDockWidget("Data Tables", self)
        self._set_dock_icon(dock_tables, "table_grid_icon")
        dock_tables.setWidget(self._data_tables)
        self._data_tables_dock = dock_tables
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock_tables)
        # Data Tables and Results share one bottom slot as tabs, so a
        # single tabbed strip keeps one sane height the splitter
        # controls.
        self.tabifyDockWidget(dock_tables, self._results_dock)
        self._results_dock.raise_()
        dock_tables.setMinimumHeight(160)

        # Run panel: the third tab in the same bottom strip. Deliberately
        # NOT a new right-side panel -- at 1280px, Explorer (260) +
        # Inspector (340) + a third 320px panel leaves a 360px canvas
        # against the spec's own 700px minimum
        # (the internal UI blueprint). Tabifying costs no new
        # width and reuses the existing splitter.

    def _build_run_dock_and_arrange(self):
        from raptor.gui.ui.run_panel import RunPanelDock
        self._run_dock = RunPanelDock(self)
        self._set_dock_icon(self._run_dock, "run_panel_icon")
        self._run_panel = self._run_dock.panel
        # The stepping debugger's parameters moved to the panel; point the
        # attributes the existing code (and tests) read at the panel's
        # real widgets, so there is ONE of each rather than a ribbon
        # spinner and a panel spinner that could disagree.
        self._step_count_spin = self._run_panel._step_count
        self._run_to_time_spin = self._run_panel._run_to
        self._breakpoint_combo = self._run_panel._breakpoint
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._run_dock)
        self.tabifyDockWidget(self._data_tables_dock, self._run_dock)
        # Results, Data Tables and Run share one physical strip, and the
        # strip can only grow to the smallest maximumHeight() among the
        # three. Tie that ceiling to whichever tab is on show: Run hugs
        # its own content, Results/Data Tables may use the window.
        self._bottom_active_dock: QDockWidget | None = self._results_dock
        for _tab in (self._results_dock, self._data_tables_dock, self._run_dock):
            _tab.visibilityChanged.connect(
                lambda visible, d=_tab:
                self._on_bottom_dock_visibility_changed(d, visible))
        # Adding Run last made it the active tab on every startup, even
        # on the Model tab. Results is the default front tab; Simulate
        # raises Run explicitly.
        self._results_dock.raise_()
        self._setup_dock_toggle(self._run_toggle_action, self._run_dock)
        self._run_panel.experiment_changed.connect(self._on_panel_experiment)
        self._run_panel.system_config_changed.connect(
            self._on_system_config_changed)
        self._run_panel.dependency_blocked.connect(self._on_panel_dependency)
        self._run_panel.cores_changed.connect(self._on_cores_changed)
        self._run_panel.scope_changed.connect(self._on_run_scope_changed)
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
        self._setup_dock_toggle(self._explorer_toggle_action, self._explorer_dock)
        self._setup_dock_toggle(self._inspector_toggle_action, self._inspector_dock)
        self._setup_dock_toggle(self._fev_inspector_toggle_action, self._fev_inspector_dock,
                                manage_enabled=False)
        self._setup_dock_toggle(self._results_toggle_action, self._results_dock)
        self._setup_dock_toggle(self._data_tables_action, self._data_tables_dock)
        self._setup_dock_toggle(
            self._live_log_toggle_action, self._live_log_dock,
            manage_enabled=False,
            on_show=lambda: self._show_live_log_dock(True),
            on_hide=self._show_inspector_dock)
        self._sync_dock_tab_bars()
        # Floating/re-docking can create a fresh QTabBar whose default
        # elide mode is ElideRight, so tabs show "Data Tab...". Re-apply
        # the no-elide rule whenever the dock placement changes.
        for d in (self._explorer_dock, self._inspector_dock, self._fev_inspector_dock, self._live_log_dock,
                  self._results_dock, self._data_tables_dock, self._run_dock):
            d.dockLocationChanged.connect(
                lambda area, dock=d:
                self._on_dock_location_changed(dock, area))
            # Floating/re-docking can create a fresh QTabBar whose default
            # elide mode is ElideRight, so tabs show "Data Tab...". Re-apply
            # the no-elide rule whenever the dock placement changes. The
            # same signal releases the constraints a DOCKED panel needs
            # (reported: "when it's detached I should be able to expand it
            # and resize it too").
            def _on_floating(floating: bool, dock: Any = d) -> None:
                QTimer.singleShot(0, self._sync_dock_tab_bars)
                self._on_dock_top_level_changed(dock, floating)
            d.topLevelChanged.connect(_on_floating)
        # Setup snapshots visibility before the window is shown, so
        # tabified panels looked unchecked although present. Re-sync once
        # the layout is live (also after reset).
        QTimer.singleShot(0, self._sync_dock_toggles)

        # `saveState()` keys each dock by object name; without one Qt drops it from the saved layout.
        for dock, name in ((self._explorer_dock, "ExplorerDock"), (self._inspector_dock, "InspectorDock"),
                           (self._fev_inspector_dock, "FevInspectorDock"),
                           (self._live_log_dock, "LiveLogDock"), (self._results_dock, "ResultsDock"),
                           (self._data_tables_dock, "DataTablesDock"), (self._run_dock, "RunPanelDock")):
            dock.setObjectName(name)

        # A dock's tab and its title bar carry the same glyph as the
        # ribbon command that opens it, so a tab is recognisable without
        # reading its text. Set before the custom titlebars are built.
        self._apply_dock_icons()

        # Minimize-to-stub: Data Tables + Results collapse together as
        # one bottom stub since they're tabified into one physical slot
        # -- minimizing either one collapses both.
        self._make_dock_minimizable([self._explorer_dock], "left")
        self._make_dock_minimizable([self._inspector_dock], "right")
        self._make_dock_minimizable(
            [self._results_dock, self._data_tables_dock, self._run_dock], "bottom",
            restore_max=lambda: self._max_bottom_dock_height())
        # Snapshot the first-run arrangement. Reset Layout restores this
        # instead of re-tabifying repeatedly (which created orphan tab
        # bars and could leave Data Tables in front).
        self._default_dock_state = self.saveState()
        # Startup is an unrun model: Inspector front, Live Log parked
        # until a step session exists.
        self._reset_right_docks()

    #: `(dock attribute, icons.py factory, colour token)` for every dock
    #: that appears in a tab strip or a title bar.
    _DOCK_ICONS = (
        ("_explorer_dock", "explorer_dock_icon", "text"),
        ("_inspector_dock", "inspector_dock_icon", "text"),
        ("_fev_inspector_dock", "fev_panel_icon", "accent"),
        ("_live_log_dock", "log_icon", "text"),
        ("_results_dock", "results_dock_icon", "text"),
        ("_data_tables_dock", "table_grid_icon", "text"),
        ("_run_dock", "run_panel_icon", "text"),
    )

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
        self._update_title()
        self._sync_hier_up_action()
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
        # Add Hierarchy and Collapse depend on how deep this level is.
        self._refresh_commands(self._command_state(reuse_validity=True),
                               only={"can_add_hierarchy", "can_collapse"})
        if inside and name is not None:
            self._statusbar.showMessage(
                f"Viewing hierarchy level: {name.name} (double-click an "
                f"Out marker or use Up/Home to return)", 6000)

    def _sync_hier_up_action(self) -> None:
        """The ribbon's Up button is hierarchy navigation only."""
        action = getattr(self, "_hier_up_action", None)
        view = getattr(getattr(self, "_diagram_dock", None), "view", None)
        if action is not None and view is not None:
            action.setEnabled(view._current_parent != 0)

    def _fine_nudge_or_hierarchy(self) -> None:
        """Alt+Up: fine-nudge a focused canvas selection, else ascend.

        The ribbon's Up button keeps calling `go_up` directly; this chord
        is the only route that may move something. With no nudgeable
        selection (including an arrow-only selection), in a text field,
        or with focus outside the canvas, it falls back to the original
        hierarchy-up behaviour.
        """
        view = self._diagram_dock.view
        view._suppress_key_tips()
        focus = QApplication.focusWidget()
        canvas_focused = (focus is None or focus is view
                          or view.isAncestorOf(focus))
        if canvas_focused and view.has_nudgeable_selection():
            if view.nudge_selection(0, -1, fine=True):
                return
        view.go_up()

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
        """Apply Project Settings / Run panel SYSTEM_DATA edits through the
        undoable row writer, then refresh every dependent readout."""
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

    def _on_fev_element_locate(self, kind: str, element_id: int) -> None:
        """The Failure Effects table's "Go to element" action."""
        if not self._diagram_dock.view.locate_id(kind, element_id):
            self._statusbar.showMessage(
                f"Couldn't find {kind} id {element_id} on the canvas", 6000)

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
        if self._key_tips_active:
            if self._key_press_event_filter(self, event):
                return
            if not self._key_tips_active:
                # The key turned the tips off (an arrow, Esc...): it was
                # meant for the focused widget, which the grab bypassed.
                focus = QApplication.focusWidget()
                if focus is not None and focus is not self:
                    QApplication.sendEvent(focus, event)
                    return
        if (not self._key_tips_active
                and event.modifiers() == _Qt.KeyboardModifier.AltModifier
                and _Qt.Key.Key_A <= event.key() <= _Qt.Key.Key_Z):
            # Alt held while the letter goes down: the same route as
            # Alt, release, letter -- for the people who chord it.
            letter = chr(event.key())
            if letter == FILE_KEY_TIP or letter in TAB_KEY_TIPS.values():
                self._alt_alone = False
                self._show_key_tips()
                self._key_press_event_filter(self, event)
                return
        import time
        self._alt_alone = event.key() == _Qt.Key.Key_Alt
        self._alt_down_at = time.monotonic()
        if event.key() == _Qt.Key.Key_Escape:
            if self._handle_escape():
                return
        super().keyPressEvent(event)

    def changeEvent(self, event):                       # noqa: N802
        # The tips hold a keyboard grab: switching away (another window
        # of this app, or another app) turns them off rather than leave
        # the keys captured.
        if (event.type() == QEvent.Type.ActivationChange
                and not self.isActiveWindow()):
            self._dismiss_key_tips()
        if (event.type() == QEvent.Type.ActivationChange
                and self.isActiveWindow()):
            # Help follows the model window the user is working in.
            from raptor.gui.ui.help_service import note_window_activated
            note_window_activated(self)
        super().changeEvent(event)

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
        if getattr(self, "_step_finalize_state", None) is not None:
            self._statusbar.showMessage(
                "Finalizing the stepped run — results appear when ready", 4000)
            return True
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
        """Connect the child widgets' signals to the window, one source at a time."""
        self._wire_explorer_signals()
        self._wire_inspector_signals()
        self._wire_canvas_signals()
        self._wire_widget_hooks()
        self._wire_results_signals()

    def _wire_explorer_signals(self):
        self._explorer.block_selected.connect(self._on_explorer_block_selected)
        self._explorer.node_selected.connect(self._on_explorer_node_selected)
        self._explorer.event_selected.connect(self._on_explorer_event_selected)
        self._explorer.hier_selected.connect(self._on_explorer_hier_selected)
        self._explorer.system_config_selected.connect(self._on_explorer_sysconfig_selected)

    def _wire_inspector_signals(self):
        self._inspector.block_renamed.connect(self._on_block_renamed)
        self._inspector.block_comment_changed.connect(
            lambda bid, txt: self._on_comment_committed("block", bid, txt))
        self._inspector.element_comment_changed.connect(
            self._on_comment_committed)
        self._inspector.comment_to_annotation_requested.connect(
            lambda etype, oid: self._diagram_dock.view
            .comment_to_annotation(etype, oid))
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
        self._inspector.block_duty_cycle_changed.connect(
            lambda bid, v: self._diagram_dock.view
            .set_block_duty_cycle(bid, v))
        self._inspector.block_start_age_changed.connect(
            lambda bid, v: self._diagram_dock.view
            .set_block_start_age(bid, v))
        self._inspector.block_restoration_factor_changed.connect(
            lambda bid, v: self._diagram_dock.view
            .set_block_restoration_factor(bid, v))
        self._inspector.block_restoration_type_changed.connect(
            lambda bid, v: self._diagram_dock.view
            .set_block_restoration_type(bid, v))
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
            lambda lid, col, old, new: self._link_cell_edit(
                self._diagram_dock.view.set_link_flow_field, lid, col, old, new))
        # Node Standby tab's per-incoming-link switchover table (same
        # LINK_DATA/undo plumbing as the flow cells above).
        self._inspector.link_standby_cell_changed.connect(
            lambda lid, col, old, new: self._link_cell_edit(
                self._diagram_dock.view.set_link_standby_field, lid, col, old, new))
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
        self._inspector.annotation_arrow_pick_requested.connect(
            lambda aid, end: self._diagram_dock.view.start_anchor_pick(aid, end))
        self._inspector.annotation_anchor_pick_requested.connect(
            lambda aid: self._diagram_dock.view.start_anchor_pick(aid))
        self._inspector.rates_changed.connect(self._mark_dirty)
        self._inspector.system_threshold_changed.connect(
            self._on_system_threshold_changed)
        self._inspector.system_config_changed.connect(
            self._on_system_config_changed)
        self._inspector.system_comment_changed.connect(
            self._on_system_comment_changed)

    def _wire_canvas_signals(self):
        # Flush a pending Comment draft BEFORE `_set_level`/`load_rbd`
        # rebuilds the scene and Inspector; the level_changed handlers run
        # after the old editor has already been destroyed.
        self._diagram_dock.view.level_about_to_change.connect(
            self._inspector.flush_pending_comment)
        self._diagram_dock.view.structural_about_to_change.connect(
            self._inspector.flush_pending_comment)
        self._diagram_dock.view.event_selected.connect(
            self._on_canvas_event_selected)
        self._diagram_dock.view.selection_changed.connect(
            self._results.refresh_weak_selection)

    def _wire_widget_hooks(self):
        # The Inspector's Anchor controls list this level's elements.
        self._inspector.anchor_source = self._diagram_dock.view
        self._results.set_status_hint_fn(
            lambda msg: self._statusbar.showMessage(msg, 6000))
        self._results.set_hierarchy_options({})
        self._results.set_selected_block_names_fn(
            lambda: {self._diagram_dock.view.blocks[b].name
                     for b in self._diagram_dock.view.blocks
                     if b in self._diagram_dock.view._block_items
                     and self._diagram_dock.view._block_items[b]
                     .isSelected()})

    def _wire_results_signals(self):
        # Event-log rows carry element names; resolve them against the
        # loaded model and select on the canvas (which also populates the
        # Inspector via the normal selection path).
        self._results.element_clicked.connect(self._on_result_element_clicked)
        self._results.contents_changed.connect(self._sync_results_notice)
        self._results.hierarchy_filter_changed.connect(
            self._on_results_hier_filter)
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
        self._apply_dock_icons()
        self._live_log_panel.refresh_colours()
        for rethemer in (self._dock_titlebar_rethemers or []):
            rethemer()
        fev_inspector = getattr(self, "_fev_inspector", None)
        if fev_inspector is not None:
            fev_inspector.refresh_theme()

    #: Contrast prompts waiting their turn, shared by every model window.
    #: A theme change makes each open window ask; nesting one modal
    #: `exec()` inside another left a prompt behind a different window
    #: and the foreground window looked frozen. They run one at a time.
    _contrast_queue: list[tuple[MainWindow, int]] = []
    _contrast_busy: bool = False

    def _on_annotations_may_be_invisible(self, count: int) -> None:
        """The view has already run the contrast check (`DiagramView.
        find_low_contrast_annotations`); this asks before touching
        anything the user deliberately coloured. With several model
        windows open the prompts are sequenced, each raised above its
        own window before it is shown."""
        MainWindow._contrast_queue.append((self, count))
        if MainWindow._contrast_busy:
            return
        MainWindow._contrast_busy = True
        try:
            while MainWindow._contrast_queue:
                win, n = MainWindow._contrast_queue.pop(0)
                try:
                    win._ask_contrast_fix(n)
                except RuntimeError:
                    pass  # window was closed while it waited
        finally:
            MainWindow._contrast_busy = False

    def _ask_contrast_fix(self, count: int) -> None:
        from raptor.gui.ui.dialogs import question_box
        # Bring this window forward so the modal opens on top of it,
        # never behind another model window.
        self.raise_()
        self.activateWindow()
        plural = "s" if count != 1 else ""
        if question_box(
                self, "Annotations may be hard to see",
                f"{count} annotation{plural} may be hard to see against "
                f"the new theme.\n\nAdjust their text/border colour to fit?",
                yes_text="Adjust colours", no_text="Leave as is",
                default_yes=True):
            view = self._diagram_dock.view
            view.fix_annotation_contrast(view.find_low_contrast_annotations())

    def _sync_link_routing(self) -> None:
        """Apply the saved link style and show it in the View combo."""
        from raptor.gui.ui.diagram_items import set_link_routing
        mode = str(settings.get("appearance/link_routing", "straight"))
        set_link_routing(mode)
        self._sync_link_routing_combo()

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
        self._schedule_stack_check()

    def _schedule_stack_check(self) -> None:
        """Elements dropped on top of each other must show up as a Model
        warning right away, but a move does not re-validate the model. Only
        the (cheap) stacking rule is re-run, once the dragging pauses."""
        if getattr(self, "_loading_model", False) or self._rbd is None:
            return
        timer = getattr(self, "_stack_timer", None)
        if timer is None:
            from PySide6.QtCore import QTimer
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(400)
            timer.timeout.connect(self._refresh_stack_warnings)
            self._stack_timer = timer
        timer.start()

    def _refresh_stack_warnings(self) -> None:
        """Swap the "drawn on top of" warnings for those of the elements'
        current positions, in the memoised validation, and refresh the badge."""
        rbd = self._rbd
        if rbd is None or getattr(self, "_loading_model", False):
            return
        scene = self._diagram_dock.view.scene()
        if scene is not None and scene.mouseGrabberItem() is not None:
            # An element is being dragged: leave the badge, the status bar
            # and the Issues list alone until it is dropped, so nothing in
            # the window changes under the user's hand mid-gesture.
            self._schedule_stack_check()
            return
        cached = getattr(self, "_validation_cache", None)
        if cached is None or cached[0] != getattr(self, "_model_revision", 0):
            # No answer for this revision to patch: the full pass covers it.
            self._ensure_validation_timer().start()
            return
        from raptor.api.validate import _check_stacked_blocks
        marker = "is drawn on top of"
        stacked = self._warnings_from_api(_check_stacked_blocks(rbd))
        old = cached[2]
        new = [w for w in old if marker not in w] + stacked
        if new == old:
            return
        self._validation_cache = (cached[0], cached[1], new)
        self._set_validation_badge(not cached[1], cached[1],
                                   self._validation_warnings())
        fresh = [w for w in stacked if w not in old]
        if fresh:
            more = f" (and {len(fresh) - 1} more)" if len(fresh) > 1 else ""
            self._statusbar.showMessage(f"Warning: {fresh[0]}{more}", 8000)

    def _mark_view_dirty(self) -> None:
        """A view option changed (link style, block labels): the file now
        differs from what is on disk, but no model data changed."""
        if getattr(self, "_loading_model", False) or self._rbd is None:
            return
        self._dirty = True
        self._update_title()

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

    def _checked_overlay_state(self) -> dict[str, bool]:
        """Which canvas overlays the user currently has on."""
        return {attr: bool(getattr(self, attr).isChecked())
                for attr in self._OVERLAY_ACTION_ATTRS}

    def _reapply_checked_overlays(self, state: dict[str, bool] | None) -> None:
        """Restore the overlays a same-run refresh cleared off the canvas.

        `_sync_overlay_actions` clears every overlay because a NEW result
        set invalidates them. When the refresh is only the baseline being
        completed with its Importance data, the numbers the user is looking
        at are unchanged -- throwing away their Weak Link/Capacity view is
        not a new-run decision.
        """
        if not state:
            return
        for attr, was_on in state.items():
            if not was_on:
                continue
            action = getattr(self, attr, None)
            if action is None or not action.isEnabled():
                continue
            if attr == "_weak_color_action":
                if not getattr(self._diagram_dock.view,
                               "_weak_colors_on", False):
                    self._toggle_weak_colors()
            elif attr == "_capacity_overlay_action":
                self._on_capacity_overlay_toggled(True)
            elif attr == "_cost_overlay_action":
                self._on_cost_overlay_toggled(True)
            elif attr == "_spares_overlay_action":
                self._on_spares_overlay_toggled(True)
            elif attr == "_importance_overlay_action":
                self._on_importance_overlay_toggled(True)

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

    def _on_comment_changed(self, etype: str, oid: int,
                            stored: str) -> None:
        """Comment command (including undo/redo) changed the model.

        If the Inspector still shows that element, update its editor in
        place. The deferred `model_changed` refresh then skips the
        Inspector for this one target only; any coalesced change for a
        different target still rebuilds normally.
        """
        if self._inspector.set_comment_source(etype, oid, stored):
            self._suppress_inspector_refresh_target: tuple[str, int] | None = (
                etype, oid)

    def _on_comment_committed(self, etype: str, oid: int, text: str) -> None:
        """Inspector Comment commit: undoable write, no Inspector rebuild.

        The editor already shows the text the user just typed. The
        command's `comment_changed` signal updates it in place (and scopes
        the one-shot refresh suppression); rebuilding would delete the
        live editor and move the pane's scroll/caret. Everything else the
        deferred refresh does -- dirty title, validation, tables -- still
        runs.
        """
        view = self._diagram_dock.view
        stored = text.replace("\n", "~")
        if view.current_comment_stored(etype, oid) != stored:
            view.set_element_comment(etype, oid, text)
        # The old lambda called this even when the text was unchanged;
        # keeping that also covers a commit after the model was already
        # put back by undo.
        self._mark_dirty()

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
            # A comment command updated the live Inspector editor in
            # place; re-populating would destroy it and jump the pane.
            # The suppression is scoped to the exact target it updated,
            # so a coalesced refresh for another element still happens.
            suppress = getattr(
                self, "_suppress_inspector_refresh_target", None)
            self._suppress_inspector_refresh_target = None
            if suppress is not None and \
                    self._inspector.current_target() == suppress:
                pass
            else:
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
        # Deselect is another way the Inspector stops showing the element
        # whose Comment box holds a draft.
        self._inspector.flush_pending_comment()
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
        gone: dict[str, Any] = {"block": v.blocks, "node": v.nodes, "event": v.events,
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
            item = v._anno_items.get(cast(int, oid))   # annotation ids are ints
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

    def _reset_live_log(self):
        """Clear the stepping Live Log dock and its fed-counter together:
        the counter reset alone would leave the previous run's rows above
        the new session's lines."""
        self._live_log_panel.clear()
        self._live_log_dock.setWindowTitle("Live Log")

    def _end_step_session(self, *, clear_sink: bool = True) -> bool:
        """End the current stepped session through one path.

        Raw `_step_session = None` assignments skipped the dock reset and
        the Live Log's action refresh, so a failed step left a visible
        panel whose action was already disabled. Returns True when a
        session was actually open. `clear_sink=False` keeps the reports
        Finish/Terminate are about to present."""
        if getattr(self, "_step_session", None) is None:
            return False
        self._step_session = None
        if clear_sink:
            self._step_sink = []
        self._diagram_dock.view.clear_live_states()
        # The run is over, so the run settings unlock. Derived, not
        # remembered -- see `_sync_run_settings_lock`.
        self._sync_run_settings_lock()
        self._live_log_session_shown = False
        self._reset_right_docks()
        return True

    def _invalidate_step_session(self):
        self._leave_fev()
        self._on_fev_log_close()
        if getattr(self, "_fev_status_dialog", None) is not None:
            cast(Any, self)._fev_status_dialog.close()
        # A Terminate finalize may still be running; its results are for
        # the session being discarded, so drop them and let the worker
        # finish on its own.
        finalizing = self._step_finalize_state is not None
        self._discard_step_finalize()
        ended = self._end_step_session()
        if not ended:
            # A discarded finalize has no session left, but its live
            # colours and stale Live Log rows must still go.
            self._reset_live_log()
            self._diagram_dock.view.clear_live_states()
        if ended:
            self._statusbar.showMessage("Stepped run discarded — model changed")
        elif finalizing:
            self._statusbar.showMessage(
                "Early-terminate results discarded — model changed", 6000)

    def _update_title(self):
        star = "*" if self._dirty else ""
        level = self._level_title()
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
            self.setWindowTitle(f"RAPTOR Studio — {name}{star}{level}")
        else:
            filename = Path(self._current_file).name
            self.setWindowTitle(f"RAPTOR Studio — {filename}{star}{level}")
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

    #: Longest hierarchy name the title bar spells out.
    _TITLE_LEVEL_CHARS = 40

    def _level_title(self) -> str:
        """" — Home" or " — <hierarchy name> (id N)": the level on screen,
        for the title bar (empty with no model). Long names are elided; the
        Explorer's tooltip has the full one."""
        dock = getattr(self, "_diagram_dock", None)
        view = getattr(dock, "view", None)
        if self._rbd is None or view is None:
            return ""
        parent = getattr(view, "_current_parent", 0) or 0
        if not parent:
            return " — Home"
        hier = view.hiers.get(parent)
        name = str(getattr(hier, "name", "") or "")
        if len(name) > self._TITLE_LEVEL_CHARS:
            name = name[:self._TITLE_LEVEL_CHARS - 1] + "…"
        return f" — {name} (id {parent})" if name else f" — hierarchy {parent}"

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
        from PySide6.QtWidgets import (QDialog, QPushButton)
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

    def _open_analytic_dialog(self):
        """Model ▸ Analytical: exact static reliability/availability."""
        if self._rbd is None:
            return
        from raptor.gui.ui.analytic_dialog import open_analytic_dialog
        open_analytic_dialog(self)

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
        view = self._diagram_dock.view
        hid = view.collapse_selection()
        if hid is None:
            # A refusal (a boundary that cannot be rebuilt, or a selection a
            # hierarchy cannot hold) has already shown its own message; only
            # an empty selection needs this hint.
            if not any(view.selected_elements()[:3]):
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
        self._sync_auto_layout_tooltip()

    def _on_tool_mode_changed(self, mode: str):
        """Keep the ribbon's tool buttons in step with the view.

        `_set_tool` is the user-facing path and already leaves the other
        tool buttons unchecked. This is the other direction: the VIEW
        disarming itself (Esc, or a one-shot annotation placement
        committing) has to clear the button, or it stays lit while the
        tool is spent and the next canvas click does something the user
        no longer asked for.
        """
        # Exactly one tool button is lit, and it is the view's mode: Select
        # is a member of the set, so arming an Add tool unlights Select and
        # disarming one (Esc, a one-shot placement) lights it again.
        for action, m in self._tool_actions():
            want = m == mode
            if action.isChecked() != want:
                action.blockSignals(True)
                action.setChecked(want)
                action.blockSignals(False)

    def _tool_actions(self):
        """(action, tool-mode) for every mutually-exclusive canvas tool,
        Select included."""
        return ((self._select_action, "select"),
                (self._add_block_tool, "add_block"),
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
        if on:
            v.tool_mode = mode
        elif v.tool_mode == mode:
            # Unchecking the armed tool falls back to Select. Unchecking
            # Select itself leaves the mode at "select": there is no
            # "no tool" state, so the sync below re-lights it.
            v.tool_mode = "select"
        # The view only broadcasts a CHANGED mode, so sync here as well;
        # the buttons then always match the view's one armed tool.
        self._on_tool_mode_changed(v.tool_mode)

    # ------------------------------------------------------------------
    # Busy indicator for known-slow, main-thread work (Open / New)
    # ------------------------------------------------------------------


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


    _LAYOUT_VERSION = 1

    def closeEvent(self, event):
        """Confirm unsaved changes before closing."""
        # Commit a Comment draft before the unsaved-change prompt/write
        # path, so closing cannot silently discard the last few keystrokes.
        self._inspector.flush_pending_comment()
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
            self._step_finalize_timer.stop()
            self._close_report_streams()
            # Clean exit: drop the crash-recovery copy so it isn't
            # offered as a "crash" on the next launch.
            self._clear_recovery()
            self._save_layout()
            # The Analytical window is a window of its own too: left open,
            # it kept the application (and its solve) running after the
            # main window closed. Closing it cancels the solve.
            analytic = getattr(self, "_analytic_dialog", None)
            if analytic is not None:
                analytic.shut_down()
            from raptor.gui import open_models
            from raptor.gui.ui.help_service import notify_window_closed
            open_models.unregister(self)
            # Help is process-wide: closing one model window must leave it
            # open while another remains. The service transfers it to a
            # live window, and closes it only when none are left (#206).
            notify_window_closed(self)
            self._help_viewer = None
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
        finalize = getattr(self, "_step_finalize_state", None)
        if finalize is not None:
            # Closing discards the in-flight finalize: its results cannot
            # be shown after the window is gone. Keep its state until the
            # worker is actually done, then let the poll clear/unlock it.
            self._discard_step_finalize()
            thread = self._step_finalize_thread
            deadline = time.monotonic() + 15.0
            while thread is not None and thread.is_alive() \
                    and time.monotonic() < deadline:
                QApplication.processEvents()
                thread.join(0.05)
            state = self._step_finalize_state
            if state is not None and state.get("done"):
                self._step_finalize_poll_tick()
            return True
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


    _NO_BREAKPOINT = "No breakpoint"


    #: Longest plausible rendering per summary readout -- the fixed
    #: width every label keeps regardless of the live value.
    _SUMMARY_SAMPLES = {
        "Ao": "Ao 0.999999999",
        "MTBDE": "MTBDE 999999.999",
        "MDT": "MDT 999999.999",
        "R": "R 0.999",
        "G/Y/R": "● 100.0%  ● 100.0%  ● 100.0%",
    }

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
            v.tool_mode = "select"
            self._on_tool_mode_changed("select")
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
            self._end_step_session()

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
            self._resize_docks([dock], [self._results_full_target()],
                             Qt.Orientation.Vertical)
        else:
            self._release_bottom_strip_ceiling()
            self._resize_docks([dock], [260], Qt.Orientation.Vertical)


    #: Shortest the Data Tables tab may open at: header plus several rows
    #: (it used to open as one row and a scrollbar under the other docks).
    DATA_TABLES_OPEN_HEIGHT = 260

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
            self._resize_docks([d], [target], Qt.Orientation.Vertical)
        except Exception:
            pass

    #: How much of the results-population bar each stage represents. The
    #: stage counts are not equal work: detail tables and graphs dominate.
    _RESULT_STAGE_PCT = {
        "overview": 5,
        "detail tables": 35,
        "level filter": 40,
        "graphs": 70,
        "raw report": 85,
        "event log": 100,
    }

    def _results_stage_progress(self, label: str, done: int, total: int) -> None:
        """Advance one results-population stage without flickering.

        `set_results` builds tables, graphs and the raw preview on the GUI
        thread. The old code called `showMessage` and `processEvents()` for
        every stage; a fast batch therefore repainted the status text six
        times in quick succession, which read as flicker. Drive the thin
        progress bar monotonically instead, and repaint the message at most
        once every `_STAGE_PUMP_SECONDS`.

        The re-entrancy guard keeps `processEvents()` from landing back in
        `_bg_poll_tick` (or another results population) while the dock is
        half-built; user input is excluded so a Run/Close click cannot
        either. The click is not lost -- it is delivered once population
        returns.
        """
        pct = self._RESULT_STAGE_PCT.get(label)
        if pct is None:
            pct = 100 * int(done) // max(int(total), 1)
        pct = max(int(pct), int(getattr(self, "_results_stage_pct", 0)))
        pct = min(pct, 100)
        if getattr(self, "_results_follow_up", False):
            # More work follows this population (importance variants). A
            # "100%" here would be indistinguishable from "finished" and
            # is exactly what used to sit on screen for minutes.
            pct = min(pct, 99)
        self._results_stage_pct = pct
        self._run_progress.setVisible(True)
        self._run_progress.setValue(pct)
        self._run_progress_label.setVisible(True)
        self._run_progress_label.setText(f"{pct}%")
        now = time.monotonic()
        if pct >= 100 or now - getattr(self, "_last_results_pump", 0.0) >= \
                self._STAGE_PUMP_SECONDS:
            self._last_results_pump = now
            self._statusbar.showMessage(f"Processing results — {label}…")
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    #: Minimum gap between event-loop pumps from the per-run report-stage
    #: callback. The runner calls it once per run during the End-of-Run
    #: build; pumping the loop thousands of times would make a large
    #: batch's report phase slower than the report itself.
    _STAGE_PUMP_SECONDS = 0.05

    def _set_results_with_progress(self, *args, follow_up: bool = False,
                                   **kwargs):
        """Call ResultsDock.set_results with smooth stage feedback, guarded.

        Returns False when another population is already in progress; the
        caller can then retry on the next poll instead of assuming the
        dock now holds its results. `follow_up=True` leaves the progress
        bar visible below 100% for a phase that is still to come
        (importance variants); the normal final population still clears it.
        """
        if getattr(self, "_results_populating", False):
            return False
        self._results_populating = True
        self._results_stage_pct = 0
        self._last_results_pump = 0.0
        self._results_follow_up = bool(follow_up)
        self._run_progress.setRange(0, 100)
        self._run_progress.setValue(0)
        self._run_progress_label.setText("0%")
        self._run_progress.setVisible(True)
        self._run_progress_label.setVisible(True)
        self._statusbar.showMessage("Processing results…")
        QApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        try:
            kwargs.setdefault("progress", self._results_stage_progress)
            self._results.set_results(*args, **kwargs)
        finally:
            self._results_populating = False
            self._results_stage_pct = 0
            self._results_follow_up = False
            if not follow_up:
                self._run_progress.setValue(100)
                self._run_progress_label.setText("100%")
                self._run_progress.setVisible(False)
                self._run_progress_label.setVisible(False)
        return True

    def _report_stage_progress(self, done: int, total: int, label: str) -> None:
        """Worker/poll-facing report-stage callback (no Qt widgets).

        Runs on whichever thread built the reports -- on the GUI thread in
        the synchronous path, where it also paints (throttled). The
        background path passes a plain-data recorder instead, and the poll
        renders it.
        """
        total = max(int(total), 1)
        done = int(done)
        self._statusbar.showMessage(
            f"Processing results — {label} {done}/{total}")
        now = time.monotonic()
        if done >= total or now - getattr(self, "_last_stage_pump", 0.0) >= \
                self._STAGE_PUMP_SECONDS:
            self._last_stage_pump = now
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def _auto_layout(self):
        """Run Auto Layout with visible busy feedback.

        The layout itself is fast, but the annotations-follow pass used to
        scan every element for every element (quadratic) and the canvas
        rebuild is on the UI thread. Without this the app simply froze for
        seconds after Ctrl+L, especially from a small hierarchy inside a
        large model.
        """
        view = getattr(getattr(self, "_diagram_dock", None), "view", None)
        if view is None:
            return
        label = view.level_label()
        selected, total = view.layout_selection_count()
        # Same rule as `DiagramView.auto_layout`: a partial selection is
        # laid out on its own, otherwise the whole level.
        count = selected if 0 < selected < total else total
        what = _items_phrase(count, selected=0 < selected < total)
        place = "on Home" if label == "Home" else f"in {label}"
        self._statusbar.showMessage(f"Auto Layout — {what} {place}…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents(
            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        done = False
        try:
            view.auto_layout()
            done = True
        finally:
            QApplication.restoreOverrideCursor()
            if done:
                self._statusbar.showMessage(
                    f"Auto Layout done: {what} {place}", 6000)

    def _sync_auto_layout_tooltip(self) -> None:
        """Auto Layout's tooltip says what it will arrange: the selection
        only (with its size) when some but not all elements are selected,
        otherwise the whole level."""
        view = getattr(getattr(self, "_diagram_dock", None), "view", None)
        action = getattr(self, "_auto_layout_action", None)
        if view is None or action is None or getattr(view, "_loading", False):
            return
        selected, total = view.layout_selection_count()
        if 0 < selected < total:
            tip = (f"Auto Layout the {_items_phrase(selected, selected=True)}"
                   f" only, centred where they are now; nothing else moves."
                   f" (Ctrl+L)")
        else:
            tip = _AUTO_LAYOUT_TIP
        self._commands.set_tooltip(action, tip)

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


    _RECENT_KEY = "files/recent"
    #: The File view lists and searches them, so the list can be long.
    _RECENT_MAX = 50





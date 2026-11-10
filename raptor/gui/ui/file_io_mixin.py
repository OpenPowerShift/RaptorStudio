"""File commands: new, open, save, save as, close, quit and the busy cursor.

Expects on `self`: `_rbd`, `_current_file`, `_diagram_dock`, `_statusbar`."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raptor.gui.ui.qtcompat import req

from raptor.gui.ui import msgbox
import os
import re
from pathlib import Path
from PySide6.QtCore import (Qt, QTimer)
from PySide6.QtWidgets import (
    QApplication, QFileDialog,
)

from raptor.gui.ui import filedialogs as fdg
from raptor.gui.ui import settings
from raptor.core import run_experiment
from raptor.core.fileio.rbd import save_rbd, RbdFile
from raptor.core.fileio import rbds as rbds_io
from raptor.core.fileio.typed import system_config_from_row
from raptor.core.edit.new_model import build_new_model




if TYPE_CHECKING:
    # Mixin methods call Qt on `self`; type-check them as a main window.
    from PySide6.QtWidgets import QMainWindow as _Base
    import PySide6.QtGui
    import PySide6.QtWidgets
    import raptor.gui.ui.data_tables
    import raptor.gui.ui.diagram
    import raptor.gui.ui.explorer
    import raptor.gui.ui.inspector
    import raptor.gui.ui.results
    import raptor.gui.ui.run_panel
else:
    _Base = object


class FileIoMixin(_Base):
    """File commands: new, open, save, save as, close, quit and the busy cursor."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        def _block_defaults(self) -> dict: ...
        def _bump_model_revision(self) -> None: ...
        _busy_dialog: Any  # really PySide6.QtWidgets.QProgressDialog | None
        def _cancel_pending_block_selection(self) -> None: ...
        def _confirm_discard_unsaved(self, *, closing: bool = ...) -> bool: ...
        def _current_dock_extents(self) -> dict | None: ...
        _current_file: str | None
        _data_tables: raptor.gui.ui.data_tables.DataTablesDock
        _diagram_dock: raptor.gui.ui.diagram.DiagramDock
        _dirty: bool
        _dock_extents_at_load_start: Any  # really dict[Any, Any] | None
        def _ensure_validation_timer(self) -> Any: ...
        _explorer: raptor.gui.ui.explorer.ExplorerDock
        def _find_combo_refresh(self, _filter_text: str = ...) -> Any: ...
        def _give_canvas_room(self, *, force: bool = ...) -> Any: ...
        def _hide_phase_labels(self) -> None: ...
        _inspector: raptor.gui.ui.inspector.InspectorDock
        def _invalidate_step_session(self) -> Any: ...
        _loading_model: bool
        def _migrate_report_stream_settings(self) -> None: ...
        def _open_path(self, path: str) -> Any: ...
        _opened_legacy_format: bool
        def _populate_breakpoints(self) -> Any: ...
        def _push_hierarchy_options(self) -> Any: ...
        _rbd: Any  # really raptor.core.fileio.rbd.RbdFile | None
        def _refresh_commands(self, state: Any = ..., only: set[str] | None = ...) -> None: ...
        def _refresh_goto_combo(self) -> Any: ...
        def _refresh_run_scope_options(self) -> None: ...
        def _refresh_summary_freeze(self, units: str | None = ...) -> None: ...
        def _refuse_during_run(self, what: str) -> bool: ...
        def _remember_recent(self, path: str) -> Any: ...
        def _reset_right_docks(self) -> Any: ...
        def _reset_status_summary(self) -> Any: ...
        _results: raptor.gui.ui.results.ResultsDock
        _results_float_action: Any
        _results_show_action: Any
        _run_panel: raptor.gui.ui.run_panel.RunPanel
        _run_progress: PySide6.QtWidgets.QProgressBar
        _run_progress_label: PySide6.QtWidgets.QLabel
        _run_scope_return_level: int | None
        _run_to_label: PySide6.QtWidgets.QLabel
        def _schedule_validation(self) -> Any: ...
        _statusbar: PySide6.QtWidgets.QStatusBar
        def _sync_analysis_checkboxes(self) -> Any: ...
        def _sync_block_label_controls(self) -> None: ...
        def _sync_link_routing_combo(self) -> None: ...
        def _sync_overlay_actions(self, report_data: Any) -> None: ...
        def _sync_run_mirrors(self) -> None: ...
        def _sync_run_panel_units(self) -> None: ...
        def _sync_simulate_enabled_state(self) -> Any: ...
        _undo_stack: PySide6.QtGui.QUndoStack
        _units_chip: PySide6.QtWidgets.QLabel
        def _update_overlay_chip(self) -> None: ...
        def _update_readiness(self) -> None: ...
        def _update_system_swatch(self, colour: Any, force: bool = ...) -> None: ...
        def _update_title(self) -> Any: ...
        def _validate_model(self, quiet: bool = ...) -> Any: ...
        _validation_badge: PySide6.QtWidgets.QPushButton
        def _window_for_open(self, path: str | None = ...) -> Any: ...
        def show_start_page(self) -> None: ...

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
        # The discard prompt must see the comment draft as part of the
        # model; otherwise Cancel/Don't Save loses/writes it too late.
        self._inspector.flush_pending_comment()
        if not self._confirm_discard_unsaved(closing=True):
            return
        self._invalidate_step_session()
        # Closing the model is not an edit: always return the right-hand
        # docks to their unrun state.
        self._reset_right_docks()
        # Reset the view's per-file state, then leave the canvas EMPTY.
        # It used to keep showing that placeholder model -- a diagram, a full
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
        # The placeholder model above must not leave an Explorer tree (it
        # lists the model's rows, which the placeholder still has), a Data
        # Tables row or a live Run panel behind with no model underneath.
        self._explorer.set_view(None)
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
        # Last-window close already did this; also covers a quit with no
        # windows left (and any path that skipped closeEvent).
        from raptor.gui.ui.help_service import close_help_viewer
        close_help_viewer()
        QApplication.quit()

    def _new_file(self):
        target = self._window_for_open()
        if target is not self:
            target._new_file()
            return
        if self._refuse_during_run("create a new model"):
            return
        self._inspector.flush_pending_comment()
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
        self._busy_dialog: QProgressDialog | None = dlg
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
        self._dock_extents_at_load_start = self._current_dock_extents()
        # Must run before `_loading_model` and before the model swap: the
        # draft belongs to the old model and its old Inspector editor.
        self._inspector.flush_pending_comment()
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
        # A build tool armed on the previous model (Add Node, Connect...)
        # must not carry over: the new model showed its cursor and the
        # first click placed an element. The view's mode change also
        # unchecks the ribbon button.
        self._diagram_dock.view.tool_mode = "select"
        self._rbd = rbd
        # a stepped session belongs to the previous model -- stepping
        # after an Open must never touch the old engine context
        self._invalidate_step_session()
        # A New/Open model is unrun even when no step session was open:
        # Inspector front, Live Log parked.
        self._reset_right_docks()
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
        # The view options the file was saved with, before the canvas draws
        # (link routes depend on them); a file without any keeps ours.
        v.apply_view_options(getattr(rbd, "view", None) or {})
        self._sync_link_routing_combo()
        v.load_rbd(rbd)
        self._sync_block_label_controls()
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
        self._data_tables.set_view(v, defer_large=True)
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
        # A new model starts on whole-model scope even if a hierarchy id
        # from the previous file happens to collide.
        self._run_scope_return_level = None
        self._run_panel.set_hierarchies([])
        self._refresh_run_scope_options()
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

        rbd = self._rbd

        def n(section: str, skip_default_block: bool = False) -> int:
            sec = rbd.sections.get(section)
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
        # A comment draft typed within the debounce window is part of
        # what the user is saving; commit it before the write-back.
        self._inspector.flush_pending_comment()
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
            rbds_io.save_rbds(req(self._rbd), path)
            # The open model is now THIS file: reports and the run
            # manifest title themselves with `RbdFile.file_name`, so a
            # Save As has to move it with the file instead of leaving
            # the template's "NewModel.rbd" behind. (`save_rbd` does the
            # same for the legacy format.)
            rbd = req(self._rbd)
            rbd.file_name = Path(path).name
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
        block_sec = req(self._rbd).sections.get("BLOCK_DATA")
        age_blocks = 0
        for row in (block_sec.rows if block_sec is not None else []):
            try:
                if (float(row.get("_DutyCycle", 1.0)) != 1.0
                        or float(row.get("_StartAge", 0.0)) != 0.0
                        or float(row.get("_RestoreFactor", 0.0)) != 0.0
                        or int(float(row.get("_RestoreType", 0))) != 0):
                    age_blocks += 1
            except (TypeError, ValueError):
                continue
        if age_blocks:
            msgbox.warning(
                self, "Age settings not supported in the .rbd format",
                f"{age_blocks} block(s) use duty cycle, start age or "
                "restoration settings. The .rbd format has no "
                "columns for them and they will be DROPPED. The simulation "
                "values that follow are unaffected in this session; save as "
                ".rbds to keep the settings.")
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
        save_rbd(req(self._rbd), path)
        # The layout is exported as it is (the same note the CLI and MCP
        # export give): say so when Raptor 7 would draw it overlapping.
        from raptor.api.validate import layout_warning
        crowded = layout_warning(req(self._rbd))
        if crowded:
            msgbox.warning(self, "Layout exported as it is",
                           "The .rbd was saved with this layout, but "
                           + crowded)
        return True

    def _open_model_any(self, path: str):
        """Read .rbds or legacy .rbd by extension: the loader the CLI and MCP
        server use too, with the same schema check and messages."""
        from raptor.core.fileio.model_file import load_model_file
        return load_model_file(path)

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

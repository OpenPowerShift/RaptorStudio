"""Failure Effects View in the main window.

Raptor 7's Failure Effects View is a mode of its own: the model is shown in
its all-operating state (phase 1), a click fails or restores an element, the
phase arrows and the two erasers move through the scenario, and a text summary
and a log file say what the system is doing. Nothing is simulated. The engine
side is `raptor.core.engine.fev.FevSession`; this module is only the window.

A mixin rather than a collaborator for the same reason as
`run_experiment_mixin`: every method reaches a dozen pieces of window state.

Expects on `self`: `_rbd`, `_diagram_dock.view`, `_statusbar`,
`_fev_view_action`, `_fev_log_*_action`, `_fev_status_dialog`,
`_fev_status_text`, `_step_session`, `_run_or_step_active()`,
`_invalidate_step_session()`, `_refresh_commands()`, `_update_system_swatch()`.
"""
from __future__ import annotations

from raptor.gui.ui import msgbox

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from raptor.gui.ui import filedialogs as fdg

if TYPE_CHECKING:
    from raptor.core.engine.fev import FevSession


class FevMixin:
    """Enter / leave Failure Effects View, act on elements, move through phases."""

    _fev: FevSession | None = None
    _fev_status_dialog: Any = None
    _fev_status_text: Any = None
    _fev_log: Any = None

    if TYPE_CHECKING:      # pragma: no cover - typing only
        def __getattr__(self, name: str) -> Any:
            """The window's own attributes, which this mixin reaches through `self`."""

    # ---- entering and leaving ------------------------------------------------

    def _on_fev_toggle(self, checked: bool) -> None:
        if checked:
            if not self._enter_fev():
                self._set_fev_check(False)
        else:
            self._leave_fev()

    def _set_fev_check(self, on: bool) -> None:
        act = getattr(self, "_fev_view_action", None)
        if act is not None and act.isChecked() != on:
            act.blockSignals(True)
            act.setChecked(on)
            act.blockSignals(False)

    def _enter_fev(self) -> bool:
        if self._fev is not None:
            return True
        if self._rbd is None:
            return False
        if getattr(self, "_run_in_progress", False):
            self._statusbar.showMessage(
                "Failure Effects: stop the run first", 6000)
            return False
        if getattr(self, "_step_session", None) is not None:
            ans = QMessageBox.question(
                self,  # type: ignore[arg-type]
                "Failure Effects",
                "A stepped run is in progress. Opening Failure Effects View "
                "discards it. Continue?")
            if ans != QMessageBox.StandardButton.Yes:
                return False
            self._invalidate_step_session()
        from raptor.core.engine.errors import UnrunnableModel
        from raptor.core.engine.fev import FevSession
        QApplication.setOverrideCursor(_wait_cursor_shape())
        try:
            session = FevSession(self._model_for_engine())
        except UnrunnableModel as exc:
            QApplication.restoreOverrideCursor()
            msgbox.warning(self, "Failure Effects", str(exc))
            return False
        except Exception as exc:      # noqa: BLE001 - shown to the user, not swallowed
            QApplication.restoreOverrideCursor()
            msgbox.critical(
                self, "Failure Effects",
                f"Failure Effects View could not start:\n{exc}")
            return False
        QApplication.restoreOverrideCursor()
        self._fev = session
        self._set_fev_check(True)
        if not self._diagram_dock.isVisible():
            self._diagram_dock.show()
        self._diagram_dock.view.set_fev_view_open(True)
        self._refresh_commands()
        self._fev_refresh("Failure Effects View — click a block to fail or restore it")
        return True

    def _leave_fev(self) -> None:
        if self._fev is None:
            return
        self._on_fev_log_close()
        if getattr(self, "_fev_status_dialog", None) is not None:
            self._fev_status_dialog.close()
        self._fev = None
        view = self._diagram_dock.view
        view.set_fev_view_open(False)
        view.clear_live_states()
        self._update_system_swatch(None)
        self._set_fev_check(False)
        self._refresh_commands()
        self._statusbar.showMessage("Left Failure Effects View", 4000)

    # ---- drawing -------------------------------------------------------------

    def _fev_refresh(self, message: str = "") -> None:
        s = self._fev
        if s is None:
            return
        self._diagram_dock.view.update_live_states(s.blocks, model=s.model, phase=s.phase_number if s.phased else None)
        self._update_system_swatch(s.system_color, force=True)
        text = f"{message} — " if message else ""
        text += f"System {s.system_color.value}"
        if s.phased:
            n = len(s.model.phases)
            text += f" — Phase {s.phase_number}/{n} \"{s.model.phases[s.phase_number - 1].phase_name}\""
        if s.fev_dep_group:
            text += f" — {len(s.fev_dep_group)} induced (hatched)"
        if self._fev_show_criticality():
            from raptor.core.engine.reports import real_to_str
            text += f" — Criticality = {real_to_str(s.criticality)}"
        flow = s.capacity()
        if flow is not None:
            # Not in Raptor 7's view: the flow this state delivers, as the
            # CLI's `fev --capacity` and the MCP tools report it.
            share = (f" ({flow['delivered_fraction']:.1%})"
                     if flow["delivered_fraction"] is not None else "")
            text += f" — Flow {flow['delivered']} of {flow['flow_generated']}{share}"
        self._statusbar.showMessage(text)
        if getattr(self, "_fev_status_dialog", None) is not None \
                and self._fev_status_dialog.isVisible():
            self._fev_status_text.setPlainText(self._fev_text())

    @staticmethod
    def _fev_show_criticality() -> bool:
        """The `show_criticality` developer option (Raptor 7's master-switch line
        `Criticality = x` in the status bar, the FMECA text and the SPF file)."""
        from raptor.gui import dev_settings
        return dev_settings.current_options().show_criticality

    def _fev_text(self) -> str:
        """The Failure Effects text, then the capacity lines for a model with
        capacity analysis (the written log keeps Raptor 7's format)."""
        from raptor.core.engine.fmeca import build_fmeca, capacity_lines
        session = self._fev
        assert session is not None   # only called while the view is open
        lines = build_fmeca(session, show_criticality=self._fev_show_criticality())
        flow = session.capacity()
        if flow is not None:
            lines = [*lines, *capacity_lines(flow)]
        return "\n".join(lines)

    # ---- acting on elements --------------------------------------------------

    def _fev_selected(self):
        """("block"|"event", id, name) for the single selected block or event."""
        from raptor.gui.ui.diagram import _BlockItem, _EventItem
        v = self._diagram_dock.view
        chosen = [it for it in v._scene.selectedItems()
                  if isinstance(it, (_BlockItem, _EventItem))]
        if len(chosen) != 1:
            return None
        it = chosen[0]
        if isinstance(it, _BlockItem):
            return "block", it._block_id, v.blocks[it._block_id].name
        return "event", it._event_id, v.events[it._event_id].name

    def _on_fev_fail(self) -> None:
        self._fev_trigger("fail")

    def _on_fev_repair(self) -> None:
        self._fev_trigger("repair")

    def _fev_trigger(self, mode: str) -> None:
        """Either order: an element already selected acts at once; otherwise the
        next click on one does."""
        if not self._enter_fev():
            self._set_fev_check(self._fev is not None)
            return
        picked = self._fev_selected()
        if picked is not None:
            self._fev_apply(mode, *picked)
            return
        self._diagram_dock.view.set_fev_armed(mode)
        self._statusbar.showMessage(
            f"{mode.title()} armed — click a block on the canvas (Esc to cancel)")

    def _on_fev_target_clicked(self, element_id: int, mode: str) -> None:
        """A click on the canvas while Failure Effects View is open."""
        if self._fev is None:
            return
        view = self._diagram_dock.view
        if mode == "event":
            event = self._fev.model.events_by_id.get(element_id)
            self._fev_apply("click", "event", element_id, event.name if event else "event")
        else:
            b = self._fev.model.blocks_by_id.get(element_id)
            self._fev_apply(mode, "block", element_id, b.name if b else "block")
        if mode in ("fail", "repair"):
            view.set_fev_armed("click")

    def _fev_apply(self, mode: str, kind: str, element_id: int, name: str) -> None:
        from raptor.core.model import BlockStatus, EventStatus
        s = self._fev
        if s is None:
            return
        if kind == "event":
            event = s.model.events_by_id[element_id]
            if (mode == "fail" and event.op_status != EventStatus.SUCCESS) or \
                    (mode == "repair" and event.op_status != EventStatus.FAILURE):
                self._statusbar.showMessage(f"Failure Effects — {name} is {event.op_status.value}; nothing to {mode}")
                return
            s.click_event(element_id)
        else:
            b = s.model.blocks_by_id[element_id]
            operating = b.op_status == BlockStatus.RUNNING or (
                b.op_status == BlockStatus.STANDBY and b.sb_stress > 0.0)
            failed = b.op_status in (BlockStatus.REPAIRING, BlockStatus.PM)
            if (mode == "fail" and not operating) or (mode == "repair" and not failed):
                self._statusbar.showMessage(
                    f"Failure Effects — {name} is {b.op_status.value}; nothing to {mode}")
                return
            s.click_block(element_id)
        self._fev_refresh(name)

    # ---- phases and erasers --------------------------------------------------

    def _on_fev_phase_prev(self) -> None:
        if self._fev is not None and self._fev.phased:
            self._fev.previous_phase()
            self._fev_refresh()

    def _on_fev_phase_next(self) -> None:
        if self._fev is not None and self._fev.phased:
            self._fev.next_phase()
            self._fev_refresh()

    def _choose_fev_phase(self, labels: list[str], current: int) -> int | None:
        """Ask which phase to go to (Raptor 7's Go To Phase menu,  205): its position in `labels`,
        or None if cancelled. A seam: tests replace it."""
        from PySide6.QtWidgets import QInputDialog
        label, ok = QInputDialog.getItem(
            self,  # type: ignore[arg-type]
            "Go To Phase", "Phase:", labels, current - 1, False)
        return labels.index(label) + 1 if ok else None

    def _on_fev_goto_phase(self) -> None:
        s = self._fev
        if s is None or not s.phased:
            return
        labels = [f"{i:03d}  {p.phase_name}" for i, p in enumerate(s.model.phases, 1)]
        choice = self._choose_fev_phase(labels, s.phase_number)
        if choice is not None:
            s.goto_phase(choice)
            self._fev_refresh()

    def _build_fev_menu(self):
        """Raptor 7's Failure Effects right-click menu: Write to Log, Display Failure
        Effects, Refresh Phase, Refresh All, Home, Up, Center-zoom. Entries are disabled as there: no log open, no
        phases, at the top level."""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(cast(QWidget, self))
        view = self._diagram_dock.view
        at_top = view._current_parent == 0
        entries = [
            ("Write to Log", self._on_fev_log_write, self._fev_log is not None),
            ("Display Failure Effects…", self._on_fev_show_status, True),
            ("Refresh Phase", self._on_fev_erase_phase, bool(self._fev and self._fev.phased)),
            ("Refresh All", self._on_fev_erase_all, True),
            ("Home", view.go_home, not at_top),
            ("Up", view.go_up, not at_top),
            ("Center-zoom", self._zoom_to_selection, True),
        ]
        for label, fn, enabled in entries:
            act = menu.addAction(label)
            act.setEnabled(enabled)
            act.triggered.connect(lambda _c=False, f=fn: f())
        return menu

    def _exec_fev_menu(self, menu, global_pos) -> None:
        """A seam: `QMenu.exec` cannot be stubbed, so tests replace this."""
        menu.exec(global_pos)

    def _on_fev_menu(self, global_pos) -> None:
        if self._fev is not None:
            self._exec_fev_menu(self._build_fev_menu(), global_pos)

    def _on_fev_erase_phase(self) -> None:
        if self._fev is not None and self._fev.phased:
            self._fev.refresh_phase()
            self._fev_refresh("Failures cleared")

    def _on_fev_erase_all(self) -> None:
        if self._fev is not None:
            self._fev.refresh_all()
            self._fev_refresh("Everything operating")

    # ---- the text: System Status Summary and the Interactive Log --------------

    def _on_fev_show_status(self) -> None:
        """"Display Failure Effects": the same text the log receives, in a
        non-modal window that follows the session."""
        if self._fev is None:
            self._statusbar.showMessage("Failure Effects: open Failure Effects View first")
            return
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import (
            QDialog, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout,
        )
        dlg = self._fev_status_dialog
        if dlg is None or not dlg.isVisible():
            dlg = QDialog(self)  # type: ignore[arg-type]
            dlg.setWindowTitle("System Status Summary")
            dlg.resize(560, 420)
            v = QVBoxLayout(dlg)
            self._fev_status_text = QPlainTextEdit()
            self._fev_status_text.setReadOnly(True)
            self._fev_status_text.setFont(
                QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            v.addWidget(self._fev_status_text)
            row = QHBoxLayout()
            write = QPushButton("Write to Log")
            write.clicked.connect(self._on_fev_log_write)
            row.addStretch(1)
            row.addWidget(write)
            v.addLayout(row)
            self._fev_status_write_btn = write
            self._fev_status_dialog = dlg
        self._fev_status_write_btn.setEnabled(self._fev_log is not None)
        self._fev_status_text.setPlainText(self._fev_text())
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_fev_log_open(self) -> None:
        if self._fev is None:
            self._statusbar.showMessage("Interactive Log: open Failure Effects View first")
            return
        path, _ = fdg.getSaveFileName(
            self, "Open Interactive Log", "interactive_log.txt",
            "Text files (*.txt)")
        if not path:
            return
        try:
            fh = open(path, "wb")          # noqa: SIM115 -- held open until Close Log
        except OSError as e:
            msgbox.warning(self, "Interactive Log", f"Could not create log:\n{e}")
            return
        self._fev_log = {"path": path, "fh": fh}
        self._fev_log_write_action.setEnabled(True)
        self._fev_log_close_action.setEnabled(True)
        self._statusbar.showMessage(f"Interactive Log open: {Path(path).name}", 6000)

    def _on_fev_log_write(self) -> None:
        """One more block of the Failure Effects text, in Raptor 7's layout
        (CRLF line ends)."""
        if not self._fev_log or self._fev is None:
            return
        from raptor.core.engine.fmeca import build_fmeca, format_fmeca_file_block
        block = format_fmeca_file_block(
            build_fmeca(self._fev, show_criticality=self._fev_show_criticality()))
        self._fev_log["fh"].write(block.encode("latin-1", "replace"))
        self._fev_log["fh"].flush()
        self._statusbar.showMessage(
            f"Wrote to {Path(self._fev_log['path']).name}", 6000)

    def _on_fev_log_close(self) -> None:
        log = getattr(self, "_fev_log", None)
        if not log:
            return
        try:
            log["fh"].close()
        except OSError:
            pass
        self._fev_log = None
        if getattr(self, "_fev_log_write_action", None) is not None:
            self._fev_log_write_action.setEnabled(False)
            self._fev_log_close_action.setEnabled(False)
        self._statusbar.showMessage("Interactive Log closed", 4000)


def _wait_cursor_shape():
    from PySide6.QtCore import Qt
    return Qt.CursorShape.WaitCursor

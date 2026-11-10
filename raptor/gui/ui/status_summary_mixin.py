"""The status-bar readouts: run summary, phase labels and the system swatch.

Expects on `self`: `_statusbar` and the latest report data."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raptor.gui.ui.window_support import _NoMinimumWidth

from raptor.gui.ui.qtcompat import req

from PySide6.QtCore import (Qt)
from PySide6.QtWidgets import (
    QLabel, QHBoxLayout,
)



from raptor.gui.resources.design_tokens import (
    APPENDIX_D_STATE_COLOURS, active_colour,
)


if TYPE_CHECKING:
    # Mixin methods call Qt on `self`; type-check them as a main window.
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class StatusSummaryMixin(_Base):
    """The status-bar readouts: run summary, phase labels and the system swatch."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        def __getattr__(self, name: str) -> Any:
            """State owned by `MainWindow`; typed loosely here."""
            ...

    def _ensure_summary_labels(self):
        """Create the permanent status-bar stat readouts once."""
        if hasattr(self, "_summary_labels"):
            return
        from PySide6.QtWidgets import QLabel, QSizePolicy
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

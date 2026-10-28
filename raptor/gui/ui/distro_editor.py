"""Reusable distribution-entry widget.

One widget -- distro combo + the right named parameter fields for the
picked distribution (GetParameters labels, via Inspector's
own `DISTRO_PARAM_LABELS`) + the same `CheckValidInput` validation the
Inspector commits under + optional Mean/Std Dev statistics -- so the
pool dialogs and the Phases dialog give the same distribution entry
experience the Inspector's Failure/Repair/PM forms do.

Deliberately not a replacement for the Inspector's `_distribution_params_row`: that
one additionally carries formula/@Rate fields, display-unit conversion
and per-field undo plumbing this widget doesn't need (its callers'
fields are plain numbers).
"""
from __future__ import annotations
from raptor.gui.ui.modality import exec_dialog

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QLabel, QLineEdit, QWidget)

from raptor.core.distro_rules import (
    DISTRO_PARAM_LABELS, validate_distro_params, param_error,
    params_after_change)
from raptor.gui.ui.inspector import compute_stats
from raptor.gui.ui import param_entry


class DistroEditor(QWidget):
    """Standard distribution entry: Type combo + one labelled field per
    parameter + Mean/Std Dev readouts. Emits the FULL value whenever a
    change leaves a valid set.

    A field is checked on its own when it is committed (a non-number, or
    one its own rule refuses, is put back). The rules that tie fields
    together -- Uniform min <= max, Triangular ordering -- are checked on
    the whole set: while they fail, the typed value stays, the message
    shows under the fields and nothing is emitted, so a Triangular can be
    moved up one field at a time. Every field edit used to be checked
    against the whole set, so after Fixed 0.25 became Binomial (N Trials
    0.25) no Probability could be entered at all."""

    #: (distro_code, [p1, p2, p3]) committed values -- always a valid set
    changed = Signal(int, list)
    #: Rejected input (reverted) -- same signal name/shape as
    #: InspectorDock.validation_failed so hosts share one handler.
    validation_failed = Signal(str)
    #: The whole set became valid (True) or invalid (False).
    validity_changed = Signal(bool)

    def __init__(self, distro: int, params: list[float],
                 use: str = "Distribution", units: str = "",
                 show_stats: bool = True, parent=None,
                 display_unit: str | None = None, label_width: int = 0):
        super().__init__(parent)
        #: minimum width of the label column, so a host form's own labels and these line up
        self._label_width = int(label_width)
        self._use = use
        self._units = units or ""
        # Optional display-unit override (same convention as every
        # other time field in this app): values are shown/entered in
        # `display_unit`, committed in the model's native `units`.
        self._display_unit = display_unit
        self._stats_labels: tuple[QLabel, QLabel] | None = None
        self._combo: QComboBox | None = None
        self._param_edits: list[QLineEdit] = []
        self._error_label: QLabel | None = None
        self._distro = int(distro)
        self._current = list(params) + [0.0] * (3 - len(params))
        self._valid = self.error() is None
        self._build_form()

    # ------------------------------------------------------------------

    def _u_suffix(self) -> str:
        return f" ({self._units})" if self._units else ""

    def _shown_units(self) -> str:
        return self._display_unit or self._units

    def _shown_suffix(self) -> str:
        """Unit suffix for the Mean / Std Dev readouts.

        The SHOWN unit, not the native one. `_u_suffix` (native) was the bug:
        it labelled a display-unit-converted number with the wrong unit, or
        left an unconverted native number labelled as if it were shown."""
        u = self._shown_units()
        return f" ({u})" if u else ""

    def _to_display(self, v: float) -> float:
        """Native -> shown, for the readouts.

        Same conversion the parameter fields use (`_display_text`). No-op
        when there is no native unit to convert from, which is also the
        case for a probability-valued distribution whose mean is a
        probability rather than a duration."""
        from raptor.gui.ui.units_conv import native_to_display
        if not self._units:
            return v
        return native_to_display(v, self._units, self._display_unit)

    def _is_time_param(self, idx: int) -> bool:
        labels = DISTRO_PARAM_LABELS.get(self._distro, ("", "", ""))
        lab = labels[idx] if idx < len(labels) else ""
        return bool(lab) and lab.lower() != "probability"

    def _entry_values(self) -> list[float]:
        """Current canonical values in shown units, then entry space."""
        from raptor.gui.ui.units_conv import native_to_display
        shown = [
            native_to_display(v, self._units, self._display_unit)
            if self._is_time_param(j) else v
            for j, v in enumerate(self._current)]
        # Lambda mode is not applied to these generic "other" boxes in
        # Raptor, but mu-sigma mode is.
        return param_entry.to_entry(self._distro, shown, lambda_mode=False)

    def _display_text(self, idx: int) -> str:
        return f"{self._entry_values()[idx]:.6g}"

    def _display_to_native(self, v: float) -> float:
        from raptor.gui.ui.units_conv import display_to_native
        if not self._units:
            return v
        return display_to_native(v, self._units, self._display_unit)

    def _build_form(self) -> None:
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setVerticalSpacing(2)
        form.setContentsMargins(0, 0, 0, 0)
        if self._label_width:
            from raptor.gui.resources.design_tokens import SPACING
            form.setHorizontalSpacing(SPACING["md"])     # the host form's own column gap
        # DELEGATED (review E-14). `DistroEditor`'s callers (phase lengths,
        # trigger and pool-row distributions) are all Raptor 7's generic
        # `DistBox`, so it offers that list: no Empirical, no None.
        from raptor.gui.ui.distro_combo import fill_distro_combo
        self._combo = fill_distro_combo(QComboBox(), self._distro,
                                        is_failure=True, kind="delay")
        self._combo.activated.connect(self._on_distro_changed)
        form.addRow("Type:", self._combo)
        self._param_edits = []
        labels = param_entry.labels(self._distro, lambda_mode=False)
        for i, lab in enumerate(labels):
            if not lab:
                continue
            is_time = self._is_time_param(i)
            edit = QLineEdit(self._display_text(i))
            edit.setToolTip(lab)
            edit.setAccessibleName(f"{self._use} {lab}")
            edit.editingFinished.connect(
                lambda idx=i, e=edit: self._commit_param(idx, e))
            self._param_edits.append(edit)
            unit = param_entry.unit_for(self._distro, i, self._shown_units(),
                                        lambda_mode=False)
            unit_text = f" ({unit})" if unit and is_time else ""
            form.addRow(f"{self._use} {lab}{unit_text}:", edit)
        # The whole-set problem, if any, in words under the fields.
        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        from raptor.gui.resources.design_tokens import active_colour
        self._error_label.setStyleSheet(f"color: {active_colour()['warn']}")
        form.addRow(self._error_label)
        # Labels don't survive a layout teardown, so always make fresh ones.
        self._stats_labels = (QLabel("—"), QLabel("—"))
        mean_lbl, sd_lbl = self._stats_labels
        form.addRow(f"Mean{self._u_suffix()}:", mean_lbl)
        form.addRow(f"Std Dev{self._u_suffix()}:", sd_lbl)
        if self._label_width:
            for r in range(form.rowCount()):
                item = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
                label = item.widget() if item is not None else None
                if label is not None:
                    label.setMinimumWidth(self._label_width)
        self._refresh_stats()
        self._show_error()

    def error(self) -> str | None:
        """Why the current set is not valid, or None."""
        return validate_distro_params(self._distro, list(self._current),
                                       self._use)

    def is_valid(self) -> bool:
        return self.error() is None

    def _show_error(self) -> None:
        err = self.error()
        if self._error_label is not None:
            self._error_label.setText(
                f"{err}. Not applied until this is fixed." if err else "")
            self._error_label.setVisible(err is not None)
        valid = err is None
        if valid != self._valid:
            self._valid = valid
            self.validity_changed.emit(valid)

    def _on_distro_changed(self) -> None:
        code = self._combo.currentData() if self._combo else None
        if code is None:
            return
        code = int(code)
        if code == self._distro:
            return
        # The old values where they still mean the same, else valid ones
        # with the same mean (as the Inspector does).
        seeded = params_after_change(self._distro, list(self._current), code,
                                     self._use,
                                     default_mean=self._current[0] or 1.0)
        self._distro = code
        if seeded is not None:
            self._current = list(seeded)
        self._rebuild()
        if self.is_valid():
            # A change of distribution is an edit too: hosts that save as
            # they go (the spare-pool rows) lost it until a field changed.
            self.changed.emit(self._distro, list(self._current))

    def _rebuild(self) -> None:
        # Rebuild the form for the distribution's own parameter fields
        # (Gamma -> Shape/Scale/Location, Normal -> Mean/Std Dev, ...).
        old = self.layout()
        if old is not None:
            QWidget().setLayout(old)
        self._build_form()

    def _commit_param(self, idx: int, edit: QLineEdit) -> None:
        try:
            v = float(edit.text().strip())
        except ValueError:
            edit.setText(self._display_text(idx))
            self.validation_failed.emit("not a number")
            return
        from raptor.gui.ui.units_conv import native_to_display, display_to_native
        shown_old = [
            native_to_display(val, self._units, self._display_unit)
            if self._is_time_param(j) else val
            for j, val in enumerate(self._current)]
        entered = list(self._entry_values())
        entered[idx] = v
        converted = param_entry.from_entry(self._distro, entered,
                                           old=shown_old, lambda_mode=False)
        trial = [
            display_to_native(converted[j], self._units,
                              self._display_unit)
            if self._is_time_param(j) else converted[j]
            for j in range(3)]
        err = param_error(self._distro, idx, trial[idx], self._use)
        if err is not None:
            edit.setText(self._display_text(idx))
            self.validation_failed.emit(err)
            self._show_message(err)
            return
        trial_set = list(self._current)
        if param_entry.is_mu_sigma(self._distro):
            trial_set[:2] = trial[:2]
        else:
            trial_set[idx] = trial[idx]
        if trial_set == self._current:
            edit.setText(self._display_text(idx))
            return
        self._current = trial_set
        edit.setText(self._display_text(idx))
        self._refresh_stats()
        self._show_error()
        if self.is_valid():
            self.changed.emit(self._distro, list(self._current))

    def _show_message(self, text: str) -> None:
        """A refused field's reason, under the fields (until the next edit)."""
        if self._error_label is not None:
            self._error_label.setText(text)
            self._error_label.setVisible(True)

    def _refresh_stats(self) -> None:
        if self._stats_labels is None:
            return
        # No figures for a set that is not a distribution yet.
        stats = (compute_stats(self._distro, list(self._current))
                 if self.is_valid() else None)
        mean_lbl, sd_lbl = self._stats_labels
        mean_v, sd_v = stats if stats is not None else (None, None)
        # DELEGATED (and the arithmetic corrected). This used to print the
        # RAW native number while labelling it with the NATIVE unit
        # (`_u_suffix()`), even though every parameter field in this same
        # form goes through `_display_text` -> `native_to_display`. With a
        # display-unit override active, one form showed "Mean 8760 (hours)"
        # beside "1 (years)" -- a silently wrong number, in the widget whose
        # own docstring claims parity with the Inspector's forms. The
        # Inspector's `_distribution_params_row` already converted; now both do, and both
        # label with the SHOWN unit.
        shown_suffix = self._shown_suffix()
        for lbl, val in ((mean_lbl, mean_v), (sd_lbl, sd_v)):
            if val is None:
                lbl.setText("—")
            elif isinstance(val, str):
                lbl.setText(val)
            else:
                shown = self._to_display(val)
                lbl.setText(f"{shown:.4g}{shown_suffix}")

    # ---- public API ------------------------------------------------

    def value(self) -> tuple[int, list[float]]:
        return self._distro, list(self._current)

    def set_value(self, distro: int, params: list[float]) -> None:
        self._distro = int(distro)
        self._current = list(params) + [0.0] * (3 - len(params))
        if self._combo is not None:
            ci = self._combo.findData(self._distro)
            self._combo.setCurrentIndex(max(ci, 0))
        self._rebuild()


def edit_distro_dialog(parent, distro: int, params: list[float],
                       use: str = "Distribution",
                       units: str = "") -> tuple[int, list[float]] | None:
    """Modal mini-dialog around `DistroEditor`; returns the committed
    (distro, params) or None on cancel."""
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"{use} distribution")
    lay = QVBoxLayout(dlg)
    editor = DistroEditor(distro, params, use=use, units=units)
    lay.addWidget(editor)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
    # OK only for a valid set: it used to hand back whatever was there, so
    # Binomial(N Trials 0.25, Probability 0) could reach the model.
    ok.setEnabled(editor.is_valid())
    editor.validity_changed.connect(ok.setEnabled)
    buttons.accepted.connect(
        lambda: dlg.accept() if editor.is_valid() else None)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    if exec_dialog(dlg) != QDialog.DialogCode.Accepted:
        return None
    return editor.value()

"""Inspector row builders: distribution, stream, number, name and comment editors.

Expects on `self`: the Inspector's tab layouts and undo stack."""
from __future__ import annotations

from raptor.gui.ui.inspector_common import (
    _CommentEdit,
    _FormulaCmd,
    _vbox,
)


from typing import TYPE_CHECKING, Any
from collections.abc import Callable
from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QWidget, QLayout, QVBoxLayout, QLabel, QGroupBox, QFormLayout, QHBoxLayout, QLineEdit,
    QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox, QPushButton,
)
from raptor.core.engine.diststats import compute_stats

# The distribution rules are `raptor.core.distro_rules`'s, not this
# module's (review D-04) -- they are pure model rules and belong in a
# Qt-free home. Imported here because this widget still uses both.
from raptor.core.distro_rules import (
    DISTRO_PARAM_LABELS, validate_distro_params, param_error)
from raptor.gui.resources.design_tokens import (
    SPACING,
)
from raptor.gui.ui import param_entry

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
    import PySide6.QtCore
else:
    _Base = object


class InspectorRowsMixin(_Base):
    """Inspector row builders: distribution, stream, number, name and comment editors."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        _comment_edit: Any
        def _comment_for(self, etype: str, oid: int, name: str) -> str: ...
        _rbd: Any  # really raptor.core.fileio.rbd.RbdFile | None
        _stack: Any  # really Any | None
        block_fail_stream_changed: PySide6.QtCore.SignalInstance
        block_repair_stream_changed: PySide6.QtCore.SignalInstance
        comment_to_annotation_requested: PySide6.QtCore.SignalInstance
        element_comment_changed: PySide6.QtCore.SignalInstance
        manage_pool_requested: PySide6.QtCore.SignalInstance
        phasing_changed: PySide6.QtCore.SignalInstance
        rates_changed: PySide6.QtCore.SignalInstance
        validation_failed: PySide6.QtCore.SignalInstance

    def _group(self, title: str, layout: QLayout | None) -> tuple[QGroupBox, QFormLayout]:
        layout = _vbox(layout)
        gb = QGroupBox(title)
        gb.setFlat(False)
        fl = QFormLayout(gb)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        fl.setFormAlignment(Qt.AlignmentFlag.AlignLeft)
        fl.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        fl.setHorizontalSpacing(SPACING["md"])
        fl.setVerticalSpacing(2)
        layout.insertWidget(layout.count() - 1, gb)
        return gb, fl

    def _distro_type_row(self, form: QFormLayout, distro: int,
                         is_failure: bool, bid: int, signal) -> None:
        """Editable Type combo (legacy Failure/Repair dialogs offer the
        full distribution list; 's own ConvertToString set
        is DISTRO_NAMES). `activated` fires on USER choice only, so
        re-populating the tab never echoes back."""
        # DELEGATED (review E-14): the code-18 relabel is owned by
        # `distro_combo.distro_display_name`, which decides it once from
        # `is_failure`. This site was the conditional copy; two dialogs
        # applied it unconditionally and data_tables not at all.
        from raptor.gui.ui.distro_combo import fill_distro_combo
        combo = fill_distro_combo(QComboBox(), distro,
                                  is_failure=is_failure)
        ci = combo.findData(distro)
        if ci < 0:
            combo.setToolTip(f"Unknown distribution code #{distro} — "
                             "pick one to fix it")
        combo.activated.connect(
            lambda _i, b=bid, cmb=combo: signal.emit(b, cmb.currentData()))
        form.addRow("Type:", combo)

    def _stream_editor_row(self, form: QFormLayout, cur: int,
                           which: str, bid: int) -> None:
        """Legacy Simulation Theory stream editor ( + ): System radio with a shared A..J combo
        vs an independent stream number with a per-side antithetic
        check (stored NEGATIVE). Picking a SYSTEM letter sets BOTH
        failure and repair streams -- intsArray[11] AND [12] come from
        one sysCombo. Widgets bound to valid ranges = validation by
        construction; the receiver re-checks anyway."""
        from raptor.core.rng import FETCH_RAPTOR_SEED
        max_ind = len(FETCH_RAPTOR_SEED) - 1

        c = QWidget()
        v = QVBoxLayout(c)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        # Mode and Stream on separate rows.
        row1 = QHBoxLayout(); row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)
        row1.addWidget(QLabel("Mode"))
        mode = QComboBox()
        mode.setMinimumWidth(120)
        mode.addItem("System", "sys")
        mode.addItem("Independent", "ind")
        row1.addWidget(mode); row1.addStretch(1)
        v.addLayout(row1)

        row2 = QHBoxLayout(); row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        row2.addWidget(QLabel("Stream"))
        letter = QComboBox()
        letter.setMinimumWidth(64)
        for i in range(10):
            letter.addItem(chr(ord("A") + i), 201 + i)
        row2.addWidget(letter)
        spin = QSpinBox()
        spin.setRange(1, max_ind)
        spin.setSuffix(f" of {max_ind}")
        spin.setMinimumWidth(96)
        row2.addWidget(spin)
        anti = QCheckBox("Anti")
        anti.setToolTip("Antithetic draws (stored as a negative "
                        "stream number)")
        row2.addWidget(anti)
        row2.addStretch(1)
        v.addLayout(row2)

        def populate() -> None:
            if 201 <= cur <= 210:
                mode.setCurrentIndex(0)
                li = letter.findData(cur)
                letter.setCurrentIndex(max(li, 0))
                letter.setVisible(True)
                spin.setVisible(False)
                anti.setVisible(False)
            else:
                mode.setCurrentIndex(1)
                spin.setValue(min(max(abs(cur), 1), max_ind))
                anti.setChecked(cur < 0)
                letter.setVisible(False)
                spin.setVisible(True)
                anti.setVisible(True)

        def send() -> None:
            if mode.currentData() == "sys":
                code = letter.currentData()
                self.block_fail_stream_changed.emit(bid, code)
                self.block_repair_stream_changed.emit(bid, code)
            else:
                v = spin.value() * (-1 if anti.isChecked() else 1)
                sig = (self.block_fail_stream_changed if which == "F"
                       else self.block_repair_stream_changed)
                sig.emit(bid, v)

        populate()
        def _on_block_stream_mode_changed(_i):
            populate()
            send()
        mode.activated.connect(_on_block_stream_mode_changed)
        letter.activated.connect(
            lambda _i: send() if mode.currentData() == "sys" else None)
        spin.valueChanged.connect(
            lambda _v: send() if mode.currentData() == "ind" else None)
        anti.toggled.connect(
            lambda _on: send() if mode.currentData() == "ind" else None)
        c.setToolTip(
            f"Random number stream. System A..J are shared streams; "
            f"independent streams draw from seeds 1..{max_ind}.")
        form.addRow("Stream:", c)

    @staticmethod
    def _row(form: QFormLayout, label: str, value, tooltip: str = ""):
        text = f"{value:.4g}" if isinstance(value, float) else str(value)
        vlabel = QLabel(text)
        vlabel.setWordWrap(True)
        vlabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(label + ":", vlabel)
        if tooltip:
            vlabel.setToolTip(tooltip)
            key_label = form.labelForField(vlabel)
            if key_label is not None:
                key_label.setToolTip(tooltip)
        return vlabel

    def _distribution_params_row(self, form: QFormLayout, distro: int, use: str, values: list[float], on_commit,
                    addr_prefix: str | None = None,
                    lambda_mode: bool | None = None):
        """A distribution's Param1/2/3 row, labelled the way the legacy
        Block Properties dialog labels it (GetParameters):
        one named field per parameter the distribution actually has
        (Weibull -> Shape/Scale/Location, Exponential -> Mean/Location,
        Fixed -> Constant, ...), with hidden slots shown as nothing at
        all (e.g. Empirical/None have no parameter boxes).

        `on_commit(new_values)` fires with the full current triple when
        any one field loses focus/Enter with a changed, valid value, so
        the receiver gets a complete row rather than tracking partial
        edits. Invalid text or a value outside the distribution's domain
        (the CheckValidInput rules -- negative Mean, Triangular
        Min<Mode<Max violated, ...) reverts that one field to its
        last-known-good value; `validation_failed` carries the reason.

        `addr_prefix` (e.g. "block:5:F") turns each field into a
        formula/rate field: typed text can be a plain number, a
        restricted arithmetic expression (optionally with a trailing
        "# basis" comment), or "@RateName" linking to the Rates library --
        see `expr_eval.py`. The original text is stored in
        `RbdFile.formulas[f"{addr_prefix}Param{i+1}"]` and shown again
        next time this field populates, so the basis isn't lost; a plain
        number clears any formula record for that slot. `None` (blocks
        called without a stable element id, e.g. inside a still-unsaved
        template row) falls back to plain-float-only editing."""
        labels_raw = DISTRO_PARAM_LABELS.get(distro, ("", "", ""))
        entry_labels = param_entry.labels(distro, lambda_mode=lambda_mode)
        # Show the model's time units on dimension-full labels: Shape/
        # N Trials/Probability stay bare; Mean/Scale/Location/Min/Max/
        # Mode/Std Dev carry e.g. '(hours)'. The semantic name comes
        # first and the unit sits in brackets, so Weibull reads Shape /
        # Scale / Location top-to-bottom. In lambda/mu-sigma entry modes
        # the unit is the entry space's: per hours, or ln(hours).
        native_units = getattr(self, "_units_str", "") or ""
        display_unit = getattr(self, "_display_unit", None)
        shown_units = display_unit or native_units
        is_time = [bool(lab) and lab not in
                  ("Shape", "Shape2", "N Trials", "Probability")
                  for lab in labels_raw]
        labels = []
        for j, lab in enumerate(entry_labels):
            if not lab:
                labels.append("")
                continue
            unit = param_entry.unit_for(distro, j, shown_units,
                                        lambda_mode=lambda_mode)
            labels.append(f"{lab} ({unit})" if unit and is_time[j] else lab)
        visible = [i for i, lab in enumerate(labels) if lab]
        # Empirical (16) has no per-param labels at all
        # (`DISTRO_PARAM_LABELS[16] == ("", "", "")` -- it's edited as
        # a whole data-point list, not 3 named slots), so `visible` is
        # empty for it. Let it fall through to the "Enter data…" button
        # built below: the per-param loop is a no-op on an empty
        # `visible`, so this only skips the label row, not the button.
        if not visible and distro != 16:
            form.addRow(use + " params:", QLabel("(no parameters)"))
            return
        # Three slots whatever arrives: a list left short by a distribution
        # change (an Empirical's points) crashed every redraw of the block.
        current = (list(values) + [0.0, 0.0, 0.0])[:max(3, len(values))]
        from raptor.core.expr_eval import (
            resolve, is_plain_number, is_bare_rate_ref, ExpressionError)
        from raptor.core.units_conv import native_to_display, display_to_native

        def addr_of(idx: int) -> str | None:
            return f"{addr_prefix}Param{idx + 1}" if addr_prefix else None

        def shown_canonical() -> list[float]:
            """Canonical values in the shown unit, before the entry-mode transform."""
            return [native_to_display(v, native_units, display_unit)
                    if is_time[j] else v for j, v in enumerate(current)]

        shown_current = shown_canonical()
        entry_current = param_entry.to_entry(distro, shown_current,
                                             lambda_mode=lambda_mode)

        def display_text(idx: int) -> str:
            addr = addr_of(idx)
            formula = (self._rbd.formulas.get(addr)
                      if (addr and self._rbd is not None) else None)
            if formula:
                if "rate" in formula:
                    return f"@{formula['rate']}"
                if "expr" in formula:
                    return formula["expr"]
            return f"{entry_current[idx]:.6g}"

        current_text = [display_text(i) for i in visible]
        # Fields holding a typed value that breaks only a rule tying the
        # fields together (Triangular Min < Mode < Max): {idx: (value,
        # pos, edit)}. Written with the edit that makes the set valid, so
        # Triangular 5, 10, 15 can become 20, 30, 40 field by field; each
        # field used to be put back as soon as it broke the ordering.
        pending: dict[int, tuple] = {}
        # One parameter per row (not packed side-by-side): the Inspector
        # sidebar is narrow, and a 3-parameter distribution (e.g.
        # Weibull's Shape/Scale/Location) squeezed into one horizontal
        # row would clip the later fields off-screen.
        for pos, i in enumerate(visible):
            edit = QLineEdit(current_text[pos])
            edit.setToolTip(labels[i])
            # A rate-capable field is wrapped in a container (edit + rate
            # button), so the form's label becomes the CONTAINER's buddy,
            # not the edit's, and a screen reader announced an unnamed box.
            edit.setAccessibleName(f"{use} {labels[i]}")
            if addr_prefix is None:
                edit.setValidator(QDoubleValidator())
            else:
                from raptor.gui.ui.rates_dialog import attach_rate_autocomplete
                attach_rate_autocomplete(
                    edit, lambda: [r.get("name", "") for r in
                                  (getattr(self._rbd, "rates", None) or [])])

            def commit(idx=i, pos=pos, edit=edit):
                nonlocal shown_current, entry_current
                text = edit.text().strip()
                addr = addr_of(idx)
                rates = getattr(self._rbd, "rates", None) if self._rbd else None
                is_formula = addr_prefix is not None and \
                    not is_plain_number(text)
                try:
                    if is_formula:
                        new_v = resolve(text, rates)   # native, as typed
                        new_pair = None
                    else:
                        raw = float(text)
                        if param_entry.is_rate_field(
                                distro, idx, lambda_mode=lambda_mode) \
                                and raw <= 0.0:
                            edit.setText(current_text[pos])
                            self.validation_failed.emit(
                                f"{use} {labels[idx].split(' (')[0]} "
                                "must be greater than 0")
                            return
                        entered = list(entry_current)
                        entered[idx] = raw
                        converted = param_entry.from_entry(
                            distro, entered, old=shown_current,
                            lambda_mode=lambda_mode)
                        if param_entry.is_mu_sigma(distro):
                            new_pair = [
                                display_to_native(converted[j], native_units,
                                                  display_unit)
                                if is_time[j] else converted[j]
                                for j in range(2)]
                            new_v = new_pair[idx]
                        else:
                            new_pair = None
                            new_v = display_to_native(converted[idx],
                                                      native_units,
                                                      display_unit) \
                                if is_time[idx] else converted[idx]
                except (ExpressionError, ValueError) as e:
                    edit.setText(current_text[pos])
                    self.validation_failed.emit(
                        str(e) if is_formula else "not a number")
                    return
                if new_v == current[idx] and text == current_text[pos]:
                    return
                own = param_error(distro, idx, new_v, use)
                if own is not None:            # wrong whatever the others say
                    edit.setText(current_text[pos])
                    self.validation_failed.emit(own)
                    return
                trial = list(current)
                for j, (pv, _p, _e) in pending.items():
                    trial[j] = pv
                if new_pair is not None:
                    trial[:2] = new_pair
                else:
                    trial[idx] = new_v
                err = validate_distro_params(distro, trial, use)
                if err is not None:
                    if is_formula:             # only a plain number is held
                        edit.setText(current_text[pos])
                        self.validation_failed.emit(err)
                        return
                    pending[idx] = (new_v, pos, edit)
                    self.validation_failed.emit(
                        f"{err}. Not applied until the other fields make "
                        f"it valid.")
                    return
                held = {j: h for j, h in pending.items() if j != idx}
                pending.clear()
                for j, (pv, ppos, pedit) in held.items():
                    current[j] = pv
                    shown_current = shown_canonical()
                    entry_current = param_entry.to_entry(distro,
                                                         shown_current,
                                                         lambda_mode=lambda_mode)
                    # A number typed over a rate link unlinks it, as a
                    # plain commit does (below).
                    paddr = addr_of(j)
                    if paddr and self._rbd is not None and \
                            paddr in self._rbd.formulas:
                        old_f = self._rbd.formulas.pop(paddr)
                        if self._stack is not None:
                            self._stack.push(_FormulaCmd(
                                self._rbd, paddr, old_f, None))
                    current_text[ppos] = display_text(j)
                    pedit.setText(current_text[ppos])
                if new_pair is not None:
                    current[:2] = new_pair
                else:
                    current[idx] = new_v
                shown_current = shown_canonical()
                entry_current = param_entry.to_entry(distro, shown_current,
                                                     lambda_mode=lambda_mode)
                if addr and self._rbd is not None:
                    old_formula = self._rbd.formulas.get(addr)
                    if is_formula:
                        bare_rate = is_bare_rate_ref(text)
                        if bare_rate is not None:
                            self._rbd.formulas[addr] = {
                                "rate": bare_rate, "value": new_v}
                        else:
                            # Also covers an embedded "@Rate * 1.4" style
                            # reference -- stored as a plain expr so
                            # `display_text` redisplays it verbatim, same
                            # as any other formula.
                            self._rbd.formulas[addr] = {
                                "expr": text, "value": new_v}
                    else:
                        self._rbd.formulas.pop(addr, None)
                        text = display_text(idx)
                    new_formula = self._rbd.formulas.get(addr)
                    # Wrap the basis-record change in its own undo command
                    # (the value command that follows doesn't carry it).
                    if old_formula != new_formula and self._stack is not None:
                        self._stack.push(_FormulaCmd(
                            self._rbd, addr, old_formula, new_formula))
                else:
                    text = display_text(idx)
                current_text[pos] = text
                edit.setText(text)
                on_commit(list(current))

            edit.editingFinished.connect(commit)
            row_widget: QWidget = edit
            if addr_prefix is not None:
                row_widget = QWidget()
                hl = QHBoxLayout(row_widget)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(4)
                hl.addWidget(edit, 1)
                rate_btn = QPushButton("📎")
                rate_btn.setFixedWidth(28)
                rate_btn.setAccessibleName(
                    f"Use a saved rate or formula for {use} {labels[i]}")
                rate_btn.setToolTip(
                    "Use a saved rate, or type a formula (e.g. "
                    "\"0.00015 * 20000 / 8760  # 0.00015 fail/km, "
                    "20000 km/yr\")")
                rate_btn.clicked.connect(
                    lambda _c=False, e=edit, c=commit:
                    self._pick_rate_into(e, c))
                hl.addWidget(rate_btn)
            form.addRow(f"{use} {labels[i]}:", row_widget)

        # --- Update Statistics (legacy parity):
        # mean/stdev of this distribution's committed params, shown as
        # read-only labels and refreshed on every commit. For Empirical
        # (16), an 'Enter data…' button opens the data-entry dialog.
        stats_labels: dict[str, Any] = {}
        u_stats = f" ({shown_units})" if shown_units else ""
        if distro == 16:
            enter_b = QPushButton(f"Enter data… ({use})")
            from raptor.gui.ui.empirical_dialog import open_empirical_dialog
            def open_entry():
                # full table lives on the block (emp_data key) if a
                # previous dialog pass stored it; otherwise seed from the
                # block's own points (all of them: the .rbd keeps an
                # empirical failure or repair law's points on a
                # continuation line)
                prior = getattr(self, "_emp_data", {}).get(id(enter_b))
                vals = prior if prior is not None else \
                    [v for v in current if v]
                shown_vals = [native_to_display(v, native_units, display_unit)
                             for v in vals]
                out = open_empirical_dialog(self, shown_vals,
                                            u_stats.strip(" ()"))
                if out is not None:
                    native_out = [display_to_native(v, native_units,
                                                    display_unit)
                                 for v in out]
                    on_commit(list(native_out))   # engine interpolates N points
                    _refresh()
            enter_b.clicked.connect(open_entry)
            stats_labels["__btn"] = enter_b
        mean_lbl = QLabel("—"); sd_lbl = QLabel("—")
        stats_labels["Mean"] = mean_lbl; stats_labels["Std Dev"] = sd_lbl
        if distro == 16:
            form.addRow("", enter_b)
        form.addRow(f"Mean{u_stats}:", mean_lbl)
        form.addRow(f"Std Dev{u_stats}:", sd_lbl)
        def _refresh():
            # `compute_stats` returns a bare None (not a 2-tuple) for an
            # invalid/insufficient parameterization (its own docstring:
            # "caller shows '-'") -- e.g. Beta with a 2nd shape param of
            # 0 -- so guard the unpack.
            stats = compute_stats(distro, list(current))
            mean_v, sd_v = stats if stats is not None else (None, None)
            for key, val in (("Mean", mean_v), ("Std Dev", sd_v)):
                lbl = stats_labels[key]
                if val is None:
                    lbl.setText("—")
                elif isinstance(val, str):
                    lbl.setText(val)
                else:
                    shown = native_to_display(val, native_units,
                                              display_unit)
                    lbl.setText(f"{shown:.4g}{u_stats}")
        _refresh()
        original_on_commit = on_commit

        def on_commit_with_stats(vals):
            # Plain assignment (not a per-index loop) so Empirical's
            # arbitrary-length `vals` from the data-entry dialog can grow
            # or shrink `current`.
            current[:] = vals
            _refresh()
            original_on_commit(vals)
        on_commit = on_commit_with_stats

    def _pick_rate_into(self, edit: QLineEdit, commit_fn) -> None:
        """"📎" button on a formula-enabled field: open the Rates
        picker, insert "@Name" into the field, and commit it
        immediately (matching what typing "@Name" by hand and pressing
        Enter would do)."""
        from raptor.gui.ui.rates_dialog import pick_rate
        name = pick_rate(self, self._rbd,
                         lambda: self.rates_changed.emit())
        if name:
            edit.setText(f"@{name}")
            commit_fn()

    def _float_row(self, form: QFormLayout, label: str, value: float, on_commit,
                   is_time: bool = False):
        """A single editable numeric field -- same commit/revert shape as
        `_distribution_params_row`'s per-field logic, just for one value instead of
        three (e.g. PM Frequency, not a 3-param distribution row).

        `is_time=True`: the field is a time-dimensioned value (PM
        Frequency, not e.g. a 0-100 threshold percentage) -- it shows and
        accepts the model's display unit (native unless a display-unit
        override is set) instead of the raw stored number, converting on
        the way in and out. Callers with a non-time value (percentages,
        counts) leave this False, which also means no unit suffix is added
        to `label`."""
        from raptor.core.units_conv import native_to_display, display_to_native
        native_units = getattr(self, "_units_str", "") or ""
        display_unit = getattr(self, "_display_unit", None) if is_time else None
        shown_units = (display_unit or native_units) if is_time else ""
        shown_label = f"{label} ({shown_units})" if shown_units else label

        def to_shown(v: float) -> float:
            return native_to_display(v, native_units, display_unit) \
                if is_time else v

        last_committed = {"value": value}
        edit = QLineEdit(f"{to_shown(value):.6g}")
        edit.setAccessibleName(shown_label)
        edit.setValidator(QDoubleValidator())

        def commit():
            text = edit.text().strip()
            try:
                raw = float(text)
            except ValueError:
                edit.setText(f"{to_shown(last_committed['value']):.6g}")
                return
            new_v = display_to_native(raw, native_units, display_unit) \
                if is_time else raw
            if new_v == last_committed["value"]:
                return
            last_committed["value"] = new_v
            edit.setText(f"{to_shown(new_v):.6g}")
            on_commit(new_v)

        edit.editingFinished.connect(commit)
        form.addRow(shown_label + ":", edit)
        # Long labels must wrap, not force the tab wide: the PM tab's
        # "At start-up, PM cycle has already elapsed (hours):" row would
        # otherwise make the narrow Inspector dock sprout a horizontal
        # scrollbar.
        lbl = form.labelForField(edit)
        if isinstance(lbl, QLabel):
            lbl.setWordWrap(True)
        return edit

    def _int_row(self, form: QFormLayout, label: str, value: int, on_commit, *,
                 minimum: int = 1, maximum: int | None = None):
        """Integer sibling of `_float_row` -- e.g. a node's K-out-of-N
        `good_paths` threshold, not a distribution parameter."""
        last_committed = {"value": value}
        edit = QLineEdit(str(value))
        edit.setAccessibleName(label)
        validator = QIntValidator()
        validator.setBottom(minimum)
        if maximum is not None:
            validator.setTop(maximum)
        edit.setValidator(validator)

        def commit():
            text = edit.text().strip()
            try:
                new_v = int(text)
            except ValueError:
                edit.setText(str(last_committed["value"]))
                return
            # `QIntValidator.setTop`/`setBottom` only reject some out-of-
            # range keystrokes while typing (its own "Intermediate" state
            # is permissive about digit COUNT, not the final value) --
            # clamp explicitly here so a value outside [minimum, maximum]
            # can never actually commit.
            if new_v < minimum:
                new_v = minimum
            elif maximum is not None and new_v > maximum:
                new_v = maximum
            if new_v == last_committed["value"]:
                edit.setText(str(new_v))
                return
            last_committed["value"] = new_v
            edit.setText(str(new_v))
            on_commit(new_v)

        edit.editingFinished.connect(commit)
        form.addRow(label + ":", edit)
        return edit

    def _name_edit(self, form: QFormLayout, current_name: str, on_commit):
        """An editable `Name` row -- `on_commit(new_name)` fires on Enter
        or focus-loss, only when the text actually changed (matching
        `editingFinished`'s "fires once you're done, not per keystroke"
        semantics, so a rename doesn't rebuild the canvas label on every
        keystroke).

        Whitespace is collapsed to underscores, not just stripped at the
        ends: `.rbd` rows are whitespace-delimited (`rbd.py`'s
        `_split_row`, plain `line.split()`), so a name containing an
        embedded space would shift every later column on save. This
        matches the underscore convention used by Raptor 7 names such as
        `SolarP_500W` and `BatteriesNiCd`."""
        edit = QLineEdit(current_name)
        edit.setAccessibleName("Name")
        # A plain closure over `current_name` would go stale after the
        # first successful commit: a second edit to an empty field on the
        # same QLineEdit, with no re-populate in between, would revert to
        # the original name shown when the field was built, not the
        # just-committed one. Track the last-known-good value in this
        # mutable cell instead, updated on every real commit.
        last_committed = {"name": current_name}

        def commit():
            new_name = "_".join(edit.text().split()) or last_committed["name"]
            if new_name != edit.text():
                edit.setText(new_name)
            if new_name != last_committed["name"]:
                last_committed["name"] = new_name
                on_commit(new_name)

        edit.editingFinished.connect(commit)
        # Original enforces names <= 20 chars. This port deliberately
        # keeps long names in the native .rbds format, so instead of
        # blocking, tell the user when a name is past the legacy limit
        # (the legacy .rbd save already truncates, with its own warning).
        def _len_hint(_t: str = ""):
            n = len(edit.text())
            if n > 20:
                edit.setToolTip(
                    f"{n} characters — the .rbd file format truncates "
                    f"names to 20 on save (.rbds keeps the full name).")
            else:
                edit.setToolTip("")
        edit.textChanged.connect(_len_hint)
        _len_hint()
        form.addRow("Name:", edit)
        return edit

    def _comment_editor(self, etype: str, oid: int, name: str,
                        on_commit: Callable[[str], None]) -> _CommentEdit:
        """Build the debounced Comment box for one element kind."""
        edit = _CommentEdit(
            self._comment_for(etype, oid, name).replace("~", "\n"),
            on_commit)
        edit.setFixedHeight(56)
        edit.setPlaceholderText(
            "Markdown supported: **bold**, *italic*, lists, links…")
        edit.setToolTip(
            "Free-text comment stored with the model (Part #, data "
            "source, etc.). Markdown is supported and is rendered when "
            "the comment is shown as a canvas annotation. Committed when "
            "you pause or leave the box.")
        return edit

    def _comment_markdown_hint(self, form: QFormLayout,
                               edit: QWidget) -> QLabel:
        """A visible reminder that comment text is rendered as Markdown."""
        hint = QLabel(
            "Markdown supported: **bold**, *italic*, lists, links and "
            "headings are rendered on linked canvas annotations.")
        hint.setObjectName("SectionNote")
        hint.setWordWrap(True)
        hint.setToolTip(edit.toolTip())
        form.addRow("", hint)
        return hint

    def _convert_comment_button(self, form: QFormLayout, etype: str,
                                oid: int, edit: _CommentEdit) -> QPushButton:
        """General > Identity's comment -> canvas annotation action.

        It flushes any debounced draft first, so clicking it immediately
        after typing converts exactly what is in the box. Disabled while
        the comment is empty: an empty annotation conveys nothing.
        """
        button = QPushButton("Convert comment to annotation")
        button.setToolTip(
            "Create a Markdown text annotation on the canvas from this "
            "comment, next to the element")

        def has_convertible_text() -> bool:
            # The stored format uses `~` for newlines, so a comment of
            # only tildes/newlines is blank and must not enable the
            # button (conversion would refuse it after the click).
            return bool(edit.toPlainText()
                        .replace("~", "\n").strip())

        button.setEnabled(has_convertible_text())
        edit.textChanged.connect(
            lambda: button.setEnabled(has_convertible_text()))

        def convert() -> None:
            edit.commit()
            self.comment_to_annotation_requested.emit(etype, oid)

        button.clicked.connect(convert)
        form.addRow(button)
        return button

    def _comment_row(self, form: QFormLayout, etype: str, oid: int, name: str) -> None:
        """Legacy Raptor 7 comment box (Part #, data provenance…), same
        debounced widget/behaviour as the block General tab's own (which
        stays on its bespoke `block_comment_changed` path rather than
        being rewired through here). Used for node/event/hier, whose
        Raptor 7 Node/Event/Hierarchy Properties General tabs all have
        this box too. Commits via the generic
        `element_comment_changed(etype, oid, text)`, which
        `DiagramView.set_element_comment` accepts.
        """
        edit = self._comment_editor(
            etype, oid, name,
            lambda txt: self.element_comment_changed.emit(etype, oid, txt))
        self._comment_edit = edit
        form.addRow("Comment:", edit)
        self._comment_markdown_hint(form, edit)
        self._convert_comment_button(form, etype, oid, edit)

    def _phasing_group(self, layout: QLayout | None, kind: str, oid: int,
                       phased_now: bool,
                       entries_now: list[tuple[str, float]]):
        """Phase assignment UI: only when the model
        defines phases. One Apply button commits the whole table via a
        single undoable command."""
        layout = _vbox(layout)
        sec = self._rbd.sections.get("PHASES") if self._rbd else None
        if not sec or not sec.rows:
            # No mission phases defined yet -- still surface a way in
            # (Raptor 7's Options menu, mirrored here) rather than
            # silently hiding the whole feature.
            _gb, fl = self._group("Phasing", layout)
            self._row(fl, "Status", "No phases defined")
            define_btn = QPushButton("Define Phases…")
            define_btn.setToolTip(
                "Create mission phases")
            define_btn.clicked.connect(
                lambda: self.manage_pool_requested.emit("PHASES"))
            fl.addRow(define_btn)
            return
        phase_names = [r.get("PhaseName", f"Phase {i + 1}")
                       for i, r in enumerate(sec.rows)]
        _gb, fl = self._group("Phasing", layout)
        phased_cb = QCheckBox("Phased")
        phased_cb.setChecked(phased_now)
        phased_cb.setToolTip(
            "A phased element's attributes change from one phase to "
            "another (below) -- shown hatched on the canvas.")
        fl.addRow(phased_cb)
        editors: list[tuple[QComboBox, Any]] = []
        for pi, phase_name in enumerate(phase_names):
            code_now = entries_now[pi][0] if pi < len(entries_now) else (
                "A" if kind != "node" else "L")
            val_now = entries_now[pi][1] if pi < len(entries_now) else 1.0
            combo = QComboBox()
            if kind == "node":
                for code, label in (("K", "k paths"), ("C", "Cut"),
                                    ("L", "Linked")):
                    combo.addItem(label, code)
            else:
                for code, label in (("A", "Active stress"), ("L", "Linked"),
                                    ("C", "Cut")):
                    combo.addItem(label, code)
            ci = combo.findData(code_now)
            combo.setCurrentIndex(max(ci, 0))
            spin: QSpinBox | QDoubleSpinBox
            if kind == "node":
                spin = QSpinBox()
                spin.setRange(0, 999)
                spin.setValue(int(val_now) if code_now == "K" and val_now
                              else max(int(val_now), 0))
            else:
                spin = QDoubleSpinBox()
                spin.setRange(-1e9, 1e9)
                spin.setDecimals(3)
                spin.setValue(float(val_now))
            fl.addRow(f"{phase_name}:", combo)
            fl.addRow("", spin)
            editors.append((combo, spin))

        def apply_phasing():
            entries = []
            for combo, spin in editors:
                code = combo.currentData()
                if kind == "node" and code == "K":
                    entries.append(("K", float(spin.value())))
                else:
                    entries.append((code, float(spin.value())))
            self.phasing_changed.emit(kind, oid,
                                      phased_cb.isChecked(), entries)

        apply_btn = QPushButton("Apply phasing")
        apply_btn.clicked.connect(apply_phasing)
        fl.addRow(apply_btn)
        manage_btn = QPushButton("Manage Phases…")
        manage_btn.setToolTip(
            "Add, rename or delete mission phases")
        manage_btn.clicked.connect(
            lambda: self.manage_pool_requested.emit("PHASES"))
        fl.addRow(manage_btn)

"""Master-detail dialogs for Spare Pools, Resources, Triggers and
Phases (Raptor 7: Options ▸ Spares/Resources/Triggers/Phases).

Layout mirrors the Raptor 7: a list of existing entities on the left,
named fields on the right in a titled card, Apply / Close along the
bottom (the real dialogs run Help/Cancel/Delete/Apply/OK across the
bottom, not stacked beside the fields). Every Apply is one undoable
macro of RowCellCmds; Add/Delete push TableRowCmd. Distribution fields
are a combo + per-label parameter boxes exactly like the Inspector.
"""
from __future__ import annotations
from raptor.gui.ui.modality import exec_dialog

from typing import Any

from raptor.gui.ui.qtcompat import req

from PySide6.QtWidgets import QFrame

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
    QFormLayout, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton, QLabel, QGroupBox, QScrollArea,
)
from PySide6.QtCore import Qt

from raptor.gui.ui.data_tables import DataTablesDock
from raptor.gui.ui.diagram import RowCellCmd, TableRowCmd
from raptor.gui.resources.design_tokens import SPACING, active_colour

# ---------------------------------------------------------------------------
# field specs -- (label, column, kind). kind:
#   'str' | 'int' | 'float' | 'bool' | 'dist'  (dist drives Param1-3)
# time-dimension floats get the model's units suffix automatically.

_SPECS: dict[str, dict[str, Any]] = {
    "SPARE_POOL_DATA": {
        "title": "Spare Pools",
        "noun": "spare pool",
        "key": "PoolName",
        "list_key": "PoolName",
        "fields": [
            ("Pool name", "PoolName", "str"),
            ("Requisition order? (RSO)", "RSO", "bool"),
            ("Initial stock", "Stock", "int"),
            ("New spares arrive every", "ArriveEvery", "float"),
            # Boolean strategy toggle, not a quantity: the underlying
            # column is read via typed.py's `_b()`, like its RSO/SLO
            # siblings.
            ("Emergency ordering (ESO)", "ESO", "bool"),
            ("Emergency shipping time", "EmerTime", "float"),
            ("Stock-level ordering (SLO)", "SLO", "bool"),
            ("Order level", "Level", "int"),
            ("Order quantity", "Quan", "int"),
            ("Order evaluation time", "SLOTime", "float"),
            # Spare-pool money fields, parsed and consumed by the
            # engine (typed.py's spare_pool_from_row). The
            # similarly-named fields on RESOURCE_DATA are a different
            # pool type.
            ("Spare purchase cost", "SpCost", "float"),
            ("Emergency shipping cost", "emerShipCost", "float"),
        ],
        "time_fields": {"ArriveEvery", "EmerTime", "SLOTime"},
        # rows that only apply while their toggle is on
        "dependents": {"RSO": ["ArriveEvery"], "ESO": ["EmerTime", "emerShipCost"],
                       "SLO": ["Level", "Quan", "SLOTime"]},
        "tips": {
            "RSO": "Routine Spare Ordering (RSO): reorder a spare on a "
                   "fixed schedule, before stock actually runs out.",
            "ESO": "Emergency Spare Ordering (ESO): rush-order a spare "
                   "(usually faster but costlier) when the pool runs out.",
            "EmerTime": "How long an emergency (ESO) spare takes to "
                        "arrive.",
            "SLO": "Stock-Level Ordering (SLO): automatically reorder "
                   "once the pool's stock drops to a threshold level.",
            "Level": "Stock level that triggers an SLO reorder.",
            "Quan": "How many spares an SLO reorder brings in.",
            "SLOTime": "How long an SLO reorder takes to arrive.",
            "SpCost": "Cost to purchase one spare (regular ordering).",
            "emerShipCost": "Extra shipping surcharge for an emergency "
                            "(ESO) spare.",
        },
    },
    "RESOURCE_DATA": {
        "title": "Resource Pools",
        "noun": "resource pool",
        "key": "PoolName",
        "list_key": "PoolName",
        "fields": [
            ("Resource pool name", "PoolName", "str"),
            ("Number in pool", "NumIn", "int"),
            ("Spare cost", "SpareCost", "float"),
            ("Fixed cost per use", "FixedPerUse", "float"),
            ("Cost per time unit", "PerTimeUnit", "float"),
            ("Phases", "Phases", "int"),
        ],
        "time_fields": set(),
    },
    "TRIGGERS": {
        "title": "Triggers",
        "noun": "trigger",
        "key": "TriggerName",
        "list_key": "TriggerName",
        "fields": [
            ("Trigger name", "TriggerName", "str"),
            ("Frequency distribution", "Dist", "dist"),
            ("Offset before first firing", "InitUsed", "float"),
            ("Repeats (0 = fire once)", "Repeats", "int"),
        ],
        "time_fields": {"InitUsed"},
    },
    "PHASES": {
        "title": "Phases",
        "key": "PhaseName",
        "list_key": "PhaseName",
        "fields": [
            ("Phase name", "PhaseName", "str"),
            ("Part of mission", "Mission", "bool"),
            ("Length distribution", "Dist", "dist"),
        ],
        "time_fields": set(),
    },
}

_TIME_PARAM_HINT = "time"


def _row_bool(row: dict, col: str) -> bool:
    """DELEGATED to `core.fileio.typed.cell_truthy` (review E-13).

    This used to be its own allow-list, `in ("1", "T", "TRUE")`. The engine
    read the same cells through `typed._b`, which used a deny-list --
    so `"false"`, `"no"`, `"2"` and a bare `"F"` were False here and True
    there. Since this function seeds the checkbox state at the line below
    and the engine reads what the checkbox writes, that was a case of the
    GUI showing a flag off while the engine ran with it on."""
    from raptor.core.fileio.typed import cell_truthy
    return cell_truthy(row.get(col))


def _row_num(row: dict, col: str, default: float = 0.0) -> float:
    try:
        return float(row.get(col, default))
    except (TypeError, ValueError):
        return default


def plain_english_summary(section: str, row: dict, units: str) -> str | None:
    """One-sentence, human-readable summary of a pool row's current
    settings. The raw RSO/ESO/SLO field grid says "SLO: [x]  Level: 2
    Quan: 3" but doesn't read as a policy at a glance, the problem
    results.py's Summary tab solves for run statistics."""
    u = f" {units}" if units else ""
    if section == "SPARE_POOL_DATA":
        name = row.get("PoolName") or "This pool"
        stock = row.get("Stock", "0")
        parts = [f"{name} starts with {stock} spare(s) on hand."]
        if _row_bool(row, "RSO"):
            parts.append("New spares are routinely reordered every "
                         f"{_row_num(row, 'ArriveEvery'):g}{u}.")
        if _row_bool(row, "ESO"):
            parts.append("An emergency spare can be rushed in within "
                         f"{_row_num(row, 'EmerTime'):g}{u} if the pool "
                         "runs dry.")
        if _row_bool(row, "SLO"):
            parts.append("Stock is automatically reordered once it "
                         f"drops to {row.get('Level', '0')}, bringing "
                         f"in {row.get('Quan', '0')} more within "
                         f"{_row_num(row, 'SLOTime'):g}{u}.")
        if not any(_row_bool(row, c) for c in ("RSO", "ESO", "SLO")):
            parts.append("No automatic reordering is configured — "
                         "spares must be added manually.")
        return " ".join(parts)
    if section == "RESOURCE_DATA":
        name = row.get("PoolName") or "This resource pool"
        n = row.get("NumIn", "0")
        # "per hours" reads wrong -- the model's own units string is
        # always the plural label used elsewhere as a spinbox suffix
        # ("hours", "days", ...), never the singular a "per X" phrase
        # needs.
        per_unit = units[:-1] if units.lower().endswith("s") else units
        return (f"{name} provides {n} resource(s): "
               f"{_row_num(row, 'SpareCost'):g} to acquire each, "
               f"{_row_num(row, 'FixedPerUse'):g} per use, plus "
               f"{_row_num(row, 'PerTimeUnit'):g} per "
               f"{per_unit or 'time unit'} while in use.")
    return None


class _EntityDialog(QDialog):
    """One master-detail dialog instance for a given section."""

    def __init__(self, win, section: str):
        spec = _SPECS[section]
        self.spec = spec
        self.section = section
        self.units = getattr(win, "_current_units", lambda: "")() or ""
        # Display-unit override: view/enter this dialog's own time
        # fields (ArriveEvery/EmerTime/SLOTime/InitUsed/trigger-frequency
        # params) in e.g. years too. `None` unless the model has an
        # override set -- native display in that case.
        self.display_unit = getattr(win._rbd, "display_unit", None)
        self._param_is_time: list[bool] = []
        super().__init__(win)
        self.setWindowTitle(spec["title"])
        self.setModal(True)

        self.rows = [dict(r) for r in
                     win._rbd.sections[section].rows]
        self.win = win

        outer = QVBoxLayout(self)
        outer.setSpacing(SPACING["md"])

        top = QHBoxLayout()
        top.setSpacing(SPACING["md"])
        outer.addLayout(top, 1)

        list_col = QVBoxLayout()
        list_col.setSpacing(SPACING["xs"])
        list_col.addWidget(QLabel(spec["title"]))
        self.listw = QListWidget()
        self.listw.setMinimumWidth(170)
        list_col.addWidget(self.listw, 1)
        top.addLayout(list_col, 1)
        list_col.setContentsMargins(0, 0, 0, 0)

        # Fields live in a titled card (matches the Inspector's grouped
        # sections) inside a scroll area so a long spec (Spare Pools has
        # 10 fields) never gets clipped by a modest dialog size.
        card = QGroupBox(f"{spec['title']} — Details")
        self.form = QFormLayout(card)
        # Left-aligned labels in one fixed-width column: right-aligned labels made the first row ("Trigger name")
        # look indented next to the distribution editor's own, left-aligned rows.
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.form.setHorizontalSpacing(SPACING["md"])
        self.form.setVerticalSpacing(SPACING["xs"])
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(card)
        top.addWidget(scroll, 3)

        self._editors: dict[str, Any] = {}
        self._param_editors: list[QDoubleSpinBox] = []

        # One bottom action bar (Raptor 7 runs Help/Cancel/Delete/
        # Apply/OK across the bottom of these dialogs, not stacked in a
        # side column) -- entity list actions on the left, dialog actions
        # on the right.
        bottom = QHBoxLayout()
        bottom.setSpacing(SPACING["sm"])
        add_b = QPushButton("Add")
        del_b = QPushButton("Delete")
        apply_b = QPushButton("Apply")
        close_b = QPushButton("Close")
        bottom.addWidget(add_b)
        bottom.addWidget(del_b)
        bottom.addStretch(1)
        bottom.addWidget(apply_b)
        bottom.addWidget(close_b)
        outer.addLayout(bottom)

        self._dirty = False
        #: Rows added in this dialog session but not yet Applied
        #: (ids of the buffered dicts).
        self._pending_add: set[int] = set()
        #: Original key values of EXISTING rows deleted this session but
        #: not yet Applied.
        self._pending_delete: set[str] = set()
        #: id(buffer row) -> the row's key value as of the last Apply /
        #: dialog open (name edits in the buffer must not break the
        #: match back to the live row).
        self._orig_keys: dict[int, str] = {}
        #: The buffer row the visible form is currently bound to.
        self._form_row: dict | None = None
        add_b.clicked.connect(self._add_entity)
        del_b.clicked.connect(self._delete_entity)
        apply_b.clicked.connect(self._apply_edits)
        close_b.clicked.connect(self.reject)

        self.listw.currentRowChanged.connect(
            lambda _r: self._build_form(self._current_row()))
        self._reload_list(0)

    # ------------------------------------------------------------------
    def _reload_list(self, select_row: int = 0):
        key = self.spec["list_key"]
        # setdefault, NOT overwrite: a buffer row the user renamed must
        # keep its ORIGINAL key so Apply can still find the live row.
        for r in self.rows:
            self._orig_keys.setdefault(id(r), r.get(key, ""))
        self.listw.blockSignals(True)
        self.listw.clear()
        for r in self.rows:
            self.listw.addItem(QListWidgetItem(r.get(key, "?")))
        self.listw.blockSignals(False)
        if self.rows:
            self.listw.setCurrentRow(
                min(max(select_row, 0), len(self.rows) - 1))
        else:
            self._build_form(None)

    def _current_row(self) -> dict | None:
        i = self.listw.currentRow()
        return self.rows[i] if 0 <= i < len(self.rows) else None

    # ------------------------------------------------------------------
    def _build_form(self, row: dict | None):
        """Populate the right-hand form from `row` (or disable it).
        Apply-gated buffering: the OUTGOING row's editors are flushed
        into the buffer first so switching rows never loses an edit,
        and nothing reaches the model until Apply."""
        self._flush_form()
        self._building = True
        try:
            self._build_form_inner(row)
        finally:
            self._building = False
        self._form_row = row

    def _flush_form(self):
        """Collect the visible editors into their buffer row (no model
        write -- see _apply_edits)."""
        if getattr(self, "_building", False):
            return
        row = getattr(self, "_form_row", None)
        if row is None or not hasattr(self, "_editors"):
            return
        self._collect(row)
        self._update_note()

    def _update_note(self):
        note = getattr(self, "_plain_note", None)
        row = getattr(self, "_form_row", None)
        if note is None or row is None:
            return
        sentence = plain_english_summary(self.section, row, self.units)
        if sentence:
            note.setText(sentence)

    def _on_editor_changed(self):
        """Buffer an editor edit: mark dirty, collect into the buffer
        row, refresh the plain-English note. The model is only touched
        on Apply."""
        if getattr(self, "_building", False):
            return
        self._dirty = True
        self._flush_form()

    def _build_form_inner(self, row: dict | None):
        while self.form.count():
            item = self.form.takeAt(0)
            w = req(item).widget()
            if w is not None:
                # setParent(None) FIRST: an unparented widget stops
                # painting at its old geometry immediately, whereas
                # deleteLater() alone leaves it a visible ghost until the
                # event loop processes the deferred delete -- visible as
                # the new row's label overlapping the old "(no entries)"
                # placeholder text right after Add.
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._editors.clear()
        self._param_editors = []
        self._param_is_time = []
        self._plain_note = None
        if row is None:
            self.form.addRow(QLabel("(no entries — press Add)"))
            return
        units = self.units
        sentence = plain_english_summary(self.section, row, units)
        if sentence:
            note = QLabel(sentence)
            note.setWordWrap(True)
            note.setStyleSheet(
                f"color: {active_colour()['muted']}; font-style: italic;")
            self.form.addRow(note)
            self._plain_note = note
        e: Any
        for label, col, kind in self.spec["fields"]:
            if kind == "str":
                e = QLineEdit(str(row.get(col, "")))
                if col == self.spec["key"]:
                    e.editingFinished.connect(lambda ed=e: self._guard_name(ed))
                e.editingFinished.connect(self._on_editor_changed)
                e.editingFinished.connect(
                    lambda e2=e: e2.deselect())
                self.form.addRow(label + ":", e)
                self._editors[col] = e
            elif kind == "int":
                e = QSpinBox()
                e.setRange(-1_000_000, 1_000_000)
                try:
                    e.setValue(int(float(row.get(col, 0))))
                except (TypeError, ValueError):
                    pass
                e.valueChanged.connect(self._on_editor_changed)
                self.form.addRow(label + ":", e)
                self._editors[col] = e
            elif kind == "bool":
                e = QCheckBox()
                e.setChecked(_row_bool(row, col))
                e.toggled.connect(self._on_editor_changed)
                self.form.addRow(label, e)
                self._editors[col] = e
            elif kind == "float":
                from raptor.gui.ui.units_conv import native_to_display
                e = QDoubleSpinBox()
                e.setRange(-1e12, 1e12)
                e.setDecimals(4)
                is_time = col in self.spec.get("time_fields", ())
                shown_units = (self.display_unit or units) if is_time \
                    else ""
                try:
                    raw = float(row.get(col, 0))
                    if is_time:
                        raw = native_to_display(raw, units,
                                                self.display_unit)
                    e.setValue(raw)
                except (TypeError, ValueError):
                    pass
                if shown_units:
                    e.setSuffix(f" {shown_units}")
                e.valueChanged.connect(self._on_editor_changed)
                self.form.addRow(label + ":", e)
                self._editors[col] = e
            elif kind == "dist":
                # Reusable standard distribution entry: the same combo +
                # named param fields + CheckValidInput validation +
                # Mean/Std Dev stats the Inspector's Failure/Repair/PM
                # forms use, via DistroEditor.
                from raptor.gui.ui.distro_editor import DistroEditor
                editor = DistroEditor(
                    int(float(row.get(col, 19) or 19)),
                    [_row_num(row, f"Param{i}", 0.0) for i in (1, 2, 3)],
                    use=label, units=units,
                    display_unit=self.display_unit, label_width=self._LABEL_W)
                editor.changed.connect(self._on_editor_changed)
                editor.validation_failed.connect(self._show_validation)
                self.form.addRow(editor)
                self._editors[col] = editor

        for r in range(self.form.rowCount()):
            item = self.form.itemAt(r, QFormLayout.ItemRole.LabelRole)
            label = item.widget() if item is not None else None
            if label is not None:
                label.setMinimumWidth(self._LABEL_W)
        self._wire_dependents()
        # Acronym fields (RSO/ESO/SLO, ...) get the plain-English
        # expansion as a tooltip on both the editor and its row label.
        tips = self.spec.get("tips", {})
        for col, tip in tips.items():
            tip_editor = self._editors.get(col)
            if tip_editor is None:
                continue
            tip_editor.setToolTip(tip)
            key_label = self.form.labelForField(tip_editor)
            if key_label is not None:
                key_label.setToolTip(tip)

    #: width of the label column, shared with the embedded distribution editor
    _LABEL_W = 225

    def _wire_dependents(self) -> None:
        """A strategy toggle (RSO, ESO, SLO) owns the rows that only mean something while it is on; with it off they
        are shown greyed (and not editable) instead of looking live."""
        for toggle, dependents in self.spec.get("dependents", {}).items():
            box = self._editors.get(toggle)
            if box is None:
                continue

            def sync(on: bool, deps=dependents) -> None:
                for col in deps:
                    editor = self._editors.get(col)
                    if editor is None:
                        continue
                    editor.setEnabled(bool(on))
                    label = self.form.labelForField(editor)
                    if label is not None:
                        label.setEnabled(bool(on))
            box.toggled.connect(sync)
            sync(box.isChecked())

    def _guard_name(self, editor) -> None:
        """Refuse a name another entry of this kind already has: blocks refer to pools and triggers by name, so two
        of one name could not both be used. (Raptor 7's dialogs offer to overwrite the existing entry instead.)"""
        key = self.spec["key"]
        row = getattr(self, "_form_row", None)
        if row is None:
            return
        new = editor.text().strip()
        if new and any(r is not row and r.get(key) == new for r in self.rows):
            editor.setText(str(row.get(key, "")))
            self._show_validation(f"A {self.spec.get('noun', 'entry')} named '{new}' already exists; "
                                  "names must be unique.")

    def wanted_height(self) -> int:
        """Height at which the details form shows without its vertical scroll bar (bounded by the screen)."""
        card = self.form.parentWidget()
        content = card.sizeHint().height() if card is not None else 400
        bottom = 90                                      # button bar, margins and the dialog frame
        screen = self.screen()
        cap = int(screen.availableGeometry().height() * 0.92) if screen is not None else 900
        return min(max(content + bottom, 460), cap)

    def _show_validation(self, msg: str) -> None:
        """Validation rejection from the DistroEditor: visible, not
        silent -- echo on the dialog's summary note slot."""
        note = getattr(self, "_plain_note", None)
        if note is None:
            note = QLabel()
            note.setWordWrap(True)
            self.form.addRow(note)
            self._plain_note = note
        note.setText(msg)
        note.setStyleSheet(
            f"color: {active_colour()['failed']}; font-style: italic;")

    def reject(self) -> None:
        """Close/Cancel just closes: the Apply-gated buffer is
        throwaway (the model was never touched), so there is no discard
        confirmation, matching the Raptor 7's own Cancel buttons."""
        self._dirty = False
        super().reject()

    def _collect(self, row: dict) -> dict:
        """Write editor values back into `row` (string-typed like the
        legacy columns). Time-dimensioned fields convert DISPLAY unit
        -> the model's own native unit here -- the row (and everything
        downstream: RowCellCmd, the saved file) only ever sees native
        values, matching every other field in this app."""
        from raptor.gui.ui.units_conv import display_to_native
        for _label, col, kind in self.spec["fields"]:
            w = self._editors.get(col)
            if w is None:
                continue
            if kind == "bool":
                # templates store flags as 1/0 -- match them so a
                # no-op Apply doesn't churn the value
                row[col] = "1" if w.isChecked() else "0"
            elif kind == "int":
                row[col] = str(w.value())
            elif kind == "float":
                v = w.value()
                if col in self.spec.get("time_fields", ()):
                    v = display_to_native(v, self.units,
                                          self.display_unit)
                row[col] = f"{v:.6f}"
            elif kind == "dist":
                # DistroEditor owns both the code and the Param1-3
                # values (validation included).
                distro, params = w.value()
                row[col] = str(distro)
                for i, p in enumerate(params, start=1):
                    row[f"Param{i}"] = f"{p:.6f}"
            else:
                row[col] = w.text()
        return row

    # ------------------------------------------------------------------
    def _apply_edits(self):
        """Push the whole buffered set (edits + adds + deletes) onto
        the undo stack as one macro. Nothing touches the model until
        then; because the buffer is throwaway, Discard needs no undo
        gymnastics."""
        if getattr(self, "_building", False):
            return
        self._flush_form()
        key_col = self.spec["key"]
        # Duplicate-name confirmation (original behaviour, IIntract's own
        # duplicate pool/resource/trigger prompt). Two buffer rows
        # sharing a key -- e.g. renaming one pool onto another's name --
        # is legal but must not happen silently.
        names = [b.get(key_col, "") for b in self.rows]
        dupes = sorted({n for n in names if n and names.count(n) > 1})
        if dupes:
            from raptor.gui.ui.dialogs import question_box
            if not question_box(
                    self, "Duplicate name",
                    "These names appear on more than one entry: "
                    + ", ".join(dupes)
                    + ".\n\nApply anyway?",
                    yes_text="Apply anyway", no_text="Cancel"):
                return
        v = self.win._diagram_dock.view
        sec = self.win._rbd.sections[self.section]
        n_cmds = 0
        v._stack.beginMacro(f"Edit {self.spec['title']}")
        try:
            # deletes first, so a delete+re-add of the same name lands
            # in a legal order on the live section
            for key_val in list(self._pending_delete):
                live = next((r for r in sec.rows
                             if r.get(key_col) == key_val), None)
                if live is None:
                    continue
                idx = sec.rows.index(live)
                v._stack.push(TableRowCmd(v, self.section, live, idx,
                                          add=False))
                n_cmds += 1
            for buffer in self.rows:
                if id(buffer) in self._pending_add:
                    v._stack.push(TableRowCmd(v, self.section, buffer,
                                              len(sec.rows), add=True))
                    n_cmds += 1
                    continue
                live = next((r for r in sec.rows if r.get(key_col)
                             == self._orig_keys.get(id(buffer))), None)
                if live is None:
                    continue
                idx = sec.rows.index(live)
                edits = [(col, live.get(col, ""), buffer.get(col, ""))
                         for _label, col, _kind in self.spec["fields"]
                         if live.get(col, "") != buffer.get(col, "")]
                # DistroEditor's validated Param1-3 live on the buffer
                # row too (collected with everything else).
                edits += [(f"Param{i}", live.get(f"Param{i}", ""),
                           buffer.get(f"Param{i}", ""))
                          for i in (1, 2, 3)
                          if live.get(f"Param{i}", "") !=
                          buffer.get(f"Param{i}", "")]
                for col, old, new in edits:
                    v._stack.push(RowCellCmd(v, self.section, idx, col,
                                             old, new))
                    n_cmds += 1
        finally:
            v._stack.endMacro()
        if n_cmds:
            self.win._mark_dirty()
        self._dirty = False
        self._pending_add.clear()
        self._pending_delete.clear()
        # Re-sync the buffer from the now-live rows (fresh dict identity)
        self.rows = [dict(r) for r in sec.rows]
        self._orig_keys = {id(r): r.get(key_col, "") for r in self.rows}
        keep = self.listw.currentRow()
        self._reload_list(keep)
        self._form_row = self._current_row()

    # ------------------------------------------------------------------
    def _add_entity(self):
        template = dict(DataTablesDock._KEYED_TEMPLATES[self.section])
        key_col = self.spec["key"]
        base_name = template[key_col]
        n = 1
        existing = {r.get(key_col, "") for r in self.rows}
        new_name = base_name
        while new_name in existing:
            n += 1
            new_name = f"{base_name}_{n}"
        template[key_col] = new_name
        self._flush_form()
        self.rows.append(template)
        self._pending_add.add(id(template))
        self._orig_keys[id(template)] = new_name
        self._dirty = True
        self._reload_list(len(self.rows) - 1)

    def _delete_entity(self):
        i = self.listw.currentRow()
        if not (0 <= i < len(self.rows)):
            return
        buffer = self.rows[i]
        was_pending_add = id(buffer) in self._pending_add
        self._flush_form()
        self._pending_add.discard(id(buffer))
        if not was_pending_add:
            orig = self._orig_keys.get(id(buffer))
            if orig is not None:
                self._pending_delete.add(orig)
        self.rows.pop(i)
        self._dirty = True
        self._reload_list(max(0, i - 1))


def open_pool_dialog(win, section: str) -> None:
    """Entry point used by the ribbon's Pools/Triggers buttons and by
    the Inspector's contextual "Manage…" buttons."""
    if getattr(win, "_rbd", None) is None:
        return
    dlg = _EntityDialog(win, section)
    dlg.resize(900, dlg.wanted_height())
    exec_dialog(dlg)

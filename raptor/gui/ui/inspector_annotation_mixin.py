"""Inspector annotation tab: anchors, colours and fonts.

Expects on `self`: the Inspector's tab layouts and the selected annotation."""
from __future__ import annotations

from raptor.gui.ui.inspector_common import (
    _ANNO_KIND_LABELS,
)

from raptor.gui.ui.qtcompat import req

from typing import TYPE_CHECKING, Any, cast
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QScrollArea, QLabel, QFormLayout, QHBoxLayout, QLineEdit,
    QSpinBox, QDoubleSpinBox, QComboBox, QPushButton,
    QToolButton, QSizePolicy,
)
from raptor.core.annotations import ANNO_DEFAULT_LOCATION

# The distribution rules are `raptor.core.distro_rules`'s, not this
# module's (review D-04) -- they are pure model rules and belong in a
# Qt-free home. Imported here because this widget still uses both.
from raptor.gui.resources.design_tokens import (
    SPACING, active_colour,
)

#: Raptor 7's `UnitsCombo` items, in its own order
#: The box was
#: editable, so these are presets rather than a closed list: Raptor 7
#: reads `unitsBox.Text()` and every label is built from it.

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _Base
else:
    _Base = object


class InspectorAnnotationMixin(_Base):
    """Inspector annotation tab: anchors, colours and fonts."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _current_block_id: Any
        _current_node_id: Any
        _current_target: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    def show_annotation(self, item):
        """Colours & Font for one canvas annotation (rect/ellipse/text
        overlay shape), in the Inspector rather than the separate modal
        "Annotation Properties" dialog it replaced. Each control commits
        its own undo step via `annotation_prop_changed`, which the
        receiver (`DiagramView.set_annotation_props`) turns into one
        `AnnoEditCmd` snapshotting the full before/after state."""
        from raptor.gui.ui.diagram import ANNO_DEFAULTS
        self._begin_populate()
        self._configure_tabs("annotation")
        aid = getattr(item, "_anno_id", None)
        kind = getattr(item, "kind", "annotation")
        # The ribbon's tool buttons say "Rectangle"/"Ellipse"/"Text"/
        # "Arrow"; `kind.title()` said "Rect", so the two disagreed.
        self._header.setText(
            f"Annotation: {_ANNO_KIND_LABELS.get(kind, kind.title())}"
            f"  (ID {aid})")
        # Record what the Inspector is showing. Without this it kept the
        # last element's ("block", id), so a prop commit (e.g. a line
        # width change) fired model_changed -> _refresh_inspector and the
        # panel jumped back to that block instead of staying on the
        # selected annotation.
        self._current_target = ("annotation", aid)
        self._current_block_id = None
        self._current_node_id = None
        for i in range(self._tabs.count()):
            self._clear_tab(cast(QScrollArea, self._tabs.widget(i)))
        w = self._scroll_general.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_general)

        props = dict(item.props)

        def emit_changes(changes: dict):
            changed = {k: v for k, v in changes.items()
                      if props.get(k) != v}
            if not changed:
                return
            props.update(changed)
            self.annotation_prop_changed.emit(aid, changed)

        _gb, fl = self._group("Colours", layout)
        # An arrow has no separate border/fill split -- the "border" IS
        # the whole visible annotation, so this reuses the same prop
        # (border_colour/border_width) under a label that matches what
        # the user is actually looking at.
        line_label = "Line" if kind == "arrow" else "Border"
        self._colour_row(fl, line_label, props.get(
            "border_colour", ANNO_DEFAULTS["border_colour"]),
            lambda v: emit_changes({"border_colour": v}),
            allow_none=(kind != "arrow"))
        width_spin = QDoubleSpinBox()
        # 0 == no border (the old separate "draw border"
        # checkbox is gone), so 0 must stay reachable in this spinbox.
        # (Arrow never stores 0 by default -- see _begin_annotation_
        # arrow -- but the field stays usable if the user wants it.)
        width_spin.setRange(0.0, 20.0)
        width_spin.setSingleStep(0.5)
        width_spin.setSuffix(" px")
        width_spin.setValue(float(props.get(
            "border_width", ANNO_DEFAULTS["border_width"])))
        width_spin.valueChanged.connect(
            lambda v: emit_changes({"border_width": v}))
        fl.addRow(f"{line_label} width:", width_spin)

        # Line style dropdown -- one shared prop/control for rect,
        # ellipse and arrow alike. Text's border never had a dash concept
        # and doesn't get the control (the prop still round-trips
        # harmlessly if present).
        if kind in ("rect", "ellipse", "arrow"):
            from raptor.gui.ui.diagram import ANNO_LINE_STYLES
            style_labels = {"solid": "Solid", "dash": "Dash",
                            "dot": "Dot", "dash-dot": "Dash-Dot",
                            "dash-dot-dot": "Dash-Dot-Dot"}
            style_combo = QComboBox()
            for key in ANNO_LINE_STYLES:
                style_combo.addItem(style_labels.get(key, key.title()),
                                    key)
            cur_style = props.get("line_style",
                                  ANNO_DEFAULTS["line_style"])
            idx = style_combo.findData(cur_style)
            style_combo.setCurrentIndex(idx if idx >= 0 else 0)
            style_combo.currentIndexChanged.connect(
                lambda i: emit_changes(
                    {"line_style": style_combo.itemData(i)}))
            fl.addRow("Line style:", style_combo)

        if kind != "arrow":
            self._colour_row(fl, "Fill", props.get("fill_colour"),
                             lambda v: emit_changes({"fill_colour": v}),
                             allow_none=True)
        if kind == "text":
            self._colour_row(fl, "Text", props.get(
                "text_colour", ANNO_DEFAULTS["text_colour"]),
                lambda v: emit_changes({"text_colour": v}))

        if kind != "arrow":
            _gb, fl = self._group("Font", layout)
            self._font_row(fl, props, emit_changes)

        self._anchor_group(layout, aid, kind)
        self._restore_tab()

    def _anchor_group(self, layout, aid, kind: str) -> None:
        """Anchor: what the annotation is attached to. Anchored, it moves
        with its element (dragging it, Align, Auto Layout); "Not anchored"
        removes the link and leaves it where it is. Each change is one
        undoable edit (`annotation_prop_changed` with an `anchor`)."""
        src = self.anchor_source
        if src is None or aid is None:
            return
        entry = src.annotation_entry(int(aid)) or {}
        anchor = entry.get("anchor") if isinstance(entry.get("anchor"), dict) else None
        targets = src.anchor_targets(exclude=int(aid))
        _gb, fl = self._group("Anchor", layout)

        def combo_for(current_ref, current_name, none_label):
            box = QComboBox()
            box.addItem(none_label, None)
            for label, ref in targets:
                box.addItem(label, ref)
            idx = box.findData(current_ref) if current_ref else -1
            if idx < 0 and current_name:
                idx = next((i for i in range(1, box.count())
                            if box.itemText(i).split(": ", 1)[-1] == current_name), -1)
            box.setCurrentIndex(max(idx, 0))
            return box

        def name_of(box) -> str:
            return box.currentText().split(": ", 1)[-1]

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        break_link = QPushButton("Break link")
        break_link.setToolTip("Stop following the target; the annotation "
                              "stays where it is")
        break_link.setEnabled(anchor is not None)
        break_link.clicked.connect(
            lambda _c=False: self.annotation_prop_changed.emit(
                aid, {"anchor": None}))
        buttons.addWidget(break_link)

        if kind == "arrow":
            tail = combo_for(anchor and anchor.get("from_ref"), anchor and anchor.get("from"),
                             "Free")
            head = combo_for(anchor and anchor.get("to_ref"), anchor and anchor.get("to"),
                             "Free")

            def commit_ends(_i=None):
                new: dict[str, Any] = {}
                for end, box in (("from", tail), ("to", head)):
                    if box.currentData() is not None:
                        new[end] = name_of(box)
                        new[f"{end}_ref"] = box.currentData()
                if not new:
                    if anchor is not None:
                        self.annotation_prop_changed.emit(aid, {"anchor": None})
                    return
                self.annotation_prop_changed.emit(aid, {"anchor": new})

            def end_row(box, end: str):
                pick = QPushButton("Pick…")
                pick.setToolTip(f"Click, then click an element or annotation "
                                f"to attach the arrow's {end} end to it (Esc cancels)")
                pick.setEnabled(hasattr(src, "start_anchor_pick"))
                pick.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                pick.clicked.connect(
                    lambda _c=False: self.annotation_arrow_pick_requested.emit(int(aid), end))
                remove = QPushButton("Remove")
                remove.setToolTip(f"Detach the {end} end: it stays where it is and "
                                  "no longer follows anything")
                remove.setEnabled(box.currentData() is not None)
                remove.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                remove.clicked.connect(lambda _c=False: box.setCurrentIndex(0))
                row = QWidget()
                lay = QHBoxLayout(row)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(4)
                box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                box.setMinimumContentsLength(8)
                box.setSizeAdjustPolicy(
                    QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                lay.addWidget(box, 1)
                lay.addWidget(pick)
                lay.addWidget(remove)
                return row

            tail.currentIndexChanged.connect(commit_ends)
            head.currentIndexChanged.connect(commit_ends)
            fl.addRow("From:", end_row(tail, "from"))
            fl.addRow("To:", end_row(head, "to"))
            note = ("Each end can be attached to an element or another "
                    "annotation: it then follows it and stops at its edge. "
                    "Pick… chooses the target by clicking it; Remove frees "
                    "that end where it is.")
        else:
            target = combo_for(anchor and anchor.get("ref"),
                               anchor and anchor.get("target"), "Not anchored")
            # Editable, so the list doubles as a search field: type part of
            # an element name or annotation label and Qt's completer narrows
            # it. `NoInsert` stops a half-typed name becoming an option.
            target.setEditable(True)
            target.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            # The Inspector is ~380 px wide; an unconstrained combo plus a
            # "Pick on canvas…" button overflowed it and clipped both. Let
            # the combo expand into whatever is left and cap its preferred
            # width instead of sizing to the longest element name.
            target.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            target.setMinimumContentsLength(8)
            target.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Fixed)
            if target.completer() is not None:
                target.completer().setFilterMode(
                    Qt.MatchFlag.MatchContains)
                target.completer().setCaseSensitivity(
                    Qt.CaseSensitivity.CaseInsensitive)
            pick = QPushButton("Pick on canvas…")
            pick.setToolTip("Click, then click an element or another "
                            "annotation to anchor this annotation to it "
                            "(Esc cancels)")
            pick.setEnabled(hasattr(src, "start_anchor_pick"))
            pick.setSizePolicy(QSizePolicy.Policy.Fixed,
                               QSizePolicy.Policy.Fixed)
            pick.clicked.connect(
                lambda _checked=False: self.annotation_anchor_pick_requested
                .emit(int(aid)))
            target_row = QWidget()
            target_layout = QHBoxLayout(target_row)
            target_layout.setContentsMargins(0, 0, 0, 0)
            target_layout.setSpacing(4)
            target_layout.addWidget(target, 1)
            target_layout.addWidget(pick)

            location = QComboBox()
            for key, label in self._ANCHOR_LOCATIONS:
                location.addItem(label, key)
            location.setCurrentIndex(max(
                location.findData((anchor or {}).get(
                    "location", ANNO_DEFAULT_LOCATION)), 0))
            gap = QDoubleSpinBox()
            gap.setRange(0.0, 500.0)
            gap.setSuffix(" px")
            gap.setValue(float((anchor or {}).get("gap", 12.0)))
            location.setEnabled(anchor is not None)
            gap.setEnabled(anchor is not None)

            def sync_gap(_v=None):
                gap.setEnabled(anchor is not None
                               and location.currentData() != "center")

            sync_gap()

            def commit(_v=None):
                if target.currentData() is None:
                    if anchor is not None:
                        self.annotation_prop_changed.emit(aid, {"anchor": None})
                    return
                self.annotation_prop_changed.emit(aid, {"anchor": {
                    "target": name_of(target), "ref": target.currentData(),
                    "location": location.currentData(),
                    "gap": float(gap.value())}})

            target.currentIndexChanged.connect(commit)
            location.currentIndexChanged.connect(sync_gap)
            location.currentIndexChanged.connect(commit)
            gap.editingFinished.connect(commit)
            fl.addRow("Anchored to:", target_row)
            fl.addRow("Location:", location)
            fl.addRow("Gap:", gap)
            offset = bool(anchor and (anchor.get("dx") or anchor.get("dy")))
            reset = QPushButton("Reset position")
            reset.setToolTip("Put the annotation back at its anchored "
                             "location (undoes where you dragged it)")
            reset.setEnabled(offset)

            def reset_position():
                self.annotation_prop_changed.emit(aid, {"anchor": {
                    k: v for k, v in (anchor or {}).items()
                    if k not in ("dx", "dy")}})

            reset.clicked.connect(lambda _c=False: reset_position())
            buttons.addWidget(reset)
            note = ("Anchored, it moves with its target. Centre ignores the "
                    "gap. Drag the annotation to place it where you like; "
                    "it keeps following its target from there. Reset "
                    "position returns it to its Location, Break link frees "
                    "it. Use Pick on canvas… to choose the target by "
                    "clicking it.")
        buttons.addStretch(1)
        fl.addRow(buttons)
        hint = QLabel(note)
        hint.setWordWrap(True)
        hint.setObjectName("SectionNote")
        fl.addRow(hint)

    def _colour_row(self, form: QFormLayout, label: str, current,
                    on_commit, allow_none: bool = False):
        """Swatch + paste-able hex field + (when `allow_none`) an
        explicit "no colour" toggle.

        `allow_none` is what makes a colour *removable*, and it is the
        point of the third control: until now the only ways to get
        "none" were to clear the hex field (undiscoverable) or to pick a
        fully transparent swatch in the colour dialog (also
        undiscoverable). A visible toggle makes the state obvious and
        one click to set, and it round-trips the SAME stored value --
        `None` -- that `_anno_pen_brush`/`_AnnoText` treat as "don't
        draw this"."""
        from PySide6.QtGui import QColor
        state = {"current": current}
        btn = QPushButton()
        btn.setFixedWidth(28)
        btn.setToolTip(f"Pick a {label.lower()} colour")
        # Paste-able hex field: without it the swatch button would be the
        # only way to change the colour, and being a QPushButton its hex
        # text couldn't be clicked into or pasted over. Typing/pasting
        # here updates the swatch directly, without the QColorDialog.
        hex_edit = QLineEdit()
        hex_edit.setFixedWidth(80)
        hex_edit.setPlaceholderText("#RRGGBB")
        none_btn = None
        if allow_none:
            none_btn = QToolButton()
            none_btn.setText("None")
            none_btn.setCheckable(True)
            none_btn.setFixedWidth(46)
            none_btn.setToolTip(
                f"Draw no {label.lower()} at all (stores null)")

        def restyle():
            c = state["current"]
            # A border that contrasts with the swatch itself, so it stays
            # visible even when the colour matches the panel behind it
            # (a white swatch on a white Inspector otherwise disappears).
            if c:
                col = QColor(c)
                lum = (0.299 * col.red() + 0.587 * col.green()
                       + 0.114 * col.blue())
                border = "#000000" if lum > 140 else "#FFFFFF"
            else:
                border = active_colour()["muted"]
            btn.setStyleSheet(
                f"background: {c or 'transparent'}; "
                f"border: 2px solid {border}; border-radius: 3px;")
            if hex_edit.text() != (c or ""):
                hex_edit.setText(c or "")
            if none_btn is not None:
                was = none_btn.blockSignals(True)
                none_btn.setChecked(c is None)
                none_btn.blockSignals(was)
        restyle()

        # The last real colour committed, so toggling "None" off can put
        # it back instead of leaving the row blank. Kept here rather
        # than captured at construction: the user may pick a colour, hit
        # None, pick another, hit None again, and expect the LATHER one
        # back.
        memory: dict = {"last": current}

        def commit(new_val, *, from_hex: bool):
            state["current"] = new_val
            if new_val:
                memory["last"] = new_val
            restyle()
            if not from_hex:
                hex_edit.setText(new_val or "")
            on_commit(new_val)

        if none_btn is not None:
            def _none_toggled(on: bool):
                commit(None if on else (memory["last"] or "#5D7080"),
                       from_hex=True)
            none_btn.toggled.connect(_none_toggled)

        def pick():
            from PySide6.QtWidgets import QColorDialog
            base = QColor(state["current"] or "#FFFFFF")
            if allow_none:
                c = QColorDialog.getColor(base, self, f"{label} colour",
                                          QColorDialog.ColorDialogOption.ShowAlphaChannel)
            else:
                c = QColorDialog.getColor(base, self, f"{label} colour")
            if not c.isValid():
                return
            new_val = None if (allow_none and c.alpha() == 0) else c.name()
            commit(new_val, from_hex=False)
        btn.clicked.connect(pick)

        def hex_committed():
            text = hex_edit.text().strip()
            if not text:
                if allow_none:
                    commit(None, from_hex=True)
                else:
                    hex_edit.setText(state["current"] or "")
                return
            if not text.startswith("#"):
                text = "#" + text
            c = QColor(text)
            if not c.isValid():
                hex_edit.setText(state["current"] or "")   # reject, restore
                return
            commit(c.name(), from_hex=True)
        hex_edit.editingFinished.connect(hex_committed)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        # The three controls are sized to their content and packed to
        # the left. Previously the row widget carried no stretch, so
        # QFormLayout's AllNonFixedFieldsGrow spread the swatch and the
        # hex field apart with a visible gap between them, while the
        # spin/combo rows above stretched to the full column width.
        row.addWidget(btn)
        row.addSpacing(SPACING["xs"])
        row.addWidget(hex_edit)
        if none_btn is not None:
            row.addSpacing(SPACING["xs"])
            row.addWidget(none_btn)
        row.addStretch(1)
        row_w = QWidget()
        row_w.setLayout(row)
        form.addRow(label + ":", row_w)
        return none_btn

    def _font_row(self, form: QFormLayout, props: dict, emit_changes):
        """Font controls: a "Font..." button for family, plus the two
        adjustments people actually make most (bold/italic and size) as
        direct controls.

        Bold, italic and size were previously reachable ONLY through a
        modal `QFontDialog`, which is three fields and an OK/Cancel to
        get at a single toggle. Each control here commits on its own so
        it is one undo step per change, and the button still commits
        family/size/bold/italic together (matching the old single-button
        behaviour) so picking a family in one dialog is still one step.
        """
        btn = QPushButton()

        def label_text() -> str:
            fam = props.get("font_family", "sans-serif")
            size = props.get("font_size", 10)
            extra = "".join((
                " Bold" if props.get("font_bold") else "",
                " Italic" if props.get("font_italic") else ""))
            return f"{fam}, {size}pt{extra}"
        btn.setText(label_text())
        btn.setToolTip("Pick a font family (and size, bold, italic)")

        def pick():
            from PySide6.QtGui import QFont
            from PySide6.QtWidgets import QFontDialog
            cur = QFont(props.get("font_family", "sans-serif"),
                       int(props.get("font_size", 10)))
            cur.setBold(bool(props.get("font_bold")))
            cur.setItalic(bool(props.get("font_italic")))
            ok, f = QFontDialog.getFont(cur, self, "Font")
            if not ok:
                return
            emit_changes({
                "font_family": f.family(), "font_size": f.pointSize(),
                "font_bold": f.bold(), "font_italic": f.italic()})
            btn.setText(label_text())
            _sync()
        btn.clicked.connect(pick)

        bold = QToolButton()
        bold.setText("B")
        bold.setCheckable(True)
        bold.setFixedWidth(30)
        bold.setToolTip("Bold")
        italic = QToolButton()
        italic.setText("I")
        italic.setCheckable(True)
        italic.setFixedWidth(30)
        italic.setToolTip("Italic")
        size = QSpinBox()
        size.setRange(4, 96)
        size.setSuffix(" pt")
        size.setFixedWidth(64)
        size.setToolTip("Text size in points")

        def _sync():
            bold.blockSignals(True)
            italic.blockSignals(True)
            size.blockSignals(True)
            bold.setChecked(bool(props.get("font_bold")))
            italic.setChecked(bool(props.get("font_italic")))
            size.setValue(int(props.get("font_size", 10)))
            bold.blockSignals(False)
            italic.blockSignals(False)
            size.blockSignals(False)

        def _commit_and_relabel(changes: dict):
            emit_changes(changes)
            btn.setText(label_text())

        bold.toggled.connect(
            lambda on: _commit_and_relabel({"font_bold": on}))
        italic.toggled.connect(
            lambda on: _commit_and_relabel({"font_italic": on}))
        size.valueChanged.connect(
            lambda v: _commit_and_relabel({"font_size": v}))
        _sync()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(btn)
        row.addSpacing(SPACING["xs"])
        row.addWidget(bold)
        row.addWidget(italic)
        row.addSpacing(SPACING["xs"])
        row.addWidget(size)
        row.addStretch(1)
        row_w = QWidget()
        row_w.setLayout(row)
        form.addRow("Font:", row_w)

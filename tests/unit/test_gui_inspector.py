"""Headless GUI tests for the Inspector's field naming and validation.

Runs offscreen (`QT_QPA_PLATFORM=offscreen`) with PySide6 and no modal
dialogs; skipped entirely when PySide6 is not installed.

Ground truth for the assertions is Raptor 7
(ConvertToString, GetParameters, CheckValidInput, the
Input-Tables simStart/GDType/stream display code ) and
`raptor/core/rng.py`'s `Distribution` enum, which matches the real `.rbd`
column codes.
"""
from __future__ import annotations

import pytest

PySide6 = pytest.importorskip("PySide6")

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QLineEdit

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.resources.design_tokens import ThemeManager
from raptor.core.distro_rules import DISTRO_NAMES
from raptor.gui.ui.inspector import (
    validate_distro_params,
    InspectorDock,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    # ThemeManager is a process-wide singleton; leave it light for other tests.
    ThemeManager.instance().set_dark(False)


def _labels_of(group: QGroupBox) -> list[str]:
    return [w.text() for w in group.findChildren(QLabel) if w.text()]


def _group(scroll, title: str) -> QGroupBox | None:
    for gb in scroll.widget().findChildren(QGroupBox):
        if gb.title() == title:
            return gb
    return None


def _edits_of(group: QGroupBox) -> list[QLineEdit]:
    # skip QComboBox's internal editor line-edit (Type/Stream combos)
    return [e for e in group.findChildren(QLineEdit)
            if e.objectName() != "qt_spinbox_lineedit"]


def test_distro_names_match_legacy_source():
    assert DISTRO_NAMES[4] == "Exponential"
    assert DISTRO_NAMES[10] == "Uniform Real"
    assert DISTRO_NAMES[15] == "Weibull"
    assert DISTRO_NAMES[17] == "Point Estimate"
    assert DISTRO_NAMES[19] == "Fixed"
    assert DISTRO_NAMES[21] == "Extreme Value"


def test_failure_params_labelled_per_distribution(qapp):
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/PreventMxDefer/input.rbd"))
    dock.set_rbd(rbd)
    # Block 5: Weibull(15) failure, Lognormal(7) repair.
    from raptor.core.fileio.typed import block_from_row

    block = next(b for row in rbd.sections["BLOCK_DATA"].rows
                 if (b := block_from_row(row)).id == 5)
    dock._populate_block(block)
    QApplication.processEvents()

    fail = _group(dock._scroll_failure, "Failure Distribution")
    labels = _labels_of(fail)
    # Type is now an editable combo (not a static label)
    from PySide6.QtWidgets import QComboBox
    type_combo = fail.findChildren(QComboBox)[0]
    assert type_combo.currentText() == "Weibull"
    # Each parameter is its own form row (packed side-by-side it clipped
    # in the narrow Inspector dock), prefixed with the distribution's use
    # ("Failure"/"Repair"/"Pre-LDT"/...) so Pre-LDT and Post-LDT stay
    # distinguishable within the same "Logistical Constraints" group.
    # Time-dimension labels carry the model's units.
    assert "Failure Shape:" in labels
    assert "Failure Scale (hours):" in labels \
        and "Failure Location (hours):" in labels
    edits = _edits_of(fail)
    assert len(edits) == 3
    assert edits[0].toolTip() == "Shape"

    repair = _group(dock._scroll_repair, "Repair Distribution")
    rcombo = repair.findChildren(QComboBox)[0]
    assert rcombo.currentText() == "Lognormal"
    rlabels = _labels_of(repair)
    assert "Repair Mean (hours):" in rlabels \
        and "Repair Standard Dev (hours):" in rlabels


def test_empirical_repair_has_no_param_boxes(qapp):
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/ComplexExample/input.rbd"))
    dock.set_rbd(rbd)
    from raptor.core.fileio.typed import block_from_row

    block = next(b for row in rbd.sections["BLOCK_DATA"].rows
                 if (b := block_from_row(row)).id == 1)
    dock._populate_block(block)
    QApplication.processEvents()
    repair = _group(dock._scroll_repair, "Repair Distribution")
    # distro 18 (None) and 16 (Empirical) show no parameter boxes at all,
    # matching GetParameters' empty tags -> hidden boxes in the source.
    assert _edits_of(repair) == []


def test_invalid_param_reverts_and_signals(qapp):
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/PreventMxDefer/input.rbd"))
    dock.set_rbd(rbd)
    from raptor.core.fileio.typed import block_from_row

    block = next(b for row in rbd.sections["BLOCK_DATA"].rows
                 if (b := block_from_row(row)).id == 5)
    dock._populate_block(block)
    QApplication.processEvents()
    fail = _group(dock._scroll_failure, "Failure Distribution")
    shape_edit = _edits_of(fail)[0]

    errors: list[str] = []
    dock.validation_failed.connect(errors.append)
    # MainWindow's role: apply committed values to the live model.
    dock.block_fail_params_changed.connect(
        lambda bid, vals: setattr(block, "fail_values", vals))
    shape_edit.setText("-0.5")
    shape_edit.editingFinished.emit()
    assert shape_edit.text() == "1.2"  # reverted to last committed value
    assert errors and "Shape" in errors[-1]

    shape_edit.setText("2.0")
    shape_edit.editingFinished.emit()
    assert shape_edit.text() == "2"
    assert block.fail_values[0] == 2.0  # committed to the live model object


def testvalidate_distro_params_rules():
    # Triangular ordering (CheckValidInput's "Min < Mode < Max" branch).
    assert validate_distro_params(14, [3.0, 2.0, 5.0], "Failure")
    assert validate_distro_params(14, [1.0, 2.0, 5.0], "Failure") is None
    # Uniform Real: both-zero forbidden.
    assert validate_distro_params(10, [0.0, 0.0], "Failure")
    assert validate_distro_params(10, [0.0, 1.0], "Failure") is None
    # Exponential Mean >= 0.000001.
    assert validate_distro_params(4, [-1.0, 0.0], "Repair")
    # Chi-square shape must be a positive integer.
    assert validate_distro_params(2, [2.5, 0.0, 0.0], "Failure")
    assert validate_distro_params(2, [3.0, 0.0, 0.0], "Failure") is None
    # Point Estimate P Success in (0, 1].
    assert validate_distro_params(17, [1.5], "Failure")
    assert validate_distro_params(17, [0.5], "Failure") is None


@pytest.mark.slow
def test_theme_switch_rethemes_canvas(qapp):
    # NB: marked slow — in the FULL suite this toggle re-polishes every
    # widget accumulated by prior tests (Qt scales setStyleSheet with
    # alive-widget count even after deleteLater flush attempts), taking
    # ~45 s here vs 0.06 s standalone. Runs via -m slow.
    from raptor.gui.ui.diagram import DiagramView

    view = DiagramView()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/UniformProbe/input.rbd"))
    view.load_rbd(rbd)
    QApplication.processEvents()

    canvas_before = view.backgroundBrush().color().name()
    block_item = next(iter(view._block_items.values()))
    label_before = block_item._label.defaultTextColor().name()

    ThemeManager.instance().set_dark(True)
    QApplication.processEvents()

    assert view.backgroundBrush().color().name() != canvas_before
    assert block_item._label.defaultTextColor().name() != label_before
    assert view.backgroundBrush().color().name() == "#111927"
    assert block_item._label.defaultTextColor().name() == "#e1e8f0"
    ThemeManager.instance().set_dark(False)
    QApplication.processEvents()
    assert view.backgroundBrush().color().name() == canvas_before


def test_node_tab_shows_true_n_column(qapp):
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/UniformProbe/input.rbd"))
    dock.set_rbd(rbd)
    from raptor.core.fileio.typed import node_from_row

    node = next(n for row in rbd.sections["NODE_DATA"].rows
                if (n := node_from_row(row)).id == 2)
    dock._populate_node(node)
    QApplication.processEvents()
    group = _group(dock._scroll_general, "K-out-of-N Logic")
    rows = _labels_of(group)
    # The end node's raw row is `k=-1 n=1 Out=0`: n shows the raw `n`
    # column (1), not `Out` (0).
    i = rows.index("Total paths (n):")
    assert rows[i + 1] == "1"


def test_node_standby_tab_present_editable_and_gated(qapp):
    """The Node Properties Standby tab (manual Ch.8 Fig 8.2, three
    checkboxes + k* value). The Cold/PR/Autos/k* columns were already
    read by `typed.py` but had no tab of their own or editor."""
    from PySide6.QtWidgets import QCheckBox
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/CS_Probe/input.rbd"))
    dock.set_rbd(rbd)
    from raptor.core.fileio.typed import node_from_row

    node = next(node_from_row(row) for row in rbd.sections["NODE_DATA"].rows)
    dock._populate_node(node)
    QApplication.processEvents()

    idx = next(i for i in range(dock._tabs.count())
              if "Standby" in dock._tabs.tabText(i))
    assert dock._tabs.isTabVisible(idx)
    page = dock._tabs.widget(idx)
    checks = {cb.text(): cb for cb in page.findChildren(QCheckBox)}
    assert len(checks) == 3
    engage = next(cb for t, cb in checks.items() if "Engage" in t)
    pr = next(cb for t, cb in checks.items() if "Return alternates" in t)
    autos = next(cb for t, cb in checks.items() if "automatic switchover" in t)
    # PR/Autos are gated on the "engage standby" checkbox (manual: "the
    # first checkbox, which engages the remainder of the tab").
    assert engage.isChecked() == bool(node.cold_standby)
    assert pr.isEnabled() == bool(node.cold_standby)
    assert autos.isEnabled() == bool(node.cold_standby)

    captured = {}
    dock.node_cold_standby_changed.connect(
        lambda nid, v: captured.setdefault("cold", (nid, v)))
    dock.node_k_star_changed.connect(
        lambda nid, v: captured.setdefault("kstar", (nid, v)))
    engage.setChecked(not engage.isChecked())
    assert captured["cold"] == (node.id, not bool(node.cold_standby))


def test_node_kstar_bounded_by_n(qapp):
    """k* target is bounded by n. n is CS_Probe's node "Power"'s own raw
    NODE_DATA `n` column (3), distinct from `RBDNode.connect_out_of_num`."""
    from PySide6.QtWidgets import QLineEdit
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/CS_Probe/input.rbd"))
    dock.set_rbd(rbd)
    from raptor.core.fileio.typed import node_from_row

    power = next(node_from_row(row) for row in rbd.sections["NODE_DATA"].rows
                if row.get("Name") == "Power")
    raw = dock._raw_node_row(power.id)
    assert raw.get("n") == "3"
    dock._populate_node(power)
    QApplication.processEvents()

    idx = next(i for i in range(dock._tabs.count())
              if "Standby" in dock._tabs.tabText(i))
    page = dock._tabs.widget(idx)
    kstar_edit = next(e for e in page.findChildren(QLineEdit)
                      if e.validator() is not None
                      and e.validator().top() == 3)
    captured = {}
    dock.node_k_star_changed.connect(
        lambda nid, v: captured.setdefault("kstar", (nid, v)))
    kstar_edit.setText("99")
    kstar_edit.editingFinished.emit()
    assert kstar_edit.text() == "3"
    assert captured["kstar"] == (power.id, 3)


def test_node_standby_switchover_table_edits_links(qapp):
    """The per-incoming-link switchover table (Priority / Auto Prob /
    Auto Time / Man Time; LINK_DATA ColdPri/AProb/AutoTime/ManTime)
    routes edits through the undoable signal with the original's range
    checks (Priority 1-99)."""
    from PySide6.QtWidgets import QTableWidget
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/CS_Probe/input.rbd"))
    dock.set_rbd(rbd)
    from raptor.core.fileio.typed import node_from_row

    nodes = [node_from_row(row) for row in rbd.sections["NODE_DATA"].rows]
    node = next(n for n in nodes if dock._link_rows_for_node(n.id)[0])
    dock._populate_node(node)
    QApplication.processEvents()

    idx = next(i for i in range(dock._tabs.count())
              if "Standby" in dock._tabs.tabText(i))
    page = dock._tabs.widget(idx)
    tables = [t for t in page.findChildren(QTableWidget)
              if t.columnCount() == 5]
    assert tables, "switchover table missing from the Standby tab"
    t = tables[0]
    assert t.rowCount() >= 1, "node fixture has no incoming links?"

    captured = {}
    dock.link_standby_cell_changed.connect(
        lambda lid, col, old, new: captured.setdefault("edit",
                                                       (lid, col, old, new)))
    pri = t.item(0, 1)
    old = pri.text()
    pri.setText("7")
    assert captured.get("edit") is not None
    assert captured["edit"][1] == "ColdPri"
    assert captured["edit"][3] == "7"

    # Out-of-range priority is rejected and the cell reverts to the live
    # row's stored value.
    t.item(0, 1).setText("500")
    assert t.item(0, 1).text() == old



def test_node_capacity_flow_tables_and_generated_text(qapp):
    """The Node Properties Capacity tab shows a link table once a flow
    checkbox is unchecked, plus a read-only "Flow generated = N" note
    (manual Ch.14 Fig 14.1, a system-wide setting echoed here).

    Node "n1" (ID 16) in `tests/golden/CS_Probe` is the model captured
    from Raptor 7's node dialog; the expected values match what it showed.
"""
    from PySide6.QtWidgets import QTableWidget
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/CS_Probe/input.rbd"))
    dock.set_rbd(rbd)
    from raptor.core.fileio.typed import node_from_row

    node = next(node_from_row(row) for row in rbd.sections["NODE_DATA"].rows
               if row.get("ID") == "16")
    assert node.full_flow and not node.any_path   # matches the screenshot

    dock._populate_node(node)
    QApplication.processEvents()
    idx = next(i for i in range(dock._tabs.count())
              if "Capacity" in dock._tabs.tabText(i))
    page = dock._tabs.widget(idx)

    note = next((w for w in page.findChildren(QLabel)
                if "Flow generated" in w.text()), None)
    assert note is not None and note.text() == "Flow generated = 1500"

    tables = page.findChildren(QTableWidget)
    # full_flow=True (Flow In table hidden), any_path=False (Flow Out
    # table shown) -- exactly the screenshot's own state.
    assert len(tables) == 1
    t = tables[0]
    assert [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())] \
        == ["Link To", "Type", "Priority"]
    rows = {t.item(r, 0).text(): (t.item(r, 1).text(), t.item(r, 2).text())
           for r in range(t.rowCount())}
    assert rows == {"500path": ("Hier", "50"), "650path": ("Hier", "50"),
                    "900path": ("Hier", "50")}


def test_node_capacity_flow_in_table_shows_confirmed_sentinel_keywords(qapp):
    """Flip both checkboxes off: Flow In's table shows the "MaxFlow"/
    "FullFlow" keyword text real Raptor shows for the -1 sentinel in
    nomFlow/MaxFlow, not the raw -1."""
    from PySide6.QtWidgets import QTableWidget
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/CS_Probe/input.rbd"))
    dock.set_rbd(rbd)
    from raptor.core.fileio.typed import node_from_row

    node = next(node_from_row(row) for row in rbd.sections["NODE_DATA"].rows
               if row.get("ID") == "16")
    node.full_flow = False
    dock._populate_node(node)
    QApplication.processEvents()
    idx = next(i for i in range(dock._tabs.count())
              if "Capacity" in dock._tabs.tabText(i))
    page = dock._tabs.widget(idx)
    t = next(t for t in page.findChildren(QTableWidget)
            if t.columnCount() == 4)
    assert [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())] \
        == ["Link From", "Type", "Nominal Flow", "Max Flow"]
    assert t.rowCount() == 1
    assert [t.item(0, c).text() for c in range(4)] == \
        ["Attitude", "Block", "MaxFlow", "FullFlow"]


def test_show_annotation_only_general_tab_and_colour_font_controls(
        qapp, monkeypatch):
    """Right-click "Colours & Font…" opens controls in the Inspector (the
    separate modal dialog is gone): `show_annotation` shows only the
    General tab, which carries the Colours and Font groups."""
    from raptor.gui.ui.diagram import DiagramView
    from PySide6.QtCore import QPointF

    v = DiagramView()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/TwoBlockRedundancy",
                                "input.rbd"))
    v.load_rbd(rbd)
    v.tool_mode = "anno_rect"
    v._handle_tool_click(QPointF(100, 100))       # arm (two-click placement)
    v._handle_tool_click(QPointF(220, 170))       # commit
    aid, item = next(iter(v._anno_items.items()))

    dock = InspectorDock()
    dock.set_rbd(rbd)
    dock.show_annotation(item)
    QApplication.processEvents()

    visible = [dock._tabs.tabText(i) for i in range(dock._tabs.count())
              if dock._tabs.isTabVisible(i)]
    assert visible == ["⏺ General"]
    assert f"ID {aid}" in dock._header.text()
    assert _group(dock._scroll_general, "Colours") is not None
    assert _group(dock._scroll_general, "Font") is not None

    # Border-colour swatch button -> commits through annotation_prop_changed
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog, QPushButton
    monkeypatch.setattr(QColorDialog, "getColor",
                        staticmethod(lambda *a, **kw: QColor("#00FF00")))
    changes = []
    dock.annotation_prop_changed.connect(
        lambda aid_, ch: changes.append((aid_, ch)))
    colours = _group(dock._scroll_general, "Colours")
    border_btn = next(b for b in colours.findChildren(QPushButton))
    border_btn.click()
    assert changes == [(aid, {"border_colour": "#00ff00"})]


def test_show_annotation_arrow_hides_fill_and_font_shows_line_style(qapp):
    """An arrow has no fill or text, so the Fill and Font groups are hidden
    for it (unlike rect/ellipse/text), but it does get a "Line style"
    dropdown labelled "Line" rather than "Border", since the line is the
    whole shape."""
    from PySide6.QtWidgets import QComboBox
    from raptor.gui.ui.diagram import DiagramView, ANNO_LINE_STYLES
    from PySide6.QtCore import QPointF

    v = DiagramView()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/TwoBlockRedundancy",
                                "input.rbd"))
    v.load_rbd(rbd)
    v.tool_mode = "anno_arrow"
    v._handle_tool_click(QPointF(0, 0))
    v._handle_tool_click(QPointF(100, 0))
    aid, item = next(iter(v._anno_items.items()))

    dock = InspectorDock()
    dock.set_rbd(rbd)
    dock.show_annotation(item)
    QApplication.processEvents()

    colours = _group(dock._scroll_general, "Colours")
    assert colours is not None
    assert _group(dock._scroll_general, "Font") is None, \
        "an arrow has no text -- Font group must be hidden"
    labels = _labels_of(colours)
    assert any(l.startswith("Line") for l in labels), labels
    assert not any(l.startswith("Fill") for l in labels), labels

    combo = next(w for w in colours.findChildren(QComboBox))
    assert combo.count() == len(ANNO_LINE_STYLES)
    assert combo.currentData() == "solid"

    changes = []
    dock.annotation_prop_changed.connect(
        lambda aid_, ch: changes.append((aid_, ch)))
    combo.setCurrentIndex(combo.findData("dash"))
    assert changes == [(aid, {"line_style": "dash"})]


def test_failure_param_formula_and_rate_link(qapp):
    """Failure/Repair Param fields accept a formula or "@RateName" instead
    of a bare number, so a failure rate can stay linked to its km/yr
    basis rather than showing a flattened float. The typed text is
    stored in `RbdFile.formulas` and shown verbatim on the next
    populate; committing a plain number clears that record."""
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/PreventMxDefer/input.rbd"))
    dock.set_rbd(rbd)
    rbd.rates.append({
        "name": "Highway",
        "expr": "0.00015 * 20000 / 8760  # 0.00015 fail/km, 20000 km/yr",
        "value": 0.00015 * 20000 / 8760,
    })
    from raptor.core.fileio.typed import block_from_row
    block = next(b for row in rbd.sections["BLOCK_DATA"].rows
                if (b := block_from_row(row)).id == 5)
    addr = "block:5:FParam1"

    dock._populate_block(block)
    QApplication.processEvents()
    fail = _group(dock._scroll_failure, "Failure Distribution")
    shape_edit = _edits_of(fail)[0]

    shape_edit.setText("@Highway")
    shape_edit.editingFinished.emit()
    QApplication.processEvents()
    assert abs(rbd.formulas[addr]["value"]
              - 0.00015 * 20000 / 8760) < 1e-9
    assert rbd.formulas[addr]["rate"] == "Highway"

    # re-populate (as if reopening/reselecting the block) -- the field
    # must show "@Highway" again, not the flattened number
    dock._populate_block(block)
    QApplication.processEvents()
    fail2 = _group(dock._scroll_failure, "Failure Distribution")
    shape_edit2 = _edits_of(fail2)[0]
    assert shape_edit2.text() == "@Highway"

    # a plain number clears the formula record
    shape_edit2.setText("2.5")
    shape_edit2.editingFinished.emit()
    QApplication.processEvents()
    assert addr not in rbd.formulas


def test_failure_param_rejects_invalid_expression(qapp):
    """An invalid formula reverts the field and reports through
    `validation_failed`, same as an out-of-domain plain number always
    has."""
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/PreventMxDefer/input.rbd"))
    dock.set_rbd(rbd)
    from raptor.core.fileio.typed import block_from_row
    block = next(b for row in rbd.sections["BLOCK_DATA"].rows
                if (b := block_from_row(row)).id == 5)
    dock._populate_block(block)
    QApplication.processEvents()
    fail = _group(dock._scroll_failure, "Failure Distribution")
    shape_edit = _edits_of(fail)[0]
    original = shape_edit.text()

    errors = []
    dock.validation_failed.connect(errors.append)
    shape_edit.setText("__import__('os')")
    shape_edit.editingFinished.emit()
    QApplication.processEvents()
    assert shape_edit.text() == original
    assert errors
    assert "block:5:FParam1" not in rbd.formulas


def test_display_unit_converts_param_fields_and_labels(qapp):
    """A display-unit override (view/enter values in e.g. years on an
    hours-native model) shows converted numbers and "(unit)" labels on
    `_distribution_params_row` fields, and converts back to the model's native unit
    on commit -- the stored value always ends up in native hours."""
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/PreventMxDefer/input.rbd"))
    dock.set_rbd(rbd)
    from raptor.core.fileio.typed import block_from_row
    block = next(b for row in rbd.sections["BLOCK_DATA"].rows
                if (b := block_from_row(row)).id == 5)
    native_scale = block.fail_values[1]   # Weibull Scale, in hours

    dock.set_display_unit("years")
    dock._populate_block(block)
    QApplication.processEvents()
    fail = _group(dock._scroll_failure, "Failure Distribution")
    labels = _labels_of(fail)
    assert any("(years)" in lab for lab in labels), labels
    scale_edit = _edits_of(fail)[1]
    assert abs(float(scale_edit.text()) - native_scale / 8760) < 1e-3

    scale_edit.setText("2")   # 2 years
    scale_edit.editingFinished.emit()
    QApplication.processEvents()
    # The model object (updated via on_commit -> block_fail_params_changed
    # -> the caller) is not wired in this unit test, but the displayed
    # text must reflect what was actually committed in native units.
    assert abs(float(scale_edit.text()) - 2.0) < 1e-6


def test_display_unit_none_is_native_unchanged(qapp):
    """No override (`None`, the default) renders natively, with no unit
    text or conversion."""
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/PreventMxDefer/input.rbd"))
    dock.set_rbd(rbd)
    assert dock._display_unit is None
    from raptor.core.fileio.typed import block_from_row
    block = next(b for row in rbd.sections["BLOCK_DATA"].rows
                if (b := block_from_row(row)).id == 5)
    dock._populate_block(block)
    QApplication.processEvents()
    fail = _group(dock._scroll_failure, "Failure Distribution")
    scale_edit = _edits_of(fail)[1]
    assert abs(float(scale_edit.text()) - block.fail_values[1]) < 1e-6


def test_empirical_enter_data_button_reachable_and_unit_aware(qapp, monkeypatch):
    """The Empirical "Enter data…" button is reachable and unit-aware.

    `DISTRO_PARAM_LABELS[16]` is all empty (Empirical has no named
    per-param slots -- it is a whole data-point list), so `_distribution_params_row`
    would otherwise hit its "no parameters" early return before
    building the button. The values shown to the dialog are converted
    into the display unit like every other time field.
"""
    from PySide6.QtWidgets import QPushButton, QDialog, QListWidget, QLabel
    from raptor.core.fileio.typed import block_from_row
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/PreventMxDefer/input.rbd"))
    dock.set_rbd(rbd)
    dock.set_display_unit("years")
    block = next(b for row in rbd.sections["BLOCK_DATA"].rows
                if (b := block_from_row(row)).id == 5)
    block.fail_distro = 16
    block.fail_values = [8760.0, 17520.0, 0.0]   # 1yr, 2yr in native hours

    dock._populate_block(block)
    QApplication.processEvents()
    buttons = [b for b in dock._scroll_failure.widget().findChildren(QPushButton)
              if "Enter data" in b.text()]
    assert buttons, "'Enter data…' button must exist for Empirical"

    captured = {}

    def fake_exec(self):
        lst = self.findChildren(QListWidget)[0]
        captured["shown"] = sorted(float(lst.item(i).text())
                                   for i in range(lst.count()))
        captured["head"] = self.findChildren(QLabel)[0].text()
        return QDialog.Accepted   # round-trip: OK with no edits
    monkeypatch.setattr(QDialog, "exec", fake_exec)

    buttons[0].click()
    assert captured["shown"] == [1.0, 2.0], \
        "dialog must show values already converted to the display unit"
    assert "years" in captured["head"]
    # OK-with-no-edits round-trips back to the original native values,
    # proving display_to_native(native_to_display(x)) == x through the
    # real UI path.
    assert abs(block.fail_values[0] - 8760.0) < 1e-3
    assert abs(block.fail_values[1] - 17520.0) < 1e-3


def test_empirical_shows_mean_and_stddev(qapp):
    """Empirical (16) distributions show Mean/Std Dev rows like every
    other distribution, computed from the data list by
    `diststats.compute_stats`."""
    from raptor.core.fileio.typed import block_from_row
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/PreventMxDefer/input.rbd"))
    dock.set_rbd(rbd)
    block = next(b for row in rbd.sections["BLOCK_DATA"].rows
                if (b := block_from_row(row)).id == 5)
    block.fail_distro = 16
    block.fail_values = [10.0, 20.0, 30.0]
    dock._populate_block(block)
    QApplication.processEvents()
    fail = _group(dock._scroll_failure, "Failure Distribution")
    labels = _labels_of(fail)
    assert any(t.startswith("Mean") for t in labels), labels
    assert any(t.startswith("Std Dev") for t in labels), labels
    form = fail.layout()
    mean_text = next(
        form.itemAt(i, form.ItemRole.FieldRole).widget().text()
        for i in range(form.rowCount())
        if (li := form.itemAt(i, form.ItemRole.LabelRole)) and li.widget()
        and li.widget().text().startswith("Mean"))
    assert mean_text != "—"
    assert abs(float(mean_text.split()[0]) - 20.0) < 1e-3, mean_text


def test_empirical_commit_beyond_three_points_does_not_crash(qapp, monkeypatch):
    """Committing a >3-point curve from the Empirical "Enter data…" dialog
    does not raise. The shared param list is replaced wholesale
    (`current[:] = vals`), so it grows to hold every point rather than
    assigning past a fixed 3-slot list."""
    from PySide6.QtWidgets import QPushButton
    from raptor.core.fileio.typed import block_from_row
    import raptor.gui.ui.empirical_dialog as ed

    def fake_open_empirical_dialog(parent, values, unit_label):
        return [5.0, 10.0, 15.0, 20.0, 25.0]   # 5 points, > the 3 slots
    # `inspector.py` does `from ...empirical_dialog import
    # open_empirical_dialog` INSIDE the method that builds this form,
    # binding it into a closure local -- patching the source module's
    # attribute must happen BEFORE `_populate_block` runs that import,
    # unlike a plain module-level patch.
    monkeypatch.setattr(ed, "open_empirical_dialog",
                        fake_open_empirical_dialog)

    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/PreventMxDefer/input.rbd"))
    dock.set_rbd(rbd)
    block = next(b for row in rbd.sections["BLOCK_DATA"].rows
                if (b := block_from_row(row)).id == 5)
    block.fail_distro = 16
    block.fail_values = [10.0, 20.0, 30.0]
    dock._populate_block(block)
    QApplication.processEvents()
    buttons = [b for b in dock._scroll_failure.widget().findChildren(QPushButton)
              if "Enter data" in b.text()]

    buttons[0].click()   # must not raise IndexError
    fail = _group(dock._scroll_failure, "Failure Distribution")
    form = fail.layout()
    mean_text = sd_text = None
    for i in range(form.rowCount()):
        lbl_item = form.itemAt(i, form.ItemRole.LabelRole)
        if lbl_item and lbl_item.widget() and \
                lbl_item.widget().text().startswith("Mean"):
            mean_text = form.itemAt(i, form.ItemRole.FieldRole).widget().text()
        if lbl_item and lbl_item.widget() and \
                lbl_item.widget().text().startswith("Std Dev"):
            sd_text = form.itemAt(i, form.ItemRole.FieldRole).widget().text()
    # mean/stdev of [5, 10, 15, 20, 25] -- proves `current` actually
    # grew to hold all 5 points, not just the first 3
    assert abs(float(mean_text.split()[0]) - 15.0) < 1e-3, mean_text
    assert abs(float(sd_text.split()[0]) - 7.071) < 1e-2, sd_text


def test_show_hier_has_phasing_checkbox(qapp):
    """Manual Ch.17: hierarchies can be marked phased too (shown hatched on
    the canvas), but unlike block/node/event they have no per-phase
    value table, just a checkbox."""
    from PySide6.QtWidgets import QCheckBox
    from raptor.core.fileio.typed import hier_from_row
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/Phasing1/input.rbd"))
    dock.set_rbd(rbd)
    phased_row = next(r for r in rbd.sections["HIERARCHY_DATA"].rows
                      if r.get("hierarchyName") == "500path")
    unphased_row = next(r for r in rbd.sections["HIERARCHY_DATA"].rows
                        if r.get("hierarchyName") == "650path")
    phased_hier = hier_from_row(phased_row)
    unphased_hier = hier_from_row(unphased_row)
    assert phased_hier.uses_phasing and not unphased_hier.uses_phasing

    dock.show_hier(phased_hier)
    QApplication.processEvents()
    cb = next(c for c in dock._scroll_general.widget().findChildren(QCheckBox)
             if c.text() == "Phased")
    assert cb.isChecked()

    captured = {}
    dock.phasing_changed.connect(
        lambda kind, oid, phased, entries:
            captured.setdefault("emitted", (kind, oid, phased, entries)))
    cb.setChecked(False)
    assert captured["emitted"] == ("hier", phased_hier.id, False, [])

    dock.show_hier(unphased_hier)
    QApplication.processEvents()
    cb2 = next(c for c in dock._scroll_general.widget().findChildren(QCheckBox)
              if c.text() == "Phased")
    assert not cb2.isChecked()


def test_invalid_distro_params_dont_crash_mean_stddev_refresh(qapp):
    """An invalid parameterization does not crash the Failure tab's live
    Mean/Std Dev refresh. `compute_stats` returns a bare None for such
    params (Beta with a zero second shape), and the refresh handles it
    by showing the placeholder."""
    from raptor.core.fileio.typed import block_from_row
    dock = InspectorDock()
    rbd = load_rbd(os.path.join(
        REPO, "tests/golden/TwoBlockRedundancy/input.rbd"))
    dock.set_rbd(rbd)
    block = block_from_row(rbd.sections["BLOCK_DATA"].rows[0])
    block.fail_distro = 1          # Beta
    block.fail_values = [100.0, 0.0, 0.0]   # invalid: 2nd shape param is 0
    dock._populate_block(block)    # must not raise
    QApplication.processEvents()

    fail = _group(dock._scroll_failure, "Failure Distribution")
    texts = [w.text() for w in fail.findChildren(QLabel)]
    assert "Mean (hours):" in texts   # caption present
    assert "—" in texts               # value label shows the placeholder,
                                       # not a stale/garbage number


def test_formula_record_edit_is_undoable(qapp):
    """Typing a formula stores a `RbdFile.formulas` basis record. Ctrl+Z
    restored the numeric value but left the record behind; the basis
    change is now its own undo command."""
    from PySide6.QtGui import QUndoStack
    from raptor.core.fileio.typed import block_from_row

    dock = InspectorDock()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/TwoBlockRedundancy",
                               "input.rbd"))
    stack = QUndoStack()
    dock.set_rbd(rbd)
    dock.set_undo_stack(stack)
    block = next(block_from_row(r) for r in rbd.sections["BLOCK_DATA"].rows
                 if r.get("ID") == "3")
    dock._populate_block(block)
    QApplication.processEvents()

    page = dock._scroll_failure.widget()
    edits = [e for e in page.findChildren(QLineEdit) if e.validator() is None]
    assert edits, "no formula-enabled failure param field"
    edit = edits[0]
    addr = "block:3:FParam1"
    assert addr not in rbd.formulas

    edit.setText("0.25 * 2")
    edit.editingFinished.emit()
    assert rbd.formulas.get(addr, {}).get("expr") == "0.25 * 2"
    assert rbd.formulas[addr]["value"] == 0.5

    stack.undo()
    assert addr not in rbd.formulas, "undo must remove the basis record"


def test_show_annotation_keeps_the_inspector_on_the_annotation(qapp):
    """After a prop commit fires model_changed and defers an Inspector
    refresh, the panel must stay on the annotation -- not jump back to the
    last block shown (the reported line-width bug)."""
    from raptor.gui.ui.diagram import DiagramView
    from PySide6.QtCore import QPointF

    v = DiagramView()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/TwoBlockRedundancy",
                                "input.rbd"))
    v.load_rbd(rbd)
    v.tool_mode = "anno_rect"
    v._handle_tool_click(QPointF(100, 100))
    v._handle_tool_click(QPointF(240, 190))
    aid, item = next(iter(v._anno_items.items()))

    dock = InspectorDock()
    dock.set_rbd(rbd)
    # Show a block first, so a stale target would be a block.
    dock.show_block(next(iter(v.blocks)), v.blocks)
    dock.show_annotation(item)
    assert dock.current_target() == ("annotation", aid)


def test_colour_swatch_border_contrasts_with_the_swatch(qapp):
    """The swatch outline contrasts with its own fill, so it stays visible
    when the colour matches the panel behind it."""
    from raptor.gui.ui.diagram import DiagramView
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QPushButton

    v = DiagramView()
    rbd = load_rbd(os.path.join(REPO, "tests/golden/TwoBlockRedundancy",
                                "input.rbd"))
    v.load_rbd(rbd)
    v.tool_mode = "anno_rect"
    v._handle_tool_click(QPointF(100, 100))
    v._handle_tool_click(QPointF(240, 190))
    dock = InspectorDock()
    dock.set_rbd(rbd)

    def border_of(colour):
        _, item = next(iter(v._anno_items.items()))
        item.props["border_colour"] = colour
        dock.show_annotation(item)
        colours = _group(dock._scroll_general, "Colours")
        btn = next(b for b in colours.findChildren(QPushButton))
        return btn.styleSheet()

    assert "2px solid #000000" in border_of("#FFFFFF")
    assert "2px solid #FFFFFF" in border_of("#000000")


class TestEmptyState:
    """With nothing selected the Inspector used to show 'No selection'
    above eight empty property tabs. It now says what to do and what the
    model holds."""

    def _dock(self, qapp):
        from raptor.gui.ui.inspector import InspectorDock
        d = InspectorDock()
        d.show()
        qapp.processEvents()
        return d

    def test_starts_in_the_empty_state(self, qapp):
        d = self._dock(qapp)
        assert d._empty.isVisibleTo(d)
        assert not d._tabs.isVisibleTo(d)
        assert "Open or create a model" in d._empty.text()

    def test_summarises_the_model_without_counting_the_template_row(
            self, qapp):
        from raptor.core.fileio.rbd import load_rbd
        rbd = load_rbd("tests/golden/TwoBlockRedundancy/input.rbd")
        d = self._dock(qapp)
        d.set_rbd(rbd)
        d.clear_selection()
        real = [r for r in rbd.sections["BLOCK_DATA"].rows
                if str(r.get("ID", "")).strip() not in ("0", "0.0",
                                                        "0.000000")]
        assert f"{len(real)} blocks" in d._empty.text()

    def test_selecting_shows_the_tabs_and_clearing_brings_it_back(
            self, qapp):
        from raptor.core.fileio.rbd import load_rbd
        from raptor.core.fileio.typed import block_from_row
        rbd = load_rbd("tests/golden/TwoBlockRedundancy/input.rbd")
        d = self._dock(qapp)
        d.set_rbd(rbd)
        row = next(r for r in rbd.sections["BLOCK_DATA"].rows
                   if str(r.get("ID", "")).strip() not in ("0", "0.0",
                                                           "0.000000"))
        b = block_from_row(row)
        d.show_block(b.id, {b.id: b})
        qapp.processEvents()
        assert d._tabs.isVisibleTo(d)
        assert not d._empty.isVisibleTo(d)
        d.clear_selection()
        qapp.processEvents()
        assert d._empty.isVisibleTo(d)
        assert not d._tabs.isVisibleTo(d)

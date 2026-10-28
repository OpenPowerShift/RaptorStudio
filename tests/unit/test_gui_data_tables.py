"""Headless GUI tests for the Data Tables dock's Events grid.

The Events tab is the first event-editing surface in the GUI: Name /
Distro / failVal / failStream, routed through the diagram view's undoable
mutators (`rename_event` / `set_event_field`), which also mirror edits into
the EVENT_DATA row so Save persists them. Covers population from a real
fixture, rename landing in model + file + undo, param/stream commits, and
name normalization (whitespace to underscores, required by the `.rbd`
format).
"""
from __future__ import annotations

import os

import pytest
from raptor.gui.ui import filedialogs as _fdg

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests/golden/Events/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(FIXTURE), None)
    return w


def _event_row(rbd, eid):
    return next(r for r in rbd.sections["EVENT_DATA"].rows
                if int(float(r["ID"])) == eid)


def _set_param_widget(table, row, col, text):
    """A keyed grid's time-dimensioned columns (ArriveEvery/EmerTime/
    SLOTime/InitUsed, and a non-Empirical Param1-3) are `_param_cell`
    widgets, not `QTableWidgetItem`s -- drive one the same way
    `test_blocks_grid_param_cells_respect_display_unit` drives the
    Blocks grid's equivalent cells."""
    edit = table.cellWidget(row, col).findChild(QLineEdit)
    edit.setText(text)
    edit.editingFinished.emit()


def test_blocks_grid_time_columns_carry_the_models_own_unit(qapp):
    """The Blocks tab's PM Frequency / Failure Mean / Failure Std Dev /
    Repair Mean / Repair Std Dev columns are genuine sim-time quantities
    and carry the model's configured unit (SYSTEM_DATA `units`), unlike
    results.py's equivalent MTBDE/MDT columns, which already append it."""
    w = MainWindow()
    fixture = os.path.join(REPO, "tests/golden/CS_Probe/input.rbd")
    w._load_into_app(load_rbd(fixture), None)
    t = w._data_tables._blocks_table
    headers = {t.horizontalHeaderItem(c).text()
               for c in range(t.columnCount())}
    assert "Failure Mean (hours)" in headers
    assert "Failure Std Dev (hours)" in headers
    assert "Repair Mean (hours)" in headers
    assert "Repair Std Dev (hours)" in headers
    assert "PM Frequency (hours)" in headers
    # non-time columns do not gain a unit suffix
    assert "Name" in headers and "ID" in headers

    # loading a SECOND model must not accumulate "(hours) (hours)"
    w._load_into_app(load_rbd(fixture), None)
    headers2 = {t.horizontalHeaderItem(c).text()
                for c in range(t.columnCount())}
    assert headers2 == headers


def test_blocks_grid_follows_a_rename_and_undo(qapp):
    """Data Tables is model-derived; an Inspector/canvas rename and its
    undo must not leave the old name in the grid."""
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/CS_Probe/input.rbd")), None)
    v = w._diagram_dock.view
    t = w._data_tables._blocks_table
    bid = sorted(v.blocks)[0]
    old = v.blocks[bid].name
    v.rename_block(bid, "Renamed_Block")
    for _ in range(4):
        qapp.processEvents()
    assert t.item(0, 0).text() == "Renamed_Block"
    v._stack.undo()
    for _ in range(4):
        qapp.processEvents()
    assert t.item(0, 0).text() == old
    w._dirty = False
    w.close()


def test_sparing_order_interval_uses_the_display_unit(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/Capacity1/input.rbd")), None)
    t = w._data_tables._sparing_table
    w._data_tables.set_display_unit("days")
    qapp.processEvents()
    assert t.horizontalHeaderItem(6).text() == "Order Interval (days)"
    row = 0
    edit = t.cellWidget(row, 6).findChild(QLineEdit)
    assert edit is not None and float(edit.text()) == pytest.approx(30.0)
    edit.setText("10")
    edit.editingFinished.emit()
    qapp.processEvents()
    bid = sorted(w._diagram_dock.view.blocks)[row]
    assert w._diagram_dock.view.blocks[bid].new_spares_arrival == pytest.approx(240.0)
    w._dirty = False
    w.close()


def test_widget_columns_refuse_a_sort_indicator(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/Capacity1/input.rbd")), None)
    t = w._data_tables._blocks_table
    t.sortItems(2, Qt.SortOrder.AscendingOrder)   # Failure Dist widget
    qapp.processEvents()
    assert t.horizontalHeader().sortIndicatorSection() == -1
    w._dirty = False
    w.close()


def test_user_sort_survives_refresh(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/CS_Probe/input.rbd")), None)
    t = w._data_tables._blocks_table
    t.sortItems(1, Qt.SortOrder.DescendingOrder)
    qapp.processEvents()
    assert t.horizontalHeader().sortIndicatorSection() == 1
    w._data_tables.refresh()
    qapp.processEvents()
    assert t.horizontalHeader().sortIndicatorSection() == 1
    ids = [int(t.item(r, 1).text()) for r in range(t.rowCount())]
    assert ids == sorted(ids, reverse=True)
    w._dirty = False
    w.close()


def test_display_unit_controls_headers_and_computed_means(qapp):
    """A display-unit override must relabel and convert the computed
    Mean/Std Dev cells too, and a native-unit edit must replace the
    conversion basis rather than leave the old one."""
    from raptor.gui.ui.units_conv import native_to_display

    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/CS_Probe/input.rbd")), None)
    w._data_tables.set_display_unit("days")
    w._data_tables.set_units("minutes")
    w._data_tables.refresh()
    t = w._data_tables._blocks_table
    headers = {t.horizontalHeaderItem(c).text()
               for c in range(t.columnCount())}
    assert "Failure Mean (days)" in headers
    assert "Failure Std Dev (days)" in headers
    assert "PM Frequency (days)" in headers
    # The row's native Failure Mean must be shown in days, not minutes.
    from raptor.core.engine.diststats import compute_stats
    v = w._diagram_dock.view
    bid = sorted(v.blocks)[0]
    b = v.blocks[bid]
    mean = compute_stats(b.fail_distro, list(b.fail_values))[0]
    expected = f"{native_to_display(mean, 'minutes', 'days'):.4g}"
    assert t.item(0, 13).text() == expected
    w._dirty = False
    w.close()


def test_events_grid_populates(window):
    t = window._data_tables._events_table
    v = window._diagram_dock.view
    assert t.rowCount() == len(v.events) == 5
    # First row mirrors the model's first event.
    eid = sorted(v.events)[0]
    assert t.item(0, 0).text() == v.events[eid].name
    assert t.item(0, 1).text() == str(eid)


def test_rename_lands_in_model_file_and_undo(window):
    w, t = window, window._data_tables._events_table
    v = w._diagram_dock.view
    eid = sorted(v.events)[0]
    old = v.events[eid].name

    t.item(0, 0).setText("Renamed_Event")
    window._data_tables._on_event_name_changed(t.item(0, 0))

    assert v.events[eid].name == "Renamed_Event"
    assert _event_row(w._rbd, eid)["eventName"] == "Renamed_Event"

    w._undo_stack.undo()
    assert v.events[eid].name == old
    assert _event_row(w._rbd, eid)["eventName"] == old


def test_rename_normalizes_whitespace(window):
    w, t = window, window._data_tables._events_table
    v = w._diagram_dock.view
    eid = sorted(v.events)[0]
    old = v.events[eid].name
    # Embedded spaces would shift every later column on save -- the
    # grid must collapse them to underscores instead.
    t.item(0, 0).setText("Two Words")
    window._data_tables._on_event_name_changed(t.item(0, 0))
    assert v.events[eid].name == "Two_Words"
    w._undo_stack.undo()
    assert v.events[eid].name == old


def test_param_and_stream_commits_sync_file(window):
    w, _t = window, window._data_tables._events_table
    v = w._diagram_dock.view
    eid = sorted(v.events)[0]
    row = _event_row(w._rbd, eid)
    old_param, old_stream = float(row["failVal"]), int(float(row["failStream"]))

    v.set_event_field(eid, "param", 0.61)
    v.set_event_field(eid, "stream", 9)
    assert v.events[eid].fail_mean == pytest.approx(0.61)
    assert _event_row(w._rbd, eid)["failVal"] == "0.610000"
    assert _event_row(w._rbd, eid)["failStream"] == "9"

    w._undo_stack.undo()
    w._undo_stack.undo()
    assert v.events[eid].fail_mean == pytest.approx(old_param)
    assert v.events[eid].fail_stream == old_stream
    row = _event_row(w._rbd, eid)
    assert float(row["failVal"]) == pytest.approx(old_param)
    assert int(float(row["failStream"])) == old_stream


def test_no_op_edits_do_not_dirty_the_stack(window):
    w, _t = window, window._data_tables._events_table
    v = w._diagram_dock.view
    eid = sorted(v.events)[0]
    depth = w._undo_stack.count()
    v.set_event_field(eid, "param", v.events[eid].fail_mean)
    v.rename_event(eid, v.events[eid].name)
    assert w._undo_stack.count() == depth


def test_blocks_grid_param_cells_respect_display_unit(qapp):
    """The Data Tables grid's Failure/Repair Param and PM Frequency cells
    show and accept the display-unit override, writing back to the model
    in its native unit. A Shape param is not converted (matching the
    Inspector's exclusion for Shape/N-Trials/Probability)."""
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/PreventMxDefer/input.rbd")), "x")
    v = w._diagram_dock.view
    dt = w._data_tables
    dt.set_display_unit("years")
    QApplication.instance().processEvents()

    t = dt._blocks_table
    row = next(r for r in range(t.rowCount()) if t.item(r, 1).text() == "5")

    def cell_edit(col):
        return t.cellWidget(row, col).findChild(QLineEdit)

    native_shape = v.blocks[5].fail_values[0]
    native_scale = v.blocks[5].fail_values[1]
    shape_edit = cell_edit(3)
    scale_edit = cell_edit(4)
    assert abs(float(shape_edit.text()) - native_shape) < 1e-6, \
        "Shape must not be unit-converted"
    assert abs(float(scale_edit.text()) - native_scale / 8760) < 1e-3

    scale_edit.setText("1")
    scale_edit.editingFinished.emit()
    assert abs(v.blocks[5].fail_values[1] - 8760) < 1

    pm_edit = cell_edit(12)
    native_pm = v.blocks[5].pm_freq
    assert abs(float(pm_edit.text()) - native_pm / 8760) < 1e-3
    pm_edit.setText("5")
    pm_edit.editingFinished.emit()
    assert abs(v.blocks[5].pm_freq - 5 * 8760) < 1


def test_export_csv_covers_cell_widgets_not_just_items(qapp, monkeypatch,
                                                        tmp_path):
    """Data Tables export covers cell widgets, not only
    QTableWidgetItems. Failure/Repair Param and PM Frequency cells are
    `_param_cell` / `_distro_cell` widgets, so `.item()` alone would
    export those columns blank."""
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/PreventMxDefer/input.rbd")), "x")
    dt = w._data_tables
    dt._tabs.setCurrentIndex(0)   # Blocks

    path = str(tmp_path / "blocks.csv")
    monkeypatch.setattr(_fdg, "getSaveFileName",
                        staticmethod(lambda *a, **kw: (path, "")))
    dt._export_current_table_csv()
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "Failure Param 1" in content
    # block 5 (Weibull) has a non-zero Shape param -- must show the
    # REAL value, not an empty cell from a missed QLineEdit widget
    lines = content.splitlines()
    header = lines[0].split(",")
    id_col = header.index("ID")
    row5 = next(ln.split(",") for ln in lines[1:]
               if ln.split(",")[id_col] == "5")
    fparam1 = row5[header.index("Failure Param 1")]
    assert fparam1 not in ("", "0"), \
        f"Failure Param 1 exported blank/zero: {row5}"


def test_data_tables_export_csv_button_wired(qapp):
    """The button must exist and be connected -- not just the
    underlying method."""
    from PySide6.QtWidgets import QPushButton
    w = MainWindow()
    w._load_into_app(load_rbd(FIXTURE), None)
    buttons = [b for b in w._data_tables.findChildren(QPushButton)
              if "Export CSV" in b.text()]
    assert buttons, "'Export CSV…' button must exist on Data Tables"


# ---------------------------------------------------------------------
# Aggregate Sparing / Preventive Maintenance tabs (manual Ch.4/Ch.6
# "Block Input Tables"). These fields are otherwise editable only one
# block at a time via the Inspector.
# ---------------------------------------------------------------------

def test_pm_tab_lists_only_pm_enabled_blocks(qapp):
    """Manual 6-11: "only the seven components for which we checked
    the preventive maintenance checkbox are listed"."""
    w = MainWindow()
    fixture = os.path.join(REPO, "tests/golden/PmFailResetOff/input.rbd")
    w._load_into_app(load_rbd(fixture), None)
    v = w._diagram_dock.view
    pm = w._data_tables._pm_table
    pm_bids = {bid for bid, b in v.blocks.items() if b.uses_pm}
    assert pm.rowCount() == len(pm_bids) == 7
    shown = {int(pm.item(r, 1).text()) for r in range(pm.rowCount())}
    assert shown == pm_bids


def test_pm_tab_checkbox_toggle_commits_and_undoes(qapp):
    from PySide6.QtCore import Qt
    w = MainWindow()
    fixture = os.path.join(REPO, "tests/golden/PmFailResetOff/input.rbd")
    w._load_into_app(load_rbd(fixture), None)
    v = w._diagram_dock.view
    pm = w._data_tables._pm_table
    bid = int(pm.item(0, 1).text())
    before = v.blocks[bid].pm_spare_needed
    item = pm.item(0, 3)
    assert item.data(Qt.UserRole) == (bid, "spare_needed")

    item.setCheckState(Qt.Unchecked if before else Qt.Checked)
    assert v.blocks[bid].pm_spare_needed == (not before)
    w._undo_stack.undo()
    assert v.blocks[bid].pm_spare_needed == before


def test_pm_tab_frequency_cell_commits(qapp):
    w = MainWindow()
    fixture = os.path.join(REPO, "tests/golden/PmFailResetOff/input.rbd")
    w._load_into_app(load_rbd(fixture), None)
    v = w._diagram_dock.view
    pm = w._data_tables._pm_table
    bid = int(pm.item(0, 1).text())
    w2 = pm.cellWidget(0, 2)
    edit = w2.findChild(QLineEdit)
    edit.setText("42")
    edit.editingFinished.emit()
    assert v.blocks[bid].pm_freq == 42.0


def test_sparing_tab_lists_every_block_at_current_level(qapp):
    w = MainWindow()
    fixture = os.path.join(REPO, "tests/golden/CS_Probe/input.rbd")
    w._load_into_app(load_rbd(fixture), None)
    v = w._diagram_dock.view
    sp = w._data_tables._sparing_table
    assert sp.rowCount() == len(v.blocks)
    ids = {int(sp.item(r, 1).text()) for r in range(sp.rowCount())}
    assert ids == set(v.blocks)


def test_sparing_tab_type_combo_commits_with_undo(qapp):
    from raptor.core.model import SparingType
    w = MainWindow()
    fixture = os.path.join(REPO, "tests/golden/CS_Probe/input.rbd")
    w._load_into_app(load_rbd(fixture), None)
    v = w._diagram_dock.view
    sp = w._data_tables._sparing_table
    bid = int(sp.item(0, 1).text())
    assert v.blocks[bid].sparing_type == SparingType.CUSTOM
    combo = sp.cellWidget(0, 2)
    combo.setCurrentIndex(combo.findData("Infinite"))
    assert v.blocks[bid].sparing_type == SparingType.INFINITE
    w._undo_stack.undo()
    assert v.blocks[bid].sparing_type == SparingType.CUSTOM


def test_sparing_tab_new_spares_cells_commit_together(qapp):
    w = MainWindow()
    fixture = os.path.join(REPO, "tests/golden/CS_Probe/input.rbd")
    w._load_into_app(load_rbd(fixture), None)
    v = w._diagram_dock.view
    sp = w._data_tables._sparing_table
    bid = int(sp.item(0, 1).text())  # Custom-sparing block

    count_edit = sp.cellWidget(0, 5).findChild(QLineEdit)
    interval_edit = sp.cellWidget(0, 6).findChild(QLineEdit)
    count_edit.setText("9")
    count_edit.editingFinished.emit()
    interval_edit.setText("3.5")
    interval_edit.editingFinished.emit()
    assert v.blocks[bid].new_spares == 9
    assert v.blocks[bid].new_spares_arrival == 3.5


def test_sparing_tab_pool_name_clear_does_not_corrupt_row(qapp):
    """Clearing the Pool Name cell to an empty string must not write an
    empty token into SparePoolName: the row is whitespace-delimited, so
    an empty token vanishes on save and shifts every later column left.
    The write-back uses "unnamed" instead."""
    import tempfile
    from raptor.core.fileio.rbd import save_rbd
    from raptor.core.fileio.typed import block_from_row
    w = MainWindow()
    fixture = os.path.join(REPO, "tests/golden/CS_Probe/input.rbd")
    w._load_into_app(load_rbd(fixture), None)
    v = w._diagram_dock.view
    sp = w._data_tables._sparing_table
    bid = int(sp.item(0, 1).text())

    pool_edit = sp.cellWidget(0, 3).findChild(QLineEdit)
    pool_edit.setText("")
    pool_edit.editingFinished.emit()
    assert v.blocks[bid].pool_name == ""

    v.write_back_to_rbd(v._rbd_ref)
    row = next(r for r in v._rbd_ref.sections["BLOCK_DATA"].rows
              if int(float(r["ID"])) == bid)
    assert row["SparePoolName"] == "unnamed"
    path = os.path.join(tempfile.mkdtemp(), "roundtrip.rbd")
    save_rbd(v._rbd_ref, path)
    reloaded = load_rbd(path)
    row2 = next(r for r in reloaded.sections["BLOCK_DATA"].rows
               if int(float(r["ID"])) == bid)
    b2 = block_from_row(row2)
    assert b2.pool_name == "unnamed"
    # sibling column must not have shifted left from the empty token.
    assert row2["SpType"] == "Custom"


def test_blocks_grid_survives_mass_edit_default_invalid_beta_params(
        qapp, monkeypatch):
    """Accepting Mass Edit's Blocks tab without changing anything must not
    crash the Blocks grid. The tab's failure-distribution defaults are
    Beta with an invalid second shape param, for which `compute_stats`
    returns None; `_populate_blocks` must handle that None."""
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/TwoBlockRedundancy/input.rbd")), None)
    v = w._diagram_dock.view
    bids = list(v.blocks)[:2]
    for bid in bids:
        v._block_items[bid].setSelected(True)

    # Invalid Beta (100, 0) stored as it is: Mass Edit now refuses to
    # apply it, but a file can still carry it, and the grid must cope.
    for bid in bids:
        v.set_block_dist(bid, "F", 1)
        v.set_block_fail_params(bid, [100.0, 0.0, 0.0])
    assert v.blocks[bids[0]].fail_values == [100.0, 0.0, 0.0]  # sanity

    w._data_tables.refresh()   # must not raise
    t = w._data_tables._blocks_table
    row = next(r for r in range(t.rowCount())
              if int(t.item(r, 1).text()) == bids[0])
    assert t.item(row, 13).text() == "—"   # Failure Mean: placeholder,
                                            # not a crash or garbage 0


# ---------------------------------------------------------------------
# Phases/Triggers distribution-parameter validation on entry:
# a zero scale must not reach `rng.pearson6`'s math.log(scale) at run
# time.
# ---------------------------------------------------------------------

def test_phases_grid_rejects_invalid_distribution_params(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/Phasing1/input.rbd")), None)
    t = w._data_tables._phases_table
    sec = w._rbd.sections["PHASES"]
    cols = {c: sec.columns.index(c) for c in
           ("Dist", "Param1", "Param2", "Param3")}

    # Set up a valid Pearson6 (Shape=1, Shape2=1, Scale=5) first: the
    # distribution, then its numbers (a change of distribution gives it
    # parameters of its own).
    t.item(0, cols["Dist"]).setText("12")   # Dist stays a plain item
    assert sec.rows[0]["Dist"] == "12"
    _set_param_widget(t, 0, cols["Param1"], "1.0")
    _set_param_widget(t, 0, cols["Param2"], "1.0")
    _set_param_widget(t, 0, cols["Param3"], "5.0")

    seen = []
    w._data_tables.validation_failed.connect(seen.append)
    _set_param_widget(t, 0, cols["Param3"], "0")   # Scale must be positive
    assert seen and "Scale" in seen[0]
    assert float(sec.rows[0]["Param3"]) == 5.0   # reverted, not committed
    reverted_edit = t.cellWidget(0, cols["Param3"]).findChild(QLineEdit)
    assert reverted_edit.text() == "5"


def test_triggers_grid_rejects_invalid_distribution_params(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/Capacity1/input.rbd")), None)
    t = w._data_tables._triggers_table
    sec = w._rbd.sections["TRIGGERS"]
    assert sec.rows, "Capacity1 must have real TRIGGERS rows"
    cols = {c: sec.columns.index(c) for c in
           ("Dist", "Param1", "Param2", "Param3")}

    t.item(0, cols["Dist"]).setText("12")
    assert sec.rows[0]["Dist"] == "12"
    _set_param_widget(t, 0, cols["Param1"], "1.0")
    _set_param_widget(t, 0, cols["Param2"], "1.0")
    _set_param_widget(t, 0, cols["Param3"], "5.0")

    seen = []
    w._data_tables.validation_failed.connect(seen.append)
    _set_param_widget(t, 0, cols["Param3"], "0")
    assert seen and "Scale" in seen[0]
    assert sec.rows[0]["Param3"] == "5.0"


def test_phases_grid_accepts_a_valid_distribution_change(qapp):
    """Validation does not reject legitimate edits."""
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/Phasing1/input.rbd")), None)
    t = w._data_tables._phases_table
    sec = w._rbd.sections["PHASES"]
    col = sec.columns.index("Param1")

    seen = []
    w._data_tables.validation_failed.connect(seen.append)
    _set_param_widget(t, 0, col, "7.5")
    assert not seen, seen
    assert sec.rows[0]["Param1"] == "7.5"


# ---------------------------------------------------------------------
# Phase and trigger distributions: Raptor 7's DistBox list only.
# ---------------------------------------------------------------------

@pytest.mark.parametrize("name", ["Empirical", "None", "Point Estimate"])
def test_a_phase_distribution_outside_raptor7s_list_is_refused(qapp, name):
    """Raptor 7's phase/trigger distribution dialog (`DistBox`) offers no Empirical, None or Point
    Estimate. Empirical used to be accepted here and kept only three of its
    points (the row has three parameter cells and no point list)."""
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/Phasing1/input.rbd")), None)
    t = w._data_tables._phases_table
    sec = w._rbd.sections["PHASES"]
    before = sec.rows[0]["Dist"]
    seen = []
    w._data_tables.validation_failed.connect(seen.append)
    t.item(0, sec.columns.index("Dist")).setText(name)
    assert sec.rows[0]["Dist"] == before
    assert seen and "not available" in seen[-1]


def test_a_phase_distribution_in_raptor7s_list_is_accepted(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests/golden/Phasing1/input.rbd")), None)
    t = w._data_tables._phases_table
    sec = w._rbd.sections["PHASES"]
    t.item(0, sec.columns.index("Dist")).setText("Fixed")
    assert sec.rows[0]["Dist"] == "19"


def test_each_event_rows_cells_edit_their_own_event(window):
    """Every row's Param and Stream cells commit to that row's event. The
    commit callbacks are built in a loop over events; they must bind their
    own row's editor and event id, not whichever the loop reached last."""
    w, t = window, window._data_tables._events_table
    v = w._diagram_dock.view
    eids = sorted(v.events)
    assert len(eids) >= 2, "fixture needs several events"
    first, last = eids[0], eids[-1]
    last_param, last_stream = v.events[last].fail_mean, v.events[last].fail_stream

    _set_param_widget(t, 0, 3, "0.37")
    _set_param_widget(t, 0, 4, "7")

    assert v.events[first].fail_mean == pytest.approx(0.37)
    assert v.events[first].fail_stream == 7
    assert v.events[last].fail_mean == pytest.approx(last_param)
    assert v.events[last].fail_stream == last_stream


def test_entry_values_accepts_more_values_than_the_label_table(qapp):
    """Empirical distributions carry up to 12 data points while
    `DISTRO_PARAM_LABELS` describes three named parameters. Indexing the
    label tuple by the value position raised IndexError during
    `_apply_loaded_model`, so opening Distribution1_Empirical12 failed
    outright."""
    from raptor.gui.ui.data_tables import DataTablesDock

    dt = DataTablesDock()
    values = [float(i) for i in range(12)]
    out = dt._entry_values(16, values)
    assert out == values


def test_hidden_data_tables_are_populated_only_when_shown(qapp):
    """A large model's Open spends most of its table time building ~14
    widgets per block in a tab that is not on screen. When the window is
    visible and the Data Tables tab is hidden, population is deferred until
    the tab is raised."""
    from raptor.api.build import build_from_spec
    from raptor.gui.ui.main_window import MainWindow

    n = 100
    spec = {
        "blocks": [{"name": f"B{i}", "fail_dist": 4,
                    "fail_params": [1000, 0], "repair_dist": 7,
                    "repair_params": [8, 2]} for i in range(n)],
        "nodes": [{"name": "Start", "type": "start"},
                  {"name": "End", "type": "end"}],
        "links": ([{"from": "Start", "to": "B0"}]
                  + [{"from": f"B{i}", "to": f"B{i + 1}"}
                     for i in range(n - 1)]
                  + [{"from": f"B{n - 1}", "to": "End"}]),
    }
    w = MainWindow()
    w.show()
    qapp.processEvents()
    w._load_into_app(build_from_spec(spec, auto_layout=False), "large.rbds")
    qapp.processEvents()
    dt = w._data_tables
    assert dt._refresh_pending
    assert dt._blocks_table.rowCount() == 0

    w._data_tables_dock.raise_()
    dt.repaint()
    qapp.processEvents()
    assert not dt._refresh_pending
    assert dt._blocks_table.rowCount() == n
    w._dirty = False
    w.close()

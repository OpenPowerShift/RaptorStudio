"""Data Tables: the Costs page (one row per block, 14 cost columns).

Every cost cell commits the WHOLE row through `DiagramView.set_block_costs`,
so the block and its BLOCK_DATA row move together and one edit is one undo
step. Values follow the Inspector's range and three-decimal precision;
non-finite/out-of-range text reverts. A Pooled block's spare and emergency
shipping costs are read-only, as in the Inspector.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Capacity1/input.rbd"      # Infinite, Custom and Pooled
SECOND = "tests/golden/CS_Probe/input.rbd"      # different block names/costs
WHOLE = "new_docs/models/DC2.rbds"              # Home holds no blocks


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _open_window(path, loader):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow

    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w._load_into_app(loader(path), path)
    w.resize(1400, 800)
    w.show()
    QApplication.processEvents()
    return w


@pytest.fixture
def win(qapp, monkeypatch):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import dialogs

    w = _open_window(MODEL, load_rbd)
    w._asked = []
    monkeypatch.setattr(
        dialogs, "ask_apply_to_rows",
        lambda parent, n: (w._asked.append(n) or (True, False)))
    yield w
    w._dirty = False
    w.close()


@pytest.fixture
def whole_win(qapp):
    from raptor.core.fileio.rbds import load_rbds

    w = _open_window(WHOLE, load_rbds)
    yield w
    w._dirty = False
    w.close()


def _cost_table(win):
    dt = win._data_tables
    t = dt._costs_table
    dt._tabs.setCurrentWidget(t)
    return dt, t, win._diagram_dock.view


def _bid(t, row):
    return int(t.item(row, 1).text())


def _row_of(t, bid):
    return next(r for r in range(t.rowCount()) if _bid(t, r) == bid)


def _col(name):
    from raptor.gui.model_edit import BLOCK_COST_FIELDS
    return 2 + [c for _a, c in BLOCK_COST_FIELDS].index(name)


def _select(table, rows):
    sm = table.selectionModel()
    sm.clearSelection()
    for r in rows:
        sm.select(table.model().index(r, 0),
                  QItemSelectionModel.SelectionFlag.Select
                  | QItemSelectionModel.SelectionFlag.Rows)


def _cur(table, row, col):
    table.selectionModel().setCurrentIndex(
        table.model().index(row, col),
        QItemSelectionModel.SelectionFlag.NoUpdate)


def _block_row(rbd, bid):
    return next(r for r in rbd.sections["BLOCK_DATA"].rows
                if int(float(r["ID"])) == bid)


def _large_spec(n: int) -> dict:
    """A simple series model with n blocks and two nodes."""
    return {
        "blocks": [{"name": f"X{i}", "fail_dist": 4,
                    "fail_params": [1000, 0], "repair_dist": 7,
                    "repair_params": [8, 2]} for i in range(n)],
        "nodes": [{"name": "Start", "type": "start"},
                  {"name": "End", "type": "end"}],
        "links": ([{"from": "Start", "to": "X0"}]
                  + [{"from": f"X{i}", "to": f"X{i + 1}"}
                     for i in range(n - 1)]
                  + [{"from": f"X{n - 1}", "to": "End"}]),
    }


def test_costs_page_has_the_block_and_cost_columns_and_every_block(win):
    from raptor.gui.model_edit import BLOCK_COST_FIELDS
    from raptor.gui.ui.table_export import RAW_VALUE_ROLE

    dt, t, view = _cost_table(win)
    assert dt._tabs.tabText(dt._tabs.indexOf(t)) == "Costs"
    headers = [t.horizontalHeaderItem(c).text()
               for c in range(t.columnCount())]
    # Literal, not derived from BLOCK_COST_FIELDS: a reorder of that tuple
    # must not silently move the displayed columns too.
    assert headers == [
        "Name", "ID", "Initial cost", "Running cost", "Standby cost",
        "Idle cost", "Repair fixed cost", "Repair cost per time",
        "Repair hold cost per time", "PM fixed cost", "PM cost per time",
        "PM hold cost per time", "Done fixed cost", "Done cost per time",
        "Spare cost", "Emergency shipping cost", "Level"]
    assert t.rowCount() == len(view.blocks)
    assert {_bid(t, r) for r in range(t.rowCount())} == set(view.blocks)
    assert t.isColumnHidden(t.columnCount() - 1)   # not Whole model
    for r in range(t.rowCount()):
        block = view.blocks[_bid(t, r)]
        for c in (0, 1):
            assert not (t.item(r, c).flags() & Qt.ItemFlag.ItemIsEditable)
        for (attr, _c), col in zip(BLOCK_COST_FIELDS, range(2, 16)):
            assert t.item(r, col).text() == f"{getattr(block, attr):.3f}"
            assert t.item(r, col).data(RAW_VALUE_ROLE) == pytest.approx(
                getattr(block, attr))


def test_an_edit_leaves_the_other_cost_columns_byte_for_byte(win, qapp):
    """The costs page must not rewrite all 14 BLOCK_DATA columns: a long
    decimal and a blank cost in the same row survive an unrelated edit."""
    _dt, t, view = _cost_table(win)
    row = 0
    bid = _bid(t, row)
    data = _block_row(view._rbd_ref, bid)
    data["standby"] = "0.123456789"
    data["idle"] = ""
    col = _col("initial")
    old = view.blocks[bid].initial_cost

    t.item(row, col).setText(f"{old + 1:g}")
    qapp.processEvents()

    data = _block_row(view._rbd_ref, bid)
    assert data["standby"] == "0.123456789"
    assert data["idle"] == ""
    assert float(data["initial"]) == pytest.approx(old + 1)


def test_undo_restores_the_exact_old_cost_text(win):
    """A saved cost with more than six decimals comes back verbatim on
    undo, not rounded through the all-columns `_f6` writer."""
    _dt, t, view = _cost_table(win)
    row = 0
    bid = _bid(t, row)
    col = _col("initial")
    data = _block_row(view._rbd_ref, bid)
    data["initial"] = "1.23456789"
    view.blocks[bid].initial_cost = 1.23456789
    _dt._set_cost_text(t.item(row, col), 1.23456789)

    t.item(row, col).setText("2.5")
    assert _block_row(view._rbd_ref, bid)["initial"] == "2.500000"

    view._stack.undo()
    assert _block_row(view._rbd_ref, bid)["initial"] == "1.23456789"
    assert view.blocks[bid].initial_cost == pytest.approx(1.23456789)


def test_a_cell_commit_updates_block_and_file_in_one_undo_step(win, qapp):
    _dt, t, view = _cost_table(win)
    row = 0
    bid = _bid(t, row)
    col = _col("Running")
    old = view.blocks[bid].operating_cost
    depth = view._stack.count()

    t.item(row, col).setText("12.5")
    qapp.processEvents()

    assert view.blocks[bid].operating_cost == pytest.approx(12.5)
    assert float(_block_row(view._rbd_ref, bid)["Running"]) == pytest.approx(
        12.5)
    assert t.item(row, col).text() == "12.500"
    assert view._stack.count() == depth + 1
    assert win._dirty

    view._stack.undo()
    assert view.blocks[bid].operating_cost == pytest.approx(old)
    assert float(_block_row(view._rbd_ref, bid)["Running"]) == pytest.approx(
        old)


@pytest.mark.parametrize("text", ["nan", "inf", "-inf", "1e13", "-1e13",
                                  "not a number", ""])
def test_invalid_cost_text_reverts(win, text):
    _dt, t, view = _cost_table(win)
    row = 0
    bid = _bid(t, row)
    col = _col("initial")
    item = t.item(row, col)
    old = view.blocks[bid].initial_cost
    depth = view._stack.count()

    item.setText(text)

    assert t.item(row, col).text() == f"{old:.3f}"
    assert view.blocks[bid].initial_cost == pytest.approx(old)
    assert view._stack.count() == depth
    assert float(_block_row(view._rbd_ref, bid)["initial"]) == pytest.approx(old)


def test_pooled_blocks_spare_costs_are_read_only(win):
    from raptor.core.model import SparingType

    dt, t, view = _cost_table(win)
    pooled = [b for b in view.blocks.values()
              if b.sparing_type == SparingType.SPARE_POOL]
    assert pooled
    for block in pooled:
        row = _row_of(t, block.id)
        for name, before in (("perSpare", block.spare_cost),
                             ("emerShipping", block.emergency_shipping_cost)):
            item = t.item(row, _col(name))
            assert not (item.flags() & Qt.ItemFlag.ItemIsEditable)
            assert item.toolTip() == dt._POOLED_COST_TIP
            item.setText("99")           # even programmatically refused
            assert getattr(block, "spare_cost" if name == "perSpare"
                           else "emergency_shipping_cost") == pytest.approx(
                               before)
            assert t.item(row, _col(name)).text() == f"{before:.3f}"

    normal = next(b for b in view.blocks.values()
                  if b.sparing_type != SparingType.SPARE_POOL)
    row = _row_of(t, normal.id)
    for name in ("perSpare", "emerShipping"):
        assert t.item(row, _col(name)).flags() & Qt.ItemFlag.ItemIsEditable


def test_paste_fills_the_selected_rows_in_one_undo_step(win, qapp):
    dt, t, view = _cost_table(win)
    _select(t, [0, 1, 2])
    ids = [_bid(t, r) for r in range(3)]
    col = _col("Running")
    _cur(t, 0, col)
    before_running = {i: view.blocks[i].operating_cost for i in ids}
    before_initial = {i: view.blocks[i].initial_cost for i in ids}
    depth = view._stack.count()

    assert dt.paste_into_selection("7.5")
    qapp.processEvents()

    assert all(view.blocks[i].operating_cost == pytest.approx(7.5)
               for i in ids)
    # The other 13 values of every target row stay as they were: only the
    # edited column is set.
    assert all(view.blocks[i].initial_cost == pytest.approx(before_initial[i])
               for i in ids)
    assert view._stack.count() == depth + 1
    assert win._asked == [3]

    view._stack.undo()
    assert all(view.blocks[i].operating_cost == pytest.approx(
        before_running[i]) for i in ids)


def test_paste_still_fills_when_the_first_selected_row_already_matches(
        win, qapp):
    """The fill path must not stop at an equal first target: the other
    selected rows still need the pasted value."""
    dt, t, view = _cost_table(win)
    _select(t, [0, 1, 2])
    ids = [_bid(t, r) for r in range(3)]
    col = _col("Running")
    _cur(t, 0, col)
    text = t.item(0, col).text()          # exactly what row 0 shows now
    before = {i: view.blocks[i].operating_cost for i in ids}
    assert len({round(v, 3) for v in before.values()}) > 1
    depth = view._stack.count()

    assert dt.paste_into_selection(text)
    qapp.processEvents()

    assert all(view.blocks[i].operating_cost == pytest.approx(float(text))
               for i in ids)
    assert view._stack.count() == depth + 1
    assert win._asked and win._asked[-1] >= 1


def test_ctrl_d_skips_a_read_only_pooled_source_row(win):
    """A read-only Pooled cell must not lend its value to the editable
    rows: Ctrl+D takes the first EDITABLE selected row as the source."""
    from raptor.core.model import SparingType

    dt, t, view = _cost_table(win)
    col = _col("perSpare")
    pooled = next(b for b in view.blocks.values()
                  if b.sparing_type == SparingType.SPARE_POOL)
    normal = [b for b in view.blocks.values()
              if b.sparing_type != SparingType.SPARE_POOL]
    assert len(normal) >= 2
    a, b = normal[0], normal[1]
    assert a.spare_cost != b.spare_cost
    _select(t, [_row_of(t, pooled.id), _row_of(t, a.id),
                _row_of(t, b.id)])
    _cur(t, _row_of(t, pooled.id), col)

    assert dt.fill_down()
    assert view.blocks[a.id].spare_cost == pytest.approx(a.spare_cost)
    assert view.blocks[b.id].spare_cost == pytest.approx(a.spare_cost)
    assert view.blocks[pooled.id].spare_cost == pytest.approx(pooled.spare_cost)


def test_multi_row_spare_edits_skip_pooled_rows(win, qapp):
    from raptor.core.model import SparingType

    dt, t, view = _cost_table(win)
    pooled = next(b for b in view.blocks.values()
                  if b.sparing_type == SparingType.SPARE_POOL)
    normal = [b for b in view.blocks.values()
              if b.sparing_type != SparingType.SPARE_POOL][:2]
    rows = [_row_of(t, pooled.id)] + [_row_of(t, b.id) for b in normal]
    for name, attr in (("perSpare", "spare_cost"),
                       ("emerShipping", "emergency_shipping_cost")):
        col = _col(name)
        _select(t, rows)
        _cur(t, rows[-1], col)
        before_pool = getattr(pooled, attr)
        depth = view._stack.count()

        assert dt.paste_into_selection("4321")
        qapp.processEvents()

        assert getattr(view.blocks[pooled.id], attr) == pytest.approx(
            before_pool)
        assert all(getattr(view.blocks[b.id], attr) == pytest.approx(4321)
                   for b in normal)
        assert view._stack.count() == depth + 1
        assert "1 pooled row takes it from its pool" in (
            win._statusbar.currentMessage())


def test_ctrl_d_fills_down_across_the_selected_rows(win):
    dt, t, view = _cost_table(win)
    col = _col("Running")
    _select(t, [0])
    t.item(0, col).setText("8.5")
    assert view.blocks[_bid(t, 0)].operating_cost == pytest.approx(8.5)

    _select(t, [0, 1, 2])
    _cur(t, 0, col)
    assert dt.fill_down()
    assert all(view.blocks[_bid(t, r)].operating_cost == pytest.approx(8.5)
               for r in range(3))


def _tick_whole(win, qapp):
    dt = win._data_tables
    dt._tabs.setCurrentWidget(dt._costs_table)
    dt._whole_box.setChecked(True)
    qapp.processEvents()
    t = dt._costs_table
    assert t.rowCount() > 0
    return dt, t, win._diagram_dock.view


def test_whole_model_cost_rows_are_read_only_and_look_it(whole_win, qapp):
    dt, t, _view = _tick_whole(whole_win, qapp)
    assert t.rowCount() == 14
    assert not t.isColumnHidden(t.columnCount() - 1)
    grey = QColor(Qt.GlobalColor.gray)
    for r in range(t.rowCount()):
        for c in range(t.columnCount()):
            item = t.item(r, c)
            assert item is not None
            assert not (item.flags() & Qt.ItemFlag.ItemIsEditable)
            assert item.foreground().color() == grey
            assert dt._FOREIGN_TIP in item.toolTip()
        before = t.item(r, _col("Running")).text()
        t.item(r, _col("Running")).setText("99")
        assert t.item(r, _col("Running")).text() == before


def test_whole_model_live_rows_stay_editable_after_entering_a_level(
        whole_win, qapp):
    dt, t, view = _tick_whole(whole_win, qapp)
    # Toggle the cache off and on, then change the live level: the cached
    # whole-model rows must be rebuilt for the new live id set.
    dt._whole_box.setChecked(False)
    dt._whole_box.setChecked(True)
    hid = next(iter(view._hier_items))
    view.enter_level(hid)
    qapp.processEvents()
    dt.refresh(force=True)
    t = dt._costs_table
    live = set(view.blocks)
    assert live
    editable = {_bid(t, r) for r in range(t.rowCount())
                if t.item(r, _col("Running")).flags()
                & Qt.ItemFlag.ItemIsEditable}
    assert editable == live
    assert t.rowCount() == 14


def test_whole_model_level_names_follow_a_hierarchy_rename(whole_win, qapp):
    dt, t, view = _tick_whole(whole_win, qapp)
    hid = next(iter(view._hier_items))
    view.rename_hier(hid, "RenamedLevel")
    qapp.processEvents()
    dt.refresh(force=True)
    t = dt._costs_table
    levels = {t.item(r, t.columnCount() - 1).text()
              for r in range(t.rowCount())}
    assert any("RenamedLevel" in level for level in levels)


def test_whole_model_cost_context_menu_goes_to_the_level(whole_win, qapp):
    dt, t, _view = _tick_whole(whole_win, qapp)
    seen = []
    dt.element_locate_id_requested.connect(
        lambda kind, oid: seen.append((kind, oid)))
    pos = t.visualRect(t.model().index(0, 0)).center()
    menu = dt._row_menu(t, "block", pos)
    assert menu.actions()[0].text().startswith("Go to level: ")
    menu.actions()[0].trigger()
    assert seen and seen[0][0] == "block"


def test_empty_costs_tab_says_which_level_has_no_blocks(whole_win, qapp):
    dt = whole_win._data_tables
    dt._tabs.setCurrentWidget(dt._costs_table)
    qapp.processEvents()
    t = dt._costs_table
    assert t.rowCount() == 0                 # Home has no blocks
    label = dt._empty_labels[t]
    assert label.text().startswith("No blocks on this level")


def test_a_second_model_never_reuses_the_first_models_cost_rows(win, qapp):
    from raptor.core.fileio.rbd import load_rbd

    dt = win._data_tables
    _tick_whole(win, qapp)                   # cache the first model
    second = load_rbd(SECOND)
    win._load_into_app(second, SECOND)
    qapp.processEvents()
    dt.refresh(force=True)
    dt._tabs.setCurrentWidget(dt._costs_table)
    t = dt._costs_table
    view = win._diagram_dock.view
    rows = [r for r in second.sections["BLOCK_DATA"].rows
            if str(r.get("ID", "")).strip() not in ("0", "0.0", "0.000000")]
    expected = {int(float(r["ID"])) for r in rows}
    assert {_bid(t, r) for r in range(t.rowCount())} == expected
    assert {t.item(r, 0).text() for r in range(t.rowCount())} == {
        r["blockName"] for r in rows}

    # Editing a LIVE row must hit the second model's block, not a stale
    # row with the same id from the first model.
    bid = next(iter(view.blocks))
    row = _row_of(t, bid)
    old = view.blocks[bid].operating_cost
    changed = old + 1.25
    t.item(row, _col("Running")).setText(f"{changed:g}")
    qapp.processEvents()
    assert view.blocks[bid].operating_cost == pytest.approx(changed)


def test_whole_model_population_checks_its_cache_once(qapp, monkeypatch):
    """149 rows must not call `_WholeModel.matches` 149 times: the cache
    is validated once per refresh and shared by the populate loop."""
    from raptor.api.build import build_from_spec
    from raptor.gui.ui.data_tables import DataTablesDock, _WholeModel
    from raptor.gui.ui.main_window import MainWindow

    n = 500
    spec = _large_spec(n)
    w = MainWindow()
    w.show()
    qapp.processEvents()
    w._load_into_app(build_from_spec(spec, auto_layout=False), "many.rbds")
    qapp.processEvents()
    dt = w._data_tables
    dt._tabs.setCurrentWidget(dt._costs_table)
    calls = []
    src_calls = []
    original = _WholeModel.matches
    original_src = DataTablesDock._src
    monkeypatch.setattr(
        _WholeModel, "matches",
        lambda self, view: (calls.append(1) or original(self, view)))
    monkeypatch.setattr(
        DataTablesDock, "_src",
        lambda self: (src_calls.append(1) or original_src(self)))
    dt._whole_box.setChecked(True)
    qapp.processEvents()
    assert dt._costs_table.rowCount() == n
    assert len(calls) <= 2
    assert len(src_calls) <= 5
    w._dirty = False
    w.close()


def test_deferred_second_model_drops_the_old_editable_rows(win, qapp):
    """The deferred-fill path returns before refresh; the old model's
    editable rows must already be gone, not point at the new model."""
    from raptor.api.build import build_from_spec
    from raptor.gui.ui.diagram import DiagramView

    dt = win._data_tables
    assert dt._costs_table.rowCount() > 0
    n = 410
    spec = _large_spec(n)
    second = DiagramView()
    second.load_rbd(build_from_spec(spec, auto_layout=False))
    qapp.processEvents()

    dt.set_view(second, defer_large=True)
    assert dt._refresh_pending
    for table in dt._all_tables:
        assert table.rowCount() == 0

    dt._tabs.setCurrentWidget(dt._costs_table)
    dt.refresh(force=True)
    qapp.processEvents()
    assert dt._costs_table.rowCount() == n


def test_real_open_clears_hidden_tables_before_the_deferred_fill(win, qapp):
    """The real Open path reuses the view and has already replaced its
    _rbd_ref; the dock must still recognise the model change and drop the
    old rows before returning to the event loop."""
    from raptor.api.build import build_from_spec

    dt = win._data_tables
    assert dt._costs_table.rowCount() > 0
    win._data_tables_dock.hide()
    qapp.processEvents()

    win._load_into_app(build_from_spec(_large_spec(410), auto_layout=False),
                       "large.rbds")
    # Deliberately no processEvents here: this is the gap before first paint.
    assert dt._refresh_pending
    for table in dt._all_tables:
        assert table.rowCount() == 0

    dt._tabs.setCurrentWidget(dt._costs_table)
    dt.refresh(force=True)


def test_whole_model_population_is_silent(qapp, monkeypatch):
    """Locking foreign rows must not re-enter the Costs commit handler."""
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import settings
    from raptor.gui.ui.data_tables import DataTablesDock
    from raptor.gui.ui.main_window import MainWindow

    calls = []
    original = DataTablesDock._on_cost_cell_changed
    monkeypatch.setattr(
        DataTablesDock, "_on_cost_cell_changed",
        lambda self, item: (calls.append(item) or original(self, item)))
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w._load_into_app(load_rbds(WHOLE), WHOLE)
    w.show()
    qapp.processEvents()
    dt = w._data_tables
    dt._tabs.setCurrentWidget(dt._costs_table)
    status = []
    invalid = []
    dt.status_message.connect(status.append)
    dt.validation_failed.connect(invalid.append)

    calls.clear()
    dt._whole_box.setChecked(True)
    qapp.processEvents()

    t = dt._costs_table
    assert t.rowCount() == 14                   # every level is foreign here
    for r in range(t.rowCount()):
        for c in range(t.columnCount()):
            assert not (t.item(r, c).flags() & Qt.ItemFlag.ItemIsEditable)
    assert calls == []
    assert status == []
    assert invalid == []
    w._dirty = False
    w.close()


def test_csv_filter_and_sort_use_the_displayed_rows(win, qapp):
    dt, t, view = _cost_table(win)
    bar = dt._filter_bars[t]
    assert bar.available()
    bar.set_shown(True)
    col = _col("Running")
    bar._editors[col].setText(">200")
    QTest.qWait(bar._timer.interval() + 80)
    shown, total = bar.rows_shown_of_total()
    expected = sum(1 for b in view.blocks.values() if b.operating_cost > 200)
    assert (shown, total) == (expected, len(view.blocks))

    from raptor.gui.ui.table_export import table_to_grid
    headers, rows = table_to_grid(t)
    assert headers == [t.horizontalHeaderItem(c).text()
                       for c in range(t.columnCount())
                       if not t.isColumnHidden(c)]
    assert len(rows) == shown
    first_bid = int(rows[0][1])
    assert rows[0][col] == str(float(view.blocks[first_bid].operating_cost))
    bar.clear()

    t.sortItems(col, Qt.SortOrder.DescendingOrder)
    qapp.processEvents()
    values = [float(t.item(r, col).text()) for r in range(t.rowCount())]
    assert values == sorted(values, reverse=True)


def test_editing_after_a_sort_updates_the_row_the_user_sees(win, qapp):
    _dt, t, view = _cost_table(win)
    col = _col("Running")
    t.sortItems(col, Qt.SortOrder.DescendingOrder)
    qapp.processEvents()
    row = 1
    bid = _bid(t, row)
    before_initial = view.blocks[bid].initial_cost
    new = view.blocks[bid].operating_cost + 0.5

    t.item(row, col).setText(f"{new:g}")
    qapp.processEvents()

    assert view.blocks[bid].operating_cost == pytest.approx(new)
    assert view.blocks[bid].initial_cost == pytest.approx(before_initial)


def test_filter_and_export_use_the_full_precision_cost(win, qapp):
    """Display is the Inspector's three decimals; filter and CSV read the
    exact stored value, so a cost between two displayed values matches."""
    from raptor.gui.model_edit import BLOCK_COST_FIELDS
    from raptor.gui.ui.table_export import RAW_VALUE_ROLE, table_to_grid

    dt, t, view = _cost_table(win)
    row = 0
    bid = _bid(t, row)
    idx = [c for _a, c in BLOCK_COST_FIELDS].index("Running")
    values = [getattr(view.blocks[bid], a) for a, _c in BLOCK_COST_FIELDS]
    values[idx] = 0.123456789
    view.set_block_costs(bid, values, index=idx)
    dt.refresh(force=True)
    t = dt._costs_table
    row = _row_of(t, bid)
    item = t.item(row, _col("Running"))
    assert item.text() == "0.123"
    assert item.data(RAW_VALUE_ROLE) == pytest.approx(0.123456789)

    bar = dt._filter_bars[t]
    bar.set_shown(True)
    col = _col("Running")
    bar._editors[col].setText(">0.1231")
    QTest.qWait(bar._timer.interval() + 80)
    shown, total = bar.rows_shown_of_total()
    assert (shown, total) == (total, len(view.blocks))
    assert not t.isRowHidden(row)

    _headers, rows = table_to_grid(t)
    exported = next(r for r in rows if int(r[1]) == bid)
    assert exported[col] == "0.123456789"
    bar.clear()


def test_cost_policy_is_shared_with_the_inspector(qapp):
    from PySide6.QtWidgets import QDoubleSpinBox

    from raptor.core.fileio.rbd import load_rbd
    from raptor.core.model import SparingType
    from raptor.gui.model_edit import (
        BLOCK_COST_DECIMALS, BLOCK_COST_FIELDS, BLOCK_COST_LIMIT,
        POOLED_COST_TIP, cost_field_readonly)
    from raptor.gui.ui.data_tables import DataTablesDock
    from raptor.gui.ui.diagram import DiagramView
    from raptor.gui.ui.inspector import InspectorDock

    view = DiagramView()
    view.load_rbd(load_rbd(MODEL))
    pooled = next(b for b in view.blocks.values()
                  if b.sparing_type == SparingType.SPARE_POOL)
    dock = InspectorDock()
    dock.set_rbd(view._rbd_ref)
    dock._populate_block(pooled)
    qapp.processEvents()
    spins = dock._scroll_costs.widget().findChildren(QDoubleSpinBox)
    assert len(spins) == len(BLOCK_COST_FIELDS)
    assert all(sp.minimum() == -BLOCK_COST_LIMIT
               and sp.maximum() == BLOCK_COST_LIMIT for sp in spins)
    assert all(sp.decimals() == BLOCK_COST_DECIMALS for sp in spins)
    assert not spins[-2].isEnabled() and not spins[-1].isEnabled()
    assert spins[-2].toolTip() == spins[-1].toolTip() == POOLED_COST_TIP
    assert cost_field_readonly(pooled, "spare_cost")
    assert cost_field_readonly(pooled, "emergency_shipping_cost")
    assert not cost_field_readonly(pooled, "initial_cost")
    assert DataTablesDock._COST_LIMIT == BLOCK_COST_LIMIT
    assert DataTablesDock._COST_DECIMALS == BLOCK_COST_DECIMALS

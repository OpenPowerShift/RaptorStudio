"""Large data grids build cell widgets for the rows on screen only (#146).

A distribution combo or entry field costs about a millisecond and a Blocks row
has nine, so building them for every row of a few-thousand-block model froze
the app for minutes. Items stay for every row; widgets, their hover text and
their accessible names come per row as it scrolls into view."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from raptor.api.build import build_from_spec

pytestmark = pytest.mark.gui

N = 150          # more than data_tables._LAZY_ROWS


def _model(n: int = N):
    blocks = [{"name": f"B{i:03d}"} for i in range(n)]
    links = ([{"from": "Start", "to": "B000"}]
             + [{"from": f"B{i:03d}", "to": f"B{i + 1:03d}"}
                for i in range(n - 1)]
             + [{"from": f"B{n - 1:03d}", "to": "End"}])
    return build_from_spec({
        "name": "Big", "blocks": blocks,
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": links, "system": {"term_condition": 100, "num_runs": 1}},
        auto_layout=True)


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w.resize(1500, 900)
    w.show()
    w._load_into_app(_model(), None)
    QApplication.processEvents()
    w._data_tables.refresh(force=True)
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _built(table, col: int = 2) -> list[int]:
    return [r for r in range(table.rowCount())
            if table.cellWidget(r, col) is not None]


def test_only_the_rows_on_screen_get_their_widgets(win):
    t = win._data_tables._blocks_table
    assert t.rowCount() == N
    built = _built(t)
    assert 0 < len(built) < N // 2
    assert 0 in built and N - 1 not in built
    # Every row still shows its name, id, counts and statistics.
    for r in (0, N // 2, N - 1):
        for col in (0, 1, 6, 11, 13):
            assert t.item(r, col) is not None


def test_scrolling_builds_the_rows_that_come_into_view(win):
    t = win._data_tables._blocks_table
    assert t.cellWidget(N - 1, 2) is None
    bar = t.verticalScrollBar()
    bar.setValue(bar.maximum())
    QApplication.processEvents()
    assert t.cellWidget(N - 1, 2) is not None
    assert t.cellWidget(N - 1, 3) is not None


def test_a_row_built_on_scroll_is_complete_and_editable(win):
    t = win._data_tables._blocks_table
    t.verticalScrollBar().setValue(t.verticalScrollBar().maximum())
    QApplication.processEvents()
    row = N - 1
    bid = int(t.item(row, 1).text())
    # Hover text and accessible names come with the widgets.
    assert t.item(row, 0).text() in t.cellWidget(row, 3).toolTip()
    edit = t.cellWidget(row, 3).findChild(QLineEdit)
    assert t.item(row, 0).text() in edit.accessibleName()
    edit.setText("123")
    edit.editingFinished.emit()
    assert win._diagram_dock.view.blocks[bid].fail_values[0] == 123


def test_export_builds_every_row_first_and_matches_an_eager_build(
        win, monkeypatch):
    from raptor.gui.ui import data_tables
    from raptor.gui.ui.table_export import table_to_grid
    t = win._data_tables._blocks_table
    assert len(_built(t)) < N
    lazy = table_to_grid(t, raw=True, cell_widgets=True)
    assert len(_built(t)) == N
    assert all(cell for row in lazy[1] for cell in row[:6])

    monkeypatch.setattr(data_tables, "_LAZY_ROWS", 10 ** 9)
    win._data_tables.refresh(force=True)
    assert len(_built(t)) == N
    eager = table_to_grid(t, raw=True, cell_widgets=True)
    assert lazy == eager


def test_sorting_keeps_each_widget_with_its_row(win):
    t = win._data_tables._blocks_table
    t.sortItems(0, Qt.SortOrder.DescendingOrder)
    QApplication.processEvents()
    QApplication.processEvents()
    built = _built(t)
    assert built and 0 in built
    for r in built[:10]:
        name = t.item(r, 0).text()
        edit = t.cellWidget(r, 3).findChild(QLineEdit)
        assert name in edit.accessibleName()
    assert t.item(0, 0).text() == f"B{N - 1:03d}"


def test_the_sparing_grid_is_on_demand_too(win):
    t = win._data_tables._sparing_table
    win._data_tables._tabs.setCurrentWidget(t)      # as the user opens it
    win._data_tables._populate_sparing()
    QApplication.processEvents()
    built = _built(t)
    assert 0 < len(built) < N // 2
    bar = t.verticalScrollBar()
    bar.setValue(bar.maximum())
    QApplication.processEvents()
    assert t.cellWidget(N - 1, 2) is not None


def test_a_small_model_still_builds_everything_at_once():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w.show()
    try:
        w._load_into_app(_model(20), None)
        w._data_tables.refresh(force=True)
        t = w._data_tables._blocks_table
        assert _built(t) == list(range(20))
    finally:
        w._dirty = False
        w.close()


def test_a_selective_filter_builds_only_the_widgets_of_the_rows_it_shows():
    """A filter that leaves few or no rows showing must not realize the
    hidden ones (rowAt answers -1 then, which once meant "everything")."""
    import pytest as _pytest
    _pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QTableWidget
    from raptor.gui.ui import data_tables as dt
    QApplication.instance() or QApplication([])
    dock = dt.DataTablesDock()
    t = QTableWidget(500, 3)
    for r in range(500):
        for c in range(3):
            t.setItem(r, c, dt.QTableWidgetItem(str(r)))
    dock._lazy_pending[t] = set(range(500))
    built = []
    dock._lazy_builders[t] = lambda tb, r, key: built.append(r)
    dock._row_key = staticmethod(lambda tb, r: r)
    for r in range(500):
        t.setRowHidden(r, r not in (7, 300))
    dock._realize_visible(t)
    assert sorted(built) == [7, 300]
    built.clear()
    for r in range(500):
        t.setRowHidden(r, True)
    dock._realize_visible(t)
    assert built == []

"""Data Tables: select several rows, edit one cell, change them all."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Phasing3/input.rbd"


@pytest.fixture
def win(monkeypatch):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui import dialogs, settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    w.resize(1400, 800)
    w.show()
    QApplication.processEvents()
    w._asked = []
    monkeypatch.setattr(dialogs, "ask_apply_to_rows",
                        lambda parent, n: (w._asked.append(n) or (True, False)))
    yield w
    w._dirty = False
    w.close()


def _select(table, rows):
    sm = table.selectionModel()
    sm.clearSelection()
    for r in rows:
        sm.select(table.model().index(r, 0),
                  QItemSelectionModel.SelectionFlag.Select
                  | QItemSelectionModel.SelectionFlag.Rows)


def _cur(t, row, col):
    """Make a cell current without touching the selection (clicking into a
    cell's editor does the same; `setCurrentCell` would reselect)."""
    t.selectionModel().setCurrentIndex(
        t.model().index(row, col), QItemSelectionModel.SelectionFlag.NoUpdate)


def _bid(t, r):
    return int(t.item(r, 1).text())


def _type(t, row, col, text):
    _cur(t, row, col)
    edit = t.cellWidget(row, col).findChild(QLineEdit)
    edit.setText(text)
    edit.editingFinished.emit()


def _blocks(win):
    dt = win._data_tables
    t = dt._blocks_table
    dt._realize_all(t)
    return dt, t, win._diagram_dock.view


def test_editing_a_selected_row_changes_every_selected_row_in_one_step(win):
    _dt, t, view = _blocks(win)
    _select(t, [0, 1, 2])
    ids = [_bid(t, r) for r in range(3)]
    before = {i: list(view.blocks[i].fail_values) for i in view.blocks}
    n = view._stack.count()
    _type(t, 0, 3, "4321")                 # Failure Param 1
    for i in ids:
        assert view.blocks[i].fail_values[0] == 4321.0
    untouched = [i for i in view.blocks if i not in ids]
    assert all(view.blocks[i].fail_values == before[i] for i in untouched)
    assert view._stack.count() == n + 1     # ONE undo step
    view._stack.undo()
    assert all(list(view.blocks[i].fail_values) == before[i] for i in ids)
    view._stack.redo()
    assert all(view.blocks[i].fail_values[0] == 4321.0 for i in ids)
    assert "on 3 rows" in win._statusbar.currentMessage()


def test_an_unselected_row_edits_alone(win):
    _dt, t, view = _blocks(win)
    _select(t, [1, 2])
    _type(t, 0, 3, "777")                  # row 0 is not in the selection
    assert view.blocks[_bid(t, 0)].fail_values[0] == 777.0
    assert view.blocks[_bid(t, 1)].fail_values[0] != 777.0


def test_one_selected_row_is_a_single_edit(win):
    _dt, t, view = _blocks(win)
    _select(t, [1])
    _type(t, 1, 3, "555")
    assert view.blocks[_bid(t, 1)].fail_values[0] == 555.0
    assert win._asked == []


def test_a_refused_multi_edit_reverts_every_row(win, monkeypatch):
    from raptor.gui.ui import msgbox
    shown = []
    monkeypatch.setattr(msgbox, "warning", lambda *a, **k: shown.append(a))
    dt, t, view = _blocks(win)
    _select(t, [0, 1, 2])
    ids = [_bid(t, r) for r in range(3)]
    before = {i: view.blocks[i].pm_freq for i in ids}
    n = view._stack.count()
    # a PM frequency the input rules refuse (they need > 0 when PM is off)
    dt._apply_rows(ids[0], "PM Frequency", "-1",
                   lambda k: view.set_block_pm_freq(k, -1.0) or True)
    QApplication.processEvents()
    assert shown                                    # the gate spoke once
    assert view._stack.count() == n                 # nothing kept
    assert {i: view.blocks[i].pm_freq for i in ids} == before


def test_filter_limits_the_rows_changed(win):
    _dt, t, view = _blocks(win)
    _select(t, [0, 1, 2])
    t.setRowHidden(1, True)                        # filtered out
    hidden = _bid(t, 1)
    before = view.blocks[hidden].fail_values[0]
    _type(t, 0, 3, "999")
    assert view.blocks[_bid(t, 0)].fail_values[0] == 999.0
    assert view.blocks[_bid(t, 2)].fail_values[0] == 999.0
    assert view.blocks[hidden].fail_values[0] == before


def test_distribution_change_applies_to_every_selected_row(win):
    _dt, t, view = _blocks(win)
    _select(t, [0, 1])
    ids = [_bid(t, 0), _bid(t, 1)]
    combo = t.cellWidget(0, 2)
    assert isinstance(combo, QComboBox)
    now = {view.blocks[i].fail_distro for i in ids}
    target = next(i for i in range(combo.count())
                  if combo.itemData(i) not in now
                  and combo.itemText(i).lower().startswith("gamma"))
    combo.setCurrentIndex(target)
    codes = {view.blocks[i].fail_distro for i in ids}
    assert len(codes) == 1 and codes.isdisjoint(now)
    assert "Failure Dist" in win._statusbar.currentMessage()


def test_a_parameter_a_distribution_lacks_is_skipped_and_reported(win):
    dt, t, view = _blocks(win)
    _select(t, [0, 1])
    a, b = _bid(t, 0), _bid(t, 1)
    view.change_block_dist(a, "F", 4)             # Exponential: 2 params
    view.change_block_dist(b, "F", 15)            # Weibull: 3 params
    dt.refresh(force=True)
    QApplication.processEvents()
    t = dt._blocks_table
    dt._realize_all(t)
    rows = {_bid(t, r): r for r in range(t.rowCount())}
    _select(t, [rows[a], rows[b]])
    before = view.blocks[a].fail_values[2]
    _type(t, rows[b], 5, "12")                    # Failure Param 3
    assert view.blocks[b].fail_values[2] == 12.0
    assert view.blocks[a].fail_values[2] == before
    assert "without that parameter skipped" in win._statusbar.currentMessage()


def test_the_confirmation_is_asked_once_when_told_not_to_ask_again(
        win, monkeypatch):
    from raptor.gui.ui import dialogs
    asked = []
    monkeypatch.setattr(dialogs, "ask_apply_to_rows",
                        lambda parent, n: (asked.append(n) or (True, True)))
    dt, t, _view = _blocks(win)
    _select(t, [0, 1, 2])
    _type(t, 0, 3, "11")
    _select(t, [0, 1, 2])
    _type(t, 0, 3, "12")
    assert asked == [3]
    assert dt._multi_confirmed


def test_declining_the_confirmation_changes_nothing(win, monkeypatch):
    from raptor.gui.ui import dialogs
    monkeypatch.setattr(dialogs, "ask_apply_to_rows", lambda p, n: (False, False))
    _dt, t, view = _blocks(win)
    _select(t, [0, 1])
    before = view.blocks[_bid(t, 0)].fail_values[0]
    n = view._stack.count()
    _type(t, 0, 3, "31337")
    assert view._stack.count() == n
    assert view.blocks[_bid(t, 0)].fail_values[0] == before


def test_ctrl_d_fills_down(win):
    dt, t, view = _blocks(win)
    ids = [_bid(t, r) for r in range(3)]
    _type(t, 0, 3, "808")                          # one row, one edit
    _select(t, [0, 1, 2])
    _cur(t, 0, 3)
    assert dt.fill_down()
    assert [view.blocks[i].fail_values[0] for i in ids] == [808.0] * 3


def test_pasting_one_value_fills_the_selected_rows(win):
    dt, t, view = _blocks(win)
    _select(t, [0, 1, 2])
    _cur(t, 0, 3)
    assert dt.paste_into_selection("606")
    assert all(view.blocks[_bid(t, r)].fail_values[0] == 606.0
               for r in range(3))
    assert not dt.paste_into_selection("a\nb")     # not a single value


def test_banner_counts_the_selected_rows(win):
    dt, t, _view = _blocks(win)
    _select(t, [0])
    assert not dt._banner.isVisible() and dt._banner.text() == ""
    _select(t, [0, 1, 2, 3])
    QApplication.processEvents()
    assert dt._banner.text() == (
        "4 rows selected. Editing a cell will change all 4.")
    assert not dt._banner.isHidden()
    dt.grab().save("/tmp/claude-1000/-home-mulhollandd-Documents-raptor-play/"
                   "6b447a50-9509-483d-9fb4-caf03d0e3767/scratchpad/banner.png")


def test_read_only_columns_are_never_multi_edited(win):
    _dt, t, view = _blocks(win)
    _select(t, [0, 1])
    name_before = view.blocks[_bid(t, 1)].name
    t.item(0, 0).setText("Same")                   # a name is per row
    assert view.blocks[_bid(t, 1)].name == name_before


def test_the_window_paste_offers_the_focused_grid_its_value_first(monkeypatch):
    """Ctrl+V reaches the window's Paste command, not the grid's key handler:
    a focused Data Tables grid must get the clipboard text before the canvas."""
    from PySide6.QtWidgets import QApplication
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    win = MainWindow()
    got = []
    grid = win._data_tables._blocks_table
    grid.paste_text = lambda text: got.append(text) or True
    canvas_pastes = []
    monkeypatch.setattr(win._diagram_dock.view, "paste",
                        lambda *a, **k: canvas_pastes.append(1))
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: grid))
    QApplication.clipboard().setText("42")
    win._edit_paste()
    assert got == ["42"] and canvas_pastes == []
    grid.paste_text = lambda text: False          # the grid declines
    win._edit_paste()
    assert canvas_pastes == [1]
    win.close()

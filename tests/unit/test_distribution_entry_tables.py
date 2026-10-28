"""Distribution entry through the Data Tables grids, Block Defaults and Mass
Edit: an invalid value is refused or reported, a valid one lands, and nothing
invalid reaches the BLOCK_DATA / PHASES row or the typed object."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QDialog, QLineEdit)

from raptor.core.distro_rules import validate_distro_params

pytestmark = pytest.mark.gui

AIRLINER = "new_docs/models/AirlinerMission.rbds"


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win():
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbds(AIRLINER), AIRLINER)
    yield w
    w._dirty = False
    w.close()


def _row(win, bid):
    return next(r for r in win._rbd.sections["BLOCK_DATA"].rows
                if r.get("ID") == str(bid))


def _row_pair(row, p="F"):
    return (int(row[f"{p}Dist"]),
            [float(row[f"{p}Param{i}"]) for i in (1, 2, 3)])


def _both_valid(win, bid, p="F"):
    b = win._diagram_dock.view.blocks[bid]
    code, vals = (b.fail_distro, b.fail_values) if p == "F" else (
        b.repair_distro, b.repair_values)
    typed = validate_distro_params(code, (list(vals) + [0.0] * 3)[:3], "x")
    code_r, vals_r = _row_pair(_row(win, bid), p)
    rowv = validate_distro_params(code_r, vals_r, "x")
    return typed, rowv


def _grid_row(t, bid):
    return next(r for r in range(t.rowCount()) if int(t.item(r, 1).text()) == bid)


def _grid_commit(t, r, c, text):
    e = t.cellWidget(r, c).findChild(QLineEdit)
    e.setText(text)
    e.editingFinished.emit()


# --------------------------------------------------------------------------
# Data Tables: Blocks grid
# --------------------------------------------------------------------------

def test_blocks_grid_param_values_valid_land_invalid_refused(win):
    v = win._diagram_dock.view
    dt = win._data_tables
    bid = min(v.blocks)
    v.set_block_dist(bid, "F", 3)
    v.set_block_fail_params(bid, [4.0, 0.5, 0.0])
    dt.refresh()
    t = dt._blocks_table
    r = _grid_row(t, bid)
    for text in ("nan", "inf", "abc", "1.5", "-0.2", "0"):
        _grid_commit(t, r, 4, text)                       # Probability
        assert v.blocks[bid].fail_values[1] == 0.5, text
        assert _row_pair(_row(win, bid))[1][1] == pytest.approx(0.5), text
    for text in ("0.25", "2.5", "0", "-1", "nan"):
        _grid_commit(t, r, 3, text)                       # N Trials
        assert v.blocks[bid].fail_values[0] == 4.0 if text != "0.25" else True
    assert v.blocks[bid].fail_values[0] == 4.0
    _grid_commit(t, r, 4, "0.4")
    _grid_commit(t, r, 3, "6")
    assert v.blocks[bid].fail_values[:2] == [6.0, pytest.approx(0.4)]
    assert _both_valid(win, bid) == (None, None)


@pytest.mark.parametrize("which,col", [("F", 2), ("R", 7)])
@pytest.mark.parametrize("new", [3, 2, 14, 10, 15])
def test_blocks_grid_distribution_change_leaves_a_valid_pair(win, which, col, new):
    """Changing the combo must not leave the old numbers as an invalid set
    (Exponential(100) -> Binomial read 100 as N Trials with Probability 0)."""
    v = win._diagram_dock.view
    dt = win._data_tables
    bid = min(v.blocks)
    v.set_block_dist(bid, which, 19)
    (v.set_block_fail_params if which == "F" else v.set_block_repair_params)(
        bid, [0.25, 0.0, 0.0])
    dt.refresh()
    t = dt._blocks_table
    combo = t.cellWidget(_grid_row(t, bid), col)
    combo.setCurrentIndex(combo.findData(new))
    typed, rowv = _both_valid(win, bid, which)
    assert (typed, rowv) == (None, None), (new, typed, rowv)


# --------------------------------------------------------------------------
# Data Tables: Phases grid (keyed)
# --------------------------------------------------------------------------

def _phases_table(dt):
    return next(t for t, (sec, _k, _c) in dt._keyed.items() if sec == "PHASES")


def _phase(win, name="Taxi_Out"):
    return next(r for r in win._rbd.sections["PHASES"].rows
                if r["PhaseName"] == name)


def _phase_pair(win, name="Taxi_Out"):
    ph = _phase(win, name)
    return int(ph["Dist"]), [float(ph[f"Param{i}"]) for i in (1, 2, 3)]


def test_phases_grid_bad_param_is_refused_and_the_row_stays_valid(win):
    dt = win._data_tables
    dt.refresh()
    t = _phases_table(dt)
    sec = win._rbd.sections["PHASES"]
    c = sec.columns.index("Param1")
    r = next(i for i, row in enumerate(sec.rows) if row["PhaseName"] == "Taxi_Out")
    before = _phase_pair(win)
    for text in ("nan", "-3", "abc"):
        w = t.cellWidget(r, c)
        if w is not None:
            e = w.findChild(QLineEdit)
            e.setText(text)
            e.editingFinished.emit()
        else:
            t.item(r, c).setText(text)
        assert _phase_pair(win) == before, text
    code, vals = _phase_pair(win)
    assert validate_distro_params(code, vals, "x") is None


def test_phases_grid_fixed_to_binomial_can_be_entered(win):
    dt = win._data_tables
    dt.refresh()
    t = _phases_table(dt)
    sec = win._rbd.sections["PHASES"]
    c = sec.columns.index("Dist")
    r = next(i for i, row in enumerate(sec.rows) if row["PhaseName"] == "Taxi_Out")
    t.item(r, c).setText("Binomial")
    code, vals = _phase_pair(win)
    assert code == 3
    assert validate_distro_params(code, vals, "x") is None


# --------------------------------------------------------------------------
# Block Defaults and Mass Edit
# --------------------------------------------------------------------------

@pytest.fixture
def keep_settings():
    from raptor.gui.ui import settings
    saved = settings.get_dict("editing/block_defaults")
    yield
    settings.set_value("editing/block_defaults", saved)


def _defaults_dialog(monkeypatch, steps):
    from raptor.gui.ui import dialogs

    def fake_exec(dlg):
        combos = [c for c in dlg.findChildren(QComboBox)
                  if c.findData(3) >= 0 and c.findData(15) >= 0]
        spins = dlg.findChildren(QDoubleSpinBox)
        steps(dlg, combos, spins)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs, "exec_dialog", fake_exec)


def test_block_defaults_valid_binomial_lands(win, monkeypatch, keep_settings):
    from raptor.gui.ui import dialogs

    def steps(dlg, combos, spins):
        combos[0].setCurrentIndex(combos[0].findData(3))
        vis = [s for s in spins if s.isVisibleTo(dlg)][:2]
        vis[0].setValue(5.0)
        vis[1].setValue(0.4)

    _defaults_dialog(monkeypatch, steps)
    dialogs.edit_block_defaults(win)
    v = win._diagram_dock.view
    assert v.default_block_fdist == 3
    assert validate_distro_params(3, v.default_block_fparams, "x") is None


def test_block_defaults_invalid_binomial_is_refused(win, monkeypatch, keep_settings):
    from raptor.gui.ui import dialogs

    def steps(dlg, combos, spins):
        combos[0].setCurrentIndex(combos[0].findData(3))
        vis = [s for s in spins if s.isVisibleTo(dlg)][:2]
        vis[0].setValue(2.5)
        vis[1].setValue(1.5)

    _defaults_dialog(monkeypatch, steps)
    dialogs.edit_block_defaults(win)
    v = win._diagram_dock.view
    assert not (v.default_block_fdist == 3 and validate_distro_params(
        3, v.default_block_fparams, "x") is not None)


def _select_two(win):
    v = win._diagram_dock.view
    bids = sorted(v.blocks)[:2]
    for b in bids:
        v._block_items[b].setSelected(True)
    return v, bids


def _mass_edit(monkeypatch, steps):
    from raptor.gui.ui import dialogs

    def fake_exec(dlg):
        box = next(b for b in dlg.findChildren(QCheckBox)
                   if b.text() == "Failure distribution:")
        combo = next(c for c in dlg.findChildren(QComboBox)
                     if c.findData(1) >= 0 and c.findData(15) >= 0)
        box.setChecked(True)
        spins = [s for s in dlg.findChildren(QDoubleSpinBox)]
        steps(dlg, combo, spins)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs, "exec_dialog", fake_exec)


def test_mass_edit_valid_binomial_lands_on_every_block(win, monkeypatch):
    from raptor.gui.ui import dialogs
    v, bids = _select_two(win)

    def steps(dlg, combo, spins):
        combo.setCurrentIndex(combo.findData(3))
        vis = [s for s in spins if s.isVisibleTo(dlg)][:2]
        vis[0].setValue(5.0)
        vis[1].setValue(0.4)

    _mass_edit(monkeypatch, steps)
    dialogs.on_mass_edit(win)
    for b in bids:
        assert v.blocks[b].fail_distro == 3
        assert _both_valid(win, b) == (None, None)


def test_mass_edit_invalid_binomial_does_not_reach_the_model(win, monkeypatch):
    from raptor.gui.ui import dialogs
    _v, bids = _select_two(win)

    def steps(dlg, combo, spins):
        combo.setCurrentIndex(combo.findData(3))
        vis = [s for s in spins if s.isVisibleTo(dlg)][:2]
        vis[0].setValue(2.5)
        vis[1].setValue(1.5)

    _mass_edit(monkeypatch, steps)
    dialogs.on_mass_edit(win)
    for b in bids:
        assert _both_valid(win, b) == (None, None), b

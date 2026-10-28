"""Raptor's lambda / mu-sigma entry preferences, across every entry surface.

The model always stores canonical values. The preference changes what the
widgets show and accept: an Exponential's first parameter as a rate, a
Lognormal's two as mu/sigma. These tests drive the real Inspector, Data
Tables, Block Defaults and Mass Edit widgets.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("RAPTOR_INSPECTOR_EAGER_TABS", "1")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QLabel,
    QLineEdit,
)

from raptor.core.fileio.rbd import load_rbd  # noqa: E402
from raptor.gui.ui import dialogs, param_entry, settings  # noqa: E402
from raptor.gui.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.gui

GOLD = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "golden")
MODEL = os.path.join(GOLD, "Phasing3", "input.rbd")
BLOCK = 4          # Thermal: Exponential, mean 2435
MEAN = 2450.0      # avoid depending on the fixture's exact value


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _reset_modes():
    settings.remove("editing/exp_mode")
    settings.remove("editing/ln_mode")
    yield
    settings.remove("editing/exp_mode")
    settings.remove("editing/ln_mode")


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), None)
    v = w._diagram_dock.view
    v.set_block_dist(BLOCK, "F", 4)
    v.set_block_fail_params(BLOCK, [MEAN, 0.0, 0.0])
    yield w
    w._dirty = False
    w.close()


def _edits(w, prefix="Failure "):
    return [e for e in w._inspector.findChildren(QLineEdit)
            if e.accessibleName().startswith(prefix)]


def _inspector(w):
    w._inspector.show_block(BLOCK, w._diagram_dock.view.blocks)


# ---------------------------------------------------------------------------
# The pure conversion
# ---------------------------------------------------------------------------

def test_rate_mode_round_trips_and_keeps_untouched_means():
    settings.set_value("editing/exp_mode", "rate")
    assert param_entry.labels(4)[0] == "Rate"
    entry = param_entry.to_entry(4, [MEAN, 0.0, 0.0])
    assert entry[0] == pytest.approx(1.0 / MEAN)
    # Typing the displayed (rounded) rate back must not move the mean.
    assert param_entry.from_entry(4, [1.0 / MEAN, 0.0, 0.0],
                                  old=[MEAN, 0.0, 0.0])[0] == MEAN
    assert param_entry.from_entry(4, [0.002, 0.0, 0.0])[0] == 500.0


def test_mu_sigma_round_trips_to_mean_and_stdev():
    settings.set_value("editing/ln_mode", "mu_sigma")
    assert param_entry.labels(7)[:2] == ("Mu", "Sigma")
    entry = param_entry.to_entry(7, [100.0, 10.0, 0.0])
    back = param_entry.from_entry(7, entry)
    assert back[:2] == pytest.approx([100.0, 10.0], rel=1e-5)


# ---------------------------------------------------------------------------
# Inspector
# ---------------------------------------------------------------------------

def test_inspector_shows_and_accepts_a_rate(win):
    settings.set_value("editing/exp_mode", "rate")
    _inspector(win)
    edits = _edits(win)
    assert edits[0].accessibleName() == "Failure Rate (per hours)"
    assert float(edits[0].text()) == pytest.approx(1.0 / MEAN, rel=1e-5)

    # Editing Location (the other field) must keep the untouched mean.
    edits[1].setText("1")
    edits[1].editingFinished.emit()
    assert win._diagram_dock.view.blocks[BLOCK].fail_values[:2] == \
        pytest.approx([MEAN, 1.0])

    edits = _edits(win)
    edits[0].setText("0.002")
    edits[0].editingFinished.emit()
    assert win._diagram_dock.view.blocks[BLOCK].fail_values[0] == \
        pytest.approx(500.0)


def test_inspector_shows_mu_and_sigma(win):
    settings.set_value("editing/ln_mode", "mu_sigma")
    v = win._diagram_dock.view
    v.set_block_dist(BLOCK, "F", 7)
    v.set_block_fail_params(BLOCK, [100.0, 10.0, 0.0])
    _inspector(win)
    edits = _edits(win)
    assert [e.accessibleName() for e in edits[:2]] == [
        "Failure Mu (ln(hours))", "Failure Sigma (ln(hours))"]
    assert float(edits[0].text()) == pytest.approx(4.6002, rel=1e-4)


def test_generic_other_box_keeps_the_mean_in_rate_mode(win):
    """Raptor applies lambda mode to failure/repair laws only."""
    settings.set_value("editing/exp_mode", "rate")
    v = win._diagram_dock.view
    v.set_block_pm_enabled(BLOCK, True)
    v.set_block_pm_dist(BLOCK, 4)
    v.set_block_pm_params(BLOCK, [100.0, 0.0, 0.0])
    _inspector(win)
    edits = [e for e in win._inspector.findChildren(QLineEdit)
             if e.accessibleName().startswith("PM ")]
    assert edits[0].accessibleName() == "PM Mean (hours)"
    assert float(edits[0].text()) == pytest.approx(100.0)


def test_inspector_rejects_a_zero_rate(win):
    settings.set_value("editing/exp_mode", "rate")
    _inspector(win)
    edits = _edits(win)
    edits[0].setText("0")
    edits[0].editingFinished.emit()
    assert win._diagram_dock.view.blocks[BLOCK].fail_values[0] == \
        pytest.approx(MEAN)
    assert float(_edits(win)[0].text()) == pytest.approx(1.0 / MEAN,
                                                        rel=1e-5)


# ---------------------------------------------------------------------------
# Data Tables grid
# ---------------------------------------------------------------------------

def test_blocks_grid_shows_and_accepts_a_rate(win):
    settings.set_value("editing/exp_mode", "rate")
    dt = win._data_tables
    dt.refresh()
    t = dt._blocks_table
    row = next(r for r in range(t.rowCount())
               if int(t.item(r, 1).text()) == BLOCK)
    cell = t.cellWidget(row, 3).findChild(QLineEdit)
    assert float(cell.text()) == pytest.approx(1.0 / MEAN, rel=1e-5)

    cell.setText("0.002")
    cell.editingFinished.emit()
    assert win._diagram_dock.view.blocks[BLOCK].fail_values[0] == \
        pytest.approx(500.0)
    row_row = next(r for r in win._rbd.sections["BLOCK_DATA"].rows
                   if r.get("ID") == str(BLOCK))
    assert float(row_row["FParam1"]) == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# Block Defaults / Mass Edit
# ---------------------------------------------------------------------------

def _defaults_dialog(win, monkeypatch, probe):
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: probe(self) or QDialog.DialogCode.Accepted)
    dialogs.edit_block_defaults(win)


def test_block_defaults_take_a_rate(win, monkeypatch):
    settings.set_value("editing/exp_mode", "rate")
    settings.set_value("editing/block_defaults", {
        "fdist": 4, "fparams": [100.0, 0.0, 0.0],
        "rdist": 7, "rparams": [10.0, 2.0, 0.0], "sparing": "None"})

    def probe(dlg):
        spins = dlg.findChildren(QDoubleSpinBox)
        assert spins[0].value() == pytest.approx(0.01)
        spins[0].setValue(0.02)
    _defaults_dialog(win, monkeypatch, probe)
    assert win._block_defaults()["fparams"][0] == pytest.approx(50.0)


def test_mass_edit_shows_rate_and_applies_canonical_mean(win, monkeypatch):
    settings.set_value("editing/exp_mode", "rate")
    v = win._diagram_dock.view
    ids = sorted(b for b in v.blocks if b != 0)[:2]
    for bid in ids:
        v._block_items[bid].setSelected(True)

    def probe(dlg):
        combo = next(c for c in dlg.findChildren(QComboBox)
                     if c.count() >= 18)
        combo.setCurrentIndex(combo.findData(4))
        assert any(lbl.text() == "Rate:" for lbl in dlg.findChildren(QLabel))
        box = next(b for b in dlg.findChildren(QCheckBox)
                   if b.text() == "Failure distribution:")
        box.setChecked(True)
        spin = next(s for s in dlg.findChildren(QDoubleSpinBox)
                    if s.isVisibleTo(dlg))
        spin.setValue(0.02)

    monkeypatch.setattr(QDialog, "exec",
                        lambda self: probe(self) or QDialog.DialogCode.Accepted)
    dialogs.on_mass_edit(win)
    assert v.blocks[ids[0]].fail_values[0] == pytest.approx(50.0)

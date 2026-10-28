"""Results > Weak Link > Spread: Green and Red at the quartiles of the
run's per-block metric, so a highly available model's blocks are not all
the same colour. Also: changing the bands keeps the results, and the
colours really repaint."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "new_docs/models/HospitalPower.rbds"


@pytest.fixture
def win():
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("files/save_before_run", "never")
    w = MainWindow()
    w._load_into_app(load_rbds(MODEL), MODEL)
    w._set_exp_field("trials", 20)
    # 20 x 8,760 h is over the long-batch guard; this fixture is about the
    # overlay, not the confirmation dialog.
    w._confirm_long_batch = lambda n: True
    w._run_simulation()
    # Spread applies to the ACTIVE overlay; a run no longer turns one on.
    w._toggle_weak_colors()
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def test_spread_puts_the_bands_at_the_quartiles_and_keeps_results(win):
    assert win._weak_spread_action.isEnabled()
    win._weak_spread_action.trigger()
    QApplication.processEvents()
    gy, yr = win._gy_spin.value(), win._yr_spin.value()
    assert 0.99 < yr < gy <= 1.0
    assert win._results.has_results(), "a band change discarded the results"
    v = win._diagram_dock.view
    colours = {it._live_colour for it in v._block_items.values()}
    assert len(colours) >= 2, "Spread must separate the blocks"


def test_spread_is_one_undo_step(win):
    """Both threshold writes must undo together; one Ctrl+Z used to
    leave the bands half-changed."""
    stack = win._diagram_dock.view._stack
    before = stack.index()
    gy, yr = win._gy_spin.value(), win._yr_spin.value()
    win._weak_spread_action.trigger()
    QApplication.processEvents()
    assert stack.index() == before + 1
    stack.undo()
    QApplication.processEvents()
    assert win._gy_spin.value() == pytest.approx(gy)
    assert win._yr_spin.value() == pytest.approx(yr)
    row = win._rbd.sections["SYSTEM_DATA"].rows[0]
    assert float(row["GYthreshold"]) == pytest.approx(gy)
    assert float(row["YRthreshold"]) == pytest.approx(yr)


def test_colours_repaint_after_the_bands_change(win):
    """A stale remembered colour left blocks plain after the colouring was
    turned off and on (which every band change does)."""
    v = win._diagram_dock.view
    win._weak_spread_action.trigger()
    win._on_system_threshold_changed("GYthreshold", 0.999999)
    for item in v._block_items.values():
        if item._live_colour:
            assert item.brush().color().name() == item._live_colour


def test_counts_and_shares_read_sensibly():
    from raptor.gui.ui.results import _fmt_count, _pct
    assert _fmt_count(0.02) == "0.02"
    assert _fmt_count(0) == "0"
    assert _pct(0.01) == "under 0.1%"
    assert _pct(92.4) == "92.4%"


def test_capacity_overlay_explains_a_model_with_no_capacity_nodes():
    """HospitalPower's nodes are all unrestricted (infinite pipes): the
    overlay painted them 0 % green and said "highest utilization 0.0 %"."""
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("files/save_before_run", "never")
    w = MainWindow()
    try:
        w._load_into_app(load_rbds(MODEL), MODEL)
        exp = dict(w._current_experiment())
        w._set_exp_field("analyses", sorted(set(exp["analyses"]) | {"capacity"}))
        w._set_exp_field("trials", 5)
        w._run_simulation()
        btn = w._capacity_overlay_action
        btn.setChecked(True)
        QApplication.processEvents()
        assert not btn.isChecked()
        assert "No capacity nodes" in w._statusbar.currentMessage()
    finally:
        w._dirty = False
        w.close()


def test_spread_bands_cost_by_quartile_when_cost_is_showing():
    """With Cost showing, Spread bands the blocks by cost quartile: the
    dearest quarter Red, the cheapest quarter Green."""
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("files/save_before_run", "never")
    w = MainWindow()
    try:
        w._load_into_app(load_rbds(MODEL), MODEL)
        exp = dict(w._current_experiment())
        w._set_exp_field("analyses", sorted(set(exp["analyses"]) | {"cost"}))
        w._set_exp_field("trials", 5)
        w._run_simulation()
        assert w._cost_overlay_action.isEnabled()
        w._cost_overlay_action.setChecked(True)
        QApplication.processEvents()
        v = w._diagram_dock.view
        assert v._last_result_visuals["kind"] == "cost"
        w._weak_spread_action.trigger()
        QApplication.processEvents()
        assert v._last_result_visuals["kind"] == "cost"
        assert v._last_result_visuals["spread"] is True
        assert w._cost_overlay_action.isChecked()
        assert not w._weak_color_action.isChecked()
        tips = [it.toolTip() for it in v._block_items.values()]
        assert any("quartile" in t for t in tips)
        reds = sum("→ Red" in t for t in tips)
        greens = sum("→ Green" in t for t in tips)
        assert reds >= 1 and greens >= 1
    finally:
        w._dirty = False
        w.close()

"""The engine runs the model the user sees, edits included.

Canvas and Inspector edits live in the view's element objects until they
are written back to the file rows, which the engine reads. Nothing wrote
them back before a run, so the first run after an edit simulated the
previous model (found by the GUI test pass: a PM interval changed to 300
ran at 400 until the second press).
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "tests/golden/TwoBlockRedundancy/input.rbd"


@pytest.fixture
def win():
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    yield w
    w._dirty = False
    w.close()


def _edit_first_block(w) -> tuple[int, float]:
    v = w._diagram_dock.view
    bid = min(v.blocks)
    new = v.blocks[bid].fail_values[0] * 3 + 1
    v.set_block_fail_params(bid, [new] + list(v.blocks[bid].fail_values[1:]))
    return bid, new


def _row(rbd, bid):
    return next(r for r in rbd.sections["BLOCK_DATA"].rows
                if int(float(r["ID"])) == bid)


def test_the_first_run_after_an_edit_uses_the_edit(win, monkeypatch):
    bid, new = _edit_first_block(win)
    seen = {}

    def fake_run(rbd, *a, **k):
        seen["value"] = float(_row(rbd, bid)["FParam1"])
        raise RuntimeError("stop here")
    import raptor.gui.ui.main_window as mw
    monkeypatch.setattr(mw, "run_simulation_with_reports", fake_run)
    monkeypatch.setattr(win, "_maybe_save_before_run", lambda: True)
    monkeypatch.setattr(mw.msgbox, "critical", lambda *a, **k: None)
    win._run_simulation()
    assert seen["value"] == pytest.approx(new)


def test_a_step_session_starts_from_the_edit(win):
    bid, new = _edit_first_block(win)
    win._ensure_step_session()
    assert float(_row(win._rbd, bid)["FParam1"]) == pytest.approx(new)


def test_a_background_run_gets_its_own_copy(win):
    bid, new = _edit_first_block(win)
    copy = win._model_for_engine(copy=True)
    assert copy is not win._rbd
    assert float(_row(copy, bid)["FParam1"]) == pytest.approx(new)


def test_breakpoints_cover_every_level_and_follow_renames():
    """REGRESSION: the list held only the level on screen, so the data
    centre (every block inside a hierarchy) offered nothing to stop on; and
    a renamed block kept its old name, so its breakpoint never fired."""
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow()
    try:
        path = "new_docs/models/DC1.rbds"
        w._load_into_app(load_rbds(path), path)
        combo = w._breakpoint_combo
        labels = [combo.itemText(i) for i in range(combo.count())]
        assert any(t.endswith("/ Utility_A") for t in labels), labels
        i = next(i for i, t in enumerate(labels) if t.endswith("/ Utility_A"))
        combo.setCurrentIndex(i)
        bid = combo.currentData()
        v = w._diagram_dock.view
        v.enter_level(next(h for h, o in v.hiers.items() if o.name == "Power_sub"))
        v.rename_block(bid, "Grid_Feed")
        w._do_view_refresh()
        assert combo.currentData() == bid
        assert w._bp_name() == "Grid_Feed"
    finally:
        w._dirty = False
        w.close()


def test_changing_distribution_gives_valid_parameters_with_the_same_mean(win):
    """REGRESSION: Exponential(100) switched to Weibull kept [100, 0, 0]
    (shape 100, scale 0), so the user's first edit was refused for the
    scale they had not reached yet."""
    from raptor.core.engine.dependency import distro_mean
    v = win._diagram_dock.view
    bid = min(v.blocks)
    before = (v.blocks[bid].fail_distro, list(v.blocks[bid].fail_values))
    mean = distro_mean(*before)
    win._on_block_dist_type_changed(bid, "F", 15)          # Weibull
    b = v.blocks[bid]
    assert b.fail_distro == 15
    assert distro_mean(15, b.fail_values) == pytest.approx(mean)
    v._stack.undo()                                         # one step
    assert (v.blocks[bid].fail_distro, list(v.blocks[bid].fail_values)) == before


def test_running_straight_after_a_move_is_not_an_edit_during_the_run(win, monkeypatch):
    """REGRESSION: a move gesture (a drag, Auto Layout) owes one more
    'model changed' on a timer; pressing Run inside that window delivered
    it mid-run, which read as an edit and stopped the run."""
    armed = []
    monkeypatch.setattr(win, "_arm_edit_guard", lambda: armed.append(1))
    monkeypatch.setattr(win, "_maybe_save_before_run", lambda: True)
    win._diagram_dock.view.auto_layout()
    win._set_exp_field("trials", 5)
    win._run_simulation()
    assert not armed
    assert "Completed 5 run(s)" in win._statusbar.currentMessage()


def test_an_inspector_edit_survives_changing_hierarchy_level(tmp_path):
    """Failure/repair parameters, PM frequency and node fields used to
    live only on the canvas's objects until Save; entering and leaving a
    hierarchy re-read the model and silently dropped them."""
    import os
    from PySide6.QtWidgets import QApplication
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    m = "new_docs/models/HospitalPower.rbds"
    before = os.path.getmtime(m)
    w = MainWindow()
    try:
        w._load_into_app(load_rbds(m), m)
        v = w._diagram_dock.view
        v.set_block_fail_params(1, [1000, 0, 0])
        v.set_block_pm_freq(1, 999.0)
        v.enter_level(100)
        v.go_up()
        assert v.blocks[1].fail_values[0] == 1000
        assert v.blocks[1].pm_freq == 999.0
        row = next(r for r in w._model_for_engine().section("BLOCK_DATA").rows
                   if r["ID"] == "1")
        assert float(row["FParam1"]) == 1000
        assert os.path.getmtime(m) == before, "nothing is saved to disk"
    finally:
        w._dirty = False
        w.close()

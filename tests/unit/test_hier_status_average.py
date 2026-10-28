"""A hierarchy's status dot shows the average over every run (a4c3730a).

Before, the dot showed how the LAST trial ended -- nearly always "all up".
`weak_metric.hier_time_shares` is the pure part; `MainWindow._apply_hier_status`
bands it by the ribbon's G/Y and Y/R thresholds and hands it to the canvas.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core.engine.weak_metric import hier_time_shares

MODEL = "new_docs/models/WaterTreatment.rbds"


# -- the pure part ---------------------------------------------------------

def _st(**kw):
    base = dict(green=0.0, yellow=0.0, red=0.0, blue=0.0, brown=0.0,
                orange=0.0)
    base.update(kw)
    return base


def test_shares_add_to_one_and_availability_is_working_degraded_standby():
    out = hier_time_shares([{1: _st(green=50, yellow=20, blue=10, red=10,
                                    brown=5, orange=5)}])[1]
    assert out["working"] == pytest.approx(0.5)
    assert out["degraded"] == pytest.approx(0.2)
    assert out["standby"] == pytest.approx(0.1)
    assert out["down"] == pytest.approx(0.2)      # red + brown + orange
    four = out["working"] + out["degraded"] + out["standby"] + out["down"]
    assert four == pytest.approx(1.0)
    assert out["availability"] == pytest.approx(0.8)
    assert out["availability"] == pytest.approx(
        out["working"] + out["degraded"] + out["standby"])
    assert out["runs"] == 1


def test_it_is_the_mean_of_ratios_not_a_ratio_of_sums():
    runs = [{1: _st(green=10)},                    # always working
            {1: _st(red=100)}]                     # always down, 10x longer
    out = hier_time_shares(runs)[1]
    assert out["availability"] == pytest.approx(0.5)   # not 10/110
    assert out["working"] == pytest.approx(0.5)
    assert out["down"] == pytest.approx(0.5)
    assert out["runs"] == 2


def test_a_run_with_no_time_is_skipped_not_counted_as_zero():
    runs = [{1: _st()},                            # zero total
            {1: _st(green=3, red=1)},
            {}, None]
    out = hier_time_shares(runs)[1]
    assert out["runs"] == 1
    assert out["availability"] == pytest.approx(0.75)


def test_a_hierarchy_with_only_empty_runs_is_absent_and_hierarchies_are_separate():
    out = hier_time_shares([{1: _st(), 2: _st(green=1)},
                            {2: _st(red=1)}])
    assert 1 not in out
    assert out[2]["availability"] == pytest.approx(0.5)
    assert hier_time_shares([]) == {}


def test_missing_buckets_count_as_zero():
    out = hier_time_shares([{1: {"green": 1.0, "red": 1.0}}])[1]
    assert out["availability"] == pytest.approx(0.5)


# -- the window ------------------------------------------------------------



def _wait(pred, seconds=60):
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while not pred() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert pred()


@pytest.fixture
def win(monkeypatch):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui import cores
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(cores, "chosen", lambda: 1)
    w = MainWindow()
    w._load_into_app(load_rbds(MODEL), MODEL)
    w._live_sim_action.setChecked(False)
    w._set_exp_field("trials", 4)
    yield w
    w._dirty = False
    w.close()


def _run(w, analyses):
    w._set_exp_field("analyses", analyses)
    w._dirty = False                               # no save-first prompt
    w._on_run()
    _wait(lambda: not w._run_in_progress)
    assert w._results.has_results()


def _items(w):
    items = w._diagram_dock.view._hier_items
    assert len(items) == 4, "WaterTreatment has four hierarchies"
    return items


def _band(avail, gy, yr):
    return "AllUp" if avail >= gy else "Degraded" if avail >= yr else "Down"


@pytest.mark.gui
def test_the_dot_shows_only_with_weak_link_data(win):
    _run(win, [])
    for it in _items(win).values():
        assert not it._dsi_dot.isVisible()
        assert it._dsi_info is None
        assert it.toolTip() == "" and it._dsi_dot.toolTip() == ""


@pytest.mark.gui
def test_the_dot_is_the_banded_average_over_every_run(win):
    _run(win, ["weak"])
    gy, yr = win._gy_spin.value(), win._yr_spin.value()
    for it in _items(win).values():
        info = it._dsi_info
        assert it._dsi_dot.isVisible()
        assert info["kind"] == "average" and info["runs"] == 4
        total = (info["working"] + info["degraded"] + info["standby"]
                 + info["down"])
        assert total == pytest.approx(1.0)
        assert info["state"] == _band(info["availability"], gy, yr)
        # the tooltip is on the dot itself and on the item, and says "runs"
        tip = it._dsi_dot.toolTip()
        assert tip == it.toolTip()
        assert tip.startswith(
            "Descendant Status Indicator (DSI): over 4 runs")


@pytest.mark.gui
def test_changing_a_threshold_rebands_the_dots_without_a_new_run(win):
    _run(win, ["weak"])
    avail = {h: it._dsi_info["availability"] for h, it in _items(win).items()}
    # Green only at a perfect 1.0: every hierarchy that was ever down or
    # partly failed is no longer AllUp.
    win._on_system_threshold_changed("GYthreshold", 1.0)
    win._on_system_threshold_changed("YRthreshold", 0.0)
    for h, it in _items(win).items():
        want = "AllUp" if avail[h] >= 1.0 else "Degraded"
        assert it._dsi_info["state"] == want
        assert it._dsi_info["availability"] == pytest.approx(avail[h])
    # Yellow only at 0.9999 (Raptor needs Y/R below G/Y): anything short of
    # it is red.
    win._on_system_threshold_changed("YRthreshold", 0.9999)
    for h, it in _items(win).items():
        want = ("AllUp" if avail[h] >= 1.0 else
                "Degraded" if avail[h] >= 0.9999 else "Down")
        assert it._dsi_info["state"] == want
    # And back: loose bands make everything green.
    win._on_system_threshold_changed("YRthreshold", 0.0)
    win._on_system_threshold_changed("GYthreshold", 0.10)
    for it in _items(win).values():
        assert it._dsi_info["state"] == "AllUp"


@pytest.mark.gui
def test_apply_hier_status_with_synthetic_runs_and_without(win):
    ids = list(_items(win))
    hid = ids[0]
    runs = [SimpleNamespace(weak_hiers={hid: _st(green=90, red=10)}),
            SimpleNamespace(weak_hiers={hid: _st(green=70, red=30)}),
            SimpleNamespace(weak_hiers={})]
    win._apply_hier_status(runs)
    it = _items(win)[hid]
    assert it._dsi_info["availability"] == pytest.approx(0.8)
    assert it._dsi_info["runs"] == 2
    assert it._dsi_info["state"] == "Down"        # 0.8 is below both bands
    # (default thresholds G/Y 0.95, Y/R 0.9)
    for other in ids[1:]:
        assert not _items(win)[other]._dsi_dot.isVisible()
    # no weak data at all clears the dot
    win._apply_hier_status([SimpleNamespace(weak_hiers={})])
    assert not it._dsi_dot.isVisible()
    win._apply_hier_status(None)
    assert not it._dsi_dot.isVisible()


@pytest.mark.gui
def test_live_info_still_works_and_reads_status_dot(win):
    it = next(iter(_items(win).values()))
    it.set_dsi({"state": "Degraded", "nodes": 4, "down": 1, "degraded": 2})
    assert it._dsi_dot.isVisible()
    tip = it._dsi_dot.toolTip()
    assert tip == it.toolTip()
    assert tip.startswith("Descendant Status Indicator (DSI): this hierarchy "
                          "and the hierarchies inside it are up; at least "
                          "one is degraded")
    assert "(1/4 nodes down, 2 degraded)" in tip
    it.set_dsi({"state": "AllUp"})
    assert it._dsi_dot.toolTip() == (
        "Descendant Status Indicator (DSI): this hierarchy and the "
        "hierarchies inside it are up, and none is degraded.")
    it.set_dsi(None)
    assert not it._dsi_dot.isVisible()
    assert it._dsi_dot.toolTip() == "" and it.toolTip() == ""

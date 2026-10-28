"""The reported chain: an Empirical with its only point deleted was
accepted; the Issues list then showed the block's old name after a rename
and said it was "on another hierarchy level"; changing it to Binomial
crashed the Inspector (IndexError), and the app later segfaulted."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "new_docs/models/HospitalPower.rbds"


@pytest.fixture
def win():
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow()
    w._load_into_app(load_rbds(MODEL), MODEL)
    yield w
    w._dirty = False
    w.close()


def _empty_empirical(win, bid):
    v = win._diagram_dock.view
    v.set_block_dist(bid, "F", 16)
    v.set_block_fail_params(bid, [])
    return v


def test_an_empirical_with_no_points_is_an_issue(win):
    _empty_empirical(win, 1)
    issues = win._validation_issues()
    assert any("Empirical but has no data points" in i for i in issues), issues


def test_the_issue_shows_the_new_name_and_locates_the_block(win):
    v = _empty_empirical(win, 13)                # Main_Switchboard
    v.rename_block(13, "Board")
    issue = next(i for i in win._validation_issues() if "Empirical" in i)
    assert "'Board'" in issue
    v.enter_level(100)                           # somewhere else
    assert win._locate_issue(issue)
    assert v._current_parent == 0
    assert v._block_items[13].isSelected()


def test_changing_an_empty_empirical_to_binomial_does_not_crash(win):
    v = _empty_empirical(win, 1)
    win._on_block_dist_type_changed(1, "F", 3)   # Binomial
    b = v.blocks[1]
    assert b.fail_distro == 3 and len(b.fail_values) >= 2
    assert not any("Block 1 " in i for i in win._validation_issues()
                   if "fail" in i)


@pytest.mark.parametrize("code", [c for c in range(1, 23) if c not in (16, 18)])
def test_every_distribution_change_leaves_valid_parameters(win, code):
    from raptor.core.distro_rules import validate_distro_params
    v = _empty_empirical(win, 1)
    win._on_block_dist_type_changed(1, "F", code)
    vals = list(v.blocks[1].fail_values) + [0.0] * 3
    assert validate_distro_params(code, vals[:3], "failure") is None, code


def test_the_data_dialog_needs_a_point_and_deletes_the_one_selected(
        monkeypatch):
    from PySide6.QtWidgets import QListWidget, QPushButton
    from raptor.gui.ui import empirical_dialog as ed
    QApplication.instance() or QApplication([])
    seen = {}

    def fake_exec(dlg):
        buttons = {b.text(): b for b in dlg.findChildren(QPushButton)}
        lst = dlg.findChildren(QListWidget)[0]
        lst.setCurrentRow(2)            # 1234567.89, shown as 1.23457e+06
        buttons["Delete selected"].click()
        seen["after_one"] = lst.count()
        for _ in range(lst.count()):
            lst.setCurrentRow(0)
            buttons["Delete selected"].click()
        seen["ok_enabled_when_empty"] = buttons["OK"].isEnabled()
        return 0
    monkeypatch.setattr(ed, "exec_dialog", fake_exec)
    ed.open_empirical_dialog(None, [5.0, 1234567.89, 7.0])
    assert seen["after_one"] == 2
    assert seen["ok_enabled_when_empty"] is False


def test_the_run_panel_points_to_the_issues_instead_of_quoting_one(win):
    _empty_empirical(win, 1)
    win._update_readiness()
    text = win._run_panel._readiness.text()
    assert "1 problem" in text and "Show them" in text
    assert "Utility_Grid" not in text


def test_flow_sentinels_read_as_raptor_7_shows_them():
    """-2 showed as a bare "-2" in the node's Capacity table."""
    from raptor.gui.ui.inspector import InspectorDock
    f = InspectorDock._flow_cell_text
    assert f("-1", "MaxFlow", 3) == "MaxFlow"
    assert f("-2", "MaxFlow", 3) == "MaxFlow/3"
    assert f("-2", "FullFlow", 2) == "FullFlow/2"
    assert f("250", "FullFlow", 2) == "250"


def test_failure_effects_colours_the_failed_blocks_own_links(win):
    """Engine links are numbered 1..N in file order (as Raptor 7 does);
    the canvas knows them by file id. HospitalPower's ids start at 18, so
    failing ATS_A painted Start -> Supply_Split instead of ATS_A's links."""
    win._fev_view_action.trigger()
    QApplication.processEvents()
    v = win._diagram_dock.view
    red = {}
    win._on_fev_target_clicked(7, "click")          # ATS_B
    QApplication.processEvents()
    rows = {int(r["ID"]): r for r in win._rbd.section("LINK_DATA").rows}
    for item in v._links:
        if item.pen().color().name().lower() == "#ff0000":
            red[item._link_num] = rows[item._link_num]
    assert red, "nothing turned red"
    assert all("7" in (r["FromID"], r["ToID"]) and "Block" in
               (r["FromType"], r["ToType"]) for r in red.values()), red

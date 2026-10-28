"""Ch.11 Weak Link settings/selection + state-colour hover tooltips."""

from __future__ import annotations

import os
import pathlib

import pytest

from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication, QTableWidget

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.resources.design_tokens import APPENDIX_D_STATE_COLOURS
from raptor.gui.ui.results import ResultsDock

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_fmt_si_uses_engineering_notation_above_1000():
    """MTBDE/MDT/MTBM/MMT span from fractions of an hour to hundreds of
    thousands, so values of 1000 and above use engineering notation
    ("1.235M"). Sub-1000 stays plain, including sub-1 fractions
    ("0.5 hours" rather than "500m hours")."""
    from raptor.gui.ui.results import _fmt_si
    assert _fmt_si(None) == "N/A"
    assert _fmt_si(0) == "0"
    assert _fmt_si(0.5) == "0.5"
    assert _fmt_si(999) == "999"
    assert _fmt_si(1000) == "1k"
    assert _fmt_si(123456) == "123.5k"
    assert _fmt_si(1234567) == "1.235M"
    assert _fmt_si(1234567890) == "1.235G"


def test_band_respects_thresholds():
    assert ResultsDock._band(0.97, 0.95, 0.90)[1] == "Green"
    assert ResultsDock._band(0.93, 0.95, 0.90)[1] == "Yellow"
    assert ResultsDock._band(0.50, 0.95, 0.90)[1] == "Red"


def test_stats_columns():
    mean, mn, mx, sd, sem = ResultsDock._stats([0.9, 0.8, 0.7])
    assert mean == pytest.approx(0.8)
    assert (mn, mx) == (0.7, 0.9)
    assert sd == pytest.approx(0.1)
    assert sem == pytest.approx(0.1 / 3 ** 0.5)


def test_block_dependability_excludes_dependency_idle_time(qapp):
    """Manual Ch.11: dependability excludes the outside influences of
    standby (blues), preventive maintenance (oranges) and dependency
    (brown). A block's dependency-induced idle time (`BlockStatus.IDLE`,
    entered via dependency transitions) is dropped from both sides of the
    ratio, matching the node bucket below it. Exercises the pure
    bucket-math helper with hand-built per-run stats."""
    dock = ResultsDock()
    runs = [{1: {"name": "B1", "run": 80.0, "standby": 0.0,
                "idle": 15.0, "pm": 0.0, "pm_hold": 0.0,
                "repair": 5.0, "rep_hold": 0.0, "done": 0.0}}]

    avail_rows = dock._weak_metric_rows(
        runs, ("run", "standby"),
        ("idle", "pm", "pm_hold", "repair", "rep_hold", "done"))
    assert avail_rows[0][1][0] == pytest.approx(80.0 / 100.0)

    # Dependability: "idle" (dependency time) dropped from both sides,
    # not just left out of the up-bucket -- 80 / (80+5), not 80/(80+5+15).
    dep_rows = dock._weak_metric_rows(
        runs, ("run",), ("repair", "rep_hold", "done"))
    assert dep_rows[0][1][0] == pytest.approx(80.0 / 85.0)
    assert dep_rows[0][1][0] > avail_rows[0][1][0], \
        "excluding dependency downtime must raise dependability above availability here"


def test_the_canvas_colours_blocks_by_the_same_number_the_table_shows(qapp):
    """D-07: the canvas overlay and the Weak Link table computed the
    Dependability ratio from two different bucket sets, so the same block
    could be painted green in the table and red on the canvas.

    Before the fix `main_window` kept `idle` in the denominator
    (`PRINCIPAL_DOWN = ("idle", "repair", "rep_hold", "done")`) while
    `results.py` dropped it, per manual Ch.11. Both then fed
    `apply_weak_colors` with the same GY/YR thresholds.

    This compares the two views on one real run of one model with
    dependencies -- the only circumstance in which the two ever differ.
    """
    rbd = load_rbd(os.path.join(GOLD, "Ch07Dependent", "input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    rbd.sections["SYSTEM_DATA"].rows[0]["nodeAnalType"] = "Dependability"
    report_data: list = []
    run_simulation_with_reports(rbd, num_runs=2, date="fixed",
                                report_sink=report_data)
    runs = [d.weak_stats for d in report_data if d.weak_stats]
    assert runs, "fixture produced no weak-link data"

    # The tab's own numbers. The bucket CHOICE is pinned separately (and
    # structurally) by test_weak_metric.py's consumer checks; here the
    # buckets come from the shared owner and the assertion is the Ch.11
    # arithmetic, so a wrong denominator fails here even if the tab were
    # to revert.
    from raptor.core.engine.weak_metric import buckets
    tbup, tbdown = buckets("Dependability")
    dock = ResultsDock()
    table_rows = dict(dock._weak_metric_rows(runs, tbup, tbdown))
    # `_weak_metric_rows` returns ONE ratio per run; the number the tab
    # displays (and the canvas compares against) is their mean.
    per_block = {name: sum(vals) / len(vals)
                 for name, vals in table_rows.items() if vals}

    # Only blocks that actually accumulated dependency-idle time can tell
    # the two denominators apart; for the rest the ratios are equal by
    # construction and the comparison would prove nothing.
    candidates = [bid for bid in runs[0]
                  if any((ws.get(bid) or {}).get("idle", 0.0)
                         for ws in runs)]
    assert candidates, (
        "fixture has no dependency-idle time, so this test cannot "
        "distinguish the two denominators; pick a model with dependencies"
    )

    checked = 0
    for bid in candidates:
        # `_weak_metric_rows` keys its output by element NAME, not id.
        stats0 = runs[0][bid]
        name = stats0.get("name", str(bid))
        if name not in per_block:
            continue
        # Per-run ratios, averaged -- the same definition both views use
        # (and asserted in test_weak_metric.py). Pooling the buckets first
        # would give a slightly different number, so do not do that here.
        fresh, stale = [], []
        for ws in runs:
            stats = ws.get(bid)
            if stats is None:
                continue
            up = stats.get("run", 0.0)
            down = sum(stats.get(k, 0.0)
                       for k in ("repair", "rep_hold", "done"))
            idle = stats.get("idle", 0.0)
            fresh.append(up / (up + down) if (up + down) else 0.0)
            stale.append(up / (up + down + idle) if (up + down + idle) else 0.0)
        expected = sum(fresh) / len(fresh)
        old_value = sum(stale) / len(stale)
        checked += 1
        assert per_block[name] == pytest.approx(expected), (
            f"block {bid} ({name!r}): dependability counted dependency-idle "
            f"time. got {per_block[name]:.6f}, Ch.11 wants {expected:.6f} "
            f"-- the pre-fix canvas would have shown {old_value:.6f}"
        )
        assert old_value < per_block[name], (
            "fixture no longer discriminates: including idle would have "
            "given the same number"
        )
    assert checked, (
        f"none of the {len(candidates)} dependency-idle blocks appeared in "
        "the tab's output; the test proved nothing"
    )


def test_node_weak_stats_actually_accumulate():
    """`NodeStatistics` state times accumulate for nodes when the model has
    `weakAnalysis` enabled, so the node Availability/Dependability table
    shows real data rather than zeros. The Start node is excluded from
    tracking and stays all-zero."""
    rbd = load_rbd(os.path.join(GOLD, "Capacity1", "input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    report_data: list = []
    run_simulation_with_reports(rbd, num_runs=2, date="fixed",
                                report_sink=report_data)
    weak_nodes = report_data[0].weak_nodes
    assert weak_nodes, "weak_nodes must be populated when weakAnalysis=1"
    # Sum only the state-time keys: the dict also carries non-time
    # metadata (`m_completed`, `type_node`, `report_anal`), which a
    # blanket sum would fold in.
    time_keys = ("green", "yellow", "red", "blue", "brown", "orange")
    total = sum(d[k] for d in weak_nodes.values() for k in time_keys)
    assert total > 0, \
        "node state-time bookkeeping never accumulated anything"

    # The Start node is excluded (type_node==1, matching
    # change_node_state's `is_start_or_end_node` contract) and must stay
    # all-zero. This also guards the run-end flush, which closes every
    # other node's final segment but skips this one.
    start = next(d for d in weak_nodes.values() if d["name"] == "start")
    assert sum(start[k] for k in time_keys) == 0.0


def test_node_weak_stats_stay_sane_across_multiple_runs():
    """`reset_node_and_links_for_new_run` also resets
    `node.state_start_time`. Without it, a node's first transition in run
    N>1 subtracts the previous run's final timestamp, crediting a large
    negative duration to the run's starting state. Checks that every node
    state time across Phasing1's runs stays within
    [0, ending_sim_time]."""
    rbd = load_rbd(os.path.join(GOLD, "Phasing1", "input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    report_data: list = []
    run_simulation_with_reports(rbd, num_runs=3, date="fixed",
                                report_sink=report_data)
    assert len(report_data) == 3
    for i, d in enumerate(report_data):
        for _nid, states in d.weak_nodes.items():
            for key, val in states.items():
                if key == "name":
                    continue
                assert -1e-6 <= val <= d.ending_sim_time + 1e-6, (
                    f"run {i} node {states['name']!r} state {key!r} "
                    f"= {val} outside [0, {d.ending_sim_time}]")


def test_weak_tab_layout_and_selection_filter(qapp):
    rbd = load_rbd(os.path.join(GOLD, "Capacity1", "input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_selected_block_names_fn(lambda: {"unnamed"})
    dock.set_results(logs, eor, fin, report_data=report_data)
    idx = next(i for i in range(dock._details_tabs.count())
               if dock._details_tabs.tabText(i) == "Weak Link")
    page = dock._details_tabs.widget(idx)
    headers = [t.horizontalHeaderItem(c).text()
               for t in page.findChildren(QTableWidget)
               for c in range(t.columnCount())][:2]
    assert headers == ["Name", "Colour"]
    # ticking 'Selected elements only' rebuilds the tab filtered
    from PySide6.QtWidgets import QCheckBox
    chk = page.findChildren(QCheckBox)[0]
    chk.setChecked(True)
    idx = next(i for i in range(dock._details_tabs.count())
               if dock._details_tabs.tabText(i) == "Weak Link")
    table = dock._details_tabs.widget(idx).findChildren(QTableWidget)[0]
    names = {table.item(r, 0).text() for r in range(table.rowCount())}
    assert names <= {"unnamed"}, names


def test_node_state_breakdown_table_sums_to_100_percent(qapp):
    """Per-state %Green/%Yellow/%Red/%Blue/%Brown/%Orange breakdown for
    nodes (manual Ch.13 Fig 13.10 "State_Data"), the raw numbers behind
    the rolled-up availability/dependability figure. Depends on the
    node_weak_stats fix covered by
    `test_node_weak_stats_actually_accumulate`."""
    rbd = load_rbd(os.path.join(GOLD, "Capacity1", "input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data)
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=report_data)
    idx = next(i for i in range(dock._details_tabs.count())
              if dock._details_tabs.tabText(i) == "Weak Link")
    page = dock._details_tabs.widget(idx)
    tables = page.findChildren(QTableWidget)
    pct_table = tables[-1]
    assert [pct_table.horizontalHeaderItem(c).text()
           for c in range(pct_table.columnCount())] == [
        "Name", "% Green", "% Yellow", "% Red",
        "% Blue", "% Brown", "% Orange"]
    assert pct_table.rowCount() > 0
    saw_100 = False
    for r in range(pct_table.rowCount()):
        pcts = [float(pct_table.item(r, c).text())
                for c in range(1, pct_table.columnCount())]
        total = sum(pcts)
        # Every row is either all-zero (the Start node is deliberately
        # excluded from tracking, `change_node_state`'s own
        # `is_start_or_end_node` contract; a node that never actually
        # transitioned during this short 2-run simulation has no data
        # either -- not a bug) or sums to 100% (real tracked time).
        assert total == pytest.approx(0.0, abs=0.05) or \
            total == pytest.approx(100.0, abs=0.05), \
            (pct_table.item(r, 0).text(), pcts)
        saw_100 = saw_100 or total == pytest.approx(100.0, abs=0.05)
    assert saw_100, "no node showed real tracked state time -- the bug this covers"


def test_overview_card_matches_the_models_own_configured_metric(qapp):
    """The Overview card shows whichever metric the model reports on
    (`nodeAnalType`), not always Availability. Phasing3 is configured for
    Dependability, whose value genuinely differs from Availability in its
    Final Results report; the test reads both live and checks the card
    matches the configured one."""
    from raptor.core.fileio.typed import system_config_from_row
    rbd = load_rbd(os.path.join(GOLD, "Phasing3", "input.rbd"))
    cfg = system_config_from_row(rbd.sections["SYSTEM_DATA"].rows[0])
    assert cfg.node_anal_type == "Dependability"
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=10, date="fixed", report_sink=report_data)
    # Row layout is "<label> Min Mean Max StDev SEM" -- index 2 is Mean.
    real_dependability = next(
        float(line.split()[2]) for line in fin
        if line.startswith("Dependability"))
    real_availability = next(
        float(line.split()[2]) for line in fin
        if line.startswith("Availability"))
    assert real_dependability != real_availability   # genuinely different

    dock = ResultsDock()
    dock.set_weak_settings(cfg.node_anal_type, cfg.gy_threshold,
                           cfg.yr_threshold)
    dock.set_results(logs, eor, fin, report_data=report_data,
                     units=cfg.units)
    card = dock._cards["Availability"]
    assert card._title.text() == "Dependability"
    assert float(card._value.text()) == pytest.approx(real_dependability)


def test_swapping_analysis_type_live_refreshes_the_overview_card(qapp):
    """Swapping `nodeAnalType` (the Weak Link tab's Analysis Type combo)
    refreshes the Overview card immediately, so it agrees with the new
    setting without waiting for the next run."""
    from raptor.gui.ui.main_window import MainWindow

    rbd = load_rbd(os.path.join(GOLD, "Phasing3", "input.rbd"))
    win = MainWindow()
    win._load_into_app(rbd, "x")
    QApplication.instance().processEvents()

    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        win._rbd, num_runs=10, date="fixed", report_sink=report_data)
    from raptor.core.fileio.typed import system_config_from_row
    cfg = system_config_from_row(win._rbd.sections["SYSTEM_DATA"].rows[0])
    win._push_weak_settings()
    win._results.set_results(logs, eor, fin, report_data=report_data,
                             units=cfg.units)
    QApplication.instance().processEvents()

    card = win._results._cards["Availability"]
    assert card._title.text() == "Dependability"   # Phasing3's own config

    # Snapshots of Phasing3's real simulation output for the two metrics.
    # They are not this test's subject (card refresh on swap), so they
    # drift when an engine fix changes what Phasing3 simulates.
    win._on_weak_type_changed("Availability")
    assert card._title.text() == "Availability"
    assert float(card._value.text()) == pytest.approx(0.983319307)

    win._on_weak_type_changed("Dependability")
    assert card._title.text() == "Dependability"
    assert float(card._value.text()) == pytest.approx(0.982875671)


def test_live_state_tooltips(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                     "input.rbd")))
    class FakeBlock:
        id = 1
        name = "Pump"
        op_status = type("S", (), {"value": "Repairing"})()
    blocks = []
    for bid, _item in list(v._block_items.items())[:1]:
        blk = FakeBlock(); blk.id = bid
        blocks.append(blk)
    v.update_live_states(blocks)
    tip = next(iter(v._block_items.values())).toolTip()
    assert "Repairing" in tip and "DOWN" in tip and "Pump" in tip


def test_live_states_colour_nodes_links_and_hierarchies_too(qapp):
    """`update_live_states` colours every element type from its own live
    status, not just blocks (Appendix D "Node/Event/Hierarchy Colors";
    `LinkStatus` mirrors the node palette)."""
    from raptor.core.model import NodeStatus, LinkStatus
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "CS_Probe", "input.rbd")))
    assert v._node_items and v._hier_items and v.links   # sanity: real data

    for n in v.nodes.values():
        n.status = NodeStatus.DEGRADED
    for h in v.hiers.values():
        h.status = NodeStatus.DOWN
    for lk in v.links:
        lk.status = LinkStatus.DOWN
    v.update_live_states(v.blocks)

    node_item = next(iter(v._node_items.values()))
    assert node_item.brush().color().name() == \
        APPENDIX_D_STATE_COLOURS["degraded"].lower()
    hier_item = next(iter(v._hier_items.values()))
    assert hier_item.brush().color().name() == \
        APPENDIX_D_STATE_COLOURS["repairing"].lower()
    from raptor.gui.ui.diagram import _LinkItem
    link_item = next(i for i in v._scene.items() if isinstance(i, _LinkItem))
    assert link_item.pen().color().name() == \
        APPENDIX_D_STATE_COLOURS["repairing"].lower()

    # A normal retheme (leaving the Simulate/Results ribbon tab) must
    # restore the themed appearance for every element type, same as it
    # already did for blocks.
    v.retheme()
    assert node_item.brush().color().name() != \
        APPENDIX_D_STATE_COLOURS["degraded"].lower()


def test_live_states_colour_events(qapp):
    from raptor.core.model import EventStatus
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "Events", "input.rbd")))
    assert v._event_items   # sanity: this fixture actually has events

    for e in v.events.values():
        e.op_status = EventStatus.FAILURE
    v.update_live_states(v.blocks)

    item = next(iter(v._event_items.values()))
    assert item.brush().color().name() == \
        APPENDIX_D_STATE_COLOURS["repairing"].lower()
    assert "Failed" in item.toolTip()


# --- Ch.17 Fig 17.12/17.13: stress-shaded Running colour + Link/Cut
# overlay squares/diamonds ---------------------------------------------

def test_live_states_stress_shading_for_phased_running_blocks(qapp):
    """A phased block that is currently Running shades among three
    greens by its own live `run_state.phase_stress`: nominal (==1), under-stress
    (<1) or over-stress (>1). A non-phased block stays the plain
    "Running" green regardless of `phase_stress`."""
    from raptor.core.model import BlockStatus
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "Phasing1", "input.rbd")))
    phased_ids = [bid for bid, b in v.blocks.items() if b.uses_phasing]
    assert phased_ids   # sanity: this fixture actually has phased blocks
    bid = phased_ids[0]
    other_bid = next(b for b in v.blocks if b not in phased_ids)

    for b in v.blocks.values():
        b.op_status = BlockStatus.RUNNING
    v.blocks[other_bid].run_state.phase_stress = 2.5   # non-phased -- must be ignored

    v.blocks[bid].run_state.phase_stress = 1.0
    v.update_live_states(v.blocks)
    assert v._block_items[bid].brush().color().name() == \
        APPENDIX_D_STATE_COLOURS["running"].lower()
    assert v._block_items[other_bid].brush().color().name() == \
        APPENDIX_D_STATE_COLOURS["running"].lower()

    v.blocks[bid].run_state.phase_stress = 0.5
    v.update_live_states(v.blocks)
    tip = v._block_items[bid].toolTip()
    assert v._block_items[bid].brush().color().name() == v._STRESS_COLOURS["under"].lower()
    assert "under-stress" in tip

    v.blocks[bid].run_state.phase_stress = 2.0
    v.update_live_states(v.blocks)
    assert v._block_items[bid].brush().color().name() == v._STRESS_COLOURS["over"].lower()
    assert "over-stress" in v._block_items[bid].toolTip()
    # Raptor 7's `ChangeRunningColor`: below 1 is the DARK green, above 1 the BRIGHT one
    def lum(c):
        return c.red() * 0.3 + c.green() * 0.59 + c.blue() * 0.11
    from PySide6.QtGui import QColor
    under, over = (QColor(v._STRESS_COLOURS[k]) for k in ("under", "over"))
    nominal = QColor(APPENDIX_D_STATE_COLOURS["running"])
    assert lum(under) < lum(nominal) < lum(over)

    # With the run's current phase given, the block's own value for that phase decides, not the stale field
    v.blocks[bid].run_state.phase_stress = 1.0
    v.blocks[bid].phase_value = [1.0, 1.5, 0.5]
    for phase, want in ((1, "nominal"), (2, "over"), (3, "under")):
        v.update_live_states(v.blocks, phase=phase)
        assert v._block_items[bid].brush().color().name() == v._STRESS_COLOURS[want].lower(), phase


def test_live_states_link_cut_overlay_on_blocks_and_events(qapp):
    """`active_status` LINKED/CUT draws a small white/black badge (Ch.17
    Fig 17.13's square on blocks, Fig 17.16-adjacent diamond on
    events) on top of whatever state colour is already showing,
    cleared again once the live view ends."""
    from raptor.core.model import ActiveStatus
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "Phasing1", "input.rbd")))
    assert v.blocks and v._event_items

    bid = next(iter(v.blocks))
    eid = next(iter(v.events))
    v.blocks[bid].active_status = ActiveStatus.LINKED
    v.events[eid].active_status = ActiveStatus.CUT
    v.update_live_states(v.blocks)

    assert v._block_items[bid]._link_cut == "linked"
    assert v._event_items[eid]._link_cut == "cut"

    v.blocks[bid].active_status = ActiveStatus.ACTIVE
    v.update_live_states(v.blocks)
    assert v._block_items[bid]._link_cut is None

    v.clear_live_states()
    assert v._event_items[eid]._link_cut is None


# --- Results Overlay: Capacity/Cost/Spares on-canvas colouring, using
# the same DiagramView.apply_*_colors/_last_result_visuals mechanism as
# the Weak Link overlay, the four mutually exclusive. ----------------

def test_capacity_overlay_colours_nodes_by_utilization(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "Capacity1", "input.rbd")))
    report_data: list = []
    run_simulation_with_reports(
        v._rbd_ref, num_runs=2, date="fixed", report_sink=report_data)
    caps = [d.capacity for d in report_data if d.capacity is not None]
    assert caps, "Capacity1 must have real capacity data"
    node_ids = {nid for cap in caps for nid in cap.nodes}
    usage = {nid: sum(cap.nodes[nid].usage for cap in caps
                      if nid in cap.nodes) /
                  len([1 for cap in caps if nid in cap.nodes])
            for nid in node_ids}

    v.apply_capacity_colors(usage)
    assert v._last_result_visuals["kind"] == "capacity"
    high_nid = max(usage, key=usage.get)
    item = v._node_items[high_nid]
    assert "Red" in item.toolTip() or usage[high_nid] < 0.9
    # >= 90% utilization must be Red -- Capacity1's Start node runs
    # at full flow (100%).
    assert usage[high_nid] >= 0.9
    assert "Red" in item.toolTip()
    # Fuel-gauge badge: fraction plus the same band colour as the
    # fill/tooltip, not a separately-computed value.
    frac, colour = item._capacity_badge
    assert frac == pytest.approx(usage[high_nid])
    from raptor.gui.resources.design_tokens import ThemeManager
    assert colour == ThemeManager.instance().colour["result_red"]

    v.clear_live_states(keep_result_visuals=False)
    assert v._last_result_visuals is None
    assert item._capacity_badge is None


def test_capacity_gauge_badge_area_is_within_boundingrect(qapp):
    """The capacity gauge is drawn just below the node ellipse, so
    `_NodeItem.boundingRect()` must cover it or Qt clips it from the
    paint/update invalidation region."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "Phasing1", "input.rbd")))
    nid = next(iter(v._node_items))
    item = v._node_items[nid]
    item.set_capacity_badge(0.5, "#CC4545")
    br = item.boundingRect()
    gauge_area = QRectF(-item._r * 0.9, item._r + 4, item._r * 1.8, 5.0)
    assert br.contains(gauge_area)


def test_cost_overlay_bands_relative_to_most_expensive_block(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "Phasing1", "input.rbd")))
    cost_by_block = {5: 300.0, 4: 150.0, 6: 30.0}   # 100%/50%/10% of max

    v.apply_cost_colors(cost_by_block)
    assert v._last_result_visuals["kind"] == "cost"
    assert "Red" in v._block_items[5].toolTip()      # 100% of max
    assert "Yellow" in v._block_items[4].toolTip()    # >= 2/3
    assert "Green" in v._block_items[6].toolTip()     # < 1/3
    assert "$300.00" in v._block_items[5].toolTip()
    # Every cost-covered block gets a "$" badge regardless of band.
    for bid in cost_by_block:
        glyph, _colour = v._block_items[bid]._result_badge
        assert glyph == "$"
    v.clear_live_states(keep_result_visuals=False)
    for bid in cost_by_block:
        assert v._block_items[bid]._result_badge is None


def test_spares_overlay_flags_any_exhausted_time(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "Phasing1", "input.rbd")))
    exhausted = {5: 0.05, 4: 0.0}
    sparing_cost = {5: 1234.56}

    v.apply_spares_colors(exhausted, sparing_cost)
    assert v._last_result_visuals["kind"] == "spares"
    tip5 = v._block_items[5].toolTip()
    assert "Red" in tip5 and "5.00%" in tip5 and "$1,234.56" in tip5
    tip4 = v._block_items[4].toolTip()
    assert "Green" in tip4 and "0.00%" in tip4
    # The "!" badge goes only to the flagged block (exhausted time > 0);
    # the unflagged block gets no badge, matching this overlay's
    # flag-not-gradient design.
    assert v._block_items[5]._result_badge[0] == "!"
    assert v._block_items[4]._result_badge is None


def test_canvas_overlays_mutually_exclusive_across_all_four(qapp):
    """Weak Link, Capacity, Cost and Spares (the Results ribbon's Show on
    Canvas group) share ONE canvas -- activating any one must deactivate whichever
    other was on, including keeping their own UI (checked state) in
    sync so a stale checked button never lies about what's shown."""
    from raptor.gui.ui.main_window import MainWindow
    rbd = load_rbd(os.path.join(GOLD, "Phasing1", "input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    w = MainWindow()
    w._load_into_app(rbd, None)
    QApplication.instance().processEvents()
    w._run_count_spin.setValue(1)
    w._run_simulation()
    QApplication.instance().processEvents()

    v = w._diagram_dock.view
    cbtn = w._cost_overlay_action
    sbtn = w._spares_overlay_action
    assert cbtn.isEnabled() and sbtn.isEnabled()

    cbtn.setChecked(True)
    QApplication.instance().processEvents()
    assert v._last_result_visuals["kind"] == "cost"

    sbtn.setChecked(True)
    QApplication.instance().processEvents()
    assert v._last_result_visuals["kind"] == "spares"
    assert not cbtn.isChecked(), \
        "activating Spares must uncheck the Cost button"

    w._toggle_weak_colors()
    QApplication.instance().processEvents()
    assert v._last_result_visuals["kind"] == "weak"
    assert not sbtn.isChecked(), \
        "activating Weak Link must uncheck the Spares button"
    assert w._weak_color_action.isChecked()

    cbtn.setChecked(True)
    QApplication.instance().processEvents()
    assert v._last_result_visuals["kind"] == "cost"
    assert not w._weak_color_action.isChecked(), \
        "activating Cost must uncheck the Weak Link ribbon action"


def test_capacity_overlay_offered_only_with_capacity_data(qapp):
    """The ribbon's Capacity overlay is enabled by a run that produced
    capacity data, and goes off and disabled when the results go."""
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), None)
    QApplication.instance().processEvents()
    w._run_count_spin.setValue(1)
    w._run_simulation()
    QApplication.instance().processEvents()
    assert not w._capacity_overlay_action.isEnabled()

    w._load_into_app(load_rbd(os.path.join(
        GOLD, "Capacity1", "input.rbd")), None)
    QApplication.instance().processEvents()
    w._run_simulation()
    QApplication.instance().processEvents()
    act = w._capacity_overlay_action
    assert act.isEnabled()
    act.setChecked(True)
    QApplication.instance().processEvents()
    assert act.isChecked()
    assert w._diagram_dock.view._last_result_visuals["kind"] == "capacity"

    w._discard_results()
    assert not act.isEnabled() and not act.isChecked()


def test_result_overlay_survives_hierarchy_level_navigation(qapp):
    """Same guarantee the pre-existing Weak Link overlay already has
    (`_last_result_visuals`): traversing into/out of a hierarchy level
    must not lose the active Cost overlay."""
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "HierarchyExample",
                                     "input.rbd")))
    assert v.hiers
    cost_by_block = {bid: float(i * 100) for i, bid in
                     enumerate(v.blocks)}
    v.apply_cost_colors(cost_by_block)
    hid = next(iter(v.hiers))
    v.enter_level(hid)
    v.go_home()
    assert v._last_result_visuals is not None
    assert v._last_result_visuals["kind"] == "cost"


def test_hierarchy_subtree_copy_paste_undo(qapp):
    """Copy a hierarchy OVAL -> whole subtree travels (markers,
    elements, internal links); paste duplicates under fresh ids;
    one undo removes everything."""
    from raptor.core.fileio.rbd import load_rbd
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "HierarchyExample",
                                     "input.rbd")))
    src_hid = 23
    # children live INSIDE the level; count them from raw rows
    rbd_rows = v._rbd_ref.sections["BLOCK_DATA"].rows
    src_block_count = sum(1 for r in rbd_rows
                          if int(float(r.get("ParentId", 0))) == src_hid)
    assert src_block_count >= 1, "fixture expectation"
    it = v._hier_items[src_hid]
    v._scene.clearSelection()
    it.setSelected(True)
    n = v.copy_selection()
    assert n >= src_block_count + 2   # subtree blocks + 2 markers
    placed = v.paste(at_model=(1200.0, 400.0))
    assert placed >= src_block_count + 2
    new_hid = max(v.hiers)
    assert new_hid != src_hid
    new_children = sum(1 for r in v._rbd_ref.sections["BLOCK_DATA"].rows
                       if int(float(r.get("ParentId", 0))) == new_hid)
    assert new_children == src_block_count
    # markers exist and are unique
    hobj = v.hiers[new_hid]
    assert hobj.in_id != hobj.out_id
    assert hobj.in_id in v.nodes and hobj.out_id in v.nodes
    _total_blocks_before_undo = None
    v._stack.undo()
    assert new_hid not in v.hiers


def _real_reliability_table(path):
    """{name: (min, mean, max)} from a real Final Results report's
    Reliability_Data block for the given section (NODE then BLOCK)."""
    out = {"NODE": {}, "BLOCK": {}}
    sect = None
    in_rel = False
    for line in open(path):
        s = line.rstrip("\n")
        if s.startswith("NODE_ANALYSIS"):
            sect = "NODE"
        elif s.startswith("BLOCK_ANALYSIS"):
            sect = "BLOCK"
        elif s.startswith(("EVENT_ANALYSIS", "HIERARCHY_ANALYSIS")):
            sect = None
        if s.startswith("Reliability_Data"):
            in_rel = True
            continue
        if in_rel:
            if not s.strip():
                in_rel = False
                continue
            parts = s.split()
            if parts[0] in ("Node", "Block") or sect is None:
                continue
            out[sect][parts[0]] = tuple(float(x) for x in parts[1:4])
    return out


def test_reliability_table_matches_the_real_final_results(qapp):
    """Weak-Link Reliability: the GUI table shows the per-element
    `m_completed / missions`, digit for digit what real's Final Results
    Reliability_Data block reports (PhasedWeakAnalysisMultiMissionProbe,
    a multi-mission phased model)."""
    name = "PhasedWeakAnalysisMultiMissionProbe"
    rbd = load_rbd(os.path.join(GOLD, name, "input.rbd"))
    sec = rbd.sections["SYSTEM_DATA"]
    assert sec.rows[0].get("weakAnalysis") in ("1", 1, True)
    data = []
    run_simulation_with_reports(rbd, date="fixed", report_sink=data)
    dock = ResultsDock()
    dock.set_weak_settings("Reliability", 0.95, 0.90)
    dock._report_data = data
    runs = [d.weak_stats for d in data if d.weak_stats]
    missions = [d.missions_attempted for d in data if d.weak_stats]
    rows = dict(dock._weak_metric_rows(runs, (), (), missions=missions))
    real = _real_reliability_table(os.path.join(
        GOLD, name, "legacy", f"rpt_final_results_{name}.txt"))["BLOCK"]
    assert real, "premise: the real report has a block Reliability table"
    for blk, (lo, mean, hi) in real.items():
        vals = rows[blk]
        assert min(vals) == pytest.approx(lo, abs=5e-9), blk
        assert sum(vals) / len(vals) == pytest.approx(mean, abs=5e-9), blk
        assert max(vals) == pytest.approx(hi, abs=5e-9), blk


@pytest.mark.parametrize("kind", ["Availability", "Dependability",
                                  "Reliability"])
def test_weak_page_header_and_values_follow_the_analysis_type(qapp, kind):
    """All three Ch.11 analysis types build the page; the metric column is
    labelled with the type, and Reliability's numbers differ from
    Availability's (they are mission-based, not time-based)."""
    name = "PhasedWeakAnalysisMultiMissionProbe"
    rbd = load_rbd(os.path.join(GOLD, name, "input.rbd"))
    data = []
    run_simulation_with_reports(rbd, num_runs=3, date="fixed",
                                report_sink=data)
    dock = ResultsDock()
    dock.set_weak_settings(kind, 0.95, 0.90)
    dock._report_data = data
    page = dock._build_weak_page()
    tables = page.findChildren(QTableWidget)
    heads = [t.horizontalHeaderItem(2).text() for t in tables
             if t.columnCount() >= 3 and t.horizontalHeaderItem(2)
             and t.horizontalHeaderItem(2).text() in
             ("Availability", "Dependability", "Reliability")]
    assert heads and set(heads) == {kind}, heads


def test_every_link_status_is_painted_the_colour_raptor_7_gives_it(qapp):
    """`ChangeLinkStatus`: Up black (the port's resting colour), Down and Done red, Idle brown, Standby blue, PM
    amber. Done used to fall through to the resting colour."""
    from raptor.core.model import LinkStatus
    from raptor.gui.ui.diagram import _LinkItem
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "HierarchyExample", "input.rbd")))
    want = {LinkStatus.DOWN: "repairing", LinkStatus.DONE: "repairing", LinkStatus.IDLE: "idle",
            LinkStatus.STANDBY: "warm_standby", LinkStatus.PM: "maintenance"}
    resting = None
    for status in (LinkStatus.UP, *want):
        for lk in v.links:
            lk.status = status
        v.update_live_states(v.blocks)
        pen = next(i for i in v._scene.items() if isinstance(i, _LinkItem)).pen().color().name()
        if status is LinkStatus.UP:
            resting = pen
        else:
            assert pen == APPENDIX_D_STATE_COLOURS[want[status]].lower(), status
            assert pen != resting


def test_importance_overlay_bands_relative_to_the_largest(qapp):
    """Importance bands are relative to the run's largest value, and the
    rank is badged: red is 'most leverage', not 'worst state'."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView

    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "Phasing1", "input.rbd")))
    values = {5: 0.30, 4: 0.15, 6: 0.03}     # 100% / 50% / 10% of max
    v.apply_importance_colors(values, measure="Birnbaum",
                              metric_name="Availability")
    assert v._last_result_visuals["kind"] == "importance"
    assert "Red" in v._block_items[5].toolTip()
    assert "Yellow" in v._block_items[4].toolTip()
    assert "Green" in v._block_items[6].toolTip()
    assert "rank 1 of 3" in v._block_items[5].toolTip()
    assert "not a fault reading" in v._block_items[5].toolTip()
    glyph, _colour = v._block_items[5]._result_badge
    assert glyph == "1"
    v.clear_live_states(keep_result_visuals=False)
    assert v._block_items[5]._result_badge is None

"""The model editor, with no Qt and no window.

Before it was extracted from `DiagramView` none of this could be tested
without constructing a MainWindow. Here the host is a plain object holding
the typed model dicts parsed from a real file, so each edit runs in
milliseconds and the assertions are on the objects and rows themselves.
"""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.typed import block_from_row, event_from_row, node_from_row
from raptor.gui.model_edit import (BLOCK_COST_FIELDS, DEPENDENCY_BADGE,
                                   SUBLABELS, EditedFields, ModelEditor,
                                   Refresh, UnknownEditKey)

FIXTURE = "tests/golden/EventDepProbe/input.rbd"


def _ids(rows, col="ID"):
    out = []
    for r in rows:
        try:
            i = int(float(r[col]))
        except (KeyError, ValueError):
            continue
        if i != 0:
            out.append(i)
    return out


@pytest.fixture
def host():
    rbd = load_rbd(FIXTURE)
    blocks = {block_from_row(r).id: block_from_row(r)
              for r in rbd.sections["BLOCK_DATA"].rows
              if int(float(r["ID"])) != 0}
    nodes = {node_from_row(r).id: node_from_row(r)
             for r in rbd.sections["NODE_DATA"].rows}
    events = {event_from_row(r).id: event_from_row(r)
              for r in rbd.sections["EVENT_DATA"].rows}
    return SimpleNamespace(blocks=blocks, nodes=nodes, events=events, hiers={},
                           rbd=rbd, edited=EditedFields())


@pytest.fixture
def ed(host):
    return ModelEditor(host)


def _bid(host):
    return next(iter(host.blocks))


def test_module_needs_no_qt():
    """The point of the extraction."""
    code = ("import sys, raptor.gui.model_edit;"
            "sys.exit(any(m.startswith('PySide') for m in sys.modules))")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


class TestBlockEdits:
    def test_scalar_edit_sets_the_object_and_the_row(self, host, ed):
        bid = _bid(host)
        ed.apply_value("PM stagger", bid, 4.5)
        assert host.blocks[bid].pm_stagger == 4.5
        assert ed.block_row(bid)["PMStaggerTime"] == "4.500000"

    def test_sequence_edit_spreads_over_attrs_and_columns(self, host, ed):
        bid = _bid(host)
        ed.apply_value("dependency fields", bid, (10, 20, 30, 40, 50))
        b, row = host.blocks[bid], ed.block_row(bid)
        assert (b.dep_nothing_percent, b.dep_idle_percent, b.dep_pm_percent,
                b.dep_fail_percent) == (10, 20, 30, 40)
        assert [row[c] for c in ("DepNothing", "DepIdle", "DepPM",
                                 "DepFail", "DepPMThresh")] == [
            "10.000000", "20.000000", "30.000000", "40.000000", "50.000000"]

    def test_a_list_valued_attribute_is_copied_not_aliased(self, host, ed):
        bid = _bid(host)
        given = [1.0, 2.0, 3.0]
        ed.apply_value("failure params", bid, given)
        given[0] = 99.0
        assert host.blocks[bid].fail_values[0] == 1.0

    def test_costs_cover_every_cost_field(self, host, ed):
        bid = _bid(host)
        vals = [float(i + 1) for i in range(len(BLOCK_COST_FIELDS))]
        ed.apply_value("costs", bid, vals)
        row = ed.block_row(bid)
        for (attr, col), v in zip(BLOCK_COST_FIELDS, vals):
            assert getattr(host.blocks[bid], attr) == v
            assert row[col] == f"{v:.6f}"

    def test_an_empty_pool_name_is_written_as_the_placeholder(self, host, ed):
        """A whitespace-delimited row loses an empty token and shifts every
        later column left."""
        bid = _bid(host)
        ed.apply_value("pool name", bid, "")
        assert ed.block_row(bid)["SparePoolName"] == "unnamed"
        ed.apply_value("pool name", bid, "PoolX")
        assert ed.block_row(bid)["SparePoolName"] == "PoolX"
        assert host.blocks[bid].pool_name == "PoolX"

    def test_a_pm_trigger_clears_its_name_when_untriggered(self, host, ed):
        bid = _bid(host)
        ed.apply_value("pm trigger", bid, (True, "TrigA"))
        assert ed.block_row(bid)["pmTrig"] == "TrigA"
        ed.apply_value("pm trigger", bid, (False, "TrigA"))
        assert ed.block_row(bid)["pmTrig"] == ""
        assert ed.block_row(bid)["pmTriggered"] == "0"
        assert host.blocks[bid].pm_trig == ""

    @pytest.mark.parametrize("which,f,r", [("F", 211, None), ("R", None, 212),
                                           ("FR", 210, 210)])
    def test_stream_edit_targets_the_named_streams(self, host, ed,
                                                   which, f, r):
        bid = _bid(host)
        before = (host.blocks[bid].fail_stream, host.blocks[bid].repair_stream)
        ed.apply_value("stream edit", bid, (which, f or r))
        b = host.blocks[bid]
        assert b.fail_stream == (f if f is not None else before[0])
        assert b.repair_stream == (r if r is not None else before[1])
        row = ed.block_row(bid)
        assert row["FStream"] == str(b.fail_stream)
        assert row["RStream"] == str(b.repair_stream)

    def test_pm_option_family(self, host, ed):
        bid = _bid(host)
        ed.apply_value("block pm refresh", bid, False)
        assert host.blocks[bid].pm_refresh is False
        ed.apply_value("block pm refresh", bid, True)
        assert host.blocks[bid].pm_refresh is True


class TestNodeEdits:
    def test_every_node_edit_is_recorded_for_save(self, host, ed):
        nid = next(iter(host.nodes))
        ed.apply_value("node k", nid, 2)
        ed.apply_value("node full flow", nid, True)
        ed.apply_value("node cold standby", nid, True)
        assert nid in host.edited.good_paths
        assert nid in host.edited.flow_flags
        assert nid in host.edited.standby
        assert nid not in host.edited.node_deps

    def test_an_untouched_node_is_not_recorded(self, host, ed):
        nid = next(iter(host.nodes))
        assert not host.edited.good_paths and nid not in host.edited.standby

    def test_a_node_dependency_asks_for_a_badge_redraw(self, host, ed):
        nid = next(iter(host.nodes))
        out = ed.apply_value("node dependency", nid, (2, "RBDBlock"))
        assert out == [Refresh(DEPENDENCY_BADGE, "node", nid)]
        assert (host.nodes[nid].dependency_num,
                host.nodes[nid].dep_type) == (2, "RBDBlock")


class TestEventEdits:
    def test_an_event_edit_mirrors_into_its_row(self, host, ed):
        eid = next(iter(host.events))
        ed.apply_value("event param", eid, 0.25)
        row = ed.row("event", eid)
        assert host.events[eid].fail_mean == 0.25
        assert row["failVal"] == "0.250000"

    def test_the_stream_is_coerced_to_int(self, host, ed):
        """The signal that carries it is float-typed; a float stream would
        be written as '208.0'."""
        eid = next(iter(host.events))
        ed.apply_value("event stream", eid, 208.0)
        assert host.events[eid].fail_stream == 208
        assert isinstance(host.events[eid].fail_stream, int)
        assert ed.row("event", eid)["failStream"] == "208"

    def test_costs_spread_over_three_fields(self, host, ed):
        eid = next(iter(host.events))
        ed.apply_value("event costs", eid, (1.0, 2.0, 3.0))
        e = host.events[eid]
        assert (e.initial_cost, e.operating_cost, e.repairing_cost) == \
            (1.0, 2.0, 3.0)
        assert ed.row("event", eid)["RepCost"] == "3.000000"


class TestRefreshes:
    def test_param_edits_ask_for_the_sublabels(self, host, ed):
        bid = _bid(host)
        assert ed.apply_value("failure params", bid, [1.0, 2.0, 3.0]) == \
            [Refresh(SUBLABELS, "block", bid)]
        assert ed.apply_value("repair params", bid, [1.0, 2.0, 3.0]) == \
            [Refresh(SUBLABELS, "block", bid)]

    def test_a_plain_edit_asks_for_nothing(self, host, ed):
        assert ed.apply_value("initial stock", _bid(host), 7) == []


class TestFailureModes:
    def test_an_unknown_key_raises_instead_of_discarding_the_edit(self, ed,
                                                                  host):
        """The if-chain this replaced fell off the end and did nothing."""
        with pytest.raises(UnknownEditKey):
            ed.apply_value("failure paramz", _bid(host), [1, 2, 3])

    def test_an_unknown_pm_option_raises(self, ed, host):
        with pytest.raises(UnknownEditKey):
            ed.apply_value("block pm nonsense", _bid(host), True)

    def test_an_element_that_no_longer_exists_is_skipped(self, ed):
        assert ed.apply_value("PM stagger", 999_999, 1.0) == []
        assert ed.apply_value("node k", 999_999, 1) == []

    def test_no_file_row_still_updates_the_object(self, host):
        """Before a model is saved/loaded there may be no rows at all."""
        host.rbd = None
        bid = _bid(host)
        ModelEditor(host).apply_value("PM stagger", bid, 2.0)
        assert host.blocks[bid].pm_stagger == 2.0

    def test_handles_and_known_keys_agree(self, ed):
        assert all(ed.handles(k) for k in ModelEditor.known_keys())
        assert not ed.handles("not a key")


class TestRename:
    def test_block_rename_moves_its_comment_row(self, host, ed):
        bid = _bid(host)
        old = host.blocks[bid].name
        host.rbd.sections["COMMENTS"].rows.append(
            {"Type": "Block", "Name": old, "ID": "0"})
        assert ed.rename("block", bid, "Pump_2") is True
        assert host.blocks[bid].name == "Pump_2"
        c = host.rbd.sections["COMMENTS"].rows[-1]
        assert (c["Name"], c["ID"]) == ("Pump_2", str(bid))

    def test_node_uses_node_name(self, host, ed):
        nid = next(iter(host.nodes))
        ed.rename("node", nid, "Junction")
        assert host.nodes[nid].node_name == "Junction"

    def test_event_rename_reaches_its_row(self, host, ed):
        eid = next(iter(host.events))
        ed.rename("event", eid, "Storm")
        assert ed.row("event", eid)["eventName"] == "Storm"

    def test_hierarchy_rename_reaches_its_row(self, host, ed):
        sec = host.rbd.sections["HIERARCHY_DATA"]
        sec.rows.append({"ID": "7", "hierarchyName": "H"})
        host.hiers[7] = SimpleNamespace(name="H")
        assert ed.rename("hier", 7, "Plant")
        assert sec.rows[-1]["hierarchyName"] == "Plant"

    def test_unknown_element_or_kind_changes_nothing(self, host, ed):
        assert ed.rename("block", 999_999, "x") is False
        assert ed.rename("bogus", 1, "x") is False


class TestDist:
    def test_failure_and_repair_are_separate(self, host, ed):
        bid = _bid(host)
        ed.apply_dist(bid, "F", (3, 2))
        ed.apply_dist(bid, "R", (5, 4))
        b, row = host.blocks[bid], ed.block_row(bid)
        assert (b.fail_distro, b.num_fail_params) == (3, 2)
        assert (b.repair_distro, b.num_repair_params) == (5, 4)
        assert (row["FDist"], row["#FParam"], row["RDist"],
                row["#RParam"]) == ("3", "2", "5", "4")

    def test_missing_block_and_missing_file(self, host, ed):
        ed.apply_dist(999_999, "F", (1, 1))
        host.rbd = None
        bid = _bid(host)
        ed.apply_dist(bid, "F", (2, 3))
        assert host.blocks[bid].fail_distro == 2


class TestWriteBack:
    def test_blocks_are_written_from_the_objects(self, host, ed):
        bid = _bid(host)
        host.blocks[bid].pm_freq = 99.0
        host.blocks[bid].dep_type = ""
        ed.write_back(host.rbd)
        row = ed.block_row(bid)
        assert row["PMFreq"] == "99.000000"
        assert row["DepType"] == "None"        # an empty token would vanish

    def test_an_untouched_node_keeps_its_file_value_for_the_lossy_fields(
            self, host, ed):
        """`k=-1` in the file means "never opened"; the typed object coerces
        it to 1. Writing it back blindly would turn the sentinel into a
        literal 1 on every save."""
        nid = next(iter(host.nodes))
        row = ed.row("node", nid)
        row["k"] = "-1"
        host.nodes[nid].good_paths = 1
        ed.write_back(host.rbd)
        assert row["k"] == "-1"
        ed.apply_value("node k", nid, 2)
        ed.write_back(host.rbd)
        assert row["k"] == "2"

    def test_write_back_is_idempotent(self, host, ed):
        ed.write_back(host.rbd)
        once = [dict(r) for s in host.rbd.sections.values() for r in s.rows]
        ed.write_back(host.rbd)
        twice = [dict(r) for s in host.rbd.sections.values() for r in s.rows]
        assert once == twice

    def test_only_listed_hierarchies_are_written(self, host, ed):
        sec = host.rbd.sections["HIERARCHY_DATA"]
        sec.rows.extend([{"ID": "7", "xPos": "1", "yPos": "1"},
                         {"ID": "8", "xPos": "1", "yPos": "1"}])
        host.hiers[7] = SimpleNamespace(x_position=5.0, y_position=6.0)
        host.hiers[8] = SimpleNamespace(x_position=5.0, y_position=6.0)
        ed.write_back(host.rbd, hier_ids={7})
        assert sec.rows[-2]["xPos"] == "5.000000"
        assert sec.rows[-1]["xPos"] == "1"        # another level: untouched

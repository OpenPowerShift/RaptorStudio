"""`graph.build_efpa_graph` and `graph.upstream_domains` on the real
Phasing1 fixture: 3 hierarchies, 2 events, block and event chains. Most
tests edit the loaded model's links in memory to reach connection shapes
no golden fixture carries."""
from __future__ import annotations

import pytest

from raptor.core.engine.graph import build_efpa_graph, upstream_domains
from raptor.core.engine.load import load_model
from raptor.core.fileio.rbd import load_rbd

RBD = "tests/golden/Phasing1/input.rbd"
# Phasing1 ids: hierarchy 23 has In node 24 and Out node 25; link 21 is
# Hier 23 -> node 19; event 32 -> block 36 (link 32) -> node 19.


@pytest.fixture
def model():
    return load_model(load_rbd(RBD))


def _rebuild(m):
    build_efpa_graph(m.nodes_by_id, m.blocks_by_id, m.events_by_id, m.hiers_by_id, m.links_by_id)


def _graph_state(m):
    return (
        {n.id: (list(n.propagates_to_node_ids), list(n.propagates_to_link_ids), list(n.in_paths_array))
         for n in m.nodes_by_id.values()},
        {b.id: b.downstream_link_id for b in m.blocks_by_id.values()},
        {e.id: e.downstream_link_id for e in m.events_by_id.values()},
    )


def test_rebuild_is_idempotent(model):
    before = _graph_state(model)
    _rebuild(model)
    assert _graph_state(model) == before


def test_out_node_direct_link_equals_hierarchy_link(model):
    """A hierarchy's output authored on its Out node instead of the oval
    gives the same graph."""
    before = _graph_state(model)
    link = model.links_by_id[21]
    link.connect_f_ref, link.connect_from_id = "RBDNode", 25
    _rebuild(model)
    assert _graph_state(model) == before


def test_event_into_hierarchy_takes_out_nodes_link(model):
    """An event wired straight into a hierarchy lands on its In node, so its
    downstream_link_id becomes the Out node's downstream link."""
    link = model.links_by_id[32]
    link.connect_t_ref, link.connect_to_id = "RBDHier", 23
    _rebuild(model)
    out = model.nodes_by_id[25]
    assert model.events_by_id[32].downstream_link_id == out.propagates_to_link_ids[0]


def test_block_into_hierarchy_takes_out_nodes_link(model):
    link = model.links_by_id[33]   # Nuclear_Reactor -> node 19
    link.connect_t_ref, link.connect_to_id = "RBDHier", 26
    _rebuild(model)
    assert model.blocks_by_id[36].downstream_link_id == model.nodes_by_id[28].propagates_to_link_ids[0]


def test_surplus_block_output_is_pruned(model):
    """A block with two outputs keeps only the lowest-id link (real Raptor's
    behaviour; see tests/golden/MultiOutLink)."""
    before = _graph_state(model)
    extra = model.links_by_id[33]
    model.links_by_id[99] = type(extra)(**{**vars(extra), "object_num": 99, "connect_to_id": 15})
    _rebuild(model)
    assert 99 not in model.links_by_id
    assert _graph_state(model) == before


def test_element_into_unwired_hierarchy_names_the_fix(model):
    link = model.links_by_id[32]
    link.connect_t_ref, link.connect_to_id = "RBDHier", 23
    del model.links_by_id[21]   # hierarchy 23 has no output
    with pytest.raises(NotImplementedError, match="Wire the hierarchy's output"):
        _rebuild(model)


def test_block_without_output_is_an_error(model):
    del model.links_by_id[4]   # Bus_UR -> node 20
    # The message must NAME the block: it arrives at run time, and a bare
    # "RBDBlock id=3" gives the user nothing to go and look at on a
    # canvas of named blocks.
    named = next(b for b in model.blocks_by_id.values() if b.id == 3)
    with pytest.raises(ValueError) as exc:
        _rebuild(model)
    msg = str(exc.value)
    assert "no outgoing link" in msg
    assert "id=3" in msg
    assert named.name in msg, f"error must name the block, got: {msg}"


@pytest.mark.parametrize("link_id", [4, 16])   # a block's walk; a node's link
def test_unknown_target_kind_is_refused(model, link_id):
    model.links_by_id[link_id].connect_t_ref = "RBDFoo"
    with pytest.raises(NotImplementedError, match="RBDFoo target"):
        _rebuild(model)


def test_cyclic_graph_is_refused(model):
    link = model.links_by_id[30]   # node 18 -> end; send it back to node 16
    link.connect_to_id = 16
    with pytest.raises(NotImplementedError, match="loop detected"):
        _rebuild(model)


def _domains(m):
    return upstream_domains(m.nodes_by_id, m.blocks_by_id, m.events_by_id,
                            m.links_by_id, m.hiers_by_id)


def test_upstream_domains_reach_events_and_hierarchy_contents(model):
    d = _domains(model)
    end = next(i for i in d.values() if i.get("end"))
    kinds = {k for k, _ in end["sources"]}
    names = {n for _, n in end["sources"]}
    assert kinds == {"Block", "Event"}
    assert {"Inspection", "Ground_Control"} <= names
    assert {"SolarP_500W", "SolarP_650W", "SolarP_900W", "Nuclear_Reactor"} <= names
    assert not names & {"500path", "650path", "900path"}



# Weak Link domain diagrams on other fixtures (moved from test_domains.py,
# which imports Qt and so runs only in the gui tier).
GOLDENS = "tests/golden"


def _fixture_domains(name):
    m = load_model(load_rbd(f"{GOLDENS}/{name}/input.rbd"))
    return upstream_domains(m.nodes_by_id, m.blocks_by_id, m.events_by_id,
                            m.links_by_id, m.hiers_by_id)


def test_two_block_end_domain_has_both_parallel_blocks():
    d = _fixture_domains("TwoBlockRedundancy")
    end = next(i for i in d.values() if i.get("end"))
    assert len(end["sources"]) == 2  # both "unnamed", #id-disambiguated
    assert all(k == "Block" for k, _ in end["sources"])
    assert end["paths"] == ["join"]


def test_start_and_self_excluded():
    d = _fixture_domains("TwoBlockRedundancy")
    assert not any(i.get("end") and i["name"] == "start" for i in d.values())
    join = next(i for i in d.values() if i["name"] == "join")
    assert "join" not in join["paths"]


def test_hierarchy_end_domain_spans_all_levels():
    d = _fixture_domains("HierarchyExample")
    end = next(i for i in d.values() if i.get("end"))
    names = {n for _, n in end["sources"]}
    # sub-level internals pulled through the hierarchy boundary...
    assert {"SolarP_500W", "SolarP_650W", "SolarP_900W"} <= names
    assert "Thermal" in names and len(names) == 13
    # ...and the ovals themselves are not listed beside their contents
    assert not names & {"500path", "650path", "900path"}
    # feeders of the hierarchies' In side starve the cone too
    assert set(end["paths"]) == {"500path", "650path", "900path",
                                 "Comm", "n1", "n2", "Power"}


def test_regular_node_domain_is_its_own_cone():
    d = _fixture_domains("HierarchyExample")
    comm = next(i for i in d.values() if i["name"] == "Comm"
                and not i.get("end"))
    names = {n for _, n in comm["sources"]}
    # direct feeders...
    assert {"HighGain", "LowGain"} <= names
    # ...plus everything that feeds them (through-block propagation)
    assert {"Thermal", "SolarP_500W"} <= names
    end_names = {n for _, n in next(
        i for i in d.values() if i.get("end"))["sources"]}
    assert names <= end_names


def test_describe_names_the_element_when_it_can():
    from raptor.core.engine.graph import _describe
    assert _describe(lambda k, i: "Bus_UR", "RBDBlock", 3) == "RBDBlock id=3 'Bus_UR'"


@pytest.mark.parametrize("names", [None, lambda k, i: ""])
def test_describe_falls_back_to_the_bare_id(names):
    """No resolver, or a blank name: the id alone, no empty quotes."""
    from raptor.core.engine.graph import _describe
    assert _describe(names, "RBDBlock", 3) == "RBDBlock id=3"



def test_a_long_series_chain_loads_without_recursion():
    """Regression: `_walk_forward` followed every block in a series
    recursively, so a 500-block chain raised RecursionError while LOADING
    the model (before any simulation). The walk is iterative now; 600
    blocks is past the old default recursion limit with room to spare."""
    from raptor.api.build import build_from_spec

    n = 600
    spec = {
        "blocks": [
            {"name": f"B{i}", "fail_dist": 4, "fail_params": [1000, 0],
             "repair_dist": 7, "repair_params": [8, 2]}
            for i in range(n)
        ],
        "nodes": [{"name": "Start", "type": "start"},
                  {"name": "End", "type": "end"}],
        "links": ([{"from": "Start", "to": "B0"}]
                  + [{"from": f"B{i}", "to": f"B{i + 1}"}
                     for i in range(n - 1)]
                  + [{"from": f"B{n - 1}", "to": "End"}]),
        "system": {"term_condition": 8760, "num_runs": 1},
    }
    model = load_model(build_from_spec(spec, auto_layout=False))
    assert len(model.blocks_by_id) == n
    assert len(model.links_by_id) == n + 1

"""`_run_test_fail` against the routine it replaced.

The original reset every node's path counter for every origin (quadratic in the node
count) and walked by recursion (a chain of ~1,000 nodes ran past the recursion limit).
It is kept here, unchanged apart from its name, as the reference the new walk must
agree with: same pruned `propagates_to_*` arrays on every golden fixture.
"""
from __future__ import annotations

import glob
import os
import pathlib

import pytest

from raptor.core.engine import graph
from raptor.core.engine.load import load_model
from raptor.core.fileio.rbd import load_rbd
from raptor.core.model import Link, RBDHier, RBDNode

GOLD = pathlib.Path(__file__).resolve().parents[1] / "golden"
FIXTURES = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(str(GOLD / "*" / "input.rbd")))


def reference_run_test_fail(
    origin: RBDNode,
    nodes_by_id: dict[int, RBDNode],
    hiers_by_id: dict[int, RBDHier],
    links_by_id: dict[int, Link],
    efpa_tested: set[int],
) -> None:
    """Port of Raptor 7 driving `RBDNodeObj.TestFail`
   for one origin node -- see module
    docstring for what this computes and why it's needed. Mutates
    `origin.propagates_to_node_ids`/`propagates_to_link_ids` and adds `origin.id` to
    `efpa_tested` (so LATER origins whose own walk passes through this
    node reuse its pruned result instead of raw NRBD values -- order-
    sensitive, matching the source's own single top-to-bottom pass).
    """
    # Raptor 7: GoodPathsRequired is uniformly 1 for EVERY
    # node during this ENTIRE pass (reachability-only, NOT the node's own
    # real K-of-N config -- that's a separate, already-ported runtime
    # mechanism, nodes.determine_node_status). NumGoodPaths resets to each
    # node's own EconnectIntoNum, except the origin, seeded to exactly 1
    # (GoodPathsRequired) so its own first TestFail decrement always
    # triggers.
    for node in nodes_by_id.values():
        node.num_good_paths = len(node.in_paths_array)
    origin.num_good_paths = 1

    if origin.parent_id != 0:
        origin_hier = hiers_by_id[origin.parent_id]
        mama = origin_hier.level if origin.type_node == 5 else origin_hier.level + 1
    else:
        mama = 0

    downstream_group: list[int] = []  # link ids, accumulation order
    propagated_group: set[int] = set()  # node ids
    loop_hits = 0

    def test_fail(node_id: int) -> None:
        nonlocal loop_hits
        node = nodes_by_id[node_id]
        if node_id == origin.id:
            loop_hits += 1
            if loop_hits == 2:
                raise NotImplementedError(
                    f"TestFail loop detected at node id={node_id} -- RunEFPA's "
                    "LoopError abort path is not ported (no fixture has a cyclic RBD graph)"
                )
        node.num_good_paths -= 1
        if node.num_good_paths != 0:  # GoodPathsRequired(1) - 1 == 0
            return

        qualifies_hier_out = node.type_node == 5 and (
            hiers_by_id[node.parent_id].level >= mama or node_id == origin.id
        )
        if node.type_node == 2 or qualifies_hier_out:
            propagated_group.add(node_id)
            if node_id in efpa_tested:
                targets, paths = node.propagates_to_node_ids, node.propagates_to_link_ids
            else:
                targets, paths = node.next_node_ids, node.next_node_link_ids
            for target_id, link_id in zip(targets, paths):
                downstream_group.append(link_id)
                test_fail(target_id)
        elif node.type_node == 4:
            propagated_group.add(node_id)
            in_hier = hiers_by_id[node.parent_id]
            test_fail(in_hier.out_id)
        # elif node.type_node == 5 (disqualified): stop, no propagation.

    test_fail(origin.id)

    # `downstream_group` holds link ids; the target node id for each one is
    # re-derived from `links_by_id` here rather than reused from the walk's own
    # `targets` (`next_node_ids`/`propagates_to_node_ids`). For an ordinary
    # `RBDNode` target the value is the same either way, but for a link whose
    # own target is a `RBDHier` (a node connecting directly into a hierarchy,
    # `connect_t_ref="RBDHier"`, see `_node_forward_targets`) the raw
    # `links_by_id[lid].connect_to_id` is the hierarchy's own id, not a real
    # `RBDNode` id, so it must redirect to that hierarchy's `in_id` the same
    # way `_node_forward_targets` does. `propagated_group` (built from the
    # walk's already-redirected `target_id`s) is compared against the same
    # resolved ids.
    def _resolved_target_node_id(lid: int) -> int:
        link = links_by_id[lid]
        if link.connect_t_ref == "RBDHier":
            return hiers_by_id[link.connect_to_id].in_id
        return link.connect_to_id

    kept = [lid for lid in downstream_group if _resolved_target_node_id(lid) not in propagated_group]
    origin.propagates_to_node_ids = [_resolved_target_node_id(lid) for lid in kept]
    origin.propagates_to_link_ids = kept
    efpa_tested.add(origin.id)


def _use_reference(monkeypatch) -> None:
    """Run the pass with the reference walk and no look-ahead results."""
    monkeypatch.setattr(
        graph, "_run_test_fail",
        lambda origin, nodes, hiers, links, tested, *_ahead:
        reference_run_test_fail(origin, nodes, hiers, links, tested))


def _propagation(model) -> dict[int, tuple[list[int], list[int]]]:
    return {nid: (list(n.propagates_to_node_ids), list(n.propagates_to_link_ids))
            for nid, n in model.nodes_by_id.items()}


@pytest.mark.parametrize("fixture", FIXTURES)
def test_the_walk_prunes_exactly_as_the_routine_it_replaced(fixture, monkeypatch):
    path = str(GOLD / fixture / "input.rbd")
    new = _propagation(load_model(load_rbd(path)))
    _use_reference(monkeypatch)
    old = _propagation(load_model(load_rbd(path)))
    assert new == old


def test_a_chain_of_a_thousand_nodes_loads(tmp_path):
    """The recursive walk raised RecursionError on a long enough series chain."""
    from raptor.api.workspace import Workspace
    ws = Workspace(str(tmp_path))
    mid = ws.create_model("chain", template="series", n=1000)["model_id"]
    model = load_model(ws.get(mid).rbd)
    assert len(model.nodes_by_id) >= 1000


# --- the downstream-first look-ahead (`graph._look_ahead`) -------------------
#
# The look-ahead works a result out before the pass reaches that node, on the
# argument that for a walk confined to one hierarchy level the order cannot
# matter. These tests hold it to the reference on everything there is: every
# shipped and fixture `.rbds` (the hierarchical ones are the hard case) and
# randomly wired graphs with re-converging paths.

REPO = pathlib.Path(__file__).resolve().parents[2]
RBDS_MODELS = sorted(
    {p for pattern in ("new_docs/models/*.rbds", "tests/fixtures/**/*.rbds",
                       "raptor/**/*.rbds")
     for p in glob.glob(str(REPO / pattern), recursive=True)})


@pytest.mark.parametrize("path", RBDS_MODELS,
                         ids=[os.path.relpath(p, REPO) for p in RBDS_MODELS])
def test_look_ahead_agrees_with_the_reference_on_rbds_models(path, monkeypatch):
    from raptor.core.engine.errors import UnrunnableModel
    from raptor.core.fileio.rbds import load_rbds
    try:
        new = _propagation(load_model(load_rbds(path)))
    except (UnrunnableModel, NotImplementedError) as exc:
        new = repr(exc)
    _use_reference(monkeypatch)
    try:
        old = _propagation(load_model(load_rbds(path)))
    except (UnrunnableModel, NotImplementedError) as exc:
        old = repr(exc)
    assert new == old


def _random_spec(seed: int) -> dict:
    """A randomly wired flat model: series blocks, parallel groups, and
    extra block paths that skip ahead, so paths re-converge on nodes that
    need several of them."""
    import random
    rng = random.Random(seed)
    blocks, links = [], []
    nodes = [{"name": "Start", "type": "start"}, {"name": "End", "type": "end"}]
    regular: list[str] = []

    def block() -> str:
        name = f"B{len(blocks) + 1}"
        blocks.append({"name": name, "fail_dist": "Exponential",
                       "fail_params": [1000], "repair_dist": "Lognormal",
                       "repair_params": [8, 2]})
        return name

    def node() -> str:
        name = f"N{len(regular) + 1}"
        nodes.append({"name": name, "type": "regular"})
        regular.append(name)
        return name

    prev = "Start"
    for _ in range(rng.randint(4, 14)):
        nxt = node()
        for _ in range(rng.choice((1, 1, 2, 3, 4))):
            b = block()
            links.append({"from": prev, "to": b})
            links.append({"from": b, "to": nxt})
        prev = nxt
    links.append({"from": prev, "to": "End"})
    # Skip-ahead paths between regular nodes (never backwards: no loops).
    for _ in range(rng.randint(0, 6)):
        if len(regular) < 3:
            break
        i = rng.randrange(0, len(regular) - 1)
        j = rng.randrange(i + 1, len(regular))
        b = block()
        links.append({"from": regular[i], "to": b})
        links.append({"from": b, "to": regular[j]})
    rng.shuffle(nodes)          # the pass runs in node order: vary it
    return {"blocks": blocks, "nodes": nodes, "links": links}


@pytest.mark.parametrize("seed", range(60))
def test_look_ahead_agrees_with_the_reference_on_random_graphs(seed, monkeypatch):
    from raptor.api import build
    from raptor.core.engine.errors import UnrunnableModel
    rbd = build.build_from_spec(_random_spec(seed))
    try:
        new = _propagation(load_model(rbd))
    except (UnrunnableModel, NotImplementedError) as exc:
        new = repr(exc)
    _use_reference(monkeypatch)
    try:
        old = _propagation(load_model(rbd))
    except (UnrunnableModel, NotImplementedError) as exc:
        old = repr(exc)
    assert new == old


def test_look_ahead_is_used_on_a_long_series(tmp_path):
    """The point of it: a long chain no longer walks to the end from every
    node. Counted in steps, not seconds, so the test is not a stopwatch."""
    from raptor.api.workspace import Workspace
    ws = Workspace(str(tmp_path))
    mid = ws.create_model("chain", template="series", n=400)["model_id"]
    model = load_model(ws.get(mid).rbd)
    origins = [n for n in model.nodes_by_id.values() if n.type_node in (2, 5)]
    ahead, _levels = graph._look_ahead(origins, model.nodes_by_id,
                                       model.hiers_by_id, model.links_by_id)
    assert len(ahead) == len(origins)

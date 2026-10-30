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


def _propagation(model) -> dict[int, tuple[list[int], list[int]]]:
    return {nid: (list(n.propagates_to_node_ids), list(n.propagates_to_link_ids))
            for nid, n in model.nodes_by_id.items()}


@pytest.mark.parametrize("fixture", FIXTURES)
def test_the_walk_prunes_exactly_as_the_routine_it_replaced(fixture, monkeypatch):
    path = str(GOLD / fixture / "input.rbd")
    new = _propagation(load_model(load_rbd(path)))
    monkeypatch.setattr(graph, "_run_test_fail", reference_run_test_fail)
    old = _propagation(load_model(load_rbd(path)))
    assert new == old


def test_a_chain_of_a_thousand_nodes_loads(tmp_path):
    """The recursive walk raised RecursionError on a long enough series chain."""
    from raptor.api.workspace import Workspace
    ws = Workspace(str(tmp_path))
    mid = ws.create_model("chain", template="series", n=1000)["model_id"]
    model = load_model(ws.get(mid).rbd)
    assert len(model.nodes_by_id) >= 1000

"""Regression tests for `rbdlib.fix_link_counts`.

`fix_link_counts` used to hard-code a Type-3 (End) node's `n` to `1`
regardless of its real incoming link count. Real Raptor accepts an End
node with several paths converging on it directly -- the checked-in
golden fixtures `MixedDepPerc` and `WakeOrderDependent` both have a
Type-3 End with `n=2` and two real incoming links, and across every
`.rbd` under `tests/`, 195 of 196 Type-3 nodes have `n` exactly equal to
their real incoming link count (the one exception, `MultiOutLink`, is a
deliberately-broken fixture used to test the fan-out validation rule).

Because every MCP topology-editing tool (`add_block`, `add_node`,
`connect`, `delete_element`, `build_model`) calls `fix_link_counts` to
keep `In`/`Out`/`n` in sync, the old hard-coded `n=1` silently corrupted
any legitimately-multi-input End node the moment an unrelated edit
touched the model -- turning a valid file into one `builder.validate()`
then (correctly) rejects as an Access-Violation risk.
"""
from __future__ import annotations

from pathlib import Path

from raptor.core.fileio.rbdlib import fix_link_counts

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


def _link(lid, ftype, fid, ttype, tid):
    return {"ID": str(lid), "FromType": ftype, "FromID": str(fid),
            "ToType": ttype, "ToID": str(tid)}


def test_end_node_n_matches_two_real_incoming_links():
    """A Type-3 End node with 2 real incoming links keeps n=2, not 1."""
    nodes = [{"ID": "1", "Type": "1", "n": "0", "Out": "0"},   # Start
             {"ID": "2", "Type": "3", "n": "1", "Out": "0"}]   # End (stale n)
    links = [_link(1, "Node", 5, "Node", 2), _link(2, "Node", 6, "Node", 2)]
    fix_link_counts([], nodes, [], links)
    end = next(n for n in nodes if n["ID"] == "2")
    assert end["n"] == "2"


def test_end_node_n_matches_single_incoming_link():
    """The ordinary single-path case still gives n=1."""
    nodes = [{"ID": "1", "Type": "1", "n": "0", "Out": "0"},
             {"ID": "2", "Type": "3", "n": "1", "Out": "0"}]
    links = [_link(1, "Node", 5, "Node", 2)]
    fix_link_counts([], nodes, [], links)
    end = next(n for n in nodes if n["ID"] == "2")
    assert end["n"] == "1"


def test_hierarchy_out_marker_stays_pinned_to_one():
    """Type-5 (hierarchy Out marker) is left at the historical n=1
    convention -- no golden fixture ever shows it otherwise, unlike
    Type-3, so this hasn't been changed."""
    nodes = [{"ID": "1", "Type": "5", "n": "1", "Out": "0"}]
    links = [_link(1, "Node", 9, "Node", 1), _link(2, "Node", 10, "Node", 1)]
    fix_link_counts([], nodes, [], links)
    marker = nodes[0]
    assert marker["n"] == "1"


def test_start_node_n_stays_zero():
    nodes = [{"ID": "1", "Type": "1", "n": "0", "Out": "2"}]
    links = [_link(1, "Node", 1, "Block", 5), _link(2, "Node", 1, "Block", 6)]
    fix_link_counts([], nodes, [], links)
    assert nodes[0]["n"] == "0"


def test_golden_multi_input_end_nodes_are_unaffected_by_fix_link_counts():
    """Loading the real fixtures and re-deriving counts must reproduce
    their own checked-in n, not silently rewrite it to 1."""
    from raptor.core.fileio.rbd import load_rbd
    for fixture in ("MixedDepPerc", "WakeOrderDependent"):
        rbd = load_rbd(str(GOLDEN / fixture / "input.rbd"))
        node_sec = rbd.section("NODE_DATA")
        link_sec = rbd.section("LINK_DATA")
        end_before = next(n for n in node_sec.rows if n.get("Type") == "3")
        assert end_before["n"] == "2", fixture  # sanity: the fixture itself
        fix_link_counts(rbd.section("BLOCK_DATA").rows, node_sec.rows,
                        rbd.section("EVENT_DATA").rows, link_sec.rows)
        end_after = next(n for n in node_sec.rows if n.get("Type") == "3")
        assert end_after["n"] == "2", (
            f"{fixture}: fix_link_counts corrupted End node n to "
            f"{end_after['n']!r}"
        )


def test_editing_elsewhere_in_a_multi_input_end_model_preserves_it():
    """End-to-end: opening MixedDepPerc via the Workspace and running an
    unrelated MCP edit tool (add_node) must not corrupt its End node's
    n=2 -- the exact regression this bug caused."""
    import tempfile
    from raptor.api.build import add_node
    from raptor.api.workspace import Workspace

    ws = Workspace(root=Path(tempfile.mkdtemp()))
    model_id = ws.open_model(str(GOLDEN / "MixedDepPerc" / "input.rbd"))["model_id"]
    handle = ws.get(model_id)
    end_before = next(n for n in handle.rbd.section("NODE_DATA").rows
                      if n.get("Type") == "3")
    assert end_before["n"] == "2"

    add_node(ws, model_id, "ScratchNode", type="regular")

    end_after = next(n for n in handle.rbd.section("NODE_DATA").rows
                     if n.get("Type") == "3")
    assert end_after["n"] == "2", (
        "an unrelated add_node call corrupted the End node's n back to 1"
    )

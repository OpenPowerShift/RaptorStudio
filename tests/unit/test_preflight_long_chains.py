"""The cycle check handles chains longer than Python's recursion limit (#150)."""
from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.core.model import Link
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.preflight import preflight_issues

LONG = sys.getrecursionlimit() + 500


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _view(n: int) -> DiagramView:
    blocks = [{"name": f"B{i}"} for i in range(n)]
    links = ([{"from": "Start", "to": "B0"}]
             + [{"from": f"B{i}", "to": f"B{i + 1}"} for i in range(n - 1)]
             + [{"from": f"B{n - 1}", "to": "End"}])
    v = DiagramView()
    v.load_rbd(build_from_spec({
        "name": "Chain", "blocks": blocks,
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": links, "system": {"term_condition": 100, "num_runs": 1}},
        auto_layout=False))
    return v


def _cycles(view) -> list[str]:
    issues = preflight_issues(view._rbd_ref, view,
                              lambda kind, oid: f"{kind} {oid}")
    return [i for i in issues if "cyclic connection" in i]


def test_a_chain_longer_than_the_recursion_limit_checks_without_error(qapp):
    view = _view(LONG)
    assert _cycles(view) == []


def test_a_cycle_at_the_end_of_a_very_long_chain_is_still_found(qapp):
    view = _view(LONG)
    last, mid = f"B{LONG - 1}", f"B{LONG - 10}"
    ids = {b.name: b.id for b in view.blocks.values()}
    view.links.append(Link(object_num=99001, connect_from_id=ids[last],
                           connect_f_ref="RBDBlock",
                           connect_to_id=ids[mid], connect_t_ref="RBDBlock"))
    found = _cycles(view)
    assert len(found) == 1
    assert "B" in found[0] and " -> " in found[0]


def test_a_block_linked_to_itself_is_a_cycle(qapp):
    view = _view(5)
    b = next(iter(view.blocks.values()))
    view.links.append(Link(object_num=99002, connect_from_id=b.id,
                           connect_f_ref="RBDBlock", connect_to_id=b.id,
                           connect_t_ref="RBDBlock"))
    found = _cycles(view)
    assert len(found) == 1 and b.name in found[0]


def test_a_two_block_loop_names_the_blocks_in_order(qapp):
    view = _view(5)
    ids = {b.name: b.id for b in view.blocks.values()}
    view.links.append(Link(object_num=99003, connect_from_id=ids["B3"],
                           connect_f_ref="RBDBlock", connect_to_id=ids["B2"],
                           connect_t_ref="RBDBlock"))
    found = _cycles(view)
    assert len(found) == 1
    assert "B2 -> B3" in found[0]


def test_a_diamond_is_not_a_cycle(qapp):
    """Shared downstream nodes are reached twice; that is not a loop."""
    v = DiagramView()
    v.load_rbd(build_from_spec({
        "name": "Diamond",
        "blocks": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"},
                  {"id": 3, "name": "Join", "type": "regular"}],
        "links": [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
                  {"from": "A", "to": "Join"}, {"from": "B", "to": "Join"},
                  {"from": "Join", "to": "C"}, {"from": "C", "to": "End"}],
        "system": {"term_condition": 100, "num_runs": 1}}, auto_layout=False))
    assert _cycles(v) == []

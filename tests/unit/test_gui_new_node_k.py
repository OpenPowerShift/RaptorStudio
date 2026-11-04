"""A new connect node starts as a fresh node needing one good path, and its
k/n badge follows every k edit and link change.

A placed node used to copy the first node row of its type -- or, with no
other connect node in the model, the Start node (k=0) -- so its first
incoming link was refused with "Good paths must be between 1 and 1!", and
it inherited a neighbour's k, dependency, standby and phases. The k/n
badge was drawn only on a full canvas rebuild, so a k edit kept showing
the old value.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from raptor.api.build import build_from_spec  # noqa: E402
from raptor.gui.ui import msgbox  # noqa: E402
from raptor.gui.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.gui

_BLOCK = {"fail_dist": 4, "fail_params": [100.0, 0, 0],
          "repair_dist": 4, "repair_params": [10.0, 0, 0]}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def refusals(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(msgbox, "warning",
                        lambda _p, title, text, *a, **k: shown.append(
                            f"{title}: {text}"))
    return shown


def _series():
    """Start -> A -> End: no connect node to copy, only Start, which a
    model file stores with k=0."""
    rbd = build_from_spec({
        "blocks": [{"name": "A", **_BLOCK}],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}]})
    for row in rbd.sections["NODE_DATA"].rows:
        if row.get("Type") == "1":
            row["k"] = "0"
    rbd.sections["NODE_DATA"].rows.sort(key=lambda r: r.get("Type") != "1")
    return rbd


def _two_of_two():
    """A and B in parallel into a 2-of-2 node J, the first connect node
    in the file (the one a new node used to copy)."""
    return build_from_spec({
        "blocks": [{"name": "A", **_BLOCK}, {"name": "B", **_BLOCK}],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "J", "type": 2, "k": 2, "n": 2, "out": 1},
                  {"name": "S", "type": 2, "k": 1, "n": 1, "out": 2},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "S"}, {"from": "S", "to": "A"},
                  {"from": "S", "to": "B"}, {"from": "A", "to": "J"},
                  {"from": "B", "to": "J"}, {"from": "J", "to": "End"}]})


def _window(rbd):
    w = MainWindow()
    w._load_into_app(rbd, None)
    return w, w._diagram_dock.view


def _close(w):
    w._dirty = False
    w.close()


@pytest.mark.parametrize("model", [_series, _two_of_two])
def test_a_new_node_needs_one_good_path_and_takes_a_link(qapp, refusals,
                                                         model):
    w, v = _window(model())
    nid = v.add_node("node_new", 40.0, 40.0)
    node = v.nodes[nid]
    assert node.good_paths == 1
    assert node.dependency_num == 0 and not node.cold_standby
    bid = v.add_block("Feeder", 30.0, 40.0)
    assert v.connect_elements("Block", bid, "Node", nid) is not None
    assert refusals == []
    assert any(link.connect_t_ref == "RBDNode" and link.connect_to_id == nid
               for link in v.links)
    _close(w)


def test_the_badge_follows_k_and_the_links(qapp, refusals):
    w, v = _window(_two_of_two())
    jid = next(n for n, node in v.nodes.items() if node.node_name == "J")
    item = v._node_items[jid]
    assert item._inner_label.toPlainText() == "2/2"
    v.set_node_good_paths(jid, 1)
    assert item._inner_label.toPlainText() == "1/2"
    v._stack.undo()
    assert item._inner_label.toPlainText() == "2/2"
    # A third path in: n changes without any k edit.
    v.set_node_good_paths(jid, 1)
    sid = next(n for n, node in v.nodes.items() if node.node_name == "S")
    cid = v.add_block("C", 20.0, 60.0)
    v.connect_elements("Node", sid, "Block", cid)
    v.connect_elements("Block", cid, "Node", jid)
    assert refusals == []
    assert item._inner_label.toPlainText() == "1/3"
    _close(w)


def test_placing_a_node_says_how_to_make_it_k_of_n(qapp):
    w, v = _window(_series())
    said: list[str] = []
    v.status_message_sink = said.append
    v.tool_mode = "add_node"
    v._handle_tool_click(QPointF(200.0, 200.0))
    assert any("Required good (k)" in s for s in said)
    _close(w)


def test_a_refusal_does_not_name_another_product(qapp, refusals):
    w, v = _window(_two_of_two())
    jid = next(n for n, node in v.nodes.items() if node.node_name == "J")
    v.set_node_good_paths(jid, 5)          # only 2 paths in
    assert refusals and all("Raptor" not in r for r in refusals)
    assert v.nodes[jid].good_paths == 2    # refused, left as it was
    _close(w)

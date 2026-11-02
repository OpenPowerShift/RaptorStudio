"""Hierarchy traversal parses one level, not the whole model."""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.gui.ui import diagram as diagram_mod
from raptor.gui.ui.diagram import DiagramView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


N = 2_000
HID = 100


def _model():
    blocks = ([{"name": "HomeBlock", "id": 1}]
              + [{"name": f"B{i}", "id": 10 + i, "parent": HID}
                 for i in range(N)])
    nodes = [
        {"id": 2, "name": "Start", "type": "start"},
        {"id": 3, "name": "End", "type": "end"},
        {"id": 4, "name": "HIn", "type": "in", "parent": HID},
        {"id": 5, "name": "HOut", "type": "out", "parent": HID},
    ]
    links = [
        {"from": "Start", "to": "HomeBlock"},
        {"from": "HomeBlock", "to": "HIn"},
        {"from": "HOut", "to": "End"},
        {"from": "HIn", "to": "B0"},
    ]
    links += [{"from": f"B{i}", "to": f"B{i + 1}"}
              for i in range(N - 1)]
    links += [{"from": f"B{N - 1}", "to": "HOut"}]
    return build_from_spec({
        "name": "BigHierarchy",
        "blocks": blocks,
        "nodes": nodes,
        "links": links,
        "hierarchies": [{"id": HID, "name": "Big",
                         "in_id": 4, "out_id": 5}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)


def test_returning_home_parses_only_home_rows(qapp, monkeypatch):
    view = DiagramView()
    view.load_rbd(_model())
    view.enter_level(HID)
    original = diagram_mod.block_from_row
    seen: list[int] = []

    def counting(row):
        seen.append(1)
        return original(row)

    monkeypatch.setattr(diagram_mod, "block_from_row", counting)
    view.go_home()
    # The home level has one real block plus the ID=0 template row.
    assert len(seen) <= 2, f"parsed {len(seen)} block rows for a one-block level"


def test_enter_and_exit_stay_within_a_time_budget(qapp):
    view = DiagramView()
    view.load_rbd(_model())
    started = time.perf_counter()
    view.enter_level(HID)
    enter = time.perf_counter() - started
    started = time.perf_counter()
    view.go_home()
    leave = time.perf_counter() - started
    # Generous bounds: this is a regression guard against O(whole model)
    # parsing creeping back, not a benchmark of the CI machine.
    assert enter < 12.0, f"entering {N} elements took {enter:.1f}s"
    assert leave < 3.0, f"leaving took {leave:.1f}s"


def test_links_on_a_level_have_both_ends_visible(qapp):
    view = DiagramView()
    view.load_rbd(_model())
    view.enter_level(HID)
    visible = {"Block": set(view.blocks), "Node": set(view.nodes),
               "Event": set(view.events), "Hier": set(view.hiers)}
    for link in view.links:
        for ref, ident in ((link.connect_f_ref, link.connect_from_id),
                           (link.connect_t_ref, link.connect_to_id)):
            kind = ref.replace("RBD", "", 1)
            assert ident in visible[kind], (
                f"link {link.object_num} has hidden endpoint {kind}:{ident}")
    assert len(view.links) == N + 1

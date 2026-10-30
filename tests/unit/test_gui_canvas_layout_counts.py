"""Canvas geometry and live topology counts after edits.

Three faults reported from use:
* labels were clipped because the block only widened for its title;
* the View ribbon's Show Phase control was too narrow and stayed enabled
  on a model with no phases;
* Auto Layout left events where they were, because it followed the
  hierarchy ParentId (an EVENT_DATA field), not the flow;
* deleting a node removed links that touched a *block* with the same id.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("RAPTOR_INSPECTOR_EAGER_TABS", "1")

from PySide6.QtWidgets import QApplication  # noqa: E402

from raptor.api.build import build_from_spec  # noqa: E402
from raptor.core.fileio.rbd import load_rbd  # noqa: E402
from raptor.gui.ui import settings  # noqa: E402
from raptor.gui.ui.diagram_items import BLOCK_W  # noqa: E402
from raptor.gui.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.gui

GOLD = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _default_labels():
    settings.remove("appearance/block_labels")
    yield
    settings.remove("appearance/block_labels")


def _window(qapp, rbd):
    w = MainWindow()
    w._load_into_app(rbd, None)
    return w


def _collision_model():
    """Block 1 and Node 1 share an id; Start/End use 14/15."""
    return build_from_spec({
        "blocks": [{"id": 1, "name": "B1"},
                   {"id": 2, "name": "B2"}],
        "nodes": [{"id": 14, "name": "Start", "type": "start"},
                  {"id": 1, "name": "Join", "type": "regular",
                   "k": 1, "n": 1},
                  {"id": 15, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "B1"},
                  {"from": "B1", "to": "B2"},
                  {"from": "B2", "to": "Join"},
                  {"from": "Join", "to": "End"}],
    })


def _event_model():
    return build_from_spec({
        "blocks": [{"id": 1, "name": "B1"}, {"id": 2, "name": "B2"}],
        "nodes": [{"id": 14, "name": "Start", "type": "start"},
                  {"id": 15, "name": "End", "type": "end"}],
        "events": [{"id": 1, "name": "Ev"}],
        "links": [{"from": "Start", "to": "B1"},
                  {"from": "B1", "to": "Ev"},
                  {"from": "Ev", "to": "B2"},
                  {"from": "B2", "to": "End"}],
    })


# ---------------------------------------------------------------------------
# Labels widen the block
# ---------------------------------------------------------------------------

def test_a_long_label_widens_the_block(qapp):
    rbd = build_from_spec({
        "blocks": [{"id": 1, "name": "B1", "fail_dist": 19,
                    "fail_params": [1234567],
                    "repair_dist": 19, "repair_params": [987654]}],
        "nodes": [{"id": 14, "name": "Start", "type": "start"},
                  {"id": 15, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "B1"},
                  {"from": "B1", "to": "End"}],
    })
    w = _window(qapp, rbd)
    item = w._diagram_dock.view._block_items[1]
    assert item._sub.toPlainText()
    assert item.rect().width() > BLOCK_W
    assert item._sub.boundingRect().width() <= item.rect().width()
    w._dirty = False
    w.close()


# ---------------------------------------------------------------------------
# Show Phase control
# ---------------------------------------------------------------------------

def test_show_phase_is_disabled_without_phases(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(
        os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd")), None)
    combo = w._block_phase_combo
    assert not combo.isEnabled()
    assert combo.currentData() == -1
    assert "no phases" in combo.toolTip().lower()
    w._dirty = False
    w.close()


def test_show_phase_is_wide_and_enabled_with_phases(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(
        os.path.join(GOLD, "PhasingDemo", "input.rbd")), None)
    combo = w._block_phase_combo
    assert combo.isEnabled()
    assert combo.width() >= 190
    assert combo.count() == 7              # Model + six PHASES rows
    w._dirty = False
    w.close()


# ---------------------------------------------------------------------------
# Auto Layout includes events; event shows its probability second line
# ---------------------------------------------------------------------------

def test_event_shows_probability_on_a_second_line_and_widens(qapp):
    w = _window(qapp, _event_model())
    ev = w._diagram_dock.view._event_items[1]
    assert ev._prob_label.toPlainText() == "P=1"
    assert ev._label.toPlainText() == "Ev"
    assert ev._prob_label.y() > ev._label.y()
    narrow = ev._half_w
    ev.set_name("A much longer event name")
    assert ev._half_w > narrow
    w._dirty = False
    w.close()


def test_auto_layout_places_an_event_on_the_flow(qapp):
    w = _window(qapp, _event_model())
    v = w._diagram_dock.view
    event = v._event_items[1]
    event.setPos(5000, 5000)
    v.auto_layout()
    event = v._event_items[1]              # rebuilt by auto_layout
    b1 = v._block_items[1].scenePos().x()
    b2 = v._block_items[2].scenePos().x()
    assert min(b1, b2) < event.scenePos().x() < max(b1, b2)
    assert event.scenePos().x() != 5000
    w._dirty = False
    w.close()


def test_the_auto_layout_button_uses_the_shorter_gui_budget(qapp, monkeypatch):
    """The button blocks the UI thread, so it must not get the engine's
    30 s default."""
    from raptor.core.fileio import legacy_layout
    seen = []
    real = legacy_layout.layout_container_positions

    def spy(*args, **kwargs):
        seen.append(legacy_layout.refine_budget_s())
        return real(*args, **kwargs)

    w = _window(qapp, _event_model())     # building the model lays it out too
    monkeypatch.setattr(legacy_layout, "layout_container_positions", spy)
    w._diagram_dock.view.auto_layout()
    assert seen == [legacy_layout.GUI_REFINE_BUDGET_S]
    assert legacy_layout.GUI_REFINE_BUDGET_S < legacy_layout.REFINE_BUDGET_S
    w._dirty = False
    w.close()


@pytest.mark.parametrize("suffix", [".rbds", ".rbd"])
def test_saving_from_the_gui_uses_the_shorter_gui_budget(qapp, monkeypatch,
                                                         tmp_path, suffix):
    """Saving can re-lay out a crammed model, on the UI thread."""
    from raptor.core.fileio import legacy_layout
    seen = []
    real = legacy_layout.normalize_legacy_layout

    def spy(*args, **kwargs):
        seen.append(legacy_layout.refine_budget_s())
        return real(*args, **kwargs)

    w = _window(qapp, _event_model())
    monkeypatch.setattr(legacy_layout, "normalize_legacy_layout", spy)
    assert w._write_model(str(tmp_path / f"m{suffix}"))
    assert seen and set(seen) == {legacy_layout.GUI_REFINE_BUDGET_S}
    w._dirty = False
    w.close()


# ---------------------------------------------------------------------------
# Deleting with cross-kind id collisions, and n recounting
# ---------------------------------------------------------------------------

def _link_pairs(win):
    return [(r.get("FromType"), r.get("FromID"),
             r.get("ToType"), r.get("ToID"))
            for r in win._rbd.sections["LINK_DATA"].rows]


def _node_row(win, nid):
    return next(r for r in win._rbd.sections["NODE_DATA"].rows
                if int(r["ID"]) == nid)


def test_deleting_a_node_keeps_links_whose_block_shares_its_id(qapp):
    w = _window(qapp, _collision_model())
    v = w._diagram_dock.view
    v._scene.clearSelection()
    v._node_items[1].setSelected(True)
    v.delete_selected()
    pairs = _link_pairs(w)
    assert ("Block", "1", "Block", "2") in pairs, pairs
    assert len(pairs) == 2, pairs
    w._dirty = False
    w.close()


def test_adding_and_reconnecting_recounts_node_n(qapp):
    w = _window(qapp, _collision_model())
    v = w._diagram_dock.view
    nid = v.add_node("Extra", 5, 5)
    assert next(r for r in v._rbd_ref.sections["NODE_DATA"].rows
                if int(r["ID"]) == nid)["n"] == "0"
    b3 = v.add_block("C1", 6, 6)
    b4 = v.add_block("C2", 7, 7)
    v.connect_elements("Block", b3, "Node", nid)
    v.connect_elements("Block", b4, "Node", nid)
    assert next(r for r in v._rbd_ref.sections["NODE_DATA"].rows
                if int(r["ID"]) == nid)["n"] == "2"
    # Delete one of those links: n follows the real topology again.
    li = next(it for it in v._links
              if it._link_num is not None
              and _link_row(v, it).get("ToType") == "Node"
              and _link_row(v, it).get("ToID") == str(nid))
    v._scene.clearSelection()
    li.setSelected(True)
    v.delete_selected()
    assert next(r for r in v._rbd_ref.sections["NODE_DATA"].rows
                if r["ID"] == str(nid))["n"] == "1"
    w._dirty = False
    w.close()


def _link_row(v, link_item) -> dict:
    return next(r for r in v._rbd_ref.sections["LINK_DATA"].rows
                if int(r["ID"]) == link_item._link_num)

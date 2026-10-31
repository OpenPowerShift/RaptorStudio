"""Anchoring to annotations and the `location` vocabulary.

The anchor object used to say ``side`` and could only name an element.
It now says ``location``, accepts ``center``, and can name another
annotation, whose bounding box is the placement target. These tests pin
the geometry, the stored shape, chained following and the cycle guard.
"""
from __future__ import annotations

import pytest

from raptor.api.annotations import (
    follow_layout,
    place_annotation,
    update_annotation,
)
from raptor.api.build import BuildError
from raptor.api.workspace import Workspace
from raptor.gui.new_model import NEW_MODEL_TEXT
from raptor.core.fileio.rbd import parse_rbd


@pytest.fixture
def rbd():
    return parse_rbd(NEW_MODEL_TEXT)


def _workspace(tmp_path):
    ws = Workspace(tmp_path / "ws")
    mid = ws.register_model(parse_rbd(NEW_MODEL_TEXT), name="m")["model_id"]
    return ws, mid


class TestLocation:
    def test_center_puts_the_box_over_the_target(self, rbd):
        target = place_annotation(rbd, kind="ellipse",
                                  x=100.0, y=100.0, w=100.0, h=60.0)
        note = place_annotation(rbd, kind="text", text="over",
                                adjacent_to=f"Annotation:{target['id']}",
                                location="center")
        assert note["x"] == pytest.approx(
            target["x"] + target["w"] / 2 - note["w"] / 2)
        assert note["y"] == pytest.approx(
            target["y"] + target["h"] / 2 - note["h"] / 2)

    def test_center_ignores_the_gap(self, rbd):
        target = place_annotation(rbd, kind="rect",
                                  x=0.0, y=0.0, w=100.0, h=50.0)
        a = place_annotation(rbd, kind="text", text="t",
                             adjacent_to=f"Annotation:{target['id']}",
                             location="center", gap=0.0)
        b = place_annotation(rbd, kind="text", text="t",
                             adjacent_to=f"Annotation:{target['id']}",
                             location="center", gap=500.0)
        assert (a["x"], a["y"]) == (b["x"], b["y"])

    def test_the_default_location_is_above(self, rbd):
        note = place_annotation(rbd, kind="text", text="t", adjacent_to=1)
        assert note["anchor"]["location"] == "above"

    def test_the_anchor_stores_location_not_side(self, rbd):
        note = place_annotation(rbd, kind="text", text="t",
                                adjacent_to=1, location="left")
        assert note["anchor"]["location"] == "left"
        assert "side" not in note["anchor"]

    @pytest.mark.parametrize("bad", ["diagonal", "", "Centre"])
    def test_unknown_location_is_rejected(self, rbd, bad):
        with pytest.raises(BuildError) as exc:
            place_annotation(rbd, kind="text", text="t", x=1.0, y=1.0,
                             location=bad)
        assert "location" in str(exc.value)


class TestAnnotationTargets:
    def test_anchor_to_an_annotation_by_id(self, rbd):
        target = place_annotation(rbd, kind="rect",
                                  x=100.0, y=100.0, w=80.0, h=40.0)
        note = place_annotation(rbd, kind="text", text="next",
                                adjacent_to=f"Annotation:{target['id']}",
                                location="right", gap=10.0)
        assert note["anchor"]["ref"] == f"Annotation:{target['id']}"
        assert note["x"] == pytest.approx(target["x"] + target["w"] + 10.0)

    def test_anchor_to_an_annotation_by_unique_label(self, rbd):
        target = place_annotation(rbd, kind="rect", text="Pump bay",
                                  x=100.0, y=100.0, w=80.0, h=40.0)
        note = place_annotation(rbd, kind="text", text="next",
                                adjacent_to="Pump bay")
        assert note["anchor"]["ref"] == f"Annotation:{target['id']}"

    def test_duplicate_labels_are_ambiguous(self, rbd):
        place_annotation(rbd, kind="rect", text="same", x=0.0, y=0.0,
                         w=20.0, h=20.0)
        place_annotation(rbd, kind="rect", text="same", x=50.0, y=0.0,
                         w=20.0, h=20.0)
        with pytest.raises(BuildError) as exc:
            place_annotation(rbd, kind="text", text="t", adjacent_to="same")
        assert "ambiguous" in str(exc.value)

    def test_an_arrow_target_uses_its_endpoint_bounding_box(self, rbd):
        arrow = place_annotation(rbd, kind="arrow",
                                 x1=0.0, y1=0.0, x2=100.0, y2=50.0)
        note = place_annotation(rbd, kind="text", text="over",
                                adjacent_to=f"Annotation:{arrow['id']}",
                                location="center")
        assert note["x"] + note["w"] / 2 == pytest.approx(50.0)
        assert note["y"] + note["h"] / 2 == pytest.approx(25.0)

    def test_unknown_annotation_id_is_rejected(self, rbd):
        with pytest.raises(BuildError) as exc:
            place_annotation(rbd, kind="text", text="t",
                             adjacent_to="Annotation:999")
        assert "999" in str(exc.value)

    def test_a_cycle_is_rejected(self, tmp_path):
        ws, mid = _workspace(tmp_path)
        rbd = ws.get(mid).rbd
        first = place_annotation(rbd, kind="text", text="a", x=0.0, y=0.0)
        place_annotation(rbd, kind="text", text="b",
                         adjacent_to=f"Annotation:{first['id']}")
        with pytest.raises(BuildError) as exc:
            update_annotation(ws, mid, first["id"],
                              {"adjacent_to": "Annotation:2"})
        assert "cycle" in str(exc.value)


class TestChainedFollowing:
    def test_a_chain_follows_its_root_on_layout(self, rbd):
        root = place_annotation(rbd, kind="rect",
                                x=100.0, y=100.0, w=100.0, h=60.0)
        middle = place_annotation(rbd, kind="text", text="middle",
                                  adjacent_to=f"Annotation:{root['id']}",
                                  location="center")
        leaf = place_annotation(rbd, kind="text", text="leaf",
                                adjacent_to=f"Annotation:{middle['id']}",
                                location="below")
        before_leaf = (leaf["x"], leaf["y"])
        root["x"] += 40.0
        assert follow_layout(rbd) == 2
        assert middle["x"] != before_leaf[0]
        assert leaf["x"] == pytest.approx(middle["x"])
        assert leaf["y"] > middle["y"]


class TestAnchorNamesFollowRenames:
    """`ref` is the authority; the stored names must not go stale."""

    def test_a_renamed_block_updates_target_but_not_ref(self, rbd):
        from raptor.api.annotations import refresh_anchor_names
        row = next(r for r in rbd.section("BLOCK_DATA").rows if r["ID"] != "0")
        old = row["blockName"]
        note = place_annotation(rbd, kind="text", text="n", adjacent_to=old)
        ref = note["anchor"]["ref"]
        row["blockName"] = "Renamed"
        assert refresh_anchor_names(rbd) == 1
        assert note["anchor"]["target"] == "Renamed"
        assert note["anchor"]["ref"] == ref
        assert refresh_anchor_names(rbd) == 0          # idempotent

    def test_an_arrows_ends_follow_renames(self):
        from raptor.api import build
        from raptor.api.annotations import refresh_anchor_names
        rbd = build.build_from_spec(
            {"blocks": [{"name": "A", "x": 1.0, "y": 1.0},
                        {"name": "B", "x": 3.0, "y": 1.0}],
             "nodes": [{"name": "s", "type": 1, "x": 0.0, "y": 1.0},
                       {"name": "e", "type": 3, "x": 4.0, "y": 1.0}],
             "links": [{"from": "s", "to": "A"}, {"from": "A", "to": "B"},
                       {"from": "B", "to": "e"}]}, auto_layout=False)
        rows = [r for r in rbd.section("BLOCK_DATA").rows if r["ID"] != "0"]
        arrow = place_annotation(rbd, kind="arrow", from_=rows[0]["blockName"],
                                 to=rows[1]["blockName"])
        rows[1]["blockName"] = "Head"
        refresh_anchor_names(rbd)
        assert arrow["anchor"]["to"] == "Head"

    def test_editing_an_annotations_text_updates_anchors_to_it(self, rbd):
        from raptor.api.annotations import refresh_anchor_names
        root = place_annotation(rbd, kind="text", text="old label",
                                x=10.0, y=10.0)
        child = place_annotation(rbd, kind="text", text="c",
                                 adjacent_to=f"Annotation:{root['id']}")
        root["text"] = "new label"
        refresh_anchor_names(rbd)
        assert child["anchor"]["target"] == "new label"
        assert child["anchor"]["ref"] == f"Annotation:{root['id']}"

    def test_a_dangling_ref_is_left_alone(self, rbd):
        from raptor.api.annotations import refresh_anchor_names
        note = place_annotation(rbd, kind="text", text="n", x=1.0, y=1.0)
        note["anchor"] = {"target": "Gone", "ref": "Block:9999",
                          "location": "above", "gap": 12.0}
        assert refresh_anchor_names(rbd) == 0
        assert note["anchor"]["target"] == "Gone"

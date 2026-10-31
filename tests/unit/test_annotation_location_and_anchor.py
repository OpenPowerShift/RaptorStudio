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


class TestAnchorValidation:
    def _model(self):
        from raptor.api import build
        return build.build_from_spec(
            {"blocks": [{"name": "A", "x": 1.0, "y": 1.0}],
             "nodes": [{"name": "s", "type": 1, "x": 0.0, "y": 1.0},
                       {"name": "e", "type": 3, "x": 2.0, "y": 1.0}],
             "links": [{"from": "s", "to": "A"}, {"from": "A", "to": "e"}]},
            auto_layout=False)

    def _anchor_issues(self, rbd):
        from raptor.api.validate import issues
        return [i for i in issues(rbd) if i.element.startswith("Annotation")]

    def test_a_healthy_anchor_is_silent(self):
        rbd = self._model()
        place_annotation(rbd, kind="text", text="n", adjacent_to="A")
        assert self._anchor_issues(rbd) == []

    def test_a_deleted_target_is_a_warning(self):
        rbd = self._model()
        note = place_annotation(rbd, kind="text", text="n", adjacent_to="A")
        note["anchor"]["ref"] = "Block:99"           # hand-edited / deleted
        found = self._anchor_issues(rbd)
        assert len(found) == 1 and found[0].severity == "warning"
        assert "Block:99" in found[0].message

    def test_an_unknown_name_without_a_ref_is_a_warning(self):
        rbd = self._model()
        note = place_annotation(rbd, kind="text", text="n", adjacent_to="A")
        del note["anchor"]["ref"]
        note["anchor"]["target"] = "Nope"
        assert len(self._anchor_issues(rbd)) == 1

    def test_a_broken_arrow_end_is_reported(self):
        rbd = self._model()
        arrow = place_annotation(rbd, kind="arrow", from_="s", to="A")
        arrow["anchor"]["to_ref"] = "Block:42"
        assert len(self._anchor_issues(rbd)) == 1

    def test_a_hand_made_cycle_is_reported(self):
        rbd = self._model()
        a = place_annotation(rbd, kind="text", text="a", x=10.0, y=10.0)
        b = place_annotation(rbd, kind="text", text="b",
                             adjacent_to=f"Annotation:{a['id']}")
        a["anchor"] = {"target": "b", "ref": f"Annotation:{b['id']}",
                       "location": "right", "gap": 12.0}
        assert any("itself" in i.message for i in self._anchor_issues(rbd))

    def test_an_empty_anchor_is_reported(self):
        rbd = self._model()
        note = place_annotation(rbd, kind="text", text="n", x=1.0, y=1.0)
        note["anchor"] = {}
        assert len(self._anchor_issues(rbd)) == 1


class TestNudge:
    def _model(self):
        from raptor.api import build
        return build.build_from_spec(
            {"blocks": [{"name": "A", "x": 2.0, "y": 2.0},
                        {"name": "B", "x": 2.0, "y": 1.0}],
             "nodes": [{"name": "s", "type": 1, "x": 0.0, "y": 1.0},
                       {"name": "e", "type": 3, "x": 4.0, "y": 1.0}],
             "links": [{"from": "s", "to": "A"}, {"from": "A", "to": "B"},
                       {"from": "B", "to": "e"}]}, auto_layout=False)

    @staticmethod
    def _overlap(a, b):
        return (a["x"] < b["x"] + b["w"] and a["x"] + a["w"] > b["x"]
                and a["y"] < b["y"] + b["h"] and a["y"] + a["h"] > b["y"])

    def test_two_notes_wanting_the_same_spot_are_separated(self):
        rbd = self._model()
        one = place_annotation(rbd, kind="text", text="one", adjacent_to="A")
        two = place_annotation(rbd, kind="text", text="two", adjacent_to="A")
        assert self._overlap(one, two)            # same place when created
        follow_layout(rbd)
        assert not self._overlap(one, two)

    def test_a_note_is_moved_off_a_block_it_would_cover(self):
        rbd = self._model()
        # "below" A lands on B (one grid step away): it must be moved clear.
        note = place_annotation(rbd, kind="text", text="n", adjacent_to="A",
                                location="below", gap=1.0)
        from raptor.api.annotations import _element_boxes
        follow_layout(rbd)
        for _p, ref, l, t, r, b in _element_boxes(rbd):
            if ref == note["anchor"]["ref"]:
                continue
            assert not (note["x"] < r and note["x"] + note["w"] > l
                        and note["y"] < b and note["y"] + note["h"] > t), ref

    def test_the_preferred_location_is_kept_when_it_is_clear(self):
        rbd = self._model()
        note = place_annotation(rbd, kind="text", text="n", adjacent_to="A",
                                location="above")
        before = (note["x"], note["y"])
        follow_layout(rbd)
        assert (note["x"], note["y"]) == before

    def test_center_is_never_nudged(self):
        rbd = self._model()
        note = place_annotation(rbd, kind="text", text="n", adjacent_to="A",
                                location="center")
        before = (note["x"], note["y"])
        follow_layout(rbd)
        assert (note["x"], note["y"]) == before

    def test_nudging_is_idempotent(self):
        rbd = self._model()
        place_annotation(rbd, kind="text", text="one", adjacent_to="A")
        place_annotation(rbd, kind="text", text="two", adjacent_to="A")
        follow_layout(rbd)
        first = [(a["x"], a["y"]) for a in rbd.annotations]
        assert follow_layout(rbd) == 0
        assert [(a["x"], a["y"]) for a in rbd.annotations] == first

    def test_a_free_note_is_not_moved_but_is_avoided(self):
        rbd = self._model()
        note = place_annotation(rbd, kind="text", text="n", adjacent_to="A")
        free = place_annotation(rbd, kind="rect", x=note["x"], y=note["y"],
                                w=note["w"], h=note["h"])
        fx, fy = free["x"], free["y"]
        follow_layout(rbd)
        assert (free["x"], free["y"]) == (fx, fy)
        assert not self._overlap(note, free)

    def test_a_dependent_follows_a_nudged_note(self):
        rbd = self._model()
        place_annotation(rbd, kind="text", text="one", adjacent_to="A")
        two = place_annotation(rbd, kind="text", text="two", adjacent_to="A")
        three = place_annotation(rbd, kind="text", text="three",
                                 adjacent_to=f"Annotation:{two['id']}",
                                 location="below")
        follow_layout(rbd)
        assert not self._overlap(two, three)
        # Placed from `two`'s FINAL spot (not where it first wanted to be),
        # so it stays beside it even after being nudged itself.
        assert abs(three["x"] - two["x"]) < 400 and abs(three["y"] - two["y"]) < 400


class TestDraggedOffset:
    def test_a_dragged_note_keeps_its_offset_through_a_layout_and_is_not_nudged(self):
        t = TestNudge()
        rbd = t._model()
        note = place_annotation(rbd, kind="text", text="n", adjacent_to="A")
        base = (note["x"], note["y"])
        note["anchor"]["dx"], note["anchor"]["dy"] = 25.0, 10.0
        follow_layout(rbd)
        assert (note["x"], note["y"]) == pytest.approx((base[0] + 25, base[1] + 10))
        assert follow_layout(rbd) == 0

    def test_the_offset_is_part_of_the_schema(self):
        from raptor.core.fileio.rbds_schema import build_schema
        props = build_schema()["properties"]["annotations"]["items"]["properties"]["anchor"]["properties"]
        assert props["dx"] == {"type": "number"} and props["dy"] == {"type": "number"}


class TestMakeRoom:
    """Auto Layout stretches the space between stacked elements so the
    notes anchored above/below them fit, instead of sitting on a
    neighbour."""

    @staticmethod
    def _tight_model():
        from raptor.api import build
        # A above B, only one grid step apart: far too close for a note.
        return build.build_from_spec(
            {"blocks": [{"name": "A", "x": 2.0, "y": 81.25},
                        {"name": "B", "x": 2.0, "y": 78.75}],
             "nodes": [{"name": "s", "type": 1, "x": 0.0, "y": 80.0},
                       {"name": "j", "type": 2, "n": 2, "k": 2, "x": 4.0, "y": 80.0},
                       {"name": "e", "type": 3, "x": 6.0, "y": 80.0}],
             "links": [{"from": "s", "to": "A"}, {"from": "s", "to": "B"},
                       {"from": "A", "to": "j"}, {"from": "B", "to": "j"},
                       {"from": "j", "to": "e"}]}, auto_layout=False)

    @staticmethod
    def _clear_of_elements(rbd, note):
        from raptor.api.annotations import _element_boxes
        for _p, ref, l, t, r, b in _element_boxes(rbd):
            if ref == note["anchor"]["ref"]:
                continue
            assert not (note["x"] < r and note["x"] + note["w"] > l
                        and note["y"] < b and note["y"] + note["h"] > t), ref

    def test_a_stack_of_notes_between_two_blocks_gets_room(self):
        from raptor.api.annotations import make_room_for_annotations
        rbd = self._tight_model()
        # B is the lower block (smaller model y): notes above it sit in the gap.
        one = place_annotation(rbd, kind="text", text="one", adjacent_to="B")
        two = place_annotation(rbd, kind="text", text="two",
                               adjacent_to=f"Annotation:{one['id']}")
        moved = make_room_for_annotations(rbd)
        assert moved
        follow_layout(rbd)
        self._clear_of_elements(rbd, one)
        self._clear_of_elements(rbd, two)

    def test_the_joining_nodes_stay_centred_and_nothing_moves_closer(self):
        from raptor.api.annotations import make_room_for_annotations
        rbd = self._tight_model()
        one = place_annotation(rbd, kind="text", text="one", adjacent_to="B")
        place_annotation(rbd, kind="text", text="two",
                         adjacent_to=f"Annotation:{one['id']}")
        rows = {r["blockName"]: r for r in rbd.section("BLOCK_DATA").rows if r["ID"] != "0"}
        a0, b0 = float(rows["A"]["yPos"]), float(rows["B"]["yPos"])
        node = next(r for r in rbd.section("NODE_DATA").rows if r["Name"] == "j")
        mid0 = float(node["yPos"])
        make_room_for_annotations(rbd)
        a1, b1 = float(rows["A"]["yPos"]), float(rows["B"]["yPos"])
        assert a1 >= a0 and b1 <= b0 and (a1 - b1) > (a0 - b0)
        assert float(node["yPos"]) == mid0
        assert (a1 - mid0) == pytest.approx(mid0 - b1)         # still symmetric
        assert (a1 - a0) % 1.25 == pytest.approx(0, abs=1e-6) or \
            (a1 - a0) % 1.25 == pytest.approx(1.25, abs=1e-6)  # on the grid

    def test_it_is_idempotent_and_leaves_a_roomy_model_alone(self):
        from raptor.api.annotations import make_room_for_annotations
        rbd = self._tight_model()
        place_annotation(rbd, kind="text", text="one", adjacent_to="B")
        make_room_for_annotations(rbd)
        assert make_room_for_annotations(rbd) == {}

    def test_notes_on_the_outer_side_of_the_column_need_no_room(self):
        from raptor.api.annotations import make_room_for_annotations
        rbd = self._tight_model()
        place_annotation(rbd, kind="text", text="n", adjacent_to="A",
                         location="above")                # nothing above A
        place_annotation(rbd, kind="text", text="m", adjacent_to="B",
                         location="below")                # nothing below B
        assert make_room_for_annotations(rbd) == {}

    def test_side_notes_and_dragged_notes_ask_for_nothing(self):
        from raptor.api.annotations import make_room_for_annotations
        rbd = self._tight_model()
        place_annotation(rbd, kind="text", text="n", adjacent_to="B",
                         location="right")
        dragged = place_annotation(rbd, kind="text", text="d", adjacent_to="B")
        dragged["anchor"]["dx"] = 30.0
        assert make_room_for_annotations(rbd) == {}

    def test_a_model_without_notes_is_untouched(self):
        from raptor.api.annotations import make_room_for_annotations
        assert make_room_for_annotations(self._tight_model()) == {}


class TestArrowEdges:
    """An attached arrow end stops at the target's outline, not its centre,
    and each end can be attached or free independently."""

    @staticmethod
    def _model():
        from raptor.api import build
        return build.build_from_spec(
            {"blocks": [{"name": "A", "x": 2.0, "y": 80.0},
                        {"name": "B", "x": 6.0, "y": 80.0}],
             "nodes": [{"name": "s", "type": 1, "x": 0.0, "y": 80.0},
                       {"name": "e", "type": 3, "x": 8.0, "y": 80.0}],
             "links": [{"from": "s", "to": "A"}, {"from": "A", "to": "B"},
                       {"from": "B", "to": "e"}]}, auto_layout=False)

    def test_edge_point_rect_and_ellipse(self):
        from raptor.core.annotations import edge_point
        rect = {"cx": 0.0, "cy": 0.0, "half_w": 50.0, "half_h": 20.0}
        assert edge_point(rect, 200.0, 0.0, pad=0) == pytest.approx((50.0, 0.0))
        assert edge_point(rect, 0.0, -100.0, pad=0) == pytest.approx((0.0, -20.0))
        ell = dict(rect, shape="ellipse")
        assert edge_point(ell, 200.0, 0.0, pad=0) == pytest.approx((50.0, 0.0))
        x, y = edge_point(ell, 100.0, 40.0, pad=0)
        assert (x / 50.0) ** 2 + (y / 20.0) ** 2 == pytest.approx(1.0)
        assert edge_point(rect, 10.0, 5.0) == (0.0, 0.0)       # inside: centre
        assert edge_point(rect, 0.0, 0.0) == (0.0, 0.0)

    def test_an_arrow_between_blocks_runs_edge_to_edge(self):
        from raptor.api.annotations import _anchor_for
        rbd = self._model()
        arrow = place_annotation(rbd, kind="arrow", from_="A", to="B")
        a, b = _anchor_for(rbd, "A"), _anchor_for(rbd, "B")
        assert arrow["x1"] == pytest.approx(a["cx"] + a["half_w"] + 2.0, abs=0.01)
        assert arrow["x2"] == pytest.approx(b["cx"] - b["half_w"] - 2.0, abs=0.01)
        assert arrow["y1"] == pytest.approx(a["cy"], abs=0.01)

    def test_one_end_can_be_free(self):
        from raptor.api.annotations import _anchor_for
        rbd = self._model()
        arrow = place_annotation(rbd, kind="arrow", from_="A",
                                 x2=900.0, y2=-300.0)
        assert set(arrow["anchor"]) == {"from", "from_ref"}
        assert (arrow["x2"], arrow["y2"]) == (900.0, -300.0)
        a = _anchor_for(rbd, "A")
        follow_layout(rbd)
        assert (arrow["x2"], arrow["y2"]) == (900.0, -300.0)
        assert arrow["x1"] > a["cx"]                      # left the centre

    def test_a_half_named_arrow_needs_the_other_end_as_coordinates(self):
        rbd = self._model()
        with pytest.raises(BuildError):
            place_annotation(rbd, kind="arrow", from_="A")

    def test_an_arrow_can_point_at_a_note_and_stops_at_its_edge(self):
        rbd = self._model()
        note = place_annotation(rbd, kind="rect", x=400.0, y=-400.0,
                                w=100.0, h=60.0)
        arrow = place_annotation(rbd, kind="arrow", from_="A",
                                 to=f"Annotation:{note['id']}")
        top = note["y"] - 2.0
        left = note["x"] - 2.0
        assert (arrow["x2"] == pytest.approx(left, abs=0.01)
                or arrow["y2"] == pytest.approx(top, abs=0.01)
                or arrow["y2"] == pytest.approx(note["y"] + note["h"] + 2.0, abs=0.01))

    def test_a_half_attached_arrow_is_not_a_validation_problem(self):
        from raptor.api.validate import issues
        rbd = self._model()
        place_annotation(rbd, kind="arrow", from_="A", x2=10.0, y2=10.0)
        assert [i for i in issues(rbd) if i.element.startswith("Annotation")] == []


def test_the_reported_two_pumps_file_gets_room_on_auto_layout():
    """~/Downloads/test1.rbds as reported: two pumps stacked one step apart,
    a note above the lower pump and another note above that note. Before
    the gap was stretched, the upper note sat on the pump above."""
    from pathlib import Path

    from raptor.api.annotations import _element_boxes, make_room_for_annotations
    from raptor.core.fileio.rbds import load_rbds
    rbd = load_rbds(str(Path(__file__).parent / "data" / "annotation_stack_two_pumps.rbds"))
    assert make_room_for_annotations(rbd)
    follow_layout(rbd)
    for note in rbd.annotations:
        for _p, ref, l, t, r, b in _element_boxes(rbd):
            if ref == note["anchor"]["ref"]:
                continue
            assert not (note["x"] < r and note["x"] + note["w"] > l
                        and note["y"] < b and note["y"] + note["h"] > t), (note["id"], ref)

"""Pure decisions behind Auto Layout."""
import math
from types import SimpleNamespace as NS

import pytest

from raptor.gui.layout_options import (
    element_extents, grid_gap, layout_seeds, normalise_layout_options)


def _get(values):
    return lambda key, default: values.get(key, default)


class TestNormaliseOptions:
    def test_unset_options_come_from_settings(self):
        got = normalise_layout_options(None, None, None, None, _get({
            "layout/direction": "TB", "layout/max_columns": "4",
            "layout/spacing": "1.5", "layout/label_side": "left"}))
        assert got == ("TB", 4, 1.5, "left")

    def test_defaults_when_nothing_is_saved(self):
        assert normalise_layout_options(None, None, None, None, _get({})) \
            == ("LR", None, 1.0, None)

    @pytest.mark.parametrize("bad", ["sideways", "", "lr"])
    def test_an_unknown_direction_becomes_lr(self, bad):
        assert normalise_layout_options(bad, 0, 1.0, "left", _get({}))[0] == "LR"

    @pytest.mark.parametrize("saved", ["abc", None, -3, 0])
    def test_a_bad_or_non_positive_column_cap_means_no_cap(self, saved):
        got = normalise_layout_options(None, None, 1.0, "left",
                                       _get({"layout/max_columns": saved}))
        assert got[1] is None

    def test_an_explicit_column_cap_is_kept(self):
        assert normalise_layout_options("LR", 7, 1.0, None, _get({}))[1] == 7

    @pytest.mark.parametrize("spacing", [0, -1.0, math.inf, math.nan])
    def test_an_unusable_spacing_becomes_one(self, spacing):
        assert normalise_layout_options("LR", None, spacing, None, _get({}))[2] == 1.0

    def test_an_unreadable_saved_spacing_becomes_one(self):
        got = normalise_layout_options("LR", None, None, None,
                                       _get({"layout/spacing": "wide"}))
        assert got[2] == 1.0

    @pytest.mark.parametrize("side,expected", [
        ("left", "left"), ("right", "right"), ("auto", None), ("middle", None)])
    def test_label_side_is_left_right_or_automatic(self, side, expected):
        assert normalise_layout_options("LR", None, 1.0, side, _get({}))[3] == expected


class TestSeeds:
    NODES = {1: NS(type_node=1), 2: NS(type_node=2), 3: NS(type_node=3),
             4: NS(type_node=5), 5: NS(type_node=1)}

    def test_top_level_starts_at_start_markers_and_pins_terminals(self):
        starts, pins = layout_seeds(0, {}, self.NODES)
        assert starts == [("node", 1), ("node", 5)]
        assert pins == {("node", 3), ("node", 4)}        # regular node 2 is not pinned

    def test_inside_a_hierarchy_the_flow_enters_at_its_in_node(self):
        hiers = {9: NS(in_id=20, out_id=21)}
        assert layout_seeds(9, hiers, self.NODES) == ([("node", 20)], {("node", 21)})

    def test_an_unknown_parent_falls_back_to_the_top_level_rule(self):
        assert layout_seeds(99, {}, self.NODES)[0] == [("node", 1), ("node", 5)]


class TestGridGap:
    def test_smallest_positive_gap(self):
        assert grid_gap([0.0, 2.5, 3.0, 8.0], 9.0) == 0.5

    def test_unsorted_input_is_fine(self):
        assert grid_gap([8.0, 0.0, 3.0], 9.0) == 3.0

    @pytest.mark.parametrize("values", [[], [4.0], [4.0, 4.0000001]])
    def test_fallback_when_there_is_no_gap(self, values):
        assert grid_gap(values, 9.0) == 9.0


class TestExtents:
    def test_half_sizes_in_model_units_and_drawn_half_extents(self):
        sizes, render = element_extents(
            {("block", 3): (216.0, 108.0), ("node", 1): (20.0, 20.0)}, 54.0)
        assert sizes[("block", 3)] == (2.0, 1.0)
        assert render[("BLOCK_DATA", "3")] == (108.0, 54.0)
        assert render[("NODE_DATA", "1")] == (10.0, 10.0)

    def test_a_degenerate_item_still_occupies_space(self):
        sizes, _ = element_extents({("block", 1): (0.0, 0.0)}, 54.0)
        assert sizes[("block", 1)] == (0.1, 0.1)

    def test_an_unknown_kind_has_a_size_but_no_render_extent(self):
        sizes, render = element_extents({("annotation", 1): (10.0, 10.0)}, 5.0)
        assert ("annotation", 1) in sizes and render == {}

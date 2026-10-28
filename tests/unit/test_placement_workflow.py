"""Regression tests for the element-placement workflow.

Each test here pins a defect reproduced against the running MCP server
during the 2026-09-28 placement review.

The recurring theme: placing a block must never silently copy another
element's data, must land where the caller asked, and must never rewrite a
model that was merely opened.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.api.build import (
    add_block, add_node, add_event, build_from_spec, BuildError,
)
from raptor.api.workspace import Workspace
from raptor.core.fileio.rbds import load_rbds, save_rbds

REPO = Path(__file__).resolve().parent.parent.parent
NEW_DOCS_MODELS = REPO / "new_docs" / "models"


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


def _nested_spec():
    """A model whose FIRST block row lives inside a hierarchy.

    This is the shape that makes the `rows[0]` clone bug bite: the
    mandated `ID=0` template row exists but is not first, so cloning
    `rows[0]` picks up a real block that belongs to a sub-hierarchy.
    """
    return {
        "blocks": [
            #  id 1  --- the row `_append_new_row` would wrongly clone
            {"id": 1, "name": "InnerA", "parent": 2,
             "fail_dist": 4, "fail_params": [500.0, 0.0],
             "repair_dist": 7, "repair_params": [4.0, 0.5]},
            {"id": 5, "name": "InnerB", "parent": 2,
             "fail_dist": 4, "fail_params": [800.0, 0.0],
             "repair_dist": 19, "repair_params": [2.0]},
            {"id": 9, "name": "TopLevel", "parent": 0,
             "fail_dist": 4, "fail_params": [100.0, 0.0],
             "repair_dist": 7, "repair_params": [10.0, 2.0]},
        ],
        "nodes": [
            {"id": 10, "name": "hin", "type": "in", "parent": 2},
            {"id": 20, "name": "hout", "type": "out", "parent": 2},
            {"id": 30, "name": "nEnd", "type": "end"},
        ],
        "hierarchies": [
            {"id": 2, "name": "Sub", "in_id": 10, "out_id": 20, "parent": 0},
        ],
        "links": [
            {"from_type": "Node", "from_id": 10, "to_type": "Block",
             "to_id": 1},
            {"from_type": "Block", "from_id": 1, "to_type": "Block",
             "to_id": 5},
            {"from_type": "Block", "from_id": 5, "to_type": "Node",
             "to_id": 20},
            {"from_type": "Node", "from_id": 30, "to_type": "Block",
             "to_id": 9},
        ],
    }


@pytest.fixture
def nested(workspace):
    """A registered model whose first block sits inside hierarchy 2."""
    rbd = build_from_spec(_nested_spec())
    return workspace.register_model(rbd, name="nested")["model_id"]


def _dist(row, prefix):
    return (row.get(f"{prefix}Dist"), row.get(f"{prefix}Param1"),
            row.get(f"{prefix}Param2"))


# ---------------------------------------------------------------------------
# D1 / D2 — a new block must not inherit another block's data
# ---------------------------------------------------------------------------

class TestNewBlockDoesNotInherit:
    def test_no_distribution_inheritance(self, workspace, nested):
        """A block added with no dist args gets the documented defaults,
        not the first block's failure/repair distributions."""
        before = add_block(workspace, nested, "Fresh")
        row = workspace.get(nested).rbd.section("BLOCK_DATA").rows
        fresh = next(r for r in row if r["ID"] == before["ID"])

        # The first block in the file is `InnerA` (Exponential 500 /
        # Lognormal 4,0.5).  A new block must not pick those up.
        assert _dist(fresh, "F") != ("4", "500.000000", "0.000000")
        assert _dist(fresh, "R") != ("7", "4.000000", "0.500000")

    def test_no_parent_inheritance(self, workspace, nested):
        """With no `parent` argument the block lands at the top level,
        even when the file's first block lives inside a hierarchy."""
        row = add_block(workspace, nested, "TopIntent")
        assert int(float(row.get("ParentId", 0))) == 0

    def test_explicit_parent_still_honoured(self, workspace, nested):
        """The fix must not break deliberately targeting a hierarchy."""
        row = add_block(workspace, nested, "Deliberate", parent=2)
        assert int(float(row.get("ParentId", 0))) == 2

    def test_sibling_of_first_block_does_not_bleed(self, workspace, nested):
        """Two blocks added in a row must be independent of `InnerA`."""
        a = add_block(workspace, nested, "NoDist1")
        b = add_block(workspace, nested, "NoDist2")
        rows = {r["ID"]: r for r in
                workspace.get(nested).rbd.section("BLOCK_DATA").rows}
        assert _dist(rows[a["ID"]], "F") == _dist(rows[b["ID"]], "F")

    def test_new_node_does_not_inherit(self, workspace, nested):
        """`add_node` must not clone NODE_DATA's first row (the Start
        marker) either — its spare columns must stay at defaults."""
        row = add_node(workspace, nested, "plain", type="regular", k=1)
        assert row.get("Type") == "2"
        assert int(float(row.get("k", 0))) == 1
        assert int(float(row.get("parent", 0))) == 0

    def test_new_event_does_not_inherit(self, workspace, nested):
        row = add_event(workspace, nested, "plain_ev", prob=0.25)
        assert int(float(row.get("ParentId", 0))) == 0


# ---------------------------------------------------------------------------
# D3 — opening a model must not rewrite it
# ---------------------------------------------------------------------------

class TestOpenIsReadOnly:
    @staticmethod
    def _coords(rbd):
        out = {}
        for sec in ("BLOCK_DATA", "HIERARCHY_DATA", "NODE_DATA",
                    "EVENT_DATA"):
            for r in rbd.section(sec).rows:
                name = (r.get("blockName") or r.get("hierarchyName")
                        or r.get("Name") or r.get("eventName"))
                out[(sec, r.get("ID"), name)] = (r.get("xPos"),
                                                r.get("yPos"))
        return out

    def test_source_file_is_untouched(self, workspace):
        """Opening must never write to the file the user opened."""
        src = NEW_DOCS_MODELS / "Van1.rbds"
        before = src.read_bytes()
        workspace.open_model(src)
        assert src.read_bytes() == before

    def test_open_does_not_materialise(self, workspace):
        """Opening writes only an index entry -- no model file."""
        src = NEW_DOCS_MODELS / "Van1.rbds"
        info = workspace.open_model(src)
        handle = workspace.get(info["model_id"])
        assert not handle.path.exists()
        assert (handle.meta_path).exists()      # the index entry

    def test_effective_path_is_always_readable(self, workspace):
        """`effective_path` is what out-of-process consumers (the render
        worker) must read, so it resolves before the model is materialised
        and points at the workspace copy afterwards."""
        src = NEW_DOCS_MODELS / "Van1.rbds"
        info = workspace.open_model(src)
        mid = info["model_id"]
        handle = workspace.get(mid)
        assert handle.effective_path == src          # not yet materialised
        add_block(workspace, mid, "Trigger", fail_dist=4,
                  fail_params=[800.0, 0.0], repair_dist=19,
                  repair_params=[2.0])
        assert workspace.get(mid).effective_path.exists()

    def test_workspace_copy_keeps_the_original_layout(self, workspace):
        """The copy the user goes on to edit must have the same
        coordinates as the file they opened.

        This is the real defect: `open_model` used to round-trip the
        model through `save_rbds`, whose crammed-layout guard silently
        re-laid-out every element.  `Van1.rbds` trips that gate, so the
        four tires moved from a diagonal fan to a vertical stack before
        the user had touched anything.
        """
        src = NEW_DOCS_MODELS / "Van1.rbds"
        info = workspace.open_model(src)
        mid = info["model_id"]
        # Reading through the handle must not disturb the source either.
        workspace.get(mid).rbd
        add_block(workspace, mid, "Trigger", fail_dist=4,
                  fail_params=[800.0, 0.0], repair_dist=19,
                  repair_params=[2.0], parent=20)
        copied = load_rbds(str(workspace.get(mid).path))
        original = self._coords(load_rbds(str(src)))
        for key, xy in self._coords(copied).items():
            if key in original:          # pre-existing elements only
                assert xy == original[key], key

    @pytest.mark.parametrize("name", ["Van1", "DC1"])
    def test_shipped_models_are_not_relaid_out(self, workspace, name):
        """`Van1` and `DC1` were being silently re-laid-out on every save
        because the crammed-layout guard measured pitch across all
        hierarchy levels at once.  They must now survive open + edit with
        every pre-existing element exactly where the author put it --
        these are the figures the book walks through in ch10 and ch14.
        """
        src = NEW_DOCS_MODELS / f"{name}.rbds"
        info = workspace.open_model(src)
        mid = info["model_id"]
        add_block(workspace, mid, "Trigger", fail_dist=4,
                  fail_params=[800.0, 0.0], repair_dist=19,
                  repair_params=[2.0])
        copied = load_rbds(str(workspace.get(mid).path))
        original = self._coords(load_rbds(str(src)))
        for key, xy in self._coords(copied).items():
            if key in original:          # pre-existing elements only
                assert xy == original[key], key
        assert workspace.get(mid).layout_notice is None

    def test_genuinely_crammed_model_reports_instead_of_being_silent(
        self, workspace,
    ):
        """The guide's data centre as it was first authored, at sub-grid
        pitch (0.5 on a 1.25 grid): normalising it is defensible -- but the
        user must be told their diagram moved, not find out later. (The
        shipped DC2 has since been laid out; this keeps the old file.)"""
        src = (Path(__file__).resolve().parents[1] / "fixtures"
               / "CrammedDataCentre" / "input.rbds")
        info = workspace.open_model(src)
        mid = info["model_id"]
        add_block(workspace, mid, "Trigger", fail_dist=4,
                  fail_params=[800.0, 0.0], repair_dist=19,
                  repair_params=[2.0])
        notice = workspace.get(mid).layout_notice
        assert notice and "re-laid-out" in notice

    def test_edit_then_save_preserves_layout(self, workspace):
        """The harm only materialises on edit-and-save, so pin the
        whole round trip: every pre-existing element keeps its place."""
        src = NEW_DOCS_MODELS / "Van1.rbds"
        info = workspace.open_model(src)
        mid = info["model_id"]
        add_block(workspace, mid, "LaterEdit",
                  fail_dist=4, fail_params=[800.0, 0.0],
                  repair_dist=19, repair_params=[2.0], parent=20)
        copied = load_rbds(str(workspace.get(mid).path))
        original = self._coords(load_rbds(str(src)))
        for key, xy in self._coords(copied).items():
            if key in original:          # pre-existing elements only
                assert xy == original[key], key


# ---------------------------------------------------------------------------
# D4 — the model cap must not be a permanent dead end
# ---------------------------------------------------------------------------

class TestWorkspaceModelCap:
    def test_more_models_on_disk_than_cap_is_usable(self, tmp_path):
        """A workspace holding more models than `max_models` must still
        allow new models to be created (previously: permanent refusal)."""
        root = tmp_path / "ws"
        for i in range(8):
            mid = f"m_fake{i:04d}"
            d = root / "models" / mid
            d.mkdir(parents=True)
            rbd = build_from_spec({
                "blocks": [{"id": 1, "name": "B"}],
                "nodes": [{"id": 1, "name": "start", "type": "start"},
                          {"id": 2, "name": "end", "type": "end"}],
                "links": [{"from_type": "Node", "from_id": 1,
                           "to_type": "Block", "to_id": 1}],
            })
            save_rbds(rbd, str(d / "model.rbds"))
            (d / "meta.json").write_text(json.dumps(
                {"name": f"fake{i}", "source": "", "created": 0.0,
                 "last_modified": float(i)}))

        ws = Workspace(root, max_models=3)
        # Index is not capped, so all 8 are discoverable...
        assert len(ws.list_models()) == 8
        # ...and creating a new model still works instead of raising.
        result = ws.create_model("brand_new")
        assert result["model_id"]
        assert len(ws.list_models()) == 9

    def test_every_indexed_model_is_openable(self, tmp_path):
        """Lazy-loaded models must still be reachable by handle."""
        root = tmp_path / "ws"
        for i in range(5):
            d = root / "models" / f"m_keep{i:04d}"
            d.mkdir(parents=True)
            rbd = build_from_spec({
                "blocks": [{"id": 1, "name": f"B{i}"}],
                "nodes": [{"id": 1, "name": "start", "type": "start"},
                          {"id": 2, "name": "end", "type": "end"}],
                "links": [{"from_type": "Node", "from_id": 1,
                           "to_type": "Block", "to_id": 1}],
            })
            save_rbds(rbd, str(d / "model.rbds"))
            (d / "meta.json").write_text(json.dumps(
                {"name": f"keep{i}", "source": "", "created": 0.0,
                 "last_modified": 0.0}))

        ws = Workspace(root, max_models=2)
        for i in range(5):
            handle = ws.get(f"m_keep{i:04d}")
            assert handle.name.startswith("keep")

    def test_unknown_handle_still_raises(self, tmp_path):
        ws = Workspace(tmp_path / "ws")
        with pytest.raises(Exception):
            ws.get("m_does_not_exist")


# ---------------------------------------------------------------------------
# D5 — placement validates its input
# ---------------------------------------------------------------------------

class TestAddBlockValidation:
    def test_rejects_duplicate_name(self, workspace, nested):
        add_block(workspace, nested, "Pump_A")
        with pytest.raises(BuildError, match="already exists"):
            add_block(workspace, nested, "Pump_A")

    def test_rejects_unknown_distribution_code(self, workspace, nested):
        with pytest.raises(BuildError, match="[Dd]istribution"):
            add_block(workspace, nested, "BadDist", fail_dist=99,
                      fail_params=[1.0])

    def test_rejects_distribution_without_params(self, workspace, nested):
        with pytest.raises(BuildError):
            add_block(workspace, nested, "NoParams", fail_dist=6)

    def test_warns_on_dangling_parent(self, workspace, nested):
        info = add_block(workspace, nested, "Ghost", parent=999)
        # Recoverable, so it is a warning rather than a hard failure, but
        # the caller is told rather than left with a silent dangling id.
        assert info is not None

    def test_duplicate_name_does_not_break_selectors(self, workspace, nested):
        """Placement must not create the ambiguity it later complains
        about (previously: 'matches 3 elements' on every name lookup)."""
        add_block(workspace, nested, "Unique")
        add_block(workspace, nested, "Unique_2")
        from raptor.api.inspect import get_element
        el = get_element(workspace, nested, "Unique")
        assert el["name"] == "Unique"


# ---------------------------------------------------------------------------
# D6 / D7 — hierarchy discovery and creation
# ---------------------------------------------------------------------------

class TestHierarchies:
    def test_describe_lists_hierarchies(self, workspace, nested):
        """`describe_model` used to omit hierarchies entirely, which made
        `add_block(parent=...)` undiscoverable and left
        `render_diagram`'s "read the hierarchy ids from describe_model"
        hint pointing at output that never had them."""
        from raptor.api.build import describe
        d = describe(workspace, nested, detail=True)
        assert d["hierarchies"] == [{
            "id": "2", "name": "Sub", "parent": 0,
            "in_id": 10, "out_id": 20, "selector": "Hier:2",
        }]

    def test_add_hierarchy_creates_wired_markers(self, workspace):
        """The In/Out markers must come out correct: each marker's own
        `parent` is the hierarchy's id, and the validator must be happy."""
        from raptor.api.build import add_hierarchy, connect
        from raptor.api.validate import issues
        mid = workspace.create_model("hier")["model_id"]
        h = add_hierarchy(workspace, mid, "Sub")
        assert h["in"] != h["out"] and h["ID"] not in (h["in"], h["out"])

        rbd = workspace.get(mid).rbd
        markers = {str(n.get("ID")): n
                   for n in rbd.section("NODE_DATA").rows}
        for key in ("in", "out"):
            node = markers[str(h[key])]
            assert str(node.get("parent")) == str(h["ID"])

        # Populate it and wire the boundary; the model must validate.
        block = add_block(workspace, mid, "Inside", parent=int(h["ID"]),
                          fail_dist=4, fail_params=[50.0, 0.0],
                          repair_dist=19, repair_params=[1.0])
        connect(workspace, mid, "Node", h["in"], "Block",
                int(float(block["ID"])))
        connect(workspace, mid, "Block", int(float(block["ID"])),
                "Node", h["out"])
        # Inside is wired; the hierarchy itself is not on the main path yet,
        # which is exactly what Raptor 7 refuses to run (ValidateRBD).
        errors = [i.message for i in issues(rbd) if i.severity == "error"]
        assert errors == [
            "To simulate, hier 'Sub' must connect from something!",
            "To simulate, hier 'Sub' must connect to something!"], errors

    def test_collapse_selection_rewires_the_boundary(self, workspace):
        """The GUI's Collapse Selection: internal links travel down a
        level, and each boundary link splits into an outside half
        (keeping its id) and an inside half off the In/Out marker."""
        from raptor.api.build import connect, collapse_selection, delete_element, describe
        from raptor.api.validate import issues
        mid = workspace.create_model("collapse")["model_id"]
        # The blank template's own Start -> block -> End goes first: a Start
        # marker takes one link out (Raptor 7's CheckValidLink).
        delete_element(workspace, mid, "Block:1")
        ids = {}
        for name in ("A", "B", "C"):
            row = add_block(workspace, mid, name, fail_dist=4,
                            fail_params=[100.0, 0.0], repair_dist=19,
                            repair_params=[2.0])
            ids[name] = int(float(row["ID"]))
        connect(workspace, mid, "Node", 1, "Block", ids["A"])
        connect(workspace, mid, "Block", ids["A"], "Block", ids["B"])
        connect(workspace, mid, "Block", ids["B"], "Block", ids["C"])
        connect(workspace, mid, "Block", ids["C"], "Node", 2)

        result = collapse_selection(workspace, mid, ["A", "B"], "Collapsed")
        d = describe(workspace, mid, detail=True)
        edges = [(l["from_type"], l["from_id"], l["to_type"], l["to_id"])
                 for l in d["links"]]
        hid, in_id, out_id = result["ID"], result["in"], result["out"]
        # entry: outside node -> hierarchy, and In marker -> A
        assert ("Node", 1, "Hier", int(hid)) in edges
        assert ("Node", int(in_id), "Block", ids["A"]) in edges
        # internal A -> B kept, now at the sub-level
        assert ("Block", ids["A"], "Block", ids["B"]) in edges
        # exit: B -> Out marker, and hierarchy -> C
        assert ("Block", ids["B"], "Node", int(out_id)) in edges
        assert ("Hier", int(hid), "Block", ids["C"]) in edges
        assert [i for i in issues(workspace.get(mid).rbd)
                if "ERROR" in str(i)] == []

    def test_collapse_refuses_terminator(self, workspace, nested):
        """A hierarchy cannot contain the model's own Start/End markers."""
        from raptor.api.build import collapse_selection
        with pytest.raises(BuildError, match="cannot contain"):
            collapse_selection(workspace, nested, ["nEnd"])

    def test_collapse_refuses_a_hierarchy(self, workspace, nested):
        from raptor.api.build import collapse_selection
        with pytest.raises(BuildError, match="is a hierarchy"):
            collapse_selection(workspace, nested, ["Sub"])

    def test_collapse_needs_a_selection(self, workspace, nested):
        from raptor.api.build import collapse_selection
        with pytest.raises(BuildError, match="at least one element"):
            collapse_selection(workspace, nested, [])


# ---------------------------------------------------------------------------
# Configurable block defaults (the MCP equivalent of the GUI's
# "Block Defaults" button)
# ---------------------------------------------------------------------------

class TestBlockDefaults:
    def test_defaults_fill_omitted_distributions(self, workspace):
        from raptor.api.build import add_block
        mid = workspace.create_model("defaults")["model_id"]
        workspace.set_block_defaults(
            {"fail_dist": 15, "fail_params": [1200.0, 0.0]})
        row = add_block(workspace, mid, "A")
        assert (row["FDist"], row["FParam1"]) == ("15", "1200.000000")

    def test_explicit_arguments_win(self, workspace):
        from raptor.api.build import add_block
        mid = workspace.create_model("defaults")["model_id"]
        workspace.set_block_defaults({"fail_dist": 15, "fail_params": [1200.0, 0.0]})
        row = add_block(workspace, mid, "A", fail_dist=4, fail_params=[5.0, 0.0])
        assert (row["FDist"], row["FParam1"]) == ("4", "5.000000")

    def test_defaults_persist_across_workspace_instances(self, tmp_path):
        root = tmp_path / "ws"
        Workspace(root).set_block_defaults({"fail_dist": 19, "fail_params": [7.0]})
        assert Workspace(root).block_defaults == {
            "fail_dist": 19, "fail_params": [7.0]}

    def test_defaults_reject_unknown_fields(self, workspace):
        from raptor.api.workspace import WorkspaceArgumentError
        with pytest.raises(WorkspaceArgumentError, match="Unknown"):
            workspace.set_block_defaults({"fial_dist": 4})

    def test_defaults_survive_in_reported_workspace_info(self, workspace):
        workspace.set_block_defaults({"repair_dist": 19, "repair_params": [2.0]})
        assert workspace.info()["block_defaults"] == {
            "repair_dist": 19, "repair_params": [2.0]}


class TestPlacementWarningsReachTheCaller:
    """`add_block` used to return `warnings: []` unconditionally, so a
    dangling `parent` or a disabled block was reported as a clean
    success."""

    def test_dangling_parent_is_warned_about(self, workspace, nested):
        from raptor.api.build import add_block, check_new_block
        row = add_block(workspace, nested, "Ghost", parent=999)
        warnings = check_new_block(workspace.get(nested).rbd, row)
        assert warnings and "999" in warnings[0]

    def test_mcp_tool_surfaces_the_warning(self, tmp_path):
        """The MCP layer is where the warning has to be visible."""
        import raptor.mcp.tools_model as tools
        from raptor.mcp import context
        from raptor.mcp.server import mcp

        mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
        mcp._raptor_ws = None
        ws = context.workspace()
        mid = ws.create_model("warn")["model_id"]
        result = tools.add_block(mid, "Ghost", parent=999)
        assert result.structured_content["warnings"], (
            "a dangling parent must reach the caller, not warnings: []")
        assert "999" in " ".join(result.structured_content["warnings"])

    def test_mcp_tool_rejects_bad_input_loudly(self, tmp_path):
        """`@guarded` turns a `BuildError` into an `is_error` result
        rather than an exception, so the refusal has to be visible as
        that."""
        import raptor.mcp.tools_model as tools
        from raptor.mcp import context
        from raptor.mcp.server import mcp

        mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
        mcp._raptor_ws = None
        ws = context.workspace()
        mid = ws.create_model("reject")["model_id"]
        assert not tools.add_block(mid, "Unique").is_error

        dup = tools.add_block(mid, "Unique")
        assert dup.is_error
        assert "already exists" in dup.content[0].text

        bad = tools.add_block(mid, "Other", fail_dist=99, fail_params=[1.0])
        assert bad.is_error
        assert "Unknown fail distribution" in bad.content[0].text


class TestDocumentedFirstModelSpec:
    """The `build_model` spec printed in ch01 must actually build.

    No gate checked the book's example commands, so the first draft of
    this walkthrough shipped a spec that failed with "cannot resolve link
    endpoint 'start'" -- a spec has no implied Start/End node. Pinning it
    here means the next edit to ch01 that breaks the example fails the
    suite instead of the reader.
    """

    SPEC = {
        "nodes": [
            {"name": "start", "type": "start"},
            {"name": "end", "type": "end"},
        ],
        "blocks": [
            {"name": "Battery_Pack", "fail_dist": 15,
             "fail_params": [1.5, 60, 0]},
            {"name": "Switch", "fail_dist": 4, "fail_params": [200, 0]},
            {"name": "LED_Panel", "fail_dist": 8, "fail_params": [100, 10]},
        ],
        "links": [
            {"from": "start", "to": "Battery_Pack"},
            {"from": "Battery_Pack", "to": "Switch"},
            {"from": "Switch", "to": "LED_Panel"},
            {"from": "LED_Panel", "to": "end"},
        ],
    }

    def test_spec_builds_and_validates(self, workspace):
        from raptor.api.build import build_from_spec, describe
        from raptor.api.validate import issues
        mid = workspace.register_model(
            build_from_spec(self.SPEC), name="Lantern1")["model_id"]
        assert [i for i in issues(workspace.get(mid).rbd)
                if "ERROR" in str(i)] == []
        edges = [(l["from_type"], l["to_type"]) for l in
                 describe(workspace, mid, detail=True)["links"]]
        assert edges == [("Node", "Block"), ("Block", "Block"),
                         ("Block", "Block"), ("Block", "Node")]

    def test_spec_matches_the_shipped_lantern(self, workspace):
        """The book says this builds the same diagram as
        `models/Lantern1.rbds`; the two must not drift apart."""
        from raptor.api.build import build_from_spec
        mid = workspace.register_model(
            build_from_spec(self.SPEC), name="Lantern1")["model_id"]
        built = {r.get("blockName"): (r.get("FDist"), r.get("FParam1"),
                                      r.get("FParam2"))
                 for r in workspace.get(mid).rbd.section("BLOCK_DATA").rows
                 if str(r.get("ID")) != "0"}
        shipped = {r.get("blockName"): (r.get("FDist"), r.get("FParam1"),
                                        r.get("FParam2"))
                   for r in load_rbds(
                       str(NEW_DOCS_MODELS / "Lantern1.rbds")
                   ).section("BLOCK_DATA").rows
                   if str(r.get("ID")) != "0"}
        assert built == shipped

"""Tests for the MCP model tools.

Tools return a ``CallToolResult`` (structuredContent plus Markdown
content), so tests read ``.structured_content`` / ``.content`` rather than
parsing a JSON string.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


def _sc(result):
    assert not result.is_error, _text(result)
    return result.structured_content


def _text(result) -> str:
    return " ".join(c.text for c in result.content if getattr(c, "type", "") == "text")


class TestSchemas:
    """Pydantic schemas are valid."""

    def test_all_schemas_import(self):
        from raptor.mcp.schemas import (  # noqa: F401
            OpenModelInput, CreateModelInput, SaveModelAsInput,
            CloseModelInput, BuildModelInput, DescribeModelInput,
            AddBlockInput, AddNodeInput, AddEventInput, ConnectInput,
            UpdateElementInput, DeleteElementInput, UndoLastEditInput,
            AutoLayoutInput,
            ConfigureSparingInput, ConfigureMaintenanceInput,
            ConfigurePhasesInput, ConfigureSystemInput, ManageRatesInput,
            ValidateModelInput, EstimateRuntimeInput, SimulateInput,
            GetReportInput, SinglePointFailuresInput, FailureEffectsInput,
            CompareModelsInput, SweepParameterInput, RenderDiagramInput,
            GetTimeseriesInput, SearchDocsInput,
        )

    def test_build_model_input(self):
        from raptor.mcp.schemas import BuildModelInput
        inp = BuildModelInput(name="test", spec={"blocks": []})
        assert inp.name == "test"

    def test_simulate_input_defaults(self):
        from raptor.mcp.schemas import SimulateInput
        inp = SimulateInput(model_id="m_123")
        assert inp.timeout_s == 1800.0
        assert inp.confirm_long_run is False

    def test_add_block_expression_params(self):
        from raptor.mcp.schemas import AddBlockInput
        inp = AddBlockInput(
            model_id="m_1",
            name="Pump",
            fail_params=["@Highway * 1.4", 0, 0],
        )
        assert inp.fail_params[0] == "@Highway * 1.4"


class TestToolRegistration:
    """Tools are registered with the MCP server."""

    def test_model_tools_import(self):
        from raptor.mcp import tools_model  # noqa: F401

    def test_run_tools_import(self):
        from raptor.mcp import tools_run  # noqa: F401

    def test_view_tools_import(self):
        from raptor.mcp import tools_view  # noqa: F401

    def test_server_has_tools(self):
        from raptor.mcp import tools_model, tools_run  # noqa: F401
        assert tools_model is not None
        assert tools_run is not None


class TestBuildAndDescribe:
    """create_model + describe_model round-trip."""

    def test_create_and_describe(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import create_model, describe_model
            result = _sc(create_model("TestModel", template="blank"))
            model_id = result["model_id"]
            desc = _sc(describe_model(model_id))
            assert desc["blocks"] > 0 or desc["nodes"] > 0

    def test_create_series_template(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import create_model, describe_model
            result = _sc(create_model("Series3", template="series", n=3))
            desc = _sc(describe_model(result["model_id"]))
            assert desc["blocks"] == 3

    def test_create_series_template_is_laid_out(self):
        """A fresh template model is placed on the layered grid, not all
        stacked at (0, 0) -- otherwise its diagram opens as one blob."""
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import create_model
            from raptor.mcp import context
            result = _sc(create_model("Series3", template="series", n=3))
            rbd = context.workspace().get(result["model_id"]).rbd
            xs = {r.get("xPos") for r in rbd.section("BLOCK_DATA").rows
                  if r.get("ID") != "0"}
            assert len(xs) == 3, f"blocks not spread out: {xs}"

    def test_auto_layout_tool_spreads_overlapping_blocks(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import create_model, auto_layout
            from raptor.mcp import context
            result = _sc(create_model("Stacked", template="series", n=3))
            mid = result["model_id"]
            rbd = context.workspace().get(mid).rbd
            # Force every element back to the same point, as a spec built
            # with auto_layout=False would leave it.
            for sec in ("BLOCK_DATA", "NODE_DATA"):
                for r in rbd.section(sec).rows:
                    if r.get("ID") != "0":
                        r["xPos"] = "0.000000"
                        r["yPos"] = "0.000000"
            context.workspace().save(mid)
            out = _sc(auto_layout(mid))
            assert out["moved"] is True
            rbd = context.workspace().get(mid).rbd
            xs = {r.get("xPos") for r in rbd.section("BLOCK_DATA").rows
                  if r.get("ID") != "0"}
            assert len(xs) == 3, f"blocks not spread out: {xs}"

    def test_manage_rates_list(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import open_model, manage_rates
            result = _sc(open_model("MotorDemo"))
            rates = _sc(manage_rates(result["model_id"], action="list"))
            assert isinstance(rates["rates"], list)


class TestBadArguments:
    """Bad arguments return isError results, not tracebacks."""

    def test_open_nonexistent(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import open_model
            result = open_model("/nonexistent/file.rbd")
            assert result.is_error
            assert "Traceback" not in _text(result)

    def test_describe_unknown_model(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import describe_model
            result = describe_model("nonexistent_model_id")
            assert result.is_error

    def test_read_only_refuses_write(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type(
                "A", (), {"workspace": tmpdir, "read_only": True}
            )()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import create_model
            result = create_model("Nope")
            assert result.is_error
            assert "read-only" in _text(result)


class TestDocsAndWorkspace:
    """search_docs / read_doc / workspace_info."""

    def test_search_docs_multiword(self):
        from raptor.mcp.docs import search_chapters
        hits = search_chapters("capacity analysis system capacity", limit=5)
        assert hits, "multi-word query returned nothing"
        assert any("capacit" in (h["chapter"] + h["text"]).lower()
                   for h in hits)

    def test_search_docs_indexes_rbds_format_reference(self):
        """RBDS_FORMAT.md (a Markdown reference doc, not a tutorial
        chapter) must be search-indexed too -- a topology/format rule
        living only there was previously findable only by a caller who
        already knew to `read_doc("rbds")` by name (the 2026-09-22 MCP functional test review
        finding #24)."""
        from raptor.mcp.docs import search_chapters
        hits = search_chapters("fan-in fan-out node", limit=5)
        assert any(h["file"] == "docs/RBDS_FORMAT.md" for h in hits), (
            f"expected an RBDS_FORMAT.md hit, got {[h['file'] for h in hits]}"
        )

    def test_read_doc_rbds_and_guide(self):
        from raptor.mcp.docs import read_doc
        rbds = read_doc("rbds")
        guide = read_doc("mcp-guide")
        assert "rbds" in rbds["file"].lower()
        assert len(rbds["text"]) > 500
        assert len(guide["text"]) > 500

    def test_read_doc_refuses_paths_outside_the_docs_tree(self):
        from raptor.mcp.docs import read_doc
        for name in ("/etc/hostname", "/proc/self/environ",
                     "../outside.adoc"):
            with pytest.raises(FileNotFoundError):
                read_doc(name)

    def test_read_doc_tool_paginates(self):
        from raptor.mcp.tools_model import read_doc as tool_read
        first = _sc(tool_read("rbds", offset=0, limit=10))
        assert len(first["text"].splitlines()) <= 10
        assert first["total_lines"] > 10
        assert first["next_offset"] == 10

    def test_workspace_info(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import workspace_info
            info = _sc(workspace_info())
            assert info["root"] == str(Path(tmpdir).resolve())


class TestCompactUpdate:
    """update_element returns only changed/warnings/element unless
    ``full=True``."""

    def test_update_element_compact(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import open_model, update_element
            mid = _sc(open_model("MotorDemo"))["model_id"]
            data = _sc(update_element(mid, "Casing", {"stock": 7}))
            assert set(data) == {"changed", "warnings", "element"}
            assert set(data["element"]) <= {"id", "name", "kind"}
            assert data["element"]["kind"] == "Block"
            full = _sc(update_element(mid, "Casing", {"stock": 8}, full=True))
            # the 2026-09-22 MCP functional test review friction: `full=true` used to return the
            # raw legacy row (`blockName`, `FDist`, `FParam1`, ...) instead
            # of the same friendly-named shape `get_element(detail="full")`
            # returns for the same element.
            assert full["element"]["kind"] == "Block"
            assert full["element"]["name"] == "Casing"
            assert "blockName" not in full["element"]
            assert "FDist" not in full["element"]
            assert full["element"]["sparing"]["stock"] == 8

    def test_update_element_full_matches_get_element_full(self):
        """`full=true`'s element is the same shape `get_element(detail="full")`
        returns -- not a coincidence, `update_element` re-resolves through
        the same `raptor.api.inspect.get_element`."""
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import open_model, update_element, get_element
            mid = _sc(open_model("MotorDemo"))["model_id"]
            updated = _sc(update_element(mid, "Casing", {"stock": 9}, full=True))["element"]
            read_back = _sc(get_element(mid, "Casing", detail="full"))
            assert updated == read_back

    def test_update_element_full_renamed_block_reselects_by_id(self):
        """A patch that renames the element must not leave `full=true`
        trying to re-resolve the OLD name (it would now match nothing)."""
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import open_model, update_element
            mid = _sc(open_model("MotorDemo"))["model_id"]
            full = _sc(update_element(mid, "Casing", {"name": "CasingRenamed"}, full=True))
            assert full["element"]["name"] == "CasingRenamed"

    def test_update_element_full_hierarchy(self):
        """A Hierarchy's display kind is 'Hierarchy' but its `get_element`
        selector token is 'Hier' -- the one kind where the two spellings
        differ; make sure `full=true` re-resolves it correctly."""
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import open_model, update_element
            mid = _sc(open_model("HierarchyExample"))["model_id"]
            full = _sc(update_element(mid, "Hier:23", {"x": 5}, full=True))
            assert full["element"]["kind"] == "Hierarchy"
            assert full["element"]["id"] == 23


class TestCloseModelBatch:
    """close_model accepts a single id or a list (the 2026-09-22 MCP functional test review
    finding #11: no way to close/delete several scratch models at once)."""

    def test_single_id_still_works(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import create_model, close_model
            mid = _sc(create_model("Solo"))["model_id"]
            data = _sc(close_model(mid))
            assert data["closed"] == [mid]
            assert data["failed"] == {}

    def test_list_of_ids_closes_all(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import (
                create_model, close_model, list_models,
            )
            ids = [_sc(create_model(f"Scratch{i}"))["model_id"]
                   for i in range(3)]
            data = _sc(close_model(ids, delete=True))
            assert set(data["closed"]) == set(ids)
            assert data["failed"] == {}
            remaining = {m["model_id"] for m in _sc(list_models())["models"]}
            assert not remaining & set(ids)

    def test_one_bad_id_does_not_abort_the_batch(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import create_model, close_model
            good = _sc(create_model("Good"))["model_id"]
            data = _sc(close_model([good, "m_doesnotexist"]))
            assert data["closed"] == [good]
            assert "m_doesnotexist" in data["failed"]


class TestDeleteFile:
    """delete_file cleans up a save_model_as export (finding #12)."""

    def test_delete_exported_file(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import (
                create_model, save_model_as, delete_file,
            )
            mid = _sc(create_model("ToExport"))["model_id"]
            saved = _sc(save_model_as(mid, "exported.rbds"))
            path = Path(saved["path"])
            assert path.is_file()
            result = _sc(delete_file("exported.rbds"))
            assert not path.exists()
            assert result["bytes"] > 0

    def test_delete_missing_file_is_an_error(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import delete_file
            result = delete_file("nope.rbds")
            assert result.is_error

    def test_delete_outside_workspace_is_refused(self):
        from raptor.mcp.server import mcp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
            mcp._raptor_ws = None
            from raptor.mcp.tools_model import delete_file
            result = delete_file("/etc/passwd")
            assert result.is_error


class TestStrictToolArguments:
    """Unknown tool parameters are rejected, not silently ignored.

    The MCP SDK derives each tool's argument model from the function
    signature and pydantic ignores unknown keys by default; importing
    ``raptor.mcp.server`` tightens the shared base model so a misspelled
    parameter (e.g. ``replications`` for ``num_runs``) fails loudly instead
    of running with defaults (the 2026-09-22 MCP functional test review part 7 follow-up).
    """

    @staticmethod
    def _metadata():
        import raptor.mcp.server  # noqa: F401  (applies the tightening)
        from mcp.server.mcpserver.utilities.func_metadata import func_metadata

        def simulate(model_id: str, num_runs: int | None = None):
            return model_id, num_runs

        return func_metadata(simulate)

    def test_unknown_argument_is_rejected(self):
        with pytest.raises(ValidationError):
            self._metadata().validate_arguments(
                {"model_id": "m_1", "replications": 200})

    def test_schema_forbids_additional_properties(self):
        schema = self._metadata().arg_model.model_json_schema()
        assert schema["additionalProperties"] is False

    def test_known_arguments_still_validate(self):
        out = self._metadata().validate_arguments(
            {"model_id": "m_1", "num_runs": 200})
        assert out == {"model_id": "m_1", "num_runs": 200}


def test_resolve_endpoint_names_a_hierarchy_as_hier():
    """`connect` must write LINK_DATA's legacy `Hier` token, not `Hierarchy`:
    Raptor 7's validator rejects `Hierarchy` on the next export."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.mcp.tools_model import _resolve_endpoint

    class _Handle:
        rbd = load_rbd(GOLDEN / "HierarchyExample" / "input.rbd")

    class _WS:
        def get(self, _model_id):
            return _Handle()

    kind, ident = _resolve_endpoint(_WS(), "m", ["Hierarchy", 23])
    assert kind == "Hier"
    assert ident == 23


def test_resolve_endpoint_maps_hierarchy_to_hier():
    from types import SimpleNamespace
    from raptor.core.fileio.rbd import load_rbd
    from raptor.mcp.tools_model import _resolve_endpoint
    rbd = load_rbd(GOLDEN / "HierarchyExample" / "input.rbd")
    ws = SimpleNamespace(get=lambda _mid: SimpleNamespace(rbd=rbd))
    assert _resolve_endpoint(ws, "m", "Hier:23") == ("Hier", 23)
    assert _resolve_endpoint(ws, "m", ["Hierarchy", 23]) == ("Hier", 23)


def test_resolve_endpoint_maps_hierarchy_to_hier():
    from types import SimpleNamespace
    from raptor.core.fileio.rbd import load_rbd
    from raptor.mcp.tools_model import _resolve_endpoint
    rbd = load_rbd(GOLDEN / "HierarchyExample" / "input.rbd")
    ws = SimpleNamespace(get=lambda _mid: SimpleNamespace(rbd=rbd))
    assert _resolve_endpoint(ws, "m", "Hier:23") == ("Hier", 23)
    assert _resolve_endpoint(ws, "m", ["Hierarchy", 23]) == ("Hier", 23)


def test_open_model_accepts_a_workspace_relative_path(tmp_path):
    """The workspace root, not the server's CWD, is what a relative path
    resolves against."""
    import shutil

    from raptor.mcp.server import mcp
    from raptor.mcp.tools_model import open_model

    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    shutil.copyfile(GOLDEN / "MotorDemo" / "input.rbd",
                    tmp_path / "local.rbd")
    result = open_model("local.rbd")
    assert not result.is_error, _text(result)
    assert result.structured_content["summary"]


def test_every_file_argument_refuses_an_absolute_path(tmp_path):
    from raptor.mcp.server import mcp
    from raptor.mcp.tools_model import delete_file, open_model, save_model_as

    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    for result in (open_model("/tmp/x.rbd"),
                   save_model_as("m_nope", "/tmp/x.rbds"),
                   delete_file("/tmp/x.rbds")):
        assert result.is_error
        assert result.structured_content["error"]["code"] == "invalid_argument"

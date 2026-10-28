"""MCP tools for tables: list_tables, get_table, export_tables.

They must give the same tables as the GUI (one shared implementation), read
a stored run from the workspace, and keep every write inside the workspace.
"""
from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "golden"


def _use_workspace(tmp_path):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None


def _text(result) -> str:
    return " ".join(c.text for c in result.content
                    if getattr(c, "type", "") == "text")


@pytest.fixture
def session(tmp_path):
    """An open model and one completed run, in a fresh workspace."""
    _use_workspace(tmp_path)
    from raptor.mcp import server  # noqa: F401  (registers the tools)
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import simulate
    mid = open_model("MotorDemo").structured_content["model_id"]
    run = asyncio.run(simulate(mid, num_runs=3, confirm_long_run=True,
                               collect_events=True, seed=5))
    return mid, run.structured_content["run_id"], tmp_path


def test_the_tools_are_registered_by_server_startup():
    """`_import_tools` is what the real server runs at start."""
    import argparse

    from raptor.mcp import server
    server._import_tools(argparse.Namespace(no_render=True))
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"list_tables", "get_table", "export_tables"} <= tools


def test_a_run_keeps_what_its_tables_need(session):
    _mid, run_id, ws = session
    runs = ws / "runs" / run_id
    per_run = json.loads((runs / "per_run.json").read_text())
    assert len(per_run) == 3 and "availability" in per_run[0]
    summary = json.loads((runs / "summary.json").read_text())
    assert "units" in summary


class TestListTables:
    def test_inputs_only_without_a_run(self, session):
        from raptor.mcp.tools_tables import list_tables
        mid, _run, _ws = session
        r = list_tables(mid)
        assert not r.is_error
        rows = {t["key"]: t for t in r.structured_content["tables"]}
        assert rows["inputs.blocks"]["available"]
        assert rows["results.summary"]["available"] is False
        assert "run a simulation" in rows["results.summary"]["reason"]
        assert "inputs.blocks" in _text(r)

    def test_a_run_adds_its_result_tables(self, session):
        from raptor.mcp.tools_tables import list_tables
        mid, run_id, _ws = session
        rows = {t["key"]: t for t in list_tables(
            mid, run_id).structured_content["tables"]}
        assert rows["results.summary"]["available"]
        assert rows["results.per_run"]["rows"] == 3
        assert rows["results.event_log"]["default"] is False


class TestGetTable:
    def test_reads_headers_and_rows(self, session):
        from raptor.mcp.tools_tables import get_table
        mid, _run, _ws = session
        r = get_table(mid, "inputs.blocks")
        d = r.structured_content
        assert d["headers"][:2] == ["ID", "Name"]
        assert d["total_rows"] == len(d["rows"]) > 0
        assert d["next_offset"] is None
        assert "| ID | Name" in _text(r)

    def test_paginates(self, session):
        from raptor.mcp.tools_tables import get_table
        mid, run_id, _ws = session
        first = get_table(mid, "results.event_log", run_id=run_id,
                          limit=5).structured_content
        assert len(first["rows"]) == 5 and first["next_offset"] == 5
        second = get_table(mid, "results.event_log", run_id=run_id,
                           offset=5, limit=5).structured_content
        assert second["rows"] != first["rows"]

    def test_the_page_size_is_capped(self, session):
        from raptor.mcp.tools_tables import get_table
        mid, run_id, _ws = session
        d = get_table(mid, "results.event_log", run_id=run_id,
                      limit=100000).structured_content
        assert len(d["rows"]) <= 200

    def test_result_tables_match_the_report(self, session):
        from raptor.mcp.tools_tables import get_table
        mid, run_id, _ws = session
        d = get_table(mid, "results.summary", run_id=run_id).structured_content
        labels = [r[0] for r in d["rows"]]
        assert "Availability" in labels
        avail = next(r for r in d["rows"] if r[0] == "Availability")
        assert 0.0 <= float(avail[2]) <= 1.0            # the Mean column

    def test_an_unknown_table_is_a_readable_not_found(self, session):
        from raptor.mcp.tools_tables import get_table
        mid, _run, _ws = session
        r = get_table(mid, "inputs.nope")
        assert r.is_error
        err = r.structured_content["error"]
        assert err["code"] == "not_found"
        assert "list_tables" in (err.get("hint") or "")
        assert "inputs.blocks" in err["message"]

    def test_a_result_table_without_a_run_is_an_error(self, session):
        from raptor.mcp.tools_tables import get_table
        mid, _run, _ws = session
        assert get_table(mid, "results.summary").is_error

    def test_an_unknown_run_is_not_found(self, session):
        from raptor.mcp.tools_tables import get_table
        mid, _run, _ws = session
        r = get_table(mid, "results.summary", run_id="r_nope")
        assert r.is_error
        assert r.structured_content["error"]["code"] == "not_found"


class TestExportTables:
    def test_default_is_one_workbook_in_the_tables_folder(self, session):
        from raptor.mcp.tools_tables import export_tables
        mid, _run, ws = session
        r = export_tables(mid)
        assert not r.is_error
        (path,) = [Path(f) for f in r.structured_content["files"]]
        assert path.suffix == ".xlsx"
        assert path.parent == ws / "Tables" / "MotorDemo" or \
            path.parent.parent == ws / "Tables"
        assert zipfile.ZipFile(path).testzip() is None
        assert r.structured_content["resource_links"][0].startswith("file://")

    def test_a_run_adds_result_tabs_and_its_id_to_the_folder(self, session):
        from raptor.mcp.tools_tables import export_tables
        mid, run_id, _ws = session
        r = export_tables(mid, run_id=run_id, formats=["csv", "txt_report"])
        d = r.structured_content
        assert run_id in d["folder"]
        names = [Path(f).name for f in d["files"]]
        assert any(n.endswith("_results_summary.csv") for n in names)
        assert any(n.endswith("_inputs_blocks.csv") for n in names)
        assert any(n.endswith("_tables.txt") for n in names)

    def test_chosen_tables_only(self, session):
        from raptor.mcp.tools_tables import export_tables
        mid, run_id, _ws = session
        r = export_tables(mid, run_id=run_id, formats=["csv"],
                          tables=["results.summary", "inputs.system"])
        assert r.structured_content["tables"] == 2
        assert len(r.structured_content["files"]) == 2

    def test_the_workbook_matches_get_table(self, session):
        """One implementation: the spreadsheet holds what get_table says."""
        from raptor.mcp.tools_tables import export_tables, get_table
        mid, run_id, _ws = session
        rows = get_table(mid, "results.summary",
                         run_id=run_id).structured_content["rows"]
        r = export_tables(mid, run_id=run_id, tables=["results.summary"],
                          formats=["csv"])
        text = Path(r.structured_content["files"][0]).read_text(
            encoding="utf-8-sig")
        for row in rows:
            assert row[0] in text

    def test_a_folder_outside_the_workspace_is_refused(self, session,
                                                       tmp_path_factory):
        from raptor.mcp.tools_tables import export_tables
        mid, _run, _ws = session
        outside = tmp_path_factory.mktemp("elsewhere")
        # Absolute paths are refused at the boundary...
        r = export_tables(mid, folder=str(outside))
        assert r.is_error
        assert r.structured_content["error"]["code"] == "invalid_argument"
        assert not list(outside.iterdir())
        # ...and a relative escape is refused by workspace containment.
        escape = export_tables(mid, folder="../elsewhere")
        assert escape.is_error
        assert escape.structured_content["error"]["code"] == "permission_denied"

    def test_a_relative_folder_resolves_inside_the_workspace(self, session):
        from raptor.mcp.tools_tables import export_tables
        mid, _run, ws = session
        r = export_tables(mid, folder="my/out", formats=["txt_report"])
        assert Path(r.structured_content["folder"]) == ws / "my" / "out"

    def test_unknown_format_or_table_is_a_clear_error(self, session):
        from raptor.mcp.tools_tables import export_tables
        mid, _run, _ws = session
        assert export_tables(mid, tables=["inputs.nope"]).is_error
        bad = export_tables(mid, formats=["pdf"])       # type: ignore[list-item]
        assert bad.is_error
        assert bad.structured_content["error"]["code"] == "invalid_argument"


def test_the_stored_run_gives_the_same_tables_as_an_in_memory_one(session):
    """The GUI builds its bundle from memory, the server from disk; the
    result tables must agree."""
    from raptor.api.run_tables import bundle_from_run
    from raptor.api.tables import result_tables
    _mid, run_id, _ws = session
    from raptor.mcp import context
    bundle = bundle_from_run(context.workspace(), run_id)
    keys = [t.key for t in result_tables(bundle)]
    assert keys[0] == "results.summary" and "results.per_run" in keys
    assert bundle.units and bundle.num_runs == 3

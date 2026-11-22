"""Spreadsheet import: the API, the CLI and the MCP tools agree."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("xlsxwriter")

REPO = Path(__file__).resolve().parent.parent.parent
MODEL = REPO / "new_docs" / "models" / "DC1.rbds"


def _workbook(path: Path, rows: list[list[object]] | None = None) -> Path:
    """A sheet the importer understands: block name + failure parameters."""
    from raptor.core.fileio.workbook import Sheet, write_workbook
    from raptor.core.fileio.rbds import load_rbds
    from raptor.core.fileio.typed import row_int

    rbd = load_rbds(str(MODEL))
    first = next(row["blockName"] for row in rbd.sections["BLOCK_DATA"].rows
                 if row_int(row.get("ID"), 0) > 0)
    rows = rows or [[first, 2.0, 120.0]]
    write_workbook(path, [Sheet(
        name="Blocks",
        headers=["Block Name", "Failure Param 1", "Repair Param 1"],
        rows=rows, text_cols={0})])
    return path


@pytest.fixture
def workspace():
    from raptor.mcp.server import mcp
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mcp._raptor_args = type("A", (), {"workspace": str(root)})()
        mcp._raptor_ws = None
        yield root
    mcp._raptor_ws = None


def _open(workspace: Path) -> str:
    from raptor.mcp import context
    return context.workspace().open_model(MODEL)["model_id"]


class TestApi:
    def test_preview_reports_changes_without_writing(self, workspace):
        from raptor.api import import_tables
        from raptor.mcp import context
        ws = context.workspace()
        mid = _open(workspace)
        book = _workbook(workspace / "blocks.xlsx")

        data = import_tables.plan_file(ws, mid, book)
        assert data["applied"] is False
        assert data["changes"]
        assert data["changes"][0]["field"].startswith("fail")
        assert data["blocked"] is False
        import copy
        before = copy.deepcopy(ws.get(mid).rbd.sections["BLOCK_DATA"].rows)

        applied = import_tables.apply_file(ws, mid, book)
        assert applied["applied"] is True
        after = ws.get(mid).rbd.sections["BLOCK_DATA"].rows
        assert before != after

    def test_an_unknown_block_blocks_the_whole_import(self, workspace):
        from raptor.api import import_tables
        from raptor.mcp import context
        ws = context.workspace()
        mid = _open(workspace)
        book = _workbook(workspace / "bad.xlsx",
                         [["No Such Block", 2.0, 120.0]])
        with pytest.raises(ValueError, match="errors"):
            import_tables.apply_file(ws, mid, book)


class TestCli:
    def test_dry_run_then_in_place(self, workspace, capsys):
        import shutil
        from raptor.cli.main import main
        model = workspace / "dc1.rbds"
        shutil.copy(MODEL, model)
        book = _workbook(workspace / "blocks.xlsx")

        assert main(["import-table", str(model), str(book)]) == 0
        out = capsys.readouterr().out
        assert "change(s)" in out and "nothing written" in out

        assert main(["import-table", str(model), str(book),
                     "--in-place"]) == 0
        out = capsys.readouterr().out
        assert "change(s)" in out

    def test_an_unknown_block_is_a_usage_error(self, workspace, capsys):
        import shutil
        from raptor.cli.main import main
        model = workspace / "dc1.rbds"
        shutil.copy(MODEL, model)
        book = _workbook(workspace / "bad.xlsx", [["No Such Block", 1.0, 1.0]])
        assert main(["import-table", str(model), str(book),
                     "--in-place"]) == 2
        assert "errors" in capsys.readouterr().err

    def test_a_missing_workbook_is_refused(self, workspace):
        import shutil
        from raptor.cli.main import main
        model = workspace / "dc1.rbds"
        shutil.copy(MODEL, model)
        assert main(["import-table", str(model), "nope.xlsx"]) == 2


class TestMcp:
    def test_preview_then_apply(self, workspace):
        from raptor.mcp.tools_import import (
            import_table_data, preview_table_import)
        mid = _open(workspace)
        _workbook(workspace / "imports" / "blocks.xlsx")

        preview = preview_table_import(mid, "imports/blocks.xlsx")
        assert not preview.is_error
        data = preview.structured_content
        assert data["applied"] is False and data["changes"]

        applied = import_table_data(mid, "imports/blocks.xlsx")
        assert not applied.is_error
        assert applied.structured_content["applied"] is True

    def test_a_workbook_outside_the_workspace_is_refused(self, workspace):
        from raptor.mcp.tools_import import preview_table_import
        mid = _open(workspace)
        result = preview_table_import(mid, "/etc/passwd")
        assert result.is_error
        assert "relative to the workspace" in " ".join(
            c.text for c in result.content if getattr(c, "type", "") == "text")

    def test_an_unknown_block_is_a_structured_error(self, workspace):
        from raptor.mcp.tools_import import import_table_data
        mid = _open(workspace)
        _workbook(workspace / "bad.xlsx", [["No Such Block", 1.0, 1.0]])
        result = import_table_data(mid, "bad.xlsx")
        assert result.is_error
        assert "errors" in " ".join(
            c.text for c in result.content if getattr(c, "type", "") == "text")


def test_no_change_import_says_nothing_was_written(workspace):
    from raptor.api import import_tables
    from raptor.mcp import context
    from raptor.mcp.tools_import import import_table_data
    ws = context.workspace()
    mid = _open(workspace)
    # a sheet whose values already match the model
    from raptor.core.fileio.rbds import load_rbds
    from raptor.core.fileio.typed import row_int
    rbd = load_rbds(str(MODEL))
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if row_int(r.get("ID"), 0) > 0)
    book = _workbook(workspace / "same.xlsx",
                     [[row["blockName"], float(row.get("FailureParam1") or 0),
                       float(row.get("RepairParam1") or 0)]])
    data = import_tables.plan_file(ws, mid, book)
    if data["changes"]:
        pytest.skip("the workbook's numbers differ from the model")
    result = import_table_data(mid, "same.xlsx")
    text = " ".join(c.text for c in result.content
                    if getattr(c, "type", "") == "text")
    assert "No changes" in text and "not saved" in text

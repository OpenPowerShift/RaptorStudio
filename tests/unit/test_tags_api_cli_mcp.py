"""Component tags: the API, the CLI and the MCP tools agree."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden" / "TwoBlockRedundancy" / "input.rbd"


def _rbds(tmp: Path) -> Path:
    """The golden model as `.rbds` (tags need the modern format)."""
    from raptor.core.fileio.model_file import load_model_file
    from raptor.core.fileio.rbds import save_rbds
    target = tmp / "two.rbds"
    save_rbds(load_model_file(GOLDEN), target, meta={"file_name": target.name})
    return target


@pytest.fixture
def workspace():
    from raptor.mcp.server import mcp
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mcp._raptor_args = type("A", (), {"workspace": str(root),
                                          "read_only": False})()
        mcp._raptor_ws = None
        yield root
    mcp._raptor_ws = None


def _open(root: Path):
    from raptor.mcp import context
    ws = context.workspace()
    return ws, ws.open_model(_rbds(root))["model_id"]


class TestApi:
    def test_apply_describe_rename_delete(self, workspace):
        from raptor.api import tags
        ws, mid = _open(workspace)
        tags.set_tags(ws, mid, ["Block:3", "block:4"], ["pumps", " pumps "])
        rows = tags.list_tags(ws, mid)["tags"]
        assert [(r["tag"], r["elements"]) for r in rows] == [("pumps", 2)]
        row = tags.describe_tag(ws, mid, "pumps", colour="#123456",
                                description="Feed")
        assert row["colour"] == "#123456" and row["description"] == "Feed"
        renamed = tags.rename_tag(ws, mid, "pumps", "feed pumps")
        assert renamed["elements"] == 2 and not renamed["merged"]
        assert tags.tags_of(ws, mid, "Block:3")["tags"] == ["feed pumps"]
        assert tags.delete_tag(ws, mid, "feed pumps")["elements"] == 2
        assert tags.list_tags(ws, mid)["tags"] == []

    def test_unknown_refs_and_tags_are_refused(self, workspace):
        from raptor.api import tags
        ws, mid = _open(workspace)
        with pytest.raises(ValueError, match="no such element"):
            tags.set_tags(ws, mid, ["Block:999"], ["x"])
        with pytest.raises(ValueError, match="no tag"):
            tags.rename_tag(ws, mid, "missing", "y")
        with pytest.raises(ValueError, match="mode"):
            tags.set_tags(ws, mid, ["Block:3"], ["x"], mode="merge")

    def test_describe_creates_an_empty_tag(self, workspace):
        from raptor.api import tags
        ws, mid = _open(workspace)
        tags.describe_tag(ws, mid, "later", description="for spares")
        rows = tags.list_tags(ws, mid)["tags"]
        assert rows == [{"tag": "later", "colour": rows[0]["colour"],
                         "description": "for spares", "elements": 0}]


class TestCli:
    def test_apply_list_and_show_in_place(self, workspace, capsys):
        from raptor.cli.main import main
        path = _rbds(workspace)
        assert main(["tags", str(path), "apply", "Block:3", "--tags",
                     "pumps,rotating", "--in-place"]) == 0
        assert main(["tags", str(path), "describe", "pumps",
                     "--description", "Feed", "--in-place"]) == 0
        capsys.readouterr()
        assert main(["tags", str(path), "list", "-f", "json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert {r["tag"]: r["elements"] for r in data["tags"]} == \
            {"pumps": 1, "rotating": 1}
        assert main(["tags", str(path), "show", "Block:3"]) == 0
        assert "pumps, rotating" in capsys.readouterr().out

    def test_without_in_place_nothing_is_written(self, workspace, capsys):
        from raptor.cli.main import main
        path = _rbds(workspace)
        before = path.read_text()
        assert main(["tags", str(path), "apply", "Block:3", "--tags",
                     "x"]) == 0
        assert "pass --in-place" in capsys.readouterr().out
        assert path.read_text() == before

    def test_a_legacy_file_is_refused_for_in_place_writes(self, workspace,
                                                         capsys):
        import shutil
        from raptor.cli.main import main
        legacy = workspace / "two.rbd"
        shutil.copy(GOLDEN, legacy)
        assert main(["tags", str(legacy), "apply", "Block:3", "--tags",
                     "x", "--in-place"]) != 0
        assert ".rbds only" in capsys.readouterr().err


class TestMcp:
    def test_tools_round_trip(self, workspace):
        import raptor.mcp.tools_tags as tt
        from raptor.mcp import context
        _ws, mid = _open(workspace)
        assert not tt.tag_elements(mid, ["Block:3"], ["pumps"]).is_error
        assert not tt.describe_tag(mid, "pumps", colour="#123456").is_error
        listed = tt.list_tags(mid)
        assert not listed.is_error
        assert listed.structured_content["tags"][0]["colour"] == "#123456"
        assert not tt.rename_tag(mid, "pumps", "feed").is_error
        assert not tt.delete_tag(mid, "feed").is_error
        assert context.workspace().get(mid).rbd.tags == {}

    def test_an_unknown_tag_is_a_structured_error(self, workspace):
        import raptor.mcp.tools_tags as tt
        _ws, mid = _open(workspace)
        result = tt.delete_tag(mid, "missing")
        assert result.is_error
        assert "no tag" in json.dumps(result.structured_content or {}) + \
            str(result.content)

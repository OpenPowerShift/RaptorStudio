"""Component groups and tags: the API, the CLI and the MCP tools agree."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden" / "TwoBlockRedundancy" / "input.rbd"


def _rbds(tmp: Path) -> Path:
    """The golden model as `.rbds` (groups need the modern format)."""
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
        mcp._raptor_args = type("A", (), {"workspace": str(root)})()
        mcp._raptor_ws = None
        yield root
    mcp._raptor_ws = None


class TestApi:
    def test_create_list_update_delete_round_trip(self, workspace):
        from raptor.api import groups
        from raptor.mcp import context
        ws = context.workspace()
        mid = ws.open_model(_rbds(workspace))["model_id"]

        created = groups.create_group(
            ws, mid, "Pumps", ["Block:3", "Block:4"], tag="rotating",
            colour="#2E7D32", description="the two pumps")
        assert created["members"] == 2
        listed = groups.list_groups(ws, mid)
        assert listed["count"] == 1
        assert listed["groups"][0]["id"] == created["group"]["id"]

        patched = groups.update_group(ws, mid, "Pumps", name="Pumps A",
                                      add=["block:3"])
        assert patched["group"]["name"] == "Pumps A"
        assert patched["members"] == 2

        with pytest.raises(ValueError, match="no such element"):
            groups.update_group(ws, mid, "Pumps A", add=["Block:99"])

        removed = groups.delete_group(ws, mid, "Pumps A")
        assert removed["name"] == "Pumps A"
        assert groups.list_groups(ws, mid)["count"] == 0

    def test_tags_are_cleaned_and_unknown_refs_refused(self, workspace):
        from raptor.api import groups
        from raptor.mcp import context
        ws = context.workspace()
        mid = ws.open_model(_rbds(workspace))["model_id"]

        result = groups.set_tags(ws, mid, ["Block:3", "block:3"],
                                 [" rotating ", "rotating"])
        assert result["count"] == 1
        assert result["changed"][0]["tags"] == ["rotating"]
        assert groups.tags_of(ws, mid, "block:3")["tags"] == ["rotating"]

        groups.set_tags(ws, mid, ["Block:3"], [], mode="replace")
        assert groups.tags_of(ws, mid, "block:3")["tags"] == []

        with pytest.raises(ValueError, match="no such element"):
            groups.set_tags(ws, mid, ["Block:99"], ["x"])

    def test_housekeeping_uses_the_same_store(self, workspace):
        """A group survives a save/reload through the workspace."""
        from raptor.api import groups
        from raptor.mcp import context
        ws = context.workspace()
        mid = ws.open_model(_rbds(workspace))["model_id"]
        groups.create_group(ws, mid, "Cooling", ["Node:1"])
        ws.save(mid)
        mid2 = ws.open_model(ws.get(mid).path)["model_id"]
        assert groups.list_groups(ws, mid2)["count"] == 1


class TestCli:
    def test_add_list_and_tag_in_place(self, workspace, capsys):
        from raptor.cli.main import main
        model = _rbds(workspace)
        assert main(["groups", str(model), "add", "Pumps",
                     "--members", "Block:3,Block:4",
                     "--description", "the pumps", "--in-place"]) == 0
        assert main(["groups", str(model), "list"]) == 0
        out = capsys.readouterr().out
        assert "Pumps" in out and "2 member(s)" in out

        assert main(["groups", str(model), "tag", "Block:3",
                     "--tags", "rotating", "--in-place"]) == 0
        assert main(["groups", str(model), "tags", "block:3"]) == 0
        assert "rotating" in capsys.readouterr().out

    def test_a_legacy_file_is_refused_for_in_place_writes(self, workspace, capsys):
        from raptor.cli.main import main
        legacy = workspace / "legacy.rbd"
        shutil.copy(GOLDEN, legacy)
        assert main(["groups", str(legacy), "add", "X", "--in-place"]) == 2
        err = capsys.readouterr().err
        assert ".rbds only" in err

    def test_json_output(self, workspace, capsys):
        import json
        from raptor.cli.main import main
        model = _rbds(workspace)
        assert main(["groups", str(model), "list", "-f", "json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["count"] == 0


class TestMcp:
    def _open(self, workspace: Path) -> str:
        from raptor.mcp import context
        return context.workspace().open_model(_rbds(workspace))["model_id"]

    def test_tools_round_trip(self, workspace):
        from raptor.mcp.tools_groups import (
            create_group, delete_group, list_groups, tag_elements,
            update_group)
        mid = self._open(workspace)

        created = create_group(mid, "Pumps", ["Block:3", "Block:4"],
                               colour="#2E7D32")
        assert created.structured_content["members"] == 2

        listed = list_groups(mid)
        assert listed.structured_content["count"] == 1
        assert listed.structured_content["groups"][0]["name"] == "Pumps"

        patched = update_group(mid, "Pumps", name="Pumps A")
        assert patched.structured_content["group"]["name"] == "Pumps A"

        tagged = tag_elements(mid, ["Block:4"], ["standby"], mode="add")
        assert tagged.structured_content["changed"][0]["tags"] == ["standby"]

        removed = delete_group(mid, "Pumps A")
        assert removed.structured_content["name"] == "Pumps A"
        assert list_groups(mid).structured_content["count"] == 0

    def test_an_unknown_group_is_a_structured_error(self, workspace):
        from raptor.mcp.tools_groups import update_group
        mid = self._open(workspace)
        result = update_group(mid, "Nope", name="X")
        assert result.is_error
        assert "no group" in " ".join(
            c.text for c in result.content if getattr(c, "type", "") == "text")


def test_create_without_members_keeps_the_existing_list(workspace):
    from raptor.api import groups
    from raptor.mcp import context
    ws = context.workspace()
    mid = ws.open_model(_rbds(workspace))["model_id"]
    groups.create_group(ws, mid, "Pumps", ["Block:3", "Block:4"])
    again = groups.create_group(ws, mid, "Pumps", colour="#123456")
    assert again["members"] == 2


def test_a_rename_cannot_collide_with_another_group(workspace):
    from raptor.api import groups
    from raptor.mcp import context
    ws = context.workspace()
    mid = ws.open_model(_rbds(workspace))["model_id"]
    groups.create_group(ws, mid, "Pumps", ["Block:3"])
    groups.create_group(ws, mid, "Valves", ["Block:4"])
    with pytest.raises(ValueError, match="already exists"):
        groups.update_group(ws, mid, "Valves", name="pumps")


def test_replace_and_add_are_mutually_exclusive(workspace):
    from raptor.api import groups
    from raptor.mcp import context
    ws = context.workspace()
    mid = ws.open_model(_rbds(workspace))["model_id"]
    groups.create_group(ws, mid, "Pumps", ["Block:3"])
    with pytest.raises(ValueError, match="not both"):
        groups.update_group(ws, mid, "Pumps", members=["Block:3"],
                            add=["Block:4"])


def test_removing_a_ref_whose_element_is_gone_still_works(workspace):
    from raptor.api import groups
    from raptor.mcp import context
    from raptor.core.fileio.typed import row_int
    ws = context.workspace()
    mid = ws.open_model(_rbds(workspace))["model_id"]
    groups.create_group(ws, mid, "Pumps", ["Block:3", "Block:4"])
    # delete a block behind the group's back, as an MCP delete would
    rows = ws.get(mid).rbd.sections["BLOCK_DATA"].rows
    rows[:] = [r for r in rows if row_int(r.get("ID"), 0) != 3]
    patched = groups.update_group(ws, mid, "Pumps", remove=["Block:3"])
    assert patched["members"] == 1


def test_read_only_refuses_group_and_tag_writes(workspace):
    from raptor.mcp import context
    from raptor.mcp.tools_groups import create_group, tag_elements
    mid = context.workspace().open_model(_rbds(workspace))["model_id"]
    args = context.parsed_args()
    args.read_only = True
    try:
        created = create_group(mid, "Pumps", ["Block:3"])
        tagged = tag_elements(mid, ["Block:3"], ["rotating"])
    finally:
        args.read_only = False
    for result in (created, tagged):
        assert result.is_error
        text = " ".join(c.text for c in result.content
                        if getattr(c, "type", "") == "text")
        assert "read-only" in text

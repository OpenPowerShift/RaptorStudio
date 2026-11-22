"""MCP tools for project health, share-safe copies and version history."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytest.importorskip("mcp")

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


def _sc(result):
    assert not result.is_error, " ".join(
        c.text for c in result.content if getattr(c, "type", "") == "text")
    return result.structured_content


def _text(result) -> str:
    return " ".join(c.text for c in result.content
                    if getattr(c, "type", "") == "text")


@pytest.fixture
def workspace():
    from raptor.mcp.server import mcp
    with tempfile.TemporaryDirectory() as tmpdir:
        mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
        mcp._raptor_ws = None
        yield Path(tmpdir)
    mcp._raptor_ws = None


def _open(workspace: Path) -> str:
    """A model in the workspace: a golden file copied in and opened."""
    from raptor.mcp.tools_model import open_model
    source = GOLDEN / "TwoBlockRedundancy" / "input.rbd"
    target = workspace / "block.rbd"
    target.write_bytes(source.read_bytes())
    return _sc(open_model("block.rbd"))["model_id"]


def test_the_tools_are_registered():
    from raptor.mcp import tools_project  # noqa: F401
    from raptor.mcp.server import import_all_tool_modules, mcp
    import_all_tool_modules()
    names = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert {"project_health", "share_safe_copy", "list_versions",
            "snapshot_version", "label_version",
            "restore_version"} <= names


def test_project_health_counts_and_findings(workspace):
    from raptor.mcp.tools_project import project_health
    data = _sc(project_health(_open(workspace)))
    assert data["counts"]["blocks"] == 2
    assert "block" in data["summary"]
    assert isinstance(data["errors"], list)
    assert isinstance(data["warnings"], list)


def test_share_safe_copy_removes_prose_and_never_touches_the_source(workspace):
    from raptor.mcp.tools_project import share_safe_copy
    from raptor.mcp import context
    model_id = _open(workspace)
    handle = context.workspace().get(model_id)
    before = Path(handle.effective_path).read_bytes()

    result = share_safe_copy(model_id, "out/shared")
    data = _sc(result)
    assert data["path"] == "out/shared.rbds"
    written = workspace / data["path"]
    assert written.is_file()
    assert data["counts"]["names"] > 0
    assert data["residual_risk"]
    assert Path(handle.effective_path).read_bytes() == before
    # the copy is a readable model with its structure intact
    from raptor.core.fileio.model_file import load_model_file
    copy = load_model_file(written)
    real_blocks = [r for r in copy.section("BLOCK_DATA").rows
                   if str(r.get("ID", "")) not in ("", "0")]
    assert len(real_blocks) == 2


def test_share_safe_copy_can_keep_every_kind(workspace):
    from raptor.mcp.tools_project import share_safe_copy
    data = _sc(share_safe_copy(
        _open(workspace), "keep.rbds", names=False, comments=False,
        annotations="keep", metadata=False, advisor=False, rates=False))
    assert data["removed"] == 0


def test_share_safe_copy_refuses_an_absolute_path(workspace):
    from raptor.mcp.tools_project import share_safe_copy
    result = share_safe_copy(_open(workspace), "/tmp/escape.rbds")
    assert result.is_error
    assert "relative to the workspace" in _text(result)


def test_versions_round_trip(workspace):
    from raptor.mcp import context
    from raptor.mcp.tools_project import (
        label_version, list_versions, restore_version, snapshot_version)
    model_id = _open(workspace)

    empty = _sc(list_versions(model_id))
    assert empty["versions"] == []

    first = _sc(snapshot_version(model_id, label="first"))
    assert first["created"] is True
    version = first["version"]["snapshot_id"]

    listed = _sc(list_versions(model_id))
    assert listed["versions"][0]["snapshot_id"] == version
    assert listed["versions"][0]["label"] == "first"

    _sc(label_version(model_id, version, "released"))
    assert _sc(list_versions(model_id))["versions"][0]["label"] == "released"

    restored = _sc(restore_version(model_id, version, "restored"))
    assert (workspace / restored["path"]).is_file()
    assert restored["snapshot_id"] == version
    # the open model's own file was not replaced
    assert context.workspace().get(model_id).effective_path.exists()


def test_an_unchanged_file_is_relabelled_not_duplicated(workspace):
    from raptor.mcp.tools_project import list_versions, snapshot_version
    model_id = _open(workspace)
    _sc(snapshot_version(model_id, label="one"))
    again = _sc(snapshot_version(model_id, label="two"))
    assert again["created"] is False
    versions = _sc(list_versions(model_id))["versions"]
    assert len(versions) == 1
    assert versions[0]["label"] == "two"


def test_restore_refuses_a_managed_path(workspace):
    from raptor.mcp.tools_project import (
        restore_version, snapshot_version)
    model_id = _open(workspace)
    version = _sc(snapshot_version(model_id))["version"]["snapshot_id"]
    result = restore_version(model_id, version, "models/clobber.rbds")
    assert result.is_error
    assert "managed by the workspace" in _text(result)


def test_unknown_version_is_an_error(workspace):
    from raptor.mcp.tools_project import restore_version
    result = restore_version(_open(workspace), "nope", "x.rbds")
    assert result.is_error
    assert "no version" in _text(result)

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


def test_versions_carry_a_markdown_description(workspace):
    from raptor.mcp.tools_project import (
        label_version, list_versions, snapshot_version)
    model_id = _open(workspace)
    first = _sc(snapshot_version(model_id, label="a", description="# Why\n\n- x"))
    version = first["version"]["snapshot_id"]
    assert first["version"]["description"] == "# Why\n\n- x"
    assert _sc(list_versions(model_id))["versions"][0]["description"] == "# Why\n\n- x"
    # description only: the label is kept
    out = _sc(label_version(model_id, version, description="new **note**"))
    assert out["label"] == "a" and out["description"] == "new **note**"
    # label only: the description is kept
    out = _sc(label_version(model_id, version, "b"))
    assert out["label"] == "b" and out["description"] == "new **note**"
    out = _sc(label_version(model_id, version, description=""))
    assert out["description"] == ""
    assert label_version(model_id, version).is_error
    assert label_version(model_id, "nope", description="x").is_error


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


def test_read_only_refuses_the_share_safe_copy(workspace):
    from raptor.mcp import context
    from raptor.mcp.tools_project import share_safe_copy
    mid = _open(workspace)
    args = context.parsed_args()
    args.read_only = True
    try:
        result = share_safe_copy(mid, "out/shared")
    finally:
        args.read_only = False
    assert result.is_error
    assert "read-only" in _text(result)
    assert not (workspace / "out" / "shared.rbds").exists()


def test_an_unchanged_snapshot_keeps_the_documented_shape(workspace):
    from raptor.mcp.tools_project import snapshot_version
    mid = _open(workspace)
    first = _sc(snapshot_version(mid, label="one"))
    again = _sc(snapshot_version(mid, label="two"))
    assert set(first) == set(again) == {"created", "file", "version"}
    assert again["created"] is False
    assert again["version"]["snapshot_id"] == first["version"]["snapshot_id"]
    assert again["version"]["label"] == "two"
    assert not again["file"].startswith("/")


def test_share_safe_copy_refuses_to_overwrite_its_source(workspace):
    from raptor.mcp import context
    from raptor.mcp.tools_project import share_safe_copy
    # an .rbds source in the workspace, so the copy's name can collide
    from raptor.core.fileio.model_file import load_model_file
    from raptor.core.fileio.rbds import save_rbds
    source = workspace / "same.rbds"
    save_rbds(load_model_file(GOLDEN / "TwoBlockRedundancy" / "input.rbd"),
              source, meta={"file_name": source.name})
    mid = context.workspace().open_model(source)["model_id"]
    before = source.read_bytes()
    result = share_safe_copy(mid, "same.rbds")
    assert result.is_error
    assert "must not overwrite" in _text(result)
    assert source.read_bytes() == before


def test_restore_version_refuses_the_open_models_file(workspace):
    from raptor.mcp import context
    from raptor.mcp.tools_project import restore_version, snapshot_version
    mid = _open(workspace)
    version = _sc(snapshot_version(mid))["version"]["snapshot_id"]
    handle = context.workspace().get(mid)
    source = Path(handle.source)
    before = source.read_bytes()
    result = restore_version(mid, version, source.name)
    assert result.is_error
    assert "refused" in _text(result)
    assert source.read_bytes() == before


def test_restore_refuses_an_existing_target_unless_forced(workspace):
    from raptor.mcp.tools_project import restore_version, snapshot_version
    mid = _open(workspace)
    version = _sc(snapshot_version(mid))["version"]["snapshot_id"]
    out = workspace / "copy.rbds"
    out.write_bytes(b"already here")
    result = restore_version(mid, version, "copy.rbds")
    assert result.is_error
    assert "already exists" in _text(result)
    assert out.read_bytes() == b"already here"
    forced = restore_version(mid, version, "copy.rbds", force=True)
    assert not forced.is_error
    assert out.read_bytes() != b"already here"

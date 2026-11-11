"""The version is declared in six places; they must agree.

`pyproject.toml` is what gets built and published, `raptor/__init__.py` is
what the app and the CLI print, `server.json` is what the MCP Registry
lists, `packaging/raptor-studio-mcp/pyproject.toml` is the companion
package (its version and its `raptor-studio[mcp]==` pin), and
`.release-please-manifest.json` is the base release-please bumps from, and
`uv.lock` records the project's own version (it had gone stale at 0.8.0
while everything else said 0.9.0; release-please now updates it too).
They had already drifted (pyproject said 0.0.1 while `__version__` said
0.1.0), and nothing failed -- so a run could report a version no artefact
carried. release-please now updates `__init__.py` too (the
`x-release-please-version` marker); this test is the backstop for every
path that does not go through release-please.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def _pyproject() -> str:
    return tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]


def _init() -> str:
    import raptor
    return raptor.__version__


def test_pyproject_and_package_version_agree():
    assert _pyproject() == _init(), (
        "pyproject.toml and raptor/__init__.py disagree; release-please "
        "updates both (see the x-release-please-version marker)")


def test_server_json_lists_the_same_version_twice_and_matches():
    data = json.loads((REPO / "server.json").read_text())
    assert data["version"] == _pyproject()
    assert data["packages"][0]["version"] == _pyproject()


def test_release_please_manifest_matches():
    data = json.loads((REPO / ".release-please-manifest.json").read_text())
    assert data["."] == _pyproject(), (
        "the manifest is release-please's base version; a mismatch makes it "
        "cut the wrong tag")


def test_the_mcp_companion_package_matches_and_pins_the_release():
    project = tomllib.loads(
        (REPO / "packaging" / "raptor-studio-mcp" / "pyproject.toml").read_text())["project"]
    assert project["version"] == _pyproject()
    assert f"raptor-studio[mcp]=={_pyproject()}" in project["dependencies"], (
        "the companion must pin exactly this release of raptor-studio, or "
        "`uvx raptor-studio-mcp` installs a different version")


def test_uv_lock_records_the_same_version():
    lock = tomllib.loads((REPO / "uv.lock").read_text())
    own = [package["version"] for package in lock["package"]
           if package["name"] == "raptor-studio"]
    assert own == [_pyproject()], (
        "uv.lock records a different raptor-studio version. release-please cannot "
        "edit uv.lock (its TOML updater has no filter support), so on the "
        "release PR's branch set the raptor-studio entry's version line in "
        "uv.lock to the new version and push it; see RELEASING.md, "
        "\"Merge the release PR\"")


def test_release_please_updates_every_versioned_file():
    """Each file this module checks, other than pyproject.toml and the
    manifest (release-please's own), is in release-please's extra-files."""
    config = json.loads((REPO / "release-please-config.json").read_text())
    paths = {entry["path"] for entry in config["packages"]["."]["extra-files"]}
    assert {"server.json", "raptor/__init__.py", "uv.lock",
            "packaging/raptor-studio-mcp/pyproject.toml"} <= paths

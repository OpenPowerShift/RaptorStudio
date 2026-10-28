"""The version is declared in four places; they must agree.

`pyproject.toml` is what gets built and published, `raptor/__init__.py` is
what the app and the CLI print, `server.json` is what the MCP Registry
lists, and `.release-please-manifest.json` is the base release-please
bumps from. They had already drifted (pyproject said 0.0.1 while
`__version__` said 0.1.0), and nothing failed -- so a run could report a
version no artefact carried. release-please now updates `__init__.py` too
(the `x-release-please-version` marker); this test is the backstop for
every path that does not go through release-please.
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

"""An `mcp` SDK outside the supported range is reported, not crashed on (#4 item 1).

`pip install --no-deps raptor-studio` next to mcp 1.30.0 made `raptor mcp` fail
with "cannot import name 'MCPServer' from 'mcp.server'" while `raptor doctor`
printed `ok mcp SDK: 1.30.0`.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from raptor.cli.main import main
from raptor.mcp import launch, sdk

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("version", ["2.2.0", "2.2", "2.3.1", "2.10.0", "2.99.9rc1"])
def test_supported_versions_pass(version):
    assert sdk.problem(version) is None


@pytest.mark.parametrize("version", ["1.30.0", "1.0", "2.1.9", "3.0.0", "3.4"])
def test_other_versions_are_named_with_the_fix(version):
    why = sdk.problem(version)
    assert version in why and sdk.REQUIRED in why and "pip install -U" in why


def test_a_missing_sdk_says_to_install_the_extra():
    assert "raptor-studio[mcp]" in sdk.problem(None)


def test_a_version_that_cannot_be_read_does_not_block():
    assert sdk.problem("dev") is None


def test_the_range_matches_pyproject():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    spec = next(d for d in deps["optional-dependencies"]["mcp"] if d.startswith("mcp"))
    assert spec.replace(" ", "") == f"mcp{sdk.REQUIRED}"


def test_every_entry_point_goes_through_the_launcher():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["raptor-studio-mcp"] == "raptor.mcp.launch:main"
    assert "launch" in (ROOT / "raptor/mcp/__main__.py").read_text()
    assert "launch" in (ROOT / "raptor/cli/cmd_mcp.py").read_text()


def test_the_launcher_explains_a_bad_sdk_and_does_not_start(monkeypatch, capsys):
    monkeypatch.setattr(sdk, "installed", lambda: "1.30.0")
    assert launch.main(["--help"]) == 2
    err = capsys.readouterr().err
    assert "1.30.0" in err and "mcp>=2.2,<3" in err and "Traceback" not in err


def test_the_doctor_reports_a_bad_sdk(monkeypatch, capsys):
    monkeypatch.setattr(sdk, "installed", lambda: "1.30.0")
    code = main(["doctor"])
    line = next(ln for ln in capsys.readouterr().out.splitlines() if "mcp SDK" in ln)
    assert line.startswith("BAD") and "1.30.0" in line
    assert code != 0


def test_the_doctor_is_ok_with_the_installed_sdk(capsys):
    main(["doctor"])
    line = next(ln for ln in capsys.readouterr().out.splitlines() if "mcp SDK" in ln)
    assert line.startswith("ok")

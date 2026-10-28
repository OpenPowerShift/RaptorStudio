"""The bundled docs and tutorial models are found in a checkout AND shipped in
the wheel: an installed MCP server once answered `read_doc` with not-found and
listed no examples, because it only looked beside the package and the wheel
puts `docs/` and `new_docs/` under the install's data directory."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from raptor import bundled
from raptor.mcp import docs, examples

REPO = Path(__file__).resolve().parents[2]


def test_data_root_is_the_checkout_here():
    assert bundled.data_root() == REPO
    assert docs.CHAPTERS.is_dir()


def test_candidate_roots_cover_venv_and_user_installs():
    roots = bundled.candidate_roots()
    assert roots[0] == REPO
    assert Path(sys.prefix).resolve() in roots


def test_data_root_finds_an_install_prefix(tmp_path, monkeypatch):
    (tmp_path / "new_docs" / "chapters").mkdir(parents=True)
    monkeypatch.setattr(bundled, "_CHECKOUT", tmp_path / "site-packages")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    bundled.data_root.cache_clear()
    try:
        assert bundled.data_root() == tmp_path.resolve()
    finally:
        bundled.data_root.cache_clear()


def test_tutorial_models_are_examples():
    names = {e["name"] for e in examples.list_examples()}
    tutorial = {p.stem for p in (REPO / "new_docs" / "models").glob("*.rbd*")}
    assert tutorial <= names
    assert examples.find_example("water1") == REPO / "new_docs" / "models" / "Water1.rbds"


def _wheel_data_files() -> set[str]:
    table = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["setuptools"]["data-files"]
    out: set[str] = set()
    for patterns in table.values():
        for pattern in patterns:
            out |= {str(p.relative_to(REPO)) for p in REPO.glob(pattern)}
    return out


def test_wheel_ships_what_the_mcp_server_serves():
    shipped = _wheel_data_files()
    needed = [docs.RBDS_FORMAT, docs.IEC61078_DOC, docs.MCP_GUIDE,
              *docs.CHAPTERS.glob("*.adoc"),
              *(REPO / "new_docs" / "models").glob("*.rbd*")]
    missing = [str(p.relative_to(REPO)) for p in needed if str(p.relative_to(REPO)) not in shipped]
    assert not missing


def test_tutorial_models_validate_clean():
    """The shipped examples are what new users open first: no errors and no
    warnings. (Seven once fanned out from a Start or hierarchy In marker,
    which Raptor 7 refuses; gen_models.py now inserts a Split node.)"""
    from raptor.api.validate import issues
    from raptor.core.fileio.rbds import load_rbds

    dirty = {}
    for path in sorted((REPO / "new_docs" / "models").glob("*.rbds")):
        found = [str(i) for i in issues(load_rbds(str(path)))]
        if found:
            dirty[path.name] = found
    assert not dirty

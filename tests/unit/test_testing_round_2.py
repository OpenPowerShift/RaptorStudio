"""Fixes from the second round of new-user testing (CLI and GUI)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _cli(tmp_path, *args):
    env = dict(os.environ, HOME=str(tmp_path), XDG_STATE_HOME=str(tmp_path))
    return subprocess.run([sys.executable, "-m", "raptor.cli.main", *args],
                          capture_output=True, text=True, env=env, timeout=180)


def _model(tmp_path, name="m.rbds", rename=None):
    from raptor.api.build import build_from_spec, series
    from raptor.core.fileio.rbds import save_rbds
    spec = series(2)
    if rename:
        spec["blocks"][0]["name"] = rename
        for link in spec["links"]:
            for end in ("from", "to"):
                if link[end] == "Block1":
                    link[end] = rename
    path = tmp_path / name
    save_rbds(build_from_spec(spec), str(path))
    return path


def test_convert_refuses_to_overwrite_its_input(tmp_path):
    model = _model(tmp_path)
    before = model.read_bytes()
    out = _cli(tmp_path, "convert", str(model), str(model))
    assert out.returncode == 2 and "is the input file" in out.stderr
    assert model.read_bytes() == before


def test_convert_names_the_formats_for_an_unknown_extension(tmp_path):
    out = _cli(tmp_path, "convert", str(_model(tmp_path)), str(tmp_path / "a.txt"))
    assert out.returncode == 2 and ".rbds, .rbd or .awb" in out.stderr


def test_rbd_export_refuses_non_latin1_names_and_leaves_no_file(tmp_path):
    model = _model(tmp_path, rename="発電機")
    out = _cli(tmp_path, "convert", str(model), str(tmp_path / "c.rbd"))
    assert out.returncode == 6 and "発電機" in out.stderr
    assert not (tmp_path / "c.rbd").exists()


@pytest.mark.parametrize("assignment,word", [
    ("system.termination=bogus", "TimeSim"),
    ("system.runs=1.5", "whole number"),
    ("system.units=furlongs", "hours"),
])
def test_set_refuses_system_values_it_would_ignore(tmp_path, assignment, word):
    out = _cli(tmp_path, "set", str(_model(tmp_path)), "-o",
               str(tmp_path / "o.rbds"), assignment)
    assert out.returncode == 2 and word in out.stderr


def test_set_accepts_known_termination_aliases():
    from raptor.api import build
    import tempfile
    from raptor.api.workspace import Workspace
    ws = Workspace(tempfile.mkdtemp())
    mid = ws.create_model("t", template="series", n=2)["model_id"]
    build.configure_system(ws, mid, termination="cycles", units="units")
    row = ws.get(mid).rbd.sections["SYSTEM_DATA"].rows[0]
    assert row["TerminationType"] == "CycleSim" and row["units"] == "units"


def test_set_takes_a_distribution_with_its_parameters(tmp_path):
    out = _cli(tmp_path, "set", str(_model(tmp_path)), "-o", str(tmp_path / "o.rbds"),
               "block:Block1.fail_dist=weibull", "block:Block1.fail_params=1.5,500")
    assert out.returncode == 0, out.stderr


def test_assignment_groups_pair_a_law_with_its_parameters():
    from raptor.api.paths import assignment_groups
    texts = ["block:A.fail_dist=weibull", "block:A.fail_params=1.5,500",
             "system.runs=5", "block:B.fail_params=3"]
    assert assignment_groups(texts) == [texts[:2], [texts[2]], [texts[3]]]


def test_a_study_stopped_part_way_is_a_timeout_not_an_internal_error():
    from raptor.api.progress import Cancelled
    from raptor.mcp.result import error_result
    res = error_result(Cancelled(), tool="recommend_changes")
    text = res.content[0].text
    assert res.is_error and "internal" not in text and "timeout_s" in text
    assert res.structured_content["error"]["code"] == "timeout"

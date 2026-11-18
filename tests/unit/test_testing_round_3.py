"""Fixes from the third round of new-user testing (MCP and API)."""
from __future__ import annotations

import tempfile

import pytest


def _ws_model(spec=None):
    from raptor.api.build import build_from_spec, series
    from raptor.api.workspace import Workspace
    ws = Workspace(tempfile.mkdtemp())
    info = ws.register_model(build_from_spec(spec or series(3)), name="m")
    ws.save(info["model_id"])
    return ws, info["model_id"]


_PHASES = [{"name": "Launch", "duration_dist": 19, "params": [100],
            "mission": True},
           {"name": "Cruise", "duration_dist": "Fixed", "params": [500],
            "mission": True}]


def test_a_refused_phase_assignment_changes_nothing():
    from raptor.api.build import BuildError, configure_phases
    ws, mid = _ws_model()
    configure_phases(ws, mid, _PHASES,
                     assignments={"Block1": ["A: 1.000", "C: 1.000"]})
    before = [dict(r) for r in ws.get(mid).rbd.section("BLOCK_DATA").rows]
    with pytest.raises(BuildError):
        configure_phases(ws, mid, _PHASES,
                         assignments={"Block2": ["Cut", "A: 1"]})
    after = [dict(r) for r in ws.get(mid).rbd.section("BLOCK_DATA").rows]
    assert after == before


def test_phases_can_be_assigned_without_restating_them():
    from raptor.api.build import configure_phases
    ws, mid = _ws_model()
    configure_phases(ws, mid, _PHASES)
    result = configure_phases(ws, mid,
                              assignments={"Block1": ["A: 1.000", "C: 1.000"]})
    assert result["phases"] == 2 and result["assigned"] == 1


def test_a_phase_duration_may_be_named():
    from raptor.api.build import build_from_spec, series
    spec = series(2)
    spec["phases"] = _PHASES
    rbd = build_from_spec(spec)
    assert len(rbd.section("PHASES").rows) == 2


def test_renaming_a_spare_pool_renames_its_uses():
    from raptor.api import build
    ws, mid = _ws_model()
    build.add_spare_pool(ws, mid, "Seals")
    build.update_element(ws, mid, "Block1",
                         {"sparing_type": "pooled", "spare_pool": "Seals"})
    build.update_element(ws, mid, "Pool:Seals", {"name": "Seals2"})
    row = next(r for r in ws.get(mid).rbd.section("BLOCK_DATA").rows
               if r.get("blockName") == "Block1")
    assert row["SparePoolName"] == "Seals2"


@pytest.mark.parametrize("bad", [0, 6, -1, 2.5, True])
def test_an_unknown_node_type_is_refused_at_build(bad):
    from raptor.api.build import BuildError, build_from_spec, series
    spec = series(2)
    spec["nodes"].append({"name": "X", "type": bad, "n": 0, "out": 0})
    with pytest.raises(BuildError, match="node type"):
        build_from_spec(spec)


def test_a_refused_save_puts_the_model_back(monkeypatch):
    ws, mid = _ws_model()
    handle = ws.get(mid)
    n = len(handle.rbd.section("BLOCK_DATA").rows)
    handle.rbd.section("BLOCK_DATA").rows.pop()

    def too_big(size):
        raise ValueError("model too large")
    monkeypatch.setattr(ws, "_check_model_size", too_big)
    with pytest.raises(ValueError, match="too large"):
        ws.save(mid)
    assert len(ws.get(mid).rbd.section("BLOCK_DATA").rows) == n


# --- files and the command line -------------------------------------------

def test_a_bom_rbds_loads(tmp_path):
    from raptor.api.build import build_from_spec, series
    from raptor.core.fileio.rbds import load_rbds, render_rbds
    path = tmp_path / "bom.rbds"
    path.write_bytes(b"\xef\xbb\xbf"
                     + render_rbds(build_from_spec(series(2))).encode())
    assert load_rbds(str(path)).section("BLOCK_DATA").rows


def test_a_truncated_rbd_says_so(tmp_path):
    from raptor.api.build import build_from_spec, series
    from raptor.core.fileio.rbd import load_rbd, save_rbd
    whole = tmp_path / "whole.rbd"
    save_rbd(build_from_spec(series(40)), str(whole))
    text = whole.read_text(encoding="latin-1")
    cut = tmp_path / "cut.rbd"
    cut.write_text(text[: len(text) // 3], encoding="latin-1")
    load_rbd(str(whole))
    with pytest.raises(ValueError, match="truncated"):
        load_rbd(str(cut))


@pytest.mark.parametrize("field,value", [
    ("trials", -3), ("trials", 1e30), ("termination_type", "Bogus"),
    ("term_condition", -1.0)])
def test_bad_experiment_values_are_schema_errors(field, value):
    import json

    from raptor.api.build import build_from_spec, series
    from raptor.core.fileio.rbds import render_rbds
    from raptor.core.fileio.rbds_schema import check_document
    doc = json.loads(render_rbds(build_from_spec(series(2))))
    doc.setdefault("experiment", {})[field] = value
    errors = [f for f in check_document(doc) if f.severity == "error"]
    assert any(field in f.path for f in errors)


def test_metrics_match_without_case_and_unknown_ones_are_refused():
    from raptor.cli.cmd_run import _resolve_metrics
    from raptor.cli.exitcodes import CliError
    summary = {"Availability": {}, "MDT": {}}
    assert _resolve_metrics(["availability", "mdt"], summary) == \
        ["Availability", "MDT"]
    with pytest.raises(CliError, match="Bogus"):
        _resolve_metrics(["Bogus"], summary)

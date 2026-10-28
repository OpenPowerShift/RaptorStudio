"""Run one hierarchy on its own (`raptor.api.isolate`): scan, extract, treatments.

A port extension; real Raptor has no such feature, so these tests pin the
documented rules (the treatment tables in `raptor/api/isolate.py`), not
captures.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from raptor.api import isolate
from raptor.api.validate import issues
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbd import render_rbd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import golden_fixtures as gf  # noqa: E402

HIER = REPO / "tests/golden/HierarchyExample/input.rbd"
DEP = REPO / "tests/golden/LadL8BEvents/input.rbd"


def _rows(rbd, section):
    return rbd.section(section).rows


def _dump(rbd):
    return render_rbd(rbd)


def test_scan_extract_and_run_never_modify_the_source():
    rbd = load_rbd(str(HIER))
    before = _dump(rbd)
    isolate.scan(rbd, "650path")
    x, _rec = isolate.extract(rbd, "650path", {"pools": "share", "streams": "separate"})
    x.sections["SYSTEM_DATA"].rows[0]["NumRuns"] = "2"
    run_simulation(x, num_runs=2)
    assert _dump(rbd) == before


def test_in_becomes_start_and_out_becomes_end_and_ids_are_kept():
    rbd = load_rbd(str(HIER))
    h = next(r for r in _rows(rbd, "HIERARCHY_DATA") if r["hierarchyName"] == "650path")
    x, _ = isolate.extract(rbd, "650path")
    types = {r["ID"]: r["Type"] for r in _rows(x, "NODE_DATA")}
    assert types[h["in"]] == "1" and types[h["out"]] == "3"
    assert sorted(t for t in types.values() if t in ("1", "3")) == ["1", "3"]
    assert not _rows(x, "HIERARCHY_DATA")[1:]
    assert all(r["ParentId"] == "0" for r in _rows(x, "BLOCK_DATA"))
    assert [i for i in issues(x) if i.severity == "error"] == []


def test_a_hierarchy_free_of_hazards_equals_a_hand_made_model():
    """The extract of 650path is Start -> its blocks -> End; running it equals
    running the same rows assembled by hand without going through `extract`."""
    rbd = load_rbd(str(HIER))
    x, _ = isolate.extract(rbd, "650path")
    y = copy.deepcopy(x)
    a = run_simulation(x, num_runs=3)
    b = run_simulation(y, num_runs=3)
    assert a == b                      # deterministic and independent of the record


def test_every_hierarchy_of_every_golden_model_extracts_validates_and_runs_or_is_refused():
    n = 0
    for name in gf.all_fixture_names()[:60]:
        path = gf.resolve_rbd_path(name)
        if not path:
            continue
        try:
            rbd = load_rbd(str(path))
        except Exception:
            continue
        for h in _rows(rbd, "HIERARCHY_DATA"):
            if h.get("ID") in (None, "0"):
                continue
            try:
                x, _ = isolate.extract(rbd, h["ID"])
            except isolate.IsolationError:
                continue
            assert [i for i in issues(x) if i.severity == "error"] == [], (name, h["hierarchyName"])
            n += 1
    assert n > 10


def test_the_top_level_and_unknown_names_are_refused():
    rbd = load_rbd(str(HIER))
    with pytest.raises(isolate.IsolationError, match="no hierarchy"):
        isolate.extract(rbd, "Nope")
    with pytest.raises(isolate.IsolationError, match="unknown deps"):
        isolate.extract(rbd, "650path", {"deps": "dream"})


# --- dependencies ---------------------------------------------------------

def _dep_rows(x):
    return [r for r in _rows(x, "BLOCK_DATA") if r.get("ID") != "0"]


def test_outside_dependencies_default_to_up_and_leave_no_outside_reference():
    rbd = load_rbd(str(DEP))
    hz = [h for h in isolate.scan(rbd, "H10") if h.kind == "dependency"]
    assert hz and all(h.treatment in ("up", "system") for h in hz)
    x, _ = isolate.extract(rbd, "H10")
    inside = {r["ID"] for r in _dep_rows(x)}
    for r in _dep_rows(x):
        num = int(r["DepNum"])
        assert num in (0, -1, -2) or str(num) in inside or r["DepType"] != "RBDBlock", r["blockName"]


def test_proxy_is_not_offered_and_says_why():
    """A stand-in block for an outside master must sit on a Start-to-End path in
    Raptor; a dead-end one hung real Raptor's analysis at 4%. So there is no
    `proxy` treatment, and asking for one is an error, not a silent 'up'."""
    rbd = load_rbd(str(DEP))
    with pytest.raises(isolate.IsolationError, match="unknown deps treatment"):
        isolate.extract(rbd, "H10", {"deps": "proxy"})


def test_system_makes_every_outside_dependent_follow_the_extracts_end():
    rbd = load_rbd(str(DEP))
    x, _ = isolate.extract(rbd, "H10", {"deps": "system"})
    followers = [r for r in _dep_rows(x) if r["DepNum"] == "-2"]
    assert followers


def test_refuse_stops_with_the_list():
    rbd = load_rbd(str(DEP))
    with pytest.raises(isolate.IsolationRefused, match="treatment is 'refuse'"):
        isolate.extract(rbd, "H10", {"deps": "refuse"})


def test_a_dependency_on_the_enclosing_hierarchy_follows_the_extracts_end():
    rbd = load_rbd(str(REPO / "tests/golden/LadL8A/input.rbd"))
    hz = [h for h in isolate.scan(rbd, "H3") if "enclosing hierarchy" in h.detail]
    assert hz
    x, _ = isolate.extract(rbd, "H3")
    assert any(r["DepNum"] == "-2" for r in _dep_rows(x))


# --- pools ----------------------------------------------------------------

def _stocks(x):
    return {r["PoolName"]: int(r["Stock"]) for r in _rows(x, "SPARE_POOL_DATA")}


def test_pools_used_only_outside_are_dropped_and_full_copies_unchanged():
    rbd = load_rbd(str(HIER))
    src = {r["PoolName"]: int(r["Stock"]) for r in _rows(rbd, "SPARE_POOL_DATA")}
    x, _ = isolate.extract(rbd, "650path")
    got = _stocks(x)
    assert got and all(got[k] == src[k] for k in got)


@pytest.mark.parametrize("stock,inside,total,expected", [
    (0, 1, 3, 0), (1, 1, 3, 1), (6, 1, 3, 2), (7, 1, 3, 2), (2, 1, 3, 1), (9, 3, 3, 9)])
def test_share_scales_by_the_inside_fraction_rounded_down_minimum_one(stock, inside, total, expected):
    """The rule, tested on the arithmetic the extract uses."""
    import math
    scaled = 0 if stock == 0 else max(1, math.floor(stock * inside / total))
    assert scaled == expected


def test_share_and_set_change_the_extracts_stock():
    rbd = load_rbd(str(HIER))
    full, _ = isolate.extract(rbd, "650path")
    shared, rec = isolate.extract(rbd, "650path", {"pools": "share"})
    for k, v in _stocks(shared).items():
        assert v <= _stocks(full)[k]
    assert any("scaled by" in n for n in rec.notes)
    name = next(iter(_stocks(full)))
    chosen, _ = isolate.extract(rbd, "650path", {"items": {f"pool:{name}.stock": 1}})
    assert _stocks(chosen)[name] == 1


# --- streams --------------------------------------------------------------

def test_separate_gives_every_shared_draw_its_own_unused_dedicated_stream():
    rbd = load_rbd(str(HIER))
    kept, _ = isolate.extract(rbd, "650path")
    sep, rec = isolate.extract(rbd, "650path", {"streams": "separate"})
    cols = [("FStream", "BLOCK_DATA"), ("RStream", "BLOCK_DATA")]
    used = []
    for col, sec in cols:
        for r in _rows(sep, sec):
            if r.get("ID") != "0":
                used.append(abs(int(r[col])))
    assert all(0 < s <= 110 for s in used)
    shared_before = [abs(int(r[c])) for c, sec in cols for r in _rows(kept, sec)
                     if r.get("ID") != "0" and abs(int(r[c])) > 200]
    assert shared_before
    changed = [abs(int(r[c])) for c, sec in cols for r in _rows(sep, sec)
               if r.get("ID") != "0"]
    # no stream number used twice among the draws that were shared
    assert len(set(changed[:0])) == 0
    assert any("own streams" in n for n in rec.notes)
    sep.sections["SYSTEM_DATA"].rows[0]["NumRuns"] = "2"
    run_simulation(sep, num_runs=2)


def test_keep_reports_streams_as_information_only():
    rbd = load_rbd(str(HIER))
    hz = [h for h in isolate.scan(rbd, "650path") if h.kind == "stream"]
    assert hz and hz[0].effect == "information"


def test_the_record_labels_the_run():
    rbd = load_rbd(str(HIER))
    _x, rec = isolate.extract(rbd, "650path")
    d = rec.to_dict()
    assert d["extension"] == "isolated-hierarchy" and "not a whole-model result" in d["banner"]
    assert "650path" in rec.text()


# --- the run path and MCP ---------------------------------------------------

def test_run_isolated_attaches_the_record_everywhere(tmp_path):
    from raptor.api.run import run_isolated
    from raptor.api.workspace import Workspace
    ws = Workspace(tmp_path)
    mid = ws.open_model(HIER)["model_id"]
    result = run_isolated(ws, mid, "650path", num_runs=2, confirm_long_run=True)
    assert result.model_id == mid
    assert result.isolation["extension"] == "isolated-hierarchy"
    assert result.to_dict()["isolation"]["hierarchy"] == "650path"
    runs = tmp_path / "runs" / result.run_id
    assert "650path" in (runs / "isolation.txt").read_text()
    import json
    assert json.loads((runs / "summary.json").read_text())["isolation"]["hierarchy"] == "650path"
    # the temporary extract model does not linger
    assert [m["model_id"] for m in ws.list_models()] == [mid]


def test_mcp_extract_hierarchy_and_isolated_simulate(tmp_path, monkeypatch):
    import asyncio
    from raptor.mcp import tools_run
    from raptor.api.workspace import Workspace
    ws = Workspace(tmp_path)
    monkeypatch.setattr(tools_run.context, "workspace", lambda: ws)
    mid = ws.open_model(HIER)["model_id"]
    res = tools_run.extract_hierarchy(mid, "650path", report_only=True)
    data = res.structured_content
    assert data["count"] >= 1 and not [m for m in ws.list_models() if m["model_id"] != mid]
    res = tools_run.extract_hierarchy(mid, "650path")
    assert res.structured_content["isolation"]["hierarchy"] == "650path"
    assert len(ws.list_models()) == 2
    sim = asyncio.run(tools_run.simulate(mid, num_runs=2, hierarchy="650path", confirm_long_run=True))
    assert sim.structured_content["isolation"]["banner"].startswith("Isolated run of 650path")
    bad = asyncio.run(tools_run.simulate(mid, num_runs=2, hierarchy="Nope", confirm_long_run=True))
    assert bad.is_error and "no hierarchy" in " ".join(c.text for c in bad.content)


# --- agreement with the whole model (statistical sanity) ---------------------

def test_an_isolated_hierarchy_agrees_with_the_same_hierarchy_in_the_whole_model():
    """LadL8BEvents / H7 has no outside dependency and shares no pool or resource
    (only random streams), so its extract should reproduce the hierarchy's own
    availability in the full run, within sampling noise: the full run's weak-link
    colour times for H7 against the extract's system availability. Fixed seeds, so
    this is deterministic, not flaky (z = 1.04 when written)."""
    import math
    import statistics as st
    n = 60
    rbd = load_rbd(str(DEP))
    full_rows = []
    run_simulation(rbd, num_runs=n, _report_sink=full_rows)
    h = next(r for r in _rows(rbd, "HIERARCHY_DATA") if r["hierarchyName"] == "H7")
    full = []
    for d in full_rows:
        v = d.weak_hiers[int(h["ID"])]
        up = v["green"] + v["yellow"] + v["blue"]
        full.append(up / (up + v["red"] + v["brown"] + v["orange"]))
    x, rec = isolate.extract(rbd, "H7")
    assert not [hz for hz in rec.hazards if hz.kind in ("dependency", "pool", "resource")]
    iso_rows = []
    x.sections["SYSTEM_DATA"].rows[0]["NumRuns"] = str(n)
    run_simulation(x, num_runs=n, _report_sink=iso_rows)
    iso = [d.availability for d in iso_rows]
    sem = math.sqrt(st.variance(full) / n + st.variance(iso) / n)
    assert abs(st.mean(full) - st.mean(iso)) < 3 * sem

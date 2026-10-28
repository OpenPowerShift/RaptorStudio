"""`DomainTree.TXT` / `CoreFile.TXT` (Avant-garde F and G) against real captures: the flat
`MotorDemo`, the 40-block hierarchical `Hierarchy1-7`, nested hierarchies, a cold-standby probe
and the dependency/hierarchy probes. The capture's first line carries its own date and its
second its own build (`7.0.11 Student`); everything else is exact."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.engine.domain_tree import domain_tree_lines
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"
NAMES = sorted(p.parent.parent.name for p in GOLD.glob("*/legacy/rpt_domain_tree_*.txt"))


def test_there_are_real_captures():
    assert len(NAMES) >= 7


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("core,stem", [(False, "rpt_domain_tree"), (True, "rpt_core_file")])
def test_file_matches_real(name, core, stem):
    real = (GOLD / name / "legacy" / f"{stem}_{name}.txt").read_bytes().decode("latin-1")
    real = real.replace("\r", "").split("\n")
    ours = domain_tree_lines(load_rbd(str(GOLD / name / "input.rbd")), "input.rbd",
                             "Sat Oct 10 12:05:11 2026", core=core)
    assert ours[0].split(" = ")[0] == real[0].split(" = ")[0]
    assert ours[2:] == real[2:len(ours)]
    assert real[len(ours):] in ([], [""])
    assert real[1].startswith("devVersion= " + ours[1].split("= ")[1])


def test_header_date_has_no_padding():
    lines = domain_tree_lines(load_rbd(str(GOLD / "MotorDemo" / "input.rbd")), "m.rbd",
                              "Sat Oct  3 08:00:00 2026")
    assert lines[0] == "Raptor day month year = 3 Oct 2026"


def test_api_run_writes_the_files_when_the_options_are_on(tmp_path):
    from raptor.api.run import run, section_path
    from raptor.api.workspace import Workspace
    from raptor.core.dev_options import DeveloperOptions

    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLD / "MotorDemo" / "input.rbd")["model_id"]
    off = run(ws, mid, num_runs=1, timeout_s=60, confirm_long_run=True,
              developer=DeveloperOptions())
    assert not section_path(ws.root / "runs" / off.run_id, "domain_tree").exists()
    on = run(ws, mid, num_runs=1, timeout_s=60, confirm_long_run=True,
             developer=DeveloperOptions(domain_tree=True, core_file=True))
    d = ws.root / "runs" / on.run_id
    assert "Domain Tree for" in section_path(d, "domain_tree").read_text()
    assert "Kings and Serfs File for" in section_path(d, "core_file").read_text()


def test_a_long_series_chain_does_not_hit_the_recursion_limit():
    """1,500 nodes in series: the explicit stack copes where recursion would not."""
    from raptor.core.engine.domain_tree import _walk

    class N:  # a minimal stand-in for an RBD node
        type_node = 2
        in_paths_array = [1]
        node_name = "n"
        parent_id = 0

    n = 1500
    model = type("M", (), {"nodes_by_id": {i: N() for i in range(n)}})()
    groups = {i: [("nd", i + 1)] for i in range(n - 1)}
    out: list[str] = []
    _walk(model, groups, 0, "   ", out, core=False)
    assert len(out) == n - 1 and out[-1].startswith("   " * (n - 1))
    out.clear()
    _walk(model, groups, 0, "   ", out, core=True)   # one connection in each: no nesting
    assert out == []

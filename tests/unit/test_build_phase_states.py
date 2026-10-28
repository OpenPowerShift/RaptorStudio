"""`build_from_spec`'s `phases` + `phase_states`: a phased model built from scratch (no cloned fixture).

The writer must emit the `P NNN` / `P00N` header columns after the ordinary ones (a spec-built section has no
header list of its own), set the per-element `Phasing`/`Phases` flag, and the file must parse and run.
"""
from __future__ import annotations

import pytest

from raptor.api.build import BuildError, build_from_spec
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import parse_rbd, render_rbd

PHASES = [
    {"name": "Climb", "duration_dist": 8, "params": (100.0, 5.0, 0.0), "mission": 1},
    {"name": "Cruise", "duration_dist": 8, "params": (200.0, 10.0, 0.0), "mission": 1},
    {"name": "Land", "duration_dist": 8, "params": (50.0, 2.0, 0.0), "mission": 0},
]


def _spec(**extra):
    spec = {
        "name": "PhasedScratch",
        "blocks": [
            {"name": "A", "fail_dist": 4, "fail_params": [300.0], "repair_dist": 4, "repair_params": [10.0]},
            {"name": "B", "fail_dist": 4, "fail_params": [300.0], "repair_dist": 4, "repair_params": [10.0]},
            {"name": "C", "fail_dist": 4, "fail_params": [300.0], "repair_dist": 4, "repair_params": [10.0]},
        ],
        "nodes": [
            {"name": "start", "type": 1, "n": 0, "out": 3},
            {"name": "J", "type": 2, "n": 3, "k": 2, "out": 1},
            {"name": "end", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "start", "to": "A"}, {"from": "start", "to": "B"}, {"from": "start", "to": "C"},
                  {"from": "A", "to": "J"}, {"from": "B", "to": "J"}, {"from": "C", "to": "J"},
                  {"from": "J", "to": "end"}],
        "system": {"num_runs": 3, "term_condition": 2.0, "termination_type": "CycleSim"},
        "phases": PHASES,
        "phase_states": {"A": ["A: 1.000", "A: 2.000", "C: 1.000"], "B": ["L: 1.000", "A: 0.500", "A: 1.000"],
                         "J": ["1", "C", "L"]},
    }
    spec.update(extra)
    return spec


def test_headers_flags_and_ragged_rows():
    rbd = build_from_spec(_spec())
    blocks = rbd.section("BLOCK_DATA")
    assert blocks.columns[-3:] == ["P 001", "P 002", "P 003"]
    assert rbd.section("NODE_DATA").columns[-3:] == ["P001", "P002", "P003"]
    by_name = {r["blockName"]: r for r in blocks.rows}
    assert by_name["A"]["Phasing"] == "1" and by_name["B"]["Phasing"] == "1"
    assert by_name["C"]["Phasing"] == "0" and "P 001" not in by_name["C"]  # unphased: the column is simply absent
    assert rbd.counts["Phases"] == 3


def test_round_trip_and_run():
    rbd = build_from_spec(_spec())
    rbd.file_name = "PhasedScratch.rbd"
    again = parse_rbd(render_rbd(rbd))
    assert [r["PhaseName"] for r in again.section("PHASES").rows] == ["Climb", "Cruise", "Land"]
    runs = run_simulation(again, num_runs=2)
    assert any("Begin_phase_Cruise" in line for line in runs[0])
    assert any("Phase_Change" in line for line in runs[0])  # a state change driven by the phase table


def test_phase_states_needs_phases():
    spec = _spec()
    del spec["phases"]
    with pytest.raises(BuildError):
        build_from_spec(spec)


@pytest.mark.parametrize("bad", [["A: 1.000"], ["A: 1.000", "A: 1.000", None], ["A: 1.0", "Cut", "A: 1.0"]])
def test_phase_states_validated(bad):
    spec = _spec()
    spec["phase_states"] = {"A": bad}
    with pytest.raises(BuildError):
        build_from_spec(spec)

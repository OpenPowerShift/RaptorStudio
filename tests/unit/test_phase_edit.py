"""Insert / delete / clear phases against real Raptor's own saves (`tests/data/phase_editing`)."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.fileio.phase_edit import (PhaseEditError, clear_phases, delete_phase, insert_phase,
                                           phase_problems)
from raptor.core.fileio.rbd import load_rbd, save_rbd

DATA = Path(__file__).resolve().parents[1] / "data" / "phase_editing"
SECTIONS = ("PHASES", "BLOCK_DATA", "EVENT_DATA", "NODE_DATA", "HIERARCHY_DATA")


def _same(mine, real):
    assert mine.counts["Phases"] == real.counts["Phases"]
    for name in SECTIONS:
        a, b = mine.sections[name], real.sections[name]
        assert a.columns == b.columns, name
        assert a.rows == b.rows, name


def _insert_like(real_name: str, position: int):
    real = load_rbd(str(DATA / real_name))
    row = real.sections["PHASES"].rows[position - 1]
    params = tuple(float(row[f"Param{i}"]) for i in (1, 2, 3))
    rbd = load_rbd(str(DATA / "input.rbd"))
    insert_phase(rbd, position, name=row["PhaseName"], dist=int(row["Dist"]), params=params,
                 mission=row["Mission"] == "1")
    return rbd, real


@pytest.mark.parametrize("real_name,position", [("real_ins3.rbd", 3), ("real_ins1.rbd", 1), ("real_add.rbd", 6)])
def test_insert_equals_what_real_writes(real_name, position):
    mine, real = _insert_like(real_name, position)
    _same(mine, real)
    assert phase_problems(mine) == []


def test_delete_equals_what_real_writes():
    rbd = load_rbd(str(DATA / "input.rbd"))
    delete_phase(rbd, 2)
    _same(rbd, load_rbd(str(DATA / "real_del2.rbd")))


def test_clear_equals_what_real_writes_flags_stay():
    rbd = load_rbd(str(DATA / "input.rbd"))
    clear_phases(rbd)
    real = load_rbd(str(DATA / "real_clear.rbd"))
    _same(rbd, real)
    flagged = [r for r in rbd.sections["BLOCK_DATA"].rows if r.get("Phasing") == "1"]
    assert flagged and not any(c.startswith("P ") for c in rbd.sections["BLOCK_DATA"].columns)


def test_insert_then_delete_returns_the_original():
    rbd = load_rbd(str(DATA / "input.rbd"))
    before = save_text(rbd)
    for pos in (1, 3, 6):
        insert_phase(rbd, pos, name="Tmp")
        delete_phase(rbd, pos)
        assert save_text(rbd) == before


def save_text(rbd):
    from raptor.core.fileio.rbd import render_rbd
    return render_rbd(rbd)


def test_the_written_file_loads_back_with_the_same_phases(tmp_path):
    rbd, real = _insert_like("real_ins3.rbd", 3)
    out = tmp_path / "x.rbd"
    save_rbd(rbd, str(out))
    _same(load_rbd(str(out)), real)


def test_bad_arguments_change_nothing():
    rbd = load_rbd(str(DATA / "input.rbd"))
    before = save_text(rbd)
    for bad in (lambda: insert_phase(rbd, 0, name="A"), lambda: insert_phase(rbd, 7, name="A"),
                lambda: insert_phase(rbd, 2, name="has space"), lambda: insert_phase(rbd, 2, name=""),
                lambda: insert_phase(rbd, 2, name="x" * 21), lambda: delete_phase(rbd, 6), lambda: delete_phase(rbd, 0)):
        with pytest.raises(PhaseEditError):
            bad()
    assert save_text(rbd) == before


def test_an_inconsistent_file_is_refused_rather_than_made_worse():
    rbd = load_rbd(str(DATA / "input.rbd"))
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows if r.get("Phasing") == "1")
    del row["P 005"]
    assert phase_problems(rbd)
    with pytest.raises(PhaseEditError):
        insert_phase(rbd, 2, name="A")


def test_the_first_phase_of_a_cleared_model_gets_every_default():
    rbd = load_rbd(str(DATA / "input.rbd"))
    clear_phases(rbd)
    insert_phase(rbd, 1, name="First", dist=19, params=(10.0, 0.0, 0.0))
    nodes = {r["Name"]: r["P001"] for r in rbd.sections["NODE_DATA"].rows if r.get("Phases") == "1"}
    assert nodes == {"out": "1", "Power": "2"}
    assert {r["P 001"] for r in rbd.sections["EVENT_DATA"].rows if r.get("Phasing") == "1"} == {"P: 0.000"}
    assert {r["P 001"] for r in rbd.sections["BLOCK_DATA"].rows if r.get("Phasing") == "1"} == {"A: 1.000"}
    assert phase_problems(rbd) == []


@pytest.mark.parametrize("name", ["Phasing1", "Phasing3", "PhasingDemo", "PhaseBarrierProbe", "FevPhasing3"])
def test_every_insert_position_keeps_a_consistent_file(name):
    golden = Path(__file__).resolve().parents[1] / "golden" / name / "input.rbd"
    base = load_rbd(str(golden))
    n = len(base.sections["PHASES"].rows)
    for pos in range(1, n + 2):
        rbd = load_rbd(str(golden))
        insert_phase(rbd, pos, name="Tmp")
        assert phase_problems(rbd) == [], (name, pos)
        delete_phase(rbd, pos)
        assert phase_problems(rbd) == []


@pytest.mark.parametrize("name", ["input", "real_ins3", "real_ins1", "real_add", "real_del2", "real_clear"])
def test_the_engine_runs_every_real_save(name):
    from raptor.core.engine.runner import run_simulation_with_reports
    rbd = load_rbd(str(DATA / f"{name}.rbd"))
    row = rbd.section("SYSTEM_DATA").rows[0]
    row["NumRuns"] = "2"
    if name == "real_clear":      # a cycle-terminated model with no phases is refused, as real refuses it
        row["TerminationType"], row["TermCondition"] = "TimeSim", "100"
    logs, _eor, fin = run_simulation_with_reports(rbd, 2, date="D")
    assert fin and logs


def test_configure_phases_refuses_to_leave_stale_tokens(tmp_path):
    from raptor.api.build import BuildError, configure_phases
    from raptor.api.workspace import Workspace
    ws = Workspace(tmp_path)
    info = ws.register_model(load_rbd(str(DATA / "input.rbd")), name="p")
    before = len(ws.get(info["model_id"]).rbd.sections["PHASES"].rows)
    with pytest.raises(BuildError, match="phased element"):
        configure_phases(ws, info["model_id"], [{"name": "A"}, {"name": "B"}])
    assert len(ws.get(info["model_id"]).rbd.sections["PHASES"].rows) == before


def test_a_phased_rbds_model_keeps_its_phase_columns_when_written_as_rbd(tmp_path):
    """`.rbds` rows carry the phase tokens as cells, but the template header has no `P` columns: rendering the
    model as `.rbd` used to write `Phases=4` with no tokens (a file that crashes Raptor 7 on open) and the header
    counts were left at the template's. Reading now makes both agree with the rows."""
    from pathlib import Path

    from raptor.core.fileio.phase_edit import phase_problems
    from raptor.core.fileio.rbd import load_rbd, render_rbd
    from raptor.core.fileio.rbds import load_rbds
    root = Path(__file__).resolve().parents[2]
    model = load_rbds(str(root / "new_docs" / "models" / "Flight1.rbds"))
    assert phase_problems(model) == []
    assert model.counts["Phases"] == 4 and model.counts["Blocks"] == 6
    path = tmp_path / "Flight1.rbd"
    path.write_text(render_rbd(model))
    again = load_rbd(str(path))
    assert phase_problems(again) == []
    before = {r["blockName"]: [r[f"P {i:03d}"] for i in range(1, 5)]
              for r in model.sections["BLOCK_DATA"].rows if r.get("Phasing") == "1"}
    after = {r["blockName"]: [r[f"P {i:03d}"] for i in range(1, 5)]
             for r in again.sections["BLOCK_DATA"].rows if r.get("Phasing") == "1"}
    assert before and before == after

"""Models the engine refuses to run (`raptor.core.engine.model_checks`).

* a cold-standby path that depends on another path into the same cold node --
  real Raptor 7.0.11 dies with an Access Violation on `CS_DepOnColdBlock`,
  while `CS_DepOnStandbyPeer` (the dependent is NOT itself a path) runs fine;
* a dependency CYCLE is NOT refused: real runs them (the `DepCycle*Probe` and
  `DepSelfProbe` captures, matched exactly by the golden fidelity sweeps). It
  earns a validation warning.

Every fixture in the corpus must still load, apart from the one real cannot run.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.validate import issues
from raptor.core.engine.errors import UnrunnableModel
from raptor.core.engine.load import load_model
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.builder import set_fields, sync_counts
from raptor.core.fileio.rbd import RbdFile, load_rbd

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "golden"
FIXTURES = REPO / "tests" / "fixtures"
REFUSED = {"CS_DepOnColdBlock"}


def _rbd(path: Path) -> RbdFile:
    return load_rbd(str(path / "input.rbd"))


def _block_row(rbd: RbdFile, block_id: int) -> dict:
    return next(row for row in rbd.sections["BLOCK_DATA"].rows if row["ID"] == str(block_id))


def test_cold_path_that_depends_on_another_path_is_refused() -> None:
    with pytest.raises(UnrunnableModel) as excinfo:
        load_model(_rbd(FIXTURES / "CS_DepOnColdBlock"))
    [problem] = excinfo.value.problems
    assert "'DepBlk'" in problem.element
    assert "'Comm'" in problem.message and "'LowGain'" in problem.message
    assert "Access Violation" in problem.message


def test_the_engine_refuses_to_run_it() -> None:
    with pytest.raises(UnrunnableModel):
        run_simulation(_rbd(FIXTURES / "CS_DepOnColdBlock"), num_runs=1)


def test_a_dependent_that_is_not_itself_a_cold_path_still_runs() -> None:
    """`CS_DepOnStandbyPeer` depends on the same demoted candidate and real runs it."""
    assert load_model(_rbd(FIXTURES / "CS_DepOnStandbyPeer")) is not None


def test_unrunnable_model_is_still_a_not_implemented_error() -> None:
    """Callers that treat documented refusals as `NotImplementedError` keep working."""
    assert issubclass(UnrunnableModel, NotImplementedError)


def _two_block_cycle() -> RbdFile:
    """Ch07Dependent: Armature (2) already depends on PowerSupply (5); make
    PowerSupply depend on Armature."""
    rbd = _rbd(GOLDEN / "Ch07Dependent")
    set_fields(_block_row(rbd, 5), DepType="RBDBlock", DepNum=2)
    sync_counts(rbd)
    return rbd


def test_a_dependency_cycle_is_not_refused() -> None:
    """Real Raptor runs cycles, so the port must too."""
    assert load_model(_two_block_cycle()) is not None


def test_a_cycle_runs_to_completion() -> None:
    assert run_simulation(_two_block_cycle(), num_runs=1)


def test_a_block_that_depends_on_itself_is_not_refused() -> None:
    rbd = _rbd(GOLDEN / "Ch07Dependent")
    set_fields(_block_row(rbd, 2), DepType="RBDBlock", DepNum=2)
    sync_counts(rbd)
    assert load_model(rbd) is not None


def test_the_captured_cycle_fixtures_load() -> None:
    for name in ("DepCycleIdleProbe", "DepCycleFailProbe", "DepCycleTieProbe", "DepCycle3Probe", "DepSelfProbe"):
        assert load_model(_rbd(GOLDEN / name)) is not None, name


def test_the_same_dependencies_without_the_loop_are_fine() -> None:
    assert load_model(_rbd(GOLDEN / "Ch07Dependent")) is not None


def test_validate_reports_the_cold_shape_as_an_error() -> None:
    found = [i for i in issues(_rbd(FIXTURES / "CS_DepOnColdBlock")) if "Access Violation" in i.message]
    assert len(found) == 1 and found[0].severity == "error"


def test_validate_warns_about_a_cycle_without_an_error() -> None:
    found = [i for i in issues(_two_block_cycle()) if "circular dependency" in i.message]
    assert len(found) == 1 and found[0].severity == "warning"
    assert not [i for i in issues(_two_block_cycle()) if i.severity == "error" and "circular" in i.message]


def test_validate_is_quiet_for_a_runnable_model() -> None:
    assert not [i for i in issues(_rbd(FIXTURES / "CS_DepOnStandbyPeer"))
                if "Access Violation" in i.message or "circular" in i.message]


def _cycle_sim_without_phases() -> RbdFile:
    rbd = _rbd(GOLDEN / "Ch07Dependent")
    set_fields(rbd.sections["SYSTEM_DATA"].rows[0], TerminationType="CycleSim")
    return rbd


def _capacity_without_end_node() -> RbdFile:
    rbd = _rbd(GOLDEN / "Capacity1")
    for row in rbd.sections["NODE_DATA"].rows:
        if row["Type"] == "3":
            set_fields(row, Type="0")
    return rbd


@pytest.mark.parametrize("build, words", [
    (_cycle_sim_without_phases, "no phases are defined"),
    (_capacity_without_end_node, "no End node"),
])
def test_bad_settings_are_a_refusal_and_a_validation_error(build, words) -> None:
    with pytest.raises(UnrunnableModel, match=words):
        load_model(build())
    found = [i for i in issues(build()) if words in i.message]
    assert len(found) == 1 and found[0].severity == "error"


def test_an_unwired_hierarchy_output_is_a_validation_error() -> None:
    """Phasing1: event 32 feeds hierarchy 23, whose only output link (21) is removed."""
    rbd = _rbd(GOLDEN / "Phasing1")
    links = rbd.sections["LINK_DATA"]
    set_fields(next(r for r in links.rows if r["ID"] == "32"), ToType="Hier", ToID="23")
    links.rows = [r for r in links.rows if r["ID"] != "21"]
    sync_counts(rbd)
    with pytest.raises(UnrunnableModel, match="Wire the hierarchy's output"):
        load_model(rbd)
    found = [i for i in issues(rbd) if "Wire the hierarchy's output" in i.message]
    assert len(found) == 1 and found[0].severity == "error"


def _corpus() -> list[Path]:
    return sorted(p.parent for base in (GOLDEN, FIXTURES) for p in base.rglob("input.rbd"))


@pytest.mark.parametrize("fixture", _corpus(), ids=lambda p: p.name)
def test_no_other_fixture_is_refused(fixture: Path) -> None:
    """A refusal rule that catches a model real Raptor runs would be a regression."""
    try:
        load_model(load_rbd(str(fixture / "input.rbd")))
    except UnrunnableModel:
        assert fixture.name in REFUSED, f"{fixture.name} is refused but real Raptor runs it"
    except Exception:  # noqa: BLE001 - other load failures are pinned elsewhere
        pass
    else:
        assert fixture.name not in REFUSED

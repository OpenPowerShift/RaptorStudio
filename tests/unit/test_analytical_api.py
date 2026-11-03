"""API and CLI surface for the analytical solver."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.api.analytical import SCHEMA, analyse
from raptor.api.workspace import Workspace
from raptor.cli import exitcodes as X
from raptor.cli.main import main
from raptor.core.analytical.errors import BddTooLarge, NotApplicable

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "new_docs" / "models"


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


def _open(workspace, name):
    return workspace.open_model(MODELS / f"{name}.rbds")["model_id"]


def test_api_result_shape_and_json_safety(workspace):
    model_id = _open(workspace, "Lantern1")
    result = analyse(workspace, model_id, times=[0.0, 10.0, 48.0],
                     equation=True)
    assert result["schema"] == SCHEMA
    assert result["mode"] == "reliability"
    assert result["applicable"] is True
    assert result["reliability"] == pytest.approx(0.38460369, rel=1e-6)
    assert result["mttf"] == pytest.approx(43.1426, rel=1e-4)
    assert result["bx"]["10"] == pytest.approx(9.153885, rel=1e-5)
    assert len(result["times"]) == 3
    assert result["components"]
    assert result["importance"]
    assert result["equation"].startswith("(R_")
    # No NaN or Infinity anywhere.
    json.dumps(result, allow_nan=False)


def test_api_availability_mode(workspace):
    model_id = _open(workspace, "Vendo1")
    result = analyse(workspace, model_id, mode="availability")
    assert result["availability"] == pytest.approx(0.9706853, abs=1e-6)
    assert result["unavailability"] == pytest.approx(0.0293147, abs=1e-6)
    assert {row["name"] for row in result["importance"]} == {
        "Compressor", "Keypad"}


def test_api_refuses_phased_model(workspace):
    model_id = _open(workspace, "AirlinerMission")
    with pytest.raises(NotApplicable, match="has phases"):
        analyse(workspace, model_id)


def test_cli_text_output(capsys):
    code = main(["analytic", str(MODELS / "Lantern1.rbds"), "--at", "48"])
    captured = capsys.readouterr()
    assert code == X.OK
    assert "reliability:" in captured.out
    assert "MTTF:" in captured.out
    assert "Importance:" in captured.out
    assert "B10=" in captured.out


def test_cli_json_output(capsys):
    code = main(["analytic", str(MODELS / "Vendo1.rbds"),
                 "--mode", "availability", "-f", "json"])
    captured = capsys.readouterr()
    assert code == X.OK
    payload = json.loads(captured.out)
    assert payload["schema"] == SCHEMA
    assert payload["mode"] == "availability"
    assert payload["availability"] == pytest.approx(0.9706853, abs=1e-6)


def test_cli_refusal_exit_code(capsys):
    code = main(["analytic", str(MODELS / "AirlinerMission.rbds")])
    captured = capsys.readouterr()
    assert code == X.INVALID
    assert "has phases" in captured.err


def test_cli_ignore_repairs(capsys):
    code = main(["analytic", str(MODELS / "CarCare0.rbds"),
                 "--ignore-repairs", "-f", "json"])
    captured = capsys.readouterr()
    assert code == X.OK
    payload = json.loads(captured.out)
    assert payload["assumptions"]


def test_cli_dependency_choice(capsys):
    code = main(["analytic", str(MODELS / "EspressoDep1.rbds"),
                 "--at", "100", "--ignore", "repairs",
                 "--dependencies", "down", "-f", "json"])
    captured = capsys.readouterr()
    assert code == X.OK, captured.err
    payload = json.loads(captured.out)
    assert payload["scope"]["dependencies"] == "down"
    assert any("dependencies are treated as down" in note
               for note in payload["assumptions"])


def test_cli_text_shows_the_dependency_treatment(capsys):
    code = main(["analytic", str(MODELS / "EspressoDep1.rbds"),
                 "--at", "100", "--ignore", "repairs",
                 "--dependencies", "up"])
    captured = capsys.readouterr()
    assert code == X.OK, captured.err
    assert "Dependencies treated as: up" in captured.out


def test_cli_refuses_a_dependency_without_a_choice(capsys):
    code = main(["analytic", str(MODELS / "EspressoDep1.rbds"),
                 "--at", "100", "--ignore", "repairs"])
    captured = capsys.readouterr()
    assert code == X.INVALID
    assert "Choose whether dependencies" in captured.err


def test_api_rejects_an_unknown_dependency_choice(workspace):
    model_id = _open(workspace, "EspressoDep1")
    with pytest.raises(ValueError, match="dependencies must be one of"):
        analyse(workspace, model_id, at=100.0, dependencies="sideways")


def test_api_rejects_non_finite_arguments(workspace):
    model_id = _open(workspace, "Lantern1")
    for kwargs in ({"at": float("nan")}, {"at": float("inf")},
                   {"times": [float("inf")]}, {"tau": float("nan")},
                   {"bx": [float("inf")]}):
        with pytest.raises(ValueError, match="finite"):
            analyse(workspace, model_id, **kwargs)


def test_availability_time_is_not_echoed(workspace):
    model_id = _open(workspace, "Vendo1")
    result = analyse(workspace, model_id, mode="availability")
    assert result["time"] is None


def test_api_caps_curve_and_bx_lists(workspace):
    model_id = _open(workspace, "Lantern1")
    with pytest.raises(ValueError, match="250"):
        analyse(workspace, model_id, times=[1.0] * 10_001)
    with pytest.raises(ValueError, match="20 BX"):
        analyse(workspace, model_id, bx=[10.0] * 101)


def test_api_rejects_out_of_range_bx(workspace):
    model_id = _open(workspace, "Lantern1")
    with pytest.raises(ValueError, match=r"\[0, 100\]"):
        analyse(workspace, model_id, bx=[101.0])


def test_progress_reports_build_and_importance(workspace):
    model_id = _open(workspace, "Lantern1")
    events = []
    result = analyse(workspace, model_id,
                     progress=lambda stage, done, total: events.append(
                         (stage, done, total)))
    assert result["importance"]
    stages = {stage for stage, _, _ in events}
    assert "Building BDD" in stages or any(
        stage.startswith("Building BDD") for stage in stages)
    assert "Evaluating results" in stages
    assert "Computing importance" in stages
    assert any(stage.startswith("Building BDD") and total
               for stage, _, total in events)


def test_the_pass_total_leaves_room_for_the_stages_to_come(workspace):
    """BX once used seven times its share of the plan, so the count reached
    "pass N of about N" before importance began and then sat there."""
    model_id = _open(workspace, "Lantern1")
    events = []
    analyse(workspace, model_id, bx=[10.0, 50.0],
            progress=lambda stage, done, total: events.append(
                (stage, done, total)))
    entry = next(event for event in events
                 if event[0] == "Computing importance")
    assert entry[1] < entry[2]
    assert "Working out BX life" in {stage for stage, _, _ in events}


def test_availability_does_not_announce_a_bx_stage(workspace):
    model_id = _open(workspace, "Lantern1")
    events = []
    analyse(workspace, model_id, mode="availability",
            progress=lambda stage, done, total: events.append(stage))
    assert not any("BX" in stage for stage in events)


def test_progress_is_one_figure_that_never_goes_back(workspace):
    model_id = _open(workspace, "Lantern1")
    figures = []
    analyse(workspace, model_id,
            progress=lambda stage, done, total: figures.append((done, total))
            if total else None)
    assert {total for _, total in figures} == {1000}
    done = [d for d, _ in figures]
    assert done == sorted(done) and done[-1] == 1000


def test_both_modes_share_settings_and_one_progress_figure(workspace,
                                                           tmp_path):
    import zipfile

    from raptor.api.analytical import analyse_modes
    from raptor.api.analytical_export import export_file_name, export_xlsx
    model_id = _open(workspace, "Lantern1")
    figures = []
    results = analyse_modes(
        workspace, model_id, ["reliability", "availability"],
        bx=[10.0], tau=5.0, ignore=["repairs"],
        progress=lambda stage, done, total: figures.append((stage, done))
        if total else None)
    assert [r["mode"] for r in results] == ["reliability", "availability"]
    assert "conditional" in results[0] and "conditional" not in results[1]
    done = [d for _, d in figures]
    assert done == sorted(done)
    assert any(s.startswith("Availability: ") for s, _ in figures)
    name = export_file_name("Lantern1", results)
    assert name == "Lantern1_analytical_reliability_availability.xlsx"
    path = export_xlsx(results, tmp_path / name)
    sheets = zipfile.ZipFile(path).read("xl/workbook.xml").decode()
    assert "Reliability Elements" in sheets and "Availability Elements" in sheets
    assert export_file_name("Lantern1", results[:1]) == \
        "Lantern1_analytical_reliability.xlsx"


def test_importance_false_still_returns_the_key(workspace):
    model_id = _open(workspace, "Lantern1")
    result = analyse(workspace, model_id, importance=False)
    assert result["importance"] == []


def test_solver_limits_can_be_lowered_or_raised(workspace):
    model_id = _open(workspace, "Lantern1")
    with pytest.raises(NotApplicable, match="at most 1"):
        analyse(workspace, model_id, max_variables=1)
    with pytest.raises(BddTooLarge, match="2 nodes"):
        analyse(workspace, model_id, max_bdd_nodes=2)


def test_cli_progress_json(capsys):
    code = main(["analytic", str(MODELS / "Lantern1.rbds"),
                 "--progress", "json"])
    captured = capsys.readouterr()
    assert code == X.OK
    progress = [line for line in captured.err.splitlines()
                if '"event": "progress"' in line]
    assert progress
    assert '"stage": "Building BDD' in progress[0] or any(
        '"stage": "Building BDD' in line for line in progress)


def test_cli_both_modes_json_and_a_workbook(capsys, tmp_path):
    import zipfile
    code = main(["analytic", str(MODELS / "Lantern1.rbds"), "--mode", "both",
                 "--tau", "5", "--xlsx", str(tmp_path), "-f", "json",
                 "--progress", "json"])
    captured = capsys.readouterr()
    assert code == X.OK
    results = json.loads(captured.out)["results"]
    assert [r["mode"] for r in results] == ["reliability", "availability"]
    path = tmp_path / "Lantern1_analytical_reliability_availability.xlsx"
    assert path.exists() and f"wrote {path}" in captured.err
    sheets = zipfile.ZipFile(path).read("xl/workbook.xml").decode()
    assert "Availability Elements" in sheets
    events = [json.loads(line) for line in captured.err.splitlines()
              if '"event": "progress"' in line]
    figures = [e["done"] for e in events if e["total"]]
    assert figures == sorted(figures) and figures[-1] == 1000
    assert any(e["stage"].startswith("Availability: ") for e in events)


def test_cli_both_modes_csv_has_a_mode_column(capsys):
    code = main(["analytic", str(MODELS / "Lantern1.rbds"), "--mode", "both",
                 "-f", "csv", "--progress", "none"])
    lines = capsys.readouterr().out.splitlines()
    assert code == X.OK
    assert lines[0] == "mode,section,name,value"
    assert lines.count("mode,section,name,value") == 1
    assert any(line.startswith("availability,summary,availability")
               for line in lines)


def test_cli_xlsx_to_a_file_name(capsys, tmp_path):
    target = tmp_path / "mine.xlsx"
    code = main(["analytic", str(MODELS / "Lantern1.rbds"),
                 "--xlsx", str(target), "--progress", "none"])
    capsys.readouterr()
    assert code == X.OK and target.exists()


def test_cli_phase_all_gives_one_result_per_phase(capsys, tmp_path):
    import zipfile
    code = main(["analytic", str(MODELS / "AirlinerMission.rbds"),
                 "--phase", "all", "--ignore", "repairs,pm,spares,resources",
                 "--xlsx", str(tmp_path), "-f", "csv", "--progress", "none"])
    captured = capsys.readouterr()
    assert code == X.OK
    lines = captured.out.splitlines()
    assert lines[0] == "phase,section,name,value"
    phases = {line.split(",")[0] for line in lines[1:]
              if ",summary,reliability," in line}
    assert phases == {"1", "2", "3", "4", "5"}
    path = tmp_path / "AirlinerMission_analytical_reliability_all_phases.xlsx"
    sheets = zipfile.ZipFile(path).read("xl/workbook.xml").decode()
    assert "Phase 1 Summary" in sheets and "Phase 5 Summary" in sheets


def test_phase_all_needs_a_phased_model(workspace):
    from raptor.api.analytical import analyse_modes
    model_id = _open(workspace, "Lantern1")
    with pytest.raises(ValueError, match="phased model"):
        analyse_modes(workspace, model_id, ["reliability"], phase="all")


def test_cli_rejects_a_phase_that_is_not_a_number(capsys):
    with pytest.raises(SystemExit):
        main(["analytic", str(MODELS / "Lantern1.rbds"), "--phase", "two"])
    assert "number or 'all'" in capsys.readouterr().err


def test_bdd_limit_cannot_exceed_what_the_machine_allows(monkeypatch):
    from raptor.api.analytical import analyse_rbd
    from raptor.core.analytical import limits
    from raptor.core.fileio.rbd import load_rbd
    # 4 GB free: half of it at 350 bytes a node is about 6.1M nodes.
    monkeypatch.setattr(limits, "available_memory", lambda: 4 * 1024 ** 3)
    allowed = limits.max_bdd_nodes_allowed()
    assert 6_000_000 < allowed < 6_200_000
    with pytest.raises(ValueError, match="this machine allows"):
        analyse_rbd(load_rbd("tests/golden/TwoBlockRedundancy/input.rbd"),
                    max_bdd_nodes=allowed + 1)
    result = analyse_rbd(load_rbd("tests/golden/TwoBlockRedundancy/input.rbd"),
                         mode="availability", max_bdd_nodes=allowed)
    assert result["bdd_nodes"] > 0


def test_the_ceiling_never_drops_below_the_default(monkeypatch):
    from raptor.core.analytical import limits
    monkeypatch.setattr(limits, "available_memory", lambda: 100 * 1024 ** 2)
    assert limits.max_bdd_nodes_allowed() == limits.DEFAULT_BDD_NODES
    monkeypatch.setattr(limits, "available_memory", lambda: None)
    assert limits.max_bdd_nodes_allowed() == 8_000_000


def test_a_size_refusal_says_what_was_reached_and_what_to_do():
    from raptor.core.analytical.bdd import BDD
    bdd = BDD(max_nodes=40)
    vs = [bdd.variable(i) for i in range(12)]
    with pytest.raises(BddTooLarge) as caught:
        bdd.threshold([bdd.Or(vs[i], vs[(i + 5) % 12]) for i in range(12)], 6)
    text = str(caught.value)
    assert "limit of 40 nodes" in text and "allows a limit of up to" in text
    assert "one hierarchy at a time" in text
    assert caught.value.limit == 40 and caught.value.allowed >= 2_000_000


def test_one_hierarchy_can_be_solved_as_a_standalone_model():
    from raptor.api.analytical import analyse_rbd, scope_options
    from raptor.core.fileio.rbd import load_rbd
    rbd = load_rbd(str(REPO / "tests/golden/HierarchyExample/input.rbd"))
    offered = scope_options(rbd)
    assert {"id": 23, "name": "500path"} in offered["hierarchies"]
    result = analyse_rbd(
        rbd, hierarchy=23, dependencies="up", ignore_repairs=True,
        ignore=("pm", "spares", "resources"), importance=False)
    assert result["hierarchy"] == "500path"
    assert result["scope"]["hierarchy"] == "500path"
    assert 0.0 < result["reliability"] < 1.0


def test_bdd_node_count_is_comma_grouped_in_the_summary():
    from raptor.api.analytical import analyse_rbd
    from raptor.api.analytical_text import summary_text
    from raptor.core.fileio.rbds import load_rbds
    result = analyse_rbd(load_rbds(str(MODELS / "Lantern1.rbds")),
                         importance=False)
    assert f"{result['bdd_nodes']:,} BDD nodes" in summary_text(result)


def test_an_unknown_hierarchy_is_refused_with_a_message():
    from raptor.api.analytical import analyse_rbd
    from raptor.core.fileio.rbd import load_rbd
    rbd = load_rbd(str(REPO / "tests/golden/HierarchyExample/input.rbd"))
    with pytest.raises(ValueError, match="hierarchy"):
        analyse_rbd(rbd, hierarchy="no-such-hierarchy")


def test_hierarchy_scope_is_named_in_text_and_csv(capsys):
    from raptor.api.analytical import analyse_rbd
    from raptor.api.analytical_text import summary_text
    from raptor.core.fileio.rbd import load_rbd
    rbd = load_rbd(str(REPO / "tests/golden/HierarchyExample/input.rbd"))
    result = analyse_rbd(rbd, hierarchy=23, dependencies="up",
                         ignore_repairs=True,
                         ignore=("pm", "spares", "resources"),
                         importance=False)
    text = summary_text(result)
    assert "Hierarchy analysed: 500path" in text
    code = main(["analytic", str(REPO / "tests/golden/HierarchyExample/input.rbd"),
                 "--hierarchy", "500path", "--dependencies", "up",
                 "--ignore", "pm,repairs,spares,resources",
                 "--no-importance", "-f", "csv"])
    out = capsys.readouterr().out
    assert code == X.OK
    assert "hierarchy,500path" in out


def test_cross_boundary_dependencies_refuse_the_down_choice():
    from raptor.api.analytical import analyse_rbd
    from raptor.core.fileio.rbd import load_rbd
    rbd = load_rbd(str(REPO / "tests/golden/LadL7HierFull/input.rbd"))
    common = dict(hierarchy="H3", ignore_repairs=True,
                  ignore=("pm", "spares", "resources"), importance=False)
    assert analyse_rbd(rbd, dependencies="up", **common)["reliability"] > 0
    with pytest.raises(ValueError, match="boundary"):
        analyse_rbd(rbd, dependencies="down", **common)


def test_bad_hierarchy_and_limit_are_usage_errors(capsys):
    model = str(REPO / "tests/golden/HierarchyExample/input.rbd")
    assert main(["analytic", model, "--hierarchy", "NoSuchHierarchy"]) == X.USAGE
    err = capsys.readouterr().err
    assert "no hierarchy" in err
    assert main(["analytic", model, "--max-bdd-nodes", "1"]) == X.USAGE
    err = capsys.readouterr().err
    assert "at least 2" in err

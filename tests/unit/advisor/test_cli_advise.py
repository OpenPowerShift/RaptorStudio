"""`raptor advise`: output formats, filters and exit codes."""
from __future__ import annotations

import json

from raptor.api import build
from raptor.cli import exitcodes as X
from raptor.cli.main import main
from raptor.core.fileio.rbds import save_rbds

from .helpers import block, parallel_spec, series_spec


def run_cli(capsys, *argv):
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def model(tmp_path, spec, name="m.rbds"):
    path = tmp_path / name
    save_rbds(build.build_from_spec(spec), str(path))
    return str(path)


def flawed(tmp_path):
    return model(tmp_path, parallel_spec(
        block("A"), block("B", dep_type="RBDBlock", dep_num=1, dep_idle=100)))


def test_text_lists_findings_most_important_first(capsys, tmp_path):
    code, out, _ = run_cli(capsys, "advise", flawed(tmp_path))
    assert code == X.OK
    lines = out.splitlines()
    assert "0 error(s), 1 warning(s)" in lines[0]
    assert "warning: M-STR-002: B backs up A" in lines[1]
    assert any("fix: Remove the dependency [block:2.dep_type=None; block:2.dep_num=0]" in line for line in lines)
    assert "why:" not in out


def test_why_adds_the_reasoning(capsys, tmp_path):
    _, out, _ = run_cli(capsys, "advise", flawed(tmp_path), "--why")
    assert "why: A dependent block reacts" in out


def test_json_is_structured(capsys, tmp_path):
    code, out, _ = run_cli(capsys, "advise", flawed(tmp_path), "-f", "json")
    doc = json.loads(out)
    assert code == X.OK and doc["schema"] == "raptor-advise/1"
    result = doc["results"][0]
    assert result["counts"]["warning"] == 1
    first = result["findings"][0]
    assert first["rule"] == "M-STR-002" and first["basis"] == "exact"
    assert first["targets"][0]["path"] == "block:2.dep_type"
    assert first["fixes"][0]["assignments"] == ["block:2.dep_type=None", "block:2.dep_num=0"]


def test_min_severity_and_only_and_skip(capsys, tmp_path):
    path = flawed(tmp_path)
    _, out, _ = run_cli(capsys, "advise", path, "--min-severity", "warning", "-f", "json")
    assert {f["severity"] for f in json.loads(out)["results"][0]["findings"]} == {"warning"}
    _, out, _ = run_cli(capsys, "advise", path, "--only", "M-SPR", "-f", "json")
    assert {f["rule"] for f in json.loads(out)["results"][0]["findings"]} == {"M-SPR-001"}
    _, out, _ = run_cli(capsys, "advise", path, "--skip", "M-STR,M-SPR", "-f", "json")
    assert json.loads(out)["results"][0]["findings"] == []


def test_exit_code_is_3_on_errors_and_on_warnings_when_strict(capsys, tmp_path):
    path = flawed(tmp_path)
    assert run_cli(capsys, "advise", path, "--strict")[0] == X.INVALID
    crew = series_spec(block("A", num_resources=1, resource_name="Crew",
                             repair_resources_needed=2),
                       resources=[{"name": "Crew", "num_in": 1}])
    code, out, _ = run_cli(capsys, "advise", model(tmp_path, crew, "crew.rbds"))
    assert code == X.INVALID and "error: M-RES-001" in out


def test_markdown_table(capsys, tmp_path):
    _, out, _ = run_cli(capsys, "advise", flawed(tmp_path), "-f", "md")
    assert "| Level | Rule | Finding | Method |" in out and "| warning | M-STR-002 |" in out


def test_list_rules(capsys):
    code, out, _ = run_cli(capsys, "advise", "--list-rules", "-f", "json")
    rules = json.loads(out)["rules"]
    assert code == X.OK and {"M-STR-002", "M-SPR-003", "M-RES-001"} <= {r["id"] for r in rules}


def test_needs_a_model(capsys):
    code, _, err = run_cli(capsys, "advise")
    assert code == X.USAGE and "MODEL" in err


def test_several_models_and_a_missing_one(capsys, tmp_path):
    code, out, _ = run_cli(capsys, "advise", flawed(tmp_path), str(tmp_path / "none.rbds"))
    assert code == X.USAGE and "no such model file" in out and "M-STR-002" in out


def test_target_runs_the_model_and_gives_a_verdict(capsys, tmp_path):
    spec = series_spec(block("A", fail_params=[500, 0, 0], repair_params=[20, 5, 0]),
                       experiment={"trials": 40, "termination_type": "TimeSim",
                                   "term_condition": 2000})
    path = model(tmp_path, spec, "run.rbds")
    code, out, _ = run_cli(capsys, "advise", path, "--target", "system.ao>=0.5", "-f", "json")
    found = {f["rule"]: f for f in json.loads(out)["results"][0]["findings"]}
    assert code == X.OK and found["R-REQ-001"]["title"] == "Target met"
    # the run is attributed, so the drivers findings the GUI gives appear too
    assert any(rule.startswith("R-DRV-") for rule in found), sorted(found)
    code, out, _ = run_cli(capsys, "advise", path, "--target", "system.ao>=0.9999")
    assert "R-REQ-001 (statistical): Target missed" in out


def test_bad_target_is_a_usage_error(capsys, tmp_path):
    code, _, err = run_cli(capsys, "advise", flawed(tmp_path), "--target", "system.ao=1")
    assert code == X.USAGE and "METRIC>=VALUE" in err


def test_a_target_on_an_unknown_metric_is_refused_and_percent_is_accepted():
    """"ao>=0.99" used to give no verdict and no word why; "99.5%" was not a
    number although the output prints percentages."""
    import pytest

    from raptor.cli.cmd_advise import _targets
    from raptor.cli.exitcodes import CliError
    assert _targets(["ao>=0.99"]) == [{"metric": "ao", "op": ">=", "value": 0.99}]
    assert _targets(["system.ao>=99.5%"])[0]["value"] == pytest.approx(0.995)
    with pytest.raises(CliError, match="unknown requirement metric 'bogus'"):
        _targets(["bogus>=1"])

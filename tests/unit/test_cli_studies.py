"""`raptor whatif`, `sensitivity` and the optimisers on the command line:
exit status, progress and its silencing."""
from __future__ import annotations

import json

import pytest

from raptor.api import build
from raptor.cli import exitcodes as X
from raptor.cli.main import main
from raptor.core.fileio.rbds import save_rbds

SPEC = {
    "blocks": [{"name": "A", "fail_params": [100, 0, 0], "repair_params": [5, 0, 0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
    "experiment": {"trials": 2, "termination_type": "TimeSim", "term_condition": 500},
}


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "m.rbds"
    save_rbds(build.build_from_spec(SPEC), str(path))
    return str(path)


def run_cli(capsys, *argv):
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_a_scenario_that_cannot_run_fails_the_command(capsys, model):
    code, _, err = run_cli(capsys, "whatif", model, "--change", "block:A.no_such_field=1",
                           "--trials", "2")
    assert code != X.OK
    assert "did not run" in err or "no_such_field" in err


def test_a_good_scenario_succeeds(capsys, model):
    code, out, _ = run_cli(capsys, "whatif", model, "--change", "block:A.repair_params=2,1,0",
                           "--trials", "2", "-f", "json")
    assert code == X.OK and json.loads(out)["schema"] == "raptor-whatif/1"


def test_json_progress_is_one_object_per_line(capsys, model):
    code, _, err = run_cli(capsys, "whatif", model, "--change", "block:A.repair_params=2,1,0",
                           "--trials", "2", "--progress", "json")
    assert code == X.OK
    events = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    assert events and all(e["event"] == "progress" for e in events)


@pytest.mark.parametrize("before, after", [
    (["-q"], ["--progress", "json"]), ([], ["--progress", "none"]), ([], [])])
def test_progress_is_silent_when_asked_or_not_a_terminal(capsys, model, before, after):
    # under pytest stderr is not a terminal, so `auto` (no flag) is silent too
    code, _, err = run_cli(capsys, *before, "whatif", model, "--change",
                           "block:A.repair_params=2,1,0", "--trials", "2", *after)
    assert code == X.OK
    assert "[" not in err and '"event"' not in err


def test_sensitivity_refuses_an_unknown_metric(capsys, model):
    code, _, err = run_cli(capsys, "sensitivity", model, "--trials", "2", "--top", "1",
                           "--metric", "bogus")
    assert code != X.OK and "bogus" in err

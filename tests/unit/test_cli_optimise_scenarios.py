"""`raptor optimise spares|pm|resources`, `raptor whatif` and `raptor
sensitivity` on the command line: text and JSON output, --apply writing the
model, and the refusals. (CI coverage of `cmd_optimise.py` was 57 % and of
`cmd_scenarios.py` 62 %: the output and error paths had no tests.)"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from raptor.api import build
from raptor.cli import exitcodes as X
from raptor.cli.main import main
from raptor.core.fileio.rbds import load_rbds, save_rbds

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from gen_mcp_output_schemas import OPT_SPEC  # noqa: E402

FAST = ["-n", "2", "--seed", "2"]


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "opt.rbds"
    save_rbds(build.build_from_spec(OPT_SPEC), str(path))
    return str(path)


def run_cli(capsys, *argv):
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _pool_stock(path):
    rows = load_rbds(path).sections["SPARE_POOL_DATA"].rows
    return next(float(r["Stock"]) for r in rows if r.get("PoolName") == "Seals")


# ---- optimise ---------------------------------------------------------------

def test_optimise_with_no_action_says_what_to_use(capsys):
    code, _, err = run_cli(capsys, "optimise")
    assert code == X.USAGE and "raptor optimise spares MODEL" in err


@pytest.mark.parametrize("target, words", [
    ("system.ao", "use a comparison"),
    ("system.ao>=high", "is not a number"),
])
def test_optimise_refuses_a_bad_target(capsys, model, target, words):
    code, _, err = run_cli(capsys, "optimise", "spares", model, "--target", target)
    assert code == X.USAGE and words in err


def test_spares_text_json_and_apply(capsys, model):
    code, out, _ = run_cli(capsys, "optimise", "spares", model, "--target",
                           "system.ao>=0.5", "--budget", "2", *FAST)
    assert code == X.OK and out.startswith("Base: ") and "cost" in out
    code, out, _ = run_cli(capsys, "optimise", "spares", model, "--target",
                           "system.ao>=0.5", "--budget", "2", "--pools", "Seals",
                           *FAST, "-f", "json")
    data = json.loads(out)
    assert code == X.OK and data["schema"].startswith("raptor-optimise")
    assert all(c.startswith("pool:Seals.") for c in data["changes"])
    before = _pool_stock(model)
    code, _, err = run_cli(capsys, "optimise", "spares", model, "--target",
                           "system.ao>=0.5", "--budget", "2", *FAST, "--apply")
    assert code == X.OK
    rec = data["recommended"]["stocks"]["Seals"]
    if rec == before:
        # already at the recommended stock: no change, nothing written
        assert data["changes"] == [] and "wrote" not in err
    else:
        assert "wrote the recommended stocks" in err and _pool_stock(model) == rec


def test_spares_refuses_an_unknown_pool(capsys, model):
    code, _, err = run_cli(capsys, "optimise", "spares", model, "--pools", "Nope",
                           "--budget", "2", *FAST)
    assert code == X.USAGE and "no spare pool named 'Nope'" in err


def test_pm_text_json_and_apply(capsys, model):
    code, out, _ = run_cli(capsys, "optimise", "pm", model, *FAST)
    assert code == X.OK and out.startswith("Pump: ")
    code, out, _ = run_cli(capsys, "optimise", "pm", model, "--blocks", "Pump",
                           *FAST, "-f", "json")
    data = json.loads(out)
    assert data["schema"] == "raptor-optimise-pm/1"
    assert [r["block"] for r in data["recommendations"]] == ["Pump"]
    code, _, err = run_cli(capsys, "optimise", "pm", model, *FAST, "--apply")
    assert code == X.OK and "change(s) to" in err


def test_pm_refuses_an_unknown_block(capsys, model):
    """A misspelt name used to be dropped: the study ran on nothing, exit 0."""
    code, _, err = run_cli(capsys, "optimise", "pm", model, "--blocks", "Nope", *FAST)
    assert code == X.USAGE and "no block named 'Nope'" in err


def test_pm_cost_objective_needs_cost_analysis(capsys, model):
    """Without cost analysis no run has a Grand Total: it used to report
    "worthwhile" in the screen and then "No block could be screened"."""
    code, _, err = run_cli(capsys, "optimise", "pm", model, "--objective", "cost", *FAST)
    assert code == X.USAGE and "needs cost analysis" in err
    assert run_cli(capsys, "set", model, "--in-place", "system.cost=true")[0] == X.OK
    code, out, _ = run_cli(capsys, "optimise", "pm", model, "--objective", "cost",
                           *FAST, "-f", "json")
    data = json.loads(out)
    assert code == X.OK and data["metric"] == "grand_total"
    assert data["recommendations"][0]["options_simulated"] > 0


def test_resources_text_json_and_apply(capsys, model):
    code, out, _ = run_cli(capsys, "optimise", "resources", model, "--target",
                           "system.ao>=0.5", "--extra", "1", *FAST)
    assert code == X.OK and out.startswith("Crew: ") and "crews" in out
    code, out, _ = run_cli(capsys, "optimise", "resources", model, "--resources",
                           "Crew", "--target", "system.ao>=0.5", "--extra", "0",
                           *FAST, "-f", "json")
    data = json.loads(out)
    assert data["schema"] == "raptor-optimise-resources/1"
    assert data["recommendations"][0]["resource"] == "Crew"
    code, _, err = run_cli(capsys, "optimise", "resources", model, "--target",
                           "system.ao>=0.5", "--extra", "0", *FAST, "--apply")
    assert code == X.OK and "wrote the recommended sizes" in err


def test_apply_reports_an_unwritable_model(capsys, model, monkeypatch):
    """--apply writes through a temporary file; a failed save is an output
    error and leaves the model as it was."""
    def fail(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr("raptor.api.workspace.Workspace.save_model_as", fail)
    before = Path(model).read_bytes()
    code, _, err = run_cli(capsys, "optimise", "pm", model, *FAST, "--apply")
    assert code == X.OUTPUT and "disk full" in err
    assert Path(model).read_bytes() == before


# ---- whatif -----------------------------------------------------------------

CHANGE = "block:Pump.repair_params=2,0,0"


def test_whatif_text_output(capsys, model):
    code, out, _ = run_cli(capsys, "whatif", model, "--change", CHANGE,
                           "--name", "Faster repair", *FAST)
    assert code == X.OK and "Faster repair" in out


def test_whatif_requirement_and_precision(capsys, model):
    code, out, _ = run_cli(capsys, "whatif", model, "--change", CHANGE,
                           "--requirement", "system.ao>=0.5", "--precision", "50%",
                           "-n", "4", "--seed", "2", "-f", "json")
    assert code == X.OK
    assert json.loads(out)["schema"] == "raptor-whatif/1"


@pytest.mark.parametrize("argv, words", [
    (["--requirement", "system.ao"], "use a comparison"),
    (["--requirement", "system.ao>=lots"], "is not a number"),
    (["--precision", "tight"], "could not convert"),
])
def test_whatif_refusals(capsys, model, argv, words):
    code, _, err = run_cli(capsys, "whatif", model, "--change", CHANGE, *argv, *FAST)
    assert code == X.USAGE and words in err


def test_whatif_save_and_change_file(capsys, model, tmp_path):
    saved = tmp_path / "s.json"
    code, out, err = run_cli(capsys, "whatif", model, "--change", CHANGE,
                             "--name", "Fast", "--save", str(saved))
    assert code == X.OK and "wrote 1 scenario(s)" in err and not out   # saved, not run
    assert json.loads(saved.read_text())[0]["name"] == "Fast" \
        if isinstance(json.loads(saved.read_text()), list) \
        else json.loads(saved.read_text())["scenarios"][0]["name"] == "Fast"
    listing = tmp_path / "list.json"
    listing.write_text(json.dumps([{"name": "Fast", "changes": [CHANGE]},
                                   {"name": "Slow", "changes": ["block:Pump.repair_params=9,0,0"]}]))
    code, out, _ = run_cli(capsys, "whatif", model, "--change-file", str(listing), *FAST,
                           "-f", "json")
    assert code == X.OK and len(json.loads(out)["rows"]) >= 2


@pytest.mark.parametrize("content, words", [
    (None, "cannot read"), ("{ broken", "cannot read"),
    ('{"name": "x"}', "must hold a JSON list"), ('[1]', 'needs "changes"'),
])
def test_whatif_refuses_a_bad_change_file(capsys, model, tmp_path, content, words):
    path = tmp_path / "c.json"
    if content is not None:
        path.write_text(content)
    code, _, err = run_cli(capsys, "whatif", model, "--change-file", str(path), *FAST)
    assert code == X.USAGE and words in err


# ---- sensitivity ------------------------------------------------------------

def test_sensitivity_text_and_json(capsys, model):
    code, out, _ = run_cli(capsys, "sensitivity", model, "--top", "2", *FAST)
    assert code == X.OK and "Pump" in out
    code, out, _ = run_cli(capsys, "sensitivity", model, "--top", "2", *FAST, "-f", "json")
    assert code == X.OK and json.loads(out)["schema"] == "raptor-sensitivity/1"


def test_sensitivity_refuses_a_model_with_nothing_to_vary(capsys, tmp_path):
    path = tmp_path / "n.rbds"
    save_rbds(build.build_from_spec({
        "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
        "links": [{"from": "Start", "to": "End"}]}), str(path))
    code, _, err = run_cli(capsys, "sensitivity", str(path), *FAST)
    assert code in (X.REFUSED, X.INVALID) and err

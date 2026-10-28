"""The `raptor` command line: behaviour, exit codes, output discipline.

No Qt is needed except for the GUI-routing tests, which stub the GUI.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from raptor.cli import exitcodes as X
from raptor.cli.main import main

REPO = Path(__file__).resolve().parents[2]
GOLD = REPO / "tests" / "golden"
MOTOR = str(GOLD / "MotorDemo" / "input.rbd")
SPF = str(GOLD / "SPFProbe" / "input.rbd")
CYCLE = str(GOLD / "DepCycleIdleProbe" / "input.rbd")


def run_cli(capsys, *argv):
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# --- routing: no command means the GUI ------------------------------------

@pytest.fixture
def gui_calls(monkeypatch):
    calls = []

    def fake(args):
        calls.append((args.model, args.new, args.theme))
        return 0
    monkeypatch.setattr("raptor.cli.cmd_gui.gui_command", fake)
    # the parser captured the function object at registration time
    import raptor.cli.cmd_gui as g
    orig = g.add_parser

    def add_parser(sub):
        orig(sub)
        sub.choices["gui"].set_defaults(func=fake)
    monkeypatch.setattr(g, "add_parser", add_parser)
    return calls


def test_no_arguments_opens_the_gui(gui_calls):
    assert main([]) == 0
    assert gui_calls == [(None, False, None)]


def test_a_bare_model_path_opens_the_gui_with_it(gui_calls):
    assert main([MOTOR]) == 0
    assert gui_calls == [(MOTOR, False, None)]


def test_global_options_before_a_model_still_route_to_the_gui(gui_calls):
    assert main(["-v", MOTOR]) == 0
    assert gui_calls[0][0] == MOTOR


def test_gui_without_a_display_says_so_and_exits_2(monkeypatch, capsys):
    for var in ("DISPLAY", "WAYLAND_DISPLAY", "QT_QPA_PLATFORM"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    code, out, err = run_cli(capsys, "gui")
    assert code == X.USAGE
    assert "no display" in err and "raptor run" in err
    assert out == ""


def test_a_mistyped_command_is_a_usage_error_not_a_file_open(capsys):
    code, _out, err = run_cli(capsys, "rnu", MOTOR)
    assert code == X.USAGE
    assert "unknown command" in err


def test_legacy_slash_switches_translate_and_password_is_ignored(capsys):
    from raptor.cli.main import _legacy_switches
    assert _legacy_switches(["/sd", "/password-zt", "x.rbd"]) == [
        "--sim-details", "x.rbd"]
    assert "not needed" in capsys.readouterr().err


# --- exit codes ------------------------------------------------------------

def test_missing_model_is_2(capsys):
    code, _, err = run_cli(capsys, "run", "no/such/model.rbd")
    assert code == X.USAGE and "no such model" in err


def test_validate_clean_is_0(capsys):
    code, out, _ = run_cli(capsys, "validate", MOTOR)
    assert code == 0 and "0 error(s)" in out


def test_invalid_model_is_3(capsys, tmp_path):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.core.fileio.builder import save
    rbd = load_rbd(MOTOR)
    for row in rbd.sections["BLOCK_DATA"].rows:
        if row.get("ID") != "0":
            row["FParam2"] = "0.0"
            break
    bad = tmp_path / "bad.rbd"
    save(rbd, str(bad))
    code, _, err = run_cli(capsys, "run", str(bad), "-n", "1")
    assert code == X.INVALID and "error" in err
    code, out, _ = run_cli(capsys, "validate", str(bad), "-f", "json")
    assert code == X.INVALID
    assert json.loads(out)["results"][0]["errors"]


def test_strict_makes_warnings_fail(capsys):
    code, _, _ = run_cli(capsys, "validate", CYCLE)
    assert code == X.OK
    code, _, _ = run_cli(capsys, "validate", CYCLE, "--strict")
    assert code == X.INVALID


def test_bad_set_path_is_2_and_names_the_path(capsys):
    code, _, err = run_cli(capsys, "run", MOTOR, "-n", "1",
                           "--set", "block:NoSuchBlock.fail_dist=weibull")
    assert code == X.USAGE and "NoSuchBlock" in err


@pytest.mark.parametrize("bad", ["bogus", "block:Casing=1",
                                 "weird:thing.x=1", "system.nonsense=1"])
def test_malformed_set_paths_are_2(capsys, bad):
    code, _, err = run_cli(capsys, "run", MOTOR, "-n", "1", "--set", bad)
    assert code == X.USAGE and err


# --- stdout discipline -----------------------------------------------------

@pytest.mark.parametrize("verbosity", [[], ["-vv"]])
def test_json_on_stdout_is_pure_json_even_with_warnings_and_verbosity(
        capsys, verbosity):
    code, out, err = run_cli(capsys, *verbosity, "run", CYCLE, "-n", "2",
                             "-f", "json")
    assert code == 0
    doc = json.loads(out)               # the WHOLE of stdout parses
    assert doc["schema"] == "raptor-results/1"
    assert any("circular" in w for w in doc["warnings"])
    assert "warning:" in err            # warnings went to stderr


def test_results_document_content(capsys):
    _code, out, _ = run_cli(capsys, "run", MOTOR, "-n", "3", "-f", "json",
                           "--seed", "7")
    doc = json.loads(out)
    assert doc["options"]["runs"] == 3 and doc["options"]["seed"] == 7
    assert len(doc["runs"]) == 3
    a = doc["summary"]["Availability"]
    assert a["min"] <= a["mean"] <= a["max"]
    assert doc["model"]["units"] == "hours"
    assert doc["versions"]["raptor"]
    # N/A is null, never a string
    assert doc["summary"]["MTBMs"]["mean"] is None


def test_seed_changes_results_and_is_reproducible(capsys):
    def avail(seed):
        _, out, _ = run_cli(capsys, "run", MOTOR, "-n", "5", "-f", "json",
                            *(["--seed", str(seed)] if seed else []))
        return json.loads(out)["summary"]["Availability"]["mean"]
    assert avail(11) == avail(11)
    assert avail(11) != avail(12)


def test_default_run_is_deterministic(capsys):
    _, a, _ = run_cli(capsys, "run", MOTOR, "-n", "4", "-f", "json")
    _, b, _ = run_cli(capsys, "run", MOTOR, "-n", "4", "-f", "json")
    da, db = json.loads(a), json.loads(b)
    da.pop("wall_seconds"); db.pop("wall_seconds")
    assert da == db


def test_json_path_dash_writes_to_stdout_only_once(capsys):
    _code, out, _ = run_cli(capsys, "run", MOTOR, "-n", "1", "--json", "-")
    assert json.loads(out)["schema"] == "raptor-results/1"


# --- run options -----------------------------------------------------------

def test_set_changes_the_run_but_not_the_file(capsys):
    before = Path(MOTOR).read_bytes()
    _, base, _ = run_cli(capsys, "run", MOTOR, "-n", "5", "-f", "json")
    _, slow, _ = run_cli(capsys, "run", MOTOR, "-n", "5", "-f", "json",
                         "--set", "block:Casing.fail_params[1]=20")
    assert Path(MOTOR).read_bytes() == before
    a = json.loads(base)["summary"]["Availability"]["mean"]
    b = json.loads(slow)["summary"]["Availability"]["mean"]
    assert b < a


def test_stop_failures_switches_the_termination(capsys):
    _, out, _ = run_cli(capsys, "run", MOTOR, "-n", "2", "-f", "json",
                        "--stop", "failures=2")
    doc = json.loads(out)
    assert doc["options"]["termination"] == "FailSim"
    assert doc["options"]["term_condition"] == 2.0


@pytest.mark.parametrize("bad", ["failures", "weeks=3", "failures=x",
                                 "time=5"])
def test_bad_stop_is_2(capsys, bad):
    code, _, _err = run_cli(capsys, "run", MOTOR, "--stop", bad)
    assert code == X.USAGE


def test_length_conflicts_with_a_failure_stop(capsys):
    code, _, _err = run_cli(capsys, "run", MOTOR, "--stop", "failures=3",
                           "--length", "100")
    assert code == X.USAGE


def test_save_model_writes_the_changed_model(capsys, tmp_path):
    out = tmp_path / "changed.rbds"
    code, _, _ = run_cli(capsys, "run", MOTOR, "-n", "1", "-f", "none",
                         "--set", "system.length=77", "--save-model",
                         str(out))
    assert code == 0 and out.is_file()
    code, text, _ = run_cli(capsys, "get", str(out), "system.length")
    assert float(text.strip()) == 77.0


def test_set_file_lines_and_toml(capsys, tmp_path):
    f = tmp_path / "chg.txt"
    f.write_text("# comment\nsystem.runs=2\nsystem.length=50\n")
    _code, out, _ = run_cli(capsys, "run", MOTOR, "-f", "json",
                           "--set-file", str(f))
    doc = json.loads(out)
    assert doc["options"]["runs"] == 2 and doc["options"]["term_condition"] == 50
    t = tmp_path / "chg.toml"
    t.write_text('[system]\nruns = 3\n')
    _, out, _ = run_cli(capsys, "run", MOTOR, "-f", "json",
                        "--set-file", str(t))
    assert json.loads(out)["options"]["runs"] == 3


# --- reports are the real-format files --------------------------------------

def test_reports_match_the_api_text_exactly(capsys, tmp_path):
    out = tmp_path / "o"
    code, _, _ = run_cli(capsys, "run", MOTOR, "-n", "3", "-f", "none",
                         "-r", "final", "-r", "end-of-run", "-o", str(out))
    assert code == 0
    files = sorted(p.name for p in out.iterdir())
    assert files == ["rpt_end_of_run_input.txt", "rpt_final_results_input.txt"]
    final = (out / "rpt_final_results_input.txt").read_text()
    assert final.startswith("Final_Results_Report")
    assert "Availability" in final


def test_unknown_report_name_is_2(capsys):
    code, _, _err = run_cli(capsys, "run", MOTOR, "-r", "nonsense")
    assert code == X.USAGE


# --- the other commands ------------------------------------------------------

def test_get_and_set_round_trip(capsys, tmp_path):
    out = tmp_path / "m.rbds"
    code, _, _ = run_cli(capsys, "set", MOTOR, "block:Casing.fail_dist=15",
                         "block:Casing.fail_params[1]=999", "-o", str(out))
    assert code == 0
    _, v, _ = run_cli(capsys, "get", str(out), "block:Casing.fail_params[1]")
    assert float(v.strip()) == 999.0
    _, v, _ = run_cli(capsys, "get", str(out), "block:Casing.fail_dist")
    assert v.strip() == "15"


def test_set_refuses_without_a_destination(capsys):
    code, _, err = run_cli(capsys, "set", MOTOR, "system.runs=3")
    assert code == X.USAGE and "in-place" in err


def test_set_in_place_with_backup(capsys, tmp_path):
    m = tmp_path / "m.rbds"
    run_cli(capsys, "convert", MOTOR, str(m))
    original = m.read_bytes()
    code, _, _ = run_cli(capsys, "set", str(m), "system.runs=9",
                         "--in-place", "--backup")
    assert code == 0
    assert (tmp_path / "m.rbds.bak").read_bytes() == original
    _, v, _ = run_cli(capsys, "get", str(m), "system.runs")
    assert v.strip() == "9"


def test_set_failure_leaves_the_file_untouched(capsys, tmp_path):
    m = tmp_path / "m.rbds"
    run_cli(capsys, "convert", MOTOR, str(m))
    original = m.read_bytes()
    code, _, _ = run_cli(capsys, "set", str(m), "block:NoSuch.name=x",
                         "--in-place")
    assert code == X.USAGE and m.read_bytes() == original


def test_convert_round_trip_rbd_rbds_rbd_keeps_the_model(capsys, tmp_path):
    a, b = tmp_path / "a.rbds", tmp_path / "b.rbd"
    assert run_cli(capsys, "convert", MOTOR, str(a))[0] == 0
    assert run_cli(capsys, "convert", str(a), str(b))[0] == 0
    _, x, _ = run_cli(capsys, "run", MOTOR, "-n", "2", "-f", "json")
    _, y, _ = run_cli(capsys, "run", str(b), "-n", "2", "-f", "json")
    sx, sy = json.loads(x)["summary"], json.loads(y)["summary"]
    assert sx == sy


def test_sweep_grid_and_json(capsys):
    code, out, _ = run_cli(
        capsys, "sweep", MOTOR, "--vary", "block:Casing.fail_params[1]=100,900",
        "--vary", "system.length=36,72", "-n", "2", "-f", "json")
    doc = json.loads(out)
    assert code == 0 and len(doc["rows"]) == 4
    vals = {(r["values"]["block:Casing.fail_params[1]"],
             r["values"]["system.length"]) for r in doc["rows"]}
    assert vals == {("100", "36"), ("100", "72"), ("900", "36"), ("900", "72")}


@pytest.mark.parametrize("spec,expected", [
    ("block:A.x=1:3:1", ["1", "2", "3"]),
    ("block:A.x=0.1:0.5:0.2", ["0.1", "0.3", "0.5"]),
    ("block:A.x=5:1:-2", ["5", "3", "1"]),
    ("block:A.x=a,b", ["a", "b"]),
])
def test_vary_ranges_have_no_float_noise(spec, expected):
    from raptor.api.batch import expand_vary
    assert expand_vary(spec)[1] == expected


def test_vary_rejects_a_range_that_cannot_arrive():
    from raptor.api.batch import expand_vary
    from raptor.api.paths import PathError
    with pytest.raises(PathError):
        expand_vary("system.runs=1:5:-1")


def test_compare_needs_two(capsys):
    code, _, _err = run_cli(capsys, "compare", MOTOR)
    assert code == X.USAGE


def test_spf_text_and_file(capsys, tmp_path):
    f = tmp_path / "spf.txt"
    code, _out, _ = run_cli(capsys, "spf", SPF, "-o", str(f))
    assert code == 0
    assert f.read_bytes().startswith(b"Single Point Failure File")


def test_fev_script_reproduces_the_real_phased_log(capsys, tmp_path):
    """The captured `Phasing3` session, replayed through the command line."""
    script = tmp_path / "s.fev"
    script.write_text("""
        write
        next        # phase 2
        write
        click Thermal
        write
        erase-phase
        write
        next
        write
        next
        write
        prev
        write
        erase
        write
    """)
    log = tmp_path / "log.txt"
    model = str(GOLD / "FevPhasing3" / "input.rbd")
    code, _out, _ = run_cli(capsys, "fev", model, "--script", str(script),
                           "--log", str(log))
    assert code == 0
    real = GOLD / "FevPhasing3" / "legacy" / "fevlog_FevPhasing3.txt"
    assert log.read_bytes() == real.read_bytes()


def test_fev_options_and_json(capsys):
    model = str(GOLD / "FevPhasing3" / "input.rbd")
    code, out, _ = run_cli(capsys, "fev", model, "--phase", "2", "--fail",
                           "Thermal", "-f", "json")
    assert code == 0
    doc = json.loads(out)
    assert doc["schema"] == "raptor-fev/1"
    w = doc["writes"][0]
    assert w["system_status"] == "Red" and w["phase"] == 2
    assert "Thermal" in w["effects"]["failed"]


def test_fev_bad_input_is_exit_3(capsys, tmp_path):
    model = str(GOLD / "FevPhasing3" / "input.rbd")
    code, _, err = run_cli(capsys, "fev", model, "--phase", "99")
    assert code == X.INVALID and "phase 99" in err
    code, _, err = run_cli(capsys, "fev", model, "--fail", "NoSuchBlock")
    assert code == X.INVALID
    s = tmp_path / "bad.fev"
    s.write_text("explode now\n")
    code, _, err = run_cli(capsys, "fev", model, "--script", str(s))
    assert code == X.INVALID and "line 1" in err


def test_info_json_has_a_schema(capsys):
    _code, out, _ = run_cli(capsys, "info", MOTOR, "-f", "json")
    assert json.loads(out)["schema"] == "raptor-info/1"


def test_help_topics(capsys):
    code, out, _ = run_cli(capsys, "help", "exit-codes")
    assert "130" in out
    code, out, _ = run_cli(capsys, "help", "set-paths")
    assert "kind:selector.field" in out
    code, _, _err = run_cli(capsys, "help", "nonsense")
    assert code == X.USAGE


def test_completion_scripts_mention_every_command(capsys):
    for shell in ("bash", "zsh", "fish"):
        code, out, _ = run_cli(capsys, "completion", shell)
        assert code == 0
        for cmd in ("run", "validate", "sweep", "mcp"):
            assert cmd in out, (shell, cmd)


def test_every_registered_command_is_routable():
    """COMMANDS (the routing list) and the parser must agree."""
    from raptor.cli.main import COMMANDS, build_parser
    parser = build_parser()
    registered = set()
    for a in parser._actions:
        if isinstance(getattr(a, "choices", None), dict):
            registered |= set(a.choices)
    assert registered == set(COMMANDS), registered ^ set(COMMANDS)


# --- structure ---------------------------------------------------------------

def test_the_cli_package_never_imports_the_engine_directly():
    """No simulation logic in the CLI: it goes through raptor.api."""
    bad = []
    for path in (REPO / "raptor" / "cli").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom):
                mod = node.module
            elif isinstance(node, ast.Import):
                mod = node.names[0].name
            if mod and (mod.startswith("raptor.core.engine")
                        or mod.startswith("raptor.core.fileio")):
                bad.append(f"{path.name}: {mod}")
    assert not bad, bad


def test_report_reads_a_stored_run(capsys, tmp_path):
    ws = tmp_path / "ws"
    code, out, _ = run_cli(capsys, "run", MOTOR, "-n", "2", "-f", "json",
                           "--workspace", str(ws))
    run_id = json.loads(out)["run_id"]
    assert run_id
    code, text, _ = run_cli(capsys, "report", run_id, "--workspace", str(ws))
    assert code == 0 and text.startswith("Final_Results_Report")
    code, _, _err = run_cli(capsys, "report", "nope", "--workspace", str(ws))
    assert code == X.USAGE


# --- isolated hierarchy (a port extension) --------------------------------

HIERARCHY = str(GOLD / "HierarchyExample" / "input.rbd")


def test_run_hierarchy_labels_every_output(capsys, tmp_path):
    code, out, _err = run_cli(capsys, "run", HIERARCHY, "--hierarchy", "650path",
                             "-n", "2", "-f", "json", "-o", str(tmp_path), "-r", "final")
    assert code == 0
    doc = json.loads(out)
    assert doc["isolation"]["extension"] == "isolated-hierarchy"
    assert "not a whole-model result" in doc["isolation"]["banner"]
    assert any("Isolated run" in w for w in doc["warnings"])
    names = {Path(f).name for f in doc["files"]}
    assert any(n.startswith("isolation_") for n in names)
    text = next(Path(f) for f in doc["files"] if Path(f).name.startswith("isolation_")).read_text()
    assert "650path" in text and "always available" in text


def test_run_hierarchy_text_summary_carries_the_banner(capsys):
    code, out, _ = run_cli(capsys, "run", HIERARCHY, "--hierarchy", "650path", "-n", "2")
    assert code == 0 and out.startswith("Isolated run of 650path")


def test_unknown_hierarchy_is_a_usage_error_and_refuse_is_exit_4(capsys):
    code, _, err = run_cli(capsys, "run", HIERARCHY, "--hierarchy", "Nope", "-n", "1")
    assert code == X.USAGE and "no hierarchy" in err
    code, _, err = run_cli(capsys, "run", HIERARCHY, "--hierarchy", "650path", "-n", "1",
                           "--pools", "refuse")
    assert code == X.REFUSED and "refuse" in err


def test_extract_saves_a_model_that_validates_and_reloads(capsys, tmp_path):
    out = tmp_path / "part.rbd"
    code, _, _ = run_cli(capsys, "extract", HIERARCHY, "--hierarchy", "650path", "-o", str(out))
    assert code == 0 and out.is_file()
    code, _, _ = run_cli(capsys, "validate", str(out), "-f", "json")
    assert code == 0
    from raptor.core.fileio.rbd import load_rbd
    r = load_rbd(str(out))
    assert sorted(n["Type"] for n in r.section("NODE_DATA").rows if n["Type"] in ("1", "3")) == ["1", "3"]
    code, _text, _ = run_cli(capsys, "run", str(out), "-n", "2")
    assert code == 0


def test_extract_report_hazards_lists_without_writing(capsys, tmp_path):
    code, out, _ = run_cli(capsys, "extract", HIERARCHY, "--hierarchy", "650path",
                           "--report-hazards", "-f", "json")
    assert code == 0
    doc = json.loads(out)
    assert doc["schema"] == "raptor-hazards/1"
    assert {h["kind"] for h in doc["hazards"]} >= {"pool", "stream"}
    code, _, _err = run_cli(capsys, "extract", HIERARCHY, "--hierarchy", "650path")
    assert code == X.USAGE


def test_fev_criticality_line_is_off_by_default_and_matches_real_when_on(capsys, tmp_path):
    model = str(GOLD / "FevPhasing3" / "input.rbd")
    script = tmp_path / "s.fev"
    script.write_text("write\nnext\nwrite\nclick Thermal\nwrite\nerase-phase\nwrite\nclick Attitude\nwrite\n")
    log = tmp_path / "log.txt"
    code, out, _ = run_cli(capsys, "fev", model, "--script", str(script), "--log", str(log),
                           "--criticality")
    assert code == 0
    real = GOLD / "FevPhasing3" / "legacy" / "fevlog_criticality_FevPhasing3.txt"
    assert log.read_bytes() == real.read_bytes()
    code, out, _ = run_cli(capsys, "fev", model, "--phase", "2", "--fail", "Thermal")
    assert "Criticality" not in out


# --- step -------------------------------------------------------------------

def test_step_ndjson_stops_match_the_stepsession_and_report_changes(capsys):
    import json
    from raptor.core.engine.step_session import StepSession
    from raptor.core.fileio.rbd import load_rbd
    path = str(GOLD / "TwoBlockRedundancy" / "input.rbd")
    code, out, _ = run_cli(capsys, "step", path, "--events", "6")
    assert code == 0
    recs = [json.loads(line) for line in out.splitlines()]
    assert [r["step"] for r in recs] == [1, 2, 3, 4, 5, 6]
    session = StepSession(load_rbd(path))
    times = []
    for _ in range(6):
        session.step_to_change()
        times.append(session.time)
    assert [r["time"] for r in recs] == times
    # a block failing shows up as a status change with its event-log line
    failing = [r for r in recs if r["changes"]]
    assert failing and failing[0]["changes"][0]["to"] == "Repairing"
    assert any("Failed" in ln for ln in failing[0]["log"])


def test_step_until_break_and_text_format(capsys):
    import json
    path = str(GOLD / "TwoBlockRedundancy" / "input.rbd")
    code, out, _ = run_cli(capsys, "step", path, "--until", "1.0")
    recs = [json.loads(line) for line in out.splitlines()]
    assert code == 0 and recs[-1]["time"] >= 1.0 and all(r["time"] < 1.0 for r in recs[:-1])
    code, out, _ = run_cli(capsys, "step", path, "--break", "4", "--events", "50")
    recs = [json.loads(line) for line in out.splitlines()]
    assert code == 0 and any(c["id"] == 4 for c in recs[-1]["changes"])
    code, out, _ = run_cli(capsys, "step", path, "--events", "2", "-f", "text")
    assert code == 0 and out.startswith("[1] t=0 system up")


def test_step_runs_to_the_end_and_refuses_unknown_blocks(capsys):
    import json
    path = str(GOLD / "TwoBlockRedundancy" / "input.rbd")
    code, out, _ = run_cli(capsys, "step", path, "--fine", "--until", "0.5")
    assert code == 0 and json.loads(out.splitlines()[-1])["time"] >= 0.5
    code, _, err = run_cli(capsys, "step", path, "--break", "NoSuchBlock")
    assert code == 2 and "NoSuchBlock" in err
    code, _, err = run_cli(capsys, "step", path, "--events", "0")
    assert code == 2


def test_step_run_n_starts_from_the_batch_streams(capsys):
    import json
    from raptor.core.engine.runner import run_simulation
    from raptor.core.fileio.rbd import load_rbd
    path = str(GOLD / "TwoBlockRedundancy" / "input.rbd")
    rbd = load_rbd(path)
    rbd.section("SYSTEM_DATA").rows[0]["NumRuns"] = "3"
    logs = run_simulation(rbd, 3)
    first_failure = next(float(ln.split("\t")[0]) for ln in logs[2] if "\tFailed" in ln)
    code, out, _ = run_cli(capsys, "step", path, "--run", "3", "--events", "2")
    recs = [json.loads(line) for line in out.splitlines()]
    assert code == 0 and [r["run"] for r in recs] == [3, 3]
    assert recs[0]["time"] == 0.0 and round(recs[1]["time"], 6) == first_failure
    code, _, err = run_cli(capsys, "step", path, "--run", "99")
    assert code == 2 and "99" in err


# --- diff ---------------------------------------------------------------------

@pytest.fixture
def diff_pair(tmp_path):
    """TwoBlockRedundancy as .rbd, and a copy saved as .rbds with one block's failure changed."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.core.fileio.rbds import save_rbds
    rbd = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    rbd.section("BLOCK_DATA").rows[0]["xPos"] = "55"
    same = tmp_path / "same.rbds"
    save_rbds(rbd, str(same))
    rbd.section("BLOCK_DATA").rows[0]["FParam1"] = "123"
    changed = tmp_path / "changed.rbds"
    save_rbds(rbd, str(changed))
    return str(GOLD / "TwoBlockRedundancy" / "input.rbd"), str(same), str(changed)


def test_diff_identical_rbd_vs_rbds(capsys, diff_pair):
    orig, same, _ = diff_pair
    code, out, _ = run_cli(capsys, "diff", orig, same, "--exit-code")
    assert code == 0 and "no differences" in out       # the layout move is ignored by default
    code, out, _ = run_cli(capsys, "diff", orig, same, "--all", "--exit-code")
    assert code == 1 and "no differences" not in out and "Elements:" in out


def test_diff_text_and_exit_codes(capsys, diff_pair):
    orig, same, changed = diff_pair
    code, out, _ = run_cli(capsys, "diff", same, changed)
    assert code == 0                                    # 0 unless --exit-code
    assert "Failure distribution" not in out and "failure" in out and "Elements:" in out
    code, _, _ = run_cli(capsys, "diff", same, changed, "--exit-code")
    assert code == 1
    code, _, _ = run_cli(capsys, "diff", orig, changed, "--exit-code")   # .rbd vs .rbds
    assert code == 1


def test_diff_json_md_csv(capsys, diff_pair):
    import csv
    import io
    import json
    _, same, changed = diff_pair
    code, out, _ = run_cli(capsys, "diff", same, changed, "-f", "json")
    data = json.loads(out)
    assert code == 0 and data["schema"] == "raptor.model-diff/1" and not data["identical"]
    assert data["a"]["path"] == same and data["ignored"] == ["derived", "layout"]
    code, out, _ = run_cli(capsys, "diff", same, changed, "-f", "md")
    assert "| Element | Field | A | B |" in out
    code, out, _ = run_cli(capsys, "diff", same, changed, "-f", "csv")
    rows = list(csv.DictReader(io.StringIO(out)))
    assert len(rows) == 1 and rows[0]["group"] == "elements" and rows[0]["field"] == "failure"
    assert list(rows[0]) == ["group", "kind", "path", "field", "category", "a", "b"]


def test_diff_only_and_ignore(capsys, diff_pair):
    _, same, changed = diff_pair
    code, out, _ = run_cli(capsys, "diff", same, changed, "--only", "links,rates", "--exit-code")
    assert code == 0 and "no differences" in out         # the change is outside those groups
    code, out, _ = run_cli(capsys, "diff", same, changed, "--ignore", "layout,failure", "--exit-code")
    assert code == 0 and "no differences" in out
    code, _, err = run_cli(capsys, "diff", same, changed, "--ignore", "bogus")
    assert code == 2 and "bogus" in err
    code, _, err = run_cli(capsys, "diff", same, changed, "--only", "nope")
    assert code == 2 and "nope" in err


def test_diff_missing_file_and_match(capsys, diff_pair, tmp_path):
    orig, same, changed = diff_pair
    code, _, err = run_cli(capsys, "diff", orig, str(tmp_path / "missing.rbds"))
    assert code == 2 and "missing.rbds" in err
    code, _, _ = run_cli(capsys, "diff", same, changed, "--match", "id", "--exit-code")
    assert code == 1


def test_run_importance_prints_the_ranking_and_carries_it_in_json(capsys):
    """`raptor run --importance` once computed the measures and then dropped
    them: `results_document` never copied them, so neither the text summary
    nor `-f json` showed a ranking."""
    lantern = str(REPO / "new_docs" / "models" / "Lantern1.rbds")
    argv = ("run", lantern, "-n", "20", "-t", "200", "-j", "1", "--seed", "3",
            "--importance", "both", "--progress", "none")
    code, out, _ = run_cli(capsys, *argv)
    assert code == 0
    assert "Vesely-Fussell" in out and "Birnbaum" in out
    code, out, _ = run_cli(capsys, *argv, "-f", "json")
    assert code == 0
    doc = json.loads(out)
    assert doc["importance"]["blocks"], doc["importance"].keys()

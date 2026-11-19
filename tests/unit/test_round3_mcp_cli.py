"""The rest of the third round of new-user testing: MCP/API inputs that were
taken silently, and command-line messages and exit codes."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

GOLD = Path(__file__).resolve().parents[1] / "golden"
TWO = str(GOLD / "TwoBlockRedundancy" / "input.rbd")


def _ws_model(spec=None):
    from raptor.api.build import build_from_spec, series
    from raptor.api.workspace import Workspace
    ws = Workspace(tempfile.mkdtemp())
    info = ws.register_model(build_from_spec(spec or series(3)), name="m")
    ws.save(info["model_id"])
    return ws, info["model_id"]


def _cli(capsys, *argv):
    from raptor.cli.main import main
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# --- MCP / API ---------------------------------------------------------------

@pytest.mark.parametrize("streams", ["abc", "1 2 3", [0] * 11, [111] * 11, [True] * 11])
def test_bad_system_streams_are_refused_before_anything_changes(streams):
    from raptor.api.build import BuildError, configure_system
    ws, mid = _ws_model()
    row = ws.get(mid).rbd.section("SYSTEM_DATA").rows[0]
    before = dict(row)
    with pytest.raises(BuildError, match="system_streams"):
        configure_system(ws, mid, num_runs=7, system_streams=streams)
    assert row == before


def test_good_system_streams_are_stored():
    from raptor.api.build import configure_system
    ws, mid = _ws_model()
    configure_system(ws, mid, system_streams="1, 2 3 4 5 6 7 8 9 10 -11")
    row = ws.get(mid).rbd.section("SYSTEM_DATA").rows[0]
    assert row["SysStreamsArray"] == "1 2 3 4 5 6 7 8 9 10 -11"


def test_a_dependency_number_below_minus_two_is_an_error():
    from raptor.api.build import update_element
    from raptor.api.validate import issues
    ws, mid = _ws_model()
    update_element(ws, mid, "Block1", {"dep_type": "RBDBlock", "dep_num": -5,
                                       "dep_idle": 100})
    found = [i for i in issues(ws.get(mid).rbd) if "-5" in i.message]
    assert found and found[0].severity == "error"


def test_an_ambiguous_name_lists_the_selectors_to_use():
    from raptor.api.build import AmbiguousName, resolve_element
    from raptor.api.edit import duplicate_elements
    ws, mid = _ws_model()
    rbd = ws.get(mid).rbd
    rbd.section("BLOCK_DATA").rows[-1]["blockName"] = "Block1"
    with pytest.raises(AmbiguousName, match=r"Block:\d+, Block:\d+"):
        resolve_element(rbd, "Block1")
    result = duplicate_elements(ws, mid, ["Block:1"])
    assert result["created"] and set(result["created"][0]) == {"kind", "id", "name", "selector"}


def test_duplicating_nothing_says_what_cannot_be_copied():
    from raptor.api.edit import duplicate_elements
    ws, mid = _ws_model()
    with pytest.raises(ValueError, match="cannot be copied"):
        duplicate_elements(ws, mid, ["Start"])


def test_an_inserted_assembly_keeps_free_names_and_lists_renames():
    from raptor.api.build import build_from_spec, series
    from raptor.api.library.assembly import assembly_from_elements, insert_assembly
    source = build_from_spec(series(2))
    part = assembly_from_elements(source, ["Block1"], name="one")
    target = build_from_spec({"blocks": [{"name": "Other"}],
                              "nodes": [{"name": "Start", "type": 1},
                                        {"name": "End", "type": 3}],
                              "links": [{"from": "Start", "to": "Other"},
                                        {"from": "Other", "to": "End"}]})
    first = insert_assembly(target, part)
    assert first["renamed"] == {}
    second = insert_assembly(target, part)
    assert list(second["renamed"]) == ["Block1"]
    assert second["renamed"]["Block1"] != "Block1"


def test_annual_availability_below_must_be_a_share():
    from raptor.api.insight import drivers
    with pytest.raises(ValueError, match="0 to 1"):
        drivers.periods(None, "run", 8760.0, below=1.5)


def test_fit_from_experience_refuses_a_repeated_share():
    from raptor.mcp.tools_optimise import fit_from_experience
    fn = getattr(fit_from_experience, "__wrapped__", fit_from_experience)
    with pytest.raises(ValueError, match="once"):
        fn("weibull", [{"probability": 0.5, "age": 10},
                       {"probability": 0.5, "age": 10}])


# --- command line --------------------------------------------------------------

def test_jobs_beyond_the_cores_are_capped(capsys, monkeypatch):
    import os

    from raptor.cli.common import job_count
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    assert job_count("2") == 2
    assert job_count("64") == 4
    assert "4 cores" in capsys.readouterr().err


def test_a_run_setting_the_rules_refuse_is_a_usage_error(capsys):
    code, _out, err = _cli(capsys, "run", TWO, "-t", "0", "-f", "none")
    assert code == 2
    assert "traceback" not in err
    assert "before the end" not in err      # not repeated in other words
    with pytest.raises(SystemExit) as stop:
        _cli(capsys, "run", TWO, "-t", "nan")
    assert stop.value.code == 2 and "finite" in capsys.readouterr().err


def test_json_elsewhere_points_at_the_format_option(capsys):
    code, _out, err = _cli(capsys, "validate", "--json", TWO)
    assert code == 2 and "use -f json" in err
    code, _out, err = _cli(capsys, "set", "--json", TWO)
    assert code == 2 and "no JSON output" in err


def test_a_global_option_value_is_not_the_command(capsys):
    _code, _out, err = _cli(capsys, "--color", "never", "validate", TWO)
    assert "unknown command" not in err


def test_get_prints_what_set_takes(capsys):
    code, out, _err = _cli(capsys, "get", "-f", "json", TWO, "block:3.fail_dist",
                           "block:3.fail_params", "node:1.type",
                           "system.term_condition", "system.capacity_analysis")
    assert code == 0
    values = json.loads(out)
    assert values["block:3.fail_dist"] == "uniform_real"
    assert values["block:3.fail_params"] == [0.0, 1.0, 0.0]
    assert values["node:1.type"] == "start"
    assert values["system.term_condition"] == 100.0
    assert values["system.capacity_analysis"] == 0


def test_convert_to_a_mismatched_extension_is_refused_up_front(capsys, tmp_path):
    code, _out, err = _cli(capsys, "convert", TWO, str(tmp_path / "o.dat"), "--to", "rbds")
    assert code == 2 and "o.rbds" in err


def test_a_folder_is_not_a_missing_model(capsys, tmp_path):
    code, _out, err = _cli(capsys, "run", str(tmp_path))
    assert code == 2 and "is a folder" in err


def test_bad_file_messages_are_plain(tmp_path):
    from raptor.core.fileio.rbd import parse_rbd
    from raptor.core.fileio.rbds import parse_rbds
    with pytest.raises(ValueError, match='no "format"'):
        parse_rbds("{}")
    with pytest.raises(ValueError, match="binary"):
        parse_rbd("\x002\x14\x8d\n")


def test_change_file_scenarios_need_a_list_of_changes(capsys, tmp_path):
    bad = tmp_path / "cf.json"
    bad.write_text(json.dumps([{"name": "B", "changes": {"block:3.fail_params": "1,2"}}]))
    code, _out, err = _cli(capsys, "whatif", TWO, "--change-file", str(bad))
    assert code == 2 and "non-empty list" in err

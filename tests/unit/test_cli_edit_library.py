"""`raptor edit` and `raptor library` on the command line: every action,
text and JSON output, writing back with --in-place, and the refusals.

Both commands had no command-line tests at all (CI coverage of
`cmd_edit.py` 33 %, `cmd_library.py` 42 %), though their API halves were
tested."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from raptor.api import build
from raptor.cli import exitcodes as X
from raptor.cli.main import main
from raptor.core.fileio.rbds import load_rbds, save_rbds

GOLD = Path(__file__).resolve().parents[1] / "golden"

SPEC = {
    "blocks": [{"name": "A", "fail_dist": 4, "fail_params": [100, 0, 0],
                "repair_dist": 4, "repair_params": [5, 0, 0]},
               {"name": "B", "fail_dist": 4, "fail_params": [300, 0, 0],
                "repair_dist": 4, "repair_params": [9, 0, 0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "B"}, {"from": "B", "to": "End"}],
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


def _blocks(path):
    return {r["blockName"]: r for r in load_rbds(path).sections["BLOCK_DATA"].rows
            if r.get("ID") not in (None, "0")}


def _fail_mean(path, name):
    return float(_blocks(path)[name]["FParam1"])


# ---- raptor edit ------------------------------------------------------------

def test_edit_with_no_action_says_what_to_use(capsys, model):
    code, _, err = run_cli(capsys, "edit", model)
    assert code == X.USAGE and "duplicate|paste-data|out-of-service" in err


def test_duplicate_writes_only_with_in_place(capsys, model):
    code, out, _ = run_cli(capsys, "edit", model, "duplicate", "A", "--copies", "2")
    assert code == X.OK and "Duplicated 2" in out and "--in-place" in out
    assert len(_blocks(model)) == 2                       # file untouched
    code, out, _ = run_cli(capsys, "edit", model, "duplicate", "A", "--in-place", "-f", "json")
    assert code == X.OK and json.loads(out)["count"] == 1
    assert len(_blocks(model)) == 3


@pytest.mark.parametrize("argv, words", [
    (["duplicate", "A", "--copies", "0"], "--copies must be at least 1"),
    (["duplicate", "NoSuchBlock"], "NoSuchBlock"),
])
def test_duplicate_refusals(capsys, model, argv, words):
    code, _, err = run_cli(capsys, "edit", model, *argv)
    assert code == X.USAGE and words in err


def test_paste_data_copies_failure_data(capsys, model):
    code, out, _ = run_cli(capsys, "edit", model, "paste-data", "--source", "B",
                           "--targets", "A", "--groups", "Failure", "--in-place")
    assert code == X.OK and "pasted failure onto 1 element(s)" in out.lower()
    assert _fail_mean(model, "A") == _fail_mean(model, "B") == 300
    code, out, _ = run_cli(capsys, "edit", model, "paste-data", "--source", "B",
                           "--targets", "A", "-f", "json")
    assert code == X.OK and json.loads(out)["blocks"] == 1


def test_paste_data_copies_tags_and_comment(capsys, model):
    code, out, _ = run_cli(capsys, "edit", model, "paste-data", "--source", "B",
                           "--targets", "A", "--groups", "Tags,Comment",
                           "--tag-mode", "replace", "--comment-mode", "append",
                           "-f", "json")
    assert code == X.OK and json.loads(out)["blocks"] == 1
    with pytest.raises(SystemExit):        # argparse refuses an unknown mode
        run_cli(capsys, "edit", model, "paste-data", "--source", "B",
                "--targets", "A", "--groups", "Tags", "--tag-mode", "merge")


def test_paste_data_refuses_an_unknown_group(capsys, model):
    """It used to copy nothing, print "Copied nonsense data" and exit 0."""
    code, _, err = run_cli(capsys, "edit", model, "paste-data", "--source", "B",
                           "--targets", "A", "--groups", "Nonsense")
    assert code == X.USAGE and "unknown data group(s) 'Nonsense'" in err and "Failure" in err


def test_out_of_service_round_trip(capsys, model):
    code, out, _ = run_cli(capsys, "edit", model, "out-of-service", "A", "--in-place")
    assert code == X.OK and "block A: cut" in out and "Out of service: A (cut)" in out
    assert load_rbds(model).out_of_service
    code, out, _ = run_cli(capsys, "edit", model, "out-of-service", "-f", "json")
    data = json.loads(out)
    assert data["changed"] == [] and data["out_of_service"][0]["state"] == "cut"
    code, out, _ = run_cli(capsys, "edit", model, "out-of-service", "A", "--state", "linked")
    assert "(in the workspace; pass --in-place)" in out
    assert load_rbds(model).out_of_service == {"block:" + _blocks(model)["A"]["ID"]: "cut"}
    code, out, _ = run_cli(capsys, "edit", model, "out-of-service", "A",
                           "--state", "in-service", "--in-place")
    assert "Out of service: nothing" in out and not load_rbds(model).out_of_service


def test_out_of_service_runs_say_so(capsys, model):
    run_cli(capsys, "edit", model, "out-of-service", "A", "--in-place")
    code, out, err = run_cli(capsys, "run", model, "-n", "2")
    assert code == X.OK, err
    assert "Out of service: A (cut)" in out + err


@pytest.mark.parametrize("target, words", [
    ("Start", "only a block, event or hierarchy"),
    ("NoSuchBlock", "NoSuchBlock"),
])
def test_out_of_service_refusals(capsys, model, target, words):
    code, _, err = run_cli(capsys, "edit", model, "out-of-service", target)
    assert code == X.USAGE and words in err


def test_out_of_service_refuses_to_write_a_legacy_rbd(capsys, tmp_path):
    path = tmp_path / "h.rbd"
    shutil.copy(GOLD / "HierarchyExample" / "input.rbd", path)
    code, _, err = run_cli(capsys, "edit", str(path), "out-of-service", "Attitude", "--in-place")
    assert code == X.USAGE and "save the model as .rbds first" in err


def test_out_of_service_takes_a_hierarchy_by_its_name(capsys, tmp_path):
    path = tmp_path / "h.rbds"
    assert main(["convert", str(GOLD / "HierarchyExample" / "input.rbd"), str(path)]) == X.OK
    capsys.readouterr()
    # "900path" is also the name of the hierarchy's own In node
    code, out, _ = run_cli(capsys, "edit", str(path), "out-of-service", "900path",
                           "--state", "linked", "--in-place")
    assert code == X.OK and "hier 900path: linked" in out


# ---- raptor library ---------------------------------------------------------

@pytest.fixture
def lib(tmp_path):
    return str(tmp_path / "parts.rbdlib")


def _add(capsys, lib, model, *extra):
    code, _, err = run_cli(capsys, "library", "add", lib, model, "--block", "B",
                           "--name", "Pump", "--category", "Pumps",
                           "--source", "handbook p.12", "--basis", "handbook", *extra)
    assert code == X.OK, err
    return err


def test_library_with_no_action_lists_them(capsys):
    code, _, err = run_cli(capsys, "library")
    assert code == X.USAGE and "list|show|import|add|insert|apply|sync|detach" in err


def test_add_list_and_show(capsys, lib, model):
    assert "added" in _add(capsys, lib, model)
    code, out, _ = run_cli(capsys, "library", "list", lib)
    assert code == X.OK and "1 part(s)" in out and "Pump" in out and "handbook p.12" in out
    code, out, _ = run_cli(capsys, "library", "list", lib, "-f", "json")
    parts = json.loads(out)["parts"]
    assert parts[0]["name"] == "Pump" and parts[0]["basis"] == "handbook"
    pid = parts[0]["id"]
    code, out, _ = run_cli(capsys, "library", "show", lib, pid)
    assert code == X.OK and "Pump" in out and "basis: handbook" in out
    code, out, _ = run_cli(capsys, "library", "show", lib, pid, "-f", "json")
    assert json.loads(out)["name"] == "Pump"


def test_basis_judgement_is_read_as_experience(capsys, lib, model):
    run_cli(capsys, "library", "add", lib, model, "--block", "A", "--basis", "judgement")
    _, out, _ = run_cli(capsys, "library", "list", lib, "-f", "json")
    assert json.loads(out)["parts"][0]["basis"] == "experience"


@pytest.mark.parametrize("argv, words", [
    (["list", "NOFILE"], "no such library"),
    (["show", "LIB", "nope"], "no part 'nope'"),
    (["apply", "MODEL", "--library", "LIB", "--part", "nope", "--blocks", "A"], "no part 'nope'"),
    (["insert", "MODEL", "--library", "LIB", "--part", "nope"], "no part 'nope'"),
    (["add", "LIB", "MODEL", "--block", "Start"], "is not a block"),
    (["add", "LIB", "MODEL", "--block", "9", "--kind", "annotation"], "annotation '9' not found"),
])
def test_library_refusals(capsys, lib, model, tmp_path, argv, words):
    _add(capsys, lib, model)
    argv = [a.replace("NOFILE", str(tmp_path / "none.rbdlib")).replace("LIB", lib)
            .replace("MODEL", model) for a in argv]
    code, _, err = run_cli(capsys, "library", *argv)
    assert code == X.USAGE and words in err


def test_a_broken_library_file_is_a_usage_error(capsys, tmp_path):
    bad = tmp_path / "bad.rbdlib"
    bad.write_text("{ not json")
    code, _, err = run_cli(capsys, "library", "list", str(bad))
    assert code == X.USAGE and err


def test_apply_sync_and_detach(capsys, lib, model):
    _add(capsys, lib, model)
    pid = json.loads(run_cli(capsys, "library", "list", lib, "-f", "json")[1])["parts"][0]["id"]
    code, _, err = run_cli(capsys, "library", "apply", model, "--library", lib,
                           "--part", pid, "--blocks", "A")
    assert code == X.OK and "pass --in-place" in err
    assert _fail_mean(model, "A") == 100                    # not written
    code, _, err = run_cli(capsys, "library", "apply", model, "--library", lib,
                           "--part", pid, "--blocks", "A", "--in-place")
    assert code == X.OK and "linked 1 block(s)" in err
    assert _fail_mean(model, "A") == 300

    code, out, _ = run_cli(capsys, "library", "sync", model, "--library", lib)
    assert code == X.OK and "Every linked block matches its part type." in out

    # the part changes in the library: sync reports it, then applies it
    data = json.loads(Path(lib).read_text())
    part = data["parts"][0]
    part["fields"]["fail_params"] = [450.0, 0.0]
    Path(lib).write_text(json.dumps(data))
    code, out, _ = run_cli(capsys, "library", "sync", model, "--library", lib)
    assert code == X.OK and "fail_params" in out and "450" in out and "Dry run" in out
    code, out, _ = run_cli(capsys, "library", "sync", model, "--library", lib, "-f", "json")
    assert json.loads(out)["pending"]
    code, _, err = run_cli(capsys, "library", "sync", model, "--library", lib, "--apply")
    assert "pass --in-place with --apply" in err and _fail_mean(model, "A") == 300
    code, out, _ = run_cli(capsys, "library", "sync", model, "--library", lib,
                           "--apply", "--in-place")
    assert code == X.OK and "Applied 1 block update(s)" in out
    assert _fail_mean(model, "A") == 450

    code, _, err = run_cli(capsys, "library", "detach", model, "--block", "A", "--in-place")
    assert code == X.OK and not err
    code, _, err = run_cli(capsys, "library", "detach", model, "--block", "A", "--in-place")
    assert "had no part link" in err


def test_assembly_add_and_insert(capsys, lib, model):
    code, _, err = run_cli(capsys, "library", "add", lib, model, "--assembly", "A,B",
                           "--name", "Pair")
    assert code == X.OK and "added assembly" in err
    code, out, _ = run_cli(capsys, "library", "insert", model, "--library", lib,
                           "--part", "Pair")
    assert code == X.OK and "inserted Pair: " in out
    assert len(_blocks(model)) == 2                          # workspace only
    code, out, _ = run_cli(capsys, "library", "insert", model, "--library", lib,
                           "--part", "Pair", "--at", "500,40", "--in-place", "-f", "json")
    assert code == X.OK and json.loads(out)["count"] >= 2
    assert len(_blocks(model)) == 4
    code, _, err = run_cli(capsys, "library", "insert", model, "--library", lib,
                           "--part", "Pair", "--at", "nonsense")
    assert code == X.USAGE and "--at takes X,Y" in err
    code, _, err = run_cli(capsys, "library", "add", lib, model, "--assembly", "Nope")
    assert code == X.USAGE and err


def test_import_a_legacy_library(capsys, tmp_path):
    out_lib = str(tmp_path / "imported.rbdlib")
    src = str(GOLD / "Phasing3" / "legacy" / "lib_Phasing3.rbl")
    code, _, err = run_cli(capsys, "library", "import", out_lib, src, "--name", "Old")
    assert code == X.OK and "wrote" in err
    code, _, err = run_cli(capsys, "library", "import", out_lib, src)
    assert code == X.USAGE and "pass --force" in err
    code, _, err = run_cli(capsys, "library", "import", out_lib, src, "--force")
    assert code == X.OK
    code, _, err = run_cli(capsys, "library", "import", str(tmp_path / "x.rbdlib"),
                           str(tmp_path / "missing.rbl"))
    assert code == X.USAGE and err

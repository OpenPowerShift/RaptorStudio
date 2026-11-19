"""Fourth round of new-user testing: command line and MCP round trips
(get -> set, whatif --save -> --change-file), study time limits on the
command line, parts by name, and plain messages."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ESPRESSO = str(ROOT / "new_docs" / "models" / "Espresso1.rbds")


def _cli(capsys, *argv):
    from raptor.cli.main import main
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_get_prints_parameter_lists_set_takes_back(capsys, tmp_path):
    code, out, _ = _cli(capsys, "get", ESPRESSO, "block:Pump_A.fail_params")
    assert code == 0 and out.strip() == "400,0,0"
    code, out, _ = _cli(capsys, "get", ESPRESSO, "block:Pump_A.fail_params",
                        "block:Pump_A.fail_dist")
    line = out.splitlines()[0]
    assert line == "block:Pump_A.fail_params=400,0,0"
    code, _, err = _cli(capsys, "set", ESPRESSO, line, "-o", str(tmp_path / "a.rbds"))
    assert code == 0, err
    # and the JSON form `get -f json` prints
    code, _, err = _cli(capsys, "set", ESPRESSO,
                        "block:Pump_A.fail_params=[400.0, 0.0, 0.0]",
                        "-o", str(tmp_path / "b.rbds"))
    assert code == 0, err


def test_parse_value_takes_the_bracketed_list():
    from raptor.api.paths import parse_value
    assert parse_value("[400.0, 0.0, 0.0]") == [400.0, 0.0, 0.0]
    assert parse_value("[5]") == [5]
    assert parse_value("400,0,0") == [400, 0, 0]


def test_whatif_reads_back_what_it_saved(capsys, tmp_path):
    saved = tmp_path / "sc.json"
    code, _, _ = _cli(capsys, "whatif", ESPRESSO, "--change",
                      "block:Pump_A.fail_params=800,0,0", "--name", "Better",
                      "--save", str(saved))
    assert code == 0 and "scenarios" in json.loads(saved.read_text())
    code, out, err = _cli(capsys, "-q", "whatif", ESPRESSO, "--change-file",
                          str(saved), "-n", "5")
    assert code == 0, err
    assert "Better" in out


def test_whatif_with_nothing_to_change_is_refused(capsys):
    code, _, err = _cli(capsys, "whatif", ESPRESSO, "-n", "5")
    assert code == 2 and "nothing to compare" in err


@pytest.mark.parametrize("command", ["sensitivity", "recommend", "whatif"])
def test_studies_take_a_time_limit(capsys, command):
    from raptor.cli.main import build_parser
    args = build_parser().parse_args([command, ESPRESSO, "--timeout", "90s"])
    from raptor.cli.cmd_run import study_timeout
    assert study_timeout(args) == 90.0


def test_a_zero_or_bad_duration_is_refused(capsys):
    code, _, err = _cli(capsys, "sensitivity", ESPRESSO, "--timeout", "0")
    assert code == 2 and "above 0" in err


def test_a_file_that_is_not_json_says_so_plainly(tmp_path):
    from raptor.core.fileio.rbds import parse_rbds
    with pytest.raises(ValueError, match="not JSON"):
        parse_rbds("garbage")


def test_the_airliner_example_can_plot_availability():
    """A 5 h mission with a 10 h availability-plot interval was an error
    as soon as the plot was ticked."""
    from raptor.core.fileio.rbds import load_rbds
    rbd = load_rbds(str(ROOT / "new_docs" / "models" / "AirlinerMission.rbds"))
    assert rbd.experiment["recording"]["ao_interval"] <= rbd.experiment["term_condition"]


@pytest.fixture()
def view():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from raptor.core.edit.new_model import build_new_model
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(build_new_model())
    return v


def test_exports_leave_the_selection_outline_out(view):
    bid = next(iter(view.blocks))
    item = view._block_items[bid]
    item.setSelected(True)
    with view.grid_hidden():
        assert not item.isSelected()
    assert item.isSelected()


def test_a_library_note_is_placed_clear_of_the_blocks(view):
    from types import SimpleNamespace as NS
    bid = next(iter(view.blocks))
    block = view._block_items[bid].sceneBoundingRect()
    view.centerOn(block.center())
    aid = view.add_annotation_from_part(NS(fields={"kind": "text", "text": "hi"}))
    assert not view._anno_items[aid].sceneBoundingRect().intersects(block)

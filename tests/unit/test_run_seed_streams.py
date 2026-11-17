"""Run seed and per-block random streams (#292)."""
from __future__ import annotations

import json
import time

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core import run_experiment as rx
from raptor.core.edit import own_streams as osx

MODEL = "new_docs/models/Espresso1.rbds"


# -- the seed in the run settings -------------------------------------------------

def test_the_default_is_raptor_7s_fixed_table():
    exp = rx.defaults()
    assert exp["seed_mode"] == "fixed" and rx.resolve_seed(exp) == 0
    assert rx.seed_words(exp) == "Raptor 7 seeds"


def test_a_number_is_used_and_a_new_seed_is_drawn_each_time():
    assert rx.resolve_seed({"seed_mode": "number", "seed": 1234}) == 1234
    new = {"seed_mode": "new"}
    draws = {rx.resolve_seed(new) for _ in range(5)}
    assert len(draws) > 1 and all(0 < d <= rx.SEED_MAX for d in draws)


def test_hand_edited_seeds_are_tidied():
    exp = rx.normalise({"seed_mode": " NUMBER ", "seed": "-5"})
    assert exp["seed_mode"] == "number" and exp["seed"] == 0
    assert rx.normalise({"seed_mode": "dice"})["seed_mode"] == "fixed"
    assert rx.normalise({"seed": 2**40})["seed"] == rx.SEED_MAX


def test_a_fixed_seed_is_not_written_to_the_file(tmp_path):
    from raptor.core.fileio.rbds import load_rbds, save_rbds
    rbd = load_rbds(MODEL)
    save_rbds(rbd, str(tmp_path / "plain.rbds"))
    doc = json.loads((tmp_path / "plain.rbds").read_text())
    assert "seed_mode" not in doc["experiment"]
    assert "seed" not in doc["experiment"]
    rbd.experiment = rx.normalise({**rbd.experiment, "seed_mode": "number",
                                   "seed": 99})
    save_rbds(rbd, str(tmp_path / "seeded.rbds"))
    back = load_rbds(str(tmp_path / "seeded.rbds"))
    assert (back.experiment["seed_mode"], back.experiment["seed"]) == (
        "number", 99)


def test_the_manifest_records_the_seed_used():
    m = rx.manifest({"seed_mode": "new"}, model_hash="h", model_name="m",
                    seed=4321)
    assert m["seed"] == 4321 and "Number 4321" in m["seed_strategy"]
    fixed = rx.manifest({}, model_hash="h", model_name="m")
    assert fixed["seed"] == 0 and "Raptor 7" in fixed["seed_strategy"]


def test_the_api_uses_a_saved_number_seed(tmp_path):
    from raptor.api.run import run as run_model
    from raptor.api.workspace import Workspace
    from raptor.core.fileio.rbds import load_rbds
    ws = Workspace(str(tmp_path))
    rbd = load_rbds(MODEL)
    rbd.experiment = rx.normalise({**rbd.experiment, "seed_mode": "number",
                                   "seed": 42})
    mid = ws.register_model(rbd, name="seeded")["model_id"]
    assert run_model(ws, mid, num_runs=3).seed == 42
    rbd2 = load_rbds(MODEL)
    rbd2.experiment = rx.normalise({**rbd2.experiment, "seed_mode": "new"})
    mid2 = ws.register_model(rbd2, name="new")["model_id"]
    assert run_model(ws, mid2, num_runs=3).seed == 0, (
        "New is a GUI setting: a study's variants must stay paired")


# -- own streams ------------------------------------------------------------------

def test_there_is_room_for_49_blocks():
    """110 seed-table entries, 11 used by System streams A..K, two a block."""
    assert osx.capacity(osx.DEFAULT_SYS_STREAMS) == 49


def test_free_streams_avoid_the_system_streams_and_ones_in_use():
    free = osx.free_streams([1, -2, 203], osx.DEFAULT_SYS_STREAMS)
    # 1 and 2 are used (an antithetic stream is its twin's mirror); 203 is
    # System stream C, seed entry 9, already a System entry.
    assert 1 not in free and 2 not in free
    assert not set(free) & set(osx.DEFAULT_SYS_STREAMS)
    assert free[0] == 3


def test_plan_keeps_unique_streams_and_reassigns_shared_ones():
    blocks = [(1, 203, 203), (2, 3, 4), (3, 3, 5), (4, 11, 12)]
    plan, unserved = osx.plan_all(blocks, [], osx.DEFAULT_SYS_STREAMS)
    assert unserved == 0
    assert 4 not in plan                      # 11/12: its own already
    assert set(plan) == {1, 2, 3}             # 2 and 3 share stream 3
    streams = [s for pair in plan.values() for s in pair] + [11, 12]
    assert len(streams) == len(set(streams))


def test_after_49_blocks_the_pairs_roll_over():
    blocks = [(i, 203, 203) for i in range(1, 61)]
    plan, reused = osx.plan_all(blocks, [], osx.DEFAULT_SYS_STREAMS)
    assert len(plan) == 60 and reused == 11
    pairs = osx.stream_pairs(osx.DEFAULT_SYS_STREAMS)
    assert [plan[i] for i in range(1, 50)] == pairs
    assert [plan[i] for i in range(50, 61)] == pairs[:11]
    for fail, repair in plan.values():
        assert fail != repair


def test_reuse_picks_the_least_used_pair():
    pairs = osx.stream_pairs(osx.DEFAULT_SYS_STREAMS)
    in_use = [s for pair in pairs for s in pair] + list(pairs[0])
    chosen, reused = osx.assign(1, in_use, osx.DEFAULT_SYS_STREAMS)
    assert chosen == [pairs[1]] and reused == 1


def test_cli_seed_takes_a_number_or_new():
    import argparse

    from raptor.cli.common import seed_arg
    assert seed_arg("1234") == 1234
    assert 0 < seed_arg("new") <= rx.SEED_MAX
    with pytest.raises(argparse.ArgumentTypeError):
        seed_arg("-1")
    with pytest.raises(argparse.ArgumentTypeError):
        seed_arg("dice")


def test_the_cli_reports_the_seed_it_used(capsys):
    from raptor.cli.main import main
    assert main(["run", MODEL, "-n", "3", "--seed", "77"]) == 0
    assert "sequential streams, seed 77" in capsys.readouterr().out


def test_the_reproduce_hint_names_the_seed_everywhere():
    from raptor.api.run import streams_note
    note = streams_note({"streams": "parallel", "seed": 99})
    assert "--seed 99" in note and "seed=99" in note
    assert "Number 99" in note


# -- the window -------------------------------------------------------------------

def _wait(pred, seconds=90):
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while not pred() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert pred()


@pytest.fixture
def win(monkeypatch):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui import cores
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    from raptor.gui.ui.own_streams_mixin import NEW_BLOCKS_KEY
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(cores, "chosen", lambda: 1)
    settings.set_value(NEW_BLOCKS_KEY, False)
    w = MainWindow()
    w._load_into_app(load_rbds(MODEL), MODEL)
    w._live_sim_action.setChecked(False)
    w._set_exp_field("trials", 4)
    yield w
    settings.set_value(NEW_BLOCKS_KEY, False)
    w._dirty = False
    w.close()


def _run(w):
    w._dirty = False
    w._on_run()
    _wait(lambda: not w._run_in_progress)
    return w._results._manifest, repr(w._results._event_logs)


@pytest.mark.gui
def test_the_seed_reaches_the_run_and_is_recorded(win):
    fixed_manifest, fixed_logs = _run(win)
    assert fixed_manifest["seed"] == 0
    win._set_exp_fields({"seed_mode": "number", "seed": 1234})
    seeded_manifest, seeded_logs = _run(win)
    assert seeded_manifest["seed"] == 1234
    assert seeded_logs != fixed_logs, "a different seed, different draws"
    _again, again_logs = _run(win)
    assert again_logs == seeded_logs, "the same seed repeats the run"


@pytest.mark.gui
@pytest.mark.parametrize("cores", [1, 4])
def test_a_gui_run_is_repeated_exactly_by_the_api(win, monkeypatch, cores,
                                                  tmp_path):
    """Seed N on C cores in the GUI == seed=N, workers=min(C, 2) in the
    API (CLI `--seed N -j ...`, MCP seed/workers): the same numbers."""
    from raptor.api.run import run as api_run
    from raptor.api.workspace import Workspace
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui import cores as gui_cores
    monkeypatch.setattr(gui_cores, "chosen", lambda: cores)
    win._set_exp_fields({"trials": 20, "seed_mode": "number", "seed": 1234})
    _run(win)
    gui = [ln for ln in win._results._final_lines if "Availability " in ln]
    ws = Workspace(str(tmp_path))
    mid = ws.register_model(load_rbds(MODEL), name="m")["model_id"]
    result = api_run(ws, mid, num_runs=20, workers=min(cores, 2), seed=1234)
    api = [ln for ln in result.final_results.splitlines()
           if "Availability " in ln]
    assert gui and gui == api


@pytest.mark.gui
def test_a_new_seed_is_shown_so_the_run_can_be_repeated(win):
    win._set_exp_field("seed_mode", "new")
    manifest, _logs = _run(win)
    seed = manifest["seed"]
    assert seed > 0 and win._run_panel.last_seed == seed
    panel = win._run_panel
    panel._seed_mode.setCurrentIndex(panel._seed_mode.findData("number"))
    exp = win._current_experiment()
    assert (exp["seed_mode"], exp["seed"]) == ("number", seed)


@pytest.mark.gui
def test_the_seed_is_named_when_it_is_not_fixed(win):
    panel = win._run_panel
    assert "seed" not in panel.more_options_summary()
    win._set_exp_fields({"seed_mode": "number", "seed": 77})
    assert panel.more_options_summary().endswith("· seed 77")
    win._update_readiness()
    assert "seed 77" in panel._readiness.text()


@pytest.mark.gui
def test_studies_follow_the_seed(win):
    from raptor.gui.ui.analysis_dialog import study_seed
    assert study_seed(win) is None
    win._set_exp_fields({"seed_mode": "number", "seed": 5})
    assert study_seed(win) == 5


@pytest.mark.gui
def test_all_blocks_gives_every_block_a_pair_in_one_undo_step(
        win, monkeypatch):
    from raptor.gui.ui import dialogs
    monkeypatch.setattr(dialogs, "question_box", lambda *a, **k: True)
    view = win._diagram_dock.view
    before = {bid: (b.fail_stream, b.repair_stream)
              for bid, b in view.blocks.items()}
    win._give_blocks_own_streams()
    after = [(b.fail_stream, b.repair_stream) for b in view.blocks.values()]
    flat = [s for pair in after for s in pair]
    assert all(0 < s <= osx.MAX_INDEPENDENT for s in flat)
    assert len(flat) == len(set(flat))
    view._stack.undo()
    assert {bid: (b.fail_stream, b.repair_stream)
            for bid, b in view.blocks.items()} == before


@pytest.mark.gui
def test_new_blocks_get_their_own_streams_while_the_toggle_is_on(win):
    from raptor.gui.ui import settings
    from raptor.gui.ui.own_streams_mixin import NEW_BLOCKS_KEY
    view = win._diagram_dock.view
    win._own_streams_new_action.setChecked(True)
    assert settings.get_bool(NEW_BLOCKS_KEY, False) is True
    bid = view.add_block("Fresh", 400.0, 400.0)
    block = view.blocks[bid]
    assert osx.has_own_streams(block.fail_stream, block.repair_stream)
    win._own_streams_new_action.setChecked(False)
    bid2 = view.add_block("Plain", 500.0, 400.0)
    assert view.blocks[bid2].fail_stream == 203


@pytest.mark.gui
def test_the_model_tab_has_one_random_streams_menu(win):
    menu = win._streams_menu_action.menu()
    assert win._streams_menu_action.text() == "Random Streams"
    assert menu.actions() == [win._own_streams_action,
                              win._own_streams_new_action]
    assert win._own_streams_new_action.isCheckable()

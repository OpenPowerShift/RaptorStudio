"""The GUI reports the `.rbds` schema check: an error stops the open with a
message naming the field, and warnings appear in the Issues list."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SAMPLE = Path(__file__).resolve().parents[2] / "new_docs" / "models" / "HospitalPower.rbds"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, monkeypatch):
    from raptor.gui.ui import main_window as mw
    shown: list = []
    monkeypatch.setattr(mw.msgbox, "critical", lambda parent, title, text, *a, **k: shown.append(text))
    w = mw.MainWindow()
    w._confirm_discard_unsaved = lambda **k: True
    w.shown = shown
    yield w
    w._dirty = False
    w.close()


def _write(tmp_path, name, change):
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    change(doc)
    path = tmp_path / name
    path.write_text(json.dumps(doc))
    return str(path)


def test_a_schema_error_stops_the_open_and_names_the_field(win, tmp_path):
    path = _write(tmp_path, "bad.rbds",
                  lambda d: d["elements"]["blocks"][1].update({"pm_freq": "abc"}))
    win._open_path(path)
    assert win.shown and 'blocks[1] "Fuel_Day_Tank".pm_freq: must be a number' in win.shown[-1]


def test_schema_warnings_are_in_the_issues_list(win, tmp_path):
    path = _write(tmp_path, "warn.rbds",
                  lambda d: d["elements"]["blocks"][0].update({"fial_dist": 15}))
    win._open_path(path)
    assert not win.shown
    win._validation_issues()
    warnings = win._validation_cache[2]
    assert any("fial_dist" in w and "did you mean `fail_dist`" in w for w in warnings)


def _bad_and_newer(tmp_path):
    bad = _write(tmp_path, "bad.rbds",
                 lambda d: d["elements"]["blocks"][1].update({"pm_freq": "abc"}))
    newer = _write(tmp_path, "newer.rbds", lambda d: d.update(
        {"version": 99, "meta": {"generator": "RAPTOR Studio 9.9.9"}}))
    return bad, newer


def test_the_gui_cli_and_mcp_open_through_one_loader_and_say_the_same(win, tmp_path, monkeypatch, capsys):
    """Every front end reads a model with `load_model_file`, so a bad file or
    one from a newer version gets the same message in each."""
    import raptor.core.fileio.model_file as model_file
    from raptor.cli.main import main
    from raptor.gui.ui.diff_dialog import load_model as diff_load
    from raptor.mcp.server import mcp
    from raptor.mcp.tools_model import open_model

    calls: list[str] = []
    real = model_file.load_model_file

    def spy(path):
        calls.append(str(path))
        return real(path)

    monkeypatch.setattr(model_file, "load_model_file", spy)
    bad, newer = _bad_and_newer(tmp_path)
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    for path, needle in ((bad, 'pm_freq: must be a number'),
                         (newer, "saved by RAPTOR Studio 9.9.9")):
        calls.clear()
        win._open_path(path)
        gui = win.shown[-1]
        main(["validate", path])
        cli = capsys.readouterr().out
        mcp_msg = open_model(Path(path).name).structured_content["error"]["message"]
        with pytest.raises(ValueError) as err:
            diff_load(path)
        for text in (gui, cli, mcp_msg, str(err.value)):
            assert needle in text, text
        assert len(calls) >= 4 and all(c.endswith(Path(path).name) for c in calls)

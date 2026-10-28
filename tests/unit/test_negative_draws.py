"""The negative-time warning against a real capture of `NegDrawProbe`
(a Normal(0.5, 1) failure time, so some draws are negative; real clamps them to 0.000001)."""
from __future__ import annotations

from pathlib import Path

from raptor.core.engine import negative_draws as neg
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden" / "NegDrawProbe"


def test_count_equals_the_clamped_values_in_the_real_sd_file():
    real = (GOLD / "simDetails_NegDrawProbe.txt").read_bytes().decode().split("\r\n")
    clamped = sum(1 for line in real if line.endswith("  0.000001"))
    meta: dict = {}
    run_simulation(load_rbd(str(GOLD / "input.rbd")), metadata_sink=meta)
    assert clamped == 11
    assert sum(meta["negative_draws"]) == clamped
    assert len(meta["negative_draws"]) == 3          # one count per run


def test_the_warning_changes_no_number():
    rbd = load_rbd(str(GOLD / "input.rbd"))
    a = run_simulation(rbd, metadata_sink={})
    b = run_simulation(rbd)
    assert [list(x) for x in a] == [list(x) for x in b]


def test_no_negative_draws_means_no_note():
    rbd = load_rbd(str(GOLD.parent / "TwoBlockRedundancy" / "input.rbd"))
    meta: dict = {}
    run_simulation(rbd, num_runs=2, metadata_sink=meta)
    assert sum(meta["negative_draws"]) == 0 and neg.note(meta["negative_draws"]) is None


def test_the_note_and_the_ignore_flag():
    assert "11 negative time(s)" in neg.note([4, 0, 7])
    assert "in 2 of 3 run(s)" in neg.note([4, 0, 7])
    rbd = load_rbd(str(GOLD / "input.rbd"))
    assert neg.ignoring(rbd) is False
    rbd.section("SYSTEM_DATA").rows[0]["ShowNegErrMsg"] = "1"
    assert neg.ignoring(rbd) is True


def test_api_run_warns_unless_ignored(tmp_path):
    from raptor.api.run import run
    from raptor.api.workspace import Workspace
    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLD / "input.rbd")["model_id"]
    res = run(ws, mid, timeout_s=60, confirm_long_run=True)
    assert any("negative time(s)" in w for w in res.warnings)

"""TieOrderProbe: equal-time entries inserted at different times, captured
from real Raptor with no RNG involved.

`test_real_capture_shows_fifo_and_deferred_tell_start` reads only the
captured file, so it pins the real ordering independently of the port.
`test_port_matches_real_events` checks that the port reproduces it.
"""
from __future__ import annotations

import pathlib


from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd

GOLDEN = pathlib.Path(__file__).parent.parent / "golden" / "TieOrderProbe"


def _real_rows() -> list[tuple[str, ...]]:
    lines = [l.rstrip("\r\n") for l in
             (GOLDEN / "legacy" / "rpt_events_TieOrderProbe.txt").read_text(encoding="latin-1").splitlines()
             if "\t" in l]
    return [tuple(c.strip() for c in l.split("\t")[:5]) for l in lines[1:]]   # drop header


def _port_rows() -> list[tuple[str, ...]]:
    lines = run_simulation(load_rbd(str(GOLDEN / "input.rbd")), num_runs=1)[0]
    return [tuple(c.strip() for c in l.split("\t")[:5]) for l in lines if "\t" in l]


def _at(rows, t: float) -> list[tuple[str, str]]:
    return [(r[2], r[3]) for r in rows if r[0] and abs(float(r[0]) - t) < 1e-6 and r[1] == "Block"]


def test_real_capture_shows_fifo_and_deferred_tell_start() -> None:
    real = _real_rows()
    # t=100: B5's failure timer was inserted at t=0, B3's repair at t=20, B4's at t=50.
    # FIFO => B5, B3, B4. (LIFO would be B4,B3,B5; lowest Id B3,B4,B5; highest B5,B4,B3.)
    at100 = _at(real, 100.0)
    order = [b for b, a in at100 if a in ("Failed", "Repaired")]
    assert order == ["TieB5", "TieB3", "TieB4"]
    # The TELL Repair spawned by B5's failure starts AFTER both pending completions.
    names = [(b, a) for b, a in at100]
    assert names.index(("TieB5", "Repairing")) > names.index(("TieB4", "Good"))
    # ...so the intermediate all-parallel-down state never exists in the capture.
    assert not any(r[0] and abs(float(r[0]) - 100.0) < 1e-6 and r[1] == "Node"
                   and r[2] == "join" and r[3] == "Bad" for r in real)
    # t=320: both failure rows precede both Repairing rows (TELL starts at the tail, in creation order).
    at320 = [a for _b, a in _at(real, 320.0)]
    assert at320 == ["Failed", "Failed", "Repairing", "Repairing"]


def test_port_matches_real_events() -> None:
    # Real writes a leading `Begin_Run_1` marker row that `run_simulation`'s per-run line
    # list does not carry; that is a harness difference, not an ordering one, so it is
    # excluded from both sides. Every remaining difference is a genuine ordering gap.
    strip = lambda rows: [r for r in rows if not r[2].startswith("Begin_Run")]
    assert strip(_port_rows()) == strip(_real_rows())

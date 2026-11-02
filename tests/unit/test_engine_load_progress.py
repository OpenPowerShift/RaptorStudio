"""`load_model`'s row parsing and progress contract.

Failure Effects View rebuilds the engine model when it opens. That build is
the long pole on a large model, so the GUI reports its progress. These tests
pin the two properties the GUI relies on: every row is parsed exactly once
(the old comprehensions parsed nodes/hierarchies/events twice), and the
callback reaches both the block read and the path pass.
"""
from __future__ import annotations

from collections import Counter

from raptor.core.engine import load as load_mod
from raptor.core.engine.load import load_model
from raptor.core.fileio.rbd import load_rbd

RBD = "tests/golden/Phasing1/input.rbd"


def _parsed_once(monkeypatch, filename: str) -> dict[str, int]:
    rbd = load_rbd(filename)
    counts: Counter[str] = Counter()
    for key, name in (("block", "block_from_row"),
                      ("event", "event_from_row"),
                      ("node", "node_from_row"),
                      ("hier", "hier_from_row")):
        real = getattr(load_mod, name)

        def wrapper(row, _real=real, _key=key):
            counts[_key] += 1
            return _real(row)

        monkeypatch.setattr(load_mod, name, wrapper)
    load_model(rbd)
    return counts


def test_load_model_parses_every_row_once(monkeypatch):
    rbd = load_rbd(RBD)
    counts = _parsed_once(monkeypatch, RBD)
    assert counts["block"] == len(rbd.sections["BLOCK_DATA"].rows) - 1
    assert counts["event"] == len(rbd.sections["EVENT_DATA"].rows)
    assert counts["node"] == len(rbd.sections["NODE_DATA"].rows)
    assert counts["hier"] == len(rbd.sections["HIERARCHY_DATA"].rows)


def test_load_model_reports_the_block_read_and_the_path_pass():
    rbd = load_rbd(RBD)
    seen: list[tuple[str, int, int]] = []
    load_model(rbd, progress=lambda stage, done, total:
               seen.append((stage, done, total)))
    assert seen
    assert seen[0][0] == "Reading blocks"
    assert ("Reading blocks", len(rbd.sections["BLOCK_DATA"].rows),
            len(rbd.sections["BLOCK_DATA"].rows)) in seen
    path_calls = [s for s in seen if s[0] == "Analysing paths"]
    assert path_calls and path_calls[-1][1] == path_calls[-1][2]

"""Validation warns about blocks drawn on top of each other."""
from __future__ import annotations

from raptor.api.validate import issues
from raptor.core.fileio.rbd import load_rbd


def _set(rbd, name, x, y):
    for r in rbd.sections["BLOCK_DATA"].rows:
        if r.get("blockName") == name:
            r["xPos"], r["yPos"] = str(x), str(y)


def _stacked(rbd):
    return [i for i in issues(rbd) if "drawn on top of" in i.message]


def test_blocks_at_one_spot_warn_and_name_the_top_one():
    rbd = load_rbd("tests/golden/Phasing3/input.rbd")
    names = [r["blockName"] for r in rbd.sections["BLOCK_DATA"].rows
             if r.get("ID") not in ("0", None)]
    a, b = names[0], names[1]
    _set(rbd, a, 50, 50)
    _set(rbd, b, 50, 50)
    found = _stacked(rbd)
    assert len(found) == 1
    w = found[0]
    assert w.severity == "warning"
    assert b in w.element and f"'{a}'" in w.message     # later row is on top


def test_blocks_apart_do_not_warn():
    rbd = load_rbd("tests/golden/Phasing3/input.rbd")
    assert _stacked(rbd) == []

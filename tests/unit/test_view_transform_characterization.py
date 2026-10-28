"""Characterisation of `DiagramView._compute_model_transform`.

The golden holds the (origin, flip, dx/dy grid, scale) it derives for every
tracked golden fixture, captured from the ORIGINAL method before it moved
into the Qt-free `raptor.gui.view_transform`. Regenerate on purpose with
`python tests/unit/test_view_transform_characterization.py --write`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Direct execution (`python tests/unit/<this file> --write`) puts the
# script's own directory on sys.path, not the repository root; add the
# root so the shared `tests.unit.golden_support` import resolves both
# under pytest and as a script.
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.unit.golden_support import assert_matches_golden, regenerate

GOLDEN = REPO / "tests" / "unit" / "data" / "view_transform_golden.json"
KEYS = ("_origin_x", "_flip_base", "_model_dx_min", "_model_dy_min", "_scale")


def _fixtures() -> list[Path]:
    return sorted((REPO / "tests" / "golden").glob("*/input.rbd"))


def observe() -> dict:
    from PySide6.QtWidgets import QApplication
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    QApplication.instance() or QApplication([])
    v = DiagramView()
    out: dict = {}
    for p in _fixtures():
        rbd = load_rbd(str(p))
        v._compute_model_transform(rbd)
        out[p.parent.name] = {
            "view": [round(getattr(v, k), 9) for k in KEYS],
            "stored": {k: round(x, 9) for k, x in rbd.view_transform.items()}}
    # frozen branch: reuse verbatim
    rbd = load_rbd(str(_fixtures()[0]))
    rbd.view_transform = {"scale": 3.5, "origin_x": 1.25, "flip_base": 9.0,
                          "dx_min": 2.0, "dy_min": 4.0}
    v._compute_model_transform(rbd)
    out["__frozen__"] = {"view": [getattr(v, k) for k in KEYS],
                         "stored": dict(rbd.view_transform)}
    # a model with no positioned elements
    rbd = load_rbd(str(_fixtures()[0]))
    for sec in ("BLOCK_DATA", "NODE_DATA", "HIERARCHY_DATA"):
        if sec in rbd.sections:
            rbd.sections[sec].rows = []
    v._compute_model_transform(rbd)
    out["__empty__"] = {"view": [getattr(v, k) for k in KEYS],
                        "stored": dict(rbd.view_transform)}
    return out


@pytest.mark.gui
def test_transform_unchanged(observed):
    # Fixtures added since the golden was captured are not in it; every one
    # that WAS recorded must still be there and unchanged.
    assert_matches_golden(observed, GOLDEN, subset=True)


@pytest.mark.gui
def test_golden_is_not_degenerate(observed):
    scales = {tuple(v["view"]) for v in observed.values()}
    assert len(scales) > 10


if __name__ == "__main__":
    regenerate(GOLDEN, observe)

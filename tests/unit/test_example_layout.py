"""Layout-quality gate for the bundled example models.

The start-page examples are the first diagrams a user sees, so they must
open on the same size-aware auto-layout the GUI's Auto Layout button uses:
every coordinate on Raptor 7's 1.25 grid, no block or hierarchy oval
overlapping another element on its own level, and both thumbnail themes
present. `tools/relayout_examples.py` repairs a model that fails the first
two; `tools/make_example_thumbnails.py` rebuilds the last.

Coordinates and view zooms only -- this test never runs the engine, so it
stays independent of the quoted-number gates.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.fileio import element_size
from raptor.core.fileio.legacy_layout import GRID
from raptor.core.fileio.rbds import load_rbds
from raptor.gui.examples import EXAMPLES, TUTORIALS, thumbnail

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "new_docs" / "models"

_POSITIONED = (
    ("BLOCK_DATA", "block"),
    ("NODE_DATA", "node"),
    ("EVENT_DATA", "event"),
    ("HIERARCHY_DATA", "hier"),
)
_SOLID = {"block", "hier"}


def _positioned(rbd):
    """(section, kind, id, parent, x, y, name) for every real element."""
    out = []
    for section, kind in _POSITIONED:
        sec = rbd.sections.get(section)
        if not sec:
            continue
        parent_col = {"BLOCK_DATA": "ParentId", "NODE_DATA": "parent",
                      "EVENT_DATA": "ParentId", "HIERARCHY_DATA": "Parent"}[section]
        for row in sec.rows:
            if str(row.get("ID", "0")) == "0":
                continue
            out.append((section, kind, str(row["ID"]),
                        int(float(row.get(parent_col, 0) or 0)),
                        float(row["xPos"]), float(row["yPos"]),
                        row.get(element_size.NAME_COLUMNS[section], "")))
    return out


def _box(item):
    section, _kind, _ident, _parent, _x, _y, name = item
    return (element_size.model_half_width(section, name,
                                          element_size.REFERENCE_SCALE),
            element_size.model_half_height(section, name,
                                           element_size.REFERENCE_SCALE))


@pytest.mark.parametrize("ex", (*EXAMPLES, *TUTORIALS), ids=lambda e: e.file)
def test_example_coordinates_sit_on_the_legacy_grid(ex):
    rbd = load_rbds(str(MODELS / ex.file))
    off = [(ident, x, y) for _section, _kind, ident, _parent, x, y, _name
           in _positioned(rbd)
           if abs(x / GRID - round(x / GRID)) > 1e-6
           or abs(y / GRID - round(y / GRID)) > 1e-6]
    assert not off, (
        f"{ex.file}: {len(off)} element(s) off the {GRID}-unit grid, e.g. "
        f"{off[:5]}; run tools/relayout_examples.py")


@pytest.mark.parametrize("ex", (*EXAMPLES, *TUTORIALS), ids=lambda e: e.file)
def test_example_has_no_same_level_block_or_oval_overlap(ex):
    rbd = load_rbds(str(MODELS / ex.file))
    levels: dict[int, list] = {}
    for item in _positioned(rbd):
        if item[1] in _SOLID:
            levels.setdefault(item[3], []).append(item)

    clashes = []
    for parent, items in levels.items():
        for i, first in enumerate(items):
            hw1, hh1 = _box(first)
            for second in items[i + 1:]:
                hw2, hh2 = _box(second)
                if (abs(first[4] - second[4]) < hw1 + hw2 - 1e-9
                        and abs(first[5] - second[5]) < hh1 + hh2 - 1e-9):
                    clashes.append(
                        f"level {parent}: {first[1]} {first[6]!r} at "
                        f"({first[4]}, {first[5]}) overlaps {second[1]} "
                        f"{second[6]!r} at ({second[4]}, {second[5]})")
    assert not clashes, (
        f"{ex.file}: same-level overlap(s):\n" + "\n".join(clashes[:8])
        + "\nrun tools/relayout_examples.py")


@pytest.mark.parametrize("ex", (*EXAMPLES, *TUTORIALS), ids=lambda e: e.file)
def test_example_has_both_thumbnail_themes(ex):
    for dark in (False, True):
        path = thumbnail(ex, dark)
        assert path.is_file() and path.stat().st_size > 0, (
            f"{path.name} missing or empty; run "
            "tools/make_example_thumbnails.py")

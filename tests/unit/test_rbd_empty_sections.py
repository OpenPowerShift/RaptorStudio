"""A model built from a spec must be written with every section real Raptor expects.

`build_from_spec` (MCP `build_model`) leaves empty sections with no column header and omits COMMENTS. Real
Raptor opens such a file to a blank window with no error (2026-10-01), so the writer gives each empty section
its canonical header and writes the missing ones (`render_rbd`, `_canonical_empty_columns`)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from raptor.api.build import build_from_spec, series
from raptor.core.fileio.rbd import parse_rbd, render_rbd

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "tools"))
import golden_fixtures as gf  # noqa: E402

_EMPTY_SECTIONS = ["HIERARCHY_DATA", "EVENT_DATA", "COMMENTS", "SPARE_POOL_DATA", "RESOURCE_DATA", "PHASES",
                   "TRIGGERS"]


def _headers(text: str) -> dict[str, list[str]]:
    lines = text.replace("\r\n", "\n").split("\n")
    out = {}
    for i, ln in enumerate(lines):
        if ln.startswith("/___") and ln.endswith("___"):
            out[ln.strip("/_")] = lines[i + 1].split()
    return out


def test_spec_built_model_carries_every_section_header_real_writes() -> None:
    text = render_rbd(build_from_spec(series(3)))
    real = _headers((_REPO / "tests" / "golden" / "PoolStockProbe" / "input.rbd").read_text(encoding="latin-1"))
    mine = _headers(text)
    assert list(mine) == list(real)                       # same sections, same order, COMMENTS included
    for name in _EMPTY_SECTIONS:
        assert mine[name] == real[name], name


@pytest.mark.parametrize("fixture", ["PoolStockProbe", "LadL1Series"])
def test_real_files_render_with_their_own_headers(fixture: str) -> None:
    path = gf.resolve_rbd_path(fixture)
    assert path is not None
    src = path.read_text(encoding="latin-1")
    assert _headers(render_rbd(parse_rbd(src))) == _headers(src)


def test_header_survives_a_parse_round_trip() -> None:
    again = parse_rbd(render_rbd(build_from_spec(series(2))))
    for name in _EMPTY_SECTIONS:
        assert again.section(name).rows == [], name
        assert again.section(name).columns, name


def test_end_node_with_k_above_one_needs_one_good_path_and_is_flagged() -> None:
    """Real needs one good path at an End node whatever its k says (`LadL2Res`, real 2026-10-01: k=2 n=3 logs
    `2-1/3`, 100 runs). The loader follows it and `validate()` warns, because the GUI cannot author it."""
    from raptor.core.fileio.builder import validate
    from raptor.core.fileio.typed import node_from_row

    rbd = build_from_spec({
        "blocks": [{"name": f"B{i}", "fail_dist": 4, "fail_params": [100.0], "repair_dist": 4,
                    "repair_params": [5.0]} for i in (1, 2, 3)],
        "nodes": [{"name": "start", "type": 1, "n": 0, "out": 3},
                  {"name": "end", "type": 3, "n": 3, "k": 2, "out": 0}],
        "links": [{"from": "start", "to": f"B{i}"} for i in (1, 2, 3)]
                 + [{"from": f"B{i}", "to": "end"} for i in (1, 2, 3)],
    })
    end_row = next(r for r in rbd.section("NODE_DATA").rows if r["Type"] == "3")
    assert end_row["k"] == "2"
    assert node_from_row(end_row).good_paths == 1
    assert any(w.startswith("WARN") and "End node" in w for w in validate(rbd))
    join_row = dict(end_row, Type="2")
    assert node_from_row(join_row).good_paths == 2          # an ordinary join keeps its k


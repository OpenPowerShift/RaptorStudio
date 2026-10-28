"""Empirical (16) distributions in the legacy file (`ER3-16`).

Real writes an empirical block's data points on a continuation line after the
block's own line, with 0 in `FParam1..3`.
The loader used to take that line for a phantom object and sample
from three zeros.
"""
from __future__ import annotations

import pytest

from raptor.core.engine.load import load_model
from raptor.core.fileio.rbd import load_rbd, parse_rbd, render_rbd
from raptor.core.fileio.rbds import parse_rbds, render_rbds
from raptor.core.fileio.rbdlib import make_trigger_row

BASE = "tests/golden/TwoBlockRedundancy/input.rbd"


def _with_empirical():
    rbd = load_rbd(BASE)
    row = rbd.sections["BLOCK_DATA"].rows[0]
    row.update(FDist="16", **{"#FParam": "5"}, FParam1="0", FParam2="0", FParam3="0",
               FEmp="10 20 30 40 50", RDist="16", **{"#RParam": "4"}, REmp="1 2 3 4")
    return rbd, row["ID"]


def _block(rbd, ident):
    return load_model(rbd).blocks_by_id[int(ident)]


def test_points_reach_the_block_and_the_repair_side_too():
    rbd, ident = _with_empirical()
    block = _block(rbd, ident)
    assert block.fail_values == [10.0, 20.0, 30.0, 40.0, 50.0]
    assert block.repair_values == [1.0, 2.0, 3.0, 4.0]


@pytest.mark.parametrize("roundtrip", [
    lambda r: parse_rbd(render_rbd(r)),
    lambda r: parse_rbds(render_rbds(r)),
    lambda r: parse_rbd(render_rbd(parse_rbds(render_rbds(r)))),
], ids=["rbd", "rbds", "rbds-then-rbd"])
def test_points_survive_every_save_path(roundtrip):
    rbd, ident = _with_empirical()
    assert _block(roundtrip(rbd), ident).fail_values == [10.0, 20.0, 30.0, 40.0, 50.0]


def test_the_rows_after_an_empirical_block_are_not_shifted_or_duplicated():
    rbd, _ident = _with_empirical()
    before = [r["blockName"] for r in rbd.sections["BLOCK_DATA"].rows]
    again = parse_rbd(render_rbd(rbd))
    assert [r["blockName"] for r in again.sections["BLOCK_DATA"].rows] == before


def test_the_continuation_line_sits_after_the_block_line_as_real_writes_it():
    rbd, _ = _with_empirical()
    lines = render_rbd(rbd).splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith(rbd.sections["BLOCK_DATA"].rows[0]["blockName"] + " "))
    assert lines[at + 1].split() == ["10.000000", "20.000000", "30.000000", "40.000000", "50.000000"]
    assert lines[at + 2].split() == ["1.000000", "2.000000", "3.000000", "4.000000"]


def test_points_may_wrap_over_several_lines():
    """Real reads reals from a stream, so a hand-edited file may break the line."""
    rbd, ident = _with_empirical()
    text = render_rbd(rbd).replace("10.000000         20.000000", "10.000000\n         20.000000")
    assert _block(parse_rbd(text), ident).fail_values == [10.0, 20.0, 30.0, 40.0, 50.0]


def test_a_block_that_is_not_empirical_is_untouched():
    rbd = load_rbd(BASE)
    assert "FEmp" not in rbd.sections["BLOCK_DATA"].rows[0]
    assert len(parse_rbd(render_rbd(rbd)).sections["BLOCK_DATA"].rows) == len(rbd.sections["BLOCK_DATA"].rows)


# The two edit paths that write a block's values back to its row. Both kept only
# the three parameter columns: the API (MCP, `raptor set`) stored three of five
# points under a `#FParam=5` header, so an exported .rbd read the next block's
# line as data; the GUI saved `#FParam=0` and no points at all.

POINTS = [21.0, 3.0, 13.0, 5.0, 8.0]
SORTED = [3.0, 5.0, 8.0, 13.0, 21.0]


def test_an_api_edit_keeps_every_point_through_an_rbd_export(tmp_path):
    from raptor.api import build
    from raptor.api.workspace import Workspace
    ws = Workspace(tmp_path / "ws")
    mid = ws.open_model(BASE)["model_id"]
    ident = ws.get(mid).rbd.sections["BLOCK_DATA"].rows[0]["ID"]
    build.update_element(ws, mid, f"Block:{ident}", {
        "fail_dist": 16, "fail_params": POINTS,
        "repair_dist": 16, "repair_params": POINTS[:4]})
    again = parse_rbd(render_rbd(ws.get(mid).rbd))
    block = _block(again, ident)
    assert block.fail_values == SORTED                       # sorted, as GetEmpirical does
    assert block.repair_values == sorted(POINTS[:4])
    assert [r["blockName"] for r in again.sections["BLOCK_DATA"].rows] == \
        [r["blockName"] for r in ws.get(mid).rbd.sections["BLOCK_DATA"].rows]


def test_an_api_edit_away_from_empirical_drops_the_points(tmp_path):
    from raptor.api import build
    from raptor.api.workspace import Workspace
    ws = Workspace(tmp_path / "ws")
    mid = ws.open_model(BASE)["model_id"]
    ident = ws.get(mid).rbd.sections["BLOCK_DATA"].rows[0]["ID"]
    build.update_element(ws, mid, f"Block:{ident}", {"fail_dist": 16, "fail_params": POINTS})
    build.update_element(ws, mid, f"Block:{ident}", {"fail_dist": 4, "fail_params": [100.0, 0.0]})
    row = ws.get(mid).rbd.sections["BLOCK_DATA"].rows[0]
    assert "FEmp" not in row and row["#FParam"] == "2"
    assert _block(parse_rbd(render_rbd(ws.get(mid).rbd)), ident).fail_values[:2] == [100.0, 0.0]


def test_a_gui_edit_keeps_every_point_through_a_save():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from raptor.gui.ui.diagram import DiagramView
    QApplication.instance() or QApplication([])
    rbd = load_rbd(BASE)
    view = DiagramView()
    view.load_rbd(rbd)
    ident = sorted(view.blocks)[0]
    view.set_block_dist(ident, "F", 16)
    view.set_block_fail_params(ident, POINTS)
    view.write_back_to_rbd(rbd)
    assert _block(parse_rbd(render_rbd(rbd)), ident).fail_values == SORTED


def test_export_never_emits_empirical_points_as_a_column():
    """A spec-built block keeps FEmp/REmp in its row dict; the legacy writer
    must not turn those into header columns or inline tokens.  Raptor 7 reads
    BLOCK_DATA positionally and crashes when the row is longer than the header.
    """
    rbd, _ = _with_empirical()
    lines = render_rbd(rbd).splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("/___BLOCK_DATA___"))
    header = lines[start + 1].split()
    assert "FEmp" not in header and "REmp" not in header
    end = next(i for i in range(start + 2, len(lines)) if lines[i].startswith("EndOfBlockData"))
    for ln in lines[start + 2:end]:
        if not ln.strip() or ln.lstrip()[0].isdigit():
            continue  # continuation line
        assert len(ln.split()) == len(header)


def test_triggerrow_pads_short_parameter_lists():
    row = make_trigger_row("T", dist=19, params=[500])
    assert [row["Param1"], row["Param2"], row["Param3"]] == ["500", "0.000000", "0.000000"]


def test_end_marker_is_reported_as_lowercase_end():
    """Raptor 7 prints the type-3 node as `end` in event logs and Final Results
    whatever name the .rbd row carries; the model keeps the authored name."""
    rbd = load_rbd(BASE)
    end = next(r for r in rbd.sections["NODE_DATA"].rows if r.get("Type") == "3")
    end["Name"] = "End"
    model = load_model(rbd)
    node = next(n for n in model.nodes_by_id.values() if n.type_node == 3)
    assert node.node_name == "End"
    assert node.report_name == "end"

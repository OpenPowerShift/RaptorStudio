"""An .rbl and a .json library give the same import (#147).

The JSON file keeps the whole BLOCK_DATA row, but reading it applies only the
fields an .rbl can hold, so the format a library was saved in never changes
what Import Library does."""
from __future__ import annotations

import copy
import json

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbl import LibEntry, template_row
from raptor.gui.ui.block_library import export_library, load_library, rbl_fields
from raptor.gui.ui.diagram import DiagramView

MODEL = "tests/golden/Phasing3/input.rbd"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def libs(tmp_path):
    rows = load_rbd(MODEL).sections["BLOCK_DATA"].rows
    rbl, js = str(tmp_path / "l.rbl"), str(tmp_path / "l.json")
    export_library(rbl, rows)
    export_library(js, rows)
    return rbl, js


def _num_or_text(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def test_the_two_formats_give_the_same_templates(libs):
    a = {t["name"]: t["row"] for t in load_library(libs[0])}
    b = {t["name"]: t["row"] for t in load_library(libs[1])}
    assert a.keys() == b.keys() and a
    for name in a:
        assert a[name].keys() == b[name].keys(), name
        for col in a[name]:
            assert _num_or_text(a[name][col]) == _num_or_text(b[name][col]), \
                (name, col)


def test_a_json_template_carries_only_what_an_rbl_carries(libs):
    allowed = set(template_row(LibEntry("")))
    for t in load_library(libs[1]):
        assert set(t["row"]) <= allowed, set(t["row"]) - allowed
        for dropped in ("FStream", "RStream", "DepNothing", "DepIdle",
                        "xPos", "ID", "ParentId"):
            assert dropped not in t["row"]


def test_the_json_file_still_records_the_whole_block(libs):
    """Retained for the record and for older readers: only the READ narrows."""
    doc = json.loads(open(libs[1]).read())
    row = doc["templates"][0]["row"]
    assert {"FStream", "RStream", "DepIdle", "xPos", "ID"} <= set(row)


def test_updating_from_either_format_gives_the_same_model(qapp, libs):
    def update(path):
        v = DiagramView()
        rbd = load_rbd(MODEL)
        # Make every block differ from the library first, in the fields the
        # JSON used to overwrite and the .rbl never touched.
        for r in rbd.sections["BLOCK_DATA"].rows:
            if str(r.get("ID", "0")) != "0":
                r.update({"FStream": "9", "RStream": "9", "DepIdle": "42.00",
                          "FParam1": "123456"})
        v.load_rbd(rbd)
        v.update_blocks_from_library(load_library(path))
        return copy.deepcopy(v._rbd_ref.sections["BLOCK_DATA"].rows)

    via_rbl, via_json = update(libs[0]), update(libs[1])
    assert len(via_rbl) == len(via_json)
    for a, b in zip(via_rbl, via_json):
        for col in a:
            assert _num_or_text(a[col]) == _num_or_text(b.get(col)), \
                (a.get("blockName"), col)
    live = [r for r in via_json if str(r.get("ID", "0")) != "0"]
    assert all(r["FStream"] == "9" and r["DepIdle"] == "42.00" for r in live)
    assert all(r["FParam1"] != "123456" for r in live)     # the update did run


@pytest.mark.parametrize("dep_num,expected", [("-2", "-2"), ("-1", "0"),
                                              ("0", "0"), ("7", "0"),
                                              ("", "0"), ("junk", "0")])
def test_only_dependent_on_the_system_survives(dep_num, expected):
    row = {"blockName": "X", "FDist": "4", "DepNum": dep_num,
           "DepType": "RBDNode"}
    got = rbl_fields(row)
    assert got["DepNum"] == expected and got["DepType"] == "None"

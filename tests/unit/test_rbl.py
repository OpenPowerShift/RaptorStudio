"""The original's block library file (`.rbl`) against real files: its shipped `ch20.rbl` and the
export real Raptor wrote for `Phasing3` (2026-10-10)."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbl import (RblError, blocks_from_rows, read_rbl, template_row, write_rbl)

GOLD = Path(__file__).resolve().parents[1] / "golden" / "Phasing3" / "legacy"


def _floats(v):
    return [float(x) for x in v]


def test_the_shipped_library_round_trips_byte_for_byte():
    raw = (GOLD / "lib_ch20_shipped.rbl").read_bytes()
    entries, blank = read_rbl(raw.decode("latin-1"))
    assert len(entries) == 17 and not blank
    blocks = [dict(name=e.name, fail_dist=e.fail_dist, fail_values=_floats(e.fail_values),
                   repair_dist=e.repair_dist, repair_values=_floats(e.repair_values),
                   pre_dist=e.pre_dist, post_dist=e.post_dist, pre_vals=_floats(e.pre_vals),
                   post_vals=_floats(e.post_vals), dependent=e.dependent) for e in entries]
    assert write_rbl(blocks, "library_update.rbd", "Tue_Jan_04_10:35:11_2005").encode("latin-1") == raw


def test_the_export_of_a_model_matches_the_real_export():
    rbd = load_rbd(str(GOLD.parent / "input.rbd"))
    real = (GOLD / "lib_Phasing3.rbl").read_bytes()
    out = write_rbl(blocks_from_rows(rbd.section("BLOCK_DATA").rows), "input.rbd",
                    "Sat_Oct_10_15:26:13_2026")
    assert out.encode("latin-1") == real


def test_export_then_import_gives_the_same_blocks():
    rbd = load_rbd(str(GOLD.parent / "input.rbd"))
    text = write_rbl(blocks_from_rows(rbd.section("BLOCK_DATA").rows), "m.rbd", "Sat_Oct_10_15:26:13_2026")
    entries, _ = read_rbl(text)
    assert [e.name for e in entries] == [r["blockName"] for r in rbd.section("BLOCK_DATA").rows if r["ID"] != "0"]
    row = template_row(entries[0])
    src = next(r for r in rbd.section("BLOCK_DATA").rows if r["blockName"] == entries[0].name)
    assert float(row["FParam1"]) == float(src["FParam1"]) and row["FDist"] == src["FDist"]


def test_duplicate_names_get_a_suffix():
    text = ("Raptor7.0/x.rbd/Sat_Oct_10_15:26:13_2026\r\nheading\r\n"
            "A  4  100.0  0.0  0.0  7  10.0  2.0  0.0  19  19  No  0.0 0.0 0.0  0.0 0.0 0.0\r\n"
            "A  4  200.0  0.0  0.0  7  10.0  2.0  0.0  19  19  No  0.0 0.0 0.0  0.0 0.0 0.0\r\n"
            "A  4  300.0  0.0  0.0  7  10.0  2.0  0.0  19  19  No  0.0 0.0 0.0  0.0 0.0 0.0\r\n")
    entries, _ = read_rbl(text)
    assert [e.name for e in entries] == ["A", "A-2", "A-3"]


def test_a_line_starting_with_a_blank_is_skipped_and_reported():
    text = ("Raptor7.0/x.rbd/s\r\nh\r\nA 4 1 0 0 7 1 1 0 19 19 No 0 0 0 0 0 0\r\n  B 4 1 0 0 7 1 1 0\r\n")
    entries, blank = read_rbl(text)
    assert [e.name for e in entries] == ["A"] and blank


def test_a_file_that_is_not_a_library_is_refused():
    with pytest.raises(RblError, match="properly formatted"):
        read_rbl("not a library\r\n")


@pytest.mark.parametrize("dist", ["16", "0", "23"])
def test_an_invalid_distribution_refuses_the_whole_file(dist):
    text = f"Raptor7.0/x.rbd/s\r\nh\r\nA {dist} 1 1 1 7 1 1 0 19 19 No 0 0 0 0 0 0\r\n"
    with pytest.raises(RblError, match="invalid distribution"):
        read_rbl(text)


def test_parameters_are_clamped_into_their_ranges():
    text = ("Raptor7.0/x.rbd/s\r\nh\r\n"
            "Expo 4 -5 -3 0 7 -1 0 0 19 19 No 0 0 0 0 0 0\r\n"
            "Bino 3 12.7 5 0 8 1 1 0 19 19 No 0 0 0 0 0 0\r\n"
            "Tri  14 5 3 2 10 5 1 0 19 19 No 0 0 0 0 0 0\r\n")
    e, _ = read_rbl(text)
    assert e[0].fail_values[:2] == ["100", "0"]               # exponential: mean back to 100, location to 0
    assert e[0].repair_values[:2] == ["10", "2"]              # lognormal: both out of range
    assert e[1].fail_values[:2] == ["12", "0.8"]              # binomial: n truncated, p out of range
    assert e[2].fail_values[:2] == ["0.500000", "1.000000"]    # triangular: halved until in order (5,3,2 -> 1.5,1 -> .5)
    assert e[2].repair_values[0] == "1"                       # uniform real: min above max, set to max


def test_a_short_line_takes_the_defaults():
    entries, _ = read_rbl("Raptor7.0/x.rbd/s\r\nh\r\nOnly\r\n")
    e = entries[0]
    assert (e.fail_dist, e.fail_values[0], e.repair_dist, e.pre_dist, e.dependent) == (4, "100", 7, "0", False)


def test_version_six_libraries_read_their_fixed_ldts():
    text = ("Raptor6.0/x.rbd/s\r\nh\r\nA 4 100 0 0 0 7 10 2 0 0 3.5 4.5 Yes\r\n")
    e = read_rbl(text)[0][0]
    row = template_row(e)
    assert row["Pre-LDTDist"] == "19" and row["Param1#1"] == "3.5" and row["Param1#2"] == "4.5"
    assert e.dependent is True


def test_the_gui_module_reads_and_writes_rbl_and_keeps_json(tmp_path):
    from raptor.gui.ui.block_library import export_library, load_library, rbl_stamp
    assert rbl_stamp("Sat Oct 10 15:26:13 2026") == "Sat_Oct_10_15:26:13_2026"
    rbd = load_rbd(str(GOLD.parent / "input.rbd"))
    rows = rbd.section("BLOCK_DATA").rows
    rbl = tmp_path / "m.rbl"
    n = export_library(str(rbl), rows, "input.rbd")
    assert n == 16 and rbl.read_bytes().startswith(b"Raptor7.0/input.rbd/")
    t = load_library(str(rbl))
    assert len(t) == 16 and t[0]["name"] == "Attitude" and t[0]["row"]["FDist"] == "15"
    js = tmp_path / "m.json"
    export_library(str(js), rows)
    assert len(load_library(str(js))) == 16
    shipped = load_library(str(GOLD / "lib_ch20_shipped.rbl"))
    assert len(shipped) == 17

"""Block library export, import and update."""

from __future__ import annotations

import os
import pathlib

import pytest

from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.block_library import export_library, load_library
from raptor.gui.ui.diagram import DiagramView

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def lib(qapp, tmp_path_factory):
    """HierarchyExample's blocks exported as a library, in this module's own
    temp dir: every test gets it whatever order (or xdist worker) it runs
    in, and parallel runs never share one file."""
    path = str(tmp_path_factory.mktemp("block_library") / "test_lib.json")
    export_library(path, _view(qapp)._rbd_ref.sections["BLOCK_DATA"].rows)
    return path


def _view(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        GOLD, "HierarchyExample", "input.rbd")))
    return v


def test_export_import_roundtrip(qapp, tmp_path):
    v = _view(qapp)
    path = str(tmp_path / "roundtrip.json")
    n = export_library(path, v._rbd_ref.sections["BLOCK_DATA"].rows)
    assert n >= 13
    templates = {t["name"]: t["row"] for t in load_library(path)}
    assert "Thermal" in templates
    assert templates["Thermal"]["FDist"] == \
        str(next(b for b in v.blocks.values() if b.name == "Thermal")
            .fail_distro)


def test_add_block_from_template_undoable(qapp, lib):
    v = _view(qapp)
    t0 = next(t for t in load_library(lib) if t["name"] == "HighGain")
    n0 = len(v.blocks)
    bid = v.add_block_from_template(t0["row"])
    assert bid and len(v.blocks) == n0 + 1
    blk = v.blocks[bid]
    assert blk.name == "HighGain"
    assert blk.fail_distro == int(t0["row"]["FDist"])
    assert "FStream" not in t0["row"]       # an .rbl has no streams (#147)
    v._stack.undo()
    assert len(v.blocks) == n0


def test_update_matching_restores_on_undo(qapp, lib):
    v = _view(qapp)
    t0 = next(t for t in load_library(lib) if t["name"] == "Thermal")
    therm = next(b for b in v.blocks.values() if b.name == "Thermal")
    orig = (therm.fail_distro, list(therm.fail_values),
            therm.repair_distro, therm.fail_stream)
    # A template that differs in every updated column.
    import copy
    row = copy.deepcopy(t0["row"])
    row.update({"FDist": "15", "#FParam": "3", "FStream": "-9",
                "FParam1": "11", "FParam2": "22", "FParam3": "33"})
    n = v.update_blocks_from_library([{"name": "Thermal", "row": row}])
    assert n == 1
    assert therm.fail_distro == 15
    assert [round(x) for x in therm.fail_values[:3]] == [11, 22, 33]
    assert therm.fail_stream == -9
    v._stack.undo()
    assert therm.fail_distro == orig[0]
    assert therm.fail_values == orig[1]
    assert therm.repair_distro == orig[2]
    assert therm.fail_stream == orig[3]


def test_update_matching_applies_ldt_and_dependency(qapp, lib):
    """Library "update matching" applies the LDT and Dependency columns
    as well as the failure and repair columns."""
    import copy
    v = _view(qapp)
    t0 = next(t for t in load_library(lib) if t["name"] == "Thermal")
    row = copy.deepcopy(t0["row"])
    row.update({"DepType": "Block", "DepNum": "1",
                "Pre-LDTDist": "8"})
    n = v.update_blocks_from_library([{"name": "Thermal", "row": row}])
    assert n == 1
    live = next(r for r in v._rbd_ref.sections["BLOCK_DATA"].rows
                if r.get("blockName") == "Thermal")
    assert live["DepType"] == "Block"
    assert live["Pre-LDTDist"] == "8"

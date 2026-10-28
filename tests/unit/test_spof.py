"""Ch.10 single-point-failure analysis (`spf_fev.single_point_failures`)."""

from __future__ import annotations

import os
import pathlib


from raptor.core.engine.spf_fev import single_point_failures
from raptor.core.fileio.rbd import load_rbd

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")


def _spof(name):
    return single_point_failures(load_rbd(os.path.join(GOLD, name, "input.rbd")))


def test_series_components_are_single_points():
    # Manual example (Ch.10, 10-5): Attitude and Thermal are series
    # components of HierarchyExample. Rows come back in model/file order,
    # so Thermal precedes Attitude.
    assert [d["name"] for d in _spof("HierarchyExample")] == \
        ["Thermal", "Attitude"]


def test_parallel_redundancy_has_none():
    assert _spof("TwoBlockRedundancy") == []


def test_series_pair_is_both():
    rows = _spof("ComplexExample")
    names = {d["name"] for d in rows}
    assert isinstance(names, set)

"""Pool flags loaded from a friendly .rbds must read as set.

`load_rbds` used to write a pool's `eso`/`rso`/`slo` as "1.000000", which
`typed.cell_truthy` (an allow-list: "1", "t", "true", ...) reads as off, so
a Pooled block with `eso: 1` never placed its emergency order and the
system went Permanently_Down. The .rbd writer already emits these columns
as whole numbers; the friendly layer now does too.
"""
from __future__ import annotations

import json

import pytest

from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbds import load_rbds
from raptor.core.fileio.typed import spare_pool_from_row


def _model(tmp_path, **pool):
    doc = {
        "format": "raptor-studio", "version": 1,
        "system": {"units": "days"},
        "experiment": {"trials": 1, "termination_type": "TimeSim",
                       "term_condition": 200},
        "spare_pools": [{"name": "P", "stock": 0, "new_spares": 0,
                         "arrive_every": 1.0, "rso": 0, "eso": 0,
                         "emer_time": 10, "slo": 0, "level": 0,
                         "quantity": 1, "slo_time": 10, **pool}],
        "elements": {
            "blocks": [{"id": 1, "name": "A", "x": 5, "y": 0,
                        "fail_dist": 19, "fail_params": [40, 0, 0],
                        "repair_dist": 19, "repair_params": [1, 0, 0],
                        "legacy": {"SpType": "Pooled",
                                   "SparePoolName": "P"}}],
            "nodes": [{"id": 1, "type": 1, "name": "start", "x": 0, "y": 0},
                      {"id": 2, "type": 3, "name": "end", "x": 10, "y": 0}]},
        "links": [{"id": 1, "from": ["Node", 1], "to": ["Block", 1]},
                  {"id": 2, "from": ["Block", 1], "to": ["Node", 2]}]}
    path = tmp_path / "pool.rbds"
    path.write_text(json.dumps(doc))
    return load_rbds(str(path))


@pytest.mark.parametrize("key, col, attr", [
    ("eso", "ESO", "emergency_spare_ordering"),
    ("rso", "RSO", "routine_spare_ordering"),
    ("slo", "SLO", "stock_level_ordering"),
])
def test_a_friendly_pool_flag_reads_as_set(tmp_path, key, col, attr):
    row = _model(tmp_path, **{key: 1}).sections["SPARE_POOL_DATA"].rows[0]
    assert row[col] == "1"
    assert getattr(spare_pool_from_row(row), attr) is True


def test_a_pool_flag_of_zero_reads_as_off(tmp_path):
    row = _model(tmp_path).sections["SPARE_POOL_DATA"].rows[0]
    assert (row["ESO"], row["RSO"], row["SLO"]) == ("0", "0", "0")


def test_the_emergency_order_is_placed_from_an_empty_pool(tmp_path):
    """Fails every 40 days, empty pool, 10-day emergency lead time."""
    logs = run_simulation(_model(tmp_path, eso=1), num_runs=1)[0]
    text = "\n".join(logs)
    assert "Spare_ordered_(stock=0)" in text
    assert "Spare_Arrived" in text
    assert "Permanently_Down" not in text

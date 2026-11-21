"""The Charts tab's availability and capacity targets travel with the model."""
from __future__ import annotations

import json

from raptor.api.build import build_from_spec, series
from raptor.core.fileio.rbds import load_rbds, render_rbds


def test_targets_round_trip_through_an_rbds_file(tmp_path):
    rbd = build_from_spec(series(2))
    rbd.targets = {"availability": 0.995, "capacity": 0.98}
    text = render_rbds(rbd)
    doc = json.loads(text)
    assert doc["targets"] == {"availability": 0.995, "capacity": 0.98}
    path = tmp_path / "targets.rbds"
    path.write_text(text, encoding="utf-8")
    loaded = load_rbds(str(path))
    assert loaded.targets == {"availability": 0.995, "capacity": 0.98}


def test_cleared_or_invalid_targets_are_not_written():
    rbd = build_from_spec(series(2))
    rbd.targets = {"availability": None, "capacity": 1.5}
    assert "targets" not in json.loads(render_rbds(rbd))


def test_a_target_is_not_part_of_the_model_identity():
    from raptor.api.run import model_hash
    rbd = build_from_spec(series(2))
    before = model_hash(rbd)
    rbd.targets = {"availability": 0.995, "capacity": 0.98}
    assert model_hash(rbd) == before


def test_a_file_without_targets_loads_with_an_empty_set(tmp_path):
    rbd = build_from_spec(series(2))
    path = tmp_path / "plain.rbds"
    path.write_text(render_rbds(rbd), encoding="utf-8")
    assert load_rbds(str(path)).targets == {}

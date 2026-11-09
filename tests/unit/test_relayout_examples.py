"""The layout-only relayout tool: `--check` never writes, a real run changes
layout fields and nothing else, and the committed examples are current.

`tools/relayout_examples.py` is the reproducible repair for a bundled model
that was hand-placed or predates the size-aware engine. These tests keep the
two properties that make it safe to run over the book: check mode is
read-only, and the rewrite is layout-only (coordinates, stored view zooms,
and the stale frozen scene transform).
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import relayout_examples  # noqa: E402

MODELS = ROOT / "new_docs" / "models"


def _without_layout(doc: dict) -> dict:
    """The model with every layout-owned field removed."""
    doc = copy.deepcopy(doc)
    for elements in (doc.get("elements") or {}).values():
        for element in elements:
            element.pop("x", None)
            element.pop("y", None)
            legacy = element.get("legacy")
            if isinstance(legacy, dict):
                for key in ("zoom", "xOrigin", "yOrigin"):
                    legacy.pop(key, None)
    system = doc.get("system") or {}
    legacy = system.get("legacy")
    if isinstance(legacy, dict):
        for key in ("zoomVal", "xOrigin", "yOrigin"):
            legacy.pop(key, None)
    doc.pop("view_transform", None)
    return doc


def _off_grid_copy(tmp_path: Path) -> tuple[Path, dict]:
    raw = json.loads((MODELS / "Water1.rbds").read_text(encoding="utf-8"))
    raw["elements"]["blocks"][0]["x"] += 0.13
    raw["view_transform"] = {
        "scale": 1.0, "origin_x": 0.0, "flip_base": 0.0,
        "dx_min": 0.0, "dy_min": 0.0,
    }
    path = tmp_path / "moved.rbds"
    path.write_text(json.dumps(raw, indent=1, ensure_ascii=False),
                    encoding="utf-8")
    return path, raw


def test_check_never_writes_even_when_a_model_would_move(tmp_path):
    path, _raw = _off_grid_copy(tmp_path)
    before = path.read_bytes()

    assert relayout_examples.relayout(path, dry_run=True) is True
    assert path.read_bytes() == before

    assert relayout_examples.main(["--check", str(path)]) == 1
    assert path.read_bytes() == before


def test_real_run_changes_layout_only(tmp_path):
    path, raw = _off_grid_copy(tmp_path)
    before = _without_layout(raw)

    assert relayout_examples.relayout(path) is True

    after = json.loads(path.read_text(encoding="utf-8"))
    assert _without_layout(after) == before
    assert "view_transform" not in after
    for element in after["elements"]["blocks"]:
        assert abs(element["x"] / 1.25 - round(element["x"] / 1.25)) < 1e-6
        assert abs(element["y"] / 1.25 - round(element["y"] / 1.25)) < 1e-6


def test_bundled_examples_are_on_the_current_layout():
    """`--check` over the book is read-only and must report clean."""
    assert relayout_examples.main(["--check"]) == 0

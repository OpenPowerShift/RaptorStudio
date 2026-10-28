"""The ribbon's structure and every action's properties, against a stored
snapshot (tests/unit/data/ribbon_snapshot.json).

The docs gate checks that tab / group / control / tooltip TEXT did not
move. It cannot see whether a button is large, checkable, initially
checked, enabled before or after a model opens, or what shortcut context
it has -- so a structural change to the ribbon can pass every other test
and still change behaviour. This is what pins those.

When a change is INTENDED, regenerate and review the diff in the commit:

    python tools/ribbon_snapshot.py --write
"""
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.gui

REPO = Path(__file__).resolve().parents[2]


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "ribbon_snapshot", REPO / "tools" / "ribbon_snapshot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ribbon_matches_the_stored_snapshot():
    from raptor.gui.ui import settings
    # The snapshot assumes DEFAULT settings. Animate's initial state comes
    # from a persisted preference, which an earlier test in the session
    # may have changed.
    settings.remove("execution/default_animate")
    tool = _load_tool()
    old = json.loads(tool.SNAPSHOT.read_text())
    differences = tool.diff(old, tool.take(isolate=False))
    assert not differences, (
        "the ribbon changed. If that was intended, run "
        "`python tools/ribbon_snapshot.py --write` and review "
        "the snapshot diff in the commit:\n  "
        + "\n  ".join(differences[:40]))

"""Shared plumbing for the characterisation-golden suites.

Eight modules capture an observed structure, compare it against a JSON
golden and can regenerate that file with ``--write``. Each carried its
own copy of the comparison, the regeneration block and (in three cases)
the same ``_plain`` normaliser. The copies were not all identical -- one
reported only the first five mismatches, another compared the whole dict --
so this module is the one place the rule is stated.

It is deliberately not named ``test_*``: pytest must not collect it.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def plain(value: Any) -> Any:
    """JSON-shaped copy of *value*, stringifying anything not JSON-native.

    Dictionaries keep string keys; lists/tuples become lists. Used by the
    canvas suites, whose observed structure contains row dicts and Qt
    enums.
    """
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return (value if isinstance(value, (int, float, str, bool)) or value is None
            else str(value))


def assert_matches_golden(observed: dict, golden_path: Path, *,
                          subset: bool = False) -> None:
    """Assert the observation equals the committed golden, naming misses.

    ``subset`` is for suites whose fixture corpus has grown since the
    golden was captured: every recorded arm must still be present and
    unchanged, but a new fixture may add one.

    Reports the first few differing case names rather than dumping two
    whole structures: the usual failure is one changed arm, and the name
    is what points at the code.
    """
    golden = json.loads(golden_path.read_text())
    if subset:
        missing = sorted(set(golden) - set(observed))
        assert not missing, missing[:5]
    else:
        assert set(observed) == set(golden), (
            f"case sets differ: only observed "
            f"{sorted(set(observed) - set(golden))[:5]}, only golden "
            f"{sorted(set(golden) - set(observed))[:5]}")
    bad = [key for key in golden if golden[key] != observed[key]]
    assert not bad, bad[:5]


def regenerate(golden_path: Path, observe: Callable[[], dict]) -> None:
    """``python <suite>.py --write``: rewrite the golden from the code.

    Regeneration is always deliberate and never happens during a pytest
    run; the module's own docstring says so.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if "--write" not in sys.argv:
        print("pass --write to (re)generate the golden file")
        return
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    data = observe()
    golden_path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    print(f"wrote {golden_path.relative_to(REPO)} ({len(data)} arms)")

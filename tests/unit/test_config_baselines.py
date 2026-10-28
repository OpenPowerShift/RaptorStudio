"""Config baselines must not name files that no longer exist.

`pyproject.toml`'s `[tool.ruff.lint.per-file-ignores]` carried
`raptor/core/engine/dependent_block.py` after that module was removed
(block-dependents run the ordinary BlockProcess). A waiver for a deleted file is a waiver nobody is maintaining,
and unlike the other two baselines here nothing checked for it.

The `coverage_floors.json` and `mypy_gui_baseline.json` equivalents are
already clean -- their gates check in BOTH directions (flagging a floor
with no data as well as data with no floor:
`engine_coverage.py:106-116`, `mypy_gate.py:58-59`). This module gives
the ruff list the same treatment.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _per_file_ignores() -> dict[str, list[str]]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["ruff"]["lint"]["per-file-ignores"]


class TestRuffPerFileIgnores:
    def test_every_glob_entry_matches_a_real_file(self):
        """A non-glob key naming a missing file is dead configuration."""
        ignores = _per_file_ignores()
        missing = [
            pattern for pattern in ignores
            if "*" not in pattern and not (REPO / pattern).is_file()
        ]
        assert not missing, (
            "ruff waivers name files that do not exist, so nothing enforces "
            f"them and nobody notices they are stale: {missing}"
        )

    def test_the_list_is_not_empty(self):
        """Guards against the previous test passing because the key vanished."""
        assert len(_per_file_ignores()) > 40, (
            "the waiver list shrank unexpectedly -- confirm that was intended"
        )

    @pytest.mark.parametrize("pattern", [
        "raptor/core/engine/block_lifecycle.py",
        "raptor/core/engine/orchestrator.py",
        "raptor/core/engine/kickoff.py",
    ])
    def test_known_debt_still_carries_its_waiver(self, pattern):
        """The files the pyproject comment names as the first de-ratchet
        targets must keep their exemptions until they are actually split."""
        assert pattern in _per_file_ignores(), (
            f"{pattern} lost its complexity waiver -- it has not been "
            "refactored yet, so this would turn CI red for no reason"
        )


class TestOtherBaselinesAreAlreadyChecked:
    """Not a test of ours -- a record of why only the ruff list needed one."""

    def test_coverage_and_mypy_baselines_have_no_stale_keys(self):
        for rel in ("tools/coverage_floors.json",
                    "tools/mypy_gui_baseline.json"):
            path = REPO / rel
            if not path.is_file():
                continue
            import json
            keys = json.loads(path.read_text(encoding="utf-8"))
            module_like = [k for k in keys
                           if k.endswith(".py") and "*" not in k]
            missing = [k for k in module_like
                       if not (REPO / k).exists() and "/" in k]
            assert not missing, f"{rel} has stale keys: {missing}"

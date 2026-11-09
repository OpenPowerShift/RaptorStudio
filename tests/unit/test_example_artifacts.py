"""Committed example thumbnails must match a fresh render.

The start-page cards are checked-in PNGs, so a coordinated relayout can
look correct in the models while every card still shows the old picture.
`make_example_thumbnails.py --check` renders to a temporary directory and
compares the decoded PNG bytes without touching the working tree.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import make_example_thumbnails  # noqa: E402


def test_committed_example_thumbnails_are_current():
    assert make_example_thumbnails.main(["--check"]) == 0, (
        "a committed start-page thumbnail is stale; run "
        "`.venv/bin/python tools/make_example_thumbnails.py`")

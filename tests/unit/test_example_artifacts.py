"""Committed example thumbnails must match a fresh render.

The start-page cards are checked-in PNGs, so a coordinated relayout can
look correct in the models while every card still shows the old picture.
`make_example_thumbnails.py --check` renders to a temporary directory and
compares the decoded PNG bytes without touching the working tree.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_committed_example_thumbnails_are_current():
    # Render in a child process: the check builds Qt canvases, and a
    # parallel xdist worker's theme/scale state must not perturb it.
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_example_thumbnails.py"),
         "--check"],
        cwd=ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen",
             "PYTHONPATH": str(ROOT)},
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, (
        "a committed start-page thumbnail is stale; run "
        "`.venv/bin/python tools/make_example_thumbnails.py`\n"
        + result.stderr + result.stdout)

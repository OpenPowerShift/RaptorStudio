"""The stylesheet's glyph images: one reused folder, never one per process."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("PySide6")


def test_glyph_images_go_to_one_reused_folder(monkeypatch):
    """U6: a folder per process was removed only on a clean exit, so every
    crash or hard exit left one in the temp folder."""
    from raptor.gui.resources import indicator_images as images

    monkeypatch.setattr(images, "_dir", None)
    first = images._folder()
    monkeypatch.setattr(images, "_dir", None)
    assert images._folder() == first
    assert not first.name.startswith("raptor-studio-qss-")
    assert not str(first).startswith(tempfile.gettempdir() + os.sep + "raptor-studio-qss-")


def test_a_glyph_is_written_whole(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication

    from raptor.gui.resources import indicator_images as images

    QApplication.instance() or QApplication([])

    monkeypatch.setattr(images, "_dir", tmp_path)
    path = Path(images._write("probe", "#123456", lambda p, c, w, h: p.fillRect(0, 0, w, h, c)))
    assert path.exists() and path.stat().st_size > 0
    assert not list(tmp_path.glob("*.part.png"))

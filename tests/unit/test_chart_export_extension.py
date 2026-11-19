"""A chart saved under a name with no extension gets the type's own: a
PNG named "outage" wrote nothing at all, as the format comes from it."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def chart():
    from PySide6.QtWidgets import QApplication

    from raptor.api.insight import charts
    from raptor.gui.ui.chart_widget import ChartWidget
    QApplication.instance() or QApplication([])
    widget = ChartWidget()
    widget.set_spec(charts.ecdf([{"availability": v} for v in (0.95, 0.97, 0.99)]))
    return widget


def _answer(monkeypatch, path):
    from raptor.gui.ui import filedialogs
    monkeypatch.setattr(filedialogs, "getSaveFileName", lambda *a, **k: (str(path), ""))


@pytest.mark.parametrize("ask, ext, magic", [
    ("_ask_png", ".png", b"\x89PNG"), ("_ask_svg", ".svg", b"<"),
    ("_ask_pdf", ".pdf", b"%PDF"), ("_ask_csv", ".csv", b"")])
def test_a_name_without_an_extension_gets_one(chart, monkeypatch, tmp_path, ask, ext, magic):
    _answer(monkeypatch, tmp_path / "outage")
    getattr(chart, ask)()
    saved = tmp_path / f"outage{ext}"
    assert saved.is_file() and saved.read_bytes().lstrip().startswith(magic)
    assert not (tmp_path / "outage").exists()


def test_an_existing_file_is_only_replaced_when_asked(chart, monkeypatch, tmp_path):
    from raptor.gui.ui import dialogs
    (tmp_path / "outage.png").write_bytes(b"keep")
    _answer(monkeypatch, tmp_path / "outage")
    monkeypatch.setattr(dialogs, "question_box", lambda *a, **k: False)
    chart._ask_png()
    assert (tmp_path / "outage.png").read_bytes() == b"keep"


def test_the_charts_page_menu_adds_it_too(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication

    from raptor.gui.ui.charts_page import ChartsPage
    QApplication.instance() or QApplication([])
    page = ChartsPage()
    _answer(monkeypatch, tmp_path / "chart")
    assert page._ask("Save chart", "PNG image", ".png") == str(tmp_path / "chart.png")

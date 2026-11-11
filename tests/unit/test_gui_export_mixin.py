"""Headless tests for the export commands (SVG, PNG, clipboard, workbook, PDF).

Every file dialog is replaced (a real modal dialog blocks a headless run); the files
the exports write are then checked on disk.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from raptor.core.fileio.rbd import load_rbd  # noqa: E402
from raptor.gui.ui import filedialogs as fdg  # noqa: E402
from raptor.gui.ui import msgbox  # noqa: E402
from raptor.gui.ui.main_window import MainWindow  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests/golden/HierarchyExample/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(FIXTURE), FIXTURE)
    return w


@pytest.fixture
def errors(monkeypatch):
    """Collect critical boxes instead of showing them."""
    seen = []
    monkeypatch.setattr(msgbox, "critical", lambda parent, title, text: seen.append(title))
    return seen


def _status(w):
    return w.statusBar().currentMessage()


def _pick(monkeypatch, path):
    monkeypatch.setattr(fdg, "getSaveFileName", lambda *a, **k: (path, ""))


# ------------------------------------------------------------------- SVG

def test_svg_export_writes_an_svg_without_the_grid(window, monkeypatch, tmp_path):
    out = tmp_path / "d.svg"
    _pick(monkeypatch, str(out))
    window._export_diagram_svg()
    text = out.read_text(encoding="utf-8")
    assert "<svg" in text
    assert "Diagram SVG saved: d.svg" in _status(window)


def test_svg_export_cancel_writes_nothing(window, monkeypatch, tmp_path):
    _pick(monkeypatch, "")
    window._export_diagram_svg()
    assert list(tmp_path.iterdir()) == []


def test_svg_export_does_nothing_without_a_model(window, monkeypatch):
    window._rbd = None
    _pick(monkeypatch, "never.svg")
    window._export_diagram_svg()
    assert not os.path.exists("never.svg")


def test_svg_export_reports_an_unwritable_path(window, monkeypatch, errors, tmp_path):
    _pick(monkeypatch, str(tmp_path / "missing_dir" / "d.svg"))
    window._export_diagram_svg()
    assert errors == ["Export Diagram Failed"]
    assert "SVG export failed" in _status(window)


# ------------------------------------------------------------------- PNG

def _stub_png_dialog(monkeypatch, files):
    monkeypatch.setattr(QFileDialog, "exec", lambda self: 1 if files else 0)
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: files)


def test_bitmap_export_saves_a_png(window, monkeypatch, tmp_path):
    out = tmp_path / "d.png"
    _stub_png_dialog(monkeypatch, [str(out)])
    window._export_bitmap()
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "Saved diagram bitmap" in _status(window)


def test_bitmap_export_cancel_writes_nothing(window, monkeypatch, tmp_path):
    _stub_png_dialog(monkeypatch, [])
    window._export_bitmap()
    assert list(tmp_path.iterdir()) == []


def test_bitmap_export_reports_a_failed_save(window, monkeypatch, errors, tmp_path):
    _stub_png_dialog(monkeypatch, [str(tmp_path / "no_such_dir" / "d.png")])
    window._export_bitmap()
    assert errors == ["Export Failed"]


def test_scene_png_scale_defaults_to_the_window_setting(window, monkeypatch):
    seen = {}

    def fake(view, rect, scale):
        seen["scale"] = scale
        return "pixmap"
    monkeypatch.setattr("raptor.render.scene_pixmap.render_scene_to_pixmap", fake)
    assert window._render_scene_png(window._diagram_dock.view.sceneRect()) == "pixmap"
    assert seen["scale"] == window._BITMAP_SCALE
    window._render_scene_png(window._diagram_dock.view.sceneRect(), scale=1)
    assert seen["scale"] == 1


def test_copy_view_puts_a_pixmap_on_the_clipboard(window):
    QApplication.clipboard().clear()
    window._copy_view_to_clipboard()
    assert not QApplication.clipboard().pixmap().isNull()
    assert "Copied view to clipboard" in _status(window)


# -------------------------------------------------------------- workbooks

@pytest.mark.parametrize("method,ext", [("_export_workbook_xlsx", ".xlsx"),
                                         ("_export_workbook_ods", ".ods")])
def test_workbook_export_writes_the_chosen_type(window, monkeypatch, tmp_path,
                                                method, ext):
    asked = {}

    def ask(parent, caption, default):
        asked["caption"], asked["default"] = caption, default
        return str(tmp_path / f"out{ext}")
    monkeypatch.setattr(fdg, "ask_spreadsheet_path", ask)
    getattr(window, method)()
    assert asked["default"].endswith(f"_tables{ext}")
    assert (tmp_path / f"out{ext}").stat().st_size > 0
    assert "Saved" in _status(window)


def test_workbook_export_cancel_writes_nothing(window, monkeypatch, tmp_path):
    monkeypatch.setattr(fdg, "ask_spreadsheet_path", lambda *a: None)
    window._export_workbook_xlsx()
    assert list(tmp_path.iterdir()) == []


def test_workbook_export_reports_a_write_failure(window, monkeypatch, errors, tmp_path):
    monkeypatch.setattr(fdg, "ask_spreadsheet_path",
                        lambda *a: str(tmp_path / "x.xlsx"))

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr("raptor.api.table_export.write_tables_workbook", boom)
    window._export_workbook_xlsx()
    assert QApplication.overrideCursor() is None     # the wait cursor is released
    assert errors == ["Export Failed"]
    assert "Workbook export failed" in _status(window)


def test_workbook_export_does_nothing_without_a_model(window, monkeypatch):
    window._rbd = None
    monkeypatch.setattr(fdg, "ask_spreadsheet_path",
                        lambda *a: pytest.fail("must not ask"))
    window._export_workbook_xlsx()


# -------------------------------------------------------------------- PDF

def test_pdf_export_cancelled_at_the_options_dialog(window, monkeypatch):
    monkeypatch.setattr("raptor.gui.ui.printing._prompt_export_options",
                        lambda parent, title: None)
    monkeypatch.setattr(fdg, "getSaveFileName",
                        lambda *a, **k: pytest.fail("must not ask for a path"))
    window._export_pdf()


def test_pdf_export_failure_shows_a_dialog_and_resets_state(window, monkeypatch,
                                                            errors, tmp_path):
    monkeypatch.setattr("raptor.gui.ui.printing._prompt_export_options",
                        lambda parent, title: object())

    def boom(win, path, opts):
        raise RuntimeError("renderer exploded")
    monkeypatch.setattr("raptor.gui.ui.report_pdf.export_pdf", boom)
    _pick(monkeypatch, str(tmp_path / "r.pdf"))
    window._export_pdf()
    assert errors == ["Export PDF Failed"]
    assert window._export_in_progress is False       # reset by `finally`
    assert QApplication.overrideCursor() is None     # cursor restored too
    assert "PDF export failed" in _status(window)


def test_pdf_export_success_reports_the_file(window, monkeypatch, tmp_path):
    monkeypatch.setattr("raptor.gui.ui.printing._prompt_export_options",
                        lambda parent, title: object())
    calls = []
    monkeypatch.setattr("raptor.gui.ui.report_pdf.export_pdf",
                        lambda win, path, opts: calls.append(path))
    _pick(monkeypatch, str(tmp_path / "r.pdf"))
    window._export_pdf()
    assert calls == [str(tmp_path / "r.pdf")]
    assert "PDF saved: r.pdf" in _status(window)

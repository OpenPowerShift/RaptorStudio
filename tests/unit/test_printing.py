"""Print / Export PDF.

Both make one report (`report_pdf` gathers it, `raptor.api.report_html`
lays it out); Print sends its pages to the printer. Tests cover the shared
section picker, the report with every section and with none, Print, and
the diagram's "pages across"."""
from __future__ import annotations

import os

import pytest
from raptor.gui.ui import filedialogs as _fdg

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtPrintSupport import QPrintDialog, QPrinter

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow
from raptor.gui.ui import printing
from raptor.gui.ui import report_pdf

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOLD = os.path.join(REPO, "tests", "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def w(qapp):
    win = MainWindow()
    win._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")
    win._run_count_spin.setValue(1)
    win._run_simulation()
    QApplication.instance().processEvents()
    return win


def test_open_print_dialog_exists_and_completes_without_crashing(
        w, monkeypatch):
    """`open_print_dialog` reaches `report_pdf.print_report`."""
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(QPrintDialog, "exec",
                        lambda self: QPrintDialog.DialogCode.Accepted)
    captured = {}
    monkeypatch.setattr(report_pdf, "print_report",
                        lambda win, printer, opts: captured.update(opts=opts))
    printing.open_print_dialog(w)
    assert "opts" in captured, "print_report was never reached"
    assert captured["opts"]["diagram"] is True


def test_prompt_export_options_covers_every_result_surface(w, monkeypatch):
    """The shared picker offers the full section set; after a run every
    section is ticked except the two long ones (event log, raw reports)."""
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    opts = printing._prompt_export_options(w, "Test")
    for key in ("diagram", "results", "weak", "graphs"):
        assert opts[key] is True, key
    assert opts["event_log"] is False and opts["raw"] is False
    assert opts["orientation"] == "portrait"


def test_prompt_export_options_disables_run_dependent_sections_when_empty(
        qapp, monkeypatch):
    from raptor.gui.ui.main_window import MainWindow
    empty_w = MainWindow()
    empty_w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")
    # no run performed -- _report_data is empty

    captured_checks = {}

    def fake_exec(self):
        from PySide6.QtWidgets import QCheckBox
        for cb in self.findChildren(QCheckBox):
            captured_checks[cb.text()] = cb.isEnabled()
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    opts = printing._prompt_export_options(empty_w, "Test")
    assert opts["diagram"] is True       # never gated on a run
    assert opts["results"] is False      # gated, and unchecked when disabled
    assert opts["graphs"] is False


def test_export_pdf_renders_every_section(w, tmp_path):
    opts = {"diagram": True, "data_tables": True, "results": True,
            "weak": True, "graphs": True, "event_log": True, "raw": True,
            "name": "Test", "revision": "1", "date": "2026-08-30"}
    path = tmp_path / "report.pdf"
    report_pdf.export_pdf(w, str(path), opts)
    assert path.read_bytes()[:5] == b"%PDF-"
    import tempfile
    from pathlib import Path
    from raptor.api.report_html import build_html
    with tempfile.TemporaryDirectory() as td:
        html = build_html(report_pdf.gather(w, opts, Path(td)))
    for heading in ("Diagram", "Model data", "Results", "Weak link analysis",
                    "Event log", "Raw reports"):
        assert f">{heading}</h2>" in html, heading
    assert "Availability" in html and "Run on" in html


def test_export_pdf_handles_no_results_gracefully(qapp, tmp_path):
    """Every section requested but no run performed yet -- the renderer
    must still produce a valid (mostly placeholder-text) PDF, not crash."""
    from raptor.gui.ui.main_window import MainWindow
    empty_w = MainWindow()
    empty_w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")
    opts = {"diagram": True, "results": True, "weak": True,
           "graphs": True, "event_log": True, "raw": True,
           "name": "", "revision": "", "date": ""}
    path = str(tmp_path / "empty_report.pdf")
    report_pdf.export_pdf(empty_w, path, opts)
    assert os.path.exists(path)
    assert os.path.getsize(path) > 0


def test_main_window_export_pdf_uses_shared_options_dialog(w, monkeypatch, tmp_path):
    """`_export_pdf` goes through the same options picker as Print."""
    called = {"prompted": False}

    def fake_prompt(win, title):
        called["prompted"] = True
        return {"diagram": True, "results": False, "weak": False,
               "graphs": False, "event_log": False, "raw": False,
               "name": "", "revision": "", "date": ""}
    monkeypatch.setattr(printing, "_prompt_export_options", fake_prompt)
    path = str(tmp_path / "via_ribbon.pdf")
    monkeypatch.setattr(_fdg, "getSaveFileName",
                        staticmethod(lambda *a, **kw: (path, "")))
    w._export_pdf()
    assert called["prompted"], "_export_pdf must prompt for section options"
    assert os.path.exists(path)


# ---------------------------------------------------------------------
# Print: the report's pages, one sheet each; and "Pages across".
# ---------------------------------------------------------------------

class _CountingPrinter(QPrinter):
    """A real `QPrinter` that also counts `newPage()` calls."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.new_page_calls = 0

    def newPage(self):
        self.new_page_calls += 1
        return super().newPage()


def test_print_puts_each_report_page_on_its_own_sheet(w, tmp_path):
    opts = {"diagram": True, "data_tables": True, "diagram_tiles": 1,
            "name": "x", "revision": "1", "date": "2026"}
    pdf = tmp_path / "report.pdf"
    report_pdf.export_pdf(w, str(pdf), opts)
    from PySide6.QtPdf import QPdfDocument
    doc = QPdfDocument()
    doc.load(str(pdf))
    pages = doc.pageCount()
    doc.close()
    printer = _CountingPrinter(QPrinter.PrinterMode.ScreenResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "printed.pdf"))
    report_pdf.print_report(w, printer, opts)
    assert pages >= 3                       # cover, contents, diagram ...
    assert printer.new_page_calls + 1 == pages


def test_pages_across_splits_each_level_into_that_many_images(w, tmp_path):
    one = report_pdf.render_diagrams(w, tmp_path, tiles=1)
    three = report_pdf.render_diagrams(w, tmp_path, tiles=3)
    assert len(three) == 3 * len(one)
    assert [(f.part, f.of) for f in three[:3]] == [(1, 3), (2, 3), (3, 3)]
    assert three[0].title == one[0].title


def test_prompt_export_options_exposes_diagram_tiles(w, monkeypatch):
    """The "Pages across" spinbox must round-trip through opts."""
    from PySide6.QtWidgets import QSpinBox

    def fake_exec(self):
        for sp in self.findChildren(QSpinBox):
            sp.setValue(4)
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    opts = printing._prompt_export_options(w, "Print")
    assert opts["diagram_tiles"] == 4


def test_prompt_export_options_defaults_diagram_tiles_to_one(w, monkeypatch):
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    opts = printing._prompt_export_options(w, "Print")
    assert opts["diagram_tiles"] == 1


# ---------------------------------------------------------------------
# SVG diagram export -- the same content-rect measurement as the
# report's diagram pages, targeting a QSvgGenerator paint device. No options dialog: a diagram export has exactly one sensible
# output (the current view), unlike Print/Export PDF's section picker.
# ---------------------------------------------------------------------

def test_export_diagram_svg_writes_real_vector_content(w, monkeypatch,
                                                        tmp_path):
    path = str(tmp_path / "diagram.svg")
    monkeypatch.setattr(_fdg, "getSaveFileName",
                        staticmethod(lambda *a, **kw: (path, "")))
    w._export_diagram_svg()
    assert os.path.exists(path)
    text = open(path, encoding="utf-8").read()
    assert text.startswith("<?xml")
    assert "<svg" in text and "viewBox" in text
    assert os.path.getsize(path) > 1000   # not just an empty shell


def test_export_diagram_svg_cancel_does_not_write_a_file(w, monkeypatch,
                                                          tmp_path):
    monkeypatch.setattr(_fdg, "getSaveFileName",
                        staticmethod(lambda *a, **kw: ("", "")))
    w._export_diagram_svg()
    assert list(tmp_path.iterdir()) == []


def test_export_svg_action_enabled_after_load(w):
    assert w._export_svg_action.isEnabled()


def test_export_svg_action_disabled_before_any_model_loaded(qapp):
    empty_w = MainWindow()
    assert not empty_w._export_svg_action.isEnabled()

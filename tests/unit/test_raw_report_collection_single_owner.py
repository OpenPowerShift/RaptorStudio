"""Raw-report collection has one owner.

The Results dock's raw report views (End of Run, Final Results, Interim
Cost) are gathered in one place, `printing.collect_raw_report_sections`:
it reaches into `win._results._raw_views`, adds the interim cost view and
drops empty views. The PDF report (`report_pdf`, which Print also uses)
takes its raw reports from it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from raptor.gui.ui.printing import collect_raw_report_sections

REPO = Path(__file__).resolve().parent.parent.parent


class _View:
    """Stands in for a `QTextEdit`-shaped results view."""

    def __init__(self, text: str) -> None:
        self._text = text

    def toPlainText(self) -> str:
        return self._text


class _Results:
    def __init__(self, raw_views, interim=None) -> None:
        self._raw_views = raw_views
        self._interim_cost_view = interim


class _Win:
    def __init__(self, results) -> None:
        self._results = results


def _win(raw_views=None, interim=None) -> _Win:
    return _Win(_Results(raw_views or {}, interim))


class TestCollection:
    def test_a_view_becomes_a_titled_section(self):
        got = collect_raw_report_sections(
            _win({"End of Run": _View("line one\nline two")}))
        assert got == [("Raw Report — End of Run", ["line one", "line two"])]

    def test_several_views_keep_their_order(self):
        got = collect_raw_report_sections(_win({
            "End of Run": _View("a"),
            "Final Results": _View("b"),
        }))
        assert [t for t, _ in got] == ["Raw Report — End of Run",
                                       "Raw Report — Final Results"]

    def test_the_interim_view_is_injected(self):
        """`_interim_cost_view` is a separate attribute from `_raw_views`,
        so it has to be added explicitly."""
        got = collect_raw_report_sections(
            _win({"End of Run": _View("a")}, interim=_View("interim")))
        assert "Raw Report — Interim Cost" in [t for t, _ in got]

    def test_an_empty_view_is_dropped(self):
        got = collect_raw_report_sections(_win({
            "End of Run": _View("a"),
            "Final Results": _View("   \n  "),      # whitespace only
        }))
        assert [t for t, _ in got] == ["Raw Report — End of Run"]

    def test_the_text_is_not_reflowed(self):
        """Byte-exact engine output: a long line stays one line."""
        line = "x" * 300
        got = collect_raw_report_sections(_win({"Final Results": _View(line)}))
        assert got[0][1] == [line]

    def test_no_views_gives_no_sections(self):
        """The collector returns EMPTY, not a placeholder: the report leaves
        out a section that has nothing in it."""
        assert collect_raw_report_sections(_win()) == []
        assert collect_raw_report_sections(
            _win({"End of Run": _View("")})) == []

    def test_a_missing_results_dock_is_not_an_error(self):
        """`getattr` on privates, so a dock that has not built the views yet
        must not raise."""
        win = type("W", (), {"_results": type("R", (), {})()})()
        assert collect_raw_report_sections(win) == []


class TestTheReportUsesIt:
    def test_the_report_delegates(self):
        import inspect

        from raptor.gui.ui import report_pdf
        src = inspect.getsource(report_pdf._raw_blocks)
        assert "collect_raw_report_sections" in src

    def test_the_interim_cost_section_reaches_the_report(self):
        from raptor.gui.ui.report_pdf import _raw_blocks
        blocks = _raw_blocks(_win({"End of Run": _View("final figures")},
                                  interim=_View("mid-run cost so far")))
        assert [b.title for b in blocks] == ["End of Run", "Interim Cost"]
        assert list(blocks[1].lines) == ["mid-run cost so far"]

    def test_the_text_reaches_the_report_as_written(self):
        from raptor.api.report_html import _raw_html
        from raptor.gui.ui.report_pdf import _raw_blocks
        html = _raw_html(_raw_blocks(_win({"End of Run": _View(
            "line one\n  line two")})))
        assert '<pre class="raw">line one\n  line two</pre>' in html


class TestOneOwner:
    FILES = ["raptor/gui/ui/printing.py", "raptor/gui/ui/report_pdf.py"]

    def test_the_collector_is_defined_once(self):
        found = []
        for rel in self.FILES:
            tree = ast.parse((REPO / rel).read_text())
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name in (
                        "collect_raw_report_sections", "_raw_report_sections"):
                    found.append(f"{rel}:{node.lineno}")
        assert len(found) == 1, f"the collector is defined {len(found)}x: {found}"

    @pytest.mark.parametrize("rel", FILES)
    def test_only_the_owner_reaches_into_the_raw_views(self, rel):
        """`win._results._raw_views` is the duplicated line. Counted as an
        AST attribute chain, not as text: the collector's docstring names
        it while explaining the contract."""
        # Count real attribute CHAINS, not string literals: the collection
        # is written `getattr(win._results, "_raw_views", ...)`, so a naive
        # Attribute scan finds nothing and a naive text scan finds the
        # docstring mention too. This looks for the attribute form.
        tree = ast.parse((REPO / rel).read_text())
        hits = [n.lineno for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and n.attr == "_raw_views"]
        hits += [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.Constant)
                 and isinstance(n.value, str)
                 and n.value == "_raw_views"]
        if rel.endswith("printing.py"):
            # printing owns it; exactly one place.
            assert len(hits) == 1, (
                f"printing.py reaches _raw_views {len(hits)} times: {hits}")
        else:
            assert not hits, (
                f"report_pdf.py:{hits} gathers the raw views itself; call "
                "printing.collect_raw_report_sections")

    def test_the_interim_injection_exists_once(self):
        for rel in self.FILES:
            tree = ast.parse((REPO / rel).read_text())
            hits = [n.lineno for n in ast.walk(tree)
                    if isinstance(n, ast.Attribute)
                    and n.attr == "_interim_cost_view"]
            hits += [n.lineno for n in ast.walk(tree)
                     if isinstance(n, ast.Constant)
                     and isinstance(n.value, str)
                     and n.value == "_interim_cost_view"]
            expected = 1 if rel.endswith("printing.py") else 0
            assert len(hits) == expected, (
                f"{rel} touches _interim_cost_view {len(hits)} times "
                f"(expected {expected}): {hits}")

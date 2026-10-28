"""The Final_Results table parse has ONE owner: `api.tables`.

`gui.ui.results.ResultsDock` used to carry a second copy of
`parse_final_sections` (its own `_SECTION_RE` class attribute, its own
`value_re`, and a dead third regex, `_numeric`, that was never read and
lacked the `[eE][+-]?\\d+` exponent support the two live regexes carry).
The two docstrings also contradicted each other about row splitting, so a
maintainer trusting the GUI's would "fix" the API's and break it.

These tests pin the delegation: the same report text through the API and
through the GUI's Final Results path must produce the same sections, and
`results.py` must no longer contain a parse of its own. The
scientific-notation row is the discriminator -- the dead `_numeric`
regex would have dropped it, and with it the whole MTBDE row.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.api import tables as api_tables
from raptor.gui.ui.results import ResultsDock

RESULTS_PY = (Path(__file__).resolve().parents[2]
              / "raptor" / "gui" / "ui" / "results.py")

#: One report exercising every value shape the parser has to accept:
#: scientific notation, the report's own `>` censoring marker, `N/A` for a
#: figure the report declined to compute, and a NAMED-ROW table (the
#: Sparing section, whose header's first token IS the label column, so a
#: row has exactly len(columns) tokens rather than len(columns) + 1).
FINAL_LINES = [
    "Final_Results_Report for x.rbd  fixed",
    "Results from 8 runs",
    "***SUMMARY***",
    "              Minimum         Mean            Maximum         St.Dev          SEM",
    "Availability  0.746514202     0.752532575     0.758550947     0.008511264     0.006018372",
    "MTBDE(5-runs) 1.5e-05         412.512000      >21.230000      N/A            1.647180e-03",
    "MTBMs         N/A             N/A             N/A             N/A            N/A",
    "***SPARING***",
    "blockName   SpareSource   MinUsed   AveUsed   MaxUsed   StDev",
    "unnamed     Custom             3       3.00        3      0.00",
]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _gui_sections(qapp):
    """The GUI's own entry point, on a real dock (the adapter is a method,
    so its caller is `ResultsDock._populate_details`)."""
    dock = ResultsDock()
    return dock._parse_final_sections(list(FINAL_LINES))


# ---------------------------------------------------------------------
# Agreement: one parse, two front doors
# ---------------------------------------------------------------------

def test_api_and_gui_agree_on_every_section(qapp):
    assert _gui_sections(qapp) == api_tables.parse_final_sections(FINAL_LINES)


def test_api_and_gui_agree_on_a_report_with_no_named_row_table(qapp):
    """The second real shape: a stats table only, where a row is label +
    len(columns) values. Both doors must still agree."""
    stats_only = [ln for ln in FINAL_LINES
                  if not ln.startswith(("***SPARING", "blockName", "unnamed"))]
    dock = ResultsDock()
    assert dock._parse_final_sections(stats_only) == \
        api_tables.parse_final_sections(stats_only)


def test_the_exercised_cases_are_all_really_parsed():
    """Guard the fixture itself: if the report text stopped exercising
    scientific notation / `>` / `N/A` / a named-row table, the agreement
    tests above would pass while testing nothing."""
    sections = {title: (cols, rows)
                for title, cols, rows in api_tables.parse_final_sections(FINAL_LINES)}
    assert set(sections) == {"SUMMARY", "SPARING"}
    summary_cols, summary_rows = sections["SUMMARY"]
    assert summary_cols == ["Minimum", "Mean", "Maximum", "St.Dev", "SEM"]
    labels = [label for label, _ in summary_rows]
    assert labels == ["Availability", "MTBDE(5-runs)", "MTBMs"]
    mtbde = dict(summary_rows)["MTBDE(5-runs)"]
    assert mtbde == ["1.5e-05", "412.512000", ">21.230000", "N/A", "1.647180e-03"]
    # The named-row table: label + exactly len(columns) values.
    spare_cols, spare_rows = sections["SPARING"]
    assert spare_cols == ["blockName", "SpareSource", "MinUsed", "AveUsed",
                          "MaxUsed", "StDev"]
    assert spare_rows == [("unnamed", ["Custom", "3", "3.00", "3", "0.00"])]


# ---------------------------------------------------------------------
# The GUI's actual Final Results view
# ---------------------------------------------------------------------

def test_final_results_grid_shows_the_scientific_notation_row(qapp):
    """End to end through the tab the user reads: the MTBDE row survives,
    with its `>` marker, its `N/A` and its exponent-formatted values.

    This is the assertion the dead `_numeric` regex would have broken: it
    has no exponent group, so it rejects `1.5e-05`, the row fails the
    all-values-are-numeric test, and the row silently disappears from the
    Summary table."""
    dock = ResultsDock()
    dock._populate_details(list(FINAL_LINES))
    summary = dock.section_table("SUMMARY")
    rows = {summary.item(r, 0).text(): [summary.item(r, c).text()
                                        for c in range(1, summary.columnCount())]
            for r in range(summary.rowCount())}
    assert "MTBDE (5 runs)" in rows, \
        f"the exponent-formatted MTBDE row was dropped: {sorted(rows)}"
    assert rows["MTBDE (5 runs)"] == ["0.000015", "412.51", ">21.23",
                                      "N/A", "0.0016472"]
    # ... and the named-row table is tabbed and labelled as before.
    spare = dock.section_table("SPARING")
    assert spare.item(0, 0).text() == "unnamed"
    assert spare.item(0, 1).text() == "Custom"


# ---------------------------------------------------------------------
# Structural: results.py holds no parse of its own
# ---------------------------------------------------------------------

def _results_tree() -> ast.Module:
    return ast.parse(RESULTS_PY.read_text(encoding="utf-8"))


def _results_ast_find(*names: str) -> list[ast.AST]:
    """Every named function / class-level assignment anywhere in the file."""
    out: list[ast.AST] = []
    for node in ast.walk(_results_tree()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in names:
            out.append(node)
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id in names
                   for t in node.targets):
                out.append(node)
    return out


def test_results_defines_no_section_or_value_regex():
    """No `re.compile` anywhere in results.py. The section regex, the
    value regex and the dead third copy were the whole shadow parse."""
    tree = _results_tree()
    compiles = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "compile"]
    assert not compiles, [ast.unparse(n) for n in compiles]
    assert not _results_ast_find("_SECTION_RE", "_numeric", "value_re",
                                 "is_header", "flush")


def test_parse_final_sections_is_only_a_delegation():
    """The method survives (its caller replaces an entry in the returned
    list), but its body is a docstring and a single return of the API
    function -- no loop, no regex, no local state."""
    found = [n for n in _results_ast_find("_parse_final_sections")
             if isinstance(n, ast.FunctionDef)]
    assert len(found) == 1, "expected exactly one _parse_final_sections"
    (method,) = found
    body = method.body
    assert isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
        and isinstance(body[0].value.value, str), "expected a docstring first"
    assert len(body) == 2, [ast.unparse(s) for s in body]
    stmt = body[1]
    assert isinstance(stmt, ast.Return), ast.unparse(stmt)
    assert ast.unparse(stmt.value) == "parse_final_sections(lines)"


def test_results_imports_the_canonical_parser_from_the_api():
    imported = [alias.name
                for node in ast.walk(_results_tree())
                if isinstance(node, ast.ImportFrom)
                and node.module == "raptor.api.tables"
                for alias in node.names]
    assert "parse_final_sections" in imported


# ---------------------------------------------------------------------
# The docstrings document ONE row-splitting rule
# ---------------------------------------------------------------------

def test_the_two_docstrings_no_longer_contradict_each_other():
    api_doc = (api_tables.parse_final_sections.__doc__ or "").lower()
    gui_doc = (ResultsDock._parse_final_sections.__doc__ or "").lower()
    # The implementation takes the FIRST token as the label. Say so once,
    # canonically, and do not assert the opposite anywhere.
    assert "first" in api_doc and "last" not in api_doc
    assert "last" not in gui_doc
    # The GUI points at the canonical docstring rather than restating the
    # rule (restating it is how the two drifted apart).
    assert "api.tables.parse_final_sections" in gui_doc
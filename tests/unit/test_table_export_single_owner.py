"""Table extraction has one owner (review E-11).

"Turn this `QTableWidget` into headers plus rows" was written six times, and
the copies disagreed about two things that change the OUTPUT, not the taste:

1. **Precision.** `results.py` stashes a full-precision value on cells via
   `RAW_VALUE_ROLE` (`Qt.UserRole + 1`) -- written in four places, read by
   exactly **one** of the six. So *Copy Table* gave the exact double while
   *Export CSV*, *Print/PDF* and the *AsciiDoc report* gave the rounded grid
   text, **for the same table**.

2. **Cell widgets.** Only `data_tables.py` knew that distribution combos and
   parameter fields are `QComboBox`/`QLineEdit` cell *widgets* rather than
   items. The other five read `item(r, c).text()` unconditionally, so those
   columns exported **blank**.

Both defaults are minority behaviours here, which is why `table_to_grid`
defaults them **on**: making the one-correct-path the default is what stops
the other five from being quietly wrong.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from raptor.gui.ui.results import RAW_VALUE_ROLE
from raptor.gui.ui.table_export import (
    cell_text,
    grid_to_csv,
    grid_to_html,
    grid_to_tsv,
    table_to_grid,
)

REPO = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def table(qapp):
    """A table shaped like a Results-dock grid: a stashed raw value, an
    elided name in column 0, and a combo-box CELL WIDGET."""
    from PySide6.QtWidgets import QComboBox, QTableWidget, QTableWidgetItem
    t = QTableWidget(2, 3)
    t.setHorizontalHeaderLabels(["Block", "Availability", "Cost"])

    name = QTableWidgetItem("Solar_Array_Panel_AveryLongName")
    name.setData(RAW_VALUE_ROLE, "Solar_Array_Panel_AveryLongName")
    name.setText("Solar_Array_Pa…")
    t.setItem(0, 0, name)

    avail = QTableWidgetItem("1.0000000")
    avail.setData(RAW_VALUE_ROLE, 0.9999999999)   # 10 dp, display shows 7
    t.setItem(0, 1, avail)
    t.setItem(0, 2, QTableWidgetItem("12345.678"))

    t.setItem(1, 0, QTableWidgetItem("Pump"))
    combo = QComboBox()
    combo.addItems(["Uniform Real", "Gamma"])
    t.setCellWidget(1, 1, combo)                   # no item() at (1, 1)
    t.setItem(1, 2, QTableWidgetItem("7"))
    return t


class TestPrecision:
    def test_a_stashed_raw_value_wins_over_the_display_text(self, table, qapp):
        """THE BUG. Five of six paths printed the rounded text here."""
        assert cell_text(table, 0, 1, raw=True) == "0.9999999999"

    def test_raw_false_gives_the_display_text(self, table, qapp):
        assert cell_text(table, 0, 1, raw=False) == "1.0000000"

    def test_column_zero_gives_the_unelided_name(self, table, qapp):
        """Column 0 carries the full name in Qt.UserRole; the visible text
        is truncated with an ellipsis."""
        assert cell_text(table, 0, 0, raw=True) == "Solar_Array_Panel_AveryLongName"

    def test_a_cell_with_no_stashed_value_falls_back_to_text(self, table, qapp):
        assert cell_text(table, 0, 2, raw=True) == "12345.678"

    def test_the_grid_carries_the_precision(self, table, qapp):
        headers, rows = table_to_grid(table)
        assert rows[0][1] == "0.9999999999"
        assert headers == ["Block", "Availability", "Cost"]

    def test_hidden_rows_are_not_exported(self, table, qapp):
        table.setRowHidden(0, True)
        _headers, rows = table_to_grid(table)
        assert len(rows) == 1
        assert rows[0][0] != table.item(0, 0).text()

    def test_raw_is_on_by_default(self, table, qapp):
        """The default is the load-bearing part: five call sites relied on
        the rounded text, and one caller opting in is not a fix."""
        _headers, rows = table_to_grid(table)
        rows_off = table_to_grid(table, raw=False)[1]
        assert rows[0][1] != rows_off[0][1]


class TestCellWidgets:
    def test_a_combo_cell_is_read_through(self, table, qapp):
        """THE OTHER BUG. Five of six paths exported this column blank."""
        assert cell_text(table, 1, 1, raw=True) == "Uniform Real"

    def test_cell_widgets_can_be_switched_off(self, table, qapp):
        assert cell_text(table, 1, 1, cell_widgets=False) == ""

    def test_it_is_on_by_default(self, table, qapp):
        _headers, rows = table_to_grid(table)
        assert rows[1][1] == "Uniform Real"
        off = table_to_grid(table, cell_widgets=False)[1]
        assert off[1][1] == ""

    def test_a_line_edit_inside_a_container_is_found(self, qapp):
        """Parameter fields are a container holding a QLineEdit, not a bare
        QLineEdit."""
        from PySide6.QtWidgets import QLineEdit, QTableWidget, QWidget
        t = QTableWidget(1, 1)
        holder = QWidget()
        edit = QLineEdit("0.00015")
        # Qt must actually parent the child for findChild to reach it, so
        # this order matters: parent first, then install the holder.
        edit.setParent(holder)
        t.setCellWidget(0, 0, holder)
        assert cell_text(t, 0, 0) == "0.00015"


class TestSerialisers:
    def test_tsv(self, table, qapp):
        out = grid_to_tsv(*table_to_grid(table))
        assert out.splitlines()[0] == "Block\tAvailability\tCost"
        assert "0.9999999999" in out

    def test_csv_quotes_rather_than_hand_rolling(self, table, qapp):
        grid = table_to_grid(table)
        out = grid_to_csv(*grid)
        assert "0.9999999999" in out
        assert out.splitlines()[0].startswith("Block,Availability,Cost")

    def test_csv_quotes_an_embedded_comma(self, qapp):
        import csv as _csv
        import io
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
        t = QTableWidget(1, 1)
        t.setHorizontalHeaderLabels(["H"])
        t.setItem(0, 0, QTableWidgetItem("a,b"))
        headers, rows = table_to_grid(t)
        parsed = list(_csv.reader(io.StringIO(grid_to_csv(headers, rows))))
        assert parsed[1][0] == "a,b"

    def test_html(self, table, qapp):
        headers, rows = table_to_grid(table)
        out = grid_to_html(headers, rows)
        assert "<th>Block</th>" in out
        assert "<td>0.9999999999</td>" in out
        assert out.count("<tr>") == len(rows) + 1

    def test_html_escapes_markup(self, qapp):
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
        t = QTableWidget(1, 1)
        t.setHorizontalHeaderLabels(["H"])
        t.setItem(0, 0, QTableWidgetItem("a < b & c"))
        headers, rows = table_to_grid(t)
        out = grid_to_html(headers, rows)
        assert "&lt;" in out and "&amp;" in out
        assert "a < b" not in out


class TestNoSecondExtraction:
    """Structural. Six copies that mostly agree are indistinguishable from
    one copy by behaviour -- which is how this survived."""

    SITES = ["raptor/gui/ui/results.py", "raptor/gui/ui/printing.py",
             "raptor/gui/ui/report_pdf.py", "raptor/gui/ui/data_tables.py"]

    @pytest.mark.parametrize("rel", SITES)
    def test_no_site_walks_the_widget_by_hand(self, rel):
        tree = ast.parse((REPO / rel).read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute)
                    and node.attr in ("item", "horizontalHeaderItem")):
                continue
            # A call chain like table.item(r, c).text() is the shape.
            parent_text = getattr(node, "parent", None)
            if (isinstance(parent_text, ast.Attribute)
                    and parent_text.attr == "text"):
                pytest.fail(
                    f"{rel}:{node.lineno} reads a cell via "
                    f"`.item(...).text()`; use table_export.table_to_grid")

    #: Modules that READ the raw value, and so must import the constant
    #: rather than re-spelling `Qt.UserRole + 1`.
    READERS = ["raptor/gui/ui/table_export.py"]

    def test_a_reader_imports_the_raw_role(self):
        """`RAW_VALUE_ROLE` is written in `results.py` and must be READ from
        the same constant, not re-spelled.

        Scoped to the readers, not every module: `phasing_dialog.py` also
        defines a `Qt.UserRole + 1` constant, but it is a *different* role
        (its phase cell's value, with its own `_MODE_ROLE` alongside) that
        this extraction never touches. Collapsing those two would be a real
        bug -- they are two independent meanings on the same Qt role
        number, scoped to different tables.
        """
        for rel in self.READERS:
            src = (REPO / rel).read_text()
            tree = ast.parse(src)
            defines_own = any(
                isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "RAW_VALUE_ROLE"
                        for t in n.targets)
                for n in tree.body)
            assert not defines_own, (
                f"{rel} defines its own RAW_VALUE_ROLE; results.py owns it")
            assert "from raptor.gui.ui.results import RAW_VALUE_ROLE" in src, (
                f"{rel} must import the constant, not re-spell the role")
        # And nothing else reads `.data(RAW_VALUE_ROLE)` directly.
        for rel in self.SITES:
            tree = ast.parse((REPO / rel).read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "data"
                        and node.args
                        and isinstance(node.args[0], ast.Name)
                        and node.args[0].id == "RAW_VALUE_ROLE"):
                    # The WRITE sites in results.py are expected.
                    if rel.endswith("results.py"):
                        continue
                    pytest.fail(
                        f"{rel}:{node.lineno} reads RAW_VALUE_ROLE directly; "
                        "use table_export.cell_text so the two behaviours "
                        "(raw value, cell widgets) travel together")

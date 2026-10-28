"""One QTableWidget → grid extraction, and one set of serialisers.

"Turn this `QTableWidget` into headers plus rows" was written out six times,
and the copies disagreed about two things that change the output:

1. **Precision.** `results.py` stashes a full-precision value on cells via
   `RAW_VALUE_ROLE` (`Qt.UserRole + 1`) — written in four places, and read
   by exactly **one** of the six. So *Copy Table* gave the exact double
   while *Export CSV*, *Print/PDF* and the *AsciiDoc report* gave the
   rounded grid text, **for the same table**.

2. **Cell widgets.** Only `data_tables.py` knew that distribution combos
   and parameter fields are `QComboBox`/`QLineEdit` cell *widgets* rather
   than items. The other five read `item(r, c).text()` unconditionally, so
   those columns exported **blank**.

Neither is a formatting preference: (1) is a wrong number in a file the
user keeps, and (2) is a missing column. So the extraction lives here once,
with both behaviours on by default, and each caller supplies only the
serialiser — which is the one thing that genuinely *should* differ per
destination.

`RAW_VALUE_ROLE` is re-exported from `results.py` rather than duplicated,
so the constant has one home too.
"""
from __future__ import annotations

import csv
import io
from typing import Any

from PySide6.QtCore import Qt

#: Carries the full-precision value behind a display cell. Owned by
#: `results.py`, which is the only module that *writes* it; re-exported here
#: because this is the only module that reads it across call sites.
from raptor.gui.ui.results import RAW_VALUE_ROLE  # noqa: F401
from raptor.gui.ui.results import CENSOR_ROLE  # noqa: F401

__all__ = [
    "CENSOR_ROLE",
    "RAW_VALUE_ROLE",
    "cell_text",
    "grid_to_csv",
    "grid_to_html",
    "grid_to_tsv",
    "table_to_grid",
]



def _widget_text(w: Any) -> str:
    """Best-effort text from a cell *widget*.

    Data Tables puts a `QComboBox` in the distribution columns and a
    `QLineEdit`-bearing editor in the parameter columns. Reading
    `item(r, c).text()` for those yields `None` -- or, worse, an empty
    string, which is indistinguishable from a genuinely empty cell.
    """
    from PySide6.QtWidgets import QComboBox, QLineEdit
    if isinstance(w, QComboBox):
        return w.currentText()
    if w is not None:
        edit = w.findChild(QLineEdit)
        if edit is not None:
            return edit.text()
    return ""


def cell_text(table: Any, r: int, c: int, *,
              raw: bool = True, cell_widgets: bool = True) -> str:
    """One cell, as text.

    *raw* prefers a stashed full-precision value over the rounded display
    text, and falls back to `Qt.UserRole` for column 0 -- which carries the
    un-elided element name, where the visible text may be truncated with an
    ellipsis. *cell_widgets* reads through to a `QComboBox`/`QLineEdit` when
    the cell holds no item.
    """
    item = table.item(r, c)
    if item is not None:
        if raw:
            stashed = item.data(RAW_VALUE_ROLE)
            if stashed is not None:
                return str(stashed)
            if c == 0:
                # `Qt.ItemDataRole.UserRole`, not the bare `Qt.UserRole`
                # alias: same value (256), but the bare form is absent from
                # PySide6's type stubs, so mypy rejects it. `results.py` uses
                # the bare form and has the same latent error -- it is simply
                # already counted in that file's baseline, which is exactly
                # how a new file inherits a rule nobody wrote down.
                full = item.data(Qt.ItemDataRole.UserRole)
                if full:
                    return str(full)
        return item.text()
    if cell_widgets:
        return _widget_text(table.cellWidget(r, c))
    return ""


def table_to_grid(table: Any, *, raw: bool = True,
                  cell_widgets: bool = True) -> tuple[list[str],
                                                     list[list[str]]]:
    """`table` as `(headers, rows)`, both all-strings.

    *raw* and *cell_widgets* default to **True** because both defaults were
    the minority: only one of six sites honoured the raw value and only one
    knew about cell widgets. Defaulting them on is what makes the remaining
    five paths correct rather than merely consistent.
    """
    column_count = table.columnCount()
    headers = []
    for c in range(column_count):
        head = table.horizontalHeaderItem(c)
        headers.append(head.text() if head is not None else "")
    # Respect the active hierarchy/filter: exporting rows the user cannot
    # see made a 3-row filtered view copy all 16 underlying blocks.
    visible = [r for r in range(table.rowCount())
               if not table.isRowHidden(r)]
    rows = [[cell_text(table, r, c, raw=raw, cell_widgets=cell_widgets)
             for c in range(column_count)]
            for r in visible]
    # A ">" display prefix is not a number. Keep the numeric value in its
    # column and add a parallel flag column so a spreadsheet can both sum
    # the value and know it is a lower bound.
    censor_cols = [
        c for c in range(column_count)
        if any((table.item(r, c) is not None
                and table.item(r, c).data(CENSOR_ROLE))
               for r in visible)
    ]
    if censor_cols:
        headers = headers + [f"{headers[c]} (censored)" for c in censor_cols]
        for row, r in zip(rows, visible):
            row.extend(
                ">" if (table.item(r, c) is not None
                        and table.item(r, c).data(CENSOR_ROLE)) else ""
                for c in censor_cols)
    return headers, rows


def grid_to_tsv(headers: list[str], rows: list[list[str]]) -> str:
    """Tab-separated, which is what the clipboard path wants."""
    return "\n".join(["\t".join(headers)]
                     + ["\t".join(r) for r in rows])


def grid_to_csv(headers: list[str], rows: list[list[str]]) -> str:
    """RFC-4180 CSV as a string (quoting handled by `csv`, not by hand)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue()


def _escape(text: str) -> str:
    """Minimal HTML text escaping.

    Deliberately not `html.escape` with quote=True: these are element and
    <i>text</i> fragments, so the only real hazards are `<`, `>` and `&`.
    Quoting `"` and `'` here would visibly corrupt names containing an
    apostrophe, which this codebase has plenty of.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def grid_to_html(headers: list[str], rows: list[list[str]]) -> str:
    out = ["<table border='1' cellspacing='0' cellpadding='3'>",
           "<tr>" + "".join(f"<th>{_escape(h)}</th>" for h in headers)
           + "</tr>"]
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{_escape(c)}</td>" for c in r)
                   + "</tr>")
    out.append("</table>")
    return "".join(out)

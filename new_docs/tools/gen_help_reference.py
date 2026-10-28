"""Generate a first-draft UI reference (docs/manual-reference.adoc) by
introspecting the live GUI: ribbon pages/groups/actions with their
tooltips and shortcuts, plus Data Tables column sets. Human editors
flesh out docs/manual.adoc around this generated include; re-run and
diff after GUI changes to catch drift.

Usage (from repo root): .venv/bin/python new_docs/tools/gen_help_reference.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "docs",
                   "manual-reference.adoc")


def esc(s):
    return (s or "").replace("|", "\\|").strip()


def control_name(widget, group):
    """What the user calls a field: its accessible name, its own text, or
    the label written beside it -- never the Qt class name."""
    from PySide6.QtWidgets import QAbstractButton, QLabel
    name = widget.accessibleName()
    if not name and isinstance(widget, QAbstractButton):
        name = widget.text().replace("&", "")
    if not name:
        centre = widget.mapTo(group, widget.rect().center())
        best = None
        for lab in group.findChildren(QLabel):
            if not lab.text().strip() or lab.isHidden():
                continue
            lc = lab.mapTo(group, lab.rect().center())
            gap = widget.mapTo(group, widget.rect().topLeft()).x() - (
                lab.mapTo(group, lab.rect().topRight()).x())
            if abs(lc.y() - centre.y()) < 10 and gap >= -2:
                if best is None or gap < best[0]:
                    best = (gap, lab.text())
        if best and best[1].strip().rstrip(":") not in ("",):
            name = best[1].strip().rstrip(":")
    if not name:
        name = {"QSpinBox": "Number field", "QDoubleSpinBox": "Number field",
                "QComboBox": "Choice", "QCheckBox": "Tick box",
                "QLineEdit": "Text field"}.get(type(widget).__name__, "Field")
    return name


def _file_view_lines(w, esc, base_tooltip) -> list[str]:
    """The File view (the File button left of the tabs)."""
    out: list[str] = []
    from raptor.gui.command_table import BACKSTAGE, BACKSTAGE_ACTIONS
    out.append("=== File view")
    out.append("")
    out.append("The File button, left of the tabs, opens a full-page view:")
    out.append("a searchable list of recent files, the file commands,")
    out.append("Print and Export, and a top level AI Assistant link to the")
    out.append("documentation.")
    out.append("")

    def table(title, ids):
        out.append(f"==== {title}")
        out.append("")
        out.append("[cols=\"2,4,1\",options=\"header\"]")
        out.append("|===")
        out.append("|Control |What it does |Shortcut / value")
        for cid in ids:
            act = w._commands.action(cid)
            name = act.text().replace("&", "")
            sc = act.shortcut().toString() if act.shortcut() else ""
            out.append(f"|{esc(name)} |{esc(base_tooltip(act))} |{esc(sc)}")
        out.append("|===")
        out.append("")

    for section, ids in BACKSTAGE:
        table(section, ids)
    if BACKSTAGE_ACTIONS:
        table("AI Assistant", BACKSTAGE_ACTIONS)
    return out


def generate_lines():
    """Build the reference doc's lines by introspecting a live
    MainWindow. Pure (no file I/O) so a drift-check test can call this
    and compare against the checked-in file without writing anything."""
    from PySide6.QtWidgets import (QApplication, QLineEdit,
                                   QSpinBox, QDoubleSpinBox, QComboBox,
                                   QCheckBox, QToolButton, QAbstractSpinBox)

    app = QApplication.instance() or QApplication([])
    from raptor.gui.commands import base_tooltip
    from raptor.gui.ui.main_window import MainWindow
    from raptor.gui.widgets.ribbon import RibbonGroup
    w = MainWindow()
    w.show()
    app.processEvents()

    lines = []
    lines.append("[[generated-ui-reference]]")
    lines.append("== Every ribbon control")
    lines.append("")
    lines.append("Each ribbon tab, group by group: the control's name, what its")
    lines.append("tooltip says, and its shortcut (or, for a field, its kind and")
    lines.append("current value).")
    lines.append("")

    # The cores control states this machine's core count; the reference must
    # read the same wherever it is generated (CI runners have 2 cores).
    from raptor.gui import cores
    cores_spin = getattr(w, "_cores_spin", None)

    ribbon = w._ribbon
    for i in range(ribbon.tab_bar.count()):
        title = ribbon.tab_bar.tabText(i)
        page = ribbon._pages[i]
        lines.append(f"=== {title} tab")
        lines.append("")
        for group in page.findChildren(RibbonGroup):
            gname = group._label.text()
            rows = []
            for child in group.findChildren(QToolButton):
                act = child.defaultAction()
                if act is None:
                    continue
                name = act.text().replace("&", "") or type(child).__name__
                tip = esc(base_tooltip(act))
                sc = act.shortcut().toString() if act.shortcut() else ""
                rows.append((name, tip, sc))
            for wtype in (QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox,
                          QLineEdit):
                for widget in group.findChildren(wtype):
                    # The text box inside a number field or an editable
                    # choice is part of that control, not a control.
                    if (isinstance(widget, QLineEdit)
                            and isinstance(widget.parent(),
                                           (QAbstractSpinBox, QComboBox))):
                        continue
                    tip = esc(base_tooltip(widget))
                    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                        label = f"number, now {widget.value():g}"
                    elif isinstance(widget, QComboBox):
                        label = (f"choice, now {widget.currentText()}" if widget.currentText() else "choice")
                    elif isinstance(widget, QCheckBox):
                        label = "tick box"
                    else:
                        label = "text"
                    if widget is cores_spin:
                        tip = tip.replace(f"(this machine has {cores.available()})",
                                          "(by default, every core this machine has)")
                        label = "number, every core by default"
                    rows.append((control_name(widget, group), tip, label))
            if not rows:
                continue
            lines.append(f"==== {gname}")
            lines.append("")
            lines.append("[cols=\"2,4,1\",options=\"header\"]")
            lines.append("|===")
            lines.append("|Control |What it does |Shortcut / value")
            for name, tip, extra in rows:
                lines.append(f"|{esc(name)} |{tip} |{esc(extra)}")
            lines.append("|===")
            lines.append("")

    lines += _file_view_lines(w, esc, base_tooltip)

    # Data Tables columns
    from raptor.gui.ui.data_tables import DataTablesDock
    cols = DataTablesDock._BLOCK_COLS
    lines.append("=== Data Tables columns")
    lines.append("")
    lines.append("[cols=\"2,8\",options=\"header\"]")
    lines.append("|===")
    lines.append("|Tab |Columns")
    lines.append(f"|Blocks |{esc(', '.join(cols))}")
    lines.append("|===")
    lines.append("")
    return lines


def main():
    lines = generate_lines()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("wrote", OUT, f"({len(lines)} lines)")


if __name__ == "__main__":
    main()

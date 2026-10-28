"""Distribution combos have one owner and one display-name rule (E-14).

Ten near-identical `QComboBox` population loops existed, and the code-18
`"None (unrepairable)"` relabel was applied **four different ways**:

| Site | Rule |
|---|---|
| `inspector.py`'s combo fill | **conditional** — relabel only when *not* a failure law |
| `dialogs.py` (×2 repair combos) | **unconditional** |
| `inspector.py`'s read-only `_row` branch | a **fourth** rendering, testing `== 18` separately |
| `data_tables.py::_distro_cell` | **no relabel at all** |

So the Repair distribution read *"None (unrepairable)"* in the Inspector and
two dialogs, but the raw *"None"* in the **Data Tables** grid — the same
block, on the same screen, under two names for one value.

The conditional rule is the correct one for a reason that is about the
format rather than taste: code 18 means *a block that cannot fail* as a
failure law, and *a block that fails and never comes back* as a repair law.
Relabelling it unconditionally tells the user "cannot fail" in a combo that
is choosing a repair law.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from raptor.core.distro_rules import DISTRO_NAMES
from raptor.gui.ui.distro_combo import distro_display_name, fill_distro_combo

REPO = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class TestDistroDisplayName:
    """The rule, once."""

    def test_code_18_is_relabelled_for_a_repair_law(self):
        assert distro_display_name(18, is_failure=False) == "None (unrepairable)"

    def test_code_18_keeps_its_name_for_a_failure_law(self):
        """THE POINT. A failure law's "None" means the block cannot fail,
        which is already the clearest possible name for it."""
        assert distro_display_name(18, is_failure=True) == "None"

    @pytest.mark.parametrize("code", sorted(c for c in DISTRO_NAMES if c != 18))
    def test_no_other_distribution_is_affected(self, code):
        """Only 18 is special; every other name is identical either way."""
        assert distro_display_name(code, is_failure=True) == \
            distro_display_name(code, is_failure=False) == DISTRO_NAMES[code]

    def test_an_unknown_code_is_named_rather_than_blanked(self):
        name = distro_display_name(999, is_failure=True)
        assert "999" in name, (
            "an unknown code must be visible in the combo, not an empty "
            "entry the user cannot identify")


class TestFillDistroCombo:
    def _combo(self):
        from PySide6.QtWidgets import QComboBox
        return QComboBox()

    def test_a_failure_combo_offers_the_failure_list(self, qapp):
        from raptor.core.distro_rules import OFFERED
        c = fill_distro_combo(self._combo(), 4, is_failure=True)
        assert [c.itemData(i) for i in range(c.count())] == list(OFFERED["failure"])

    def test_it_selects_the_requested_code(self, qapp):
        c = fill_distro_combo(self._combo(), 15, is_failure=True)
        assert c.currentData() == 15

    def test_a_repair_combo_relabels_18(self, qapp):
        c = fill_distro_combo(self._combo(), 4, is_failure=False)
        texts = [c.itemText(i) for i in range(c.count())]
        assert "None (unrepairable)" in texts

    def test_a_failure_combo_does_not(self, qapp):
        c = fill_distro_combo(self._combo(), 4, is_failure=True)
        texts = [c.itemText(i) for i in range(c.count())]
        assert "None (unrepairable)" not in texts

    def test_the_repair_combo_adds_unrepairable(self, qapp):
        """Raptor 7's RepairCombo is its FailCombo plus Fixed and None;
       this port's failure list also has
        Fixed, so the repair list adds only None, which is relabelled."""
        fail = fill_distro_combo(self._combo(), 4, is_failure=True)
        rep = fill_distro_combo(self._combo(), 4, is_failure=False)
        f = {fail.itemData(i): fail.itemText(i) for i in range(fail.count())}
        r = {rep.itemData(i): rep.itemText(i) for i in range(rep.count())}
        assert set(r) - set(f) == {18}
        assert r[18] == "None (unrepairable)" and r[19] == f[19] == "Fixed"
        assert all(r[k] == f[k] for k in f)

    def test_none_means_propose_a_default_not_a_warning(self, qapp):
        """A Mass-Edit combo offers the first entry as a proposal. That is
        not a mistake, so it must not be labelled like a corrupt file."""
        c = fill_distro_combo(self._combo(), None, is_failure=True)
        assert c.currentIndex() == 0
        assert c.toolTip() == "", (
            "a proposal-default combo is not an unknown-code situation")

    def test_an_unknown_code_is_selected_to_zero_AND_flagged(self, qapp):
        """A hand-edited file with a code that does not exist. Landing on
        the first entry silently would make it look valid."""
        c = fill_distro_combo(self._combo(), 999, is_failure=True)
        assert c.currentIndex() == 0
        assert "999" in c.toolTip(), (
            "an unknown code must warn, or the next save silently replaces "
            "the model's distribution")
        assert "replace" in c.toolTip().lower()


class TestTheGridAndTheInspectorAgree:
    """The user-visible half: one block, one screen, one name."""

    def _beta_repair_block_view(self, qapp):
        """A window whose selected block's REPAIR distribution is code 18."""
        from raptor.gui.ui.main_window import MainWindow
        from raptor.core.fileio.rbd import load_rbd
        win = MainWindow()
        rbd = load_rbd(str(REPO / "tests" / "golden" / "TwoBlockRedundancy"
                           / "input.rbd"))
        win._load_into_app(rbd, "x")
        view = win._diagram_dock.view
        bid = next(iter(view.blocks))
        view.blocks[bid].repair_distro = 18
        return win, view, bid

    def test_data_tables_relabels_the_repair_cell(self, qapp):
        """THE BUG. `_distro_cell` applied no relabel, so this grid showed
        "None" where the Inspector showed "None (unrepairable)".

        Checked on the call site rather than through a live grid: the point
        is the *argument*, and a rendered grid would only show it for a
        block whose repair distribution happens to be 18."""
        import inspect

        from raptor.gui.ui import data_tables
        src = inspect.getsource(data_tables)
        assert "for code in sorted(DISTRO_NAMES)" not in src, (
            "data_tables has grown its own combo loop again")
        assert "fill_distro_combo" in src
        assert "is_failure=False" in src, (
            "the repair column must be filled with is_failure=False, or "
            "the grid goes back to disagreeing with the Inspector")

    def test_the_read_only_repair_row_uses_the_shared_label(self, qapp):
        """The FOURTH copy: `_populate_repair` computed `_name` twice and
        never read it -- dead since an earlier refactor -- while still
        retyping the code-18 literal."""
        import ast
        import inspect
        import textwrap

        from raptor.gui.ui import inspector
        src = inspect.getsource(inspector.InspectorDock._populate_repair)
        assert "None (unrepairable)" not in src, (
            "the read-only repair row retypes the label instead of "
            "importing distro_combo._UNREPAIRABLE")
        tree = ast.parse(textwrap.dedent(src))
        stores = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Name) and n.id == "_name"
                  and isinstance(n.ctx, ast.Store)]
        loads = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Name) and n.id == "_name"
                 and isinstance(n.ctx, ast.Load)]
        assert not stores or loads, (
            f"_name is assigned {len(stores)} time(s) and read "
            f"{len(loads)} -- dead code")


class TestNoSecondLoop:
    """Structural. Ten copies that agree are indistinguishable from one
    copy by behaviour, which is why this needs an AST check."""

    FILES = ["distro_editor.py", "inspector.py", "data_tables.py",
             "dialogs.py"]

    @pytest.mark.parametrize("fname", FILES)
    def test_no_module_populates_a_combo_by_hand(self, fname):
        path = REPO / "raptor" / "gui" / "ui" / fname
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute)
                    and node.func.attr == "addItem"):
                continue
            # `addItem(...)` directly inside a `for ... in sorted(DISTRO_NAMES)`
            # loop is the duplicated shape.
            offenders = [n for n in ast.walk(tree)
                         if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Attribute)
                         and n.func.attr == "addItem"
                         and "DISTRO_NAMES" in ast.dump(n)]
            assert not offenders, (
                f"{fname} still builds a distro combo by hand: line "
                f"{offenders[0].lineno}; call "
                "gui.ui.distro_combo.fill_distro_combo instead")
            return

    def test_only_the_owner_populates_a_combo(self):
        owner = (REPO / "raptor" / "gui" / "ui" / "distro_combo.py").read_text()
        assert "addItem" in owner
        for fname in self.FILES:
            path = REPO / "raptor" / "gui" / "ui" / fname
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "addItem"
                        and "None (unrepairable)" in ast.dump(node)):
                    pytest.fail(
                        f"{fname} still applies the code-18 relabel "
                        f"inline (line {node.lineno})")

    def test_the_display_name_string_lives_in_one_place(self):
        """The literal must not be retyped elsewhere.

        Counted as string CONSTANTS, not as text: `distro_combo.py`'s own
        docstring names the literal when explaining what it replaced, and a
        text count flags the explanation instead of a second copy. My first
        version of this test did exactly that and failed for the wrong
        reason.
        """
        for fname in self.FILES:
            path = REPO / "raptor" / "gui" / "ui" / fname
            tree = ast.parse(path.read_text())
            literals = [n.value for n in ast.walk(tree)
                        if isinstance(n, ast.Constant)
                        and isinstance(n.value, str)
                        and "None (unrepairable)" in n.value]
            assert not literals, (
                f"{fname} retypes the code-18 label at line(s) "
                f"{[n.lineno for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and 'None (unrepairable)' in n.value]}; "
                "it belongs in distro_combo._UNREPAIRABLE")

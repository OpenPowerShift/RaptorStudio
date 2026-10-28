"""Each distribution picker offers Raptor 7's own list for that picker
(`raptor/core/distro_rules.py` RAPTOR7_LISTS, from Raptor 7's combo item
lists), plus Fixed for a failure law
(`OFFERED`), which the API also accepts."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from raptor.core.distro_rules import OFFERED, RAPTOR7_LISTS  # noqa: E402
from raptor.gui.ui.distro_combo import fill_distro_combo  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QApplication.instance() or QApplication([])


def _codes(combo):
    return [combo.itemData(i) for i in range(combo.count())]


@pytest.mark.parametrize("kind,has_empirical,has_fixed,has_none", [
    ("failure", True, True, False),
    ("repair", True, True, True),
    ("delay", False, True, False),
])
def test_each_picker_offers_raptor7s_list(kind, has_empirical, has_fixed, has_none):
    codes = _codes(fill_distro_combo(QComboBox(), None, is_failure=kind != "repair", kind=kind))
    assert codes == list(OFFERED[kind])
    assert (16 in codes, 19 in codes, 18 in codes) == (has_empirical, has_fixed, has_none)
    assert 17 not in codes and 20 not in codes      # Point Estimate, Exponential Power: never offered


def test_the_lists_are_in_raptor7s_alphabetical_order():
    names = [n for n in ("Beta", "Binomial", "Chi-square", "Empirical", "Erlang")]
    combo = fill_distro_combo(QComboBox(), None, is_failure=True)
    assert [combo.itemText(i) for i in range(5)] == names


def test_a_value_the_file_already_holds_is_kept_selectable():
    """A model written elsewhere with Exponential Power as a PM duration must
    still show (and keep) it, not land on Beta and be replaced on save."""
    combo = fill_distro_combo(QComboBox(), 20, is_failure=True, kind="delay")
    assert combo.currentData() == 20
    assert "not offered by Raptor 7" in combo.currentText()


def test_fixed_for_a_failure_law_is_the_only_addition_to_raptor7s_lists():
    for kind, codes in OFFERED.items():
        extra = [c for c in codes if c not in RAPTOR7_LISTS[kind]]
        assert extra == ([19] if kind == "failure" else []), kind
        assert [c for c in codes if c != 19 or kind != "failure"] == list(RAPTOR7_LISTS[kind])

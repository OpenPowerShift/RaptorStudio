"""One form per "how many?" question: k defaults to 1 and says what it means."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from raptor.gui.ui import count_form as cf


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_hint_names_redundancy_and_its_absence():
    assert "survives 1 failure at once" in cf.redundancy_hint(1, 2)
    assert "survives 2 failures" in cf.redundancy_hint(1, 3)
    assert "no redundancy" in cf.redundancy_hint(3, 3)


def test_duplicate_in_parallel_counts_the_original(qapp):
    form = cf.redundancy_form(None, "Duplicate in Parallel", count_label="Copies to add:",
                              count_default=1, count_max=50, already=1)
    assert form.values() == {"count": 1, "k": 1}
    k = form._fields["k"]
    assert k.maximum() == 2
    form._fields["count"].setValue(3)
    assert k.maximum() == 4
    labels = [w.text() for w in form.findChildren(cf.QLabel)]
    assert "How many of the 4 must work (k):" in labels
    assert any("survives 3 failures" in t for t in labels)
    form.deleteLater()


def test_array_form_defaults_leave_a_gap(qapp):
    form = cf.array_form(None)
    assert form.values() == {"copies": 3, "direction": "right", "spacing": 2}
    form.deleteLater()


def test_cancel_returns_none(qapp, monkeypatch):
    monkeypatch.setattr(cf.CountForm, "exec", lambda self: cf.QDialog.DialogCode.Rejected)
    assert cf.ask_array(None) is None
    assert cf.ask_redundancy(None, "x", count_label="n", count_default=2, count_max=5) is None
    monkeypatch.setattr(cf.CountForm, "exec", lambda self: cf.QDialog.DialogCode.Accepted)
    assert cf.ask_redundancy(None, "x", count_label="n", count_default=2, count_max=5) == (2, 1)


def test_duplicate_away_from_the_canvas_says_why(qapp):
    from raptor.api import build
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    spec = {"blocks": [{"name": "A"}],
            "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
            "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}]}
    w._load_into_app(build.build_from_spec(spec), None)
    w._edit_duplicate()                      # nothing has the focus offscreen
    assert "click the diagram" in w._statusbar.currentMessage()
    w._dirty = False
    w.close()
    w.deleteLater()


def test_computed_means_are_written_out_not_in_e_notation():
    from raptor.gui.ui.data_tables_common import _fmt_stat
    assert _fmt_stat(328500.0) == "328,500" and _fmt_stat(100000) == "100,000"
    assert _fmt_stat(12.345678) == "12.35" and _fmt_stat(None) == "—"

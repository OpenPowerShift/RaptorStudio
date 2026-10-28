"""Mass Edit dialog (manual 20-7): applying edits to multiple selected
blocks, events, nodes and hierarchies, and which tabs are shown per kind.
"""

from __future__ import annotations

import os
import tempfile

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (QApplication, QDialog, QLineEdit, QRadioButton, QCheckBox, QGroupBox)

from raptor.core.fileio.rbd import load_rbd, save_rbd
from raptor.core.fileio.typed import (block_from_row, event_from_row,
                                      node_from_row, hier_from_row)
from raptor.gui.ui.main_window import MainWindow
from raptor.gui.ui import dialogs

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOLD = os.path.join(REPO, "tests", "golden")
FIXTURE = os.path.join(GOLD, "Phasing1", "input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def w(qapp):
    win = MainWindow()
    win._load_into_app(load_rbd(FIXTURE), None)
    qapp.processEvents()
    return win


def _select(v, *, blocks=(), nodes=(), events=(), hiers=()):
    for bid in blocks:
        v._block_items[bid].setSelected(True)
    for nid in nodes:
        v._node_items[nid].setSelected(True)
    for eid in events:
        v._event_items[eid].setSelected(True)
    for hid in hiers:
        v._hier_items[hid].setSelected(True)


def _choose_rename_mode(dlg, mode):
    """Pick a rename mode ("replace" / "prefix" / "suffix") on every tab."""
    from PySide6.QtWidgets import QComboBox
    for c in dlg.findChildren(QComboBox):
        i = c.findData(mode)
        if i >= 0 and c.findData("suffix") >= 0:
            c.setCurrentIndex(i)


def _tick_all(dlg):
    """Tick every field's "change" box, as a user does to apply it: only
    ticked fields apply (an untouched Apply used to overwrite every
    selected block with the dialog's starting values)."""
    for box in dlg.findChildren(QCheckBox):
        if box.text().endswith(":") or box.text() == "Change the dependency":
            box.setChecked(True)


def _regular_nodes(v):
    return [nid for nid, it in v._node_items.items()
            if v.nodes[nid].type_node == 2]


def _roundtrip_row(v, rbd, section, id_col, oid, parser):
    v.write_back_to_rbd(rbd)
    path = os.path.join(tempfile.mkdtemp(), "roundtrip.rbd")
    save_rbd(rbd, path)
    reloaded = load_rbd(path)
    row = next(r for r in reloaded.sections[section].rows
              if int(float(r[id_col])) == oid)
    return parser(row)


def test_mass_edit_requires_at_least_two_selected(w):
    dialogs.on_mass_edit(w)
    assert "at least two" in w._statusbar.currentMessage()


def test_blocks_tab_survives_when_phases_defined(qapp, w, monkeypatch):
    """Defining phases must not clobber the Blocks tab's distribution
    assignment: with phases present, `fail_distro` and `repair_distro`
    are still written as integer distribution codes, not phasing
    letters."""
    v = w._diagram_dock.view
    bids = sorted(v.blocks)[:2]
    _select(v, blocks=bids)

    def fill(self):
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.on_mass_edit(w)

    for bid in bids:
        assert isinstance(v.blocks[bid].fail_distro, int)
        assert isinstance(v.blocks[bid].repair_distro, int)


def test_blocks_tab_applies_dist_stream_sparing_and_optional_rename(
        qapp, w, monkeypatch):
    v = w._diagram_dock.view
    bids = sorted(v.blocks)[:2]
    _select(v, blocks=bids)
    orig_f = {v.blocks[b].fail_distro for b in bids}
    orig_r = {v.blocks[b].repair_distro for b in bids}

    def fill(self):
        _tick_all(self)
        for edit in self.findChildren(QLineEdit):
            edit.setText("MassRenamed")
        _choose_rename_mode(self, "replace")
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.on_mass_edit(w)

    for bid in bids:
        b = v.blocks[bid]
        assert b.name == "MassRenamed"
        # Pre-filled from one selected block, then applied to all.
        assert b.fail_distro in orig_f
        assert b.repair_distro in orig_r
        assert b.fail_stream == 201 and b.repair_stream == 201  # System A
        assert b.sparing_type.name  # applied to some real SparingType
    assert len({v.blocks[b].fail_distro for b in bids}) == 1

    b2 = _roundtrip_row(v, v._rbd_ref, "BLOCK_DATA", "ID", bids[0],
                        block_from_row)
    assert b2.name == "MassRenamed"
    assert b2.fail_distro == v.blocks[bids[0]].fail_distro


def test_blocks_tab_sparing_combo_excludes_unsimulatable_types(qapp, w,
                                                                monkeypatch):
    """The Sparing combo offers only the four types the engine supports
    (None, Spare Pool, Infinite, Custom); Cold Pool and Resource raise
    `NotImplementedError` in `block_lifecycle.py` at simulate time."""
    from PySide6.QtWidgets import QComboBox
    v = w._diagram_dock.view
    bids = sorted(v.blocks)[:2]
    _select(v, blocks=bids)

    seen = {}

    def fill(self):
        combos = self.findChildren(QComboBox)
        sparing_combo = next(c for c in combos
                             if c.findText("Custom") >= 0
                             and c.findText("Infinite") >= 0)
        seen["items"] = [sparing_combo.itemText(i)
                         for i in range(sparing_combo.count())]
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.on_mass_edit(w)

    assert "Cold Pool" not in seen["items"]
    assert "Resource" not in seen["items"]
    assert set(seen["items"]) == {"None", "Spare Pool", "Infinite",
                                  "Custom"}


def test_blocks_tab_sparing_pooled_also_sets_pool_name(qapp, w,
                                                        monkeypatch):
    """Selecting Sparing=Spare Pool also selects a pool name, so the block
    does not end up with an unresolvable `pool_name`."""
    from PySide6.QtWidgets import QComboBox
    v = w._diagram_dock.view
    bids = sorted(v.blocks)[:2]
    _select(v, blocks=bids)
    pool_name = next(iter(
        w._rbd.sections["SPARE_POOL_DATA"].rows), {}).get("PoolName")
    assert pool_name, "fixture must have at least one real spare pool"

    def fill(self):
        _tick_all(self)
        combos = self.findChildren(QComboBox)
        sparing_combo = next(c for c in combos
                             if c.findText("Spare Pool") >= 0)
        sparing_combo.setCurrentIndex(
            sparing_combo.findText("Spare Pool"))
        pool_combo = next(c for c in combos
                          if c.findData(pool_name) >= 0
                          and c is not sparing_combo)
        pool_combo.setCurrentIndex(pool_combo.findData(pool_name))
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.on_mass_edit(w)

    for bid in bids:
        assert v.blocks[bid].sparing_type.name == "SPARE_POOL"
        assert v.blocks[bid].pool_name == pool_name


def test_blocks_tab_rename_left_blank_keeps_existing_names(qapp, w,
                                                            monkeypatch):
    v = w._diagram_dock.view
    bids = sorted(v.blocks)[:2]
    before = {bid: v.blocks[bid].name for bid in bids}
    _select(v, blocks=bids)

    def fill(self):
        return QDialog.Accepted  # rename field left blank
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.on_mass_edit(w)

    for bid in bids:
        assert v.blocks[bid].name == before[bid]


def test_events_tab_applies_param_stream_and_rename(qapp, w, monkeypatch):
    v = w._diagram_dock.view
    bids = sorted(v.blocks)[:1]
    eids = list(v.events)
    assert len(eids) >= 1
    _select(v, blocks=bids, events=eids)

    def fill(self):
        _tick_all(self)
        for edit in self.findChildren(QLineEdit):
            if edit.placeholderText():
                edit.setText("EvtRenamed")
        _choose_rename_mode(self, "replace")
        return QDialog.Accepted  # stream picker left at System/A default
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.on_mass_edit(w)

    for eid in eids:
        e = v.events[eid]
        assert e.name == "EvtRenamed"
        assert e.fail_stream == 201
    e2 = _roundtrip_row(v, v._rbd_ref, "EVENT_DATA", "ID", eids[0],
                        event_from_row)
    assert e2.name == "EvtRenamed"


def test_nodes_tab_applies_k_value_and_dependency_modes(qapp, w,
                                                         monkeypatch):
    v = w._diagram_dock.view
    nids = _regular_nodes(v)[:2]
    bids = sorted(v.blocks)[:1]
    _select(v, blocks=bids, nodes=nids)

    def fill(self):
        _tick_all(self)
        for gb in self.findChildren(QGroupBox):
            if gb.title() == "Dependency":
                for r in gb.findChildren(QRadioButton):
                    if r.text() == "System dependent":
                        r.setChecked(True)
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.on_mass_edit(w)

    for nid in nids:
        assert v.nodes[nid].dependency_num == -2
        assert v.nodes[nid].dep_type == ""
    n2 = _roundtrip_row(v, v._rbd_ref, "NODE_DATA", "ID", nids[0],
                        node_from_row)
    assert n2.dependency_num == -2


def test_nodes_tab_dependent_on_block(qapp, w, monkeypatch):
    v = w._diagram_dock.view
    nids = _regular_nodes(v)[:1]
    bids = sorted(v.blocks)[:1]
    _select(v, blocks=bids, nodes=nids)

    def fill(self):
        _tick_all(self)
        for gb in self.findChildren(QGroupBox):
            if gb.title() == "Dependency":
                for r in gb.findChildren(QRadioButton):
                    if r.text() == "Dependent on:":
                        r.setChecked(True)
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.on_mass_edit(w)

    nid = nids[0]
    assert v.nodes[nid].dep_type == "RBDBlock"
    assert v.nodes[nid].dependency_num in {bid for bid, _ in
                                           _block_id_name_pairs(w._rbd)}


def _block_id_name_pairs(rbd):
    sec = rbd.sections.get("BLOCK_DATA")
    out = []
    for row in sec.rows:
        try:
            out.append((int(float(row.get("ID", "-1"))), row.get("blockName")))
        except (ValueError, KeyError):
            continue
    return out


def test_hierarchies_tab_rename_only_no_dependency_offered(qapp, w,
                                                            monkeypatch):
    v = w._diagram_dock.view
    hids = list(v.hiers)[:2]
    bids = sorted(v.blocks)[:1]
    _select(v, blocks=bids, hiers=hids)

    def fill(self):
        for edit in self.findChildren(QLineEdit):
            if edit.placeholderText():
                edit.setText("HierRenamed")
        _choose_rename_mode(self, "replace")
        # The Hierarchies tab page itself offers no Dependency control;
        # the Nodes tab's own Dependency group box lives elsewhere.
        from PySide6.QtWidgets import QTabWidget
        tabs = self.findChild(QTabWidget)
        hier_idx = next(i for i in range(tabs.count())
                        if tabs.tabText(i).startswith("Hierarchies"))
        hier_page = tabs.widget(hier_idx)
        assert not hier_page.findChildren(QGroupBox)
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.on_mass_edit(w)

    for hid in hids:
        assert v.hiers[hid].name == "HierRenamed"
    h2 = _roundtrip_row(v, v._rbd_ref, "HIERARCHY_DATA", "ID", hids[0],
                        hier_from_row)
    assert h2.name == "HierRenamed"


def test_phasing_tab_applies_to_all_four_kinds(qapp, w, monkeypatch):
    """Picking a not-yet-phased block, node, event and hierarchy and
    applying phasing flips `uses_phasing` on all four. Phasing1 already
    has both events phased, so the event only confirms the
    post-condition."""
    v = w._diagram_dock.view
    bid = next(bid for bid, b in v.blocks.items() if not b.uses_phasing)
    nid = next(nid for nid in _regular_nodes(v)
              if not v.nodes[nid].uses_phasing)
    eid = next(iter(v.events))
    hid = next(hid for hid, h in v.hiers.items() if not h.uses_phasing)
    _select(v, blocks=[bid], nodes=[nid], events=[eid], hiers=[hid])
    assert not v.blocks[bid].uses_phasing
    assert not v.nodes[nid].uses_phasing
    assert not v.hiers[hid].uses_phasing

    def fill(self):
        for cb in self.findChildren(QCheckBox):
            if cb.text() == "Apply phasing to all selected":
                cb.setChecked(True)
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.on_mass_edit(w)

    assert v.blocks[bid].uses_phasing
    assert v.nodes[nid].uses_phasing
    assert v.events[eid].uses_phasing
    assert v.hiers[hid].uses_phasing
    assert "phasing on 4 element" in w._statusbar.currentMessage()


def test_tabs_hidden_for_kinds_not_selected(qapp, w, monkeypatch):
    v = w._diagram_dock.view
    bids = sorted(v.blocks)[:2]
    _select(v, blocks=bids)

    seen = {}

    def fill(self):
        from PySide6.QtWidgets import QTabWidget
        tabs = self.findChild(QTabWidget)
        seen["titles"] = [tabs.tabText(i) for i in range(tabs.count())]
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fill)
    dialogs.on_mass_edit(w)

    titles = seen["titles"]
    assert any(t.startswith("Blocks") for t in titles)
    assert not any(t.startswith("Nodes") for t in titles)
    assert not any(t.startswith("Events") for t in titles)
    assert not any(t.startswith("Hierarchies") for t in titles)


class TestRenameModes:
    """The original offered append-as-prefix / append-as-suffix / replace in
    entirety. This port only replaced, which gives every selected element
    the SAME name -- arguably worse than not renaming at all."""

    @pytest.mark.parametrize("old,text,mode,expected", [
        ("Pump", "_v2", "suffix", "Pump_v2"),
        ("Pump", "A_", "prefix", "A_Pump"),
        ("Pump", "Valve", "replace", "Valve"),
        ("Pump", "", "suffix", "Pump"),
        ("Pump", "", "replace", "Pump"),
        ("", "X", "suffix", "X"),
    ])
    def test_mass_rename_rule(self, old, text, mode, expected):
        assert dialogs.mass_rename(old, text, mode) == expected

    def _apply(self, qapp, w, monkeypatch, mode, text):
        v = w._diagram_dock.view
        bids = sorted(v.blocks)[:2]
        before = {b: v.blocks[b].name for b in bids}
        _select(v, blocks=bids)

        def fill(self):
            for edit in self.findChildren(QLineEdit):
                edit.setText(text)
            if mode:
                _choose_rename_mode(self, mode)
            return QDialog.Accepted
        monkeypatch.setattr(QDialog, "exec", fill)
        dialogs.on_mass_edit(w)
        return v, bids, before

    def test_suffix_keeps_each_elements_own_name(self, qapp, w,
                                                 monkeypatch):
        v, bids, before = self._apply(qapp, w, monkeypatch, "suffix", "_x")
        for b in bids:
            assert v.blocks[b].name == before[b] + "_x"
        assert len({v.blocks[b].name for b in bids}) == len(
            {before[b] for b in bids}), "names must stay distinct"

    def test_prefix(self, qapp, w, monkeypatch):
        v, bids, before = self._apply(qapp, w, monkeypatch, "prefix", "P_")
        for b in bids:
            assert v.blocks[b].name == "P_" + before[b]

    def test_default_mode_never_flattens_names(self, qapp, w,
                                               monkeypatch):
        """Untouched, the mode must be the safe one (suffix), not Replace."""
        v, bids, before = self._apply(qapp, w, monkeypatch, None, "_d")
        for b in bids:
            assert v.blocks[b].name == before[b] + "_d"

    def test_blank_text_renames_nothing(self, qapp, w, monkeypatch):
        v, bids, before = self._apply(qapp, w, monkeypatch, "prefix", "")
        for b in bids:
            assert v.blocks[b].name == before[b]


def test_an_untouched_apply_changes_nothing(qapp, w, monkeypatch):
    """REGRESSION: every field applied unconditionally, so Apply without
    touching anything wrote Beta(100, 0) over every selected block --
    which then failed at run time with a division by zero."""
    v = w._diagram_dock.view
    bids = sorted(v.blocks)[:2]
    _select(v, blocks=bids)
    rows = {bid: dict(next(r for r in v._rbd_ref.sections["BLOCK_DATA"].rows
                           if int(float(r["ID"])) == bid)) for bid in bids}
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    dialogs.on_mass_edit(w)
    v.write_back_to_rbd(v._rbd_ref)
    for bid in bids:
        now = next(r for r in v._rbd_ref.sections["BLOCK_DATA"].rows
                   if int(float(r["ID"])) == bid)
        for col in ("FDist", "FParam1", "FParam2", "RDist", "RParam1",
                    "FStream", "SpType"):
            assert now[col] == rows[bid][col], (bid, col)
    assert "nothing" in w._statusbar.currentMessage()

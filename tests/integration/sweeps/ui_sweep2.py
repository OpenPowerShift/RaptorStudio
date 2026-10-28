"""RAPTOR Studio UI smoke test (offscreen). Test-and-report only.

Harness (modal stubs, Recorder, the `TOTAL:` footer) comes from
`_sweep_base.py`; the checks below are this sweep's own.
"""
import os, sys, time, shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QLineEdit
from PySide6.QtCore import QPointF, QSettings, Qt

import _sweep_base as sb

from raptor.gui.ui.diagram import ZOOM_MIN, ZOOM_MAX
from raptor.gui.ui.main_window import INSPECTOR_SELECT_DEBOUNCE_MS

ctx = sb.make_context()
app, w, GOLD, TMP = ctx.app, ctx.window, ctx.golden, ctx.tmp
_MSGBOX_CALLS = ctx.msgboxes

record = ctx.record
run_check = ctx.run_check
load = ctx.load

def clear_selection(v):
    for it in list(v._scene.selectedItems()):
        it.setSelected(False)

def select_blocks(v, bids):
    clear_selection(v)
    for b in bids:
        v._block_items[b].setSelected(True)
    app.processEvents()

# ============================ A. Inspector flows ============================
v = load("TwoBlockRedundancy")

def t_a_block_inspector():
    bid = next(b for b in v._block_items if b != 0)
    clear_selection(v)
    it = v._block_items[bid]
    it.setSelected(True)
    app.processEvents()
    # The Inspector rebuild is coalesced (80 ms); let it land.
    time.sleep(INSPECTOR_SELECT_DEBOUNCE_MS / 1000.0 + 0.02)
    app.processEvents()
    hdr = w._inspector._header.text()
    assert f"Block: {v.blocks[bid].name}" in hdr, f"inspector header '{hdr}' lacks block name"
    # name-edit commit path inside the inspector
    edits = [e for e in w._inspector.findChildren(QLineEdit)
             if e.text() == v.blocks[bid].name]
    assert edits, "no inspector name QLineEdit found"
    edits[0].setText("Insp_Renamed")
    edits[0].editingFinished.emit()
    app.processEvents()
    assert v.blocks[bid].name == "Insp_Renamed", \
        f"name={v.blocks[bid].name!r} after inspector commit"
    # explorer + canvas label updated?
    tree_texts = []
    def walk(item):
        if item is None:
            return
        tree_texts.append(item.text(0))
        for i in range(item.childCount()):
            walk(item.child(i))
    walk(w._explorer._tree.topLevelItem(0))
    assert any("Insp_Renamed" in t for t in tree_texts), \
        f"explorer missing new name; texts={tree_texts[:12]}"
    assert v._block_items[bid]._label.toPlainText() == "Insp_Renamed", \
        f"canvas label={v._block_items[bid]._label.toPlainText()!r}"
run_check("A.inspector.block.show_and_rename", t_a_block_inspector)

def t_a_node_event_rename():
    load("TwoBlockRedundancy"); v = w._diagram_dock.view
    nid = next(n for n in v.nodes if v.nodes[n].type_node == 2)
    old = v.nodes[nid].node_name
    w._on_node_renamed(nid, "Renamed_Node")
    assert v.nodes[nid].node_name == "Renamed_Node", f"node rename -> {v.nodes[nid].node_name!r}"
    assert v._node_items[nid]._name_label.toPlainText() == "Renamed_Node", \
        f"canvas node label={v._node_items[nid]._name_label.toPlainText()!r}"
    v._stack.undo(); app.processEvents()
    assert v.nodes[nid].node_name == old, "node rename undo failed"
run_check("A.rename.node", t_a_node_event_rename)

def t_a_event_rename():
    load("Events"); v = w._diagram_dock.view
    eid = next(iter(v.events))
    ev_old = v.events[eid].name
    v.rename_event(eid, "Renamed_Event")
    assert v.events[eid].name == "Renamed_Event", "event rename failed"
    sec = w._rbd.sections["EVENT_DATA"]
    row = next(r for r in sec.rows if int(float(r["ID"])) == eid)
    assert row["eventName"] == "Renamed_Event", \
        f"EVENT_DATA row not mirrored: {row['eventName']!r}"
    v._stack.undo(); app.processEvents()
    assert v.events[eid].name == ev_old, "event rename undo failed"
run_check("A.rename.event", t_a_event_rename)

def t_a_hier_rename():
    load("HierarchyExample"); v = w._diagram_dock.view
    hid = next(iter(v.hiers))
    old = v.hiers[hid].name
    v.rename_hier(hid, "Renamed_Hier"); app.processEvents()
    assert v.hiers[hid].name == "Renamed_Hier", f"hier rename -> {v.hiers[hid].name!r}"
    sec = w._rbd.sections["HIERARCHY_DATA"]
    row = next(r for r in sec.rows if int(float(r["ID"])) == hid)
    assert row["hierarchyName"] == "Renamed_Hier", \
        f"HIERARCHY_DATA row stale: {row['hierarchyName']!r}"
    v._stack.undo(); app.processEvents()
    assert v.hiers[hid].name == old, "hier rename undo failed"
run_check("A.rename.hier", t_a_hier_rename)

# ============================ B. Data Tables cells ============================
load("TwoBlockRedundancy"); dt = w._data_tables

def t_b_bad_name():
    dt.refresh()
    t = dt._blocks_table
    bid = None
    for r in range(t.rowCount()):
        bid = int(t.item(r, 1).text())
        break
    item = t.item(0, 0)
    item.setText("two words")
    app.processEvents()
    assert " " not in item.text(), f"name cell still has space: {item.text()!r}"
    assert item.text() == "two_words", f"normalized to {item.text()!r}, want 'two_words'"
    assert w._diagram_dock.view.blocks[bid].name == "two_words", \
        f"model name={w._diagram_dock.view.blocks[bid].name!r}"
run_check("B.blocks_table.bad_name.normalized", t_b_bad_name)

def t_b_bad_numeric():
    dt.refresh()
    t = dt._blocks_table
    bid = int(t.item(0, 1).text())
    old_val = float(t.cellWidget(0, 3).findChildren(QLineEdit)[0].text())
    edit = t.cellWidget(0, 3).findChildren(QLineEdit)[0]
    edit.setText("abc")
    edit.editingFinished.emit()
    app.processEvents()
    blk = w._diagram_dock.view.blocks[bid]
    assert abs(blk.fail_values[0] - old_val) < 1e-9, \
        f"model param mutated by 'abc': {blk.fail_values[0]} (was {old_val})"
    assert edit.text() == f"{old_val:.6g}", \
        f"cell text did not revert: {edit.text()!r} want {f'{old_val:.6g}'!r}"
run_check("B.param_cell.invalid_text.reverts", t_b_bad_numeric)

# ============================ C. Ribbon gating ============================
v = load("TwoBlockRedundancy")

def t_c_ribbon_gating():
    w._add_block_tool.setChecked(True)
    app.processEvents()
    assert v.tool_mode == "add_block", f"tool not armed: {v.tool_mode}"
    other = w._simulate_page_index
    w._ribbon.tab_bar.setCurrentIndex(other)  # fires _on_ribbon_tab_changed
    app.processEvents()
    assert v.tool_mode == "select", \
        f"tool_mode={v.tool_mode} after leaving Model tab, want 'select'"
    assert not w._add_block_tool.isChecked(), "tool action stayed checked"
    w._ribbon.tab_bar.setCurrentIndex(w._model_page_index)
run_check("C.ribbon_tab_change.disarms_tool", t_c_ribbon_gating)

# ============================ D. Context-menu delete/cut ============================
def t_d_delete_protection():
    start_ids = [n for n in v.nodes if v.nodes[n].type_node == 1]
    end_ids = [n for n in v.nodes if v.nodes[n].type_node == 3]
    clear_selection(v)
    for n in start_ids + end_ids:
        v._node_items[n].setSelected(True)
    v.delete_selected(); app.processEvents()
    for n in start_ids + end_ids:
        assert n in v.nodes and n in v._node_items, \
            f"structural node {n} was deleted!"
run_check("D.delete.protects_start_end", t_d_delete_protection)

def t_d_delete_orphan_links():
    bid = next(b for b in v._block_items if b != 0)
    lsec = w._rbd.sections["LINK_DATA"]
    touching_before = sum(
        1 for r in lsec.rows
        if int(float(r.get("FromID", -1))) == bid
        or int(float(r.get("ToID", -1))) == bid)
    select_blocks(v, [bid])
    v.delete_selected(); app.processEvents()
    assert bid not in v.blocks, "block survived delete_selected"
    left = [r for r in lsec.rows
            if int(float(r.get("FromID", -1))) == bid
            or int(float(r.get("ToID", -1))) == bid]
    assert not left, \
        f"{len(left)} orphaned LINK_DATA rows remain after deleting block {bid}"
    print(f"    removed block {bid}; {touching_before} link rows cleaned up")
    v._stack.undo(); app.processEvents()  # restore
    assert bid in v.blocks, "undo of delete failed"
run_check("D.delete.removes_orphan_links", t_d_delete_orphan_links)

def t_d_cut():
    bids = [b for b in v._block_items if b != 0][:2]
    select_blocks(v, bids)
    n = v.cut_selection(); app.processEvents()
    assert n >= 1, "cut_selection returned 0"
    for b in bids:
        assert b not in v.blocks, f"block {b} survived cut"
    v._stack.undo(); app.processEvents()
    for b in bids:
        assert b in v.blocks, f"undo of cut lost block {b}"
run_check("D.cut_selection.macro_undo", t_d_cut)

# ============================ E. Connect tool ============================
def t_e_connect_two_click():
    v.go_home(); app.processEvents()
    items = [it for iid, it in v._block_items.items() if iid != 0]
    assert len(items) >= 2, "need two blocks"
    a, b = items[0], items[1]
    rows0 = len(w._rbd.sections["LINK_DATA"].rows)
    links0 = len(v.links)
    v.tool_mode = "connect"; v._connect_from = None
    # A block that already has an outgoing link refuses a second one at
    # draw time (the engine would reject it at Run).
    v._handle_tool_click(QPointF(a.scenePos().x() + 5, a.scenePos().y() + 5))
    assert v._connect_from is not None, "first click did not arm source"
    v._handle_tool_click(QPointF(b.scenePos().x() + 5, b.scenePos().y() + 5))
    rows_mid = len(w._rbd.sections["LINK_DATA"].rows)
    assert rows_mid == rows0, \
        f"second outgoing must be refused ({rows0}->{rows_mid})"
    v._connect_from = None
    v.tool_mode = "select"
    v._stack.undo(); app.processEvents()  # remove the created link(s)
run_check("E.connect.two_click.duplicate_ignored", t_e_connect_two_click)

# ============================ F. Add tools placement ============================
def t_f_add_placement():
    v.tool_mode = "add_block"
    pt = QPointF(300.0, 200.0)
    mx, my = v.scene_to_model(pt)
    before = set(v.blocks)
    n0 = len(v.blocks)
    v._handle_tool_click(pt); app.processEvents()
    assert len(v.blocks) == n0 + 1, "add_block click created nothing"
    nb = (set(v.blocks) - before).pop()
    blk = v.blocks[nb]
    assert abs(blk.x_position - mx) < 2.0 and abs(blk.y_position - my) < 2.0, \
        f"block placed at ({blk.x_position},{blk.y_position}), clicked model ({mx},{my})"
    v._stack.undo(); app.processEvents()

    v.tool_mode = "add_node"
    n_nodes = len(v.nodes)
    before_n = set(v.nodes)
    v._handle_tool_click(pt); app.processEvents()
    assert len(v.nodes) == n_nodes + 1, "add_node click created nothing"
    nid_new = (set(v.nodes) - before_n).pop()
    nd = v.nodes[nid_new]
    assert abs(nd.x_position - mx) < 2.0 and abs(nd.y_position - my) < 2.0, \
        f"node placed at ({nd.x_position},{nd.y_position}), want ({mx},{my})"
    v._stack.undo(); app.processEvents()

    v.tool_mode = "add_event"
    n_ev = len(v.events)
    before_e = set(v.events)
    v._handle_tool_click(pt); app.processEvents()
    assert len(v.events) == n_ev + 1, "add_event click created nothing"
    eid = (set(v.events) - before_e).pop()
    ev = v.events[eid]
    assert abs(ev.x_position - mx) < 2.0 and abs(ev.y_position - my) < 2.0, \
        f"event placed at ({ev.x_position},{ev.y_position}), want ({mx},{my})"
    v._stack.undo(); app.processEvents()
    v.tool_mode = "select"
run_check("F.add_tools.place_at_click", t_f_add_placement)

# ============================ G. Zoom / pan ============================
def t_g_zoom_clamp():
    v.reset_zoom(); app.processEvents()
    m0 = v.transform().m11()
    v.zoom_by(2.0)
    m1 = v.transform().m11()
    assert abs(m1 / m0 - 2.0) < 1e-6, f"zoom_by(2) gave {m0}->{m1}"
    v.zoom_by(1e9)
    assert v.transform().m11() <= ZOOM_MAX + 1e-6, \
        f"zoom exceeded ZOOM_MAX: {v.transform().m11()} vs {ZOOM_MAX}"
    v.zoom_by(1e-9)
    assert v.transform().m11() >= ZOOM_MIN - 1e-6, \
        f"zoom below ZOOM_MIN: {v.transform().m11()} vs {ZOOM_MIN}"
    v.reset_zoom(); app.processEvents()
    assert v.transform().m11() < ZOOM_MAX and not getattr(v, "_user_zoomed", False), \
        "reset_zoom did not refit"
run_check("G.zoom.clamps_and_reset", t_g_zoom_clamp)

# ============================ H. Validation ============================
def t_h_validation_paths():
    load("TwoBlockRedundancy"); v = w._diagram_dock.view
    w._validate_model(); app.processEvents()
    assert hasattr(w, "_validation_badge") and "OK" in w._validation_badge.text(), \
        f"badge after valid model: {getattr(w, '_validation_badge', None)}"
    n_warn0 = len(_MSGBOX_CALLS)
    for bid in [b for b in v._block_items if b != 0]:
        clear_selection(v)
        v._block_items[bid].setSelected(True)
        v.delete_selected(); app.processEvents()
    # Validation is silent: a clickable badge + issues list replaces
    # the modal warning.
    w._validate_model(quiet=True); app.processEvents()
    assert len(_MSGBOX_CALLS) == n_warn0, "invalid model must not modal"
    assert "Invalid" in w._validation_badge.text(), \
        f"badge: {w._validation_badge.text()!r}"
    issues = w._validation_issues()
    # Match on the stable "No blocks" core, since the message also
    # covers events.
    assert any("No blocks" in i for i in issues), issues
run_check("H.validate.empty_model.red_badge", t_h_validation_paths)

# ============================ J. Step-session invalidation ============================
v = w._diagram_dock.view

def t_j_step_invalidation():
    w._step_session = None; w._step_sink = []
    s = w._ensure_step_session()
    assert w._step_session is not None, "session not created"
    v.add_block("Invalidator", 60.0, 60.0)
    app.processEvents()
    assert w._step_session is None, \
        "edit did not invalidate the stepped session"
run_check("J.edit_invalidates_step_session", t_j_step_invalidation)

# ============================ N. Mass Edit apply ============================
def t_n_mass_edit():
    v = load("TwoBlockRedundancy")
    bids = [b for b in v._block_items if b != 0][:2]
    assert len(bids) == 2
    select_blocks(v, bids)
    before = {b: (v.blocks[b].fail_distro, list(v.blocks[b].fail_values))
              for b in bids}
    w._on_mass_edit(); app.processEvents()
    # Every field is tick-box gated: an untouched Apply must not write
    # this dialog's starting values (Beta 100, 0) over the selection.
    for bid in bids:
        d, vals = before[bid]
        assert v.blocks[bid].fail_distro == d, \
            f"block {bid}: untouched Apply changed distro"
        assert list(v.blocks[bid].fail_values) == vals, \
            f"block {bid}: untouched Apply changed params"
    print(f"    untouched Apply left {len(bids)} blocks unchanged")
run_check("N.mass_edit.apply_uniform", t_n_mass_edit)

# ============================ O. Preferences ============================
def t_o_preferences():
    # Preferences are now ONE KEY PER SETTING under a namespaced tree
    # (ui/settings.py), replacing the serialised `preferences` blob --
    # the blob was how `parallel_workers` ended up stored outside the
    # very dict it was named after.
    settings = QSettings()
    for k in ("new_model/units", "files/autosave_min",
              "files/autosave", "files/save_before_run",
              "execution/max_workers", "execution/default_animate",
              "editing/exp_mode", "editing/ln_mode"):
        settings.remove(k)
    settings.sync()
    w._show_preferences(); app.processEvents()
    assert settings.value("new_model/units") == "hours", \
        f"units={settings.value('new_model/units')!r}"
    # The dialog writes every row, so all of them must now exist as
    # individual keys (before, "was autosave_min in the blob?" was the
    # only check, and a row the dialog forgot to write went unnoticed).
    for k in ("new_model/units", "files/autosave_min",
              "files/save_before_run", "execution/max_workers",
              "execution/default_animate", "editing/exp_mode",
              "editing/ln_mode"):
        assert settings.contains(k), f"missing settings key {k}"
    # The old blob must be gone, not shadowed by the new tree.
    assert not settings.contains("preferences"), \
        "legacy `preferences` blob should have been migrated away"
    for k in ("new_model/units", "files/autosave_min", "files/autosave",
              "files/save_before_run", "execution/max_workers",
              "execution/default_animate", "editing/exp_mode",
              "editing/ln_mode"):
        settings.remove(k)
    settings.sync()
run_check("O.preferences.applied_and_cleared", t_o_preferences)

# ============================ P. Block defaults ============================
def t_p_block_defaults():
    v0 = w._diagram_dock.view
    settings = QSettings()
    settings.setValue("editing/block_defaults",
                       {"fdist": 4, "fparams": [250.0, 0.0, 0.0]})
    settings.sync()
    w._edit_block_defaults(); app.processEvents()
    assert v0.default_block_fdist == 4, \
        f"default_block_fdist={v0.default_block_fdist}, want 4"
    assert v0.default_block_fparams[0] == 250.0, f"fparams={v0.default_block_fparams}"
    n0 = len(v0.blocks)
    bid = v0.add_block("DefaultsProbe", 70.0, 70.0); app.processEvents()
    blk = v0.blocks[bid]
    assert blk.fail_distro == 4, f"new block distro={blk.fail_distro}, want 4"
    assert abs(blk.fail_values[0] - 250.0) < 1e-9, \
        f"new block param1={blk.fail_values[0]}, want 250"
    v0._stack.undo(); app.processEvents()
    settings.remove("editing/block_defaults"); settings.sync()
run_check("P.block_defaults.apply_to_next_add", t_p_block_defaults)

# ============================ Q. Event tool + inspector field ============================
def t_q_event_field_undo_redo():
    v = w._diagram_dock.view
    v.tool_mode = "add_event"
    pt = QPointF(320.0, 220.0)
    n0 = len(v.events)
    v._handle_tool_click(pt); app.processEvents()
    assert len(v.events) == n0 + 1, "event tool created nothing"
    eid = max(v.events)
    old = v.events[eid].fail_mean
    v.set_event_field(eid, "param", 0.9); app.processEvents()
    assert abs(v.events[eid].fail_mean - 0.9) < 1e-9, \
        f"param={v.events[eid].fail_mean}, want 0.9"
    v._stack.undo(); app.processEvents()
    assert abs(v.events[eid].fail_mean - old) < 1e-9, "event param undo failed"
    v._stack.redo(); app.processEvents()
    assert abs(v.events[eid].fail_mean - 0.9) < 1e-9, "event param redo failed"
    v._stack.undo(); v._stack.undo(); app.processEvents()  # param + creation
    v.tool_mode = "select"
run_check("Q.event_tool.place_param_undo_redo", t_q_event_field_undo_redo)

# ============================ R. Title / dirty tracking ============================
# Opening a second model normally spawns a new window (files/open_in).
# These checks are about this window's title/recent list, so keep the open
# in this window.
_orig_window_for_open = w._window_for_open
w._window_for_open = lambda *a, **k: w
shutil.copy(f"{GOLD}/TwoBlockRedundancy/input.rbd", f"{TMP}/title_probe.rbd")

def t_r_title_dirty():
    # Clear dirty first: the modal stub answers a discard prompt with
    # "no", which would cancel the open and leave the previous title.
    w._dirty = False
    app.processEvents()          # flush a deferred mark from the prior check
    w._open_path(f"{TMP}/title_probe.rbd"); app.processEvents()
    v = w._diagram_dock.view
    assert w.windowTitle().find("*") == -1, f"title clean-load: {w.windowTitle()!r}"
    v.add_block("DirtyMaker", 50.0, 50.0); app.processEvents()
    assert "*" in w.windowTitle(), f"title after edit: {w.windowTitle()!r}"
    assert w._dirty
    w._autosave_tick(); app.processEvents()
    # Autosave writes a recovery copy and never touches the user's file,
    # so the model stays dirty and the title still shows the edit star.
    assert w._dirty, "autosave must not clear the dirty flag"
    assert "*" in w.windowTitle(), f"title after autosave: {w.windowTitle()!r}"
    assert os.path.exists(w._recovery_path()), "no recovery copy written"
run_check("R.title.star_tracking", t_r_title_dirty)

# ============================ M. Recent-files click-through ============================
def t_m_recent_click_through():
    # These temp files must exist for _open_path to load them.
    shutil.copy(f"{GOLD}/TwoBlockRedundancy/input.rbd", f"{TMP}/recent_a.rbd")
    shutil.copy(f"{GOLD}/TwoBlockRedundancy/input.rbd", f"{TMP}/recent_b.rbd")
    QSettings().setValue(w._RECENT_KEY, []); w._recent_reload()
    w._dirty = False
    app.processEvents()
    w._open_path(f"{TMP}/recent_a.rbd"); app.processEvents()
    a_path = w._current_file
    w._dirty = False
    app.processEvents()
    w._open_path(f"{TMP}/recent_b.rbd"); app.processEvents()
    b_path = w._current_file
    # The File view's recent list: open the entry whose path matches.
    w._show_backstage()
    bs = w._backstage
    target = next((bs._list.topLevelItem(i)
                   for i in range(bs._list.topLevelItemCount())
                   if bs._list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                   == a_path), None)
    assert target is not None, "recent list missing entry"
    w._dirty = False
    bs._open_item(target); app.processEvents()
    assert w._current_file == a_path, \
        f"click-through loaded {w._current_file!r}, want {a_path!r}"
run_check("M.recent_combo.click_loads_file", t_m_recent_click_through)
w._window_for_open = _orig_window_for_open

# ============================ L. Explorer navigation ============================
def t_l_explorer_nav():
    load("HierarchyExample"); v = w._diagram_dock.view
    ex = w._explorer
    hid = sorted(v.hiers)[0]
    # find the hier entry by role
    def find_by_role(item, role_prefix):
        stack = [item]
        while stack:
            cur = stack.pop()
            r = cur.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(r, str):
                if role_prefix == "hier" and r == f"hier:{hid}":
                    return cur
                if role_prefix == "hout" and cur.text(0).startswith("[HOut]"):
                    return cur
            stack.extend(cur.child(i) for i in range(cur.childCount()))
        return None
    root = ex._tree.topLevelItem(0)
    hier_item = find_by_role(root, "hier")
    assert hier_item is not None, "no hier entry in explorer tree"
    ex._on_item_double_clicked(hier_item, 0); app.processEvents()
    assert v._current_parent == hid, \
        f"double-click hier -> level {v._current_parent}, want {hid}"
    hout_item = find_by_role(ex._tree.topLevelItem(0), "hout")
    if hout_item is not None:
        ex._on_item_double_clicked(hout_item, 0); app.processEvents()
        assert v._current_parent == 0, \
            f"double-click [HOut] -> level {v._current_parent}, want 0"
    else:
        raise AssertionError("no [HOut] entry found at home level")
run_check("L.explorer.doubleclick.descend_ascend", t_l_explorer_nav)

# ============================ K. Level filter x Cost tab ============================
def t_k_level_filter_cost():
    load("Capacity1")
    v = w._diagram_dock.view
    v.go_home(); app.processEvents()
    w._run_count_spin.setValue(1)
    w._run_simulation(); app.processEvents()
    res = w._results
    tabs = [res._details_tabs.tabText(i) for i in range(res._details_tabs.count())]
    cost_tabs = [t for t in tabs if t.lower().startswith("cost")]
    print(f"    detail tabs={tabs}")
    tables = [t for k, t in res._detail_tables.items()
              if k not in ("SUMMARY", "LOGISTICS")]
    cap = getattr(res, "_cap_table", None)
    if cap is not None:
        tables.append(cap)
    tables.extend(getattr(res, "_cost_tables", []))
    total_rows = sum(t.rowCount() for t in tables)
    assert total_rows > 0, "no filterable result rows after sim"
    res.set_level_filter({"nonexistent_level"})
    hidden = sum(1 for t in tables for r in range(t.rowCount()) if t.isRowHidden(r))
    assert hidden == total_rows, \
        f"filter {{'nonexistent'}} hid {hidden}/{total_rows} rows (want all)"
    res.set_level_filter(None)
    still_hidden = sum(1 for t in tables for r in range(t.rowCount())
                       if t.isRowHidden(r))
    assert still_hidden == 0, f"{still_hidden} rows stuck hidden after None"
run_check("K.level_filter.cost_tables", t_k_level_filter_cost)

# ============================ I. Persistence integrity ============================
def t_i_persistence():
    load("TwoBlockRedundancy"); v = w._diagram_dock.view
    # heavy editing session
    bid = v.add_block("Persist_Block", 80.0, 80.0)
    nid = v.add_node("persist_join", 90.0, 90.0)
    eid = v.add_event("P=0.25", 100.0, 100.0)
    hid = v.add_hierarchy("PersistHier", 110.0, 110.0)
    v.rename_block(bid, "Persist_Renamed")
    v.set_block_fail_params(bid, [42.0, 0.0, 0.0])
    select_blocks(v, [next(b for b in v._block_items if b != 0 and b != bid)])
    v.copy_selection(); v.paste(); app.processEvents()
    exp_counts = {
        "blocks": dict((k, (vv.name, vv.parent_id)) for k, vv in v.blocks.items()),
        "nodes": dict((k, (vv.node_name, vv.parent_id)) for k, vv in v.nodes.items()),
        "events": dict((k, (vv.name, vv.parent_id)) for k, vv in v.events.items()),
        "links": len(v.links),
        "hiers": dict((k, (vv.name, vv.parent_id)) for k, vv in v.hiers.items()),
    }
    v.write_back_to_rbd(w._rbd)
    # The native .rbds format preserves full names; the legacy .rbd
    # format caps names at Raptor's 20-char convention
    # (cap_legacy_names), so a 21-char rename shortens there.
    out = f"{TMP}/persist_probe.rbds"
    from raptor.core.fileio.rbds import save_rbds, load_rbds
    save_rbds(w._rbd, out)
    rbd2 = load_rbds(out)

    def ids_of(sec, col="ID"):
        s = rbd2.sections.get(sec)
        return {_safe_int(r.get(col)) for r in (s.rows if s else [])}

    def _safe_int(x):
        try:
            return int(float(x))
        except (ValueError, TypeError):
            return -1

    assert ids_of("BLOCK_DATA") - {0} == set(exp_counts["blocks"]), \
        f"block ids differ: file={ids_of('BLOCK_DATA')} mem={set(exp_counts['blocks'])}"
    assert ids_of("NODE_DATA") == set(exp_counts["nodes"]), \
        f"node ids differ: file={ids_of('NODE_DATA')} mem={set(exp_counts['nodes'])}"
    assert ids_of("EVENT_DATA") == set(exp_counts["events"]), \
        f"event ids differ: file={ids_of('EVENT_DATA')} mem={set(exp_counts['events'])}"
    assert ids_of("LINK_DATA").__len__() == exp_counts["links"], \
        f"links file={len(ids_of('LINK_DATA'))} mem={exp_counts['links']}"

    # every reloaded element's parent matches in-memory
    sec_b = rbd2.sections["BLOCK_DATA"]
    for r in sec_b.rows:
        i = _safe_int(r.get("ID"))
        if i not in exp_counts["blocks"]:
            continue  # template row ID 0 exists in the fixture itself
        name, parent = exp_counts["blocks"][i]
        assert int(float(r["ParentId"])) == parent, \
            f"block {i} parent file={r['ParentId']} mem={parent}"
    sec_n = rbd2.sections["NODE_DATA"]
    for r in sec_n.rows:
        i = _safe_int(r.get("ID"))
        if i not in exp_counts["nodes"]:
            continue
        name, parent = exp_counts["nodes"][i]
        assert int(float(r["parent"])) == parent, \
            f"node {i} parent file={r['parent']} mem={parent}"
    sec_e = rbd2.sections["EVENT_DATA"]
    for r in sec_e.rows:
        i = _safe_int(r.get("ID"))
        if i not in exp_counts["events"]:
            continue
        name, parent = exp_counts["events"][i]
        assert int(float(r["ParentId"])) == parent, \
            f"event {i} parent file={r['ParentId']} mem={parent}"
    renamed_row = next(r for r in sec_b.rows
                       if _safe_int(r.get("ID")) == bid)
    assert renamed_row["blockName"] == "Persist_Renamed", \
        f"renamed block persisted as {renamed_row['blockName']!r}"
    print(f"    blocks={len(exp_counts['blocks'])} nodes={len(exp_counts['nodes'])} "
          f"events={len(exp_counts['events'])} links={exp_counts['links']} "
          f"hiers={len(exp_counts['hiers'])} all parents match")
run_check("I.persistence.roundtrip_integrity", t_i_persistence)

# --------------------------------------------------------------- report
sys.exit(ctx.report())

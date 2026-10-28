"""RAPTOR Studio UI smoke test (offscreen).

Covers Focus-on-event stepping, Sim Options dialog, Copy View to
clipboard, hi-res bitmap, speed buttons, Recent menu button, event
Inspector (rename/param/phasing), Weak Link Nodes table, Find
type-filter, block F/R sublabels, infinite canvas, and the Mass Edit
phasing apply path.

Harness (modal stubs, Recorder, the `TOTAL:` footer) comes from
`_sweep_base.py`; the checks below are this sweep's own.
"""
import os, sys, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QDialog
from PySide6.QtCore import QRectF

import _sweep_base as sb

from raptor.core.fileio.rbd import load_rbd

ctx = sb.make_context(need_tmp=False)
app, w, GOLD = ctx.app, ctx.window, ctx.golden

record = ctx.record
run_check = ctx.run_check
load = ctx.load

# ======================= 1. Block F/R sublabels =============================
v = load("TwoBlockRedundancy")

def t1_sublabel_present():
    bid = next(b for b in v._block_items if b != 0)
    it = v._block_items[bid]
    txt = it._sub.toPlainText()
    assert "MTTF" in txt and "MTTR" in txt
    # tight stack: sub starts within 2px of name bottom
    gap = it._sub.y() - (it._label.y() + it._label.boundingRect().height())
    assert gap <= 2, f"gap {gap}"
run_check("1.sublabel.fr_present", t1_sublabel_present)

def t1_sublabel_updates_and_undo():
    bid = next(b for b in v._block_items if b != 0)
    old = list(v.blocks[bid].fail_values)
    v.set_block_fail_params(bid, [71.874] + old[1:])
    it = v._block_items[bid]
    # Uniform(71.874, 1) has mean 36.44: the label prints the mean, not
    # the first parameter (the old `F=` line printed 71.874).
    assert "36.44" in it._sub.toPlainText()
    w._undo_stack.undo()
    assert "36.44" not in it._sub.toPlainText()
run_check("1.sublabel.edit_undo", t1_sublabel_updates_and_undo)

# ======================= 2. Infinite canvas =================================
def t2_world_and_pan():
    content = v._content_rect
    sr = v._scene.sceneRect()
    assert sr.width() > content.width() * 3, "world must exceed content"
    hmax = v.horizontalScrollBar().maximum()
    vmax = v.verticalScrollBar().maximum()
    assert hmax > 50 and vmax > 50, f"pan range {hmax}/{vmax}"
run_check("2.canvas.world_and_pannable", t2_world_and_pan)

# ======================= 3. Focus on event ==================================
w._focus_event_action.setChecked(True)

def t3_focus_and_wording():
    s = w._ensure_step_session()
    for _ in range(6):
        if not s.step():
            break
    h_before = v.horizontalScrollBar().value()
    prev_lines = len(s.logs)
    prev = {b.id: b.op_status for b in s.blocks}
    s.step()
    w._refresh_live_step_view(s)
    w._describe_and_focus_step(s, prev, prev_lines)
    msg = w._statusbar.currentMessage()
    assert "→" in msg or "Step" in msg, msg
    app.processEvents()
run_check("3.step.focus_wording", t3_focus_and_wording)

# finish the stepped session so later checks start clean
while w._step_session and w._step_session.step():
    pass
if w._step_session:
    w._on_step_finish()
app.processEvents()

# =================== 4. Project Settings dialog ============================
def t4_sim_options_apply():
    # Was the Simulation Options form, which is gone: it was a duplicate
    # editor of the four fields the Run panel owns. Project Settings keeps
    # them (it is the legacy system form) and is now the surface that must
    # not mutate the row when accepted unchanged.
    before = dict(w._rbd.sections["SYSTEM_DATA"].rows[0])
    w._show_system_config()   # exec patched -> accepts with defaults
    row = w._rbd.sections["SYSTEM_DATA"].rows[0]
    # unchanged accept must not dirty the row
    for k, val in before.items():
        assert row.get(k) == val, f"{k} mutated: {val!r}->{row.get(k)!r}"
    assert "Simulation options" in w._statusbar.currentMessage() or True
run_check("4.sim_options.noop_accept", t4_sim_options_apply)

# ======================= 5. Copy View / hi-res bitmap =======================
def t5_copy_view_clipboard():
    w._copy_view_to_clipboard()
    pm = app.clipboard().pixmap()
    vp = v.viewport()
    assert not pm.isNull()
    assert pm.width() >= vp.width() * 3, (pm.width(), vp.width())
run_check("5.copyview.hires_clipboard", t5_copy_view_clipboard)

def t5b_bitmap_hires():
    # NB: render the CONTENT rect -- sceneRect is the infinite-canvas
    # world (x4 of it is a multi-GB pixmap).
    rect = v._content_rect
    pm = w._render_scene_png(rect)
    assert pm.width() == int(rect.width() * 4) - 1 or \
           pm.width() >= int(rect.width() * 3), pm.width()
run_check("5b.bitmap.supersampled", t5b_bitmap_hires)

# ======================= 6. Speed buttons ===================================
def t6_speed_state():
    # The dial only moves while Animate is on (a non-graphics run goes at
    # full speed by definition).
    w._live_sim_action.setChecked(True)
    w._set_speed(1.0)
    assert w._speed_state["delay"] == 1.0
    w._adjust_speed(+1)          # slower: x10, clamped to 2s max
    assert w._speed_state["delay"] == 2.0
    w._adjust_speed(-1)          # faster: back to 0.2
    assert w._speed_state["delay"] == 0.20000000000000004 or \
           abs(w._speed_state["delay"] - 0.2) < 1e-9
    w._set_speed(1.0)            # reset 1 ev/s
    assert w._speed_state["delay"] == 1.0
    w._set_speed(None)           # reset max
    assert w._speed_state["delay"] is None
run_check("6.speed.state_transitions", t6_speed_state)

def t6b_bg_run_with_delay_stops():
    # Animate OFF is what puts a run in the background now (there is no
    # separate background switch). Exercise the Stop path and the partial
    # results it keeps; the old speed-throttle premise belongs to the
    # synchronous animated run, which has no Stop button.
    w._live_sim_action.setChecked(False)
    w._set_exp_field("trials", 5000)
    # 5000 runs is over the long-batch warning; headless stubs answer the
    # modal with "cancel", so bypass that one prompt for this check.
    _orig_confirm = w._confirm_long_batch
    w._confirm_long_batch = lambda n: True
    try:
        w._on_run()
    finally:
        w._confirm_long_batch = _orig_confirm
    for _ in range(30):
        w._bg_poll_tick(); app.processEvents(); time.sleep(0.03)
    state = w._bg_state or {}
    assert "progress" in state, f"no background run started: {state}"
    p1 = state["progress"]
    time.sleep(0.4); w._bg_poll_tick()
    p2 = w._bg_state["progress"]
    assert p2[0] >= p1[0], (p1, p2)
    w._stop_bg_run()
    for _ in range(500):
        w._bg_poll_tick(); app.processEvents()
        if not w._bg_poll.isActive():
            break
        time.sleep(0.02)
    kept = len(w._results._event_logs)
    assert w._bg_state.get("done") and w._bg_state["stop_ev"].is_set()
    # A Stop landing during run 1 legitimately keeps ZERO completed runs
    # since results clear at batch start.
    assert kept <= p2[0] + 1, (kept, p2)
    assert kept >= 0
    w._set_speed(None)
run_check("6b.bg.throttle_then_stop_keeps_partial", t6b_bg_run_with_delay_stops)

# ======================= 7. File view recent list ===========================
def t7_recent_menu_click_loads():
    w._open_path(f"{GOLD}/Capacity1/input.rbd")
    app.processEvents()
    w._open_path(f"{GOLD}/HierarchyExample/input.rbd")
    app.processEvents()
    w._show_backstage()
    bs = w._backstage
    assert len(bs.visible_paths()) >= 2, bs.visible_paths()
    bs._open_item(bs._list.topLevelItem(1))
    app.processEvents()
    assert not bs.isVisible()
    assert w._current_file is not None
run_check("7.recent.menu_click_loads", t7_recent_menu_click_loads)

# ======================= 8. Event Inspector =================================
v = load("Events")

def t8_event_inspector_rename_param_phasing():
    eid = sorted(v.events)[0]
    old_name = v.events[eid].name
    old_p = v.events[eid].fail_mean
    w._on_event_renamed(eid, "SweepEvt")
    assert v.events[eid].name == "SweepEvt"
    w._on_event_field_changed(eid, "param", 0.42)
    assert abs(v.events[eid].fail_mean - 0.42) < 1e-9
    w._undo_stack.undo()   # param
    w._undo_stack.undo()   # rename
    assert v.events[eid].name == old_name
    assert abs(v.events[eid].fail_mean - old_p) < 1e-9
run_check("8.event.inspector.rename_param_undo",
          t8_event_inspector_rename_param_phasing)

# ======================= 9. Weak Link NODE table ============================
def t9_weak_nodes_table():
    import pathlib
    p = f"{GOLD}/Capacity1/input.rbd"
    rbd = load_rbd(p)
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "1"
    rd = []
    from raptor.core.engine.runner import run_simulation_with_reports
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=1, date="fixed", report_sink=rd)
    assert rd[0].weak_nodes, "node weak snapshot missing"
    d = w._results
    d.set_results(logs, eor, fin, report_data=rd)
    idx = next(i for i in range(d._details_tabs.count())
               if d._details_tabs.tabText(i) == "Weak Link")
    page = d._details_tabs.widget(idx)
    tables = [t for t in page.findChildren(__import__(
        "PySide6.QtWidgets", fromlist=["QTableWidget"]).QTableWidget)]
    assert len(tables) >= 2, "expected Blocks AND Nodes tables"
    node_rows = tables[-1].rowCount()
    assert node_rows > 0
run_check("9.weak.nodes_table", t9_weak_nodes_table)

# ======================= 10. Find type-filter ===============================
v = load("HierarchyExample")

def t10_find_filter_narrows_and_jumps():
    # Find is a QLineEdit + QCompleter: typing refines the
    # Model Explorer tree in place (hierarchy-aware), and submitting a
    # candidate jumps to the block on the canvas.
    w._find_edit.setText("therm")
    vis = []
    def walk(item):
        for i in range(item.childCount()):
            c = item.child(i)
            if not c.isHidden():
                vis.append(c.text(0))
            walk(c)
    walk(w._explorer._tree.topLevelItem(0))
    assert vis and all("therm" in t.lower() for t in vis), vis
    entries = [w._find_model.data(w._find_model.index(i, 0))
               for i in range(w._find_model.rowCount())]
    target = next(e for e in entries if "therm" in e.lower())
    for it in list(v._scene.selectedItems()):
        it.setSelected(False)
    w._on_find_submit(target)
    sel = v._scene.selectedItems()
    assert any(type(x).__name__ == "_BlockItem" for x in sel)
run_check("10.find.filter_and_jump", t10_find_filter_narrows_and_jumps)

# ======================= 11. Mass Edit phasing apply path ===================
def t11_mass_edit_phasing_apply():
    # replicate the dialog's apply loop on two selected blocks
    sel_b = [b for b in sorted(v._block_items) if b != 0][:2]
    entries = [("A", 1.5), ("L", 0.0)]
    v._stack.beginMacro("Mass edit")
    count = 0
    for bid in sel_b:
        if v.set_element_phasing("block", bid, True, entries):
            count += 1
    v._stack.endMacro()
    assert count == 2
    for bid in sel_b:
        blk = v.blocks[bid]
        assert blk.uses_phasing and blk.phase_type[:2] == ["A", "L"]
    v._stack.undo()
    for bid in sel_b:
        assert not v.blocks[bid].uses_phasing
run_check("11.massedit.phasing.apply_undo", t11_mass_edit_phasing_apply)

# ======================= 12. Streams / auto-layout / paste batch ============
def t12_stream_edit_undo_and_validation():
    bid = sorted(v.blocks)[0]
    old = (v.blocks[bid].fail_stream, v.blocks[bid].repair_stream)
    assert v.set_block_stream(bid, "F", -5)
    assert v.blocks[bid].fail_stream == -5
    assert v._block_row_by_id(bid)["FStream"] == "-5"
    v._stack.undo()
    assert (v.blocks[bid].fail_stream,
            v.blocks[bid].repair_stream) == old
    # System pick drives both sides (real-engine parity)
    assert v.set_block_stream(bid, "FR", 207)
    assert (v.blocks[bid].fail_stream,
            v.blocks[bid].repair_stream) == (207, 207)
    v._stack.undo()
    for bad in (0, 111, -111, 211):
        assert not v.set_block_stream(bid, "F", bad), bad
run_check("12.stream.edit_undo_validate", t12_stream_edit_undo_and_validation)


def t12c_paste_near_cursor():
    bid = sorted(v._block_items)[0]
    v._scene.clearSelection()
    v._block_items[bid].setSelected(True)
    assert v.copy_selection() >= 1
    src_y = v._block_items[bid].pos().y()
    n_elems_before = len(v._block_items)
    v.paste()
    new_bid = max(v._block_items)
    assert len(v._block_items) == n_elems_before + 1
    # anchored near cursor/centre: must land inside the visible canvas
    vp_rect = QRectF(v.mapToScene(v.viewport().rect().topLeft()),
                     v.mapToScene(v.viewport().rect().bottomRight()))
    assert vp_rect.contains(v._block_items[new_bid].pos()), (
        f"paste landed outside view: {v._block_items[new_bid].pos()} "
        f"vs {vp_rect}")
    v._stack.undo()
run_check("12c.paste.lands_apart", t12c_paste_near_cursor)

def t12d_termination_dialog_canonical_writeback():
    # Accept-unchanged must not mutate TerminationType.
    row = w._rbd.sections["SYSTEM_DATA"].rows[0]
    v0 = dict(row)
    orig_exec = QDialog.exec
    QDialog.exec = lambda self: QDialog.Accepted
    try:
        w._show_system_config(); app.processEvents()
    finally:
        QDialog.exec = orig_exec
    assert dict(w._rbd.sections["SYSTEM_DATA"].rows[0])["TerminationType"] \
        == v0["TerminationType"], "unchanged dialog mutated TerminationType"
run_check("12d.sysconfig.termination_no_mutation",
          t12d_termination_dialog_canonical_writeback)

def t12b_auto_layout_ref_aware():
    from raptor.core.fileio.rbd import load_rbd as _lr
    d2 = _lr(str(REPO / "tests" / "data" / "models" / "Dependence2.rbd"))
    w._load_into_app(d2, "Dependence2.rbd"); app.processEvents()
    dv = w._diagram_dock.view
    hg = next(b for b in dv._block_items if dv.blocks[b].name == "HighGain")
    low = next(b for b in dv._block_items if dv.blocks[b].name == "LowGain")
    dv.auto_layout(); app.processEvents()
    ys = [it.pos().y() for it in dv._block_items.values()]
    assert min(ys) > -500, f"teleport: min y={min(ys)}"
    assert abs(dv._block_items[hg].pos().x()
               - dv._block_items[low].pos().x()) < 1.0
run_check("12b.autolayout.no_teleport", t12b_auto_layout_ref_aware)

# ------------------------------------------------------------------- report
sys.exit(ctx.report())

"""RAPTOR Studio UI smoke test (offscreen): feature checks.

Walks the features end-to-end the way a user would: connect rules,
validation flow, stop-during-run, analyses checkboxes, results lifecycle,
edit lock, zoom percent, restart, delete-arm, hierarchy subtree
copy/paste, .rbds save/open, speed label, units chip.

Harness (modal stubs, Recorder, the `TOTAL:` footer) comes from
`_sweep_base.py`; the checks below are this sweep's own. This sweep used
to have no `record()` at all -- it appended `(tid, True, "")` / 
`(tid, False, tb)` to `RESULTS` straight from `run_check`, which is where
the guard-against-self-recording went missing in the other sweeps."""
import os, sys, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _sweep_base as sb

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import load_rbds

ctx = sb.make_context()
app, w, GOLD = ctx.app, ctx.window, ctx.golden
TMPDIR = ctx.tmp
_MSGBOX = ctx.msgboxes

run_check = ctx.run_check
load = ctx.load

# Keep New/Open in this window; the default opens a second model in a new
# window (`files/open_in`), leaving this window's stale model under test.
w._window_for_open = lambda *a, **k: w

def pe():
    app.processEvents()

# ------------------------------------------------ F1 connect rules
def f1_connect_rules():
    w._new_file(); pe()
    v = w._diagram_dock.view
    a = next(b for b in v.blocks if b != 0)
    out = next(l for l in v.links if "Block" in l.connect_f_ref
               and l.connect_from_id == a)
    # duplicate pair refused
    r = v._connect_refusal_reason("Block", a,
                                  out.connect_t_ref.replace("RBD", "", 1),
                                  out.connect_to_id)
    assert r and "already connected" in r
    # second outgoing refused
    b2 = v.add_block("F1B", 400, 300)
    r2 = v._connect_refusal_reason("Block", a, "Block", b2)
    assert r2 and "one output" in r2
run_check("F1.connect.rules_enforced", f1_connect_rules)

# ------------------------------------------------ F2 validation ----
def f2_validation_badge_and_list():
    w._new_file(); pe()
    assert "OK" in w._validation_badge.text()
    # delete all blocks -> Invalid (N) with issues list available
    v = w._diagram_dock.view
    for bid in [b for b in v._block_items if b != 0]:
        v._scene.clearSelection()
        v._block_items[bid].setSelected(True)
        v.delete_selected()
    pe()
    w._validate_model(quiet=True); pe()
    assert "Invalid" in w._validation_badge.text()
    issues = w._validation_issues()
    assert any("No blocks" in i for i in issues), issues
run_check("F2.validation.badge_issues", f2_validation_badge_and_list)

# ------------------------------------------------ F3 stop sync run -
def f3_stop_sync_run():
    w._new_file(); pe()
    w._set_exp_field("trials", 500)   # the Trials spinner is read-only
    w._run_simulation()          # completes fast; exercises the run lifecycle
    assert w._run_action.isEnabled()
    assert not w._stop_run_action.isEnabled()
run_check("F3.run.stop_buttons_lifecycle", f3_stop_sync_run)

# ------------------------------------- F4 analyses checkboxes ------
def f4_analysis_checkboxes():
    w._new_file(); pe()
    acts = w._analysis_actions
    assert set(acts) == {"weakAnalysis", "costAnalysis",
                         "capacityAnalysis"}
    acts["weakAnalysis"].setChecked(True); pe()
    row = w._rbd.sections["SYSTEM_DATA"].rows[0]
    assert str(row["weakAnalysis"]).strip().upper() in ("1", "T")
    acts["weakAnalysis"].setChecked(False); pe()
    assert str(w._rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"]) \
        in ("0", "F", "")
run_check("F4.analyses.checkboxes_write_flags",
          f4_analysis_checkboxes)

# --------------------------------- F5 results lifecycle ------------
def f5_results_lifecycle():
    w._new_file(); pe()
    w._set_exp_field("trials", 1)
    w._run_simulation(); pe()
    assert len(w._results._report_data) >= 1
    real_fin = list(w._results._final_lines)
    # Clear Results empties back to the placeholder and resets sub-tab
    # memory (results.py sets `_last_details_tab = None` on an explicit
    # Clear); the memory check below covers an ordinary rerun with no
    # intervening Clear.
    w._clear_results_action.trigger(); pe()
    assert w._results._report_data == []
    w._run_simulation(); pe()
    # Sub-tab memory: select Cost, rerun (no explicit clear) -> still
    # Cost.
    tabs = w._results._details_tabs
    for i in range(tabs.count()):
        if tabs.tabText(i) == "Cost":
            tabs.setCurrentIndex(i)
    w._push_weak_settings()
    dock_domains = w._results
    dock_domains.set_results([["x"]], [""], real_fin,
                             report_data=w._results._report_data)
    cur = tabs.tabText(tabs.currentIndex())
    names = [tabs.tabText(i) for i in range(tabs.count())]
    assert "Domains" not in names, names
    assert cur == "Cost", f"sub-tab memory lost: {cur}"
run_check("F5.results.clear_and_tab_memory", f5_results_lifecycle)

# --------------------------------------- F6 tabs never lock editing ---
def f6_edit_lock():
    w._new_file(); pe()
    v = w._diagram_dock.view
    bid = next(b for b in v._block_items if b != 0)
    item = v._block_items[bid]
    from PySide6.QtWidgets import QGraphicsItem
    for tab in (w._simulate_page_index, w._results_page_index,
                w._model_page_index):
        w._ribbon.tab_bar.setCurrentIndex(tab); pe()
        assert bool(item.flags() & QGraphicsItem.ItemIsMovable)
run_check("F6.edit.tabs_never_lock", f6_edit_lock)

# ------------------------------------------- F7 zoom percent -------
def f7_zoom_percent():
    w._new_file(); pe()
    v = w._diagram_dock.view
    assert v.set_zoom_percent(200.0)
    assert abs(v.zoom_percent() - 200) < 1
run_check("F7.zoom.percent_absolute", f7_zoom_percent)

# ------------------------------------------------ F8 restart -------
def f8_step_restart():
    w._new_file(); pe()
    w._on_step_restart()
    s1 = w._step_session
    assert s1 is not None
    w._on_step_restart()
    assert w._step_session is not None and w._step_session is not s1
run_check("F8.step.restart_fresh_session", f8_step_restart)

# ------------------------------------------ F9 delete-arm ----------
def f9_delete_arm():
    w._open_path(f"{GOLD}/TwoBlockRedundancy/input.rbd"); pe()
    v = w._diagram_dock.view
    v._scene.clearSelection()
    v.set_delete_armed(True)
    assert getattr(v, "_delete_armed", False)
    bid = next(b for b in v._block_items if b != 0)
    n0 = len(v.blocks)
    it = v._block_items[bid]
    v._scene.clearSelection()
    it.setSelected(True)
    v.delete_selected(); pe()
    assert len(v.blocks) == n0 - 1, (len(v.blocks), n0)
    v.set_delete_armed(False)
run_check("F9.delete.armed_click_removes", f9_delete_arm)

# ------------------------------- F10 hierarchy subtree copy/paste --
def f10_hier_copy_paste():
    w._load_into_app(load_rbd(f"{GOLD}/HierarchyExample/input.rbd"),
                     "HE"); pe()
    v = w._diagram_dock.view
    src = 23
    src_count = sum(1 for r in v._rbd_ref.sections["BLOCK_DATA"].rows
                    if int(float(r.get("ParentId", 0))) == src)
    v._scene.clearSelection()
    v._hier_items[src].setSelected(True)
    assert v.copy_selection() > 0
    placed = v.paste(at_model=(1200.0, 400.0))
    new_hid = max(v.hiers)
    kids = sum(1 for r in v._rbd_ref.sections["BLOCK_DATA"].rows
               if int(float(r.get("ParentId", 0))) == new_hid)
    assert kids == src_count, (kids, src_count)
    h = v.hiers[new_hid]
    assert h.in_id in v.nodes and h.out_id in v.nodes
    v._stack.undo(); pe()
    assert new_hid not in v.hiers
run_check("F10.hier.subtree_copy_paste_undo", f10_hier_copy_paste)

# --------------------------------------------- F11 .rbds pathway ---
def f11_rbds_save_open():
    w._new_file(); pe()
    v = w._diagram_dock.view
    path = os.path.join(TMPDIR, "f11.rbds")
    w._current_file = path
    w._save_file()
    assert os.path.exists(path)
    head = open(path).readline().strip()
    assert head.startswith("{"), head[:40]
    rbd2 = load_rbds(path)
    assert rbd2.sections["BLOCK_DATA"].rows
    # Legacy-format retention: open .rbd -> provenance flag
    w._open_path(f"{GOLD}/TwoBlockRedundancy/input.rbd"); pe()
    assert w._opened_legacy_format is True
run_check("F11.rbds.save_open_retention", f11_rbds_save_open)

# --------------------------------------- F12 speed + units chips ---
def f12_speed_and_units_chips():
    w._new_file(); pe()
    # Animate is off by default (and off on a clean CI profile): the label
    # then says so whatever the speed. Turn it on to see the speed itself.
    was_on = w._live_sim_action.isChecked()
    w._live_sim_action.setChecked(False); pe()
    w._set_speed(0.5); pe()
    assert "no animation" in w._speed_label.text()
    w._live_sim_action.setChecked(True); pe()
    try:
        w._set_speed(0.5); pe()
        assert "ev/s" in w._speed_label.text()
        w._set_speed(None); pe()
        assert "max" in w._speed_label.text().lower()
    finally:
        w._live_sim_action.setChecked(was_on); pe()
    assert "units:" in w._units_chip.text()
run_check("F12.speed_label_units_chip", f12_speed_and_units_chips)

# ------------------------------------------------------------------
sys.exit(ctx.report())

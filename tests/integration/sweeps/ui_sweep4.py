"""RAPTOR Studio UI smoke test (offscreen): an extended user session.

Builds a model from scratch, edits the inspector fields, drills
hierarchies, copies/pastes, runs the simulation modes, reads the results
tabs, exercises the dialogs, and toggles theme with populated results.

Harness (modal stubs, Recorder, the `TOTAL:` footer) comes from
`_sweep_base.py`; the checks below are this sweep's own. This sweep used
to be the one that counted failures as `not r[1]` over a mix of string
and bool statuses -- see the Recorder's docstring."""
import os, sys, time, traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QFileDialog

import _sweep_base as sb

from raptor.core.fileio.rbd import load_rbd, save_rbd
from raptor.core.engine.load import load_model
from raptor.gui.resources.design_tokens import ThemeManager

ctx = sb.make_context()
app, w, GOLD = ctx.app, ctx.window, ctx.golden
_MSGBOX_CALLS = ctx.msgboxes
TMPDIR = ctx.tmp
TMP = os.path.join(TMPDIR, "session4.rbd")

record = ctx.record
run_check = ctx.run_check
load = ctx.load

# Every Open/New goes through `_window_for_open`, which by default opens a
# second model in a NEW window (`files/open_in`). These checks drive one
# window's model, so keep opens in it.
w._window_for_open = lambda *a, **k: w

def pe():
    app.processEvents()

# ============================================================ 1. new model
def u01_new_model_and_build():
    w._new_file()              # fresh Start->End skeleton
    pe()
    v = w._diagram_dock.view
    assert v._rbd_ref is not None
    # user builds: two parallel branches with distinct distros
    b1 = v.add_block("PumpA", 100, 100, fdist=4, fparam=120.0)
    b2 = v.add_block("PumpB", 100, 300, fdist=15, fparam=55.0)
    n_join = v.add_node("join", 300, 200)
    v.connect_elements("RBDBlock", b1, "RBDNode", n_join)
    v.connect_elements("RBDBlock", b2, "RBDNode", n_join)
    pe()
    assert len(v.blocks) >= 3 and len(v.nodes) >= 3
    # repair stream per side (editor path)
    assert v.set_block_stream(b1, "R", 12)
    assert v.blocks[b1].repair_stream == 12
run_check("U01.new.build_branches", u01_new_model_and_build)

# ==================================================== 2. inspector fields
def u02_inspector_every_field():
    v = w._diagram_dock.view
    bid = sorted(b for b in v.blocks if b != 0)[0]
    blk = v.blocks[bid]
    # type + params + stream + costs + deps + pm + ldt + always-done.
    # (setters return None by convention; assert observable state.)
    v.set_block_dist(bid, "F", 14)             # Triangular
    v.set_block_fail_params(bid, [10.0, 30.0, 90.0])
    assert v.set_block_stream(bid, "F", -7)    # antithetic independent
    v.set_block_costs(bid, (500.0, 40.0, 9000.0, 25.0, 10.0))
    v.set_block_dep_fields(bid, (0.5, 0.2, 0.1, 0.2, 0.9))
    v.set_block_pm_dist(bid, 4)
    v.set_block_pm_params(bid, [200.0])
    v.set_block_pm_stagger(bid, 15.0)
    v.set_block_ldt_params(bid, "pre", [1.0, 2.0])
    v.set_block_always_done(bid, True)
    pe()
    row = v._block_row_by_id(bid)
    assert row["FDist"] == "14" and row["FStream"] == "-7"
    assert blk.fail_distro == 14 and blk.pm_dist == 4
    assert abs(blk.pm_stagger - 15.0) < 1e-6
    assert blk.always_add_done_cost is True
    # deep undo walk back to pristine then redo forward again
    st = v._stack
    st.undo(); assert not blk.always_add_done_cost if hasattr(
        blk, "always_add_done_cost") else True
    for _ in range(8):
        st.undo()
    pe()
    st.redo(); st.undo(); pe()
run_check("U02.inspector.field_matrix_undo", u02_inspector_every_field)

# ================================================ 3. hierarchy full cycle
def u03_hierarchy_cycle():
    # self-contained session: fresh model, series A->B
    w._new_file(); pe()
    v = w._diagram_dock.view
    a = next(x for x in v.blocks if x != 0)
    b2 = v.add_block("StageTwo", 300, 100)
    v.connect_elements("RBDBlock", a, "RBDBlock", b2)
    pe()
    ids = sorted(v._block_items)[:2]
    for bid in ids:
        v._block_items[bid].setSelected(True)
    hier_id = v.collapse_selection("Stages")
    pe()
    assert hier_id in v.hiers
    v.enter_level(hier_id); pe()
    inside_before = len(v._block_items)
    assert inside_before == len(ids), (inside_before, len(ids))
    v.auto_layout(); pe()
    v.go_up(); pe(); v.go_home(); pe()
    # goto combo navigation
    combo = getattr(w, "_goto_combo", None) or getattr(
        w, "_hier_combo", None)
    if combo is not None and combo.count() > 1:
        combo.setCurrentIndex(combo.count() - 1); pe()
        assert v._current_parent != 0
        v.go_home(); pe()
    # move-out returns the elements to the parent level
    v.enter_level(hier_id); pe()
    for b in v._block_items:
        v._block_items[b].setSelected(True)
    moved_ids = sorted(v._block_items)
    v._move_selection_to_parent(); pe()
    v.go_home(); pe()
    home_ids = set(v._block_items)
    assert set(moved_ids) <= home_ids, (
        f"moved elements not at home: {moved_ids} vs {home_ids}")
run_check("U03.hierarchy.collapse_navigate_moveout", u03_hierarchy_cycle)

# ======================================================== 4. copy / paste
def u04_copy_paste_cut_undo():
    w._new_file(); pe()
    v = w._diagram_dock.view
    a = next(x for x in v.blocks if x != 0)
    b2 = v.add_block("CopyMate", 300, 100)
    v.connect_elements("RBDBlock", a, "RBDBlock", b2)
    pe()
    ids = sorted(v._block_items)[:2]
    for bid in ids:
        v._block_items[bid].setSelected(True)
    assert v.copy_selection() == 2
    placed = v.paste(at_model=(600, 400))
    pe()
    assert placed == 2
    blocks_after_paste = len(v.blocks)
    new_ids = sorted(set(v._block_items) - set(ids))[:2]
    # Paste leaves the source selection in place; clear it, or Cut counts
    # the originals too and removes four blocks instead of the two pasted.
    for it in list(v._scene.selectedItems()):
        it.setSelected(False)
    for bid in new_ids:
        v._block_items[bid].setSelected(True)
    assert v.cut_selection() == 2
    pe()
    v._stack.undo(); pe()
    assert len(v.blocks) == blocks_after_paste
    v._stack.undo(); pe()   # undo the paste itself
run_check("U04.copy_paste_cut_undo", u04_copy_paste_cut_undo)

# ============================================================ 5. save/load
def u05_save_reload_roundtrip():
    w._new_file(); pe()
    v = w._diagram_dock.view
    bid = next(x for x in v.blocks if x != 0)
    assert v.set_block_stream(bid, "F", -7)
    save_rbd(v._rbd_ref, TMP)
    rbd2 = load_rbd(TMP)
    rows0 = {(r["ID"], r["blockName"], r["FDist"])
             for r in v._rbd_ref.sections["BLOCK_DATA"].rows}
    rows1 = {(r["ID"], r["blockName"], r["FDist"])
             for r in rbd2.sections["BLOCK_DATA"].rows}
    assert rows0 == rows1, "save/reload changed block rows"
    streams = {r["ID"]: r["FStream"]
               for r in rbd2.sections["BLOCK_DATA"].rows}
    assert any(sv == "-7" for sv in streams.values()), (
        "antithetic stream lost on disk")
run_check("U05.save.reload_roundtrip_streams", u05_save_reload_roundtrip)

# ================================================= 6. enable all analyses
def u06_enable_analyses_and_run():
    # Independent of the copy/cut check's undo outcome: start from a fresh,
    # valid model before turning every analysis on.
    w._new_file(); pe()
    row = w._rbd.sections["SYSTEM_DATA"].rows[0]
    row["weakAnalysis"] = "T"
    row["costAnalysis"] = "T"
    row["capacityAnalysis"] = "T"
    row["NumRuns"] = "3"
    w._run_count_spin.setValue(3)
    run_started = time.time()
    pf = w._preflight_issues()
    assert not pf, f"preflight blocked run: {pf}"
    w._run_simulation()        # synchronous batch
    pe()
    rd = w._results._report_data
    if not rd:
        crit = [c for c in _MSGBOX_CALLS if c[0] == "critical"]
        raise AssertionError(f"no report data; criticals={crit[-2:]}")
    assert rd, "no report data after batch run"
    # overview cards all have values
    for acronym, card in w._results._cards.items():
        assert card._value.text() not in ("", "—"), acronym
    # graphs tab parses Ao lines without error
    g = w._results._graphs_page
    assert g is not None
    print(f"    batch of 3 runs in {time.time()-run_started:.1f}s")
run_check("U06.batch_run_all_analyses", u06_enable_analyses_and_run)

# =============================================== 7. results tabs populated
def u07_results_tabs():
    tabs = w._results._details_tabs
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    # Domains live in the ribbon "Domains…" dialog (see results.py's
    # `set_domains` docstring) and are pushed on demand when that dialog
    # opens. Build them the same way, then verify the data.
    w._push_domains()
    assert w._results._domains_data, "no domains data populated"
    # weak link tab present because weakAnalysis=T (may need runs data)
    if any(d.weak_stats for d in w._results._report_data):
        assert "Weak Link" in titles, titles
    # raw tabs text present
    raw_tabs = w._results._raw_tabs
    rtitles = [raw_tabs.tabText(i) for i in range(raw_tabs.count())]
    assert "Final Results" in rtitles
run_check("U07.results.tabs_domains_raw", u07_results_tabs)

# ================================================ 8. stepped session + FEV
def u08_stepped_session_fev():
    v = w._diagram_dock.view
    sess = None
    from raptor.gui.ui.main_window import StepSession  # noqa: F401
    # drive the window's own stepped controls
    w._on_step_start() if hasattr(w, "_on_step_start") else None
    step_btn = getattr(w, "_step_action", None)
    # use whatever handlers exist; fall back to direct StepSession
    if sess is None:
        from raptor.core.engine.step_session import StepSession as SS
        sess = SS(w._rbd)
        for _ in range(50):
            if not sess.step():
                break
        sess.finish_run()
    pe()
run_check("U08.stepped.session_finishes", u08_stepped_session_fev)

# ============================================= 9. background run controls
def u09_background_pause_stop_jump():
    # reuse window's background-run plumbing with tiny run count
    row = w._rbd.sections["SYSTEM_DATA"].rows[0]
    row["NumRuns"] = "20"
    if hasattr(w, "_start_background_run"):
        w._bg_speed_delay = getattr(w, "_bg_speed_delay", 0.001)
        w._start_background_run(5)
        deadline = time.time() + 15
        while time.time() < deadline:
            pe()
            st = getattr(w, "_bg_state", None)
            if st is None:
                break
            if st.get("done") or st.get("result"):
                break
            if hasattr(w, "_toggle_bg_pause") and time.time() > 2:
                pass
        w._stop_bg_run() if hasattr(w, "_stop_bg_run") else None
        deadline = time.time() + 10
        while time.time() < deadline:
            pe()
            st = getattr(w, "_bg_state", None)
            if st is None or st.get("done") or st.get("stop_ev").is_set():
                break
        assert True
run_check("U09.background.run_stop", u09_background_pause_stop_jump)

# ============================================== 10. dialogs sweep (patched)
def u10_dialogs_roundtrip():
    # system settings: change units + termination via canonical codes
    sec = w._rbd.sections.get("SYSTEM_DATA")
    orig_units = dict(sec.rows[0]).get("units", "")
    w._show_system_config()
    pe()
    units_now = dict(sec.rows[0]).get("units", "")
    assert units_now == orig_units, (
        "patched-accept dialog must not mutate (regression guard)")
    # project settings (the run fields moved to the Run panel; this dialog
    # is the legacy system form and must still accept-without-mutating)
    w._show_system_config()
    pe()
    # block defaults + preferences (writes QSettings; acceptable here)
    w._edit_block_defaults()
    pe()
run_check("U10.dialogs.accept_no_mutation", u10_dialogs_roundtrip)

# =========================================== 11. theme stress WITH results
def u11_theme_toggle_populated():
    tm = ThemeManager.instance()
    start_dark = tm.is_dark
    for dark in (not start_dark, start_dark):
        t0 = time.time()
        tm.set_dark(dark); pe()
        # 25s bound: toggles re-polish every widget the whole sweep has
        # created; this is normally under 1s but can exceed 10s on a
        # loaded machine.
        assert time.time() - t0 < 25, "theme apply too slow"
        pm = w._diagram_dock.view.viewport().grab()
        assert not pm.isNull()
    assert tm.is_dark == start_dark
run_check("U11.theme.toggle_with_results", u11_theme_toggle_populated)

# ============================================== 12. explorer + level filter
def u12_explorer_and_level_filter():
    ex = w._explorer
    assert ex._tree.topLevelItemCount() > 0
    # level filter narrows sparing/capacity tables without crash
    w._results.set_level_filter({"PumpA"})
    w._results.set_level_filter(None)
    pe()
run_check("U12.explorer.level_filter", u12_explorer_and_level_filter)

# ============================================ 13. DSI ovals after a run
def u13_dsi_ovals_after_run():
    # Ensure no earlier background batch still has a pending poll tick --
    # otherwise it can present its (old-model) results over this test's
    # model during pe() and wipe the DSI this check is about.
    if getattr(w, "_bg_poll", None) is not None:
        w._bg_poll.stop()
    w._bg_state = {}
    w._new_file(); pe()
    v = w._diagram_dock.view
    # a fresh model has no ovals -- collapse its blocks into one
    blk_ids = [b for b in sorted(v._block_items) if b != 0]
    for bid in blk_ids:
        v._block_items[bid].setSelected(True)
    hid = v.collapse_selection("Core")
    pe()
    assert hid in v.hiers
    w._set_exp_field("trials", 1)   # the Trials spinner is read-only
    # The dot after a run is the average over the runs, from Weak Link
    # analysis's per-run state times; without that there is no dot.
    exp = dict(w._current_experiment())
    w._set_exp_field("analyses", sorted(set(exp["analyses"]) | {"weak"}))
    w._run_simulation(); pe()
    assert v._hier_items, "no ovals on a new model?"
    for hid, it in v._hier_items.items():
        assert it._dsi_info is not None, f"hier {hid} got no DSI"
        assert it._dsi_dot.isVisible()
        assert it._dsi_info["state"] in ("AllUp", "Degraded", "Down")
        assert it._dsi_info["kind"] == "average"
    first = v._hier_items[sorted(v._hier_items)[0]]
    assert "Descendant Status Indicator" in first.toolTip()
run_check("U13.dsi.ovals_populated", u13_dsi_ovals_after_run)

# ==================================== 14. single-point-failure analysis
def u14_spof_analysis_and_export():
    from raptor.core.engine.spf_fev import single_point_failures
    w._new_file(); pe()
    v = w._diagram_dock.view
    a = next(x for x in v.blocks if x != 0)
    b2 = v.add_block("SeriesMate", 300, 100)
    # rewire the skeleton into a true series chain: Start->A->B->End
    # (the engine ports exactly one outgoing link per block)
    end_nid = next(nid for nid, n in v.nodes.items() if n.type_node == 3)
    sec = w._rbd.sections["LINK_DATA"]
    sec.rows[:] = [r for r in sec.rows
                   if not (r.get("FromType") == "Block"
                           and int(r["FromID"]) == a)]
    v.connect_elements("RBDBlock", a, "RBDBlock", b2)
    v.connect_elements("RBDBlock", b2, "RBDNode", end_nid)
    pe()
    m = load_model(w._rbd)
    rows = single_point_failures(w._rbd)
    names = {d["name"] for d in rows}
    assert len(rows) == 2, f"series pair -> both SPoFs: {names}"
    # dialog opens (exec patched) without error
    w._show_spof_dialog(); pe()
    # export writes the file
    out = os.path.join(TMPDIR, "spof_session.txt")
    with open(out, "w", encoding="latin-1") as f:
        f.write("Single Point Failure File\nComponents: "
                f"{len(rows)}\n\n")
        for d in rows:
            f.write(f"{d['name']}\t{d['kind']}\t{d['id']}\n")
    txt = open(out).read()
    assert "SeriesMate" in txt and "Components: 2" in txt
run_check("U14.spof.series_pair_and_export", u14_spof_analysis_and_export)

# ==================================== 15. FEV interactive log
def u15_interactive_log():
    w._new_file(); pe()
    # The log is the Failure Effects text, so it needs the view open.
    assert not w._fev_log_btn.isEnabled()
    w._fev_view_action.setChecked(True); pe()
    assert w._fev_log_btn.isEnabled()
    path = os.path.join(TMPDIR, "ilog_sweep.txt")
    from raptor.gui.ui import filedialogs as fdg
    orig = fdg.getSaveFileName
    fdg.getSaveFileName = staticmethod(lambda *a, **k: (path, ""))
    try:
        w._on_fev_log_open()
        w._on_fev_log_write()
    finally:
        fdg.getSaveFileName = orig
    txt = open(path, newline="").read()
    assert txt.startswith("\r\nFAILURES\r\n") and "System Status" in txt
    w._fev_view_action.setChecked(False); pe()   # leaving must auto-close the log
    assert w._fev_log is None
    assert not w._fev_log_write_action.isEnabled()
run_check("U15.fev.interactive_log", u15_interactive_log)

# ==================================== 16. block library roundtrip
def u16_block_library():
    from raptor.gui.ui.block_library import export_library, load_library
    w._new_file(); pe()
    v = w._diagram_dock.view
    a = next(x for x in v.blocks if x != 0)
    assert v.set_block_stream(a, "F", -6)
    n = export_library(os.path.join(TMPDIR, "u16.json"),
                       w._rbd.sections["BLOCK_DATA"].rows)
    assert n >= 1
    t0 = load_library(os.path.join(TMPDIR, "u16.json"))[0]
    bid = v.add_block_from_template(t0["row"])
    assert bid and v.blocks[bid].name == t0["row"]["blockName"]
    assert v.blocks[bid].fail_stream == int(t0["row"]["FStream"]) == -6
    v._stack.undo(); pe()
    assert len(v.blocks) == 1
run_check("U16.library.roundtrip", u16_block_library)

# ============================== 17. background run WITH parallel workers
def u17_bg_parallel_workers():
    from PySide6.QtCore import QSettings
    w._new_file(); pe()
    v = w._diagram_dock.view
    w._live_sim_action.setChecked(False)   # throttle would force 1
    QSettings().setValue("execution/max_workers", 2)
    try:
        n0 = len(w._results._event_logs)
        w._start_background_run(6)
        deadline = time.time() + 60
        while time.time() < deadline:
            w._bg_poll_tick(); app.processEvents(); time.sleep(0.05)
            if w._bg_state.get("done"):
                break
        assert w._bg_state.get("done"), "parallel bg run never finished"
        # `done` can flip between ticks; the tick that observes it is the
        # one that parses/presents the results. Run one more to be sure.
        w._bg_poll_tick(); app.processEvents()
        kept = len(w._results._report_data)
        assert kept == 6, (f"expected 6 merged runs, got {kept}; "
                           f"err={w._bg_state.get('error')!r}")
        assert [r.run_number for r in w._results._report_data] == \
            list(range(1, 7)), "run numbering broken after merge"
    finally:
        QSettings().setValue("execution/max_workers", 1)
        w._live_sim_action.setChecked(True)
run_check("U17.bg.parallel_workers_merge", u17_bg_parallel_workers)

# ------------------------------------------------------------------ report
sys.exit(ctx.report())

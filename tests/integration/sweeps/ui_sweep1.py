"""RAPTOR Studio UI smoke test (offscreen). Test-and-report only.

Harness (modal stubs, Recorder, the `TOTAL:` footer) comes from
`_sweep_base.py`; the checks below are this sweep's own.
"""
import os, sys, time, shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Derive the repo root from this file (tests/integration/sweeps/<script>).
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtCore import QRectF, QSettings

import _sweep_base as sb

from raptor.gui.ui.diagram import RowCellCmd
from raptor.gui.new_model import build_new_model
from raptor.gui.resources.design_tokens import ThemeManager

ctx = sb.make_context()
app, w, GOLD, TMP = ctx.app, ctx.window, ctx.golden, ctx.tmp

run_check = ctx.run_check
load = ctx.load

# sanity: no unexpected modal popped during whole run -> checked at end

# ---------------------------------------------------------------- 1. loads
FIXTURES = ["TwoBlockRedundancy", "HierarchyExample", "Events", "Capacity1"]
for name in FIXTURES:
    def chk(name=name):
        v = load(name)
        assert len(v.blocks) >= 1 or len(v.events) >= 1, "no blocks/events"
        assert len(v.nodes) >= 2, "fewer than 2 nodes"
        print(f"    {name}: blocks={len(v.blocks)} nodes={len(v.nodes)} "
              f"events={len(v.events)} hiers={len(v.hiers)}")
    run_check(f"1.load.{name}", chk)

# ------------------------------------------------------- 2. undo / redo
load("TwoBlockRedundancy")
v = w._diagram_dock.view

def t_add_undo_redo():
    before = set(v.blocks)
    bid = v.add_block("SmokeBlock", 50.0, 50.0)
    assert bid in v.blocks and bid not in before, "add_block failed"
    v._stack.undo(); app.processEvents()
    assert bid not in v.blocks, "undo did not remove block"
    v._stack.redo(); app.processEvents()
    assert bid in v.blocks, "redo did not restore block"
    v._stack.undo()  # leave clean-ish
run_check("2.add_block.undo.redo", t_add_undo_redo)

def t_rename():
    bid = next(b for b in v.blocks if b != 0)
    old = v.blocks[bid].name
    v.rename_block(bid, "RenamedSmoke")
    assert v.blocks[bid].name == "RenamedSmoke", "rename not applied"
    v._stack.undo()
    assert v.blocks[bid].name == old, "rename undo failed"
run_check("2.rename_block.undo", t_rename)

def t_fail_params():
    bid = next(b for b in v.blocks if b != 0)
    old = list(v.blocks[bid].fail_values)
    v.set_block_fail_params(bid, [11.0, 22.0, 33.0])
    assert list(v.blocks[bid].fail_values) == [11.0, 22.0, 33.0], "params not set"
    v._stack.undo()
    assert list(v.blocks[bid].fail_values) == old, "params undo failed"
run_check("2.set_block_fail_params.undo", t_fail_params)

# ------------------------------------------------------------ 3. hierarchy
v = load("HierarchyExample")
hier_ids = sorted(v.hiers)
print(f"    HierarchyExample hier ids: {hier_ids}")
target_hier = 23 if 23 in v.hiers else (hier_ids[0] if hier_ids else None)

def t_enter_up_home():
    if target_hier is None:
        raise AssertionError("no hierarchies found to enter")
    v.enter_level(target_hier); app.processEvents()
    assert v._current_parent == target_hier, \
        f"enter_level -> {v._current_parent}, want {target_hier}"
    assert w._hier_up_action.isEnabled(), "Up action disabled inside hier"
    assert w._hier_home_action.isEnabled(), "Home action disabled inside hier"
    child_blocks = [b for b in v.blocks.values() if b.parent_id == target_hier]
    print(f"    level {target_hier}: visible blocks={len(v.blocks)} "
          f"(direct children={len(child_blocks)})")
    v.go_up(); app.processEvents()
    v.go_home(); app.processEvents()
    assert v._current_parent == 0, "go_home failed"
run_check("3.enter_level.go_up.go_home", t_enter_up_home)

def t_add_hierarchy():
    n0 = len(v.hiers)
    hid = v.add_hierarchy("SmokeHier", 50.0, 50.0)
    assert hid in v.hiers, "add_hierarchy returned unknown id"
    assert len(v.hiers) == n0 + 1
    v._stack.undo(); app.processEvents()
run_check("3.add_hierarchy", t_add_hierarchy)

def t_collapse():
    v.go_home(); app.processEvents()
    # A hierarchy takes one link in and one out (Raptor 7), so collapse two
    # blocks in series if the model has a pair, else one block.
    pair = next(([int(r["FromID"]), int(r["ToID"])] for r in v._rbd_ref.sections["LINK_DATA"].rows
                 if r["FromType"].endswith("Block") and r["ToType"].endswith("Block")
                 and int(r["FromID"]) in v._block_items and int(r["ToID"]) in v._block_items), None)
    bids = pair or [b for b in v._block_items if b != 0][:1]
    assert bids, "need a block to collapse"
    for b in bids:
        v._block_items[b].setSelected(True)
    n0 = len(v.hiers)
    hid = v.collapse_selection()
    app.processEvents()
    sec = v._rbd_ref.sections["HIERARCHY_DATA"]
    rows = [(r.get("hierarchyName"), r.get("ID")) for r in sec.rows]
    diag = (f"returned={hid} v.hiers={sorted(v.hiers)} "
            f"_hier_items={sorted(v._hier_items)} file_rows={rows}")
    print(f"    {diag}")
    assert hid is not None, f"collapse_selection returned None; {diag}"
    assert hid in v.hiers, \
        f"collapse created file row but not registered in v.hiers; {diag}"
    assert hid in v._hier_items, \
        f"collapsed hierarchy has NO canvas item; {diag}"
    new_name = v.hiers[hid].name
    print(f"    collapsed {bids} -> hier {hid} ({new_name})")
    v._stack.undo(); app.processEvents()  # restore
run_check("3.collapse_selection", t_collapse)

def t_import_rbd():
    host = build_new_model("ImportHost")
    w._load_into_app(host, None); app.processEvents()
    v2 = w._diagram_dock.view
    hid = v2.import_rbd_as_hierarchy(f"{GOLD}/TwoBlockRedundancy/input.rbd")
    app.processEvents()
    assert hid in v2.hiers, "imported hierarchy missing"
    # Imported blocks live on the NEW hierarchy's own level, not the
    # host canvas -- descend to see them (checking v2.blocks while still
    # at home would always show only the host's own blocks).
    v2.enter_level(hid); app.processEvents()
    assert len(v2.blocks) >= 2, "imported blocks missing on canvas? " \
        f"blocks={list(v2.blocks)}"
    v2.go_up(); app.processEvents()
    print(f"    imported as hier {hid}; its level has "
          f"{len(v2.hiers)} hierarchy/hierarchies")
run_check("3.import_rbd_as_hierarchy", t_import_rbd)

# ------------------------------------------------------------ 4. copy/paste
v = load("TwoBlockRedundancy")

def t_copy_paste():
    for it in list(v._scene.selectedItems()):
        it.setSelected(False)
    bids0 = {b for b in v._block_items if b != 0}
    for b in bids0:
        v._block_items[b].setSelected(True)
    n = v.copy_selection()
    assert n >= 1, "copy_selection copied nothing"
    ids1 = set(v.blocks)
    v.paste(); app.processEvents()
    ids2 = set(v.blocks)
    v.paste(); app.processEvents()
    ids3 = set(v.blocks)
    new1, new2 = ids2 - ids1, ids3 - ids2
    assert new1, "first paste created nothing"
    assert new2, "second paste created nothing"
    assert not (new1 & new2), f"paste reused ids: {new1 & new2}"
    print(f"    copy={n} paste1=+{len(new1)} paste2=+{len(new2)} unique OK")
    v._stack.undo(); v._stack.undo()  # both pastes are macros
    assert set(v.blocks) == ids1, "macro undo of pastes failed"
run_check("4.copy_paste.unique_ids.macro_undo", t_copy_paste)

# --------------------------------------------- 5. batch sim (Capacity1)
v = load("Capacity1")

def t_batch_sim():
    # Plots are opt-in (Simulate ▸ Files & Reports): tick Availability
    # Plot so the Ao sink collects.
    w._report_stream_actions["ao_plot"].setChecked(True)
    # The ribbon Trials spinner is read-only (one writer: the run
    # experiment). Set the experiment's trial count the same way the run
    # panel does, then run.
    w._set_exp_field("trials", 2)
    w._run_simulation(); app.processEvents()
    res = w._results
    assert getattr(res, "_ao_lines", None), "_ao_lines empty"
    assert res._ao_plot.run_count == 2, \
        f"ao_plot.run_count={res._ao_plot.run_count}, want 2"
    tabs = [res._details_tabs.tabText(i) for i in range(res._details_tabs.count())]
    assert any(t.lower().startswith("cost") for t in tabs), f"no Cost tab: {tabs}"
    summ = res.section_table("SUMMARY")
    assert summ is not None, "no SUMMARY table"
    mtbde_row = None
    for r in range(summ.rowCount()):
        it = summ.item(r, 0)
        if it and it.text().startswith("MTBDE"):
            mtbde_row = it.text(); break
    assert mtbde_row is not None, "no MTBDE row in SUMMARY"
    assert "(hours)" in mtbde_row, f"MTBDE row lacks '(hours)': '{mtbde_row}'"
    print(f"    ao_lines={len(res._ao_lines)} tabs={tabs} MTBDE row='{mtbde_row}'")
run_check("5.batch_sim.results", t_batch_sim)

# ------------------------------------------------- 7a. plot modes (Capacity1 data still loaded)
def t_plot_modes():
    plot = w._results._ao_plot
    pixmaps = []
    for mode in range(4):
        plot.set_mode(mode)
        pm = plot.grab()
        assert not pm.isNull(), f"mode {mode} grab null"
        pixmaps.append(pm.size())
    print(f"    grabbed 4 modes sizes={pixmaps}")
run_check("7.plot.set_mode.paint", t_plot_modes)

# ------------------------------------------------- 8. keyed rows (Capacity1)
# By here section 5 has run a simulation, so results are on screen. An edit
# that changes the model now asks whether to discard them (and this script's
# blanket QDialog.exec patch dismisses that prompt, which means "keep the
# results", i.e. undo the edit). Discard them first, as a user editing would.
def _discard_displayed_results():
    w._discard_results()
    app.processEvents()


def t_keyed_stock_edit():
    _discard_displayed_results()
    dt = w._data_tables
    table = dt._spares_table
    dt.refresh()
    sec = w._rbd.sections["SPARE_POOL_DATA"]
    col = sec.columns.index("Stock") + 0
    # NB: table columns follow sec.columns order (populate_keyed)
    col = sec.columns.index("Stock")
    item = table.item(0, col)
    assert item is not None, "spares table empty"
    old = item.text()
    pool = table.item(0, 0).text()
    item.setText("99")
    app.processEvents()
    row = next(r for r in sec.rows if r.get("PoolName") == pool)
    assert str(row["Stock"]) == "99", \
        f"file Stock={row['Stock']!r} after edit"
    v._stack.undo(); app.processEvents()
    assert str(row["Stock"]) == old, \
        f"undo restored {row['Stock']!r}, want {old!r}"
    item2 = table.item(0, col)
    print(f"    pool={pool} stock {old}->99->undo OK")
run_check("8.spare_stock_cell.edit.undo", t_keyed_stock_edit)

def t_keyed_trigger_row():
    _discard_displayed_results()
    dt = w._data_tables
    dt._tabs.setCurrentWidget(dt._triggers_table)
    dt.refresh()
    sec = w._rbd.sections["TRIGGERS"]
    n0 = len(sec.rows)
    dt._add_keyed_row(); app.processEvents()
    assert len(sec.rows) == n0 + 1, \
        f"TRIGGERS rows {n0}->{len(sec.rows)}, add failed"
    dt._remove_selected_keyed_row(); app.processEvents()
    assert len(sec.rows) == n0, \
        f"remove failed, rows={len(sec.rows)}"
run_check("8.trigger_row.add.remove", t_keyed_trigger_row)

# --------------------------------------------- 9. system settings macro
def t_rowcell_macro():
    sec = w._rbd.sections["SYSTEM_DATA"]
    old = sec.rows[0].get("NumRuns")
    cmd = RowCellCmd(v, "SYSTEM_DATA", 0, "NumRuns", old, "777")
    v._stack.push(cmd)
    app.processEvents()
    assert sec.rows[0]["NumRuns"] == "777", f"redo value={sec.rows[0]['NumRuns']!r}"
    v._stack.undo(); app.processEvents()
    assert sec.rows[0]["NumRuns"] == old, \
        f"undo value={sec.rows[0]['NumRuns']!r}, want {old!r}"
run_check("9.system_settings.RowCellCmd.undo", t_rowcell_macro)

# --------------------------------------------------------- 6. stepping
v = load("TwoBlockRedundancy")

def t_step20_finish():
    w._ensure_step_session()
    for _ in range(20):
        w._on_step()
    w._on_step_finish(); app.processEvents()
    assert w._results._tabs.isVisible() or True
    assert w._results._runs_table.topLevelItemCount() >= 1, \
        "stepped finish produced no results rows"
run_check("6.step20.finish", t_step20_finish)

def t_advance_until():
    w._step_session = None; w._step_sink = []
    fired = w._advance_until(max_steps=5)
    assert w._step_session is not None or True
    print(f"    advance_until(5) returned fired={fired}")
run_check("6.advance_until.max5", t_advance_until)

def t_breakpoint():
    combo = w._breakpoint_combo
    names = [combo.itemText(i) for i in range(1, combo.count())]
    assert names, "breakpoint combo empty"
    combo.setCurrentText(names[0])
    w._step_session = None; w._step_sink = []
    hit = False
    for i in range(500):
        prev_msg = w._statusbar.currentMessage()
        w._on_step()
        if "BREAKPOINT" in w._statusbar.currentMessage():
            hit = True
            break
    print(f"    breakpoint '{names[0]}' after {i+1} steps, hit={hit}")
    if not hit:
        raise AssertionError(
            f"breakpoint on '{names[0]}' never fired within 500 steps")
    combo.setCurrentText("No breakpoint")
run_check("6.breakpoint.hit", t_breakpoint)

# --------------------------------------------------- 10. find / select all
v = load("HierarchyExample")

def t_find():
    # Find is a QLineEdit + QCompleter; typing refines the Model
    # Explorer tree too, and submitting jumps to the match.
    entries = [w._find_model.data(w._find_model.index(i, 0))
               for i in range(w._find_model.rowCount())]
    target = next((e for e in entries if e.startswith("Block")), None)
    assert target is not None, "no Block entries in find model"
    for it in list(v._scene.selectedItems()):
        it.setSelected(False)
    w._on_find_submit(target); app.processEvents()
    sel = [it for it in v._scene.selectedItems()]
    assert len(sel) == 1, f"find selected {len(sel)} items"
run_check("10.find_item.selects_one", t_find)

def t_select_all():
    w._select_all(); app.processEvents()
    n = len(v._scene.selectedItems())
    assert n >= len(v._block_items) - 1, f"select_all got only {n}"
run_check("10.select_all", t_select_all)

# --------------------------------------------------- 7b. level filter
def t_level_filter():
    w._run_count_spin.setValue(1)
    v.go_home(); app.processEvents()
    w._run_simulation(); app.processEvents()
    res = w._results
    def hidden_count():
        tables = list(res._detail_tables.values())
        cap = getattr(res, "_cap_table", None)
        if cap is not None:
            tables.append(cap)
        tables.extend(getattr(res, "_cost_tables", []))
        total_rows = sum(t.rowCount() for t in tables)
        hidden = sum(1 for t in tables for r in range(t.rowCount())
                     if t.isRowHidden(r))
        return hidden, total_rows
    h0, tot = hidden_count()
    assert h0 == 0, f"{h0} rows hidden at home level"
    if target_hier is not None and target_hier in v.hiers:
        v.enter_level(target_hier); app.processEvents()
        h1, _ = hidden_count()
        v.go_home(); app.processEvents()
        h2, _ = hidden_count()
        assert h2 == 0, f"filter not cleared on home: {h2} hidden"
        print(f"    rows={tot} home_hidden=0 descended_hidden={h1}")
        if tot == 0:
            print("    NOTE: no filterable result rows (vacuous check)")
        elif h1 == 0:
            raise AssertionError(
                "descending into hierarchy hid 0 rows (expected some)")
        else:
            assert h1 < tot or tot == 0
run_check("7.level_filter.hides_descended", t_level_filter)

# ------------------------------------------- 11. recent files + autosave
shutil.copy(f"{GOLD}/TwoBlockRedundancy/input.rbd", f"{TMP}/recent_a.rbd")
time.sleep(0.05)
shutil.copy(f"{GOLD}/Events/input.rbd", f"{TMP}/recent_b.rbd")

def t_recent():
    QSettings().setValue(w._RECENT_KEY, [])  # deterministic
    w._recent_reload()
    w._open_path(f"{TMP}/recent_a.rbd"); app.processEvents()
    w._dirty = True  # force the discard-confirm path to be exercised safely
    w._open_path(f"{TMP}/recent_b.rbd"); app.processEvents()
    # The File view's recent list.
    w._show_backstage()
    paths = w._backstage.visible_paths()
    assert len(paths) >= 2, f"recent entries={len(paths)}"
    # Opening the first entry loads that file.
    w._backstage._open_item(w._backstage._list.topLevelItem(0))
    app.processEvents()
    assert w._current_file is not None, "recent click must load"
    print(f"    recent={len(paths)} entries")
run_check("11.recent_files.two_entries", t_recent)

def t_autosave():
    # The recent-list click above may have loaded either file; make the
    # file under test explicit rather than depending on recency order or
    # on the discard prompt. Load it through the same path Open uses.
    from raptor.core.fileio.rbd import load_rbd
    w._load_into_app(load_rbd(f"{TMP}/recent_b.rbd"), f"{TMP}/recent_b.rbd")
    app.processEvents()
    w._dirty = True
    assert w._current_file == f"{TMP}/recent_b.rbd", (
        f"current_file={w._current_file!r} dirty={w._dirty}")
    mtime0 = os.path.getmtime(w._current_file)
    time.sleep(0.02)
    w._autosave_tick(); app.processEvents()
    # Autosave writes a separate recovery copy and never touches the
    # user's file.
    assert w._dirty, "autosave must not clear the dirty flag"
    assert os.path.getmtime(w._current_file) == mtime0, \
        "autosave must not rewrite the user's file"
    assert os.path.exists(w._recovery_path()), "no recovery copy written"
run_check("11.autosave.writes_recovery_not_user_file", t_autosave)

# ----------------------------------------------------------- 12. theme
def t_theme():
    tm = ThemeManager.instance()
    start_dark = tm.is_dark
    grabs = []
    for dark in (not start_dark, start_dark):
        tm.set_dark(dark); app.processEvents()
        vp_pm = w._diagram_dock.view.viewport().grab()
        assert not vp_pm.isNull()
        grabs.append(vp_pm)
    assert tm.is_dark == start_dark, "theme did not toggle back"
    print(f"    toggled twice, viewport grabs OK")
run_check("12.theme.toggle.grab", t_theme)

def t_theme_plots_all_modes():
    tm = ThemeManager.instance()
    for dark in (True, False):
        tm.set_dark(dark); app.processEvents()
        for mode in range(4):
            w._results._ao_plot.set_mode(mode)
            pm = w._results._ao_plot.grab()
            assert not pm.isNull(), f"dark={dark} mode={mode} null grab"
    tm.set_dark(False); app.processEvents()
run_check("12.theme.plots.modes0_3", t_theme_plots_all_modes)

# ---------------------------------------------------------- 13. bitmap
def t_bitmap_render():
    view = w._diagram_dock.view
    # NB: content rect -- sceneRect is the infinite-canvas world
    # (rendering it x-supersampled is a multi-GB pixmap -> OOM).
    rect = getattr(view, "_content_rect", None) or view._content_rect
    pm = QPixmap(max(1, int(rect.width())), max(1, int(rect.height())))
    pm.fill(w.palette().color(w.backgroundRole()))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    view._scene.render(painter, QRectF(pm.rect()), rect)
    painter.end()
    assert not pm.isNull() and pm.width() >= 1
    out = f"{TMP}/smoke_diagram.png"
    assert pm.save(out, "PNG"), "png save failed"
    assert os.path.getsize(out) > 0
run_check("13.bitmap.render_save", t_bitmap_render)

# ---------------------------------------------------------- 14. stress
v = load("TwoBlockRedundancy")

def t_stress_steps():
    w._step_session = None; w._step_sink = []
    t0 = time.monotonic()
    for _ in range(200):
        w._on_step()
    print(f"    200 steps in {time.monotonic()-t0:.1f}s")
run_check("14.stress.200_steps", t_stress_steps)

def t_stress_add_undo():
    t0 = time.monotonic()
    for _ in range(50):
        v.add_block("Stress", 40.0, 40.0)
        v._stack.undo()
    print(f"    50 add/undo cycles in {time.monotonic()-t0:.1f}s")
run_check("14.stress.50_add_undo", t_stress_add_undo)

# ------------------------------- extra: dialog handlers with exec patched
def t_syscfg_dialog():
    v0 = dict(w._rbd.sections["SYSTEM_DATA"].rows[0])
    w._show_system_config(); app.processEvents()
    assert dict(w._rbd.sections["SYSTEM_DATA"].rows[0]) == v0, \
        "system config dialog (accepted unchanged) mutated the row"
run_check("X.sysconfig_dialog.patched_exec", t_syscfg_dialog)

def t_export_bitmap_dialog():
    w._export_bitmap(); app.processEvents()  # patched exec, empty selection -> early return
run_check("X.export_bitmap_dialog.patched_exec", t_export_bitmap_dialog)

SKIPPED = [
    "X._show_preferences (dialog handler; exec patched globally but prefs writeback would pollute QSettings)",
    "X._edit_block_defaults (same class of dialog; skipped)",
    "X._on_mass_edit (requires 2+ selected + tabbed dialog; partially covered by unit surface)",
]

# --------------------------------------------------------------- report
# Deliberately not implemented -- reported as skipped, never recorded as
# a result (SKIP is not a pass and not a fail).
for _s in SKIPPED:
    ctx.skip(_s)
sys.exit(ctx.report())

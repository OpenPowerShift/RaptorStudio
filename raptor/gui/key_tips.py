"""Alt key tips for the ribbon, as data.

Two levels, as in Office: Alt shows a letter on the File button and on each
tab; that letter opens the tab and shows a letter on each of its buttons;
the second letter runs the command (Alt, R, S = Results > Show Results).
Letters are fixed here, never derived from a label, so renaming a button
cannot reassign a key somebody already learned. They are unique among the
tabs, and among the buttons of one tab (`tests/unit/test_key_tips.py`
checks both, and that every ribbon button has one).

Custom controls (combo boxes, spin boxes, the Align menu...) are reached
with Tab or the mouse and carry no tip.
"""
from __future__ import annotations

#: Ribbon tab title -> first letter. File is the File button left of the
#: tabs; it is F, not the E it used to be (F was Finish's).
FILE_KEY_TIP = "F"
TAB_KEY_TIPS: dict[str, str] = {
    "Model": "M", "Arrange": "A", "Data": "D", "Simulate": "S",
    "Debug": "B", "Results": "R", "View": "V", "Help": "H",
}

#: Tab title -> {command id: letter}, for the buttons on that tab.
KEY_TIPS: dict[str, dict[str, str]] = {
    "Model": {
        "home.paste": "V", "home.cut": "X", "home.copy": "C",
        "home.select_all": "K", "model.select": "S", "model.connect": "N",
        "model.add_block": "B", "model.add_node": "O",
        "model.add_event": "E", "model.add_hier": "H",
        "model.collapse": "L", "model.delete": "D", "home.syscfg": "P",
        "home.validate": "I", "arrange.anno_text": "T",
        "arrange.anno_rect": "R", "arrange.anno_ellipse": "Y",
        "arrange.anno_arrow": "W", "model.import_rbd": "M",
        "sim.extract_hierarchy": "U", "model.hier_up": "J",
        "model.hier_home": "F",
    },
    "Arrange": {
        "model.auto_layout": "L", "view.snap": "G", "arrange.order_front": "F",
        "arrange.order_forward": "O", "arrange.order_backward": "K",
        "arrange.order_back": "B", "arrange.flip_h": "H",
        "arrange.flip_v": "V",
    },
    "Data": {
        "data.phases": "P", "data.block_defaults": "D", "data.mass_edit": "M",
        "data.export_library": "E", "data.import_library": "I",
    },
    "Simulate": {
        "sim.run": "R", "sim.live_sim": "A", "sim.run_settings": "S",
        "sim.pause": "P", "sim.stop_run": "T", "sim.jump": "J",
        "sim.sweep": "W", "sim.compare": "C",
    },
    "Debug": {
        "sim.step": "S", "sim.step_next_time": "N", "sim.step_finish": "F",
        "sim.restart_step": "R", "sim.terminate": "T",
        "view.focus_event": "E", "analyse.fev_view": "V",
        "analyse.fev_fail": "A", "analyse.fev_repair": "P",
        "analyse.fev_status": "U", "analyse.fev_erase_phase": "H",
        "analyse.fev_erase_all": "L", "analyse.fev_phase_prev": "B",
        "analyse.fev_phase_next": "X", "analyse.fev_goto_phase": "G",
    },
    "Results": {
        "results.results_show": "S", "results.results_float": "W",
        "results.clear_results": "C", "results.spof": "P",
        "results.domains": "D", "results.analytical": "A",
        "results.weak_color": "K", "results.capacity_overlay": "U",
        "results.cost_overlay": "O", "results.spares_overlay": "R",
        "results.importance_overlay": "I", "results.weak_spread": "T",
    },
    "View": {
        "arrange.fit": "F", "arrange.zoom_selection": "S",
        "arrange.zoom_in": "I", "arrange.zoom_out": "O",
        "arrange.zoom_percent": "Z", "arrange.zoom_hierarchy": "H",
        "view.explorer_toggle": "E", "view.inspector_toggle": "N",
        "view.fev_inspector_toggle": "V", "view.results_toggle": "R",
        "view.run_toggle": "U", "view.data_tables": "D",
        "view.live_log_toggle": "G",
        "view.reset_layout": "L", "view.theme": "T", "view.prefs": "P",
    },
    "Help": {
        "help.guide": "G", "help.manual": "M", "help.formulas": "O",
        "help.mcp": "A", "help.shortcuts": "K", "help.about": "B",
        "help.report_issue": "R",
    },
}

"""The ribbon's commands and layout, as data.

`COMMANDS` says what every plain command is (text, tooltip, shortcut,
handler, icon, when it is available). `LAYOUT` says which page, group and
position it sits in, and names the builder for each composite control that
is more than a button (`ribbon_builders.py`). `commands.py` turns both into
the live ribbon.

Adding a command is a `CommandSpec` here plus a `Btn` in `LAYOUT`; moving
one is editing `LAYOUT`. Nothing here imports Qt widgets or `MainWindow`, so
`test_command_table.py` can check the whole table without building a window.

Availability: `enabled_when` names a rule in `commands.PREDICATES` --
"model_open", "has_file", "can_simulate" (valid model, nothing running),
"run_active", "background_run". `CommandRegistry.refresh` applies them all
from one `AppState`, which `MainWindow._command_state` derives from live
objects, so it is safe to call at any time. That replaced ~30 scattered
`setEnabled` calls (each run start and end, a timer, the load path) that
disagreed with each other -- and ~50 lines in `_load_into_app` that left 37
commands enabled after File > Close. Commands that follow something else (a
selection, the analysis lock) still carry `enabled=False/True` and are
driven by their own code.
"""
from __future__ import annotations

from raptor.gui.commands import Btn, CommandSpec, Custom, Group, Page

COMMANDS: tuple[CommandSpec, ...] = (
    # ---- File --------------------------------------------------------
    CommandSpec(
        id="file.open",
        key_tip="O",
        attr="_open_action",
        text="Open",
        icon="folder_open_icon:text",
        tooltip="Open an .rbd or .rbds file (Ctrl+O)",
        handler="_open_file",
        shortcut="Ctrl+O",
        enabled_when="not_running"),
    CommandSpec(
        id="file.save",
        key_tip="V",
        attr="_save_action",
        text="Save",
        icon="save_icon:text",
        tooltip="Save (Ctrl+S)",
        handler="_save_file",
        shortcut="Ctrl+S",
        # Any open model: a never-saved one asks where (Save As). It used
        # to need a file on disk, so Ctrl+S on a new model or an example
        # silently did nothing.
        enabled_when="model_open"),
    CommandSpec(
        id="file.save_as",
        attr="_save_as_action",
        text="Save As",
        icon="save_as_icon:text",
        tooltip="Save to a new .rbds or .rbd file (Ctrl+Shift+S)",
        handler="_save_file_as",
        shortcut="Ctrl+Shift+S",
        enabled_when="model_open"),
    CommandSpec(
        id="file.new",
        key_tip="W",
        attr="_new_action",
        text="New",
        icon="new_file_icon:text",
        tooltip="Create a new blank model (Ctrl+N)",
        handler="_new_file",
        shortcut="Ctrl+N",
        enabled_when="not_running"),
    CommandSpec(
        id="file.close_file",
        attr="_close_file_action",
        text="Close",
        icon="close_file_icon:text",
        tooltip="Close the current model (with save prompt)",
        handler="_close_current_file",
        enabled_when="can_close"),
    CommandSpec(
        id="file.quit",
        attr="_quit_action",
        text="Quit",
        icon="close_x_icon:text",
        tooltip="Quit RAPTOR Studio (Ctrl+Q)",
        handler="_quit_all",
        shortcut="Ctrl+Q"),
    CommandSpec(
        id="file.print",
        attr="_print_action",
        text="Print…",
        icon="printer_icon:text",
        tooltip=(
            "Print diagrams, simulation results and the Weak Link analysis with a"
            " project title block (Ctrl+P)"),
        handler="_open_print_dialog",
        shortcut="Ctrl+P",
        enabled_when="model_open"),
    CommandSpec(
        id="file.export_pdf",
        attr="_export_pdf_action",
        text="Export PDF…",
        icon="pdf_icon:text",
        tooltip=(
            "Save the printable report (diagram + results + weak link) as a PDF "
            "file"),
        handler="_export_pdf",
        enabled_when="model_open"),
    CommandSpec(
        id="file.export_tables",
        attr="_export_tables_action",
        text="Export Tables\u2026",
        icon="export_tables_icon:text",
        tooltip=(
            "Write the model's tables (blocks, nodes, links, ... ) and a "
            "run's result tables as CSV, text or one spreadsheet with a tab "
            "per table"),
        handler="_export_tables",
        enabled_when="model_open"),
    CommandSpec(
        id="file.export_svg",
        attr="_export_svg_action",
        text="Export Diagram SVG…",
        icon="svg_icon:text",
        tooltip=(
            "Save the currently displayed hierarchy level as a scalable vector "
            "(.svg) file, for editing or embedding elsewhere at any resolution"),
        handler="_export_diagram_svg",
        enabled_when="model_open"),
    CommandSpec(
        id="file.export_png",
        attr="_export_png_action",
        text="Save Bitmap",
        icon="image_icon:text",
        tooltip="Save the diagram view as a PNG image",
        handler="_export_bitmap",
        enabled_when="model_open"),
    CommandSpec(
        id="file.copy_view",
        key_tip="Y",
        attr="_copy_view_action",
        text="Copy View",
        icon="copy_view_icon:text",
        tooltip="Copy the current view to the clipboard as a high-resolution PNG",
        handler="_copy_view_to_clipboard",
        enabled_when="model_open"),
    # ---- Home --------------------------------------------------------
    CommandSpec(
        id="home.select_all",
        attr="_select_all_action",
        text="Select All",
        icon="select_all_icon:accent",
        tooltip="Select all blocks/nodes/events/hierarchies (Ctrl+A)",
        handler="_select_all",
        shortcut="Ctrl+A",
        enabled_when="model_open"),
    CommandSpec(
        id="home.copy",
        attr="_copy_action",
        text="Copy",
        icon="copy_icon:text",
        tooltip="Copy the selected elements",
        handler="_diagram_dock.view.copy_selection",
        enabled_when="model_open"),
    CommandSpec(
        id="home.cut",
        attr="_cut_action",
        text="Cut",
        icon="cut_icon:text",
        tooltip="Copy and delete the selected elements",
        handler="_diagram_dock.view.cut_selection",
        enabled_when="model_open"),
    CommandSpec(
        id="home.paste",
        attr="_paste_action",
        text="Paste",
        icon="paste_icon:text",
        tooltip="Paste previously copied elements with fresh ids",
        handler="_diagram_dock.view.paste",
        enabled_when="model_open"),
    CommandSpec(
        id="home.syscfg",
        key_tip="C",
        attr="_syscfg_action",
        text="Project Settings",
        icon="model_info_icon:text",
        tooltip="Project Settings — simulation-wide configuration for this model",
        handler="_open_project_settings",
        enabled_when="model_open"),
    CommandSpec(
        id="home.validate",
        key_tip="L",
        attr="_validate_action",
        text="Issues",
        icon="check_badge_icon:go",
        tooltip=(
            "What stops the model running, and warnings that do not: a "
            "tick when it can run, a red cross when it cannot (F7)"),
        handler="_validate_model",
        kwargs=(("quiet", False),),
        shortcut="F7",
        enabled_when="model_open"),
    # ---- Model -------------------------------------------------------
    CommandSpec(
        id="model.import_rbd",
        attr="_import_rbd_action",
        text="Import RBD",
        icon="import_rbd_icon:text",
        tooltip=(
            "Merge an existing model file in as one hierarchy: its Start/End "
            "markers become the new level's In/Out"),
        handler="_import_rbd",
        enabled_when="model_open"),
    CommandSpec(
        id="model.select",
        attr="_select_action",
        text="Select",
        icon="mouse_arrow_icon:accent",
        tooltip="Select tool (Q)",
        shortcut="Q",
        checkable=True,
        checked=True,),
    CommandSpec(
        id="model.auto_layout",
        attr="_auto_layout_action",
        text="Auto Layout",
        icon="autolayout_icon:accent",
        tooltip=(
            "Rearrange by graph flow (columns left-to-right). Selections are laid"
            " out alone. (Ctrl+L)"),
        handler="_diagram_dock.view.auto_layout",
        shortcut="Ctrl+L",
        enabled_when="model_open"),
    CommandSpec(
        id="model.hier_up",
        attr="_hier_up_action",
        text="Up",
        icon="up_filled_icon:accent",
        tooltip="Ascend one hierarchy level (or double-click an Out marker)",
        handler="_diagram_dock.view.go_up",
        enabled=False),
    CommandSpec(
        id="model.hier_home",
        attr="_hier_home_action",
        text="Home",
        icon="home_icon:accent",
        tooltip="Return to the home level",
        handler="_diagram_dock.view.go_home",
        enabled=False),
    CommandSpec(
        id="model.collapse",
        attr="_collapse_action",
        text="Collapse Selection",
        icon="collapse_icon:accent",
        tooltip=(
            "Move the selected elements into a NEW hierarchy; boundary links are "
            "rewired to the hierarchy oval"),
        handler="_on_collapse_selection",
        enabled_when="has_structural_selection"),
    CommandSpec(
        id="model.delete",
        attr="_delete_tool",
        text="Delete Selected",
        icon="delete_icon:text",
        tooltip="Delete selected blocks/nodes (Del)",
        handler="_diagram_dock.view.delete_selected",
        shortcut="Del",
        enabled_when="has_selection"),
    CommandSpec(
        id="model.add_block",
        attr="_add_block_tool",
        text="Add Block",
        icon="block_icon:accent",
        tooltip="Click the canvas to place a new block",
        handler="_set_tool",
        args=("add_block",),
        on="toggled",
        checkable=True,
        enabled_when="model_open"),
    CommandSpec(
        id="model.add_node",
        attr="_add_node_tool",
        text="Add Node",
        icon="node_icon:accent",
        tooltip="Click the canvas to place a new Regular node",
        handler="_set_tool",
        args=("add_node",),
        on="toggled",
        checkable=True,
        enabled_when="model_open"),
    CommandSpec(
        id="model.add_event",
        attr="_add_event_tool",
        text="Add Event",
        icon="event_icon:accent",
        tooltip=(
            "Click the canvas to place a new Bernoulli event (P=0.5 — edit it in "
            "the Inspector or Data Tables)"),
        handler="_set_tool",
        args=("add_event",),
        on="toggled",
        checkable=True,
        enabled_when="model_open"),
    CommandSpec(
        id="model.add_hier",
        attr="_add_hier_tool",
        text="Add Hierarchy",
        icon="hierarchy_icon:accent",
        tooltip="Click the canvas to place a new hierarchy (with its In/Out markers)",
        handler="_set_tool",
        args=("add_hierarchy",),
        on="toggled",
        checkable=True,
        enabled_when="model_open"),
    CommandSpec(
        id="model.connect",
        attr="_connect_tool",
        text="Connect",
        icon="connect_icon:accent",
        tooltip="Click the source element, then the target",
        handler="_set_tool",
        args=("connect",),
        on="toggled",
        checkable=True,
        enabled_when="model_open"),
    # ---- Arrange -----------------------------------------------------
    CommandSpec(
        id="arrange.fit",
        attr="_fit_action",
        text="Fit",
        icon="fit_frame_icon:accent",
        tooltip="Fit all: fit the whole diagram to the window (Ctrl+0)",
        handler="_fit_all",
        shortcut="Ctrl+0",
        enabled_when="model_open"),
    CommandSpec(
        id="arrange.zoom_percent",
        attr="_zoom_percent_action",
        text="Zoom %",
        icon="percent_zoom_icon:accent",
        tooltip="Zoom percent: set an exact zoom percentage",
        handler="_show_zoom_percent",
        enabled_when="model_open"),
    CommandSpec(
        id="arrange.zoom_in",
        attr="_zoom_in_action",
        text="Zoom In",
        icon="zoom_in_icon:accent",
        tooltip="Zoom into the diagram (Ctrl+= or Ctrl+wheel)",
        handler="_diagram_dock.view.zoom_by",
        args=(1.25,),
        shortcut="Ctrl+=",),
    CommandSpec(
        id="arrange.zoom_out",
        attr="_zoom_out_action",
        text="Zoom Out",
        icon="zoom_out_icon:accent",
        tooltip="Zoom out of the diagram (Ctrl+- or Ctrl+wheel)",
        handler="_diagram_dock.view.zoom_by",
        args=(0.8,),
        shortcut="Ctrl+-",),
    CommandSpec(
        id="arrange.zoom_selection",
        attr="_zoom_selection_action",
        text="Selection",
        icon="zoom_selection_icon:accent",
        tooltip=("Zoom to selection: fit the selected elements in the window"
                 " (Ctrl+Shift+0)"),
        handler="_zoom_to_selection",
        shortcut="Ctrl+Shift+0",
        enabled_when="has_movable_selection"),
    CommandSpec(
        id="arrange.zoom_hierarchy",
        attr="_zoom_hierarchy_action",
        text="Hierarchy",
        icon="zoom_hierarchy_icon:accent",
        tooltip=("Zoom to hierarchy: enter the selected hierarchy oval (like"
                 " double-clicking it)"),
        handler="_zoom_to_hierarchy",
        enabled_when="can_zoom_hierarchy"),
    CommandSpec(
        id="arrange.flip_h",
        attr="_flip_h_action",
        text="Flip Horizontal",
        icon="flip_h_icon:accent",
        tooltip=(
            "Mirror the selected items left-to-right about the middle of their"
            " span (select 2+). Shapes keep their size; only positions"
            " reflect."),
        handler="_diagram_dock.view.flip_selected",
        args=("h",),
        enabled_when="can_align"),
    CommandSpec(
        id="arrange.flip_v",
        attr="_flip_v_action",
        text="Flip Vertical",
        icon="flip_v_icon:accent",
        tooltip=(
            "Mirror the selected items top-to-bottom about the middle of their"
            " span (select 2+). Shapes keep their size; only positions"
            " reflect."),
        handler="_diagram_dock.view.flip_selected",
        args=("v",),
        enabled_when="can_align"),
    CommandSpec(
        id="arrange.anno_text",
        attr="_anno_text_tool",
        text="Text",
        icon="text_annotation_icon:accent",
        tooltip="Click to place a text annotation (double-click to edit)",
        handler="_set_tool",
        args=("anno_text",),
        on="toggled",
        checkable=True,
        enabled_when="model_open"),
    CommandSpec(
        id="arrange.anno_rect",
        attr="_anno_rect_tool",
        text="Rectangle",
        icon="rect_annotation_icon:accent",
        tooltip="Drag a rectangle annotation",
        handler="_set_tool",
        args=("anno_rect",),
        on="toggled",
        checkable=True,
        enabled_when="model_open"),
    CommandSpec(
        id="arrange.anno_ellipse",
        attr="_anno_ellipse_tool",
        text="Ellipse",
        icon="ellipse_annotation_icon:accent",
        tooltip="Drag an ellipse annotation",
        handler="_set_tool",
        args=("anno_ellipse",),
        on="toggled",
        checkable=True,
        enabled_when="model_open"),
    CommandSpec(
        id="arrange.anno_arrow",
        attr="_anno_arrow_tool",
        text="Arrow",
        icon="arrow_annotation_icon:accent",
        tooltip="Click to place the tail, click again to place the head",
        handler="_set_tool",
        args=("anno_arrow",),
        on="toggled",
        checkable=True,
        enabled_when="model_open"),
    CommandSpec(
        id="arrange.order_front",
        attr="_anno_order_front_action",
        text="Bring to Front",
        icon="bring_to_front_icon:accent",
        tooltip="Place the selected annotations in front of every diagram element (Ctrl+Shift+])",
        handler="_on_annotation_order",
        args=("front",),
        shortcut="Ctrl+Shift+]",
        # Follows the annotation selection (`_on_selection_changed`).
        enabled=False),
    CommandSpec(
        id="arrange.order_forward",
        attr="_anno_order_forward_action",
        text="Bring Forward",
        icon="bring_forward_icon:accent",
        tooltip="Move the selected annotations one step up the stack (Ctrl+])",
        handler="_on_annotation_order",
        args=("forward",),
        shortcut="Ctrl+]",
        # Follows the annotation selection (`_on_selection_changed`).
        enabled=False),
    CommandSpec(
        id="arrange.order_backward",
        attr="_anno_order_backward_action",
        text="Send Backward",
        icon="send_backward_icon:accent",
        tooltip="Move the selected annotations one step down the stack (Ctrl+[)",
        handler="_on_annotation_order",
        args=("backward",),
        shortcut="Ctrl+[",
        # Follows the annotation selection (`_on_selection_changed`).
        enabled=False),
    CommandSpec(
        id="arrange.order_back",
        attr="_anno_order_back_action",
        text="Send to Back",
        icon="send_to_back_icon:accent",
        tooltip="Place the selected annotations behind every diagram element (links and hierarchy ovals included) (Ctrl+Shift+[)",
        handler="_on_annotation_order",
        args=("back",),
        shortcut="Ctrl+Shift+[",
        # Follows the annotation selection (`_on_selection_changed`).
        enabled=False),
    # ---- Data --------------------------------------------------------
    CommandSpec(
        id="data.export_library",
        attr="_export_library_action",
        text="Export Library",
        icon="export_lib_icon:text",
        tooltip="Save every block of this model as reusable templates",
        handler="_export_block_library",
        enabled_when="model_open"),
    CommandSpec(
        id="data.import_library",
        attr="_import_library_action",
        text="Import Library",
        icon="import_lib_icon:text",
        tooltip=(
            "Add a block template from a library file, or update same-named "
            "blocks' failure/repair data"),
        handler="_import_block_library",
        enabled_when="model_open"),
    CommandSpec(
        id="data.block_defaults",
        attr="_block_defaults_action",
        text="Block Defaults…",
        icon="form_defaults_icon:text",
        tooltip="Set the default failure/repair distribution for NEW blocks",
        handler="_edit_block_defaults",
        enabled_when="model_open"),
    CommandSpec(
        id="data.mass_edit",
        attr="_mass_edit_action",
        text="Mass Edit…",
        icon="pencil_multi_icon:text",
        tooltip=(
            "Apply a failure/repair distribution to all selected elements at once"
            " (select 2+ elements)"),
        handler="_on_mass_edit",
        enabled_when="can_mass_edit"),
    CommandSpec(
        id="data.phases",
        attr="_phases_action",
        text="Phases…",
        icon="phase_icon:text",
        tooltip=(
            "Phases — mission phase durations and per-element Active/Link/Cut "
            "assignment"),
        handler="_open_phasing_dialog",
        enabled_when="model_open"),
    # ---- Simulate ----------------------------------------------------
    CommandSpec(
        id="sim.run",
        key_tip="R",
        attr="_run_action",
        text="Run",
        icon="play_icon:go",
        tooltip=(
            "Run the simulation (F5): at full speed on the cores shown "
            "beside it, or animated on one core when Animate is on"),
        handler="_on_run",
        shortcut="F5",
        enabled_when="can_start_batch"),
    CommandSpec(
        id="sim.step",
        key_tip="S",
        attr="_step_action",
        text="Step",
        icon="step_icon:accent",
        tooltip=(
            "Advance the simulation ONE event (F6) — watch states change event-"
            "by-event"),
        handler="_on_step",
        shortcut="F6",
        enabled_when="can_simulate"),
    CommandSpec(
        id="sim.step_next_time",
        key_tip="N",
        attr="_step_next_time_action",
        text="Next Time",
        icon="next_time_icon:accent",
        tooltip=(
            "Advance to the next DISTINCT simulation time (Shift+F6) — steps "
            "through every event at the current time in one click"),
        handler="_on_step_next_time",
        shortcut="Shift+F6",
        enabled_when="can_simulate"),
    CommandSpec(
        id="sim.step_finish",
        key_tip="F",
        attr="_step_finish_action",
        text="Finish",
        icon="finish_flag_icon:go",
        tooltip="Run the stepped simulation to completion and produce reports",
        handler="_on_step_finish",
        enabled_when="stepping"),
    CommandSpec(
        id="sim.run_to",
        attr="_run_to_action",
        text="Run to time…",
        icon="run_to_time_icon:accent",
        tooltip=(
            "Advance until the sim time set in the Run panel is reached (or the "
            "run ends)"),
        handler="_on_run_to",
        enabled_when="can_start_batch"),
    CommandSpec(
        id="sim.terminate",
        attr="_terminate_action",
        text="Terminate",
        icon="terminate_icon:stop",
        tooltip=(
            "Early Terminate: finalize the stepped run at the "
            "CURRENT sim time — partial results are kept"),
        handler="_on_step_terminate",
        enabled_when="stepping"),
    CommandSpec(
        id="sim.restart_step",
        key_tip="X",
        attr="_restart_step_action",
        text="Restart",
        icon="restart_icon:text",
        tooltip=(
            "Restart (B-11): discard this stepped run and begin a fresh one — all"
            " state reset to kickoff values"),
        handler="_on_step_restart",
        enabled_when="stepping"),
    CommandSpec(
        id="sim.run_settings",
        attr="_run_settings_action",
        text="Run Settings…",
        icon="sliders_icon:text",
        tooltip=(
            "Open the Run panel — the single editor for trials, "
            "termination, analyses and recording"),
        handler="_show_run_panel",
        enabled_when="run_settings_available"),
    CommandSpec(
        id="sim.sweep",
        attr="_sweep_action",
        text="Sweep…",
        icon="sweep_icon:accent",
        tooltip=(
            "Run the model once per value of one number -- a failure or "
            "repair parameter, a rate, the k of a k-of-n node -- and see the "
            "results as a table and a chart. Full speed, on the cores in "
            "the Cores box. Runs on copies: the open model is not changed."),
        handler="_open_sweep_dialog",
        enabled_when="can_start_batch"),
    CommandSpec(
        id="sim.compare",
        attr="_compare_action",
        text="Compare…",
        icon="compare_icon:accent",
        tooltip=(
            "Run several models -- this one and others from disk -- with the "
            "same number of trials and compare their results, best in bold. "
            "Full speed, on the cores in the Cores box. Runs on copies: "
            "nothing is changed."),
        handler="_open_compare_dialog",
        enabled_when="can_start_batch"),
    CommandSpec(
        id="sim.extract_hierarchy",
        attr="_extract_hierarchy_action",
        text="Extract Hierarchy…",
        icon="extract_hierarchy_icon:accent",
        tooltip=(
            "Save the selected hierarchy as a standalone model (its input always "
            "available) and open it to run on its own. Shows what crosses the "
            "hierarchy's boundary and how each crossing is treated."),
        handler="_on_extract_hierarchy",
        enabled_when="can_extract_hierarchy"),
    CommandSpec(
        id="sim.pause",
        key_tip="A",
        attr="_pause_action",
        text="Pause",
        icon="_bars:text",
        handler="_toggle_bg_pause",
        enabled_when="background_run"),
    CommandSpec(
        id="sim.stop_run",
        key_tip="P",
        attr="_stop_run_action",
        text="Stop",
        icon="_stop_sq:stop",
        handler="_stop_bg_run",
        enabled_when="run_active"),
    CommandSpec(
        id="sim.jump",
        key_tip="J",
        attr="_jump_action",
        text="Jump",
        icon="_skip_end:text",
        tooltip=("While stepping: let this run finish and stop at the next run's first change. "
                 "In a background batch: abandon the current run and start the next one"),
        handler="_jump_bg_run",
        enabled_when="jump_available"),
    CommandSpec(
        id="sim.live_sim",
        key_tip="M",
        attr="_live_sim_action",
        text="Animate",
        icon="animate_icon:accent",
        tooltip=(
            "Animate: a clean run you can watch -- on one core, the diagram "
            "re-coloured as each event happens and the event log streamed "
            "into the Results panel. Off: full speed on several cores (the "
            "Cores box). Remembered between sessions; not part of the model."),
        handler="_set_animate",
        on="toggled",
        checkable=True),
    CommandSpec(
        id="sim.speed_slower",
        attr="_speed_slower",
        text="Slower",
        icon="slower_icon:text",
        tooltip="Decrease animation speed ×10",
        handler="_adjust_speed",
        args=(1,)),
    CommandSpec(
        id="sim.speed_reset_1",
        attr="_speed_reset_1",
        text="1x",
        icon="one_evs_icon:text",
        tooltip="Reset to 1 event per second (1 ev/s)",
        handler="_set_speed",
        args=(1.0,)),
    CommandSpec(
        id="sim.speed_faster",
        attr="_speed_faster",
        text="Faster",
        icon="faster_icon:text",
        tooltip="Increase animation speed ×10",
        handler="_adjust_speed",
        args=(-1,)),
    CommandSpec(
        id="sim.speed_reset_max",
        attr="_speed_reset_max",
        text="Max",
        icon="max_icon:text",
        tooltip="Run as fast as possible",
        handler="_set_speed",
        args=(None,)),
    # ---- Debug tab: failure-effects (what-if) commands ---------------
    CommandSpec(
        id="analyse.fev_view",
        attr="_fev_view_action",
        text="Failure Effects",
        icon="fev_icon:accent",
        tooltip=(
            "Failure Effects View: fail and repair blocks by clicking them and see "
            "what happens to the system, with nothing simulated. Click again to leave."),
        handler="_on_fev_toggle",
        on="toggled",
        checkable=True,
        enabled_when="can_fev"),
    CommandSpec(
        id="analyse.fev_fail",
        attr="_fev_fail_action",
        text="Fail",
        icon="fail_icon:stop",
        tooltip=(
            "Fail a block (or an event) NOW. Select one first, or click Fail then "
            "click it. Opens Failure Effects View if it is not open."),
        handler="_on_fev_fail",
        enabled_when="can_fev"),
    CommandSpec(
        id="analyse.fev_repair",
        attr="_fev_repair_action",
        text="Repair",
        icon="wrench_icon:go",
        tooltip=(
            "Put a failed block (or event) back to Running NOW. Select one first, or "
            "click Repair then click it."),
        handler="_on_fev_repair",
        enabled_when="can_fev"),
    CommandSpec(
        id="analyse.fev_phase_prev",
        attr="_fev_phase_prev_action",
        text="Prev Phase",
        icon="phase_prev_icon:text",
        tooltip="Failure Effects View: go to the previous phase (wraps to the last).",
        handler="_on_fev_phase_prev",
        enabled_when="fev_phased"),
    CommandSpec(
        id="analyse.fev_phase_next",
        attr="_fev_phase_next_action",
        text="Next Phase",
        icon="phase_next_icon:text",
        tooltip="Failure Effects View: go to the next phase (wraps to the first).",
        handler="_on_fev_phase_next",
        enabled_when="fev_phased"),
    CommandSpec(
        id="analyse.fev_goto_phase",
        attr="_fev_goto_phase_action",
        text="Go To Phase…",
        icon="phase_goto_icon:text",
        tooltip="Failure Effects View: choose a phase from the list and go straight to it.",
        handler="_on_fev_goto_phase",
        enabled_when="fev_phased"),
    CommandSpec(
        id="analyse.fev_erase_phase",
        attr="_fev_erase_phase_action",
        text="Clear Failures",
        icon="eraser_icon:text",
        tooltip=(
            "Small eraser: repair every failed or PM block and every failed event, "
            "and stay in this phase. A block that is Idle stays Idle."),
        handler="_on_fev_erase_phase",
        enabled_when="fev_phased"),
    CommandSpec(
        id="analyse.fev_erase_all",
        attr="_fev_erase_all_action",
        text="Reset All",
        icon="reset_all_icon:text",
        tooltip=(
            "Large eraser: everything operating again, back to phase 1."),
        handler="_on_fev_erase_all",
        enabled_when="fev_active"),
    CommandSpec(
        id="analyse.fev_status",
        attr="_fev_status_action",
        text="Status",
        icon="status_summary_icon:text",
        tooltip=(
            "Display Failure Effects: the failed components, the induced failures, "
            "the down and degraded nodes and the system status"),
        handler="_on_fev_show_status",
        enabled_when="fev_active"),
    # ---- Results -----------------------------------------------------
    CommandSpec(
        id="results.clear_results",
        attr="_clear_results_action",
        text="Clear Results",
        icon="clear_results_icon:text",
        tooltip="Empty every results tab and return to the placeholder",
        handler="_clear_results_clicked",
        enabled_when="has_results"),
    CommandSpec(
        id="results.spof",
        attr="_spof_action",
        text="Single-Point Failures",
        icon="spof_icon:warn",
        tooltip=(
            "List every component that alone can fail the system — series "
            "elements; export as a text file"),
        handler="_show_spof_dialog",
        enabled_when="model_open"),
    CommandSpec(
        id="results.domains",
        attr="_domains_action",
        text="Domains…",
        icon="domains_icon:accent",
        tooltip="Domain of each node — which elements' failures can propagate to it",
        handler="_show_domains_dialog",
        enabled_when="model_open"),
    CommandSpec(
        id="results.weak_color",
        attr="_weak_color_action",
        text="Weak Links",
        icon="traffic_light_icon:text",
        tooltip=(
            "Colour every block, node, event and hierarchy by its "
            "post-simulation availability, dependability or reliability, "
            "against the model's G/Y and Y/R thresholds; the value is "
            "printed under each element (digits in Preferences ▸ Advanced). "
            "Enable Weak Link in the Run panel's Analysis section."),
        handler="_toggle_weak_colors",
        checkable=True,
        enabled=False),
    CommandSpec(
        id="results.capacity_overlay",
        attr="_capacity_overlay_action",
        text="Capacity",
        icon="gauge_icon:accent",
        tooltip=(
            "Colour each capacity node by its average utilization (Green < "
            "70% · Yellow < 90% · Red ≥ 90%). Needs Capacity analysis and at "
            "least one node without unrestricted flow."),
        handler="_on_capacity_overlay_toggled",
        on="toggled",
        checkable=True,
        enabled=False),
    CommandSpec(
        id="results.cost_overlay",
        attr="_cost_overlay_action",
        text="Cost",
        icon="dollar_icon:accent",
        tooltip=(
            "Colour each block by its total life-cycle cost, relative to the "
            "most expensive block (Green < 1/3 · Yellow < 2/3 · Red ≥ 2/3); "
            "Spread bands it by quartile instead. Needs Cost Analysis."),
        handler="_on_cost_overlay_toggled",
        on="toggled",
        checkable=True,
        enabled=False),
    CommandSpec(
        id="results.spares_overlay",
        attr="_spares_overlay_action",
        text="Spares",
        icon="spare_pool_icon:warn",
        tooltip=(
            "Colour each block Red if it ever ran out of spares or reached "
            "end of life during the run (% of run time and sparing cost on "
            "hover), Green otherwise. Needs Weak Link analysis."),
        handler="_on_spares_overlay_toggled",
        on="toggled",
        checkable=True,
        enabled=False),
    CommandSpec(
        id="results.importance_overlay",
        attr="_importance_overlay_action",
        text="Importance",
        icon="rank_icon:accent",
        tooltip=(
            "Colour each block by its importance, using the measure the "
            "Importance tab ranks by (Vesely–Fussell when it is defined, "
            "since Birnbaum saturates in a series system). Red = highest leverage (≥ ⅔ of the largest value), "
            "Yellow ≥ ⅓, Green below; the value is printed under each "
            "block. This is a RANKING, not a health reading: red means "
            "'fix this first'. Greyed out until the run has importance "
            "measures: switch on Run ▸ Analysis ▸ Importance measures and "
            "run again."),
        handler="_on_importance_overlay_toggled",
        on="toggled",
        checkable=True,
        enabled=False),
    CommandSpec(
        id="results.weak_spread",
        attr="_weak_spread_action",
        text="Spread",
        icon="spread_icon:accent",
        tooltip=(
            "Band the ACTIVE colouring by this result set's values: the best "
            "quarter Green, the worst quarter Red, the middle half Yellow. "
            "Applies to Weak Links or Cost; Capacity and Spares use fixed or "
            "flag bands. For Weak Links it sets the two thresholds."),
        handler="_spread_overlay",
        enabled=False),
    CommandSpec(
        id="home.diff",
        attr="_diff_action",
        text="Differences…",
        icon="diff_icon:accent",
        tooltip=(
            "What is different between this model and another: its saved "
            "file (what you have changed), any model, or a recent one -- "
            "parameters and wiring. Nothing is run."),
        handler="_open_diff_dialog",
        enabled_when="model_open"),
    CommandSpec(
        id="results.results_show",
        attr="_results_show_action",
        text="Show Results",
        icon="chart_icon:accent",
        tooltip="Bring the Results panel to the front, full height (Ctrl+Shift+R)",
        handler="_show_results_full",
        shortcut="Ctrl+Shift+R",
        enabled=False),
    CommandSpec(
        id="results.results_float",
        attr="_results_float_action",
        text="Float Window",
        icon="float_window_icon:text",
        tooltip="Open the Results panel as its own resizable window",
        handler="_float_results",
        on="toggled",
        checkable=True,
        enabled=False),
    # ---- View --------------------------------------------------------
    CommandSpec(
        id="view.snap",
        attr="_snap_action",
        text="Snap to Grid",
        icon="snap_grid_icon:accent",
        tooltip=(
            "Align dragged/placed items to the grid (new work only — opened files"
            " are never reshuffled)"),
        handler="_diagram_dock.view.set_snap_enabled",
        on="toggled",
        checkable=True,
        checked=True,
        enabled_when="model_open"),
    CommandSpec(
        id="view.focus_event",
        attr="_focus_event_action",
        text="Focus Event",
        icon="crosshair_icon:accent",
        tooltip="Centre the view on the element that changed at each step",
        checkable=True,
        checked=True),
    CommandSpec(
        id="view.data_tables",
        attr="_data_tables_action",
        text="Data Tables",
        icon="table_grid_icon:text",
        tooltip="Show the Data Tables panel — every block/node in an editable grid",
        checkable=True,
        checked=True),
    CommandSpec(
        id="view.theme",
        attr="_theme_action",
        text="Dark Theme",
        icon="half_theme_icon:text",
        tooltip="Toggle light/dark theme",
        handler="_toggle_theme",
        on="toggled",
        checkable=True,),
    CommandSpec(
        id="view.prefs",
        attr="_prefs_action",
        text="Preferences…",
        icon="preferences_icon:text",
        tooltip="Preferences: defaults for new models, working habits, this model's display unit, advanced and developer options",
        handler="_show_preferences"),
    CommandSpec(
        id="view.explorer_toggle",
        attr="_explorer_toggle_action",
        text="Explorer",
        icon="explorer_dock_icon:text",
        checkable=True),
    CommandSpec(
        id="view.inspector_toggle",
        attr="_inspector_toggle_action",
        text="Inspector",
        icon="inspector_dock_icon:text",
        checkable=True),
    CommandSpec(
        id="view.results_toggle",
        attr="_results_toggle_action",
        text="Results",
        icon="results_dock_icon:text",
        checkable=True),
    CommandSpec(
        id="view.run_toggle",
        attr="_run_toggle_action",
        text="Run Panel",
        icon="run_panel_icon:text",
        tooltip=(
            "Show the Run panel — trials, termination, analyses and recording for"
            " the next run"),
        checkable=True),
    CommandSpec(
        id="view.reset_layout",
        attr="_reset_layout_action",
        text="Reset Layout",
        icon="reset_layout_icon:text",
        tooltip=("Put every panel back where it starts: docked, open and full size. The saved layout is "
                 "forgotten too"),
        handler="reset_layout"),
    # ---- Help --------------------------------------------------------
    CommandSpec(
        id="help.guide",
        attr="_guide_action",
        text="User Guide",
        icon="guide_icon:accent",
        tooltip=(
            "Open the User Guide: reliability simulation from "
            "scratch, with companion models"),
        handler="_open_doc",
        args=("book.html",)),
    CommandSpec(
        id="help.manual",
        attr="_manual_action",
        text="Reference Manual",
        icon="manual_icon:accent",
        tooltip=(
            "Open the field-by-field reference: every ribbon control, dialog "
            "setting and workflow. Tip: F1 jumps straight to the section for the "
            "focused dock or dialog"),
        handler="_open_doc",
        args=("manual.html",)),
    CommandSpec(
        id="help.mcp",
        attr="_mcp_docs_action",
        text="AI Assistant",
        icon="chain_link_icon:accent",
        tooltip=(
            "Set up an AI assistant over MCP (Claude Code, Claude Desktop, "
            "OpenCode, ...): opens the documentation"),
        handler="_open_doc",
        args=("manual.html", "ai-assistant")),
    CommandSpec(
        id="help.formulas",
        attr="_formulas_action",
        text="Formulas",
        icon="formulas_icon:accent",
        tooltip=(
            "Reliability formulas quick reference — every metric the engine "
            "reports (availability, MTBDE, MDT, reliability, SEM), the "
            "distribution table, and the limits of an RBD model, all on one page"),
        handler="_open_doc",
        args=("formulas.html",)),
    CommandSpec(
        id="help.shortcuts",
        attr="_shortcuts_action",
        text="Keyboard Shortcuts",
        icon="keyboard_icon:accent",
        tooltip="Every shortcut key, one page",
        handler="_open_shortcuts"),
    CommandSpec(
        id="help.about",
        attr="_about_action",
        text="About",
        icon="about_icon:accent",
        tooltip="About RAPTOR Studio — version, credits and licence",
        handler="_show_about"),
    CommandSpec(
        id="help.report_issue",
        attr="_report_issue_action",
        text="Report Issue",
        icon="report_issue_icon:accent",
        tooltip=(
            "Report a bug or request a feature — opens the GitHub issue tracker "
            "with a new, prefilled issue"),
        handler="_report_issue"),
    # ---- History (built by the undo stack; see the "undo"/"redo" factories)
    CommandSpec(
        id="home.undo",
        attr="_undo_action",
        text="Undo",
        icon="undo_icon:text",
        tooltip="Undo the last edit (Ctrl+Z)",
        handler="_refresh_data_tables",
        standard_key="Undo",
        factory="undo"),
    CommandSpec(
        id="home.redo",
        attr="_redo_action",
        text="Redo",
        icon="redo_icon:text",
        tooltip="Redo an undone edit (Ctrl+Shift+Z / Ctrl+Y)",
        handler="_refresh_data_tables",
        standard_key="Redo",
        factory="redo"),
)


def _p(title: str, index_attr: str, *groups: Group) -> Page:
    return Page(title, index_attr, groups)


#: The full-page File view (`ui/backstage.py`), opened by the File button
#: left of the tabs, as in Office: (section title, command ids). Commands
#: here are placed as surely as those on a ribbon page.
BACKSTAGE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("File", ("file.new", "file.open", "file.save", "file.save_as",
              "file.close_file", "file.quit", "home.diff")),
    ("Print and Export", ("file.print", "file.export_pdf", "file.export_tables",
               "file.export_svg", "file.export_png", "file.copy_view")),
)

#: Commands with a top level entry in the File view's nav column, after
#: the pages (so a documentation link is not buried in a section that is
#: about printing and exporting).
BACKSTAGE_ACTIONS: tuple[str, ...] = ("help.mcp",)


#: Tab order is list order; within a group, left to right. A large button
#: is a column of its own; small ones stack three to a column.
LAYOUT: tuple[Page, ...] = (
    # One first tab for building the model. Home used to hold only the
    # clipboard, Undo/Redo (also on the quick-access bar, always visible)
    # and Project Settings/Issues -- model commands, split from the rest of
    # the model's by a tab.
    _p("Model", "_model_page_index",
       Group("Clipboard", (Btn("home.paste", True), Btn("home.cut"),
                           Btn("home.copy"), Btn("home.select_all")),
             priority=2),
       Group("Tools", (Btn("model.select", True), Btn("model.connect", True))),
       Group("Build", (Btn("model.add_block", True),
                       Btn("model.add_node", True),
                       Btn("model.add_event", True),
                       Btn("model.add_hier", True),
                       Btn("model.collapse"), Btn("model.delete"))),
       Group("Model", (Btn("home.syscfg", True), Btn("home.validate", True)),
             priority=4),
       # Annotations are put on the canvas like elements, so they sit
       # beside Build; what is done to them afterwards is on Arrange.
       Group("Annotate", (Btn("arrange.anno_text", True),
                          Btn("arrange.anno_rect"),
                          Btn("arrange.anno_ellipse"),
                          Btn("arrange.anno_arrow")), priority=3),
       Group("Hierarchy", (Btn("model.import_rbd"),
                           Btn("sim.extract_hierarchy"),
                           Btn("model.hier_up"), Btn("model.hier_home"),
                           Custom("_build_goto_combo")), priority=2)),
    _p("Arrange", "_arrange_page_index",
       Group("Layout", (Btn("model.auto_layout", True), Btn("view.snap"))),
       Group("Order", (Btn("arrange.order_front"),
                       Btn("arrange.order_forward"),
                       Btn("arrange.order_backward"),
                       Btn("arrange.order_back"))),
       Group("Align", (Custom("_build_align_button"),
                       Btn("arrange.flip_h"), Btn("arrange.flip_v")))),
    _p("Data", "_data_page_index",
       Group("Reliability Data", (Btn("data.phases", True),
                                  Custom("_build_rates_button"),
                                  Custom("_build_pool_buttons")), priority=4),
       Group("Bulk Edit", (Btn("data.block_defaults", True),
                           Btn("data.mass_edit", True)), priority=2),
       Group("Library", (Btn("data.export_library"),
                         Btn("data.import_library")), priority=1)),
    _p("Simulate", "_simulate_page_index",
       # Run and Animate side by side: Animate is the run mode (one core,
       # watched); off, Run uses the cores shown right beside it.
       Group("Run", (Btn("sim.run", True), Btn("sim.live_sim", True),
                     Custom("_build_trials_box"), Custom("_build_cores_box"),
                     Btn("sim.run_settings"))),
       Group("Control", (Btn("sim.pause"), Btn("sim.stop_run"),
                         Btn("sim.jump")), priority=4),
       # Analyses are run settings and live in the Run panel's Analysis
       # box. No second set of toggles on the ribbon.
       Group("Animation", (Custom("_build_speed_controls"),), priority=1),
       # Many runs at once: one parameter across values, or several models.
       Group("Batch", (Btn("sim.sweep", True), Btn("sim.compare", True)),
             priority=3)),
    # Stepping and what-if failures are one activity -- pause, fail a block,
    # step on, repair it -- so they share a tab instead of making the user
    # cross between two. (Raptor 7 keeps its Failure Effects view a
    # sibling of the Simulation view, not a step removed from it.)
    _p("Debug", "_debug_page_index",
       Group("Step", (Btn("sim.step", True), Btn("sim.step_next_time", True),
                      Btn("sim.step_finish", True),
                      Custom("_build_step_menu"), Btn("sim.restart_step"),
                      Btn("sim.terminate"), Btn("view.focus_event")),
             priority=4),
       Group("Failure Effects", (Btn("analyse.fev_view", True),
                                 Btn("analyse.fev_fail"),
                                 Btn("analyse.fev_repair"),
                                 Btn("analyse.fev_status"),
                                 Btn("analyse.fev_erase_phase"),
                                 Btn("analyse.fev_erase_all"),
                                 Custom("_build_fev_log_button")), priority=3),
       Group("Phase", (Btn("analyse.fev_phase_prev"),
                       Btn("analyse.fev_phase_next"),
                       Btn("analyse.fev_goto_phase")), priority=2)),
    _p("Results", "_results_page_index",
       Group("Show", (Btn("results.results_show", True),
                      Btn("results.results_float", True),
                      Btn("results.clear_results"),
                      Btn("results.spof"), Btn("results.domains"))),
       Group("Show on Canvas", (Btn("results.weak_color", True),
                                Btn("results.capacity_overlay"),
                                Btn("results.cost_overlay"),
                                Btn("results.spares_overlay"),
                                Btn("results.importance_overlay"))),
       # Spread belongs with the band controls: it changes the active
       # overlay's bands (Weak Links or Cost), not the overlay itself.
       Group("Bands", (Custom("_build_weak_link_settings"),
                       Btn("results.weak_spread", True)), priority=2)),
    _p("View", "_view_page_index",
       Group("Zoom", (Btn("arrange.fit", True),
                      Btn("arrange.zoom_selection", True),
                      Btn("arrange.zoom_in"), Btn("arrange.zoom_out"),
                      Btn("arrange.zoom_percent"),
                      Btn("arrange.zoom_hierarchy")), priority=4),
       Group("Panels", (Btn("view.explorer_toggle"),
                        Btn("view.inspector_toggle"),
                        Btn("view.results_toggle"), Btn("view.run_toggle"),
                        Btn("view.data_tables"), Btn("view.reset_layout")),
             priority=3),
       Group("Appearance", (Btn("view.theme", True), Btn("view.prefs", True),
                            Custom("_build_block_labels_settings")),
             priority=2)),
    _p("Help", "_help_page_index",
       Group("Documentation", (Btn("help.guide", True),
                               Btn("help.manual", True),
                               Btn("help.formulas", True),
                               Btn("help.mcp"),
                               Btn("help.shortcuts", True)), priority=4),
       Group("Support", (Btn("help.about", True),
                         Btn("help.report_issue", True)), priority=2)),
)

"""Selection and edit signals from the canvas, explorer and inspector.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations


from typing import TYPE_CHECKING





if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
    import PySide6.QtCore
    import PySide6.QtWidgets
    import raptor.gui.ui.diagram
    import raptor.gui.ui.explorer
    import raptor.gui.ui.inspector
else:
    _Base = object


class SelectionMixin(_Base):
    """Selection and edit signals from the canvas, explorer and inspector."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        _diagram_dock: raptor.gui.ui.diagram.DiagramDock
        _explorer: raptor.gui.ui.explorer.ExplorerDock
        _inspector: raptor.gui.ui.inspector.InspectorDock
        _inspector_select_timer: PySide6.QtCore.QTimer
        _pending_block_selection: int | None
        _pending_inspector_selection: tuple[str, int] | None
        _statusbar: PySide6.QtWidgets.QStatusBar

    def _queue_inspector_selection(self, kind: str, oid: int) -> None:
        """Queue one Inspector rebuild on the debounce timer (see
        `_on_canvas_block_selected`)."""
        if kind == "block":
            self._pending_block_selection = int(oid)
            self._pending_inspector_selection = None
        else:
            self._pending_block_selection = None
            self._pending_inspector_selection = (kind, int(oid))
        self._inspector_select_timer.start()

    def _on_canvas_block_selected(self, block_id: int, _name: str):
        """Queue the Inspector rebuild instead of doing it in the click.

        A click costs ~9 ms of Inspector rebuild on a large model, and a
        rubber-band drag or arrow-key walk emits a selection for every
        element it passes. Coalescing means the rebuild happens once, for
        the element the user stopped on; the click itself returns at once
        and the canvas repaint is not queued behind seven tab builders.
        """
        self._queue_inspector_selection("block", block_id)

    def _flush_canvas_block_selection(self) -> None:
        bid = self._pending_block_selection
        other = self._pending_inspector_selection
        self._pending_block_selection = None
        self._pending_inspector_selection = None
        v = self._diagram_dock.view
        # The model may have been rebuilt (level change, reopen) while the
        # click was queued; a stale id must not show another model's block.
        if bid is not None:
            if bid in v.blocks:
                self._inspector.show_block(bid, v.blocks)
            return
        if other is None:
            return
        kind, oid = other
        if kind == "node" and oid in v.nodes:
            self._inspector.show_node(oid, v.nodes)
        elif kind == "event" and oid in v.events:
            self._inspector.show_event(v.events[oid])
        elif kind == "hier" and oid in v.hiers:
            self._inspector.show_hier(v.hiers[oid])
        elif kind == "annotation":
            item = v._anno_items.get(oid)
            if item is not None:
                self._inspector.show_annotation(item)

    def _cancel_pending_block_selection(self) -> None:
        self._pending_block_selection = None
        self._pending_inspector_selection = None
        self._inspector_select_timer.stop()

    def _on_canvas_node_selected(self, node_id: int, _name: str):
        # Same coalescing as a block: a node's Inspector rebuild is the
        # heavier of the two (its Dependency tab lists every block).
        self._queue_inspector_selection("node", node_id)

    def _on_canvas_event_selected(self, event_id: int):
        self._queue_inspector_selection("event", event_id)

    def _select_canvas_item(self, item) -> None:
        """Select `item` alone and centre the view on it -- shared by
        every Explorer selection handler, which otherwise only updates
        the Inspector's content without touching the canvas selection or
        scroll position. Safe to call with `None` (nothing found -- a
        no-op, not an error)."""
        if item is None:
            return
        v = self._diagram_dock.view
        for it in list(v._scene.selectedItems()):
            it.setSelected(False)
        item.setSelected(True)
        v.centerOn(item)

    # The Explorer lists the whole model; the canvas and the Inspector work
    # on the level on screen. Each handler first opens the element's own
    # level (`reveal_element`), then selects it.
    def _on_explorer_block_selected(self, block_id: int):
        self._cancel_pending_block_selection()
        v = self._diagram_dock.view
        if not v.reveal_element("block", block_id):
            return
        self._inspector.show_block(block_id, v.blocks)
        self._select_canvas_item(v._block_items.get(block_id))

    def _on_explorer_node_selected(self, node_id: int):
        self._cancel_pending_block_selection()
        v = self._diagram_dock.view
        if not v.reveal_element("node", node_id):
            return
        self._inspector.show_node(node_id, v.nodes)
        self._select_canvas_item(v._node_items.get(node_id))

    def _on_explorer_hier_selected(self, hier_id: int):
        """A hierarchy row selects its oval on the level that holds it; the
        canvas selection queues the Inspector, as a click on the oval does."""
        self._cancel_pending_block_selection()
        v = self._diagram_dock.view
        if v.reveal_element("hier", hier_id):
            self._select_canvas_item(v._hier_items.get(hier_id))

    def _on_explorer_event_selected(self, event_id: int):
        v = self._diagram_dock.view
        if not v.reveal_element("event", event_id):
            return
        event = v.events.get(event_id)
        if event is not None:
            self._inspector.show_event(event)
        self._select_canvas_item(v._event_items.get(event_id))

    def _on_explorer_sysconfig_selected(self):
        self._inspector.show_system_config()

    def _note_shared_name(self, kind: str, name: str, source: tuple) -> None:
        """Elements may share a name (Raptor 7's own FactoryDemo has two `CMD_5000`), so a rename is never refused;
        but a selector, a report row or a search by that name then matches both, so say so."""
        items, attr = source
        n = sum(1 for o in items.values() if getattr(o, attr, None) == name)
        if n > 1:
            self._statusbar.showMessage(
                f"{n} {kind}s are now named '{name}'. That is allowed, but selectors, searches and reports that "
                f"use the name will match all of them.", 9000)

    def _on_block_renamed(self, block_id: int, new_name: str):
        v = self._diagram_dock.view
        v.rename_block(block_id, new_name)  # Also marks the model dirty (model_changed).
        self._note_shared_name('block', new_name, (v.blocks, 'name'))
        self._explorer.set_view(v)
        self._inspector.show_block(block_id, v.blocks)

    def _on_node_renamed(self, node_id: int, new_name: str):
        v = self._diagram_dock.view
        v.rename_node(node_id, new_name)  # Also marks the model dirty (model_changed).
        self._note_shared_name('node', new_name, (v.nodes, 'node_name'))
        self._explorer.set_view(v)
        self._inspector.show_node(node_id, v.nodes)

    def _on_event_renamed(self, eid: int, new_name: str):
        v = self._diagram_dock.view
        v.rename_event(eid, new_name)
        self._note_shared_name('event', new_name, (v.events, 'name'))
        self._explorer.refresh()
        if eid in v.events:
            self._inspector.show_event(v.events[eid])

    def _on_event_field_changed(self, eid: int, field: str, value: float):
        self._diagram_dock.view.set_event_field(eid, field, value)

    def _on_phasing_changed(self, kind: str, oid: int, phased: bool,
                            entries: list):
        self._diagram_dock.view.set_element_phasing(
            kind, oid, phased, entries)
        self._statusbar.showMessage(
            f"Phasing {'set' if phased else 'cleared'} for "
            f"{kind} {oid}")

    def _on_hier_renamed(self, hier_id: int, new_name: str):
        v = self._diagram_dock.view
        v.rename_hier(hier_id, new_name)
        self._note_shared_name('hierarchy', new_name, (v.hiers, 'name'))
        self._explorer.refresh()
        self._inspector.show_hier(v.hiers[hier_id])

    def _on_block_dist_type_changed(self, bid: int, which: str, code: int):
        """Inspector distribution change. The parameters used to stay as
        they were, so Exponential(100) became Weibull(shape 100, scale 0),
        and the first edit was refused for the field the user had not
        touched yet. When the old values are not valid for the new
        distribution, it gets valid ones with the same mean -- in the same
        undo step (`DiagramView.change_block_dist`, shared with the Data
        Tables grid)."""
        v = self._diagram_dock.view
        if bid not in v.blocks:
            return
        v.change_block_dist(bid, which, code)
        self._inspector.show_block(bid, v.blocks)

    def _on_block_fail_params_changed(self, block_id: int, values: list):
        self._diagram_dock.view.set_block_fail_params(block_id, values)

    def _on_block_stream_changed(self, block_id: int, value: int):
        """Inspector stream edit. A SYSTEM pick (201..210) arrives from
        both the failure and repair signals in quick succession -- the
        dialog writes both sides from one combo -- so route each side's
        own signal to its own 'which'."""
        v = self._diagram_dock.view
        sender = self.sender()
        which = ("F" if sender is self._inspector.block_fail_stream_changed
                 else "R")
        if not v.set_block_stream(block_id, which, value):
            self._statusbar.showMessage(
                "Invalid stream — independent 1..110, system A..J "
                "(antithetic stored as negative)", 8000)

    def _on_block_repair_params_changed(self, block_id: int, values: list):
        self._diagram_dock.view.set_block_repair_params(block_id, values)

    def _on_block_pm_freq_changed(self, block_id: int, value: float):
        self._diagram_dock.view.set_block_pm_freq(block_id, value)

    def _on_node_good_paths_changed(self, node_id: int, value: int):
        self._diagram_dock.view.set_node_good_paths(node_id, value)

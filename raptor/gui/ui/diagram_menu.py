"""The diagram canvas right-click menus (a mixin of `DiagramView`)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any, cast
from PySide6.QtWidgets import (
    QGraphicsItem,
)
from raptor.gui.ui.diagram_items import (
    _BlockItem,
    _EventItem,
    _HierItem,
    _LinkItem,
    _NodeItem,
    _view_status,
)
from raptor.gui.ui.diagram_annotations import (
    ANNO_KIND_NOUNS,
    _ANNO_TYPES,
)
from raptor.gui.ui.diagram_common import (
    _dep_focus_offered,
    _properties_offered,
)


if TYPE_CHECKING:
    from raptor.gui.ui.diagram import DiagramView

class ContextMenuMixin:
    """Builds and shows the right-click menus for the canvas."""

    def _exec_menu(self: DiagramView, menu, event):  # type: ignore[misc]
        """Show `menu` at the pointer and return the chosen QAction (or
        None if dismissed).

        Every context menu in `_show_context_menu` goes through this one
        seam rather than calling `QMenu.exec` inline. `QMenu.exec` is an
        overloaded, non-virtual PySide6 method that cannot be monkey-
        patched from the outside (unlike `QDialog.exec`, which the UI
        sweeps already stub), so a method is the only way to drive these
        menus headlessly -- and being able to assert on the items and
        their enabled state is the only way to keep the "disabled when
        appropriate" contract honest.
        """
        return menu.exec(self.viewport().mapToGlobal(
            event.position().toPoint()))

    def _show_context_menu(self: DiagramView, event):  # type: ignore[misc]
        from PySide6.QtWidgets import QMenu
        if getattr(self, "_fev_view_open", False):
            # Failure Effects View has its own menu on every right-click (menu 4).
            self.fev_menu_requested.emit(self.viewport().mapToGlobal(event.position().toPoint()))
            event.accept()
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        sel_count = len([it for it in self._scene.selectedItems()
                         if it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable])
        align_menu = None
        if sel_count >= 2:
            menu = QMenu(self)
            align_menu = menu.addMenu("Align")
            for label, m in (("Left", "left"), ("Right", "right"),
                             ("Top", "top"), ("Bottom", "bottom"),
                             ("Centre horizontally", "hcenter"),
                             ("Centre vertically", "vcenter")):
                align_menu.addAction(label,
                                     lambda m2=m: self.align_selected(m2))
            dist_menu = menu.addMenu("Distribute")
            dist_menu.addAction("Horizontally",
                                lambda: self.distribute_selected("h"))
            dist_menu.addAction("Vertically",
                                lambda: self.distribute_selected("v"))
            menu.addSeparator()
            out_act = None
            if self._current_parent != 0:
                out_act = menu.addAction("Move to parent level")
            self._hier_move_submenu(menu)
            # Z-order only when the selection actually contains a note
            # (see the single-element menu's own note).
            order_acts = (self._add_order_actions(menu)
                          if self._selected_annotation_ids() else {})
            remove_links_act0 = menu.addAction("Remove Links")
            # The count includes the annotations -- the delete takes
            # them along now, so the number has to (and `sel_count`
            # alone misses arrows, which are not ItemIsMovable).
            del_act = menu.addAction(
                f"Delete {self._n_deletable_selected()} selected")
            chosen0 = self._exec_menu(menu, event)
            if chosen0 is remove_links_act0:
                self.remove_links_selected()
            elif chosen0 is del_act:
                self.delete_selected()
            elif chosen0 is out_act:
                self._move_selection_to_parent()
            else:
                for key, act in order_acts.items():
                    if chosen0 is act:
                        self.set_annotation_order(key)
                        break
            event.accept()
            return

        # right-click on a hierarchy oval -> Cut/Copy subtree + Delete.
        # Delete = DissolveCmd: children promote to this level, oval +
        # markers removed, one undo step (same path the cut entry uses).
        for it2 in self._scene.items(scene_pos):
            if isinstance(it2, _HierItem):
                hid_menu = QMenu(self)
                cut_h = hid_menu.addAction("Cut hierarchy")
                copy_h = hid_menu.addAction("Copy hierarchy")
                hid_menu.addSeparator()
                del_h = hid_menu.addAction("Delete hierarchy")
                chosen_h = self._exec_menu(hid_menu, event)
                if chosen_h is copy_h:
                    self._scene.clearSelection()
                    it2.setSelected(True)
                    self.copy_selection()
                    _view_status(self, "Hierarchy copied — Paste here "
                                 "on the canvas to duplicate")
                elif chosen_h is cut_h:
                    self._scene.clearSelection()
                    it2.setSelected(True)
                    self.copy_selection()
                    self.delete_selected()   # DissolveCmd: children
                    # promote, oval + markers removed (one undo step)
                    _view_status(self, "Hierarchy cut — Paste to "
                                 "recreate it")
                elif chosen_h is del_h:
                    self._scene.clearSelection()
                    it2.setSelected(True)
                    self.delete_selected()
                event.accept()
                return

        # right-click on an annotation -> the full command set for an
        # overlay: edit, order, duplicate, delete. copy_selection()/
        # paste() already handle annotations; this menu needs its own
        # entries since annotations short-circuit past the block/node/
        # event menu below.
        #
        # "Paste Here" is demoted BELOW Cut/Copy (and renamed to say it
        # drops the copy on this exact spot, which is why it used to read
        # as "paste on top of the thing I clicked"). Every item is
        # enabled only when it would do something.
        for it in self._scene.items(scene_pos):
            if isinstance(it, tuple(_ANNO_TYPES)):
                if not it.isSelected():
                    self._scene.clearSelection()
                    it.setSelected(True)
                mx, my = self.scene_to_model(scene_pos)
                n_annos = len(self._selected_annotation_ids())
                menu2 = QMenu(self)
                # Edit the text in place rather than making the user
                # hunt for the item's middle: this annotation is already
                # the sole selection.
                edit_act = None
                if getattr(it, "kind", None) == "text":
                    edit_act = menu2.addAction("Edit Text…")
                cut_act2 = menu2.addAction("Cut")
                copy_act2 = menu2.addAction("Copy")
                paste_act2 = menu2.addAction("Paste Here (at pointer)")
                paste_act2.setEnabled(bool(self._paste_buffer))
                menu2.addSeparator()
                # "&&" not "&": Qt reads a single ampersand in an action
                # text as a mnemonic marker and underlines the NEXT
                # character, so this item rendered as "Colours _Font…".
                # Doubling it draws a literal ampersand, which is what
                # the label says.
                prop_act = menu2.addAction("Colours && Font…")
                # Colours & Font really opens the panel: it emits
                # `properties_requested` like the element menu's
                # "Properties" does, so a closed Inspector dock is
                # raised instead of the press doing nothing but writing
                # a status-bar line. It no longer re-selects just `it`,
                # which used to silently collapse a multi-selection.
                order_acts = self._add_order_actions(menu2)
                dup_act = menu2.addAction("Duplicate")
                # "Style one, then place the rest": adopt THIS
                # annotation's colours/line/font as the starting style
                # for every future shape of its kind, so a diagram full
                # of callouts does not need the Inspector reopened per
                # shape. Offers the reset too, but only once a default
                # has actually been saved for that kind.
                kind = getattr(it, "kind", "text")
                nouns = ANNO_KIND_NOUNS.get(kind, (kind, kind))
                set_def_act = menu2.addAction(
                    f"Set as Default for New {nouns[1]}")
                clear_def_act = None
                if self.has_annotation_style_default(kind):
                    clear_def_act = menu2.addAction(
                        f"Reset {nouns[1]} Default to Factory")
                menu2.addSeparator()
                del_act = menu2.addAction(
                    f"Delete {n_annos} annotations" if n_annos > 1
                    else "Delete annotation")
                chosen2 = self._exec_menu(menu2, event)
                if chosen2 is paste_act2:
                    self.paste(at_model=(mx, my))
                elif chosen2 is copy_act2:
                    self.copy_selection()
                elif chosen2 is cut_act2:
                    self.cut_selection()
                elif chosen2 is edit_act:
                    self._edit_annotation_text(it)
                elif chosen2 is prop_act:
                    # Raises the Inspector the same way the element
                    # menu's "Properties" does; the status line says
                    # where it went, so the press is never silent.
                    _view_status(self, "Opening Colours & Font for this "
                                 "annotation in the Inspector")
                    self.properties_requested.emit()
                elif chosen2 is dup_act:
                    self._duplicate_selected_annotations()
                elif chosen2 is set_def_act:
                    self.set_annotation_style_default(kind, cast(Any, it).props)
                elif chosen2 is clear_def_act:
                    self.clear_annotation_style_default(kind)
                elif chosen2 is del_act:
                    self.delete_selected()
                else:
                    for key, act in order_acts.items():
                        if chosen2 is act:
                            self.set_annotation_order(key)
                            break
                event.accept()
                return
        """Right-click an element -> Delete (selection-aware: right-
        clicking a selected item keeps the whole multi-selection)."""
        clicked = None
        for it in self._scene.items(scene_pos):
            if isinstance(it, (_BlockItem, _NodeItem, _EventItem, _LinkItem)):
                clicked = it
                break
        if clicked is None:
            # Empty canvas: there was previously NO menu at all here, so
            # right-click paste was impossible and there was nowhere to
            # reach Select All or the Snap toggle from the mouse.
            self._show_background_context_menu(event, scene_pos)
            return
        if not clicked.isSelected():
            self._scene.clearSelection()
            clicked.setSelected(True)
        menu = QMenu(self)
        kind = ("Link" if isinstance(clicked, _LinkItem)
                else "Event" if isinstance(clicked, _EventItem)
                else "Node" if isinstance(clicked, _NodeItem) else "Block")
        n_sel = len(self._scene.selectedItems())
        n_annos = len(self._selected_annotation_ids())
        mx, my = self.scene_to_model(scene_pos)
        # Cut/Copy/Paste in the conventional order, with "Paste Here"
        # last of the three and renamed: it drops the copy on THIS exact
        # spot, which is why the old first-positioned "Paste here" read
        # as "paste on top of the thing I clicked".
        copy_act = menu.addAction("Copy")
        copy_act.setEnabled(n_sel > 0 or n_annos > 0)
        cut_act = menu.addAction("Cut")
        cut_act.setEnabled(n_sel > 0 or n_annos > 0)
        paste_act = menu.addAction("Paste Here (at pointer)")
        paste_act.setEnabled(bool(self._paste_buffer))
        out_act = None
        if self._current_parent != 0 and n_sel > 0:
            menu.addSeparator()
            out_act = menu.addAction("Move to parent level")
        self._hier_move_submenu(menu)
        menu.addSeparator()
        remove_links_act = None
        if kind != "Link":
            label_rl = ("Remove Links from selected"
                       if n_sel > 1 else "Remove Links")
            remove_links_act = menu.addAction(label_rl)
        # The z-order moves only exist when an annotation is in the
        # selection -- a right-click on a lone block cannot send it
        # anywhere, and four permanently dead items are worse than none.
        order_acts = self._add_order_actions(menu) if n_annos else {}
        # Selection-aware, and counting the ANNOTATIONS too: the delete
        # now takes them along (see `delete_selected`), so a label that
        # read "Delete 1 selected" for a block-plus-note selection was
        # wrong twice over.
        n_total = self._n_deletable_selected()
        if n_total > 1:
            label = f"Delete {n_total} selected"
        elif n_annos:
            label = "Delete annotation"
        else:
            label = f"Delete {kind}"
        del_action = menu.addAction(label)
        # Properties -- only for a single Block/Node/Event: a Link has no
        # Inspector view of its own, and "properties of N selected" is
        # ambiguous. The item is already the sole selection by this point
        # (`clicked.setSelected(True)` above), which already populated
        # the Inspector's content -- `properties_requested` only needs to
        # make MainWindow actually show/raise the dock itself.
        props_action = None
        if _properties_offered(kind, n_sel):
            menu.addSeparator()
            props_action = menu.addAction("Properties")
        # "Focus on Dependency": the only way to jump to a dependency
        # badge's target; only offered for a single Block/Node that
        # actually carries a badge.
        dep_action = None
        if _dep_focus_offered(n_sel, bool(getattr(clicked, "_dep_badge", None))):
            menu.addSeparator()
            dep_action = menu.addAction("Focus on Dependency")
        chosen = self._exec_menu(menu, event)
        if chosen is paste_act:
            self.paste(at_model=(mx, my))
        elif chosen is out_act:
            self._move_selection_to_parent()
        elif chosen is copy_act:
            self.copy_selection()
        elif chosen is cut_act:
            self.cut_selection()
        elif chosen is remove_links_act:
            self.remove_links_selected()
        elif chosen is del_action:
            self.delete_selected()
        elif chosen is props_action:
            self.properties_requested.emit()
        elif chosen is dep_action:
            self._on_dep_click(clicked)
        else:
            for key, act in order_acts.items():
                if chosen is act:
                    self.set_annotation_order(key)
                    break

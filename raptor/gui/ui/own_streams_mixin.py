"""Give blocks separate random streams (#292).

Every block shares System stream C (203) by default, as in Raptor 7.
Three places give blocks failure and repair streams of their own:

* **Data ▸ Bulk Edit ▸ Separate Streams** writes them into the model now
  (one undo step) -- for a model that should keep them, say to hand on.
* **Preferences ▸ Working ▸ New blocks**, a remembered setting that
  `diagram_commands.AddCmd` reads when a block is added, pasted or
  duplicated -- a design habit, so it is not on the ribbon.
* **Run panel ▸ More options ▸ Streams** separates them for the run only,
  on the engine's copy (`own_streams.separated_for_run`); the model is not
  edited.

There are 49 pairs; after that the least-used are reused. The rules live
in `raptor.core.edit.own_streams`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raptor.core.edit import own_streams
from raptor.gui.ui import settings

#: The remembered toggle (see `settings.DEFAULTS`).
NEW_BLOCKS_KEY = "model/own_streams_new_blocks"

if TYPE_CHECKING:
    from PySide6.QtGui import QAction

    class _Base:
        _diagram_dock: Any
        _own_streams_action: QAction
        _rbd: Any
        _statusbar: Any
else:
    _Base = object


def sys_streams_of(rbd: Any) -> tuple[int, ...]:
    """The model's System streams A..K (its SysStreamsArray)."""
    sec = getattr(rbd, "sections", {}).get("SYSTEM_DATA") if rbd else None
    row = sec.rows[0] if sec is not None and sec.rows else {}
    return own_streams.parse_sys_streams(row.get("SysStreamsArray"))


def _reuse_note(reused: int, pairs: int) -> str:
    return (f"{reused} block(s) share a stream pair with another block "
            f"(there are {pairs} pairs)")


class OwnStreamsMixin(_Base):
    """MainWindow's separate-streams command and new-block setting."""

    def _give_blocks_own_streams(self) -> None:
        """Data ▸ Bulk Edit ▸ Separate Streams: write them into the model."""
        if self._rbd is None:
            return
        view = self._diagram_dock.view
        blocks = [(bid, int(b.fail_stream), int(b.repair_stream))
                  for bid, b in sorted(view.blocks.items())]
        events = [int(e.fail_stream) for e in view.events.values()]
        sys_streams = sys_streams_of(self._rbd)
        plan, reused = own_streams.plan_all(blocks, events, sys_streams)
        # Past 49 blocks the shared pairs are planned again, onto the same
        # pairs: leave out what would not change, so a second run says
        # there is nothing to do instead of asking and adding an empty undo
        # step.
        current = {bid: (fail, repair) for bid, fail, repair in blocks}
        plan = {bid: pair for bid, pair in plan.items()
                if current.get(bid) != pair}
        if not plan:
            self._statusbar.showMessage(
                "Every block already has separate random streams", 8000)
            return
        pairs = own_streams.capacity(sys_streams)
        text = (f"Give {len(plan)} block(s) separate failure and repair "
                "random streams?\n\nResults will change and will no longer "
                "match Raptor 7's. Undo puts the streams back.")
        if reused:
            text += (f"\n\nThere are {pairs} stream pairs, so {reused} "
                     "block(s) will share a pair with another block. That "
                     "is still sound, and better than one shared stream.")
        from raptor.gui.ui import dialogs
        if not dialogs.question_box(self, "Separate Random Streams", text,
                                    yes_text="Separate the streams",
                                    no_text="Cancel"):
            return
        view._stack.beginMacro("Separate random streams")
        try:
            for bid, (fail, repair) in plan.items():
                view.set_block_stream(bid, "F", fail)
                view.set_block_stream(bid, "R", repair)
        finally:
            view._stack.endMacro()
        self._statusbar.showMessage(
            f"{len(plan)} block(s) now have separate random streams"
            + (f"; {_reuse_note(reused, pairs)}" if reused else ""), 10000)

    def _set_own_streams_for_new_blocks(self, on: bool) -> None:
        """Preferences ▸ Working ▸ New blocks (remembered per user)."""
        settings.set_value(NEW_BLOCKS_KEY, bool(on))
        self._diagram_dock.view.own_streams_for_new_blocks = bool(on)

    def _init_own_streams_toggle(self) -> None:
        """Hand the remembered new-block setting to the canvas."""
        on = settings.get_bool(NEW_BLOCKS_KEY, False)
        view = self._diagram_dock.view
        view.own_streams_for_new_blocks = on
        view.streams_reused.connect(self._on_streams_reused)

    def _on_streams_reused(self, count: int) -> None:
        pairs = own_streams.capacity(sys_streams_of(self._rbd))
        self._statusbar.showMessage(
            f"New block streams rolled over: {_reuse_note(count, pairs)}",
            10000)

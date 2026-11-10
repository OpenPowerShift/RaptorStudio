"""Report output streams: which reports a run writes, and their lifetime.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations


from typing import TYPE_CHECKING, Any





if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class ReportStreamsMixin(_Base):
    """Report output streams: which reports a run writes, and their lifetime."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        _active_report_streams: Any
        _rbd: Any
        _run_own_change: Any
        _statusbar: Any

    def _report_choices(self) -> dict[str, bool]:
        """Snapshot the Files & Reports checkboxes on the GUI thread.

        A background worker must not read QAction state: Qt widgets belong
        to the GUI thread, and reading one while the canvas paints is a
        data race that showed up as an occasional segfault in Python's
        cyclic GC. The worker gets this plain dict instead.
        """
        acts = getattr(self, "_report_stream_actions", {})
        choices = {key: bool(act.isChecked()) for key, act in acts.items()}
        analyses = self._analysis_flags()
        for key, flag, label in (
                ("capacity_plot", "capacityAnalysis", "Capacity"),
                ("cost_plot", "costAnalysis", "Cost")):
            if not choices.get(key):
                continue
            if analyses.get(flag):
                continue
            # The run is already active here, so this untick would look
            # like a user edit to the edit-during-run guard and prompt to
            # cancel the very run that made it.
            choices[key] = False
            act = acts.get(key)
            if act is not None:
                self._run_own_change = True
                try:
                    act.setChecked(False)
                finally:
                    self._run_own_change = False
            self._statusbar.showMessage(
                f"{label} plot not collected — turn on "
                f"Analyses ▸ {label} to record it", 8000)
        return choices

    def _open_report_streams(self, choices: dict[str, bool] | None = None) -> dict:
        """Create the disk-backed ResultStreams for this batch's ticked
        Files & Reports checkboxes (None = the sink is not collected and
        is not handed to the engine at all). Closes the previous batch's
        temp files first: plot rows must never accumulate in RAM across
        runs.

        `choices` is the plain snapshot from `_report_choices`; pass it
        from a worker thread. None reads the live checkboxes, which is only
        safe on the GUI thread (the synchronous run path).
        """
        from raptor.gui.ui.result_streams import ResultStream
        if choices is None:
            choices = self._report_choices()
        self._close_report_streams()
        streams: dict = {}
        # The End-of-Run report is O(runs x elements). Always spool it to a
        # temp stream; the Results dock shows a bounded preview and Save
        # Raw Report exports the whole file. This is not a user-facing
        # "Files & Reports" opt-in: without it the GUI has to hold the
        # whole report in RAM.
        streams["end_of_run"] = ResultStream("end_of_run")
        if choices.get("ao_plot"):
            # The engine writes its own file header once on first sight
            # of an empty sink (`if _ao_sink is not None and not _ao_-
            # sink`) — ResultStream's list-like truthiness keeps that
            # working, so no GUI-side header here.
            streams["ao"] = ResultStream("ao")
        # The Capacity and Cost sinks are DEPENDENT on their analysis
        # flags, and the dependency used to live only in tooltip prose
        # ("Requires Capacity Analysis" / "Requires Cost Analysis") in a
        # group 600px away from the toggles that set it. Ticking the plot
        # on a model without the analysis handed the engine a sink that
        # nothing could ever write to, and the user got an empty plot
        # and no message.
        #
        # Enforced HERE, in the command layer, rather than only in the
        # widget: a legacy .rbd import, a profile load, or any other
        # programmatic route to the flag can set the plot without going
        # through the checkbox, and a widget-only gate would be bypassed.
        # When it bites we say so, once, in the status bar, naming what to
        # turn on.
        for key in ("capacity_plot", "cost_plot"):
            if choices.get(key):
                streams["capacity" if key == "capacity_plot" else "cost"] \
                    = ResultStream(
                        "capacity" if key == "capacity_plot" else "cost")
        return streams

    def _analysis_flags(self) -> dict:
        """The three SYSTEM_DATA analysis flags as booleans, read
        straight from the row. Empty dict when no model is open, so
        callers treat every dependent feature as unavailable rather than
        assuming it is on."""
        if self._rbd is None:
            return {}
        sec = self._rbd.sections.get("SYSTEM_DATA")
        if not sec or not sec.rows:
            return {}
        row = sec.rows[0]

        def flag(col: str) -> bool:
            v = str(row.get(col, "")).strip().upper()
            return v.startswith(("1", "T", "TRUE"))
        return {"weakAnalysis": flag("weakAnalysis"),
                "costAnalysis": flag("costAnalysis"),
                "capacityAnalysis": flag("capacityAnalysis")}

    def _remember_report_streams(self, streams: dict) -> None:
        """Adopt this batch's streams (the Results dock reads them lazily
        from disk); they stay alive until the NEXT batch opens them."""
        self._active_report_streams = [s for s in streams.values()
                                       if s is not None]

    def _close_report_streams(self) -> None:
        for s in getattr(self, "_active_report_streams", None) or []:
            s.close()
        self._active_report_streams = []

    @staticmethod
    def _discard_report_streams(streams) -> None:
        """Close a batch's streams that never became the active set (the
        run failed before `_remember_report_streams`). Without this their
        temp files are orphaned: every batch opens an End-of-Run stream,
        so a failed run would leak one file per attempt."""
        for s in (streams or {}).values():
            if s is None:
                continue
            try:
                s.close()
            except Exception:       # noqa: BLE001 - cleanup must not mask
                pass

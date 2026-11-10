"""Canvas overlays: weak-link, spread, importance, capacity, cost and spares.

Expects on `self`: `_diagram_dock`, `_results_dock`, the overlay actions."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raptor.gui.ui.qtcompat import req

import math
from PySide6.QtCore import (Qt)
from PySide6.QtWidgets import (
    QApplication,
)

from raptor.gui.ui import settings
from raptor.core.engine.weak_metric import (
    canonical as canonical_analysis, cross_run_event_metric,
    cross_run_node_metric, cross_run_weak_metric)
from raptor.core.fileio.typed import system_config_from_row




if TYPE_CHECKING:
    # Mixin methods call Qt on `self`; type-check them as a main window.
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class OverlayMixin(_Base):
    """Canvas overlays: weak-link, spread, importance, capacity, cost and spares."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        def __getattr__(self, name: str) -> Any:
            """State owned by `MainWindow`; typed loosely here."""
            ...

    def _sync_analysis_checkboxes(self):
        """Reflect SYSTEM_DATA flags onto the Simulate ribbon checks +
        sync the Weak Link metric/band widgets."""
        if self._rbd is None:
            return
        try:
            from raptor.core.fileio.typed import system_config_from_row
            cfg = system_config_from_row(
                self._rbd.sections["SYSTEM_DATA"].rows[0])
        except (ValueError, KeyError, IndexError, AttributeError):
            return
        mapping = {"weakAnalysis": cfg.weak_analysis,
                   "costAnalysis": cfg.cost_analysis,
                   "capacityAnalysis": cfg.capacity_analysis}
        for col, checked in mapping.items():
            act = self._analysis_actions.get(col)
            if act is not None and act.isChecked() != bool(checked):
                act.blockSignals(True)
                act.setChecked(bool(checked))
                act.blockSignals(False)
        # weak band widgets
        want_type = (cfg.node_anal_type or "Availability").strip()
        i = self._weak_type_combo.findText(want_type.capitalize())
        if i >= 0 and self._weak_type_combo.currentIndex() != i:
            self._weak_type_combo.blockSignals(True)
            self._weak_type_combo.setCurrentIndex(i)
            self._weak_type_combo.blockSignals(False)
        self._gy_spin.blockSignals(True)
        self._gy_spin.setValue(cfg.gy_threshold)
        self._gy_spin.blockSignals(False)
        self._yr_spin.blockSignals(True)
        self._yr_spin.setValue(cfg.yr_threshold)
        self._yr_spin.blockSignals(False)
        self._results.set_weak_settings(want_type, cfg.gy_threshold,
                                        cfg.yr_threshold)

    def _weak_metric(self):
        """All Weak Link metrics from the last result set.

        Returns `(blocks, nodes, events, hierarchies, metric_name, cfg)`,
        or None with the reason on the status bar. Blocks, nodes, events
        and hierarchies are each `{id: mean metric}`; Raptor 7 colours
        all four and prints the value under each.

        Memoised per result set and metric/thresholds: on a large model
        the cross-run average walks every element in every run (about a
        second at 3,500 blocks x 100 runs). Toggling the overlay off and
        on again, or re-banding after a threshold edit, used to pay that
        every time; a new batch (or a different metric) is a new key.
        """
        data = self._results._report_data or []
        runs = [d.weak_stats for d in data if d.weak_stats]
        node_runs = [d.weak_nodes for d in data if d.weak_nodes]
        event_runs = [d.weak_events for d in data if d.weak_events]
        hier_runs = [d.weak_hiers for d in data if d.weak_hiers]
        if not runs and not node_runs and not event_runs:
            self._statusbar.showMessage(
                "No weak-link data — enable Weak Link in the Run panel's "
                "Analysis section and run again")
            return None
        try:
            cfg = system_config_from_row(
                req(self._rbd).sections["SYSTEM_DATA"].rows[0])
        except (ValueError, KeyError, IndexError):
            self._statusbar.showMessage("No SYSTEM_DATA row — cannot "
                                        "read weak-link settings")
            return None
        metric_name = canonical_analysis(cfg.node_anal_type)
        key = (id(data), len(data), metric_name,
               cfg.gy_threshold, cfg.yr_threshold)
        cached = getattr(self, "_weak_metric_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        cold = not getattr(self, "_run_in_progress", False)
        if cold:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._statusbar.showMessage("Computing Weak Link values…")
            # Paint ONLY the status bar: `processEvents()` here would let a
            # second click re-enter the toggle while the metric is half
            # computed.
            self._statusbar.repaint()
        try:
            # Per Raptor 7: thresholds are fractions of the METRIC itself
            # (e.g. Availability >= GY -> Green). The bucket sets live in
            # `core.engine.weak_metric` -- they used to be spelled out here,
            # and this copy kept `idle` in the Dependability denominator while
            # the results tab dropped it, so the same block could be coloured
            # green in the table and red on the canvas.
            def ids_of(run_list):
                return set().union(*(set(r) for r in run_list)) \
                    if run_list else set()

            block_ids = ids_of(runs)
            node_ids = ids_of(node_runs)
            event_ids = ids_of(event_runs)
            hier_ids = ids_of(hier_runs)
            block_missions = [d.missions_attempted for d in data if d.weak_stats]
            node_missions = [d.missions_attempted for d in data if d.weak_nodes]
            event_missions = [d.missions_attempted for d in data if d.weak_events]
            hier_missions = [d.missions_attempted for d in data if d.weak_hiers]
            result = (
                cross_run_weak_metric(runs, block_ids, metric_name,
                                      missions=block_missions),
                cross_run_node_metric(node_runs, node_ids, metric_name,
                                      missions=node_missions),
                cross_run_event_metric(event_runs, event_ids, metric_name,
                                       missions=event_missions),
                cross_run_node_metric(hier_runs, hier_ids, metric_name,
                                      missions=hier_missions),
                metric_name, cfg,
            )
        finally:
            if cold:
                QApplication.restoreOverrideCursor()
        self._weak_metric_cache = (key, result)
        return result

    def _spread_overlay(self):
        """Band the ACTIVE overlay by its own quartiles.

        Spread used to fall through to Weak Links whenever Cost was not
        current, so pressing it with Spares showing silently turned Weak
        Links on and rewrote the model thresholds. Capacity and Spares do
        not have a meaningful quartile band here; say so instead of
        switching the user's overlay.
        """
        if self._capacity_overlay_action.isChecked():
            self._statusbar.showMessage(
                "Capacity bands are fixed at Green < 70%, Yellow < 90%, "
                "Red >= 90%; there are no spread thresholds to set", 8000)
            return
        if self._spares_overlay_action.isChecked():
            self._statusbar.showMessage(
                "Spares is a flag (any exhausted time is Red), not a "
                "quartile band; there is nothing to spread", 8000)
            return
        if self._importance_overlay_action.isChecked():
            self._statusbar.showMessage(
                "Importance bands are already relative to the largest "
                "value in this run (Red >= 2/3, Yellow >= 1/3); a quartile "
                "split would not add information", 8000)
            return
        if self._cost_overlay_action.isChecked():
            self._cost_spread = True
            self._on_cost_overlay_toggled(True)
            return
        if self._weak_color_action.isChecked():
            self._spread_weak_thresholds()
            return
        self._statusbar.showMessage(
            "Choose an overlay first: Spread applies to Weak Links or Cost",
            8000)

    def _spread_weak_thresholds(self):
        """Results > Weak Link > Spread: Green and Red at the quartiles of
        this run's per-block metric, so the colours separate the blocks.
        A highly available model put every block above 0.95, all Green,
        which said nothing about which one was weakest."""
        import math
        import statistics
        found = self._weak_metric()
        if found is None:
            return
        metric, _nodes, _events, _hiers, metric_name, _cfg = found
        vals = sorted(v for v in metric.values() if math.isfinite(v))
        if len(vals) < 2 or vals[0] == vals[-1]:
            self._statusbar.showMessage(
                f"Every block has the same {metric_name.lower()} — there is "
                f"nothing to spread", 6000)
            return
        q1, _median, q3 = statistics.quantiles(vals, n=4, method="inclusive")
        if q3 == q1:                 # most blocks tie: split at the ends
            q1, q3 = vals[0] + 1e-9, vals[-1]
        # Red is strictly below Y/R, Green at or above G/Y. The model's own
        # rule refuses Y/R above G/Y at any moment, so move whichever keeps
        # that true first.
        order = [("YRthreshold", q1), ("GYthreshold", q3)]
        if q1 > self._gy_spin.value():
            order.reverse()
        self._apply_threshold_edits(order, "Spread Weak Link bands")
        red = sum(v < q1 for v in vals)
        green = sum(v >= q3 for v in vals)
        self._statusbar.showMessage(
            f"Weak Link bands spread by quartile: Green ≥ {q3:.6g} "
            f"({green} blocks), Red < {q1:.6g} ({red} blocks)", 8000)

    def _weak_flat_band_hint(self, values, gy: float, yr: float,
                             metric_name: str) -> str | None:
        """Status hint when every Weak Link value lands in ONE band.

        A highly reliable model puts every block above the Green
        threshold, so the overlay paints one flat colour and ranks
        nothing; the same is true of a model entirely inside the Yellow
        band or entirely below Red. The user's thresholds are NOT
        rewritten -- Spread is the user's call, and this only points at
        it. Returns None when the values do span more than one band.
        """
        finite = [v for v in values if math.isfinite(v)]
        if not finite:
            return None
        hi, lo = max(gy, yr), min(gy, yr)
        bands = {"Green" if v >= hi else
                 "Yellow" if v >= lo else "Red" for v in finite}
        if len(bands) != 1:
            return None
        band = bands.pop()
        if band == "Green":
            return (f"All {metric_name} values are above the Green "
                    f"threshold (lowest {min(finite):.6g}) — press Spread "
                    f"to band this run")
        if band == "Red":
            return (f"Every {metric_name} value is below the Red threshold "
                    f"(highest {max(finite):.6g}) — press Spread to band "
                    f"this run")
        return (f"Every {metric_name} value is in the Yellow band "
                f"({min(finite):.6g}–{max(finite):.6g}) — press Spread to "
                f"band this run")

    def _toggle_weak_colors(self):
        # Explicit `setChecked` throughout (never relying on Qt's own
        # auto-toggle-on-click) -- several existing call sites invoke
        # this as a plain Python method, not through the QAction's own
        # `.trigger()`, which wouldn't flip its checked state at all;
        # and a checkable action clicked for Raptor 7 would auto-toggle
        # before this even runs, so an early "no data" return must
        # explicitly correct it back to unchecked or the ribbon shows
        # a stuck pressed-looking button with nothing actually shown.
        v = self._diagram_dock.view
        if getattr(v, "_weak_colors_on", False):
            v.clear_result_overlays()
            self._weak_color_action.setChecked(False)
            self._statusbar.showMessage("Weak Link colouring off")
            self._update_overlay_chip()
            return
        found = self._weak_metric()
        if found is None:
            self._weak_color_action.setChecked(False)
            return
        (metric, node_metric, event_metric, hier_metric,
         metric_name, cfg) = found
        gy, yr = cfg.gy_threshold, cfg.yr_threshold
        digits = settings.get_int("appearance/weak_link_digits", 3)
        self._deactivate_other_canvas_overlays(except_kind="weak")
        v.clear_result_overlays()
        v._weak_colors_on = True
        self._weak_color_action.setChecked(True)
        v.apply_weak_colors(
            metric, gy, yr, metric_name=metric_name,
            node_metric=node_metric, event_metric=event_metric,
            hier_metric=hier_metric, digits=digits)
        all_values = [*metric.values(), *node_metric.values(),
                      *event_metric.values(), *hier_metric.values()]
        worst = min(all_values) if all_values else 1.0
        # A flat all-Green (or all-Red/all-Yellow) canvas ranks nothing.
        # Say so ONCE, here on activation -- never on a repaint or a
        # selection, which do not pass through this handler -- and leave
        # the bands and Spread itself untouched.
        hint = self._weak_flat_band_hint(all_values, gy, yr, metric_name)
        if hint is not None:
            self._statusbar.showMessage(hint, 12000)
        else:
            self._statusbar.showMessage(
                f"Weak Link colouring on ({metric_name}) — lowest value "
                f"{worst:.6g} (Green ≥ {gy:.6g} ≥ Yellow ≥ {yr:.6g} > Red)")
        self._update_overlay_chip()

    def _update_overlay_chip(self) -> None:
        """Status-bar readout of the one active canvas overlay."""
        chip = getattr(self, "_overlay_chip", None)
        if chip is None:
            return
        v = self._diagram_dock.view
        vis = getattr(v, "_last_result_visuals", None) or {}
        kind = "weak" if getattr(v, "_weak_colors_on", False) \
            else vis.get("kind")
        names = {"weak": "Weak Links", "capacity": "Capacity",
                 "cost": "Cost", "spares": "Spares",
                 "importance": "Importance"}
        if kind:
            chip.setText(f"Canvas: {names.get(kind, kind)}")
            chip.show()
        else:
            chip.clear()
            chip.hide()

    def _on_importance_overlay_toggled(self, on: bool):
        v = self._diagram_dock.view
        if not on:
            v.clear_result_overlays()
            self._statusbar.showMessage("Importance colouring off")
            self._update_overlay_chip()
            return
        data = getattr(self._results, "_importance", None) or {}
        metric = getattr(self._results, "_importance_metric", "Availability")
        entry = (data.get("metrics") or {}).get(metric)
        if not entry or not entry.get("rows"):
            self._statusbar.showMessage(
                "No importance measures in this run. Switch on Run ▸ Analysis "
                "▸ Importance measures and run again — it costs one extra "
                "batch per block (two for Both).", 12000)
            self._uncheck_overlay("_importance_overlay_action")
            return
        # Colour by the SAME measure the results tab ranks by, so the canvas
        # and the table cannot disagree. `ranking_measure` prefers
        # Vesely-Fussell because Birnbaum saturates in a series model.
        from raptor.api.importance import RANKING_LABELS, ranking_measure
        rows = entry["rows"]
        key = ranking_measure(rows)
        measure = RANKING_LABELS[key]
        values = {int(r["block_id"]): float(r[key]) for r in rows
                  if r.get(key) is not None}
        if not values:
            self._statusbar.showMessage(
                f"No {measure} values to colour: every block is undefined "
                f"for this run")
            self._uncheck_overlay("_importance_overlay_action")
            return
        self._deactivate_other_canvas_overlays(except_kind="importance")
        v.clear_result_overlays()
        v.apply_importance_colors(values, measure=measure,
                                  metric_name=metric)
        top_bid = max(values, key=lambda k: values[k])
        top_name = next((r["block"] for r in rows
                         if int(r["block_id"]) == top_bid), str(top_bid))
        self._statusbar.showMessage(
            f"Importance colouring on ({measure}, on {metric}) — most "
            f"influential block: {top_name} ({values[top_bid]:.6g}). "
            f"Red means 'fix this first', not 'this is failing'.")
        self._update_overlay_chip()

    def _on_importance_metric_changed(self, label: str) -> None:
        """Keep the canvas on the metric the Importance tab is showing.

        The overlay colours by the selected metric's rows, so switching
        Availability -> Reliability left the diagram coloured by a metric
        the table no longer displayed, with a status line still naming it.
        Only re-applies when the overlay is actually on: turning it on
        later reads the current metric anyway.
        """
        action = getattr(self, "_importance_overlay_action", None)
        if action is None or not action.isChecked():
            return
        self._on_importance_overlay_toggled(True)

    def _deactivate_other_canvas_overlays(self, except_kind: str):
        """The canvas has exactly one active colour overlay at a time
        (`DiagramView._last_result_visuals`'s own single-slot design):
        Weak Link, Capacity, Cost and Spares, all on the Results ribbon's
        Show on Canvas group. Called by whichever one is about to turn on, so the
        other three's own UI (checked state) doesn't go stale. Doesn't
        itself clear the canvas -- the caller immediately overwrites with
        its own colours, so an intermediate clear would just flicker."""
        v = self._diagram_dock.view
        if except_kind != "weak" and getattr(v, "_weak_colors_on", False):
            v._weak_colors_on = False
            self._weak_color_action.setChecked(False)
        for kind in ("capacity", "cost", "spares", "importance"):
            if kind != except_kind:
                self._uncheck_overlay(f"_{kind}_overlay_action")

    def _on_capacity_overlay_toggled(self, on: bool):
        v = self._diagram_dock.view
        if not on:
            v.clear_result_overlays()
            self._statusbar.showMessage("Capacity colouring off")
            self._update_overlay_chip()
            return
        caps = [d.capacity for d in (self._results._report_data or [])
                if d.capacity is not None]
        if not caps:
            self._statusbar.showMessage(
                "No capacity data — enable Capacity in the Run panel's "
                "Analysis section and run again")
            self._uncheck_overlay("_capacity_overlay_action")
            return
        usage: dict[int, float] = {}
        capability: dict[int, float] = {}
        node_ids = {nid for cap in caps for nid in cap.nodes}
        for nid in node_ids:
            per_run = [cap.nodes[nid] for cap in caps if nid in cap.nodes]
            # Only capacity nodes ("Unrestricted flow" off) have a
            # utilisation: an unrestricted node is an infinite pipe, which
            # Raptor 7 reports as N/A. Colouring those 0 % green made a
            # model with no capacity nodes read "highest utilization 0.0 %".
            if not per_run or any(s.full_flow for s in per_run):
                continue
            usage[nid] = sum(s.usage for s in per_run) / len(per_run)
            capability[nid] = (sum(s.capability for s in per_run)
                               / len(per_run))
        if not usage:
            self._statusbar.showMessage(
                "No capacity nodes to colour: every node has unrestricted "
                "flow (an infinite pipe). Untick \"Unrestricted flow\" on a "
                "node in the Inspector and give its incoming links a "
                "maximum flow, then run again.", 12000)
            self._uncheck_overlay("_capacity_overlay_action")
            return
        self._deactivate_other_canvas_overlays(except_kind="capacity")
        v.clear_result_overlays()
        v.apply_capacity_colors(usage, capability)
        worst = max(usage.values()) if usage else 0.0
        self._statusbar.showMessage(
            f"Capacity colouring on — highest utilization "
            f"{worst * 100:.1f}% (Green < 70% · Yellow < 90% · Red ≥ 90%)")
        self._update_overlay_chip()

    def _on_cost_overlay_toggled(self, on: bool):
        v = self._diagram_dock.view
        if not on:
            v.clear_result_overlays()
            self._statusbar.showMessage("Cost colouring off")
            self._update_overlay_chip()
            return
        costs = [d.cost for d in (self._results._report_data or [])
                if d.cost is not None]
        if not costs:
            self._statusbar.showMessage(
                "No cost data — enable Cost Analysis in Project "
                "Settings and run again")
            self._uncheck_overlay("_cost_overlay_action")
            return
        cost_by_block: dict[int, float] = {}
        block_ids = {bid for c in costs for bid in c.blocks}
        for bid in block_ids:
            per_run = [c.blocks[bid].total for c in costs if bid in c.blocks]
            if per_run:
                cost_by_block[bid] = sum(per_run) / len(per_run)
        self._deactivate_other_canvas_overlays(except_kind="cost")
        v.clear_result_overlays()
        spread = getattr(self, "_cost_spread", False)
        v.apply_cost_colors(cost_by_block, spread=spread)
        top = max(cost_by_block.values()) if cost_by_block else 0.0
        self._statusbar.showMessage(
            f"Cost colouring on{' (spread by quartile)' if spread else ''}"
            f" — most expensive block ${top:,.2f} total life-cycle cost")
        self._update_overlay_chip()

    def _on_spares_overlay_toggled(self, on: bool):
        v = self._diagram_dock.view
        if not on:
            v.clear_result_overlays()
            self._statusbar.showMessage("Spares colouring off")
            self._update_overlay_chip()
            return
        runs = [d.weak_stats for d in (self._results._report_data or [])
               if d.weak_stats]
        costs = [d.cost for d in (self._results._report_data or [])
                if d.cost is not None]
        if not runs:
            self._statusbar.showMessage(
                "No weak-link data — enable Weak Link in the Run panel's "
                "Analysis section and run again (Spares reads the "
                "same per-block state-time data)")
            self._uncheck_overlay("_spares_overlay_action")
            return
        exhausted_frac: dict[int, float] = {}
        for bid in runs[0]:
            fracs = []
            for ws in runs:
                if bid not in ws:
                    continue
                total = sum(ws[bid][k] for k in
                           ("run", "standby", "idle", "pm", "pm_hold",
                            "repair", "rep_hold", "done"))
                fracs.append((ws[bid]["done"] / total) if total else 0.0)
            if fracs:
                exhausted_frac[bid] = sum(fracs) / len(fracs)
        sparing_cost: dict[int, float] = {}
        block_ids = {bid for c in costs for bid in c.blocks}
        for bid in block_ids:
            per_run = [c.blocks[bid].sparing for c in costs
                      if bid in c.blocks]
            if per_run:
                sparing_cost[bid] = sum(per_run) / len(per_run)
        self._deactivate_other_canvas_overlays(except_kind="spares")
        v.clear_result_overlays()
        v.apply_spares_colors(exhausted_frac, sparing_cost)
        flagged = sum(1 for f in exhausted_frac.values() if f > 0.0)
        self._statusbar.showMessage(
            f"Spares colouring on — {flagged} of {len(exhausted_frac)} "
            f"block(s) had spares/life-exhausted time this run")
        self._update_overlay_chip()

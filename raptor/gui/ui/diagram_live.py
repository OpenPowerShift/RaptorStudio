"""Live-simulation and result overlays for the diagram canvas: state
colours during a run, weak-link / capacity / cost / spares bands, and the
live-state cache re-applied on level changes. A mixin of `DiagramView`."""
from __future__ import annotations

from raptor.gui.ui.qtcompat import req
from typing import TYPE_CHECKING, Any
from raptor.gui.resources.design_tokens import (
    APPENDIX_D_STATE_COLOURS, COLOUR, ThemeManager,
)
from raptor.gui.ui.diagram_items import (
    _LinkItem,
)


if TYPE_CHECKING:
    from raptor.gui.ui.diagram import DiagramView


def _modsim_round(value: float) -> int:
    """Modsim `ROUND` for the positive flow values here: half away from
    zero (Python's `round` is half-to-even)."""
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def _flow_label(flow: int) -> str:
    """Port of `RBDNodeObj.ShowFlow`'s number formatting
   : 1-9999 plain, then K/M/G with the
    source's own rounding and thresholds. Used under capacity nodes
    during the live view."""
    if flow < 10000:
        return str(flow)
    if flow < 999500:
        return f"{_modsim_round(flow / 1000)}K"
    if flow < 9950000:
        return f"{_modsim_round(flow / 100000) / 10:.1f}M"
    if flow < 999500000:
        return f"{_modsim_round(flow / 1000000)}M"
    if flow < 9950000000:
        # The source's own branch labels this M, not G.
        return f"{_modsim_round(flow / 100000000) / 10:.1f}M"
    if flow < 100000000000:
        return f"{_modsim_round(flow / 1000000000)}G"
    return str(flow)


class LiveOverlayMixin:
    """Colours the canvas items from live engine state and run results."""

    #: Live-view block colours: Raptor's own outerColor CASE
    #: Sourced from APPENDIX_D_STATE_COLOURS
    #: (single source of truth). The extra "Done flips to LimeGreen once
    #: runCompleted" rule is not applied here: live colouring is torn
    #: down when a batch ends, so the completed-run frame is never
    #: shown.
    _LIVE_STATE_COLOURS = {
        "Running": APPENDIX_D_STATE_COLOURS["running"],
        "Repairing": APPENDIX_D_STATE_COLOURS["repairing"],
        "Done": APPENDIX_D_STATE_COLOURS["done"],
        "Idle": APPENDIX_D_STATE_COLOURS["idle"],
        "Standby": APPENDIX_D_STATE_COLOURS["warm_standby"],
        "PM": APPENDIX_D_STATE_COLOURS["maintenance"],
        "PMhold": APPENDIX_D_STATE_COLOURS["pm_hold"],
        "RepHold": APPENDIX_D_STATE_COLOURS["hold"],
    }

    #: Raptor 7 "Component Functioning State Colors" -- a phased,
    #: running component shades among three greens by its stress
    #: (nominal=1, under-stress <1, over-stress >1). `ChangeRunningColor`
    #: Running below 1 is DarkGreen, at 1 LimeGreen, above 1
    #: MediumSpringGreen -- so under-stress is the DARK green and over-stress the BRIGHT one
    #: (captured from Raptor 7 on Phasing3 2026-10-11: Thermal at stress 1.5 bright, at 0.5 dark;
    #: these two used to be the other way round). A Standby block follows its standby stress the
    #: same way: 0 Blue, below 1 SkyBlue, above 1 Turquoise.
    _STRESS_COLOURS = {
        "nominal": APPENDIX_D_STATE_COLOURS["running"],
        "under": "#1F7A1F",
        "over": "#3CF08C",
    }
    _STANDBY_STRESS_COLOURS = {
        "none": APPENDIX_D_STATE_COLOURS["warm_standby"],
        "under": "#87CEEB",
        "over": "#40E0D0",
    }

    #: Hover help for each live colour (manual Appendix D categories).
    _LIVE_STATE_HELP = {
        "Running": "Running — UP: functioning normally",
        "Repairing": "Repairing — DOWN: failed, repair in progress",
        "Done": "Done — DOWN: life exhausted, awaiting replacement",
        "Idle": "Idle — DOWN: constrained by an upstream dependency",
        "Standby": "Standby — UP: reserved (warm standby)",
        "PM": "PM — DOWN: scheduled preventive maintenance",
        "PMhold": "PM hold — DOWN: waiting to start PM",
        "RepHold": "Repair hold — DOWN: waiting for repair resources",
    }

    #: Node/Hierarchy live colours (Appendix D "Node Colors"/"Hierarchy
    #: Colors" -- both use `NodeStatus`, same enum, same six states,
    #: hierarchy status being a rollup of the same vocabulary). Exact
    #: `ChangeNodeState`/`ChangeStatus` CASE colours: Degraded is
    #: Yellow, not a port-specific amber (the DSI uses the same colour).
    _NODE_STATE_COLOURS = {
        "AllUp": APPENDIX_D_STATE_COLOURS["running"],          # j==n
        "Degraded": APPENDIX_D_STATE_COLOURS["degraded"],      # Yellow
        "NodeStandby": APPENDIX_D_STATE_COLOURS["warm_standby"],
        "NodeIdle": APPENDIX_D_STATE_COLOURS["idle"],
        "NodePM": APPENDIX_D_STATE_COLOURS["maintenance"],
        "Down": APPENDIX_D_STATE_COLOURS["repairing"],
    }
    _NODE_STATE_HELP = {
        "AllUp": "Good — UP: every inbound link functional (j = n)",
        "Degraded": "Degraded — UP: enough links up (k ≤ j < n)",
        "NodeStandby": "Standby — UP: enough links in standby to switch over",
        "NodeIdle": "Idle — DOWN: constrained by an upstream dependency",
        "NodePM": "PM — DOWN: below k due to upstream preventive maintenance",
        "Down": "Bad — DOWN: below k due to failed/cut upstream links",
    }

    #: Event live colours (Appendix D "Event Colors": Success/Armed/
    #: Failed) -- an event's own draw outcome, not a block-style up/down
    #: state, so it doesn't reuse the block/node palettes directly.
    _EVENT_STATE_COLOURS = {
        "Success": APPENDIX_D_STATE_COLOURS["running"],
        "Armed": "#6080A0",     # phased, not yet fired -- pending
        "Failure": APPENDIX_D_STATE_COLOURS["repairing"],
    }
    _EVENT_STATE_HELP = {
        "Success": "Success — fired: random draw ≤ the event's p-value",
        "Armed": "Armed — phased, not yet fired",
        "Failure": "Failed — fired: random draw > the event's p-value",
    }

    #: Link live colours -- Raptor's own ChangeLinkStatus colour CASE
    #: LinkUp=Black, LinkDown=Red, LinkIdle=
    #: Sienna (brown), LinkStandby=Blue, LinkPM=Orange, LinkDone=Red.
    #: Our canvas's own default/resting pen is `COLOUR["muted"]` (dark
    #: grey) -- that is what an up link resolves to here (theme token,
    #: not hardcoded black). LinkDone is not in this map: it falls
    #: through to the muted fallback.
    _LINK_STATE_COLOURS = {
        "LinkDown": APPENDIX_D_STATE_COLOURS["repairing"],
        "LinkIdle": APPENDIX_D_STATE_COLOURS["idle"],
        "LinkStandby": APPENDIX_D_STATE_COLOURS["warm_standby"],
        "LinkPM": APPENDIX_D_STATE_COLOURS["maintenance"],
        # `ChangeLinkStatus` paints LinkDone red, like LinkDown; without this entry a Done link fell
        # through to the resting colour.
        "LinkDone": APPENDIX_D_STATE_COLOURS["repairing"],
    }
    _LINK_STATE_HELP = {
        "LinkUp": "Up — functional (the normal, resting colour)",
        "LinkDown": "Down — failed or cut (red)",
        "LinkIdle": "Idle — constrained by an upstream dependency "
                    "(sienna/brown)",
        "LinkStandby": "Standby — reserved (blue)",
        "LinkPM": "PM — endpoint undergoing preventive maintenance "
                  "(orange)",
        "LinkDone": "Done — endpoint's life exhausted this trial (red)",
    }

    def update_live_states(self: DiagramView, blocks, cap_data: tuple | None = None,  # type: ignore[misc]
                           model=None, phase: int | None = None) -> None:
        """Re-colour every block/node/link/event/hierarchy item from its
        live status during a simulation (the Simulation View animation).
        `blocks` accepts either a {block_id: block} mapping or an
        iterable of block objects (the stepped debugger passes a
        list).

        `model` (the engine's live SimulationModel): the canvas's own
        blocks/nodes/links/etc. are a separate parse of the same .rbd
        rows, so node/link/event/hierarchy colours must come from the
        engine model to animate during a run. When given,
        node/link/event/hier statuses (and the capacity-restricted node
        flags below) come from the engine model; without it the view's
        own objects are used.

        `phase` (the run's current phase number, 1-based): a phased running block's stress shade is
        read from its own value for that phase, as Raptor 7's `ChangeRunningColor(phaseValue[
        phaseNumber])` does; without it the engine's last computed stress is used, which only
        catches up when the block's own process next recomputes it.

        `cap_data` (when capacity analysis is enabled): (node_flow,
        node_capacity) -- paints the live FLOW under the Start marker and
        the capacity-restricted nodes, Raptor 7 `ShowFlow` style (see its
        own number formatting). `None` clears the readouts."""
        if not isinstance(blocks, dict):
            blocks = {b.id: b for b in blocks}

        def _key(v):
            return v.value if hasattr(v, "value") else str(v)

        nodes_src: Any
        hiers_src: Any
        events_src: Any
        links_src: Any
        if model is None:
            nodes_src = self.nodes
            hiers_src = self.hiers
            events_src = getattr(self, "events", None) or {}
            links_src = self.links
        elif isinstance(model, dict):
            # Plain per-status maps (background-run worker -> main
            # thread: only plain data crosses the thread boundary).
            nodes_src = model.get("nodes") or {}
            hiers_src = model.get("hiers") or {}
            events_src = model.get("events") or {}
            links_src = model.get("links") or {}
        else:
            nodes_src = model.nodes_by_id
            hiers_src = model.hiers_by_id
            events_src = model.events_by_id
            links_src = model.links_by_id

        for bid, item in self._block_items.items():
            block = blocks.get(bid)
            if block is None:
                continue
            status = getattr(block, "op_status", None)
            key = req(status).value if hasattr(status, "value") else str(status)
            colour = self._LIVE_STATE_COLOURS.get(key, COLOUR["surface"])
            help_txt = self._LIVE_STATE_HELP.get(key, key)
            name = getattr(block, "name", "") or f"Block {bid}"
            # Raptor 7: a phased component that is functioning
            # (Running) shows one of three green shades for its stress
            # (nominal/under/over) -- "stress values only affect those
            # components that are running". Non-phased blocks (and
            # phased-but-not-Running ones) keep the plain state colour.
            if key == "Running" and getattr(block, "uses_phasing", False):
                runtime = getattr(block, "run_state", None)
                stress = runtime.phase_stress if runtime is not None else 1.0
                values = getattr(block, "phase_value", None)
                if phase is not None and values and 1 <= phase <= len(values):
                    stress = values[phase - 1]
                if stress < 1.0:
                    colour = self._STRESS_COLOURS["under"]
                    help_txt = f"Running — under-stress (stress={stress:.3g})"
                elif stress > 1.0:
                    colour = self._STRESS_COLOURS["over"]
                    help_txt = f"Running — over-stress (stress={stress:.3g})"
                else:
                    colour = self._STRESS_COLOURS["nominal"]
                    help_txt = f"Running — nominal (stress={stress:.3g})"
            if key == "Standby":
                sb = getattr(block, "sb_stress", 0.0) or 0.0
                if sb > 1.0:
                    colour = self._STANDBY_STRESS_COLOURS["over"]
                    help_txt = f"Standby — UP: reserved (standby stress {sb:.3g}, over 1)"
                elif 0.0 < sb < 1.0:
                    colour = self._STANDBY_STRESS_COLOURS["under"]
                    help_txt = f"Standby — UP: reserved (standby stress {sb:.3g}, under 1)"
            item.set_live_fill(
                colour,
                f"{name} — {help_txt}")
            active = getattr(block, "active_status", None)
            active_key = req(active).value if hasattr(active, "value") else active
            item.set_link_cut(
                "linked" if active_key == "Linked" else
                "cut" if active_key == "Cut" else None)
            runtime = getattr(block, "run_state", None)
            item.set_fev_hatch(bool(getattr(runtime, "fev_induced_by_dependency", False)))

        for nid, nitem in self._node_items.items():
            node = nodes_src.get(nid)
            if node is None:
                continue
            status = getattr(node, "status", None)
            key = _key(status)
            colour = self._NODE_STATE_COLOURS.get(key, COLOUR["surface"])
            help_txt = self._NODE_STATE_HELP.get(key, key)
            name = getattr(node, "node_name", "") or f"Node {nid}"
            nitem.set_live_fill(colour, f"{name} — {help_txt}")
            nitem.set_live_capacity_text(None)

        for hid, hitem in self._hier_items.items():
            hier = hiers_src.get(hid)
            if hier is None:
                continue
            status = getattr(hier, "status", None)
            key = _key(status)
            colour = self._NODE_STATE_COLOURS.get(key, COLOUR["surface"])
            help_txt = self._NODE_STATE_HELP.get(key, key)
            name = getattr(hier, "name", "") or f"Hierarchy {hid}"
            hitem.set_live_fill(colour, f"{name} — {help_txt}")
            # The DSI triangles are live too. Raptor's ChangeDSIStatus
            # colours them from the descendant counters (dsiState), with
            # White/Black overriding when the out node is Linked/Cut
            out_node = nodes_src.get(getattr(hier, "out_id", 0)) \
                if hasattr(nodes_src, "get") else None
            active = getattr(out_node, "active_status", None)
            active_key = req(active).value if hasattr(active, "value") else active
            dsi_key = _key(getattr(hier, "dsi_state", status))
            info = {"state": dsi_key,
                    "nodes": 0,
                    "down": getattr(hier, "num_currently_failed", 0),
                    "degraded": getattr(hier, "dsi_yellows", 0)}
            if active_key in ("Linked", "Cut"):
                info = dict(info, state=active_key)
            hitem.set_dsi(info, COLOUR)

        for event_item in self._event_items.values():
            event = events_src.get(event_item._event_id)
            if event is None:
                continue
            status = getattr(event, "op_status", None)
            key = _key(status)
            colour = self._EVENT_STATE_COLOURS.get(key, COLOUR["surface"])
            help_txt = self._EVENT_STATE_HELP.get(key, key)
            name = getattr(event, "name", "") or f"Event {event_item._event_id}"
            event_item.set_live_fill(colour, f"{name} — {help_txt}")
            active = getattr(event, "active_status", None)
            active_key = req(active).value if hasattr(active, "value") else active
            event_item.set_link_cut(
                "linked" if active_key == "Linked" else
                "cut" if active_key == "Cut" else None)

        # Links have no per-id item map (unlike blocks/nodes/hiers/
        # events) -- `retheme()` finds them the same way, by scanning
        # scene items, so this matches that already-accepted pattern
        # rather than adding a new persistent map for one element type.
        if isinstance(links_src, dict):
            links_by_num = dict(links_src)
        else:
            links_by_num = {source_link.object_num: source_link for source_link in links_src}
        if model is not None:
            links_by_num = self._engine_links_by_file_id(links_by_num)
        if links_by_num:
            for sitem in self._scene.items():
                if not isinstance(sitem, _LinkItem):
                    continue
                link = links_by_num.get(sitem._link_num)
                if link is None:
                    continue
                status = getattr(link, "status", None)
                key = _key(status)
                if key == "LinkUp":
                    # Raptor paints up links black -- the link's normal
                    # resting look, not a state colour (Raptor 7
                    # ChangeLinkStatus's own colour CASE). Our canvas's
                    # default/resting pen colour is the same token.
                    colour = COLOUR["muted"]
                else:
                    colour = self._LINK_STATE_COLOURS.get(
                        key, COLOUR["muted"])
                help_txt = self._LINK_STATE_HELP.get(key, key)
                sitem.set_live_fill(colour, help_txt)

        # BOUNDARY-LINE COLOURING: the hierarchy's OUTPUT boundary
        # links have no ReconfigureNode driver in the engine (after
        # RunEFPA's fix-up no block's EFPAnode is a hierarchy's In node),
        # so colour them here at paint time, mirroring the oval: Down ->
        # LinkDown red, Degraded/Idle -> LinkIdle sienna, PM -> PM
        # orange, AllUp/Standby -> Up (the resting muted). Engine
        # link.status is untouched -- display-only. Runs after the
        # engine-status link loop above so this wins for boundary links.
        # Keyed by the `_key()` string form (NodeStatus.value).
        boundary_map = {"Down": APPENDIX_D_STATE_COLOURS["repairing"],
                        "NodeIdle": APPENDIX_D_STATE_COLOURS["idle"],
                        "NodePM": APPENDIX_D_STATE_COLOURS["maintenance"],
                        "Degraded": APPENDIX_D_STATE_COLOURS["idle"],
                        "NodeStandby": APPENDIX_D_STATE_COLOURS["warm_standby"],
                        "AllUp": COLOUR["muted"]}
        # Only the ovals ON SCREEN this level have boundary links in the
        # scene; scanning every hierarchy in the model (217 on the BESS
        # model) made each frame rescan 217 x 5,184 LINK_DATA rows.
        boundary_links = self._hier_boundary_links()
        link_items: dict[int, list[_LinkItem]] = {}
        for sitem in self._scene.items():
            if isinstance(sitem, _LinkItem) and sitem._link_num is not None:
                link_items.setdefault(sitem._link_num, []).append(sitem)
        for hid in self._hier_items:
            hier = hiers_src.get(hid)
            if hier is None:
                continue
            s_key = _key(getattr(hier, "status", None))
            colour = boundary_map.get(s_key, COLOUR["muted"])
            for num in boundary_links.get(hid, ()):
                link = links_by_num.get(num)
                if link is None:
                    continue
                # Only override links the engine considers Up -- a
                # Down/Idle/PM engine status already paints via the
                # link loop above and wins.
                engine_key = _key(getattr(link, "status", None))
                if engine_key not in (None, "LinkUp"):
                    continue
                for link_item in link_items.get(num, ()):
                    link_item.set_live_fill(colour)

        # Live FLOW readout on the nodes (Raptor 7's `ShowFlow` /
        # `AnalyzeSystem`): the Start marker gets "Flow=26880"
        # capacity-restricted type-2 nodes get
        # the flow formatted as 1-9999 / 10K / 1.2M / 3G
        # The Out marker and ordinary nodes
        # get nothing. Raptor 7 shows FLOW only -- capacity is a
        # results-view figure, not a live label.
        if cap_data is not None:
            flow_by_id, _cap_by_id = cap_data
            for nid, nitem in self._node_items.items():
                node = nodes_src.get(nid)
                f = flow_by_id.get(nid)
                if node is None or f is None:
                    nitem.set_live_capacity_text(None)
                    continue
                node_type = getattr(node, "type_node", 0)
                if node_type == 1:
                    nitem.set_live_capacity_text(f"Flow={f}")
                elif node_type == 2 and getattr(node, "cap_node", False):
                    nitem.set_live_capacity_text(_flow_label(f))
                else:
                    nitem.set_live_capacity_text(None)

        # Live-state retention: cache the live sources so a level change
        # (enter/go_up/go_home -- which rebuilds the scene with freshly
        # built, default-coloured items) can re-apply the run's current
        # colours to the new level's items without waiting for the next
        # engine event.
        self._live_state_cache = (blocks if isinstance(blocks, dict)
                                  else {b.id: b for b in blocks},
                                  cap_data, model, phase)

    def _engine_links_by_file_id(self: DiagramView, by_engine_num: dict  # type: ignore[misc]
                                 ) -> dict:
        """Re-key the engine's links by their FILE id. The engine numbers
        links 1..N in file order, as Raptor 7 does on load
        (`core.engine.load`); the canvas knows each link by the id in the
        file. A model whose link ids are not 1..N (ids come from one pool
        shared with blocks and nodes, so HospitalPower's start at 18)
        painted engine link 18 -- the one into ATS_A -- on the canvas link
        with file id 18, Start -> Supply_Split."""
        rbd = getattr(self, "_rbd_ref", None)
        sec = rbd.sections.get("LINK_DATA") if rbd is not None else None
        if sec is None:
            return by_engine_num
        file_ids = getattr(self, "_link_file_ids_cache", None)
        if file_ids is None:
            file_ids = []
            for row in sec.rows:
                try:
                    file_ids.append(int(float(row.get("ID", "0"))))
                except ValueError:
                    file_ids.append(0)
            self._link_file_ids_cache = file_ids
        # The overwhelmingly common case: the file's ids ARE 1..N, so
        # the engine's own numbering already is the file numbering.
        if len(file_ids) == len(by_engine_num) and all(
                fid == n for n, fid in enumerate(file_ids, start=1)):
            return by_engine_num
        return {file_ids[n - 1]: link for n, link in by_engine_num.items()
                if 1 <= n <= len(file_ids)}

    def _invalidate_live_caches(self: DiagramView) -> None:  # type: ignore[misc]
        """A model edit can add, remove or re-endpoint a link; the
        boundary map belongs to the previous rows. `model_changed` fires
        only for real (non-layout) edits, so this stays cheap."""
        self._hier_boundary_cache = None
        self._link_file_ids_cache = None

    def _hier_boundary_links(self: DiagramView) -> dict[int, tuple[int, ...]]:  # type: ignore[misc]
        """`{hierarchy id: LINK_DATA ids leaving it}` -- built once per
        model, not once per hierarchy per frame. This is the data behind
        Raptor 7's boundary-link colouring (the links that follow the
        oval's colour). The link INTO a hierarchy does not: Raptor 7
        keeps it in its own state (black while its source is up) when the
        hierarchy goes red, checked against its Failure Effects View on
        `FevPhasing3` (a failed `650path` is red, the link into it stays
        black, the link out turns red). Engine status untouched;
        display-only. Invalidated by `DiagramView._invalidate_live_caches`
        on a model edit / new load."""
        cached = getattr(self, "_hier_boundary_cache", None)
        if cached is not None:
            return cached
        out: dict[int, list[int]] = {}
        rbd = getattr(self, "_rbd_ref", None)
        sec = rbd.sections.get("LINK_DATA") if rbd else None
        for row in (sec.rows if sec else []):
            if row.get("FromType") != "Hier":
                continue
            try:
                hid = int(float(row.get("FromID", 0) or 0))
                lid = int(float(row.get("ID", "")))
            except (TypeError, ValueError):
                continue
            out.setdefault(hid, []).append(lid)
        frozen = {hid: tuple(nums) for hid, nums in out.items()}
        self._hier_boundary_cache = frozen
        return frozen

    def _hier_boundary_link_nums(self: DiagramView, hier_id: int) -> list[int]:  # type: ignore[misc]
        """LINK_DATA ids of one hierarchy's OUTPUT boundary links (kept
        for callers/tests; the live loop uses `_hier_boundary_links` so
        it never rescans the rows)."""
        return list(self._hier_boundary_links().get(hier_id, ()))

    def _reapply_live_states(self: DiagramView) -> None:  # type: ignore[misc]
        """Re-paint the current level from the cached live state (set by
        `update_live_states` during an active run). Called by
        `_set_level` after a scene rebuild mid-simulation; a no-op when
        no live run is in progress (cache absent)."""
        cached = getattr(self, "_live_state_cache", None)
        if not cached:
            return
        self.update_live_states(cached[0], cap_data=cached[1],
                                model=cached[2], phase=cached[3])

    def clear_live_states(self: DiagramView, keep_result_visuals: bool = True):  # type: ignore[misc]
        """Restore the themed (non-simulation) appearance after the live
        run view ends -- e.g. leaving the Simulate ribbon tab.

        `keep_result_visuals` (default True): the last run's weak-link
        bands are a result view, not animation -- they must survive
        ribbon switches and level traversal while the model is
        unchanged. Only an explicit weak-colour toggle-off or a new
        model load passes False to forget them."""
        if not keep_result_visuals:
            self._last_result_visuals = None
        # The live-state cache belongs to the running animation -- a
        # finished/stopped run must not re-paint fresh items with its
        # stale end-state on the next level traversal.
        self._live_state_cache = None

        def _restore():
            self.retheme()
            # Result-overlay badges ($/!/capacity gauge) are separate,
            # explicit item-level state (not touched by `retheme()`) --
            # clear them first so a turned-off overlay doesn't leave
            # stale badges behind; if `vis` below re-applies an
            # overlay, that call's own per-item loop re-sets them
            # fresh right after, overwriting this clear correctly.
            for item in self._block_items.values():
                item.set_result_badge(None, None)
                item.set_anal_label(None)
            for node_item in self._node_items.values():
                node_item.set_capacity_badge(None, None)
                node_item.set_live_capacity_text(None)
                node_item.set_anal_label(None)
                if hasattr(node_item, "_inner_label"):
                    node_item._inner_label.setToolTip(node_item._inner_tip())
            for event_item in self._event_items.values():
                event_item.set_anal_label(None)
            for hier_item in self._hier_items.values():
                hier_item.set_anal_label(None)
            self._reapply_result_visuals()
        self._loading = True
        try:
            _restore()
        finally:
            self._loading = False
        for item in self._block_items.values():
            item.set_link_cut(None)
            item.set_fev_hatch(False)
        for event_item in self._event_items.values():
            event_item.set_link_cut(None)

    def clear_result_overlays(self: DiagramView) -> None:  # type: ignore[misc]
        """Remove EVERY result overlay, atomically.

        The ribbon has one overlay slot, but the canvas used to carry
        independent item state: switching Capacity -> Cost left capacity
        gauges and node colours behind, and a silently-unchecked action
        could never clear them. Clear badges, gauges, live text and live
        fills, then restore each item's base tooltip and themed look.
        """
        self._last_result_visuals = None
        self._weak_colors_on = False
        for item in self._block_items.values():
            item.set_result_badge(None, None)
            item.set_anal_label(None)
            if hasattr(item, "_live_colour"):
                item._live_colour = None
            item.setToolTip((getattr(item, "_dep_badge", None) or {}).get(
                "tooltip", ""))
        for node_item in self._node_items.values():
            node_item.set_capacity_badge(None, None)
            node_item.set_live_capacity_text(None)
            node_item.set_anal_label(None)
            if hasattr(node_item, "_inner_label"):
                node_item._inner_label.setToolTip(node_item._inner_tip())
            if hasattr(node_item, "_live_colour"):
                node_item._live_colour = None
            node_item.setToolTip((getattr(node_item, "_dep_badge", None) or {}).get(
                "tooltip", ""))
        for event_item in self._event_items.values():
            event_item.set_anal_label(None)
            if hasattr(event_item, "_live_colour"):
                event_item._live_colour = None
        for hier_item in self._hier_items.values():
            hier_item.set_anal_label(None)
            if hasattr(hier_item, "_live_colour"):
                hier_item._live_colour = None
        self.retheme()
        self.update()

    def _reapply_result_visuals(self: DiagramView) -> None:  # type: ignore[misc]
        """Re-apply the last run's result overlays to the items on the
        canvas now.

        One dispatch for every path that wipes or replaces the scene
        items while the model is unchanged. `retheme()` resets each
        item to its themed look, so every one of these has to put the
        overlays back or the results silently disappear:
        `_set_level` (traversal), `clear_live_states` (leaving the
        Simulate tab) and `_retheme_current` (a light/dark flip -- which
        used to lose them outright, and was the one path with no
        re-apply at all)."""
        vis = getattr(self, "_last_result_visuals", None)
        if vis:
            if vis["kind"] == "weak":
                self.apply_weak_colors(
                    vis["metric"], vis["gy"], vis["yr"],
                    metric_name=vis["metric_name"],
                    node_metric=vis.get("node_metric"),
                    event_metric=vis.get("event_metric"),
                    hier_metric=vis.get("hier_metric"),
                    digits=vis.get("digits", 3))
            elif vis["kind"] == "capacity":
                self.apply_capacity_colors(vis["usage"], vis["capability"])
            elif vis["kind"] == "cost":
                self.apply_cost_colors(vis["cost"],
                                       spread=vis.get("spread", False))
            elif vis["kind"] == "spares":
                self.apply_spares_colors(vis["exhausted_frac"],
                                         vis["sparing_cost"])
            elif vis["kind"] == "importance":
                # Missing branch: entering a hierarchy rebuilt the items and
                # repainted only the four kinds above, so the importance
                # markers vanished on entry; coming back out re-applied
                # nothing because this kind matched no case either.
                self.apply_importance_colors(
                    vis["values"],
                    measure=vis.get("measure", "Birnbaum"),
                    metric_name=vis.get("metric_name", "Availability"))
        last_hier_dsi = getattr(self, "_last_hier_dsi", None)
        if last_hier_dsi:
            self.apply_hier_dsi(last_hier_dsi)

    #: Last result visuals: weak-link bands and hierarchy DSI dots
    #: survive traversing in/out of levels as long as the model is
    #: unchanged -- level rebuilds re-apply them from here.
    _last_result_visuals = None
    #: Live run state cache (blocks, cap_data, model) -- see
    #: `update_live_states`'s tail and `_reapply_live_states`.
    _live_state_cache: tuple | None = None

    def apply_weak_colors(self: DiagramView, metric: dict[int, float],  # type: ignore[misc]
                          gy: float, yr: float,
                          metric_name: str = "Availability", *,
                          node_metric: dict[int, float] | None = None,
                          event_metric: dict[int, float] | None = None,
                          hier_metric: dict[int, float] | None = None,
                          digits: int = 3):
        """Weak Link View colouring and value labels for every element.

        Raptor 7 colours blocks, nodes, events and hierarchy ovals and
        writes the selected metric under each (`ShowAnalColor`,
        `BlockAoText`/`NodeAoText`/`EventAoText`). Green >= GY, yellow
        in [YR, GY), red < YR. `digits` is the user's display precision
        (default 3, as Raptor 7's `REALTOSigFigSTR(..., 3)`).
        """
        c = ThemeManager.instance().colour
        node_metric = node_metric or {}
        event_metric = event_metric or {}
        hier_metric = hier_metric or {}
        digits = max(0, min(6, int(digits)))
        fmt = (lambda v: f"{v:.{digits}f}")
        self._last_result_visuals = {
            "kind": "weak", "metric": dict(metric),
            "node_metric": dict(node_metric),
            "event_metric": dict(event_metric),
            "hier_metric": dict(hier_metric),
            "gy": gy, "yr": yr, "metric_name": metric_name,
            "digits": digits}

        def band(v: float) -> tuple[str, str]:
            if v >= gy:
                return c["result_green"], "Green"
            if v >= yr:
                return c["result_yellow"], "Yellow"
            return c["result_red"], "Red"

        def apply(item, values: dict[int, float], oid: int, name: str) -> None:
            v = values.get(oid)
            if v is None:
                item.set_anal_label(None)
                if hasattr(item, "_inner_label"):
                    item._inner_label.setToolTip(item._inner_tip())
                return
            colour, label = band(v)
            item.set_live_fill(
                colour,
                f"Weak link — {name}: {metric_name} {v:.6g} → {label}\n"
                f"(bands: Green ≥ {gy:.6g}, Yellow ≥ {yr:.6g}, "
                f"Red < {yr:.6g})")
            shown = fmt(v)
            item.set_anal_label(
                shown,
                f"{metric_name} {shown} → {label} band\n"
                f"(Green ≥ {gy:.{digits}f}, Yellow ≥ {yr:.{digits}f}, "
                f"Red < {yr:.{digits}f})")
            if hasattr(item, "_inner_label"):
                # Hovering the S/E or k/n badge explains the analysis
                # value as well, since the badge sits inside the node.
                item._inner_label.setToolTip(
                    f"{name} — {metric_name} {shown} → {label} band")

        for bid, item in self._block_items.items():
            block = self.blocks.get(bid)
            apply(item, metric, bid,
                  getattr(block, "name", "") or f"Block {bid}")
        for nid, item in self._node_items.items():
            node = self.nodes.get(nid)
            apply(item, node_metric, nid,
                  getattr(node, "node_name", "") or f"Node {nid}")
        for eid, item in self._event_items.items():
            event = self.events.get(eid)
            apply(item, event_metric, eid,
                  getattr(event, "name", "") or f"Event {eid}")
        for hid, item in self._hier_items.items():
            hier = self.hiers.get(hid)
            apply(item, hier_metric, hid,
                  getattr(hier, "name", "") or f"Hierarchy {hid}")

    def apply_capacity_colors(self: DiagramView, usage: dict[int, float],  # type: ignore[misc]
                              capability: dict[int, float] | None = None):
        """Results Overlay: colour flow-nodes by average utilization
        (`RunReportData.capacity.nodes[nid].usage`, already
        engine-computed as ave_flow/ave_cap). Near-max usage is the
        danger reading here, so this is the one overlay where high is
        red, unlike weak-link's high-is-good bands. Threshold-free
        (fixed 70%/90% bands): utilization is already a natural 0-100%
        scale."""
        c = ThemeManager.instance().colour
        capability = capability or {}
        self._last_result_visuals = {
            "kind": "capacity", "usage": dict(usage),
            "capability": dict(capability)}
        for nid, item in self._node_items.items():
            u = usage.get(nid)
            if u is None:
                continue
            if u >= 0.9:
                colour, band = c["result_red"], "Red"
            elif u >= 0.7:
                colour, band = c["result_yellow"], "Yellow"
            else:
                colour, band = c["result_green"], "Green"
            node = self.nodes.get(nid)
            name = getattr(node, "node_name", "") or f"Node {nid}"
            cap_txt = (f", capability {capability[nid] * 100:.1f}%"
                      if nid in capability else "")
            item.set_live_fill(
                colour,
                f"Capacity — {name}: usage {u * 100:.1f}%{cap_txt} "
                f"→ {band}\n(bands: Green < 70%, Yellow < 90%, Red ≥ 90%)")
            item.set_capacity_badge(u, colour)

    def apply_cost_colors(self: DiagramView, cost_by_block: dict[int, float],  # type: ignore[misc]
                          spread: bool = False):
        """Results Overlay: colour BLOCKS by total life-cycle cost
        (`RunReportData.cost.blocks[bid].total` -- purchase+operating+
        sparing+disposal). No natural 0-100% scale for a dollar figure
        across arbitrary models, so bands are relative to this model's
        own most expensive block (>= 2/3 of max = Red, >= 1/3 = Yellow)
        rather than a fixed threshold that would mean nothing from one
        model to the next. `spread` bands by quartile instead -- the
        dearest quarter Red, the cheapest quarter Green -- for a model
        where one very expensive block makes every other one Green."""
        import statistics
        c = ThemeManager.instance().colour
        self._last_result_visuals = {
            "kind": "cost", "cost": dict(cost_by_block), "spread": spread}
        max_cost = max(cost_by_block.values(), default=0.0)
        shown = sorted(cost for bid, cost in cost_by_block.items()
                       if bid in self._block_items)
        spread = spread and len(shown) >= 2 and shown[0] < shown[-1]
        if spread:
            q1, _median, q3 = statistics.quantiles(shown, n=4,
                                                   method="inclusive")
            if q1 == q3:            # most blocks tie: split at the ends
                q1, q3 = shown[0], shown[-1]
            basis = (f"(spread by quartile: Red ≥ ${q3:,.2f}, "
                     f"Green ≤ ${q1:,.2f})")
        else:
            basis = (f"(relative to this model's most expensive "
                     f"block, ${max_cost:,.2f})")

        def band(cost: float) -> str:
            if spread:
                return ("Red" if cost >= q3 else
                        "Green" if cost <= q1 else "Yellow")
            frac = (cost / max_cost) if max_cost > 0 else 0.0
            return ("Red" if frac >= 2 / 3 else
                    "Yellow" if frac >= 1 / 3 else "Green")

        for bid, item in self._block_items.items():
            cost = cost_by_block.get(bid)
            if cost is None:
                continue
            band_name = band(cost)
            colour = c[f"result_{band_name.lower()}"]
            block = self.blocks.get(bid)
            name = getattr(block, "name", "") or f"Block {bid}"
            item.set_live_fill(
                colour,
                f"Cost — {name}: ${cost:,.2f} total life-cycle cost "
                f"→ {band_name}\n{basis}")
            item.set_result_badge("$", colour)

    def apply_importance_colors(self: DiagramView, values: dict[int, float],  # type: ignore[misc]
                                *, measure: str = "Birnbaum",
                                metric_name: str = "Availability"):
        """Results Overlay: colour BLOCKS by an importance measure.

        This one is a RANKING, not a health reading, and the bands say so:
        red is the block with the most leverage on the system -- the one to
        fix first -- not the one that is worst today. Weak Links answers
        "what is weak now" against absolute thresholds; importance answers
        "what would improve the system most", which has no absolute scale
        at all, so the bands are relative to the largest value in this run
        (>= 2/3 Red, >= 1/3 Yellow) exactly as the Cost overlay bands
        relative to the dearest block. The rank is badged on the element
        and the value printed under it.
        """
        c = ThemeManager.instance().colour
        self._last_result_visuals = {
            "kind": "importance", "values": dict(values),
            "measure": measure, "metric_name": metric_name}
        top = max((v for v in values.values() if v is not None), default=0.0)
        ranked = sorted((v for v in values.values() if v is not None),
                        reverse=True)
        for bid, item in self._block_items.items():
            v = values.get(bid)
            if v is None:
                continue
            frac = (v / top) if top > 0 else 0.0
            band = ("Red" if frac >= 2 / 3 else
                    "Yellow" if frac >= 1 / 3 else "Green")
            colour = c[f"result_{band.lower()}"]
            block = self.blocks.get(bid)
            name = getattr(block, "name", "") or f"Block {bid}"
            rank = ranked.index(v) + 1
            item.set_live_fill(
                colour,
                f"Importance — {name}: {measure} {v:.6g} on {metric_name} "
                f"(rank {rank} of {len(ranked)}) → {band}\n"
                f"{frac * 100:.0f}% of the largest value ({top:.6g}); "
                f"bands: Red >= 2/3 of the largest, Yellow >= 1/3, "
                f"Green below\n"
                f"High importance means the system follows this block — it "
                f"is not a fault reading.")
            item.set_result_badge(str(rank), colour)
            item.set_anal_label(
                f"{v:.3f}",
                f"{measure} {v:.6g} — how far the system's "
                f"{metric_name.lower()} moves with this block")

    def apply_spares_colors(self: DiagramView, exhausted_frac: dict[int, float],  # type: ignore[misc]
                            sparing_cost: dict[int, float] | None = None):
        """Results Overlay: colour blocks by the fraction of run time
        spent in `Done` status (spares/life exhausted, unable to repair
        -- `RunReportData.weak_stats[bid]["done"]`, the same per-state
        data the Weak Link breakdown table uses). Binary-ish banding (any
        exhausted time at all is a real risk signal, not a smooth
        gradient like Capacity/Cost): green at exactly 0%, red for any
        exhausted time, yellow never used here -- deliberately simpler
        than the other two bandings since "sometimes ran out of spares"
        is closer to a flag than a continuum. `sparing_cost` (optional,
        `BlockCostRow`'s sparing total) is shown in the tooltip alongside
        the time fraction so both signals are visible without a second
        overlay."""
        c = ThemeManager.instance().colour
        sparing_cost = sparing_cost or {}
        self._last_result_visuals = {
            "kind": "spares", "exhausted_frac": dict(exhausted_frac),
            "sparing_cost": dict(sparing_cost)}
        for bid, item in self._block_items.items():
            frac = exhausted_frac.get(bid)
            if frac is None:
                continue
            colour, band = ((c["result_red"], "Red") if frac > 0.0
                            else (c["result_green"], "Green"))
            block = self.blocks.get(bid)
            name = getattr(block, "name", "") or f"Block {bid}"
            cost_txt = (f", ${sparing_cost[bid]:,.2f} sparing cost"
                       if bid in sparing_cost else "")
            item.set_live_fill(
                colour,
                f"Spares — {name}: {frac * 100:.2f}% of run time "
                f"spares/life exhausted{cost_txt} → {band}")
            item.set_result_badge("!" if frac > 0.0 else None, colour)

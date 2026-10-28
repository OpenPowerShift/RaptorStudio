"""Scoped port of `RunEFPA`'s structural-graph-building loops: given a loaded `.rbd` file's parsed `LINK_DATA`, derive each
node's `in_paths_array`/`propagates_to_node_ids`/`propagates_to_link_ids` and each
block/event's `downstream_link_id`.

Two passes, matching the source's two-loop structure:

1. Pass 1 -- for every node except End, walk each
   outgoing connection forward through any `RBDBlock`/`RBDEvent` targets to
   the next real `RBDNode`, giving `next_node_ids`/`next_node_link_ids`.
   Every block/event passed over during this walk gets its own `downstream_link_id` set
   to the same final `(node, link)` pair found.

2. `_run_test_fail` (driving `RBDNodeObj.TestFail`) -- for each Regular(2)/hierarchy-Out(5) node in
   turn as `TestElement`, a recursive reachability walk determines the real
   `propagates_to_node_ids`/`propagates_to_link_ids`: not simply the node's own NRBD
   targets, but the outer boundary of everything reachable through a chain of
   "fully-starved" intermediate nodes. A node with only one incoming
   connection (a trivial relay, e.g. a hierarchy's internal pass-through) is
   skipped over, its own status change never independently logged, in favor of
   whatever real node is on the other side.

Narrower than the source in several ways, all raising `NotImplementedError`
rather than guessing:
- No capacity analysis (`CapConnectTo`/`FindCapPaths`
  and everything downstream).
- No cold-standby priority sorting (`IdAndSortColds`) or
  `simDetails` debug logging.
- No nested hierarchies (a hierarchy whose own external connection is itself
  another hierarchy, or a node whose forward walk lands on an `RBDHier`
  target).
- `TestFail`'s `LoopError` path (the same origin node reached twice during its
  own recursive walk) raises rather than aborting the whole RunEFPA pass.
- A `Start` (`type_node=1`) node is never an origin and never a NRBD/TestFail
  target, so it is left uncomputed.
- Only one outgoing connection per block/event is supported (`In=1, Out=1` in
  every `BLOCK_DATA`/`EVENT_DATA`); multiple outgoing links raise.

Checked against `tests/golden/Cascade` and `tests/golden/HierarchyExample`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from raptor.core.engine.errors import UnrunnableModel
from raptor.core.engine.model_checks import ModelProblem
from raptor.core.model import Link, RBDBlock, RBDEvent, RBDHier, RBDNode


def _drop_surplus_outgoing(links_by_id: dict[int, Link]) -> None:
    """Remove every surplus outgoing link of a block/event origin, keeping
    the lowest-ID one. Node origins keep all their outgoing links, since
    parallel paths are legitimate there."""
    seen: set[tuple[str, int]] = set()
    doomed: list[int] = []
    for lid in sorted(links_by_id):
        link = links_by_id[lid]
        if link.connect_f_ref in ("RBDBlock", "RBDEvent"):
            key = (link.connect_f_ref, link.connect_from_id)
            if key in seen:
                doomed.append(lid)
            else:
                seen.add(key)
    for lid in doomed:
        del links_by_id[lid]


#: `(kind, id) -> the element's name`, used only to make an engine-level
#: error legible: these ids appear on no diagram, so an error naming one
#: tells the user nothing about which block to go and look at.
NameResolver = Callable[[str, int], str]


def _make_name_resolver(
    nodes_by_id: dict[int, RBDNode],
    blocks_by_id: dict[int, RBDBlock],
    events_by_id: dict[int, RBDEvent],
    hiers_by_id: dict[int, RBDHier],
) -> NameResolver:
    tables: dict[str, Mapping[int, object]] = {
        "RBDNode": nodes_by_id,
        "RBDBlock": blocks_by_id,
        "RBDEvent": events_by_id,
        "RBDHier": hiers_by_id,
    }

    def resolve(kind: str, obj_id: int) -> str:
        obj = tables.get(kind, {}).get(obj_id)
        return getattr(obj, "name", "") or ""

    return resolve


def _index_outgoing(links_by_id: dict[int, Link]) -> dict[tuple[str, int], list[Link]]:
    index: dict[tuple[str, int], list[Link]] = {}
    for link in links_by_id.values():
        index.setdefault((link.connect_f_ref, link.connect_from_id), []).append(link)
    return index


def _describe(names: NameResolver | None, kind: str, obj_id: int) -> str:
    """`Block 3 'Bus_UR'` -- how an element reads to a user. Falls back to
    the bare kind when no resolver was supplied or the name is blank."""
    who = names(kind, obj_id) if names is not None else ""
    if who:
        return f"{kind} id={obj_id} '{who}'"
    return f"{kind} id={obj_id}"


def _walk_forward(
    kind: str,
    obj_id: int,
    outgoing: dict[tuple[str, int], list[Link]],
    hiers_by_id: dict[int, RBDHier],
    names: NameResolver | None = None,
) -> tuple[int, int]:
    """Follow a block/event/node's outgoing connection forward, through any
    further `RBDBlock`/`RBDEvent` targets (a block or event connecting directly
    into another block/event, no intervening node), until landing on a real
    `RBDNode`. Returns `(node_id, link_id)` -- the `NRBDDownstreamArray`/
    `NRBDPathArray` entry `RunEFPA` computes via its `WHILE lookingForNode`
    loop.

    A `RBDHier` target (a node connecting directly into a hierarchy, no
    intervening `RBDNode`) redirects to that hierarchy's own `in_id` node
    (`type_node=4`) and returns that as the landing node. `RunEFPA`'s walk
    stops as soon as `ToReference` isn't `RBDBlock`/`RBDEvent` and stores the
    raw target id; for a `RBDHier` target that id is the hierarchy's own id,
    but every downstream consumer treats it as an `RBDNode` id. A hierarchy's
    In node is a real `RBDNode` at the hierarchy boundary, and
    `_run_test_fail` already continues a walk through a `type_node=4` node
    (redirecting to the hierarchy's `out_id`), so resolving to `in_id` here
    lets the rest of the machinery pick it up correctly.

    Raises `NotImplementedError` if the walk reaches anything other than
    `RBDNode`/`RBDBlock`/`RBDEvent`/`RBDHier`.

    The walk is iterative on purpose: it follows every block/event in a
    series chain, and a 500-block model would otherwise overflow Python's
    recursion limit while *loading* (before the simulation even starts).
    """
    where = f"{kind} id={obj_id}'s forward walk"
    visited: set[tuple[str, int]] = set()
    while True:
        key = (kind, obj_id)
        if key in visited:
            raise ValueError(
                f"{_describe(names, kind, obj_id)} is part of a forward-link "
                f"cycle -- remove the loop (connect it to a node instead), "
                f"then re-validate")
        visited.add(key)
        links = outgoing.get(key)
        if not links:
            # NAME the element -- and name THIS one, not whatever the walk
            # started from: a dead end can be several hops downstream, and the
            # bare "RBDBlock id=17" that used to be reported is not
            # actionable on a canvas of named blocks. This error arrives at RUN
            # time, so it is the user's only clue. The same condition is a
            # named validation error before the run starts
            # (`core.input_rules`: Raptor 7's 'must connect to something'), so say so.
            raise ValueError(
                f"{_describe(names, kind, obj_id)} has no outgoing link -- "
                f"connect it or delete it, then re-validate (Validate button): the engine cannot build the "
                f"RBD graph without it")
        # More than one outgoing link on a block/event origin can no longer
        # happen here (build_efpa_graph prunes them), but keep a deterministic
        # choice anyway.
        link = min(links, key=lambda l: l.object_num)
        if link.connect_t_ref == "RBDNode":
            return link.connect_to_id, link.object_num
        if link.connect_t_ref == "RBDHier":
            return hiers_by_id[link.connect_to_id].in_id, link.object_num
        if link.connect_t_ref in ("RBDBlock", "RBDEvent"):
            kind, obj_id = link.connect_t_ref, link.connect_to_id
            where = f"{kind} id={obj_id}'s forward walk"
            continue
        raise NotImplementedError(
            f"{where} reached a {link.connect_t_ref} target "
            "-- only RBDNode/RBDBlock/RBDEvent/RBDHier forward targets are ported"
        )


def _link_target(
    link: Link,
    outgoing: dict[tuple[str, int], list[Link]],
    hiers_by_id: dict[int, RBDHier],
    where: str,
    names: NameResolver | None = None,
) -> tuple[int, int]:
    """The `(node_id, link_id)` one outgoing link lands on: a node directly,
    a hierarchy's In node, or (through a block/event) wherever that
    element's own walk lands. See `_walk_forward`.

    `names` is threaded through so a dead end found several hops along
    still reports the element that is actually unconnected."""
    if link.connect_t_ref == "RBDNode":
        return link.connect_to_id, link.object_num
    if link.connect_t_ref == "RBDHier":
        return hiers_by_id[link.connect_to_id].in_id, link.object_num
    if link.connect_t_ref in ("RBDBlock", "RBDEvent"):
        return _walk_forward(link.connect_t_ref, link.connect_to_id,
                             outgoing, hiers_by_id, names)
    raise NotImplementedError(
        f"{where} reached a {link.connect_t_ref} target "
        "-- only RBDNode/RBDBlock/RBDEvent/RBDHier forward targets are ported"
    )


def _node_forward_targets(
    node: RBDNode,
    hiers_by_id: dict[int, RBDHier],
    outgoing: dict[tuple[str, int], list[Link]],
    names: NameResolver | None = None,
) -> tuple[list[int], list[int]]:
    """The raw `(connect_to, connect_path)` pair for one node's outgoing
    connection(s). A hierarchy Out (`type_node=5`) node's "connection" is its
    parent hierarchy's own external outgoing link(s); every other node uses its
    own `LINK_DATA` rows directly. Used by the NRBD pass; `_run_test_fail`
    reads `propagates_to_node_ids`/`path` once they are set instead of reusing this.
    """
    if node.type_node == 5:
        source_kind, source_id = "RBDHier", node.parent_id
    else:
        source_kind, source_id = "RBDNode", node.id

    # An Out node also reads its own direct outgoing links: the file
    # convention puts a hierarchy's external output on Hier-referencing links
    # (what the GUI's collapse/Connect writes), but a model can carry
    # `Out marker -> element` rows directly. RunEFPA's NRBD loop reads the
    # OUT node's own `EconnectOutOfNum` connections regardless of which side
    # of the boundary the rows were authored on.
    links = list(outgoing.get((source_kind, source_id), []))
    if node.type_node == 5:
        links += outgoing.get(("RBDNode", node.id), [])
    targets = [_link_target(link, outgoing, hiers_by_id,
                            f"node id={node.id}'s outgoing link", names)
               for link in links]
    return [t[0] for t in targets], [t[1] for t in targets]


def _run_test_fail(
    origin: RBDNode,
    nodes_by_id: dict[int, RBDNode],
    hiers_by_id: dict[int, RBDHier],
    links_by_id: dict[int, Link],
    efpa_tested: set[int],
) -> None:
    """Port of Raptor 7 driving `RBDNodeObj.TestFail`
   for one origin node -- see module
    docstring for what this computes and why it's needed. Mutates
    `origin.propagates_to_node_ids`/`propagates_to_link_ids` and adds `origin.id` to
    `efpa_tested` (so LATER origins whose own walk passes through this
    node reuse its pruned result instead of raw NRBD values -- order-
    sensitive, matching the source's own single top-to-bottom pass).
    """
    # Raptor 7: GoodPathsRequired is uniformly 1 for EVERY
    # node during this ENTIRE pass (reachability-only, NOT the node's own
    # real K-of-N config -- that's a separate, already-ported runtime
    # mechanism, nodes.determine_node_status). NumGoodPaths resets to each
    # node's own EconnectIntoNum, except the origin, seeded to exactly 1
    # (GoodPathsRequired) so its own first TestFail decrement always
    # triggers.
    for node in nodes_by_id.values():
        node.num_good_paths = len(node.in_paths_array)
    origin.num_good_paths = 1

    if origin.parent_id != 0:
        origin_hier = hiers_by_id[origin.parent_id]
        mama = origin_hier.level if origin.type_node == 5 else origin_hier.level + 1
    else:
        mama = 0

    downstream_group: list[int] = []  # link ids, accumulation order
    propagated_group: set[int] = set()  # node ids
    loop_hits = 0

    def test_fail(node_id: int) -> None:
        nonlocal loop_hits
        node = nodes_by_id[node_id]
        if node_id == origin.id:
            loop_hits += 1
            if loop_hits == 2:
                raise NotImplementedError(
                    f"TestFail loop detected at node id={node_id} -- RunEFPA's "
                    "LoopError abort path is not ported (no fixture has a cyclic RBD graph)"
                )
        node.num_good_paths -= 1
        if node.num_good_paths != 0:  # GoodPathsRequired(1) - 1 == 0
            return

        qualifies_hier_out = node.type_node == 5 and (
            hiers_by_id[node.parent_id].level >= mama or node_id == origin.id
        )
        if node.type_node == 2 or qualifies_hier_out:
            propagated_group.add(node_id)
            if node_id in efpa_tested:
                targets, paths = node.propagates_to_node_ids, node.propagates_to_link_ids
            else:
                targets, paths = node.next_node_ids, node.next_node_link_ids
            for target_id, link_id in zip(targets, paths):
                downstream_group.append(link_id)
                test_fail(target_id)
        elif node.type_node == 4:
            propagated_group.add(node_id)
            in_hier = hiers_by_id[node.parent_id]
            test_fail(in_hier.out_id)
        # elif node.type_node == 5 (disqualified): stop, no propagation.

    test_fail(origin.id)

    # `downstream_group` holds link ids; the target node id for each one is
    # re-derived from `links_by_id` here rather than reused from the walk's own
    # `targets` (`next_node_ids`/`propagates_to_node_ids`). For an ordinary
    # `RBDNode` target the value is the same either way, but for a link whose
    # own target is a `RBDHier` (a node connecting directly into a hierarchy,
    # `connect_t_ref="RBDHier"`, see `_node_forward_targets`) the raw
    # `links_by_id[lid].connect_to_id` is the hierarchy's own id, not a real
    # `RBDNode` id, so it must redirect to that hierarchy's `in_id` the same
    # way `_node_forward_targets` does. `propagated_group` (built from the
    # walk's already-redirected `target_id`s) is compared against the same
    # resolved ids.
    def _resolved_target_node_id(lid: int) -> int:
        link = links_by_id[lid]
        if link.connect_t_ref == "RBDHier":
            return hiers_by_id[link.connect_to_id].in_id
        return link.connect_to_id

    kept = [lid for lid in downstream_group if _resolved_target_node_id(lid) not in propagated_group]
    origin.propagates_to_node_ids = [_resolved_target_node_id(lid) for lid in kept]
    origin.propagates_to_link_ids = kept
    efpa_tested.add(origin.id)


def build_efpa_graph(
    nodes_by_id: dict[int, RBDNode],
    blocks_by_id: dict[int, RBDBlock],
    events_by_id: dict[int, RBDEvent],
    hiers_by_id: dict[int, RBDHier],
    links_by_id: dict[int, Link],
) -> None:
    """Mutates `nodes_by_id`/`blocks_by_id`/`events_by_id`'s objects in
    place: populates each node's `in_paths_array` (types 2/3/5) and
    `next_node_ids`/`next_node_link_ids`/`propagates_to_node_ids`/
    `propagates_to_link_ids` (types 2/5), and each block/event's `downstream_link_id`.
    See module docstring for the two-pass structure and what's out of
    scope (raises `NotImplementedError`).
    """
    # Raptor 7 tolerates a block/event with multiple outgoing links in the
    # editor but makes all but the lowest-ID one inert at runtime: RunEFPA's
    # preamble prints only the lowest-ID outgoing, and `tests/golden/
    # MultiOutLink`'s event log is byte-identical to the same model with only
    # that link. Prune surplus block/event-outgoing links here, before any
    # in_paths_array/NRBD bookkeeping scans them, so downstream propagation
    # cannot see them either.
    _drop_surplus_outgoing(links_by_id)
    outgoing = _index_outgoing(links_by_id)
    # So an unconnected element is named in the error rather than reported
    # as a bare id -- these ids appear on no diagram.
    names = _make_name_resolver(nodes_by_id, blocks_by_id, events_by_id,
                                hiers_by_id)

    # "This section bypasses the hierarchy elements" (Raptor 7-
    # 3692, ValidateRBD's own preamble): every link's Econnect* fields are
    # its CONNECT endpoints with hierarchy boxes resolved THROUGH --
    # a link touching an RBDHier gets its RBDNode-side endpoint (the
    # hierarchy's In/Out node id) instead. Read back by cold-standby's
    # Switch_Failed logging (the "Unable_to_switch_to_<name>" target is
    # the Econnect source) and the UpdateConnectToInfo pass right after.
    for link in links_by_id.values():
        link.resolved_from_id = link.connect_from_id
        link.resolved_to_id = link.connect_to_id
        if link.connect_t_ref == "RBDHier" and link.connect_to_id in hiers_by_id:
            link.resolved_to_kind = "RBDNode"
            link.resolved_to_id = hiers_by_id[link.connect_to_id].in_id
        else:
            link.resolved_to_kind = link.connect_t_ref
        if link.connect_f_ref == "RBDHier" and link.connect_from_id in hiers_by_id:
            link.resolved_from_kind = "RBDNode"
            link.resolved_from_id = hiers_by_id[link.connect_from_id].out_id
        else:
            link.resolved_from_kind = link.connect_f_ref

    # Pass 1: in_paths_array + NRBD values, and every
    # block/event's own downstream_link_id (set while walking THROUGH it, not via
    # a separate per-block walk).
    #
    # `type_node=4` (a hierarchy's own In node) is included in this loop:
    # `ValidateRBD` gives an In node exactly one conceptual incoming
    # connection (`SetEconnectIntoNum(1)`), the link that feeds the hierarchy
    # from outside, and `reconfigure_node`'s status aggregation
    # (`determine_node_status`) needs `in_paths_array` populated the same way
    # as any other node. A link into an In node has `connect_t_ref="RBDHier"`/
    # `connect_to_id=<that hierarchy's own id>` on disk, not
    # `connect_t_ref="RBDNode"`/`connect_to_id=<the In node's own id>`, so
    # this needs the same Hier-redirect the rest of the module applies, not
    # the plain `RBDNode` filter used for types 2/3/5.
    # Index incoming links once. The old body scanned every link for every
    # node -- O(nodes x links), the dominant term on a large model's load.
    # Order follows `links_by_id` in both cases, matching the list
    # comprehension this replaces.
    incoming_by_node: dict[int, list[int]] = {}
    incoming_by_hier_in: dict[int, list[int]] = {}
    for link in links_by_id.values():
        if link.connect_t_ref == "RBDNode":
            incoming_by_node.setdefault(link.connect_to_id, []).append(link.object_num)
        elif link.connect_t_ref == "RBDHier":
            hier = hiers_by_id.get(link.connect_to_id)
            if hier is not None:
                incoming_by_hier_in.setdefault(hier.in_id, []).append(link.object_num)
    for node in nodes_by_id.values():
        if node.type_node == 4:
            node.in_paths_array = list(incoming_by_hier_in.get(node.id, []))
            # An In node has NRBD values: its outgoing connections are the
            # hierarchy-internal links (In -> internal block -> path node),
            # and capacity's `FindCapPaths` walks through an In node via these.
            node.next_node_ids, node.next_node_link_ids = _node_forward_targets(
                node, hiers_by_id, outgoing, names)
            continue
        node.in_paths_array = list(incoming_by_node.get(node.id, []))
        if node.type_node == 3:  # End: no downstream (ReconfigureSystem's
            continue              # own separate mechanism, see cascade.py)
        # Type 1 (Start) IS included here -- unlike TestFail/EFPA (which
        # never originate at Start and skip it), the NRBD walk covers
        # EVERY non-End node in source (Raptor 7's only guard
        # is `IF node.typeNode<>3`), and capacity analysis depends on
        # Start's own NRBD arrays (`FindCapPaths` walks THEM,
        # Raptor 7). Harmless for everything else: nothing
        # outside capacity reads a Start node's nrbd arrays.
        node.next_node_ids, node.next_node_link_ids = _node_forward_targets(
                node, hiers_by_id, outgoing, names)

    # RunEFPA's block/In-node fix-up (Raptor 7, "fixes
    # blocks connected into an IN node"): a block whose forward walk
    # lands on an In node has its EFPA values re-set to that
    # hierarchy's OUT node's own first EFPA connection. Source runs
    # this AFTER the TestFail pass (the fix-up READS the OUT node's
    # TESTFAIL-computed EFPAConnectTo/Path arrays, Raptor 7),
    # so the rewrites are DEFERRED to just below Pass 2 -- deferring
    # is also why the In node check here carries no cost.
    deferred_in_landings: list[tuple[RBDBlock | RBDEvent, RBDHier]] = []
    for block in blocks_by_id.values():
        node_id, link_id = _walk_forward(
            "RBDBlock", block.id, outgoing, hiers_by_id, names)
        if nodes_by_id[node_id].type_node == 4:
            deferred_in_landings.append(
                (block, hiers_by_id[nodes_by_id[node_id].parent_id]))
        else:
            block.downstream_link_id = link_id

    for event in events_by_id.values():
        node_id, link_id = _walk_forward(
            "RBDEvent", event.id, outgoing, hiers_by_id, names)
        if nodes_by_id[node_id].type_node == 4:
            deferred_in_landings.append(
                (event, hiers_by_id[nodes_by_id[node_id].parent_id]))
        else:
            event.downstream_link_id = link_id

    # Pass 2 (TestFail): the REAL propagates_to_node_ids/
    # propagates_to_link_ids, pruning trivial relay nodes -- see module
    # docstring. Order matters: a node tested earlier in this loop has
    # its PRUNED result reused by later origins passing through it.
    efpa_tested: set[int] = set()
    for node in nodes_by_id.values():
        if node.type_node not in (2, 5):
            continue
        _run_test_fail(node, nodes_by_id, hiers_by_id, links_by_id, efpa_tested)


    # Pass 3 -- RunEFPA's block/event / In-node fix-up (Raptor 7:
    # 295-330): a block/event whose forward walk landed on an In node
    # continues THROUGH the hierarchy -- EFPA values re-set to the
    # hierarchy's OUT node's own FIRST EFPA connection (computed by
    # Pass 2 above, exactly as in source where this section runs after
    # every node's EFPA arrays are complete):
    #
    #   hier := Child(RBDHier, node.parentID)
    #   node := Child(RBDNode, hier.outID)      {the Out node}
    #   element.SetEFPAvalues(node.EFPAConnectTo[1],
    #                         node.EFPAConnectPath[1])
    #
    # The element-to-hier link id is discarded (the OUT node's own downstream
    # link becomes the element's downstream_link_id); the element-to-hier link itself
    # is still a real connection whose status is driven by the In node's
    # in_paths aggregation. An OUT node with no EFPA connection means the
    # hierarchy's output isn't wired, so this raises a message that names the
    # fix rather than the raw crash.
    for element, hier in deferred_in_landings:
        out_node = nodes_by_id[hier.out_id]
        if not out_node.propagates_to_link_ids:
            raise UnrunnableModel([ModelProblem(
                f"hierarchy {hier.id}",
                f"{type(element).__name__} {element.id} connects into it, but its Out node "
                f"(id {out_node.id}) has no outgoing connection. Wire the hierarchy's output, "
                "or remove the hierarchy.")])
        element.downstream_link_id = out_node.propagates_to_link_ids[0]

    # Port of RunEFPA's depLink/siblings registration
    #  -- runs for every model, not capacity-gated (it lives at the
    # end of the EFPA build in the source; the capacity-only copy in
    # `capacity.analyze_capacity` is a second, idempotent registration). Every
    # link whose EFPA target is a block/event -- the element's input-side line,
    # drawn on the block's other side -- joins that element's own `downstream_link_id`'s
    # sibling group, so `ChangeLinkStatus`'s sibling mirror
    # (`nodes.set_link_status_mirrored`) recolours both
    # directions of the connection.
    #
    # Reset pass first: the fill loop below must not clear groups it is also
    # filling. A link processed late (e.g. a hierarchy's `Hier -> end` output
    # link, iterated last) would otherwise have its own `siblings_group` wiped
    # by its own reset line after earlier input-side links had joined it. The
    # source's `SetDepLink(0)` clears only depLink;
    # the family itself is accumulated by AddFamilyMember and never re-cleared.
    for link in links_by_id.values():
        link.dep_link = 0
        link.siblings_group = []
        link.has_siblings = False
    for link in links_by_id.values():
        endpoint: RBDBlock | RBDEvent | None
        if link.resolved_to_kind == "RBDBlock":
            endpoint = blocks_by_id.get(link.resolved_to_id)
        elif link.resolved_to_kind == "RBDEvent":
            endpoint = events_by_id.get(link.resolved_to_id)
        else:
            continue
        if endpoint is None or endpoint.downstream_link_id not in links_by_id:
            continue
        sister = links_by_id[endpoint.downstream_link_id]
        if sister is link:
            continue
        link.dep_link = endpoint.downstream_link_id
        sister.has_siblings = True
        if link.object_num not in sister.siblings_group:
            sister.siblings_group.append(link.object_num)

def upstream_domains(
    nodes_by_id: dict[int, RBDNode],
    blocks_by_id: dict[int, RBDBlock],
    events_by_id: dict[int, RBDEvent],
    links_by_id: dict[int, Link],
    hiers_by_id: dict[int, RBDHier],
) -> dict[int, dict]:
    """Weak Link Raptor 7 "domain": per Regular/End node, WHICH elements'
    failures can propagate to it.

    Backward reachability over LINK_DATA from each target node:
    - RBDBlock / RBDEvent sources are collected (things that fail);
    - Regular nodes crossed en route are listed as path members;
    - an RBDHier target redirects to that hierarchy's own Out node
      (mirror of `_walk_forward`'s In-node redirect), pulling the whole
      sub-level into the parent's domain;
    - Start (type 1) contributes nothing (it cannot fail);
    - In/Out markers (types 4/5) are traversed but never listed.

    Returns node id -> {"name", "sources": [(kind, name)],
    "paths": [node name]} — both lists sorted case-insensitively.
    """
    incoming: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for link in links_by_id.values():
        src_kind = link.connect_f_ref.replace("RBD", "", 1)
        dst_kind = link.connect_t_ref.replace("RBD", "", 1)
        incoming.setdefault((dst_kind, link.connect_to_id), []).append(
            (src_kind, link.connect_from_id))

    def walk_back(kind: str, oid: int, block_ids: set, event_ids: set,
                  hier_ids: set, path_nodes: set, seen: set):
        key = (kind, oid)
        if key in seen:
            return
        seen.add(key)
        node = nodes_by_id.get(oid) if kind == "Node" else None
        if node is not None:
            if node.type_node == 2 and oid not in path_nodes:
                path_nodes.add(oid)
            elif node.type_node == 1:
                return  # Start cannot fail
        for src_kind, src_id in incoming.get(key, []):
            if src_kind == "Block":
                if src_id in blocks_by_id:
                    block_ids.add(src_id)
                    # keep walking: an upstream failure starves this
                    # block and still takes the target down
                    walk_back("Block", src_id, block_ids, event_ids,
                              hier_ids, path_nodes, seen)
                continue
            if src_kind == "Event":
                if src_id in events_by_id:
                    event_ids.add(src_id)
                    walk_back("Event", src_id, block_ids, event_ids,
                              hier_ids, path_nodes, seen)
                continue
            if src_kind == "Hier":
                if src_id in hiers_by_id:
                    # don't list the oval -- its contents stand in for
                    # it; enter the sub-level from its Out marker
                    # (mirror of _walk_forward's In-side redirect)
                    walk_back("Node", hiers_by_id[src_id].out_id,
                              block_ids, event_ids, hier_ids,
                              path_nodes, seen)
                    # ...and keep walking past the oval to whatever
                    # feeds ITS In side (a starved hierarchy fails too)
                    walk_back("Hier", src_id, block_ids, event_ids,
                              hier_ids, path_nodes, seen)
                continue
            walk_back(src_kind, src_id, block_ids, event_ids, hier_ids,
                      path_nodes, seen)

    def _label(kind: str, oid: int, name: str, used: dict) -> str:
        # duplicate display names get an "#id" suffix so parallel
        # elements stay distinct (TwoBlockRedundancy's two "unnamed")
        if name in used:
            used[name] += 1
            return f"{name} #{oid}"
        used[name] = 1
        return name

    result: dict[int, dict] = {}
    for nid, node in nodes_by_id.items():
        if node.type_node not in (2, 3):
            continue
        block_ids: set = set()
        event_ids: set = set()
        hier_ids: set = set()
        path_nodes: set = set()
        walk_back("Node", nid, block_ids, event_ids, hier_ids,
                  path_nodes, set())
        path_nodes.discard(nid)
        used: dict = {}
        sources = [("Block", _label("Block", bid, blocks_by_id[bid].name, used))
                   for bid in sorted(block_ids)]
        used = {}
        sources += [("Event", _label("Event", eid, events_by_id[eid].name, used))
                    for eid in sorted(event_ids)]
        used = {}
        sources += [("Hier", _label("Hier", hid, hiers_by_id[hid].name, used))
                    for hid in sorted(hier_ids)]
        pused: dict = {}
        paths = [_label("Node", pid, nodes_by_id[pid].node_name, pused)
                 for pid in sorted(path_nodes) if pid in nodes_by_id]
        result[nid] = {
            "name": node.node_name,
            "end": node.type_node == 3,
            "sources": sorted(sources, key=lambda s: s[1].lower()),
            "paths": sorted(paths, key=str.lower),
        }
    return result

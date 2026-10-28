"""Programmatic `.rbd` construction/patching + structural validation.

Sits on top of `raptor.core.fileio.rbd`'s `RbdFile`/`load_rbd`/`save_rbd`/
`render_rbd` -- the same in-memory model the GUI itself writes legacy files
from (`raptor/gui/ui/main_window.py` calls `save_rbd` directly). Building
through this model means every field stays correctly formatted
automatically; you never hand-edit fixed-width text and never get a
column-count typo.

The builders exist because hand-editing raw `.rbd` text readily produces
files that crash Raptor 7 with an ungraceful `MODSIM Runtime error:
Access Violation` -- a node's declared `n` not matching its real
incoming-link count, a stray second End node, a `start.Out` field not bumped
after adding an output. `validate()` encodes those known crash classes as
ERROR rules so they are caught locally instead of costing a Wine round-trip
through the user; WARN rules flag harmless-but-worth-a-look inconsistencies.

Usage:

    from raptor.core.fileio.builder import load, get_row, set_fields, add_row, next_id, validate, save

    rbd = load("tests/golden/HierarchyExample/input.rbd")

    block = get_row(rbd, "BLOCK_DATA", id=4)
    set_fields(block, PMFreq=50.0)

    new_id = next_id(rbd, "BLOCK_DATA")
    newblock = add_row(rbd, "BLOCK_DATA", clone_id=4, ID=new_id, blockName="BlockX")

    issues = validate(rbd)
    for i in issues:
        print(i)
    if any(i.startswith("ERROR") for i in issues):
        raise SystemExit("fix the errors above before saving")

    save(rbd, "out.rbd")

`render_rbd` re-renders the whole file and does not preserve Raptor 7's
padded column spacing -- it collapses section banners/headers to compact
single-space text. The parser here is whitespace-tolerant, so this is likely
fine, and the GUI already calls `save_rbd` as its own "save legacy .rbd"
path, but loading a `render_rbd`-produced file in the real Windows binary
under Wine is untested. Prefer patching a byte-for-byte copy of the
original text for anything headed to a Raptor 7 capture, and treat `save()` as
safe for files that stay python-side (e.g. regression-test fixtures that
never go to Wine).
"""
from __future__ import annotations

from raptor.core.fileio.rbd import RbdFile, load_rbd, save_rbd

# Sections whose header count excludes a filler "ID 0" row that's always
# present in the actual rows but never counted (BLOCK_DATA always has exactly
# one extra `ID 0` row beyond the header count; EVENT_DATA has no such filler,
# its count is exact).
_DUMMY_ROW_SECTIONS = {"BLOCK_DATA"}

#: section name -> counts dict key
_SECTION_COUNT_KEY = {
    "BLOCK_DATA": "Blocks",
    "EVENT_DATA": "Events",
    "NODE_DATA": "Nodes",
    "LINK_DATA": "Links",
    "TRIGGERS": "Triggers",
    "SPARE_POOL_DATA": "SpPools",
    "RESOURCE_DATA": "ResPools",
    "HIERARCHY_DATA": "Hiers",
    "PHASES": "Phases",
}


# --- load/save -------------------------------------------------------

def load(path: str) -> RbdFile:
    return load_rbd(path)


def save(rbd: RbdFile, path: str, *, sync_counts_first: bool = True, refuse_on_error: bool = True) -> None:
    """Write `rbd` to `path`. By default re-syncs header counts from the
    actual rows first (see `sync_counts`), and refuses to write if
    `validate()` reports any ERROR (not WARN) -- pass `refuse_on_error=False`
    for a deliberately-invalid file (e.g. a crash-probe variant)."""
    if sync_counts_first:
        sync_counts(rbd)
    if refuse_on_error:
        errors = [i for i in validate(rbd) if i.startswith("ERROR")]
        if errors:
            raise ValueError(
                "refusing to save -- validate() found errors (pass refuse_on_error=False to override):\n"
                + "\n".join(errors)
            )
    save_rbd(rbd, path)


# --- row lookup/mutation ----------------------------------------------

def get_row(rbd: RbdFile, section: str, *, id: int | str | None = None, name: str | None = None) -> dict:
    """Find a row in `section` by its ID column or Name-ish column. Raises
    KeyError if not found. The ID/Name column is named differently per
    section (`ID` almost everywhere, `blockName`/`Name`/`eventName`/... for
    the name lookup) -- pass whichever you have."""
    rows = rbd.section(section).rows
    if id is not None:
        for row in rows:
            if row.get("ID") == str(id):
                return row
        raise KeyError(f"{section}: no row with ID={id}")
    if name is not None:
        name_cols = ("blockName", "eventName", "Name", "hierarchyName", "PoolName", "TriggerName", "PhaseName")
        for row in rows:
            for col in name_cols:
                if row.get(col) == name:
                    return row
        raise KeyError(f"{section}: no row with name={name!r}")
    raise ValueError("must pass id= or name=")


def _int_or_none(value: str | None) -> int | None:
    """`int(value)`, or None when the cell is missing or not an integer."""
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def set_fields(row: dict, **fields) -> dict:
    """Set fields on a row dict, formatting floats/ints to match the
    file's own `%.6f`/plain-integer conventions. Strings pass through
    unchanged. Returns the row for chaining."""
    for key, value in fields.items():
        if key not in row:
            raise KeyError(f"row has no column {key!r} -- known columns: {sorted(row)}")
        if isinstance(value, bool):
            row[key] = "1" if value else "0"
        elif isinstance(value, float):
            row[key] = f"{value:.6f}"
        elif isinstance(value, int):
            row[key] = str(value)
        else:
            row[key] = str(value)
    return row


def _is_zero_id(row: dict) -> bool:
    try:
        return int(float(row.get("ID", -1))) == 0
    except (TypeError, ValueError):
        return False


def _default_clone_row(rows: list[dict]) -> dict | None:
    """The row a new element should be seeded from when the caller names
    no template explicitly.

    That is the section's ``ID=0`` template row -- the row the file format
    *mandates* precisely so it can carry the model's default properties for
    that element kind.  It is not always ``rows[0]`` (a saved file may
    order the section any way round), which is why it is looked up by id.

    This used to skip ``ID=0`` as "filler" and clone the first real element
    instead, so every added block silently inherited that element's
    ``ParentId`` and failure/repair distributions.
    """
    for row in rows:
        if _is_zero_id(row):
            return row
    return rows[0] if rows else None


def add_row(rbd: RbdFile, section: str, *, clone_id: int | str | None = None, clone_name: str | None = None, **fields) -> dict:
    """Append a new row to `section`, cloned from an existing row (by
    `clone_id`/`clone_name`) or from the section's `ID=0` template row if
    neither is given, then overridden with `fields` (same formatting rules
    as `set_fields`). Returns the new row. Remember to call `next_id()` for
    the new row's own `ID` and pass it in `fields`, and to fix up any
    columns that reference OTHER rows (e.g. a block's `pmTrig` name) --
    `add_row` only clones+overrides, it doesn't rewire references for you."""
    rows = rbd.section(section).rows
    template = None
    if clone_id is not None:
        template = get_row(rbd, section, id=clone_id)
    elif clone_name is not None:
        template = get_row(rbd, section, name=clone_name)
    else:
        template = _default_clone_row(rows)
    if template is None:
        # An empty section: the caller must name every column, and the row is built from them.
        columns = list(rbd.section(section).columns)
        missing = [c for c in columns if c not in fields]
        if not columns or missing:
            raise ValueError(f"{section} has no rows to clone from -- pass every field explicitly instead"
                             + (f" (missing: {', '.join(missing)})" if columns else ""))
        template = {c: "" for c in columns}
    new_row = dict(template)
    set_fields(new_row, **fields)
    rows.append(new_row)
    return new_row


def set_block_dependency(
    rbd: RbdFile, block_row: dict, dep_type: str, *,
    target_id: int | str | None = None, target_name: str | None = None,
) -> dict:
    """Set a block's dependency (`DepType` + `DepNum`), resolving the
    target row and -- critically -- the ID that `DepNum` must actually
    hold.

    For a hierarchy dependency, `DepNum` must be the hierarchy's OUT node
    id, NOT the hierarchy id. Source `CheckForDeps` resolves an `RBDHier`
    dependency as `Child("RBDNode", simDependencyNum)`,
    so a hierarchy-id `DepNum` points at a nonexistent node and crashes Raptor 7
   on simulate. This helper looks the Out node up from
    `HIERARCHY_DATA` so it can never drift.

    `dep_type` is one of `"RBDBlock"`, `"RBDNode"`, `"RBDEvent"`,
    `"RBDHier"`.  Give either `target_id` or `target_name` (the latter is
    matched against the section's own name column).  Returns `block_row`.
    """
    if dep_type == "RBDHier":
        h = get_row(rbd, "HIERARCHY_DATA", id=target_id, name=target_name)
        set_fields(block_row, DepType="RBDHier", DepNum=int(h["out"]))
    elif dep_type == "RBDBlock":
        b = get_row(rbd, "BLOCK_DATA", id=target_id, name=target_name)
        set_fields(block_row, DepType="RBDBlock", DepNum=int(b["ID"]))
    elif dep_type == "RBDEvent":
        e = get_row(rbd, "EVENT_DATA", id=target_id, name=target_name)
        set_fields(block_row, DepType="RBDEvent", DepNum=int(e["ID"]))
    elif dep_type == "RBDNode":
        n = get_row(rbd, "NODE_DATA", id=target_id, name=target_name)
        set_fields(block_row, DepType="RBDNode", DepNum=int(n["ID"]))
    else:
        raise ValueError(
            f"unknown dep_type {dep_type!r} -- use RBDBlock/RBDNode/RBDEvent/RBDHier")
    return block_row


#: Every section with its own `ID` primary key that a link, a dependency
#: field (`DepType`/`DepNum`), or model code can reference by (kind, id).
#: Raptor 7 allocates each of these from its OWN counter -- confirmed
#: by scanning every real-captured `.rbd` under `tests/`: 24 cross-kind id
#: collisions exist there (e.g. a Block and a Node both id `1`), so this is
#: NOT a structural error `validate()` should reject (`docs/RBDS_FORMAT.md`'s
#: "unique across ALL element sections" is aspirational, not Raptor 7's
#:actual behavior -- do not "fix" that doc claim into a validate() ERROR).
#: But treating a raw id as globally unique, with no check on which KIND it
#: names, is a real and recurring bug class in THIS port: found twice this
#: session (the parallel-template k-of-n diagram badge counted a link into
#: an unrelated Block sharing a Node's id; `back_flow_dependencies` matched
#: a dependent purely by id with no kind check). `next_id()` pools across
#: every section below so IDs THIS BUILDER allocates never collide across
#: kinds -- eliminating the risk for any NEWLY built model, without
#: touching how existing/Raptor 7 files validate.
_ID_POOL_SECTIONS = ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA", "HIERARCHY_DATA", "LINK_DATA")


def next_id(rbd: RbdFile, section: str, *,
            used: set[int] | None = None) -> int:
    """Lowest unused positive integer ID, pooled across every element kind
    (`_ID_POOL_SECTIONS`) -- not just `section` -- so an id this function
    hands out never collides with a Block/Node/Event/Hier/Link that already
    exists under a DIFFERENT kind, even though Raptor 7 itself tolerates
    that collision (see `_ID_POOL_SECTIONS`'s docstring for why this is a
    builder-side safety measure, not a `validate()` rule). `section` is
    still required -- it says which section the caller is about to insert
    the new row into -- but no longer scopes which ids count as "used".
    Non-monotonic IDs relative to row order are safe in Raptor 7 -- this
    just picks A safe new one, not necessarily the max+1.

    `used` is for bulk builders (``build_from_spec`` adding thousands of
    rows): pass a set collected once with :func:`used_ids`, add every id
    you allocate, and the per-call scan of every section disappears.
    """
    if section not in _ID_POOL_SECTIONS:
        raise ValueError(f"next_id: unknown section {section!r}, expected one of {_ID_POOL_SECTIONS}")
    if used is None:
        used = used_ids(rbd)
    n = 1
    while n in used:
        n += 1
    return n


def used_ids(rbd: RbdFile) -> set[int]:
    """Every positive ID currently present across the pooled sections."""
    used: set[int] = set()
    for sec in _ID_POOL_SECTIONS:
        for row in rbd.section(sec).rows:
            raw = row.get("ID", "0")
            if raw in (None, "", "0") and sec == "BLOCK_DATA":
                continue  # the mandatory ID=0 sentinel row, not a real block
            try:
                used.add(int(raw))
            except (TypeError, ValueError):
                continue
    used.discard(0)
    return used


def sync_counts(rbd: RbdFile) -> None:
    """Recompute `rbd.counts` from the actual section row counts (the
    header line Raptor 7's own GUI keeps in sync automatically).  Call
    this after add_row()/deleting rows and before render/save -- `save()`
    does this by default."""
    for section, key in _SECTION_COUNT_KEY.items():
        rows = rbd.section(section).rows
        if section in _DUMMY_ROW_SECTIONS:
            count = sum(1 for r in rows if r.get("ID") != "0")
        else:
            count = len(rows)
        rbd.counts[key] = count
    # `HierDepths` is `deepestLevel`: 1 in every Raptor 7 file that has a hierarchy (nested or not), 0 otherwise.
    rbd.counts["HierDepths"] = 1 if any(r.get("ID") != "0" for r in rbd.section("HIERARCHY_DATA").rows) else 0
    from raptor.core.fileio.phase_edit import sync_phase_columns
    sync_phase_columns(rbd)


# --- validation ---------------------------------------------------------

def validate(rbd: RbdFile) -> list[str]:
    """Return a list of "ERROR: ..." / "WARN: ..." strings describing
    structural problems.  ERRORs are the known Access-Violation-crash
    classes (node ``n`` mismatch, >1 End node, dangling link reference,
    duplicate ID).  WARNs flag harmless-but-worth-a-look inconsistencies
    (header count drift, ``Out`` mismatches) so you can sanity-check but
    aren't blocked by them."""
    issues: list[str] = []

    blocks = {r["ID"]: r for r in rbd.section("BLOCK_DATA").rows if r.get("ID") != "0"}
    nodes = {r["ID"]: r for r in rbd.section("NODE_DATA").rows}
    events = {r["ID"]: r for r in rbd.section("EVENT_DATA").rows}
    hiers = {r["ID"]: r for r in rbd.section("HIERARCHY_DATA").rows}
    links = rbd.section("LINK_DATA").rows

    # duplicate IDs
    for section_name, ids in (("BLOCK_DATA", [r["ID"] for r in rbd.section("BLOCK_DATA").rows if r.get("ID") != "0"]),
                               ("NODE_DATA", list(nodes)),
                               ("EVENT_DATA", list(events)),
                               ("LINK_DATA", [r["ID"] for r in links])):
        seen = set()
        for i in ids:
            if i in seen:
                issues.append(f"ERROR: {section_name} has a duplicate ID {i!r}")
            seen.add(i)

    # A hierarchy dependency names the hierarchy's OUT node (see `set_dependency`); the hierarchy's own id is not a
    # node, so the engine cannot resolve it.
    hier_outs = {str(h.get("out")) for h in hiers.values()}
    for b in blocks.values():
        if b.get("DepType") == "RBDHier" and str(b.get("DepNum")) not in hier_outs and b.get("DepNum") not in ("-1", "-2"):
            issues.append(
                f"ERROR: block {b.get('blockName')!r} has DepType=RBDHier but DepNum={b.get('DepNum')} is not a "
                f"hierarchy's Out node id (a hierarchy dependency names the Out node, not the hierarchy: use "
                f"set_dependency, or DepNum={sorted(hier_outs)})"
            )

    # dangling link references
    kind_map = {"Block": blocks, "Node": nodes, "Event": events, "Hier": hiers}
    for link in links:
        for side in ("From", "To"):
            kind = link.get(f"{side}Type")
            obj_id = link.get(f"{side}ID")
            table = kind_map.get(kind) if kind is not None else None
            if table is None:
                issues.append(f"ERROR: LINK_DATA id={link.get('ID')}: unknown {side}Type={kind!r}")
            elif obj_id not in table:
                issues.append(
                    f"ERROR: LINK_DATA id={link.get('ID')}: {side}={kind} id={obj_id!r} doesn't exist"
                )

    # single End (Type=3) node
    end_nodes = [nid for nid, row in nodes.items() if row.get("Type") == "3"]
    if len(end_nodes) == 0:
        issues.append("ERROR: no End (Type=3) node found")
    elif len(end_nodes) > 1:
        issues.append(
            f"ERROR: {len(end_nodes)} End (Type=3) nodes found ({end_nodes}) -- "
            "only one End node is supported; route "
            "into the existing End node instead and bump its own n"
        )

    # An End node needs one good path whatever its k says (real, `LadL2Res` 2026-10-01: k=2 n=3 logs `2-1/3`;
    # the engine matches). The GUI never offers K-of-N on an End node, so a k above 1 cannot come from a real
    # model: the intended requirement belongs on a node in front of the End node.
    for end_id in end_nodes:
        try:
            end_k = int(nodes[end_id].get("k", "1"))
        except ValueError:
            continue
        if end_k > 1:
            issues.append(
                f"WARN: End node {nodes[end_id].get('Name')!r} has k={end_k}, but Raptor 7 needs only one good "
                "path at an End node; put the K-of-N on a node in front of it (a single link into End)"
            )

    # hierarchy In/Out marker nodes: `in`/`out` must name an existing
    # node of the right Type (4/5), and that node's own `parent` column
    # must equal THIS hierarchy's own id -- not the hierarchy's
    # enclosing level (0 for a top-level hierarchy, or an outer
    # hierarchy's id for a nested one), which is the mistake this check
    # exists to catch. Confirmed against every Raptor 7 fixture
    # (HierarchyExample: node 24's parent=23, hierarchy 23's in=24, not
    # parent=0). Getting this backwards doesn't just mis-simulate --
    # `graph.py.build_efpa_graph` does `hiers_by_id[marker.parent_id]`
    # for every In node and raises a bare `KeyError: 0`, so this turns
    # that crash into a message that says what to fix.
    for hid, hier_row in hiers.items():
        for side, want_type in (("in", "4"), ("out", "5")):
            marker_id = hier_row.get(side)
            marker = nodes.get(marker_id) if marker_id is not None else None
            if marker is None:
                issues.append(
                    f"ERROR: HIERARCHY_DATA {hier_row.get('hierarchyName')!r} (id={hid}) "
                    f"{side}={marker_id!r} doesn't exist"
                )
                continue
            if marker.get("Type") != want_type:
                issues.append(
                    f"ERROR: HIERARCHY_DATA {hier_row.get('hierarchyName')!r} (id={hid}) "
                    f"{side}={marker_id} is node Type={marker.get('Type')!r}, expected {want_type!r}"
                )
            if marker.get("parent") != hid:
                issues.append(
                    f"ERROR: node {marker.get('Name')!r} (id={marker_id}) is hierarchy "
                    f"{hier_row.get('hierarchyName')!r} (id={hid})'s {side!r} marker but its own "
                    f"parent={marker.get('parent')!r} -- must equal {hid!r} (the hierarchy's "
                    "own id, NOT the hierarchy's enclosing level), or graph-building crashes "
                    "with an unhelpful KeyError"
                )

    # A hierarchy's `level` must be its nesting depth (top level = 1).
    # `RunEFPA` descends with `mamaLevel := hier.level + 1` and `TestFail`
    # uses that number to decide which children to walk; a stale flat value
    # makes the walk miss a whole subtree, leaving an empty downstream set
    # and `NEW(array, 1..0)` -- Raptor 7 then dies on Simulate with
    # "Array allocation: high bound is less than low bound".
    def _depth(hid: str, seen: frozenset[str] = frozenset()) -> int:
        parent = str(hiers[hid].get("Parent", "0"))
        if parent in ("0", "", hid) or parent not in hiers or parent in seen:
            return 1
        return _depth(parent, seen | {hid}) + 1

    for hid, hier_row in hiers.items():
        declared_level = _int_or_none(hier_row.get("level"))
        real_level = _depth(hid)
        if declared_level != real_level:
            issues.append(
                f"WARN: HIERARCHY_DATA {hier_row.get('hierarchyName')!r} (id={hid}) declares "
                f"level={declared_level} but is {real_level} level(s) deep -- Raptor 7 walks "
                f"hierarchies by level and crashes on Simulate (Array allocation); save_rbd "
                f"derives the level from Parent, so the export is repaired"
            )

    # node n (incoming path count) vs real incoming link count
    incoming_count: dict[str, int] = {nid: 0 for nid in nodes}
    for link in links:
        if link.get("ToType") == "Node":
            nid = link.get("ToID")
            if nid in incoming_count:
                incoming_count[nid] += 1
    for nid, row in nodes.items():
        declared = row.get("n")
        real = incoming_count[nid]
        declared_i = _int_or_none(declared)
        if declared_i != real:
            issues.append(
                f"ERROR: node {row.get('Name')!r} (id={nid}) declares n={declared} but has "
                f"{real} real incoming link(s) -- this crashes Raptor 7 with an Access "
                f"Violation, fix n to match exactly"
            )

    # node Out: OVER-declared (declared > real) is harmless -- every
    # long-standing fixture with this WARN has declared=2/real=1
    # ("start" nodes' own uniformly-wrong template default) and Raptor 7
    #has simulated many of them without issue.  UNDER-declared
    # (declared < real -- an internal array sized to Out gets written
    # past its bound) is the SAME crash class as the node `n` mismatch
    # ERROR above, just on a source node's outgoing side instead of a
    # sink node's incoming side.
    outgoing_count: dict[str, int] = {nid: 0 for nid in nodes}
    for link in links:
        if link.get("FromType") == "Node":
            nid = link.get("FromID")
            if nid in outgoing_count:
                outgoing_count[nid] += 1
    for nid, row in nodes.items():
        declared = row.get("Out")
        real = outgoing_count[nid]
        declared_i = _int_or_none(declared)
        if declared_i is not None and declared_i < real:
            issues.append(
                f"ERROR: node {row.get('Name')!r} (id={nid}) declares Out={declared} but has "
                f"{real} real outgoing link(s) -- UNDER-declared, the same Access-Violation "
                "crash class as a node `n` mismatch -- fix Out "
                "to match exactly"
            )
        elif declared_i != real:
            issues.append(
                f"WARN: node {row.get('Name')!r} (id={nid}) declares Out={declared} but has "
                f"{real} real outgoing link(s) -- OVER-declared, harmless in "
                "Raptor 7 (several working models have this), listed for visibility only"
            )

    # Gamma(6)/Pearson5(11)/Weibull(15) with fewer than 3 stored params:
    # source's DrawNumber adds `dataArray[3]` as a LOCATION for all three
    # (1027/1035), but the loader sizes the array to
    # #FParam/#RParam -- so with 2 params Raptor 7 reads
    # one slot past the end, i.e. HEAP GARBAGE.
    for kind, table in (("block", blocks),):
        for row in table.values():
            for dist_key, n_key in (("FDist", "#FParam"), ("RDist", "#RParam")):
                dist, n = _int_or_none(row.get(dist_key)), _int_or_none(row.get(n_key))
                if dist is None or n is None:
                    continue
                if dist in (6, 11, 15) and n < 3:
                    issues.append(
                        f"ERROR: {kind} {row.get('blockName')!r} {dist_key}={dist} with {n_key}={n} -- "
                        "Raptor 7 reads dataArray[3] (location) OUT OF BOUNDS and adds heap garbage "
                        "to every draw; set it to 3 (every real-authored model does)"
                    )

    # header counts vs actual rows
    for section, key in _SECTION_COUNT_KEY.items():
        rows = rbd.section(section).rows
        if section in _DUMMY_ROW_SECTIONS:
            real = sum(1 for r in rows if r.get("ID") != "0")
        else:
            real = len(rows)
        declared_count = rbd.counts.get(key)
        if declared_count != real:
            if section == "PHASES":
                # PHASES header-count drift crashes Raptor 7 on open
                # (unlike other sections' drift, which is harmless)
                issues.append(
                    f"ERROR: header {key}={declared_count} but {section} actually has {real} row(s) "
                    "-- would crash Raptor 7 on open (unlike other sections' header-count "
                    "drift, which is harmless) -- call sync_counts(rbd) before saving, or pass "
                    "sync_counts_first=True (default) to save()"
                )
            else:
                issues.append(
                    f"WARN: header {key}={declared_count} but {section} actually has {real} row(s) "
                    "-- call sync_counts(rbd) before saving, or pass sync_counts_first=True (default) to save()"
                )

    # Phase tokens: the P columns and every phased row's tokens must match the PHASES rows (the header count is
    # checked above).
    from raptor.core.fileio.phase_edit import phase_problems
    for problem in phase_problems(rbd):
        if not problem.startswith("header Phases"):
            issues.append(f"ERROR: {problem} -- Raptor 7 reads the wrong number of phase tokens and crashes on "
                          "open; change phases through the phases API")

    return issues

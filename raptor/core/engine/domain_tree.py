"""`DomainTree.TXT` and `CoreFile.TXT` -- Raptor 7's two developer-only walks over the
EFPA graph from the End node (`CreateDomainTree`, `WriteDomains`, `WriteCores`, Avant-garde boxes "Domain Tree" and "Core File").

Each node's *descendants* (the things that feed it) are, in this order: the blocks whose EFPA
node it is, the events whose EFPA node it is, then the nodes with an EFPA connection into it
(in node order, once per connection). The domain tree prints every member, nesting under each
node by three spaces; the core ("Kings and Serfs") file prints the same walk but only indents
under nodes with more than one connection in. Names are written as `bl `, `ev `, `nd ` and
`hr ` (a type-5 node prints its hierarchy's name).
"""
from __future__ import annotations

from raptor.core.engine.load import load_model
from raptor.core.fileio.rbd import RbdFile

#: `devVersion`: the captured binary is `7.0.11 Student` (the source constant for the full build reads 7.0.12; the port follows the binary, as the file headers do).
DEV_VERSION = "7.0.11"
INDENT = "   "


def _header(kind_line: str, file_name: str, today: str, version: str) -> list[str]:
    """The five header lines; `today` is MODSIM `DateTime`'s text (`Sat Oct 10 12:05:11 2026`)."""
    day = int(today[8:10].strip() or 0)
    month = today[4:7]
    year = today[-4:]
    return [f"Raptor day month year = {day} {month} {year}", f"devVersion= {version}",
            f"{kind_line} {file_name}", ""]


def _efpa_node(model, element) -> int:
    """`EFPAnode`: the node at the far end of the element's `downstream_link_id` (hierarchy boxes
    resolved through, and the In-node landing already re-pointed by `build_efpa_graph`)."""
    link = model.links_by_id.get(element.downstream_link_id)
    if link is None:
        return 0
    return link.resolved_to_id or link.connect_to_id


def _descendants(model) -> dict[int, list[tuple[str, int]]]:
    groups: dict[int, list[tuple[str, int]]] = {nid: [] for nid in model.nodes_by_id}
    for block in model.blocks_by_id.values():
        groups.setdefault(_efpa_node(model, block), []).append(("bl", block.id))
    for event in model.events_by_id.values():
        groups.setdefault(_efpa_node(model, event), []).append(("ev", event.id))
    for node in model.nodes_by_id.values():
        for target in node.propagates_to_node_ids:
            groups.setdefault(target, []).append(("nd", node.id))
    return groups


def _label(model, kind: str, ident: int) -> str:
    if kind == "bl":
        return "bl " + model.blocks_by_id[ident].name
    if kind == "ev":
        return "ev " + model.events_by_id[ident].name
    node = model.nodes_by_id[ident]
    if node.type_node == 5:
        return "hr " + model.hiers_by_id[node.parent_id].name
    return "nd " + node.node_name


def _into(node) -> int:
    """`EconnectIntoNum`: 1 for a hierarchy's In node, else its incoming connections."""
    return 1 if node.type_node == 4 else len(node.in_paths_array)


def _walk(model, groups, seq_id: int, tab: str, out: list[str], *, core: bool) -> None:
    """The original recurses (`WriteDomains`/`WriteCores`); this keeps an explicit stack so a
    long series chain cannot exhaust Python's recursion limit."""
    stack = [(tab, iter(groups.get(seq_id, ())))]
    while stack:
        tab, members = stack[-1]
        for kind, ident in members:
            if kind != "nd":
                out.append(tab + _label(model, kind, ident))
                continue
            node = model.nodes_by_id[ident]
            child_tab = tab
            if not core or _into(node) > 1:
                out.append(tab + _label(model, kind, ident))
                child_tab = tab + INDENT
            stack.append((child_tab, iter(groups.get(ident, ()))))
            break
        else:
            stack.pop()


def domain_tree_lines(rbd: RbdFile, file_name: str, today: str, *, core: bool = False,
                      version: str = DEV_VERSION) -> list[str]:
    """The lines of `DomainTree.TXT` (`core=False`) or `CoreFile.TXT` (`core=True`)."""
    model = load_model(rbd)
    end = next((n for n in model.nodes_by_id.values() if n.type_node == 3), None)
    title = "Kings and Serfs File for" if core else "Domain Tree for"
    out = _header(title, file_name, today, version)
    if end is None:
        return out
    out.append("nd " + end.node_name)
    _walk(model, _descendants(model), end.id, INDENT, out, core=core)
    return out

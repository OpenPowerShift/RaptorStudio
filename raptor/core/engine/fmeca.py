"""The Failure Effects text: `CreateFMECAarray` and `WriteFMECAtoFile`.

Port of Raptor 7. The same text is the System Status Summary
dialog and what the interactive log file receives on every "Write To Failure
Effects Log". Checked line for line against a Raptor 7 session
(`tests/golden/FevSpaceStation/legacy/fevlog_FevSpaceStation.txt`).

Layout (exact, including the quirks):

* Each list is `"     <Label>:"` padded so the first name starts in column 25. Names
  are joined with ", "; a name that would take the line past 80 columns starts a
  new line instead, indented 25 spaces, and the line it leaves has NO trailing
  comma. The FIRST name on a line is never wrapped, however long.
* Sections, in order: `PHASING SCENARIO` (phased models only), `FAILURES`,
  `EFFECTS`, `CRITICALITY`. `Failed Components` and the two node lists always
  appear (`None` when empty); every other list only when it has members.
* A block is listed under the FIRST that applies: Cut, Linked, Repairing (Induced
  Failures when it is hatched, else Failed Components), Idle, PM.
* Only ordinary nodes (type 2) are listed as Down/Degraded Nodes; a hierarchy's
  Out node (type 5) is listed by its hierarchy's name as a Subsystem. "Down"
  means Down, Idle or PM; "Degraded" means Degraded.
* Names are in the order of the file (Raptor 7's group order), not sorted.
* The array holds 200 lines in Raptor 7 (`NEW(FMECAarray,1..200)`); more than
  that is out-of-range there, so it is refused here rather than truncated.
"""
from __future__ import annotations

from raptor.core.engine.errors import UnrunnableModel
from raptor.core.engine.model_checks import ModelProblem
from raptor.core.engine.report_format import real_to_sn
from raptor.core.engine.system_status import SystemColor
from raptor.core.model import ActiveStatus, BlockStatus, EventStatus, NodeStatus

MAX_WIDTH = 80
LABEL_WIDTH = 25
MAX_LINES = 200            # FMECAarray[1..200]
STAR_LINE = "*" * 65


def _wrapped_list(label: str, names: list[str]) -> list[str]:
    """One list, wrapped exactly as Raptor 7's loop does."""
    lines: list[str] = []
    temp = label
    size = LABEL_WIDTH
    for name in names:
        if temp == label:
            temp += name
            size += len(name)
        elif size + 2 + len(name) <= MAX_WIDTH:
            temp += ", " + name
            size += 2 + len(name)
        else:
            lines.append(temp)
            temp = " " * LABEL_WIDTH + name
            size = LABEL_WIDTH + len(name)
    lines.append(temp)
    return lines


def fmeca_lists(fev) -> dict[str, list[str]]:
    """The element names behind each Failure Effects list, in Raptor 7's order
    (what `build_fmeca` lays out as text; also the structured form for the API)."""
    model = fev.model
    cut: list[str] = []
    linked: list[str] = []
    failed: list[str] = []
    induced: list[str] = []
    idle: list[str] = []
    pm: list[str] = []
    failed_events: list[str] = []
    for b in model.blocks_by_id.values():
        if b.active_status == ActiveStatus.CUT:
            cut.append(b.name)
        elif b.active_status == ActiveStatus.LINKED:
            linked.append(b.name)
        elif b.op_status == BlockStatus.REPAIRING:
            (induced if b.run_state.fev_induced_by_dependency else failed).append(b.name)
        elif b.op_status == BlockStatus.IDLE:
            idle.append(b.name)
        elif b.op_status == BlockStatus.PM:
            pm.append(b.name)
    for e in model.events_by_id.values():
        if e.active_status == ActiveStatus.CUT:
            cut.append(e.name)
        elif e.active_status == ActiveStatus.LINKED:
            linked.append(e.name)
        elif e.op_status == EventStatus.FAILURE:
            failed_events.append(e.name)

    down_nodes: list[str] = []
    degraded_nodes: list[str] = []
    down_subs: list[str] = []
    degraded_subs: list[str] = []
    down_like = (NodeStatus.DOWN, NodeStatus.IDLE, NodeStatus.PM)
    for n in model.nodes_by_id.values():
        if n.type_node == 2:
            if n.status in down_like:
                down_nodes.append(n.node_name)
            elif n.status == NodeStatus.DEGRADED:
                degraded_nodes.append(n.node_name)
        elif n.type_node == 5:
            hier = model.hiers_by_id[n.parent_id]
            if n.status in down_like:
                down_subs.append(hier.name)
            elif n.status == NodeStatus.DEGRADED:
                degraded_subs.append(hier.name)
    return {
        "cut": cut, "linked": linked, "failed": failed, "failed_events": failed_events,
        "induced": induced, "idle": idle, "pm": pm,
        "down_subsystems": down_subs, "degraded_subsystems": degraded_subs,
        "down_nodes": down_nodes, "degraded_nodes": degraded_nodes,
    }


def build_fmeca(fev, *, show_criticality: bool = False) -> list[str]:
    """The FMECA array for the session's current state (without the
    `END_OF_ARRAY` terminator)."""
    model = fev.model
    lists = fmeca_lists(fev)
    cut, linked = lists["cut"], lists["linked"]
    failed, failed_events = lists["failed"], lists["failed_events"]
    induced, idle, pm = lists["induced"], lists["idle"], lists["pm"]
    down_subs, degraded_subs = lists["down_subsystems"], lists["degraded_subsystems"]
    down_nodes, degraded_nodes = lists["down_nodes"], lists["degraded_nodes"]

    out: list[str] = []
    if model.phases:
        phase = model.phases[fev.phase_number - 1]
        out += ["PHASING SCENARIO", "",
                "     Phase Name:         " + phase.phase_name, "",
                "     Phase Number:       " + str(fev.phase_number), ""]
        out += _wrapped_list("     Cut Components:     ", cut) \
            if cut else ["     Cut Components:     None"]
        out.append("")
        out += _wrapped_list("     Linked Components:  ", linked) \
            if linked else ["     Linked Components:  None"]
        out += ["", ""]

    out += ["FAILURES", ""]
    out += _wrapped_list("     Failed Components:  ", failed) \
        if failed else ["     Failed Components:  None"]
    out.append("")
    for label, names in (("     Failed Events:      ", failed_events),
                         ("     Induced Failures:   ", induced),
                         ("     Idle Components:    ", idle),
                         ("     PM Components:      ", pm)):
        if names:
            out += _wrapped_list(label, names)
            out.append("")

    out += ["EFFECTS", ""]
    if down_subs:
        out += _wrapped_list("     Down Subsystems:     ", down_subs)
        out.append("")
    if degraded_subs:
        out += _wrapped_list("     Degraded Subsystems: ", degraded_subs)
        out.append("")
    out += _wrapped_list("     Down Nodes:         ", down_nodes) \
        if down_nodes else ["     Down Nodes:         None"]
    out.append("")
    out += _wrapped_list("     Degraded Nodes:     ", degraded_nodes) \
        if degraded_nodes else ["     Degraded Nodes:     None"]
    out.append("")

    color = fev.system_color
    word = {SystemColor.GREEN: "Green", SystemColor.YELLOW: "Yellow",
            SystemColor.RED: "Red"}.get(color, "")
    out += ["CRITICALITY", "", "     System Status = " + word, ""]
    if show_criticality:
        out.append("     Criticality = "
                   + (real_to_sn(fev.criticality, 15, 6, 0)
                      if color == SystemColor.RED else "0.0"))
    if len(out) + 1 > MAX_LINES:
        raise UnrunnableModel([ModelProblem(
            "model",
            f"the Failure Effects text would be {len(out)} lines; Raptor 7's "
            f"array holds {MAX_LINES} and goes out of range beyond that")])
    return out


def format_fmeca_file_block(lines: list[str]) -> str:
    """What one "Write To Failure Effects Log" appends (`WriteFMECAtoFile`),
    with Raptor 7's CRLF line ends."""
    body = "\r\n" + "".join(f"{ln}\r\n" for ln in lines) + "\r\n"
    return body + STAR_LINE + "\r\n\r\n"


def capacity_lines(capacity: dict) -> list[str]:
    """`FevSession.capacity()` as a CAPACITY section laid out like the
    Failure Effects text above it (not part of Raptor 7's text or log)."""
    fraction = capacity["delivered_fraction"]
    share = f" ({fraction:.1%})" if fraction is not None else ""
    lines = ["CAPACITY", "",
             f"     Delivered Flow:     {capacity['delivered']} of {capacity['flow_generated']}{share}", "",
             f"     Lost Flow:     {capacity['lost']}", "",
             f"     System Capacity:     {capacity['system_capacity']}", ""]
    lines += [f"     {n['name']}:     flow {n['flow']}, capacity {n['capacity']}"
              for n in capacity["nodes"]]
    return lines

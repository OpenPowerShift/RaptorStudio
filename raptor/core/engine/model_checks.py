"""Models the engine refuses to run, and shapes worth a warning.

* **A cold-standby candidate that depends on another candidate of the same
  cold node** (refused: Raptor 7 crashes). `CS_DepOnColdBlock`: Raptor 7.0.11
  dies with `MODSIM Runtime error: Access Violation` on Simulate (ledger,
  "Closed / abandoned" item 3). The near-identical `CS_DepOnStandbyPeer`, where
  the dependent is NOT itself a path, runs fine in real, so only the evidenced
  shape is refused.
* **Dependency cycles** (a warning, NOT a refusal). Raptor 7 has no cycle
  check and runs them: `BackFlowDependencies(Down)` fires only when a block
  fails or enters PM, and a dependent reacts only while Running or Standby, so
  a Down cannot chase itself round a loop; a repair completion treats an Idle
  target as up. Captured 2026-09-30 and matched
  EXACTLY by the port: `DepCycleIdleProbe`, `DepCycleFailProbe`,
  `DepCycleTieProbe`, `DepCycle3Probe`, `DepSelfProbe` (two- and three-block
  loops, idle and induced-failure reactions, simultaneous failures, a
  self-dependency). Nothing beyond those sequences is captured, so the warning
  says so.

`load_model` raises `UnrunnableModel` for the refusals; the API's `validate`
reports the refusals as errors and the cycles as warnings.
"""
from __future__ import annotations

from dataclasses import dataclass

from raptor.core.engine.errors import UnrunnableModel
from raptor.core.model import Link, RBDBlock, RBDNode

#: `dep_type` values that name another element (the others are system/none).
_DEPENDS_ON_ELEMENT = ("RBDBlock", "RBDNode", "RBDEvent", "RBDHier")


@dataclass(frozen=True)
class ModelProblem:
    """One reason a model cannot be run."""

    element: str
    message: str

    def __str__(self) -> str:
        return f"{self.element}: {self.message}"


def _name(kind: str, ident: int, blocks: dict[int, RBDBlock], nodes: dict[int, RBDNode]) -> str:
    if kind == "RBDBlock" and ident in blocks:
        return f"block {blocks[ident].name!r}"
    if kind == "RBDNode" and ident in nodes:
        return f"node {nodes[ident].node_name!r}"
    return f"{kind[3:].lower()} {ident}"


def _dependency_cycles(blocks: dict[int, RBDBlock], nodes: dict[int, RBDNode]) -> list[list[tuple[str, int]]]:
    """Every distinct cycle in the graph whose edges are each block's and each
    node's own dependency. Each element has at most one outgoing edge, so a
    walk from each start either ends or closes one cycle."""
    edge: dict[tuple[str, int], tuple[str, int]] = {}
    for block in blocks.values():
        if block.dep_type in _DEPENDS_ON_ELEMENT and block.dependency_num > 0:
            edge[("RBDBlock", block.id)] = (block.dep_type, block.dependency_num)
    for node in nodes.values():
        if node.dep_type in _DEPENDS_ON_ELEMENT and node.dependency_num > 0:
            edge[("RBDNode", node.id)] = (node.dep_type, node.dependency_num)
    cycles: list[list[tuple[str, int]]] = []
    seen_cycle_members: set[tuple[str, int]] = set()
    for start in edge:
        path: list[tuple[str, int]] = []
        position: dict[tuple[str, int], int] = {}
        current: tuple[str, int] | None = start
        while current is not None and current in edge and current not in position:
            position[current] = len(path)
            path.append(current)
            current = edge[current]
        if current is not None and current in position:
            cycle = path[position[current]:]
            if not seen_cycle_members.intersection(cycle):
                seen_cycle_members.update(cycle)
                cycles.append(cycle)
    return cycles


def _cold_candidates(node: RBDNode, links: dict[int, Link]) -> set[int]:
    """Ids of the blocks whose links are this cold node's candidate paths."""
    candidates: set[int] = set()
    for link_id in node.cold_links:
        link = links[link_id]
        ref = link.resolved_from_kind or link.connect_f_ref
        if ref == "RBDBlock":
            candidates.add(link.resolved_from_id if link.resolved_from_kind else link.connect_from_id)
    return candidates


def check_runnable(blocks: dict[int, RBDBlock], nodes: dict[int, RBDNode],
                   links: dict[int, Link]) -> list[ModelProblem]:
    """Every reason this model is refused (Raptor 7 crashes on it); empty when it can run."""
    problems: list[ModelProblem] = []
    for node in nodes.values():
        if not node.cold_standby:
            continue
        candidates = _cold_candidates(node, links)
        for block_id in sorted(candidates):
            block = blocks[block_id]
            if block.dep_type == "RBDBlock" and block.dependency_num in candidates and block.dependency_num != block_id:
                target = blocks[block.dependency_num]
                problems.append(ModelProblem(
                    f"block {block.name!r}",
                    f"it is one of the cold-standby paths into node {node.node_name!r} and depends on "
                    f"{target.name!r}, another path into the same node. Raptor 7.0.11 crashes "
                    "(MODSIM Runtime error: Access Violation) on this shape, so it is refused. "
                    "Remove the dependency, or take the block out of the cold-standby node."))
    return problems


def refuse_unrunnable(blocks: dict[int, RBDBlock], nodes: dict[int, RBDNode], links: dict[int, Link]) -> None:
    """Raise `UnrunnableModel` when `check_runnable` finds anything."""
    problems = check_runnable(blocks, nodes, links)
    if problems:
        raise UnrunnableModel(problems)



def dependency_cycle_warnings(blocks: dict[int, RBDBlock], nodes: dict[int, RBDNode]) -> list[ModelProblem]:
    """One warning per dependency cycle. Legal -- Raptor 7 runs them -- but easy to
    build by accident and to misread."""
    warnings: list[ModelProblem] = []
    for cycle in _dependency_cycles(blocks, nodes):
        names = [_name(kind, ident, blocks, nodes) for kind, ident in cycle]
        loop = " depends on ".join([*names, names[0]])
        warnings.append(ModelProblem(
            names[0],
            f"circular dependency: {loop}. Raptor 7 runs these, and the captured cases "
            "(idle or induced-failure reactions, one to three blocks) resolve normally, but "
            "check it is intended."))
    return warnings

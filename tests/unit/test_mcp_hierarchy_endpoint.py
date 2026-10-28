"""A hierarchy endpoint must resolve to the legacy `Hier` link kind.

`connect` used to resolve a hierarchy to the friendly kind name
`Hierarchy` and wrote that into LINK_DATA, which the validator and Raptor 7
reject (`unknown ToType='Hierarchy'`).
"""
from __future__ import annotations

from pathlib import Path

from raptor.core.fileio.rbd import load_rbd
from raptor.mcp.tools_model import _resolve_endpoint

GOLDEN = Path(__file__).resolve().parent.parent / "golden"


class _Handle:
    def __init__(self, path: Path):
        self.rbd = load_rbd(str(path))


class _Workspace:
    def __init__(self, path: Path):
        self._handle = _Handle(path)

    def get(self, _model_id: str) -> _Handle:
        return self._handle


def test_hierarchy_name_resolves_to_hier():
    # Every hierarchy in the golden also has a node of the same name, and a
    # bare name prefers the node section -- rename the hierarchy row so the
    # name really does select a hierarchy.
    ws = _Workspace(GOLDEN / "HierarchyExample" / "input.rbd")
    row = ws.get("m").rbd.sections["HIERARCHY_DATA"].rows[0]
    row["hierarchyName"] = "PowerStringOnly"
    kind, ident = _resolve_endpoint(ws, "m", "PowerStringOnly")
    assert (kind, ident) == ("Hier", 23)


def test_hierarchy_list_form_resolves_to_hier():
    ws = _Workspace(GOLDEN / "HierarchyExample" / "input.rbd")
    kind, ident = _resolve_endpoint(ws, "m", ["Hierarchy", 23])
    assert (kind, ident) == ("Hier", 23)

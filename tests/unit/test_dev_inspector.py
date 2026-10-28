"""`/dev` hover text (source-verified; one real capture agrees)."""
from pathlib import Path

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.dev_inspector import inspector_text

GOLD = Path(__file__).resolve().parents[1] / "golden"


def test_block_and_node_text():
    rbd = load_rbd(str(GOLD / "MotorDemo" / "input.rbd"))
    block = next(r for r in rbd.section("BLOCK_DATA").rows if r["ID"] != "0")
    text = inspector_text(rbd, "block", int(block["ID"]))
    assert text.startswith("parentID = 0") and f"Id = {block['ID']}" in text
    assert "con out = 1" in text and "b-5 " in text
    node = rbd.section("NODE_DATA").rows[0]
    assert "typeNode = " in inspector_text(rbd, "node", int(node["ID"]))
    assert inspector_text(rbd, "block", 99999) is None


def test_link_text_names_id_and_endpoints():
    rbd = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    link = rbd.section("LINK_DATA").rows[0]
    text = inspector_text(rbd, "link", int(link["ID"]))
    assert text is not None
    assert text.startswith(f"Id = {link['ID']}")
    assert "parentID = " in text
    assert f"L-{len(rbd.section('LINK_DATA').rows)}" in text
    assert " → " in text
    assert inspector_text(rbd, "link", 99999) is None


def test_hierarchy_text_has_level_fields():
    rbd = load_rbd(str(GOLD / "HierarchyExample" / "input.rbd"))
    hier = rbd.section("HIERARCHY_DATA").rows[0]
    text = inspector_text(rbd, "hier", int(hier["ID"]))
    assert text is not None
    assert "parentID = " in text and "hierLevel = 0" in text
    assert "deepestLevel = " in text
    assert f"Id = {hier['ID']}" in text
    assert "con out = " in text and "level = " in text
    assert "b-" in text and "T-" in text


def test_link_text_resolves_an_event_endpoint():
    """Event links printed 'Event 32'; the event's own name is available
    in EVENT_DATA.eventName."""
    rbd = load_rbd(str(GOLD / "ColdEventProbe" / "input.rbd"))
    text = inspector_text(rbd, "link", 33)
    assert text is not None
    assert "ColdGate" in text
    assert "Event 32" not in text

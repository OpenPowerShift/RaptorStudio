"""Tags travel with their elements: copy/paste (same model and another
model), Duplicate, Import RBD as hierarchy and Extract Hierarchy."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core import tags as T
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import save_rbds

GOLD = Path(__file__).resolve().parents[1] / "golden"

pytestmark = pytest.mark.gui


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _view(name):
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(str(GOLD / name / "input.rbd")))
    return v


def _blocks(v) -> list[int]:
    return sorted(b for b in v.blocks if b)


def _new_blocks(v, before: set[int]) -> list[int]:
    return sorted(b for b in v.blocks if b and b not in before)


def test_copy_paste_puts_the_tags_on_the_copies_and_undo_removes_them(qapp):
    v = _view("TwoBlockRedundancy")
    a, b = _blocks(v)
    T.set_tags(v._rbd_ref, f"block:{a}", ["pumps"])
    T.set_tag_info(v._rbd_ref, "pumps", colour="#123456")
    v._scene.clearSelection()
    v._block_items[a].setSelected(True)
    v.copy_selection()
    before = set(v.blocks)
    v.paste(at_model=(300.0, 50.0))
    (copy,) = _new_blocks(v, before)
    assert T.tags_for(v._rbd_ref, f"block:{copy}") == ["pumps"]
    assert T.tags_for(v._rbd_ref, f"block:{b}") == []
    v._stack.undo()                       # one step: elements and tags
    assert f"block:{copy}" not in (v._rbd_ref.tags or {})
    assert T.tag_index(v._rbd_ref)["pumps"] == {f"block:{a}"}


def test_paste_into_another_model_keeps_the_targets_own_colour(qapp):
    src = _view("TwoBlockRedundancy")
    a = _blocks(src)[0]
    T.set_tags(src._rbd_ref, f"block:{a}", ["pumps", "rotating"])
    T.set_tag_info(src._rbd_ref, "pumps", colour="#111111")
    T.set_tag_info(src._rbd_ref, "rotating", description="moving")
    src._scene.clearSelection()
    src._block_items[a].setSelected(True)
    src.copy_selection()
    dst = _view("TwoBlockRedundancy")
    T.set_tag_info(dst._rbd_ref, "pumps", colour="#222222")
    before = set(dst.blocks)
    dst.paste(at_model=(300.0, 50.0), buffer=src._paste_buffer)
    (copy,) = _new_blocks(dst, before)
    assert T.tags_for(dst._rbd_ref, f"block:{copy}") == ["pumps", "rotating"]
    assert T.tag_colour(dst._rbd_ref, "pumps") == "#222222"
    assert T.tag_description(dst._rbd_ref, "rotating") == "moving"


def test_the_clipboard_payload_round_trips_the_tags(qapp):
    from raptor.core.edit import clipboard_payload as cp
    v = _view("TwoBlockRedundancy")
    a = _blocks(v)[0]
    T.set_tags(v._rbd_ref, f"block:{a}", ["pumps"])
    v._scene.clearSelection()
    v._block_items[a].setSelected(True)
    v.copy_selection()
    payload = cp.decode(cp.encode(v._paste_buffer))
    assert payload is not None
    assert ("tags", {"ref": f"block:{a}", "tags": '["pumps"]'}) in \
        payload.buffer
    assert "tag" not in cp.summary_text(payload.buffer)


def test_duplicate_tags_the_duplicates(qapp):
    v = _view("TwoBlockRedundancy")
    a = _blocks(v)[0]
    T.set_tags(v._rbd_ref, f"block:{a}", ["pumps"])
    v._scene.clearSelection()
    v._block_items[a].setSelected(True)
    before = set(v.blocks)
    v.duplicate_selection()
    (copy,) = _new_blocks(v, before)
    assert T.tags_for(v._rbd_ref, f"block:{copy}") == ["pumps"]


def test_import_rbd_as_hierarchy_carries_the_files_tags(qapp, tmp_path):
    source = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    a = min(int(r["ID"]) for r in source.sections["BLOCK_DATA"].rows
            if int(r["ID"]) > 0)
    T.set_tags(source, f"block:{a}", ["imported"])
    T.set_tag_info(source, "imported", description="from the vendor model")
    path = tmp_path / "part.rbds"
    save_rbds(source, path, meta={"file_name": path.name})
    v = _view("TwoBlockRedundancy")
    host_blocks = {f"block:{b}" for b in _blocks(v)}
    hid = v.import_rbd_as_hierarchy(str(path))
    tagged = T.tag_index(v._rbd_ref)["imported"]
    assert len(tagged) == 1 and not tagged & host_blocks
    (ref,) = tagged
    assert ref in T.inside(v._rbd_ref, hid)
    assert T.tag_description(v._rbd_ref, "imported") == \
        "from the vendor model"
    v._stack.undo()
    assert "imported" not in T.all_tags(v._rbd_ref)


def test_extract_keeps_only_the_extracted_elements_tags(qapp):
    from raptor.api import isolate
    rbd = load_rbd(str(GOLD / "HierarchyExample" / "input.rbd"))
    hid = next(int(r["ID"]) for r in rbd.sections["HIERARCHY_DATA"].rows
               if int(r["ID"]) > 0)
    inside = sorted(r for r in T.inside(rbd, hid) if r.startswith("block:"))
    outside = next(f"block:{r['ID']}" for r in
                   rbd.sections["BLOCK_DATA"].rows
                   if int(r["ID"]) > 0
                   and f"block:{r['ID']}" not in T.inside(rbd, hid))
    T.set_tags(rbd, inside[0], ["kept"])
    T.set_tags(rbd, outside, ["left behind"])
    T.set_tag_info(rbd, "left behind", colour="#123456")
    out, _record = isolate.extract(rbd, hid, {})
    assert T.tags_for(out, inside[0]) == ["kept"]
    assert T.all_tags(out) == ["kept"]

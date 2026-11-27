"""Paste Special: the Qt-free planning, the API and the carried comment."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api import edit
from raptor.api.workspace import Workspace
from raptor.core import tags as tag_model
from raptor.core.edit import paste_special as ps
from raptor.core.edit.clipboard_plan import DATA_GROUPS
from raptor.core.fileio.comments import comment_text
from raptor.core.fileio.rbds import load_rbds

ROOT = Path(__file__).resolve().parents[2]
DC2 = ROOT / "new_docs" / "models" / "DC2.rbds"


@pytest.fixture()
def rbd():
    return load_rbds(str(DC2))


def _blocks(rbd):
    return [r for r in rbd.sections["BLOCK_DATA"].rows
            if r.get("ID") not in ("0", "")]


def _target(rbd, row, kind="block"):
    return ps.Target(kind, int(row["ID"]), row)


def test_every_data_group_column_exists_in_a_real_model(rbd):
    """A column that is not in the file copies nothing, silently: it hid
    the Logistics delay parameters and the running cost."""
    columns = set(rbd.sections["BLOCK_DATA"].columns)
    for group, cols in DATA_GROUPS.items():
        assert not [c for c in cols if c not in columns], group
    assert set(ps.DEPENDENCY_COLUMNS) <= columns


def test_unknown_groups_and_modes_are_refused():
    with pytest.raises(ValueError, match="unknown data group"):
        ps.check_groups(["Failure", "Nonsense"])
    with pytest.raises(ValueError, match="tag_mode"):
        ps.check_groups(["Tags"], tag_mode="merge")
    with pytest.raises(ValueError, match="comment_mode"):
        ps.check_groups(["Comment"], comment_mode="prepend")


def test_data_groups_copy_only_their_columns(rbd):
    a, b = _blocks(rbd)[:2]
    a["Running"], b["Running"] = "9.5", "1.0"
    a["Param1#1"], b["Param1#1"] = "3", "0"
    plan = ps.plan_special(rbd, ps.source_from_model(rbd, "block", a),
                           [_target(rbd, b)], ["Costs", "Logistics delays"])
    cols = {c["column"] for c in plan.cells}
    assert {"Running", "Param1#1"} <= cols
    assert not cols & {"blockName", "ID", "xPos", "yPos", "FDist"}
    assert plan.elements == 1 and plan.unchanged == 0


def test_tags_add_replace_and_the_colour_of_a_new_tag(rbd):
    a, b = _blocks(rbd)[:2]
    ra, rb = tag_model.element_ref("block", a["ID"]), tag_model.element_ref("block", b["ID"])
    tag_model.set_tags(rbd, ra, ["pump", "critical"])
    tag_model.set_tags(rbd, rb, ["site"])
    tag_model.set_tag_info(rbd, "critical", colour="#ff0000",
                           description="must not fail")
    source = ps.source_from_model(rbd, "block", a)
    add = ps.plan_special(rbd, source, [_target(rbd, b)], ["Tags"])
    assert add.tag_changes[0]["new"] == ["site", "pump", "critical"]
    assert add.tags_after[1]["critical"]["description"] == "must not fail"
    rep = ps.plan_special(rbd, source, [_target(rbd, b)], ["Tags"],
                          tag_mode="replace")
    assert rep.tag_changes[0]["new"] == ["pump", "critical"]
    # nothing was written by planning
    assert tag_model.tags_for(rbd, rb) == ["site"]


def test_a_target_that_already_matches_is_counted_not_changed(rbd):
    a, b = _blocks(rbd)[:2]
    for row in (a, b):
        tag_model.set_tags(rbd, tag_model.element_ref("block", row["ID"]), ["x"])
    plan = ps.plan_special(rbd, ps.source_from_model(rbd, "block", a),
                           [_target(rbd, b)], ["Tags"])
    assert plan.fields == 0 and plan.unchanged == 1
    assert "already match" in plan.summary()


def test_comment_modes(rbd):
    a, b = _blocks(rbd)[:2]
    source = ps.Source("block", int(a["ID"]), a, comment="from A")
    target = _target(rbd, b)
    assert ps.write_comment(rbd, "block", target.id, target.name, "own~second")
    replace = ps.plan_special(rbd, source, [target], ["Comment"])
    assert replace.comments[0]["new"] == "from A"
    append = ps.plan_special(rbd, source, [target], ["Comment"],
                             comment_mode="append")
    assert append.comments[0]["new"] == "own~second~from A"
    only = ps.plan_special(rbd, source, [target], ["Comment"],
                           comment_mode="empty")
    assert only.comments == []
    ps.write_comment(rbd, "block", target.id, target.name, "")
    only = ps.plan_special(rbd, source, [target], ["Comment"],
                           comment_mode="empty")
    assert only.comments[0]["new"] == "from A"


def test_dependency_is_not_copied_onto_what_it_names_or_between_models(rbd):
    a, b, c = _blocks(rbd)[:3]
    a.update(DepNum=b["ID"], DepType="RBDBlock", DepIdle="100")
    source = ps.source_from_model(rbd, "block", a)
    plan = ps.plan_special(rbd, source, [_target(rbd, b), _target(rbd, c)],
                           ["Dependency"])
    assert {int(ch["id"]) for ch in plan.cells} == {int(c["ID"])}
    assert plan.notes
    away = ps.Source("block", int(a["ID"]), dict(a), same_model=False)
    why = ps.applicability(away, [_target(rbd, c)])
    assert "another model" in why["Dependency"]
    assert not ps.plan_special(rbd, away, [_target(rbd, c)],
                               ["Dependency"]).cells


def test_applicability_follows_the_kinds_selected(rbd):
    a, b = _blocks(rbd)[:2]
    source = ps.source_from_model(rbd, "block", a)
    assert all(v == "" for g, v in ps.applicability(
        source, [_target(rbd, b)]).items() if g not in ("Phasing",))
    node = next(r for r in rbd.sections["NODE_DATA"].rows
                if r.get("ID") not in ("0", ""))
    only_node = ps.applicability(source, [_target(rbd, node, "node")])
    assert only_node["Tags"] == "" and only_node["Comment"] == ""
    assert "no block" in only_node["Failure"]
    assert "nothing" in ps.applicability(source, [])["Tags"]
    # the copied block alone: nothing to paste onto
    assert "only the copied" in ps.applicability(
        source, [_target(rbd, a)])["Tags"]


def test_a_buffer_carries_tags_and_comments_to_the_source(rbd):
    a = _blocks(rbd)[0]
    ref = tag_model.element_ref("block", a["ID"])
    tag_model.set_tags(rbd, ref, ["hot"])
    tag_model.set_tag_info(rbd, "hot", colour="#123456")
    ps.write_comment(rbd, "block", int(a["ID"]), a["blockName"], "a~b")
    buffer = [("block", dict(a))]
    buffer += tag_model.buffer_items(rbd, buffer) + ps.comment_items(rbd, buffer)
    source = ps.source_from_buffer(buffer, same_model=False)
    assert source.tags == ["hot"] and source.comment == "a~b"
    assert source.tag_info["hot"]["colour"] == "#123456"


@pytest.fixture()
def workspace(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.register_model(load_rbds(str(DC2)), name="dc2")["model_id"]
    return ws, mid


def test_api_copies_tags_and_comment_between_any_elements(workspace):
    ws, mid = workspace
    rbd = ws.get(mid).rbd
    a, b = _blocks(rbd)[:2]
    tag_model.set_tags(rbd, tag_model.element_ref("block", a["ID"]), ["t1"])
    ps.write_comment(rbd, "block", int(a["ID"]), a["blockName"], "note")
    hier = rbd.sections["HIERARCHY_DATA"].rows[1]
    result = edit.copy_element_data(
        ws, mid, f"Block:{a['ID']}",
        [f"Block:{b['ID']}", f"Hier:{hier['ID']}"], ["Tags", "Comment"])
    assert result["count"] == 4 and result["elements"] == 2
    assert tag_model.tags_for(rbd, f"hier:{hier['ID']}") == ["t1"]
    assert comment_text(rbd.sections.get("COMMENTS"), "hier",
                        int(hier["ID"]), hier["hierarchyName"]) == "note"
    again = edit.copy_element_data(ws, mid, f"Block:{a['ID']}",
                                   [f"Block:{b['ID']}"], ["Tags", "Comment"])
    assert again["count"] == 0 and again["unchanged"] == 1


def test_api_refuses_unknown_groups_and_non_block_data_targets(workspace):
    ws, mid = workspace
    rbd = ws.get(mid).rbd
    a, b = _blocks(rbd)[:2]
    with pytest.raises(ValueError, match="unknown data group"):
        edit.copy_element_data(ws, mid, f"Block:{a['ID']}",
                               [f"Block:{b['ID']}"], ["Failure", "Nonsense"])
    hier = rbd.sections["HIERARCHY_DATA"].rows[1]
    with pytest.raises(ValueError, match="not a block"):
        edit.copy_element_data(ws, mid, f"Block:{a['ID']}",
                               [f"Hier:{hier['ID']}"], ["Failure"])
    with pytest.raises(ValueError, match="comment_mode"):
        edit.copy_element_data(ws, mid, f"Block:{a['ID']}",
                               [f"Block:{b['ID']}"], ["Comment"],
                               comment_mode="x")


def test_api_still_copies_failure_and_repair_by_default(workspace):
    ws, mid = workspace
    rbd = ws.get(mid).rbd
    a, b = _blocks(rbd)[:2]
    a["FParam1"], b["FParam1"] = "123.0", "5.0"
    result = edit.copy_element_data(ws, mid, f"Block:{a['ID']}",
                                    [f"Block:{b['ID']}"])
    assert b["FParam1"] == "123.0" and result["count"] >= 1

"""Component groups and tags: format round-trip and membership rules."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core import groups as G
from raptor.core.fileio.rbds import load_rbds, parse_rbds, render_rbds

MODEL = Path(__file__).resolve().parents[2] / "new_docs" / "models" / "DC1.rbds"


@pytest.fixture()
def rbd():
    return load_rbds(str(MODEL))


def _first_block(rbd) -> int:
    from raptor.core.fileio.typed import row_int
    for row in rbd.sections["BLOCK_DATA"].rows:
        oid = row_int(row.get("ID"), 0)
        if oid > 0:
            return oid
    raise AssertionError("fixture has no blocks")


def test_tags_and_groups_round_trip_through_rbds(rbd):
    bid = _first_block(rbd)
    ref = G.element_ref("block", bid)
    assert G.set_tags(rbd, ref, ["Pumps", "pumps", " rotating "])
    group = G.add_group(rbd, "Pumps", [ref], colour="#2f80ed",
                        description="Duty and standby")
    text = render_rbds(rbd)
    assert '"tags"' in text and '"groups"' in text

    back = parse_rbds(text)
    assert back.tags[ref] == ["Pumps", "pumps", "rotating"]
    assert back.groups[0]["id"] == group["id"]
    assert back.groups[0]["colour"] == "#2F80ED"
    assert back.groups[0]["members"] == [ref]


def test_a_group_can_be_an_explicit_list_a_tag_query_or_both(rbd):
    b1, b2 = list(G.valid_refs_from_model(rbd))[:2]
    G.set_tags(rbd, b2, ["rotating"])
    explicit = G.add_group(rbd, "One", [b1])
    query = G.add_group(rbd, "Rotating", tag="rotating")
    both = G.add_group(rbd, "Both", [b1], tag="rotating")

    assert G.group_members(rbd, explicit) == {b1}
    assert G.group_members(rbd, query) == {b2}
    assert G.group_members(rbd, both) == {b1, b2}


def test_a_same_named_group_is_updated_not_duplicated(rbd):
    ref = G.element_ref("block", _first_block(rbd))
    first = G.add_group(rbd, "Pumps", [ref])
    second = G.add_group(rbd, "Pumps", [], description="all pumps")
    assert second["id"] == first["id"]
    assert len(G.groups_of(rbd)) == 1
    assert G.group_by_id(rbd, first["id"])["description"] == "all pumps"


def test_prune_drops_deleted_members_but_keeps_the_group(rbd):
    ref = G.element_ref("block", _first_block(rbd))
    G.set_tags(rbd, ref, ["pumps"])
    G.add_group(rbd, "Pumps", [ref])
    G.add_group(rbd, "Rotating", tag="pumps")
    assert G.prune(rbd, []) is True
    assert rbd.tags == {}
    names = {g["name"] for g in G.groups_of(rbd)}
    assert names == {"Pumps", "Rotating"}
    assert all(not g.get("members") for g in G.groups_of(rbd))
    assert G.prune(rbd, []) is False


def test_clean_groups_drops_bad_rows_and_keeps_ids_unique(rbd):
    cleaned = G.clean_groups([
        {"name": "Pumps", "members": ["block:1", "block:1", "nope"]},
        {"name": "Pumps"},
        {"name": ""},
        "not a group",
    ])
    assert [g["name"] for g in cleaned] == ["Pumps", "Pumps"]
    assert cleaned[0]["members"] == ["block:1"]
    assert cleaned[0]["id"] != cleaned[1]["id"]


def test_view_choices_name_groups_then_unclaimed_tags(rbd):
    ref = G.element_ref("block", _first_block(rbd))
    G.add_group(rbd, "Pumps", [ref], tag="pumps")
    G.set_tags(rbd, ref, ["pumps", "Pumps", "rotating"])
    choices = G.view_choices(rbd)
    assert choices[0][:2] == ("Group: Pumps", "group")
    assert ("Tag: rotating", "tag", "rotating") in choices
    # the group's own tag query is not offered a second time, but a tag
    # that differs only in case is a different tag and is still offered
    assert not any(c[1] == "tag" and c[2] == "pumps" for c in choices)
    assert ("Tag: Pumps", "tag", "Pumps") in choices
    assert G.refs_for_view(rbd, "group", choices[0][2]) == {ref}


def test_a_legacy_save_reports_groups_and_tags_as_lost(rbd):
    from raptor.api.workspace import rbds_only_content
    ref = G.element_ref("block", _first_block(rbd))
    G.set_tags(rbd, ref, ["pumps"])
    G.add_group(rbd, "Pumps", [ref])
    lost = rbds_only_content(rbd)
    assert "component tags" in lost and "component groups" in lost


def test_bad_references_are_not_written_into_the_model(rbd):
    import json
    with pytest.raises(ValueError):
        G.set_tags(rbd, "block:0", ["pumps"])
    with pytest.raises(ValueError):
        G.set_tags(rbd, "widget:3", ["pumps"])
    # an unknown ref in a hand-edited file is ignored, not fatal
    valid = G.element_ref("block", _first_block(rbd))
    doc = json.loads(render_rbds(rbd))
    doc["tags"] = {"widget:3": ["x"], valid: ["ok"]}
    back = parse_rbds(json.dumps(doc))
    assert back.tags == {valid: ["ok"]}


def test_add_group_with_no_fields_leaves_an_existing_group_alone(rbd):
    """`add_group` is used as "make sure this group exists"; omitting a
    field must not clear it."""
    ref = G.element_ref("block", _first_block(rbd))
    first = G.add_group(rbd, "Pumps", [ref], colour="#123456",
                        description="Duty and standby", tag="pumps")
    again = G.add_group(rbd, "Pumps")
    assert again["id"] == first["id"]
    assert again["members"] == [ref]
    assert again["colour"] == "#123456"
    assert again["description"] == "Duty and standby"
    assert again["tag"] == "pumps"
    # Passing a field replaces only that field.
    G.add_group(rbd, "Pumps", [], description="")
    current = G.group_by_id(rbd, first["id"])
    assert not current.get("members")
    assert "description" not in current


def test_empty_groups_are_kept_and_taggable(rbd):
    group = G.add_group(rbd, "Spare")
    assert group["id"] == "g_spare"
    assert G.group_members(rbd, group) == set()
    assert G.refs_for_view(rbd, "group", "g_spare") == set()
    assert ("Group: Spare", "group", "g_spare") in G.view_choices(rbd)
    with pytest.raises(ValueError):
        G.add_group(rbd, "   ")


def test_refs_for_view_ignores_an_unknown_kind(rbd):
    ref = G.element_ref("block", _first_block(rbd))
    G.set_tags(rbd, ref, ["pumps"])
    assert G.refs_for_view(rbd, "everything", ref) == set()
    assert G.refs_for_view(rbd, "tag", "") == set()


def test_ancestor_hiers_returns_the_path_to_a_member(rbd):
    """A group member inside a nested hierarchy keeps its whole chain of
    enclosing ovals visible on the canvas."""
    from raptor.core.fileio.typed import row_int
    hier_rows = rbd.sections["HIERARCHY_DATA"].rows
    top = next(row_int(r.get("ID"), 0) for r in hier_rows
               if row_int(r.get("ID"), 0) > 0)
    nested = dict(next(r for r in hier_rows
                       if row_int(r.get("ID"), 0) == top))
    nested.update({"ID": "999", "Parent": str(top),
                   "hierarchyName": "Nested"})
    hier_rows.append(nested)
    block = dict(rbd.sections["BLOCK_DATA"].rows[0])
    block.update({"ID": "999", "ParentId": "999", "blockName": "Deep"})
    rbd.sections["BLOCK_DATA"].rows.append(block)

    assert G.ancestor_hiers(rbd, ["block:999"]) == {"hier:999", f"hier:{top}"}
    # A hierarchy member includes itself and its parents.
    assert G.ancestor_hiers(rbd, ["hier:999"]) == {"hier:999", f"hier:{top}"}
    # Unknown or malformed refs contribute nothing.
    assert G.ancestor_hiers(rbd, ["block:424242", "nope"]) == set()


def test_a_tags_value_that_is_a_bare_string_is_read_as_one_tag(rbd):
    """Hand-edited files and old tooling may write `"pumps"` instead of
    `["pumps"]`; it must not become a set of single-letter tags."""
    ref = G.element_ref("block", _first_block(rbd))
    rbd.tags = {ref: "pumps"}
    assert G.tags_for(rbd, ref) == ["pumps"]
    assert G.tag_index(rbd) == {"pumps": {ref}}
    assert G.all_tags(rbd) == ["pumps"]

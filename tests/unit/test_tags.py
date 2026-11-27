"""Component tags: format round-trip, colour/description, rename/delete,
and "tag within a hierarchy"."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from raptor.core import tags as T
from raptor.core.fileio.rbds import load_rbds, parse_rbds, render_rbds
from raptor.core.fileio.typed import row_int

MODEL = Path(__file__).resolve().parents[2] / "new_docs" / "models" / "DC1.rbds"
HIER = Path(__file__).resolve().parents[2] / "tests" / "golden" / \
    "HierarchyExample" / "input.rbd"


@pytest.fixture()
def rbd():
    return load_rbds(str(MODEL))


@pytest.fixture()
def hier_rbd():
    from raptor.core.fileio.rbd import load_rbd
    return load_rbd(str(HIER))


def _blocks(rbd) -> list[int]:
    return [row_int(r.get("ID"), 0) for r in rbd.sections["BLOCK_DATA"].rows
            if row_int(r.get("ID"), 0) > 0]


def test_tags_and_tag_info_round_trip_through_rbds(rbd):
    a, b = _blocks(rbd)[:2]
    T.set_tags(rbd, f"block:{a}", ["pumps", "rotating"])
    T.set_tags(rbd, f"block:{b}", ["pumps"])
    T.set_tag_info(rbd, "pumps", colour="#123456", description="Feed pumps")
    T.set_tag_info(rbd, "spare", description="Not applied yet")
    back = parse_rbds(render_rbds(rbd))
    assert T.tags_for(back, f"block:{a}") == ["pumps", "rotating"]
    assert T.tag_colour(back, "pumps") == "#123456"
    assert T.tag_description(back, "pumps") == "Feed pumps"
    # an empty, described tag survives a save
    assert "spare" in T.all_tags(back)


def test_a_default_colour_is_not_stored(rbd):
    tag = "pumps"
    T.set_tag_info(rbd, tag, colour=T.default_colour(tag))
    assert T.clean_tag_info(rbd.tag_info)[tag] == {}
    assert T.tag_colour(rbd, tag) == T.default_colour(tag)
    # the same name is the same colour every time
    assert T.default_colour(tag) == T.default_colour("pumps")


def test_a_bad_colour_is_refused(rbd):
    with pytest.raises(ValueError):
        T.set_tag_info(rbd, "pumps", colour="blue")


def test_rename_moves_the_tag_and_its_info_and_merges(rbd):
    a, b = _blocks(rbd)[:2]
    T.set_tags(rbd, f"block:{a}", ["pump"])
    T.set_tags(rbd, f"block:{b}", ["pumps"])
    T.set_tag_info(rbd, "pump", colour="#111111", description="old")
    assert T.rename_tag(rbd, "pump", "feed pump") == 1
    assert T.tags_for(rbd, f"block:{a}") == ["feed pump"]
    assert T.tag_colour(rbd, "feed pump") == "#111111"
    assert "pump" not in T.all_tags(rbd)
    # onto an existing tag: merged
    T.rename_tag(rbd, "feed pump", "pumps")
    assert T.tag_index(rbd)["pumps"] == {f"block:{a}", f"block:{b}"}


def test_delete_removes_the_tag_everywhere_and_forgets_it(rbd):
    a, b = _blocks(rbd)[:2]
    T.set_tags(rbd, f"block:{a}", ["pumps", "rotating"])
    T.set_tags(rbd, f"block:{b}", ["pumps"])
    T.set_tag_info(rbd, "pumps", description="x")
    assert T.delete_tag(rbd, "pumps") == 2
    assert T.tags_for(rbd, f"block:{a}") == ["rotating"]
    assert T.tags_for(rbd, f"block:{b}") == []
    assert "pumps" not in T.all_tags(rbd)


def test_prune_drops_deleted_elements_but_keeps_tag_info(rbd):
    a = _blocks(rbd)[0]
    T.set_tags(rbd, f"block:{a}", ["pumps"])
    T.set_tags(rbd, "block:99999", ["pumps"])
    T.set_tag_info(rbd, "pumps", description="kept")
    assert T.prune(rbd, T.valid_refs_from_model(rbd))
    assert T.tag_index(rbd)["pumps"] == {f"block:{a}"}
    assert T.tag_description(rbd, "pumps") == "kept"


def test_bad_references_and_bare_strings_are_cleaned():
    assert T.clean_tags({"block:3": "pumps", "nope": ["x"],
                         "block:0": ["y"]}) == {"block:3": ["pumps"]}
    with pytest.raises(ValueError):
        T.set_tags(SimpleNamespace(tags={}), "pump", ["x"])


def test_tag_within_a_hierarchy(hier_rbd):
    hid = next(row_int(r.get("ID"), 0)
               for r in hier_rbd.sections["HIERARCHY_DATA"].rows
               if row_int(r.get("ID"), 0) > 0)
    inside = T.inside(hier_rbd, hid)
    inner = sorted(r for r in inside if r.startswith("block:"))[0]
    outer = next(f"block:{b}" for b in _blocks(hier_rbd)
                 if f"block:{b}" not in inside)
    T.set_tags(hier_rbd, inner, ["pumps"])
    T.set_tags(hier_rbd, outer, ["pumps"])
    assert T.refs_for_view(hier_rbd, "pumps") == {inner, outer}
    assert T.refs_for_view(hier_rbd, "pumps", within=hid) == {inner}
    # the path to the inner block stays visible on the canvas
    assert f"hier:{hid}" in T.ancestor_hiers(hier_rbd, {inner})


def test_a_tagged_hierarchy_expands_to_its_contents(hier_rbd):
    hid = next(row_int(r.get("ID"), 0)
               for r in hier_rbd.sections["HIERARCHY_DATA"].rows
               if row_int(r.get("ID"), 0) > 0)
    expanded = T.expand(hier_rbd, {f"hier:{hid}"})
    assert f"hier:{hid}" in expanded
    assert T.inside(hier_rbd, hid) <= expanded
    assert len(expanded) > 1


def test_a_legacy_save_reports_tags_as_lost(rbd):
    from raptor.api.workspace import rbds_only_content
    T.set_tags(rbd, f"block:{_blocks(rbd)[0]}", ["pumps"])
    T.set_tag_info(rbd, "pumps", description="x")
    lost = rbds_only_content(rbd)
    assert "component tags" in lost
    assert "tag colours and descriptions" in lost


def test_names_of_reads_each_kinds_name_column():
    rbd = load_rbds(MODEL)
    refs = set()
    expected = set()
    for kind, (section, column) in T._NAME_COLUMNS.items():
        sec = rbd.sections.get(section)
        row = next((r for r in (sec.rows if sec else ())
                    if row_int(r.get("ID"), 0) > 0 and r.get(column)), None)
        if row is not None:
            refs.add(f"{kind}:{row_int(row.get('ID'), 0)}")
            expected.add(str(row.get(column)).strip())
    assert len(refs) >= 3
    assert T.names_of(rbd, refs) == expected
    assert T.names_of(rbd, {"block:999999", "nonsense"}) == set()


# -- new tags take distinct colours -----------------------------------------

def _make(rbd, names):
    ids = _blocks(rbd)
    for i, name in enumerate(names):
        before = T.all_tags(rbd)
        T.add_tag(rbd, f"block:{ids[i % len(ids)]}", name)
        T.colour_new_tags(rbd, before)


def test_three_new_tags_get_three_colours_in_palette_order(rbd):
    # "pumps" and "alpha" hash to the same colour; the old rule gave both it
    _make(rbd, ["a", "b", "c"])
    colours = [T.tag_colour(rbd, t) for t in ("a", "b", "c")]
    assert colours == list(T.TAG_COLOURS[:3])


def test_a_chosen_colour_that_is_the_hash_colour_is_still_right(rbd):
    tag = "pumps"
    wanted = T.default_colour(tag)
    # fill the palette up to the colour the name hashes to
    idx = T.TAG_COLOURS.index(wanted)
    filler = [f"f{i}" for i in range(idx)]
    _make(rbd, filler)
    _make(rbd, [tag])
    assert T.tag_colour(rbd, tag) == wanted
    # nothing inferable is stored
    assert "colour" not in T.clean_tag_info(rbd.tag_info).get(tag, {})


def test_the_ninth_tag_reuses_the_least_used_colour(rbd):
    _make(rbd, [f"t{i}" for i in range(8)])
    assert {T.tag_colour(rbd, f"t{i}") for i in range(8)} == set(T.TAG_COLOURS)
    # make the first colour the busiest
    T.set_tag_info(rbd, "t1", colour=T.TAG_COLOURS[0])
    _make(rbd, ["ninth"])
    counts = {c: 0 for c in T.TAG_COLOURS}
    for i in range(8):
        counts[T.tag_colour(rbd, f"t{i}")] += 1
    least = min(counts.values())
    assert counts[T.tag_colour(rbd, "ninth")] == least
    assert T.tag_colour(rbd, "ninth") != T.TAG_COLOURS[0]


def test_existing_hash_coloured_tags_keep_their_colour(rbd):
    a = _blocks(rbd)[0]
    T.set_tags(rbd, f"block:{a}", ["old1", "old2"])
    before = {t: T.tag_colour(rbd, t) for t in ("old1", "old2")}
    back = parse_rbds(render_rbds(rbd))
    assert {t: T.tag_colour(back, t) for t in before} == before
    # colouring nothing new changes nothing
    assert T.colour_new_tags(back, T.all_tags(back)) == []
    assert {t: T.tag_colour(back, t) for t in before} == before


def test_a_new_tag_avoids_an_old_hash_coloured_tag(rbd):
    a = _blocks(rbd)[0]
    T.set_tags(rbd, f"block:{a}", ["old"])
    taken = T.tag_colour(rbd, "old")
    _make(rbd, [f"n{i}" for i in range(7)])
    shown = [T.tag_colour(rbd, t) for t in T.all_tags(rbd)]
    assert len(set(shown)) == len(shown) == 8
    assert shown.count(taken) == 1


def test_new_colours_are_stored_and_survive_a_save(rbd):
    _make(rbd, ["a", "b", "c"])
    back = parse_rbds(render_rbds(rbd))
    assert [T.tag_colour(back, t) for t in "abc"] == list(T.TAG_COLOURS[:3])


def test_member_rows_name_kind_id_and_hierarchy_path(hier_rbd):
    refs = sorted(T.valid_refs_from_model(hier_rbd))
    rows = {r[0]: r for r in T.member_rows(hier_rbd, refs + ["block:99999"])}
    assert set(rows) == set(refs)           # an unknown ref is left out
    for ref, kind, oid, name, _level in rows.values():
        assert ref == f"{kind}:{oid}"
        assert " / " not in name
    nested = [r for r in rows.values() if r[4]]
    assert nested, "the fixture has elements inside a hierarchy"
    hier_names = {r[3] for r in rows.values() if r[1] == "hier"}
    for r in nested:
        assert r[4].split(" / ")[-1] in hier_names
    # a path lists the outermost first, ending at the element's own parent
    deep = [r for r in rows.values() if r[4].count(" / ")]
    for r in deep:
        parent = T._parents(hier_rbd)[r[0]]
        assert r[4].endswith(rows[f"hier:{parent}"][3])

"""Project health and share-safe declassification."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api import health
from raptor.core.fileio.rbds import load_rbds
from raptor.core.redact import RedactOptions, declassify, write_share_copy

MODEL = Path(__file__).resolve().parents[2] / "new_docs" / "models" / "DC1.rbds"


@pytest.fixture()
def rbd():
    return load_rbds(str(MODEL))


def test_health_counts_the_model_and_includes_validation(rbd):
    report = health.report(rbd)
    assert report.counts["blocks"] >= 1
    assert report.counts["links"] >= 1
    assert isinstance(report.errors, list)
    assert report.summary()


def test_health_finds_unnamed_and_duplicate_blocks(rbd):
    from raptor.core.fileio.typed import row_int
    rows = [r for r in rbd.sections["BLOCK_DATA"].rows
            if row_int(r.get("ID"), 0) > 0]
    rows[0]["blockName"] = ""
    rows[1]["blockName"] = rows[2]["blockName"]
    report = health.report(rbd)
    text = "\n".join(i.text() for i in report.issues)
    assert "has no name" in text
    assert "blocks share the name" in text


def test_declassify_removes_names_comments_and_annotations(rbd):
    from raptor.core.fileio.typed import row_int
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if row_int(r.get("ID"), 0) > 0)
    original_name = row["blockName"]
    rbd.annotations = [
        {"id": 1, "kind": "text", "text": "secret note", "parent": 0},
        {"id": 2, "kind": "rect", "parent": 0},
        {"id": 3, "kind": "arrow", "parent": 0},
    ]
    rbd.sections["COMMENTS"].rows.append({
        "Type": "Block", "ID": str(row_int(row.get("ID"), 0)),
        "Name": original_name, "Comment": "private reason"})
    copy, report = declassify(rbd)
    assert copy is not rbd
    assert row["blockName"] == original_name          # source untouched
    assert copy.sections["COMMENTS"].rows == []
    assert copy.annotations == []
    assert report.names >= 1 and report.comments == 1
    assert report.text_annotations == 1
    assert report.graphic_annotations == 2
    assert "Block" in next(
        r["blockName"] for r in copy.sections["BLOCK_DATA"].rows
        if r["ID"] == row["ID"])


def test_annotation_policy_keeps_shapes_but_removes_text(rbd):
    rbd.annotations = [
        {"id": 1, "kind": "text", "text": "secret", "parent": 0},
        {"id": 2, "kind": "rect", "parent": 0},
    ]
    copy, _report = declassify(
        rbd, RedactOptions(annotations="text", comments=False, names=False,
                           metadata=False, advisor=False))
    assert [a["kind"] for a in copy.annotations] == ["rect"]


def test_write_share_copy_writes_rbds_and_returns_the_report(rbd, tmp_path):
    dest, report = write_share_copy(rbd, tmp_path / "share")
    assert dest.suffix == ".rbds" and dest.is_file()
    assert report.removed >= 0
    assert load_rbds(str(dest)).sections["BLOCK_DATA"].rows


def test_the_cli_health_and_declassify_commands(tmp_path, capsys):
    from raptor.cli.main import main
    assert main(["health", str(MODEL)]) in (0, 1)
    out = capsys.readouterr().out
    assert "blocks" in out
    dest = tmp_path / "shared.rbds"
    assert main(["declassify", str(MODEL), str(dest),
                 "--annotations", "all"]) == 0
    assert dest.is_file()
    assert "name(s) replaced" in capsys.readouterr().out


def test_declassify_removes_rates_pool_names_and_references():
    from raptor.core.fileio.rbds import render_rbds
    rbd = load_rbds(str(Path(__file__).resolve().parents[2] /
                        "new_docs" / "models" / "WaterTreatment.rbds"))
    copy, report = declassify(rbd)
    text = render_rbds(copy)
    assert "PumpYears" not in text
    assert "SpareMotors" not in text
    assert "centrifugal pump set" not in text
    assert copy.rates == [] and copy.formulas == {}
    assert report.rates >= 1
    refs = {r.get("SparePoolName") for r in copy.sections["BLOCK_DATA"].rows}
    assert any(str(ref).startswith("SparePool_") for ref in refs)


def test_annotation_anchors_and_unknown_element_keys_do_not_leak(rbd):
    from raptor.core.fileio.rbds import render_rbds
    from raptor.core.fileio.typed import row_int
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if row_int(r.get("ID"), 0) > 0)
    rbd.annotations = [{"id": 1, "kind": "arrow", "parent": 0,
                        "anchor": {"ref": f"block:{row['ID']}",
                                   "target": row["blockName"]}}]
    row["_rbds_extra"] = {"owner": "Alice Smith"}
    copy, _report = declassify(
        rbd, RedactOptions(annotations="keep", comments=False,
                           metadata=True, rates=True))
    text = render_rbds(copy)
    assert "Alice Smith" not in text
    assert row["blockName"] not in text
    anchor = copy.annotations[0]["anchor"]
    assert anchor.get("target") != row["blockName"]
    assert anchor["ref"] == f"block:{row['ID']}"


def test_a_comment_note_is_removed_even_when_annotations_are_kept(rbd):
    rbd.annotations = [{"id": 1, "kind": "text", "parent": 0,
                        "text": "private", "comment_of": "block:1"}]
    copy, report = declassify(
        rbd, RedactOptions(annotations="keep", comments=True,
                           names=False, metadata=False, advisor=False,
                           rates=False))
    assert copy.annotations == []
    assert report.comments == 1


def test_health_counts_named_keyed_sections_and_finds_unused_pools():
    from raptor.api import health
    rbd = load_rbds(str(Path(__file__).resolve().parents[2] /
                        "new_docs" / "models" / "WaterTreatment.rbds"))
    report = health.report(rbd)
    assert report.counts["spare_pools"] >= 1
    from raptor.core.fileio.rbds import load_rbds as _load
    air = _load(str(Path(__file__).resolve().parents[2] /
                    "new_docs" / "models" / "AirlinerMission.rbds"))
    assert health.report(air).counts["phases"] >= 1


def test_declassify_refuses_to_overwrite_the_source(tmp_path):
    source = Path(__file__).resolve().parents[2] / "new_docs" / "models" / "DC1.rbds"
    rbd = load_rbds(str(source))
    with pytest.raises(ValueError, match="must not overwrite"):
        write_share_copy(rbd, source, source=source)
    # and the source is unchanged
    assert load_rbds(str(source)).sections["BLOCK_DATA"].rows[1]["blockName"] != ""


def test_cli_refuses_in_place_declassify_and_keeps_the_source(tmp_path, capsys):
    from raptor.cli.main import main
    src = tmp_path / "plant.rbds"
    src.write_text(Path(MODEL).read_text(), encoding="utf-8")
    before = src.read_text()
    assert main(["declassify", str(src), str(src)]) != 0
    assert src.read_text() == before
    assert "must not overwrite" in capsys.readouterr().err


# -- health summary and counts ------------------------------------------------

def test_health_summary_uses_a_real_singular_for_every_kind():
    from raptor.api.health import HealthReport
    assert HealthReport(counts={"hierarchies": 1}).summary() == "1 hierarchy"
    assert HealthReport(counts={"hierarchies": 3}).summary() == "3 hierarchies"
    assert HealthReport(counts={"spare_pools": 1}).summary() == "1 spare pool"
    assert HealthReport(counts={}).summary() == "no elements"


def test_health_counts_a_canvas_comment_note_as_a_comment(rbd):
    rbd.annotations = [{"id": 1, "kind": "text", "parent": 0,
                        "text": "private reason",
                        "comment_of": {"type": "block", "id": 1}}]
    assert health.report(rbd).comments == 1


def test_duplicate_block_name_finding_locates_the_first_block(rbd):
    from raptor.core.fileio.typed import row_int
    rows = [r for r in rbd.sections["BLOCK_DATA"].rows
            if row_int(r.get("ID"), 0) > 0]
    rows[1]["blockName"] = rows[0]["blockName"]
    report = health.report(rbd)
    issue = next(i for i in report.issues if "share the name" in i.text())
    assert issue.element == f"block:{row_int(rows[0].get('ID'), 0)}"


def test_health_matches_pool_references_case_insensitively():
    rbd = load_rbds(str(Path(__file__).resolve().parents[2] /
                        "new_docs" / "models" / "WaterTreatment.rbds"))
    block = next(r for r in rbd.sections["BLOCK_DATA"].rows
                 if str(r.get("SparePoolName") or "").strip()
                 not in ("", "unnamed"))
    pool = rbd.sections["SPARE_POOL_DATA"].rows[0]
    pool["PoolName"] = str(block["SparePoolName"]).upper()
    report = health.report(rbd)
    assert not any("is not used by any block" in i.text()
                   for i in report.issues)


def test_health_on_a_real_model_with_pools_and_phases():
    base = Path(__file__).resolve().parents[2] / "new_docs" / "models"
    water = health.report(load_rbds(str(base / "WaterTreatment.rbds")))
    assert water.counts["spare_pools"] == 1
    assert water.counts["blocks"] >= 1
    air = health.report(load_rbds(str(base / "AirlinerMission.rbds")))
    assert air.counts["phases"] == 5
    assert "phases" in air.summary()


# -- declassified copy on real models ----------------------------------------

def test_declassified_real_model_keeps_its_structure_and_counts():
    rbd = load_rbds(str(Path(__file__).resolve().parents[2] /
                        "new_docs" / "models" / "WaterTreatment.rbds"))
    before = health.report(rbd)
    copy, _report = declassify(rbd)
    after = health.report(copy)
    assert after.counts == before.counts
    assert len(after.errors) == len(before.errors)
    assert len(after.warnings) == len(before.warnings)
    from raptor.core.fileio.rbds import render_rbds
    text = render_rbds(copy)
    for leaked in ("Intake_Screen", "High_Lift_Pump", "SpareMotors",
                   "PumpYears", "centrifugal pump set"):
        assert leaked not in text


def test_anonymous_names_obey_the_models_own_name_rules():
    from raptor.core.input_rules import new_violations
    rbd = load_rbds(str(Path(__file__).resolve().parents[2] /
                        "new_docs" / "models" / "WaterTreatment.rbds"))
    copy, _report = declassify(rbd)
    # "Block 1" has a space, which RAPTOR's own dialogs refuse; the
    # declassified copy must not gain warnings merely because it is shared.
    assert not new_violations(rbd, copy)


def test_arrow_anchor_names_at_both_ends_do_not_leak(rbd):
    from raptor.core.fileio.rbds import render_rbds
    from raptor.core.fileio.typed import row_int
    first, second = [r for r in rbd.sections["BLOCK_DATA"].rows
                     if row_int(r.get("ID"), 0) > 0][:2]
    rbd.annotations = [{
        "id": 1, "kind": "arrow", "parent": 0,
        "anchor": {"from": first["blockName"],
                   "from_ref": f"block:{row_int(first.get('ID'), 0)}",
                   "to": second["blockName"],
                   "to_ref": f"block:{row_int(second.get('ID'), 0)}"}}]
    copy, _report = declassify(
        rbd, RedactOptions(annotations="keep", comments=False,
                           metadata=False, advisor=False, rates=False))
    anchor = copy.annotations[0]["anchor"]
    assert anchor.get("from") != first["blockName"]
    assert anchor.get("to") != second["blockName"]
    assert anchor["from_ref"] and anchor["to_ref"]
    text = render_rbds(copy)
    assert first["blockName"] not in text
    assert second["blockName"] not in text


def test_an_anchor_that_puts_a_name_in_ref_does_not_leak(rbd):
    from raptor.core.fileio.rbds import render_rbds
    from raptor.core.fileio.typed import row_int
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if row_int(r.get("ID"), 0) > 0)
    name = row["blockName"]
    rbd.annotations = [{"id": 1, "kind": "rect", "parent": 0,
                        "anchor": {"ref": name, "target": name}}]
    copy, _report = declassify(
        rbd, RedactOptions(annotations="keep", comments=False,
                           metadata=False, advisor=False, rates=False))
    anchor = copy.annotations[0]["anchor"]
    assert anchor["ref"] != name and anchor["target"] != name
    assert name not in render_rbds(copy)


def test_a_dangling_pool_reference_is_replaced_as_well(rbd):
    from raptor.core.fileio.rbds import render_rbds
    from raptor.core.fileio.typed import row_int
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if row_int(r.get("ID"), 0) > 0)
    row["SparePoolName"] = "HiddenShelf"
    copy, _report = declassify(rbd)
    text = render_rbds(copy)
    assert "HiddenShelf" not in text
    assert "SparePool_unknown" in text


def test_residual_risks_name_the_options_that_were_kept(rbd):
    _copy, kept = declassify(
        rbd, RedactOptions(names=False, comments=False, annotations="keep",
                           metadata=False, advisor=False, rates=False))
    text = " ".join(kept.risks)
    assert "structure" in text
    for phrase in ("names are kept", "Comments", "annotations are kept",
                   "metadata", "Advisor", "Rate names"):
        assert phrase in text
    _copy, removed = declassify(rbd)
    assert len(removed.risks) == 1
    assert "structure" in removed.risks[0]


def test_cli_declassify_prints_the_residual_risk(tmp_path, capsys):
    from raptor.cli.main import main
    assert main(["declassify", str(MODEL), str(tmp_path / "share.rbds")]) == 0
    out = capsys.readouterr().out
    assert "residual risk:" in out
    assert "structure" in out


def test_cli_health_says_when_there_are_no_issues(capsys):
    from raptor.cli.main import main
    assert main(["health", str(MODEL)]) == 0
    assert "no issues" in capsys.readouterr().out


def test_share_safe_copy_renames_groups_and_tags():
    """Group names, ids, descriptions and tags are typed prose; the copy
    used to keep "Acme Site 7 pumps" verbatim (groups landed after the
    share-safe copy was written)."""
    from raptor.core import groups as g
    rbd = load_rbds(MODEL)
    g.add_group(rbd, "Acme Site 7 pumps", ["block:1"], description="Owned by J. Smith")
    g.add_group(rbd, "Tagged", tag="customer-acme")
    g.set_tags(rbd, "block:1", ["customer-acme", "rotating"])
    out, _report = declassify(rbd)
    text = repr((out.groups, out.tags))
    for secret in ("Acme", "Smith", "customer-acme", "rotating", "Tagged"):
        assert secret not in text
    groups = g.groups_of(out)
    assert [grp["name"] for grp in groups] == ["Group_1", "Group_2"]
    # membership is structure: kept, and the tag-driven group still works
    assert groups[0]["members"] == ["block:1"]
    assert g.group_members(out, groups[1]) == {"block:1"}
    # the original is untouched and names=False keeps them
    assert g.groups_of(rbd)[0]["name"] == "Acme Site 7 pumps"
    kept, _ = declassify(rbd, RedactOptions(names=False))
    assert g.groups_of(kept)[0]["name"] == "Acme Site 7 pumps"

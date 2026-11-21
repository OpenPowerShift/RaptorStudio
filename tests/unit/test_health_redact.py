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

"""`.rbdz`: a ZIP archive of an `.rbds` model and its saved result files."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from raptor.core.fileio import rbdz
from raptor.core.fileio.model_file import load_model_file

MODEL = Path(__file__).resolve().parents[2] / "new_docs" / "models" / "DC1.rbds"


def _model_text() -> str:
    return MODEL.read_text(encoding="utf-8")


def test_round_trip_keeps_the_model_and_result_rows(tmp_path):
    archive = tmp_path / "plant.rbdz"
    result = {
        "final_results.txt": "Availability\t0.99\n",
        "events.txt": "Begin_Run_1\n0.0\tBlock\tA\tGood\n",
    }
    manifest = rbdz.write_archive(
        archive, _model_text(), results=result,
        manifest={"results": {"num_runs": 1, "units": "hours"}},
        created="2026-01-01T00:00:00")

    assert manifest["format"] == rbdz.FORMAT_ID
    assert manifest["version"] == rbdz.VERSION
    assert manifest["results"]["files"]["events.txt"]["bytes"] > 0

    read = rbdz.read_archive(archive)
    assert read.model_text == _model_text()
    assert read.results == result
    assert read.manifest["results"]["num_runs"] == 1
    assert set(read.names) >= {rbdz.MODEL_NAME, rbdz.MANIFEST_NAME,
                              "results/events.txt"}


def test_a_model_only_archive_opens_as_a_model(tmp_path):
    archive = tmp_path / "model.rbdz"
    rbdz.write_archive(archive, _model_text())
    rbd = rbdz.load_model(archive)
    assert rbd.sections["BLOCK_DATA"].rows
    # The one loader the GUI, CLI and MCP use accepts the archive too.
    assert load_model_file(archive).sections["BLOCK_DATA"].rows


def test_entries_are_compressed_and_stream_large_files(tmp_path):
    archive = tmp_path / "large.rbdz"
    rows = [f"{i},Availability,0.{i % 1000:03d}" for i in range(50_000)]
    rbdz.write_archive(archive, _model_text(),
                       results={"availability_plot.txt": rows})
    with zipfile.ZipFile(archive) as zf:
        info = zf.getinfo("results/availability_plot.txt")
        assert info.compress_type == zipfile.ZIP_BZIP2
        assert info.compress_size < info.file_size
        # The manifest is small metadata and stays deflate-readable.
        assert zf.getinfo(rbdz.MANIFEST_NAME).compress_type == \
            zipfile.ZIP_DEFLATED


def test_extract_results_gives_lazy_line_sources(tmp_path):
    archive = tmp_path / "saved.rbdz"
    rbdz.write_archive(archive, _model_text(),
                       results={"final_results.txt": "one\ntwo\n"})
    files, manifest = rbdz.extract_results(archive, tmp_path / "out")
    assert files["final_results.txt"].read_text() == "one\ntwo\n"
    lines = rbdz.ArchivedLines(files["final_results.txt"])
    assert len(lines) == 2 and bool(lines)
    assert lines.read_all_lines() == ["one", "two"]
    assert manifest["format"] == rbdz.FORMAT_ID


def test_an_unsafe_result_name_is_refused(tmp_path):
    archive = tmp_path / "bad.rbdz"
    with pytest.raises(rbdz.RbdzError):
        rbdz.write_archive(archive, _model_text(),
                           results={"../escape.txt": "no"})
    assert not archive.exists()


def test_an_unsafe_entry_in_a_foreign_archive_is_refused(tmp_path):
    archive = tmp_path / "evil.rbdz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(rbdz.MODEL_NAME, _model_text())
        zf.writestr("../outside.txt", "no")
    with pytest.raises(rbdz.RbdzError):
        rbdz.read_archive(archive)


def test_a_missing_model_entry_is_refused(tmp_path):
    archive = tmp_path / "empty.rbdz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(rbdz.MANIFEST_NAME, json.dumps({"format": rbdz.FORMAT_ID}))
    with pytest.raises(rbdz.RbdzError, match=r"no model\.rbds"):
        rbdz.read_archive(archive)


def test_a_newer_archive_version_is_refused(tmp_path):
    archive = tmp_path / "future.rbdz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(rbdz.MODEL_NAME, _model_text())
        zf.writestr(rbdz.MANIFEST_NAME,
                    json.dumps({"format": rbdz.FORMAT_ID,
                                "version": rbdz.VERSION + 1}))
    with pytest.raises(rbdz.RbdzError, match="newer RAPTOR Studio"):
        rbdz.read_archive(archive)


def test_results_from_a_run_folder_carry_the_files_that_exist(tmp_path):
    run = tmp_path / "runs" / "r_abc"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"run_id": "r_abc",
                                                  "num_runs": 3}))
    (run / "final_results.txt").write_text("Availability 0.9\n")
    (run / "capacity.txt").write_text("")          # empty: not carried
    sources, summary = rbdz.results_from_run(run)
    assert set(sources) == {"summary.json", "final_results.txt"}
    assert summary["num_runs"] == 3

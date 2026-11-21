"""`.rbdz`: a ZIP archive of an `.rbds` model and its saved result files."""
from __future__ import annotations

import json
import struct
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


def test_a_stream_source_is_copied_without_materialising_it(tmp_path,
                                                            monkeypatch):
    plot = tmp_path / "plot.txt"
    plot.write_text("".join(f"row {i}\n" for i in range(1000)))

    class Stream:       # the duck type ResultStream gives the archive writer
        path = str(plot)

        def read_all_lines(self):
            raise AssertionError("the writer materialised the stream")

    def no_read_bytes(self):
        raise AssertionError("the writer read the whole file at once")

    monkeypatch.setattr(Path, "read_bytes", no_read_bytes)
    archive = tmp_path / "stream.rbdz"
    rbdz.write_archive(archive, _model_text(),
                       results={"availability_plot.txt": Stream()})
    with zipfile.ZipFile(archive) as zf:
        assert zf.read("results/availability_plot.txt") == \
            plot.read_text().encode("utf-8")


def test_entries_with_another_spelling_are_read_by_their_stored_name(tmp_path):
    """Normalising a name must not make the reader look up a name that is
    not stored (`./results/x`, backslashes, directory entries)."""
    archive = tmp_path / "foreign.rbdz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("./" + rbdz.MODEL_NAME, _model_text())
        zf.writestr("results/", "")
        zf.writestr("results/nested/", "")
        zf.writestr(".\\results\\nested\\summary.json",
                    json.dumps({"num_runs": 1}))
    read = rbdz.read_archive(archive)
    assert read.results == {"nested/summary.json":
                            json.dumps({"num_runs": 1})}
    assert rbdz.MODEL_NAME in read.names
    files, _ = rbdz.extract_results(archive, tmp_path / "out")
    assert files["nested/summary.json"].read_text() == \
        json.dumps({"num_runs": 1})


def test_a_missing_stream_file_is_reported_as_an_archive_error(tmp_path):
    class Stream:
        path = str(tmp_path / "gone.txt")

        def read_all_lines(self):
            return []

    archive = tmp_path / "gone.rbdz"
    with pytest.raises(rbdz.RbdzError, match="cannot read"):
        rbdz.write_archive(archive, _model_text(),
                           results={"plot.txt": Stream()})
    assert not archive.exists()


def test_a_duplicate_entry_name_is_refused(tmp_path):
    archive = tmp_path / "duplicate.rbdz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(rbdz.MODEL_NAME, _model_text())
        zf.writestr("./" + rbdz.MODEL_NAME, _model_text())
    with pytest.raises(rbdz.RbdzError, match="two entries"):
        rbdz.read_archive(archive)


@pytest.mark.parametrize("name", [
    "..", "../escape.txt", "/absolute.txt", "C:/evil.txt",
    "nested/C:/evil.txt", "nested/../../escape.txt", "a\x00b.txt",
])
def test_the_writer_refuses_unsafe_result_names(tmp_path, name):
    archive = tmp_path / "bad.rbdz"
    with pytest.raises(rbdz.RbdzError):
        rbdz.write_archive(archive, _model_text(), results={name: "no"})
    assert not archive.exists()
    assert not list(tmp_path.glob("*.part"))


def test_a_manifest_that_is_not_an_object_is_refused(tmp_path):
    archive = tmp_path / "list-manifest.rbdz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(rbdz.MODEL_NAME, _model_text())
        zf.writestr(rbdz.MANIFEST_NAME, "[]")
    with pytest.raises(rbdz.RbdzError, match="not a JSON object"):
        rbdz.read_archive(archive)


def test_a_model_that_does_not_match_its_manifest_hash_is_refused(tmp_path):
    archive = tmp_path / "tampered.rbdz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(rbdz.MODEL_NAME, _model_text())
        zf.writestr(rbdz.MANIFEST_NAME, json.dumps({
            "format": rbdz.FORMAT_ID,
            "version": rbdz.VERSION,
            "model": {"sha256": "0" * 64},
        }))
    with pytest.raises(rbdz.RbdzError, match="does not match"):
        rbdz.read_archive(archive)


def test_a_result_file_that_is_not_utf8_is_refused(tmp_path):
    archive = tmp_path / "binary.rbdz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(rbdz.MODEL_NAME, _model_text())
        zf.writestr("results/plot.txt", b"\xff\xfe\x00")
    with pytest.raises(rbdz.RbdzError, match="not UTF-8"):
        rbdz.read_archive(archive)
    # A model-only open never decodes the result text.
    assert rbdz.read_archive(archive, with_results=False).model_text


def _corrupt_entry(path: Path, entry: str) -> None:
    """Flip a byte inside one entry's compressed payload."""
    data = bytearray(path.read_bytes())
    name = entry.encode()
    header = data.find(name) - 30
    assert data[header:header + 4] == b"PK\x03\x04"
    name_len = struct.unpack_from("<H", data, header + 26)[0]
    extra_len = struct.unpack_from("<H", data, header + 28)[0]
    payload = header + 30 + name_len + extra_len
    data[payload] ^= 0xFF
    path.write_bytes(data)


def test_a_damaged_model_entry_is_refused_as_an_archive_error(tmp_path):
    archive = tmp_path / "damaged.rbdz"
    rbdz.write_archive(archive, _model_text(), compression="fast")
    _corrupt_entry(archive, rbdz.MODEL_NAME)
    with pytest.raises(rbdz.RbdzError, match="could not be read"):
        rbdz.read_archive(archive)
    with pytest.raises(rbdz.RbdzError, match="could not be read"):
        rbdz.load_model(archive)


def test_a_damaged_result_entry_is_refused_during_extraction(tmp_path):
    archive = tmp_path / "damaged-result.rbdz"
    rbdz.write_archive(archive, _model_text(),
                       results={"plot.txt": "row\n" * 1000},
                       compression="fast")
    _corrupt_entry(archive, "results/plot.txt")
    with pytest.raises(rbdz.RbdzError, match="could not be read"):
        rbdz.extract_results(archive, tmp_path / "out")
    # The model alone never touches the damaged result stream.
    assert rbdz.read_archive(archive, with_results=False).model_text


def test_a_file_that_is_not_a_zip_is_refused(tmp_path):
    archive = tmp_path / "text.rbdz"
    archive.write_text("not a zip")
    with pytest.raises(rbdz.RbdzError, match="not a ZIP archive"):
        rbdz.read_archive(archive)
    with pytest.raises(rbdz.RbdzError, match="not a ZIP archive"):
        rbdz.extract_results(archive, tmp_path / "out")


def test_a_model_only_open_is_not_blocked_by_large_results(tmp_path):
    archive = tmp_path / "large.rbdz"
    rbdz.write_archive(archive, _model_text(),
                       results={"availability_plot.txt": "x" * 5000})
    with zipfile.ZipFile(archive) as zf:
        sizes = {info.filename: info.file_size for info in zf.infolist()}
    limit = sizes[rbdz.MODEL_NAME] + sizes[rbdz.MANIFEST_NAME] + 10
    with pytest.raises(rbdz.RbdzError, match="would unpack"):
        rbdz.read_archive(archive, max_uncompressed_bytes=limit)
    model_only = rbdz.read_archive(archive, with_results=False,
                                   max_uncompressed_bytes=limit)
    assert model_only.model_text == _model_text()


@pytest.mark.parametrize(("profile", "expected"), [
    ("fast", zipfile.ZIP_DEFLATED),
    ("balanced", zipfile.ZIP_BZIP2),
    ("smallest", zipfile.ZIP_LZMA),
])
def test_compression_profiles_apply_to_large_entries(tmp_path, profile,
                                                     expected):
    archive = tmp_path / f"{profile}.rbdz"
    rows = [f"{i},Availability,0.{i % 1000:03d}" for i in range(500)]
    rbdz.write_archive(archive, _model_text(),
                       results={"availability_plot.txt": rows},
                       compression=profile)
    with zipfile.ZipFile(archive) as zf:
        assert zf.getinfo("results/availability_plot.txt").compress_type == \
            expected
        assert zf.getinfo(rbdz.MANIFEST_NAME).compress_type == \
            zipfile.ZIP_DEFLATED


def test_an_unknown_compression_profile_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown compression"):
        rbdz.write_archive(tmp_path / "x.rbdz", _model_text(),
                           compression="turbo")


def test_has_results_tolerates_a_malformed_manifest():
    archive = rbdz.RbdzArchive(path="x.rbdz", model_text="",
                               manifest={"results": "nonsense"})
    assert not archive.has_results


def test_the_workspace_opens_an_archive_and_materialises_its_model(tmp_path):
    """The API/CLI/MCP loader keeps the archive as provenance and gives the
    rest of the workspace an ordinary model.rbds."""
    from raptor.api.workspace import Workspace

    archive = tmp_path / "plant.rbdz"
    rbdz.write_archive(archive, _model_text(),
                       results={"summary.json": "{}"})
    ws = Workspace(tmp_path / "workspace")
    model_id = ws.open_model(str(archive))["model_id"]
    handle = ws.get(model_id)
    assert handle.rbd.sections["BLOCK_DATA"].rows
    model_dir = ws.root / "models" / model_id
    assert (model_dir / "source.rbdz").is_file()
    assert (model_dir / "model.rbds").is_file()


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

"""Version history: record, dedupe, retention and restore."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.core.history import HistoryStore


def _model(tmp_path: Path, text: str = "model-v1") -> Path:
    path = tmp_path / "plant.rbds"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_save_records_one_version_and_dedupes_identical_bytes(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    first = store.record(path, reason="save")
    assert first is not None and first.reason == "save"
    assert store.record(path, reason="save") is None       # same bytes
    path.write_text("model-v2", encoding="utf-8")
    second = store.record(path, reason="manual", label="before change")
    assert second is not None and second.id != first.id
    shots = store.snapshots(path)
    assert [s.label for s in shots[:2]] == ["before change", ""]
    assert shots[0].sha256 != shots[1].sha256


def test_restore_writes_the_chosen_version_and_keeps_the_history(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    old = store.record(path)
    path.write_text("model-v2", encoding="utf-8")
    store.record(path)
    store.restore(old)
    assert path.read_text() == "model-v1"
    assert len(store.snapshots(path)) == 2


def test_restore_can_write_a_copy(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    shot = store.record(path)
    copy = tmp_path / "copy.rbds"
    store.restore(shot, copy)
    assert copy.read_text() == "model-v1"
    assert path.read_text() == "model-v1"


def test_retention_keeps_the_newest_versions_and_deletes_files(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history", keep=2, max_bytes=10_000)
    ids = []
    for i in range(4):
        path.write_text(f"model-{i}", encoding="utf-8")
        shot = store.record(path)
        ids.append(shot.id)
    shots = store.snapshots(path)
    assert [s.id for s in shots] == [ids[3], ids[2]]
    files = {p.name for p in (tmp_path / "history").rglob("*.rbds")}
    assert len(files) == 2


def test_bytes_can_be_recorded_before_they_reach_disk(tmp_path):
    path = tmp_path / "plant.rbds"
    store = HistoryStore(tmp_path / "history")
    shot = store.record(path, data=b"in-memory model")
    assert shot is not None
    assert store.read(shot) == b"in-memory model"


def test_delete_removes_only_the_named_version(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    first = store.record(path)
    path.write_text("model-v2", encoding="utf-8")
    store.record(path)
    assert store.discard(path, first.id)
    assert [s.id for s in store.snapshots(path)] != [first.id]
    assert not first.path.exists()


def test_the_cli_lists_snapshots_and_restores_a_copy(tmp_path, monkeypatch, capsys):
    from raptor.cli.main import main
    import raptor.api.history as api_history
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    first = store.record(path, reason="manual")
    monkeypatch.setattr(api_history, "default_store", lambda: store)
    assert main(["history", str(path)]) == 0
    out = capsys.readouterr().out
    assert first.id in out and "manual" in out

    copy = tmp_path / "old.rbds"
    assert main(["history", str(path), "--restore", first.id,
                 "-o", str(copy)]) == 0
    assert copy.read_text() == "model-v1"
    assert "restored" in capsys.readouterr().out


def test_restore_checks_the_checksum_and_leaves_the_target_alone(tmp_path):
    from raptor.core.history import HistoryError
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    shot = store.record(path)
    shot.path.write_bytes(b"corrupt")
    before = path.read_text()
    with pytest.raises(HistoryError, match="checksum"):
        store.restore(shot)
    assert path.read_text() == before


def test_restore_preserves_mode_and_writes_through_a_symlink(tmp_path):
    real = _model(tmp_path)
    real.chmod(0o664)
    link = tmp_path / "link.rbds"
    link.symlink_to(real)
    store = HistoryStore(tmp_path / "history")
    shot = store.record(real)
    real.write_text("new", encoding="utf-8")
    store.restore(shot, link)
    assert link.is_symlink()
    assert real.read_text() == "model-v1"
    assert (real.stat().st_mode & 0o777) == 0o664


def test_a_corrupt_index_is_recovered_from_snapshot_files(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    first = store.record(path)
    path.write_text("model-v2", encoding="utf-8")
    second = store.record(path)
    store._index_path(path).write_text("not json", encoding="utf-8")
    shots = store.snapshots(path)
    assert {s.sha256 for s in shots} == {first.sha256, second.sha256}
    assert all(s.reason == "recovered" for s in shots)


def test_recovered_versions_still_verify_their_checksum(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    first = store.record(path)
    path.write_text("model-v2", encoding="utf-8")
    store.record(path)
    store._index_path(path).write_text("not json", encoding="utf-8")
    recovered = next(s for s in store.snapshots(path)
                     if s.sha256 == first.sha256)
    assert recovered.sha256                     # computed during recovery
    store.restore(recovered)
    assert path.read_text() == "model-v1"


def test_histories_are_per_file_even_for_identical_content(tmp_path):
    a = tmp_path / "a.rbds"
    b = tmp_path / "b.rbds"
    a.write_text("same", encoding="utf-8")
    b.write_text("same", encoding="utf-8")
    store = HistoryStore(tmp_path / "history")
    store.record(a)
    assert store.snapshots(b) == []
    assert len(store.snapshots(a)) == 1


def test_a_label_on_unchanged_bytes_names_an_unlabelled_newest_version(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    shot = store.record(path)
    named = store.record(path, label="before upgrade")
    assert named is not None and named.id == shot.id
    assert [s.label for s in store.snapshots(path)] == ["before upgrade"]
    # the same label again changes nothing
    assert store.record(path, label="before upgrade") is None
    assert store.set_label(path, shot.id, "")
    assert store.snapshots(path)[0].label == ""


def test_a_different_label_on_unchanged_bytes_adds_a_version_and_keeps_the_old(
        tmp_path):
    """Overwriting the newest label destroyed the user's earlier name."""
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    first = store.record(path, label="A", reason="save")
    second = store.record(path, label="B", reason="manual")
    assert second is not None and second.id != first.id
    shots = store.snapshots(path)
    assert [(s.label, s.reason) for s in shots] == [("B", "manual"),
                                                    ("A", "save")]
    assert shots[0].sha256 == shots[1].sha256
    assert shots[0].path == shots[1].path        # one stored file
    assert shots[0].path.read_bytes() == shots[1].path.read_bytes()
    # a repeat of the newest label is still a no-op
    assert store.record(path, label="B") is None


def test_a_different_description_on_unchanged_bytes_adds_a_version(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    first = store.record(path, description="one")
    second = store.record(path, description="two")
    assert second is not None and second.id != first.id
    assert [s.description for s in store.snapshots(path)] == ["two", "one"]


def test_deleting_or_pruning_one_of_two_identical_versions_keeps_the_other(
        tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history", keep=30)
    first = store.record(path, label="A")
    second = store.record(path, label="B")
    assert store.discard(path, second.id)
    assert first.path.is_file()
    assert store.restore(store.snapshot(path, first.id),
                         tmp_path / "back.rbds").read_text() == "model-v1"


def test_the_byte_cap_keeps_the_newest_versions_and_removes_the_rest(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history", keep=30, max_bytes=2_500)
    ids = []
    for i in range(3):
        path.write_text(f"{i}" * 1_000, encoding="utf-8")
        ids.append(store.record(path).id)
    assert [s.id for s in store.snapshots(path)] == [ids[2], ids[1]]
    assert len(list((tmp_path / "history").rglob("*.rbds"))) == 2


def test_one_version_larger_than_the_byte_cap_is_still_kept(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history", keep=30, max_bytes=100)
    path.write_text("x" * 500, encoding="utf-8")
    big = store.record(path).id
    path.write_text("y" * 500, encoding="utf-8")
    newest = store.record(path).id
    assert [s.id for s in store.snapshots(path)] == [newest]
    assert big != newest


def test_index_entries_cannot_point_outside_the_history_folder(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    shot = store.record(path)
    outside = tmp_path / "outside.rbds"
    outside.write_text("secret", encoding="utf-8")
    index = store._index_path(path)
    doc = json.loads(index.read_text(encoding="utf-8"))
    doc["snapshots"].append(dict(doc["snapshots"][0],
                                 id="escape", stored="../outside.rbds"))
    index.write_text(json.dumps(doc), encoding="utf-8")
    assert [s.id for s in store.snapshots(path)] == [shot.id]


def test_recovered_index_survives_repeated_lists_and_a_new_record(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    first = store.record(path)
    path.write_text("model-v2", encoding="utf-8")
    second = store.record(path)
    store._index_path(path).write_text("not json", encoding="utf-8")
    want = {first.sha256, second.sha256}
    assert {s.sha256 for s in store.snapshots(path)} == want
    assert {s.sha256 for s in store.snapshots(path)} == want
    path.write_text("model-v3", encoding="utf-8")
    store.record(path)
    assert want <= {s.sha256 for s in store.snapshots(path)}


def test_a_labelled_version_is_pinned_against_the_retention_caps(tmp_path):
    """"Save this one" must survive ordinary pruning."""
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history", keep=2, max_bytes=10_000)
    first = store.record(path, label="baseline")
    ids = {first.id}
    for n in range(2, 7):
        path.write_text(f"model-v{n}", encoding="utf-8")
        ids.add(store.record(path).id)
    kept = {s.id for s in store.snapshots(path)}
    assert first.id in kept, "the labelled version was pruned"
    # the unlabelled ones still obey `keep`
    assert len([s for s in store.snapshots(path) if not s.label]) == 2

    # clearing the label makes it eligible again
    store.set_label(path, first.id, "")
    path.write_text("model-v8", encoding="utf-8")
    store.record(path)
    assert first.id not in {s.id for s in store.snapshots(path)}


def test_a_description_is_metadata_that_survives_relabel_and_recovery(tmp_path):
    from raptor.core.history import MAX_DESCRIPTION
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    shot = store.record(path, label="v1", description="# Why\n\n- one\r\n- two")
    assert shot.description == "# Why\n\n- one\n- two"
    assert store.snapshots(path)[0].description == shot.description
    # metadata only: the bytes and checksum are untouched
    assert shot.path.read_text() == "model-v1"
    before = shot.sha256
    assert store.set_description(path, shot.id, "changed **note**")
    assert store.snapshots(path)[0].description == "changed **note**"
    assert store.snapshots(path)[0].sha256 == before
    store.set_label(path, shot.id, "renamed")
    assert store.snapshots(path)[0].description == "changed **note**"
    assert not store.set_description(path, "nope", "x")
    assert store.set_description(path, shot.id, "  \n ")
    assert store.snapshots(path)[0].description == ""
    import json
    assert all("description" not in e for e in json.loads(
        store._index_path(path).read_text())["snapshots"])
    store.set_description(path, shot.id, "x" * (MAX_DESCRIPTION + 50))
    assert len(store.snapshots(path)[0].description) == MAX_DESCRIPTION


def test_an_index_without_descriptions_still_loads(tmp_path):
    import json
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    shot = store.record(path, label="old")
    index = store._index_path(path)
    doc = json.loads(index.read_text())
    assert all("description" not in e for e in doc["snapshots"])
    assert store.snapshots(path)[0].description == ""
    assert store.snapshots(path)[0].id == shot.id


def test_a_description_on_unchanged_bytes_describes_the_newest_version(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    shot = store.record(path, description="first note")
    # a blank description leaves the note alone and adds nothing
    assert store.record(path) is None
    assert store.snapshots(path)[0].description == "first note"
    # a different note is not allowed to overwrite the first: new version
    other = store.record(path, description="second note")
    assert other is not None and other.id != shot.id
    assert [s.description for s in store.snapshots(path)] == [
        "second note", "first note"]
    assert store.record(path, description="second note") is None
    store.set_description(path, other.id, "")
    named = store.record(path, description="third note")   # newest has none
    assert named is not None and named.id == other.id
    assert len(store.snapshots(path)) == 2
    # a label alone keeps the description
    relabel = store.record(path, label="named")
    assert relabel is not None and relabel.description == "third note"


def test_a_described_version_is_pinned_like_a_labelled_one(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history", keep=2)
    first = store.record(path, description="keep me")
    for n in range(4):
        path.write_text(f"model-{n}")
        store.record(path)
    assert first.id in [s.id for s in store.snapshots(path)]


def test_a_recovered_index_keeps_rebuilding_without_descriptions(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    store.record(path, description="lost with the index")
    store._index_path(path).write_text("{ not json")
    shots = store.snapshots(path)
    assert len(shots) == 1 and shots[0].description == ""


def test_the_cli_describes_and_lists_a_description(tmp_path, monkeypatch, capsys):
    import json
    from raptor.cli.main import main
    import raptor.api.history as api_history
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    monkeypatch.setattr(api_history, "default_store", lambda: store)
    notes = tmp_path / "notes.md"
    notes.write_text("## Notes\n\n- checked", encoding="utf-8")
    assert main(["history", str(path), "--snapshot",
                 "--description-file", str(notes)]) == 0
    shot = store.snapshots(path)[0]
    assert shot.description == "## Notes\n\n- checked"
    assert main(["history", str(path), "--describe", shot.id,
                 "--description", "inline **text**"]) == 0
    assert store.snapshots(path)[0].description == "inline **text**"
    capsys.readouterr()
    assert main(["history", str(path), "-f", "json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["snapshots"][0]["description"] == "inline **text**"
    assert main(["history", str(path)]) == 0
    assert "[described]" in capsys.readouterr().out
    assert main(["history", str(path), "--describe", shot.id,
                 "--description", ""]) == 0
    assert store.snapshots(path)[0].description == ""
    # option misuse and unknown versions
    for argv in (["--description", "x"],
                 ["--describe", shot.id],
                 ["--describe", shot.id, "--description", "a",
                  "--description-file", str(notes)]):
        assert main(["history", str(path), *argv]) == 2
    assert main(["history", str(path), "--describe", "nope",
                 "--description", "x"]) == 2


# -- explicit checkpoints, shared storage, retention ---------------------------

def _stored_files(store, path):
    return sorted(p.name for p in store._folder(path).iterdir()
                  if p.suffix in (".rbds", ".rbd", ".rbdz"))


def test_identical_bytes_are_stored_once_for_two_entries(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    first = store.record(path, reason="manual", label="A")
    second = store.record(path, reason="manual", label="B")
    assert second is not None and second.id != first.id
    assert len(store.snapshots(path)) == 2
    assert len(_stored_files(store, path)) == 1
    assert store.usage(path) == len(b"model-v1")        # counted once


def test_deleting_one_of_two_sharing_entries_keeps_the_file(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history")
    first = store.record(path, reason="manual", label="A")
    second = store.record(path, reason="manual", label="B")
    assert store.discard(path, second.id)
    assert len(_stored_files(store, path)) == 1
    assert store.read(store.snapshot(path, first.id)) == b"model-v1"
    assert store.discard(path, first.id)
    assert _stored_files(store, path) == []


def test_pruning_an_automatic_entry_keeps_a_file_a_checkpoint_shares(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history", keep=1)
    store.record(path, reason="before restore")
    kept = store.record(path, reason="manual", label="same bytes")
    for n in range(3):
        path.write_text(f"other-{n}", encoding="utf-8")
        store.record(path, reason="before restore")
    assert store.read(store.snapshot(path, kept.id)) == b"model-v1"


def test_explicit_versions_survive_beyond_keep_and_the_byte_cap(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history", keep=2, max_bytes=300)
    ids = []
    for n in range(6):
        path.write_text(f"{n}" * 200, encoding="utf-8")
        shot = store.record(path, reason="manual" if n % 2 else "snapshot",
                            label="x" if n < 3 else "")
        ids.append(shot.id)
    assert {s.id for s in store.snapshots(path)} == set(ids)
    assert store.usage(path) > store.max_bytes     # reported, not deleted


def test_before_restore_copies_are_pruned_by_count_and_bytes(tmp_path):
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history", keep=2, max_bytes=10_000)
    keep = store.record(path, reason="manual")
    auto = []
    for n in range(4):
        path.write_text(f"auto-{n}", encoding="utf-8")
        auto.append(store.record(path, reason="before restore").id)
    ids = [s.id for s in store.snapshots(path)]
    assert keep.id in ids
    assert [i for i in ids if i != keep.id] == [auto[3], auto[2]]
    small = HistoryStore(tmp_path / "h2", keep=30, max_bytes=2_500)
    out = []
    for i in range(3):
        path.write_text(f"{i}" * 1_000, encoding="utf-8")
        out.append(small.record(path, reason="before restore").id)
    assert [s.id for s in small.snapshots(path)] == [out[2], out[1]]


def test_a_legacy_index_keeps_its_names_and_old_saves_are_prunable(tmp_path):
    """An index written before content-addressing: own stored names, reason
    "save" entries are automatic, a labelled one is not."""
    path = _model(tmp_path)
    store = HistoryStore(tmp_path / "history", keep=1)
    folder = store._folder(path)
    folder.mkdir(parents=True)
    entries = []
    for n, (reason, label) in enumerate(
            [("save", "named"), ("save", ""), ("save", ""), ("manual", "")]):
        sid = f"2026010{n + 1}T000000000000-abc{n:03d}"
        data = f"legacy-{n}".encode()
        (folder / f"{sid}.rbds").write_bytes(data)
        import hashlib
        entries.append({"id": sid, "file": str(path),
                        "created": f"2026-01-0{n + 1}T00:00:00",
                        "reason": reason, "label": label,
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "stored": f"{sid}.rbds"})
    (folder / "index.json").write_text(json.dumps(
        {"version": 1, "file": str(path), "snapshots": entries}))
    assert len(store.snapshots(path)) == 4
    path.write_text("new", encoding="utf-8")
    store.record(path, reason="manual")
    reasons = [(s.reason, s.label) for s in store.snapshots(path)]
    # unlabelled legacy saves pruned down to keep=1 (the newest of them)
    assert reasons.count(("save", "")) == 1
    assert ("save", "named") in reasons and ("manual", "") in reasons
    assert store.snapshot(path, entries[0]["id"]).path.name == \
        entries[0]["stored"]


def test_a_rebuilt_index_finds_shared_and_archive_files(tmp_path):
    path = tmp_path / "plant.rbdz"
    path.write_bytes(b"PK-archive-1")
    store = HistoryStore(tmp_path / "history")
    store.record(path, reason="manual", label="A")
    store.record(path, reason="manual", label="B")
    path.write_bytes(b"PK-archive-2")
    store.record(path, reason="manual")
    store._index_path(path).write_text("not json", encoding="utf-8")
    shots = store.snapshots(path)
    assert len(shots) == 2                      # one entry per stored file
    assert {store.read(s) for s in shots} == {b"PK-archive-1",
                                              b"PK-archive-2"}
    assert all(s.sha256 and s.created for s in shots)

"""Version history: record, dedupe, retention and restore."""
from __future__ import annotations

from pathlib import Path

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

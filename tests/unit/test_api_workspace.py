"""Tests for the Workspace, model handles, and round-trips.

Covers create/edit/autosave/reopen, handle lookup errors, path
containment, undo and backups, the retention sweep, output-path
resolution, workspace info, and concurrent saves.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from raptor.api.workspace import (
    ModelHandle,
    PathOutsideWorkspace,
    UnknownHandle,
    Workspace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def ws(tmp_path: Path) -> Workspace:
    """A fresh workspace in a temp directory."""
    return Workspace(tmp_path / "workspace")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCreateModel:
    """Creating models and basic operations."""

    def test_create_blank_model(self, ws: Workspace):
        """create_model returns a model_id and summary."""
        result = ws.create_model("TestModel")
        assert "model_id" in result
        assert result["model_id"].startswith("m_")
        assert "block" in result["summary"].lower()

    def test_create_model_persists_to_disk(self, ws: Workspace):
        """The created model is written to models/<id>/model.rbds."""
        result = ws.create_model("DiskTest")
        model_id = result["model_id"]
        rbds_path = ws.root / "models" / model_id / "model.rbds"
        assert rbds_path.exists(), f"Expected {rbds_path} to exist"
        text = rbds_path.read_text()
        assert "raptor-studio" in text  # .rbds marker

    def test_create_model_has_meta_json(self, ws: Workspace):
        """meta.json is written alongside the model."""
        result = ws.create_model("MetaTest")
        model_id = result["model_id"]
        meta_path = ws.root / "models" / model_id / "meta.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["name"] == "MetaTest"
        assert "created" in meta
        assert "last_modified" in meta

    def test_list_models(self, ws: Workspace):
        """list_models returns info about all open models."""
        ws.create_model("Alpha")
        ws.create_model("Beta")
        models = ws.list_models()
        assert len(models) == 2
        names = {m["name"] for m in models}
        assert names == {"Alpha", "Beta"}

    def test_model_limit_evicts_rather_than_refusing(self, ws: Workspace):
        """Exceeding the model cap must not fail the call.

        The cap is a bound on how many models are *materialised* at once,
        not on how many a workspace may hold.  It used to raise, and
        because nothing ever evicted, a workspace that had accumulated
        more models than the cap (every one the server had ever opened)
        refused `create_model` for the rest of its life.
        """
        ws.max_models = 2
        ws.create_model("M1")
        ws.create_model("M2")
        third = ws.create_model("M3")     # must not raise
        assert third["model_id"]
        assert len(ws.list_models()) == 3

        # All three are still reachable by handle, re-read from disk.
        for name in ("M1", "M2", "M3"):
            mid = next(m["model_id"] for m in ws.list_models()
                       if m["name"] == name)
            assert ws.get(mid).name == name

    def test_cache_stays_within_the_cap(self, ws: Workspace):
        """Eviction is real: the cache is bounded even though the index
        is not."""
        ws.max_models = 2
        for i in range(5):
            ws.create_model(f"M{i}")
        assert len(ws._models) <= 2
        assert len(ws.list_models()) == 5


class TestRoundTrip:
    """Create → edit → autosave → reopen round trip."""

    def test_autosave_and_reopen(self, ws: Workspace):
        """Edit a model, save, then re-read from disk matches."""
        result = ws.create_model("RoundTrip")
        model_id = result["model_id"]
        handle = ws.get(model_id)

        # Mutate a field — use a name without .rbd suffix since
        # build_new_model sets file_name to "<name>.rbd"
        handle.rbd.file_name = "ModifiedModel.rbds"

        # Save
        ws.save(model_id)

        # Read back from disk
        from raptor.core.fileio.rbds import load_rbds
        rbd2 = load_rbds(str(handle.path))
        assert rbd2.file_name == "ModifiedModel.rbds"

    def test_backup_created_on_save(self, ws: Workspace):
        """Saving creates a .bak.1 backup."""
        result = ws.create_model("BackupTest")
        model_id = result["model_id"]
        handle = ws.get(model_id)

        # First save — no backup yet (nothing to back up)
        ws.save(model_id)

        # Mutate and save again — should create backup
        handle.rbd.file_name = "Changed"
        ws.save(model_id)

        bak = handle.path.parent / "model.rbds.1.bak"
        assert bak.exists(), "Backup .bak.1 should exist after second save"

        # The backup should contain the PREVIOUS version
        from raptor.core.fileio.rbds import load_rbds
        rbd_bak = load_rbds(str(bak))
        # build_new_model sets file_name to "<name>.rbd"
        assert rbd_bak.file_name == "BackupTest.rbd"


class TestGetModel:
    """Getting models by handle."""

    def test_get_returns_handle(self, ws: Workspace):
        """get() returns a ModelHandle with the correct id."""
        result = ws.create_model("HandleTest")
        model_id = result["model_id"]
        handle = ws.get(model_id)
        assert isinstance(handle, ModelHandle)
        assert handle.model_id == model_id
        assert handle.name == "HandleTest"

    def test_unknown_handle_raises(self, ws: Workspace):
        """get() with a bogus id raises UnknownHandle."""
        with pytest.raises(UnknownHandle, match="Unknown or expired"):
            ws.get("m_nonexistent")


class TestCloseModel:
    """Closing models."""

    def test_close_removes_from_list(self, ws: Workspace):
        """After close(), the model no longer appears in list_models."""
        result = ws.create_model("CloseMe")
        model_id = result["model_id"]
        assert len(ws.list_models()) == 1

        ws.close(model_id)
        assert len(ws.list_models()) == 0

    def test_close_delete_removes_files(self, ws: Workspace):
        """close(delete=True) removes the model directory."""
        result = ws.create_model("DeleteMe")
        model_id = result["model_id"]
        handle = ws.get(model_id)
        model_dir = handle.path.parent
        assert model_dir.exists()

        ws.close(model_id, delete=True)
        assert not model_dir.exists()


class TestUndo:
    """Undo restores the previous backup."""

    def test_undo_restores_previous(self, ws: Workspace):
        """undo() restores the model to its state before the last save."""
        result = ws.create_model("UndoTest")
        model_id = result["model_id"]
        handle = ws.get(model_id)

        # Save the initial state (creates .bak.1)
        ws.save(model_id)

        # Mutate and save again
        handle.rbd.file_name = "ChangedOnce"
        ws.save(model_id)

        # Verify the change stuck
        from raptor.core.fileio.rbds import load_rbds
        rbd_now = load_rbds(str(handle.path))
        assert rbd_now.file_name == "ChangedOnce"

        # Undo
        ws.undo(model_id)
        rbd_restored = load_rbds(str(handle.path))
        # build_new_model sets file_name to "<name>.rbd"
        assert rbd_restored.file_name == "UndoTest.rbd"

    def test_undo_no_backup_raises(self, ws: Workspace):
        """undo() with no backup raises WorkspaceError."""
        result = ws.create_model("NoBackup")
        model_id = result["model_id"]
        with pytest.raises(Exception, match="No backup"):
            ws.undo(model_id)

    def test_multi_step_undo_walks_further_back(self, ws: Workspace):
        """A regression test: `undo` used to always restore the same
        `.bak.1` (never shifting the chain), so a second consecutive
        call was a no-op instead of reaching further back."""
        model_id = ws.create_model("MultiUndo")["model_id"]
        handle = ws.get(model_id)
        for name in ("First", "Second", "Third"):
            handle.rbd.file_name = name
            ws.save(model_id)
        assert handle.rbd.file_name == "Third"

        ws.undo(model_id)
        assert ws.get(model_id).rbd.file_name == "Second"
        ws.undo(model_id)
        assert ws.get(model_id).rbd.file_name == "First"
        ws.undo(model_id)
        assert ws.get(model_id).rbd.file_name == "MultiUndo.rbd"  # initial

    def test_redo_reverses_undo_and_is_itself_multi_step(self, ws: Workspace):
        model_id = ws.create_model("UndoRedo")["model_id"]
        handle = ws.get(model_id)
        for name in ("First", "Second", "Third"):
            handle.rbd.file_name = name
            ws.save(model_id)

        ws.undo(model_id)
        ws.undo(model_id)
        assert ws.get(model_id).rbd.file_name == "First"

        ws.redo(model_id)
        assert ws.get(model_id).rbd.file_name == "Second"
        ws.redo(model_id)
        assert ws.get(model_id).rbd.file_name == "Third"

    def test_redo_with_nothing_to_redo_raises(self, ws: Workspace):
        model_id = ws.create_model("NoRedo")["model_id"]
        ws.get(model_id).rbd.file_name = "Changed"
        ws.save(model_id)
        with pytest.raises(Exception, match="No redo"):
            ws.redo(model_id)

    def test_new_edit_after_undo_clears_redo(self, ws: Workspace):
        """Undo, then make a genuinely new edit: the redo history for
        the edit you undid away must be gone, same as any editor."""
        model_id = ws.create_model("ClearRedo")["model_id"]
        handle = ws.get(model_id)
        handle.rbd.file_name = "First"
        ws.save(model_id)
        handle.rbd.file_name = "Second"
        ws.save(model_id)

        ws.undo(model_id)
        assert ws.get(model_id).rbd.file_name == "First"

        handle.rbd.file_name = "Branched"
        ws.save(model_id)

        with pytest.raises(Exception, match="No redo"):
            ws.redo(model_id)


class TestRetentionSweep:
    """Retention sweep removes old run/render artefacts."""

    def test_sweep_removes_old_runs(self, ws: Workspace):
        """Runs older than retain_days are removed."""
        ws.retain_days = 1

        # Create a fake old run directory
        run_dir = ws.root / "runs" / "r_old"
        run_dir.mkdir(parents=True)
        (run_dir / "summary.json").write_text("{}")

        # Backdate the mtime to 2 days ago
        old_time = time.time() - 2 * 86400
        os.utime(run_dir, (old_time, old_time))

        result = ws.sweep_retention()
        assert result["removed_runs"] == 1
        assert not run_dir.exists()

    def test_sweep_keeps_recent_runs(self, ws: Workspace):
        """Runs within the retention window are kept."""
        ws.retain_days = 30

        run_dir = ws.root / "runs" / "r_recent"
        run_dir.mkdir(parents=True)
        (run_dir / "summary.json").write_text("{}")

        result = ws.sweep_retention()
        assert result["removed_runs"] == 0
        assert run_dir.exists()


class TestPathContainment:
    """Paths are resolved and must stay inside the workspace."""

    def test_workspace_resolves_symlink(self, ws: Workspace):
        """Symlinks inside the workspace are allowed."""
        link_dir = ws.root / "link_target"
        link_dir.mkdir()
        real_file = link_dir / "model.rbds"
        real_file.write_text("{}")

        symlink = ws.root / "link"
        symlink.symlink_to(link_dir)

        # This should not raise
        resolved = ws._assert_inside(symlink / "model.rbds")
        assert resolved == real_file.resolve()

    def test_path_outside_workspace_rejected(self, ws: Workspace):
        """A path resolving outside the workspace is rejected."""
        outside = Path("/tmp/evil_file.rbds")
        with pytest.raises(PathOutsideWorkspace):
            ws._assert_inside(outside)


class TestResolveOutputPath:
    """Relative paths resolve against the workspace root, not the cwd."""

    def test_relative_path_resolves_against_workspace_root(self, ws: Workspace):
        resolved = ws.resolve_output_path("exports/model.rbd")
        assert resolved == (ws.root / "exports" / "model.rbd").resolve()

    def test_absolute_path_inside_workspace_allowed(self, ws: Workspace):
        target = ws.root / "exports" / "model.rbd"
        assert ws.resolve_output_path(target) == target.resolve()

    def test_relative_escape_still_rejected(self, ws: Workspace):
        with pytest.raises(PathOutsideWorkspace):
            ws.resolve_output_path("../evil.rbd")

    def test_allow_extra_still_honoured(self, ws: Workspace, tmp_path: Path):
        extra = tmp_path / "allowed"
        extra.mkdir()
        assert ws.resolve_output_path(
            extra / "model.rbd", allow_extra=[extra]
        ) == (extra / "model.rbd").resolve()


class TestWorkspaceInfo:
    """info() exposes root, write dirs, retention and limits."""

    def test_info_reports_root_and_limits(self, ws: Workspace):
        info = ws.info()
        assert info["root"] == str(ws.root)
        assert info["allowed_write"] == [str(ws.root)]
        assert info["retain_days"] == ws.retain_days
        assert info["max_models"] == ws.max_models
        assert info["max_bytes"] == ws.max_bytes

    def test_info_reflects_constructor_overrides(self, tmp_path: Path):
        ws = Workspace(tmp_path / "ws2", max_models=3,
                       retain_days=7, max_bytes=12345)
        info = ws.info()
        assert info["max_models"] == 3
        assert info["retain_days"] == 7
        assert info["max_bytes"] == 12345


class TestConcurrentSaves:
    """Concurrent saves of one model must not race in _backup."""

    def test_concurrent_saves_do_not_race(self, ws: Workspace):
        model_id = ws.create_model("Concurrent")["model_id"]
        handle = ws.get(model_id)

        errors: list[BaseException] = []
        n_threads = 16
        barrier = threading.Barrier(n_threads)

        def _save() -> None:
            try:
                barrier.wait()
                for _ in range(3):
                    ws.save(model_id)
            except BaseException as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=_save) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"concurrent saves raised: {errors!r}"

        # A valid model must remain, and the backup chain must be intact.
        from raptor.core.fileio.rbds import load_rbds
        rbd = load_rbds(str(handle.path))
        assert rbd.file_name == "Concurrent.rbd"
        assert (handle.path.parent / "model.rbds.1.bak").exists()

    def test_concurrent_save_and_undo_do_not_race(self, ws: Workspace):
        model_id = ws.create_model("ConcurrentUndo")["model_id"]

        errors: list[BaseException] = []
        barrier = threading.Barrier(8)

        def _save() -> None:
            try:
                barrier.wait()
                for _ in range(4):
                    ws.save(model_id)
            except BaseException as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        def _undo() -> None:
            try:
                barrier.wait()
                for _ in range(4):
                    try:
                        ws.undo(model_id)
                    except Exception:  # noqa: BLE001 - "no backup" is fine
                        pass
            except BaseException as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = ([threading.Thread(target=_save) for _ in range(4)]
                   + [threading.Thread(target=_undo) for _ in range(4)])
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"concurrent save/undo raised: {errors!r}"

"""The on-disk run-artefact layout has ONE owner (review D-19).

`api/run.py` writes the artefacts, so it owns the map. It used to be
spelled out four times -- the writer's inline literals, a `_SECTION_FILES`
dict for the reader, a third inline copy in `api/run_tables`, and a fourth
in `mcp/resources.py` -- and the copies had already drifted in a way that
was user-visible: `weak_link` is a DERIVED section, computed by
`get_report` from the run's block statistics and not backed by any file, so
`get_report(section="weak_link")` worked over MCP while the
`raptor://runs/{run_id}/weak_link` resource answered *"no section
'weak_link' for run ..."* -- the exact "added it to the tool, forgot the
resource" shape.

These tests pin the shared layout, the two kinds of section, and the fix.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.api.run import (
    ALL_SECTIONS,
    DERIVED_SECTIONS,
    SECTION_FILES,
    get_report,
    section_path,
)


def _workspace_with(root: Path, run_id: str):
    """A `Workspace` whose `runs/<run_id>/` exists but is empty."""
    from raptor.api.workspace import Workspace
    (root / "runs" / run_id).mkdir(parents=True)
    return Workspace(root)


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """A run directory holding one artefact of every section, written
    through the layout itself -- so the fixture cannot pass by agreeing
    with a stale literal."""
    d = tmp_path / "run1"
    d.mkdir()
    for section in SECTION_FILES:
        path = section_path(d, section)
        assert path is not None
        path.write_text(f"{section} line\n{section} line 2\n")
    return d


class TestSectionPath:
    def test_resolves_a_declared_section(self, run_dir: Path):
        path = section_path(run_dir, "final_results")
        assert path == run_dir / "final_results.txt"
        assert path.is_file()

    def test_an_unknown_section_is_none(self, run_dir: Path):
        """None, not a Path -- so a caller that forgets to check cannot
        silently open `runs_dir/None`."""
        assert section_path(run_dir, "no_such_section") is None

    def test_a_derived_section_is_none(self, run_dir: Path):
        """`weak_link` has no artefact; that is what makes it derived."""
        assert "weak_link" in DERIVED_SECTIONS
        assert section_path(run_dir, "weak_link") is None

    def test_every_declared_section_resolves(self, run_dir: Path):
        for section in SECTION_FILES:
            assert section_path(run_dir, section) is not None, section


class TestTheTwoKindsOfSection:
    def test_the_kinds_are_disjoint(self):
        assert not (set(SECTION_FILES) & set(DERIVED_SECTIONS))

    def test_every_section_is_exactly_one_kind(self):
        assert set(ALL_SECTIONS) == set(SECTION_FILES) | set(DERIVED_SECTIONS)

    def test_all_sections_is_sorted_and_complete(self):
        assert list(ALL_SECTIONS) == sorted(ALL_SECTIONS)
        assert "weak_link" in ALL_SECTIONS
        assert "final_results" in ALL_SECTIONS

    def test_a_derived_section_is_not_in_the_file_map(self):
        """The invariant that produced the 404: a section served by
        `get_report` but absent from every filename map."""
        assert "weak_link" not in SECTION_FILES


class TestRunTablesSharesTheLayout:
    def test_bundle_from_run_reads_through_section_path(self):
        """Structural: no literal filenames in the bundle builder."""
        import inspect

        from raptor.api import run_tables
        src = inspect.getsource(run_tables.bundle_from_run)
        assert "_SECTION_FILES" not in src
        for name in ("final_results.txt", "end_of_run.txt",
                     "capacity.txt", "cost.txt", "events.txt"):
            assert name not in src, (
                f"{name} is spelled out in bundle_from_run; it must come "
                "from api.run.section_path so the layout has one owner")

    def test_it_reads_the_same_files_the_layout_names(self, tmp_path: Path):
        """Behavioural: the bundle's lines are the files the layout points
        at."""
        from raptor.api.run_tables import bundle_from_run
        ws = _workspace_with(tmp_path / "ws", "run1")
        for section in SECTION_FILES:
            section_path(ws.root / "runs" / "run1", section).write_text(
                f"{section} line\n{section} line 2\n")
        bundle = bundle_from_run(ws, "run1")
        assert bundle.final_lines == [
            "final_results line", "final_results line 2"]
        assert bundle.end_of_run_lines == [
            "end_of_run line", "end_of_run line 2"]

    def test_an_absent_artefact_is_empty_not_an_error(self, tmp_path: Path):
        """A partial or older run directory must still load."""
        from raptor.api.run_tables import bundle_from_run
        ws = _workspace_with(tmp_path / "ws", "partial")
        d = ws.root / "runs" / "partial"
        (d / "summary.json").write_text(json.dumps({"model_name": "x"}))
        section_path(d, "final_results").write_text("only this\n")
        bundle = bundle_from_run(ws, "partial")
        assert bundle.final_lines == ["only this"]
        assert bundle.cost_lines == []
        assert bundle.event_logs == []


class TestGetReportUsesTheLayout:
    def test_a_file_section_is_read(self, tmp_path, run_dir: Path):
        """`get_report` needs a Workspace, so exercise the path it shares
        with the resource: a real workspace root."""
        ws_root = tmp_path / "ws"
        (ws_root / "runs").mkdir(parents=True)
        target = ws_root / "runs" / "run1"
        target.mkdir()
        for section in SECTION_FILES:
            section_path(target, section).write_text("body\n")

        from raptor.api.workspace import Workspace
        ws = Workspace(ws_root)
        out = get_report(ws, "run1", section="final_results")
        assert out["lines"] == ["body"]
        assert out["section"] == "final_results"

    def test_an_unknown_section_names_every_section(self, tmp_path):
        ws_root = tmp_path / "ws"
        (ws_root / "runs" / "run1").mkdir(parents=True)
        from raptor.api.workspace import Workspace
        ws = Workspace(ws_root)
        with pytest.raises(ValueError) as exc:
            get_report(ws, "run1", section="nope")
        # The message must list what IS available, derived from the layout.
        assert "weak_link" in str(exc.value)
        assert "final_results" in str(exc.value)

    def test_the_error_message_cannot_drift_from_the_layout(self, tmp_path):
        """Stated as an invariant: every name in ALL_SECTIONS appears in
        the 'unknown section' message. This is what keeps the error text
        honest as sections are added."""
        ws_root = tmp_path / "ws"
        (ws_root / "runs" / "run1").mkdir(parents=True)
        from raptor.api.workspace import Workspace
        ws = Workspace(ws_root)
        with pytest.raises(ValueError) as exc:
            get_report(ws, "run1", section="nope")
        msg = str(exc.value)
        for section in ALL_SECTIONS:
            assert section in msg, f"{section} missing from the error message"


class TestTheMcpResourceDelegates:
    """The live symptom: `weak_link` 404'd on the resource door while
    working on the tool door."""

    @pytest.fixture()
    def ws_with_run(self, tmp_path, monkeypatch):
        ws_root = tmp_path / "ws"
        (ws_root / "runs" / "run1").mkdir(parents=True)
        for section in SECTION_FILES:
            if section == "final_results":
                # weak_link is extracted from final_results, so it needs
                # the banner the extractor looks for.
                section_path(ws_root / "runs" / "run1", section).write_text(
                    "Summary\n***WEAK_LINK_ANALYSIS***\nWeak: 0.95\n")
            else:
                section_path(ws_root / "runs" / "run1", section).write_text(
                    f"{section} body\n")
        from raptor.api.workspace import Workspace
        from raptor.mcp import context
        ws = Workspace(ws_root)
        monkeypatch.setattr(context, "workspace", lambda *a, **k: ws)
        return ws

    def test_a_file_section_still_reads(self, ws_with_run):
        from raptor.mcp.resources import get_run_section
        assert get_run_section("run1", "cost") == "cost body\n"

    def test_weak_link_no_longer_404s(self, ws_with_run):
        """THE BUG. It used to raise 'no section weak_link for run ...'."""
        from raptor.mcp.resources import get_run_section
        text = get_run_section("run1", "weak_link")
        assert "Weak: 0.95" in text, (
            f"the derived section is unreachable on the resource door: "
            f"{text!r}")

    def test_the_resource_and_the_tool_agree_on_weak_link(self, ws_with_run):
        """Same text from both doors -- the point of delegating."""
        from raptor.mcp.resources import get_run_section
        tool = "\n".join(
            get_report(ws_with_run, "run1", section="weak_link")["lines"])
        assert get_run_section("run1", "weak_link") == tool

    def test_the_error_lists_every_section_including_weak_link(
            self, ws_with_run):
        from raptor.mcp.resources import get_run_section
        with pytest.raises(FileNotFoundError) as exc:
            get_run_section("run1", "nope")
        msg = str(exc.value)
        for section in ALL_SECTIONS:
            assert section in msg, f"{section} missing from {msg!r}"

    def test_an_unsafe_run_id_is_refused(self, ws_with_run):
        from raptor.mcp.resources import get_run_section
        with pytest.raises(FileNotFoundError):
            get_run_section("../etc/passwd", "cost")

    def test_no_private_filename_map_remains(self):
        """Structural: the fourth copy of the layout must not come back."""
        import inspect

        from raptor.mcp import resources
        assert not hasattr(resources, "_SECTION_FILES"), (
            "mcp/resources.py holds its own copy of the artefact layout "
            "again; ask api.run (the writer) via ALL_SECTIONS/section_path")
        src = inspect.getsource(resources)
        for name in SECTION_FILES.values():
            assert name not in src, (
                f"{name} is spelled out in resources.py; the layout has one "
                "owner")

"""Tests for the builder helpers and validation rules.

Covers each validation rule firing on a deliberately broken file, clean
golden models passing, the builder API functions, and the CLI `validate`
subcommand via `tools/rbd_builder.py`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.fileio.builder import (
    add_row,
    get_row,
    load,
    next_id,
    set_fields,
    sync_counts,
    validate,
)
from raptor.core.fileio.rbd import RbdFile, RbdSection

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blank_rbd() -> RbdFile:
    """Minimal valid model: MotorDemo."""
    return load(str(GOLDEN / "MotorDemo" / "input.rbd"))


def _empty_section(name: str, columns: list[str]) -> RbdSection:
    return RbdSection(name=name, columns=columns)


# ---------------------------------------------------------------------------
# Tests: each validation rule fires on a deliberately broken file
# ---------------------------------------------------------------------------

class TestValidateDuplicateID:
    """Duplicate IDs produce an ERROR."""

    def test_duplicate_block_id(self):
        rbd = _blank_rbd()
        # Clone the first real block and give it the same ID
        rows = rbd.section("BLOCK_DATA").rows
        real_blocks = [r for r in rows if r.get("ID") != "0"]
        if not real_blocks:
            pytest.skip("no blocks in fixture")
        clone = dict(real_blocks[0])
        clone["ID"] = real_blocks[0]["ID"]  # same ID
        rows.append(clone)

        issues = validate(rbd)
        dup_errors = [i for i in issues if "duplicate ID" in i and "BLOCK_DATA" in i]
        assert dup_errors, f"Expected duplicate ID error, got: {issues}"


class TestValidateDanglingLink:
    """Dangling link references produce an ERROR."""

    def test_link_references_nonexistent_block(self):
        rbd = _blank_rbd()
        links = rbd.section("LINK_DATA").rows
        if not links:
            pytest.skip("no links in fixture")
        # Point a link's ToID at a nonexistent block
        link = links[0]
        link["ToType"] = "Block"
        link["ToID"] = "99999"

        issues = validate(rbd)
        dangling = [i for i in issues if "doesn't exist" in i and "LINK_DATA" in i]
        assert dangling, f"Expected dangling link error, got: {issues}"


class TestValidateNoEndNode:
    """Missing End node produces an ERROR."""

    def test_no_end_node(self):
        rbd = _blank_rbd()
        nodes = rbd.section("NODE_DATA").rows
        # Remove all End nodes (Type=3)
        for row in nodes:
            if row.get("Type") == "3":
                row["Type"] = "0"  # change to regular

        issues = validate(rbd)
        no_end = [i for i in issues if "no End" in i]
        assert no_end, f"Expected no-End error, got: {issues}"


class TestValidateMultipleEndNodes:
    """Multiple End nodes produce an ERROR."""

    def test_two_end_nodes(self):
        rbd = _blank_rbd()
        nodes = rbd.section("NODE_DATA").rows
        end_nodes = [r for r in nodes if r.get("Type") == "3"]
        if not end_nodes:
            pytest.skip("no End node in fixture")
        # Duplicate the End node
        clone = dict(end_nodes[0])
        clone["ID"] = str(next_id(rbd, "NODE_DATA"))
        clone["Name"] = "End2"
        nodes.append(clone)

        issues = validate(rbd)
        multi_end = [i for i in issues if "End (Type=3) nodes found" in i]
        assert multi_end, f"Expected multiple-End error, got: {issues}"


class TestValidateNodeNMismatch:
    """Node ``n`` mismatch produces an ERROR."""

    def test_node_n_too_low(self):
        rbd = _blank_rbd()
        nodes = rbd.section("NODE_DATA").rows
        links = rbd.section("LINK_DATA").rows
        # Find a node with at least 1 incoming link and set n=0
        target_nids = {link["ToID"] for link in links if link.get("ToType") == "Node"}
        for row in nodes:
            if row.get("ID") in target_nids and row.get("Type") != "3":
                row["n"] = "0"  # force mismatch (real count >= 1)
                break
        else:
            # If no suitable node found, add an extra link to the start node
            # to create a mismatch (start has n=0, we'll add a link to it)
            start = next((r for r in nodes if r.get("Type") == "1"), None)
            if start is None:
                pytest.skip("no start node")
            next_link_id = str(max((int(l.get("ID", 0)) for l in links), default=0) + 1)
            links.append({
                "ID": next_link_id,
                "FromType": "Block", "FromID": "5",
                "ToType": "Node", "ToID": start["ID"],
                "pID": "0", "ColdPri": "0", "AProb": "1.0",
                "AutoTime": "0.0", "ManTime": "0.0",
                "CapPri": "0", "nomFlow": "0.0", "MaxFlow": "0.0",
            })

        issues = validate(rbd)
        n_mismatch = [i for i in issues if "declares n=" in i and "real incoming" in i]
        assert n_mismatch, f"Expected node-n mismatch error, got: {issues}"


class TestValidateNodeOutUnderDeclared:
    """Node Out under-declared produces an ERROR."""

    def test_out_too_low(self):
        rbd = _blank_rbd()
        nodes = rbd.section("NODE_DATA").rows
        links = rbd.section("LINK_DATA").rows
        # MotorDemo has start with Out=1 and 1 outgoing link.
        # Add an extra outgoing link from start and leave Out=1 → under-declared.
        start = next((r for r in nodes if r.get("Type") == "1"), None)
        if start is None:
            pytest.skip("no start node")
        # Find the end node to link to
        end = next((r for r in nodes if r.get("Type") == "3"), None)
        if end is None:
            pytest.skip("no end node")
        next_link_id = str(max((int(l.get("ID", 0)) for l in links), default=0) + 1)
        links.append({
            "ID": next_link_id,
            "FromType": "Node", "FromID": start["ID"],
            "ToType": "Node", "ToID": end["ID"],
            "pID": "0", "ColdPri": "0", "AProb": "1.0",
            "AutoTime": "0.0", "ManTime": "0.0",
            "CapPri": "0", "nomFlow": "0.0", "MaxFlow": "0.0",
        })
        # start now has 2 outgoing links but Out=1 → under-declared

        issues = validate(rbd)
        out_err = [i for i in issues if "UNDER-declared" in i and "Out=" in i]
        assert out_err, f"Expected Out under-declared error, got: {issues}"


class TestValidateNodeOutOverDeclared:
    """Node Out over-declared produces a WARN (not ERROR)."""

    def test_out_too_high(self):
        rbd = _blank_rbd()
        nodes = rbd.section("NODE_DATA").rows
        # Set start's Out=2 while it only has 1 outgoing link → over-declared
        start = next((r for r in nodes if r.get("Type") == "1"), None)
        if start is None:
            pytest.skip("no start node")
        start["Out"] = "2"

        issues = validate(rbd)
        over_warn = [i for i in issues if "OVER-declared" in i and "Out=" in i]
        assert over_warn, f"Expected Out over-declared warning, got: {issues}"
        # Must be a WARN, not an ERROR
        assert all("WARN" in w for w in over_warn)


class TestValidateHierarchyMarkerParent:
    """A hierarchy's In/Out marker node must itself be `parent`-owned by
    that hierarchy (its own id), not the hierarchy's enclosing level --
    the mistake that used to crash `graph.py.build_efpa_graph` with a
    bare `KeyError` instead of a usable message."""

    def _hier_and_markers(self, rbd):
        hiers = rbd.section("HIERARCHY_DATA").rows
        if not hiers:
            pytest.skip("fixture has no hierarchies")
        hier = hiers[0]
        nodes = rbd.section("NODE_DATA").rows
        in_node = next(r for r in nodes if r["ID"] == hier["in"])
        out_node = next(r for r in nodes if r["ID"] == hier["out"])
        return hier, in_node, out_node

    def test_in_marker_wrong_parent(self):
        rbd = load(str(GOLDEN / "HierarchyExample" / "input.rbd"))
        _hier, in_node, _out_node = self._hier_and_markers(rbd)
        in_node["parent"] = "0"  # should be hier["ID"]

        issues = validate(rbd)
        errs = [i for i in issues if "'in' marker" in i and "must equal" in i]
        assert errs, f"Expected In-marker parent error, got: {issues}"

    def test_out_marker_wrong_parent(self):
        rbd = load(str(GOLDEN / "HierarchyExample" / "input.rbd"))
        _hier, _in_node, out_node = self._hier_and_markers(rbd)
        out_node["parent"] = "0"  # should be hier["ID"]

        issues = validate(rbd)
        errs = [i for i in issues if "'out' marker" in i and "must equal" in i]
        assert errs, f"Expected Out-marker parent error, got: {issues}"

    def test_marker_wrong_type(self):
        rbd = load(str(GOLDEN / "HierarchyExample" / "input.rbd"))
        _hier, in_node, _out_node = self._hier_and_markers(rbd)
        in_node["Type"] = "2"  # should be 4

        issues = validate(rbd)
        errs = [i for i in issues if "expected '4'" in i]
        assert errs, f"Expected marker-type error, got: {issues}"

    def test_hierarchy_example_clean(self):
        rbd = load(str(GOLDEN / "HierarchyExample" / "input.rbd"))
        issues = validate(rbd)
        errs = [i for i in issues if "marker" in i]
        assert errs == [], f"HierarchyExample unexpectedly flagged: {errs}"


class TestValidateDistParamCount:
    """Gamma/Pearson5/Weibull with < 3 params produces an ERROR."""

    def test_weibull_two_params(self):
        rbd = _blank_rbd()
        blocks = {r["ID"]: r for r in rbd.section("BLOCK_DATA").rows if r.get("ID") != "0"}
        if not blocks:
            pytest.skip("no blocks in fixture")
        # Set first block to Weibull (15) with only 2 params
        first = next(iter(blocks.values()))
        first["FDist"] = "15"
        first["#FParam"] = "2"

        issues = validate(rbd)
        dist_err = [i for i in issues if "OUT OF BOUNDS" in i and "FDist=15" in i]
        assert dist_err, f"Expected dist param count error, got: {issues}"


class TestValidatePhasesHeaderCount:
    """PHASES header count mismatch produces an ERROR."""

    def test_phases_count_wrong(self):
        rbd = _blank_rbd()
        # Force the Phases count to a wrong value
        rbd.counts["Phases"] = 999

        issues = validate(rbd)
        phases_err = [i for i in issues if "PHASES" in i and "crash Raptor 7" in i]
        assert phases_err, f"Expected PHASES header error, got: {issues}"


class TestValidateHeaderCountWarn:
    """Non-PHASES header count mismatch produces a WARN."""

    def test_blocks_count_drift(self):
        rbd = _blank_rbd()
        # Force the Blocks count to a wrong value
        rbd.counts["Blocks"] = 999

        issues = validate(rbd)
        blocks_warn = [i for i in issues if "Blocks" in i and "WARN" in i]
        assert blocks_warn, f"Expected Blocks header warning, got: {issues}"


# ---------------------------------------------------------------------------
# Tests: clean golden model has no issues
# ---------------------------------------------------------------------------

class TestValidateCleanModel:
    """A known-good golden model passes validate() with no issues."""

    def test_motor_demo_clean(self):
        rbd = load(str(GOLDEN / "MotorDemo" / "input.rbd"))
        issues = validate(rbd)
        # Filter to only ERRORs — some WARNs on long-standing fixtures are expected
        errors = [i for i in issues if i.startswith("ERROR")]
        assert errors == [], f"MotorDemo unexpectedly has errors: {errors}"

    def test_cascade_clean(self):
        rbd = load(str(GOLDEN / "Cascade" / "input.rbd"))
        issues = validate(rbd)
        errors = [i for i in issues if i.startswith("ERROR")]
        assert errors == [], f"Cascade unexpectedly has errors: {errors}"


# ---------------------------------------------------------------------------
# Tests: builder API functions
# ---------------------------------------------------------------------------

class TestBuilderAPI:
    """Basic builder operations work."""

    def test_get_row_by_id(self):
        rbd = _blank_rbd()
        rows = rbd.section("BLOCK_DATA").rows
        real = [r for r in rows if r.get("ID") != "0"]
        if not real:
            pytest.skip("no blocks")
        found = get_row(rbd, "BLOCK_DATA", id=real[0]["ID"])
        assert found is real[0]

    def test_get_row_by_name(self):
        rbd = _blank_rbd()
        rows = rbd.section("BLOCK_DATA").rows
        real = [r for r in rows if r.get("ID") != "0"]
        if not real:
            pytest.skip("no blocks")
        name_col = "blockName"
        name_val = real[0].get(name_col)
        if not name_val:
            pytest.skip("block has no name")
        found = get_row(rbd, "BLOCK_DATA", name=name_val)
        assert found is real[0]

    def test_set_fields_formats_float(self):
        row = {"ID": "1", "PMFreq": "0.000000"}
        set_fields(row, PMFreq=123.456)
        assert row["PMFreq"] == "123.456000"

    def test_set_fields_formats_int(self):
        row = {"ID": "1", "n": "0"}
        set_fields(row, n=5)
        assert row["n"] == "5"

    def test_set_fields_formats_bool(self):
        row = {"ID": "1", "usesPM": "0"}
        set_fields(row, usesPM=True)
        assert row["usesPM"] == "1"

    def test_next_id_returns_unused(self):
        rbd = _blank_rbd()
        used = {int(r["ID"]) for r in rbd.section("BLOCK_DATA").rows if r.get("ID", "0") != "0"}
        nid = next_id(rbd, "BLOCK_DATA")
        assert nid > 0
        assert nid not in used

    def test_sync_counts_updates_header(self):
        rbd = _blank_rbd()
        # Manually add a block row
        add_row(rbd, "BLOCK_DATA", ID=next_id(rbd, "BLOCK_DATA"), blockName="NewBlock")
        # Counts should now be stale
        real_count = sum(1 for r in rbd.section("BLOCK_DATA").rows if r.get("ID") != "0")
        sync_counts(rbd)
        assert rbd.counts["Blocks"] == real_count


# ---------------------------------------------------------------------------
# Tests: CLI validate subcommand via tools/rbd_builder.py shim
# ---------------------------------------------------------------------------

class TestDevdocsShimCLI:
    """The tools/rbd_builder.py shim runs validate from the CLI."""

    def test_validate_clean_fixture(self, tmp_path):
        """``python3 tools/rbd_builder.py validate <fixture>`` works."""
        import subprocess
        import sys
        fixture = str(GOLDEN / "MotorDemo" / "input.rbd")
        result = subprocess.run(
            [sys.executable, "tools/rbd_builder.py", "validate", fixture],
            capture_output=True, text=True, cwd=str(REPO),
            timeout=10,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "no issues found" in result.stdout

    def test_validate_bad_usage(self):
        """Bad usage prints help and exits 2."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "tools/rbd_builder.py"],
            capture_output=True, text=True, cwd=str(REPO),
            timeout=10,
        )
        assert result.returncode == 2
        assert "usage:" in result.stderr

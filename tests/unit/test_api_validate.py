"""Tests for lightweight model validation.

Covers out-of-range Weibull shape, unsupported SimStart type, node-n
mismatch, block/event fan-in/fan-out, capacity binding warnings, clean
golden models, and the `Issue` dataclass. Uses checked-in models under
`tests/golden/`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.validate import Issue, issues
from raptor.core.fileio.rbd import RbdFile, load_rbd

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


def _load(name: str) -> RbdFile:
    return load_rbd(str(GOLDEN / name / "input.rbd"))


# ---------------------------------------------------------------------------
# Tests: each validation rule fires on a deliberately broken file
# ---------------------------------------------------------------------------

class TestWeibullShapeOutOfRange:
    """Out-of-range Weibull shape produces an error."""

    def test_weibull_shape_too_low(self):
        rbd = _load("MotorDemo")
        sec = rbd.sections.get("BLOCK_DATA")
        # Set the first real block to Weibull with shape 0.0 (invalid)
        for row in sec.rows:
            if row.get("ID", "0") != "0":
                row["FDist"] = "15"
                row["#FParam"] = "3"
                row["FParam1"] = "0.0"  # shape < 0.000001 threshold
                row["FParam2"] = "100.0"
                row["FParam3"] = "0.0"
                break

        result = issues(rbd)
        weibull_errs = [i for i in result
                        if "Shape" in i.message and "0.000001" in i.message]
        assert weibull_errs, f"Expected Weibull shape error, got: {[str(i) for i in result]}"


class TestSimStartTypes:
    """The validator accepts exactly the start types the engine implements
    (`block_lifecycle.START_TYPES`, 1..6) and rejects anything else."""

    @staticmethod
    def _with_start(value):
        rbd = _load("MotorDemo")
        sec = rbd.sections.get("BLOCK_DATA")
        for row in sec.rows:
            if row.get("ID", "0") != "0":
                row["SimStart"] = value
                break
        return rbd

    @pytest.mark.parametrize("value", ["1", "2", "3", "4", "5", "6",
                                       "3.000000", "6.000000"])
    def test_engine_start_types_are_accepted(self, value):
        result = issues(self._with_start(value))
        assert not [i for i in result if "SimStart" in i.message], \
            [str(i) for i in result]

    @pytest.mark.parametrize("value", ["0", "7", "-1", "99", "7.000000"])
    def test_other_values_are_errors(self, value):
        result = issues(self._with_start(value))
        errs = [i for i in result if "SimStart type" in i.message]
        assert errs and errs[0].severity == "error", \
            [str(i) for i in result]

    @pytest.mark.parametrize("value", ["", "abc"])
    def test_unparseable_value_falls_back_to_the_default(self, value):
        # The loader's own default is 3, so a blank cell is not an error.
        result = issues(self._with_start(value))
        assert not [i for i in result if "SimStart" in i.message]

    def test_validator_rule_is_the_engines_own_set(self):
        from raptor.core.engine.block_lifecycle import START_TYPES
        for t in range(-2, 10):
            flagged = bool([i for i in issues(self._with_start(str(t)))
                            if "SimStart type" in i.message])
            assert flagged == (t not in START_TYPES), t


class TestStructuralNodeNMismatch:
    """Structural error (node n mismatch) is caught via builder.validate()."""

    def test_node_n_mismatch(self):
        rbd = _load("MotorDemo")
        nodes = rbd.sections.get("NODE_DATA")
        links = rbd.sections.get("LINK_DATA")
        # Add an extra link to the start node (which has n=0)
        start = None
        for row in nodes.rows:
            if row.get("Type") == "1":
                start = row
                break
        assert start is not None, "no start node"
        next_id = max((int(l.get("ID", 0)) for l in links.rows), default=0) + 1
        links.rows.append({
            "ID": str(next_id),
            "FromType": "Block", "FromID": "5",
            "ToType": "Node", "ToID": start["ID"],
            "pID": "0", "ColdPri": "0", "AProb": "1.0",
            "AutoTime": "0.0", "ManTime": "0.0",
            "CapPri": "0", "nomFlow": "0.0", "MaxFlow": "0.0",
        })

        result = issues(rbd)
        n_errs = [i for i in result if "declares n=" in i.message and "real incoming" in i.message]
        assert n_errs, f"Expected node-n mismatch error, got: {[str(i) for i in result]}"


class TestBlockEventFanInOut:
    """Fan-in/fan-out on a block or event: the shapes Raptor 7 will not let
    you draw (`LinkObj.CheckValidLink`). A file that
    has them still opens and runs in Raptor 7 (`MultiOutLink` is a capture of
    one), so `validate()` warns with Raptor 7's own text; the edit tools refuse
    to create them (tests/unit/test_input_rules.py)."""

    def _add_link(self, rbd, ftype, fid, ttype, tid):
        from raptor.core.fileio.rbdlib import make_link_row
        links = rbd.section("LINK_DATA").rows
        lid = max((int(l["ID"]) for l in links), default=0) + 1
        links.append(make_link_row(lid, ftype, fid, ttype, tid))
        return lid

    def _warnings(self, rbd, text):
        return [i for i in issues(rbd) if i.severity == "warning" and text in i.message]

    def test_block_fan_out_is_a_raptor7_link_warning(self):
        rbd = _load("MotorDemo")
        end = [n for n in rbd.section("NODE_DATA").rows
               if n.get("Type") == "3"][0]
        # Casing (block 1) already feeds one node; add a second out-link.
        self._add_link(rbd, "Block", 1, "Node", end["ID"])
        assert self._warnings(rbd, "A block can only connect to one object!"), \
            [str(i) for i in issues(rbd)]

    def test_block_fan_in_is_a_raptor7_link_warning(self):
        rbd = _load("MotorDemo")
        # A second block feeding block 1 makes its real fan-in 2.
        self._add_link(rbd, "Block", 2, "Block", 1)
        assert self._warnings(rbd, "Cannot connect more than one link to a block!"), \
            [str(i) for i in issues(rbd)]

    def test_event_fan_out_is_a_raptor7_link_warning(self):
        rbd = _load("Events")
        event = rbd.section("EVENT_DATA").rows[0]
        node = rbd.section("NODE_DATA").rows[0]
        self._add_link(rbd, "Event", int(event["ID"]), "Node", int(node["ID"]))
        assert self._warnings(rbd, "An event can only connect to one object!"), \
            [str(i) for i in issues(rbd)]


class TestCapacityWarnings:
    """Capacity analysis enabled but unable to bind produces warnings."""

    def _cap_on(self, rbd, flow_generated):
        row = rbd.section("SYSTEM_DATA").rows[0]
        row["capacityAnalysis"] = "1"
        row["flowGenerated"] = str(flow_generated)
        return row

    def _capacity_warnings(self, rbd):
        return [i for i in issues(rbd) if i.severity == "warning"
                and ("capacityAnalysis" in i.message
                     or "capacity-boundary" in i.message
                     or "nom_flow/max_flow" in i.message)]

    def test_zero_demand_warns(self):
        rbd = _load("MotorDemo")
        self._cap_on(rbd, 0)
        warns = [i for i in self._capacity_warnings(rbd)
                 if "flowGenerated <= 0" in i.message]
        assert warns, [str(i) for i in issues(rbd)]

    def test_no_boundary_node_warns(self):
        rbd = _load("MotorDemo")
        self._cap_on(rbd, 100)
        warns = [i for i in self._capacity_warnings(rbd)
                 if "capacity-boundary" in i.message]
        assert warns, [str(i) for i in issues(rbd)]

    def test_caps_without_boundary_warns(self):
        rbd = _load("MotorDemo")
        self._cap_on(rbd, 100)
        rbd.section("LINK_DATA").rows[0]["MaxFlow"] = "500"
        warns = [i for i in self._capacity_warnings(rbd)
                 if "silently ignored" in i.message]
        assert warns, [str(i) for i in issues(rbd)]

    def test_boundary_and_demand_clear(self):
        rbd = _load("MotorDemo")
        self._cap_on(rbd, 100)
        # A full_flow=0 node is a capacity boundary.
        rbd.section("NODE_DATA").rows[0]["fullflow"] = "0"
        rbd.section("LINK_DATA").rows[0]["MaxFlow"] = "500"
        assert self._capacity_warnings(rbd) == []

    def test_capacity_off_no_warnings(self):
        rbd = _load("MotorDemo")
        assert self._capacity_warnings(rbd) == []


class TestGoldenFanInOut:
    """No golden model violates the fan-in/fan-out rule."""

    @pytest.mark.parametrize("name", [
        p.parent.name for p in sorted(GOLDEN.glob("*/input.rbd"))
    ])
    def test_no_golden_fan_errors(self, name):
        rbd = _load(name)
        fan = [i for i in issues(rbd) if i.severity == "error"
               and ("fan-in" in i.message or "fan-out" in i.message)]
        assert fan == [], f"{name}: {[str(i) for i in fan]}"


class TestCleanGoldenModel:
    """A known-good golden model returns no ERROR-level issues."""

    def test_motor_demo_clean(self):
        rbd = _load("MotorDemo")
        result = issues(rbd)
        errors = [i for i in result if i.severity == "error"]
        assert errors == [], f"MotorDemo unexpectedly has errors: {[str(i) for i in errors]}"

    def test_cascade_clean(self):
        rbd = _load("Cascade")
        result = issues(rbd)
        errors = [i for i in result if i.severity == "error"]
        assert errors == [], f"Cascade unexpectedly has errors: {[str(i) for i in errors]}"

    def test_space_station_clean(self):
        rbd = _load("SpaceStation")
        result = issues(rbd)
        errors = [i for i in result if i.severity == "error"]
        assert errors == [], f"SpaceStation unexpectedly has errors: {[str(i) for i in errors]}"

    def test_events_clean(self):
        rbd = _load("Events")
        result = issues(rbd)
        errors = [i for i in result if i.severity == "error"]
        assert errors == [], f"Events unexpectedly has errors: {[str(i) for i in errors]}"


class TestEventProbabilityValidation:
    """Events store their success probability in ``failVal``, not
    ``Param1..3`` -- regression test for a bug where a valid probability
    (e.g. 0.95) was always reported as "P Success must be between
    0.000001 and 1" because the check read the wrong column.
    """

    def test_valid_probability_is_not_flagged(self):
        rbd = _load("Events")
        event = rbd.section("EVENT_DATA").rows[0]
        event["failVal"] = "0.95"
        errs = [i for i in issues(rbd) if i.severity == "error"
                and "P Success" in i.message]
        assert errs == [], f"valid prob 0.95 flagged: {[str(i) for i in errs]}"

    def test_out_of_range_probability_is_still_flagged(self):
        rbd = _load("Events")
        event = rbd.section("EVENT_DATA").rows[0]
        event["failVal"] = "1.5"
        errs = [i for i in issues(rbd) if i.severity == "error"
                and "P Success" in i.message]
        assert errs, "prob 1.5 should still be flagged as out of range"


# ---------------------------------------------------------------------------
# Distribution checks on golden fixtures
# ---------------------------------------------------------------------------

class TestAgreementWithGUI:
    """Distribution parameter checks find no errors on golden fixtures."""

    @pytest.mark.parametrize("fixture", ["MotorDemo", "Cascade", "SpaceStation", "Events"])
    def test_no_distribution_errors_on_golden(self, fixture: str):
        """Distribution parameter checks should find no errors on known-good
        golden fixtures."""
        rbd = _load(fixture)
        result = issues(rbd)
        # Filter to distribution-related errors only
        dist_errs = [i for i in result if i.severity == "error"
                     and ("must be" in i.message or "cannot both" in i.message
                          or "integer" in i.message)]
        assert dist_errs == [], (
            f"{fixture} has distribution errors: {[str(i) for i in dist_errs]}"
        )


# ---------------------------------------------------------------------------
# Issue dataclass
# ---------------------------------------------------------------------------

class TestIssueDataclass:
    """Issue has the right shape and string representation."""

    def test_severity_and_message(self):
        issue = Issue("error", "Block 5 'Pump'", "Weibull shape too low")
        assert issue.severity == "error"
        assert issue.element == "Block 5 'Pump'"
        assert issue.message == "Weibull shape too low"

    def test_str_representation(self):
        issue = Issue("warning", "model", "header count drift")
        assert str(issue) == "WARN: model: header count drift"

    def test_error_str(self):
        issue = Issue("error", "Block 3", "SimStart type 1")
        assert str(issue) == "ERROR: Block 3: SimStart type 1"


class TestUndefinedSparePool:
    """A Pooled block naming a pool that does not exist used to crash the
    run with EngineInvariantError; it is now an error before Run."""

    def test_unknown_pool_is_an_error(self):
        rbd = _load("PmMisDeferGiveBackProbe")
        rows = [r for r in rbd.sections["BLOCK_DATA"].rows
                if r.get("SpType") == "Pooled"]
        assert rows, "premise: fixture has a pooled block"
        rows[0]["SparePoolName"] = "NoSuchPool"
        errs = [i for i in issues(rbd) if "NoSuchPool" in i.message]
        assert errs and errs[0].severity == "error"

    def test_existing_pools_are_accepted(self):
        rbd = _load("PmMisDeferGiveBackProbe")
        assert not [i for i in issues(rbd) if "spare pool" in i.message]


class TestGdType4:
    """GDType > 3 is read and run by real Raptor (GDTypeProbe, LadL7Gd, ZeroFail*Probe
    are captures); it is reproducible only when GDLimit / mean failure time is in
    (0, 1), so the validator warns there and refuses elsewhere."""

    def _rbd(self, gd_limit):
        from pathlib import Path
        from raptor.core.fileio.rbd import load_rbd
        rbd = load_rbd(str(Path(__file__).resolve().parents[1] / "golden" / "GDTypeProbe" / "input.rbd"))
        return rbd

    def test_the_captured_probe_is_a_warning_not_an_error(self):
        from raptor.api.validate import _check_gd_types
        issues = [i for i in _check_gd_types(self._rbd(None))]
        assert issues and all(i.severity == "warning" for i in issues)

    def test_a_ratio_outside_the_window_is_refused(self):
        from raptor.api.validate import _check_gd_types
        rbd = self._rbd(None)
        for row in rbd.section("BLOCK_DATA").rows:
            if row.get("GDType") == "4":
                row["GDLimit"] = "1000000.000000"       # ratio far above 1
        issues = _check_gd_types(rbd)
        assert issues and all(i.severity == "error" for i in issues)
        assert "uninitialised memory" in issues[0].message


def test_duplicate_trigger_pool_and_phase_names_are_warned_about():
    """Other things refer to pools, triggers and phases by name, so two with one name are ambiguous. Blocks may
    share names (Raptor 7's FactoryDemo does) and are not flagged."""
    rbd = _load("LadL6Full")
    sec = rbd.sections["TRIGGERS"]
    sec.rows.append(dict(sec.rows[0]))
    msgs = [i.message for i in issues(rbd) if "are named" in i.message]
    assert len(msgs) == 1 and "trigger" in msgs[0]
    assert not [i for i in issues(_load("FactoryDemo")) if "are named" in i.message]


def test_a_stale_flat_hierarchy_level_is_warned_about():
    """Raptor 7 walks nested hierarchies by `level`; a flat value makes the
    pre-sim analysis skip a subtree, leaving a 1..0 array allocation that
    crashes it on Simulate. `save_rbd` derives the level, so this is a
    warning rather than a blocked export."""
    rbd = _load("LadL8A")
    hiers = [r for r in rbd.section("HIERARCHY_DATA").rows if r["ID"] != "0"]
    child = next(r for r in hiers if r["Parent"] not in ("0", ""))
    for row in hiers:
        row["level"] = "1"

    found = [i for i in issues(rbd)
             if i.severity == "warning" and "level(s) deep" in i.message]
    assert found, [i.message for i in issues(rbd)]
    assert any(child["ID"] in i.message for i in found)

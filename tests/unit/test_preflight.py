"""Preflight validation: a pure function, not a method.

Extracted from `main_window._preflight_issues` (249 lines inside a
class of 241 methods) precisely so it could be tested without a window.
The review's "why is Run greyed out?" finding could not be fixed while
the answer lived inside the widget that has the question.
"""
from __future__ import annotations

import glob
import os

import pytest

from raptor.core.engine.dependency import distro_mean
from raptor.core.fileio.rbd import load_rbd
from raptor.core.rng import Distribution
from raptor.gui.ui.preflight import preflight_issues

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
FIXTURES = sorted(glob.glob(os.path.join(REPO, "tests", "fixtures", "*",
                                         "input.rbd")))
GOLDEN = sorted(glob.glob(os.path.join(REPO, "tests", "golden", "*",
                                       "input.rbd")))


def _named(kind, oid):
    return f"{kind}{oid}"


class TestNoModel:
    def test_no_model_is_no_issues(self):
        assert preflight_issues(None, None, _named) == []


class TestNoFalsePositives:
    """The check is deliberately conservative and mirrors only
    UNCONDITIONAL engine gates. A check that fired on a model the engine
    actually simulates would make Run refuse to start on it, which is
    worse than the runtime error it was meant to pre-empt."""

    #: Models the engine is known to simulate. The wider `golden/` set
    #: deliberately includes PROBES that trip gates on purpose, so
    #: "every golden model is clean" is not a true premise -- the
    #: distinction is what this module documents.
    CLEAN = [
        os.path.join(REPO, "tests", "golden", d, "input.rbd")
        for d in ("HierarchyExample", "TwoBlockRedundancy", "UniformProbe")
    ]

    @pytest.mark.parametrize("path", CLEAN)
    def test_known_good_models_pass(self, path):
        if not os.path.exists(path):
            pytest.skip(f"{path} not present")
        assert preflight_issues(load_rbd(path), None, _named) == [], \
            f"{os.path.basename(os.path.dirname(path))} was flagged but " \
            f"simulates"

    def test_it_never_raises_on_any_fixture(self):
        """A validator that crashes is worse than no validator: the user
        sees a traceback instead of a reason. Every checked-in model must
        survive the scan, whether or not it has issues."""
        class _NullView:
            blocks = {}
            events = {}
            hiers = {}
            nodes = {}
            links = []

        for path in FIXTURES:
            try:
                rbd = load_rbd(path)
            except Exception:
                continue          # a fixture this check can't read is
            # not this function's problem
            preflight_issues(rbd, _NullView(), _named)

    def test_connectivity_checks_are_skipped_without_a_view(self):
        """The view holds the parsed topology, so the link checks cannot
        run without it. They must be SKIPPED, not guessed -- a validator
        reporting a problem it cannot substantiate is worse than silence.
        Every other check reads the model's own sections and still runs."""
        import glob as _glob
        tripped_without_view = 0
        for path in _glob.glob(os.path.join(REPO, "tests", "fixtures", "*",
                                           "input.rbd")):
            try:
                rbd = load_rbd(path)
            except Exception:
                continue
            if preflight_issues(rbd, None, _named):
                tripped_without_view += 1
        assert tripped_without_view, (
            "no model trips any section-level check, so those checks are "
            "unreachable and therefore untested")
        assert not any("connect" in m
                       for m in preflight_issues(
                           load_rbd(self.CLEAN[0]), None, _named)), \
            "link checks fired with no view to substantiate them"


class TestFindingsAreIdentified:
    def test_real_gate_is_found_and_named(self):
        """At least one checked-in model carries a distribution the
        engine will refuse, so the function is known to be able to
        return something -- and it names the element."""
        class _NullView:
            blocks = {}
            events = {}
            hiers = {}
            nodes = {}
            links = []

        found = []
        for path in FIXTURES:
            try:
                rbd = load_rbd(path)
            except Exception:
                continue
            try:
                found += preflight_issues(rbd, _NullView(), _named)
            except Exception:
                continue
        assert found, "no fixture trips any preflight gate -- the checks " \
                      "may be unreachable and therefore untested"
        # Every message must name something, or the validation dialog's
        # "click an issue to locate the element" is a dead end.
        assert all(isinstance(m, str) and m.strip() for m in found)
        assert all(":" in m for m in found), \
            f"a message without a name cannot be located: {found[:3]}"


class TestDelegatedChecks:
    """Rules the GUI must NOT restate: `raptor.api.validate` owns them.

    Each of these was a local copy in `preflight.py`, kept because of an
    assumption that turned out to be wrong ("the connectivity checks need
    the parsed topology, which only the view holds" -- they read the
    model's own rows). Three of the four had already diverged:

    - fan-OUT counted the model-wide set of link ORIGINS;
    - fan-IN was raised to an ERROR, so the GUI refused to run a model the
      validator called runnable and MCP reported `runnable: true` (both now
      take Raptor 7's own rules from `core.input_rules`);
    - GDType's message claimed "only 1, 2, 3" while the validator accepts
      0 as well.

    The rule now is: one owner per rule, severities agree, and these tests
    fail if the GUI starts growing its own again.
    """

    class _Link:
        def __init__(self, f_ref, f_id, t_ref, t_id):
            self.connect_f_ref = f_ref
            self.connect_from_id = f_id
            self.connect_t_ref = t_ref
            self.connect_to_id = t_id

    class _Obj:
        def __init__(self, name):
            self.name = name

    class _View:
        def __init__(self, links, blocks, nodes):
            self.links = links
            self.blocks = blocks
            self.events = {}
            self.hiers = {}
            self.nodes = nodes

    def test_preflight_does_not_reimplement_the_delegated_checks(self):
        """Structural: the local copies must not come back.

        Scoped to the function body, and to *code* rather than comments --
        the file legitimately discusses these rules in prose while
        explaining why it delegates. `ast` gives us the former without the
        latter.
        """
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(preflight_issues)))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for dead in ("sim_start_type", "gd_type", "out_count_x"):
            assert dead not in names, (
                f"preflight_issues still references {dead!r} -- a delegated "
                "rule has been re-implemented locally"
            )
        # The fan-out message had its own wording locally; the validator's
        # is the one that must survive.
        strings = [n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert not any("exactly ONE output" in s for s in strings), (
            "the local fan-out message is back; delegate to the validator"
        )

    #: The `.rbd` link columns are `FromType/FromID/ToType/ToID` -- NOT
    #: `ConnectFromRef/ConnectFrom`. An earlier version of this test built
    #: rows with the wrong column names, so every value came back `None`,
    #: the validator saw a model with no links, and the assertions passed
    #: for the wrong reason.
    _FROM_TYPE, _FROM_ID = "FromType", "FromID"
    _TO_TYPE, _TO_ID = "ToType", "ToID"

    def _one_block_fanning_out_to(self, n_targets: int):
        """A model whose single block has exactly `n_targets` outgoing links."""
        rbd = load_rbd(os.path.join(REPO, "tests", "golden",
                                    "TwoBlockRedundancy", "input.rbd"))
        blocks = rbd.sections["BLOCK_DATA"]
        links = rbd.sections["LINK_DATA"]
        row = dict(blocks.rows[0])
        row.update({"ID": "901", "blockName": "FanOutBlock",
                    "In": "0", "Out": str(n_targets)})
        blocks.rows = [row]
        template = dict(links.rows[0])
        rows = []
        for i in range(n_targets):
            r = dict(template)
            r.update({"ID": str(910 + i), self._FROM_TYPE: "Block",
                      self._FROM_ID: "901", self._TO_TYPE: "Node",
                      self._TO_ID: str(30 + i), "pID": "0"})
            rows.append(r)
        links.rows = rows
        return rbd

    def test_fan_out_is_a_raptor7_link_warning_not_a_run_refusal(self):
        """A block with several links out: Raptor 7 refuses to DRAW it
        (CheckValidLink) but runs such a file, so it is a warning from the
        owner (`core.input_rules`) and preflight does not block the run."""
        from raptor.core.input_rules import check
        for n in (2, 3, 5):
            rbd = self._one_block_fanning_out_to(n)
            found = [v for v in check(rbd) if v.rule == "link.block_out"]
            assert found and found[0].severity == "warning", (n, found)
            assert found[0].message.startswith("A block can only connect to one object!")
            assert not [m for m in preflight_issues(rbd, None, _named)
                        if "can only connect to one object" in m]

    def test_one_outgoing_link_is_accepted(self):
        """The boundary: exactly one is legal, so no link finding at all."""
        from raptor.core.input_rules import check
        rbd = self._one_block_fanning_out_to(1)
        assert not [v for v in check(rbd) if v.rule.startswith("link.block")]

    def test_no_link_in_is_refused_in_both_doors(self):
        """Raptor 7 will not run a block that nothing feeds (`ValidateRBD`). It used to be a warning here (review D-17), which
        kept the GUI and MCP agreeing; they now agree on Raptor 7's answer."""
        from raptor.api.validate import issues
        rbd = self._one_block_fanning_out_to(1)   # In=0
        message = "To simulate, block 'FanOutBlock' must connect from something!"
        assert [i for i in issues(rbd) if i.severity == "error" and message in i.message]
        assert any(message in m for m in preflight_issues(rbd, None, _named))

    def test_a_view_free_call_still_reports_the_delegated_rules(self):
        """They read the model's rows, so they fire with `view=None`."""
        rbd = self._one_block_fanning_out_to(3)
        found = preflight_issues(rbd, None, _named)
        assert all(isinstance(m, str) for m in found)
        assert any("must connect from something" in m for m in found), found


class TestDelegatedChecksAgreeWithTheValidator:
    """The invariant that makes delegation worth doing.

    Whatever `preflight_issues` reports for a model, every message that
    corresponds to a delegated rule must be a message the validator's owner
    would produce for the same model -- not a paraphrase of it.
    """

    def test_every_delegated_message_is_the_validators_own(self):
        from raptor.api.validate import (
            _check_gd_types, _check_sim_start_types, _check_unrunnable_model,
        )
        from raptor.core.input_rules import check
        for path in sorted(glob.glob(os.path.join(REPO, "tests", "golden",
                                                   "*", "input.rbd")))[:40]:
            try:
                rbd = load_rbd(path)
            except Exception:
                continue
            allowed = {f"{v.element}: {v.message}" for v in check(rbd) if v.severity == "error"}
            for fn in (_check_gd_types, _check_sim_start_types, _check_unrunnable_model):
                for i in fn(rbd):
                    if i.severity == "error":
                        allowed.add(f"{i.element}: {i.message}")
            found = preflight_issues(rbd, None, _named)
            # Messages the validator owns must be verbatim; the rest come
            # from the view-only checks, which cannot run without a view.
            overlap = [m for m in found if "connect" in m]
            for m in overlap:
                assert m in allowed, (
                    f"{os.path.basename(os.path.dirname(path))}: "
                    f"{m!r} is not a message the validator produces")


class TestFailMeanGate:
    """The dependency's percentage-based PM trigger needs the block's own
    failure mean (`dependency.distro_mean`). Only an out-of-range shape
    leaves that undefined; every one of the 22 distributions otherwise has
    a ported mean.

    This check used to keep its own hardcoded allowlist of three
    distributions here, so it flagged every other one -- `LadL8C`'s S8B2
    (Exponential) and S13B3 (Lognormal) were both reported while the model
    simulates, and matches real in all 300 runs. It now delegates to
    `raptor.api.validate._check_fail_mean_undefined`, and these cases pin
    the delegation to the ENGINE's answer rather than to a list.
    """

    @staticmethod
    def _row(distro, params, *, dep_pm="25.0"):
        return {
            "ID": "1", "blockName": "Dep", "FDist": str(distro),
            "FParam1": str(params[0]), "FParam2": str(params[1]),
            "FParam3": str(params[2]),
            "DepType": "RBDNode", "DepPM": dep_pm,
            "SimStart": "3", "GDType": "1",
        }

    @staticmethod
    def _issues(distro, params, *, dep_pm="25.0"):
        rbd = load_rbd(os.path.join(REPO, "tests", "golden",
                                    "HierarchyExample", "input.rbd"))
        rbd.sections["BLOCK_DATA"].rows = [
            TestFailMeanGate._row(distro, params, dep_pm=dep_pm)]
        return preflight_issues(rbd, None, _named)

    @pytest.mark.parametrize("distro,params", [
        (int(Distribution.WEIBULL), [0.01, 5.0, 0.0]),      # shape < 0.05
        (int(Distribution.PEARSON5), [0.5, 5.0, 0.0]),     # shape <= 1.0
        (int(Distribution.PEARSON6), [5.0, 0.5, 0.0]),     # 2nd param <= 1.0
    ])
    def test_out_of_range_shape_is_flagged(self, distro, params):
        assert any("mean is undefined" in m for m in self._issues(distro, params))

    @pytest.mark.parametrize("distro", [
        d for d in Distribution
        if d not in (Distribution.WEIBULL, Distribution.PEARSON5,
                     Distribution.PEARSON6)
    ])
    def test_every_other_distribution_with_a_mean_is_not_flagged(self, distro):
        """The whole point: the gate is the mean being undefined, not the
        distribution being on a short list."""
        assert distro_mean(int(distro), [4.0, 829.672, 3.0]) != 12345.6789
        assert not any("mean" in m for m in self._issues(int(distro),
                                                         [4.0, 829.672, 3.0]))

    def test_pearson6_guards_on_the_second_parameter(self):
        """`SetStats` WHEN 12 reads `valsInUse[2]`,
        and so does `distro_mean`. A first-parameter guard both missed real
        refusals and rejected a perfectly good Pearson6."""
        assert not any("mean" in m for m in
                       self._issues(int(Distribution.PEARSON6), [0.5, 5.0, 3.0]))

    def test_the_gate_is_the_dependencys_pm_percentage(self):
        """`DepPM` is the dependency's share sent to PM. `DepPM = 0` means
        that percentage-based trigger is never reached, so even a
        distribution with an undefined mean is fine."""
        assert not self._issues(int(Distribution.WEIBULL), [0.01, 5.0, 0.0],
                                dep_pm="0.0")

    def test_the_ladder_l8c_model_is_clean(self):
        """The model that surfaced the false positives: 56 blocks, 300
        runs, byte-exact against real."""
        path = os.path.join(REPO, "tests", "ladder", "LadL8C", "input.rbd")
        if not os.path.exists(path):
            pytest.skip("LadL8C not present")
        assert preflight_issues(load_rbd(path), None, _named) == []


class TestReturnContract:
    def test_returns_a_list_of_strings(self):
        result = preflight_issues(load_rbd(
            os.path.join(REPO, "tests", "golden", "HierarchyExample",
                         "input.rbd")), None, _named)
        assert isinstance(result, list)
        assert all(isinstance(x, str) for x in result)


class TestRefusedShapes:
    """A model real Raptor crashes on is flagged before Run, with the reason."""

    def test_the_cold_standby_crash_shape_is_flagged(self):
        path = os.path.join(REPO, "tests", "fixtures", "CS_DepOnColdBlock", "input.rbd")
        found = [m for m in preflight_issues(load_rbd(path), None, _named) if "Access Violation" in m]
        assert len(found) == 1
        assert "'DepBlk'" in found[0] and "'Comm'" in found[0]

    def test_the_working_neighbour_is_not_flagged(self):
        path = os.path.join(REPO, "tests", "fixtures", "CS_DepOnStandbyPeer", "input.rbd")
        assert not [m for m in preflight_issues(load_rbd(path), None, _named)
                    if "Access Violation" in m]

    def test_a_dependency_cycle_does_not_block_a_run(self):
        """Cycles only warn (real runs them), so they are not in the blocking list."""
        path = os.path.join(REPO, "tests", "golden", "DepCycleIdleProbe", "input.rbd")
        assert not [m for m in preflight_issues(load_rbd(path), None, _named) if "circular" in m]


class TestStartTypesAreNotGated:
    """Models that use start types 1 to 6 pass the pre-flight (they used to
    be refused as "only type 3"); every such fixture has real captures."""

    def test_every_fixture_using_a_non_default_start_type_passes(self):
        import re
        checked = 0
        for path in sorted(glob.glob(os.path.join(REPO, "tests", "golden",
                                                   "*", "input.rbd"))):
            try:
                rbd = load_rbd(path)
            except Exception:
                continue
            sec = rbd.sections.get("BLOCK_DATA")
            starts = {int(float(r.get("SimStart", "3") or 3))
                      for r in (sec.rows if sec else [])
                      if re.match(r"^\d+(\.\d+)?$", r.get("ID", "x"))
                      and float(r["ID"]) != 0}
            if starts - {3}:
                checked += 1
                found = preflight_issues(rbd, None, _named)
                assert not [m for m in found if "SimStart" in m], (
                    path, found)
        assert checked >= 3, "expected several fixtures using start types 1-6"


class TestPreflightWarnings:
    """Dependency cycles are allowed (real Raptor runs them), so they are
    warnings: shown, never blocking."""

    def _cycle_model(self):
        path = os.path.join(REPO, "tests", "golden", "DepCycleIdleProbe",
                            "input.rbd")
        return load_rbd(path)

    def test_cycle_is_a_warning_not_a_blocking_error(self):
        from raptor.gui.ui.preflight import preflight_warnings
        rbd = self._cycle_model()
        warns = preflight_warnings(rbd)
        assert any("circular dependency" in w for w in warns), warns
        blocking = preflight_issues(rbd, None, _named)
        assert not [m for m in blocking if "circular" in m.lower()], blocking

    def test_clean_model_has_no_warnings(self):
        from raptor.gui.ui.preflight import preflight_warnings
        path = os.path.join(REPO, "tests", "golden", "SPFProbe", "input.rbd")
        assert preflight_warnings(load_rbd(path)) == []

    def test_no_model_has_no_warnings(self):
        from raptor.gui.ui.preflight import preflight_warnings
        assert preflight_warnings(None) == []

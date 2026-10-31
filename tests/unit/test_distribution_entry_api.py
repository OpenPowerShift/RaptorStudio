"""Distribution entry through the API and a hand-edited .rbds file.

An invalid distribution/parameter pair must be refused when it is entered, or
reported by `raptor.api.validate.issues`; a valid one must land and validate
cleanly. Nothing invalid may be silently accepted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.api import build, paths
from raptor.api.validate import issues
from raptor.api.workspace import Workspace
from raptor.core.fileio.rbds import parse_rbds

REPO = Path(__file__).resolve().parent.parent.parent
AIRLINER = REPO / "new_docs" / "models" / "AirlinerMission.rbds"

#: (distribution, params) pairs the rules refuse
INVALID = [
    (3, [2.5, 0.5, 0.0]),        # Binomial: N Trials not an integer
    (3, [4.0, 1.5, 0.0]),        # Binomial: probability above 1
    (3, [0.25, 0.0, 0.0]),       # the reported pair: Fixed 0.25 read as Binomial
    (14, [20.0, 10.0, 5.0]),     # Triangular out of order
    (10, [9.0, 2.0, 0.0]),       # Uniform min > max
    (15, [0.0, 100.0, 0.0]),     # Weibull shape 0
    (2, [2.5, 0.0, 0.0]),        # Chi-square shape not an integer
    (8, [-5.0, 1.0, 0.0]),       # Normal mean negative
    (4, [float("nan"), 0.0, 0.0]),
]
VALID = [
    (3, [5.0, 0.4, 0.0]),
    (14, [5.0, 10.0, 15.0]),
    (15, [1.5, 600.0, 0.0]),
    (2, [3.0, 0.0, 0.0]),
]
_ID = lambda p: f"{p[0]}-{p[1]}"      # noqa: E731


def _errors(rbd):
    return [i for i in issues(rbd) if i.severity == "error"]


def _plain_doc():
    """The Airliner document with its rate links removed: Engine_1's second
    parameter is linked to a rate, which would overwrite a hand edit."""
    d = json.loads(AIRLINER.read_text())
    d["formulas"] = {}
    return d


def _edited_rbds(fail_dist=None, fail_params=None, phase=None):
    d = _plain_doc()
    if fail_dist is not None:
        d["elements"]["blocks"][0]["fail_dist"] = fail_dist
        d["elements"]["blocks"][0]["fail_params"] = fail_params
    if phase is not None:
        d["phases"][0]["duration_dist"] = phase[0]
        d["phases"][0]["params"] = list(phase[1])
    # json.dumps writes NaN as the bare token NaN, which a hand edit can do too
    return parse_rbds(json.dumps(d), "hand_edited.rbds")


def test_the_unedited_model_has_no_errors():
    assert _errors(_edited_rbds()) == []


# --------------------------------------------------------------------------
# .rbds
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pair", INVALID, ids=_ID)
def test_rbds_invalid_failure_distribution_is_reported(pair):
    rbd = _edited_rbds(fail_dist=pair[0], fail_params=pair[1])
    errs = _errors(rbd)
    assert errs, f"{pair} was not reported"
    assert any("Engine_1" in e.element or "Engine_1" in e.message
               or "fail" in (e.element + e.message).lower() for e in errs)


@pytest.mark.parametrize("pair", VALID, ids=_ID)
def test_rbds_valid_failure_distribution_is_clean(pair):
    assert _errors(_edited_rbds(fail_dist=pair[0], fail_params=pair[1])) == []


@pytest.mark.parametrize("pair", INVALID, ids=_ID)
def test_rbds_invalid_phase_length_is_reported(pair):
    errs = _errors(_edited_rbds(phase=pair))
    assert errs, f"phase {pair} was not reported"
    assert any("hase" in (e.element + e.message) for e in errs)


@pytest.mark.parametrize("pair", VALID, ids=_ID)
def test_rbds_valid_phase_length_is_clean(pair):
    assert _errors(_edited_rbds(phase=pair)) == []


# --------------------------------------------------------------------------
# update_element / path assignments / configure_phases
# --------------------------------------------------------------------------

@pytest.fixture
def ws_model(tmp_path):
    ws = Workspace(tmp_path / "ws")
    src = tmp_path / "airliner_plain.rbds"
    src.write_text(json.dumps(_plain_doc()))
    return ws, ws.open_model(src)["model_id"]


def _refused_or_reported(ws, mid, action):
    """True when `action` raised a BuildError/PathError/ValueError, or the
    model is left reporting an error."""
    try:
        action()
    except (build.BuildError, paths.PathError, ValueError):
        return True
    return bool(_errors(ws.get(mid).rbd))


@pytest.mark.parametrize("pair", INVALID, ids=_ID)
def test_update_element_invalid_pair_is_refused_or_reported(ws_model, pair):
    ws, mid = ws_model
    assert _refused_or_reported(ws, mid, lambda: build.update_element(
        ws, mid, "Engine_1", {"fail_dist": pair[0], "fail_params": pair[1]}))


@pytest.mark.parametrize("pair", VALID, ids=_ID)
def test_update_element_valid_pair_lands_and_is_clean(ws_model, pair):
    ws, mid = ws_model
    row = build.update_element(
        ws, mid, "Engine_1", {"fail_dist": pair[0], "fail_params": pair[1]})
    assert int(float(row["FDist"])) == pair[0]
    assert [float(row[f"FParam{i}"]) for i in (1, 2, 3)] == pytest.approx(pair[1])
    assert _errors(ws.get(mid).rbd) == []


@pytest.mark.parametrize("code", [3, 2, 14, 10])
def test_update_element_code_alone_never_leaves_an_unreported_invalid_pair(ws_model, code):
    """Weibull(1.5, 600) changed to another code with no parameters: refused,
    or the pair is valid, or the model reports it (Uniform(1.5, 600) is valid)."""
    from raptor.core.distro_rules import validate_distro_params
    ws, mid = ws_model
    try:
        build.update_element(ws, mid, "Engine_1", {"fail_dist": code})
    except (build.BuildError, ValueError):
        return
    row = next(r for r in ws.get(mid).rbd.sections["BLOCK_DATA"].rows
               if r["blockName"] == "Engine_1")
    pair = (int(row["FDist"]), [float(row[f"FParam{i}"]) for i in (1, 2, 3)])
    if validate_distro_params(pair[0], pair[1], "x") is not None:
        assert _errors(ws.get(mid).rbd), f"{pair} accepted and not reported"


def test_path_assignment_binomial_with_probability(ws_model):
    ws, mid = ws_model
    # Parameters first: switching to Binomial while the block still carries
    # Weibull's (1.3, 823, 0) would make N Trials 1.3 and be refused.
    paths.apply_assignment(ws, mid, "block:Engine_1.fail_params=5,0.4")
    paths.apply_assignment(ws, mid, "block:Engine_1.fail_dist=binomial")
    row = next(r for r in ws.get(mid).rbd.sections["BLOCK_DATA"].rows
               if r["blockName"] == "Engine_1")
    assert row["FDist"] == "3" and float(row["FParam1"]) == 5.0
    assert float(row["FParam2"]) == pytest.approx(0.4)
    assert _errors(ws.get(mid).rbd) == []


@pytest.mark.parametrize("assignment", [
    "block:Engine_1.fail_dist=binomial",          # old Weibull numbers kept
    "block:Engine_1.fail_params=nan",
    "block:Engine_1.fail_params=-5",
])
def test_path_assignment_invalid_is_refused_or_reported(ws_model, assignment):
    ws, mid = ws_model
    assert _refused_or_reported(
        ws, mid, lambda: paths.apply_assignment(ws, mid, assignment))


@pytest.mark.parametrize("pair", INVALID, ids=_ID)
def test_configure_phases_invalid_length_is_refused_or_reported(ws_model, pair):
    ws, mid = ws_model
    n = len(ws.get(mid).rbd.sections["PHASES"].rows)
    phases = [{"name": f"P{i}", "duration_dist": 19, "params": (1.0, 0.0, 0.0)}
              for i in range(n)]
    phases[0] = {"name": "P0", "duration_dist": pair[0], "params": tuple(pair[1])}
    assert _refused_or_reported(ws, mid, lambda: build.configure_phases(
        ws, mid, phases))


def test_configure_phases_valid_binomial_lands(ws_model):
    ws, mid = ws_model
    n = len(ws.get(mid).rbd.sections["PHASES"].rows)
    phases = [{"name": f"P{i}", "duration_dist": 19, "params": (1.0, 0.0, 0.0)}
              for i in range(n)]
    phases[0] = {"name": "P0", "duration_dist": 3, "params": (5.0, 0.4, 0.0)}
    build.configure_phases(ws, mid, phases)
    rows = ws.get(mid).rbd.sections["PHASES"].rows
    assert rows[0]["Dist"] == "3" and float(rows[0]["Param2"]) == pytest.approx(0.4)
    assert _errors(ws.get(mid).rbd) == []

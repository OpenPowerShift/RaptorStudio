"""The analytical solver can assume every dependency is up or down."""
from __future__ import annotations

import pytest

from raptor.api.analytical import analyse_rbd, scope_options
from raptor.core.analytical.errors import NotApplicable
from raptor.core.fileio.rbd import load_rbd

MODEL = "tests/golden/BlockDependency/input.rbd"


def test_dependency_is_refused_without_a_choice():
    with pytest.raises(NotApplicable, match="dependency"):
        analyse_rbd(load_rbd(MODEL), at=100.0, ignore_repairs=True, importance=False)


@pytest.mark.parametrize("choice", ["up", "down"])
def test_dependency_choice_still_solves(choice):
    result = analyse_rbd(load_rbd(MODEL), at=100.0, ignore=["repairs"], dependencies=choice,
                         importance=False)
    assert result["scope"]["dependencies"] == choice
    assert result["reliability"] is not None
    assert any("dependencies are treated as" in note
               for note in result["assumptions"])


def test_down_is_never_more_reliable_than_up():
    up = analyse_rbd(load_rbd(MODEL), at=100.0, ignore=["repairs"], dependencies="up",
                     importance=False)["reliability"]
    down = analyse_rbd(load_rbd(MODEL), at=100.0, ignore=["repairs"], dependencies="down",
                       importance=False)["reliability"]
    assert down <= up + 1e-12


def test_scope_options_reports_dependencies():
    offered = scope_options(load_rbd(MODEL))
    assert offered["has_dependencies"] is True

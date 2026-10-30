"""A mean over the 999,999,999.999999 limit says which time unit would fit (#19)."""
from __future__ import annotations

import pytest

from raptor.api.workspace import Workspace
from raptor.core.distro_rules import (
    _UNIT_HOURS, larger_unit_hint, param_error, validate_distro_params,
)

EXPONENTIAL = 13


@pytest.mark.parametrize("value, units, unit_named", [
    (4e9, "hours", "days"),        # the reported case: ~4e9 h
    (4e10, "hours", "weeks"),      # days (1.7e9) is still too big
    (4e9, "minutes", "hours"),
    (1e11, "days", "years"),
])
def test_the_smallest_unit_that_fits_is_named(value, units, unit_named):
    assert f"to {unit_named} brings it in range" in larger_unit_hint(value, units)


def test_the_named_unit_really_brings_the_value_in_range():
    value, here = 4e10, "hours"
    named = larger_unit_hint(value, here).split("to ")[1].split(" brings")[0]
    assert value * _UNIT_HOURS[here] / _UNIT_HOURS[named] <= 999999999.999999


def test_nothing_fits_beyond_years():
    assert "No larger time unit" in larger_unit_hint(1e15, "hours")


@pytest.mark.parametrize("units", ["cycles", "", None])
def test_an_unconvertible_unit_still_gets_a_suggestion(units):
    assert "larger time unit" in larger_unit_hint(4e9, units)


def test_the_message_keeps_the_original_wording_first():
    err = param_error(EXPONENTIAL, 0, 4e9, "Block 1 fail", "hours")
    assert err.startswith("Block 1 fail Mean must be less than 999,999,999.999999")
    assert "days" in err


def test_validate_distro_params_passes_the_unit_on():
    assert "days" in validate_distro_params(EXPONENTIAL, [4e9], "x", "hours")


def test_a_value_in_range_has_no_hint():
    assert param_error(EXPONENTIAL, 0, 100.0, "x", "hours") is None


def test_validate_names_the_unit_for_a_model(tmp_path):
    from raptor.api.validate import issues
    ws = Workspace(str(tmp_path))
    mid = ws.create_model("m", template="series", n=1)["model_id"]
    rbd = ws.get(mid).rbd
    rbd.sections["BLOCK_DATA"].rows[1]["FParam1"] = "4000000000.000000"
    rbd.sections["BLOCK_DATA"].rows[1]["FDist"] = str(EXPONENTIAL)
    rbd.sections["BLOCK_DATA"].rows[1]["#FParam"] = "1"
    found = [i for i in issues(rbd) if "999,999,999" in str(i)]
    assert found and "days" in str(found[0])


def test_the_unit_table_matches_the_gui_conversions():
    from raptor.gui.ui.units_conv import HOURS_PER_UNIT
    assert _UNIT_HOURS == HOURS_PER_UNIT

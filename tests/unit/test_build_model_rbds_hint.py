"""A whole .rbds file passed as a build_model spec gets pointed at `rbds_text`
(issue #4, item 8)."""
from __future__ import annotations

import pytest

from raptor.api.build import BuildError, _validate_spec_keys


def test_a_whole_rbds_file_as_the_spec_points_to_rbds_text():
    with pytest.raises(BuildError, match="rbds_text"):
        _validate_spec_keys({"format": "raptor-studio", "version": 1, "elements": {}})


def test_an_ordinary_unknown_key_gets_no_whole_file_hint():
    with pytest.raises(BuildError) as err:
        _validate_spec_keys({"blokcs": []})
    assert "whole .rbds file" not in str(err.value)

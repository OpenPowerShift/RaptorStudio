"""A block's `SpType` string, as real Raptor's loader reads it: Infinite, Pooled and Custom map to
their types and any other string -- "ColdPool" and "Resource" included -- is
None sparing. Those two are pool-object types that a block never carries."""
from __future__ import annotations

import pytest

from raptor.core.fileio.typed import _SPARING_TYPE_BY_NAME, SPARING_TYPE_TO_FILE_STRING
from raptor.core.model import SparingType


@pytest.mark.parametrize("text,expected", [
    ("Infinite", SparingType.INFINITE),
    ("Pooled", SparingType.SPARE_POOL),
    ("Custom", SparingType.CUSTOM),
    ("None", SparingType.NONE),
    ("ColdPool", SparingType.NONE),
    ("Resource", SparingType.NONE),
])
def test_block_sparing_string_maps_like_real(text: str, expected: SparingType) -> None:
    assert _SPARING_TYPE_BY_NAME[text] is expected


def test_none_sparing_saves_as_none() -> None:
    """Three strings load as None, but a block saves back as the plain word."""
    assert SPARING_TYPE_TO_FILE_STRING[SparingType.NONE] == "None"

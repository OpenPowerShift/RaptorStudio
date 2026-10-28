"""How a draw is written in the `/sd` file: six decimals, and the C runtime's `1.#INF00` for infinity."""
from __future__ import annotations

import pytest

from raptor.core.engine.sim_details import format_draw


@pytest.mark.parametrize("value, text", [
    (10.023872, "10.023872"),
    (0.0, "0.000000"),
    (float("inf"), "1.#INF00"),
    (float("-inf"), "-1.#INF00"),
])
def test_format_draw(value, text) -> None:
    assert format_draw(value) == text

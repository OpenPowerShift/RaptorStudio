"""Tests for `tools/sd_diff.py`, the draw-for-draw `/sd` comparison tool.

The tool's own logic is exercised directly rather than through a
subprocess. The final test runs it against the `SpaceStation` fixture under
`tests/golden/` and checks that a genuinely matching capture reports no
divergence.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "tools"))

import sd_diff  # noqa: E402


def test_parse_draw_line_filters_header_noise():
    assert sd_diff._parse_draw_line("Begining Procedure RunEFPA......") is None
    assert sd_diff._parse_draw_line("   Power  through link 21") is None
    assert sd_diff._parse_draw_line("CAP STATS:  CapNodes = 7") is None
    assert sd_diff._parse_draw_line("") is None
    parsed = sd_diff._parse_draw_line("0.000000  Attitude  2966.321034")
    assert parsed == (0.0, "Attitude", "2966.321034")


def test_values_match_uses_the_rederived_noise_floor_envelope():
    # The envelope admits differences inside the absolute/relative bounds
    # and rejects those outside them. It is headroom for platform-libm
    # last-bit variation; see sd_diff.TOL_ABS.
    assert sd_diff._values_match(1.0, 1.0)
    assert sd_diff._values_match(1.0 + 5e-10, 1.0)        # inside the abs floor
    assert not sd_diff._values_match(1.0 + 5e-8, 1.0)     # outside both
    assert sd_diff._values_match(1e6 * (1 + 5e-12), 1e6)  # inside the rel bound
    assert not sd_diff._values_match(1.0, 2.0)


def test_envelope_would_catch_the_base_generator_divisor_bug():
    """The envelope must reject the relative error introduced by
    dividing by `2**31 - 1` instead of `2**31` (~4.66e-10 per draw,
    ~2.3e-7 relative at large scale), so that error cannot pass unnoticed.
    """
    assert not sd_diff._values_match(204298.016563, 204297.969997)
    # The same absolute delta at a smaller magnitude must also be
    # rejected.
    assert not sd_diff._values_match(12239.771210, 12239.724644)


def test_first_divergence_none_when_identical():
    draws = [(0.0, "A", "1.0"), (1.0, "B", "2.0")]
    assert sd_diff.first_divergence(draws, list(draws)) is None


def test_first_divergence_finds_the_first_mismatch():
    real = [(0.0, "A", "1.0"), (1.0, "B", "2.0"), (2.0, "C", "3.0")]
    ours = [(0.0, "A", "1.0"), (1.0, "B", "999.0"), (2.0, "C", "3.0")]
    assert sd_diff.first_divergence(real, ours) == 1


def test_spacestation_shows_no_divergence_across_its_full_real_capture():
    """A fixture known to be `10/10` real-run bit-exact must come back
    clean through the tool, not just the unit-level helpers above."""
    from pathlib import Path

    base = Path(REPO) / "tests" / "golden" / "SpaceStation"
    real_path = base / "legacy" / "simDetails.TXT"
    rbd_path = base / "input.rbd"
    real_draws = sd_diff.load_real_draws(real_path)
    assert len(real_draws) > 50   # sanity: the fixture has real content

    out_path = Path("/tmp") / "sd_diff_test_spacestation.TXT"
    our_draws = sd_diff.run_ours(rbd_path, 10, out_path)
    idx = sd_diff.first_divergence(real_draws, our_draws)
    assert idx is None, (
        f"SpaceStation is a 10/10 bit-exact fixture -- a "
        f"divergence here at draw {idx} means either a real regression "
        f"or a bug in the comparison tool itself, not an expected result"
    )

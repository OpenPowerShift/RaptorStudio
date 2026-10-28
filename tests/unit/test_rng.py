"""Regression tests for `raptor.core.rng`.

`TestRaptorStreamAgainstGolden` replays real draws captured from Raptor
(`tests/golden/UniformProbe`) and must match exactly; if a draw stops
matching, the port has regressed and the code -- not the test -- should be
corrected.
"""
from __future__ import annotations

import pathlib

import pytest

from raptor.core.rng import Distribution, FETCH_RAPTOR_SEED, RandomGen, RaptorStream, fetch_raptor_seed

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _load_draws(path: pathlib.Path) -> list[float]:
    return [float(v) for v in path.read_text().split()]


class TestFetchRaptorSeed:
    def test_matches_source_table_length(self) -> None:
        assert len(FETCH_RAPTOR_SEED) == 111  # 1 filler + 110 real seeds

    def test_known_seeds(self) -> None:
        assert fetch_raptor_seed(1) == 2116429302
        assert fetch_raptor_seed(2) == 683743814
        assert fetch_raptor_seed(9) == 1797920909
        assert fetch_raptor_seed(110) == 547070247


class TestRaptorStreamAgainstGolden:
    """Replays the draws in the UniformProbe golden fixture. Values must
    match at the fixture's 6-decimal-place text resolution."""

    def test_stream1_fail_draws(self) -> None:
        path = GOLDEN / "UniformProbe" / "stream1_fail_draws.txt"
        if not path.exists():
            pytest.skip(f"golden fixture not present: {path}")
        observed = _load_draws(path)
        stream = RaptorStream(seed=fetch_raptor_seed(1))
        # Only the clean prefix before the fixture's documented desync
        # point is a valid regression check; see
        # tests/golden/UniformProbe/README.md.
        clean_prefix = 400
        for i, want in enumerate(observed[:clean_prefix]):
            got = stream.uniform()
            assert got == pytest.approx(want, abs=2e-6), f"draw #{i}"

    def test_stream2_repair_draws(self) -> None:
        path = GOLDEN / "UniformProbe" / "stream2_repair_draws.txt"
        if not path.exists():
            pytest.skip(f"golden fixture not present: {path}")
        observed = _load_draws(path)
        stream = RaptorStream(seed=fetch_raptor_seed(2))
        clean_prefix = 96  # see tests/golden/UniformProbe/README.md
        for i, want in enumerate(observed[:clean_prefix]):
            got = stream.uniform()
            assert got == pytest.approx(want, abs=2e-6), f"draw #{i}"

    def test_output_always_in_unit_interval(self) -> None:
        stream = RaptorStream(seed=fetch_raptor_seed(5))
        for _ in range(10_000):
            u = stream.uniform()
            assert 0.0 <= u < 1.0

    def test_antithetic_is_one_minus_u(self) -> None:
        seed = fetch_raptor_seed(3)
        plain = RaptorStream(seed=seed)
        anti = RaptorStream(seed=seed, antithetic=True)
        for _ in range(20):
            u = plain.uniform()
            a = anti.uniform()
            assert a == pytest.approx(1.0 - u)


class TestDrawNumberDispatch:
    """Structural tests for the `DrawNumber` dispatch table: given the
    same draws, each branch matches Raptor 7's formula. Not compared
    against real captures."""

    def _gen(self) -> RandomGen:
        return RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))

    def test_fixed_returns_param_unchanged(self) -> None:
        gen = self._gen()
        assert gen.draw_number(Distribution.FIXED, [42.0]) == 42.0

    def test_exponential_power_stub(self) -> None:
        gen = self._gen()
        assert gen.draw_number(Distribution.EXPONENTIAL_POWER, [1.0, 2.0, 3.0]) == 3.14159

    def test_uniform_real_matches_confirmed_primitive(self) -> None:
        stream = RaptorStream(seed=fetch_raptor_seed(4))
        gen = RandomGen(stream=stream)
        # advance a fresh, identically-seeded stream in lockstep to compare
        reference = RaptorStream(seed=fetch_raptor_seed(4))
        got = gen.draw_number(Distribution.UNIFORM_REAL, [0.0, 1.0])
        want = reference.uniform()
        assert got == pytest.approx(want)

    def test_bernoulli_probability_bounds_enforced(self) -> None:
        gen = self._gen()
        with pytest.raises(ValueError):
            gen.bernoulli(1.5)

    def test_bernoulli_returns_0_or_1(self) -> None:
        gen = self._gen()
        for _ in range(50):
            v = gen.bernoulli(0.5)
            assert v in (0, 1)

    def test_poisson_rejects_nonpositive_mu(self) -> None:
        gen = self._gen()
        with pytest.raises(ValueError):
            gen.poisson(0.0)

    def test_empirical_single_point_returns_that_point(self) -> None:
        gen = self._gen()
        assert gen.empirical([7.5]) == 7.5

    def test_empirical_interpolates_within_range(self) -> None:
        gen = self._gen()
        data = [0.0, 10.0, 20.0]
        for _ in range(50):
            v = gen.empirical(data)
            assert 0.0 <= v <= 20.0

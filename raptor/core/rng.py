"""Port of Raptor 7's random number generator (`RandomGenObj`).

Raptor's `Normal`, `Gamma`, `Weibull`, `Beta`, `Binomial`, `UniformInt`,
`Exponential` and `UniformReal` are not implemented in Raptor 7: the
Raptor code calls into CACI's shared simulation runtime. The same runtime
ships with SIMSCRIPT II.5, whose manual (Appendix C, "SIMSCRIPT II.5 Random
Deviate Generator") publishes the source of each primitive, and that is what
this module ports. The remaining Raptor-level transforms (`LogNormal`,
`Poisson`, `ChiSquare`, `Bernoulli`, `Pearson5`, `Pearson6`, `Empirical`,
`ExtremeValue`, `Laplace`) are visible in Raptor 7 and are built
compositionally on top of those primitives.

Distribution numbering matches Raptor's own numbering (see
`ConvertToString` in  and the `CASE
currentDistro` block in `DrawNumber`), so raw
`.rbd` FDist/RDist integers map directly onto `Distribution` below without a
translation table. Real captured draws for checking the port live under
`tests/golden/`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol


# --- Base generator ----------------------------------------------------

_A = 630360016
_M = 2147483647  # 2^31 - 1


class RaptorStream:
    """Lehmer/multiplicative-congruential generator.

    x_{n+1} = (A * x_n) mod M ; output = x_{n+1} / 2**31

    Note the asymmetry: the recurrence is mod `_M` = 2**31 - 1, but the
    OUTPUT divides by 2**31. That is not a typo — it is the usual
    power-of-two speed idiom and it is what the Raptor 7 engine does. Dividing
    by `_M` instead would make every draw larger by exactly 1/_M ~ 4.66e-10.

    `SetSeed(s)` sets the internal state directly; the first output applies
    one recurrence step to the seed, so it is not the seed itself.
    """

    __slots__ = ("_antithetic", "_x")

    def __init__(self, seed: int, antithetic: bool = False) -> None:
        self._x = seed % _M
        self._antithetic = antithetic

    def uniform(self) -> float:
        """Raw U(0,1) draw."""
        self._x = (_A * self._x) % _M
        # Divide by 2**31, not `_M` (= 2**31 - 1) -- see the class docstring.
        u = self._x / 2147483648
        return 1.0 - u if self._antithetic else u


class CountingStream(RaptorStream):
    """A stream that counts its draws. Used only by multi-worker batches, to
    check that no chunk used more draws than its segment holds (`ER3-23`); the
    sequential engine keeps the plain stream."""

    __slots__ = ("draws",)

    def __init__(self, seed: int, antithetic: bool = False) -> None:
        super().__init__(seed, antithetic)
        self.draws = 0

    def uniform(self) -> float:
        self.draws += 1
        return super().uniform()


#: 110-entry seed table ported verbatim from `FetchRaptorSeed` in
#: Raptor 7, which is 1-indexed (streams 1..110);
#: index 0 here is unused filler so `FETCH_RAPTOR_SEED[n]` matches the Modsim
#: `SeedArray[n]` call sites without an off-by-one translation.
FETCH_RAPTOR_SEED: list[int] = [
    0,  # unused — keeps 1-based indexing matching the Modsim source
    2116429302, 683743814, 964393174, 1217426631, 618433579,
    1157240309, 15726055, 48108509, 1797920909, 477424540,
    1973272912, 281629770, 20006270, 1280689831, 2096730329,
    1933576050, 913566091, 246780520, 1363774876, 604901985,
    1511192140, 1259851944, 824064364, 150493284, 242708531,
    75253171, 1964472944, 1202299975, 233217322, 1911216000,
    726370533, 403498145, 993232223, 1103205531, 762430696,
    1922803170, 1385516923, 76271663, 413682397, 726466604,
    336157058, 1432650381, 1120463904, 595778810, 877722890,
    1046574445, 68911991, 2088367019, 748545416, 622401386,
    2122378830, 640690903, 1774806513, 2132545692, 2079249579,
    78130110, 852776735, 1187867272, 1351423507, 1645973084,
    1997049139, 922510944, 2045512870, 898585771, 243649545,
    1004818771, 773686062, 403188473, 372279877, 1901633463,
    498067494, 2087759558, 493157915, 597104727, 1530940798,
    1814496276, 536444882, 1663153658, 855503735, 67784357,
    1432404475, 619691088, 119025595, 880802310, 176192644,
    1116780070, 277854671, 1366580350, 1142483975, 2026948561,
    1053920743, 786262391, 1792203830, 1494667770, 1923011392,
    1433700034, 1244184613, 1147297105, 539712780, 1545929719,
    190641742, 1645390429, 264907697, 620389253, 1502074852,
    927711160, 364849192, 2049576050, 638580085, 547070247,
]
assert len(FETCH_RAPTOR_SEED) == 111  # index 0 filler + 110 real seeds


def fetch_raptor_seed(seed_num: int) -> int:
    """`FetchRaptorSeed` port. `seed_num` is 1..110, matching the Modsim source."""
    return FETCH_RAPTOR_SEED[seed_num]


class Distribution(IntEnum):
    """Matches Raptor's own distribution numbering exactly (see module docstring)."""

    BETA = 1
    CHI_SQUARE = 2
    BINOMIAL = 3
    EXPONENTIAL = 4
    ERLANG = 5
    GAMMA = 6
    LOGNORMAL = 7
    NORMAL = 8
    UNIFORM_INTEGER = 9
    UNIFORM_REAL = 10
    PEARSON5 = 11
    PEARSON6 = 12
    POISSON = 13
    TRIANGULAR = 14
    WEIBULL = 15
    EMPIRICAL = 16
    BERNOULLI = 17
    NONE = 18
    FIXED = 19
    EXPONENTIAL_POWER = 20
    EXTREME_VALUE = 21
    LAPLACE = 22


class RandomSource(Protocol):
    """What the engine needs from a random stream: one draw at a time.
    `RandomGen` is the real stream; `sim_details.NamedRandomGen` wraps one
    to record each draw for `/sd`."""

    def draw_number(self, distro: Distribution, params: list[float]) -> float: ...


def _neg_log(product: float) -> float:
    """`-ln(product)`. A product of many uniforms that underflowed to 0.0 gives +infinity, as Raptor 7
   logs `1.#INF00` for an Erlang draw of shape 1000 (`LargeShape1000Probe`)."""
    return -math.log(product) if product > 0.0 else math.inf


@dataclass
class RandomGen:
    """Port of `RandomGenObj`.

    Wraps a `RaptorStream` and implements Raptor's distribution transforms.
    The transforms are exact ports of Raptor 7; the primitives they
    call (`_uniform_real`, `_normal`, `_gamma`, ...) are ported from the
    SIMSCRIPT II.5 manual's Appendix C.
    """

    stream: RaptorStream
    #: How many draws were clamped to 0.000001, for the negative-time
    #: warning. Not part of the draw sequence.
    negative_draws: int = 0

    # --- primitives -----------------------------------------------------
    def _uniform_real(self, lo: float, hi: float) -> float:
        """`UniformReal(lo, hi)`."""
        return lo + (hi - lo) * self.stream.uniform()

    def _normal(self, mean: float, stdev: float) -> float:
        """`Normal(mean, stdev)`.

        Not the textbook Marsaglia polar method it superficially resembles:
        the rejection test pairs `X` in `(0,1)` (not `(-1,1)`) with `Y` in
        `(-1,1)`, and the final scale factor uses a FRESH uniform draw's log
        (`-2*ln(fresh_u)`), not `ln(S)` itself — three draws per loop
        iteration, not two.
        """
        while True:
            x = self.stream.uniform()
            y = 2.0 * self.stream.uniform() - 1.0
            xx = x * x
            yy = y * y
            s = xx + yy
            if s <= 1.0:
                break
        r = math.sqrt(-2.0 * math.log(self.stream.uniform())) / s
        return mean + (xx - yy) * r * stdev

    def _sum_neg_log(self, n: int) -> float:
        """`-ln` of the product of `n` uniforms, accumulated as a sum of logs so it cannot underflow.
        Raptor 7's whole-shape Gamma does exactly this: shape 1000 (a product of 1e-434) still gives a
        finite draw and matches `LargeShape1000Probe` to the last digit, where Erlang's product
        underflows to 0 and logs `1.#INF00`. For a small `n` the two forms agree to display
        precision. The fractional-shape branch shares it, unverified beyond the product's range."""
        total = 0.0
        for _ in range(n):
            total -= math.log(self.stream.uniform())
        return total

    def _gamma(self, mean: float, k: float) -> float:
        """`Gamma(mean, k)`.

        Two cases:

        * whole `k` (`d == 0.0`): draw one extra "burn" uniform, then sum `-ln(u)` over
          `trunc(k)` uniforms (`_sum_neg_log`) and return `(mean / k) * sum`.
        * non-whole `k`: an exact Erlang-style sum of `trunc(k)` uniforms'
          logs for the integer part, combined with an Ahrens-Dieter
          GS-style rejection for the fractional part.

        This is not the generic Marsaglia-Tsang algorithm.
        """
        k_whole = math.trunc(k)
        d = k - k_whole
        if d == 0.0:
            self.stream.uniform()  # burn draw, see docstring
            return (mean / k) * self._sum_neg_log(k_whole)
        z = 0.0
        x = 0.0
        if k_whole != 0:
            z = self._sum_neg_log(k_whole)
        a = 1.0 / d
        b = 1.0 / (1.0 - d)
        y = 2.0
        while y > 1.0:
            x = self.stream.uniform() ** a
            y = self.stream.uniform() ** b + x
        w = x / y
        y = -math.log(self.stream.uniform())
        return (z + w * y) * (mean / k)

    def _erlang(self, mean: float, k: int) -> float:
        """`Erlang(mean, k)`.

        A dedicated routine, not `_gamma`'s integer branch: `Erlang`'s shape
        parameter is always a whole number by definition.
        """
        e = 1.0
        for _ in range(k):
            e *= self.stream.uniform()
        return _neg_log(e) * mean / k

    def _exponential(self, mean: float) -> float:
        """`Exponential(mean)` — `-mean * ln(U)`."""
        u = self.stream.uniform()
        return -mean * math.log(u)

    def _weibull(self, scale: float, shape: float) -> float:
        """`Weibull(scale, shape)` — inverse CDF `scale * (-ln(U))**(1/shape)`.

        `draw_number` passes `(FParam2, FParam1)`, because Raptor's `.rbd`
        column order is `(shape, scale)`.
        """
        u = self.stream.uniform()
        return scale * (-math.log(u)) ** (1.0 / shape)

    def _beta(self, k1: float, k2: float) -> float:
        """`Beta(k1, k2)`.

        Two `_gamma(k, k)` draws (mean = shape), matching `beta.f`'s own
        `gamma.f(K1, K1, STREAM)` call.
        """
        x = self._gamma(k1, k1)
        y = self._gamma(k2, k2)
        return x / (x + y)

    def _uniform_int(self, lo: int, hi: int) -> int:
        """`UniformInt(lo, hi)` — `trunc(U*(hi-lo+1)) + lo`."""
        return lo + int(self.stream.uniform() * (hi - lo + 1))

    def _binomial(self, n: int, p: float) -> int:
        """`Binomial(n, p)`.

        Uses `<=`, matching the source's `when random.f(STREAM) <= P`.
        """
        return sum(1 for _ in range(n) if self.stream.uniform() <= p)

    def _triangular(self, low: float, high: float, mode: float) -> float:
        """Inverse-CDF triangular draw.

        `draw_number` passes `(low, mode, high)`, matching Raptor's
        `(min, mode, max)` `.rbd` column order; this signature is
        `(low, high, mode)`.
        """
        u = self.stream.uniform()
        c = (mode - low) / (high - low)
        if u < c:
            return low + math.sqrt(u * (high - low) * (mode - low))
        return high - math.sqrt((1 - u) * (high - low) * (high - mode))

    # --- Raptor-level transforms, ported from Raptor 7 --------------

    def chi_square(self, shape: int) -> float:
        """`ChiSquare` — sum of `shape` squared standard normals."""
        return sum(self._normal(0.0, 1.0) ** 2 for _ in range(shape))

    def bernoulli(self, probability: float) -> int:
        """`Bernoulli`."""
        if not (0.0 <= probability <= 1.0):
            raise ValueError("Bernoulli probability not between 0 and 1")
        return 1 if self._uniform_real(0.0, 1.0) <= probability else 0

    def pearson5(self, scale: float, shape: float, location: float) -> float:
        """`Pearson5`."""
        if not (scale > 0.0 and shape > 0.0):
            raise ValueError("Shape or scale for Pearson 5 draw <= 0")
        mean = shape / scale
        gamma_draw = self._gamma(mean, shape)
        return location + 1.0 / gamma_draw

    def pearson6(self, scale: float, shape: float, shape2: float) -> float:
        """`Pearson6`."""
        gamma1 = self._gamma(shape, shape)
        gamma2 = self._gamma(shape2, shape2)
        return math.exp(math.log(scale) + math.log(gamma1) - math.log(gamma2))

    def empirical(self, data: list[float]) -> float:
        """`Empirical`.

        `data` is 1-indexed in the source (`data[1]..data[HIGH(data)]`);
        pass a plain 0-indexed Python list and this method does the index
        translation.
        """
        x = self._uniform_real(0.0, 1.0)
        n = float(len(data))
        if n == 1.0:
            return data[0]
        position = (n - 1.0) * x
        i = int(position)  # TRUNC
        # data[i] and data[i+1] in 1-indexed source == data[i-1], data[i] here
        lo = data[i]
        hi = data[i + 1] if i + 1 < len(data) else data[i]
        return lo + (position - float(i)) * (hi - lo)

    def log_normal(self, mean: float, stdev: float) -> float:
        """`LogNormal`.

        Ported from Raptor 7's visible formula, which converts the
        log-normal parameters to normal ones and exponentiates a `_normal`
        draw. Mathematically equivalent to SIMSCRIPT II.5's `log.normal.f`
        (Appendix C), although the two surface forms differ
        (`S=ln(SIGMA²/MU²+1)` there vs `normal_stdev²=ln((var+mean²)/mean²)`
        here).
        """
        log_mean2 = mean ** 2
        log_var = stdev ** 2
        normal_mean = math.log(log_mean2 / math.sqrt(log_var + log_mean2))
        normal_stdev = math.sqrt(math.log((log_var + log_mean2) / log_mean2))
        return math.exp(self._normal(normal_mean, normal_stdev))

    def poisson(self, mu: float) -> int:
        """`Poisson`.

        For `mu > 6` the source uses a normal approximation and redraws
        until the rounded value is non-negative; for `mu <= 6` it counts
        uniform draws until their product drops below `exp(-mu)`.
        """
        if mu <= 0.0:
            raise ValueError("Poisson mu less than or equal to zero")
        if mu > 6.0:
            sigma = math.sqrt(mu)
            while True:
                value = round(self._normal(mu, sigma))
                if value >= 0:
                    return value
        y = math.exp(-mu)
        x = 1.0
        n = 0
        while True:
            x *= self._uniform_real(0.0, 1.0)  # `Sample()` in the source
            if x < y:
                return n
            n += 1

    def extreme_value(self, scale: float, location: float) -> float:
        """`ExtremeValue`."""
        x = self._uniform_real(0.0, 1.0)
        return location - scale * math.log(-math.log(x))

    def laplace(self, scale: float, location: float) -> float:
        """`Laplace`."""
        x = self._uniform_real(0.0, 1.0)
        if x < 0.5:
            return location + scale * math.log(2.0 * x)
        return location - scale * math.log(2.0 - 2.0 * x)

    def draw_number(self, distro: Distribution, params: list[float]) -> float:
        """Port of `DrawNumber`'s dispatch (the `CASE currentDistro` block).

        `params` is 1-indexed in the source (`dataArray[1]`, `dataArray[2]`,
        ...); pass a plain 0-indexed Python list and this method does the
        translation, matching each WHEN-clause's argument order.

        Ports the source's negative-draw clamp (`IF drawn<0.0 THEN
        drawn:=0.000001`), which sits before the `/sd` logging, so
        `sim_details.NamedRandomGen` records the clamped value.
        """
        drawn = self._dispatch_draw(distro, params)
        if drawn < 0.0:
            self.negative_draws += 1
            return 0.000001
        return drawn

    def _dispatch_draw(self, distro: Distribution, params: list[float]) -> float:
        """`DrawNumber`'s `CASE currentDistro` block (see `draw_number` for
        the negative-draw clamp wrapped around this)."""
        p = params  # alias, 0-indexed; p[0] == dataArray[1], etc.
        d = distro
        if d == Distribution.BETA:
            return self._beta(p[0], p[1])
        if d == Distribution.CHI_SQUARE:
            return self.chi_square(round(p[0]))
        if d == Distribution.BINOMIAL:
            return float(self._binomial(round(p[0]), p[1]))
        if d == Distribution.EXPONENTIAL:
            return p[1] + self._exponential(p[0])
        if d == Distribution.ERLANG:
            return self._erlang(p[0] * p[1], round(p[0]))
        if d == Distribution.GAMMA:
            return p[2] + self._gamma(p[0] * p[1], p[0])
        if d == Distribution.LOGNORMAL:
            return self.log_normal(p[0], p[1])
        if d == Distribution.NORMAL:
            return self._normal(p[0], p[1])
        if d == Distribution.UNIFORM_INTEGER:
            return float(self._uniform_int(round(p[0]), round(p[1])))
        if d == Distribution.UNIFORM_REAL:
            return self._uniform_real(p[0], p[1])
        if d == Distribution.PEARSON5:
            return self.pearson5(p[1], p[0], p[2])
        if d == Distribution.PEARSON6:
            return self.pearson6(p[2], p[0], p[1])
        if d == Distribution.POISSON:
            return float(self.poisson(p[0]))
        if d == Distribution.TRIANGULAR:
            # FParam1/2/3 = (min, mode, max) in Raptor's `.rbd` column
            # order; `_triangular`'s signature is (low, high, mode).
            return self._triangular(p[0], p[2], p[1])
        if d == Distribution.WEIBULL:
            # FParam1/FParam2 = (shape, scale) in Raptor's `.rbd` column
            # order; `_weibull`'s signature is (scale, shape).
            return p[2] + self._weibull(p[1], p[0])
        if d == Distribution.EMPIRICAL:
            return self.empirical(p)
        if d == Distribution.BERNOULLI:
            return float(self.bernoulli(p[0]))
        if d == Distribution.FIXED:
            return p[0]
        if d == Distribution.EXPONENTIAL_POWER:
            return 3.14159  # verbatim stub, matches the source exactly
        if d == Distribution.EXTREME_VALUE:
            return self.extreme_value(p[0], p[1])
        if d == Distribution.LAPLACE:
            return self.laplace(p[0], p[1])
        raise ValueError(f"Unknown distribution: {distro!r}")


def jump_ahead(stream: RaptorStream, steps: int) -> None:
    """Advance a Lehmer stream `steps` draws WITHOUT iterating:
    x_{n+k} = A^k * x_n (mod M) via fast modular exponentiation.

    Used by multi-worker batch mode to give each worker process a DISJOINT
    segment of every random stream: non-overlapping segments of the periodic
    sequence act as independent substreams, so parallel replications never
    reuse each other's draws. Not used in sequential mode, which requires
    draw-for-draw compatibility with the Raptor 7 engine.
    """
    if steps > 0:
        stream._x = (pow(_A, steps, _M) * stream._x) % _M

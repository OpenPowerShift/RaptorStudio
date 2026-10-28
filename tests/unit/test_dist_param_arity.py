"""The canvas writes the canonical distribution arity (review D-16).

`gui/ui/diagram.py::add_block` hard-coded the declared parameter count as
``"2" if fdist == 4 else row.get("#FParam", "2")`` -- two parameters for
every distribution except Exponential. The canonical table
(`core/fileio/rbdlib.py::DIST_NPARAMS`, sourced from the legacy writer)
says otherwise:

    Gamma 6, Pearson5 11, Pearson6 12, Triangular 14, Weibull 15 -> 3

So placing a Weibull from the canvas wrote ``#FParam = 2`` into a file the
*real* Raptor engine reads out of bounds -- a location slot it looks for and
does not find. That is precisely the failure the verification ladder already
records, and it is a data-corruption bug, not a style issue.

`dist_param_count` is now the one owner: `api/build.py` and
`gui/ui/diagram.py` both go through it.

These tests pin the rule at both levels -- the table itself (pure) and the
canvas path that used to diverge from it.
"""
from __future__ import annotations

import pytest

from raptor.core.fileio.rbdlib import (
    D_BERNOULLI,
    D_BETA,
    D_CHI2,
    D_EMPIRICAL,
    D_ERLANG,
    D_ERROR,
    D_EXPO,
    D_EXTREME,
    D_FIXED,
    D_GAMMA,
    D_LAPLACE,
    D_LOGNORM,
    D_NONE,
    D_NORM,
    D_P5,
    D_P6,
    D_POISSON,
    D_TRIANG,
    D_UINT,
    D_UREAL,
    D_WEIBULL,
    DIST_NPARAMS,
    DIST_NPARAMS_DISK,
    dist_param_count,
)


@pytest.fixture(scope="module")
def qapp():
    """`DiagramView` is a QGraphicsView; a QApplication must exist first."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class TestDistParamCount:
    def test_exponential_takes_two(self):
        assert dist_param_count(D_EXPO) == 2

    @pytest.mark.parametrize("dist", [
        pytest.param(D_GAMMA, id="gamma"),
        pytest.param(D_P5, id="pearson5"),
        pytest.param(D_P6, id="pearson6"),
        pytest.param(D_TRIANG, id="triangular"),
        pytest.param(D_WEIBULL, id="weibull"),
    ])
    def test_the_three_parameter_families(self, dist):
        """THE BUG. Every one of these wrote `#FParam = 2` from the canvas."""
        assert dist_param_count(dist) == 3, (
            f"distribution {dist} declares {DIST_NPARAMS.get(dist)} params; "
            "writing fewer means the engine reads a slot that is not there")

    @pytest.mark.parametrize("dist", [
        pytest.param(D_BETA, id="beta"),
        pytest.param(D_LOGNORM, id="lognorm"),
        pytest.param(D_NORM, id="norm"),
        pytest.param(D_UINT, id="uniform_int"),
        pytest.param(D_UREAL, id="uniform_real"),
        pytest.param(D_ERLANG, id="erlang"),
        pytest.param(D_EXTREME, id="extreme_value"),
        pytest.param(D_LAPLACE, id="laplace"),
    ])
    def test_the_two_parameter_families(self, dist):
        assert dist_param_count(dist) == 2

    def test_the_one_parameter_families(self):
        assert dist_param_count(D_POISSON) == 1
        assert dist_param_count(D_BERNOULLI) == 1
        assert dist_param_count(D_FIXED) == 1

    def test_the_disk_override_wins_over_the_logical_arity(self):
        """Distribution 18 (None) declares 0 logical params but is written
        as `#RParam = 1` in real fixtures -- the override table exists for
        exactly this, and the canvas must honour it rather than write 0."""
        assert DIST_NPARAMS[D_NONE] == 0
        assert DIST_NPARAMS_DISK[D_NONE] == 1
        assert dist_param_count(D_NONE) == 1

    def test_empirical_is_data_point_driven_not_a_fixed_count(self):
        """Empirical's `#params` is the number of data points, so the
        static table must not claim a fixed arity for it."""
        assert D_EMPIRICAL not in DIST_NPARAMS
        assert D_EMPIRICAL not in DIST_NPARAMS_DISK

    def test_an_unknown_code_defaults_to_the_widest_shape(self):
        """Not 2, and not 0: 3 is the widest arity the on-disk row has room
        for, so an unrecognised code writes a count the row can hold
        instead of one that truncates a parameter."""
        assert dist_param_count(999) == 3
        assert dist_param_count(None) == 3

    def test_it_agrees_with_the_table_it_wraps(self):
        """The helper must not drift from DIST_NPARAMS: for every code the
        table knows, the two must be the same number."""
        for dist in DIST_NPARAMS:
            expected = DIST_NPARAMS_DISK.get(dist, DIST_NPARAMS[dist])
            assert dist_param_count(dist) == expected, dist


class TestTheCanvasUsesTheTable:
    """The GUI path. `add_block` is what a user reaches when placing a
    block, so this is where a wrong arity becomes a corrupt file."""

    @pytest.fixture()
    def view(self, qapp):
        from raptor.core.fileio.rbd import load_rbd
        from raptor.gui.ui.diagram import DiagramView
        v = DiagramView()
        v.load_rbd(load_rbd("tests/golden/TwoBlockRedundancy/input.rbd"))
        return v

    def _place(self, view, fdist, name="Probe"):
        bid = view.add_block(name, 0.0, 0.0)
        rbd = view._rbd_ref
        return next(r for r in rbd.sections["BLOCK_DATA"].rows
                    if r.get("ID") == str(bid))

    def test_a_weibull_block_declares_three_params(self, view):
        """The headline case: dist 15 from the canvas."""
        view.default_block_fdist = D_WEIBULL
        row = self._place(view, D_WEIBULL, "WeibullPump")
        assert row["FDist"] == str(D_WEIBULL)
        assert row["#FParam"] == "3", (
            f"a Weibull placed on the canvas declares {row['#FParam']} "
            "params; the real engine reads three")

    def test_a_gamma_block_declares_three_params(self, view):
        view.default_block_fdist = D_GAMMA
        row = self._place(view, D_GAMMA, "GammaPump")
        assert row["#FParam"] == "3"

    def test_an_exponential_block_still_declares_two(self, view):
        """The boundary: the old special case (`fdist == 4` -> 2) must
        survive as a consequence of the table, not as a branch."""
        view.default_block_fdist = D_EXPO
        row = self._place(view, D_EXPO, "ExpoPump")
        assert row["#FParam"] == "2"

    def test_the_repair_count_is_derived_the_same_way(self, view):
        """`#RParam` came from the same hard-coded expression, so it had
        the same bug; it must now come from the same table."""
        view.default_block_rdist = D_WEIBULL
        row = self._place(view, D_EXPO, "RepairWeibull")
        assert row["RDist"] == str(D_WEIBULL)
        assert row["#RParam"] == "3"

    def test_the_count_matches_the_table_for_every_distribution(
            self, view):
        """The general statement of the fix: for every distro the canvas
        can be given, the declared count is the table's number."""
        for dist in (D_BETA, D_CHI2, D_ERLANG, D_EXPO, D_GAMMA, D_LOGNORM,
                     D_NORM, D_P5, D_P6, D_POISSON, D_TRIANG, D_UINT,
                     D_UREAL, D_WEIBULL, D_BERNOULLI, D_FIXED, D_ERROR,
                     D_EXTREME, D_LAPLACE):
            view.default_block_fdist = dist
            row = self._place(view, dist, f"Probe{dist}")
            assert int(row["#FParam"]) == dist_param_count(dist), (
                f"canvas wrote #{row['#FParam']} for distribution {dist}, "
                f"table says {dist_param_count(dist)}")

    def test_the_declared_count_fits_the_param_slots(self, view):
        """A structural cross-check on the row itself: the declared count
        must never exceed the Param1..3 slots the format provides. This is
        the shape of the read-out-of-bounds failure."""
        for dist in (D_GAMMA, D_P5, D_P6, D_TRIANG, D_WEIBULL, D_EXPO):
            view.default_block_fdist = dist
            row = self._place(view, dist, f"Slots{dist}")
            n = int(row["#FParam"])
            assert n <= 3, f"{dist} declares {n} > the 3 available slots"

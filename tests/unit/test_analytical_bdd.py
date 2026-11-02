"""BDD algebra: truth tables, k-of-n, bridges and size limits."""
from __future__ import annotations

import itertools

import pytest

from raptor.core.analytical.bdd import BDD, FALSE, TRUE
from raptor.core.analytical.errors import BddTooLarge


def _value(bdd: BDD, root: int, bits: list[bool]) -> bool:
    node = root
    while node not in (FALSE, TRUE):
        var, low, high = bdd.nodes[node]
        node = high if bits[var] else low
    return node == TRUE


def test_and_or_not_truth_tables():
    bdd = BDD()
    x0 = bdd.variable(0)
    x1 = bdd.variable(1)
    assert bdd.And(x0, x1) == bdd.And(x1, x0)
    for bits in itertools.product((False, True), repeat=2):
        assert _value(bdd, bdd.And(x0, x1), list(bits)) == (bits[0] and bits[1])
        assert _value(bdd, bdd.Or(x0, x1), list(bits)) == (bits[0] or bits[1])
        assert _value(bdd, bdd.neg(x0), list(bits)) == (not bits[0])


def test_threshold_matches_binomial():
    bdd = BDD()
    xs = [bdd.variable(i) for i in range(6)]
    for k in range(0, 7):
        root = bdd.threshold(xs, k)
        probability = bdd.probability(root, [0.5] * 6)
        expected = sum(
            _comb(6, j) for j in range(k, 7)) / 64.0
        assert probability == pytest.approx(expected)


def test_threshold_shared_branches_is_still_exact():
    bdd = BDD()
    a = bdd.variable(0)
    b = bdd.variable(1)
    c = bdd.variable(2)
    # Three branches, two of which share a; the threshold must not assume
    # the branch events are independent.
    root = bdd.threshold([a, bdd.And(a, b), c], 2)
    for bits in itertools.product((False, True), repeat=3):
        branches = [bits[0], bits[0] and bits[1], bits[2]]
        assert _value(bdd, root, list(bits)) == (sum(branches) >= 2)


def test_bridge_network_matches_bruteforce():
    bdd = BDD()
    a, c, d, e, f = (bdd.variable(i) for i in range(5))
    # Classic five-element bridge: (a&c) | (d&e) | (a&f&e) | (d&f&c)
    root = bdd.Or(bdd.And(a, c), bdd.And(d, e),
                  bdd.And(a, f, e), bdd.And(d, f, c))
    probabilities = [0.9, 0.8, 0.7, 0.6, 0.5]
    total = 0.0
    for bits in itertools.product((False, True), repeat=5):
        works = ((bits[0] and bits[1]) or (bits[2] and bits[3])
                 or (bits[0] and bits[4] and bits[3])
                 or (bits[2] and bits[4] and bits[1]))
        weight = 1.0
        for bit, probability in zip(bits, probabilities):
            weight *= probability if bit else 1.0 - probability
        total += weight if works else 0.0
    assert bdd.probability(root, probabilities) == pytest.approx(total)


def test_restrict():
    bdd = BDD()
    x0 = bdd.variable(0)
    x1 = bdd.variable(1)
    root = bdd.And(x0, x1)
    assert bdd.restrict(root, 0, False) == FALSE
    assert bdd.restrict(root, 0, True) == x1
    assert bdd.restrict(root, 1, False) == FALSE
    assert bdd.restrict(root, 1, True) == x0


def test_paths_to_true():
    bdd = BDD()
    x0 = bdd.variable(0)
    x1 = bdd.variable(1)
    root = bdd.Or(x0, x1)
    paths = bdd.paths_to_true(root)
    assert paths is not None
    assert len(paths) == 2
    assert {tuple(path) for path in paths} == {
        ((0, True),), ((0, False), (1, True))}


def test_size_cap_is_a_refusal():
    bdd = BDD(max_nodes=3)
    bdd.variable(0)
    with pytest.raises(BddTooLarge):
        bdd.variable(1)


def _comb(n: int, k: int) -> int:
    from math import comb
    return comb(n, k)

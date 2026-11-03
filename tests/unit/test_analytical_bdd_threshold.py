"""`BDD.threshold` against brute force, with branches sharing variables."""
from __future__ import annotations

import itertools
import random

from raptor.core.analytical.bdd import BDD


def _eval(bdd: BDD, node: int, values: dict[int, bool]) -> bool:
    while node > 1:
        var, low, high = bdd.nodes[node]
        node = high if values[var] else low
    return node == 1


def test_threshold_matches_brute_force_with_shared_variables():
    rng = random.Random(7)
    nvars = 7
    for _ in range(60):
        bdd = BDD()
        vs = [bdd.variable(i) for i in range(nvars)]
        branches = []
        for _b in range(rng.randint(1, 6)):
            picks = rng.sample(range(nvars), rng.randint(1, 3))
            op = bdd.And if rng.random() < 0.5 else bdd.Or
            branches.append(op(*(vs[i] for i in picks)))
        k = rng.randint(0, len(branches) + 1)
        root = bdd.threshold(branches, k)
        for bits in itertools.product((False, True), repeat=nvars):
            values = dict(enumerate(bits))
            want = sum(_eval(bdd, b, values) for b in branches) >= k
            assert _eval(bdd, root, values) == want

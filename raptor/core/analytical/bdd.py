"""A small reduced ordered binary decision diagram (ROBDD).

The analytical solver uses a BDD instead of path or event-space enumeration:
the size follows the structure of the Boolean function, not the number of
minimal paths, and the same diagram supplies system probability, exact
importance and the equation.

The implementation is deliberately dependency-free. Nodes are integer
indices into ``nodes``; 0 and 1 are the false and true terminals. A
non-terminal node is ``(variable, low, high)``, with ``variable`` strictly
smaller than any variable below it. The unique table keeps the diagram
reduced, so equal functions share one node.

Limits
------
`max_nodes` is a hard cap. A k-out-of-n node with many shared branches can
make the diagram exponential in the worst case; the solver raises
`BddTooLarge` rather than returning an approximation.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

import time

import numpy as np

from .errors import BddTooLarge
from .limits import DEFAULT_BDD_NODES, max_bdd_nodes_allowed, memory_text

FALSE = 0
TRUE = 1

#: A variable never has this value; used only for ordering comparisons.
_NO_VAR = 1 << 60

#: The default limit (see `limits`). A 122-block real model is ~12k nodes;
#: 2M takes a few seconds and well under 1 GB. The user may raise it up to
#: `limits.max_bdd_nodes_allowed()`, a ceiling from the machine's free memory.
MAX_BDD_NODES = DEFAULT_BDD_NODES

#: Below this many reachable nodes a pass is a plain Python loop: numpy's
#: per-call overhead would cost more than it saves.
_VECTOR_FROM = 2_000
#: Memory a batched pass may use for its node-by-case table; larger batches
#: are done in column chunks.
_BATCH_BYTES = 128 * 1024 * 1024

#: How often the optional progress callback hears about new BDD nodes.
_PROGRESS_EVERY = 8_192


@contextmanager
def recursion_headroom(limit: int = 20_000) -> Iterator[None]:
    """Raise Python's recursion limit while a diagram is built or walked.

    Building and evaluating a BDD recurses once per variable level, and the
    network walk once per block in a chain; a 5,000-block series chain needs
    more than the default 1000 frames. The variable cap keeps the real depth
    far below ``limit``.
    """
    previous = sys.getrecursionlimit()
    sys.setrecursionlimit(max(previous, limit))
    try:
        yield
    finally:
        sys.setrecursionlimit(previous)


class BDD:
    """Reduced ordered BDD over integer variables ``0..n-1``."""

    def __init__(self, max_nodes: int = MAX_BDD_NODES,
                 progress: Callable[[int], None] | None = None) -> None:
        if max_nodes < 2:
            raise ValueError("max_nodes must be at least 2")
        allowed = max_bdd_nodes_allowed()
        if max_nodes > allowed:
            raise ValueError(
                f"a BDD limit of {max_nodes:,} nodes would need "
                f"{memory_text(max_nodes)} at most; this machine allows up "
                f"to {allowed:,} ({memory_text(allowed)}), half of the "
                "memory that is free now")
        self.max_nodes = max_nodes
        self._allowed = allowed
        #: Called before every pass over the diagram (results, curve,
        #: importance) with the number of evaluations it carries (a batched
        #: pass carries many). The caller reports progress from it, and may
        #: raise from it to cancel.
        self.on_pass: Callable[[int], None] | None = None
        self._started = time.monotonic()
        self._progress = progress
        #: index 0 = FALSE, 1 = TRUE, then (variable, low, high).
        self.nodes: list[tuple[int, int, int] | None] = [None, None]
        self._unique: dict[tuple[int, int, int], int] = {}
        self._var_of: dict[int, int] = {}
        self._not_memo: dict[int, int] = {FALSE: TRUE, TRUE: FALSE}
        self._apply_memo: dict[tuple[str, int, int], int] = {}
        self._order_cache: dict[int, list[int]] = {}
        self._plan_cache: dict[int, _Plan] = {}

    def _too_large(self) -> BddTooLarge:
        """The refusal, saying what was reached and what can be done."""
        seconds = time.monotonic() - self._started
        text = (f"the solver's diagram reached the limit of "
                f"{self.max_nodes:,} nodes ({memory_text(self.max_nodes)}) "
                f"after {seconds:.1f} s without finishing.")
        if self.max_nodes < self._allowed:
            text += (f" This machine allows a limit of up to "
                     f"{self._allowed:,} nodes ({memory_text(self._allowed)})"
                     "; a larger diagram is also slower to use (importance "
                     "measures and the curve walk it many times).")
        else:
            text += " That is the largest limit this machine allows."
        text += (" Solving one hierarchy at a time is usually far quicker "
                 "than raising the limit.")
        return BddTooLarge(text, limit=self.max_nodes, seconds=seconds,
                           allowed=self._allowed)

    # -- construction -----------------------------------------------------

    @property
    def node_count(self) -> int:
        """Number of live nodes, including the two terminals."""
        return len(self.nodes)

    def var_of(self, node: int) -> int:
        """The variable at ``node``; terminals sort last."""
        return self._var_of.get(node, _NO_VAR)

    def _mk(self, var: int, low: int, high: int) -> int:
        if low == high:
            return low
        key = (var, low, high)
        got = self._unique.get(key)
        if got is not None:
            return got
        if len(self.nodes) >= self.max_nodes:
            raise self._too_large()
        node = len(self.nodes)
        self.nodes.append(key)
        self._unique[key] = node
        self._var_of[node] = var
        if self._progress is not None and node % _PROGRESS_EVERY == 0:
            self._progress(node)
        return node

    def variable(self, var: int) -> int:
        """The BDD for one variable."""
        if var < 0:
            raise ValueError("variable index must be non-negative")
        return self._mk(var, FALSE, TRUE)

    # -- Boolean operations ----------------------------------------------

    def neg(self, node: int) -> int:
        """Logical NOT."""
        got = self._not_memo.get(node)
        if got is not None:
            return got
        _, low, high = self.nodes[node]  # type: ignore[misc]
        out = self._mk(self._var_of[node], self.neg(low), self.neg(high))
        self._not_memo[node] = out
        return out

    def apply(self, op: str, left: int, right: int) -> int:  # noqa: C901, PLR0912
        """Apply ``"and"`` or ``"or"`` to two BDDs."""
        if op == "and":
            if left == FALSE or right == FALSE:
                return FALSE
            if left == TRUE:
                return right
            if right == TRUE or left == right:
                return left
        elif op == "or":
            if left == TRUE or right == TRUE:
                return TRUE
            if left == FALSE:
                return right
            if right == FALSE or left == right:
                return left
        else:
            raise ValueError(f"unknown BDD operation {op!r}")

        # Commutative operations share one memo key.
        if left > right:
            left, right = right, left
        key = (op, left, right)
        got = self._apply_memo.get(key)
        if got is not None:
            return got

        top = min(self.var_of(left), self.var_of(right))
        if left in (FALSE, TRUE) or self._var_of[left] != top:
            left_low = left_high = left
        else:
            _, left_low, left_high = self.nodes[left]  # type: ignore[misc]
        if right in (FALSE, TRUE) or self._var_of[right] != top:
            right_low = right_high = right
        else:
            _, right_low, right_high = self.nodes[right]  # type: ignore[misc]

        low = self.apply(op, left_low, right_low)
        high = self.apply(op, left_high, right_high)
        out = self._mk(top, low, high)
        self._apply_memo[key] = out
        return out

    def And(self, *nodes: int) -> int:
        """Conjunction of zero or more BDDs."""
        out = TRUE
        for node in nodes:
            out = self.apply("and", out, node)
        return out

    def Or(self, *nodes: int) -> int:
        """Disjunction of zero or more BDDs."""
        out = FALSE
        for node in nodes:
            out = self.apply("or", out, node)
        return out

    def threshold(self, branches: Sequence[int], k: int) -> int:  # noqa: C901, PLR0912
        """At least ``k`` of ``branches`` are true.

        Exact even when branches share variables. The DP tracks how many
        branches are true so far:

            G'_j = (G_j AND NOT b) OR (G_{j-1} AND b)

        with ``G_j`` the BDD for "exactly j branches true". ``TRUE`` branches
        simply lower ``k``; ``FALSE`` branches are dropped.
        """
        if k <= 0:
            return TRUE
        live: list[int] = []
        for branch in branches:
            if branch == TRUE:
                k -= 1
            elif branch != FALSE:
                live.append(branch)
            if k <= 0:
                return TRUE
        n = len(live)
        if k > n:
            return FALSE
        if n == 0:
            return TRUE if k <= 0 else FALSE
        if k == 1:
            return self.Or(*live)
        if k == n:
            return self.And(*live)

        # G[j] = exactly j true so far, for j < k; G[k] = at least k true
        # (absorbing). A count that can no longer reach k with the branches
        # still to come is dropped. Both keep the result exact; together
        # they keep at most n-k+1 live counts instead of n+1, which matters
        # for "k of n" with k close to n (a 10-of-11 node over large
        # branches built 1.7M nodes the old way).
        exact = [TRUE] + [FALSE] * k
        for done, branch in enumerate(live, 1):
            remaining = n - done
            neg = self.neg(branch)
            nxt = [FALSE] * (k + 1)
            for j in range(k + 1):
                if exact[j] == FALSE:
                    continue
                if j == k:
                    nxt[k] = self.Or(nxt[k], exact[k])
                    continue
                if j + remaining >= k:
                    nxt[j] = self.Or(nxt[j], self.And(exact[j], neg))
                if j + 1 + remaining >= k:
                    nxt[j + 1] = self.Or(nxt[j + 1],
                                         self.And(exact[j], branch))
            exact = nxt
        return exact[k]

    def restrict(self, node: int, var: int, value: bool) -> int:
        """Substitute one variable and return the reduced result."""
        memo: dict[int, int] = {}

        def visit(current: int) -> int:
            if current in (FALSE, TRUE):
                return current
            got = memo.get(current)
            if got is not None:
                return got
            node_var, low, high = self.nodes[current]  # type: ignore[misc]
            if node_var == var:
                out = visit(high if value else low)
            elif node_var > var:
                # Variables are ordered; this variable is not below here.
                out = current
            else:
                out = self._mk(node_var, visit(low), visit(high))
            memo[current] = out
            return out

        return visit(node)

    # -- probability ------------------------------------------------------

    def _reachable(self, root: int) -> list[int]:
        """Non-terminal nodes under ``root``, children before parents.

        A node is only ever made after its children (`_mk`), so increasing
        index is a valid bottom-up order. Cached per root: the diagram never
        changes once built, and the evaluation walks it hundreds of times.
        """
        got = self._order_cache.get(root)
        if got is not None:
            return got
        seen: set[int] = set()
        stack = [root]
        while stack:
            node = stack.pop()
            if node <= TRUE or node in seen:
                continue
            seen.add(node)
            _var, low, high = self.nodes[node]  # type: ignore[misc]
            stack.append(low)
            stack.append(high)
        order = sorted(seen)
        self._order_cache[root] = order
        return order

    def probability(self, root: int, probabilities: Sequence[float]) -> float:
        """Probability that ``root`` is true for independent variables.

        ``probabilities[i]`` is P(variable i = true). The multilinearity of
        the BDD is the whole point: this is exact for independent components.

        One bottom-up loop, not a recursive walk: the recursive version kept
        each pass's table alive through a reference cycle until the garbage
        collector ran, so MTTF's hundreds of passes held hundreds of tables
        (550 MB for a 77,000-node diagram).
        """
        if self.on_pass is not None:
            self.on_pass(1)
        if root <= TRUE:
            return float(root)
        for p in probabilities:
            if p < 0.0 or p > 1.0:
                raise ValueError(f"probability {p!r} is not in [0, 1]")
        if len(self._reachable(root)) >= _VECTOR_FROM:
            column = np.asarray(probabilities, dtype=float)[:, None]
            return float(self._plan(root).evaluate(column)[0])
        value: dict[int, float] = {FALSE: 0.0, TRUE: 1.0}
        nodes = self.nodes
        for node in self._reachable(root):
            var, low, high = nodes[node]  # type: ignore[misc]
            p = probabilities[var]
            value[node] = p * value[high] + (1.0 - p) * value[low]
        return value[root]

    def probability_and_derivative(
        self,
        root: int,
        probabilities: Sequence[float],
        derivatives: Sequence[float],
    ) -> tuple[float, float]:
        """Return ``(P(root), dP/dt)`` for time-dependent variable values.

        ``derivatives[i]`` is dp_i/dt. The chain rule on each BDD node is

            P  = p*P_high + (1-p)*P_low
            dP = p'*(P_high - P_low) + p*dP_high + (1-p)*dP_low

        so the system pdf is ``-dP`` when ``p_i`` are reliabilities.
        """
        if self.on_pass is not None:
            self.on_pass(1)
        if root <= TRUE:
            return float(root), 0.0
        for p in probabilities:
            if p < 0.0 or p > 1.0:
                raise ValueError(f"probability {p!r} is not in [0, 1]")
        value: dict[int, float] = {FALSE: 0.0, TRUE: 1.0}
        slope: dict[int, float] = {FALSE: 0.0, TRUE: 0.0}
        nodes = self.nodes
        for node in self._reachable(root):
            var, low, high = nodes[node]  # type: ignore[misc]
            p = probabilities[var]
            dp = derivatives[var]
            high_p, low_p = value[high], value[low]
            value[node] = p * high_p + (1.0 - p) * low_p
            slope[node] = (dp * (high_p - low_p) + p * slope[high]
                           + (1.0 - p) * slope[low])
        return value[root], slope[root]

    def probability_batch(self, root: int, probabilities) -> np.ndarray:
        """``P(root)`` for many cases in one walk.

        ``probabilities`` is a ``(variables, cases)`` array: column ``j`` is
        one case's P(variable i = true). Each node is worked out for every
        case at once, and all nodes that test the same variable together,
        so the Python loop runs once per variable rather than once per node
        per case. The arithmetic is the same expression as `probability`,
        so the results are the same to the last bit. Done in column chunks
        that keep the node-by-case table under ``_BATCH_BYTES``.
        """
        table = np.asarray(probabilities, dtype=float)
        if table.ndim != 2:
            raise ValueError("probabilities must be a (variables, cases) array")
        cases = table.shape[1]
        if root <= TRUE:
            if self.on_pass is not None:
                self.on_pass(cases)
            return np.full(cases, float(root))
        if cases and (np.any(table < 0.0) or np.any(table > 1.0)):
            bad = table[(table < 0.0) | (table > 1.0)][0]
            raise ValueError(f"probability {bad!r} is not in [0, 1]")
        plan = self._plan(root)
        chunk = max(1, _BATCH_BYTES // (8 * (plan.size + 2)))
        out = np.empty(cases)
        for begin in range(0, cases, chunk):
            stop = min(cases, begin + chunk)
            out[begin:stop] = plan.evaluate(table[:, begin:stop])
            # Count each chunk once it is done, not the whole batch up
            # front: importance on a large diagram is one batch of
            # thousands of cases taking many seconds, and counting it
            # before the work left the progress at "the last pass" for all
            # of that time.
            if self.on_pass is not None:
                self.on_pass(stop - begin)
        return out

    def _plan(self, root: int) -> _Plan:
        got = self._plan_cache.get(root)
        if got is None:
            got = _Plan(self, root, self._reachable(root))
            self._plan_cache[root] = got
        return got

    # -- support, paths and equations -------------------------------------

    def support(self, root: int) -> set[int]:
        """Variables that actually occur below ``root``."""
        seen: set[int] = set()
        out: set[int] = set()
        stack = [root]
        while stack:
            node = stack.pop()
            if node in (FALSE, TRUE) or node in seen:
                continue
            seen.add(node)
            var, low, high = self.nodes[node]  # type: ignore[misc]
            out.add(var)
            stack.extend((low, high))
        return out

    def paths_to_true(self, root: int, *, max_paths: int = 1000
                      ) -> list[list[tuple[int, bool]]] | None:
        """All root-to-TRUE paths as ``(variable, is_high)`` literals.

        Returns ``None`` when the path count exceeds ``max_paths``. The BDD is
        ordered, so each path contains a variable at most once; the
        disjunction of the path products is an exact sum-of-products form.
        """
        if root == FALSE:
            return []
        if root == TRUE:
            return [[]]
        out: list[list[tuple[int, bool]]] = []

        def visit(node: int, path: list[tuple[int, bool]]) -> bool:
            if node == FALSE:
                return True
            if node == TRUE:
                out.append(list(path))
                return len(out) <= max_paths
            var, low, high = self.nodes[node]  # type: ignore[misc]
            path.append((var, True))
            if not visit(high, path):
                return False
            path.pop()
            path.append((var, False))
            if not visit(low, path):
                return False
            path.pop()
            return True

        if not visit(root, []):
            return None
        return out


class _Plan:
    """A diagram laid out for vectorised evaluation.

    Rows 0 and 1 of the value table are the FALSE and TRUE terminals; then
    every reachable node, grouped by variable, deepest variable first. A
    node's children always test a later variable (or are terminals), so
    when a group is worked out its children's rows are already filled.
    """

    __slots__ = ("groups", "root_row", "size")

    def __init__(self, bdd: BDD, root: int, reachable: list[int]) -> None:
        nodes = bdd.nodes
        by_var: dict[int, list[int]] = {}
        for node in reachable:
            by_var.setdefault(nodes[node][0], []).append(node)  # type: ignore[index]
        row = {FALSE: 0, TRUE: 1}
        ordered: list[tuple[int, list[int]]] = sorted(by_var.items(),
                                                      reverse=True)
        next_row = 2
        for _var, members in ordered:
            for node in members:
                row[node] = next_row
                next_row += 1
        self.groups: list[tuple[int, int, int, np.ndarray, np.ndarray]] = []
        start = 2
        for var, members in ordered:
            low = np.fromiter((row[nodes[n][1]] for n in members),  # type: ignore[index]
                              dtype=np.intp, count=len(members))
            high = np.fromiter((row[nodes[n][2]] for n in members),  # type: ignore[index]
                               dtype=np.intp, count=len(members))
            self.groups.append((var, start, start + len(members), low, high))
            start += len(members)
        self.root_row = row[root]
        self.size = len(reachable)

    def evaluate(self, table: np.ndarray) -> np.ndarray:
        """Root value for each column of ``table`` (variables x cases)."""
        cases = table.shape[1]
        values = np.empty((self.size + 2, cases))
        values[0] = 0.0
        values[1] = 1.0
        for var, start, stop, low, high in self.groups:
            p = table[var]
            values[start:stop] = p * values[high] + (1.0 - p) * values[low]
        return values[self.root_row].copy()

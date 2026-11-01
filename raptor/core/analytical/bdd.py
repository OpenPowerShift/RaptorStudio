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

from collections.abc import Sequence

from .errors import BddTooLarge

FALSE = 0
TRUE = 1

#: A variable never has this value; used only for ordering comparisons.
_NO_VAR = 1 << 60

#: Default cap. A 122-block real model is ~12k nodes; 2M is generous enough
#: for large practical diagrams and small enough to fail before exhausting
#: memory.
MAX_BDD_NODES = 250_000


class BDD:
    """Reduced ordered BDD over integer variables ``0..n-1``."""

    def __init__(self, max_nodes: int = MAX_BDD_NODES) -> None:
        if max_nodes < 2:
            raise ValueError("max_nodes must be at least 2")
        self.max_nodes = max_nodes
        #: index 0 = FALSE, 1 = TRUE, then (variable, low, high).
        self.nodes: list[tuple[int, int, int] | None] = [None, None]
        self._unique: dict[tuple[int, int, int], int] = {}
        self._var_of: dict[int, int] = {}
        self._not_memo: dict[int, int] = {FALSE: TRUE, TRUE: FALSE}
        self._apply_memo: dict[tuple[str, int, int], int] = {}

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
            raise BddTooLarge(
                f"the analytical BDD exceeded {self.max_nodes:,} nodes; "
                "this model is outside the practical static subset")
        node = len(self.nodes)
        self.nodes.append(key)
        self._unique[key] = node
        self._var_of[node] = var
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

        # G[j] = exactly j true so far. Start with zero true.
        exact = [TRUE] + [FALSE] * n
        for branch in live:
            neg = self.neg(branch)
            nxt = [FALSE] * (n + 1)
            for j in range(n + 1):
                if exact[j] == FALSE:
                    continue
                if exact[j] != FALSE:
                    nxt[j] = self.Or(nxt[j], self.And(exact[j], neg))
                if j + 1 <= n:
                    nxt[j + 1] = self.Or(nxt[j + 1],
                                         self.And(exact[j], branch))
            exact = nxt
        return self.Or(*exact[k:])

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

    def probability(self, root: int, probabilities: Sequence[float]) -> float:
        """Probability that ``root`` is true for independent variables.

        ``probabilities[i]`` is P(variable i = true). The multilinearity of
        the BDD is the whole point: this is exact for independent components.
        """
        memo: dict[int, float] = {}

        def visit(node: int) -> float:
            if node == FALSE:
                return 0.0
            if node == TRUE:
                return 1.0
            got = memo.get(node)
            if got is not None:
                return got
            var, low, high = self.nodes[node]  # type: ignore[misc]
            p = probabilities[var]
            if p < 0.0 or p > 1.0:
                raise ValueError(
                    f"probability for variable {var} is {p!r}, not in [0, 1]")
            value = p * visit(high) + (1.0 - p) * visit(low)
            memo[node] = value
            return value

        return visit(root)

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
        memo: dict[int, tuple[float, float]] = {}

        def visit(node: int) -> tuple[float, float]:
            if node == FALSE:
                return 0.0, 0.0
            if node == TRUE:
                return 1.0, 0.0
            got = memo.get(node)
            if got is not None:
                return got
            var, low, high = self.nodes[node]  # type: ignore[misc]
            p = probabilities[var]
            dp = derivatives[var]
            if p < 0.0 or p > 1.0:
                raise ValueError(
                    f"probability for variable {var} is {p!r}, not in [0, 1]")
            high_p, high_dp = visit(high)
            low_p, low_dp = visit(low)
            value = p * high_p + (1.0 - p) * low_p
            slope = dp * (high_p - low_p) + p * high_dp + (1.0 - p) * low_dp
            memo[node] = (value, slope)
            return value, slope

        return visit(root)

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

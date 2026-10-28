"""The behaviour suite: tests that say what the engine *does*.

Where `tests/unit/` holds the broad regression net, everything in here is a
claim about one behaviour, written in a literal next to it and cited to the
Raptor 7 procedure it reproduces, the port code, or a `docs/engine-design/` page. See
`README.md` for the rule set (B0-B8), the harness API and how to add a rung.

Built as a ladder, not a matrix: `spine.py` holds one canonical fixture per
rung, each the rung below plus exactly one new mechanism, and
`test_b_ladder.py` asserts each rung's timeline against a committed literal.
"""
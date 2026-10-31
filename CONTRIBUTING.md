# Contributing

## Setup

```bash
uv sync --extra dev --extra mcp       # Python 3.12+
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -n 4
```

Linux headless runs need the Qt runtime libraries listed in
`.github/workflows/tests.yml`.

## Checks CI runs

| Check | Command |
|---|---|
| Tests | `pytest -q` (add `-n 4` locally) |
| Lint | `python -m ruff check .` |
| Types | `python tools/mypy_gate.py`: zero mypy errors outside `raptor/gui`, and no GUI file above its count in `tools/mypy_gui_baseline.json` |
| Engine coverage | `python tools/engine_coverage.py run --check-floors`: no engine module's branch coverage below `tools/coverage_floors.json` |
| State coverage | `python tools/state_coverage.py --check -j 4`: every block state cell listed in `docs/engine-design/state-coverage.adoc` is still reached |
| Generated pages | `python tools/gen_engine_docs.py --check` and `python tools/gen_mcp_reference.py --check`: `docs/engine-design/` and `docs/MCP.md` match the code |
| Rendered docs | `pytest tests/unit/test_docs_html_current.py` (needs asciidoctor 2.0.26): the committed HTML matches its AsciiDoc |

## Skipping CI

There are two ways to keep a small change off the 30-minute matrix. Both are narrow on purpose.

**Automatic: documentation-only changes.** The `changed` job in `.github/workflows/tests.yml` diffs the branch against
its base and sets `docs_only` when every changed file is a Markdown file (`*.md`) or a licence file (`LICENSE`,
`LICENSES/*`). The matrix jobs (`test`, `fidelity`, `long-checks`, `ui-sweeps`, `mcp-e2e`, `typecheck`) then skip.
A skipped job reports as successful, so this does not leave a required check waiting.

`docs-html` still runs: it is the only job that proves a committed artefact matches its source, and it takes minutes.
AsciiDoc sources (`docs/**/*.adoc`, `new_docs/**/*.adoc`) are **not** documentation-only for this purpose, because
they regenerate `manual.html`, `book.html` and friends. Anything outside those two groups — code, tests, tools,
packaging, workflows — runs the full matrix.

So a codename commit that edits both `raptor/gui/ui/splash.py` and `RELEASING.md` is *not* docs-only: the constant is
code. Expect the suite.

**Manual: `[skip ci]` in the commit message.** GitHub's own keyword (also `[ci skip]`, `[no ci]`, `[skip actions]`,
`[actions skip]`) stops every workflow for that push or pull request, including `release-please`.

- On a pull request, a skipped workflow leaves its checks **pending**, which can block the merge when they are
  required. Reach for it only on a branch where you do not need those checks.
- On `main`, the commit is not picked up until the next push, because `release-please` reads commits between runs.
- Never use it for a commit that changes behaviour. "Small" is not the test; "cannot be wrong" is.

The suite is tiered by the reason a test is expensive; the default run
excludes the last three, and CI gives each its own job:

| Tier | Run it with | What it holds |
|---|---|---|
| everyday (default) | `pytest -q` | everything headless except the three below |
| engine only | `pytest -q -m "not gui and not slow and not fidelity and not long"` | ~3 minutes, no Qt widget tests |
| whole-output fidelity | `pytest -q -m fidelity` | runs compared against files real Raptor wrote |
| long checks | `pytest -q -m long` | IEC 61078 Monte Carlo and the book's number gates |
| UI sweeps | `pytest -q -m slow` | offscreen MainWindow subprocess drivers |

When a GUI file's mypy count or an engine module's coverage improves, lock
the gain in with `mypy_gate.py --update` or `engine_coverage.py report
<cov.json> --update-floors`.

## Changing the engine

The engine (`raptor/core/engine`, `raptor/core/scheduler.py`,
`raptor/core/rng.py`) is pinned two ways:

- **against real Raptor**: `test_golden_fidelity`, `test_event_log_fidelity`
  and `test_sd_diff_sweep` compare our output with real captures;
- **against itself**: `test_engine_snapshot` compares every model's event
  log, reports, random draws, capacity and cost output with
  `tests/golden/SNAPSHOT.toml`, so *any* behaviour change is visible, even
  on a model that does not match real Raptor yet.

### Commit convention ("two hats")

A commit either changes behaviour or changes structure, never both.

- **`refactor:`** (and `test:`, `docs:`, `chore:`): the snapshot must stay
  identical. Do not regenerate `SNAPSHOT.toml` in these commits.
- **`fix:`/`feat:`** that changes engine output: regenerate with
  `python tools/engine_snapshot.py --write` and name the models whose
  snapshot moved, and why, in the commit message. To see what moved, run
  `python tools/engine_snapshot.py --dump DIR` here and at the parent
  commit (a `git worktree`), then `diff -r`.
- A commit that only moves or reformats code is added to
  `.git-blame-ignore-revs` (run `git config blame.ignoreRevsFile
  .git-blame-ignore-revs` once so `git blame` skips it).

### Engine code conventions

- Name the Raptor 7 procedure a ported behaviour reproduces and state the
  rule as it stands now, in a few lines. Investigation history,
  rejected alternatives and trace evidence go in the pull request
  description, not in code comments.
- The engine never reads `os.environ`; options are passed in explicitly.
- Runtime checks raise `raptor.core.engine.errors` exceptions
  (`EngineInvariantError`, `UnexpectedWake`), never `assert`, which
  Python strips under `-O`.
- Closed vocabularies (interrupt reasons, owner kinds) are enums, and
  multi-value results are `NamedTuple`s or frozen dataclasses, not bare
  tuples or strings.

## Releasing

Maintainers cut releases with release-please and approve each PyPI publish by hand; [`RELEASING.md`](RELEASING.md)
has the one-time setup and the step-by-step checklist.

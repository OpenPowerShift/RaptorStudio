# Releasing RAPTOR Studio

Releases are cut from `main` with [release-please](https://github.com/googleapis/release-please) and published by
`.github/workflows/release.yml`. Nothing reaches PyPI, the MCP Registry or the Release's downloads until a maintainer
approves the `pypi` job, after the same files have been published to TestPyPI and smoke-tested.

```
merge to main ─▶ release-please PR ─▶ merge it ─▶ tag vX.Y.Z + GitHub Release
  ─▶ tests ─▶ build ─▶ TestPyPI ─▶ smoke test ─▶ ⏸ approve ─▶ PyPI ─▶ MCP Registry
                                                                  └─▶ installers ─▶ SHA256SUMS
```

## One-time setup

Already done for this repository; repeat only for a fork or a new maintainer.

1. **Environments** (Settings → Environments):
   - `pypi`: **Required reviewers** = the maintainers who may approve a release. Tick *Prevent self-review* only if
     there is more than one maintainer.
   - `pypi-mcp`, `testpypi`, `testpypi-mcp`: no protection rules. `pypi-mcp` only ever runs after the approved `pypi`
     job.
2. **Trusted publishers**: four of them, one per project per index, each with its own environment. PyPI refuses two
   pending publishers with the same configuration for different project names. All use the GitHub publisher with
   owner `OpenPowerShift`, repository `RaptorStudio` and workflow `release.yml`:

   | Index | Project | Environment |
   |---|---|---|
   | [PyPI](https://pypi.org/manage/account/publishing/) | `raptor-studio` | `pypi` |
   | PyPI | `raptor-studio-mcp` | `pypi-mcp` |
   | [TestPyPI](https://test.pypi.org/manage/account/publishing/) | `raptor-studio` | `testpypi` |
   | TestPyPI | `raptor-studio-mcp` | `testpypi-mcp` |

   Once a project exists, its publisher is on the project's own page (Manage → Publishing). No API tokens are used
   anywhere.
3. **`RELEASE_PLEASE_TOKEN`** (Settings → Secrets and variables → Actions): a fine-grained personal access token, or a
   GitHub App token, with *Contents* and *Pull requests* read/write on this repository. A release created with the
   default `GITHUB_TOKEN` does not start `release.yml`, so without this secret every release stalls after the tag.
4. **Actions** (Settings → Actions → General): *Read and write permissions* for workflows.

## Regular release

### 1. Get `main` ready

- Merge the work for the release. Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `fix:` gives a patch release, `feat:` a minor one, and `feat!:` or a `BREAKING CHANGE:` footer a major one.
  `docs:`, `test:`, `chore:` and `refactor:` appear in the history but do not trigger a release on their own.
- Check the **Tests** workflow is green on the latest `main` commit.

### 2. Check locally (optional, recommended)

From an up-to-date checkout of `main`:

```bash
uv build --out-dir /tmp/rs-dist .
uv build --out-dir /tmp/rs-dist packaging/raptor-studio-mcp
uvx twine check /tmp/rs-dist/*
uv venv /tmp/rs-venv -p 3.13
uv pip install -p /tmp/rs-venv/bin/python --find-links /tmp/rs-dist raptor-studio-mcp
/tmp/rs-venv/bin/python tools/release_smoke.py "$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
```

The last line must end with `ok: raptor X.Y.Z; example model simulated; MCP server answered with … tools`. This
checks the current version; the release PR only changes the version number.

### 3. Merge the release PR

1. release-please keeps an open PR titled `chore(main): release X.Y.Z`. Open it.
2. Check the version is the one you expect and read the generated `CHANGELOG.md` entry; edit the PR if the wording
   needs it.
3. Check the PR changes the version in all five places: `pyproject.toml`, `raptor/__init__.py`, `server.json` (twice)
   and `packaging/raptor-studio-mcp/pyproject.toml` (the version **and** the `raptor-studio[mcp]==` pin).
4. Merge it. release-please then creates the `vX.Y.Z` tag and a published GitHub Release, which starts **Release** in
   the Actions tab.

### 4. Watch the run up to the approval

In Actions → **Release** → the run for `vX.Y.Z`:

1. **test** runs the full suite, about 30 minutes.
2. **Build sdist + wheel** checks the built version matches the tag.
3. **Publish raptor_studio / raptor_studio_mcp to TestPyPI** uploads each package from its own environment.
4. **Smoke-test the TestPyPI upload** downloads both packages back from TestPyPI, checks they are byte-identical to
   the build, installs them in a clean environment (dependencies from PyPI) and runs `tools/release_smoke.py`.
5. **Publish raptor-studio to PyPI (needs approval)** shows *Waiting*.

If any step before the approval fails, nothing public has happened apart from the TestPyPI upload. Fix the cause on
`main`, then either re-run the failed jobs (Re-run failed jobs) or, if the fix changes the code, delete the GitHub
Release and the tag and let release-please cut the next version.

### 5. Check TestPyPI yourself, then approve

1. Open `https://test.pypi.org/project/raptor-studio/X.Y.Z/` and check the description renders.
2. Optionally install it on your own machine, from a checkout of the tag:

   ```bash
   python3 -m venv /tmp/rs-test
   /tmp/rs-test/bin/pip download --no-deps --dest /tmp/rs-testpypi \
       --index-url https://test.pypi.org/simple/ raptor-studio==X.Y.Z raptor-studio-mcp==X.Y.Z
   /tmp/rs-test/bin/pip install --find-links /tmp/rs-testpypi raptor-studio-mcp==X.Y.Z
   /tmp/rs-test/bin/python tools/release_smoke.py X.Y.Z
   /tmp/rs-test/bin/raptor        # opens the desktop application
   ```

   Only our two packages come from TestPyPI (`--no-deps`); everything else comes from PyPI. Do not resolve
   dependencies from TestPyPI: anyone can upload any package name there.
3. In the waiting job, **Review deployments** → tick `pypi` → **Approve and deploy**. **Reject** stops the release
   with PyPI, the registry and the downloads untouched.

A PyPI version can never be uploaded twice, even after deleting it. Approve only when you are sure.

### 6. After approval

These run on their own, about 30 minutes:

- **Publish raptor-studio to PyPI**, then **Publish raptor-studio-mcp to PyPI**.
- **Publish to the MCP Registry**: checks `server.json` matches the tag, then retries for up to five minutes while PyPI
  catches up.
- **Installers** for Windows, macOS (Apple Silicon and Intel) and Linux, uploaded to the GitHub Release.
- **SHA256SUMS**, plus the install notes appended to the Release text.

### 7. Verify the release

```bash
uv venv /tmp/rs-pypi -p 3.13
uv pip install -p /tmp/rs-pypi/bin/python "raptor-studio-mcp==X.Y.Z"
/tmp/rs-pypi/bin/python tools/release_smoke.py X.Y.Z
uvx raptor-studio-mcp --help
```

Then:

- The Release page lists six installers and `SHA256SUMS.txt`; download them and run `sha256sum -c SHA256SUMS.txt`.
- Install at least one installer on a clean machine. It should launch, open an example, simulate, export a PDF, and
  open help with **F1**. The installers are unsigned, so expect the operating system's warning; the Release text says
  how to get past it.
- Search for `raptor-studio` in the [MCP Registry](https://registry.modelcontextprotocol.io/) and check the version.

## When something goes wrong after approval

- **PyPI publish failed:** re-run the failed job. It uploads the same build artefact with `skip-existing`, so files
  that already made it are skipped rather than failing.
- **A bad version reached PyPI:** *yank* it on PyPI (Manage → Releases → Options → Yank) with a reason, fix on `main`
  with a `fix:` commit, and release the next patch version. Do not delete it: deleting frees nothing, because the
  version number can never be reused.
- **An installer job failed for a passing reason** (a runner hiccup): re-run that job. It uploads with `--clobber`;
  then re-run **SHA256SUMS** so the checksums cover every file.
- **An installer job failed because the build itself needs a fix:** fix it on `main`, then rebuild the installers for
  the existing Release. Re-running the old job would use the workflow as it was at the tag.

  ```bash
  gh workflow run release.yml --repo OpenPowerShift/RaptorStudio --ref main -f tag=vX.Y.Z
  ```

  This builds the tag's code with `main`'s packaging (`installer/`, `THIRD_PARTY_NOTICES.md`) and workflow steps,
  uploads the installers to that Release and regenerates `SHA256SUMS.txt`. It does not touch PyPI or the MCP
  Registry.
- **The MCP Registry job failed:** re-run it once PyPI shows the version. It is safe to re-run.

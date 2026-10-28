# legacy/ — local-only working material

Everything here except this file is **git-ignored**.

| Path | Contents |
|---|---|
| `rbd/` | The original sample-model corpus (textbook problems, demos, phasing/hierarchy samples), the `.md` notes beside them, and `library/` — the scripts that generated them |

Nothing the application or CI needs lives here. Models the test suite
uses were promoted to `tests/data/models/`. `tests/integration/sweeps/ui_sweep6.py` walks
`legacy/rbd/` when it is present and is skipped otherwise.
Comments across the code that cite `legacy/rbd/<Name>.rbd` point at files
here.

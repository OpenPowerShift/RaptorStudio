"""Generate docs/advisor.adoc's rule catalogue from the live registry.

    python tools/gen_advisor_docs.py          # rewrite the generated block
    python tools/gen_advisor_docs.py --check  # exit 1 if it is stale

The prose above and below the block is hand-written; only the table is
generated, so a rule cannot be added without appearing in the docs.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "advisor.adoc"
BEGIN = "// BEGIN GENERATED RULE TABLE (tools/gen_advisor_docs.py)"
END = "// END GENERATED RULE TABLE"

INTRO = """= The Advisor: is my model right?
:toc: left
:toclevels: 3

The Advisor checks a model before it is run and a finished run after it.
Every finding has a stable rule id, a severity (`error`, `warning`,
`advice`, `info`), a basis (`exact`, `statistical`, `heuristic`) and, where
one exists, a fix. This page is the catalogue; the tutorial chapter
<<ch-advisor>> shows the workflow.

== Severities

* **error** — the model cannot run or the result cannot be trusted.
* **warning** — the result will be misleading in a way you should see first.
* **advice** — a cheaper, clearer or safer alternative exists.
* **info** — a fact worth knowing, not a defect.

== The rules

"""

FOOTER = """

== Running the checks

From the application: **Analyse ▸ Advisor**, or the canvas badge on a
model. From a terminal:

[source,bash]
----
raptor advise MODEL
raptor advise MODEL --simulate --target 'system.ao>=0.995'
raptor advise MODEL --why --only M-SPR,M-PM
raptor advise MODEL --format json
----

`--simulate` runs the model with its own settings and adds the after-run
findings (precision, requirements, drivers). `--strict` makes warnings exit
with status 3, for CI. The same rules are available to an assistant through
the MCP `advise` tool, and every finding lists its rule id so a review can
quote it.
"""


def render() -> str:
    from raptor.api.advisor import list_rules
    rows = ["[cols=\"1,2,4,2\",options=\"header\"]",
            "|===",
            "| Rule | Level | Finding | Basis",
            ""]
    for finder in sorted(list_rules(), key=lambda rule: rule.id):
        rows.append(f"| `{finder.id}` | {finder.severity} | "
                    f"{finder.title} | {finder.basis}")
    rows += ["|===", ""]
    return INTRO + "\n".join(rows) + FOOTER


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current == text:
            return 0
        print("docs/advisor.adoc is stale: run tools/gen_advisor_docs.py",
              file=sys.stderr)
        return 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

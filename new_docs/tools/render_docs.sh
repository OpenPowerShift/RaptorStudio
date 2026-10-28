#!/usr/bin/env bash
# Render the RAPTOR Studio help docs (tutorial book + reference manual +
# formulas quick reference) with the ops theme stylesheet and the
# opsmodel:: macro extension. Each output dir is self-contained
# (stylesheet copied next to the HTML):
#   new_docs/book.html      + new_docs/ops_asciidoctor.css
#   docs/manual.html        + docs/ops_asciidoctor.css
#   docs/formulas.html      + docs/ops_asciidoctor.css
# The in-app help viewer and the Help ribbon buttons look in these spots.
set -euo pipefail
# Pinned: CI (.github/workflows/tests.yml, docs-html) renders with asciidoctor 2.0.26 and compares byte for byte.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

EXT="$ROOT/new_docs/tools/ops_adoc_ext.rb"
STYLE=ops_asciidoctor.css

asciidoctor -r "$EXT" \
  -a stylesheet="$STYLE" -a linkcss \
  -a webfonts! -a font-awesome! -a reproducible \
  new_docs/book.adoc -o new_docs/book.html

# docs/ gets its own copy so manual.html and formulas.html work standalone.
cp "new_docs/$STYLE" "docs/$STYLE"
asciidoctor -r "$EXT" \
  -a stylesheet="$STYLE" -a linkcss \
  -a webfonts! -a font-awesome! -a reproducible \
  docs/manual.adoc -o docs/manual.html
asciidoctor -r "$EXT" \
  -a stylesheet="$STYLE" -a linkcss \
  -a webfonts! -a font-awesome! -a reproducible \
  docs/formulas.adoc -o docs/formulas.html
# The formulas are AsciiMath: swap the CDN MathJax for the local copy (docs/js) so they show offline.
python3 new_docs/tools/localise_mathjax.py docs/formulas.html

echo "rendered: new_docs/book.html docs/manual.html docs/formulas.html"

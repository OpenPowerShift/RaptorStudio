"""Quality gate: broken xrefs, missing images, dangling opsmodel:: links.

Checks the whole book corpus (every chapter book.adoc includes, plus
book.adoc itself) for three defect classes that otherwise only surface by
eyeballing the rendered HTML:

  1. `<<ch-id>>` / `<<ch-id,text>>` xrefs whose target has no matching
     `[[ch-id]]` anchor anywhere in the corpus.
  2. `image::path[...]` macros naming a file that does not exist under
     `new_docs/images/` (book.adoc sets `:imagesdir: images`, so paths
     are relative to that directory).
  3. `opsmodel:Name[]` macros naming a model with no matching
     `models/Name.rbds` or `models/Name.rbd`.

Also reports orphaned assets: PNG/SVG files in `new_docs/images/` that no
chapter's `image::` macro references.

Usage (from repo root): .venv/bin/python new_docs/tools/check_links.py
Exits 1 and prints every failure if anything is broken; exits 0 and
prints a one-line summary otherwise.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NEW_DOCS = os.path.join(ROOT, "new_docs")
CHAPTERS = os.path.join(NEW_DOCS, "chapters")
IMAGES = os.path.join(NEW_DOCS, "images")
MODELS = os.path.join(NEW_DOCS, "models")
BOOK = os.path.join(NEW_DOCS, "book.adoc")

ANCHOR_RE = re.compile(r"^\[\[([\w.\-]+)\]\]")
XREF_RE = re.compile(r"<<([\w.\-]+)(?:,[^>]*)?>>")
IMAGE_RE = re.compile(r"image::([^\[\s]+)\[")
OPSMODEL_RE = re.compile(r"opsmodel:([\w.\-]+)\[\]")
INCLUDE_RE = re.compile(r"^include::chapters/([\w.\-]+\.adoc)\[\]")


def book_chapter_files():
    files = []
    with open(BOOK, encoding="utf-8") as f:
        for line in f:
            m = INCLUDE_RE.match(line.strip())
            if m:
                files.append(os.path.join(CHAPTERS, m.group(1)))
    return files


def model_exists(name):
    stem = name
    for ext in (".rbds", ".rbd"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
    return (os.path.isfile(os.path.join(MODELS, stem + ".rbds"))
            or os.path.isfile(os.path.join(MODELS, stem + ".rbd")))


def main():
    chapter_files = book_chapter_files()
    corpus = [BOOK] + chapter_files

    anchors = set()
    xrefs = []       # (file, line_no, target)
    images = []      # (file, line_no, path)
    opsmodels = []    # (file, line_no, name)
    referenced_images = set()

    for path in corpus:
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                m = ANCHOR_RE.match(line.strip())
                if m:
                    anchors.add(m.group(1))
                for m in XREF_RE.finditer(line):
                    xrefs.append((path, i, m.group(1)))
                for m in IMAGE_RE.finditer(line):
                    images.append((path, i, m.group(1)))
                    referenced_images.add(m.group(1))
                for m in OPSMODEL_RE.finditer(line):
                    opsmodels.append((path, i, m.group(1)))

    failures = []

    for path, lineno, target in xrefs:
        if target not in anchors:
            failures.append(
                f"{os.path.relpath(path, ROOT)}:{lineno}: broken xref "
                f"<<{target}>> -- no [[{target}]] anchor in the corpus")

    for path, lineno, rel in images:
        full = os.path.join(IMAGES, rel)
        if not os.path.isfile(full):
            failures.append(
                f"{os.path.relpath(path, ROOT)}:{lineno}: image::{rel}[] "
                f"-- missing file new_docs/images/{rel}")

    for path, lineno, name in opsmodels:
        if not model_exists(name):
            failures.append(
                f"{os.path.relpath(path, ROOT)}:{lineno}: opsmodel:{name}[] "
                f"-- no models/{name}.rbds or .rbd")

    orphans = []
    if os.path.isdir(IMAGES):
        for fname in sorted(os.listdir(IMAGES)):
            if not fname.lower().endswith((".png", ".svg", ".jpg", ".jpeg")):
                continue
            if fname not in referenced_images:
                orphans.append(fname)
    for fname in orphans:
        failures.append(
            f"new_docs/images/{fname}: orphaned -- captured/drawn but no "
            f"chapter's image:: macro references it")

    if failures:
        print(f"check_links.py: {len(failures)} problem(s) found:\n")
        for line in failures:
            print(" -", line)
        return 1

    print(f"check_links.py: OK -- {len(chapter_files)} chapters, "
          f"{len(xrefs)} xrefs, {len(images)} images, "
          f"{len(opsmodels)} opsmodel links, 0 orphaned assets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

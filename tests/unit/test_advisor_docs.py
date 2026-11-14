"""The Advisor catalogue and tutorial chapter stay current (R1 docs)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_generated_rule_catalogue_is_current():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/gen_advisor_docs.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_every_rule_appears_in_the_catalogue():
    from raptor.api.advisor import list_rules
    text = (ROOT / "docs/advisor.adoc").read_text(encoding="utf-8")
    missing = [rule.id for rule in list_rules()
               if f"`{rule.id}`" not in text]
    assert missing == []


def test_the_advisor_chapter_is_in_the_book():
    book = (ROOT / "new_docs/book.adoc").read_text(encoding="utf-8")
    assert "chapters/ch17-advisor.adoc" in book
    chapter = (ROOT / "new_docs/chapters/ch17-advisor.adoc").read_text(
        encoding="utf-8")
    assert "[[ch-advisor]]" in chapter
    assert "raptor advise" in chapter

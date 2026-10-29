"""Docs importer: pure parsing helpers (no database)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.domain.services.support import articles as kb
from app.domain.services.support.vocabulary import CATEGORY_IDS

DOCS_DIR = Path(__file__).resolve().parents[4] / "Talk-Leee" / "src" / "app" / "docs" / "_content"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,158}[a-z0-9]$")
URL_RE = re.compile(r"^/[A-Za-z0-9/_#.-]*$")

SAMPLE = """Intro line with **bold** text.

## Open Contacts

1. Select **Contacts**.

![alt text](/docs/x.webp)

```bash
## not a heading
echo hi
```

### Sub *Section*

Sub body.

## Open Contacts

Duplicate heading body.

## Empty parent
### Child

Child body.
"""


def test_heading_id_parity():
    assert kb.heading_id("Calls fail or audio quality is poor") == "calls-fail-or-audio-quality-is-poor"
    assert kb.heading_id("A campaign will not start, or Resume is unavailable") == \
        "a-campaign-will-not-start-or-resume-is-unavailable"
    assert kb.heading_id("  --Hello, World!--  ") == "hello-world"


def test_registry_parses_real_file_and_files_exist():
    pages = kb.parse_registry((DOCS_DIR / "registry.ts").read_text(encoding="utf-8"))
    assert len(pages) >= 5
    assert pages[0].slug == "getting-started" and pages[0].title == "Getting Started"
    for page in pages:
        assert (DOCS_DIR / page.file).is_file(), page.file


def test_registry_with_no_pages_fails_loudly():
    with pytest.raises(ValueError):
        kb.parse_registry("export const docsPages = [];")


def test_split_sections_structure():
    sections = kb.split_sections(SAMPLE, page_slug="contacts", page_title="Contacts")
    by_section = [s.section for s in sections]
    assert by_section == ["", "Open Contacts", "Sub Section", "Open Contacts", "Child"]
    intro, first, sub, dup, child = sections
    assert intro.url == "/docs/contacts" and intro.slug == "contacts-intro"
    assert first.url == "/docs/contacts#open-contacts"
    assert "## not a heading" in first.body_md  # fenced code is kept, not split
    assert "![" not in first.body_md and "alt text" not in first.body_md
    assert first.body_md.startswith("## Open Contacts")
    assert sub.url == "/docs/contacts#sub-section"
    assert all(s.title == "Contacts" for s in sections)
    assert dup.slug != first.slug and dup.slug.endswith("-2")
    assert child.body_md.rstrip().endswith("Child body.")


def test_slugs_and_urls_match_checks_and_are_unique():
    long_heading = "word " * 80
    md = f"## {long_heading}\n\nbody\n\n## {long_heading}\n\nbody2\n"
    sections = kb.split_sections(md, page_slug="p" * 60, page_title="T")
    slugs = [s.slug for s in sections]
    assert len(set(slugs)) == len(slugs)
    for s in sections:
        assert SLUG_RE.match(s.slug), s.slug
        assert URL_RE.match(s.url), s.url


def test_category_mapping():
    assert kb.category_for_page("billing") == "billing"
    assert kb.category_for_page("follow-ups") == "calls_campaigns"
    assert kb.category_for_page("ai-options") == "ai_agent_voices"
    assert kb.category_for_page("connectors") == "integrations"
    assert kb.category_for_page("settings-and-security") == "account_login"
    assert kb.category_for_page("glossary") == "other"


def test_content_hash_is_stable_and_sensitive():
    a, b = kb.split_sections("## A\n\nx\n", page_slug="p", page_title="P")[0], \
        kb.split_sections("## A\n\ny\n", page_slug="p", page_title="P")[0]
    assert re.fullmatch(r"[0-9a-f]{64}", a.content_hash)
    assert a.content_hash != b.content_hash
    assert a.content_hash == kb.split_sections("## A\n\nx\n", page_slug="p", page_title="P")[0].content_hash


def test_all_real_docs_produce_valid_sections():
    pages = kb.parse_registry((DOCS_DIR / "registry.ts").read_text(encoding="utf-8"))
    all_slugs: set[str] = set()
    total = 0
    for page in pages:
        text = (DOCS_DIR / page.file).read_text(encoding="utf-8")
        for s in kb.split_sections(text, page_slug=page.slug, page_title=page.title):
            total += 1
            assert s.body_md.strip(), (page.slug, s.section)
            assert 1 <= len(s.body_md) <= 50000 and len(s.section) <= 200
            assert SLUG_RE.match(s.slug) and URL_RE.match(s.url), (s.slug, s.url)
            assert kb.category_for_page(page.slug) in CATEGORY_IDS
            assert s.slug not in all_slugs, s.slug
            all_slugs.add(s.slug)
    assert total > 20

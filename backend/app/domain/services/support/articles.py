"""Help-article search and the docs importer for the support system.

Search mirrors ``app/services/scripts/knowledge/retrieval.py``: an AND
``websearch_to_tsquery`` match, an OR fallback, and a ``word_similarity``
trigram fallback for typos. The importer turns the public docs markdown into
one article per ``##``/``###`` section so an answer can link to its anchor.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.core.db_utils import acquire_with_tenant

SIMILARITY_FLOOR = 0.3
MAX_QUERY_CHARS = 200
SNIPPET_CHARS = 240
MAX_SLUG = 160
MAX_BODY = 50000
MAX_SECTION = 200

_SEARCH_SQL = """
WITH tq AS (
    SELECT websearch_to_tsquery('english', $1) AS q_and,
           NULLIF(replace(websearch_to_tsquery('english', $1)::text, ' & ', ' | '), '')::tsquery AS q_or
), hits AS (
    SELECT a.slug, a.title, a.section, a.body_md, a.url, a.updated_at,
           ts_rank(a.search_tsv, tq.q_and) * 2
             + CASE WHEN tq.q_or IS NULL THEN 0 ELSE ts_rank(a.search_tsv, tq.q_or) END
             + word_similarity($1, a.search_text) AS rank,
           COALESCE(tq.q_or, tq.q_and) AS hq
    FROM public.support_kb_articles a, tq
    WHERE a.published
      AND (a.search_tsv @@ tq.q_and
           OR (tq.q_or IS NOT NULL AND a.search_tsv @@ tq.q_or)
           OR word_similarity($1, a.search_text) >= $3)
    ORDER BY rank DESC, a.updated_at DESC
    LIMIT $2
)
SELECT slug, title, section, url, body_md,
       ts_headline('english', body_md, hq,
                   'MaxWords=35, MinWords=12, StartSel="", StopSel=""') AS headline
FROM hits ORDER BY rank DESC, updated_at DESC
"""

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_TAG_RE = re.compile(r"<[^>]+>")
_PARTIAL_LINK_RE = re.compile(r"\]\([^)]*\)?|\([^)\s]*$")
_MD_CHARS_RE = re.compile(r"[#*_`>|\[\]]+")


def heading_id(text: str) -> str:
    """Exact port of ``headingId`` in docs-markdown.tsx."""
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", text.lower()))


def plain_text(markdown: str, limit: int = SNIPPET_CHARS) -> str:
    text = _IMAGE_RE.sub(" ", markdown)
    text = _LINK_RE.sub(r"\1", text)
    text = _TAG_RE.sub(" ", text)
    text = _PARTIAL_LINK_RE.sub(" ", text)
    text = _MD_CHARS_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _normalise_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip())[:MAX_QUERY_CHARS].strip()


async def search_articles(pool, query: str, *, limit: int = 5, tenant_id: str | None = None) -> list[dict]:
    q = _normalise_query(query)
    if len(q) < 2:
        return []
    limit = max(1, min(int(limit), 10))
    async with acquire_with_tenant(pool, tenant_id) as conn:
        rows = await conn.fetch(_SEARCH_SQL, q, limit, SIMILARITY_FLOOR)
    results = []
    for row in rows:
        snippet = plain_text(row["headline"] or "") or plain_text(row["body_md"])
        results.append({"slug": row["slug"], "title": row["title"], "section": row["section"],
                        "snippet": snippet, "url": row["url"]})
    return results


# ---- docs import -----------------------------------------------------------

@dataclass(frozen=True)
class DocsPage:
    slug: str
    title: str
    file: str


@dataclass(frozen=True)
class ArticleSection:
    slug: str
    title: str
    section: str
    body_md: str
    category: str
    url: str
    content_hash: str


@dataclass
class ImportSummary:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    unpublished: int = 0
    skipped_staff_slugs: list[str] = field(default_factory=list)


_CATEGORY_BY_PAGE = {
    "billing": "billing",
    "outbound-campaigns": "calls_campaigns", "contacts": "calls_campaigns",
    "calls-and-recordings": "calls_campaigns", "inbound": "calls_campaigns",
    "workflows": "calls_campaigns", "follow-ups": "calls_campaigns",
    "ai-options": "ai_agent_voices",
    "connectors": "integrations",
    "settings-and-security": "account_login",
}


def category_for_page(slug: str) -> str:
    return _CATEGORY_BY_PAGE.get(slug, "other")


_REGISTRY_ENTRY_RE = re.compile(
    r"\{\s*slug:\s*\"([^\"]+)\",\s*title:\s*\"([^\"]+)\",.*?file:\s*\"([^\"]+)\"\s*,?\s*\}", re.S)


def parse_registry(ts_source: str) -> list[DocsPage]:
    start = ts_source.find("docsPages")
    body = ts_source[start:] if start >= 0 else ""
    pages = [DocsPage(slug, title, file) for slug, title, file in _REGISTRY_ENTRY_RE.findall(body)]
    if not pages:
        raise ValueError("no docsPages entries could be parsed from registry.ts")
    return pages


def content_hash(title: str, section: str, body_md: str, category: str, url: str) -> str:
    return hashlib.sha256("\x1f".join((title, section, body_md, category, url)).encode("utf-8")).hexdigest()


_HEADING_RE = re.compile(r"^(#{2,3})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")


def _heading_text(raw: str) -> str:
    text = _LINK_RE.sub(r"\1", raw)
    text = re.sub(r"[*_`]+", "", text)
    return text.strip()


def _clean_body(text: str) -> str:
    text = _IMAGE_RE.sub("", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _unique_slug(base: str, taken: set[str]) -> str:
    base = base[:MAX_SLUG].rstrip("-")
    slug, n = base, 1
    while slug in taken:
        n += 1
        suffix = f"-{n}"
        slug = base[: MAX_SLUG - len(suffix)].rstrip("-") + suffix
    taken.add(slug)
    return slug


def split_sections(markdown: str, *, page_slug: str, page_title: str) -> list[ArticleSection]:
    # Each chunk: (heading text or None for the intro, its lines).
    chunks: list[tuple[str | None, list[str]]] = [(None, [])]
    in_fence = False
    for line in markdown.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING_RE.match(line)
        if match:
            chunks.append((_heading_text(match.group(2)), [line]))
        else:
            chunks[-1][1].append(line)

    category = category_for_page(page_slug)
    taken: set[str] = set()
    sections: list[ArticleSection] = []
    for heading, lines in chunks:
        body = _clean_body("\n".join(lines))
        if heading is None:
            if not body:
                continue
            section, slug_base, url = "", f"{page_slug}-intro", f"/docs/{page_slug}"
        else:
            # A heading with nothing under it (e.g. a ## directly above a ###) is not an article.
            if not _clean_body("\n".join(lines[1:])):
                continue
            hid = heading_id(heading)
            section = heading[:MAX_SECTION]
            slug_base = f"{page_slug}-{hid}" if hid else page_slug
            url = f"/docs/{page_slug}#{hid}" if hid else f"/docs/{page_slug}"
        body = body[:MAX_BODY]
        sections.append(ArticleSection(
            slug=_unique_slug(slug_base, taken), title=page_title, section=section, body_md=body,
            category=category, url=url,
            content_hash=content_hash(page_title, section, body, category, url)))
    return sections


_UPSERT_SQL = """
INSERT INTO public.support_kb_articles
    (slug, title, section, body_md, category, source, url, published, content_hash)
VALUES ($1, $2, $3, $4, $5, 'docs', $6, TRUE, $7)
ON CONFLICT (slug) DO UPDATE SET
    title = EXCLUDED.title, section = EXCLUDED.section, body_md = EXCLUDED.body_md,
    category = EXCLUDED.category, url = EXCLUDED.url, content_hash = EXCLUDED.content_hash,
    published = TRUE, updated_at = now()
WHERE support_kb_articles.source = 'docs'
  AND (support_kb_articles.content_hash IS DISTINCT FROM EXCLUDED.content_hash
       OR NOT support_kb_articles.published)
RETURNING (xmax = 0) AS inserted
"""


async def upsert_docs_articles(pool, sections: list[ArticleSection]) -> ImportSummary:
    if not sections:
        raise ValueError("refusing to import zero sections (would unpublish every docs article)")
    summary = ImportSummary()
    slugs = [s.slug for s in sections]
    async with acquire_with_tenant(pool, None) as conn:
        staff = {r["slug"] for r in await conn.fetch(
            "SELECT slug FROM public.support_kb_articles WHERE source = 'staff' AND slug = ANY($1::text[])", slugs)}
        for s in sections:
            if s.slug in staff:
                summary.skipped_staff_slugs.append(s.slug)
                continue
            row = await conn.fetchrow(_UPSERT_SQL, s.slug, s.title, s.section, s.body_md,
                                      s.category, s.url, s.content_hash)
            if row is None:
                summary.unchanged += 1
            elif row["inserted"]:
                summary.inserted += 1
            else:
                summary.updated += 1
        gone = await conn.fetch(
            "UPDATE public.support_kb_articles SET published = FALSE, updated_at = now() "
            "WHERE source = 'docs' AND published AND NOT (slug = ANY($1::text[])) RETURNING slug", slugs)
        summary.unpublished = len(gone)
    return summary

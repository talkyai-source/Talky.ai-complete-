"""Support article search and docs upsert on a migrated disposable *_test PostgreSQL."""
from __future__ import annotations

import os
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.domain.services.support import articles as kb

pytestmark = pytest.mark.integration

PREFIX = "zz-test-"


def _section(slug, body, section="Calendar setup", title="ZZ Test Page"):
    url = f"/docs/zz-test#{slug}"
    return kb.ArticleSection(slug=PREFIX + slug, title=title, section=section, body_md=body,
                             category="other", url=url,
                             content_hash=kb.content_hash(title, section, body, "other", url))


@pytest_asyncio.fixture
async def pool():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit TEST_DATABASE_URL is required")
    if urlparse(dsn).hostname not in {"127.0.0.1", "localhost"} or not urlparse(dsn).path.removeprefix("/").endswith("_test"):
        pytest.fail("Support article integration requires an explicit disposable *_test database")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, timeout=5)

    async def clean():
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'on'")
                await conn.execute("DELETE FROM public.support_kb_articles WHERE slug LIKE $1", PREFIX + "%")
    await clean()
    try:
        yield pool
    finally:
        await clean()
        await pool.close()


async def _row(pool, slug):
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            return await conn.fetchrow("SELECT * FROM public.support_kb_articles WHERE slug=$1", PREFIX + slug)


async def _import(pool, sections):
    """Import only our own fixtures; real docs rows are never touched by the unpublish step
    because we run against a disposable DB, but we scope assertions to our slugs."""
    return await kb.upsert_docs_articles(pool, sections)


@pytest.mark.asyncio
async def test_upsert_is_idempotent_and_updates_on_change(pool):
    secs = [_section("a", "Connect your calendar to book meetings."), _section("b", "Another body here.")]
    first = await _import(pool, secs)
    assert first.inserted == 2 and first.updated == 0
    second = await _import(pool, secs)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 2)
    changed = await _import(pool, [_section("a", "Changed body text."), secs[1]])
    assert (changed.inserted, changed.updated, changed.unchanged) == (0, 1, 1)
    assert (await _row(pool, "a"))["body_md"] == "Changed body text."


@pytest.mark.asyncio
async def test_staff_article_is_never_overwritten(pool):
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            await conn.execute(
                "INSERT INTO public.support_kb_articles(slug,title,body_md,category,source,url,published) "
                "VALUES($1,'Staff','staff words','other','staff','/help/x',TRUE)", PREFIX + "clash")
    summary = await _import(pool, [_section("clash", "docs words"), _section("ok", "fine")])
    assert summary.skipped_staff_slugs == [PREFIX + "clash"]
    row = await _row(pool, "clash")
    assert row["source"] == "staff" and row["body_md"] == "staff words" and row["title"] == "Staff"


@pytest.mark.asyncio
async def test_removed_sections_are_unpublished_not_deleted_and_republished(pool):
    a, b = _section("keep", "keep body"), _section("drop", "drop body")
    await _import(pool, [a, b])
    summary = await _import(pool, [a])
    assert PREFIX + "drop" in await _unpublished(pool, summary)
    row = await _row(pool, "drop")
    assert row is not None and row["published"] is False
    back = await _import(pool, [a, b])
    assert back.updated == 1 and (await _row(pool, "drop"))["published"] is True


async def _unpublished(pool, summary):
    assert summary.unpublished >= 1
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            rows = await conn.fetch("SELECT slug FROM public.support_kb_articles WHERE NOT published AND slug LIKE $1",
                                    PREFIX + "%")
    return [r["slug"] for r in rows]


@pytest.mark.asyncio
async def test_search_phrase_typo_unpublished_and_plain_snippets(pool):
    body = ("## Calendar setup\n\n**Connect** your [calendar](/docs/connectors) so the agent can "
            "book meetings.\n\n![shot](/docs/x.webp)\n\n- Open `Connectors`\n")
    live = _section("cal", body)
    hidden = _section("hidden", "Zorblax quantum widget configuration", section="Zorblax")
    await _import(pool, [live, hidden])
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            await conn.execute("UPDATE public.support_kb_articles SET published=FALSE WHERE slug=$1",
                               PREFIX + "hidden")

    exact = await kb.search_articles(pool, "connect your calendar")
    assert PREFIX + "cal" in [r["slug"] for r in exact]
    hit = next(r for r in exact if r["slug"] == PREFIX + "cal")
    assert set(hit) == {"slug", "title", "section", "snippet", "url"}
    assert len(hit["snippet"]) <= 240 and hit["snippet"]
    for ch in "*#`[]!<>":
        assert ch not in hit["snippet"]
    assert "/docs/x.webp" not in hit["snippet"]

    typo = await kb.search_articles(pool, "calender")
    assert PREFIX + "cal" in [r["slug"] for r in typo]

    assert PREFIX + "hidden" not in [r["slug"] for r in await kb.search_articles(pool, "Zorblax quantum widget")]
    assert await kb.search_articles(pool, "a") == []
    assert len(await kb.search_articles(pool, "calendar", limit=999)) <= 10


@pytest.mark.asyncio
async def test_tenant_context_search_sees_published_only(pool):
    await _import(pool, [_section("t1", "Tenant visible quokka procedure", section="Quokka"),
                         _section("t2", "Hidden wombat procedure", section="Wombat")])
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            await conn.execute("UPDATE public.support_kb_articles SET published=FALSE WHERE slug=$1", PREFIX + "t2")
    tenant = str(uuid4())
    got = [r["slug"] for r in await kb.search_articles(pool, "quokka procedure", tenant_id=tenant)]
    assert PREFIX + "t1" in got
    assert PREFIX + "t2" not in [r["slug"] for r in await kb.search_articles(pool, "wombat procedure", tenant_id=tenant)]

"""Staff help-article management. Imported docs articles are read-only here;
they change only through ``scripts/import_support_articles.py``."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg

from app.core.db_utils import acquire_with_tenant
from app.core.security.platform_admin_guard import (
    require_current_platform_admin,
    require_platform_admin_mutation,
)
from app.domain.services.audit_logger import AuditEvent
from app.domain.services.support import receipts
from app.domain.services.support.actor import SupportActor
from app.domain.services.support.errors import SupportConflict, SupportNotFound
from app.domain.services.support.ticket_service import WriteResult

_COLUMNS = "id, slug, title, section, body_md, category, source, published, url, updated_by, created_at, updated_at"
_EDITABLE = ("title", "body_md", "category", "published")


def _article(row) -> dict:
    return dict(row)


def article_url(slug: str) -> str:
    return f"/support/articles/{slug}"


async def list_articles(pool, actor: SupportActor, *, q: str | None, source: str | None,
                        published: bool | None) -> dict:
    query = (q or "").strip()[:200] or None
    async with acquire_with_tenant(pool, None, user_id=actor.user_id) as conn:
        await require_current_platform_admin(conn, actor.user_id)
        rows = await conn.fetch(
            f"""SELECT {_COLUMNS} FROM support_kb_articles
                WHERE ($1::text IS NULL OR source = $1)
                  AND ($2::boolean IS NULL OR published = $2)
                  AND ($3::text IS NULL OR search_tsv @@ websearch_to_tsquery('english', $3)
                       OR title ILIKE '%' || $3 || '%' OR slug ILIKE '%' || $3 || '%')
                ORDER BY source DESC, slug LIMIT 500""",
            source, published, query)
    return {"items": [_article(row) for row in rows]}


async def _begin(conn, audit, actor: SupportActor, request_id: UUID, operation: str) -> dict | None:
    await audit.lock_transaction(conn)
    await require_platform_admin_mutation(conn, actor.user_id)
    return await receipts.claim(conn, tenant_id=None, request_id=request_id, actor_id=actor.user_id,
                                operation=operation)


async def create_article(pool, audit, actor: SupportActor, *, request_id: UUID, slug: str, title: str,
                         body_md: str, category: str, published: bool) -> WriteResult:
    now = datetime.now(timezone.utc)
    async with acquire_with_tenant(pool, None, user_id=actor.user_id) as conn:
        replay = await _begin(conn, audit, actor, request_id, "staff.article.create")
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO support_kb_articles
                            (slug, title, section, body_md, category, source, url, published, request_id,
                             updated_by, created_at, updated_at)
                        VALUES ($1, $2, '', $3, $4, 'staff', $5, $6, $7, $8, $9, $9) RETURNING {_COLUMNS}""",
                    slug, title, body_md, category, article_url(slug), published, request_id, actor.user_id, now)
        except asyncpg.UniqueViolationError as exc:
            raise SupportConflict("An article with this slug already exists.", code="slug_taken") from exc
        await audit.log_in_transaction(
            conn, event_type=AuditEvent.SUPPORT_ARTICLE_CHANGED, action="support.article.create",
            actor_id=actor.user_id, actor_role=actor.role, resource_type="support_article", resource_id=row["id"],
            after_state={"slug": slug, "category": category, "published": published})
        body = await receipts.save(conn, tenant_id=None, request_id=request_id, actor_id=actor.user_id,
                                   operation="staff.article.create", resource_id=row["id"], status_code=201,
                                   response=_article(row))
    return WriteResult(body, 201)


async def update_article(pool, audit, actor: SupportActor, article_id: UUID, *, request_id: UUID,
                         changes: dict[str, Any]) -> WriteResult:
    now = datetime.now(timezone.utc)
    fields = {name: changes[name] for name in _EDITABLE if name in changes and changes[name] is not None}
    async with acquire_with_tenant(pool, None, user_id=actor.user_id) as conn:
        replay = await _begin(conn, audit, actor, request_id, "staff.article.update")
        if replay:
            return WriteResult(replay["response"], 200, replayed=True)
        current = await conn.fetchrow(f"SELECT {_COLUMNS} FROM support_kb_articles WHERE id = $1 FOR UPDATE",
                                      article_id)
        if current is None:
            raise SupportNotFound("Article not found.")
        if current["source"] != "staff":
            raise SupportConflict("Imported docs articles change only through the docs import.",
                                  code="article_read_only")
        row = current
        if fields:
            assignments = ", ".join(f"{name} = ${index + 2}" for index, name in enumerate(fields))
            row = await conn.fetchrow(
                f"""UPDATE support_kb_articles SET {assignments}, updated_by = ${len(fields) + 2},
                        updated_at = ${len(fields) + 3}
                    WHERE id = $1 AND source = 'staff' RETURNING {_COLUMNS}""",
                article_id, *fields.values(), actor.user_id, now)
            await audit.log_in_transaction(
                conn, event_type=AuditEvent.SUPPORT_ARTICLE_CHANGED, action="support.article.update",
                actor_id=actor.user_id, actor_role=actor.role, resource_type="support_article",
                resource_id=article_id,
                before_state={name: current[name] for name in fields if name != "body_md"},
                after_state={name: value for name, value in fields.items() if name != "body_md"},
                metadata={"body_changed": "body_md" in fields})
        body = await receipts.save(conn, tenant_id=None, request_id=request_id, actor_id=actor.user_id,
                                   operation="staff.article.update", resource_id=article_id, status_code=200,
                                   response=_article(row))
    return WriteResult(body, 200)

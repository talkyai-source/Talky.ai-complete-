"""Platform staff support console (current platform admin + support:manage)."""
from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, Query

from app.api.v1.dependencies import get_audit_logger, get_db_pool
from app.api.v1.endpoints.support import schemas
from app.api.v1.endpoints.support.deps import respond, run, settings_dep, staff_actor
from app.domain.services.audit_logger import AuditLogger
from app.domain.services.support import article_admin, notifications
from app.domain.services.support import staff_service as staff
from app.domain.services.support.actor import SupportActor
from app.domain.services.support.config import SupportSettings
from app.domain.services.support.vocabulary import (
    ArticleSource,
    Category,
    TicketPriority,
    TicketStatus,
)

admin_router = APIRouter()


@admin_router.get("/admin/support/tickets")
async def list_tickets(status: Optional[TicketStatus] = None, priority: Optional[TicketPriority] = None,
                       category: Optional[Category] = None, assignee: Optional[str] = Query(None, max_length=40),
                       tenant_id: Optional[UUID] = None, sla: Optional[Literal["breached", "at_risk"]] = None,
                       q: Optional[str] = Query(None, max_length=100), cursor: Optional[str] = Query(None, max_length=200),
                       actor: SupportActor = Depends(staff_actor), pool: asyncpg.Pool = Depends(get_db_pool)):
    return await run(staff.list_tickets(pool, actor, status=status, priority=priority, category=category,
                                        assignee=assignee, tenant_id=tenant_id, sla=sla, q=q, cursor=cursor))


@admin_router.get("/admin/support/tickets/{ticket_id}")
async def get_ticket(ticket_id: UUID, actor: SupportActor = Depends(staff_actor),
                     pool: asyncpg.Pool = Depends(get_db_pool)):
    return await run(staff.get_ticket(pool, actor, ticket_id))


@admin_router.patch("/admin/support/tickets/{ticket_id}")
async def patch_ticket(ticket_id: UUID, body: schemas.StaffTicketPatch, background: BackgroundTasks,
                       actor: SupportActor = Depends(staff_actor), pool: asyncpg.Pool = Depends(get_db_pool),
                       settings: SupportSettings = Depends(settings_dep),
                       audit: AuditLogger = Depends(get_audit_logger)):
    result = await run(staff.patch_ticket(pool, audit, actor, ticket_id, request_id=body.request_id,
                                          version=body.version, changes=body.changes(), settings=settings))
    if not result.replayed and result.effects.get("resolved"):
        background.add_task(notifications.ticket_resolved, pool, ticket_id, result.effects["tenant_id"], settings)
    return respond(result)


@admin_router.post("/admin/support/tickets/{ticket_id}/messages", status_code=201)
async def staff_message(ticket_id: UUID, body: schemas.StaffMessage, background: BackgroundTasks,
                        actor: SupportActor = Depends(staff_actor), pool: asyncpg.Pool = Depends(get_db_pool),
                        settings: SupportSettings = Depends(settings_dep),
                        audit: AuditLogger = Depends(get_audit_logger)):
    result = await run(staff.add_staff_message(pool, audit, actor, ticket_id, request_id=body.request_id,
                                               body=body.body, internal=body.internal,
                                               attachment_ids=body.attachment_ids))
    if not result.replayed and result.effects.get("public"):
        background.add_task(notifications.staff_replied, pool, ticket_id, result.effects["tenant_id"], settings)
    return respond(result)


@admin_router.get("/admin/support/articles")
async def list_articles(q: Optional[str] = Query(None, max_length=200), source: Optional[ArticleSource] = None,
                        published: Optional[bool] = None, actor: SupportActor = Depends(staff_actor),
                        pool: asyncpg.Pool = Depends(get_db_pool)):
    return await run(article_admin.list_articles(pool, actor, q=q, source=source, published=published))


@admin_router.post("/admin/support/articles", status_code=201)
async def create_article(body: schemas.ArticleCreate, actor: SupportActor = Depends(staff_actor),
                         pool: asyncpg.Pool = Depends(get_db_pool), audit: AuditLogger = Depends(get_audit_logger)):
    return respond(await run(article_admin.create_article(
        pool, audit, actor, request_id=body.request_id, slug=body.slug, title=body.title, body_md=body.body_md,
        category=body.category, published=body.published)))


@admin_router.patch("/admin/support/articles/{article_id}")
async def update_article(article_id: UUID, body: schemas.ArticlePatch, actor: SupportActor = Depends(staff_actor),
                         pool: asyncpg.Pool = Depends(get_db_pool), audit: AuditLogger = Depends(get_audit_logger)):
    changes = body.model_dump(exclude={"request_id"}, exclude_none=True)
    return respond(await run(article_admin.update_article(pool, audit, actor, article_id,
                                                          request_id=body.request_id, changes=changes)))


@admin_router.get("/admin/support/metrics")
async def metrics(days: Literal[7, 30] = 7, actor: SupportActor = Depends(staff_actor),
                  pool: asyncpg.Pool = Depends(get_db_pool)):
    return await run(staff.metrics_summary(pool, actor, days=days))

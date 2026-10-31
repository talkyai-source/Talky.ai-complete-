"""Customer ticket routes. Notifications run after the response, outside the
ticket transaction, so a failed email never undoes a ticket."""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, Query

from app.api.v1.dependencies import get_audit_logger, get_db_pool
from app.api.v1.endpoints.support import schemas
from app.api.v1.endpoints.support.deps import customer_actor, respond, run, settings_dep
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.support import notifications
from app.domain.services.support import ticket_service as tickets
from app.domain.services.support.actor import SupportActor
from app.domain.services.support.config import SupportSettings
from app.domain.services.support.vocabulary import TicketStatus
from app.infrastructure.metrics import support_metrics as metrics

logger = logging.getLogger(__name__)
router = APIRouter()


async def _audit_created(audit: AuditLogger, actor: SupportActor, effects: dict) -> None:
    try:
        await audit.log(AuditEvent.SUPPORT_TICKET_CREATED, action="support.ticket.create", actor_id=actor.user_id,
                        actor_role=actor.role, tenant_id=effects["tenant_id"], resource_type="support_ticket",
                        resource_id=effects["ticket_id"],
                        metadata={"category": effects["category"], "priority": effects["priority"],
                                  "from_conversation": effects["from_conversation"]})
    except Exception as exc:  # the ticket is committed; the audit buffer reports its own failures
        logger.warning("support_ticket_audit_failed error_type=%s", type(exc).__name__)


@router.post("/support/tickets", status_code=201)
async def create_ticket(body: schemas.CreateTicket, background: BackgroundTasks,
                        actor: SupportActor = Depends(customer_actor), pool: asyncpg.Pool = Depends(get_db_pool),
                        settings: SupportSettings = Depends(settings_dep),
                        audit: AuditLogger = Depends(get_audit_logger)):
    result = await run(tickets.create_ticket(
        pool, actor, request_id=body.request_id, subject=body.subject, category=body.category,
        description=body.description, conversation_id=body.conversation_id,
        related_campaign_id=body.related_campaign_id, related_call_id=body.related_call_id,
        attachment_ids=body.attachment_ids, settings=settings))
    if not result.replayed:
        effects = result.effects
        metrics.ticket_created(effects["category"], effects["priority"])
        await _audit_created(audit, actor, effects)
        background.add_task(notifications.ticket_created, pool, effects["ticket_id"], effects["tenant_id"],
                            effects["priority"], settings)
    return respond(result)


@router.get("/support/tickets")
async def list_tickets(status: Optional[TicketStatus] = None, scope: schemas.TicketScope = "mine",
                       cursor: Optional[str] = Query(None, max_length=200),
                       actor: SupportActor = Depends(customer_actor), pool: asyncpg.Pool = Depends(get_db_pool)):
    return await run(tickets.list_tickets(pool, actor, scope=scope, status=status, cursor=cursor))


@router.get("/support/unread")
async def unread(actor: SupportActor = Depends(customer_actor), pool: asyncpg.Pool = Depends(get_db_pool)):
    return await run(tickets.unread(pool, actor))


@router.get("/support/tickets/{ticket_id}")
async def get_ticket(ticket_id: UUID, actor: SupportActor = Depends(customer_actor),
                     pool: asyncpg.Pool = Depends(get_db_pool)):
    return await run(tickets.get_ticket(pool, actor, ticket_id))


@router.post("/support/tickets/{ticket_id}/messages", status_code=201)
async def reply(ticket_id: UUID, body: schemas.TicketMessage, actor: SupportActor = Depends(customer_actor),
                pool: asyncpg.Pool = Depends(get_db_pool)):
    return respond(await run(tickets.add_customer_message(pool, actor, ticket_id, request_id=body.request_id,
                                                          body=body.body, attachment_ids=body.attachment_ids)))


@router.post("/support/tickets/{ticket_id}/resolve")
async def resolve(ticket_id: UUID, body: schemas.RequestOnly, actor: SupportActor = Depends(customer_actor),
                  pool: asyncpg.Pool = Depends(get_db_pool)):
    return respond(await run(tickets.resolve_ticket(pool, actor, ticket_id, request_id=body.request_id)))


@router.post("/support/tickets/{ticket_id}/reopen")
async def reopen(ticket_id: UUID, body: schemas.RequestOnly, actor: SupportActor = Depends(customer_actor),
                 pool: asyncpg.Pool = Depends(get_db_pool)):
    return respond(await run(tickets.reopen_ticket(pool, actor, ticket_id, request_id=body.request_id)))


@router.post("/support/tickets/{ticket_id}/csat")
async def rate(ticket_id: UUID, body: schemas.Csat, actor: SupportActor = Depends(customer_actor),
               pool: asyncpg.Pool = Depends(get_db_pool)):
    return respond(await run(tickets.rate_ticket(pool, actor, ticket_id, request_id=body.request_id,
                                                 score=body.score, comment=body.comment)))

"""Customer support routes: config, help-article search, bot conversations."""
from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.api.v1.endpoints.auth import limiter
from app.api.v1.endpoints.support import schemas
from app.api.v1.endpoints.support.deps import (
    build_actor,
    customer_actor,
    effective_permissions,
    respond,
    run,
    settings_dep,
    user_rate_key,
)
from app.domain.services.support import conversation_service as conversations
from app.domain.services.support.actor import SupportActor
from app.domain.services.support.articles import search_articles
from app.domain.services.support.config import SupportSettings, message_rate_limit
from app.domain.services.support.vocabulary import (
    ATTACHMENT_TYPES,
    CATEGORIES,
    MAX_ATTACHMENTS_PER_MESSAGE,
)

router = APIRouter()


def _redis():
    from app.core.container import get_container

    container = get_container()
    return container.redis if container.is_initialized else None


@router.get("/support/config")
async def support_config(user: CurrentUser = Depends(get_current_user),
                         settings: SupportSettings = Depends(settings_dep)):
    """Sign-in only. Tells the app whether to show support and what the user may do."""
    if not settings.enabled_for_tenant(user.tenant_id):
        return {"enabled": False, "bot_enabled": False, "permissions": {"report": False, "read_team": False},
                "categories": [], "suggestions": [], "attachments": {"max_mb": 0, "max_per_message": 0, "types": []}}
    actor = build_actor(user, await effective_permissions(user))
    return {
        "enabled": True,
        "bot_enabled": settings.bot_enabled and actor.can_report,
        "permissions": {"report": actor.can_report, "read_team": actor.can_read_team},
        "categories": [{"id": category, "label": label} for category, label in CATEGORIES],
        "suggestions": list(conversations.SUGGESTIONS),
        "attachments": {"max_mb": settings.attachment_max_bytes // (1024 * 1024),
                        "max_per_message": MAX_ATTACHMENTS_PER_MESSAGE,
                        "types": [extension for extension, _ in ATTACHMENT_TYPES]},
    }


@router.get("/support/articles/search")
async def search_help(q: str = Query("", max_length=200), actor: SupportActor = Depends(customer_actor),
                      pool: asyncpg.Pool = Depends(get_db_pool)):
    items = await run(search_articles(pool, q, limit=8, tenant_id=actor.tenant_id))
    return {"items": [{"slug": i["slug"], "title": i["title"], "section": i["section"], "snippet": i["snippet"],
                       "url": i["url"]} for i in items]}


@router.post("/support/conversations", status_code=201)
async def start_conversation(body: schemas.StartConversation, actor: SupportActor = Depends(customer_actor),
                             pool: asyncpg.Pool = Depends(get_db_pool)):
    result = await run(conversations.start_conversation(
        pool, actor, request_id=body.request_id, page_context=body.page_context.model_dump(mode="json")))
    return respond(result)


@router.get("/support/conversations/{conversation_id}")
async def get_conversation(conversation_id: UUID, actor: SupportActor = Depends(customer_actor),
                           pool: asyncpg.Pool = Depends(get_db_pool)):
    return await run(conversations.get_conversation(pool, actor, conversation_id))


@router.post("/support/conversations/{conversation_id}/messages")
@limiter.limit(message_rate_limit, key_func=user_rate_key)
async def send_message(request: Request, response: Response, conversation_id: UUID,
                       body: schemas.ConversationMessage, actor: SupportActor = Depends(customer_actor),
                       pool: asyncpg.Pool = Depends(get_db_pool),
                       settings: SupportSettings = Depends(settings_dep)):
    result = await run(conversations.post_message(pool, _redis(), actor, conversation_id,
                                                  request_id=body.request_id, content=body.content,
                                                  settings=settings))
    return respond(result)


@router.post("/support/conversations/{conversation_id}/feedback")
async def send_feedback(conversation_id: UUID, body: schemas.Feedback, actor: SupportActor = Depends(customer_actor),
                        pool: asyncpg.Pool = Depends(get_db_pool)):
    result = await run(conversations.feedback(pool, actor, conversation_id, request_id=body.request_id,
                                              solved=body.solved))
    return respond(result)


@router.post("/support/conversations/{conversation_id}/escalate")
async def escalate(conversation_id: UUID, body: schemas.RequestOnly, actor: SupportActor = Depends(customer_actor),
                   pool: asyncpg.Pool = Depends(get_db_pool)):
    return respond(await run(conversations.escalate(pool, actor, conversation_id, request_id=body.request_id)))

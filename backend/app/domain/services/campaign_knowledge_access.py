"""Bounded, tenant-scoped authorization leases for campaign knowledge.

Campaign direction selects the permission boundary: outbound knowledge uses
``campaigns:*`` while inbound knowledge uses ``inbound:*``. A lookup followed
by a later write is unsafe because conversion can commit between them. This
module holds the same transaction advisory key as conversion, then re-reads
direction and live grants on one tenant/user-scoped connection.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator
from uuid import UUID

from app.core.db_utils import acquire_with_tenant
from app.domain.services.campaign_direction_guard import (
    CAMPAIGN_DIRECTION_LOCK_KEY_SQL,
)

logger = logging.getLogger(__name__)

CAMPAIGNS_READ_PERMISSION = "campaigns:read"
CAMPAIGNS_UPDATE_PERMISSION = "campaigns:update"
CAMPAIGNS_ADMIN_PERMISSION = "campaigns:admin"
INBOUND_READ_PERMISSION = "inbound:read"
INBOUND_MANAGE_PERMISSION = "inbound:manage"
PLATFORM_ADMIN_PERMISSION = "platform:admin"

_DEFAULT_WAIT_TIMEOUT_SECONDS = 0.5
_DEFAULT_POLL_INTERVAL_SECONDS = 0.01
_READ_LOCK_SQL = (
    "SELECT pg_try_advisory_xact_lock_shared("
    f"{CAMPAIGN_DIRECTION_LOCK_KEY_SQL})"
)
_MUTATION_LOCK_SQL = (
    "SELECT pg_try_advisory_xact_lock("
    f"{CAMPAIGN_DIRECTION_LOCK_KEY_SQL})"
)

# One statement and one connection resolve current role grants plus unexpired
# direct grants. There is deliberately no Python role-default fallback here:
# an empty/revoked grant set must remain a denial at execution time.
_EFFECTIVE_PERMISSIONS_SQL = """
SELECT DISTINCT permission_name AS name
FROM (
    SELECT p.name AS permission_name
    FROM tenant_users tu
    JOIN roles r ON r.id = tu.role_id
    JOIN role_permissions rp ON rp.role_id = r.id
    JOIN permissions p ON p.id = rp.permission_id
    WHERE tu.user_id = $1
      AND tu.tenant_id = $2
      AND tu.status = 'active'

    UNION

    SELECT p.name AS permission_name
    FROM user_permissions up
    JOIN permissions p ON p.id = up.permission_id
    WHERE up.user_id = $1
      AND (up.tenant_id IS NULL OR up.tenant_id = $2)
      AND (up.expires_at IS NULL OR up.expires_at > NOW())
) effective
"""


class CampaignKnowledgeAccessError(RuntimeError):
    """Base class for stable, caller-mappable knowledge access failures."""

    code = "knowledge_access_error"


class CampaignKnowledgeNotFound(CampaignKnowledgeAccessError):
    code = "campaign_not_found"

    def __init__(self, campaign_id: str) -> None:
        self.campaign_id = str(campaign_id)
        super().__init__("campaign not found")


class CampaignKnowledgeAccessDenied(CampaignKnowledgeAccessError):
    code = "permission_denied"

    def __init__(self, required_permission: str | None) -> None:
        self.required_permission = required_permission
        super().__init__("campaign knowledge permission denied")


class CampaignKnowledgeAccessBusy(CampaignKnowledgeAccessError):
    code = "knowledge_access_busy"

    def __init__(self, campaign_id: str) -> None:
        self.campaign_id = str(campaign_id)
        super().__init__("campaign knowledge is busy; retry")


class CampaignKnowledgeAccessUnavailable(CampaignKnowledgeAccessError):
    code = "authorization_unavailable"

    def __init__(self) -> None:
        super().__init__("campaign knowledge authorization is unavailable")


@dataclass(frozen=True, slots=True)
class CampaignKnowledgeAccessLease:
    """Authorization snapshot kept valid by the live advisory transaction."""

    conn: Any
    tenant_id: str
    campaign_id: str
    actor_user_id: str
    direction: str
    required_permission: str
    mutate: bool


def required_campaign_knowledge_permission(direction: str, *, mutate: bool) -> str:
    normalized = str(direction or "").strip().lower()
    if normalized == "inbound":
        return INBOUND_MANAGE_PERMISSION if mutate else INBOUND_READ_PERMISSION
    if normalized == "outbound":
        return CAMPAIGNS_UPDATE_PERMISSION if mutate else CAMPAIGNS_READ_PERMISSION
    raise CampaignKnowledgeAccessUnavailable()


def _has_permission(permission_names: set[str], required: str) -> bool:
    if required in permission_names or PLATFORM_ADMIN_PERMISSION in permission_names:
        return True
    # Match ``app.core.security.rbac.check_permission`` exactly. Campaigns has
    # a declared resource-admin permission; inbound intentionally does not.
    # Deriving arbitrary ``<resource>:admin`` names here could turn a stale or
    # custom catalogue row into an authority the canonical RBAC enum rejects.
    return (
        required in {CAMPAIGNS_READ_PERMISSION, CAMPAIGNS_UPDATE_PERMISSION}
        and CAMPAIGNS_ADMIN_PERMISSION in permission_names
    )


async def _current_permission_names(
    conn: Any,
    *,
    actor_user_id: str,
    tenant_id: str,
) -> set[str]:
    rows = await conn.fetch(
        _EFFECTIVE_PERMISSIONS_SQL,
        actor_user_id,
        tenant_id,
    )
    names: set[str] = set()
    for row in rows:
        name = row.get("name") if hasattr(row, "get") else row["name"]
        if name:
            names.add(str(name).strip())
    return names


async def _acquire_bounded_direction_lock(
    conn: Any,
    *,
    campaign_id: str,
    mutate: bool,
    deadline: float,
    poll_interval_seconds: float,
) -> None:
    sql = _MUTATION_LOCK_SQL if mutate else _READ_LOCK_SQL
    loop = asyncio.get_running_loop()
    while True:
        if await conn.fetchval(sql, campaign_id):
            return
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise CampaignKnowledgeAccessBusy(campaign_id)
        await asyncio.sleep(min(poll_interval_seconds, remaining))


@asynccontextmanager
async def campaign_knowledge_access_lease(
    pool: Any,
    *,
    tenant_id: str,
    campaign_id: str,
    actor_user_id: str,
    mutate: bool,
    wait_timeout_seconds: float = _DEFAULT_WAIT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
) -> AsyncIterator[CampaignKnowledgeAccessLease]:
    """Yield one authorized connection under a bounded direction lease.

    The deadline includes pool acquisition and advisory-lock retry. Callers
    must perform all protected database work through ``lease.conn``. Opening a
    second connection inside the context would reintroduce the checked/write
    race this primitive exists to close.
    """

    tenant = str(tenant_id or "").strip()
    campaign_input = str(campaign_id or "").strip()
    actor = str(actor_user_id or "").strip()
    if not actor:
        raise CampaignKnowledgeAccessDenied(None)
    if not tenant:
        raise CampaignKnowledgeAccessUnavailable()
    try:
        # One UUID spelling means the application lock and the database
        # direction trigger always hash the identical advisory key.
        campaign = str(UUID(campaign_input))
    except (ValueError, AttributeError):
        raise CampaignKnowledgeNotFound(campaign_input) from None

    timeout = max(0.0, float(wait_timeout_seconds))
    poll_interval = max(0.001, float(poll_interval_seconds))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    body_failed = False

    try:
        async with acquire_with_tenant(
            pool,
            tenant,
            user_id=actor,
            timeout=timeout,
        ) as conn:
            await _acquire_bounded_direction_lock(
                conn,
                campaign_id=campaign,
                mutate=bool(mutate),
                deadline=deadline,
                poll_interval_seconds=poll_interval,
            )
            direction = await conn.fetchval(
                "SELECT direction FROM campaigns "
                "WHERE id = $1 AND tenant_id = $2",
                campaign,
                tenant,
            )
            if direction is None:
                raise CampaignKnowledgeNotFound(campaign)
            normalized_direction = str(direction).strip().lower()
            required = required_campaign_knowledge_permission(
                normalized_direction,
                mutate=bool(mutate),
            )
            permission_names = await _current_permission_names(
                conn,
                actor_user_id=actor,
                tenant_id=tenant,
            )
            if not _has_permission(permission_names, required):
                raise CampaignKnowledgeAccessDenied(required)

            try:
                yield CampaignKnowledgeAccessLease(
                    conn=conn,
                    tenant_id=tenant,
                    campaign_id=campaign,
                    actor_user_id=actor,
                    direction=normalized_direction,
                    required_permission=required,
                    mutate=bool(mutate),
                )
            except BaseException:
                # The access primitive owns setup/commit classification, not
                # endpoint/tool semantics. Preserve caller errors verbatim.
                body_failed = True
                raise
    except CampaignKnowledgeAccessError:
        raise
    except TimeoutError as exc:
        if body_failed:
            raise
        raise CampaignKnowledgeAccessBusy(campaign) from exc
    except Exception as exc:  # noqa: BLE001 - authorization fails closed
        if body_failed:
            raise
        logger.error(
            "campaign_knowledge_access_unavailable tenant=%s campaign=%s "
            "actor=%s err_type=%s",
            tenant[:8],
            campaign[:12],
            actor[:8],
            type(exc).__name__,
        )
        raise CampaignKnowledgeAccessUnavailable() from exc


__all__ = [
    "CAMPAIGN_DIRECTION_LOCK_KEY_SQL",
    "CampaignKnowledgeAccessLease",
    "CampaignKnowledgeAccessError",
    "CampaignKnowledgeNotFound",
    "CampaignKnowledgeAccessDenied",
    "CampaignKnowledgeAccessBusy",
    "CampaignKnowledgeAccessUnavailable",
    "campaign_knowledge_access_lease",
    "required_campaign_knowledge_permission",
]

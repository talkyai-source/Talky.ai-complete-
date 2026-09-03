"""
RBAC (Role-Based Access Control) Core Module

Implements NIST RBAC standard (ANSI/INCITS 359-2004) with hierarchical roles.

Official References (verified March 2026):
  NIST RBAC Standard:
    https://csrc.nist.gov/projects/role-based-access-control
  OWASP Access Control Cheat Sheet:
    https://cheatsheetseries.owasp.org/cheatsheets/Access_Control_Cheat_Sheet.html
  OWASP Authorization Cheat Sheet:
    https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html

Architecture:
  - Role hierarchy: platform_admin > partner_admin > tenant_admin >
      campaign_manager > user > agent > billing_user > readonly
      (agent and billing_user are siblings placed below user - see UserRole)
  - Permissions are fine-grained: resource:action (e.g., "campaigns:create")
  - Users can have different roles in different tenants (tenant_scoped)
  - Platform admins bypass tenant checks (global scope)
  - Direct user permissions can override role-based (additive only)

Key Components:
  - UserRole enum: System role definitions with hierarchy
  - Permission enum: Granular permission constants
  - get_user_permissions(): Aggregate user permissions from roles + direct grants
  - check_permission(): Verify if user has required permission
  - require_role(): FastAPI dependency factory for role-based access
  - require_permission(): FastAPI dependency factory for permission-based access
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Set, List, Dict, Any
from functools import wraps

from fastapi import Depends, HTTPException, status, Request

from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client

if TYPE_CHECKING:
    from app.api.v1.dependencies import CurrentUser

logger = logging.getLogger(__name__)


# =============================================================================
# Role Definitions (NIST RBAC Core)
# =============================================================================

class UserRole(str, enum.Enum):
    """
    System role definitions with hierarchy.

    Hierarchy (highest to lowest):
        PLATFORM_ADMIN   - Full system access, all tenants
        PARTNER_ADMIN    - Multi-tenant access (reseller/partner view)
        TENANT_ADMIN     - Full access within single tenant
        CAMPAIGN_MANAGER - Owns campaigns end-to-end inside one tenant
        USER             - Standard user (limited admin)
        AGENT            - Human call operator (works calls, configures nothing)
        BILLING_USER     - Finance contact (billing only, never calls)
        READONLY         - View-only access

    The six roles named in goals.md §12's validation matrix are
    TENANT_ADMIN, CAMPAIGN_MANAGER, AGENT (alias "operator"), BILLING_USER,
    READONLY and PARTNER_ADMIN.  They must all exist as distinct roles: with
    three of them missing, ``normalize_role()`` downgraded their names to
    READONLY and a tenant seeded as e.g. "campaign_manager" was validated
    against the WRONG permission set while appearing to pass.

    AGENT is the human operator working the dialer, NOT the AI voice agent
    (which is never a principal and holds no role).

    AGENT and BILLING_USER are siblings, not a ranked pair: neither is meant
    to inherit the other's access.  The single ``level`` ladder cannot express
    that, so both are placed BELOW ``USER`` — the property that matters is
    that no ``require_role(UserRole.USER)`` gate admits either of them.  For
    these two the permission set, not the level, is the contract.
    """
    PLATFORM_ADMIN = "platform_admin"
    PARTNER_ADMIN = "partner_admin"
    TENANT_ADMIN = "tenant_admin"
    CAMPAIGN_MANAGER = "campaign_manager"
    USER = "user"
    AGENT = "agent"
    BILLING_USER = "billing_user"
    READONLY = "readonly"

    @property
    def level(self) -> int:
        """Return the hierarchy level (higher = more access)."""
        levels = {
            UserRole.PLATFORM_ADMIN: 100,
            UserRole.PARTNER_ADMIN: 80,
            UserRole.TENANT_ADMIN: 60,
            UserRole.CAMPAIGN_MANAGER: 50,
            UserRole.USER: 40,
            UserRole.AGENT: 35,
            UserRole.BILLING_USER: 30,
            UserRole.READONLY: 20,
        }
        return levels[self]

    def can_access(self, required_role: UserRole) -> bool:
        """
        Check if this role can access resources requiring `required_role`.

        Example: platform_admin.can_access(tenant_admin) -> True
                 user.can_access(tenant_admin) -> False
        """
        return self.level >= required_role.level


# Role aliases for backward compatibility.
#   admin/owner/super_admin - legacy names still present in user_profiles.role
#   operator                - goals.md §12 spells the role "Agent/operator";
#                             both spellings must land on the same role.
ROLE_ALIASES = {
    "admin": UserRole.TENANT_ADMIN,
    "owner": UserRole.TENANT_ADMIN,
    "super_admin": UserRole.PLATFORM_ADMIN,
    "operator": UserRole.AGENT,
}


class UnknownRoleError(ValueError):
    """Raised by ``normalize_role(..., strict=True)`` for an unrecognised name.

    Subclasses ``ValueError`` so any call site that already guards
    ``UserRole(value)`` with ``except ValueError`` keeps behaving as before.
    """

    def __init__(self, role_name: str):
        self.role_name = role_name
        super().__init__(f"Unrecognised role name {role_name!r}")


# Unknown role names are logged ONCE per distinct value per process: the
# realistic input space is a handful of strings from user_profiles.role, and a
# per-request ERROR at prod volume would be its own incident.  The cache is
# bounded so an attacker-controlled value cannot grow it without limit; a
# deployment that has produced this many distinct unknown role names has a
# problem worth a noisy log.
_UNKNOWN_ROLE_LOG_CACHE_MAX = 128
_unknown_roles_logged: set = set()


def reset_unknown_role_log_cache() -> None:
    """Drop the unknown-role log de-duplication cache (tests / ops)."""
    _unknown_roles_logged.clear()


def _log_unknown_role(role_name: str) -> None:
    # repr() escapes control characters and newlines, so a hostile role string
    # cannot forge extra log lines.  Truncated: the value is diagnostic only.
    shown = repr(role_name)[:80]
    already_seen = role_name in _unknown_roles_logged
    if not already_seen and len(_unknown_roles_logged) < _UNKNOWN_ROLE_LOG_CACHE_MAX:
        _unknown_roles_logged.add(role_name)
    if already_seen:
        logger.debug(
            "RBAC_UNKNOWN_ROLE: %s (repeat) still downgraded to readonly", shown
        )
        return
    logger.error(
        "RBAC_UNKNOWN_ROLE: role name %s is not a known role or alias - this "
        "principal is being downgraded to '%s'. A role that exists in a seed "
        "script, a UI picker or a database row but not in UserRole is a SILENT "
        "privilege change: the account gets read-only access while everything "
        "upstream believes it has the named role. Add the role to UserRole and "
        "ROLE_DEFAULT_PERMISSIONS, or fix the caller. Logged once per distinct "
        "value per process.",
        shown,
        UserRole.READONLY.value,
    )


def normalize_role(role_name: str, *, strict: bool = False) -> UserRole:
    """
    Normalize a role string to UserRole enum.

    Handles backward compatibility with old role names (see ``ROLE_ALIASES``).

    Args:
        role_name: the role string from a JWT claim, ``user_profiles.role`` or
            the ``roles`` table.
        strict: raise ``UnknownRoleError`` instead of downgrading. Opt-in, for
            call sites that WRITE a role (role assignment, seeding, admin
            tooling) where a typo must be a 4xx, not a quietly weaker account.

    Default (non-strict) behaviour is unchanged: an unrecognised name still
    resolves to ``READONLY`` so no authentication path can start raising and
    lock users out. What changed is that the downgrade is now logged at ERROR
    with the offending value, once per distinct value per process.
    """
    # Check aliases first
    if role_name in ROLE_ALIASES:
        return ROLE_ALIASES[role_name]

    # Try to parse as enum
    try:
        return UserRole(role_name)
    except ValueError:
        pass

    if strict:
        raise UnknownRoleError(role_name)

    # Default to readonly for unknown roles (fail-safe) - but never silently.
    _log_unknown_role(role_name)
    return UserRole.READONLY


# =============================================================================
# Permission Definitions
# =============================================================================

class Permission(str, enum.Enum):
    """
    Granular permissions in resource:action format.

    Resources: campaigns, users, tenants, billing, calls, recordings, connectors,
        analytics, platform
    Actions: create, read, update, delete, download, admin, manage, export
    """
    # Campaign permissions
    CAMPAIGNS_CREATE = "campaigns:create"
    CAMPAIGNS_READ = "campaigns:read"
    CAMPAIGNS_UPDATE = "campaigns:update"
    CAMPAIGNS_DELETE = "campaigns:delete"
    CAMPAIGNS_ADMIN = "campaigns:admin"

    # Inbound routing permissions. Route/DID/control mutations are separated
    # from ordinary campaign editing because they change public call ingress.
    INBOUND_READ = "inbound:read"
    INBOUND_MANAGE = "inbound:manage"
    INBOUND_ASSIGN = "inbound:assign"
    INBOUND_CONTROLS = "inbound:controls"

    # User permissions
    USERS_CREATE = "users:create"
    USERS_READ = "users:read"
    USERS_UPDATE = "users:update"
    USERS_DELETE = "users:delete"
    USERS_MANAGE = "users:manage"

    # Tenant permissions
    TENANTS_READ = "tenants:read"
    TENANTS_UPDATE = "tenants:update"
    TENANTS_ADMIN = "tenants:admin"

    # Billing permissions
    BILLING_READ = "billing:read"
    BILLING_UPDATE = "billing:update"
    BILLING_ADMIN = "billing:admin"

    # Call permissions
    CALLS_CREATE = "calls:create"
    CALLS_READ = "calls:read"
    CALLS_DELETE = "calls:delete"

    # Recording permissions are deliberately independent from calls:* so
    # playback, export, and irreversible deletion can be revoked separately.
    RECORDINGS_READ = "recordings:read"
    RECORDINGS_DOWNLOAD = "recordings:download"
    RECORDINGS_DELETE = "recordings:delete"

    # Connector permissions
    CONNECTORS_CREATE = "connectors:create"
    CONNECTORS_READ = "connectors:read"
    CONNECTORS_UPDATE = "connectors:update"
    CONNECTORS_DELETE = "connectors:delete"

    # Analytics permissions
    ANALYTICS_READ = "analytics:read"
    ANALYTICS_EXPORT = "analytics:export"

    # Platform admin permissions (global scope)
    PLATFORM_ADMIN = "platform:admin"
    PLATFORM_TENANTS_MANAGE = "platform:tenants:manage"
    PLATFORM_USERS_MANAGE = "platform:users:manage"
    PLATFORM_SETTINGS_MANAGE = "platform:settings:manage"


# =============================================================================
# Role-Permission Mappings
# =============================================================================

# Default permissions granted to each role
# These are used as fallback if database lookup fails
ROLE_DEFAULT_PERMISSIONS: Dict[UserRole, Set[Permission]] = {
    UserRole.READONLY: {
        Permission.CAMPAIGNS_READ,
        Permission.INBOUND_READ,
        Permission.CALLS_READ,
        Permission.RECORDINGS_READ,
        Permission.ANALYTICS_READ,
        Permission.ANALYTICS_EXPORT,
        Permission.TENANTS_READ,
        Permission.CONNECTORS_READ,
    },
    UserRole.USER: {
        Permission.CAMPAIGNS_CREATE,
        Permission.CAMPAIGNS_READ,
        Permission.CAMPAIGNS_UPDATE,
        Permission.CAMPAIGNS_DELETE,
        Permission.INBOUND_READ,
        Permission.CALLS_CREATE,
        Permission.CALLS_READ,
        Permission.RECORDINGS_READ,
        Permission.RECORDINGS_DOWNLOAD,
        Permission.CONNECTORS_CREATE,
        Permission.CONNECTORS_READ,
        Permission.CONNECTORS_UPDATE,
        Permission.CONNECTORS_DELETE,
        Permission.ANALYTICS_READ,
        Permission.ANALYTICS_EXPORT,
        Permission.TENANTS_READ,
        Permission.USERS_READ,
    },
    UserRole.AGENT: {
        # goals.md §12 "Agent/operator": a human working live calls.
        # Grants: place and see calls (calls:create/read), see the campaign and
        # script they are dialling (campaigns:read), see which inbound route
        # they answer for (inbound:read), listen back to a call in-app
        # (recordings:read), see their own numbers (analytics:read) and their
        # own tenant's name (tenants:read).
        # Denied on purpose: billing (§12 "unauthorized roles cannot" see it)
        # and tenant administration - the brief's two hard NOTs; plus every
        # campaign/inbound mutation (an operator does not reconfigure what they
        # dial), calls:delete, users:*, connectors:* (credentials), and the two
        # bulk-PII exports recordings:download / analytics:export.
        Permission.CAMPAIGNS_READ,
        Permission.INBOUND_READ,
        Permission.CALLS_CREATE,
        Permission.CALLS_READ,
        Permission.RECORDINGS_READ,
        Permission.ANALYTICS_READ,
        Permission.TENANTS_READ,
    },
    UserRole.BILLING_USER: {
        # goals.md §12 "Billing user" / "Confirm billing administrators can see
        # billing while unauthorized roles cannot".
        # Grants: read the subscription, invoices, payments and minute balance
        # (billing:read), and act on them - checkout, portal, top-up
        # (billing:update, the permission billing.py and billing_topups.py
        # actually gate on); usage figures to reconcile an invoice against
        # (analytics:read + analytics:export); tenants:read for the plan/quota
        # on the tenant record.
        # Denied on purpose: calls:create and calls:delete - a finance contact
        # must never place or hang up a call (explicit brief); calls:read and
        # recordings:* too, because call content is customer PII and billing
        # reconciliation only needs aggregate minutes; billing:admin, which
        # would wildcard-grant every billing action including subscription
        # cancellation (billing.py:314) and belongs to the tenant owner;
        # campaigns, connectors, users, inbound and tenant administration.
        Permission.BILLING_READ,
        Permission.BILLING_UPDATE,
        Permission.ANALYTICS_READ,
        Permission.ANALYTICS_EXPORT,
        Permission.TENANTS_READ,
    },
    UserRole.CAMPAIGN_MANAGER: {
        # goals.md §12 "Campaign manager": between USER and TENANT_ADMIN.
        # Exactly USER's set plus two grants:
        #   campaigns:admin  - owns the campaign resource end-to-end (via
        #                      check_permission's <resource>:admin wildcard),
        #                      including other people's campaigns in the tenant,
        #                      which a plain USER must not touch.
        #   inbound:assign   - can point an ALREADY-PROVISIONED number at one of
        #                      its campaigns.
        # Denied on purpose - this is the line to tenant_admin: inbound:manage
        # and inbound:controls (DID/route changes and live ingress controls
        # change what the public reaches), users:* beyond read (no inviting or
        # promoting people), tenants:update/tenants:admin (no tenant settings),
        # billing:* (no plan or payment access), and the two irreversible
        # deletions calls:delete and recordings:delete (retention is an admin
        # decision).
        Permission.CAMPAIGNS_CREATE,
        Permission.CAMPAIGNS_READ,
        Permission.CAMPAIGNS_UPDATE,
        Permission.CAMPAIGNS_DELETE,
        Permission.CAMPAIGNS_ADMIN,
        Permission.INBOUND_READ,
        Permission.INBOUND_ASSIGN,
        Permission.CALLS_CREATE,
        Permission.CALLS_READ,
        Permission.RECORDINGS_READ,
        Permission.RECORDINGS_DOWNLOAD,
        Permission.CONNECTORS_CREATE,
        Permission.CONNECTORS_READ,
        Permission.CONNECTORS_UPDATE,
        Permission.CONNECTORS_DELETE,
        Permission.ANALYTICS_READ,
        Permission.ANALYTICS_EXPORT,
        Permission.TENANTS_READ,
        Permission.USERS_READ,
    },
    UserRole.TENANT_ADMIN: {
        # All tenant-scoped permissions
        Permission.CAMPAIGNS_CREATE,
        Permission.CAMPAIGNS_READ,
        Permission.CAMPAIGNS_UPDATE,
        Permission.CAMPAIGNS_DELETE,
        Permission.CAMPAIGNS_ADMIN,
        Permission.INBOUND_READ,
        Permission.INBOUND_MANAGE,
        Permission.INBOUND_ASSIGN,
        Permission.INBOUND_CONTROLS,
        Permission.USERS_CREATE,
        Permission.USERS_READ,
        Permission.USERS_UPDATE,
        Permission.USERS_DELETE,
        Permission.USERS_MANAGE,
        Permission.TENANTS_READ,
        Permission.TENANTS_UPDATE,
        Permission.TENANTS_ADMIN,
        Permission.BILLING_READ,
        Permission.BILLING_UPDATE,
        Permission.BILLING_ADMIN,
        Permission.CALLS_CREATE,
        Permission.CALLS_READ,
        Permission.CALLS_DELETE,
        Permission.RECORDINGS_READ,
        Permission.RECORDINGS_DOWNLOAD,
        Permission.RECORDINGS_DELETE,
        Permission.CONNECTORS_CREATE,
        Permission.CONNECTORS_READ,
        Permission.CONNECTORS_UPDATE,
        Permission.CONNECTORS_DELETE,
        Permission.ANALYTICS_READ,
        Permission.ANALYTICS_EXPORT,
    },
    UserRole.PARTNER_ADMIN: {
        # Tenant admin + cross-tenant read + some platform permissions
        Permission.CAMPAIGNS_CREATE,
        Permission.CAMPAIGNS_READ,
        Permission.CAMPAIGNS_UPDATE,
        Permission.CAMPAIGNS_DELETE,
        Permission.CAMPAIGNS_ADMIN,
        Permission.INBOUND_READ,
        Permission.INBOUND_MANAGE,
        Permission.INBOUND_ASSIGN,
        Permission.INBOUND_CONTROLS,
        Permission.USERS_CREATE,
        Permission.USERS_READ,
        Permission.USERS_UPDATE,
        Permission.USERS_DELETE,
        Permission.USERS_MANAGE,
        Permission.TENANTS_READ,
        Permission.TENANTS_UPDATE,
        Permission.TENANTS_ADMIN,
        Permission.BILLING_READ,
        Permission.BILLING_UPDATE,
        Permission.BILLING_ADMIN,
        Permission.CALLS_CREATE,
        Permission.CALLS_READ,
        Permission.CALLS_DELETE,
        Permission.RECORDINGS_READ,
        Permission.RECORDINGS_DOWNLOAD,
        Permission.RECORDINGS_DELETE,
        Permission.CONNECTORS_CREATE,
        Permission.CONNECTORS_READ,
        Permission.CONNECTORS_UPDATE,
        Permission.CONNECTORS_DELETE,
        Permission.ANALYTICS_READ,
        Permission.ANALYTICS_EXPORT,
    },
    UserRole.PLATFORM_ADMIN: {
        # All permissions including platform:*
        Permission.CAMPAIGNS_CREATE,
        Permission.CAMPAIGNS_READ,
        Permission.CAMPAIGNS_UPDATE,
        Permission.CAMPAIGNS_DELETE,
        Permission.CAMPAIGNS_ADMIN,
        Permission.INBOUND_READ,
        Permission.INBOUND_MANAGE,
        Permission.INBOUND_ASSIGN,
        Permission.INBOUND_CONTROLS,
        Permission.USERS_CREATE,
        Permission.USERS_READ,
        Permission.USERS_UPDATE,
        Permission.USERS_DELETE,
        Permission.USERS_MANAGE,
        Permission.TENANTS_READ,
        Permission.TENANTS_UPDATE,
        Permission.TENANTS_ADMIN,
        Permission.BILLING_READ,
        Permission.BILLING_UPDATE,
        Permission.BILLING_ADMIN,
        Permission.CALLS_CREATE,
        Permission.CALLS_READ,
        Permission.CALLS_DELETE,
        Permission.RECORDINGS_READ,
        Permission.RECORDINGS_DOWNLOAD,
        Permission.RECORDINGS_DELETE,
        Permission.CONNECTORS_CREATE,
        Permission.CONNECTORS_READ,
        Permission.CONNECTORS_UPDATE,
        Permission.CONNECTORS_DELETE,
        Permission.ANALYTICS_READ,
        Permission.ANALYTICS_EXPORT,
        Permission.PLATFORM_ADMIN,
        Permission.PLATFORM_TENANTS_MANAGE,
        Permission.PLATFORM_USERS_MANAGE,
        Permission.PLATFORM_SETTINGS_MANAGE,
    },
}


# =============================================================================
# Permission Aggregation
# =============================================================================

async def get_user_permissions(
    conn,
    user_id: str,
    tenant_id: Optional[str] = None,
) -> Set[Permission]:
    """
    Get all effective permissions for a user in a tenant context.

    Aggregates permissions from:
    1. Role-based permissions (from tenant_users JOIN role_permissions)
    2. Direct user permissions (from user_permissions table)

    Args:
        conn: Database connection (asyncpg)
        user_id: User UUID
        tenant_id: Optional tenant context (None for global permissions only)

    Returns:
        Set of Permission enums
    """
    permissions: Set[Permission] = set()

    # Query 1: Role-based permissions
    if tenant_id:
        role_perms = await conn.fetch(
            """
            SELECT DISTINCT p.name
            FROM tenant_users tu
            JOIN roles r ON r.id = tu.role_id
            JOIN role_permissions rp ON rp.role_id = r.id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE tu.user_id = $1
              AND tu.tenant_id = $2
              AND tu.status = 'active'
            """,
            user_id,
            tenant_id,
        )
    else:
        # Global permissions (non-tenant-scoped roles like platform_admin)
        role_perms = await conn.fetch(
            """
            SELECT DISTINCT p.name
            FROM tenant_users tu
            JOIN roles r ON r.id = tu.role_id
            JOIN role_permissions rp ON rp.role_id = r.id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE tu.user_id = $1
              AND tu.status = 'active'
              AND r.tenant_scoped = FALSE
            """,
            user_id,
        )

    for row in role_perms:
        try:
            permissions.add(Permission(row["name"]))
        except ValueError:
            logger.warning(f"Unknown permission '{row['name']}' for user {user_id}")

    # Query 2: Direct user permissions
    user_perms = await conn.fetch(
        """
        SELECT p.name
        FROM user_permissions up
        JOIN permissions p ON p.id = up.permission_id
        WHERE up.user_id = $1
          AND (up.tenant_id IS NULL OR up.tenant_id = $2)
          AND (up.expires_at IS NULL OR up.expires_at > NOW())
        """,
        user_id,
        tenant_id,
    )

    for row in user_perms:
        try:
            permissions.add(Permission(row["name"]))
        except ValueError:
            logger.warning(f"Unknown permission '{row['name']}' for user {user_id}")

    return permissions


async def get_effective_permissions(
    pool,
    user_id: str,
    tenant_id: Optional[str],
) -> Set[Permission]:
    """Resolve current database grants without role-default fallback.

    This is the authorization primitive for high-impact inbound and live-call
    mutations. It deliberately re-reads ``role_permissions`` and
    ``user_permissions`` on every request so role revocations and bounded
    direct grants take effect immediately. Database/pool failures propagate to
    the caller, which must fail closed rather than substitute Python defaults.
    """

    async with acquire_with_tenant(
        pool, str(tenant_id) if tenant_id is not None else None
    ) as conn:
        return await get_user_permissions(conn, user_id, tenant_id)


# =============================================================================
# Deployment seeding probe
# =============================================================================
#
# ``get_user_permissions`` resolves role grants through
# ``tenant_users -> roles -> role_permissions -> permissions``.  A deployment
# whose RBAC catalogue was never seeded has NO rows on one or both sides of
# that join, so EVERY user resolves to the empty set — indistinguishable, from
# a single user's point of view, from a deliberate revocation.  The two states
# must behave differently: unseeded falls back to ROLE_DEFAULT_PERMISSIONS,
# revoked denies.
#
# The probe is deliberately GLOBAL (no user_id / tenant_id predicate) so one
# tenant's membership rows being absent cannot fool it into re-granting the
# role defaults for everybody.  It varies with the only two tables that can
# make the authorization join resolvable at all, and it flips exactly once, at
# the moment an operator seeds the catalogue AND backfills memberships.

_SEEDING_PROBE_TTL_SECONDS = 30.0

_seeding_probe_cache: Optional[tuple] = None  # (monotonic_ts, seeded)
_unseeded_fallback_warned = False


def reset_rbac_seeding_probe_cache() -> None:
    """Drop the cached seeding probe result (tests / ops hot-reload)."""
    global _seeding_probe_cache, _unseeded_fallback_warned
    _seeding_probe_cache = None
    _unseeded_fallback_warned = False


async def rbac_data_is_seeded(pool) -> bool:
    """Return True when this deployment has RBAC data to resolve against.

    Two cheap EXISTS probes in one round trip — each stops at the first tuple:
      * ``role_permissions`` has at least one row (the catalogue side), and
      * ``tenant_users`` has at least one ``active`` row (the membership side).

    Both legs are required because either one being globally empty makes the
    role-permission join return nothing for every user in the deployment.

    The answer is cached per process for ``_SEEDING_PROBE_TTL_SECONDS``.  A
    stale ``True`` only ever denies (fail-closed); a stale ``False`` keeps role
    defaults in force for at most the TTL after seeding completes.  Errors are
    never cached and propagate to the caller, which fails closed with 503.
    """
    global _seeding_probe_cache

    now = time.monotonic()
    cached = _seeding_probe_cache
    if cached is not None and (now - cached[0]) < _SEEDING_PROBE_TTL_SECONDS:
        return cached[1]

    # Seeding is a deployment-wide property and intentionally spans tenants.
    async with acquire_with_tenant(pool, None) as conn:
        row = await conn.fetchrow(
            """
            SELECT
                EXISTS (SELECT 1 FROM role_permissions) AS has_role_grants,
                EXISTS (
                    SELECT 1 FROM tenant_users WHERE status = 'active'
                ) AS has_memberships
            """
        )

    has_role_grants = bool(row["has_role_grants"]) if row else False
    has_memberships = bool(row["has_memberships"]) if row else False
    seeded = has_role_grants and has_memberships

    _seeding_probe_cache = (now, seeded)
    logger.debug(
        "RBAC seeding probe: seeded=%s role_permissions_rows=%s "
        "active_tenant_users_rows=%s ttl=%ss",
        seeded,
        has_role_grants,
        has_memberships,
        _SEEDING_PROBE_TTL_SECONDS,
    )
    return seeded


def _warn_unseeded_fallback_once() -> None:
    """Warn ONCE per process. A per-request log at prod volume is its own
    incident."""
    global _unseeded_fallback_warned
    if _unseeded_fallback_warned:
        return
    _unseeded_fallback_warned = True
    logger.warning(
        "RBAC_UNSEEDED_FALLBACK: no rows to resolve permissions against "
        "(role_permissions and/or active tenant_users are empty for this "
        "deployment) - falling back to ROLE_DEFAULT_PERMISSIONS. Run "
        "scripts/seed_rbac.py and backfill tenant_users memberships, then "
        "database grants become authoritative. Logged once per process."
    )


async def get_user_role_in_tenant(
    conn,
    user_id: str,
    tenant_id: str,
) -> Optional[UserRole]:
    """
    Get the user's role in a specific tenant.

    Returns None if user is not a member of the tenant.
    """
    row = await conn.fetchrow(
        """
        SELECT r.name
        FROM tenant_users tu
        JOIN roles r ON r.id = tu.role_id
        WHERE tu.user_id = $1
          AND tu.tenant_id = $2
          AND tu.status = 'active'
        """,
        user_id,
        tenant_id,
    )

    if row:
        return normalize_role(row["name"])
    return None


async def get_user_tenants(
    conn,
    user_id: str,
    include_pending: bool = False,
) -> List[Dict[str, Any]]:
    """
    Get all tenants a user belongs to with their roles.

    Returns list of dicts with tenant_id, tenant_name, role, is_primary.
    """
    statuses = ["active"]
    if include_pending:
        statuses.append("pending")

    rows = await conn.fetch(
        """
        SELECT
            t.id AS tenant_id,
            t.business_name AS tenant_name,
            r.name AS role_name,
            tu.is_primary,
            tu.status
        FROM tenant_users tu
        JOIN tenants t ON t.id = tu.tenant_id
        JOIN roles r ON r.id = tu.role_id
        WHERE tu.user_id = $1
          AND tu.status = ANY($2)
        ORDER BY tu.is_primary DESC, t.business_name
        """,
        user_id,
        statuses,
    )

    return [
        {
            "tenant_id": str(row["tenant_id"]),
            "tenant_name": row["tenant_name"],
            "role": row["role_name"],
            "is_primary": row["is_primary"],
            "status": row["status"],
        }
        for row in rows
    ]


# =============================================================================
# Permission Checking
# =============================================================================

def check_permission(
    user_permissions: Set[Permission],
    required: Permission,
) -> bool:
    """
    Check if user has the required permission.

    Also grants access if user has admin permission for the resource.

    Args:
        user_permissions: Set of user's effective permissions
        required: The required permission

    Returns:
        True if access granted
    """
    # Direct permission check
    if required in user_permissions:
        return True

    # Admin permission grants all actions on that resource.
    # Not every resource defines an ``<resource>:admin`` permission
    # (e.g. calls, connectors) — guard the lookup so a missing admin
    # perm fails SAFE (skip the fallback -> deny) instead of raising.
    resource = required.split(":")[0]
    try:
        admin_perm = Permission(f"{resource}:admin")
    except ValueError:
        admin_perm = None
    if admin_perm is not None and admin_perm in user_permissions:
        return True

    # Platform admin grants everything
    if Permission.PLATFORM_ADMIN in user_permissions:
        return True

    return False


async def has_permission(
    conn,
    user_id: str,
    permission: Permission,
    tenant_id: Optional[str] = None,
) -> bool:
    """
    Async check if user has a specific permission in a tenant context.
    """
    perms = await get_user_permissions(conn, user_id, tenant_id)
    return check_permission(perms, permission)


# =============================================================================
# FastAPI Dependencies
# =============================================================================

def require_role(
    min_role: UserRole,
    allow_platform_admin: bool = True,
):
    """
    FastAPI dependency factory that requires a minimum role level.

    Args:
        min_role: Minimum role required (inclusive)
        allow_platform_admin: If True, platform_admin always passes regardless of min_role

    Usage:
        @router.post("/admin-only")
        async def admin_endpoint(
            user: CurrentUser = Depends(require_role(UserRole.TENANT_ADMIN))
        ):
            ...
    """
    # Import lazily: dependencies.py re-exports this module after defining its
    # authentication dependency.  Importing dependencies at module load time
    # makes `import rbac` fail whenever it happens before `import dependencies`.
    from app.api.v1.dependencies import get_current_user

    async def role_checker(user: Any = Depends(get_current_user)) -> Any:
        user_role = normalize_role(user.role)

        # Platform admin bypass (if enabled)
        if allow_platform_admin and user_role == UserRole.PLATFORM_ADMIN:
            return user

        # Check hierarchy
        if not user_role.can_access(min_role):
            logger.warning(
                f"Role check failed: user={user.id} has {user_role.value}, "
                f"required {min_role.value}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required role: {min_role.value}",
            )

        return user

    return role_checker


def require_permission(
    permission: Permission,
    tenant_id_from: str = "header",  # "header", "path", "query", or callable
):
    """
    FastAPI dependency factory that requires a specific permission.

    Args:
        permission: The required permission
        tenant_id_from: Where to extract tenant_id from

    Usage:
        @router.post("/campaigns")
        async def create_campaign(
            user: CurrentUser = Depends(require_permission(Permission.CAMPAIGNS_CREATE)),
        ):
            ...
    """
    from app.api.v1.dependencies import get_current_user

    async def permission_checker(
        request: Request,
        user: Any = Depends(get_current_user),
    ) -> Any:
        # Extract tenant_id from request
        tenant_id = None
        if tenant_id_from == "header":
            tenant_id = request.headers.get("X-Tenant-ID")
        elif tenant_id_from == "path":
            tenant_id = request.path_params.get("tenant_id")
        elif tenant_id_from == "query":
            tenant_id = request.query_params.get("tenant_id")

        # Platform admins bypass tenant checks
        user_role = normalize_role(user.role)
        if user_role == UserRole.PLATFORM_ADMIN:
            return user

        # If no tenant_id specified, check global permissions
        if not tenant_id and user.tenant_id:
            tenant_id = user.tenant_id

        # Effective permissions come from the DATABASE (role grants + bounded
        # direct grants) — the same resolver the newer call-control, recording
        # and inbound-campaign dependencies use. Role defaults alone ignored
        # every revocation, so revoking e.g. calls:delete did not stop the
        # hangup route. Role defaults remain the fallback ONLY when there is no
        # pool at all (dev/unit contexts with no initialised container); a pool
        # that exists but fails is a 503, never a silent downgrade.
        try:
            from app.core.container import get_db_pool_from_container

            db_pool = get_db_pool_from_container()
        except Exception:
            db_pool = None

        if db_pool is None:
            user_permissions = ROLE_DEFAULT_PERMISSIONS.get(user_role, set())
        else:
            try:
                user_permissions = await get_effective_permissions(
                    db_pool, user.id, tenant_id
                )
                # An empty set is ambiguous: it means either "this user has
                # been denied everything" or "this deployment has no RBAC data
                # at all". Only the second is a reason to fall back. A
                # non-empty set proves the deployment is seeded, so the probe
                # never runs on the healthy path.
                if not user_permissions and not await rbac_data_is_seeded(db_pool):
                    user_permissions = ROLE_DEFAULT_PERMISSIONS.get(user_role, set())
                    _warn_unseeded_fallback_once()
            except Exception as exc:  # noqa: BLE001 - authorization fails closed
                logger.error(
                    f"Permission lookup failed: user={user.id} "
                    f"tenant={tenant_id} err_type={type(exc).__name__}"
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Authorization unavailable",
                ) from exc

        if not check_permission(user_permissions, permission):
            logger.warning(
                f"Permission check failed: user={user.id} "
                f"missing {permission.value} in tenant={tenant_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required permission: {permission.value}",
            )

        return user

    return permission_checker


def require_tenant_member(
    tenant_id_param: str = "tenant_id",
):
    """
    FastAPI dependency factory that requires user to be a member of the tenant.

    Usage:
        @router.get("/tenants/{tenant_id}/users")
        async def list_users(
            user: CurrentUser = Depends(require_tenant_member()),
        ):
            ...
    """
    from app.api.v1.dependencies import get_current_user

    async def tenant_checker(
        request: Request,
        user: Any = Depends(get_current_user),
    ) -> Any:
        tenant_id = request.path_params.get(tenant_id_param) or request.headers.get("X-Tenant-ID")

        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant ID required",
            )

        # Platform admins can access all tenants
        user_role = normalize_role(user.role)
        if user_role == UserRole.PLATFORM_ADMIN:
            return user

        # Check if user's tenant matches
        if str(user.tenant_id) != str(tenant_id):
            logger.warning(
                f"Tenant access denied: user={user.id} tenant={user.tenant_id} "
                f"accessed {tenant_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this tenant",
            )

        return user

    return tenant_checker


def require_tenant_admin(
    tenant_id_param: str = "tenant_id",
):
    """
    FastAPI dependency factory that requires tenant admin or higher.

    This is a convenience wrapper around require_role(UserRole.TENANT_ADMIN)
    with tenant membership verification.
    """
    return require_role(UserRole.TENANT_ADMIN)


# =============================================================================
# Decorator-style (for non-FastAPI contexts)
# =============================================================================

def requires_permission(permission: Permission):
    """
    Decorator for requiring a permission in non-FastAPI contexts.

    Usage:
        @requires_permission(Permission.CAMPAIGNS_DELETE)
        async def delete_campaign(campaign_id: str, user: CurrentUser):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            from app.api.v1.dependencies import CurrentUser

            # Extract user from kwargs or args
            user = kwargs.get('user') or kwargs.get('current_user')
            if not user and args:
                # Try to find user in positional args
                for arg in args:
                    if isinstance(arg, CurrentUser):
                        user = arg
                        break

            if not user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                )

            user_role = normalize_role(user.role)
            user_permissions = ROLE_DEFAULT_PERMISSIONS.get(user_role, set())

            if not check_permission(user_permissions, permission):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Permission denied: {permission.value}",
                )

            return await func(*args, **kwargs)
        return wrapper
    return decorator


# =============================================================================
# Middleware Helpers
# =============================================================================

class RBACContext:
    """
    Context manager for RBAC operations within a request.

    Usage:
        async with RBACContext(conn, user_id, tenant_id) as ctx:
            if ctx.has_permission(Permission.CAMPAIGNS_CREATE):
                # Create campaign
    """

    def __init__(
        self,
        conn,
        user_id: str,
        tenant_id: Optional[str] = None,
    ):
        self.conn = conn
        self.user_id = user_id
        self.tenant_id = tenant_id
        self._permissions: Optional[Set[Permission]] = None

    async def __aenter__(self):
        self._permissions = await get_user_permissions(
            self.conn, self.user_id, self.tenant_id
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._permissions = None
        return False

    def has_permission(self, permission: Permission) -> bool:
        if self._permissions is None:
            raise RuntimeError("RBACContext not initialized")
        return check_permission(self._permissions, permission)

    def has_any_permission(self, permissions: List[Permission]) -> bool:
        return any(self.has_permission(p) for p in permissions)

    def has_all_permissions(self, permissions: List[Permission]) -> bool:
        return all(self.has_permission(p) for p in permissions)


# =============================================================================
# Export convenience
# =============================================================================

__all__ = [
    "UserRole",
    "Permission",
    "ROLE_DEFAULT_PERMISSIONS",
    "ROLE_ALIASES",
    "UnknownRoleError",
    "normalize_role",
    "reset_unknown_role_log_cache",
    "get_user_permissions",
    "get_effective_permissions",
    "rbac_data_is_seeded",
    "reset_rbac_seeding_probe_cache",
    "get_user_role_in_tenant",
    "get_user_tenants",
    "check_permission",
    "has_permission",
    "require_role",
    "require_permission",
    "require_tenant_member",
    "require_tenant_admin",
    "requires_permission",
    "RBACContext",
]

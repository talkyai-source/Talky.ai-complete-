"""Strict, deterministic inbound DID routing.

An inbound call is admitted only when its dialled number matches exactly one
active ``inbound_campaigns`` binding. The binding owns the tenant, campaign
and SIP trunk choice. Trunk context is only a second isolation signal: it may
confirm a DID result, but can never select a tenant by itself.

Every uncertain state fails closed. In particular there is no default-agent,
latest-campaign, metadata or dependency-error fallback.
"""
from __future__ import annotations

import logging
import re
import uuid
import hashlib
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

INBOUND_CONTEXT_PREFIX = "from-tenant-"
ACTIVE_INBOUND_CAMPAIGN_STATUSES = ("running", "active")


@dataclass(frozen=True)
class InboundRoute:
    """Result of resolving one inbound carrier leg."""

    resolved: bool
    rejected: bool
    fallback: bool
    tenant_id: Optional[str]
    campaign_id: Optional[str]
    reason: str
    sip_trunk_id: Optional[str] = None
    inbound_campaign_id: Optional[str] = None
    config_id: Optional[str] = None
    called_did_id: Optional[str] = None
    route_version: Optional[int] = None
    config_version: Optional[int] = None


def strict_inbound_enabled() -> bool:
    """Compatibility shim: inbound routing is now unconditionally strict."""

    return True


def parse_tenant_from_context(context: Optional[str]) -> Optional[str]:
    """Parse a tenant UUID from a generated ``from-tenant-*`` context."""

    if not context:
        return None
    value = context.strip()
    if not value.lower().startswith(INBOUND_CONTEXT_PREFIX):
        return None
    candidate = value[len(INBOUND_CONTEXT_PREFIX) :]
    try:
        uuid.UUID(candidate)
    except (ValueError, AttributeError, TypeError):
        return None
    return candidate


_DID_STRIP_RE = re.compile(r"[\s().\-]")


def normalize_did(raw: Optional[str]) -> Optional[str]:
    """Normalize a carrier presentation to E.164-compatible digits."""

    if not raw:
        return None
    value = str(raw).strip()
    for scheme in ("sip:", "sips:", "tel:"):
        if value.lower().startswith(scheme):
            value = value[len(scheme) :]
            break
    value = value.split("@", 1)[0].split(";", 1)[0]
    had_plus = value.startswith("+")
    value = _DID_STRIP_RE.sub("", value)
    if had_plus and not value.startswith("+"):
        value = "+" + value
    digits = value[1:] if value.startswith("+") else value
    if not digits.isdigit() or not 7 <= len(digits) <= 15:
        return None
    return "+" + digits


def decide_inbound_route(
    *,
    context_tenant_id: Optional[str],
    did_tenant_id: Optional[str],
    campaign_id: Optional[str],
    strict: bool = True,
    sip_trunk_id: Optional[str] = None,
    inbound_campaign_id: Optional[str] = None,
    config_id: Optional[str] = None,
    called_did_id: Optional[str] = None,
    route_version: Optional[int] = None,
    config_version: Optional[int] = None,
    rejection_reason: Optional[str] = None,
) -> InboundRoute:
    """Pure fail-closed decision core.

    ``strict`` remains in the signature for callers from the earlier rollout,
    but is intentionally ignored. Disabling tenant isolation at runtime is no
    longer supported.
    """

    del strict
    if rejection_reason:
        return InboundRoute(False, True, False, None, None, rejection_reason)
    if context_tenant_id and did_tenant_id and context_tenant_id != did_tenant_id:
        return InboundRoute(False, True, False, None, None, "tenant_conflict")
    if not did_tenant_id:
        return InboundRoute(False, True, False, None, None, "unknown_did")
    if not campaign_id or not sip_trunk_id or not inbound_campaign_id:
        return InboundRoute(False, True, False, None, None, "incomplete_binding")
    return InboundRoute(
        True,
        False,
        False,
        did_tenant_id,
        campaign_id,
        "routed",
        sip_trunk_id=sip_trunk_id,
        inbound_campaign_id=inbound_campaign_id,
        config_id=config_id,
        called_did_id=called_did_id,
        route_version=route_version,
        config_version=config_version,
    )


def _did_candidates(did_norm: str) -> list[str]:
    """Compatibility helper kept for callers/tests; resolution is exact."""

    return [did_norm]


def redact_did(value: Optional[str]) -> str:
    """Return a stable, non-reversible token safe for logs.

    Raw DIDs and ANI are customer data.  Hashing keeps correlation useful
    without leaking the number into application logs.
    """

    normalized = normalize_did(value)
    if not normalized:
        return "invalid"
    return "did_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def is_active_inbound_campaign_status(status: object) -> bool:
    """One canonical application predicate for an answerable base campaign."""

    return str(status or "").strip().lower() in ACTIVE_INBOUND_CAMPAIGN_STATUSES


async def _lookup_active_bindings(conn, did_norm: str):
    """Return every currently valid active binding for a canonical DID.

    Fetching all matches (bounded at two) is deliberate. A database missing
    the unique constraint must reject ambiguity instead of letting ``LIMIT 1``
    make tenant selection depend on row order.
    """

    return await conn.fetch(
        """
        SELECT
            a.id AS inbound_campaign_id,
            a.tenant_id,
            a.campaign_id,
            a.sip_trunk_id,
            a.phone_number_id AS called_did_id,
            a.config_id,
            a.version AS route_version,
            cfg.version AS config_version
        FROM inbound_did_assignments a
        JOIN tenant_phone_numbers pn
          ON pn.id = a.phone_number_id
         AND pn.tenant_id = a.tenant_id
         AND pn.e164 = a.canonical_did
         AND pn.status = 'verified'
        JOIN campaigns c
          ON c.id = a.campaign_id
         AND c.tenant_id = a.tenant_id
         AND c.direction = 'inbound'
         AND c.status = ANY($2::text[])
        JOIN inbound_campaign_configs cfg
          ON cfg.id = a.config_id
         AND cfg.tenant_id = a.tenant_id
         AND cfg.campaign_id = a.campaign_id
         AND cfg.status = 'active'
        JOIN tenant_sip_trunks st
          ON st.id = a.sip_trunk_id
         AND st.tenant_id = a.tenant_id
         AND st.is_active = TRUE
         AND st.direction IN ('inbound', 'both')
        JOIN tenants t
          ON t.id = a.tenant_id
         AND t.status = 'active'
         AND t.subscription_status IN ('active', 'trialing')
        LEFT JOIN tenant_inbound_controls tic
          ON tic.tenant_id = a.tenant_id
        WHERE a.canonical_did = ANY($1::text[])
          AND a.status = 'active'
          AND a.valid_from <= NOW()
          AND (a.valid_to IS NULL OR a.valid_to > NOW())
          AND COALESCE(tic.inbound_enabled, FALSE) = TRUE
        ORDER BY a.id
        LIMIT 2
        """,
        _did_candidates(did_norm),
        list(ACTIVE_INBOUND_CAMPAIGN_STATUSES),
    )


def _record_route_decision(reason: str) -> None:
    try:
        from app.infrastructure.metrics.inbound_metrics import record_route_decision

        record_route_decision(reason)
    except Exception:
        pass


async def resolve_inbound_route(
    db_pool,
    *,
    called_did: Optional[str],
    context: Optional[str],
    environment: str,
) -> InboundRoute:
    """Resolve a DID through the explicit active binding, or reject."""

    del environment
    context_tenant = parse_tenant_from_context(context)
    did_norm = normalize_did(called_did)
    if not did_norm:
        _record_route_decision("invalid_did")
        return decide_inbound_route(
            context_tenant_id=context_tenant,
            did_tenant_id=None,
            campaign_id=None,
            rejection_reason="invalid_did",
        )

    try:
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(db_pool, None) as conn:
            rows = list(await _lookup_active_bindings(conn, did_norm))
    except Exception as exc:  # noqa: BLE001 - dependency uncertainty blocks
        logger.error(
            "inbound_route_decision direction=inbound status=rejected "
            "reason=routing_dependency_unavailable err_type=%s",
            type(exc).__name__,
        )
        _record_route_decision("routing_dependency_unavailable")
        return decide_inbound_route(
            context_tenant_id=context_tenant,
            did_tenant_id=None,
            campaign_id=None,
            rejection_reason="routing_dependency_unavailable",
        )

    if not rows:
        _record_route_decision("unknown_did")
        return decide_inbound_route(
            context_tenant_id=context_tenant,
            did_tenant_id=None,
            campaign_id=None,
            rejection_reason="unknown_did",
        )
    if len(rows) != 1:
        logger.critical(
            "inbound_route_decision direction=inbound status=rejected "
            "reason=ambiguous_did",
        )
        _record_route_decision("ambiguous_did")
        return decide_inbound_route(
            context_tenant_id=context_tenant,
            did_tenant_id=None,
            campaign_id=None,
            rejection_reason="ambiguous_did",
        )

    row = rows[0]
    route = decide_inbound_route(
        context_tenant_id=context_tenant,
        did_tenant_id=str(row["tenant_id"]),
        campaign_id=str(row["campaign_id"]),
        sip_trunk_id=str(row["sip_trunk_id"]),
        inbound_campaign_id=str(row["inbound_campaign_id"]),
        config_id=str(row["config_id"]),
        called_did_id=str(row["called_did_id"]),
        route_version=int(row["route_version"]),
        config_version=int(row["config_version"]),
    )
    logger.info(
        "inbound_route_decision direction=inbound status=%s reason=%s",
        "accepted" if route.resolved else "rejected",
        route.reason,
    )
    _record_route_decision(route.reason)
    return route

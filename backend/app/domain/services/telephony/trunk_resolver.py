"""Resolve tenant outbound routing without silently changing a selected trunk.

Campaign assignment wins, then tenant pool assignment, then the most recently
activated own trunk. A down/missing route or failed lookup is a refusal. Caller
IDs come from the current trunk record; assignment snapshots are display caches.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from app.domain.models.tenant_phone_number import PhoneNumberStatus
from app.domain.services.telephony.trunk_runtime import evaluate_trunk_runtime

logger = logging.getLogger(__name__)

# Fallbacks mirror the values baked into the seed + the adapter so the
# resolver's "default" branch is identical to today's hard-coded behaviour.
_DEFAULT_ENV_ENDPOINT = "blazedigitel-endpoint"
_DEFAULT_PLATFORM_TRUNK_NAME = "platform-default"


def shared_default_trunk_enabled() -> bool:
    """Whether the platform ships a shared default trunk (transition model).

    ``TELEPHONY_SHARED_DEFAULT_TRUNK`` — default ``"on"`` so nothing changes
    today. Set to ``"off"`` for the own-trunk-ONLY production model: a tenant
    with no active own trunk is REFUSED rather than silently routed onto a
    shared upstream (there is no public shared trunk in that model).
    """
    raw = os.getenv("TELEPHONY_SHARED_DEFAULT_TRUNK", "on").strip().lower()
    return raw not in {"off", "0", "false", "no"}


def env_default_endpoint() -> str:
    """The global PJSIP endpoint used today for every outbound call.

    Reads ``TELEPHONY_PJSIP_OUTBOUND_ENDPOINT`` (same env var the adapter
    reads) so the default branch stays in lock-step with the adapter's own
    fallback when this resolver returns ``None`` for the endpoint.
    """
    return os.getenv("TELEPHONY_PJSIP_OUTBOUND_ENDPOINT", _DEFAULT_ENV_ENDPOINT)


def platform_default_trunk_name() -> str:
    """Name of the seeded platform-default trunk row (case-insensitive match).

    Mirrors ``seed_platform_sip_trunk._read_platform_env``'s default so a
    tenant's shared upstream row is recognised as the default rather than
    being mistaken for an own trunk.
    """
    return (os.getenv("PLATFORM_SIP_TRUNK_NAME") or _DEFAULT_PLATFORM_TRUNK_NAME).strip()


@dataclass(frozen=True)
class TrunkRow:
    """Minimal projection of a ``tenant_sip_trunks`` row (no secrets).

    ``caller_id`` is the trunk's own configured Caller-ID (stored in the
    trunk ``metadata.caller_id`` JSON — the "basic Caller ID" the trunk form
    writes), used as the caller-ID fallback when the tenant has no verified
    DID on file.

    ``is_internal_extension`` marks a trunk that registers an internal PBX
    extension (``metadata.role == "extension"``) rather than a PSTN route. Such
    a trunk is never auto-selected for outbound — see
    :func:`choose_outbound_route`.
    """
    id: str
    trunk_name: str
    is_active: bool
    updated_at: Optional[datetime] = None
    caller_id: Optional[str] = None
    runtime_ready: bool = True
    is_internal_extension: bool = False


@dataclass(frozen=True)
class DidRow:
    """Minimal projection of a ``tenant_phone_numbers`` row."""
    e164: str
    status: str
    stir_shaken_token: Optional[str] = None


@dataclass(frozen=True)
class OutboundTrunkRoute:
    """Resolved outbound routing decision.

    ``endpoint`` is the PJSIP endpoint name to dial through (``None`` only
    when ``refused``). ``caller_id`` is the E.164 to present, or ``None``
    meaning "keep the caller's existing caller-ID" — the default path always
    returns ``None`` so existing behaviour is preserved. ``is_default`` is
    True whenever we fell back to the shared platform endpoint. ``refused``
    is True in own-trunk-only mode when the tenant has no usable own trunk /
    caller-ID — the caller must turn this into a clean 4xx, NOT a fallback.
    """
    endpoint: Optional[str]
    caller_id: Optional[str]
    trunk_id: Optional[str]
    is_default: bool
    reason: str
    refused: bool = False


def _is_platform_default(trunk: TrunkRow, platform_name: str) -> bool:
    return trunk.trunk_name.strip().lower() == platform_name.strip().lower()


def _trunk_own_verified_number(
    trunk_caller_id: Optional[str], dialable_numbers: Sequence[DidRow]
) -> Optional[str]:
    """The tenant's verified DID that equals the trunk's configured caller-ID
    (digits compared, so "17789249977" matches "+17789249977"), or None."""
    want = "".join(ch for ch in str(trunk_caller_id or "") if ch.isdigit())
    if not want:
        return None
    verified = PhoneNumberStatus.VERIFIED.value
    for row in dialable_numbers:
        have = "".join(ch for ch in str(row.e164 or "") if ch.isdigit())
        if row.status == verified and have == want:
            return row.e164
    return None


def _select_caller_id(
    dialable_numbers: Sequence[DidRow],
    *,
    is_production: bool,
) -> Optional[str]:
    """Select a verified tenant DID; stored static STIR tokens are not per-call proof."""
    verified = PhoneNumberStatus.VERIFIED.value

    def _sorted(rows: Sequence[DidRow]) -> list[DidRow]:
        return sorted(rows, key=lambda r: r.e164 or "")

    if is_production:
        eligible = [
            r for r in dialable_numbers
            if r.status == verified
        ]
        chosen = _sorted(eligible)
        return chosen[0].e164 if chosen else None

    # Non-production: prefer verified, then anything, then nothing.
    verified_rows = _sorted([r for r in dialable_numbers if r.status == verified])
    if verified_rows:
        return verified_rows[0].e164
    any_rows = _sorted(list(dialable_numbers))
    return any_rows[0].e164 if any_rows else None


def choose_outbound_route(
    *,
    active_trunks: Sequence[TrunkRow],
    dialable_numbers: Sequence[DidRow],
    env_default_endpoint: str,
    platform_default_trunk_name: str,
    is_production: bool,
    shared_default_enabled: bool = True,
) -> OutboundTrunkRoute:
    """Pure routing decision — no I/O. See module docstring.

    Precedence when a tenant has BOTH the seeded platform-default row and
    their own active trunk: the **own** trunk wins (an explicitly activated
    BYO trunk is the tenant's intent). Among multiple own active trunks the
    most-recently-updated one is chosen (deterministic, id tie-break).

    ``shared_default_enabled`` = the flag. When True (transition/back-compat)
    a tenant with no own trunk falls back to the shared platform endpoint —
    exactly today's behaviour. When False (own-trunk-only production) such a
    tenant is REFUSED, and an own trunk with no usable caller-ID is also
    refused (prefer verified DID, else the trunk's configured caller-ID,
    else refuse).
    """
    actives = [t for t in active_trunks if t.is_active]

    # An internal PBX extension trunk is an ADDRESS, not a PSTN route: it can
    # be rung, but it cannot carry an outbound call to a real number and has no
    # presentable caller-ID. Before this filter, merely activating one made it
    # the tenant's "own trunk" and it won the precedence below — so provisioning
    # extension 940003 on a tenant would have silently re-routed that tenant's
    # already-running outbound campaign onto a PBX extension. Dialling an
    # extension on purpose still works: it goes through the explicit
    # campaign-level assignment (_resolve_campaign_trunk), which outranks this.
    own_trunks = [
        t for t in actives
        if not _is_platform_default(t, platform_default_trunk_name)
        and not t.is_internal_extension
    ]

    if own_trunks:
        # Most recently updated own trunk wins; stable id tie-break.
        own = sorted(
            own_trunks,
            key=lambda t: (
                t.updated_at or datetime.min,
                str(t.id),
            ),
        )[-1]
        if not own.runtime_ready:
            return OutboundTrunkRoute(None, None, str(own.id), False, "selected_trunk_not_ready", True)
        # An explicitly configured number must never be replaced by a different
        # tenant DID merely because that other number sorts first or has a token.
        caller_id = _trunk_own_verified_number(own.caller_id, dialable_numbers)
        if own.caller_id and caller_id is None:
            if is_production:
                return OutboundTrunkRoute(None, None, str(own.id), False, "trunk_caller_id_not_verified", True)
            caller_id = own.caller_id.strip() or None
        if not own.caller_id:
            caller_id = _select_caller_id(dialable_numbers, is_production=is_production)

        # Own-trunk-only mode requires a presentable caller-ID; refuse when
        # neither a verified DID nor the trunk's configured caller-ID exists.
        # (With the shared default ON we preserve the old behaviour: route
        # with caller_id=None so the caller keeps its existing caller-ID.)
        if caller_id is None and not shared_default_enabled:
            return OutboundTrunkRoute(
                endpoint=None,
                caller_id=None,
                trunk_id=str(own.id),
                is_default=False,
                reason="no_caller_id",
                refused=True,
            )

        return OutboundTrunkRoute(
            endpoint=f"trunk-{own.id}",
            caller_id=caller_id,
            trunk_id=str(own.id),
            is_default=False,
            reason="own_trunk",
        )

    # No own active trunk.
    if not shared_default_enabled:
        # Own-trunk-only production: there is no shared upstream to fall back
        # on — refuse cleanly so the caller can tell the tenant to set up PBX.
        return OutboundTrunkRoute(
            endpoint=None,
            caller_id=None,
            trunk_id=None,
            is_default=False,
            reason="no_own_trunk",
            refused=True,
        )

    # Shared default ON → shared platform endpoint, caller-ID unchanged.
    ready_defaults = [t for t in actives if t.runtime_ready and _is_platform_default(t, platform_default_trunk_name)]
    if not ready_defaults:
        return OutboundTrunkRoute(None, None, None, False, "no_ready_trunk", True)
    reason = "platform_default"
    return OutboundTrunkRoute(
        endpoint=env_default_endpoint,
        caller_id=None,
        trunk_id=None,
        is_default=True,
        reason=reason,
    )


def _fallback_route(reason: str, *, shared_default_enabled: bool) -> OutboundTrunkRoute:
    """A failed lookup cannot establish permission or a healthy default route."""
    return OutboundTrunkRoute(None, None, None, False, reason, True)


def _coerce_metadata(metadata) -> dict:
    """Best-effort dict view of a trunk ``metadata`` column (jsonb or text)."""
    if isinstance(metadata, str):
        import json as _json
        try:
            metadata = _json.loads(metadata)
        except (ValueError, TypeError):
            return {}
    return metadata if isinstance(metadata, dict) else {}


def _is_internal_extension(metadata) -> bool:
    """True when this trunk registers an internal PBX extension, not a PSTN route.

    Set by provisioning as ``metadata.role = "extension"``. Absent on every
    existing row, so the default is False and no current trunk changes
    behaviour when this ships.
    """
    return str(_coerce_metadata(metadata).get("role") or "").strip().lower() == "extension"


def _extract_trunk_caller_id(metadata) -> Optional[str]:
    """Pull the trunk's own configured caller-ID out of the metadata JSON."""
    if isinstance(metadata, str):
        import json as _json
        try:
            metadata = _json.loads(metadata)
        except (ValueError, TypeError):
            return None
    if not isinstance(metadata, dict):
        return None
    cid = metadata.get("caller_id")
    if isinstance(cid, str) and cid.strip():
        return cid.strip()
    return None


async def requires_sip_readiness(db_pool, *, tenant_id: str, campaign: dict) -> bool:
    """Gate selected SIP inventory in auto mode without probing a PBX at start.

    Explicit cloud-provider tenants keep their provider's admission contract.
    A configured Asterisk deployment always checks, including an empty inventory.
    """
    from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory

    adapter = (
        os.getenv("TELEPHONY_ADAPTER")
        or CallControlAdapterFactory._read_pbx_backend_config()
        or "auto"
    ).lower()
    if adapter == "freeswitch":
        return False
    if db_pool is None:
        return adapter == "asterisk"
    from app.core.db_utils import acquire_with_tenant

    async with acquire_with_tenant(db_pool, tenant_id) as conn:
        row = await conn.fetchrow(
            """SELECT active_telephony_provider,
                      calling_rules->'pool_trunk' AS pool_trunk,
                      EXISTS (SELECT 1 FROM tenant_sip_trunks WHERE tenant_id=$1::uuid) AS has_trunks
               FROM tenants WHERE id=$1::uuid""",
            tenant_id,
        )
    if not row:
        # A missing tenant cannot establish a permitted route.
        return True
    provider = str(row["active_telephony_provider"] or "none").lower()
    if provider in {"twilio", "vonage"}:
        return False
    if provider != "sip":
        from app.infrastructure.telephony.provider_factory import TelephonyProviderFactory

        # Match the existing factory: the tenant's explicit SIP choice wins
        # over platform defaults; otherwise env takes precedence over YAML.
        default_provider = (
            os.getenv("TELEPHONY_PROVIDER")
            or TelephonyProviderFactory._read_config_active()
            or "auto"
        ).lower()
        if default_provider in {"twilio", "vonage"}:
            return False
    config = _coerce_metadata(campaign.get("calling_config"))
    return bool(
        adapter == "asterisk"
        or provider == "sip"
        or config.get("trunk")
        or row["pool_trunk"]
        or row["has_trunks"]
    )


async def _resolve_campaign_trunk(
    db_pool, *, campaign_id: str, tenant_id: str
) -> Optional[OutboundTrunkRoute]:
    """If this CAMPAIGN has been allotted a specific trunk, route to it.

    Per-campaign override — lets two campaigns of the same tenant dial out on
    different PBX accounts (different caller-IDs). Stored on the campaign's own
    ``campaigns.calling_config.trunk`` as ``{"id","endpoint","caller_id","label"}``
    (snapshotted at assignment time, same shape as the tenant pool allotment).
    Takes precedence over BOTH the tenant pool allotment and own-trunk
    resolution. Returns None only when there is no assignment. Errors refuse.
    """
    try:
        from app.core.db_utils import acquire_with_tenant
        async with acquire_with_tenant(db_pool, str(tenant_id)) as conn:
            raw = await conn.fetchval(
                "SELECT calling_config->'trunk' FROM campaigns "
                "WHERE id = $1::uuid AND tenant_id = $2::uuid",
                str(campaign_id), str(tenant_id),
            )
        if not raw:
            return None
        ct = raw if isinstance(raw, dict) else json.loads(raw)
        trunk_id = str(ct.get("id") or "").strip()
        if not trunk_id:
            return OutboundTrunkRoute(
                endpoint=None, caller_id=None, trunk_id=None, is_default=False,
                reason="campaign_assigned_trunk_invalid", refused=True,
            )
        async with acquire_with_tenant(db_pool, str(tenant_id)) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, trunk_name, is_active, direction, metadata,
                       live_registration_status, live_status_detail,
                       live_status_checked_at
                FROM tenant_sip_trunks
                WHERE id = $1::uuid
                  AND tenant_id = $2::uuid
                """,
                trunk_id,
                str(tenant_id),
            )
        if row and str(row["tenant_id"]) != str(tenant_id):
            return OutboundTrunkRoute(None, None, trunk_id, False, "trunk_not_authorized", True)
        runtime = (
            evaluate_trunk_runtime(dict(row), require_inbound=False) if row else None
        )
        if (
            not row
            or not runtime
            or not runtime.ready
            or row["direction"] not in {"outbound", "both"}
        ):
            return OutboundTrunkRoute(
                endpoint=None, caller_id=None, trunk_id=trunk_id, is_default=False,
                reason="campaign_assigned_trunk_not_ready", refused=True,
            )
        is_platform_default = _is_platform_default(
            TrunkRow(
                id=str(row["id"]),
                trunk_name=row["trunk_name"],
                is_active=bool(row["is_active"]),
            ),
            platform_default_trunk_name(),
        )
        endpoint = env_default_endpoint() if is_platform_default else f"trunk-{row['id']}"
        caller_id = _extract_trunk_caller_id(row["metadata"])
        return OutboundTrunkRoute(
            endpoint=endpoint,
            caller_id=caller_id,
            trunk_id=trunk_id,
            is_default=False,
            reason="campaign_assigned",
            refused=False,
        )
    except Exception as exc:  # noqa: BLE001 — explicit assignment fails closed
        logger.error(
            "campaign_trunk_resolve_failed campaign=%s err=%s",
            str(campaign_id)[:8], exc,
        )
        return OutboundTrunkRoute(
            endpoint=None, caller_id=None, trunk_id=None, is_default=False,
            reason="campaign_assigned_trunk_lookup_failed", refused=True,
        )


async def _resolve_pool_assignment(
    db_pool, *, tenant_id: str, is_production: bool
) -> Optional[OutboundTrunkRoute]:
    """If this tenant has been allotted a SHARED-POOL trunk, route to it.

    The allotment stores a trunk ID on ``tenants.calling_rules.pool_trunk``.
    The referenced trunk must belong to this tenant. ``metadata.pool`` is not
    permission to use another tenant's carrier account.
    Takes precedence over the tenant's own trunks (explicit operator intent).
    Returns None only when there is no assignment. Errors refuse.
    """
    try:
        from app.core.db_utils import acquire_with_tenant
        async with acquire_with_tenant(db_pool, str(tenant_id)) as conn:
            raw = await conn.fetchval(
                "SELECT calling_rules->'pool_trunk' FROM tenants WHERE id = $1",
                tenant_id,
            )
        if not raw:
            return None
        pt = raw if isinstance(raw, dict) else json.loads(raw)
        trunk_id = str(pt.get("id") or "").strip()
        if not trunk_id:
            return OutboundTrunkRoute(
                endpoint=None, caller_id=None, trunk_id=None, is_default=False,
                reason="pool_assigned_trunk_invalid", refused=True,
            )
        async with acquire_with_tenant(db_pool, str(tenant_id)) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id, is_active, direction, metadata,
                       live_registration_status, live_status_detail,
                       live_status_checked_at
                FROM tenant_sip_trunks
                WHERE id = $1::uuid AND tenant_id = $2::uuid AND metadata->>'pool' = 'true'
                """,
                trunk_id,
                str(tenant_id),
            )
        if row and str(row["tenant_id"]) != str(tenant_id):
            return OutboundTrunkRoute(None, None, trunk_id, False, "trunk_not_authorized", True)
        runtime = (
            evaluate_trunk_runtime(dict(row), require_inbound=False) if row else None
        )
        if (
            not row
            or not runtime
            or not runtime.ready
            or row["direction"] not in {"outbound", "both"}
        ):
            return OutboundTrunkRoute(
                endpoint=None, caller_id=None, trunk_id=trunk_id, is_default=False,
                reason="pool_assigned_trunk_not_ready", refused=True,
            )
        endpoint = f"trunk-{row['id']}"
        caller_id = _extract_trunk_caller_id(row["metadata"])
        return OutboundTrunkRoute(
            endpoint=endpoint,
            caller_id=caller_id,
            trunk_id=trunk_id,
            is_default=False,
            reason="pool_assigned",
            refused=False,
        )
    except Exception as exc:  # noqa: BLE001 — explicit assignment fails closed
        logger.error(
            "pool_assignment_resolve_failed tenant=%s err=%s", str(tenant_id)[:8], exc
        )
        return OutboundTrunkRoute(
            endpoint=None, caller_id=None, trunk_id=None, is_default=False,
            reason="pool_assigned_trunk_lookup_failed", refused=True,
        )


async def resolve_outbound_trunk(
    db_pool,
    *,
    tenant_id: Optional[str],
    environment: str,
    campaign_id: Optional[str] = None,
) -> OutboundTrunkRoute:
    """Resolve the outbound route for ``tenant_id`` (async, DB-backed).

    Fetches the tenant's active trunks and DID rows under the tenant's RLS
    context, then delegates to :func:`choose_outbound_route`. Fail-safe:
    any error resolves via :func:`_fallback_route` — the platform default
    when the shared default is ON, a clean refusal when it's OFF (so an
    own-trunk-only deployment never mis-routes onto a non-existent upstream).
    """
    shared_default = shared_default_trunk_enabled()

    if not tenant_id:
        return _fallback_route("no_tenant", shared_default_enabled=shared_default)

    is_production = environment.strip().lower() == "production"

    # Campaign-level trunk override wins over everything: two campaigns of the
    # same tenant may dial out on different PBX accounts / caller-IDs.
    if campaign_id:
        campaign_route = await _resolve_campaign_trunk(
            db_pool, campaign_id=str(campaign_id), tenant_id=str(tenant_id)
        )
        if campaign_route is not None:
            logger.info(
                "trunk_resolved tenant=%s campaign=%s endpoint=%s reason=campaign_assigned",
                str(tenant_id)[:8], str(campaign_id)[:8], campaign_route.endpoint,
            )
            return campaign_route

    # Shared-pool allotment wins: if an operator assigned this tenant a pool
    # account, dial on it (no own trunk / registration needed on the tenant).
    pool_route = await _resolve_pool_assignment(
        db_pool, tenant_id=str(tenant_id), is_production=is_production
    )
    if pool_route is not None:
        logger.info(
            "trunk_resolved tenant=%s endpoint=%s reason=pool_assigned",
            str(tenant_id)[:8], pool_route.endpoint,
        )
        return pool_route

    try:
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(db_pool, str(tenant_id)) as conn:
            trunk_rows = await conn.fetch(
                """
                SELECT id, trunk_name, is_active, updated_at, direction, metadata,
                       live_registration_status, live_status_detail,
                       live_status_checked_at
                FROM tenant_sip_trunks
                WHERE tenant_id = $1 AND is_active = TRUE
                """,
                tenant_id,
            )
            did_rows = await conn.fetch(
                """
                SELECT e164, status, stir_shaken_token
                FROM tenant_phone_numbers
                WHERE tenant_id = $1
                """,
                tenant_id,
            )
    except ValueError:
        # acquire_with_tenant raises ValueError for a non-UUID tenant id.
        return _fallback_route("invalid_tenant_id", shared_default_enabled=shared_default)
    except Exception as exc:  # noqa: BLE001 — fail safe, never block a call
        logger.error(
            "trunk_resolve_failed tenant=%s err=%s — resolver falling back",
            str(tenant_id)[:8], exc,
        )
        return _fallback_route("resolve_error", shared_default_enabled=shared_default)

    active_trunks = [
        TrunkRow(
            id=str(r["id"]),
            trunk_name=r["trunk_name"],
            is_active=bool(r["is_active"]),
            updated_at=r["updated_at"],
            caller_id=_extract_trunk_caller_id(r["metadata"]),
            runtime_ready=(
                evaluate_trunk_runtime(dict(r), require_inbound=False).ready
                and r["direction"] in {"outbound", "both"}
            ),
            is_internal_extension=_is_internal_extension(r["metadata"]),
        )
        for r in trunk_rows
    ]
    dialable_numbers = [
        DidRow(
            e164=r["e164"],
            status=r["status"],
            stir_shaken_token=r["stir_shaken_token"],
        )
        for r in did_rows
    ]

    route = choose_outbound_route(
        active_trunks=active_trunks,
        dialable_numbers=dialable_numbers,
        env_default_endpoint=env_default_endpoint(),
        platform_default_trunk_name=platform_default_trunk_name(),
        is_production=is_production,
        shared_default_enabled=shared_default,
    )
    logger.info(
        "trunk_resolved tenant=%s endpoint=%s is_default=%s refused=%s reason=%s "
        "caller_id_override=%s",
        str(tenant_id)[:8], route.endpoint, route.is_default, route.refused,
        route.reason, bool(route.caller_id),
    )
    return route

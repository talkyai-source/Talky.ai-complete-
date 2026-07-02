"""Per-tenant outbound SIP-trunk resolution (Phase A).

Given a tenant, decide which Asterisk PJSIP endpoint an outbound call must
be dialed through and which caller-ID (E.164) to present. This is the piece
that turns the per-tenant ``tenant_sip_trunks`` data model into an actual
routing decision — today every outbound call is hard-wired to a single
global endpoint (``TELEPHONY_PJSIP_OUTBOUND_ENDPOINT``), which means BYO /
own-trunk tenants share the platform upstream. This resolver keeps the
platform-default path byte-for-byte identical while giving own-trunk
tenants their isolated ``trunk-<trunkid>`` endpoint + their own number.

Design (matches the orchestrator's namespacing scheme):

  * platform-default trunk (seeded ``platform-default`` row) → the existing
    env global endpoint (``blazedigitel-endpoint`` by default). is_default.
  * tenant-owned active trunk → PJSIP objects named ``trunk-<trunkid>``.
  * caller-id: for an own-trunk route we present the tenant's own dialable
    number (``is_dialable_in_production`` in prod; graceful fallback to any
    verified / any number outside prod). For the default path we return
    ``None`` so the caller keeps whatever caller-ID it already validated —
    that is what keeps today's tenants unchanged.

The decision core (:func:`choose_outbound_route`) is a **pure** function so
it can be unit-tested offline with no DB; :func:`resolve_outbound_trunk` is
the thin async wrapper that fetches the two row-sets under the tenant's RLS
context and delegates to it. Both are fail-safe: any error resolves to the
platform default (env endpoint, unchanged caller-ID) so a resolver problem
can never block or mis-route a call.

NOTE: this module never decrypts or logs trunk passwords — it only needs
the trunk id + name to name the PJSIP endpoint. Rendering the actual
credentials into Asterisk config is Phase B.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from app.domain.models.tenant_phone_number import PhoneNumberStatus

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
    """
    id: str
    trunk_name: str
    is_active: bool
    updated_at: Optional[datetime] = None
    caller_id: Optional[str] = None


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


def _select_caller_id(
    dialable_numbers: Sequence[DidRow],
    *,
    is_production: bool,
) -> Optional[str]:
    """Pick the tenant's own caller-ID from their verified DID rows.

    Production honours the same gate as
    ``TenantPhoneNumber.is_dialable_in_production`` — verified AND a real
    STIR/SHAKEN attestation token. Outside production we fall back
    gracefully: prefer a verified number, else any number on file, so
    local/staging BYO testing isn't blocked by the attestation requirement.
    Deterministic (sorted by E.164) so the choice is stable across calls.
    """
    verified = PhoneNumberStatus.VERIFIED.value

    def _sorted(rows: Sequence[DidRow]) -> list[DidRow]:
        return sorted(rows, key=lambda r: r.e164 or "")

    if is_production:
        eligible = [
            r for r in dialable_numbers
            if r.status == verified and bool(r.stir_shaken_token)
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

    own_trunks = [
        t for t in actives
        if not _is_platform_default(t, platform_default_trunk_name)
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
        # Caller-ID: prefer a verified DID; else the trunk's own configured
        # caller-ID (metadata.caller_id); else None.
        caller_id = _select_caller_id(dialable_numbers, is_production=is_production)
        if caller_id is None and own.caller_id:
            caller_id = own.caller_id.strip() or None

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
    reason = "platform_default" if actives else "no_active_trunk"
    return OutboundTrunkRoute(
        endpoint=env_default_endpoint,
        caller_id=None,
        trunk_id=None,
        is_default=True,
        reason=reason,
    )


def _fallback_route(reason: str, *, shared_default_enabled: bool) -> OutboundTrunkRoute:
    """Fail-safe route used when the resolver can't complete.

    With the shared default ON we fall back to the platform endpoint (never
    block a call). With it OFF (own-trunk-only) we must NOT mis-route onto a
    non-existent shared upstream, so we refuse cleanly instead.
    """
    if shared_default_enabled:
        return OutboundTrunkRoute(
            endpoint=env_default_endpoint(),
            caller_id=None,
            trunk_id=None,
            is_default=True,
            reason=reason,
        )
    return OutboundTrunkRoute(
        endpoint=None,
        caller_id=None,
        trunk_id=None,
        is_default=False,
        reason=reason,
        refused=True,
    )


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


async def resolve_outbound_trunk(
    db_pool,
    *,
    tenant_id: Optional[str],
    environment: str,
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

    try:
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(db_pool, str(tenant_id)) as conn:
            trunk_rows = await conn.fetch(
                """
                SELECT id, trunk_name, is_active, updated_at, metadata
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

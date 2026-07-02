"""Per-tenant INBOUND call routing (Phase C).

Inbound calls are un-routed today: a true inbound call has no pre-existing
``calls`` row (the dialer only pre-creates one for OUTBOUND), so
``bind_telephony_call`` finds nothing and the call runs the default agent.
This module resolves an inbound call to the right TENANT (and campaign)
using two independent signals, so tenant A's number can never reach tenant B:

  * CONTEXT — a BYO/own trunk's generated endpoint sets
    ``context=from-tenant-<tenantid>`` (Phase B). If the inbound leg arrives
    in that context, Asterisk has already vouched (via ``type=identify``
    source-host match) that this is that tenant's trunk → tenant known
    directly, no DB needed.
  * DID — the dialed number. ``tenant_phone_numbers.e164`` is globally
    unique, so the called DID maps to at most one tenant.

When BOTH are present they MUST agree; a mismatch (context says tenant A, DID
belongs to tenant B) is a misconfig/spoof and is REJECTED. When neither
resolves a tenant, behaviour is flag-gated:

  * ``TELEPHONY_INBOUND_REQUIRE_TENANT`` off (default) → FALL BACK to today's
    default-agent path (nothing breaks during rollout).
  * on (own-trunk-only production) → REJECT the unrecognized DID.

The decision core (:func:`decide_inbound_route`) is PURE and unit-tested
offline; :func:`resolve_inbound_route` is the async wrapper that does the DID
→ tenant and DID → campaign lookups.

DID → campaign mapping: there is no DID→campaign link in the schema today
(campaigns have no inbound-number field; ``tenant_phone_numbers`` has no
campaign column). We use the minimal additive approach — a
``tenant_phone_numbers.metadata.campaign_id`` (no migration; the JSON already
exists), falling back to the tenant's most-recent active campaign.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

INBOUND_CONTEXT_PREFIX = "from-tenant-"


@dataclass(frozen=True)
class InboundRoute:
    """Outcome of inbound resolution.

    Exactly one of ``resolved`` / ``rejected`` / ``fallback`` is True.
      * ``resolved`` → route to (tenant_id, campaign_id) [campaign may be None
        → tenant's default agent].
      * ``rejected`` → refuse the call (conflict / strict unknown DID).
      * ``fallback`` → use today's exact default-agent path (rollout-safe).
    """
    resolved: bool
    rejected: bool
    fallback: bool
    tenant_id: Optional[str]
    campaign_id: Optional[str]
    reason: str


def strict_inbound_enabled() -> bool:
    """``TELEPHONY_INBOUND_REQUIRE_TENANT`` — default off (fallback on unknown
    DID); on = reject unrecognized DIDs (own-trunk-only production). Mirrors
    the outbound ``TELEPHONY_SHARED_DEFAULT_TRUNK`` flag."""
    return os.getenv("TELEPHONY_INBOUND_REQUIRE_TENANT", "").strip().lower() in {
        "1", "true", "on", "yes",
    }


def parse_tenant_from_context(context: Optional[str]) -> Optional[str]:
    """Return the tenant id embedded in a ``from-tenant-<uuid>`` context, or
    None. Only a well-formed UUID is accepted — a malformed suffix yields
    None (fail-closed: never trust a garbled context)."""
    if not context:
        return None
    c = context.strip()
    if c.lower().startswith(INBOUND_CONTEXT_PREFIX):
        candidate = c[len(INBOUND_CONTEXT_PREFIX):]
        try:
            uuid.UUID(candidate)
            return candidate
        except (ValueError, AttributeError, TypeError):
            return None
    return None


_DID_STRIP_RE = re.compile(r"[\s().\-]")


def normalize_did(raw: Optional[str]) -> Optional[str]:
    """Best-effort E.164 normalisation of the dialed number.

    Strips a ``sip:``/``tel:`` scheme and any ``@host`` / ``;params`` tail,
    drops spaces / dashes / parens / dots. Preserves a leading ``+``. Returns
    None for empty/garbage. The DB lookup additionally tries a ``+``-prefixed
    variant, so both ``+15551234567`` and ``15551234567`` on the wire resolve.
    """
    if not raw:
        return None
    s = str(raw).strip()
    # strip scheme
    for scheme in ("sip:", "sips:", "tel:"):
        if s.lower().startswith(scheme):
            s = s[len(scheme):]
            break
    # user@host → user ; drop any ;params
    s = s.split("@", 1)[0]
    s = s.split(";", 1)[0]
    plus = s.startswith("+")
    s = _DID_STRIP_RE.sub("", s)
    if plus and not s.startswith("+"):
        s = "+" + s
    # require at least a few digits to be a real number
    digits = s[1:] if s.startswith("+") else s
    if not digits.isdigit() or len(digits) < 3:
        return None
    return s


def decide_inbound_route(
    *,
    context_tenant_id: Optional[str],
    did_tenant_id: Optional[str],
    campaign_id: Optional[str],
    strict: bool,
) -> InboundRoute:
    """Pure routing decision — no I/O. See module docstring for the rules."""
    # Both signals present + disagree → reject (misconfig / spoof).
    if context_tenant_id and did_tenant_id and context_tenant_id != did_tenant_id:
        return InboundRoute(
            resolved=False, rejected=True, fallback=False,
            tenant_id=None, campaign_id=None, reason="tenant_conflict",
        )

    tenant_id = context_tenant_id or did_tenant_id
    if not tenant_id:
        if strict:
            return InboundRoute(
                resolved=False, rejected=True, fallback=False,
                tenant_id=None, campaign_id=None, reason="unknown_did_strict",
            )
        return InboundRoute(
            resolved=False, rejected=False, fallback=True,
            tenant_id=None, campaign_id=None, reason="unknown_did_fallback",
        )

    reason = "routed" if campaign_id else "routed_no_campaign"
    return InboundRoute(
        resolved=True, rejected=False, fallback=False,
        tenant_id=tenant_id, campaign_id=campaign_id, reason=reason,
    )


def _coerce_metadata(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json as _json
        try:
            parsed = _json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


async def _lookup_did_tenant(conn, did_norm: Optional[str]):
    """Return (tenant_id, metadata) for a verified DID, or (None, None).

    Tries the normalised value and a ``+``-prefixed variant so both E.164 and
    bare-digit presentations resolve against the globally-unique e164 column.
    """
    if not did_norm:
        return None, None
    candidates = [did_norm]
    if not did_norm.startswith("+"):
        candidates.append("+" + did_norm)
    for cand in candidates:
        row = await conn.fetchrow(
            """
            SELECT tenant_id, metadata
            FROM tenant_phone_numbers
            WHERE e164 = $1 AND status = 'verified'
            LIMIT 1
            """,
            cand,
        )
        if row:
            return str(row["tenant_id"]), row["metadata"]
    return None, None


async def _resolve_campaign(conn, tenant_id: str, did_metadata: Any) -> Optional[str]:
    """DID → campaign within a tenant. Prefer ``metadata.campaign_id`` (when it
    is a real campaign of this tenant), else the tenant's most-recent active
    campaign, else any most-recent campaign. Returns None if the tenant has
    no campaigns (caller then binds tenant-only, default agent)."""
    md = _coerce_metadata(did_metadata)
    meta_cid = md.get("campaign_id")
    if meta_cid:
        try:
            uuid.UUID(str(meta_cid))
            row = await conn.fetchrow(
                "SELECT id FROM campaigns WHERE id = $1 AND tenant_id = $2",
                str(meta_cid), tenant_id,
            )
            if row:
                return str(row["id"])
        except (ValueError, TypeError):
            pass  # bad UUID in metadata → ignore, fall through
    row = await conn.fetchrow(
        """
        SELECT id FROM campaigns
        WHERE tenant_id = $1
        ORDER BY (status IN ('running', 'active')) DESC, updated_at DESC
        LIMIT 1
        """,
        tenant_id,
    )
    return str(row["id"]) if row else None


async def resolve_inbound_route(
    db_pool,
    *,
    called_did: Optional[str],
    context: Optional[str],
    environment: str,
) -> InboundRoute:
    """Resolve an inbound call to (tenant, campaign) (async, DB-backed).

    Fail-closed: any DB error still routes purely on the trusted context
    signal (from the Asterisk ``identify`` match) when present, and otherwise
    honours the strict flag — it NEVER routes to an arbitrary tenant.
    """
    del environment  # strictness is env-driven via the flag below
    strict = strict_inbound_enabled()
    context_tenant = parse_tenant_from_context(context)
    did_norm = normalize_did(called_did)

    did_tenant: Optional[str] = None
    campaign_id: Optional[str] = None

    try:
        from app.core.db_utils import acquire_with_tenant

        # Cross-tenant read: at inbound time the tenant is unknown (that's
        # what we're resolving), and e164 is globally unique — so bypass RLS.
        async with acquire_with_tenant(db_pool, None) as conn:
            did_tenant, did_metadata = await _lookup_did_tenant(conn, did_norm)

            # Only resolve a campaign when we have a single agreed tenant.
            effective_tenant = context_tenant or did_tenant
            conflict = bool(
                context_tenant and did_tenant and context_tenant != did_tenant
            )
            if effective_tenant and not conflict:
                campaign_id = await _resolve_campaign(
                    conn, effective_tenant, did_metadata,
                )
    except Exception as exc:  # noqa: BLE001 — fail-closed, never mis-route
        logger.error(
            "inbound_resolve_failed did=%s context=%s err=%s — "
            "routing on trusted context only",
            did_norm, context, exc,
        )

    route = decide_inbound_route(
        context_tenant_id=context_tenant,
        did_tenant_id=did_tenant,
        campaign_id=campaign_id,
        strict=strict,
    )
    logger.info(
        "inbound_resolved did=%s context_tenant=%s did_tenant=%s campaign=%s "
        "resolved=%s rejected=%s fallback=%s reason=%s",
        did_norm, context_tenant, did_tenant, campaign_id,
        route.resolved, route.rejected, route.fallback, route.reason,
    )
    return route

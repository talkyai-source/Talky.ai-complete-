"""Bind an internal PBX extension to a tenant's inbound campaign.

The Settings UI can create the SIP trunk for an extension (940003 and friends),
but a trunk alone is only a registration. Two more facts have to exist before a
call to that extension can be answered, and both are tenant-scoped:

1. the trunk must declare itself an internal extension
   (``metadata.role = "extension"``) — the same flag that keeps it out of
   outbound trunk selection, so provisioning one can never re-route a running
   outbound campaign; and
2. an active ``inbound_did_assignments`` row (addressed by ``extension``
   rather than ``canonical_did``) must name the campaign and
   inbound config that should answer it.

This script writes both, in one transaction, under the tenant's own RLS context
and with an explicit ``tenant_id`` predicate on every statement. It refuses
rather than repairs: a campaign that is not this tenant's, not inbound, or has
no active config is an error, not something to guess around.

It is deliberately idempotent — re-running with the same arguments re-points the
same binding instead of creating a second one, because
``uq_inbound_live_extension`` would reject the duplicate anyway.

Usage (on the server, with backend/.env loaded):

    venv/bin/python scripts/bind_inbound_extension.py \\
        --tenant-id <uuid> --actor-user-id <uuid> \\
        --extension 940003 --campaign-id <uuid> [--activate]

``--activate`` also flips the trunk and the binding live. Without it both are
written ``paused`` so an operator can review the rendered dialplan first.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Run as `venv/bin/python scripts/bind_inbound_extension.py` — Python puts
# scripts/ on sys.path, not backend/, so make the app package importable (same
# as the other backend/scripts entry points).
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.domain.services.telephony.inbound_address import (  # noqa: E402
    parse_extension,
    canonical_extension,
)

# Every statement carries its own tenant_id predicate. RLS has been decorative
# on this database before (the app role was superuser+BYPASSRLS for months), so
# isolation is asserted in SQL, not delegated to a policy.
TRUNK_SQL = """
SELECT id, trunk_name, auth_username, direction, is_active, metadata,
       live_registration_status
FROM tenant_sip_trunks
WHERE tenant_id = $1::uuid AND auth_username = $2
"""

CAMPAIGN_SQL = """
SELECT c.id AS campaign_id,
       c.name AS campaign_name,
       c.status AS campaign_status,
       c.direction,
       cfg.id AS config_id,
       cfg.status AS config_status,
       cfg.greeting
FROM campaigns c
JOIN inbound_campaign_configs cfg
  ON cfg.campaign_id = c.id AND cfg.tenant_id = c.tenant_id
WHERE c.id = $1::uuid
  AND c.tenant_id = $2::uuid
  AND cfg.status <> 'archived'
"""

MARK_TRUNK_SQL = """
UPDATE tenant_sip_trunks
SET metadata = COALESCE(metadata, '{}'::jsonb) || $3::jsonb,
    is_active = CASE WHEN $4 THEN TRUE ELSE is_active END,
    updated_by = $5::uuid,
    updated_at = NOW()
WHERE id = $1::uuid AND tenant_id = $2::uuid
RETURNING id, is_active, metadata
"""

UPSERT_BINDING_SQL = """
INSERT INTO inbound_did_assignments (
    tenant_id, extension, canonical_did, phone_number_id,
    sip_trunk_id, campaign_id, config_id,
    status, created_by, updated_by
)
VALUES ($1::uuid, $2, NULL, NULL, $3::uuid, $4::uuid, $5::uuid, $6, $7::uuid, $7::uuid)
RETURNING id, status, version
"""

# Cross-tenant check. tenant_sip_trunks has NO unique index on auth_username,
# so another tenant can hold a trunk claiming the same carrier account. Needs a
# bypass read: the point is to see rows this tenant must not see.
FOREIGN_TRUNK_SQL = """
SELECT tenant_id, trunk_name
FROM tenant_sip_trunks
WHERE auth_username = $1 AND tenant_id <> $2::uuid AND is_active
LIMIT 1
"""

ARCHIVE_PRIOR_SQL = """
UPDATE inbound_did_assignments
SET status = 'archived', updated_by = $3::uuid, updated_at = NOW()
WHERE tenant_id = $1::uuid AND extension = $2 AND status <> 'archived'
RETURNING id
"""


def _fail(message: str) -> None:
    raise SystemExit(f"refused: {message}")


def _reviewed_public_accounts() -> set:
    """Carrier accounts the operator reviewed as PUBLIC DID mappings.

    Same file the reconciler reads. An account listed there belongs to a real
    phone number and must never be re-used as an internal extension.
    """
    path = _BACKEND_ROOT.parent / "telephony" / "asterisk" / "conf" / "verified-carrier-account-dids.json"
    try:
        return set(json.loads(path.read_text(encoding="utf-8")).keys())
    except (OSError, ValueError):
        # Unreadable inventory must not silently permit the collision.
        _fail(f"cannot read the reviewed carrier inventory at {path}")


async def bind_extension(
    pool,
    *,
    tenant_id: str,
    actor_user_id: str,
    extension: str,
    campaign_id: Optional[str],
    activate: bool,
    mark_only: bool = False,
) -> Dict[str, Any]:
    """Mark the trunk as an extension and (unless ``mark_only``) bind it.

    ``mark_only`` exists for the step before a campaign is ready: it declares
    the trunk an internal extension and can activate it, so the account
    registers with the carrier and an inbound INVITE becomes observable —
    without inventing a campaign binding. With no binding the reconciler
    renders no route and the call is refused on the fail-closed catch-all,
    which is the correct behaviour and still proves the delivery path.

    Marking is what makes activation safe: an extension trunk is excluded from
    outbound trunk selection, so activating it cannot move a running outbound
    campaign onto a PBX account.
    """
    from app.core.db_utils import acquire_with_tenant

    digits = parse_extension(canonical_extension(extension))
    if digits is None:
        _fail(f"{extension!r} is not a valid internal extension (3-8 digits)")
    if not mark_only and not campaign_id:
        _fail("--campaign-id is required unless --mark-only is given")

    # Cross-tenant check runs on its OWN bypass connection, before the tenant
    # transaction opens. `SET LOCAL app.bypass_rls` persists for the rest of a
    # transaction, so doing it inline would leave every following write — the
    # trunk UPDATE and the binding INSERT — running with RLS bypassed, which is
    # exactly the isolation this script claims to keep.
    async with acquire_with_tenant(pool, None) as bypass_conn:
        foreign = await bypass_conn.fetchrow(FOREIGN_TRUNK_SQL, digits, tenant_id)
    foreign_holder = foreign["tenant_id"] if foreign is not None else None

    async with acquire_with_tenant(pool, str(tenant_id)) as conn:
        async with conn.transaction():
            trunk = await conn.fetchrow(TRUNK_SQL, tenant_id, digits)
            if trunk is None:
                _fail(
                    f"tenant {str(tenant_id)[:8]} has no SIP trunk whose carrier "
                    f"account is {digits} — create the trunk first"
                )
            if str(trunk["direction"]).lower() not in {"inbound", "both"}:
                _fail(
                    f"trunk {trunk['trunk_name']} is direction={trunk['direction']}; "
                    "an extension that answers calls must be inbound or both"
                )
            meta = trunk["metadata"]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except ValueError:
                    meta = {}
            if not bool((meta or {}).get("register")):
                _fail(
                    f"trunk {trunk['trunk_name']} has registration disabled "
                    "(metadata.register is not true). Asterisk would emit no "
                    "registration section, never REGISTER, and the carrier would "
                    "hold no contact to deliver a call to — while the trunk still "
                    "reports healthy. Enable registration on the trunk first."
                )
            if digits in _reviewed_public_accounts():
                _fail(
                    f"{digits} is a reviewed PUBLIC carrier account in "
                    "verified-carrier-account-dids.json. Binding it as an internal "
                    "extension would take a real DID out of service."
                )
            if foreign_holder is not None:
                _fail(
                    f"carrier account {digits} is also held by an active trunk on "
                    f"tenant {str(foreign_holder)[:8]}. Extension digits are "
                    "unique across the whole carrier namespace, so exactly one "
                    "tenant may answer them."
                )

            if mark_only:
                marked = await conn.fetchrow(
                    MARK_TRUNK_SQL,
                    trunk["id"],
                    tenant_id,
                    json.dumps({"role": "extension"}),
                    bool(activate),
                    actor_user_id,
                )
                return {
                    "extension": digits,
                    "trunk_id": str(trunk["id"]),
                    "trunk_name": trunk["trunk_name"],
                    "trunk_active": bool(marked["is_active"]),
                    "campaign_id": None,
                    "campaign_name": "(none — mark only)",
                    "campaign_status": "-",
                    "config_id": None,
                    "config_status": "-",
                    "binding_id": None,
                    "binding_status": "none",
                    "replaced_bindings": 0,
                }

            campaign = await conn.fetchrow(CAMPAIGN_SQL, campaign_id, tenant_id)
            if campaign is None:
                _fail(
                    "no inbound campaign with a live config for that id on this "
                    "tenant (wrong tenant, wrong campaign, or config archived)"
                )
            if str(campaign["direction"]).lower() != "inbound":
                _fail(
                    f"campaign {campaign['campaign_name']!r} is "
                    f"direction={campaign['direction']}; only an inbound campaign "
                    "can answer an extension"
                )

            marked = await conn.fetchrow(
                MARK_TRUNK_SQL,
                trunk["id"],
                tenant_id,
                json.dumps({"role": "extension"}),
                bool(activate),
                actor_user_id,
            )

            archived = await conn.fetch(ARCHIVE_PRIOR_SQL, tenant_id, digits, actor_user_id)
            binding = await conn.fetchrow(
                UPSERT_BINDING_SQL,
                tenant_id,
                digits,
                trunk["id"],
                campaign["campaign_id"],
                campaign["config_id"],
                "active" if activate else "paused",
                actor_user_id,
            )

    return {
        "extension": digits,
        "trunk_id": str(trunk["id"]),
        "trunk_name": trunk["trunk_name"],
        "trunk_active": bool(marked["is_active"]),
        "campaign_id": str(campaign["campaign_id"]),
        "campaign_name": campaign["campaign_name"],
        "campaign_status": campaign["campaign_status"],
        "config_id": str(campaign["config_id"]),
        "config_status": campaign["config_status"],
        "binding_id": str(binding["id"]),
        "binding_status": binding["status"],
        "replaced_bindings": len(archived),
    }


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set (load backend/.env first)")

    from app.core.db import _register_jsonb_codecs

    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=2, init=_register_jsonb_codecs
    )
    try:
        result = await bind_extension(
            pool,
            tenant_id=args.tenant_id,
            actor_user_id=args.actor_user_id,
            extension=args.extension,
            campaign_id=args.campaign_id,
            activate=args.activate,
            mark_only=args.mark_only,
        )
    finally:
        await pool.close()

    for key in (
        "extension",
        "trunk_name",
        "trunk_active",
        "campaign_name",
        "campaign_status",
        "config_status",
        "binding_status",
        "replaced_bindings",
    ):
        print(f"{key}={result[key]}")
    print(f"binding_id={result['binding_id']}")
    if result["binding_status"] == "none":
        print(
            "marked as an internal extension"
            + (" and activated" if result["trunk_active"] else "")
            + "; no route will render until a campaign binding exists"
        )
    elif result["binding_status"] == "active" and result["trunk_active"]:
        print(
            "next: run reconcile_asterisk_release.sh so the dialplan renders "
            f"exten => {result['extension']}"
        )
    else:
        print("next: re-run with --activate once the review looks right")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument(
        "--actor-user-id",
        required=True,
        help="tenant admin recorded as created_by/updated_by",
    )
    parser.add_argument("--extension", required=True, help="digits only, e.g. 940003")
    parser.add_argument(
        "--campaign-id",
        help="inbound campaign to answer it (required unless --mark-only)",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="also activate the trunk and make the binding live",
    )
    parser.add_argument(
        "--mark-only",
        action="store_true",
        help=(
            "declare the trunk an internal extension without binding a campaign "
            "— registers the account so an inbound INVITE is observable, and "
            "keeps it out of outbound trunk selection"
        ),
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())

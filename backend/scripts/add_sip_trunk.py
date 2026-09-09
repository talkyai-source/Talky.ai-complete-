"""Add (and activate) a SIP trunk for a tenant exactly as the Settings UI does.

Why this exists (2026-09-09): the owner hands over carrier accounts out of band
(chat) and wants them live before opening the UI. The REST path needs the
tenant admin's own browser session, so this script performs the SAME two
operations as ``POST /telephony/sip/trunks`` + ``POST /trunks/{id}/activate``:

* the request body is validated by the endpoint's own ``SIPTrunkCreateRequest``
  (host validation, auth pair, metadata normalisation);
* the password is encrypted with the same ``get_encryption_service()``;
* the INSERT is the endpoint's column list, executed with the tenant RLS
  context installed, ``created_by/updated_by`` = the tenant admin;
* activation is the endpoint's ``is_active = TRUE`` update.

Nothing here writes Asterisk config. The reconcile step
(``reconcile_asterisk_release.sh``, root) renders ``pjsip.d`` from these rows,
and ``talky-trunk-status.timer`` writes the live registration state back for
the Settings card.

The password is read from ``SIP_TRUNK_PASSWORD`` (env) or ``--password-stdin``;
it is never accepted on the command line and never printed.

Run from ``backend/`` with the production environment loaded::

    set -a; . ./.env; set +a
    printf '%s\n' "$PW" | venv/bin/python scripts/add_sip_trunk.py \
        --tenant-id <uuid> --actor-user-id <uuid> --name blaze-pbx-940001 \
        --domain sip3.blazedigitel.com --username 940001 --password-stdin \
        --register --activate
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Dict, Optional

# The endpoint's INSERT, verbatim. Kept as a module constant so a unit test can
# prove this script and the API write the same columns.
INSERT_SQL = """
INSERT INTO tenant_sip_trunks (
    tenant_id,
    trunk_name,
    sip_domain,
    port,
    transport,
    direction,
    auth_username,
    auth_password_encrypted,
    metadata,
    created_by,
    updated_by
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $10)
RETURNING id, trunk_name, is_active
"""

# The endpoint's activate UPDATE (is_active=TRUE branch): the live status is
# reset to 'checking' so the Settings card shows "awaiting proof" until the
# 15-second trunk-status timer writes the real Asterisk registration state.
ACTIVATE_SQL = """
UPDATE tenant_sip_trunks
SET is_active = TRUE,
    live_registration_status = 'checking',
    live_status_detail = 'Awaiting Asterisk runtime proof',
    live_status_checked_at = NOW(),
    updated_by = $3,
    updated_at = NOW()
WHERE tenant_id = $1 AND id = $2
RETURNING id, is_active
"""


def build_request_body(
    *,
    name: str,
    domain: str,
    username: str,
    password: str,
    direction: str = "both",
    port: int = 5060,
    transport: str = "udp",
    register: bool = True,
    caller_id: Optional[str] = None,
) -> Dict[str, Any]:
    """The JSON body the Settings form would POST for these inputs.

    Defaults mirror ``sip-trunks-list.tsx`` (port 5060, udp, both, rfc2833,
    srtp off, 3600 s register interval). ``register`` defaults to True here
    because a carrier login that never registers cannot place or take calls.
    """
    metadata: Dict[str, Any] = {
        "register": bool(register),
        "register_interval": 3600,
        "dtmf_mode": "rfc2833",
        "srtp": False,
    }
    if caller_id:
        metadata["caller_id"] = caller_id
    return {
        "trunk_name": name,
        "sip_domain": domain,
        "port": port,
        "transport": transport,
        "direction": direction,
        "auth_username": username,
        "auth_password": password,
        "metadata": metadata,
    }


def canonical_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    """Validate through the endpoint's schema and canonicalise like the endpoint."""
    from app.api.v1.endpoints.telephony_sip._shared import _canonical_domain
    from app.api.v1.endpoints.telephony_sip.schemas import SIPTrunkCreateRequest

    payload = SIPTrunkCreateRequest(**body)
    return {
        "trunk_name": payload.trunk_name.strip(),
        "sip_domain": _canonical_domain(payload.sip_domain),
        "port": payload.port,
        "transport": payload.transport.value,
        "direction": payload.direction.value,
        "auth_username": payload.auth_username,
        "auth_password": payload.auth_password,
        "metadata": payload.metadata,
    }


async def create_trunk(
    pool,
    *,
    tenant_id: str,
    actor_user_id: str,
    body: Dict[str, Any],
    activate: bool,
) -> Dict[str, Any]:
    from app.core.db_utils import acquire_with_tenant
    from app.core.tenant_rls import apply_tenant_rls_context
    from app.infrastructure.connectors.encryption import get_encryption_service

    canon = canonical_payload(body)
    encrypted = get_encryption_service().encrypt(canon["auth_password"]) if canon["auth_password"] else None

    async with acquire_with_tenant(pool, tenant_id, user_id=actor_user_id) as conn:
        await apply_tenant_rls_context(conn, tenant_id, actor_user_id, request_id=None)
        async with conn.transaction():
            row = await conn.fetchrow(
                INSERT_SQL,
                tenant_id,
                canon["trunk_name"],
                canon["sip_domain"],
                canon["port"],
                canon["transport"],
                canon["direction"],
                canon["auth_username"],
                encrypted,
                canon["metadata"],
                actor_user_id,
            )
            result = {"id": str(row["id"]), "trunk_name": row["trunk_name"], "is_active": bool(row["is_active"])}
            if activate and not result["is_active"]:
                updated = await conn.fetchrow(ACTIVATE_SQL, tenant_id, row["id"], actor_user_id)
                result["is_active"] = bool(updated["is_active"])
    return result


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        value = sys.stdin.readline().rstrip("\r\n")
    else:
        value = os.environ.get("SIP_TRUNK_PASSWORD", "")
    if not value:
        raise SystemExit("no password: use --password-stdin or SIP_TRUNK_PASSWORD (never argv)")
    return value


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set (load backend/.env first)")
    password = _read_password(args)
    body = build_request_body(
        name=args.name,
        domain=args.domain,
        username=args.username,
        password=password,
        direction=args.direction,
        port=args.port,
        transport=args.transport,
        register=args.register,
        caller_id=args.caller_id,
    )
    # Same jsonb codec the app pool installs: the INSERT binds `metadata` as a
    # dict and relies on the codec to json.dumps it (a plain pool would raise
    # "expected str, got dict").
    from app.core.db import _register_jsonb_codecs

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, init=_register_jsonb_codecs)
    try:
        result = await create_trunk(
            pool,
            tenant_id=args.tenant_id,
            actor_user_id=args.actor_user_id,
            body=body,
            activate=args.activate,
        )
    finally:
        await pool.close()
    print(f"trunk_id={result['id']} name={result['trunk_name']} is_active={result['is_active']}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--actor-user-id", required=True, help="tenant admin recorded as created_by/updated_by")
    parser.add_argument("--name", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--direction", default="both", choices=("inbound", "outbound", "both"))
    parser.add_argument("--port", type=int, default=5060)
    parser.add_argument("--transport", default="udp", choices=("udp", "tcp", "tls"))
    parser.add_argument("--register", action="store_true", help="metadata.register=true (default false, like the UI)")
    parser.add_argument("--caller-id", default=None)
    parser.add_argument("--activate", action="store_true", help="also set is_active=TRUE (the UI's Activate button)")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

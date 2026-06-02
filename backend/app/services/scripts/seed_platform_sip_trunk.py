"""Seed the platform-default SIP trunk into every tenant.

The platform ships a shared upstream SIP trunk (e.g. Blaze Digitel) that
every tenant should be able to dial out through unless they bring their
own. The credentials live in env vars (PLATFORM_SIP_*) and are inserted
into each tenant's ``tenant_sip_trunks`` row at runtime so:

  • RLS + per-tenant encryption properties stay intact
    (the trunk row is scoped to the tenant; password Fernet-encrypted)
  • Tenants who add their own trunk simply have a second row that they
    can mark active instead
  • Rotating the upstream password is a single env var change plus a
    re-run of this script (which UPDATEs the encrypted blob in place)

Idempotent: re-running just updates the encrypted password and connection
fields on the existing matching row (tenant_id + trunk_name uniqueness),
and only flips ``tenants.active_telephony_provider`` to 'sip' for tenants
that currently have 'none'. Never overrides a tenant who has explicitly
picked twilio / vonage / a different SIP trunk.

Usage::

    cd /opt/talky/backend
    source venv/bin/activate
    export $(grep -E '^(DATABASE_URL|PLATFORM_SIP_|CONNECTOR_ENCRYPTION_KEY)=' .env | xargs)
    python -m app.services.scripts.seed_platform_sip_trunk
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import asyncpg

logger = logging.getLogger("seed_platform_sip_trunk")


def _read_platform_env() -> dict:
    """Read PLATFORM_SIP_* env vars. Empty values → seed is skipped."""
    return {
        "domain": (os.getenv("PLATFORM_SIP_DOMAIN") or "").strip(),
        "port": int(os.getenv("PLATFORM_SIP_PORT", "5060") or "5060"),
        "transport": (os.getenv("PLATFORM_SIP_TRANSPORT", "udp") or "udp").lower(),
        "username": os.getenv("PLATFORM_SIP_USERNAME") or "",
        "password": os.getenv("PLATFORM_SIP_PASSWORD") or "",
        "trunk_name": (os.getenv("PLATFORM_SIP_TRUNK_NAME") or "platform-default").strip(),
    }


async def seed_for_tenant(
    conn: asyncpg.Connection,
    tenant_id: str,
    *,
    flip_active_provider: bool = True,
) -> None:
    """
    Seed the platform-default SIP trunk into a single tenant. Called from
    the new-tenant signup hook so every new tenant gets the upstream trunk
    out of the box.

    Idempotent. Never raises — logs and swallows so a transient seed
    failure can't break signup. Caller already owns the transaction.
    """
    cfg = _read_platform_env()
    if not cfg["domain"] or not cfg["username"] or not cfg["password"]:
        logger.info("Platform SIP env not configured — skipping seed for tenant %s",
                    str(tenant_id)[:8])
        return
    try:
        from app.infrastructure.connectors.encryption import get_encryption_service
        enc = get_encryption_service().encrypt(cfg["password"])
        existing = await conn.fetchrow(
            "SELECT id FROM tenant_sip_trunks "
            "WHERE tenant_id = $1 AND lower(trunk_name) = lower($2)",
            tenant_id, cfg["trunk_name"],
        )
        if existing:
            await conn.execute(
                """
                UPDATE tenant_sip_trunks
                SET sip_domain = $1, port = $2, transport = $3, direction = 'both',
                    auth_username = $4, auth_password_encrypted = $5,
                    is_active = TRUE, updated_at = NOW()
                WHERE id = $6
                """,
                cfg["domain"], cfg["port"], cfg["transport"],
                cfg["username"], enc, existing["id"],
            )
        else:
            await conn.execute(
                """
                INSERT INTO tenant_sip_trunks
                    (tenant_id, trunk_name, sip_domain, port, transport, direction,
                     auth_username, auth_password_encrypted, is_active, metadata)
                VALUES ($1, $2, $3, $4, $5, 'both', $6, $7, TRUE, '{}'::jsonb)
                """,
                tenant_id, cfg["trunk_name"], cfg["domain"], cfg["port"],
                cfg["transport"], cfg["username"], enc,
            )
        if flip_active_provider:
            await conn.execute(
                "UPDATE tenants SET active_telephony_provider = 'sip' "
                "WHERE id = $1 AND (active_telephony_provider IS NULL OR active_telephony_provider = 'none')",
                tenant_id,
            )
    except Exception as exc:
        logger.warning("seed_for_tenant failed for %s: %s", str(tenant_id)[:8], exc)


async def seed_for_pool(
    pool: asyncpg.Pool,
    *,
    flip_active_provider: bool = True,
) -> dict:
    """Run the seed against an open asyncpg pool. Returns a stats dict.

    Idempotent and safe to re-run. Designed to be called from:
      • the standalone script entrypoint below
      • the new-tenant signup hook in auth.py
      • a manual admin endpoint if we ever build one
    """
    cfg = _read_platform_env()
    if not cfg["domain"] or not cfg["username"] or not cfg["password"]:
        logger.warning(
            "PLATFORM_SIP_* env vars missing — skipping seed. "
            "domain=%r username=%r password=%s",
            cfg["domain"], cfg["username"], "set" if cfg["password"] else "MISSING",
        )
        return {"seeded": 0, "updated": 0, "active_flipped": 0, "skipped": "missing_env"}

    if cfg["transport"] not in ("udp", "tcp", "tls"):
        raise SystemExit(f"PLATFORM_SIP_TRANSPORT must be udp|tcp|tls (got {cfg['transport']!r})")
    if cfg["port"] < 1 or cfg["port"] > 65535:
        raise SystemExit(f"PLATFORM_SIP_PORT out of range (got {cfg['port']})")

    # Encrypt the password the same way the API does.
    from app.infrastructure.connectors.encryption import get_encryption_service
    enc = get_encryption_service().encrypt(cfg["password"])

    seeded = 0
    updated = 0
    active_flipped = 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls = 'true'")
            tenants = await conn.fetch("SELECT id, active_telephony_provider FROM tenants")
            for t in tenants:
                tenant_id = t["id"]

                # Upsert by (tenant_id, lower(trunk_name)) — matches the
                # existing unique index on tenant_sip_trunks.
                existing = await conn.fetchrow(
                    """
                    SELECT id FROM tenant_sip_trunks
                    WHERE tenant_id = $1 AND lower(trunk_name) = lower($2)
                    """,
                    tenant_id, cfg["trunk_name"],
                )
                if existing:
                    await conn.execute(
                        """
                        UPDATE tenant_sip_trunks
                        SET sip_domain = $1,
                            port = $2,
                            transport = $3,
                            direction = 'both',
                            auth_username = $4,
                            auth_password_encrypted = $5,
                            is_active = TRUE,
                            updated_at = NOW()
                        WHERE id = $6
                        """,
                        cfg["domain"], cfg["port"], cfg["transport"],
                        cfg["username"], enc, existing["id"],
                    )
                    updated += 1
                else:
                    await conn.execute(
                        """
                        INSERT INTO tenant_sip_trunks
                            (tenant_id, trunk_name, sip_domain, port, transport,
                             direction, auth_username, auth_password_encrypted,
                             is_active, metadata)
                        VALUES ($1, $2, $3, $4, $5, 'both', $6, $7, TRUE, '{}'::jsonb)
                        """,
                        tenant_id, cfg["trunk_name"], cfg["domain"], cfg["port"],
                        cfg["transport"], cfg["username"], enc,
                    )
                    seeded += 1

                # Only flip the active pointer for tenants who haven't
                # made an explicit choice yet — never override twilio/vonage.
                if flip_active_provider and (t["active_telephony_provider"] in (None, "none")):
                    await conn.execute(
                        "UPDATE tenants SET active_telephony_provider = 'sip' WHERE id = $1",
                        tenant_id,
                    )
                    active_flipped += 1

    logger.info(
        "Platform SIP trunk seed: inserted=%d updated=%d active_flipped=%d "
        "trunk=%s host=%s:%s/%s",
        seeded, updated, active_flipped,
        cfg["trunk_name"], cfg["domain"], cfg["port"], cfg["transport"],
    )
    return {
        "seeded": seeded,
        "updated": updated,
        "active_flipped": active_flipped,
        "trunk_name": cfg["trunk_name"],
        "domain": cfg["domain"],
    }


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set — export it before running.", file=sys.stderr)
        sys.exit(2)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        result = await seed_for_pool(pool)
        print(result)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

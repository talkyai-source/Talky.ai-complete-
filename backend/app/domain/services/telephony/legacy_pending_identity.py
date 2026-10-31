"""Recover legacy missing identity only from durable primary-leg evidence."""
import logging

logger = logging.getLogger(__name__)


async def reconcile_pending_identity(conn, provider: str) -> None:
    """Caller holds an acquire_with_tenant(pool, None) transaction.

    This restores identity, never termination or billing. The existing recovery
    path must subsequently prove all provider legs absent and settle the call.
    Conflicting/missing proof remains pending and becomes an operator warning.
    """
    if provider != "asterisk":
        raise ValueError("legacy identity recovery supports only asterisk")
    result = await conn.execute(
        """
        WITH identity AS (
            SELECT c.id, c.tenant_id,
                   COALESCE(NULLIF(BTRIM(c.provider_call_id),''),
                            NULLIF(BTRIM(c.external_call_uuid),'')) AS provider_call_id
            FROM calls c
            WHERE c.status='termination_pending'
              AND c.direction='outbound'
              AND NULLIF(BTRIM(c.provider),'') IS NULL
              AND EXISTS (
                  SELECT 1 FROM call_legs leg
                  WHERE leg.call_id=c.id AND leg.leg_type='pstn_outbound'
                    AND leg.provider_leg_id=COALESCE(NULLIF(BTRIM(c.provider_call_id),''),
                                                    NULLIF(BTRIM(c.external_call_uuid),''))
                    AND LOWER(BTRIM(leg.provider))=$1
              )
              AND NOT EXISTS (
                  SELECT 1 FROM call_legs leg
                  WHERE leg.call_id=c.id AND leg.leg_type='pstn_outbound'
                    AND leg.provider_leg_id=COALESCE(NULLIF(BTRIM(c.provider_call_id),''),
                                                    NULLIF(BTRIM(c.external_call_uuid),''))
                    AND LOWER(BTRIM(leg.provider)) IS DISTINCT FROM $1
              )
            ORDER BY c.updated_at, c.id
            LIMIT 32 FOR UPDATE OF c SKIP LOCKED
        )
        UPDATE calls c
        SET provider=$1, provider_call_id=identity.provider_call_id
        FROM identity
        WHERE c.id=identity.id AND c.tenant_id=identity.tenant_id
        """,
        provider,
    )
    if result != "UPDATE 0":
        logger.info("termination_pending_identity_recovered result=%s", result)
    missing = await conn.fetchval(
        """
        SELECT count(*) FROM calls
        WHERE status='termination_pending'
          AND (NULLIF(BTRIM(provider),'') IS NULL OR
               COALESCE(NULLIF(BTRIM(provider_call_id),''),
                        NULLIF(BTRIM(external_call_uuid),'')) IS NULL)
        """
    )
    if missing:
        logger.warning("termination_pending_unrecoverable_identity count=%d", missing)

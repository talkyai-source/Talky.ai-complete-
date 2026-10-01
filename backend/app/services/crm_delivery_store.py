"""Small Postgres outbox for CRM work; no network writes inside transactions."""
from uuid import uuid4

from app.core.db_utils import acquire_with_tenant

MAX_ATTEMPTS = 6


class CRMDeliveryStore:
    def __init__(self, pool):
        self.pool = pool

    async def enqueue(self, tenant_id, call_id, provider, desired_key, *, legacy_id=None):
        # A historical shared ID has no durable owner. Preserve it for review;
        # never reuse it for either destination or repeat an uncertain create.
        async with acquire_with_tenant(self.pool, tenant_id) as conn:
            return dict(await conn.fetchrow("""
                INSERT INTO crm_deliveries (tenant_id, call_id, provider, desired_key, status, phase, last_error)
                VALUES ($1::uuid,$2::uuid,$3,$4,
                    CASE WHEN $5::text IS NULL THEN 'pending' ELSE 'unknown' END,
                    CASE WHEN $5::text IS NULL THEN 'pending' ELSE 'legacy_unverified' END,
                    CASE WHEN $5::text IS NULL THEN NULL ELSE 'Legacy shared CRM ID requires provider ownership review' END)
                ON CONFLICT (tenant_id,call_id,provider) DO UPDATE SET
                    desired_key = EXCLUDED.desired_key,
                    status = CASE WHEN crm_deliveries.status IN ('processing','unknown') THEN crm_deliveries.status
                        WHEN crm_deliveries.completed_key = EXCLUDED.desired_key THEN 'succeeded'
                        WHEN crm_deliveries.desired_key IS DISTINCT FROM EXCLUDED.desired_key THEN 'pending'
                        ELSE crm_deliveries.status END,
                    attempts = CASE WHEN crm_deliveries.status NOT IN ('processing','unknown')
                        AND crm_deliveries.desired_key IS DISTINCT FROM EXCLUDED.desired_key THEN 0 ELSE crm_deliveries.attempts END,
                    next_attempt_at = CASE WHEN crm_deliveries.status NOT IN ('processing','unknown')
                        AND crm_deliveries.desired_key IS DISTINCT FROM EXCLUDED.desired_key THEN NOW() ELSE crm_deliveries.next_attempt_at END,
                    updated_at = NOW()
                RETURNING *
            """, tenant_id, call_id, provider, desired_key, str(legacy_id) if legacy_id else None))

    async def claim(self, tenant_id, call_id, provider):
        async with acquire_with_tenant(self.pool, tenant_id) as conn:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    SELECT * FROM crm_deliveries
                     WHERE tenant_id=$1::uuid AND call_id=$2::uuid AND provider=$3
                       AND attempts < $4 AND next_attempt_at <= NOW()
                       AND (status IN ('pending','unknown') OR (status='processing' AND lease_expires_at < NOW()))
                     FOR UPDATE SKIP LOCKED
                """, tenant_id, call_id, provider, MAX_ATTEMPTS)
                if row is None:
                    return None
                receipt = dict(row)
                receipt['reconcile'] = receipt['status'] == 'unknown' or (
                    receipt['status'] == 'processing' and receipt['phase'] in ('creating_contact','creating_call'))
                token = str(uuid4())
                await conn.execute("""
                    UPDATE crm_deliveries SET status='processing', attempts=attempts+1,
                        lease_token=$4::uuid, lease_expires_at=NOW()+INTERVAL '2 minutes', updated_at=NOW()
                     WHERE tenant_id=$1::uuid AND call_id=$2::uuid AND provider=$3
                """, tenant_id, call_id, provider, token)
                receipt.update(lease_token=token, attempts=receipt['attempts']+1)
                return receipt

    async def save(self, receipt, *, phase=None, status=None, contact_id=None, call_id=None, error=None):
        async with acquire_with_tenant(self.pool, receipt['tenant_id']) as conn:
            result = await conn.execute("""
                UPDATE crm_deliveries SET phase=COALESCE($5,phase),
                    status=CASE WHEN $6='succeeded' AND desired_key IS DISTINCT FROM $10 THEN 'pending'
                                ELSE COALESCE($6,status) END,
                    remote_contact_id=COALESCE($7,remote_contact_id), remote_call_id=COALESCE($8,remote_call_id),
                    last_error=$9,
                    completed_key=CASE WHEN $6='succeeded' THEN $10 ELSE completed_key END,
                    next_attempt_at=CASE WHEN $6 IN ('pending','unknown') THEN NOW()+($11 * INTERVAL '1 second') ELSE NOW() END,
                    lease_token=CASE WHEN $6 IS NULL THEN lease_token ELSE NULL END,
                    lease_expires_at=CASE WHEN $6 IS NULL THEN lease_expires_at ELSE NULL END,
                    updated_at=NOW()
                 WHERE tenant_id=$1::uuid AND call_id=$2::uuid AND provider=$3 AND lease_token=$4::uuid
            """, str(receipt['tenant_id']), str(receipt['call_id']), receipt['provider'], receipt['lease_token'],
                phase, status, contact_id, call_id, error, receipt['desired_key'],
                min(30 * 2 ** (receipt['attempts']-1), 900))
            if result != 'UPDATE 1':
                raise RuntimeError('CRM delivery lease was lost')
        if phase:
            receipt['phase'] = phase
        if contact_id:
            receipt['remote_contact_id'] = contact_id
        if call_id:
            receipt['remote_call_id'] = call_id

    async def due(self, limit=20):
        # Worker is the only cross-tenant reader; delivery writes remain scoped.
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                rows = await conn.fetch("""
                    SELECT DISTINCT tenant_id, call_id FROM crm_deliveries
                    WHERE attempts < $1 AND next_attempt_at <= NOW()
                      AND (status IN ('pending','unknown') OR (status='processing' AND lease_expires_at < NOW()))
                    LIMIT $2
                """, MAX_ATTEMPTS, limit)
                return [dict(row) for row in rows]

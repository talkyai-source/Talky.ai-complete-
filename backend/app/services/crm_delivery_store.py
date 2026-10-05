"""Small Postgres outbox for CRM work; no network writes inside transactions."""
import json
from uuid import uuid4

from app.core.db_utils import acquire_with_tenant

MAX_ATTEMPTS = 6


class CRMDeliveryStore:
    def __init__(self, pool):
        self.pool = pool

    async def enqueue(self, tenant_id, call_id, provider, desired_key, *, legacy_id=None, source_revision=None):
        # A historical shared ID has no durable owner. Preserve it for review;
        # never reuse it for either destination or repeat an uncertain create.
        async with acquire_with_tenant(self.pool, tenant_id) as conn:
            source_current = await self._source_current(conn, tenant_id, call_id, source_revision)
            # A summary can commit after the caller reads it but before this
            # enqueue. Keep that work dirty instead of clearing the trigger's
            # invalidation with a hash of an obsolete body.
            if not source_current:
                desired_key = None
            row = dict(await conn.fetchrow("""
                INSERT INTO crm_deliveries (tenant_id, call_id, provider, desired_key, status, phase, last_error)
                VALUES ($1::uuid,$2::uuid,$3,$4,
                    CASE WHEN $5::text IS NULL THEN 'pending' ELSE 'unknown' END,
                    CASE WHEN $5::text IS NULL THEN 'pending' ELSE 'legacy_unverified' END,
                    CASE WHEN $5::text IS NULL THEN NULL ELSE 'Legacy shared CRM ID requires provider ownership review' END)
                ON CONFLICT (tenant_id,call_id,provider) DO UPDATE SET
                    desired_key = EXCLUDED.desired_key,
                    status = CASE WHEN crm_deliveries.status IN ('processing','unknown') THEN crm_deliveries.status
                        WHEN EXCLUDED.desired_key IS NULL THEN 'pending'
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
            row['source_current'] = source_current
            return row

    @staticmethod
    async def _source_current(conn, tenant_id, call_id, source_revision):
        if source_revision is None:
            return True
        current = await conn.fetchval("""
            SELECT xmin::text FROM calls WHERE tenant_id=$1::uuid AND id=$2::uuid FOR SHARE
        """, tenant_id, call_id)
        if current is None:
            raise RuntimeError('CRM source call is unavailable')
        return current == source_revision

    async def claim(self, tenant_id, call_id, provider, *, source_revision=None, expected_key=None):
        async with acquire_with_tenant(self.pool, tenant_id) as conn:
            async with conn.transaction():
                # Lock order matches the call trigger (call, then receipt).
                # After this transaction, a newer summary invalidates the
                # claimed hash, so save(succeeded) leaves it pending again.
                if not await self._source_current(conn, tenant_id, call_id, source_revision):
                    return None
                row = await conn.fetchrow("""
                    SELECT * FROM crm_deliveries
                     WHERE tenant_id=$1::uuid AND call_id=$2::uuid AND provider=$3
                       AND attempts < $4 AND next_attempt_at <= NOW()
                       AND (status IN ('pending','unknown') OR (status='processing' AND lease_expires_at < NOW()))
                     FOR UPDATE SKIP LOCKED
                """, tenant_id, call_id, provider, MAX_ATTEMPTS)
                if row is None or (expected_key is not None and row['desired_key'] != expected_key):
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

    async def bind_destination(self, receipt, connector_id, account_id):
        """Pin before the first effect; never adopt unowned historical IDs."""
        async with acquire_with_tenant(self.pool, receipt['tenant_id']) as conn:
            result = await conn.execute("""
                UPDATE crm_deliveries SET destination_connector_id=$5::uuid,
                    destination_account_id=$6, updated_at=NOW()
                WHERE tenant_id=$1::uuid AND call_id=$2::uuid AND provider=$3 AND lease_token=$4::uuid
                  AND ((destination_connector_id=$5::uuid AND destination_account_id=$6)
                    OR (destination_connector_id IS NULL AND destination_account_id IS NULL
                        AND remote_contact_id IS NULL AND remote_call_id IS NULL
                        AND phase NOT IN ('creating_contact','creating_call','legacy_unverified')))
            """, str(receipt['tenant_id']), str(receipt['call_id']), receipt['provider'],
                receipt['lease_token'], connector_id, account_id)
        if result != 'UPDATE 1':
            return False
        receipt.update(destination_connector_id=connector_id, destination_account_id=account_id)
        return True

    async def bind_contact_effect(self, receipt, effect):
        """Commit original resolution/create arguments before using them.

        A renewed lease cannot replace a previous effect's destination or
        input. Unknown historical effects are held, never backfilled here.
        """
        encoded = json.dumps(effect, sort_keys=True, ensure_ascii=False)
        if not isinstance(effect, dict) or len(encoded.encode("utf-8")) > 32768:
            raise ValueError("CRM contact effect is invalid or exceeds the evidence limit")
        async with acquire_with_tenant(self.pool, receipt["tenant_id"]) as conn:
            result = await conn.execute("""
                UPDATE crm_deliveries SET contact_effect=COALESCE(contact_effect,$5::jsonb),
                    updated_at=NOW()
                 WHERE tenant_id=$1::uuid AND call_id=$2::uuid AND provider=$3
                   AND lease_token=$4::uuid AND status='processing'
                   AND phase NOT IN ('creating_contact','creating_call','legacy_unverified')
                   AND remote_contact_id IS NULL AND remote_call_id IS NULL
                   AND destination_connector_id::text=$5::jsonb->>'connector_id'
                   AND destination_account_id=$5::jsonb->>'account_id'
                   AND EXISTS (SELECT 1 FROM calls c WHERE c.id=crm_deliveries.call_id
                       AND c.tenant_id=crm_deliveries.tenant_id
                       AND c.xmin::text=$5::jsonb->>'source_call_revision')
                   AND (contact_effect IS NULL OR contact_effect=$5::jsonb)
            """, str(receipt["tenant_id"]), str(receipt["call_id"]), receipt["provider"],
                receipt["lease_token"], encoded)
        if result != "UPDATE 1":
            raise RuntimeError("CRM contact create ownership changed; review required")
        receipt.update(contact_effect=effect)

    async def begin_contact_create(self, receipt, effect, *, source_revision=None):
        """The saved original arguments own the irreversible create phase."""
        encoded = json.dumps(effect, sort_keys=True, ensure_ascii=False)
        async with acquire_with_tenant(self.pool, receipt["tenant_id"]) as conn:
            result = await conn.execute("""
                UPDATE crm_deliveries SET phase='creating_contact', updated_at=NOW()
                 WHERE tenant_id=$1::uuid AND call_id=$2::uuid AND provider=$3
                   AND lease_token=$4::uuid AND status='processing'
                   AND phase='resolving_contact' AND contact_effect=$5::jsonb
                   AND remote_contact_id IS NULL AND remote_call_id IS NULL
                   AND destination_connector_id::text=$5::jsonb->>'connector_id'
                   AND destination_account_id=$5::jsonb->>'account_id'
                   AND EXISTS (SELECT 1 FROM calls c WHERE c.id=crm_deliveries.call_id
                       AND c.tenant_id=crm_deliveries.tenant_id
                       AND c.xmin::text=$6::text)
            """, str(receipt["tenant_id"]), str(receipt["call_id"]), receipt["provider"],
                receipt["lease_token"], encoded, source_revision or effect.get("source_call_revision"))
        if result != "UPDATE 1":
            raise RuntimeError("CRM original contact create evidence is unavailable")
        receipt["phase"] = "creating_contact"

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

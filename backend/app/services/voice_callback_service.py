"""Release confirmed due callbacks into the existing dialer; no new worker."""
import json
from uuid import UUID, uuid5

from app.core.db_utils import acquire_with_tenant
from app.domain.models.dialer_job import DialerJob


def _object(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def callback_job_id(action_id):
    """The existing outbox identity, shared with cancellation admission."""
    return str(uuid5(UUID(str(action_id)), "voice_callback"))


async def drain_voice_callbacks(pool, queue, limit=20):
    # This worker-only scan crosses tenants; every mutation below is scoped.
    async with acquire_with_tenant(pool, None) as conn:
        due = await conn.fetch("""
            SELECT id, tenant_id FROM assistant_actions
            WHERE type='schedule_callback' AND triggered_by='voice' AND status='scheduled'
              AND scheduled_at <= NOW() ORDER BY scheduled_at LIMIT $1
        """, limit)
    for item in due:
        tenant, action_id = str(item["tenant_id"]), str(item["id"])
        async with acquire_with_tenant(pool, tenant) as conn:
            row = await conn.fetchrow("""
                SELECT a.*, p.direction, p.status AS campaign_status, p.script_config,
                       l.phone_number, l.do_not_call, l.status AS lead_status,
                       a.scheduled_at < NOW()-INTERVAL '30 minutes' AS expired
                FROM assistant_actions a
                JOIN campaigns p ON p.id=a.campaign_id AND p.tenant_id=a.tenant_id
                JOIN leads l ON l.id=a.lead_id AND l.tenant_id=a.tenant_id AND l.campaign_id=p.id
                WHERE a.id=$1::uuid AND a.tenant_id=$2::uuid AND a.status='scheduled'
                FOR UPDATE OF a SKIP LOCKED
            """, action_id, tenant)
            if not row:
                continue
            row = dict(row)
            payload = _object(row["input_data"])["parameters"]["parameters"]
            brief = _object(_object(row["script_config"]).get("campaign_brief"))
            permitted = (row["direction"] == "outbound" and row["campaign_status"] in {"running", "active"}
                and not row["do_not_call"] and row["lead_status"] != "deleted"
                and row["phone_number"] == payload["phone"]
                and "schedule_callback" in (brief.get("approved_next_actions") or []))
            if not permitted or row["expired"]:
                await conn.execute("""
                    UPDATE assistant_actions SET status='failed', error='callback_policy_changed_or_time_expired',
                        output_data=output_data || '{"success":false,"status":"failed","confirmation_allowed":false}'::jsonb
                    WHERE id=$1::uuid AND tenant_id=$2::uuid
                """, action_id, tenant)
                continue
            job_id = callback_job_id(action_id)
            active = await conn.fetchval("""
                SELECT EXISTS(SELECT 1 FROM dialer_jobs WHERE tenant_id=$1::uuid AND lead_id=$2::uuid
                  AND id<>$3::uuid AND status IN ('pending','queued','processing','calling','retry_scheduled'))
            """, tenant, str(row["lead_id"]), job_id)
            if active:
                continue  # Wait for the original call/job to settle; never dial over it.
            await conn.execute("""
                INSERT INTO dialer_jobs (id,tenant_id,campaign_id,lead_id,phone_number,status,scheduled_at)
                VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,'pending',$6)
                ON CONFLICT (id) DO NOTHING
            """, job_id, tenant, str(row["campaign_id"]), str(row["lead_id"]), payload["phone"], row["scheduled_at"])
            # This transaction owns the action row lock. Make reservation
            # visible before releasing it or awaiting Redis: an admin can no
            # longer truthfully promise cancellation once dispatch may start.
            await conn.execute("""
                UPDATE assistant_actions SET output_data=COALESCE(output_data,'{}'::jsonb)
                    || jsonb_build_object('dialer_job_id',$3::text,'status','dispatching')
                WHERE id=$1::uuid AND tenant_id=$2::uuid AND status='scheduled'
            """, action_id, tenant, job_id)
            job = DialerJob(job_id=job_id, tenant_id=tenant, campaign_id=str(row["campaign_id"]),
                           lead_id=str(row["lead_id"]), phone_number=payload["phone"], scheduled_at=row["scheduled_at"])
        # The DB job exists before Redis, and repeated handoff is idempotent.
        key = "voice-callback-" + action_id
        if not await queue.schedule_job_once(job, idempotency_key=key):
            continue
        async with acquire_with_tenant(pool, tenant) as conn:
            await conn.execute("""
                UPDATE assistant_actions SET status='completed', completed_at=NOW(),
                  output_data=output_data || jsonb_build_object('dialer_job_id',$3::text,'status','queued')
                WHERE id=$1::uuid AND tenant_id=$2::uuid AND status='scheduled'
            """, action_id, tenant, job_id)
            await conn.execute("""
                UPDATE dialer_jobs SET status='queued' WHERE id=$1::uuid AND tenant_id=$2::uuid AND status='pending'
            """, job_id, tenant)
        await queue.confirm_retry_once(key)

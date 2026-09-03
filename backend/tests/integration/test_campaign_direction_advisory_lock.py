"""PostgreSQL proof for the campaign-direction advisory-lock trigger.

Run against a disposable database already upgraded to Alembic head:

    TALKY_MIGRATION_TEST_DATABASE_URL=postgresql://... pytest -q \
        tests/integration/test_campaign_direction_advisory_lock.py
"""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from app.domain.services.campaign_direction_guard import (
    acquire_campaign_direction_lock,
)


@pytest.mark.asyncio
async def test_direction_update_waits_for_knowledge_style_advisory_lease():
    database_url = os.getenv("TALKY_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set TALKY_MIGRATION_TEST_DATABASE_URL to a disposable database")

    setup = await asyncpg.connect(database_url)
    holder = await asyncpg.connect(database_url)
    updater = await asyncpg.connect(database_url)
    observer = await asyncpg.connect(database_url)
    tenant_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    holder_tx = holder.transaction()
    updater_tx = updater.transaction()
    update_task: asyncio.Task[str] | None = None

    try:
        assert (
            await setup.fetchval("SELECT version_num FROM alembic_version")
            == "0043_campaign_direction_lock"
        )
        await setup.execute(
            "INSERT INTO tenants (id, business_name) VALUES ($1, $2)",
            tenant_id,
            "0043 direction-lock integration tenant",
        )
        await setup.execute(
            """
            INSERT INTO campaigns (id, tenant_id, name, direction)
            VALUES ($1, $2, $3, 'outbound')
            """,
            campaign_id,
            tenant_id,
            "0043 direction-lock integration campaign",
        )

        await holder_tx.start()
        # HTTP path parameters may preserve uppercase UUID hex.  The app lock
        # must normalize it to the same canonical text used by NEW.id::text in
        # the database trigger.
        await acquire_campaign_direction_lock(holder, str(campaign_id).upper())
        assert (
            await holder.fetchval(
                "SELECT direction FROM campaigns WHERE id = $1", campaign_id
            )
            == "outbound"
        )
        # The backstop is deliberately narrow: knowledge ingestion may update
        # campaign metadata from another pooled connection while its lease is
        # held, and that non-direction write must not wait on this lock.
        assert (
            await asyncio.wait_for(
                observer.execute(
                    "UPDATE campaigns SET description = $2 WHERE id = $1",
                    campaign_id,
                    "metadata write while direction lease is held",
                ),
                timeout=1.0,
            )
            == "UPDATE 1"
        )

        await updater_tx.start()
        updater_pid = await updater.fetchval("SELECT pg_backend_pid()")
        update_task = asyncio.create_task(
            updater.execute(
                "UPDATE campaigns SET direction = 'inbound' WHERE id = $1",
                campaign_id,
            )
        )
        done, _ = await asyncio.wait({update_task}, timeout=0.25)
        assert not done, "direction update bypassed the held advisory lock"

        wait_state = await observer.fetchrow(
            """
            SELECT wait_event_type, wait_event
            FROM pg_stat_activity
            WHERE pid = $1
            """,
            updater_pid,
        )
        assert wait_state is not None
        assert wait_state["wait_event_type"] == "Lock"
        assert str(wait_state["wait_event"]).lower() == "advisory"
        assert (
            await observer.fetchval(
                "SELECT direction FROM campaigns WHERE id = $1", campaign_id
            )
            == "outbound"
        )

        await holder_tx.commit()
        assert await asyncio.wait_for(update_task, timeout=2.0) == "UPDATE 1"
        update_task = None
        await updater_tx.commit()
        assert (
            await observer.fetchval(
                "SELECT direction FROM campaigns WHERE id = $1", campaign_id
            )
            == "inbound"
        )
    finally:
        if update_task is not None and not update_task.done():
            await holder_tx.rollback()
            update_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await update_task
        if holder.is_in_transaction():
            await holder_tx.rollback()
        if updater.is_in_transaction():
            await updater_tx.rollback()
        await setup.execute("DELETE FROM tenants WHERE id = $1", tenant_id)
        await observer.close()
        await updater.close()
        await holder.close()
        await setup.close()

"""Durable ingestion faults on explicitly disposable local PG, no notifications.

Restricted NOLOGIN/NOBYPASSRLS role; incidents use a random source and the
current-authority check uses one owned administrator profile.
Migration must already be applied. No production access or provider requests.
"""
import asyncio
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastapi import HTTPException
from starlette.requests import Request
from app.api.v1.endpoints import alertmanager as am
from app.api.v1.endpoints.admin.health import incidents

pytestmark = pytest.mark.integration


def notification(*, fingerprint="0123456789abcdef", start=None, resolved=False):
    start = start or datetime.now(timezone.utc) - timedelta(minutes=3)
    return am.Notification.model_validate({"version": "4", "status": "resolved" if resolved else "firing",
        "truncatedAlerts": 0, "receiver": "isolated", "groupKey": "isolated", "alerts": [{
            "fingerprint": fingerprint, "status": "resolved" if resolved else "firing", "startsAt": start.isoformat(),
            "endsAt": (start + timedelta(seconds=30)).isoformat() if resolved else "0001-01-01T00:00:00Z",
            "labels": {"alertname": "IsolatedIngestion", "severity": "critical", "job": "synthetic",
                       "customer_email": "excluded@example.invalid"},
            "annotations": {"summary": "AD08 isolated incident", "description": "Synthetic fault evidence"},
            "generatorURL": "http://169.254.169.254/never-fetch"}]})


@pytest_asyncio.fixture
async def alert_db(monkeypatch):
    dsn = os.getenv("TEST_ADMIN_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit migrated disposable TEST_ADMIN_DATABASE_URL required")
    parsed = urlparse(dsn)
    assert parsed.hostname in {"127.0.0.1", "localhost"} and parsed.path.endswith("_test")
    owner = await asyncpg.connect(dsn)
    role = "ad08_" + uuid4().hex; source = role; faults = []; pool = None
    principal_id = uuid4()
    try:
        assert await owner.fetchval("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='incidents' AND column_name='source_system')"), "Apply 0073 first"
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(f'GRANT SELECT,INSERT,UPDATE ON incidents TO "{role}"')
        # Direct incident handlers still verify the current persisted actor.
        await owner.execute(f'GRANT SELECT ON user_profiles,tenants,tenant_users,roles TO "{role}"')
        await owner.execute("""INSERT INTO user_profiles(id,email,name,role,is_active,is_verified,email_verified_at)
            VALUES($1,$2,'Synthetic Alertmanager incident administrator','platform_admin',TRUE,TRUE,NOW())""",
            principal_id, f"{principal_id}@example.invalid")
        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute("SET statement_timeout='5s'")
        async def new_pool():
            return await asyncpg.create_pool(dsn, min_size=1, max_size=5, setup=setup)
        pool = await new_pool()
        async def fail_fingerprint(fingerprint):
            name = "ad08_fault_" + uuid4().hex
            await owner.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.source_system='{source}' AND NEW.source_fingerprint='{fingerprint}'
                THEN RAISE EXCEPTION 'isolated AD08 insertion fault'; END IF; RETURN NEW; END $$""")
            await owner.execute(f"CREATE TRIGGER {name} BEFORE INSERT OR UPDATE ON incidents FOR EACH ROW EXECUTE FUNCTION {name}()")
            faults.append(name)
        monkeypatch.setattr(am, "get_db_client", lambda: SimpleNamespace(pool=pool))
        monkeypatch.setattr(am, "configured_source", lambda: (source, "synthetic-alertmanager-token-" + "x" * 32))
        yield SimpleNamespace(owner=owner, pool=pool, source=source, new_pool=new_pool,
            client=SimpleNamespace(pool=pool), admin=SimpleNamespace(id=principal_id), fail_fingerprint=fail_fingerprint)
    finally:
        if pool is not None: await pool.close()
        for name in faults:
            await owner.execute(f"DROP TRIGGER {name} ON incidents")
            await owner.execute(f"DROP FUNCTION {name}()")
        await owner.execute("DELETE FROM incidents WHERE source_system=$1", source)
        await owner.execute("DELETE FROM user_profiles WHERE id=$1", principal_id)
        await owner.execute(f'DROP OWNED BY "{role}"')
        await owner.execute(f'DROP ROLE IF EXISTS "{role}"')
        await owner.close()


def request_for(value):
    async def receive():
        return {"type": "http.request", "body": value.model_dump_json().encode(), "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/api/v1/webhooks/alertmanager",
        "headers": [(b"content-type", b"application/json"), (b"authorization", b"Bearer synthetic-alertmanager-token-" + b"x" * 32)]}, receive)


async def test_success_is_committed_durable_and_replay_survives_new_pool(alert_db):
    db = alert_db; value = notification()
    receipt = await am.ingest_alertmanager(request_for(value))
    assert receipt["accepted_alerts"] == receipt["episode_count"] == 1
    saved = await db.owner.fetchrow("SELECT * FROM incidents WHERE source_system=$1", db.source)
    assert str(saved["id"]) == receipt["incident_ids"][0]
    assert saved["status"] == "open" and saved["source_status"] == "firing"
    labels = json.loads(saved["source_labels"])
    assert labels == {"alertname": "IsolatedIngestion", "severity": "critical", "job": "synthetic"}
    fresh_pool = await db.new_pool()
    try:
        assert await am.persist_notification(fresh_pool, db.source, value) == receipt
    finally:
        await fresh_pool.close()
    assert await db.owner.fetchval("SELECT COUNT(*) FROM incidents WHERE source_system=$1", db.source) == 1
    # Both actual read contracts must decode JSON regardless of asyncpg codec.
    detail = await incidents.get_incident(saved["id"], db.admin, db.client)
    listed = await incidents.list_incidents(db.admin, db.client, 1, 100, None, None)
    assert detail.source_labels == labels
    assert any(item.id == detail.id and item.source_status == "firing" for item in listed.items)


async def test_parallel_duplicate_and_reverse_order_batches_deduplicate_without_deadlock(alert_db):
    db = alert_db; first = notification(); second = notification(fingerprint="fedcba9876543210")
    first.alerts += second.alerts
    reverse = first.model_copy(deep=True); reverse.alerts.reverse()
    results = await asyncio.wait_for(asyncio.gather(*(am.persist_notification(db.pool, db.source, value)
        for value in [first, reverse, first, reverse, first])), timeout=15)
    assert all(item == results[0] for item in results)
    assert results[0]["episode_count"] == 2
    assert await db.owner.fetchval("SELECT COUNT(*) FROM incidents WHERE source_system=$1", db.source) == 2


async def test_resolved_before_firing_stays_recovered_and_new_episode_is_separate(alert_db):
    db = alert_db; recovered = notification(resolved=True); firing = notification(start=recovered.alerts[0].startsAt)
    first = await am.persist_notification(db.pool, db.source, recovered)
    await am.persist_notification(db.pool, db.source, firing)
    saved = await db.owner.fetchrow("SELECT * FROM incidents WHERE source_system=$1", db.source)
    assert saved["source_status"] == "recovered" and saved["source_recovered_at"] == recovered.alerts[0].endsAt
    assert saved["status"] == "open" and saved["resolved_at"] is None and saved["resolved_by"] is None
    next_episode = notification(start=recovered.alerts[0].startsAt + timedelta(seconds=60))
    second = await am.persist_notification(db.pool, db.source, next_episode)
    assert first["incident_ids"] != second["incident_ids"]
    assert await db.owner.fetchval("SELECT COUNT(*) FROM incidents WHERE source_system=$1", db.source) == 2


async def test_source_redelivery_cannot_erase_human_actor_or_reopen_resolution(alert_db):
    db = alert_db; value = notification(); result = await am.persist_notification(db.pool, db.source, value)
    incident_id = UUID(result["incident_ids"][0])
    ack = await incidents.acknowledge_incident(incident_id, db.admin, db.client)
    human = await incidents.resolve_incident(incident_id, db.admin, db.client)
    recovered = notification(start=value.alerts[0].startsAt, resolved=True)
    await asyncio.gather(am.persist_notification(db.pool, db.source, recovered), am.persist_notification(db.pool, db.source, value))
    row = await db.owner.fetchrow("SELECT * FROM incidents WHERE id=$1", incident_id)
    assert row["status"] == "resolved" and row["source_status"] == "recovered"
    assert str(row["resolved_by"]) == human["resolved_by"] and row["resolved_at"].isoformat() == human["resolved_at"]
    assert row["acknowledged_at"].isoformat() == ack["acknowledged_at"] and row["acknowledged_by"] == db.admin.id


async def test_second_alert_fault_rolls_back_entire_batch_and_http_ack_is_503(alert_db):
    db = alert_db; value = notification(); value.alerts += notification(fingerprint="ffffffffffffffff").alerts
    await db.fail_fingerprint("ffffffffffffffff")
    with pytest.raises(HTTPException) as error:
        await am.ingest_alertmanager(request_for(value))
    assert error.value.status_code == 503
    assert await db.owner.fetchval("SELECT COUNT(*) FROM incidents WHERE source_system=$1", db.source) == 0


async def test_lost_ack_after_commit_is_safe_to_replay_without_second_incident(alert_db, monkeypatch):
    db = alert_db; value = notification(); actual = am.persist_notification
    async def lose_response(*args):
        await actual(*args)
        raise ConnectionError("Synthetic lost response after successful commit")
    monkeypatch.setattr(am, "persist_notification", lose_response)
    with pytest.raises(HTTPException) as error:
        await am.ingest_alertmanager(request_for(value))
    assert error.value.status_code == 503
    assert await db.owner.fetchval("SELECT COUNT(*) FROM incidents WHERE source_system=$1", db.source) == 1
    monkeypatch.setattr(am, "persist_notification", actual)
    assert (await am.ingest_alertmanager(request_for(value)))["episode_count"] == 1
    assert await db.owner.fetchval("SELECT COUNT(*) FROM incidents WHERE source_system=$1", db.source) == 1


async def test_forced_rls_denies_tenant_read_and_source_write(alert_db):
    db = alert_db; await am.persist_notification(db.pool, db.source, notification())
    async with db.pool.acquire() as conn:
        flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        assert tuple(flags.values()) == (False, False)
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls='off'")
            await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(uuid4()))
            assert await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE source_system=$1", db.source) == 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls='off'")
                await conn.execute("INSERT INTO incidents(title,severity) VALUES('Denied source impersonation','warning')")


async def test_constraints_deny_incoherent_source_and_downgrade_retains_history(alert_db, monkeypatch):
    db = alert_db; receipt = await am.persist_notification(db.pool, db.source, notification())
    incident_id = UUID(receipt["incident_ids"][0])
    with pytest.raises(asyncpg.CheckViolationError):
        await db.owner.execute("UPDATE incidents SET source_fingerprint=NULL WHERE id=$1", incident_id)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.owner.execute("UPDATE incidents SET source_status='recovered',source_recovered_at=NULL WHERE id=$1", incident_id)
    spec = importlib.util.spec_from_file_location("ad08_migration", Path(__file__).parents[2] / "Alembic/versions/0073_alertmanager_incidents.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    statements = []; monkeypatch.setattr(module, "op", SimpleNamespace(execute=statements.append)); module.downgrade()
    with pytest.raises(asyncpg.RaiseError, match="Retain Alertmanager episode history"):
        async with db.owner.transaction():
            for statement in statements: await db.owner.execute(statement)
    assert await db.owner.fetchval("SELECT COUNT(*) FROM incidents WHERE source_system=$1", db.source) == 1

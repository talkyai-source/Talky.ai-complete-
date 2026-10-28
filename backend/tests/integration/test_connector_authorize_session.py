"""Normal-auth pending OAuth admission after a real permission-query wait.

Login, session revocation, permissions, RLS and database writes are real. OAuth
state storage and provider URL construction are explicitly synthetic boundaries;
these cases prove neither Redis persistence nor an external OAuth callback.
"""
import asyncio
import hashlib
import json
import re
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.api.v1.endpoints import connectors
from app.core.jwt_security import decode_and_validate_token
from tests.integration.test_admin_connector_session_authority import (
    connector_session_db,  # noqa: F401
    digest,
    logout_exact_session,
    seed_connector,
)
from tests.integration.test_admin_real_auth_journey import (
    API_ORIGIN,
    ORIGIN,
    journey_db,  # noqa: F401
)
from tests.integration.test_admin_users_tenants_crud import (
    call,
    crud_db,  # noqa: F401
    signed_in,
)

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []
WRITERS = ("card_authorize", "post_authorize")
CALLBACK = API_ORIGIN + "/api/v1/connectors/callback"
FRONTEND_CALLBACK = ORIGIN + "/connectors/callback"


@pytest_asyncio.fixture
async def connector_authorize_db(connector_session_db, monkeypatch):  # noqa: F811
    db = connector_session_db
    assert not await db.owner.fetchval(
        "SELECT EXISTS(SELECT 1 FROM connectors WHERE tenant_id=$1)", db.tenants[1])
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    await db.owner.execute(f'GRANT INSERT ON connectors TO "{role}"')
    monkeypatch.setenv("API_BASE_URL", API_ORIGIN)
    db.authorize_states = []
    db.authorize_factories = []
    db.authorize_urls = []
    db.authorize_callbacks = []
    db.authorize_deletions = []
    db.authorize_live_states = {}
    db.authorize_state_attempts = 0
    db.authorize_failure = None
    db.authorize_state_entered = asyncio.Event()
    db.authorize_state_release = asyncio.Event()
    db.authorize_state_release.set()

    class SyntheticOAuthManager:
        async def create_state(self, **kwargs):
            db.authorize_state_attempts += 1
            db.authorize_state_entered.set()
            await db.authorize_state_release.wait()
            if db.authorize_failure == "state":
                raise RuntimeError("Synthetic OAuth state-store failure")
            # Store values only in memory. Evidence must never serialize state
            # or verifier material, even though these are synthetic test values.
            output = {"state": str(uuid4()), "code_verifier": uuid4().hex,
                      "code_challenge": uuid4().hex, "code_challenge_method": "S256"}
            db.authorize_states.append({"input": kwargs, "output": output})
            db.authorize_live_states[output["state"]] = kwargs
            return output

        async def delete_state(self, state):
            db.authorize_deletions.append(state)
            return db.authorize_live_states.pop(state, None) is not None

    class SyntheticURLProvider:
        def __init__(self, connector_id):
            self.connector_id = connector_id

        def get_oauth_url(self, **kwargs):
            db.authorize_urls.append({"connector_id": self.connector_id, **kwargs})
            if db.authorize_failure == "url":
                raise RuntimeError("Synthetic OAuth URL construction failure")
            return "https://synthetic-oauth.invalid/authorize?" + urlencode({"state": kwargs["state"]})

        async def exchange_code(self, **_kwargs):
            db.authorize_callbacks.append(self.connector_id)
            raise AssertionError("Authorization initiation must not exchange an OAuth code")

    def factory(*, provider, tenant_id, connector_id, **_kwargs):
        assert provider == "gmail" and str(tenant_id) == str(db.tenants[1])
        identity = UUID(str(connector_id))
        db.authorize_factories.append({"provider": provider, "tenant_id": str(tenant_id),
                                       "connector_id": str(identity)})
        return SyntheticURLProvider(str(identity))

    manager = SyntheticOAuthManager()
    monkeypatch.setattr(connectors, "get_oauth_state_manager", lambda: manager)
    monkeypatch.setattr(connectors.ConnectorFactory, "create", staticmethod(factory))
    try:
        yield db
    finally:
        db.authorize_state_release.set()
        # Discover only this fixture's unique tenant, then pass exact IDs to the
        # inherited cleanup. Include unexpected baseline writes even if no state
        # was minted or the response assertion failed before observing its row.
        rows = await db.owner.fetch("SELECT id FROM connectors WHERE tenant_id=$1", db.tenants[1])
        db.connector_ids.extend(row["id"] for row in rows if row["id"] not in db.connector_ids)
        accounts = await db.owner.fetch("""SELECT id FROM connector_accounts
            WHERE tenant_id=$1 AND connector_id=ANY($2::uuid[])""", db.tenants[1], db.connector_ids)
        db.connector_account_ids.extend(row["id"] for row in accounts
                                        if row["id"] not in db.connector_account_ids)


async def authorize(db, actor, writer):
    if writer == "card_authorize":
        return await call(db, actor, "GET", "/connectors/email/authorize",
                          params={"redirect_uri": FRONTEND_CALLBACK})
    assert writer == "post_authorize"
    return await call(db, actor, "POST", "/connectors/authorize",
                      json={"type": "email", "provider": "gmail", "name": "Synthetic pending authorization"})


async def saved_state(db):
    queries = {
        "connectors": "SELECT * FROM connectors WHERE tenant_id=$1 ORDER BY id",
        "accounts": "SELECT * FROM connector_accounts WHERE tenant_id=$1 ORDER BY id",
        "audits": """SELECT * FROM audit_logs WHERE tenant_id=$1
            AND resource_type='connector' ORDER BY event_id""",
    }
    return {name: [dict(row) for row in await db.owner.fetch(query, db.tenants[1])]
            for name, query in queries.items()}


def observe_permission_entry(db, monkeypatch):
    entered, observation = asyncio.Event(), {}
    original = asyncpg.Connection.fetch

    async def observed(conn, query, *args, **kwargs):
        if (not observation and conn.get_server_pid() != db.owner.get_server_pid()
                and "FROM user_permissions up" in query and len(args) == 2
                and str(args[0]) == str(db.users[1]) and str(args[1]) == str(db.tenants[1])):
            observation.update(waiter_pid=conn.get_server_pid(), in_transaction=conn.is_in_transaction())
            entered.set()
        return await original(conn, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetch", observed)
    return entered, observation


async def prove_permission_wait(db, task, waiter_pid):
    async with asyncio.timeout(2):
        while True:
            await db.owner.execute("SELECT pg_stat_clear_snapshot()")
            waiting = await db.owner.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity a
                WHERE a.datname=current_database() AND a.pid=$1 AND a.wait_event_type='Lock'
                  AND $2=ANY(pg_blocking_pids(a.pid))
                  AND position('FROM user_permissions up' IN a.query)>0)""",
                waiter_pid, db.owner.get_server_pid())
            if waiting:
                return
            assert not task.done(), "Authorization ended before its real permission-query lock wait"
            await asyncio.sleep(0.01)


def record_result(evidence, db, response, before, after):
    evidence.update(http_status=response.status_code, before_sha256=digest(before), after_sha256=digest(after),
                    state_unchanged=before == after, mutation_audits_unchanged=before["audits"] == after["audits"],
                    pending_before=sum(row["status"] == "pending" for row in before["connectors"]),
                    pending_after=sum(row["status"] == "pending" for row in after["connectors"]),
                    account_count_before=len(before["accounts"]), account_count_after=len(after["accounts"]),
                    oauth_state_entries=len(db.authorize_states), factory_entries=len(db.authorize_factories),
                    url_entries=len(db.authorize_urls), callback_entries=len(db.authorize_callbacks),
                    oauth_state_attempts=db.authorize_state_attempts, deleted_states=len(db.authorize_deletions),
                    outstanding_states=len(db.authorize_live_states),
                    token_provider_entries=len(db.provider_calls), synthetic_oauth_storage=True,
                    synthetic_provider_url=True, auth_overridden=False, login="normal tenant password, no MFA")


@pytest.mark.parametrize("writer", WRITERS)
async def test_authorize_logout_during_permission_wait_denies_pending_write(connector_authorize_db, monkeypatch, writer):
    db = connector_authorize_db
    await seed_connector(db, "tenant_refresh")
    evidence = {"case": "logout_before_pending_authorization", "writer": writer, "expected_status": 401}
    RUN_EVIDENCE.append(evidence)
    pending = None
    async with signed_in(db, 1) as actor:
        before = await saved_state(db)
        entered, entry = observe_permission_entry(db, monkeypatch)
        try:
            async with db.owner.transaction():
                await db.owner.execute("LOCK TABLE user_permissions IN ACCESS EXCLUSIVE MODE")
                pending = asyncio.create_task(authorize(db, actor, writer))
                try:
                    await asyncio.wait_for(entered.wait(), 8)
                except TimeoutError:
                    if pending.done() and not pending.cancelled() and pending.exception() is None:
                        evidence["early_http_status"] = pending.result().status_code
                    raise
                assert entry["in_transaction"] is True
                await prove_permission_wait(db, pending, entry["waiter_pid"])
                evidence.update(actual_lock_wait_observed=True, waiter_pid=entry["waiter_pid"],
                                blocker_pid=db.owner.get_server_pid(), permission_transaction=True)
                await asyncio.wait_for(logout_exact_session(db, actor, 1), 3)
                fresh = await asyncio.wait_for(call(db, actor, "GET", "/auth/me"), 3)
                evidence["fresh_auth_status_after_logout"] = fresh.status_code
                assert fresh.status_code == 401
                await prove_permission_wait(db, pending, entry["waiter_pid"])
                evidence["logout_committed_before_lock_release"] = True
            response = await asyncio.wait_for(pending, 8)
            after = await saved_state(db)
            record_result(evidence, db, response, before, after)
            assert response.status_code == 401
            assert evidence["state_unchanged"] is True
            assert all(evidence[name] == 0 for name in (
                "oauth_state_entries", "factory_entries", "url_entries", "callback_entries", "token_provider_entries"))
        finally:
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("writer", WRITERS)
async def test_authorize_current_session_creates_one_bound_pending_connection(connector_authorize_db, writer):
    db = connector_authorize_db
    await seed_connector(db, "tenant_refresh")
    evidence = {"case": "current_session_pending_authorization", "writer": writer, "expected_status": 200}
    RUN_EVIDENCE.append(evidence)
    async with signed_in(db, 1) as actor:
        before = await saved_state(db)
        response = await authorize(db, actor, writer)
        after = await saved_state(db)
        record_result(evidence, db, response, before, after)
        assert response.status_code == 200
        old_ids = {row["id"] for row in before["connectors"]}
        created = [row for row in after["connectors"] if row["id"] not in old_ids]
        assert len(created) == 1
        row = created[0]
        assert row["tenant_id"] == db.tenants[1] and row["provider"] == "gmail"
        assert row["type"] == "email" and row["status"] == "pending"
        existing_unchanged = [item for item in after["connectors"] if item["id"] in old_ids] == before["connectors"]
        accounts_unchanged = before["accounts"] == after["accounts"]
        assert existing_unchanged and accounts_unchanged
        assert evidence["oauth_state_entries"] == evidence["factory_entries"] == evidence["url_entries"] == 1
        assert evidence["callback_entries"] == evidence["token_provider_entries"] == 0
        state, factory, url = db.authorize_states[0], db.authorize_factories[0], db.authorize_urls[0]
        supplied, output = state["input"], state["output"]
        bound = (str(supplied["tenant_id"]) == str(db.tenants[1]) and str(supplied["user_id"]) == str(db.users[1])
                 and supplied["provider"] == "gmail" and supplied["redirect_uri"] == CALLBACK
                 and str(supplied["extra_data"]["connector_id"]) == str(row["id"])
                 and factory["connector_id"] == url["connector_id"] == str(row["id"])
                 and url["redirect_uri"] == CALLBACK and url["state"] == output["state"]
                 and url["code_challenge"] == output["code_challenge"])
        parsed = urlsplit(response.json()["authorization_url"])
        returned_state_matches = parse_qs(parsed.query).get("state") == [output["state"]]
        if writer == "post_authorize":
            returned_state_matches = returned_state_matches and response.json()["state"] == output["state"]
        else:
            bound = bound and supplied["extra_data"]["frontend_redirect_uri"] == FRONTEND_CALLBACK
        existing_audits = [item for item in after["audits"] if item["resource_id"] != row["id"]]
        creation_audits = [item for item in after["audits"] if item["resource_id"] == row["id"]]
        evidence.update(created_connector_id=str(row["id"]), synthetic_state_binding_valid=bound,
                        returned_state_matches=returned_state_matches, creation_audit_count=len(creation_audits),
                        unrelated_connector_audits_unchanged=existing_audits == before["audits"])
        assert bound and returned_state_matches
        url_shape_valid = parsed.scheme == "https" and parsed.hostname == "synthetic-oauth.invalid"
        assert url_shape_valid
        assert existing_audits == before["audits"]
        assert_audits(db, creation_audits, ["connector.oauth_pending_created"])
        assert evidence["outstanding_states"] == 1 and evidence["deleted_states"] == 0


def assert_audits(db, rows, expected_actions):
    actions = [item["action"] for item in sorted(rows, key=lambda item: item["event_time"])]
    assert actions == expected_actions
    assert all(item["actor_id"] == db.users[1] and item["tenant_id"] == db.tenants[1]
               and item["actor_type"] == "user" and item["actor_role"] == "tenant_admin" for item in rows)
    # Receipt integrity hashes are expected; OAuth secrets and their hashes are
    # not. Only report the boolean, never the material being checked.
    material = [value for item in db.authorize_states for value in item["output"].values()
                if value != "S256"]
    forbidden = material + [hashlib.sha256(value.encode()).hexdigest() for value in material]
    serialized = json.dumps(rows, sort_keys=True, default=str)
    secrets_absent = not any(value in serialized for value in forbidden)
    assert secrets_absent
    operations = set()
    for item in rows:
        metadata = json.loads(item["metadata"]) if isinstance(item["metadata"], str) else item["metadata"]
        expected_keys = {"source", "operation_id", "local_status"}
        if item["action"] == "connector.oauth_pending_created":
            expected_keys |= {"connector_type", "provider"}
            assert metadata["connector_type"] == "email" and metadata["provider"] == "gmail"
        assert set(metadata) == expected_keys and metadata["source"] == "tenant_oauth_start"
        expected_status = "pending" if item["action"] == "connector.oauth_pending_created" else "error"
        assert metadata["local_status"] == expected_status
        assert item["before_state"] is None and item["after_state"] is None
        operations.add(UUID(metadata["operation_id"]))
    assert len(operations) == 1


@asynccontextmanager
async def fail_creation_audit(db):
    name = "authorize_audit_fault_" + uuid4().hex
    function_created = trigger_created = False
    try:
        await db.owner.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.tenant_id='{db.tenants[1]}'::uuid
                AND NEW.action='connector.oauth_pending_created' THEN
                RAISE EXCEPTION 'synthetic pending-creation audit failure';
            END IF; RETURN NEW; END $$""")
        function_created = True
        await db.owner.execute(f"""CREATE TRIGGER {name} BEFORE INSERT ON audit_logs
            FOR EACH ROW EXECUTE FUNCTION {name}()""")
        trigger_created = True
        yield
    finally:
        if trigger_created:
            await db.owner.execute(f"DROP TRIGGER {name} ON audit_logs")
        if function_created:
            await db.owner.execute(f"DROP FUNCTION {name}()")


@pytest.mark.parametrize("writer", WRITERS)
async def test_authorize_mandatory_audit_failure_rolls_back_pending_connection(connector_authorize_db, writer):
    db = connector_authorize_db
    await seed_connector(db, "tenant_refresh")
    evidence = {"case": "pending_creation_audit_failure", "writer": writer, "expected_status": 503}
    RUN_EVIDENCE.append(evidence)
    async with signed_in(db, 1) as actor:
        before = await saved_state(db)
        errors_before = len(db.report["database_errors"])
        async with fail_creation_audit(db):
            response = await authorize(db, actor, writer)
        after = await saved_state(db)
        record_result(evidence, db, response, before, after)
        actual_fault = any(item["sqlstate"] == "P0001" and item["operation"] == "INSERT"
                           for item in db.report["database_errors"][errors_before:])
        evidence["actual_audit_trigger_fault_observed"] = actual_fault
        assert response.status_code == 503
        assert actual_fault
        assert evidence["state_unchanged"] is True
        assert all(evidence[name] == 0 for name in (
            "oauth_state_attempts", "oauth_state_entries", "factory_entries", "url_entries",
            "callback_entries", "token_provider_entries", "deleted_states", "outstanding_states"))


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("phase", ["state", "url"])
async def test_authorize_preparation_failure_rejects_only_new_pending_connection(connector_authorize_db, writer, phase):
    db = connector_authorize_db
    await seed_connector(db, "tenant_refresh")
    db.authorize_failure = phase
    evidence = {"case": "pending_authorization_preparation_failure", "writer": writer,
                "phase": phase, "expected_status": 503}
    RUN_EVIDENCE.append(evidence)
    async with signed_in(db, 1) as actor:
        before = await saved_state(db)
        response = await authorize(db, actor, writer)
        after = await saved_state(db)
        record_result(evidence, db, response, before, after)
        old_ids = {row["id"] for row in before["connectors"]}
        created = [row for row in after["connectors"] if row["id"] not in old_ids]
        existing_unchanged = [row for row in after["connectors"] if row["id"] in old_ids] == before["connectors"]
        accounts_unchanged = before["accounts"] == after["accounts"]
        evidence.update(new_connection_count=len(created), previous_connections_unchanged=existing_unchanged,
                        accounts_unchanged=accounts_unchanged)
        assert response.status_code == 503
        assert len(created) == 1 and existing_unchanged and accounts_unchanged
        row = created[0]
        own_audits = [item for item in after["audits"] if item["resource_id"] == row["id"]]
        unrelated_unchanged = [item for item in after["audits"] if item["resource_id"] != row["id"]] == before["audits"]
        evidence.update(new_connection_status=row["status"], unrelated_connector_audits_unchanged=unrelated_unchanged,
                        new_audit_actions=[item["action"] for item in own_audits])
        assert row["status"] == "error" and unrelated_unchanged
        assert row["tenant_id"] == db.tenants[1] and row["provider"] == "gmail" and row["type"] == "email"
        assert_audits(db, own_audits, ["connector.oauth_pending_created", "connector.oauth_rejected"])
        assert evidence["oauth_state_attempts"] == 1
        assert evidence["callback_entries"] == evidence["token_provider_entries"] == evidence["outstanding_states"] == 0
        expected = int(phase == "url")
        assert evidence["oauth_state_entries"] == evidence["factory_entries"] == evidence["url_entries"] == evidence["deleted_states"] == expected
        if phase == "url":
            known_state_deleted = db.authorize_deletions == [db.authorize_states[0]["output"]["state"]]
            evidence["known_state_deleted"] = known_state_deleted
            assert known_state_deleted


def pause_creation_receipt(db, monkeypatch):
    entered, release, logout_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observation = {}
    original_value, original_execute = asyncpg.Connection.fetchval, asyncpg.Connection.execute

    async def observed_value(conn, query, *args, **kwargs):
        if (not entered.is_set() and query.startswith("INSERT INTO audit_logs (")
                and "connector.oauth_pending_created" in args and db.tenants[1] in args):
            # Inspect the actual transaction after its INSERT, then pause only
            # before the unchanged mandatory audit SQL enters asyncpg.
            pending_ids = await conn.fetch("""SELECT id FROM connectors
                WHERE tenant_id=$1 AND status='pending'""", db.tenants[1])
            assert len(pending_ids) == 1 and conn.is_in_transaction()
            observation.update(admission_pid=conn.get_server_pid(), connector_id=pending_ids[0]["id"])
            entered.set()
            await release.wait()
        return await original_value(conn, query, *args, **kwargs)

    async def observed_execute(conn, query, *args, **kwargs):
        activity = (query == "UPDATE security_sessions SET last_active_at = $1 WHERE id = $2"
                    and len(args) == 2 and str(args[1]) == str(observation.get("session_id")))
        revoke = ("UPDATE security_sessions" in query and "revoke_reason" in query
                  and len(args) == 4 and args[1] == "user_initiated"
                  and str(args[2]) == str(observation.get("session_id")) and str(args[3]) == str(db.users[1]))
        if (entered.is_set() and not logout_entered.is_set()
                and conn.get_server_pid() != observation["admission_pid"] and (activity or revoke)):
            # The normal client carries both bearer and legacy cookie. Cookie
            # agreement updates this session before logout reaches its profile
            # fence, so the first real contention is already on the held session.
            observation["logout_pid"] = conn.get_server_pid()
            observation["wait_sql"] = "session_cookie_activity_update" if activity else "device_session_revoke"
            logout_entered.set()
        return await original_execute(conn, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchval", observed_value)
    monkeypatch.setattr(asyncpg.Connection, "execute", observed_execute)
    return entered, release, logout_entered, observation


async def prove_logout_wait(db, task, observation):
    async with asyncio.timeout(2):
        while True:
            await db.owner.execute("SELECT pg_stat_clear_snapshot()")
            blocked = await db.owner.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity a
                WHERE a.datname=current_database() AND a.pid=$1 AND a.wait_event_type='Lock'
                  AND $2=ANY(pg_blocking_pids(a.pid))
                  AND (($3='session_cookie_activity_update'
                    AND position('UPDATE security_sessions SET last_active_at' IN a.query)>0
                    AND position('WHERE id = $2' IN a.query)>0)
                    OR ($3='device_session_revoke' AND position('UPDATE security_sessions' IN a.query)>0
                    AND position('revoke_reason' IN a.query)>0)))""",
                observation["logout_pid"], observation["admission_pid"], observation["wait_sql"])
            if blocked:
                return
            assert not task.done(), "Session ending did not wait for admitted pending-creation transaction"
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("ending", ["logout", "device_revoke"])
async def test_authorize_admitted_creation_commits_before_session_end_and_finishes_without_replay(connector_authorize_db, monkeypatch, writer, ending):
    db = connector_authorize_db
    await seed_connector(db, "tenant_refresh")
    evidence = {"case": "admitted_creation_serializes_session_end", "writer": writer,
                "ending": ending, "expected_status": 200}
    RUN_EVIDENCE.append(evidence)
    entered, release, logout_entered, observation = pause_creation_receipt(db, monkeypatch)
    db.authorize_state_release.clear()
    pending = logout_task = None
    async with AsyncExitStack() as stack:
        actor = await stack.enter_async_context(signed_in(db, 1))
        claims = decode_and_validate_token(actor.headers["Authorization"].split()[1])
        assert claims["sub"] == str(db.users[1])
        observation["session_id"] = UUID(claims["sid"])
        if ending == "device_revoke":
            other = await stack.enter_async_context(signed_in(db, 1))
            other_claims = decode_and_validate_token(other.headers["Authorization"].split()[1])
            assert other_claims["sub"] == claims["sub"] and other_claims["sid"] != claims["sid"]

            async def end_session():
                response = await call(db, other, "DELETE", f'/sessions/{observation["session_id"]}')
                assert response.status_code == 200 and response.json()["revoked"] is True
                assert response.json()["refresh_token_revoked"] is False
                assert await db.owner.fetchval("SELECT revoked FROM security_sessions WHERE id=$1 AND user_id=$2",
                                               observation["session_id"], db.users[1]) is True
                assert (await call(db, other, "GET", "/auth/me")).status_code == 200
        else:
            async def end_session():
                await logout_exact_session(db, actor, 1)
        before = await saved_state(db)
        try:
            pending = asyncio.create_task(authorize(db, actor, writer))
            await asyncio.wait_for(entered.wait(), 8)
            external_before_commit = await saved_state(db)
            evidence["pending_insert_uncommitted"] = before == external_before_commit
            assert evidence["pending_insert_uncommitted"] is True
            logout_task = asyncio.create_task(end_session())
            await asyncio.wait_for(logout_entered.wait(), 3)
            await prove_logout_wait(db, logout_task, observation)
            evidence.update(actual_session_end_lock_wait_observed=True, admission_pid=observation["admission_pid"],
                            session_end_pid=observation["logout_pid"], session_end_wait_sql=observation["wait_sql"])
            assert observation["wait_sql"] == ("session_cookie_activity_update" if ending == "logout" else "device_session_revoke")
            release.set()
            await asyncio.wait_for(db.authorize_state_entered.wait(), 8)
            await asyncio.wait_for(logout_task, 3)
            fresh = await call(db, actor, "GET", "/auth/me")
            assert fresh.status_code == 401
            committed = await saved_state(db)
            saved = [row for row in committed["connectors"] if row["id"] == observation["connector_id"]]
            own_audits = [item for item in committed["audits"] if item["resource_id"] == observation["connector_id"]]
            assert len(saved) == 1 and saved[0]["status"] == "pending"
            assert_audits(db, own_audits, ["connector.oauth_pending_created"])
            assert not pending.done()
            evidence.update(fresh_auth_status_after_logout=fresh.status_code,
                            creation_committed_before_session_end_completed=True,
                            oauth_state_return_held_until_after_session_end=True)
            db.authorize_state_release.set()
            response = await asyncio.wait_for(pending, 8)
            after = await saved_state(db)
            record_result(evidence, db, response, before, after)
            old_ids = {row["id"] for row in before["connectors"]}
            previous_unchanged = [row for row in after["connectors"] if row["id"] in old_ids] == before["connectors"]
            accounts_unchanged = before["accounts"] == after["accounts"]
            evidence.update(previous_connections_unchanged=previous_unchanged, accounts_unchanged=accounts_unchanged)
            assert response.status_code == 200
            assert previous_unchanged and accounts_unchanged and len(after["connectors"]) == len(before["connectors"]) + 1
            assert evidence["oauth_state_attempts"] == evidence["oauth_state_entries"] == evidence["url_entries"] == 1
            assert evidence["factory_entries"] == evidence["outstanding_states"] == 1
            assert evidence["callback_entries"] == evidence["token_provider_entries"] == evidence["deleted_states"] == 0
            state_bound = (str(db.authorize_states[0]["input"]["user_id"]) == str(db.users[1])
                           and str(db.authorize_states[0]["input"]["extra_data"]["connector_id"])
                           == str(observation["connector_id"]))
            assert state_bound
            own_audits = [item for item in after["audits"] if item["resource_id"] == observation["connector_id"]]
            assert_audits(db, own_audits, ["connector.oauth_pending_created"])
            unrelated_unchanged = [item for item in after["audits"] if item["resource_id"] != observation["connector_id"]] == before["audits"]
            assert unrelated_unchanged
        finally:
            release.set()
            db.authorize_state_release.set()
            tasks = [task for task in (pending, logout_task) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def pause_after_locked_session_permissions(db, monkeypatch, session_id):
    entered, release, observation = asyncio.Event(), asyncio.Event(), {}
    original_row, original_fetch, original_value = (
        asyncpg.Connection.fetchrow, asyncpg.Connection.fetch, asyncpg.Connection.fetchval)

    async def observed_row(conn, query, *args, **kwargs):
        row = await original_row(conn, query, *args, **kwargs)
        if ("SELECT id FROM security_sessions" in query and "FOR UPDATE" in query
                and len(args) == 2 and str(args[0]) == str(session_id) and str(args[1]) == str(db.users[1])):
            assert row is not None and str(row["id"]) == str(session_id) and conn.is_in_transaction()
            observation["admission_pid"] = conn.get_server_pid()
        return row

    async def observed_fetch(conn, query, *args, **kwargs):
        rows = await original_fetch(conn, query, *args, **kwargs)
        if (not entered.is_set() and conn.get_server_pid() == observation.get("admission_pid")
                and "FROM user_permissions up" in query and len(args) == 2
                and str(args[0]) == str(db.users[1]) and str(args[1]) == str(db.tenants[1])):
            # The real permission query has finished under the original session
            # row lock. Hold its return; this is scheduling, not a SQL lock wait,
            # fake grant, replaced clock or altered query result.
            entered.set()
            await release.wait()
        return rows

    async def observed_value(conn, query, *args, **kwargs):
        value = await original_value(conn, query, *args, **kwargs)
        if (entered.is_set() and conn.get_server_pid() == observation.get("admission_pid")
                and "SELECT EXISTS(SELECT 1 FROM security_sessions" in query and "clock_timestamp()" in query
                and len(args) == 2 and str(args[0]) == str(session_id)):
            observation["final_session_check"] = value
        return value

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", observed_row)
    monkeypatch.setattr(asyncpg.Connection, "fetch", observed_fetch)
    monkeypatch.setattr(asyncpg.Connection, "fetchval", observed_value)
    return entered, release, observation


@pytest.mark.parametrize("writer", WRITERS)
async def test_authorize_session_expires_after_actual_locked_permission_read(connector_authorize_db, monkeypatch, writer):
    db = connector_authorize_db
    await seed_connector(db, "tenant_refresh")
    evidence = {"case": "session_naturally_expires_after_permission_read", "writer": writer,
                "expected_status": 401, "synchronization": "hold unchanged permission result after actual SQL; not a SQL lock wait"}
    RUN_EVIDENCE.append(evidence)
    pending = None
    async with signed_in(db, 1) as actor:
        claims = decode_and_validate_token(actor.headers["Authorization"].split()[1])
        assert claims["sub"] == str(db.users[1])
        session_id = UUID(claims["sid"])
        expires_at = await db.owner.fetchval("""UPDATE security_sessions
            SET expires_at=clock_timestamp()+INTERVAL '2 seconds'
            WHERE id=$1 AND user_id=$2 AND revoked=FALSE RETURNING expires_at""", session_id, db.users[1])
        assert expires_at is not None
        before = await saved_state(db)
        entered, release, observation = pause_after_locked_session_permissions(db, monkeypatch, session_id)
        try:
            pending = asyncio.create_task(authorize(db, actor, writer))
            await asyncio.wait_for(entered.wait(), 3)
            observed_at = await db.owner.fetchval("SELECT clock_timestamp()")
            assert observed_at < expires_at
            evidence.update(real_permission_result_before_expiry=True, admission_pid=observation["admission_pid"],
                            original_session_row_lock_observed=True)
            async with asyncio.timeout(3):
                while (await db.owner.fetchval("SELECT clock_timestamp()")) < expires_at:
                    assert not pending.done()
                    await asyncio.sleep(0.01)
            evidence["database_wall_clock_reached_natural_expiry"] = True
            release.set()
            response = await asyncio.wait_for(pending, 5)
            after = await saved_state(db)
            record_result(evidence, db, response, before, after)
            saved = await db.owner.fetchrow("SELECT expires_at,revoked FROM security_sessions WHERE id=$1 AND user_id=$2",
                                            session_id, db.users[1])
            evidence.update(expiry_unchanged=saved["expires_at"] == expires_at, session_revoked=saved["revoked"],
                            final_database_session_check=observation.get("final_session_check"))
            assert response.status_code == 401
            assert evidence["state_unchanged"] is True and evidence["expiry_unchanged"] is True
            assert evidence["session_revoked"] is False and evidence["final_database_session_check"] is False
            assert all(evidence[name] == 0 for name in (
                "oauth_state_attempts", "oauth_state_entries", "factory_entries", "url_entries", "callback_entries",
                "token_provider_entries", "deleted_states", "outstanding_states"))
        finally:
            release.set()
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

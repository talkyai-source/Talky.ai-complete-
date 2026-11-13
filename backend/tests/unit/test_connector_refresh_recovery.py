"""F15-01: real lifecycle/worker behavior with transactional, synthetic ports.

No network, keys, environment files or database server are used. SQL fakes
retain claim ownership, transaction rollback and committed audit observations.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import re
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from app.infrastructure.connectors.base import ConnectorProviderError, OAuthTokens
from app.services import connector_lifecycle as lifecycle

SAFE_PROVIDERS = ("gmail", "google_calendar", "google_drive", "hubspot")
TENANT = UUID("10000000-0000-4000-8000-000000000001")
OTHER_TENANT = UUID("20000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)


class AuditUnavailable(RuntimeError):
    pass


class Store:
    def __init__(self):
        self.now = NOW
        self.parents = {}
        self.accounts = {}
        self.receipts = []
        self.queries = []
        self.scopes = []
        self.lock = asyncio.Lock()
        self.fail_audit = None

    def add(self, provider="gmail", *, tenant=TENANT, state="in_flight", age=61,
            reason=None, status="active"):
        cid, aid = uuid4(), uuid4()
        self.parents[cid] = dict(id=cid, tenant_id=tenant, provider=provider,
            type="crm" if provider in {"hubspot", "salesforce"} else "email",
            status=status, config={}, created_at=NOW-timedelta(days=1))
        self.accounts[aid] = dict(id=aid, tenant_id=tenant, connector_id=cid,
            status="active", created_at=NOW-timedelta(days=1),
            refresh_state=state, refresh_claim_id=uuid4() if state != "idle" else None,
            refresh_claimed_at=NOW-timedelta(seconds=age) if state != "idle" else None,
            refresh_error=reason, credential_generation=0, rotation_count=0,
            access_token_encrypted="enc:synthetic-old-access",
            refresh_token_encrypted="enc:synthetic-refresh",
            token_expires_at=NOW-timedelta(hours=1))
        return cid, aid


class Connection:
    def __init__(self, store, tenant):
        self.store, self.tenant = store, tenant
        self.audit_locked = False

    def query(self, sql, args):
        normalized = " ".join(sql.split()).lower()
        self.store.queries.append((normalized, args, self.tenant))
        return normalized

    @staticmethod
    def matches(row, where, args):
        # Only explicit equality predicates used by these lifecycle writes.
        for column, parameter in re.findall(r"(?:\b\w+\.)?(id|tenant_id|connector_id|refresh_claim_id|credential_generation)\s*=\s*\$(\d+)", where):
            if str(row.get(column)) != str(args[int(parameter)-1]):
                return False
        return True

    def update(self, sql, args):
        table = re.search(r"update (\w+)", sql).group(1)
        assert table in {"connectors", "connector_accounts"}, sql
        rows = self.store.parents if table == "connectors" else self.store.accounts
        assignments, where = sql.split(" set ", 1)[1].split(" where ", 1)
        matches = [row for row in rows.values() if self.matches(row, where, args)]
        assert len(matches) <= 1, "A lifecycle mutation must identify one owned row"
        for row in matches:
            for assignment in re.split(r",\s*(?=\w+\s*=)", assignments):
                name, expression = (part.strip() for part in assignment.split("=", 1))
                if re.fullmatch(r"\$\d+(?:::\w+)?", expression):
                    row[name] = deepcopy(args[int(re.search(r"\d+", expression).group())-1])
                elif expression == "null":
                    row[name] = None
                elif expression in {"now()", "clock_timestamp()"}:
                    row[name] = self.store.now
                elif expression.startswith("'") and expression.endswith("'"):
                    row[name] = expression[1:-1]
                elif expression.endswith("+1"):
                    row[name] = (row.get(name) or 0) + 1
                else:
                    raise AssertionError(f"Unmodelled SQL expression: {assignment}")
        return deepcopy(matches[0]) if matches else None

    async def fetchrow(self, sql, *args):
        query = self.query(sql, args)
        if query.startswith("update "):
            return self.update(query, args)
        if "from connectors" in query:
            assert "tenant_id=" in query and "for update" in query
            row = self.store.parents.get(UUID(str(args[0])))
            return deepcopy(row) if row and str(row["tenant_id"]) == str(args[1]) else None
        raise AssertionError(query)

    async def fetch(self, sql, *args):
        query = self.query(sql, args)
        if "from connector_accounts" in query and "for update" in query:
            assert "tenant_id=" in query
            return [deepcopy(row) for row in self.store.accounts.values()
                    if str(row["connector_id"]) == str(args[0]) and str(row["tenant_id"]) == str(args[1])]
        if "from connector_accounts" in query:
            assert "limit" in query and "refresh_claimed_at" in query
            limit = next(value for value in reversed(args) if isinstance(value, int))
            return [dict(tenant_id=row["tenant_id"], connector_id=row["connector_id"],
                         account_id=row["id"], refresh_claim_id=row["refresh_claim_id"])
                    for row in self.store.accounts.values()
                    if row["refresh_state"] == "in_flight" and row["refresh_claimed_at"] is not None
                    and row["refresh_claimed_at"] < self.store.now-timedelta(seconds=60)][:limit]
        raise AssertionError(query)

    async def fetchval(self, sql, *args):
        query = self.query(sql, args)
        if "exists(select 1 from connectors" in query:
            return any(row["tenant_id"] == args[0] and row["type"] == args[1]
                       and row["provider"] == args[2] and row["status"] == "active"
                       and (row["created_at"], row["id"]) > (args[3], args[4])
                       for row in self.store.parents.values())
        if query in {"select now()", "select clock_timestamp()"}:
            return self.store.now
        raise AssertionError(query)

    async def execute(self, sql, *args):
        query = self.query(sql, args)
        if query.startswith("update "):
            return "UPDATE 1" if self.update(query, args) else "UPDATE 0"
        raise AssertionError(query)


class Audit:
    def __init__(self, store):
        self.store = store

    async def lock_transaction(self, conn):
        assert self.store.lock.locked()
        conn.audit_locked = True

    async def log_in_transaction(self, conn, **receipt):
        assert conn.audit_locked and self.store.lock.locked()
        if receipt["action"] == self.store.fail_audit:
            raise AuditUnavailable("synthetic audit failure")
        self.store.receipts.append(deepcopy(receipt))
        return uuid4()


@pytest.fixture
def world(monkeypatch):
    store = Store()
    audit = Audit(store)

    @asynccontextmanager
    async def acquire(pool, tenant, **_kwargs):
        assert pool is store
        async with store.lock:
            before = deepcopy((store.parents, store.accounts, store.receipts))
            store.scopes.append(tenant)
            try:
                yield Connection(store, tenant)
            except BaseException:
                store.parents, store.accounts, store.receipts = before
                raise

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return store.now.astimezone(tz) if tz else store.now.replace(tzinfo=None)

    monkeypatch.setattr(lifecycle, "acquire_with_tenant", acquire)
    monkeypatch.setattr(lifecycle, "datetime", Clock)
    monkeypatch.setattr(lifecycle, "AuditLogger", lambda _pool: audit)
    monkeypatch.setattr(lifecycle, "get_encryption_service", lambda: NS(
        encrypt=lambda value: "enc:"+value,
        decrypt=lambda value: value.removeprefix("enc:")))

    def no_network(*_args, **_kwargs):
        raise AssertionError("Provider network access is forbidden in this test")

    monkeypatch.setattr(httpx, "AsyncClient", no_network)
    service = lifecycle.ConnectorLifecycle(NS(pool=store), actor=lifecycle.ConnectorActor.system("refresh_recovery_test"), audit_logger=audit)
    return NS(store=store, audit=audit, service=service)


def remote(world, name="gmail", *, outcome=None, token="synthetic-new-access"):
    async def refresh(value):
        assert value == "synthetic-refresh"
        assert not world.store.lock.locked(), "Provider I/O must happen after claim/audit commit"
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome:
            return await outcome()
        return OAuthTokens(access_token=token, refresh_token="synthetic-refresh", expires_at=NOW+timedelta(hours=1))
    return NS(provider_name=name, refresh_tokens=AsyncMock(side_effect=refresh),
              apply_config=lambda _config: None, config_from_tokens=lambda _tokens: {})


@pytest.mark.parametrize("provider", SAFE_PROVIDERS)
async def test_abandoned_safe_provider_claim_gets_one_audited_recovery(world, provider):
    cid, aid = world.store.add(provider)
    old_claim = world.store.accounts[aid]["refresh_claim_id"]
    provider_port = remote(world, provider)
    _, saved = await world.service.refresh(str(TENANT), str(cid), connector=provider_port)
    provider_port.refresh_tokens.assert_awaited_once()
    assert saved["refresh_state"] == "idle" and saved["credential_generation"] == 1
    assert world.store.accounts[aid]["refresh_claim_id"] is None
    assert any(receipt["action"] == "connector.refresh_uncertain"
               and receipt["metadata"]["operation_id"] == str(old_claim)
               for receipt in world.store.receipts)
    assert world.store.receipts[-1]["action"] == "connector.refresh_installed"


@pytest.mark.parametrize("age", [0, 59.999, 60])
async def test_live_or_boundary_claim_is_never_replayed(world, age):
    cid, _ = world.store.add(age=age)
    provider = remote(world)
    with pytest.raises(lifecycle.ConnectorLifecycleError):
        await world.service.refresh(str(TENANT), str(cid), connector=provider)
    provider.refresh_tokens.assert_not_awaited()
    assert not world.store.receipts


@pytest.mark.parametrize("claimed_at", [None, NOW+timedelta(seconds=1), "yesterday"])
async def test_missing_future_or_malformed_claim_time_fails_closed(world, claimed_at):
    cid, aid = world.store.add()
    world.store.accounts[aid]["refresh_claimed_at"] = claimed_at
    provider = remote(world)
    with pytest.raises(lifecycle.ConnectorLifecycleError):
        await world.service.refresh(str(TENANT), str(cid), connector=provider)
    provider.refresh_tokens.assert_not_awaited()


@pytest.mark.parametrize("provider", ["salesforce", "unknown_provider"])
async def test_rotating_or_unknown_provider_stale_claim_requires_reauthorization(world, provider):
    cid, _ = world.store.add(provider)
    port = remote(world, provider)
    with pytest.raises(lifecycle.ConnectorLifecycleError):
        await world.service.refresh(str(TENANT), str(cid), connector=port)
    port.refresh_tokens.assert_not_awaited()


@pytest.mark.parametrize("failure,reason", [
    (TimeoutError("synthetic timeout"), "provider_timeout"),
    (httpx.ReadTimeout("synthetic read timeout"), "provider_timeout"),
    (asyncio.CancelledError(), "refresh_cancelled"),
])
async def test_timeout_and_cancellation_have_distinct_durable_outcomes_and_one_retry(world, failure, reason):
    cid, aid = world.store.add(state="idle")
    original = remote(world, outcome=failure)
    with pytest.raises(type(failure)):
        await world.service.refresh(str(TENANT), str(cid), connector=original)
    assert world.store.accounts[aid]["refresh_state"] == "uncertain"
    assert world.store.accounts[aid]["refresh_error"] == reason
    recovery = remote(world)
    await world.service.refresh(str(TENANT), str(cid), connector=recovery)
    recovery.refresh_tokens.assert_awaited_once()


async def test_recovery_failure_cannot_be_retried_after_new_service_instance(world):
    cid, aid = world.store.add()
    recovery = remote(world, outcome=TimeoutError("synthetic timeout"))
    with pytest.raises(TimeoutError):
        await world.service.refresh(str(TENANT), str(cid), connector=recovery)
    recovery.refresh_tokens.assert_awaited_once()
    assert world.store.accounts[aid]["refresh_state"] == "uncertain"
    replacement = lifecycle.ConnectorLifecycle(NS(pool=world.store),
        actor=lifecycle.ConnectorActor.system("new_process"), audit_logger=world.audit)
    third = remote(world)
    with pytest.raises(lifecycle.ConnectorLifecycleError):
        await replacement.refresh(str(TENANT), str(cid), connector=third)
    third.refresh_tokens.assert_not_awaited()


async def test_abandoned_recovery_attempt_and_sweep_do_not_reset_the_single_retry_budget(world):
    cid, aid = world.store.add()
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait():
        entered.set()
        await release.wait()
        return OAuthTokens(access_token="synthetic-late-access", refresh_token="synthetic-refresh")

    task = asyncio.create_task(world.service.refresh(str(TENANT), str(cid), connector=remote(world, outcome=wait)))
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        world.store.now += timedelta(seconds=61)
        sweep = getattr(lifecycle, "sweep_stale_refresh_claims", None)
        assert callable(sweep), "F15-01 has no stale connector-claim sweep"
        assert await sweep(world.store, limit=10) == 1
        assert world.store.accounts[aid]["refresh_state"] == "uncertain"
        replacement = lifecycle.ConnectorLifecycle(NS(pool=world.store),
            actor=lifecycle.ConnectorActor.system("new_process"), audit_logger=world.audit)
        third = remote(world)
        with pytest.raises(lifecycle.ConnectorLifecycleError):
            await replacement.refresh(str(TENANT), str(cid), connector=third)
        third.refresh_tokens.assert_not_awaited()
        release.set()
        with pytest.raises(lifecycle.ConnectorLifecycleError, match="changed during refresh"):
            await task
        assert world.store.accounts[aid]["access_token_encrypted"] == "enc:synthetic-old-access"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("reason", ["provider_rejected", "provider_configuration", "unrecognized_outcome"])
async def test_known_rejection_or_unknown_reason_is_not_a_safe_retry(world, reason):
    cid, _ = world.store.add(state="uncertain", reason=reason)
    provider = remote(world)
    with pytest.raises(lifecycle.ConnectorLifecycleError):
        await world.service.refresh(str(TENANT), str(cid), connector=provider)
    provider.refresh_tokens.assert_not_awaited()


async def test_authentication_rejection_stays_terminal_and_preserves_error_identity(world):
    cid, aid = world.store.add(state="idle")
    rejection = ConnectorProviderError(provider="gmail", operation="refresh_tokens",
        category="authentication", message="Synthetic grant refused", status_code=400)
    with pytest.raises(ConnectorProviderError) as caught:
        await world.service.refresh(str(TENANT), str(cid), connector=remote(world, outcome=rejection))
    assert caught.value is rejection
    assert world.store.accounts[aid]["refresh_error"] == "provider_rejected"
    with pytest.raises(lifecycle.ConnectorLifecycleError):
        await world.service.refresh(str(TENANT), str(cid), connector=remote(world))


async def test_concurrent_stale_recovery_dispatches_only_one_provider_request(world):
    cid, _ = world.store.add()
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait():
        entered.set()
        await release.wait()
        return OAuthTokens(access_token="synthetic-new-access", refresh_token="synthetic-refresh")

    first = remote(world, outcome=wait)
    task = asyncio.create_task(world.service.refresh(str(TENANT), str(cid), connector=first))
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        second = remote(world)
        with pytest.raises(lifecycle.ConnectorLifecycleError):
            await world.service.refresh(str(TENANT), str(cid), connector=second)
        second.refresh_tokens.assert_not_awaited()
        release.set()
        await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_late_original_completion_cannot_replace_recovered_credentials(world):
    cid, aid = world.store.add(state="idle")
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait():
        entered.set()
        await release.wait()
        return OAuthTokens(access_token="synthetic-late-access", refresh_token="synthetic-late-refresh")

    original = asyncio.create_task(world.service.refresh(str(TENANT), str(cid), connector=remote(world, outcome=wait)))
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        world.store.now += timedelta(seconds=61)
        await world.service.refresh(str(TENANT), str(cid), connector=remote(world))
        release.set()
        with pytest.raises(lifecycle.ConnectorLifecycleError, match="changed during refresh"):
            await original
        assert world.store.accounts[aid]["access_token_encrypted"] == "enc:synthetic-new-access"
        assert world.store.accounts[aid]["credential_generation"] == 1
        assert world.store.accounts[aid]["refresh_state"] == "idle"
    finally:
        release.set()
        await asyncio.gather(original, return_exceptions=True)


async def test_stale_transition_audit_failure_rolls_back_before_any_provider_dispatch(world):
    cid, aid = world.store.add()
    before = deepcopy(world.store.accounts[aid])
    world.store.fail_audit = "connector.refresh_uncertain"
    provider = remote(world)
    with pytest.raises(AuditUnavailable):
        await world.service.refresh(str(TENANT), str(cid), connector=provider)
    provider.refresh_tokens.assert_not_awaited()
    assert world.store.accounts[aid] == before and not world.store.receipts


async def test_refresh_mutations_bind_tenant_and_connector_even_with_bypass_role(world):
    cid, _ = world.store.add(state="idle")
    await world.service.refresh(str(TENANT), str(cid), connector=remote(world))
    mutations = [(sql, args) for sql, args, _ in world.store.queries if sql.startswith("update ")]
    assert mutations
    for sql, args in mutations:
        where = sql.split(" where ", 1)[1]
        assert re.search(r"\btenant_id\s*=\s*\$\d+", where), sql
        assert TENANT in args
        if sql.startswith("update connector_accounts"):
            assert re.search(r"\bconnector_id\s*=\s*\$\d+", where), sql
            assert cid in args


async def test_foreign_tenant_cannot_recover_or_read_owned_credentials(world):
    cid, aid = world.store.add()
    before = deepcopy(world.store.accounts[aid])
    provider = remote(world)
    with pytest.raises(lifecycle.ConnectorLifecycleError) as caught:
        await world.service.refresh(str(OTHER_TENANT), str(cid), connector=provider)
    assert caught.value.status_code == 404
    provider.refresh_tokens.assert_not_awaited()
    assert world.store.accounts[aid] == before


async def test_stale_sweep_is_bounded_audited_tenant_scoped_and_never_contacts_provider(world):
    _, first = world.store.add()
    _, second = world.store.add(tenant=OTHER_TENANT)
    _, fresh = world.store.add(age=30)
    sweep = getattr(lifecycle, "sweep_stale_refresh_claims", None)
    assert callable(sweep), "F15-01 has no stale connector-claim sweep"
    assert await sweep(world.store, limit=1) == 1
    assert world.store.accounts[first]["refresh_state"] == "uncertain"
    assert world.store.accounts[second]["refresh_state"] == "in_flight"
    assert world.store.accounts[fresh]["refresh_state"] == "in_flight"
    assert await sweep(world.store, limit=10) == 1
    assert world.store.accounts[second]["refresh_state"] == "uncertain"
    assert await sweep(world.store, limit=10) == 0
    assert {str(receipt["tenant_id"]) for receipt in world.store.receipts} == {str(TENANT), str(OTHER_TENANT)}
    assert all(receipt["action"] == "connector.refresh_uncertain" for receipt in world.store.receipts)
    assert str(TENANT) in world.store.scopes and str(OTHER_TENANT) in world.store.scopes


@pytest.mark.parametrize("limit", [0, -1, 1001])
async def test_sweep_rejects_unbounded_or_invalid_batch_size_before_database(world, limit):
    sweep = getattr(lifecycle, "sweep_stale_refresh_claims", None)
    assert callable(sweep), "F15-01 has no stale connector-claim sweep"
    with pytest.raises(ValueError):
        await sweep(world.store, limit=limit)
    assert not world.store.queries


ADAPTERS = (
    ("app.infrastructure.connectors.email.gmail", "GmailConnector"),
    ("app.infrastructure.connectors.calendar.google_calendar", "GoogleCalendarConnector"),
    ("app.infrastructure.connectors.drive.google_drive", "GoogleDriveConnector"),
    ("app.infrastructure.connectors.crm.hubspot", "HubSpotConnector"),
)


@pytest.mark.parametrize("module_name,class_name", ADAPTERS)
@pytest.mark.parametrize("status,code,category", [(400, "invalid_grant", "authentication"), (401, "invalid_client", "configuration")])
async def test_real_refresh_adapter_rejection_is_typed_and_does_not_echo_provider_secrets(monkeypatch, caplog, module_name, class_name, status, code, category):
    module = importlib.import_module(module_name)
    connector = getattr(module, class_name)(str(TENANT), str(uuid4()))
    monkeypatch.setattr(connector, "_get_client_credentials", lambda: ("synthetic-client", "synthetic-client-secret"))
    secret = "synthetic-provider-secret-must-not-escape"
    response = httpx.Response(status, json={"error": code, "error_description": secret},
                              request=httpx.Request("POST", "https://provider.example.invalid/token"))

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, _url, **kwargs):
            assert kwargs["data"]["refresh_token"] == "synthetic-refresh"
            return response

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: Client())
    with pytest.raises(ConnectorProviderError) as caught:
        await connector.refresh_tokens("synthetic-refresh")
    assert caught.value.category == category
    assert caught.value.operation == "refresh_tokens"
    assert secret not in str(caught.value) and secret not in caplog.text


@pytest.mark.parametrize("module_name,class_name", ADAPTERS)
async def test_real_refresh_adapter_does_not_relabel_transport_timeout_as_provider_rejection(monkeypatch, module_name, class_name):
    module = importlib.import_module(module_name)
    connector = getattr(module, class_name)(str(TENANT), str(uuid4()))
    monkeypatch.setattr(connector, "_get_client_credentials", lambda: ("synthetic-client", "synthetic-secret"))
    error = httpx.ReadTimeout("synthetic provider timeout")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            raise error

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: Client())
    with pytest.raises(httpx.ReadTimeout) as caught:
        await connector.refresh_tokens("synthetic-refresh")
    assert caught.value is error


async def test_existing_reminder_worker_starts_and_stops_the_bounded_sweep_loop(monkeypatch):
    # Prevent the worker's legacy import-time dotenv hook from reading a file.
    from app.core import dotenv_compat
    monkeypatch.setattr(dotenv_compat, "load_dotenv", lambda *_args, **_kwargs: False)
    worker_module = importlib.import_module("app.workers.reminder_worker")
    worker = worker_module.ReminderWorker()
    worker._db_pool = object()
    worker.initialize = AsyncMock()
    worker.shutdown = AsyncMock()
    entered = asyncio.Event()

    async def sweep(pool, **kwargs):
        assert pool is worker._db_pool
        assert 1 <= kwargs.get("limit", 100) <= 1000
        worker.running = False
        entered.set()
        return 0

    sweep_port = AsyncMock(side_effect=sweep)
    monkeypatch.setattr(lifecycle, "sweep_stale_refresh_claims", sweep_port, raising=False)

    async def idle():
        await asyncio.Event().wait()

    for name in ("_heartbeat", "_crm_delivery_loop", "_voice_callback_loop", "_billing_delivery_loop", "_topup_usage_loop"):
        monkeypatch.setattr(worker, name, idle)

    async def process():
        try:
            await asyncio.wait_for(entered.wait(), 0.1)
        finally:
            worker.running = False
        return 0

    monkeypatch.setattr(worker, "_process_due_reminders", process)
    original_sleep = asyncio.sleep

    async def yield_tick(_delay):
        await original_sleep(0)

    monkeypatch.setattr(worker_module.asyncio, "sleep", yield_tick)
    await worker.run()
    sweep_port.assert_awaited_once()
    worker.shutdown.assert_awaited_once()

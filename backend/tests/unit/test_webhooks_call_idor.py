"""P0 regression: cross-tenant object-level authorization on call webhooks.

`/webhooks/call/goal-achieved` and `/call/mark-spam` (plus the /webhooks/secure
equivalents) verify tenant A's HMAC secret but historically mutated rows by a
body-supplied id with NO tenant predicate — so tenant A (valid secret) could
flip tenant B's call/lead/dialer_job by naming B's UUID. The fix threads the
VERIFIED tenant id into `call_service.mark_goal_achieved` / `mark_as_spam` and
scopes every UPDATE with `AND tenant_id = $`, returning a not-found (None ->
404) that is indistinguishable from a genuinely nonexistent id.

These tests exercise the service directly against a fake DB that enforces the
tenant-scoped WHERE exactly like Postgres, plus the route-level admin guard.
"""
import asyncio

import pytest

# Import dependencies first to resolve the tenant_isolation <-> dependencies
# import cycle before pulling in the domain service.
import app.api.v1.dependencies  # noqa: F401
from app.domain.services.call_service import CallService, WebhookTargetMismatch


# ---------------------------------------------------------------------------
# Fake postgres_adapter Client that honours the eq() filters like real SQL.
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._op = None
        self._payload = None
        self._filters = []
        self._single = False

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def select(self, columns="*"):
        self._op = "select"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def single(self):
        self._single = True
        return self

    def _match(self, row):
        return all(str(row.get(c)) == str(v) for c, v in self._filters)

    def execute(self):
        rows = self._store.setdefault(self._table, [])
        matched = [r for r in rows if self._match(r)]
        if self._op == "update":
            for r in matched:
                r.update(self._payload)
            data = [dict(r) for r in matched]
        else:  # select
            data = [dict(r) for r in matched]
        if self._single:
            return _Result(data[0] if data else None)
        return _Result(data)


class _FakeClient:
    def __init__(self, store):
        self._store = store

    def table(self, name):
        return _FakeQuery(self._store, name)


def _service(store):
    # call_repo / lead_repo are unused by the two methods under test; pass the
    # fake so their constructors get a harmless client.
    return CallService(db_client=_FakeClient(store))


def _run(coro):
    # Run on a fresh, dedicated event loop each call. The previous
    # `asyncio.get_event_loop().run_until_complete(...)` reused the ambient
    # loop, which an earlier pytest-asyncio test in a full-suite run may have
    # already closed/replaced — the call then raised and the coroutine was
    # never awaited (the "coroutine ... was never awaited" RuntimeWarning +
    # order-dependent failures that only showed up in the whole-suite run,
    # never when this file ran alone).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _seed():
    return {
        "calls": [
            {"id": "call-b", "tenant_id": TENANT_B, "lead_id": "lead-b",
             "dialer_job_id": "job-b", "outcome": "in_progress", "goal_achieved": False},
        ],
        "leads": [
            {"id": "lead-b", "tenant_id": TENANT_B, "status": "calling"},
        ],
        "dialer_jobs": [
            {"id": "job-b", "tenant_id": TENANT_B, "status": "processing"},
        ],
    }


# ---- goal-achieved --------------------------------------------------------
def test_goal_achieved_cross_tenant_changes_nothing():
    """Tenant A + tenant B's call_id -> None (404) and B's rows untouched."""
    store = _seed()
    svc = _service(store)
    result = _run(svc.mark_goal_achieved(tenant_id=TENANT_A, call_id="call-b"))
    assert result is None  # indistinguishable from a nonexistent id
    # B's rows must be byte-for-byte unchanged.
    assert store["calls"][0]["goal_achieved"] is False
    assert store["calls"][0]["outcome"] == "in_progress"
    assert store["dialer_jobs"][0]["status"] == "processing"


def test_goal_achieved_nonexistent_id_same_as_foreign():
    """A totally unknown id yields the SAME None result as a foreign id."""
    svc = _service(_seed())
    assert _run(svc.mark_goal_achieved(tenant_id=TENANT_A, call_id="ghost")) is None


def test_goal_achieved_same_tenant_writes():
    """Correct tenant -> real write on both the call and the dialer_job."""
    store = _seed()
    svc = _service(store)
    result = _run(svc.mark_goal_achieved(tenant_id=TENANT_B, call_id="call-b"))
    assert result == {"message": "Goal marked as achieved", "call_id": "call-b"}
    assert store["calls"][0]["goal_achieved"] is True
    assert store["dialer_jobs"][0]["status"] == "goal_achieved"


# ---- mark-spam ------------------------------------------------------------
def test_mark_spam_cross_tenant_changes_nothing():
    store = _seed()
    svc = _service(store)
    result = _run(svc.mark_as_spam(tenant_id=TENANT_A, call_id="call-b"))
    assert result is None
    assert store["calls"][0]["outcome"] == "in_progress"
    assert store["leads"][0]["status"] == "calling"


def test_mark_spam_same_tenant_writes():
    store = _seed()
    svc = _service(store)
    result = _run(svc.mark_as_spam(tenant_id=TENANT_B, call_id="call-b"))
    assert result["message"] == "Marked as spam"
    assert store["calls"][0]["outcome"] == "spam"
    assert store["leads"][0]["status"] == "dnc"


def test_mark_spam_rejects_lead_id_not_belonging_to_call():
    """A lead_id that isn't the scoped call's lead -> WebhookTargetMismatch,
    and NOTHING is written (validated before mutation)."""
    store = _seed()
    svc = _service(store)
    with pytest.raises(WebhookTargetMismatch):
        _run(svc.mark_as_spam(tenant_id=TENANT_B, call_id="call-b", lead_id="lead-x"))
    # No partial write: call outcome + lead status untouched.
    assert store["calls"][0]["outcome"] == "in_progress"
    assert store["leads"][0]["status"] == "calling"


def test_mark_spam_lead_only_cross_tenant_changes_nothing():
    """Lead-only request for a foreign lead -> None, lead untouched."""
    store = _seed()
    svc = _service(store)
    result = _run(svc.mark_as_spam(tenant_id=TENANT_A, lead_id="lead-b"))
    assert result is None
    assert store["leads"][0]["status"] == "calling"


# ---- admin/configure guard -----------------------------------------------
def test_admin_configure_rejects_unauthenticated(monkeypatch):
    """/webhooks/secure/admin/configure must 401 without an internal token."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.api.v1.endpoints.webhooks_secure as ws
    from app.api.v1.dependencies import get_db_client

    # Fail-safe posture: no INTERNAL_SERVICE_TOKEN configured -> always 401.
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)

    app = FastAPI()
    app.include_router(ws.router, prefix="/api/v1")
    app.dependency_overrides[get_db_client] = lambda: None
    client = TestClient(app, raise_server_exceptions=False)

    r = client.post(
        "/api/v1/webhooks/secure/admin/configure",
        params={"webhook_name": "call_goal_achieved", "tenant_id": TENANT_B},
    )
    assert r.status_code == 401


def test_admin_configure_accepts_valid_internal_token(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.api.v1.endpoints.webhooks_secure as ws
    from app.api.v1.dependencies import get_audit_logger, get_db_client

    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "s3cret-internal")
    # configure stores the HMAC secret encrypted (bh2) and fails closed (503)
    # without a usable CONNECTOR_ENCRYPTION_KEY, so this happy path needs one.
    from cryptography.fernet import Fernet

    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEYS_OLD", raising=False)
    # Fresh service for this key; monkeypatch restores the previous singleton.
    monkeypatch.setattr("app.infrastructure.connectors.encryption._encryption_service", None)

    from contextlib import asynccontextmanager

    events, receipts = [], []

    class _Conn:
        async def fetchrow(self, sql, *args):
            assert " ".join(sql.split()) == (
                "SELECT webhook_name, is_active FROM webhook_configs "
                "WHERE tenant_id = $1 AND webhook_name = $2 FOR UPDATE"
            )
            assert tuple(map(str, args)) == (TENANT_B, "call_goal_achieved")
            assert events == ["lock"]
            events.append("read")
            return None

        async def execute(self, sql, *args):
            assert " ".join(sql.split()) == (
                "INSERT INTO webhook_configs (tenant_id, webhook_name, secret_key) "
                "VALUES ($1, $2, $3) ON CONFLICT (tenant_id, webhook_name) "
                "DO UPDATE SET secret_key = $3, is_active = TRUE, updated_at = NOW()"
            )
            assert str(args[0]) == TENANT_B and args[1] == "call_goal_achieved"
            assert args[2].startswith("enc:v1:")
            assert events == ["lock", "read"]
            events.append("write")

    conn = _Conn()

    class _Audit:
        async def lock_transaction(self, supplied):
            assert supplied is conn and events == []
            events.append("lock")

        async def log_in_transaction(self, supplied, **entry):
            assert supplied is conn and events == ["lock", "read", "write"]
            receipts.append(entry)
            events.append("audit")

    @asynccontextmanager
    async def _acquire(_pool, _tenant_id, **_kw):
        assert _pool is _DB.pool and _tenant_id == TENANT_B
        yield conn
        assert events == ["lock", "read", "write", "audit"]
        events.append("commit")

    # PostgresClient has no execute(); the route writes through the pool.
    monkeypatch.setattr(ws, "acquire_with_tenant", _acquire)

    class _DB:
        pool = object()

    app = FastAPI()
    app.include_router(ws.router, prefix="/api/v1")
    app.dependency_overrides[get_db_client] = lambda: _DB()
    app.dependency_overrides[get_audit_logger] = lambda: _Audit()
    client = TestClient(app, raise_server_exceptions=False)

    r = client.post(
        "/api/v1/webhooks/secure/admin/configure",
        params={"webhook_name": "call_goal_achieved", "tenant_id": TENANT_B},
        headers={"X-Internal-Service-Token": "s3cret-internal"},
    )
    assert r.status_code == 200
    assert r.json()["tenant_id"] == TENANT_B
    assert events == ["lock", "read", "write", "audit", "commit"]
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["action"] == "webhook.configure"
    assert receipt["actor_type"] == "system" and receipt["actor_role"] == "internal_service"
    assert str(receipt["tenant_id"]) == TENANT_B
    assert receipt["before_state"] is None
    assert receipt["after_state"] == {
        "webhook_name": "call_goal_achieved", "is_active": True, "secret_key": "[REDACTED]",
    }

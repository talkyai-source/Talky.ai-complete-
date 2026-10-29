"""Emergency approval/issuance boundaries with transactional DB doubles.

These are not PostgreSQL locking or operational break-glass acceptance tests.
The actual TOTP verifier is used; only secret decryption and the existing
authenticated-profile fence are replaced at their external boundaries.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pyotp
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api.v1.dependencies import CurrentUser, get_current_user
from app.api.v1.endpoints import emergency_access as endpoint
from app.core.security import emergency_access as module

SECRET = "JBSWY3DPEHPK3PXP"  # synthetic test secret


class State:
    def __init__(self):
        self.requestor = uuid4()
        self.row = {"request_id": uuid4(), "requestor_id": self.requestor,
                    "scenario": "security_incident", "status": "pending", "approvers_required": 2,
                    "approvals": [], "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
                    "session_created_at": None, "requested_access": ["platform:admin"], "actions_taken": [],
                    "created_at": datetime.now(timezone.utc), "justification": "Synthetic request",
                    "approved_at": None, "session_terminated_at": None, "session_token_hash": None,
                    "reviewed_at": None, "reviewed_by": None, "review_notes": None}
        self.mfa = {}
        self.mutex = asyncio.Lock()
        self.queries = []
        self.role = "platform_admin"


class Conn:
    def __init__(self, state):
        self.state = state
        self.in_transaction = False

    def is_in_transaction(self):
        return self.in_transaction

    @asynccontextmanager
    async def transaction(self):
        async with self.state.mutex:
            self.in_transaction = True
            before = deepcopy((self.state.row, self.state.mfa))
            try:
                yield
            except BaseException:
                self.state.row, self.state.mfa = before
                raise
            finally:
                self.in_transaction = False

    async def fetchrow(self, sql, *args):
        self.state.queries.append((sql, args))
        if "FROM user_profiles" in sql:
            assert self.in_transaction and "FOR UPDATE" in sql
            return {"id": args[0], "password_hash": None, "mfa_enabled": True}
        if "FROM user_mfa" in sql:
            assert self.in_transaction and "FOR UPDATE" in sql
            return deepcopy(self.state.mfa.setdefault(str(args[0]), {
                "enabled": True, "totp_secret_enc": "encrypted-synthetic-secret", "last_used_at": None,
            }))
        if "FROM emergency_access_requests" in sql:
            if "session_token_hash = $1" in sql:
                return deepcopy(self.state.row)
            assert self.in_transaction
            return deepcopy(self.state.row)
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        self.state.queries.append((sql, args))
        if sql.startswith("SET LOCAL"):
            return "SET"
        assert self.in_transaction
        if "UPDATE user_mfa" in sql:
            self.state.mfa[str(args[0])]["last_used_at"] = args[1]
        elif "SET approvals" in sql:
            self.state.row["approvals"] = deepcopy(args[0])
            if len(args) == 6:
                self.state.row.update(status=args[1], approved_at=args[2], expires_at=args[3])
            self.state.row["actions_taken"].extend(deepcopy(args[-1]))
        elif "SET session_created_at" in sql:
            self.state.row.update(session_created_at=args[0], session_token_hash=args[1], status=args[2])
            self.state.row["actions_taken"].extend(deepcopy(args[-1]))
        else:
            raise AssertionError(sql)
        return "UPDATE 1"

    async def fetch(self, sql, *args):
        self.state.queries.append((sql, args))
        assert "LIMIT $2 OFFSET $3" in sql
        return []


class Pool:
    def __init__(self, state):
        self.state = state

    @asynccontextmanager
    async def acquire(self):
        yield Conn(self.state)


class BudgetRedis:
    """Atomic counter double; the actual Lua is separately qualified in Redis."""
    def __init__(self):
        self.now = 0
        self.windows = {}

    async def eval(self, script, keys, key, window):
        assert keys == 1 and "INCR" in script and "EXPIRE" in script
        count, expiry = self.windows.get(key, (0, None))
        if expiry is not None and expiry <= self.now:
            count, expiry = 0, None
        count += 1
        if count == 1 or expiry is None:
            expiry = self.now + window
        self.windows[key] = count, expiry
        return count

    async def publish(self, *args):
        return 1


def authority(user_id):
    request = Request({"type": "http", "method": "POST", "path": "/admin/emergency", "headers": []})
    request.state.authenticated_user_id = str(user_id)
    request.state.authenticated_session_id = str(uuid4())
    return {"auth_request": request, "current_user": CurrentUser(
        id=str(user_id), email="operator@example.test", tenant_id=str(uuid4()), role="platform_admin",
    )}


@pytest.fixture
def system(monkeypatch):
    state = State()

    async def fence(conn, request, user):
        assert conn.in_transaction
        state.queries.append(("authenticated_profile_fence", (user.id,)))
        return {"role": state.role}

    monkeypatch.setattr(module, "lock_authenticated_credential_change", fence)
    monkeypatch.setattr(module, "decrypt_totp_secret", lambda encrypted: SECRET)
    return state, module.EmergencyAccess(Pool(state), redis_client=BudgetRedis())


async def approve(service, state, user_id=None, **kwargs):
    user_id = user_id or uuid4()
    return await service.approve(state.row["request_id"], user_id,
                                 verification_code=kwargs.pop("code", pyotp.TOTP(SECRET).now()),
                                 **authority(user_id), **kwargs)


@pytest.mark.parametrize("method", ["sms", "email", "in_person", "unknown"])
async def test_unimplemented_verification_methods_fail_closed(system, method):
    state, service = system
    with pytest.raises(HTTPException) as exc:
        await approve(service, state, method=method)
    assert exc.value.status_code == 400 and state.row["approvals"] == []


async def test_real_totp_proof_is_consumed_without_retaining_the_code(system):
    state, service = system
    user_id = uuid4()
    code = pyotp.TOTP(SECRET).now()
    result = await approve(service, state, user_id, code=code)
    assert result["approvals_received"] == 1 and not result["fully_approved"]
    assert state.mfa[str(user_id)]["last_used_at"] is not None
    assert state.row["approvals"][0]["verification"] == "totp_step_up"
    assert state.row["actions_taken"][-1]["action"] == "approval_verified"
    assert "verification_code" not in repr(state.row) and code not in repr(state.queries)
    with pytest.raises(HTTPException) as exc:
        await approve(service, state, user_id, code=code)
    assert exc.value.status_code == 401 and len(state.row["approvals"]) == 1


async def test_invalid_totp_never_updates_proof_or_request(system):
    state, service = system
    with pytest.raises(HTTPException) as exc:
        await approve(service, state, code="invalid")
    assert exc.value.status_code == 401
    assert state.row["approvals"] == [] and state.mfa == {}


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin", "user"])
async def test_fresh_role_revalidation_rejects_stale_platform_authority(system, role):
    state, service = system
    state.role = role
    with pytest.raises(HTTPException) as exc:
        await approve(service, state)
    assert exc.value.status_code == 403
    assert not any("FROM user_mfa" in sql for sql, _ in state.queries)


async def test_revoked_session_fence_failure_precedes_any_approval_read(system, monkeypatch):
    state, service = system
    monkeypatch.setattr(module, "lock_authenticated_credential_change",
                        AsyncMock(side_effect=HTTPException(401, "Session ended")))
    with pytest.raises(HTTPException) as exc:
        await approve(service, state)
    assert exc.value.status_code == 401 and state.row["approvals"] == []


async def test_concurrent_distinct_approvals_are_preserved(system):
    state, service = system
    results = await asyncio.gather(approve(service, state), approve(service, state))
    assert sorted(result["approvals_received"] for result in results) == [1, 2]
    assert state.row["status"] == "approved" and len(state.row["approvals"]) == 2


async def test_requestor_cannot_self_approve_and_rollback_does_not_consume_totp(system):
    state, service = system
    with pytest.raises(ValueError, match="own emergency"):
        await approve(service, state, state.requestor)
    assert state.row["approvals"] == [] and state.mfa == {}


async def test_only_one_concurrent_session_is_issued_and_redis_loss_keeps_receipt(system, caplog):
    state, service = system
    await asyncio.gather(approve(service, state), approve(service, state))
    service.redis = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("private connector detail")))
    calls = [service.create_session(state.row["request_id"], **authority(state.requestor)) for _ in range(2)]
    results = await asyncio.gather(*calls, return_exceptions=True)
    assert sum(isinstance(result, module.EmergencySession) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert state.row["status"] == "used"
    assert sum(event["action"] == "session_created" for event in state.row["actions_taken"]) == 1
    assert "emergency_notification_failed" in caplog.text and "private connector detail" not in caplog.text


async def test_legacy_unverified_approvals_never_authorize_session(system):
    state, service = system
    state.row.update(status="approved", approvals=[{"approver_id": str(uuid4()), "verification_code": "123456"}] * 2)
    with pytest.raises(ValueError, match="Verified approvals"):
        await service.create_session(state.row["request_id"], **authority(state.requestor))
    assert state.row["session_created_at"] is None


async def test_legacy_unverified_issued_token_is_not_validated(system):
    state, service = system
    state.row.update(status="used", approvals=[{"approver_id": str(uuid4()), "verification_code": "123456"}] * 2)
    assert await service.validate_session("synthetic-old-token") is None


async def test_other_platform_admin_cannot_take_requestor_session(system):
    state, service = system
    with pytest.raises(HTTPException) as exc:
        await service.create_session(state.row["request_id"], **authority(uuid4()))
    assert exc.value.status_code == 403


@pytest.mark.parametrize("status", [None, *module.EmergencyStatus])
async def test_every_filter_uses_bounded_database_listing(system, status):
    state, service = system
    assert await service.list_requests(status=status, limit=20, offset=40, **authority(uuid4())) == []
    assert state.queries[-1][1] == (status.value if status else None, 20, 40)


def test_api_rejects_tenant_admin_and_unimplemented_approval_methods():
    app = FastAPI()
    app.include_router(endpoint.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(uuid4()), email="tenant@example.test", role="tenant_admin",
    )
    with TestClient(app) as client:
        assert client.get("/admin/emergency/requests").status_code == 403
    with pytest.raises(ValueError):
        endpoint.EmergencyApproveRequest(method="sms", verification_code="123456")


async def test_readonly_listing_revalidates_platform_role_after_profile_lock(system):
    state, service = system
    state.role = "tenant_admin"
    with pytest.raises(HTTPException) as exc:
        await service.list_requests(**authority(uuid4()))
    assert exc.value.status_code == 403
    assert not any("FROM emergency_access_requests" in sql for sql, _ in state.queries)


async def test_actual_session_fence_binds_request_identity_and_session(system, monkeypatch):
    from app.core.security.credential_issuance import lock_authenticated_credential_change
    from app.core.security import principal

    state, service = system
    user_id = uuid4()
    context = authority(user_id)
    loaded = AsyncMock(return_value={"role": "platform_admin"})
    monkeypatch.setattr(principal, "load_session_principal", loaded)
    monkeypatch.setattr(module, "lock_authenticated_credential_change", lock_authenticated_credential_change)
    await service.approve(state.row["request_id"], user_id,
                          verification_code=pyotp.TOTP(SECRET).now(), **context)
    claims = loaded.call_args.args[1]
    assert claims["sub"] == str(user_id)
    assert claims["sid"] == context["auth_request"].state.authenticated_session_id

    context["auth_request"].state.authenticated_user_id = str(uuid4())
    loaded.reset_mock()
    with pytest.raises(HTTPException) as exc:
        await service.list_requests(**context)
    assert exc.value.status_code == 401
    loaded.assert_not_awaited()


async def test_audit_transport_failure_does_not_discard_committed_one_time_token(system, caplog):
    state, service = system
    await asyncio.gather(approve(service, state), approve(service, state))
    context = authority(state.requestor)
    logger = SimpleNamespace(log=AsyncMock(side_effect=RuntimeError("private audit transport details")))
    result = await endpoint.create_emergency_session(
        http_request=context["auth_request"], request_id=state.row["request_id"],
        current_user=context["current_user"], emergency_access=service, audit_logger=logger,
    )
    assert result["session_token"] and result["audit_submission_accepted"] is False
    assert state.row["status"] == "used"
    assert state.row["actions_taken"][-1]["action"] == "session_created"
    assert "emergency_audit_submission_failed" in caplog.text
    assert "private audit transport details" not in caplog.text


@pytest.mark.parametrize("operation", ["deny", "terminate_session", "complete_review"])
async def test_missing_or_inactive_request_is_not_reported_as_mutated(system, monkeypatch, operation):
    state, service = system
    original = Conn.execute

    async def no_matching_request(conn, sql, *args):
        if "UPDATE emergency_access_requests" in sql:
            return "UPDATE 0"
        return await original(conn, sql, *args)

    monkeypatch.setattr(Conn, "execute", no_matching_request)
    user_id = uuid4()
    args = {"deny": {"denied_by": user_id, "reason": "Synthetic rejection"},
            "terminate_session": {"terminated_by": user_id},
            "complete_review": {"reviewed_by": user_id, "notes": "Synthetic review"}}[operation]
    with pytest.raises(ValueError):
        await getattr(service, operation)(state.row["request_id"], **args, **authority(user_id))


@pytest.mark.parametrize("status", ["pending", "approved"])
async def test_expiry_is_consistent_in_list_and_detail_without_persisting_a_rejected_write(system, monkeypatch, status):
    state, service = system
    state.row.update(status=status, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))

    async def listing(conn, sql, *args):
        assert "effective_status=$1" in sql and args[0] == "expired"
        return [deepcopy(state.row)]

    monkeypatch.setattr(Conn, "fetch", listing)
    context = authority(state.requestor)
    listed = await service.list_requests(status=module.EmergencyStatus.EXPIRED, **context)
    detail = await service.get_request(state.row["request_id"], **context)
    assert listed[0].status == detail.status == module.EmergencyStatus.EXPIRED
    assert state.row["status"] == status


async def test_failed_proof_budget_is_account_scoped_atomic_and_survives_refusal_rollback(system, monkeypatch):
    state, service = system
    verify = Mock(return_value=None)
    monkeypatch.setattr(module, "verify_totp_step", verify)
    user_id = uuid4()
    results = await asyncio.gather(*[
        service.approve(uuid4(), user_id, verification_code="123456", **authority(user_id)) for _ in range(20)
    ], return_exceptions=True)
    assert sum(isinstance(result, HTTPException) and result.status_code == 401 for result in results) == 5
    assert sum(isinstance(result, HTTPException) and result.status_code == 429 for result in results) == 15
    assert verify.call_count == 5
    assert len(service.redis.windows) == 1
    assert service.redis.windows[f"emergency:approval-attempts:{user_id}"][0] == 20
    assert state.row["approvals"] == [] and state.mfa == {}


async def test_attempt_window_does_not_slide_and_repairs_missing_ttl_without_refunding(system):
    _, service = system
    user_id = uuid4()
    key = f"emergency:approval-attempts:{user_id}"
    await service._claim_approval_attempt(user_id)
    service.redis.now = 299
    await service._claim_approval_attempt(user_id)
    assert service.redis.windows[key] == (2, 300)
    service.redis.now = 300
    await service._claim_approval_attempt(user_id)
    assert service.redis.windows[key] == (1, 600)
    service.redis.windows[key] = (20, None)
    with pytest.raises(HTTPException) as exc:
        await service._claim_approval_attempt(user_id)
    assert exc.value.status_code == 429 and service.redis.windows[key] == (21, 600)


@pytest.mark.parametrize("failure", ["missing", "error", "timeout", "unconfirmed"])
async def test_budget_outage_fails_closed_before_secret_comparison(system, monkeypatch, failure):
    state, service = system
    verify = Mock(return_value=None)
    monkeypatch.setattr(module, "verify_totp_step", verify)
    if failure == "missing":
        service.redis = None
    elif failure == "error":
        service.redis = SimpleNamespace(eval=AsyncMock(side_effect=RuntimeError("synthetic unavailable")))
    elif failure == "unconfirmed":
        service.redis = SimpleNamespace(eval=AsyncMock(return_value="1"))
    else:
        async def stalled(*args):
            await asyncio.sleep(1)
        service.redis = SimpleNamespace(eval=stalled)
        monkeypatch.setattr(module, "APPROVAL_ATTEMPT_TIMEOUT_SECONDS", .01)
    with pytest.raises(HTTPException) as exc:
        await approve(service, state)
    assert exc.value.status_code == 503
    verify.assert_not_called()
    assert not any("FROM user_mfa" in sql for sql, _ in state.queries)


async def test_dependency_refreshes_redis_and_pool_lifecycle(monkeypatch):
    from app.api.v1 import dependencies
    from app.core import container

    monkeypatch.setattr(dependencies, "_emergency_access_service", None)
    runtime = SimpleNamespace(redis=BudgetRedis())
    monkeypatch.setattr(container, "get_container", lambda: runtime)
    pool = object()
    first = await dependencies.get_emergency_access(db_pool=pool)
    assert first.redis is runtime.redis
    runtime.redis = None
    assert await dependencies.get_emergency_access(db_pool=pool) is first
    assert first.redis is None
    runtime.redis = BudgetRedis()
    assert (await dependencies.get_emergency_access(db_pool=object())).redis is runtime.redis

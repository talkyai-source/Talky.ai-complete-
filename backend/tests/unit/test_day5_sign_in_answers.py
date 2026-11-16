"""Day-5 sign-in answers: stable codes, plain sentences, no account oracle.

D5-10  expired 2FA session vs wrong code
D5-11  locked sign-in carries a code and a delay (lock is keyed on the address)
D5-12  unverified account: code only after the RIGHT password; resend route
D5-15  plain customer sentences for four internal messages
"""
import ast
import hashlib
import importlib
import pathlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import fakeredis
import pytest
from fastapi import HTTPException
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from app.api.v1.endpoints.auth import _shared as auth_shared
from app.api.v1.endpoints.auth import password_reset as reset
from app.api.v1.endpoints.auth.schemas import LoginRequest
from app.core import error_handlers

# the package re-exports the ``verify_email`` function, shadowing the submodule
verify_email_mod = importlib.import_module("app.api.v1.endpoints.auth.verify_email")
login_mod = importlib.import_module("app.api.v1.endpoints.auth.login")
mfa_verify = importlib.import_module("app.api.v1.endpoints.mfa.verify")
MFA_DIR = pathlib.Path(mfa_verify.__file__).parent

USER_ID = uuid4()
EMAIL = "person@example.com"


def _request(path="/auth/login", ip="203.0.113.9"):
    return Request({"type": "http", "method": "POST", "path": path, "headers": [],
                    "query_string": b"", "client": (ip, 1), "scheme": "https",
                    "server": ("a", 443)})


# ---------------------------------------------------------------------------
# D5-10 two-factor answers
# ---------------------------------------------------------------------------

class _MfaFlow:
    def __init__(self, monkeypatch, *, challenge, totp_ok, attempts_after=1):
        self.conn = SimpleNamespace(fetchrow=AsyncMock(return_value={
            "totp_secret_enc": "x", "enabled": True, "last_used_at": None}),
            execute=AsyncMock())

        @asynccontextmanager
        async def acquire(pool, tenant_id, **_):
            yield self.conn

        self.record_failed = AsyncMock(return_value=attempts_after)
        self.invalidate = AsyncMock()
        monkeypatch.setattr(mfa_verify, "acquire_with_tenant", acquire)
        monkeypatch.setattr(mfa_verify, "resolve_mfa_challenge", AsyncMock(return_value=challenge))
        monkeypatch.setattr(mfa_verify, "lock_user_credential_issuance", AsyncMock())
        monkeypatch.setattr(mfa_verify, "load_current_principal", AsyncMock(return_value={
            "email": EMAIL, "role": "tenant_admin", "tenant_id": None, "business_name": "B"}))
        monkeypatch.setattr(mfa_verify, "assert_expected_identity", lambda *a, **k: None)
        monkeypatch.setattr(mfa_verify, "check_account_locked", AsyncMock(return_value=None))
        monkeypatch.setattr(mfa_verify, "record_login_attempt", AsyncMock())
        monkeypatch.setattr(mfa_verify, "decrypt_totp_secret", lambda *_: "secret")
        monkeypatch.setattr(mfa_verify, "verify_totp_step", lambda *a, **k: 1 if totp_ok else None)
        monkeypatch.setattr(mfa_verify, "record_failed_mfa_attempt", self.record_failed)
        monkeypatch.setattr(mfa_verify, "invalidate_mfa_challenge", self.invalidate)

    async def call(self, code="123456"):
        body = mfa_verify.MFAChallengeVerifyRequest(challenge_token="tok", code=code)
        return await mfa_verify.verify_mfa_challenge(
            _request("/auth/mfa/verify"), SimpleNamespace(), body, SimpleNamespace(pool=object()))


def _detail(exc):
    assert isinstance(exc.value.detail, dict), exc.value.detail
    return exc.value.detail


async def test_wrong_code_is_two_factor_failed(monkeypatch):
    flow = _MfaFlow(monkeypatch, challenge={"id": "c1", "user_id": USER_ID}, totp_ok=False)
    with pytest.raises(HTTPException) as exc:
        await flow.call()
    assert exc.value.status_code == 401
    assert _detail(exc) == {
        "code": "two_factor_failed",
        "message": "Two-factor authentication failed. Check the code and try again."}


@pytest.mark.parametrize("challenge", [None])
async def test_expired_or_unknown_challenge_is_session_expired(monkeypatch, challenge):
    flow = _MfaFlow(monkeypatch, challenge=challenge, totp_ok=True)
    with pytest.raises(HTTPException) as exc:
        await flow.call()
    assert exc.value.status_code == 401
    assert _detail(exc) == {
        "code": "two_factor_session_expired",
        "message": "Your two-factor authentication session has expired. Sign in again."}
    flow.record_failed.assert_not_awaited()


async def test_attempt_counting_is_unchanged(monkeypatch):
    flow = _MfaFlow(monkeypatch, challenge={"id": "c1", "user_id": USER_ID}, totp_ok=False,
                    attempts_after=2)
    with pytest.raises(HTTPException):
        await flow.call()
    flow.record_failed.assert_awaited_once()
    flow.invalidate.assert_not_awaited()

    burned = _MfaFlow(monkeypatch, challenge={"id": "c1", "user_id": USER_ID}, totp_ok=False,
                      attempts_after=mfa_verify.MFA_VERIFY_MAX_ATTEMPTS)
    with pytest.raises(HTTPException) as exc:
        await burned.call()
    burned.invalidate.assert_awaited_once()
    # the fifth wrong code is still an ordinary wrong-code answer
    assert _detail(exc)["code"] == "two_factor_failed"


def _customer_strings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in {"detail", "message"}:
            found += [n.value for n in ast.walk(node.value)
                      if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in {"detail", "message"}:
                    found += [n.value for n in ast.walk(value)
                              if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return found


@pytest.mark.parametrize("path", sorted(MFA_DIR.glob("*.py")) + [pathlib.Path(login_mod.__file__)])
def test_no_customer_sentence_says_mfa(path):
    offenders = [s for s in _customer_strings(path) if "MFA" in s]
    assert offenders == []


# ---------------------------------------------------------------------------
# D5-11 / D5-12 sign-in
# ---------------------------------------------------------------------------

class _Login:
    def __init__(self, monkeypatch, *, row, password_ok=True, locked_for=None):
        self.conn = SimpleNamespace(is_in_transaction=lambda: True,
                                    fetchrow=AsyncMock(return_value=row), execute=AsyncMock())

        @asynccontextmanager
        async def acquire(pool, tenant_id, **_):
            yield self.conn

        self.attempts = AsyncMock()
        locked_until = (datetime.now(timezone.utc) + timedelta(seconds=locked_for)
                        if locked_for else None)
        monkeypatch.setattr(login_mod, "acquire_with_tenant", acquire)
        monkeypatch.setattr(login_mod, "check_account_locked", AsyncMock(return_value=locked_until))
        monkeypatch.setattr(login_mod, "seconds_until_unlocked", AsyncMock(return_value=locked_for or 0))
        monkeypatch.setattr(login_mod, "verify_password", lambda *_: password_ok)
        monkeypatch.setattr(login_mod, "record_login_attempt", self.attempts)
        monkeypatch.setattr(login_mod, "verify_turnstile", AsyncMock())

    async def call(self):
        return await login_mod.login.__wrapped__(
            _request(), LoginRequest(email=EMAIL, password="pw-123456789"),
            SimpleNamespace(pool=object()),
            SimpleNamespace(log_security_event=AsyncMock(), log=AsyncMock()))

    def reasons(self):
        return [c.kwargs.get("failure_reason") for c in self.attempts.await_args_list]


def _user(**over):
    row = {"id": USER_ID, "email": EMAIL, "name": "P", "role": "tenant_admin",
           "password_hash": "h", "tenant_id": uuid4(), "is_active": True, "is_verified": True,
           "mfa_enabled": False, "business_name": "B", "minutes_allocated": 0, "minutes_used": 0}
    row.update(over)
    return row


async def _refusal(flow):
    with pytest.raises(HTTPException) as exc:
        await flow.call()
    return exc.value


async def test_unverified_with_right_password_gets_the_verify_code(monkeypatch):
    flow = _Login(monkeypatch, row=_user(is_verified=False), password_ok=True)
    refusal = await _refusal(flow)
    assert refusal.status_code == 403
    assert refusal.detail == {"code": "email_not_verified",
                              "message": "Verify your email before signing in."}


async def test_unverified_with_wrong_password_looks_like_an_unknown_address(monkeypatch):
    unverified = await _refusal(_Login(monkeypatch, row=_user(is_verified=False), password_ok=False))
    unknown = await _refusal(_Login(monkeypatch, row=None))
    verified_wrong = await _refusal(_Login(monkeypatch, row=_user(), password_ok=False))
    for refusal in (unverified, verified_wrong):
        assert (refusal.status_code, refusal.detail, refusal.headers) == (
            unknown.status_code, unknown.detail, unknown.headers)
    assert unknown.status_code == 401 and unknown.detail == auth_shared.GENERIC_AUTH_ERROR


async def test_unverified_wrong_password_is_recorded_as_a_wrong_password(monkeypatch):
    flow = _Login(monkeypatch, row=_user(is_verified=False), password_ok=False)
    await _refusal(flow)
    assert flow.reasons() == ["wrong_password"]


async def test_locked_attempt_carries_code_delay_and_header(monkeypatch):
    flow = _Login(monkeypatch, row=_user(), locked_for=125)
    refusal = await _refusal(flow)
    assert refusal.status_code == 401
    assert refusal.detail == {"code": "sign_in_locked",
                              "message": "Too many attempts. Try again in 3 minutes.",
                              "retry_after_seconds": 125}
    assert refusal.headers == {"Retry-After": "125"}
    assert flow.reasons() == ["account_locked"]


async def test_lock_answer_is_identical_for_an_address_with_no_account(monkeypatch):
    known = _Login(monkeypatch, row=_user(), locked_for=60)
    known_refusal = await _refusal(known)
    unknown = _Login(monkeypatch, row=None, locked_for=60)
    unknown_refusal = await _refusal(unknown)
    assert (known_refusal.status_code, known_refusal.detail, known_refusal.headers) == (
        unknown_refusal.status_code, unknown_refusal.detail, unknown_refusal.headers)
    # the lock is decided before any account lookup
    unknown.conn.fetchrow.assert_not_awaited()
    assert unknown_refusal.detail["message"] == "Too many attempts. Try again in 1 minute."


# ---------------------------------------------------------------------------
# D5-12 resend route
# ---------------------------------------------------------------------------

RESEND_BODY = {"message": "If that address needs verifying, we have sent a new email."}


@pytest.fixture
def resend(monkeypatch):
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    rows = {"unverified@example.com": {"id": uuid4(), "email": "unverified@example.com",
                                        "name": "U", "is_verified": False, "is_active": True},
            "verified@example.com": {"id": uuid4(), "email": "verified@example.com",
                                      "name": "V", "is_verified": True, "is_active": True}}
    conn = SimpleNamespace(execute=AsyncMock())
    conn.fetchrow = AsyncMock(side_effect=lambda sql, email, *a: rows.get(email))

    @asynccontextmanager
    async def acquire(*args, **kwargs):
        yield conn

    sender = SimpleNamespace(send_verification_email=AsyncMock(return_value=True))
    monkeypatch.setattr(verify_email_mod, "_verification_redis", lambda: redis)
    monkeypatch.setattr(verify_email_mod, "acquire_with_tenant", acquire)
    monkeypatch.setattr(verify_email_mod, "get_email_service", lambda: sender)
    return SimpleNamespace(redis=redis, conn=conn, sender=sender)


async def _resend(email, ip="203.0.113.9"):
    return await verify_email_mod.resend_verification_email(
        _request("/auth/verify-email/resend", ip),
        verify_email_mod.ResendVerificationRequest(email=email),
        SimpleNamespace(pool=object()))


async def test_resend_answers_identically_and_sends_only_for_the_unverified(resend):
    answers = [await _resend(e) for e in
               ("unknown@example.com", "verified@example.com", "unverified@example.com")]
    assert answers == [RESEND_BODY] * 3
    resend.sender.send_verification_email.assert_awaited_once()
    kwargs = resend.sender.send_verification_email.await_args.kwargs
    assert kwargs["recipient_email"] == "unverified@example.com"
    token = kwargs["verification_link"].split("token=")[1]
    stored = [a for c in resend.conn.execute.await_args_list for a in c.args]
    assert hashlib.sha256(token.encode()).hexdigest() in stored


async def test_resend_is_limited_per_address_with_the_same_answer(resend):
    answers = [await _resend("unverified@example.com") for _ in range(5)]
    assert answers == [RESEND_BODY] * 5
    assert resend.sender.send_verification_email.await_count == 3


async def test_resend_is_limited_per_ip(resend):
    for n in range(5):
        assert await _resend(f"a{n}@example.com") == RESEND_BODY
    with pytest.raises(HTTPException) as exc:
        await _resend("fresh@example.com")
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "60"
    assert await _resend("fresh@example.com", "203.0.113.10") == RESEND_BODY


async def test_resend_send_failure_still_answers_the_same(resend):
    resend.sender.send_verification_email.side_effect = RuntimeError("smtp down")
    assert await _resend("unverified@example.com") == RESEND_BODY


# ---------------------------------------------------------------------------
# D5-15 plain sentences
# ---------------------------------------------------------------------------

async def test_rate_limit_sentence_keeps_retry_after_and_adds_seconds():
    exc = RateLimitExceeded(SimpleNamespace(error_message="5/minute", limit="5/minute"))
    response = await error_handlers.rate_limit_exception_handler(_request(), exc)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "12"
    import json
    error = json.loads(response.body)["error"]
    assert error["message"] == "Too many requests. Wait a moment and try again."
    assert error["code"] == "rate_limited"
    assert error["details"]["retry_after_seconds"] == 12


PASSWORD_RESET_SENTENCE = "Password reset is not available right now. Try again in a few minutes."


def test_reset_without_redis_uses_the_plain_sentence(monkeypatch):
    import app.core.container as container
    monkeypatch.setattr(container, "get_container",
                        lambda: SimpleNamespace(is_initialized=False, redis_enabled=False))
    with pytest.raises(HTTPException) as exc:
        reset._get_redis_or_503()
    assert exc.value.status_code == 503
    assert exc.value.detail == PASSWORD_RESET_SENTENCE


async def test_reset_attempt_claim_outage_uses_the_plain_sentence():
    redis = SimpleNamespace(eval=AsyncMock(side_effect=ConnectionError("synthetic")))
    with pytest.raises(HTTPException) as exc:
        await reset._claim_reset_attempt(redis, EMAIL, "orig", {"code_hash": "a" * 64})
    assert exc.value.status_code == 503
    assert exc.value.detail == PASSWORD_RESET_SENTENCE


def test_jwt_configuration_failure_uses_the_plain_sentence_and_logs_error(monkeypatch, caplog):
    monkeypatch.setattr(auth_shared, "encode_access_token",
                        lambda **_: (_ for _ in ()).throw(RuntimeError("no signing key")))
    with caplog.at_level("ERROR"):
        with pytest.raises(HTTPException) as exc:
            auth_shared.create_jwt("u", EMAIL, "user", None)
    assert exc.value.status_code == 503
    assert exc.value.detail == "Sign-in is not available right now. Try again in a few minutes."
    assert any(r.levelname == "ERROR" and "no signing key" in r.getMessage() for r in caplog.records)

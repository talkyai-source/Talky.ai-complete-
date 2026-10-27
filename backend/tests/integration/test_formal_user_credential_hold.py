"""Real PostgreSQL formal-user hold/issuance races; synthetic crypto, no egress."""
import asyncio
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.v1.endpoints import passkeys as passkey_api
from app.api.v1.endpoints.auth import refresh as refresh_api
from app.api.v1.endpoints.auth.schemas import LoginRequest
from app.api.v1.endpoints.mfa import verify as mfa_api
from app.api.v1.endpoints.mfa import setup, status as mfa_status, recovery
from app.api.v1.endpoints.mfa.challenge import create_mfa_challenge, resolve_mfa_challenge
from app.api.v1.endpoints.mfa.schemas import MFAChallengeVerifyRequest
from app.api.v1.endpoints.mfa.schemas import MFAConfirmRequest, MFADisableRequest, MFARegenerateCodesRequest
from app.core.db_utils import acquire_with_tenant
from app.core.security import credential_issuance, passkeys
from app.core.security.principal import PrincipalUnavailable, load_current_principal, load_session_principal
from app.core.security.refresh_tokens import issue_initial_refresh_token
from app.core.security.sessions import create_session, validate_session
from app.domain.services.minutes_quota import unavailable_minutes
from app.domain.services.suspension_service import SuspensionType
from tests.integration.test_admin_authority_invariant import authority_db  # noqa: F401

login_api = importlib.import_module("app.api.v1.endpoints.auth.login")
pytestmark = pytest.mark.integration


def request():
    return Request({"type": "http", "method": "POST", "path": "/synthetic-auth",
                    "headers": [(b"origin", b"https://app.example")], "scheme": "https",
                    "client": ("127.0.0.1", 1234), "server": ("app.example", 443)})


async def credentials(db, user):
    async with acquire_with_tenant(db.pool, None) as conn:
        await credential_issuance.lock_user_credential_issuance(conn, user)
        cookie, sid = await create_session(conn, user_id=str(user), ip_address="127.0.0.1",
            bind_to_ip=False, bind_to_fingerprint=False, return_session_id=True)
        refresh, _, family = await issue_initial_refresh_token(conn, user_id=str(user),
            tenant_id=str(db.tenant_id), session_id=sid)
    return SimpleNamespace(cookie=cookie, sid=sid, refresh=refresh, family=family)


@pytest_asyncio.fixture
async def hold_db(authority_db, monkeypatch):  # noqa: F811
    db = authority_db
    await db.owner.execute(
        f'GRANT SELECT,INSERT,UPDATE,DELETE ON security_sessions,refresh_tokens,mfa_challenges,'
        f'webauthn_challenges,user_mfa,user_passkeys,login_attempts,recovery_codes TO "{db.role}"'
    )
    await db.owner.execute(f'GRANT SELECT ON white_label_partners TO "{db.role}"')
    db.actor = await db.new_user()
    db.target = await db.new_user(profile_role="user")
    db.other = await db.new_user(profile_role="user")
    await db.owner.execute("UPDATE user_profiles SET password_hash='synthetic-password',email=$2 WHERE id=$1",
                           db.target, f"{db.target}@example.com")
    db.old = [await credentials(db, db.target), await credentials(db, db.target)]
    db.unrelated = await credentials(db, db.other)
    async with acquire_with_tenant(db.pool, None) as conn:
        db.mfa = await create_mfa_challenge(conn, str(db.target), "127.0.0.1")
        db.other_mfa = await create_mfa_challenge(conn, str(db.other), "127.0.0.1")
        db.ceremony, _ = await passkeys.create_challenge(conn, "authentication", str(db.target))
        db.registration, _ = await passkeys.create_challenge(conn, "registration", str(db.target))
        db.anonymous, _ = await passkeys.create_challenge(conn, "authentication")
        db.other_ceremony, _ = await passkeys.create_challenge(conn, "authentication", str(db.other))
    await db.owner.execute(
        "INSERT INTO user_mfa(user_id,totp_secret_enc,enabled,verified_at) VALUES($1,'synthetic',TRUE,NOW())", db.target)
    db.credential_id = "synthetic-" + str(db.target)
    await db.owner.execute("""INSERT INTO user_passkeys(user_id,credential_id,credential_public_key,sign_count)
        VALUES($1,$2,'c3ludGhldGlj',0)""", db.target, db.credential_id)
    monkeypatch.setattr(login_api, "verify_password", lambda *_: True)
    monkeypatch.setattr(login_api, "rehash_if_needed", lambda *_: None)
    monkeypatch.setattr(mfa_status, "verify_password", lambda *_: True)
    monkeypatch.setattr(mfa_api, "decrypt_totp_secret", lambda _: "synthetic")
    monkeypatch.setattr(mfa_api, "verify_totp_step", lambda *_, **__: datetime.now(timezone.utc))
    monkeypatch.setattr(passkey_api, "_validate_passkey_origin", lambda _: "https://app.example")
    monkeypatch.setattr(passkeys, "verify_authentication_response",
                        lambda **_: SimpleNamespace(new_sign_count=1, user_verified=True))
    from app.domain.services import minutes_quota
    from app.services.scripts import tenant_minutes
    monkeypatch.setattr(minutes_quota, "compute_minutes_status", AsyncMock(return_value=unavailable_minutes("synthetic")))
    monkeypatch.setattr(tenant_minutes, "compute_tenant_minutes_status", AsyncMock(return_value=unavailable_minutes("synthetic")))
    try:
        yield db
    finally:
        # Anonymous challenges have no user FK and therefore need explicit cleanup.
        await db.owner.execute("DELETE FROM webauthn_challenges WHERE id=$1", UUID(db.anonymous))


async def suspend(db):
    return await db.service.suspend_user(db.target, SuspensionType.TEMPORARY, "synthetic",
        "Formal user credential hold fixture", suspended_by=db.actor, notify_user=False)


async def issue(db, kind):
    if kind == "login":
        return await login_api.login.__wrapped__(request(), LoginRequest(
            email=f"{db.target}@example.com", password="synthetic-password"), db.client,
            SimpleNamespace(log=AsyncMock(), log_security_event=AsyncMock()))
    if kind == "mfa":
        return await mfa_api.verify_mfa_challenge(request(), Response(),
            MFAChallengeVerifyRequest(challenge_token=db.mfa, code="123456"), db.client)
    if kind == "passkey":
        return await passkey_api.login_complete.__wrapped__(request(), Response(),
            passkey_api.LoginCompleteRequest(ceremony_id=db.ceremony,
                credential_response={"id": db.credential_id}), db.client)
    return await refresh_api.refresh.__wrapped__(request(), Response(), db.client, db.old[0].refresh)


async def test_formal_hold_revokes_all_dormant_credentials_and_restore_requires_fresh_login(hold_db):
    db = hold_db
    result = await suspend(db)
    assert (result.revoked_sessions, result.revoked_refresh_tokens,
            result.invalidated_mfa_challenges, result.invalidated_passkey_challenges) == (2, 2, 1, 2)
    saved = await db.owner.fetchrow("SELECT after_state FROM audit_logs WHERE event_id=$1", result.audit_event_id)
    import json
    state = json.loads(saved["after_state"]) if isinstance(saved["after_state"], str) else saved["after_state"]
    assert state["revoked_sessions"] == 2 and state["revoked_refresh_tokens"] == 2
    await db.service.restore_user(db.target, db.actor, "Reviewed synthetic hold")
    async with acquire_with_tenant(db.pool, None) as conn:
        for old in db.old:
            assert await validate_session(conn, old.cookie) is None
            with pytest.raises(PrincipalUnavailable, match="session_ended"):
                await load_session_principal(conn, {"sub": str(db.target), "sid": old.sid,
                    "tenant_id": str(db.tenant_id)})
        assert await resolve_mfa_challenge(conn, db.mfa) is None
        assert await resolve_mfa_challenge(conn, db.other_mfa) is not None
        assert await conn.fetchval("SELECT used FROM webauthn_challenges WHERE id=$1", UUID(db.registration)) is True
        assert await validate_session(conn, db.unrelated.cookie) is not None
    with pytest.raises(HTTPException) as rejected:
        await issue(db, "refresh")
    assert rejected.value.status_code == 401
    await issue(db, "login")
    assert await db.owner.fetchval(
        "SELECT count(*) FROM security_sessions WHERE user_id=$1 AND NOT revoked", db.target) == 1
    assert await db.owner.fetchval(
        "SELECT count(*) FROM refresh_tokens WHERE user_id=$1 AND revoked_at IS NULL", db.target) == 1


async def test_pre_hold_and_during_hold_discoverable_ceremonies_do_not_revive_on_restore(hold_db):
    db = hold_db
    await suspend(db)
    async with acquire_with_tenant(db.pool, None) as conn:
        during, _ = await passkeys.create_challenge(conn, "authentication")
    fresh = None
    try:
        await db.service.restore_user(db.target, db.actor, "Reviewed synthetic hold")
        async with acquire_with_tenant(db.pool, None) as conn:
            await credential_issuance.lock_user_credential_issuance(conn, db.target)
            for ceremony in (db.anonymous, during):
                with pytest.raises(PrincipalUnavailable, match="ceremony_ended"):
                    await credential_issuance.require_current_passkey_ceremony(conn, db.target, ceremony)
            fresh, _ = await passkeys.create_challenge(conn, "authentication")
            await credential_issuance.require_current_passkey_ceremony(conn, db.target, fresh)
            # A different user's old anonymous challenge remains valid.
            await credential_issuance.require_current_passkey_ceremony(conn, db.other, db.anonymous)
        with pytest.raises(HTTPException) as rejected:
            await issue(db, "passkey")
        assert rejected.value.status_code == 401
    finally:
        await db.owner.execute("DELETE FROM webauthn_challenges WHERE id=ANY($1::uuid[])",
            [UUID(value) for value in (during, fresh) if value])


@pytest.mark.parametrize("failed_table,privilege", [
    ("security_sessions", "UPDATE"), ("refresh_tokens", "UPDATE"),
    ("mfa_challenges", "UPDATE"), ("webauthn_challenges", "UPDATE"), ("audit_logs", "INSERT"),
])
async def test_revocation_or_audit_storage_failure_rolls_back_the_entire_hold(hold_db, failed_table, privilege):
    db = hold_db
    await db.owner.execute(f'REVOKE {privilege} ON {failed_table} FROM "{db.role}"')
    with pytest.raises(Exception):
        await suspend(db)
    assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", db.target)
    assert await db.owner.fetchval("SELECT count(*) FROM suspension_events WHERE target_id=$1", db.target) == 0
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE resource_id=$1", db.target) == 0
    assert await db.owner.fetchval("SELECT count(*) FROM security_sessions WHERE user_id=$1 AND NOT revoked", db.target) == 2
    assert await db.owner.fetchval("SELECT count(*) FROM refresh_tokens WHERE user_id=$1 AND revoked_at IS NULL", db.target) == 2
    assert await db.owner.fetchval("SELECT used FROM mfa_challenges WHERE user_id=$1", db.target) is False
    assert await db.owner.fetchval("SELECT used FROM webauthn_challenges WHERE id=$1", UUID(db.ceremony)) is False


@pytest.mark.parametrize("kind", ["login", "mfa", "passkey", "refresh"])
@pytest.mark.parametrize("first", ["hold", "issuance"])
async def test_formal_hold_and_real_issuer_serialize_in_both_orders(hold_db, monkeypatch, kind, first):
    db = hold_db
    entered, release, hold_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    real_lock = credential_issuance.lock_user_credential_issuance
    holder_pid = None

    async def paused(conn, user_id):
        if first == "issuance":
            result = await real_lock(conn, user_id)
        entered.set()
        await asyncio.wait_for(release.wait(), 10)
        if first == "hold":
            result = await real_lock(conn, user_id)
        return result

    owner = {"login": login_api, "mfa": mfa_api, "refresh": refresh_api,
             "passkey": credential_issuance}[kind]
    monkeypatch.setattr(owner, "lock_user_credential_issuance", paused)
    real_audit_lock = db.audit.lock_transaction

    async def capture_hold(conn):
        nonlocal holder_pid
        holder_pid = conn.get_server_pid()
        await real_audit_lock(conn)
        hold_entered.set()

    monkeypatch.setattr(db.audit, "lock_transaction", capture_hold)
    issuing = asyncio.create_task(issue(db, kind))
    holding = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        holding = asyncio.create_task(suspend(db))
        if first == "hold":
            await asyncio.wait_for(holding, 5)
        else:
            await asyncio.wait_for(hold_entered.wait(), 5)
            waiting = False
            for _ in range(100):
                waiting = await db.owner.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1 AND wait_event_type='Lock')", holder_pid)
                if waiting:
                    break
                await asyncio.sleep(0.01)
            assert waiting, "The real hold must wait for the issuer's profile row fence"
        release.set()
        outcomes = await asyncio.wait_for(asyncio.gather(issuing, holding, return_exceptions=True), 10)
        assert not isinstance(outcomes[1], Exception), outcomes[1]
        if first == "hold":
            assert isinstance(outcomes[0], HTTPException) and outcomes[0].status_code == 401
        else:
            assert not isinstance(outcomes[0], Exception), outcomes[0]
        assert await db.owner.fetchval(
            "SELECT count(*) FROM security_sessions WHERE user_id=$1 AND NOT revoked", db.target) == 0
        assert await db.owner.fetchval(
            "SELECT count(*) FROM refresh_tokens WHERE user_id=$1 AND revoked_at IS NULL", db.target) == 0
        assert await db.owner.fetchval("SELECT NOT revoked FROM security_sessions WHERE id=$1", UUID(db.unrelated.sid))
    finally:
        release.set()
        await asyncio.gather(*(task for task in (issuing, holding) if task), return_exceptions=True)


@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize("kind", ["mfa_setup", "mfa_confirm", "mfa_disable", "recovery", "register_begin", "register_complete", "delete_passkey"])
async def test_already_authenticated_credential_write_cannot_survive_formal_hold(hold_db, kind, restored):
    db = hold_db
    admitted = request()
    admitted.state.authenticated_user_id = str(db.target)
    admitted.state.authenticated_session_id = db.old[0].sid
    user = SimpleNamespace(id=str(db.target), tenant_id=str(db.tenant_id), role="user",
        email=f"{db.target}@example.com", name="Synthetic")
    await suspend(db)
    if restored:
        await db.service.restore_user(db.target, db.actor, "Reviewed synthetic hold")
    credential_id = str(await db.owner.fetchval("SELECT id FROM user_passkeys WHERE user_id=$1", db.target))
    calls = {
        "mfa_setup": lambda: setup.setup_mfa(admitted, user, db.client),
        "mfa_confirm": lambda: setup.confirm_mfa(admitted, MFAConfirmRequest(code="123456"), user, db.client),
        "mfa_disable": lambda: mfa_status.disable_mfa(admitted, MFADisableRequest(password="synthetic-password"), user, db.client),
        "recovery": lambda: recovery.regenerate_recovery_codes(admitted, MFARegenerateCodesRequest(code="123456"), user, db.client),
        "register_begin": lambda: passkey_api.register_begin(admitted, passkey_api.RegisterBeginRequest(), user, db.client),
        "register_complete": lambda: passkey_api.register_complete(admitted,
            passkey_api.RegisterCompleteRequest(ceremony_id=db.registration, credential_response={}), user, db.client),
        "delete_passkey": lambda: passkey_api.delete_passkey(credential_id, admitted, user, db.client),
    }
    with pytest.raises(HTTPException) as rejected:
        await calls[kind]()
    assert rejected.value.status_code == 401
    assert await db.owner.fetchval("SELECT enabled FROM user_mfa WHERE user_id=$1", db.target) is True
    assert await db.owner.fetchval("SELECT count(*) FROM user_passkeys WHERE user_id=$1", db.target) == 1
    assert await db.owner.fetchval("SELECT count(*) FROM security_sessions WHERE user_id=$1 AND NOT revoked", db.target) == 0


async def test_mfa_confirmation_wins_the_fence_before_pending_password_login(hold_db, monkeypatch):
    db = hold_db
    await db.owner.execute("UPDATE user_mfa SET enabled=FALSE,verified_at=NULL WHERE user_id=$1", db.target)
    monkeypatch.setattr(setup, "decrypt_totp_secret", lambda _: "synthetic")
    monkeypatch.setattr(setup, "verify_totp_step", lambda *_, **__: datetime.now(timezone.utc))
    monkeypatch.setattr(setup, "generate_recovery_codes", lambda: ["ABCD1234EFGH5678"])
    ready, release = asyncio.Event(), asyncio.Event()
    real_lock = credential_issuance.lock_user_credential_issuance
    async def paused(conn, user_id):
        ready.set()
        await asyncio.wait_for(release.wait(), 5)
        return await real_lock(conn, user_id)
    monkeypatch.setattr(login_api, "lock_user_credential_issuance", paused)
    logging_in = asyncio.create_task(issue(db, "login"))
    try:
        await asyncio.wait_for(ready.wait(), 5)
        admitted = request()
        admitted.state.authenticated_user_id = str(db.target)
        admitted.state.authenticated_session_id = db.old[0].sid
        current = SimpleNamespace(id=str(db.target), tenant_id=str(db.tenant_id))
        confirmed = await setup.confirm_mfa(admitted, MFAConfirmRequest(code="123456"), current, db.client)
        assert confirmed.enabled
        release.set()
        response = await asyncio.wait_for(logging_in, 5)
        assert b"mfa_challenge_token" in response.body
        assert await db.owner.fetchval("SELECT count(*) FROM security_sessions WHERE user_id=$1", db.target) == 2
        assert await db.owner.fetchval("SELECT count(*) FROM refresh_tokens WHERE user_id=$1", db.target) == 2
    finally:
        release.set()
        await asyncio.gather(logging_in, return_exceptions=True)

"""Actual migrated DB credential boundaries; synthetic crypto/Redis, no email."""
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.v1.endpoints import passkeys as api
from app.api.v1.endpoints.auth import password_reset as reset
from app.api.v1.endpoints.auth import _shared as auth_shared
from app.api.v1.endpoints.mfa import verify as mfa
from app.api.v1.endpoints.mfa import recovery
from app.api.v1.endpoints.mfa import setup
from app.api.v1.endpoints.mfa.schemas import MFARegenerateCodesRequest
from app.api.v1.endpoints.mfa.challenge import create_mfa_challenge
from app.api.v1.endpoints.mfa.schemas import MFAChallengeVerifyRequest
from app.api.v1.endpoints.mfa.schemas import MFAConfirmRequest
from app.core.db_utils import acquire_with_tenant
from app.core.security import passkeys
from app.core.security.recovery import store_recovery_codes
from tests.integration.test_ag05_lead_evidence import lead_db  # noqa: F401

pytestmark = pytest.mark.integration


def request():
    return Request({'type':'http','method':'POST','path':'/synthetic','headers':[(b'origin',b'https://app.example')],
                    'client':('127.0.0.1',1234),'scheme':'https','server':('app.example',443)})


@pytest_asyncio.fixture
async def credential_db(lead_db, monkeypatch):  # noqa: F811
    db=lead_db
    await db.admin.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON user_profiles,webauthn_challenges,user_passkeys,user_mfa,mfa_challenges,recovery_codes,security_sessions,refresh_tokens,login_attempts TO "{db.role}"')
    await db.admin.execute(f'GRANT SELECT ON tenants,tenant_users,roles TO "{db.role}"')
    db.users=[uuid4(),uuid4()]
    for index,user in enumerate(db.users):
        await db.admin.execute("""INSERT INTO user_profiles(id,email,name,tenant_id,role,is_active,is_verified,email_verified_at,password_hash)
            VALUES($1,$2,'Synthetic',$3,'tenant_admin',TRUE,TRUE,NOW(),'synthetic-initial-generation')""",
            user,f'{user}@example.com',db.tenants[0])
        await db.admin.execute("INSERT INTO tenant_users(tenant_id,user_id,role_id,status) SELECT $1,$2,id,'active' FROM roles WHERE name='tenant_admin'",
                               db.tenants[0],user)
    db.client=SimpleNamespace(pool=db.pool)
    monkeypatch.setattr(api,'_validate_passkey_origin',lambda _: 'https://app.example')
    monkeypatch.setattr(passkeys,'check_admin_aaguid_allowed',lambda *_: (True,''))
    monkeypatch.setattr(passkeys,'verify_registration_response',lambda **_: SimpleNamespace(
        credential_id=b'synthetic-credential',credential_public_key=b'synthetic-public-key',
        sign_count=0,credential_backed_up=False,aaguid=None))
    try:
        yield db
    finally:
        await db.admin.execute('DELETE FROM tenant_users WHERE user_id=ANY($1::uuid[])',db.users)
        await db.admin.execute('DELETE FROM user_profiles WHERE id=ANY($1::uuid[])',db.users)


async def registration(db,user=None):
    async with acquire_with_tenant(db.pool,None) as conn:
        ceremony,_=await passkeys.create_challenge(conn,'registration',str(user or db.users[0]))
    return ceremony


async def test_registration_cannot_reassign_another_users_challenge(credential_db):
    db=credential_db
    ceremony=await registration(db)
    other=SimpleNamespace(id=str(db.users[1]),tenant_id=str(db.tenants[0]),role='tenant_admin')
    with pytest.raises(HTTPException) as caught:
        await api.register_complete(request(),api.RegisterCompleteRequest(ceremony_id=ceremony,credential_response={}),other,db.client)
    assert caught.value.status_code in (400,401)
    assert await db.admin.fetchval('SELECT COUNT(*) FROM user_passkeys WHERE user_id=ANY($1::uuid[])',db.users)==0


async def test_registration_challenge_has_one_winner_under_concurrency(credential_db,monkeypatch):
    db=credential_db
    ceremony=await registration(db)
    original=passkeys.get_and_validate_challenge
    count=0
    both=asyncio.Event()
    async def simultaneous(*args,**kwargs):
        nonlocal count
        result=await original(*args,**kwargs)
        count+=1
        if count==2: both.set()
        await asyncio.wait_for(both.wait(),5)
        return result
    monkeypatch.setattr(passkeys,'get_and_validate_challenge',simultaneous)
    async def run():
        try:
            async with acquire_with_tenant(db.pool,None) as conn:
                await passkeys.verify_registration(conn,ceremony,{})
            return True
        except ValueError:
            return False
    assert sorted(await asyncio.gather(run(),run()))==[False,True]


async def test_failed_mfa_attempt_commits_before_unauthorized_response(credential_db,monkeypatch):
    db=credential_db
    await db.admin.execute("INSERT INTO user_mfa(user_id,totp_secret_enc,enabled,verified_at) VALUES($1,'synthetic',TRUE,NOW())",db.users[0])
    async with acquire_with_tenant(db.pool,None) as conn:
        challenge=await create_mfa_challenge(conn,str(db.users[0]),'127.0.0.1')
    monkeypatch.setattr(mfa,'decrypt_totp_secret',lambda _: 'synthetic')
    monkeypatch.setattr(mfa,'verify_totp_step',lambda *_,**__: None)
    with pytest.raises(HTTPException) as caught:
        await mfa.verify_mfa_challenge(request(),Response(),MFAChallengeVerifyRequest(challenge_token=challenge,code='000000'),db.client)
    assert caught.value.status_code==401
    row=await db.admin.fetchrow('SELECT attempts,used FROM mfa_challenges WHERE user_id=$1',db.users[0])
    assert row['attempts']==1 and row['used'] is False


class ResetRedis:
    def __init__(self,payload,*,fail_cleanup=False):
        self.raw=json.dumps(payload)
        self.fail_cleanup=fail_cleanup
    async def get(self,_): return self.raw
    async def delete(self,_):
        if self.fail_cleanup: raise RuntimeError('synthetic cleanup outage')
        self.raw=None
    async def eval(self,_script,_count,_key,expected):
        if self.fail_cleanup: raise RuntimeError('synthetic cleanup outage')
        if self.raw==expected: self.raw=None; return 1
        return 0


def reset_fixture(db,monkeypatch,*,fail_cleanup=False):
    user=str(db.users[0]); email=f'{user}@example.com'
    payload={'user_id':user,'email':email,'code_hash':reset._hash_reset_code('123456'),
             'password_generation':hashlib.sha256(b'synthetic-initial-generation').hexdigest()}
    redis=ResetRedis(payload,fail_cleanup=fail_cleanup)
    monkeypatch.setattr(reset,'_get_redis_or_503',lambda: redis)
    monkeypatch.setattr(reset,'validate_password_strength',lambda _: None)
    monkeypatch.setattr(reset,'hash_password',lambda value: 'synthetic-hash-'+value)
    return redis,reset.ResetPasswordRequest(email=email,code='123456',new_password='new-password')


async def test_reset_success_survives_cleanup_failure_but_code_cannot_be_replayed(credential_db,monkeypatch):
    db=credential_db
    _,body=reset_fixture(db,monkeypatch,fail_cleanup=True)
    result=await reset.reset_password(request(),body,db.client,SimpleNamespace(log=AsyncMock()))
    assert 'reset' in result['message'].lower()
    with pytest.raises(HTTPException) as caught:
        await reset.reset_password(request(),body.model_copy(update={'new_password':'another-password'}),db.client,SimpleNamespace(log=AsyncMock()))
    assert caught.value.status_code==400
    assert await db.admin.fetchval('SELECT password_hash FROM user_profiles WHERE id=$1',db.users[0])=='synthetic-hash-new-password'


async def test_concurrent_reset_code_has_one_committed_generation(credential_db,monkeypatch):
    db=credential_db
    redis,body=reset_fixture(db,monkeypatch)
    count=0; both=asyncio.Event()
    async def read(_):
        nonlocal count
        raw=redis.raw; count+=1
        if count==2: both.set()
        await asyncio.wait_for(both.wait(),5)
        return raw
    redis.get=read
    async def attempt(password):
        try:
            await reset.reset_password(request(),body.model_copy(update={'new_password':password}),db.client,SimpleNamespace(log=AsyncMock()))
            return True
        except HTTPException as exc:
            assert exc.status_code==400
            return False
    assert sorted(await asyncio.gather(attempt('first'),attempt('second')))==[False,True]


async def test_reset_cleanup_cannot_delete_a_newer_request(credential_db,monkeypatch):
    db=credential_db
    redis,body=reset_fixture(db,monkeypatch)
    original=reset._revoke_refresh_tokens_for_reset
    newer=json.dumps({'synthetic':'newer-reset-request'})
    async def replacement(*args):
        result=await original(*args)
        redis.raw=newer
        return result
    monkeypatch.setattr(reset,'_revoke_refresh_tokens_for_reset',replacement)
    await reset.reset_password(request(),body,db.client,SimpleNamespace(log=AsyncMock()))
    assert redis.raw==newer


async def test_reset_security_write_failure_preserves_password_and_request_for_retry(credential_db,monkeypatch):
    db=credential_db
    redis,body=reset_fixture(db,monkeypatch)
    old=redis.raw
    monkeypatch.setattr(reset,'_revoke_refresh_tokens_for_reset',AsyncMock(side_effect=RuntimeError('synthetic transaction outage')))
    with pytest.raises(RuntimeError):
        await reset.reset_password(request(),body,db.client,SimpleNamespace(log=AsyncMock()))
    assert redis.raw==old
    assert await db.admin.fetchval('SELECT password_hash FROM user_profiles WHERE id=$1',db.users[0])=='synthetic-initial-generation'


async def test_recovery_regeneration_failure_does_not_destroy_existing_codes(credential_db,monkeypatch):
    db=credential_db
    await db.admin.execute("INSERT INTO user_mfa(user_id,totp_secret_enc,enabled,verified_at) VALUES($1,'synthetic',TRUE,NOW())",db.users[0])
    await db.admin.execute("INSERT INTO recovery_codes(user_id,code_hash,batch_id) VALUES($1,$2,$3)",db.users[0],str(uuid4()),uuid4())
    monkeypatch.setattr(recovery,'decrypt_totp_secret',lambda _: 'synthetic')
    monkeypatch.setattr(recovery,'verify_totp_step',lambda *_,**__: datetime.now(timezone.utc))
    monkeypatch.setattr(recovery,'store_recovery_codes',AsyncMock(side_effect=RuntimeError('synthetic replacement storage outage')))
    current=SimpleNamespace(id=str(db.users[0]),tenant_id=str(db.tenants[0]))
    with pytest.raises(RuntimeError):
        await recovery.regenerate_recovery_codes(MFARegenerateCodesRequest(code='123456'),current,db.client)
    assert await db.admin.fetchval('SELECT COUNT(*) FROM recovery_codes WHERE user_id=$1',db.users[0])==1
    assert await db.admin.fetchval('SELECT last_used_at FROM user_mfa WHERE user_id=$1',db.users[0]) is None


async def ready_mfa(db, monkeypatch):
    await db.admin.execute("INSERT INTO user_mfa(user_id,totp_secret_enc,enabled,verified_at) VALUES($1,'synthetic',TRUE,NOW())", db.users[0])
    monkeypatch.setattr(mfa, 'decrypt_totp_secret', lambda _: 'synthetic')
    step = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(mfa, 'verify_totp_step', lambda *_, last_used_at=None, **__: step if last_used_at is None else None)
    monkeypatch.setattr(mfa, '_encode_access_token', lambda **_: 'synthetic-jwt')
    monkeypatch.setattr(auth_shared, 'encode_access_token', lambda **_: 'synthetic-jwt')
    async with acquire_with_tenant(db.pool, None) as conn:
        challenge = await create_mfa_challenge(conn, str(db.users[0]), '127.0.0.1')
    return challenge, step


@pytest.mark.parametrize('same_challenge', [False, True])
async def test_mfa_concurrent_step_has_one_session(credential_db, monkeypatch, same_challenge):
    db = credential_db
    first, step = await ready_mfa(db, monkeypatch)
    second = first
    if not same_challenge:
        async with acquire_with_tenant(db.pool, None) as conn:
            second = await create_mfa_challenge(conn, str(db.users[0]), '127.0.0.1')
    async def attempt(token):
        try:
            await mfa.verify_mfa_challenge(request(), Response(), MFAChallengeVerifyRequest(challenge_token=token, code='123456'), db.client)
            return True
        except HTTPException as exc:
            assert exc.status_code == 401
            return False
    assert sorted(await asyncio.gather(attempt(first), attempt(second))) == [False, True]
    assert await db.admin.fetchval('SELECT COUNT(*) FROM security_sessions WHERE user_id=$1', db.users[0]) == 1
    assert await db.admin.fetchval('SELECT COUNT(*) FROM refresh_tokens WHERE user_id=$1', db.users[0]) == 1
    assert await db.admin.fetchval('SELECT last_used_at FROM user_mfa WHERE user_id=$1', db.users[0]) == step


@pytest.mark.parametrize('factor', ['totp', 'recovery'])
@pytest.mark.parametrize('failure_stage', ['refresh', 'body_token'])
async def test_mfa_refresh_issue_failure_rolls_back_factor_and_all_session_writes(credential_db, monkeypatch, factor, failure_stage):
    db = credential_db
    challenge, _ = await ready_mfa(db, monkeypatch)
    if factor == 'recovery':
        async with acquire_with_tenant(db.pool, None) as conn:
            await store_recovery_codes(conn, str(db.users[0]), ['ABCD1234EFGH5678'])
    original = mfa.issue_cookie_auth
    async def fail_after_refresh(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('synthetic response issuance outage')
    if failure_stage == 'refresh':
        monkeypatch.setattr(mfa, 'issue_cookie_auth', fail_after_refresh)
    else:
        def fail_token(**_):
            raise RuntimeError('synthetic signing outage')
        monkeypatch.setattr(mfa, '_encode_access_token', fail_token)
    kwargs = {'code': '123456'} if factor == 'totp' else {'recovery_code': 'ABCD1234EFGH5678'}
    with pytest.raises(RuntimeError):
        await mfa.verify_mfa_challenge(request(), Response(), MFAChallengeVerifyRequest(challenge_token=challenge, **kwargs), db.client)
    assert await db.admin.fetchval('SELECT used FROM mfa_challenges WHERE user_id=$1', db.users[0]) is False
    assert await db.admin.fetchval('SELECT last_used_at FROM user_mfa WHERE user_id=$1', db.users[0]) is None
    assert await db.admin.fetchval('SELECT COUNT(*) FROM recovery_codes WHERE user_id=$1 AND used', db.users[0]) == 0
    for table in ('security_sessions', 'refresh_tokens'):
        assert await db.admin.fetchval(f'SELECT COUNT(*) FROM {table} WHERE user_id=$1', db.users[0]) == 0
    monkeypatch.setattr(mfa, 'issue_cookie_auth', original)
    monkeypatch.setattr(mfa, '_encode_access_token', lambda **_: 'synthetic-jwt')
    result = await mfa.verify_mfa_challenge(request(), Response(), MFAChallengeVerifyRequest(challenge_token=challenge, **kwargs), db.client)
    assert result.mfa_verified is True
    assert result.tenant_id == str(db.tenants[0])


async def test_passkey_issuance_failure_rolls_back_challenge_counter_and_sessions(credential_db, monkeypatch):
    db = credential_db
    credential_id = 'c3ludGhldGlj'
    await db.admin.execute('INSERT INTO user_passkeys(user_id,credential_id,credential_public_key,sign_count) VALUES($1,$2,$3,0)', db.users[0], credential_id, 'c3ludGhldGlj')
    async with acquire_with_tenant(db.pool, None) as conn:
        ceremony, _ = await passkeys.create_challenge(conn, 'authentication', str(db.users[0]))
    monkeypatch.setattr(passkeys, 'verify_authentication_response', lambda **_: SimpleNamespace(new_sign_count=1, user_verified=True))
    monkeypatch.setattr(api, 'encode_access_token', lambda **_: 'synthetic-jwt')
    monkeypatch.setattr(auth_shared, 'encode_access_token', lambda **_: 'synthetic-jwt')
    original = api.issue_cookie_auth
    async def fail_after_refresh(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('synthetic response issuance outage')
    monkeypatch.setattr(api, 'issue_cookie_auth', fail_after_refresh)
    body = api.LoginCompleteRequest(ceremony_id=ceremony, credential_response={'id': credential_id})
    with pytest.raises(RuntimeError):
        await api.login_complete.__wrapped__(request(), Response(), body, db.client)
    assert await db.admin.fetchval('SELECT used FROM webauthn_challenges WHERE id=$1', ceremony) is False
    assert await db.admin.fetchval('SELECT sign_count FROM user_passkeys WHERE credential_id=$1', credential_id) == 0
    for table in ('security_sessions', 'refresh_tokens'):
        assert await db.admin.fetchval(f'SELECT COUNT(*) FROM {table} WHERE user_id=$1', db.users[0]) == 0
    monkeypatch.setattr(api, 'issue_cookie_auth', original)
    result = await api.login_complete.__wrapped__(request(), Response(), body, db.client)
    assert result.access_token == 'synthetic-jwt'
    assert result.tenant_id == str(db.tenants[0])
    assert await db.admin.fetchval('SELECT sign_count FROM user_passkeys WHERE credential_id=$1', credential_id) == 1


async def test_legacy_reset_without_generation_requires_a_fresh_code(credential_db, monkeypatch):
    db = credential_db
    redis, body = reset_fixture(db, monkeypatch)
    old = json.loads(redis.raw)
    old.pop('password_generation')
    redis.raw = json.dumps(old)
    with pytest.raises(HTTPException) as caught:
        await reset.reset_password(request(), body, db.client, SimpleNamespace(log=AsyncMock()))
    assert caught.value.status_code == 400
    assert await db.admin.fetchval('SELECT password_hash FROM user_profiles WHERE id=$1', db.users[0]) == 'synthetic-initial-generation'


def ready_setup(db, monkeypatch):
    monkeypatch.setattr(setup, 'generate_totp_secret', lambda: 'synthetic-fresh-secret')
    monkeypatch.setattr(setup, 'encrypt_totp_secret', lambda _: 'synthetic-encrypted-secret')
    monkeypatch.setattr(setup, 'decrypt_totp_secret', lambda _: 'synthetic-pending-secret')
    monkeypatch.setattr(setup, 'get_provisioning_uri', lambda *_: 'otpauth://synthetic')
    monkeypatch.setattr(setup, 'generate_qr_code_data_uri', lambda _: 'data:image/png;base64,synthetic')
    monkeypatch.setattr(setup, 'verify_totp_step', lambda *_, **__: datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(setup, 'generate_recovery_codes', lambda: ['ABCD1234EFGH5678'])
    return SimpleNamespace(id=str(db.users[0]), tenant_id=str(db.tenants[0]), email=f'{db.users[0]}@example.com')


async def test_mfa_setup_cannot_downgrade_concurrently_confirmed_factor(credential_db, monkeypatch):
    db = credential_db
    user = ready_setup(db, monkeypatch)
    await db.admin.execute("INSERT INTO user_mfa(user_id,totp_secret_enc,enabled) VALUES($1,'synthetic-pending-secret',FALSE)", db.users[0])
    read_pending = asyncio.Event()
    confirmed = asyncio.Event()
    original = setup.acquire_with_tenant

    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        async def fetchrow(self, sql, *args):
            result = await self.conn.fetchrow(sql, *args)
            if 'SELECT enabled FROM user_mfa' in sql:
                read_pending.set()
                await asyncio.wait_for(confirmed.wait(), 5)
            return result

    @asynccontextmanager
    async def barrier(*args):
        async with original(*args) as conn:
            yield Connection(conn)

    monkeypatch.setattr(setup, 'acquire_with_tenant', barrier)

    async def confirm():
        await asyncio.wait_for(read_pending.wait(), 5)
        try:
            result = await setup.confirm_mfa(MFAConfirmRequest(code='123456'), user, db.client)
            assert result.enabled is True
        finally:
            confirmed.set()

    async def restart_setup():
        try:
            await setup.setup_mfa(user, db.client)
            return 'downgraded'
        except HTTPException as exc:
            return exc.status_code

    result, _ = await asyncio.gather(restart_setup(), confirm())
    assert result == 409
    row = await db.admin.fetchrow('SELECT enabled,totp_secret_enc FROM user_mfa WHERE user_id=$1', db.users[0])
    assert row['enabled'] is True and row['totp_secret_enc'] == 'synthetic-pending-secret'
    assert await db.admin.fetchval('SELECT mfa_enabled FROM user_profiles WHERE id=$1', db.users[0]) is True
    assert await db.admin.fetchval('SELECT COUNT(*) FROM recovery_codes WHERE user_id=$1', db.users[0]) == 1


async def test_mfa_new_and_pending_setup_remain_available(credential_db, monkeypatch):
    db = credential_db
    user = ready_setup(db, monkeypatch)
    for _ in range(2):
        result = await setup.setup_mfa(user, db.client)
        assert result.account == user.email
        assert await db.admin.fetchval('SELECT enabled FROM user_mfa WHERE user_id=$1', db.users[0]) is False

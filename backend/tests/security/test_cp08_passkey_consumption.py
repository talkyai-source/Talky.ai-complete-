"""The verified crypto result alone cannot win a consumed DB challenge."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import hashlib
import json

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

import pytest

from app.core.security import passkeys


@pytest.mark.parametrize('ceremony',['registration','authentication'])
async def test_lost_challenge_consume_cannot_authorize_credentials(monkeypatch,ceremony):
    conn=SimpleNamespace(fetchrow=AsyncMock(return_value={
        'challenge':'c3ludGhldGlj','ceremony':ceremony,'user_id':None,'ip_address':None,
        'expires_at':datetime.now(timezone.utc)+timedelta(minutes=1),'used':False}),
        execute=AsyncMock(return_value='UPDATE 0'))
    monkeypatch.setattr(passkeys,'verify_registration_response',lambda **_: SimpleNamespace(
        credential_id=b'synthetic',credential_public_key=b'key',sign_count=0,credential_backed_up=False,aaguid=None))
    monkeypatch.setattr(passkeys,'verify_authentication_response',lambda **_: SimpleNamespace(new_sign_count=1,user_verified=True))
    with pytest.raises(ValueError):
        if ceremony=='registration':
            await passkeys.verify_registration(conn,str(uuid4()),{})
        else:
            await passkeys.verify_authentication(conn,str(uuid4()),{},'synthetic','c3ludGhldGlj',0)


@pytest.mark.parametrize('ceremony',['registration','authentication'])
@pytest.mark.parametrize('verified',[False,True])
async def test_actual_webauthn_sdk_requires_user_verification(monkeypatch,ceremony,verified):
    # Actual py_webauthn parsing/signature/flag checks using an ephemeral key.
    # This is protocol evidence, not physical authenticator compatibility.
    monkeypatch.setenv('PASSKEY_REQUIRE_USER_VERIFICATION','false')
    b64=passkeys.bytes_to_base64url
    challenge=b'synthetic-challenge-32-bytes-long!'
    credential_id=b'synthetic-credential'
    key=ec.generate_private_key(ec.SECP256R1())
    numbers=key.public_key().public_numbers()
    public=cbor2.dumps({1:2,3:-7,-1:1,-2:numbers.x.to_bytes(32,'big'),-3:numbers.y.to_bytes(32,'big')})
    client=json.dumps({'type':'webauthn.create' if ceremony=='registration' else 'webauthn.get',
        'challenge':b64(challenge),'origin':'https://app.example','crossOrigin':False}).encode()
    flags=1 | (4 if verified else 0) | (64 if ceremony=='registration' else 0)
    auth=hashlib.sha256(b'app.example').digest()+bytes([flags])+(1).to_bytes(4,'big')
    if ceremony=='registration':
        auth+=bytes(16)+len(credential_id).to_bytes(2,'big')+credential_id+public
        response={'clientDataJSON':b64(client),'attestationObject':b64(cbor2.dumps({'fmt':'none','authData':auth,'attStmt':{}}))}
    else:
        signature=key.sign(auth+hashlib.sha256(client).digest(),ec.ECDSA(hashes.SHA256()))
        response={'clientDataJSON':b64(client),'authenticatorData':b64(auth),'signature':b64(signature)}
    credential={'id':b64(credential_id),'rawId':b64(credential_id),'type':'public-key','response':response}
    conn=SimpleNamespace(fetchrow=AsyncMock(return_value={
        'challenge':b64(challenge),'ceremony':ceremony,'user_id':None,'ip_address':None,
        'expires_at':datetime.now(timezone.utc)+timedelta(minutes=1),'used':False}),
        execute=AsyncMock(return_value='UPDATE 1'))
    async def verify():
        kwargs={'expected_origin':'https://app.example','expected_rp_id':'app.example'}
        if ceremony=='registration':
            return await passkeys.verify_registration(conn,str(uuid4()),credential,**kwargs)
        return await passkeys.verify_authentication(conn,str(uuid4()),credential,b64(credential_id),b64(public),0,**kwargs)
    if not verified:
        with pytest.raises(ValueError):
            await verify()
        conn.execute.assert_not_awaited()
    else:
        result=await verify()
        if ceremony=='authentication': assert result.user_verified is True
        conn.execute.assert_awaited_once()

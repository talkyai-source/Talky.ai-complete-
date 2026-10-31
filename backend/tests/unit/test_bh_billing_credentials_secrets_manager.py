"""bughunt billing-money-credentials-6 / -7 - secrets rotation and API-key
validation.

6: rotate() generated a new API key and threw the plaintext away, stored `{}`
   for every other secret type, always minted tk_live_ keys and hard-coded a
   90-day expiry.
7: one undecryptable DEK aborted validate_api_key for every tenant; failed
   validations left no trace and the public route had no explicit rate limit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import secrets as sec
from app.domain.services import secrets_manager as sm
from app.domain.services.secrets_manager import SecretsManager

OWNER = uuid4()
MASTER = b"k" * 32


class _Ctx:
    def __init__(self, conn):
        self._c = conn

    async def __aenter__(self):
        return self._c

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.executed: list[tuple[str, tuple]] = []

    def transaction(self):
        return _Ctx(self)

    async def fetchrow(self, query, *args):
        for r in self.rows:
            if str(r["secret_id"]) == str(args[0]):
                return r
        return None

    async def fetch(self, query, *args):
        return list(self.rows)

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "OK"


class _Pool:
    def __init__(self, conn):
        self._c = conn

    def acquire(self, *a, **k):
        return _Ctx(self._c)


def _mgr(rows):
    conn = _Conn(rows)
    return SecretsManager(_Pool(conn), master_key=MASTER, redis_client=None), conn


def _row(mgr, secret_type, value, *, days=30, secret_id=None):
    dek = mgr._generate_dek()
    ct, iv = mgr._encrypt_value(value, dek)
    created = datetime.utcnow() - timedelta(days=1)
    return {
        "secret_id": secret_id or uuid4(),
        "tenant_id": OWNER,
        "secret_type": secret_type,
        "secret_name": "n",
        "description": "d",
        "version": 1,
        "created_at": created,
        "expires_at": created + timedelta(days=days),
        "permissions": {"permissions": ["calls:read"]},
        "encrypted_value": ct,
        "encrypted_dek": mgr._encrypt_dek(dek),
        "iv": iv,
        "is_active": True,
    }


def _insert_args(conn):
    (q, args), = [e for e in conn.executed if "INSERT INTO tenant_secrets" in e[0]]
    return args


def _decrypt_inserted(mgr, args):
    # (new_id, tenant, admin, type, name, desc, ct, enc_dek, iv, algo, perms,
    #  version, rotated_from, expires, active)
    dek = mgr._decrypt_dek(args[7])
    return mgr._decrypt_value(args[6], args[8], dek)


@pytest.mark.asyncio
async def test_rotating_a_test_api_key_returns_a_usable_test_key_with_same_period():
    probe, _ = _mgr([])
    old_key = probe._generate_api_key(test=True)
    value = {"api_key_hash": probe._hash_api_key(old_key), "prefix": old_key[:16]}
    mgr, conn = _mgr([])
    row = _row(mgr, "TENANT_API_KEY", value, days=30)
    conn.rows.append(row)

    new_id, new_key = await mgr.rotate(row["secret_id"], rotated_by=uuid4(), tenant_id=OWNER)

    assert isinstance(new_id, UUID)
    assert new_key and new_key.startswith(SecretsManager.API_KEY_PREFIX_TEST)
    args = _insert_args(conn)
    stored = _decrypt_inserted(mgr, args)
    assert stored["api_key_hash"] == mgr._hash_api_key(new_key)
    assert stored["prefix"] == new_key[:16]
    expires = args[13]
    assert timedelta(days=29) < expires - datetime.utcnow() < timedelta(days=31)


@pytest.mark.asyncio
async def test_rotating_a_live_api_key_stays_live():
    probe, _ = _mgr([])
    old_key = probe._generate_api_key()
    mgr, conn = _mgr([])
    row = _row(mgr, "TENANT_API_KEY", {"api_key_hash": "x", "prefix": old_key[:16]})
    conn.rows.append(row)
    _, new_key = await mgr.rotate(row["secret_id"], rotated_by=uuid4(), tenant_id=OWNER)
    assert new_key.startswith(SecretsManager.API_KEY_PREFIX)


@pytest.mark.asyncio
async def test_rotating_non_api_key_without_value_is_refused_and_writes_nothing():
    mgr, conn = _mgr([])
    row = _row(mgr, "WEBHOOK_HMAC", {"secret": "s3cret"})
    conn.rows.append(row)
    with pytest.raises(sm.SecretValueRequired):
        await mgr.rotate(row["secret_id"], rotated_by=uuid4(), tenant_id=OWNER)
    # old secret untouched, no empty replacement (SET LOCAL is the RLS scope)
    assert [q for q, _ in conn.executed if "tenant_secrets" in q] == []


@pytest.mark.asyncio
async def test_rotating_non_api_key_with_value_stores_that_value():
    mgr, conn = _mgr([])
    row = _row(mgr, "WEBHOOK_HMAC", {"secret": "old"})
    conn.rows.append(row)
    new_id, api_key = await mgr.rotate(
        row["secret_id"], rotated_by=uuid4(), tenant_id=OWNER, new_value={"secret": "new"}
    )
    assert api_key is None
    assert _decrypt_inserted(mgr, _insert_args(conn)) == {"secret": "new"}


def _route_user():
    return {"id": uuid4(), "tenant_id": str(OWNER)}


@pytest.mark.asyncio
async def test_route_returns_new_api_key_once_and_passes_value():
    mgr = MagicMock()
    new_id = uuid4()
    mgr.rotate = AsyncMock(return_value=(new_id, "tk_live_NEWKEY"))
    out = await sec.rotate_secret(
        tenant_id=OWNER, secret_id=uuid4(),
        data=sec.RotateSecretRequest(value={"secret": "v"}),
        current_user=_route_user(), secrets_manager=mgr, audit_logger=AsyncMock(),
    )
    assert out["api_key"] == "tk_live_NEWKEY"
    assert out["new_secret_id"] == new_id
    assert mgr.rotate.call_args.kwargs["new_value"] == {"secret": "v"}


@pytest.mark.asyncio
async def test_route_audit_log_never_contains_the_plaintext_key():
    mgr = MagicMock()
    mgr.rotate = AsyncMock(return_value=(uuid4(), "tk_live_SUPERSECRET"))
    audit = AsyncMock()
    await sec.rotate_secret(
        tenant_id=OWNER, secret_id=uuid4(), data=sec.RotateSecretRequest(),
        current_user=_route_user(), secrets_manager=mgr, audit_logger=audit,
    )
    assert "SUPERSECRET" not in repr(audit.log.call_args)


@pytest.mark.asyncio
async def test_route_maps_missing_value_to_400_and_missing_secret_to_404():
    mgr = MagicMock()
    mgr.rotate = AsyncMock(side_effect=sm.SecretValueRequired("value required"))
    with pytest.raises(HTTPException) as ei:
        await sec.rotate_secret(
            tenant_id=OWNER, secret_id=uuid4(), data=sec.RotateSecretRequest(),
            current_user=_route_user(), secrets_manager=mgr, audit_logger=AsyncMock(),
        )
    assert ei.value.status_code == 400
    mgr.rotate = AsyncMock(side_effect=ValueError("Secret not found"))
    with pytest.raises(HTTPException) as ei:
        await sec.rotate_secret(
            tenant_id=OWNER, secret_id=uuid4(), data=sec.RotateSecretRequest(),
            current_user=_route_user(), secrets_manager=mgr, audit_logger=AsyncMock(),
        )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_platform_route_returns_new_api_key_and_maps_400():
    mgr = MagicMock()
    mgr.rotate = AsyncMock(return_value=(uuid4(), "tk_live_PLAT"))
    out = await sec.rotate_platform_secret(
        secret_id=uuid4(), data=sec.RotateSecretRequest(),
        current_user=_route_user(), secrets_manager=mgr, audit_logger=AsyncMock(),
    )
    assert out["api_key"] == "tk_live_PLAT"
    mgr.rotate = AsyncMock(side_effect=sm.SecretValueRequired("value required"))
    with pytest.raises(HTTPException) as ei:
        await sec.rotate_platform_secret(
            secret_id=uuid4(), data=sec.RotateSecretRequest(),
            current_user=_route_user(), secrets_manager=mgr, audit_logger=AsyncMock(),
        )
    assert ei.value.status_code == 400


# ---------------------------------------------------------------- finding 7

@pytest.mark.asyncio
async def test_one_corrupt_dek_does_not_break_validation_for_other_tenants(caplog):
    mgr, conn = _mgr([])
    good_key = mgr._generate_api_key()
    bad = _row(mgr, "TENANT_API_KEY", {"api_key_hash": "irrelevant"})
    bad["encrypted_dek"] = b"\x00" * 40  # cannot be unwrapped
    good = _row(mgr, "TENANT_API_KEY", {"api_key_hash": mgr._hash_api_key(good_key)})
    conn.rows.extend([bad, good])
    with caplog.at_level(logging.WARNING, logger=sm.__name__):
        result = await mgr.validate_api_key(good_key)
    assert result is not None and result["tenant_id"] == OWNER
    assert any("dek" in r.getMessage().lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_failed_validation_is_logged_without_key_material(caplog):
    mgr, conn = _mgr([])
    conn.rows.append(_row(mgr, "TENANT_API_KEY", {"api_key_hash": "nope"}))
    presented = mgr.API_KEY_PREFIX + "Z" * 48
    with caplog.at_level(logging.WARNING, logger=sm.__name__):
        assert await mgr.validate_api_key(presented) is None
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "api_key_validation_failed" in msgs
    assert "ZZZZ" not in msgs


class _Req:
    def __init__(self, ip="203.0.113.7", xff=None):
        self.client = type("C", (), {"host": ip})()
        self.headers = {"X-Forwarded-For": xff} if xff else {}


@pytest.mark.asyncio
async def test_validate_route_rate_limits_per_ip():
    sec._VALIDATE_ATTEMPTS.clear()
    mgr = MagicMock()
    mgr.validate_api_key = AsyncMock(return_value=None)
    body = sec.ValidateApiKeyRequest(api_key="tk_live_x")
    limit = sec._VALIDATE_MAX_PER_WINDOW
    statuses = []
    for _ in range(limit + 3):
        try:
            await sec.validate_api_key(body, request=_Req(), secrets_manager=mgr)
        except HTTPException as e:
            statuses.append(e.status_code)
    assert statuses == [401] * limit + [429] * 3
    # A different client is unaffected.
    with pytest.raises(HTTPException) as ei:
        await sec.validate_api_key(body, request=_Req(ip="198.51.100.9"), secrets_manager=mgr)
    assert ei.value.status_code == 401
    sec._VALIDATE_ATTEMPTS.clear()

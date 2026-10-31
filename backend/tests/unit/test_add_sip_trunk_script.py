"""scripts/add_sip_trunk.py must write exactly what POST /telephony/sip/trunks writes."""
from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import importlib.util

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "add_sip_trunk.py"
_spec = importlib.util.spec_from_file_location("add_sip_trunk", _SCRIPT)
add_sip_trunk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(add_sip_trunk)

_ENDPOINT = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "endpoints" / "telephony_sip" / "trunks.py"


def _insert_columns(sql: str) -> list[str]:
    m = re.search(r"INSERT INTO tenant_sip_trunks\s*\((.*?)\)\s*VALUES", sql, re.S)
    assert m, sql
    return [c.strip() for c in m.group(1).split(",") if c.strip()]


def test_insert_writes_the_same_columns_as_the_endpoint():
    endpoint_sql = _ENDPOINT.read_text(encoding="utf-8")
    assert _insert_columns(add_sip_trunk.INSERT_SQL) == _insert_columns(endpoint_sql)
    assert "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $10)" in add_sip_trunk.INSERT_SQL


def test_body_is_what_the_settings_form_sends_and_passes_the_endpoint_schema():
    body = add_sip_trunk.build_request_body(
        name="blaze-pbx-940001", domain=" SIP3.Blazedigitel.com ", username="940001", password="secret-x1"
    )
    canon = add_sip_trunk.canonical_payload(body)
    assert canon["sip_domain"] == "sip3.blazedigitel.com"
    assert canon["port"] == 5060 and canon["transport"] == "udp" and canon["direction"] == "both"
    assert canon["metadata"] == {"register": True, "register_interval": 3600, "dtmf_mode": "rfc2833", "srtp": False}
    assert "caller_id" not in canon["metadata"]


def test_schema_rejects_a_username_without_password():
    body = add_sip_trunk.build_request_body(name="t-1", domain="sip3.blazedigitel.com", username="940001", password="")
    with pytest.raises(Exception):
        add_sip_trunk.canonical_payload(body)


@pytest.mark.asyncio
async def test_create_encrypts_installs_tenant_context_and_activates(monkeypatch):
    tenant = str(uuid.uuid4())
    actor = str(uuid.uuid4())
    trunk_id = uuid.uuid4()
    calls: list[tuple[str, tuple]] = []

    class _Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Conn:
        def transaction(self):
            return _Tx()

        async def fetchrow(self, sql, *args):
            calls.append((" ".join(sql.split()), args))
            if sql.lstrip().startswith("INSERT"):
                return {"id": trunk_id, "trunk_name": args[1], "is_active": False}
            return {"id": trunk_id, "is_active": True}

    acquired = {}

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id, *, user_id=None, **kw):
        acquired.update({"tenant_id": tenant_id, "user_id": user_id})
        yield _Conn()

    rls = {}

    async def fake_rls(conn, tenant_id, user_id=None, request_id=None):
        rls.update({"tenant_id": tenant_id, "user_id": user_id})

    class _Enc:
        def encrypt(self, value):
            return "enc:" + "*" * len(value)

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", fake_acquire)
    monkeypatch.setattr("app.core.tenant_rls.apply_tenant_rls_context", fake_rls)
    monkeypatch.setattr("app.infrastructure.connectors.encryption.get_encryption_service", lambda: _Enc())

    body = add_sip_trunk.build_request_body(
        name="blaze-pbx-940001", domain="sip3.blazedigitel.com", username="940001", password="secret-x1"
    )
    result = await add_sip_trunk.create_trunk(object(), tenant_id=tenant, actor_user_id=actor, body=body, activate=True)

    assert result == {"id": str(trunk_id), "trunk_name": "blaze-pbx-940001", "is_active": True}
    # Tenant context installed the way the endpoint does it (RLS + audit actor).
    assert acquired == {"tenant_id": tenant, "user_id": actor} and rls == {"tenant_id": tenant, "user_id": actor}
    insert_sql, insert_args = calls[0]
    assert insert_sql.startswith("INSERT INTO tenant_sip_trunks")
    assert insert_args[0] == tenant and insert_args[9] == actor
    assert insert_args[7] == "enc:*********", "password must be stored encrypted"
    assert "secret-x1" not in repr(insert_args), "plaintext password must never reach the database"
    activate_sql, activate_args = calls[1]
    assert "SET is_active = TRUE" in activate_sql and "live_registration_status = 'checking'" in activate_sql
    assert activate_args == (tenant, trunk_id, actor)

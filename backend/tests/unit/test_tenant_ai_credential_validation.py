"""F3-06: real mounted API, strict tenant transaction/audit fakes, no providers."""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1 import dependencies
from app.api.v1.endpoints import tenant_ai_credentials as ep

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
KEY_ID = "33333333-3333-4333-8333-333333333333"
NEW_ID = "44444444-4444-4444-8444-444444444444"
ACTOR = "55555555-5555-4555-8555-555555555555"
PLAIN = "synthetic-private-provider-key-1234"
CHECKED = datetime(2026, 11, 6, tzinfo=timezone.utc)


class Store:
    def __init__(self):
        self.rows = [{"id": KEY_ID, "tenant_id": TENANT, "provider": "groq", "credential_kind": "api_key",
                      "encrypted_key": "old-ciphertext", "last4": "0000", "label": "existing", "status": "active",
                      "validation_status": None, "last_checked_at": None, "updated_at": CHECKED - timedelta(days=1),
                      "created_at": CHECKED - timedelta(days=2), "last_used_at": None}]
        self.events = []
        self.audit = []
        self.depth = 0
        self.acquires = 0
        self.audit_fail = False

    @asynccontextmanager
    async def transaction(self):
        snapshot = deepcopy((self.rows, self.audit))
        self.depth += 1
        try:
            yield self
        except BaseException:
            self.rows, self.audit = snapshot
            raise
        finally:
            self.depth -= 1

    async def fetch(self, sql, *args):
        text = " ".join(sql.split())
        assert "FROM tenant_ai_credentials" in text and "WHERE tenant_id = $1" in text
        assert args == (TENANT,)
        return deepcopy(self.rows)

    async def fetchrow(self, sql, *args):
        text = " ".join(sql.split())
        if text.startswith("SELECT"):
            assert "FROM tenant_ai_credentials" in text and ("tenant_id=$1" in text or "tenant_id = $1" in text)
            assert args[0] == TENANT
            if "FOR UPDATE" in text:
                assert "audit-lock" in self.events
            if args[1] == "groq":
                return deepcopy(next((row for row in self.rows if row["status"] == "active"), None))
            return deepcopy(next((row for row in self.rows if row["id"] == str(args[1])), None))
        if text.startswith("INSERT INTO tenant_ai_credentials"):
            assert self.depth > 0 and "audit-lock" in self.events
            assert args[:3] == (TENANT, "groq", "api_key")
            row = {**self.rows[0], "id": NEW_ID, "encrypted_key": args[3], "last4": args[4], "label": args[5], "status": "active"}
            if "validation_status" in text:
                row.update(validation_status=args[6], last_checked_at=args[7])
            self.rows.append(row)
            self.events.append("insert")
            return deepcopy(row)
        if text.startswith("UPDATE tenant_ai_credentials") and "validation_status" in text:
            assert self.depth > 0 and "audit-lock" in self.events
            assert "tenant_id" in text and "updated_at" in text and "RETURNING" in text
            assert args[:2] == (TENANT, KEY_ID)
            row = next((r for r in self.rows if r["id"] == KEY_ID), None)
            if row is None or row["updated_at"] != args[4]:
                return None
            row.update(validation_status=args[2], last_checked_at=args[3])
            self.events.append("validate-update")
            return deepcopy(row)
        raise AssertionError("Unexpected credential SQL: " + text)

    async def execute(self, sql, *args):
        text = " ".join(sql.split())
        assert text.startswith("UPDATE tenant_ai_credentials SET status = 'disabled'"), text
        assert "WHERE tenant_id = $1 AND provider = $2" in text
        assert args == (TENANT, "groq", "api_key")
        assert self.depth > 0 and "audit-lock" in self.events
        for row in self.rows:
            if row["status"] == "active":
                row["status"] = "disabled"
        self.events.append("disable")
        return "UPDATE 1"


@pytest.fixture
def case(monkeypatch):
    store = Store()
    user = dependencies.CurrentUser(id=ACTOR, email="synthetic@example.invalid", tenant_id=TENANT, role="tenant_admin")

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        assert tenant_id == TENANT
        store.acquires += 1
        async with store.transaction():
            yield store

    async def lock(conn):
        assert conn is store and store.depth > 0
        store.events.append("audit-lock")

    async def audit(conn, **event):
        assert conn is store and store.depth > 0
        assert event["tenant_id"] == TENANT
        assert PLAIN not in str(event) and "ciphertext" not in str(event)
        if store.audit_fail:
            raise RuntimeError("synthetic audit unavailable")
        store.audit.append(event)

    class Encryption:
        def encrypt(self, value):
            assert value == PLAIN
            store.events.append("encrypt")
            return "new-ciphertext"

        def decrypt(self, value):
            assert value == "old-ciphertext"
            return PLAIN

    async def valid_probe(provider, key):
        assert store.depth == 0, "provider I/O must not hold a database transaction"
        assert provider == "groq" and key == PLAIN
        store.events.append("probe")
        return SimpleNamespace(status="valid", checked_at=CHECKED)

    probe = AsyncMock(side_effect=valid_probe)
    monkeypatch.setattr(ep, "acquire_with_tenant", acquire)
    monkeypatch.setattr(ep, "probe_provider_key", probe, raising=False)
    monkeypatch.setattr(ep, "get_encryption_service", lambda: Encryption())
    invalidations = []
    monkeypatch.setattr(ep.CredentialResolver, "invalidate_cache", lambda **kwargs: invalidations.append(kwargs))
    app = FastAPI()
    app.include_router(ep.router, prefix="/api/v1")
    app.dependency_overrides[dependencies.get_current_user] = lambda: user
    app.dependency_overrides[dependencies.get_db_pool] = lambda: object()
    app.dependency_overrides[dependencies.get_audit_logger] = lambda: SimpleNamespace(lock_transaction=lock, log_in_transaction=audit)
    return SimpleNamespace(store=store, user=user, app=app, probe=probe, invalidations=invalidations)


async def request(case, method="POST", suffix="", payload=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app, raise_app_exceptions=False),
                                 base_url="https://synthetic.example.invalid") as client:
        return await client.request(method, "/api/v1/tenant-ai-credentials/" + suffix,
                                    json=payload if payload is not None else ({"provider": "groq", "api_key": PLAIN} if not suffix and method == "POST" else None))


async def test_valid_create_probes_before_rotation_and_persists_validation(case):
    response = await request(case)
    assert response.status_code == 201, response.text
    assert case.store.events.index("probe") < case.store.events.index("encrypt") < case.store.events.index("disable")
    assert response.json()["validation_status"] == "valid"
    assert response.json()["last_checked_at"] == CHECKED.isoformat()
    assert [row["status"] for row in case.store.rows] == ["disabled", "active"]
    assert case.store.rows[1]["validation_status"] == "valid"
    assert case.store.audit[0]["action"] == "ai_credential.rotate"
    assert PLAIN not in response.text and "ciphertext" not in response.text
    assert len(case.invalidations) == 1


async def test_invalid_candidate_never_replaces_or_encrypts_prior_active_key(case):
    case.probe.side_effect = None
    case.probe.return_value = SimpleNamespace(status="invalid", checked_at=CHECKED)
    before = deepcopy(case.store.rows)
    response = await request(case)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "provider_key_invalid"
    assert case.store.acquires == 0 and case.store.events == []
    assert case.store.rows == before and case.store.audit == [] and case.invalidations == []
    assert PLAIN not in response.text


async def test_provider_outage_preserves_prior_key_and_is_not_invalid(case):
    from app.domain.services.provider_key_validation import ProviderProbeUnavailable
    case.probe.side_effect = ProviderProbeUnavailable("provider_unavailable")
    before = deepcopy(case.store.rows)
    response = await request(case)
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "provider_key_check_unavailable"
    assert case.store.rows == before and case.store.acquires == 0


@pytest.mark.parametrize("payload", [{"provider": "unknown", "api_key": PLAIN},
                                    {"provider": "groq", "api_key": PLAIN, "credential_kind": "service_account_json"}])
async def test_unsupported_provider_or_credential_kind_is_not_saved(case, payload):
    response = await request(case, payload=payload)
    assert response.status_code in (400, 422), response.text
    case.probe.assert_not_awaited()
    assert case.store.acquires == 0


async def test_list_exposes_unchecked_legacy_state_without_ciphertext(case):
    response = await request(case, "GET")
    assert response.status_code == 200
    assert response.json()[0]["validation_status"] is None
    assert response.json()[0]["last_checked_at"] is None
    assert "ciphertext" not in response.text


@pytest.mark.parametrize("status", ["valid", "invalid"])
async def test_recheck_persists_result_audited_without_changing_activation(case, status):
    async def checked(provider, key):
        assert case.store.depth == 0
        return SimpleNamespace(status=status, checked_at=CHECKED)

    case.probe.side_effect = checked
    response = await request(case, suffix=KEY_ID + "/check")
    assert response.status_code == 200, response.text
    assert response.json()["validation_status"] == status
    assert response.json()["last_checked_at"] == CHECKED.isoformat()
    assert case.store.rows[0]["status"] == "active"
    assert case.store.rows[0]["encrypted_key"] == "old-ciphertext"
    assert case.store.audit[0]["action"] == "ai_credential.check"


async def test_recheck_missing_or_other_tenant_id_does_not_decrypt_or_probe(case):
    response = await request(case, suffix=OTHER + "/check")
    assert response.status_code == 404
    case.probe.assert_not_awaited()
    assert case.store.audit == []


async def test_late_probe_does_not_overwrite_a_rotated_credential(case):
    async def rotated(provider, key):
        case.store.rows[0].update(status="disabled", updated_at=CHECKED)
        return SimpleNamespace(status="invalid", checked_at=CHECKED)

    case.probe.side_effect = rotated
    response = await request(case, suffix=KEY_ID + "/check")
    assert response.status_code == 409, response.text
    assert case.store.rows[0]["validation_status"] is None
    assert case.store.rows[0]["status"] == "disabled"
    assert case.store.audit == []


async def test_recheck_outage_keeps_the_last_observation_and_does_not_audit_invalid(case):
    from app.domain.services.provider_key_validation import ProviderProbeUnavailable
    case.store.rows[0].update(validation_status="valid", last_checked_at=CHECKED - timedelta(days=1))
    before = deepcopy(case.store.rows)
    case.probe.side_effect = ProviderProbeUnavailable("provider_unavailable")
    response = await request(case, suffix=KEY_ID + "/check")
    assert response.status_code == 503, response.text
    assert case.store.rows == before and case.store.audit == []


async def test_recheck_audit_failure_rolls_back_the_observation(case):
    case.store.audit_fail = True
    before = deepcopy(case.store.rows)
    response = await request(case, suffix=KEY_ID + "/check")
    assert response.status_code == 500
    assert case.store.rows == before


async def test_audit_failure_rolls_back_rotation_and_validation(case):
    case.store.audit_fail = True
    before = deepcopy(case.store.rows)
    response = await request(case)
    assert response.status_code == 500
    assert case.store.rows == before
    assert case.invalidations == []


@pytest.mark.parametrize("suffix", ["", KEY_ID + "/check"])
async def test_nonadmin_cannot_probe_or_mutate_credentials(case, suffix):
    case.user.role = "user"
    response = await request(case, suffix=suffix)
    assert response.status_code == 403
    case.probe.assert_not_awaited()
    assert case.store.acquires == 0


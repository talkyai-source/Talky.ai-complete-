from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints.telephony_sip import (
    CodecPolicyCreateRequest,
    RoutePolicyCreateRequest,
    SIPTrunkCreateRequest,
    SIPTrunkUpdateRequest,
    activate_codec_policy,
    activate_route_policy,
    activate_sip_trunk,
    create_codec_policy,
    create_route_policy,
    create_sip_trunk,
    deactivate_codec_policy,
    deactivate_route_policy,
    deactivate_sip_trunk,
    get_telephony_quota_status,
    list_codec_policies,
    list_route_policies,
    list_sip_trunks,
    update_sip_trunk,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_FILE = REPO_ROOT / "database" / "migrations" / "20260224_add_tenant_sip_onboarding.sql"


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _AcquireContext:
    def __init__(self, conn: "FakeTelephonyConn") -> None:
        self._conn = conn

    async def __aenter__(self) -> "FakeTelephonyConn":
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class FakePool:
    def __init__(self, conn: "FakeTelephonyConn") -> None:
        self._conn = conn

    def acquire(self) -> _AcquireContext:
        return _AcquireContext(self._conn)


@dataclass
class _IdempotencyEntry:
    tenant_id: str
    operation: str
    idempotency_key: str
    request_hash: str
    response_body: Optional[Dict[str, Any]] = None
    status_code: Optional[int] = None


class FakeTelephonyConn:
    def __init__(self) -> None:
        self.trunks: Dict[str, Dict[str, Any]] = {}
        self.codec_policies: Dict[str, Dict[str, Any]] = {}
        self.route_policies: Dict[str, Dict[str, Any]] = {}
        self.threshold_policies: List[Dict[str, Any]] = []
        self.quota_events: List[Dict[str, Any]] = []
        self.idempotency: Dict[Tuple[str, str, str], _IdempotencyEntry] = {}
        self.current_tenant_id: Optional[str] = None
        self.current_user_id: Optional[str] = None
        self.current_request_id: Optional[str] = None

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def fetch(self, query: str, *args):
        normalized = " ".join(query.split())
        if "FROM tenant_telephony_threshold_policies" in normalized:
            tenant_id = args[0]
            scope = args[1]
            metric_key = args[2] if len(args) > 2 else None
            rows = [
                row
                for row in self.threshold_policies
                if row["tenant_id"] == tenant_id
                and row["policy_scope"] == scope
                and row["is_active"] is True
                and (metric_key is None or row["metric_key"] in {metric_key, "*"})
            ]
            rows.sort(key=lambda row: row["metric_key"])
            return rows
        if "FROM tenant_sip_trunks" in normalized and "ORDER BY created_at DESC" in normalized:
            tenant_id = args[0]
            rows = [row for row in self.trunks.values() if row["tenant_id"] == tenant_id]
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return rows
        if "FROM tenant_codec_policies" in normalized and "ORDER BY created_at DESC" in normalized:
            tenant_id = args[0]
            rows = [row for row in self.codec_policies.values() if row["tenant_id"] == tenant_id]
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return rows
        if "FROM tenant_route_policies" in normalized and "ORDER BY priority ASC" in normalized:
            tenant_id = args[0]
            rows = [row for row in self.route_policies.values() if row["tenant_id"] == tenant_id]
            rows.sort(key=lambda r: (r["priority"], -r["created_at"].timestamp()))
            return rows
        raise AssertionError(f"Unexpected fetch query: {normalized}")

    async def fetchrow(self, query: str, *args):
        normalized = " ".join(query.split())

        if "FROM tenant_telephony_threshold_policies" in normalized:
            tenant_id, scope, metric_key = args
            candidates = [
                row
                for row in self.threshold_policies
                if row["tenant_id"] == tenant_id
                and row["policy_scope"] == scope
                and row["is_active"] is True
                and row["metric_key"] in {metric_key, "*"}
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda row: (0 if row["metric_key"] == metric_key else 1, row["policy_name"]))
            return candidates[0]

        if normalized.startswith("INSERT INTO tenant_telephony_idempotency"):
            tenant_id, operation, idem_key, request_hash, _window_secs = args
            key = (tenant_id, operation, idem_key)
            if key in self.idempotency:
                return None
            self.idempotency[key] = _IdempotencyEntry(
                tenant_id=tenant_id,
                operation=operation,
                idempotency_key=idem_key,
                request_hash=request_hash,
            )
            return {"id": str(uuid4())}

        if normalized.startswith("SELECT request_hash, response_body, status_code FROM tenant_telephony_idempotency"):
            tenant_id, operation, idem_key = args
            entry = self.idempotency.get((tenant_id, operation, idem_key))
            if not entry:
                return None
            return {
                "request_hash": entry.request_hash,
                "response_body": entry.response_body,
                "status_code": entry.status_code,
            }

        if normalized.startswith("INSERT INTO tenant_sip_trunks"):
            (
                tenant_id,
                trunk_name,
                sip_domain,
                port,
                transport,
                direction,
                auth_username,
                auth_password_encrypted,
                metadata_json,
                created_by,
            ) = args

            for row in self.trunks.values():
                if row["tenant_id"] == tenant_id and row["trunk_name"].lower() == trunk_name.lower():
                    import asyncpg

                    raise asyncpg.UniqueViolationError("duplicate trunk")

            trunk_id = str(uuid4())
            now = datetime.now(timezone.utc)
            row = {
                "id": trunk_id,
                "tenant_id": tenant_id,
                "trunk_name": trunk_name,
                "sip_domain": sip_domain,
                "port": port,
                "transport": transport,
                "direction": direction,
                "is_active": False,
                "auth_username": auth_username,
                "auth_password_encrypted": auth_password_encrypted,
                "metadata": json.loads(metadata_json),
                "created_at": now,
                "updated_at": now,
                "created_by": created_by,
                "updated_by": created_by,
            }
            self.trunks[trunk_id] = row
            return row

        if "FROM tenant_sip_trunks WHERE tenant_id = $1 AND id = $2" in normalized:
            tenant_id, trunk_id = args
            trunk = self.trunks.get(str(trunk_id))
            if not trunk or trunk["tenant_id"] != tenant_id:
                return None
            return trunk

        if normalized.startswith("UPDATE tenant_sip_trunks SET trunk_name"):
            (
                tenant_id,
                trunk_id,
                trunk_name,
                sip_domain,
                port,
                transport,
                direction,
                auth_username,
                auth_password_encrypted,
                metadata_json,
                updated_by,
            ) = args
            trunk = self.trunks.get(str(trunk_id))
            if not trunk or trunk["tenant_id"] != tenant_id:
                return None
            trunk.update(
                {
                    "trunk_name": trunk_name,
                    "sip_domain": sip_domain,
                    "port": port,
                    "transport": transport,
                    "direction": direction,
                    "auth_username": auth_username,
                    "auth_password_encrypted": auth_password_encrypted,
                    "metadata": json.loads(metadata_json),
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": updated_by,
                }
            )
            return trunk

        if normalized.startswith("UPDATE tenant_sip_trunks SET is_active"):
            tenant_id, trunk_id, active_state, updated_by = args
            trunk = self.trunks.get(str(trunk_id))
            if not trunk or trunk["tenant_id"] != tenant_id:
                return None
            trunk.update(
                {
                    "is_active": bool(active_state),
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": updated_by,
                }
            )
            return trunk

        if normalized.startswith("INSERT INTO tenant_codec_policies"):
            (
                tenant_id,
                policy_name,
                allowed_codecs,
                preferred_codec,
                sample_rate_hz,
                ptime_ms,
                max_bitrate_kbps,
                jitter_buffer_ms,
                metadata_json,
                created_by,
            ) = args

            for row in self.codec_policies.values():
                if row["tenant_id"] == tenant_id and row["policy_name"].lower() == policy_name.lower():
                    import asyncpg

                    raise asyncpg.UniqueViolationError("duplicate codec policy")

            policy_id = str(uuid4())
            now = datetime.now(timezone.utc)
            row = {
                "id": policy_id,
                "tenant_id": tenant_id,
                "policy_name": policy_name,
                "allowed_codecs": list(allowed_codecs),
                "preferred_codec": preferred_codec,
                "sample_rate_hz": sample_rate_hz,
                "ptime_ms": ptime_ms,
                "max_bitrate_kbps": max_bitrate_kbps,
                "jitter_buffer_ms": jitter_buffer_ms,
                "is_active": False,
                "metadata": json.loads(metadata_json),
                "created_at": now,
                "updated_at": now,
                "created_by": created_by,
                "updated_by": created_by,
            }
            self.codec_policies[policy_id] = row
            return row

        if "FROM tenant_codec_policies WHERE tenant_id = $1 AND id = $2" in normalized:
            tenant_id, policy_id = args
            policy = self.codec_policies.get(str(policy_id))
            if not policy or policy["tenant_id"] != tenant_id:
                return None
            return policy

        if normalized.startswith("UPDATE tenant_codec_policies SET policy_name"):
            (
                tenant_id,
                policy_id,
                policy_name,
                allowed_codecs,
                preferred_codec,
                sample_rate_hz,
                ptime_ms,
                max_bitrate_kbps,
                jitter_buffer_ms,
                metadata_json,
                updated_by,
            ) = args
            policy = self.codec_policies.get(str(policy_id))
            if not policy or policy["tenant_id"] != tenant_id:
                return None
            policy.update(
                {
                    "policy_name": policy_name,
                    "allowed_codecs": list(allowed_codecs),
                    "preferred_codec": preferred_codec,
                    "sample_rate_hz": sample_rate_hz,
                    "ptime_ms": ptime_ms,
                    "max_bitrate_kbps": max_bitrate_kbps,
                    "jitter_buffer_ms": jitter_buffer_ms,
                    "metadata": json.loads(metadata_json),
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": updated_by,
                }
            )
            return policy

        if normalized.startswith("UPDATE tenant_codec_policies SET is_active"):
            tenant_id, policy_id, active_state, updated_by = args
            policy = self.codec_policies.get(str(policy_id))
            if not policy or policy["tenant_id"] != tenant_id:
                return None
            policy.update(
                {
                    "is_active": bool(active_state),
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": updated_by,
                }
            )
            return policy

        if normalized.startswith("INSERT INTO tenant_route_policies"):
            (
                tenant_id,
                policy_name,
                route_type,
                priority,
                match_pattern,
                target_trunk_id,
                codec_policy_id,
                strip_digits,
                prepend_digits,
                is_active,
                metadata_json,
                created_by,
            ) = args

            for row in self.route_policies.values():
                if row["tenant_id"] == tenant_id and row["policy_name"].lower() == policy_name.lower():
                    import asyncpg

                    raise asyncpg.UniqueViolationError("duplicate route policy")

            trunk = self.trunks.get(str(target_trunk_id))
            if not trunk or trunk["tenant_id"] != tenant_id:
                import asyncpg

                raise asyncpg.ForeignKeyViolationError("invalid trunk")

            if codec_policy_id is not None:
                policy = self.codec_policies.get(str(codec_policy_id))
                if not policy or policy["tenant_id"] != tenant_id:
                    import asyncpg

                    raise asyncpg.ForeignKeyViolationError("invalid codec policy")

            route_id = str(uuid4())
            now = datetime.now(timezone.utc)
            row = {
                "id": route_id,
                "tenant_id": tenant_id,
                "policy_name": policy_name,
                "route_type": route_type,
                "priority": priority,
                "match_pattern": match_pattern,
                "target_trunk_id": str(target_trunk_id),
                "codec_policy_id": str(codec_policy_id) if codec_policy_id else None,
                "strip_digits": strip_digits,
                "prepend_digits": prepend_digits,
                "is_active": bool(is_active),
                "metadata": json.loads(metadata_json),
                "created_at": now,
                "updated_at": now,
                "created_by": created_by,
                "updated_by": created_by,
            }
            self.route_policies[route_id] = row
            return row

        if "FROM tenant_route_policies WHERE tenant_id = $1 AND id = $2" in normalized:
            tenant_id, policy_id = args
            policy = self.route_policies.get(str(policy_id))
            if not policy or policy["tenant_id"] != tenant_id:
                return None
            return policy

        if normalized.startswith("UPDATE tenant_route_policies SET policy_name"):
            (
                tenant_id,
                policy_id,
                policy_name,
                route_type,
                priority,
                match_pattern,
                target_trunk_id,
                codec_policy_id,
                strip_digits,
                prepend_digits,
                is_active,
                metadata_json,
                updated_by,
            ) = args
            policy = self.route_policies.get(str(policy_id))
            if not policy or policy["tenant_id"] != tenant_id:
                return None

            trunk = self.trunks.get(str(target_trunk_id))
            if not trunk or trunk["tenant_id"] != tenant_id:
                import asyncpg

                raise asyncpg.ForeignKeyViolationError("invalid trunk")
            if codec_policy_id is not None:
                codec = self.codec_policies.get(str(codec_policy_id))
                if not codec or codec["tenant_id"] != tenant_id:
                    import asyncpg

                    raise asyncpg.ForeignKeyViolationError("invalid codec")

            policy.update(
                {
                    "policy_name": policy_name,
                    "route_type": route_type,
                    "priority": priority,
                    "match_pattern": match_pattern,
                    "target_trunk_id": str(target_trunk_id),
                    "codec_policy_id": str(codec_policy_id) if codec_policy_id else None,
                    "strip_digits": strip_digits,
                    "prepend_digits": prepend_digits,
                    "is_active": bool(is_active),
                    "metadata": json.loads(metadata_json),
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": updated_by,
                }
            )
            return policy

        if normalized.startswith("UPDATE tenant_route_policies SET is_active"):
            tenant_id, policy_id, active_state, updated_by = args
            policy = self.route_policies.get(str(policy_id))
            if not policy or policy["tenant_id"] != tenant_id:
                return None
            policy.update(
                {
                    "is_active": bool(active_state),
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": updated_by,
                }
            )
            return policy

        raise AssertionError(f"Unexpected fetchrow query: {normalized}")

    async def execute(self, query: str, *args):
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT set_config('app.current_tenant_id'"):
            self.current_tenant_id = str(args[0]) if args and args[0] is not None else None
            return "SELECT 1"
        if normalized.startswith("SELECT set_config('app.current_user_id'"):
            self.current_user_id = str(args[0]) if args and args[0] is not None else None
            return "SELECT 1"
        if normalized.startswith("SELECT set_config('app.current_request_id'"):
            self.current_request_id = str(args[0]) if args and args[0] is not None else None
            return "SELECT 1"
        if normalized.startswith("UPDATE tenant_telephony_idempotency SET response_body"):
            tenant_id, operation, idem_key, response_body_json, status_code, _resource_type, _resource_id = args
            key = (tenant_id, operation, idem_key)
            entry = self.idempotency.get(key)
            if entry:
                entry.response_body = json.loads(response_body_json)
                entry.status_code = int(status_code)
            return "UPDATE 1"
        if normalized.startswith("INSERT INTO tenant_telephony_quota_events"):
            (
                tenant_id,
                policy_id,
                event_type,
                policy_scope,
                metric_key,
                counter_value,
                threshold_value,
                window_seconds,
                block_ttl_seconds,
                request_id,
                details_json,
                created_by,
            ) = args
            self.quota_events.append(
                {
                    "tenant_id": tenant_id,
                    "policy_id": str(policy_id) if policy_id else None,
                    "event_type": event_type,
                    "policy_scope": policy_scope,
                    "metric_key": metric_key,
                    "counter_value": int(counter_value),
                    "threshold_value": int(threshold_value) if threshold_value is not None else None,
                    "window_seconds": int(window_seconds),
                    "block_ttl_seconds": int(block_ttl_seconds),
                    "request_id": request_id,
                    "details": json.loads(details_json),
                    "created_by": created_by,
                }
            )
            return "INSERT 1"
        raise AssertionError(f"Unexpected execute query: {normalized}")


@pytest.fixture
def wsf_context():
    conn = FakeTelephonyConn()
    pool = FakePool(conn)
    user = CurrentUser(
        id=str(uuid4()),
        email="wsf@example.com",
        tenant_id=str(uuid4()),
        role="admin",
    )
    conn.threshold_policies.append(
        {
            "id": str(uuid4()),
            "tenant_id": user.tenant_id,
            "policy_name": "api-default",
            "policy_scope": "api_mutation",
            "metric_key": "*",
            "window_seconds": 60,
            "warn_threshold": 20,
            "throttle_threshold": 30,
            "block_threshold": 45,
            "block_duration_seconds": 300,
            "throttle_retry_seconds": 2,
            "metadata": {"seeded": True},
            "is_active": True,
        }
    )
    return conn, pool, user


def _make_request(path: str) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _json_body(response) -> Dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


def _create_payload(name: str = "main-trunk") -> SIPTrunkCreateRequest:
    return SIPTrunkCreateRequest(
        trunk_name=name,
        sip_domain="sip.example.com",
        port=5060,
        transport="udp",
        direction="both",
        auth_username="user1",
        auth_password="pass1",
        metadata={"region": "us-east"},
    )


def _create_codec_payload(name: str = "codec-main") -> CodecPolicyCreateRequest:
    return CodecPolicyCreateRequest(
        policy_name=name,
        allowed_codecs=["PCMU", "PCMA"],
        preferred_codec="PCMU",
        sample_rate_hz=8000,
        ptime_ms=20,
        jitter_buffer_ms=60,
        metadata={"profile": "default"},
    )


@pytest.mark.asyncio
async def test_create_trunk_requires_idempotency_key(wsf_context):
    _conn, pool, user = wsf_context
    response = await create_sip_trunk(
        payload=_create_payload(),
        request=_make_request("/api/v1/telephony/sip/trunks"),
        idempotency_key=None,
        current_user=user,
        db_pool=pool,
    )

    assert response.status_code == 400
    assert response.media_type == "application/problem+json"
    assert _json_body(response)["title"] == "Idempotency Key Required"


@pytest.mark.asyncio
async def test_create_trunk_success_and_list(wsf_context):
    conn, pool, user = wsf_context
    created = await create_sip_trunk(
        payload=_create_payload("trunk-a"),
        request=_make_request("/api/v1/telephony/sip/trunks"),
        idempotency_key="k-1",
        current_user=user,
        db_pool=pool,
    )

    assert created.status_code == 201
    created_body = _json_body(created)
    assert created_body["trunk_name"] == "trunk-a"
    assert created_body["auth_configured"] is True
    assert conn.trunks[created_body["id"]]["auth_password_encrypted"] != "pass1"

    listed = await list_sip_trunks(
        request=_make_request("/api/v1/telephony/sip/trunks"),
        current_user=user,
        db_pool=pool,
    )
    assert len(listed) == 1
    assert str(listed[0].id) == created_body["id"]
    assert conn.current_tenant_id == user.tenant_id
    assert conn.current_user_id == user.id


@pytest.mark.asyncio
async def test_create_trunk_idempotent_replay_returns_cached_result(wsf_context):
    _conn, pool, user = wsf_context
    payload = _create_payload("replay-trunk")

    first = await create_sip_trunk(
        payload=payload,
        request=_make_request("/api/v1/telephony/sip/trunks"),
        idempotency_key="k-replay",
        current_user=user,
        db_pool=pool,
    )
    second = await create_sip_trunk(
        payload=payload,
        request=_make_request("/api/v1/telephony/sip/trunks"),
        idempotency_key="k-replay",
        current_user=user,
        db_pool=pool,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert _json_body(first)["id"] == _json_body(second)["id"]


@pytest.mark.asyncio
async def test_create_trunk_idempotency_mismatch_returns_conflict(wsf_context):
    _conn, pool, user = wsf_context

    first = await create_sip_trunk(
        payload=_create_payload("idem-trunk"),
        request=_make_request("/api/v1/telephony/sip/trunks"),
        idempotency_key="k-conflict",
        current_user=user,
        db_pool=pool,
    )
    assert first.status_code == 201

    second = await create_sip_trunk(
        payload=_create_payload("idem-trunk-changed"),
        request=_make_request("/api/v1/telephony/sip/trunks"),
        idempotency_key="k-conflict",
        current_user=user,
        db_pool=pool,
    )
    assert second.status_code == 409
    assert _json_body(second)["title"] == "Idempotency Conflict"


@pytest.mark.asyncio
async def test_update_trunk_rejects_half_auth_patch(wsf_context):
    _conn, pool, user = wsf_context
    no_auth_payload = SIPTrunkCreateRequest(
        trunk_name="patch-trunk",
        sip_domain="sip.example.com",
        port=5060,
        transport="udp",
        direction="both",
        metadata={"region": "us-east"},
    )
    created = await create_sip_trunk(
        payload=no_auth_payload,
        request=_make_request("/api/v1/telephony/sip/trunks"),
        idempotency_key="k-patch-create",
        current_user=user,
        db_pool=pool,
    )
    trunk_id = _json_body(created)["id"]

    patched = await update_sip_trunk(
        trunk_id=trunk_id,
        payload=SIPTrunkUpdateRequest(auth_username="new-user"),
        request=_make_request(f"/api/v1/telephony/sip/trunks/{trunk_id}"),
        idempotency_key="k-patch",
        current_user=user,
        db_pool=pool,
    )
    assert patched.status_code == 400
    assert _json_body(patched)["title"] == "Invalid Authentication Configuration"


@pytest.mark.asyncio
async def test_activate_and_deactivate_trunk(wsf_context):
    _conn, pool, user = wsf_context
    created = await create_sip_trunk(
        payload=_create_payload("activate-trunk"),
        request=_make_request("/api/v1/telephony/sip/trunks"),
        idempotency_key="k-activate-create",
        current_user=user,
        db_pool=pool,
    )
    trunk_id = _json_body(created)["id"]

    active = await activate_sip_trunk(
        trunk_id=trunk_id,
        request=_make_request(f"/api/v1/telephony/sip/trunks/{trunk_id}/activate"),
        idempotency_key="k-activate",
        current_user=user,
        db_pool=pool,
    )
    assert active.is_active is True

    inactive = await deactivate_sip_trunk(
        trunk_id=trunk_id,
        request=_make_request(f"/api/v1/telephony/sip/trunks/{trunk_id}/deactivate"),
        idempotency_key="k-deactivate",
        current_user=user,
        db_pool=pool,
    )
    assert inactive.is_active is False


@pytest.mark.asyncio
async def test_cross_tenant_update_trunk_is_denied(wsf_context):
    _conn, pool, user = wsf_context
    created = await create_sip_trunk(
        payload=_create_payload("tenant-a-trunk"),
        request=_make_request("/api/v1/telephony/sip/trunks"),
        idempotency_key="k-tenant-a",
        current_user=user,
        db_pool=pool,
    )
    trunk_id = _json_body(created)["id"]

    other_tenant_user = CurrentUser(
        id=str(uuid4()),
        email="other@example.com",
        tenant_id=str(uuid4()),
        role="admin",
    )
    response = await update_sip_trunk(
        trunk_id=trunk_id,
        payload=SIPTrunkUpdateRequest(trunk_name="hijack-attempt"),
        request=_make_request(f"/api/v1/telephony/sip/trunks/{trunk_id}"),
        idempotency_key="k-tenant-b",
        current_user=other_tenant_user,
        db_pool=pool,
    )
    assert response.status_code == 404
    assert _json_body(response)["title"] == "Trunk Not Found"


@pytest.mark.asyncio
async def test_tenant_context_required_returns_problem_json(wsf_context):
    _conn, pool, _user = wsf_context
    no_tenant_user = CurrentUser(
        id=str(uuid4()),
        email="no-tenant@example.com",
        tenant_id=None,
        role="user",
    )

    response = await list_sip_trunks(
        request=_make_request("/api/v1/telephony/sip/trunks"),
        current_user=no_tenant_user,
        db_pool=pool,
    )

    assert response.status_code == 403
    assert response.media_type == "application/problem+json"
    assert _json_body(response)["title"] == "Tenant Context Required"


@pytest.mark.asyncio
async def test_create_codec_policy_list_and_toggle_active_state(wsf_context):
    _conn, pool, user = wsf_context

    created = await create_codec_policy(
        payload=_create_codec_payload("codec-a"),
        request=_make_request("/api/v1/telephony/sip/codec-policies"),
        idempotency_key="k-codec-create",
        current_user=user,
        db_pool=pool,
    )
    assert created.status_code == 201
    body = _json_body(created)
    assert body["policy_name"] == "codec-a"
    assert body["preferred_codec"] == "PCMU"

    listed = await list_codec_policies(
        request=_make_request("/api/v1/telephony/sip/codec-policies"),
        current_user=user,
        db_pool=pool,
    )
    assert len(listed) == 1
    codec_id = body["id"]
    assert str(listed[0].id) == codec_id

    active = await activate_codec_policy(
        policy_id=codec_id,
        request=_make_request(f"/api/v1/telephony/sip/codec-policies/{codec_id}/activate"),
        idempotency_key="k-codec-active",
        current_user=user,
        db_pool=pool,
    )
    assert active.is_active is True

    inactive = await deactivate_codec_policy(
        policy_id=codec_id,
        request=_make_request(f"/api/v1/telephony/sip/codec-policies/{codec_id}/deactivate"),
        idempotency_key="k-codec-inactive",
        current_user=user,
        db_pool=pool,
    )
    assert inactive.is_active is False


@pytest.mark.asyncio
async def test_create_route_policy_with_valid_references_and_toggle(wsf_context):
    _conn, pool, user = wsf_context

    trunk = await create_sip_trunk(
        payload=_create_payload("route-trunk"),
        request=_make_request("/api/v1/telephony/sip/trunks"),
        idempotency_key="k-route-trunk",
        current_user=user,
        db_pool=pool,
    )
    trunk_id = _json_body(trunk)["id"]

    codec = await create_codec_policy(
        payload=_create_codec_payload("route-codec"),
        request=_make_request("/api/v1/telephony/sip/codec-policies"),
        idempotency_key="k-route-codec",
        current_user=user,
        db_pool=pool,
    )
    codec_id = _json_body(codec)["id"]

    route_payload = RoutePolicyCreateRequest(
        policy_name="outbound-default",
        route_type="outbound",
        priority=10,
        match_pattern=r"^\\+?[1-9][0-9]{7,14}$",
        target_trunk_id=trunk_id,
        codec_policy_id=codec_id,
        strip_digits=0,
        prepend_digits=None,
        is_active=True,
        metadata={"purpose": "default"},
    )
    route = await create_route_policy(
        payload=route_payload,
        request=_make_request("/api/v1/telephony/sip/route-policies"),
        idempotency_key="k-route-create",
        current_user=user,
        db_pool=pool,
    )
    assert route.status_code == 201
    route_id = _json_body(route)["id"]

    listed = await list_route_policies(
        request=_make_request("/api/v1/telephony/sip/route-policies"),
        current_user=user,
        db_pool=pool,
    )
    assert len(listed) == 1
    assert str(listed[0].id) == route_id

    active = await activate_route_policy(
        policy_id=route_id,
        request=_make_request(f"/api/v1/telephony/sip/route-policies/{route_id}/activate"),
        idempotency_key="k-route-active",
        current_user=user,
        db_pool=pool,
    )
    assert active.is_active is True

    inactive = await deactivate_route_policy(
        policy_id=route_id,
        request=_make_request(f"/api/v1/telephony/sip/route-policies/{route_id}/deactivate"),
        idempotency_key="k-route-inactive",
        current_user=user,
        db_pool=pool,
    )
    assert inactive.is_active is False


@pytest.mark.asyncio
async def test_create_route_policy_rejects_invalid_trunk_reference(wsf_context):
    _conn, pool, user = wsf_context
    route_payload = RoutePolicyCreateRequest(
        policy_name="invalid-route",
        route_type="outbound",
        priority=10,
        match_pattern=r"^\\+?[1-9][0-9]{7,14}$",
        target_trunk_id=str(uuid4()),
        codec_policy_id=None,
        strip_digits=0,
        prepend_digits=None,
        is_active=True,
        metadata={},
    )

    response = await create_route_policy(
        payload=route_payload,
        request=_make_request("/api/v1/telephony/sip/route-policies"),
        idempotency_key="k-route-invalid-ref",
        current_user=user,
        db_pool=pool,
    )
    assert response.status_code == 400
    assert _json_body(response)["title"] == "Invalid Route References"


@pytest.mark.asyncio
async def test_quota_status_endpoint_returns_policy_rows(wsf_context):
    _conn, pool, user = wsf_context

    response = await get_telephony_quota_status(
        request=_make_request("/api/v1/telephony/sip/quotas/status"),
        policy_scope="api_mutation",
        metric_key="sip_trunks:create",
        current_user=user,
        db_pool=pool,
    )

    assert response.tenant_id == user.tenant_id
    assert response.policy_scope == "api_mutation"
    assert len(response.metrics) >= 1


def test_ws_f_migration_contains_required_objects():
    sql = MIGRATION_FILE.read_text(encoding="utf-8")
    required_markers = [
        "CREATE TABLE IF NOT EXISTS tenant_sip_trunks",
        "CREATE TABLE IF NOT EXISTS tenant_route_policies",
        "CREATE TABLE IF NOT EXISTS tenant_codec_policies",
        "CREATE TABLE IF NOT EXISTS tenant_telephony_idempotency",
        "CONSTRAINT fk_tenant_route_policies_trunk",
        "UNIQUE (tenant_id, operation, idempotency_key)",
    ]
    for marker in required_markers:
        assert marker in sql, f"Missing migration marker: {marker}"

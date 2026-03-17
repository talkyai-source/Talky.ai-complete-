from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints.telephony_runtime import (
    RuntimeActivateRequest,
    RuntimeRollbackRequest,
    activate_runtime_policy,
    get_runtime_activation_metrics,
    list_runtime_policy_versions,
    preview_runtime_policy,
    rollback_runtime_policy,
)


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _AcquireContext:
    def __init__(self, conn: "FakeRuntimeConn") -> None:
        self._conn = conn

    async def __aenter__(self) -> "FakeRuntimeConn":
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, conn: "FakeRuntimeConn") -> None:
        self._conn = conn

    def acquire(self) -> _AcquireContext:
        return _AcquireContext(self._conn)


@dataclass
class _IdemEntry:
    request_hash: str
    response_body: Optional[Dict[str, Any]] = None
    status_code: Optional[int] = None


class FakeRuntimeConn:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.trunks: Dict[str, Dict[str, Any]] = {}
        self.codecs: Dict[str, Dict[str, Any]] = {}
        self.routes: Dict[str, Dict[str, Any]] = {}
        self.trust_policies: Dict[str, Dict[str, Any]] = {}
        self.threshold_policies: list[Dict[str, Any]] = []
        self.versions: Dict[str, Dict[str, Any]] = {}
        self.events: list[Dict[str, Any]] = []
        self.idempotency: Dict[Tuple[str, str, str], _IdemEntry] = {}
        self.current_tenant_id: Optional[str] = None
        self.current_user_id: Optional[str] = None
        self.current_request_id: Optional[str] = None

        self._seed_defaults()

    def _seed_defaults(self) -> None:
        now = datetime.now(timezone.utc)
        trunk_id = str(uuid4())
        codec_id = str(uuid4())
        route_id = str(uuid4())
        trust_policy_id = str(uuid4())
        self.trunks[trunk_id] = {
            "id": trunk_id,
            "tenant_id": self.tenant_id,
            "trunk_name": "primary-trunk",
            "sip_domain": "sip.example.com",
            "port": 5060,
            "transport": "udp",
            "direction": "both",
            "is_active": True,
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }
        self.codecs[codec_id] = {
            "id": codec_id,
            "tenant_id": self.tenant_id,
            "policy_name": "codec-default",
            "allowed_codecs": ["PCMU", "PCMA"],
            "preferred_codec": "PCMU",
            "sample_rate_hz": 8000,
            "ptime_ms": 20,
            "max_bitrate_kbps": None,
            "jitter_buffer_ms": 60,
            "is_active": True,
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }
        self.routes[route_id] = {
            "id": route_id,
            "tenant_id": self.tenant_id,
            "policy_name": "route-default",
            "route_type": "outbound",
            "priority": 100,
            "match_pattern": r"^\+?[1-9]\d{7,14}$",
            "target_trunk_id": trunk_id,
            "codec_policy_id": codec_id,
            "strip_digits": 0,
            "prepend_digits": None,
            "is_active": True,
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }
        self.trust_policies[trust_policy_id] = {
            "id": trust_policy_id,
            "tenant_id": self.tenant_id,
            "policy_name": "default-trust",
            "allowed_source_cidrs": ["10.0.0.0/24"],
            "blocked_source_cidrs": [],
            "kamailio_group": 1,
            "priority": 100,
            "is_active": True,
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }
        self.threshold_policies.append(
            {
                "id": str(uuid4()),
                "tenant_id": self.tenant_id,
                "policy_name": "runtime-default",
                "policy_scope": "runtime_mutation",
                "metric_key": "*",
                "window_seconds": 60,
                "warn_threshold": 10,
                "throttle_threshold": 15,
                "block_threshold": 20,
                "block_duration_seconds": 300,
                "throttle_retry_seconds": 2,
                "metadata": {"seeded": True},
                "is_active": True,
            }
        )

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def fetch(self, query: str, *args):
        normalized = " ".join(query.split())
        if "FROM tenant_telephony_threshold_policies" in normalized:
            tenant_id, scope = args[0], args[1]
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
        if "FROM tenant_sip_trunks" in normalized:
            tenant_id = args[0]
            rows = [
                row for row in self.trunks.values()
                if row["tenant_id"] == tenant_id and row["is_active"] is True
            ]
            rows.sort(key=lambda row: (row["trunk_name"].lower(), row["id"]))
            return rows
        if "FROM tenant_codec_policies" in normalized:
            tenant_id = args[0]
            rows = [
                row for row in self.codecs.values()
                if row["tenant_id"] == tenant_id and row["is_active"] is True
            ]
            rows.sort(key=lambda row: (row["policy_name"].lower(), row["id"]))
            return rows
        if "FROM tenant_route_policies" in normalized:
            tenant_id = args[0]
            rows = [
                row for row in self.routes.values()
                if row["tenant_id"] == tenant_id and row["is_active"] is True
            ]
            rows.sort(key=lambda row: (row["route_type"], row["priority"], row["policy_name"].lower(), row["id"]))
            return rows
        if "FROM tenant_sip_trust_policies" in normalized:
            tenant_id = args[0]
            rows = [
                row for row in self.trust_policies.values()
                if row["tenant_id"] == tenant_id and row["is_active"] is True
            ]
            rows.sort(key=lambda row: (row["priority"], row["kamailio_group"], row["policy_name"].lower(), row["id"]))
            return rows
        if "FROM tenant_runtime_policy_versions" in normalized and "ORDER BY policy_version DESC" in normalized:
            tenant_id = args[0]
            rows = [row for row in self.versions.values() if row["tenant_id"] == tenant_id]
            rows.sort(key=lambda row: row["policy_version"], reverse=True)
            return rows
        raise AssertionError(f"Unexpected fetch query: {normalized}")

    async def fetchval(self, query: str, *args):
        normalized = " ".join(query.split())
        if "SELECT COALESCE(MAX(policy_version), 0) + 1 FROM tenant_runtime_policy_versions" in normalized:
            tenant_id = args[0]
            current = [row["policy_version"] for row in self.versions.values() if row["tenant_id"] == tenant_id]
            return (max(current) if current else 0) + 1
        raise AssertionError(f"Unexpected fetchval query: {normalized}")

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
            tenant_id, operation, key, request_hash, _window = args
            idem_key = (tenant_id, operation, key)
            if idem_key in self.idempotency:
                return None
            self.idempotency[idem_key] = _IdemEntry(request_hash=request_hash)
            return {"id": str(uuid4())}

        if normalized.startswith("SELECT request_hash, response_body, status_code FROM tenant_telephony_idempotency"):
            tenant_id, operation, key = args
            entry = self.idempotency.get((tenant_id, operation, key))
            if not entry:
                return None
            return {
                "request_hash": entry.request_hash,
                "response_body": entry.response_body,
                "status_code": entry.status_code,
            }

        if normalized.startswith("INSERT INTO tenant_runtime_policy_versions"):
            (
                tenant_id,
                policy_version,
                source_hash,
                schema_version,
                input_snapshot_json,
                compiled_artifact_json,
                validation_json,
                created_by,
            ) = args
            now = datetime.now(timezone.utc)
            version_id = str(uuid4())
            row = {
                "id": version_id,
                "tenant_id": tenant_id,
                "policy_version": int(policy_version),
                "source_hash": source_hash,
                "schema_version": schema_version,
                "input_snapshot": json.loads(input_snapshot_json),
                "compiled_artifact": json.loads(compiled_artifact_json),
                "validation_report": json.loads(validation_json),
                "build_status": "compiled",
                "is_active": False,
                "is_last_good": False,
                "created_by": created_by,
                "activated_by": None,
                "created_at": now,
                "updated_at": now,
                "activated_at": None,
            }
            self.versions[version_id] = row
            return {
                "id": UUID(version_id),
                "policy_version": row["policy_version"],
                "source_hash": row["source_hash"],
            }

        if "FROM tenant_runtime_policy_versions" in normalized and "is_active = TRUE" in normalized:
            tenant_id = args[0]
            active_rows = [
                row for row in self.versions.values() if row["tenant_id"] == tenant_id and row["is_active"] is True
            ]
            if not active_rows:
                return None
            active_rows.sort(key=lambda row: row["policy_version"], reverse=True)
            row = active_rows[0]
            return {
                "id": UUID(row["id"]),
                "policy_version": row["policy_version"],
                "compiled_artifact": row["compiled_artifact"],
            }

        if "WHERE tenant_id = $1 AND policy_version = $2" in normalized:
            tenant_id, policy_version = args
            for row in self.versions.values():
                if row["tenant_id"] == tenant_id and row["policy_version"] == int(policy_version):
                    return {
                        "id": UUID(row["id"]),
                        "policy_version": row["policy_version"],
                        "compiled_artifact": row["compiled_artifact"],
                    }
            return None

        if "policy_version < $2" in normalized and "build_status IN ('active', 'superseded', 'rolled_back')" in normalized:
            tenant_id, active_version = args
            candidates = [
                row
                for row in self.versions.values()
                if row["tenant_id"] == tenant_id
                and row["policy_version"] < int(active_version)
                and row["build_status"] in {"active", "superseded", "rolled_back"}
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda row: row["policy_version"], reverse=True)
            row = candidates[0]
            return {
                "id": UUID(row["id"]),
                "policy_version": row["policy_version"],
                "compiled_artifact": row["compiled_artifact"],
            }

        if normalized.startswith("WITH scoped_events AS"):
            tenant_id, _window_hours = args
            scoped_events = [event for event in self.events if event["tenant_id"] == tenant_id]

            activation_success_count = sum(
                1
                for event in scoped_events
                if event["action"] == "activate"
                and event["stage"] == "commit"
                and event["status"] == "succeeded"
            )
            activation_failure_count = sum(
                1
                for event in scoped_events
                if event["action"] == "activate" and event["status"] == "failed"
            )
            rollback_success_count = sum(
                1
                for event in scoped_events
                if event["action"] == "rollback"
                and event["stage"] == "rollback"
                and event["status"] == "succeeded"
            )
            rollback_failure_count = sum(
                1
                for event in scoped_events
                if event["action"] == "rollback" and event["status"] == "failed"
            )

            rollback_started = {
                event.get("request_id"): event["created_at"]
                for event in scoped_events
                if event["action"] == "rollback"
                and event["stage"] == "rollback"
                and event["status"] == "started"
                and event.get("request_id")
            }
            rollback_latencies_ms = []
            for event in scoped_events:
                if (
                    event["action"] == "rollback"
                    and event["stage"] == "rollback"
                    and event["status"] in {"succeeded", "failed"}
                    and event.get("request_id") in rollback_started
                ):
                    start = rollback_started[event["request_id"]]
                    end = event["created_at"]
                    rollback_latencies_ms.append((end - start).total_seconds() * 1000.0)
            rollback_latencies_ms.sort()

            def _percentile(values: list[float], percentile: float) -> float:
                if not values:
                    return 0.0
                if len(values) == 1:
                    return values[0]
                index = (len(values) - 1) * percentile
                low = int(index)
                high = min(low + 1, len(values) - 1)
                fraction = index - low
                return values[low] + (values[high] - values[low]) * fraction

            return {
                "activation_success_count": activation_success_count,
                "activation_failure_count": activation_failure_count,
                "rollback_success_count": rollback_success_count,
                "rollback_failure_count": rollback_failure_count,
                "rollback_p50_ms": _percentile(rollback_latencies_ms, 0.50),
                "rollback_p95_ms": _percentile(rollback_latencies_ms, 0.95),
                "rollback_max_ms": rollback_latencies_ms[-1] if rollback_latencies_ms else 0.0,
            }

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
            tenant_id, operation, key, body_json, status_code, _resource_type, _resource_id = args
            entry = self.idempotency[(tenant_id, operation, key)]
            entry.response_body = json.loads(body_json)
            entry.status_code = int(status_code)
            return "UPDATE 1"

        if normalized.startswith("INSERT INTO tenant_runtime_policy_events"):
            (
                tenant_id,
                policy_version_id,
                action,
                stage,
                status,
                details_json,
                request_id,
                created_by,
            ) = args
            created_at = datetime.now(timezone.utc)
            self.events.append(
                {
                    "tenant_id": tenant_id,
                    "policy_version_id": str(policy_version_id),
                    "action": action,
                    "stage": stage,
                    "status": status,
                    "details": json.loads(details_json),
                    "request_id": request_id,
                    "created_by": created_by,
                    "created_at": created_at,
                }
            )
            return "INSERT 1"

        if normalized.startswith("UPDATE tenant_runtime_policy_versions SET build_status = 'failed'"):
            tenant_id, version_id, validation_json = args
            row = self.versions[str(version_id)]
            if row["tenant_id"] == tenant_id:
                row["build_status"] = "failed"
                row["validation_report"] = json.loads(validation_json)
                row["updated_at"] = datetime.now(timezone.utc)
            return "UPDATE 1"

        if normalized.startswith("UPDATE tenant_runtime_policy_versions SET is_active = FALSE") and "id <> $2" in normalized:
            tenant_id, current_version_id = args
            for row in self.versions.values():
                if row["tenant_id"] != tenant_id:
                    continue
                if row["id"] != str(current_version_id) and row["is_active"]:
                    row["is_active"] = False
                    row["is_last_good"] = False
                    row["build_status"] = "superseded"
                    row["updated_at"] = datetime.now(timezone.utc)
            return "UPDATE 1"

        if normalized.startswith("UPDATE tenant_runtime_policy_versions SET build_status = 'active'") or normalized.startswith(
            "UPDATE tenant_runtime_policy_versions SET is_active = TRUE"
        ):
            tenant_id, version_id, activated_by = args
            row = self.versions[str(version_id)]
            if row["tenant_id"] == tenant_id:
                row["build_status"] = "active"
                row["is_active"] = True
                row["is_last_good"] = True
                row["activated_by"] = activated_by
                row["activated_at"] = datetime.now(timezone.utc)
                row["updated_at"] = datetime.now(timezone.utc)
            return "UPDATE 1"

        if normalized.startswith("UPDATE tenant_runtime_policy_versions SET is_active = FALSE") and "AND is_active = TRUE" in normalized and "id <> $2" not in normalized:
            tenant_id, active_version_id = args
            for row in self.versions.values():
                if row["tenant_id"] == tenant_id and row["is_active"]:
                    row["is_active"] = False
                    row["is_last_good"] = False
                    if row["id"] == str(active_version_id):
                        row["build_status"] = "rolled_back"
                    row["updated_at"] = datetime.now(timezone.utc)
            return "UPDATE 1"

        raise AssertionError(f"Unexpected execute query: {normalized}")


class FakeRuntimeAdapter:
    def __init__(self) -> None:
        self.apply_calls = 0
        self.verify_calls = 0

    async def apply(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        self.apply_calls += 1
        return {"ok": True, "artifact_schema": artifact["schema_version"]}

    async def verify(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        self.verify_calls += 1
        return {"ok": True, "artifact_schema": artifact["schema_version"]}


@pytest.fixture
def ws_g_context():
    tenant_id = str(uuid4())
    user = CurrentUser(
        id=str(uuid4()),
        email="runtime@example.com",
        tenant_id=tenant_id,
        role="admin",
    )
    conn = FakeRuntimeConn(tenant_id=tenant_id)
    pool = FakePool(conn)
    adapter = FakeRuntimeAdapter()
    return conn, pool, user, adapter


def _request(path: str) -> Request:
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


@pytest.mark.asyncio
async def test_preview_compile_returns_artifact(ws_g_context):
    conn, pool, user, _adapter = ws_g_context
    response = await preview_runtime_policy(
        request=_request("/api/v1/telephony/sip/runtime/compile/preview"),
        current_user=user,
        db_pool=pool,
    )
    assert response.schema_version == "ws-g.v1"
    assert response.active_routes == 1
    assert response.active_trunks == 1
    assert response.active_trust_policies == 1
    assert conn.current_tenant_id == user.tenant_id
    assert conn.current_user_id == user.id
    assert conn.current_request_id == ""


@pytest.mark.asyncio
async def test_activate_runtime_policy_success(ws_g_context):
    conn, pool, user, adapter = ws_g_context
    result = await activate_runtime_policy(
        payload=RuntimeActivateRequest(note="first activation"),
        request=_request("/api/v1/telephony/sip/runtime/activate"),
        idempotency_key="runtime-1",
        x_request_id="req-1",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )
    assert result["build_status"] == "active"
    assert result["policy_version"] == 1
    assert adapter.apply_calls == 1
    assert adapter.verify_calls == 1

    versions = [row for row in conn.versions.values() if row["is_active"]]
    assert len(versions) == 1
    assert versions[0]["policy_version"] == 1
    stages = {(event["action"], event["stage"], event["status"]) for event in conn.events}
    assert ("activate", "precheck", "succeeded") in stages
    assert ("activate", "apply", "succeeded") in stages
    assert ("activate", "verify", "succeeded") in stages
    assert ("activate", "commit", "succeeded") in stages


@pytest.mark.asyncio
async def test_activate_runtime_policy_rejects_invalid_policy(ws_g_context):
    conn, pool, user, adapter = ws_g_context
    only_trunk_id = next(iter(conn.trunks))
    conn.trunks[only_trunk_id]["is_active"] = False
    response = await activate_runtime_policy(
        payload=RuntimeActivateRequest(note="invalid"),
        request=_request("/api/v1/telephony/sip/runtime/activate"),
        idempotency_key="runtime-invalid",
        x_request_id="req-invalid",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )
    assert response.status_code == 422
    assert response.media_type == "application/problem+json"
    body = _json_body(response)
    assert body["title"] == "Runtime Policy Validation Failed"
    assert len(conn.versions) == 0
    assert adapter.apply_calls == 0
    assert adapter.verify_calls == 0


@pytest.mark.asyncio
async def test_activate_runtime_policy_idempotent_replay(ws_g_context):
    _conn, pool, user, adapter = ws_g_context
    first = await activate_runtime_policy(
        payload=RuntimeActivateRequest(note="idem"),
        request=_request("/api/v1/telephony/sip/runtime/activate"),
        idempotency_key="runtime-replay",
        x_request_id="req-replay-1",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )
    second = await activate_runtime_policy(
        payload=RuntimeActivateRequest(note="idem"),
        request=_request("/api/v1/telephony/sip/runtime/activate"),
        idempotency_key="runtime-replay",
        x_request_id="req-replay-2",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )
    assert first["policy_version"] == 1
    assert second.status_code == 200
    assert _json_body(second)["policy_version"] == 1
    # apply/verify are called only for first request
    assert adapter.apply_calls == 1
    assert adapter.verify_calls == 1


@pytest.mark.asyncio
async def test_rollback_runtime_policy_switches_active_version(ws_g_context):
    conn, pool, user, adapter = ws_g_context
    first = await activate_runtime_policy(
        payload=RuntimeActivateRequest(note="v1"),
        request=_request("/api/v1/telephony/sip/runtime/activate"),
        idempotency_key="runtime-v1",
        x_request_id="req-v1",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )
    assert first["policy_version"] == 1

    # Mutate route priority so source hash/version changes on second activation.
    route_id = next(iter(conn.routes))
    conn.routes[route_id]["priority"] = 50
    second = await activate_runtime_policy(
        payload=RuntimeActivateRequest(note="v2"),
        request=_request("/api/v1/telephony/sip/runtime/activate"),
        idempotency_key="runtime-v2",
        x_request_id="req-v2",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )
    assert second["policy_version"] == 2

    rollback = await rollback_runtime_policy(
        payload=RuntimeRollbackRequest(target_version=1, reason="operator rollback"),
        request=_request("/api/v1/telephony/sip/runtime/rollback"),
        idempotency_key="runtime-rb-1",
        x_request_id="req-rb-1",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )
    assert rollback["status"] == "rolled_back"
    assert rollback["from_version"] == 2
    assert rollback["to_version"] == 1

    active = [row for row in conn.versions.values() if row["is_active"]]
    assert len(active) == 1
    assert active[0]["policy_version"] == 1

    listed = await list_runtime_policy_versions(
        request=_request("/api/v1/telephony/sip/runtime/versions"),
        current_user=user,
        db_pool=pool,
    )
    assert listed[0].policy_version == 2
    assert listed[1].policy_version == 1


@pytest.mark.asyncio
async def test_cross_tenant_runtime_versions_are_isolated(ws_g_context):
    _conn, pool, user, adapter = ws_g_context
    await activate_runtime_policy(
        payload=RuntimeActivateRequest(note="tenant-a"),
        request=_request("/api/v1/telephony/sip/runtime/activate"),
        idempotency_key="runtime-tenant-a",
        x_request_id="req-tenant-a",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )

    other_user = CurrentUser(
        id=str(uuid4()),
        email="tenant-b@example.com",
        tenant_id=str(uuid4()),
        role="admin",
    )
    versions = await list_runtime_policy_versions(
        request=_request("/api/v1/telephony/sip/runtime/versions"),
        current_user=other_user,
        db_pool=pool,
    )
    assert versions == []


@pytest.mark.asyncio
async def test_runtime_activation_metrics_include_success_and_rollback_latency(ws_g_context):
    conn, pool, user, adapter = ws_g_context

    first = await activate_runtime_policy(
        payload=RuntimeActivateRequest(note="metrics-v1"),
        request=_request("/api/v1/telephony/sip/runtime/activate"),
        idempotency_key="metrics-v1",
        x_request_id="metrics-act-1",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )
    assert first["policy_version"] == 1

    route_id = next(iter(conn.routes))
    conn.routes[route_id]["priority"] = 42
    second = await activate_runtime_policy(
        payload=RuntimeActivateRequest(note="metrics-v2"),
        request=_request("/api/v1/telephony/sip/runtime/activate"),
        idempotency_key="metrics-v2",
        x_request_id="metrics-act-2",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )
    assert second["policy_version"] == 2

    rollback = await rollback_runtime_policy(
        payload=RuntimeRollbackRequest(target_version=1, reason="metrics rollback"),
        request=_request("/api/v1/telephony/sip/runtime/rollback"),
        idempotency_key="metrics-rb-1",
        x_request_id="metrics-rb-1",
        current_user=user,
        db_pool=pool,
        adapter=adapter,
    )
    assert rollback["status"] == "rolled_back"

    metrics = await get_runtime_activation_metrics(
        request=_request("/api/v1/telephony/sip/runtime/metrics/activation"),
        window_hours=24,
        current_user=user,
        db_pool=pool,
    )
    assert metrics.activation_success_count == 2
    assert metrics.activation_failure_count == 0
    assert metrics.activation_success_rate_pct == 100.0
    assert metrics.rollback_success_count == 1
    assert metrics.rollback_failure_count == 0
    assert metrics.rollback_latency_max_ms >= metrics.rollback_latency_p95_ms
    assert metrics.rollback_latency_p95_ms >= metrics.rollback_latency_p50_ms

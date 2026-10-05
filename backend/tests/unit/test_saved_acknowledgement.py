"""Actual service/executor/route handoff; all storage and provider IO synthetic."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, Request

from app.api.v1 import dependencies
from app.api.v1.endpoints.admin import actions
from app.domain.services.voice_pipeline import action_execution as voice
from app.domain.services.voice_pipeline.contact_capture import CaptureStatus, ContactCaptureState
from app.infrastructure.assistant.proposals import pop_proposal, store_proposal
from app.infrastructure.assistant.tools import comms, dispatch
from app.services import email_service
from app.services.action_execution import DurableActionExecutor
from app.services.connector_resolver import reviewed_authorization_identity
from app.services.saved_acknowledgement import source_digest
from tests.unit.test_voice_email_receipt_proof import Rows as VoiceRows


class Query:
    def __init__(self, db):
        self.db, self.filters, self.mode, self.one = db, [], "select", False

    def select(self, *_):
        return self

    def insert(self, value):
        self.mode, self.value = "insert", value
        return self

    def update(self, value):
        self.mode, self.value = "update", value
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def single(self):
        self.one = True
        return self

    def execute(self):
        if self.mode == "insert":
            self.db.inner.append(deepcopy(self.value))
            return SimpleNamespace(data=[deepcopy(self.value)], error=None)
        found = [row for row in self.db.inner + ([self.db.row] if self.db.row else [])
                 if all(str(row.get(key)) == str(value) for key, value in self.filters)]
        if self.mode == "update":
            if self.db.fail_inner_save and self.value.get("status") == "completed":
                raise RuntimeError("Synthetic inner save failure")
            for row in found:
                row.update(deepcopy(self.value))
        value = deepcopy(found[0] if found else None) if self.one else deepcopy(found)
        return SimpleNamespace(data=value, error=None)


class Rows(VoiceRows):
    def __init__(self):
        super().__init__()
        self.pool = self
        self.inner, self.events = [], []
        self.actor, self.session = str(uuid4()), str(uuid4())
        self.owned = self.active = self.verified = self.session_active = True
        self.role = self.dependency_role = "platform_admin"
        self.fail_audit = self.fail_update = self.fail_inner_save = False
        self.current_user = self.current_request = None
        self.sql = []
        self.after_lock = None
        self.now = datetime(2026, 10, 6, 12, 30, 1, 123456, tzinfo=timezone.utc)

    def table(self, name):
        assert name == "assistant_actions", "Recovery must not load current connector credentials"
        return Query(self)

    @asynccontextmanager
    async def transaction(self):
        before = deepcopy((self.row, self.events, self.inner))
        try:
            yield
        except BaseException:
            self.row, self.events, self.inner = before
            raise
        else:
            self.commits += 1
        finally:
            self.current_user = self.current_request = None

    async def fetchrow(self, sql, *args):
        sql = " ".join(sql.split())
        self.sql.append(sql)
        if "FROM security_sessions" in sql:
            assert args[0] == self.session and args[2] == self.actor
            return {"id": self.session} if self.session_active else None
        if "FROM user_profiles up" in sql:
            assert args == (self.actor,)
            return dict(id=self.actor, tenant_id=self.tenant, profile_role=self.role,
                        is_active=self.active, is_verified=self.verified,
                        membership_status="active", membership_role=self.role)
        if sql.startswith("SELECT * FROM assistant_action_resolutions"):
            if "actor_id=$1" in sql:
                return next((deepcopy(e) for e in self.events if (e["actor_id"], e["request_id"]) == args), None)
            return next((deepcopy(e) for e in self.events if (e["id"], e["action_id"], e["tenant_id"]) == args), None)
        if sql.startswith("INSERT INTO assistant_action_resolutions"):
            assert self.current_user == self.actor and self.current_request == args[4]
            if self.fail_audit:
                raise RuntimeError("Synthetic audit insert failed")
            keys = ("id", "tenant_id", "action_id", "actor_id", "request_id", "source_digest", "reason")
            event = dict(zip(keys, args[:7]))
            event.update(actor_role="platform_admin", original_status="unknown", recovered_status="completed",
                         provider_status=args[9], original_output=json.loads(args[7]),
                         original_timestamps=json.loads(args[8]), recorded_at=self.now)
            self.events.append(event)
            return deepcopy(event)
        if sql.startswith("SELECT * FROM assistant_actions WHERE id="):
            if self.after_lock:
                self.after_lock()
            return deepcopy(self.row) if self.row and self.row["id"] == args[0] else None
        result = await super().fetchrow(sql, *args)
        if sql.startswith("INSERT INTO assistant_actions") and result:
            self.row.update(call_id=args[4], lead_id=args[5], campaign_id=args[6], user_id=args[7],
                            triggered_by=args[8], conversation_id=args[9], created_at=self.now,
                            started_at=None, completed_at=None, scheduled_at=None,
                            tenants={"business_name": "Synthetic"})
            return deepcopy(self.row)
        return result

    async def fetchval(self, sql, *args):
        sql = " ".join(sql.split())
        self.sql.append(sql)
        if sql.startswith("SELECT EXISTS(SELECT 1 FROM calls"):
            assert args == (self.call, self.tenant, self.campaign, self.lead)
            return self.owned
        if sql.startswith("UPDATE assistant_actions SET status='completed'"):
            assert args[:2] == (self.row["id"], self.tenant)
            if self.fail_update:
                raise RuntimeError("Synthetic status update failed")
            self.row.update(status="completed", output_data=json.loads(args[2]))
            return self.row["id"]
        result = await super().fetchval(sql, *args)
        if sql.startswith("UPDATE assistant_actions SET status='running'"):
            self.row["started_at"] = self.now
        return result

    async def execute(self, sql, *args):
        sql = " ".join(sql.split())
        self.sql.append(sql)
        if "set_config('app.current_user_id'" in sql:
            self.current_user = args[0]
            return "SELECT 1"
        if "set_config('app.current_request_id'" in sql:
            self.current_request = args[0]
            return "SELECT 1"
        if sql.startswith("SET LOCAL app.bypass_rls"):
            return "SET"
        result = await super().execute(sql, *args)
        if sql.startswith("UPDATE assistant_actions SET status=$3"):
            self.row["completed_at"] = self.now
        return result


@pytest.fixture
async def harness(monkeypatch):
    def no_network(*_args, **_kwargs):
        raise AssertionError("Recovery test attempted network IO")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    db = Rows()
    connector = SimpleNamespace(tenant_id=db.tenant, account_row_id=str(uuid4()), external_account_id=None,
                               send_email=AsyncMock(return_value=SimpleNamespace(id="saved-message", thread_id="saved-thread")))
    cid = str(uuid4())
    proof = reviewed_authorization_identity(connector, cid, "gmail")
    monkeypatch.setattr(email_service, "get_encryption_service", lambda: None)
    # Only the credential/current-account boundary is synthetic; actual inner
    # intent/bind/send/save and outer claim/save-failure behavior execute.
    service = email_service.EmailService(db)
    monkeypatch.setattr(service, "_get_active_email_connector", AsyncMock(return_value=(connector, cid, "gmail")))
    monkeypatch.setattr(email_service, "check_reviewed_authorization_current", lambda *_: None)
    monkeypatch.setattr(email_service, "EmailService", lambda _pool: service)
    monkeypatch.setattr(email_service, "get_email_service", lambda _pool: service)
    monkeypatch.setattr(dispatch, "_authorize_action_tool", AsyncMock(return_value=None))
    yield db, service, connector, proof
    monkeypatch.undo()


async def create_unknown(harness, source="assistant"):
    db, service, connector, proof = harness
    if source == "voice":
        capture = ContactCaptureState(kind="email", status=CaptureStatus.CONFIRMED,
            normalized_value="saved@example.invalid", confirmed_at=db.now, from_caller=True)
        session = SimpleNamespace(tenant_id=db.tenant, campaign_id=db.campaign, call_id=db.call, turn_id=1,
            captured_slots=SimpleNamespace(email_capture=capture), _voice_action_pool=db)
        proposal = await voice.execute_connected_voice_action(session, "send_email", {}, "Please email me.")
        assert proposal["status"] == "needs_confirmation"
        session.turn_id = 2
        session._voice_action_delivered_text = proposal["confirmation_summary"]
        db.fail_next_save = True
        result = await voice.execute_connected_voice_action(session, "send_email", {}, "yes")
        return result
    preview = await comms.send_email(db.tenant, db, to=["saved@example.invalid"], subject="Résumé café", body="Saved café body")
    proposal = store_proposal(tool="send_email", args={}, result=preview, tenant_id=db.tenant, actor_user_id=db.actor)
    assert pop_proposal(proposal["proposal_id"], db.tenant, db.actor) == proposal
    assert connector.send_email.await_count == 0
    db.fail_next_save = True
    return await dispatch.dispatch_tool("send_email", db.tenant, db, None, {**proposal["args"], "confirm": True},
        actor_user_id=db.actor, trusted_proposal_apply=True, proposal_id=proposal["proposal_id"])


@asynccontextmanager
async def client(db):
    app = FastAPI()
    app.include_router(actions.router, prefix="/admin")
    async def current(request: Request):
        request.state.authenticated_user_id = db.actor
        request.state.authenticated_session_id = db.session
        return dependencies.CurrentUser(id=db.actor, email="admin@example.invalid", tenant_id=db.tenant, role=db.dependency_role)
    app.dependency_overrides[dependencies.get_current_user] = current
    app.dependency_overrides[dependencies.get_db_client] = lambda: db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as http:
        yield http


def review(db):
    return {"request_id": str(uuid4()), "expected_source_digest": source_digest(db.row), "reason": "Reviewed original saved Gmail acknowledgement"}


@pytest.mark.parametrize("source", ["assistant", "voice"])
async def test_actual_email_service_executor_unknown_recovery_and_no_effect_replay(harness, source):
    db, _, connector, _ = harness
    result = await create_unknown(harness, source)
    assert result["status"] == db.row["status"] == "unknown"
    assert db.inner[0]["status"] == "completed" and connector.send_email.await_count == 1
    before, body = deepcopy(db.row), review(db)
    async with client(db) as http:
        detail = (await http.get(f"/admin/actions/{db.row['id']}")).json()
        assert detail["acknowledgement_recovery"]["source_digest"] == body["expected_source_digest"]
        first = await http.post(f"/admin/actions/{db.row['id']}/recover-acknowledgement", json=body)
        assert first.status_code == 200, first.text
        second = await http.post(f"/admin/actions/{db.row['id']}/recover-acknowledgement", json=body)
        assert second.status_code == 200 and second.json() == first.json()
        detail = (await http.get(f"/admin/actions/{db.row['id']}")).json()
        assert detail["status"] == "completed" and detail["acknowledgement_recovery"] is None
        assert detail["acknowledgement_recovery_record"]["id"] == first.json()["id"]
    assert db.events[0]["original_output"] == before["output_data"]
    assert {k: v for k, v in db.row.items() if k not in {"status", "output_data"}} == {
        k: v for k, v in before.items() if k not in {"status", "output_data"}}
    never = AsyncMock(side_effect=AssertionError("Recovery replay cannot resend"))
    replay = await DurableActionExecutor(db).execute(tenant_id=db.tenant, action="send_email",
        payload=db.row["input_data"]["parameters"], idempotency_key=db.row["idempotency_key"], executor=never)
    assert replay["success"] is True and replay["replayed"] is True
    never.assert_not_awaited()
    assert connector.send_email.await_count == 1 and len(db.events) == 1
    assert "saved@example.invalid" not in json.dumps(first.json()) and "saved-message" not in json.dumps(first.json())


@pytest.mark.parametrize("failed", ["fail_audit", "fail_update"])
async def test_route_rolls_back_event_and_status_together_on_storage_failure(harness, failed):
    db, _, connector, _ = harness
    await create_unknown(harness)
    before, body = deepcopy(db.row), review(db)
    setattr(db, failed, True)
    async with client(db) as http:
        response = await http.post(f"/admin/actions/{db.row['id']}/recover-acknowledgement", json=body)
    assert response.status_code == 503
    assert db.row == before and db.events == [] and connector.send_email.await_count == 1


@pytest.mark.parametrize("field,value", [("active", False), ("verified", False), ("session_active", False), ("role", "tenant_admin"), ("role", "partner_admin")])
async def test_mutation_rechecks_current_principal_and_session(harness, field, value):
    db, *_ = harness
    await create_unknown(harness)
    body = review(db)
    setattr(db, field, value)
    async with client(db) as http:
        response = await http.post(f"/admin/actions/{db.row['id']}/recover-acknowledgement", json=body)
    assert response.status_code in {401, 403} and db.events == [] and db.row["status"] == "unknown"


@pytest.mark.parametrize("change", ["session_active", "role"])
async def test_revocation_while_waiting_for_action_lock_denies_mutation(harness, change):
    db, *_ = harness
    await create_unknown(harness)
    db.after_lock = lambda: setattr(db, change, False if change == "session_active" else "tenant_admin")
    async with client(db) as http:
        response = await http.post(f"/admin/actions/{db.row['id']}/recover-acknowledgement", json=review(db))
    assert response.status_code in {401, 403} and not db.events and db.row["status"] == "unknown"


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin"])
async def test_non_platform_detail_has_no_recovery_capability_or_record(harness, role):
    db, *_ = harness
    await create_unknown(harness)
    db.dependency_role = role
    async with client(db) as http:
        detail = (await http.get(f"/admin/actions/{db.row['id']}")).json()
        assert detail["acknowledgement_recovery"] is None and detail["acknowledgement_recovery_record"] is None
        response = await http.post(f"/admin/actions/{db.row['id']}/recover-acknowledgement", json=review(db))
    assert response.status_code == 403 and not db.events


@pytest.mark.parametrize("marker", ["bad", [], {"id": "bad"}, 7, True, "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"])
async def test_malformed_historical_recovery_marker_does_not_break_detail(harness, marker):
    db, *_ = harness
    await create_unknown(harness)
    db.row["output_data"]["receipt_recovery_id"] = marker
    async with client(db) as http:
        response = await http.get(f"/admin/actions/{db.row['id']}")
    assert response.status_code == 200
    assert response.json()["acknowledgement_recovery_record"] is None
    assert response.json()["acknowledgement_recovery"] is None


async def test_voice_saved_relationship_is_required_for_detail_and_mutation(harness):
    db, *_ = harness
    await create_unknown(harness, "voice")
    db.owned = False
    async with client(db) as http:
        detail = (await http.get(f"/admin/actions/{db.row['id']}")).json()
        assert detail["acknowledgement_recovery"] is None
        response = await http.post(f"/admin/actions/{db.row['id']}/recover-acknowledgement", json=review(db))
    assert response.status_code == 409 and not db.events


@pytest.mark.parametrize("change", ["digest", "reason", "action"])
async def test_replay_requires_same_actor_request_and_body(harness, change):
    db, *_ = harness
    await create_unknown(harness)
    body, aid = review(db), db.row["id"]
    async with client(db) as http:
        assert (await http.post(f"/admin/actions/{aid}/recover-acknowledgement", json=body)).status_code == 200
        if change == "digest":
            body["expected_source_digest"] = "0" * 64
        elif change == "reason":
            body["reason"] = "Different review"
        else:
            aid = str(uuid4())
        assert (await http.post(f"/admin/actions/{aid}/recover-acknowledgement", json=body)).status_code == 409
    assert len(db.events) == 1


async def test_stale_source_is_denied_without_credential_lookup(harness):
    db, service, connector, _ = harness
    await create_unknown(harness)
    body = review(db)
    db.row["error"] = "Changed after detail"
    service._get_active_email_connector.side_effect = AssertionError("Historical recovery must not resolve credentials")
    async with client(db) as http:
        assert (await http.post(f"/admin/actions/{db.row['id']}/recover-acknowledgement", json=body)).status_code == 409
        assert (await http.post(f"/admin/actions/{db.row['id']}/recover-acknowledgement", json=review(db))).status_code == 200
    assert connector.send_email.await_count == 1


@pytest.mark.parametrize("source", ["assistant", "voice"])
async def test_inner_save_failure_never_qualifies_even_with_message_id(harness, source):
    db, *_ = harness
    db.fail_inner_save = True
    await create_unknown(harness, source)
    assert actions._gmail_recovery_candidate(db.row) is None


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(status="running"),
    lambda r: r.update(type="submit_form"),
    lambda r: r["output_data"].update(version=True),
    lambda r: r["output_data"].update(version=1.0),
    lambda r: r["output_data"].update(action_id=str(uuid4())),
    lambda r: r["output_data"].update(success=True),
    lambda r: r["output_data"].update(confirmation_allowed=True),
    lambda r: r["output_data"].update(provider="gmail"),
    lambda r: r["output_data"]["provider_result"].update(success=1),
    lambda r: r["output_data"]["provider_result"].update(confirmation_allowed=1),
    lambda r: r["output_data"]["provider_result"].pop("account_row_id"),
    lambda r: r["output_data"]["provider_result"].pop("child_action_id"),
    lambda r: r["output_data"]["provider_result"].update(child_action_id=r["id"]),
    lambda r: r["output_data"]["provider_result"].update(message_id=None),
    lambda r: r["output_data"]["provider_result"].update(recipient_count=True),
    lambda r: r["output_data"]["provider_result"].update(recipients=["other@example.invalid"]),
    lambda r: r["output_data"]["provider_result"].update(tenant_id=str(uuid4())),
    lambda r: r["input_data"].update(request_hash="0" * 64),
    lambda r: r["input_data"]["parameters"].update(subject="different"),
    lambda r: r.update(idempotency_key="assistant:wrong:prop_0123456789abcdef"),
    lambda r: r.update(user_id=None),
])
async def test_invalid_receipt_or_intent_stays_unknown(harness, mutation):
    db, *_ = harness
    await create_unknown(harness)
    mutation(db.row)
    assert actions._gmail_recovery_candidate(db.row) is None


async def test_digest_native_uuid_aware_time_and_adapter_strings_have_parity(harness):
    db, *_ = harness
    await create_unknown(harness)
    original = source_digest(db.row)
    strings = deepcopy(db.row)
    native = deepcopy(db.row)
    for key in ("id", "tenant_id", "user_id"):
        native[key] = UUID(native[key])
    for key in ("created_at", "started_at", "completed_at"):
        strings[key] = strings[key].astimezone(timezone(timedelta(hours=5))).isoformat()
    strings["input_data"] = json.dumps(strings["input_data"])
    strings["output_data"] = json.dumps(strings["output_data"])
    assert source_digest(strings) == source_digest(native) == original
    for bad in ("not a date", db.now.replace(tzinfo=None), None):
        native["created_at"] = bad
        with pytest.raises(ValueError):
            source_digest(native)

"""Voice receipts retain safe inner evidence through the actual durable executor."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.services.voice_pipeline import action_execution as voice
from app.domain.services.voice_pipeline.contact_capture import CaptureStatus, ContactCaptureState
from app.services import email_service
from app.services.action_execution import public_action_receipt


class Rows:
    """Synthetic SQL port; real policy/claim/save/projection methods execute."""
    def __init__(self):
        self.tenant, self.call, self.campaign, self.lead = [str(uuid4()) for _ in range(4)]
        self.row = None
        self.fail_next_save = False
        self.commits = 0
        self.context = {
            "call_id": self.call, "campaign_id": self.campaign, "lead_id": self.lead,
            "call_status": "in_progress", "campaign_status": "active", "direction": "outbound",
            "script_config": {"campaign_brief": {
                "approved_next_actions": ["send_email", "submit_form"],
                "email_action": {"subject": "Requested details", "body": "PRIVATE_APPROVED_BODY"},
                "form_action": {"name": "Contact request", "recipient": "private-inbox@example.invalid",
                                "subject": "Contact request", "fields": ["email"]},
            }},
        }

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        before = deepcopy(self.row)
        try:
            yield
        except BaseException:
            self.row = before
            raise
        else:
            self.commits += 1

    async def fetchrow(self, sql, *args):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT c.id AS call_id"):
            assert args == (self.tenant, self.campaign, self.call)
            return deepcopy(self.context)
        if sql.startswith("INSERT INTO assistant_actions"):
            if self.row is not None:
                return None
            self.row = {"id": str(uuid4()), "tenant_id": args[0], "type": args[1], "status": "pending",
                        "idempotency_key": args[2], "input_data": json.loads(args[3]),
                        "output_data": None, "triggered_by": "voice"}
            return deepcopy(self.row)
        if sql.startswith("SELECT * FROM assistant_actions"):
            assert args == (self.tenant, self.row["idempotency_key"])
            return deepcopy(self.row)
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT EXISTS(SELECT 1 FROM connectors"):
            assert args == (self.tenant,)
            return True
        if sql.startswith("UPDATE assistant_actions SET status='running'"):
            assert args == (self.tenant, self.row["id"])
            if self.row["status"] != "pending":
                return None
            self.row["status"] = "running"
            return self.row["id"]
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        sql = " ".join(sql.split())
        if sql.startswith("SET LOCAL app.current_tenant_id"):
            return "SET"
        if sql.startswith("UPDATE assistant_actions SET status=$3"):
            if self.fail_next_save:
                self.fail_next_save = False
                raise RuntimeError("Synthetic first outer save failure")
            assert args[:2] == (self.tenant, self.row["id"])
            if self.row["status"] != "running":
                return "UPDATE 0"
            self.row.update(status=args[2], output_data=json.loads(args[3]))
            return "UPDATE 1"
        raise AssertionError(sql)


@pytest.fixture
async def setup_voice(monkeypatch):
    def no_network(*_args, **_kwargs):
        raise AssertionError("Synthetic voice receipt control attempted network I/O")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    db = Rows()
    capture = ContactCaptureState(kind="email", status=CaptureStatus.CONFIRMED,
        normalized_value="private-caller@example.invalid", confirmed_at=datetime.now(timezone.utc), from_caller=True)
    session = SimpleNamespace(tenant_id=db.tenant, campaign_id=db.campaign, call_id=db.call, turn_id=1,
        captured_slots=SimpleNamespace(email_capture=capture), _voice_action_pool=db)
    send = AsyncMock()
    monkeypatch.setattr(email_service, "EmailService", lambda _pool: SimpleNamespace(send_email=send))
    yield db, session, send
    monkeypatch.undo()


async def dispatch(setup, action, inner, *, fail_outer_save=False):
    db, session, send = setup

    async def service_return(**kwargs):
        assert db.row["status"] == "running" and db.commits >= 3
        assert kwargs["tenant_id"] == db.tenant and kwargs["call_id"] == db.call
        return deepcopy(inner)

    send.side_effect = service_return
    proposal = await voice.execute_connected_voice_action(session, action, {}, "Please do that.")
    assert proposal["status"] == "needs_confirmation" and db.row is None
    send.assert_not_awaited()
    session.turn_id = 2
    session._voice_action_delivered_text = proposal["confirmation_summary"]
    db.fail_next_save = fail_outer_save
    result = await voice.execute_connected_voice_action(session, action, {}, "yes")
    assert send.await_count == 1
    return result, public_action_receipt(db.row)


def receipt(db, state, external=True):
    original = {"identity_version": "authorization_row_v1", "tenant_id": db.tenant,
        "connector_id": str(uuid4()), "provider": "gmail", "account_row_id": str(uuid4()),
        "message_id": "synthetic-message", "action_id": str(uuid4())}
    if external:
        original["external_account_id"] = "genuine-synthetic-subject"
    return {**original, "success": state == "accepted", "confirmation_allowed": state == "accepted", "status": state,
            "recipients": ["private-caller@example.invalid"], "body": "PRIVATE_APPROVED_BODY",
            "error": "PRIVATE_PROVIDER_ERROR", "access_token": "PRIVATE_TOKEN"}


def assert_evidence(actual, inner):
    for key in ("identity_version", "tenant_id", "account_row_id", "connector_id", "provider", "message_id"):
        assert actual[key] == inner[key]
    assert actual["child_action_id"] == inner["action_id"]
    if "external_account_id" in inner:
        assert actual["external_account_id"] == inner["external_account_id"]
    else:
        assert "external_account_id" not in actual


def assert_no_private_payload(*values):
    serialized = json.dumps(values)
    for private in ("private-caller@example.invalid", "private-inbox@example.invalid", "PRIVATE_APPROVED_BODY", "PRIVATE_PROVIDER_ERROR", "PRIVATE_TOKEN"):
        assert private not in serialized


@pytest.mark.parametrize("action", ["send_email", "submit_form"])
@pytest.mark.parametrize("state", ["accepted", "unknown", "failed"])
@pytest.mark.parametrize("external", [False, True])
async def test_actual_voice_and_outer_receipt_preserve_original_proof_without_payload(setup_voice, action, state, external):
    db, session, send = setup_voice
    inner = receipt(db, state, external)
    result, public = await dispatch(setup_voice, action, inner)
    expected_status = "provider_accepted" if state == "accepted" else state
    assert result["status"] == expected_status
    assert result["success"] is (state == "accepted") and result["confirmation_allowed"] is (state == "accepted")
    assert_evidence(result, inner)
    assert_evidence(db.row["output_data"], inner)
    assert_evidence(public["receipt"], inner)
    assert result["action_id"] == db.row["id"] != inner["action_id"]
    assert public["success"] is (state == "accepted") and public["confirmation_allowed"] is (state == "accepted")
    assert_no_private_payload(result, db.row["output_data"], public)
    replay = await voice.execute_connected_voice_action(session, action, {}, "yes")
    assert replay["replayed"] is True and send.await_count == 1
    assert_evidence(replay, inner)


@pytest.mark.parametrize("action", ["send_email", "submit_form"])
async def test_outer_save_failure_retains_safe_provider_evidence_without_confirmation(setup_voice, action):
    db, session, send = setup_voice
    inner = receipt(db, "accepted")
    result, public = await dispatch(setup_voice, action, inner, fail_outer_save=True)
    assert result["status"] == db.row["status"] == "unknown"
    assert result["success"] is False and result["confirmation_allowed"] is False
    assert_evidence(result["provider_result"], inner)
    assert_evidence(public["receipt"], inner)
    assert public["success"] is False and public["confirmation_allowed"] is False
    assert_no_private_payload(result, db.row["output_data"], public)
    replay = await voice.execute_connected_voice_action(session, action, {}, "yes")
    assert replay["replayed"] is True and send.await_count == 1


@pytest.mark.parametrize("action", ["send_email", "submit_form"])
async def test_missing_historical_proof_is_not_inferred_from_current_call(setup_voice, action):
    db, _, _ = setup_voice
    inner = {"success": True, "status": "accepted", "message_id": "legacy-message", "provider": "gmail"}
    result, public = await dispatch(setup_voice, action, inner)
    assert result["success"] is True and result["status"] == "provider_accepted"
    for key in ("identity_version", "tenant_id", "account_row_id", "connector_id", "external_account_id", "child_action_id"):
        assert key not in result and key not in db.row["output_data"] and key not in public["receipt"]


@pytest.mark.parametrize("action", ["send_email", "submit_form"])
async def test_allowlist_rejects_invalid_evidence_and_missing_message_never_confirms(setup_voice, action):
    db, _, _ = setup_voice
    inner = receipt(db, "unknown")
    inner.update(account_row_id=[], connector_id="x" * 513, action_id={"untrusted": "object"}, message_id=None,
                 external_account_id="   ", identity_version=123)
    result, public = await dispatch(setup_voice, action, inner)
    assert result["success"] is False and result["confirmation_allowed"] is False
    assert public["success"] is False and public["confirmation_allowed"] is False
    for key in ("account_row_id", "connector_id", "child_action_id", "external_account_id", "identity_version", "message_id"):
        assert key not in result and key not in public["receipt"]
    assert result["tenant_id"] == db.tenant and result["provider"] == "gmail"
    assert_no_private_payload(result, public)

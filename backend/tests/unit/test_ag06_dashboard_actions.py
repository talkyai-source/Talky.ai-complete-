from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.services.email_service import EmailService
from app.services.meeting_service import MeetingService
from app.infrastructure.assistant.tools import comms, meetings

PIN = {"connector_id": "connector-a", "provider": "gmail", "external_account_id": "account-a"}


def email_service(account="account-b"):
    service = object.__new__(EmailService)
    service.template_manager = MagicMock()
    service._create_action_record = AsyncMock(return_value="child-action")
    service._update_action_status = AsyncMock()
    connector = SimpleNamespace(
        external_account_id=account,
        send_email=AsyncMock(return_value=SimpleNamespace(id="provider-message")),
    )
    service._get_active_email_connector = AsyncMock(
        return_value=(connector, "connector-a", "gmail")
    )
    return service, connector


@pytest.mark.asyncio
async def test_email_reconnected_account_cannot_use_old_approval():
    service, connector = email_service()
    result = await service.send_email(
        "tenant", ["synthetic@example.test"], "Subject", "Body", reviewed_connector=PIN
    )
    assert result["success"] is False
    assert result["status"] == "failed"
    connector.send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_preview_freezes_visible_account_and_content(monkeypatch):
    service, connector = email_service("account-a")
    monkeypatch.setattr("app.services.email_service.get_email_service", lambda _: service)
    result = await comms.send_email(
        "tenant", object(), to=["synthetic@example.test"], subject="Subject", body="Body"
    )
    assert result["_apply_args"]["_reviewed_connector"] == PIN
    assert any("account-a" in str(change["after"]) for change in result["changes"])
    connector.send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_calendar_preview_freezes_account_and_time(monkeypatch):
    service = object.__new__(MeetingService)
    service._get_active_calendar_connector = AsyncMock(
        return_value=(
            SimpleNamespace(external_account_id="account-a"),
            "connector-a",
            "google_calendar",
        )
    )
    monkeypatch.setattr("app.services.meeting_service.get_meeting_service", lambda _: service)
    result = await meetings.book_meeting(
        "tenant", object(), "Synthetic meeting", "2099-01-01T12:00:00+00:00"
    )
    assert result["_apply_args"]["_reviewed_connector"]["external_account_id"] == "account-a"
    assert result["_apply_args"]["start_time"] == "2099-01-01T12:00:00+00:00"


def test_receipt_projection_never_reports_running_provider_result_as_success():
    from app.services.action_execution import public_action_receipt

    row = {
        "id": "action",
        "type": "send_email",
        "status": "running",
        "output_data": {
            "success": True,
            "message_id": "message",
            "access_token": "not-a-real-secret",
            "body": "private",
        },
    }
    receipt = public_action_receipt(row)
    assert receipt["status"] == "running"
    assert receipt["success"] is False
    assert receipt["receipt"]["message_id"] == "message"
    assert "private" not in str(receipt) and "access_token" not in str(receipt)


@pytest.mark.asyncio
async def test_review_then_reconnect_never_sends_on_new_account(monkeypatch):
    service, connector = email_service("account-a")
    monkeypatch.setattr("app.services.email_service.get_email_service", lambda _: service)
    preview = await comms.send_email(
        "tenant", object(), to=["synthetic@example.test"], subject="Subject", body="Body"
    )
    connector.external_account_id = "account-b"
    result = await comms.send_email("tenant", object(), confirm=True, **preview["_apply_args"])
    assert result["status"] == "failed"
    connector.send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_reviewed_email_auth_refresh_cannot_switch_accounts():
    from app.infrastructure.connectors.base import ConnectorProviderError

    service, old = email_service("account-a")
    old.send_email.side_effect = ConnectorProviderError(
        provider="gmail",
        operation="send_email",
        category="authentication",
        status_code=401,
        message="expired",
    )
    fresh = SimpleNamespace(external_account_id="account-b", send_email=AsyncMock())
    service._get_active_email_connector.side_effect = [
        (old, "connector-a", "gmail"),
        (fresh, "connector-a", "gmail"),
    ]
    result = await service.send_email(
        "tenant", ["synthetic@example.test"], "Subject", "Body", reviewed_connector=PIN
    )
    assert result["status"] == "failed"
    old.send_email.assert_awaited_once()
    fresh.send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_reviewed_account_receipt_is_provider_accepted_not_delivered():
    service, connector = email_service("account-a")
    result = await service.send_email(
        "tenant", ["synthetic@example.test"], "Subject", "Body", reviewed_connector=PIN
    )
    assert result["success"] and result["status"] == "accepted"
    assert result["external_account_id"] == "account-a"
    connector.send_email.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_external_identity_cannot_create_email_proposal(monkeypatch):
    service, _ = email_service(None)
    monkeypatch.setattr("app.services.email_service.get_email_service", lambda _: service)
    result = await comms.send_email(
        "tenant", object(), to=["synthetic@example.test"], subject="Subject", body="Body"
    )
    assert result["success"] is False and "_apply_args" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["account_changed", "legacy_missing", "meeting_changed"])
async def test_calendar_update_refuses_unproved_or_changed_target(case):
    from datetime import datetime, timezone
    from app.services.connector_resolver import ReviewedConnectorChanged

    service = object.__new__(MeetingService)
    connector = SimpleNamespace(
        external_account_id="calendar-a", update_event=AsyncMock(), delete_event=AsyncMock()
    )
    meeting = {
        "id": "meeting",
        "connector_id": "calendar",
        "external_event_id": "event",
        "start_time": datetime(2099, 1, 1, tzinfo=timezone.utc).isoformat(),
        "end_time": datetime(2099, 1, 1, 1, tzinfo=timezone.utc).isoformat(),
        "title": "Original",
        "metadata": {"provider": "google_calendar", "external_account_id": "calendar-a"},
    }
    reviewed_meeting = service.meeting_identity(meeting)
    pin = {
        "connector_id": "calendar",
        "provider": "google_calendar",
        "external_account_id": "calendar-a",
    }
    if case == "account_changed":
        connector.external_account_id = "calendar-b"
    if case == "legacy_missing":
        meeting["metadata"] = {}
    if case == "meeting_changed":
        meeting["external_event_id"] = "other-event"
    service.get_meeting = AsyncMock(return_value=meeting)
    service._get_active_calendar_connector = AsyncMock(
        return_value=(connector, "calendar", "google_calendar")
    )
    service._start_action = MagicMock()
    with pytest.raises(ReviewedConnectorChanged):
        await service.update_meeting(
            "tenant",
            "meeting",
            new_title="New",
            reviewed_connector=pin,
            reviewed_meeting=reviewed_meeting,
        )
    connector.update_event.assert_not_awaited()
    service._start_action.assert_not_called()


class ReceiptDB:
    def __init__(self, rows):
        self.rows, self.filters, self.pool = rows, [], self

    def acquire(self):
        return self

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def execute(self, *_):
        pass

    async def fetchrow(self, sql, tenant, actor, reference):
        field = "idempotency_key" if "idempotency_key=$3" in sql else "id"
        return next(
            (
                row
                for row in self.rows
                if row.get("tenant_id") == tenant
                and row.get("user_id") == actor
                and row.get(field) == reference
            ),
            None,
        )

    def table(self, table):
        if table == "assistant_conversations":
            query = MagicMock()
            query.insert.return_value = query
            query.update.return_value = query
            query.eq.return_value = query
            query.single.return_value = query
            query.execute.return_value = SimpleNamespace(data={"id": "synthetic-conversation"})
            return query
        assert table == "assistant_actions"
        return ReceiptQuery(self)


class ReceiptQuery:
    def __init__(self, db):
        self.db, self.filters = db, []

    def select(self, *_, **__):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, _):
        return self

    def execute(self):
        self.db.filters.extend(self.filters)
        return SimpleNamespace(
            data=[
                row
                for row in self.db.rows
                if all(str(row.get(k)) == str(v) for k, v in self.filters)
            ],
            error=None,
        )


@pytest.mark.parametrize(
    "tenant,actor,found",
    [
        ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002", True),
        ("00000000-0000-0000-0000-000000000003", "00000000-0000-0000-0000-000000000002", False),
        ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000003", False),
    ],
)
async def test_receipt_recovery_is_both_tenant_and_actor_owned(tenant, actor, found):
    from app.services.action_execution import find_owned_action_receipt

    db = ReceiptDB(
        [
            {
                "id": "receipt",
                "tenant_id": "00000000-0000-0000-0000-000000000001",
                "user_id": "00000000-0000-0000-0000-000000000002",
                "idempotency_key": "assistant:00000000-0000-0000-0000-000000000002:proposal",
                "type": "send_email",
                "status": "unknown",
                "output_data": {"provider_result": {"message_id": "remote", "body": "private"}},
            }
        ]
    )
    result = await find_owned_action_receipt(
        db, tenant_id=tenant, user_id=actor, proposal_id="proposal"
    )
    assert (result is not None) is found
    if result:
        assert result["status"] == "unknown" and not result["success"]
        assert result["receipt"] == {"message_id": "remote"}


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_status", ["completed", "running", "unknown"])
async def test_actual_websocket_lost_response_recovers_without_redispatch(
    monkeypatch, saved_status
):
    from fastapi import WebSocketDisconnect
    from app.api.v1.endpoints import assistant_ws
    from app.infrastructure.assistant.proposals import store_proposal, _PENDING
    from app.infrastructure.assistant.tools import dispatch

    _PENDING.clear()
    proposal = store_proposal(
        tool="send_email",
        args={},
        result={"preview": True, "changes": []},
        tenant_id="00000000-0000-0000-0000-000000000001",
        actor_user_id="00000000-0000-0000-0000-000000000002",
    )
    pid = proposal["proposal_id"]
    db = ReceiptDB([])
    effect_count = 0

    async def execute(*args, **kwargs):
        nonlocal effect_count
        effect_count += 1
        db.rows.append(
            {
                "id": "receipt",
                "tenant_id": "00000000-0000-0000-0000-000000000001",
                "user_id": "00000000-0000-0000-0000-000000000002",
                "idempotency_key": f"assistant:00000000-0000-0000-0000-000000000002:{pid}",
                "type": "send_email",
                "status": saved_status,
                "output_data": {
                    "message_id": "remote",
                    "provider": "gmail",
                    "success": saved_status == "completed",
                    "confirmation_allowed": saved_status == "completed",
                },
            }
        )
        return {
            "action_id": "receipt",
            "success": saved_status == "completed",
            "confirmation_allowed": saved_status == "completed",
            "status": "accepted" if saved_status == "completed" else saved_status,
        }

    class Socket:
        headers = {}
        cookies = {"talky_at": "synthetic-token"}

        def __init__(self):
            self.incoming = [
                {"type": "apply_proposal", "proposal_id": pid},
                {"type": "apply_proposal", "proposal_id": pid},
                {"type": "proposal_status", "proposal_id": pid},
            ]
            self.messages, self.dropped = [], False

        async def accept(self):
            pass

        async def close(self, **_):
            pass

        async def receive_json(self):
            if not self.incoming:
                raise WebSocketDisconnect()
            return self.incoming.pop(0)

        async def send_json(self, data):
            if data["type"] == "proposal_result" and not self.dropped:
                self.dropped = True  # acknowledgement lost after committed effect
                return
            self.messages.append(data)

    monkeypatch.setattr(assistant_ws, "get_db_client", lambda: db)
    monkeypatch.setattr(
        assistant_ws,
        "decode_and_validate_token",
        lambda _: {"sub": "00000000-0000-0000-0000-000000000002", "sid": "synthetic-session"},
    )
    monkeypatch.setattr(
        assistant_ws,
        "load_session_principal",
        AsyncMock(return_value={"tenant_id":"00000000-0000-0000-0000-000000000001"}),
    )
    monkeypatch.setattr(
        assistant_ws, "get_tenant_assistant_model", AsyncMock(return_value="unused")
    )
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def auth_acquire(*_):
        yield None
    monkeypatch.setattr(assistant_ws, "acquire_with_tenant", auth_acquire)
    monkeypatch.setattr(assistant_ws, "check_assistant_session", AsyncMock())
    monkeypatch.setattr(dispatch, "dispatch_tool", execute)
    socket = Socket()
    await assistant_ws.assistant_chat(socket, token=None, conversation_id=None)
    results = [row for row in socket.messages if row["type"] == "proposal_result"]
    assert effect_count == 1 and len(results) == 2
    assert all(row["action_id"] == "receipt" and row["status"] == saved_status for row in results)
    assert all(row["applied"] is (saved_status == "completed") for row in results)
    assert results[-1]["receipt"]["message_id"] == "remote"


async def test_status_without_receipt_is_uncertain_not_an_invitation_to_repeat():
    from app.api.v1.endpoints.assistant_ws import _proposal_status

    result = await _proposal_status(
        ReceiptDB([]),
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        "unknown-proposal",
    )
    assert result["status"] == "unavailable" and not result["applied"]
    assert "ask again" not in result["error"] and "do not submit" in result["error"]


@pytest.mark.parametrize(
    "output",
    [
        {"success": True, "confirmation_allowed": False},
        {"success": True},
        {},
        {"success": False, "confirmation_allowed": True},
    ],
)
def test_completed_record_does_not_manufacture_confirmation(output):
    from app.services.action_execution import public_action_receipt
    from app.api.v1.endpoints.assistant_ws import proposal_receipt_result

    receipt = public_action_receipt(
        {"id": "receipt", "type": "send_email", "status": "completed", "output_data": output}
    )
    result = proposal_receipt_result("proposal", receipt)
    assert receipt["status"] == "completed" and not receipt["confirmation_allowed"]
    assert not result["applied"] and "unverified" in result["error"]


@pytest.mark.asyncio
async def test_model_nested_plan_review_markers_are_not_trusted(monkeypatch):
    from app.infrastructure.assistant.tools import dispatch

    original = dispatch.ALL_TOOLS["execute_action_plan"]["function"]
    invoked = AsyncMock(return_value={"preview": True})
    monkeypatch.setitem(dispatch.ALL_TOOLS["execute_action_plan"], "function", invoked)
    monkeypatch.setattr(dispatch, "_authorize_action_tool", AsyncMock(return_value=None))
    await dispatch.dispatch_tool(
        "execute_action_plan",
        "tenant",
        object(),
        None,
        {
            "intent": "Plan",
            "actions": [
                {
                    "type": "send_email",
                    "_reviewed_connector": PIN,
                    "_prepared_report": {"sender": "forged"},
                    "body": "body",
                }
            ],
            "context": {"_reviewed_meeting": {"id": "forged"}},
        },
        actor_user_id="actor",
    )
    kwargs = invoked.await_args.kwargs
    assert kwargs["actions"] == [{"type": "send_email", "body": "body"}]
    assert kwargs["context"] == {}
    assert original is not invoked


@pytest.mark.asyncio
async def test_revoked_permission_after_claim_prevents_effect(monkeypatch):
    from app.infrastructure.assistant.tools import dispatch

    effect = AsyncMock(return_value={"success": True})
    monkeypatch.setitem(dispatch.ALL_TOOLS["send_email"], "function", effect)
    checks = AsyncMock(side_effect=[None, {"error": "permission_denied"}])
    monkeypatch.setattr(dispatch, "_authorize_action_tool", checks)

    async def claim_then_invoke(**kwargs):
        return await kwargs["executor"]()

    monkeypatch.setattr(
        "app.services.action_execution.DurableActionExecutor",
        lambda _: SimpleNamespace(execute=claim_then_invoke),
    )
    result = await dispatch.dispatch_tool(
        "send_email",
        "tenant",
        SimpleNamespace(pool=object()),
        None,
        {"confirm": True, "_reviewed_connector": PIN},
        actor_user_id="actor",
        trusted_proposal_apply=True,
        proposal_id="proposal",
    )
    assert result["status"] == "failed" and result["error"] == "permission_denied"
    effect.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_preserves_only_server_frozen_account_at_existing_executor(monkeypatch):
    from app.services.assistant_plan_steps import execute_action

    effect = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("app.infrastructure.assistant.tools.send_email", effect)
    await execute_action(
        object(),
        "send_email",
        "tenant",
        {"to": ["synthetic@example.test"], "_reviewed_connector": PIN},
        {},
    )
    assert effect.await_args.kwargs["_reviewed_connector"] == PIN


@pytest.mark.asyncio
async def test_support_report_reconnect_cannot_fall_back_to_smtp(monkeypatch):
    service, connector = email_service("account-a")
    monkeypatch.setenv("SUPPORT_REPORT_EMAIL", "support@example.test")
    monkeypatch.setattr("app.services.email_service.get_email_service", lambda _: service)
    smtp = MagicMock()
    monkeypatch.setattr("app.infrastructure.connectors.email.smtp.SMTPConnector", smtp)
    preview = await comms.report_issue(
        "tenant", object(), "Synthetic issue", contact_email="reporter@example.test"
    )
    connector.external_account_id = "account-b"
    result = await comms.report_issue("tenant", object(), confirm=True, **preview["_apply_args"])
    assert result["status"] == "failed"
    connector.send_email.assert_not_awaited()
    smtp.assert_not_called()


@pytest.mark.asyncio
async def test_support_smtp_identity_change_requires_new_review(monkeypatch):
    from app.services.email_service import EmailNotConnectedError

    monkeypatch.setenv("SUPPORT_REPORT_EMAIL", "support@example.test")
    backend = SimpleNamespace(
        review_connector=AsyncMock(side_effect=EmailNotConnectedError()), send_email=AsyncMock()
    )
    smtp = SimpleNamespace(
        host="smtp.example.test",
        port=587,
        from_email="support@example.test",
        send_email=AsyncMock(),
    )
    factory = MagicMock(return_value=smtp)
    factory.is_configured.return_value = True
    monkeypatch.setattr("app.services.email_service.get_email_service", lambda _: backend)
    monkeypatch.setattr("app.infrastructure.connectors.email.smtp.SMTPConnector", factory)
    preview = await comms.report_issue(
        "tenant", object(), "Synthetic issue", contact_email="reporter@example.test"
    )
    smtp.from_email = "other@example.test"
    result = await comms.report_issue("tenant", object(), confirm=True, **preview["_apply_args"])
    assert result["status"] == "failed"
    smtp.send_email.assert_not_awaited()
    backend.send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_email_401_refresh_also_cannot_cross_accounts():
    from app.infrastructure.connectors.base import ConnectorProviderError

    service, old = email_service("account-a")
    old.send_email.side_effect = ConnectorProviderError(
        provider="gmail",
        operation="send_email",
        category="authentication",
        status_code=401,
        message="expired",
    )
    fresh = SimpleNamespace(external_account_id="account-b", send_email=AsyncMock())
    service._get_active_email_connector.side_effect = [
        (old, "connector-a", "gmail"),
        (fresh, "connector-a", "gmail"),
    ]
    result = await service.send_email(
        "tenant", ["synthetic@example.test"], "Subject", "Body", triggered_by="voice"
    )
    assert result["status"] == "failed"
    fresh.send_email.assert_not_awaited()
    assert result["external_account_id"] == "account-a"


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("attendees", [{"email": "other@example.test"}]),
        ("description", "Different purpose"),
        ("status", "cancelled"),
    ],
)
def test_reviewed_meeting_identity_covers_recipients_and_state(field, new_value):
    meeting = {
        "id": "meeting",
        "attendees": [{"email": "approved@example.test"}],
        "description": "Reviewed purpose",
        "status": "scheduled",
    }
    original = MeetingService.meeting_identity(meeting)
    assert MeetingService.meeting_identity({**meeting, field: new_value}) != original


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_status", ["completed", "unknown", "running"])
async def test_voice_websocket_recovers_original_receipt_without_speaking_false_success(
    monkeypatch, saved_status
):
    import asyncio
    import json
    from app.api.v1.endpoints import assistant_voice_ws as voice
    from app.infrastructure.assistant.proposals import store_proposal, _PENDING
    from app.infrastructure.assistant.tools import dispatch

    tenant, actor = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
    _PENDING.clear()
    proposal = store_proposal(
        tool="send_email",
        args={},
        result={"preview": True, "changes": []},
        tenant_id=tenant,
        actor_user_id=actor,
    )
    pid = proposal["proposal_id"]
    db, sent_text, effects = ReceiptDB([]), [], []

    async def execute(*args, **kwargs):
        effects.append("effect")
        result = {
            "success": saved_status == "completed",
            "confirmation_allowed": saved_status == "completed",
            "status": "accepted" if saved_status == "completed" else saved_status,
            "message_id": "remote",
        }
        db.rows.append(
            {
                "id": "receipt",
                "tenant_id": tenant,
                "user_id": actor,
                "idempotency_key": f"assistant:{actor}:{pid}",
                "type": "send_email",
                "status": saved_status,
                "output_data": result,
            }
        )
        return {**result, "action_id": "receipt"}

    class STT:
        initialize = AsyncMock()
        cleanup = AsyncMock()

        async def stream_transcribe(self, *args, **kwargs):
            await asyncio.Event().wait()
            yield None

    class TTS:
        initialize = AsyncMock()
        cleanup = AsyncMock()

        async def stream_synthesize(self, *, text, **kwargs):
            sent_text.append(text)
            if False:
                yield None

    class Socket:
        def __init__(self):
            self.messages, self.dropped = [], False
            self.frames = [
                {"type": "apply_proposal", "proposal_id": pid},
                {"type": "apply_proposal", "proposal_id": pid},
                {"type": "proposal_status", "proposal_id": pid},
                {"type": "end"},
            ]

        async def receive(self):
            return {"type": "websocket.receive", "text": json.dumps(self.frames.pop(0))}

        async def send_json(self, data):
            if data["type"] == "proposal_result" and not self.dropped:
                self.dropped = True
                return
            self.messages.append(data)

        async def send_bytes(self, _):
            pass

        async def close(self, **_):
            pass

    monkeypatch.setattr(
        "app.domain.services.credential_resolver.get_credential_resolver",
        lambda: SimpleNamespace(resolve=AsyncMock(return_value="synthetic-unused")),
    )
    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.DeepgramFluxSTTProvider", STT)
    monkeypatch.setattr("app.infrastructure.tts.cartesia.CartesiaTTSProvider", TTS)
    monkeypatch.setattr(voice, "get_tenant_assistant_model", AsyncMock(return_value="unused"))
    monkeypatch.setattr(dispatch, "dispatch_tool", execute)
    socket = Socket()
    await voice._run_voice_session(
        websocket=socket,
        session_id="synthetic",
        tenant_id=tenant,
        user_id=actor,
        conversation_id=None,
        db_client=db,
    )
    results = [row for row in socket.messages if row["type"] == "proposal_result"]
    assert len(effects) == 1 and len(results) == 2
    assert all(row["status"] == saved_status and row["action_id"] == "receipt" for row in results)
    assert all(row["applied"] is (saved_status == "completed") for row in results)
    if saved_status == "completed":
        assert any("Delivery is not confirmed" in text for text in sent_text)
    else:
        assert not any(
            text == "Done." or "Applied the proposed changes" in text for text in sent_text
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", ["saved", "empty", "missing_id", "error"])
async def test_reminder_proof_requires_an_acknowledged_insert(ack):
    from tests.unit.test_reminder_recipient_resolution import ReminderDB, reminder_params
    from app.services.assistant_plan_steps import schedule_reminder
    from app.services.action_execution import public_action_receipt

    db = ReminderDB()
    original_table = db.table

    def table(name):
        if name != "reminders" or ack == "saved":
            return original_table(name)
        query = MagicMock()
        query.insert.return_value = query
        query.execute.return_value = SimpleNamespace(
            data=(
                []
                if ack == "empty"
                else [{}] if ack == "missing_id" else [{"id": "not-acknowledged"}]
            ),
            error="rejected" if ack == "error" else None,
        )
        return query

    db.table = table
    result = await schedule_reminder(db, "tenant", reminder_params(), {})
    assert result.get("confirmation_allowed", False) is (ack == "saved")
    if ack == "saved":
        projected = public_action_receipt(
            {"id": "outer", "status": "scheduled", "output_data": result}
        )
        assert (
            projected["confirmation_allowed"] and projected["receipt"]["reminder_id"] == "reminder"
        )
    else:
        assert not result["success"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proofs,skipped,expected",
    [
        ([True, True], None, True),
        ([True, False], None, False),
        ([True, None], None, False),
        ([None], None, False),
        ([True, None], 1, True),
        ([None], 0, False),
        ([], None, False),
    ],
)
async def test_workflow_confirmation_requires_explicit_proof_for_every_executed_step(
    monkeypatch, proofs, skipped, expected
):
    from app.domain.models.action_plan import ActionPlan, ActionStep, ActionStepResult
    from app.infrastructure.assistant.tools.workflow import execute_action_plan

    actions = [ActionStep(type="send_email") for _ in (proofs or [None])]
    results = [
        ActionStepResult(
            step_index=index,
            action_type="send_email",
            success=True,
            skipped=index == skipped,
            result={} if proof is None else {"confirmation_allowed": proof},
        )
        for index, proof in enumerate(proofs)
    ]
    plan = ActionPlan(
        id="plan",
        tenant_id="tenant",
        intent="Reviewed plan",
        actions=actions,
        step_results=results,
        status="completed",
    )
    service = SimpleNamespace(
        create_plan=AsyncMock(return_value=plan), execute_plan=AsyncMock(return_value=plan)
    )
    monkeypatch.setattr(
        "app.services.assistant_agent_service.get_assistant_agent_service", lambda _: service
    )
    monkeypatch.setattr(
        "app.infrastructure.assistant.tools.dispatch._authorize_action_tool",
        AsyncMock(return_value=None),
    )
    result = await execute_action_plan(
        "tenant",
        object(),
        "Reviewed plan",
        [{"type": "send_email"}],
        confirm=True,
        actor_user_id="actor",
    )
    assert result["success"] is True
    assert result["confirmation_allowed"] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "cancel"])
@pytest.mark.parametrize("change", ["external_event_id", "attendees", "account", "deleted", None])
async def test_calendar_rechecks_reviewed_meeting_after_connector_resolution(operation, change):
    import asyncio
    import copy
    from tests.unit.test_calendar_delivery_contract import DB, service as calendar_service
    from app.services.connector_resolver import ReviewedConnectorChanged

    db = DB()
    subject, connector = calendar_service(db)
    reviewed = subject.meeting_identity(db.meeting)
    pin = {
        "connector_id": "pinned-calendar",
        "provider": "google_calendar",
        "external_account_id": "calendar-account",
    }

    async def read_meeting(*_):
        return copy.deepcopy(db.meeting)

    async def resolve(*_, **__):
        await asyncio.sleep(0)
        if change == "external_event_id":
            db.meeting["external_event_id"] = "different-event"
        elif change == "attendees":
            db.meeting["attendees"] = [{"email": "changed@example.test"}]
        elif change == "account":
            db.meeting["metadata"]["external_account_id"] = "different-account"
        elif change == "deleted":
            db.meeting = None
        return connector, "pinned-calendar", "google_calendar"

    subject.get_meeting = AsyncMock(side_effect=read_meeting)
    subject._get_active_calendar_connector = AsyncMock(side_effect=resolve)
    arguments = {"reviewed_connector": pin, "reviewed_meeting": reviewed}
    method = subject.update_meeting if operation == "update" else subject.cancel_meeting
    if operation == "update":
        arguments["new_title"] = "Reviewed change"
    if change is None:
        result = await method("tenant", "meeting", **arguments)
        assert result["success"] and result["confirmation_allowed"]
    else:
        with pytest.raises(ReviewedConnectorChanged):
            await method("tenant", "meeting", **arguments)
        connector.update_event.assert_not_awaited()
        connector.delete_event.assert_not_awaited()
        assert db.writes == []

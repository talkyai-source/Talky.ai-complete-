"""Action previews, explicit timezones and persisted workflows at their boundaries."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.infrastructure.assistant.tools import meetings
from app.infrastructure.assistant.tools.llm_schemas import GROQ_TOOL_SCHEMAS
from app.services.assistant_agent_service import AssistantAgentService
from app.services.assistant_plan_steps import schedule_reminder


def test_calendar_and_workflow_schemas_match_executor_arguments():
    schemas = {item["function"]["name"]: item["function"]["parameters"] for item in GROQ_TOOL_SCHEMAS}
    for name in ("book_meeting", "update_meeting", "cancel_meeting", "schedule_reminder", "execute_action_plan"):
        assert "confirm" in schemas[name]["properties"]
    assert "timezone_name" in schemas["check_availability"]["required"]


@pytest.mark.asyncio
async def test_availability_accepts_advertised_date_and_uses_timezone(monkeypatch):
    service = SimpleNamespace(get_availability=AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.meeting_service.get_meeting_service", lambda _: service)
    result = await meetings.check_availability("tenant", object(), date="2030-10-02", timezone_name="Asia/Karachi")
    assert result["success"] is True
    start = service.get_availability.call_args.kwargs["start_time"]
    assert start.hour == 9 and start.utcoffset() == timedelta(hours=5)


@pytest.mark.asyncio
async def test_booking_preview_and_ambiguous_time_never_create_event(monkeypatch):
    service = SimpleNamespace(review_connector=AsyncMock(return_value={"connector_id": "calendar", "provider": "google_calendar", "external_account_id": "account"}), create_meeting=AsyncMock(return_value={"success": True}))
    monkeypatch.setattr("app.services.meeting_service.get_meeting_service", lambda _: service)
    preview = await meetings.book_meeting("tenant", object(), title="Demo", start_time="2030-10-02T09:00:00+05:00")
    invalid = await meetings.book_meeting("tenant", object(), title="Demo", start_time="2030-10-02T09:00:00", confirm=True)
    assert preview["preview"] is True
    assert invalid["success"] is False and "offset" in invalid["error"]
    service.create_meeting.assert_not_called()
    applied = await meetings.book_meeting("tenant", object(), confirm=True, **preview["_apply_args"])
    assert applied["success"] is True
    service.create_meeting.assert_awaited_once()


@pytest.mark.asyncio
async def test_reminder_requires_explicit_time_and_preview_writes_nothing():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{"id": "lead", "phone_number": "+15555550100"}]
    missing = await schedule_reminder(db, "tenant", {"lead_id": "lead"}, {})
    assert missing["success"] is False
    when = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    preview = await schedule_reminder(db, "tenant", {"lead_id": "lead", "scheduled_at": when, "reminder_type": "sms"}, {}, preview=True)
    assert preview["preview"] is True
    assert preview["_apply_args"]["scheduled_at"] == when
    db.table.return_value.insert.assert_not_called()


@pytest.mark.asyncio
async def test_workflow_persistence_failure_stops_before_actions():
    db = MagicMock()
    db.table.return_value.insert.return_value.execute.side_effect = RuntimeError("db down")
    service = AssistantAgentService(db)
    with pytest.raises(RuntimeError, match="no actions were run"):
        await service.create_plan(tenant_id="tenant", intent="Send", context={}, actions=[{"type": "send_sms", "to": ["+15555550100"], "message": "Hello"}])


@pytest.mark.asyncio
async def test_workflow_rechecks_permission_before_each_step(monkeypatch):
    from app.domain.models.action_plan import ActionPlan, ActionStep
    execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("app.services.assistant_agent_service.execute_action", execute)
    plan = ActionPlan(tenant_id="tenant", intent="Send", context={}, actions=[ActionStep(type="send_sms")])
    service = AssistantAgentService(MagicMock())
    result = await service.execute_plan(plan, authorize_action=AsyncMock(return_value={"error": "permission_denied"}))
    assert result.successful_steps == 0
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_workflow_freezes_child_preview_instead_of_re_resolving_contact(monkeypatch):
    from app.core.security.rbac import Permission
    from app.infrastructure.assistant.tools import ALL_TOOLS
    from app.infrastructure.assistant.tools.workflow import execute_action_plan
    monkeypatch.setattr("app.infrastructure.assistant.tools.dispatch.get_effective_permissions", AsyncMock(return_value={Permission.EMAIL_SEND}))
    preview = AsyncMock(return_value={"preview": True, "changes": [{"field": "To", "after": "frozen@example.com"}],
        "_apply_args": {"to": ["frozen@example.com"], "subject": "Frozen", "body": "Reviewed content"}})
    monkeypatch.setitem(ALL_TOOLS["send_email"], "function", preview)
    result = await execute_action_plan("tenant", SimpleNamespace(pool=object()), "Email lead",
        [{"type": "send_email", "lead_id": "lead-1", "subject": "Requested", "body": "Requested body"}],
        actor_user_id="actor")
    assert result["preview"] is True
    saved = result["_apply_args"]["actions"][0]
    assert saved["to"] == ["frozen@example.com"] and saved["body"] == "Reviewed content"
    assert "lead_id" not in saved


@pytest.mark.asyncio
async def test_unknown_external_result_stops_remaining_plan_steps(monkeypatch):
    from app.domain.models.action_plan import ActionPlan, ActionStep
    execute = AsyncMock(return_value={"success": False, "status": "unknown"})
    monkeypatch.setattr("app.services.assistant_agent_service.execute_action", execute)
    plan = ActionPlan(tenant_id="tenant", intent="Send", context={}, actions=[
        ActionStep(type="send_sms"), ActionStep(type="send_email")])
    result = await AssistantAgentService(MagicMock()).execute_plan(plan)
    execute.assert_awaited_once()
    assert result.successful_steps == 0 and "unknown" in result.error
    assert result.current_step == 1


@pytest.mark.asyncio
async def test_actual_step_catches_transport_exception_as_unknown_and_stops_failure_branch(monkeypatch):
    from app.domain.models.action_plan import ActionPlan, ActionStep
    send = AsyncMock(side_effect=TimeoutError("Provider may have accepted"))
    followup = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("app.infrastructure.assistant.tools.send_sms", send)
    monkeypatch.setattr("app.infrastructure.assistant.tools.send_email", followup)
    plan = ActionPlan(tenant_id="tenant", intent="Send", context={}, actions=[
        ActionStep(type="send_sms", parameters={"to": ["+15555550100"], "message": "Test"}),
        ActionStep(type="send_email", condition="if_previous_failed")])
    result = await AssistantAgentService(MagicMock()).execute_plan(plan)
    send.assert_awaited_once()
    followup.assert_not_called()
    assert result.step_results[0].result["status"] == "unknown"

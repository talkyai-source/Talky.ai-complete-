"""Assistant action tools must not hand an exception's own text to the customer.

Package B of the error-text design: ``meetings.py``, ``workflow.py``, ``comms.py``
(send_email) and ``app/services/assistant_plan_steps.py``.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import asyncpg
import pytest

from app.domain.services.email_template_manager import EmailContentValidationError
from app.infrastructure.assistant.tools import comms, dispatch as dispatch_module, meetings, workflow
from app.services.assistant_plan_steps import schedule_reminder as plan_step_schedule_reminder
from app.services.connector_resolver import ConnectorLookupError, ReviewedConnectorChanged
from app.services.email_service import EmailNotConnectedError
from app.services.meeting_service import CalendarNotConnectedError
from tests.unit.assistant.test_tool_error_text import (
    MARKER,
    TENANT,
    _assert_masked,
    _BoomDB,
)

FUTURE = "2099-10-02T09:00:00+05:00"


@pytest.fixture(autouse=True)
def _log_level(caplog):
    caplog.set_level(logging.INFO)


def _use_service(monkeypatch, service):
    monkeypatch.setattr("app.services.meeting_service.get_meeting_service", lambda _db: service)


def _service_raising(exc):
    async def boom(*_a, **_k):
        raise exc

    return SimpleNamespace(
        get_availability=boom, review_connector=boom, review_meeting=boom,
        create_meeting=boom, update_meeting=boom, cancel_meeting=boom,
        meeting_identity=lambda current: {},
    )


# --- meetings ----------------------------------------------------------------

async def _check_availability(monkeypatch, exc):
    _use_service(monkeypatch, _service_raising(exc))
    return await meetings.check_availability(
        TENANT, object(), date="2099-10-02", timezone_name="Asia/Karachi",
    )


async def _book_meeting(monkeypatch, exc):
    _use_service(monkeypatch, _service_raising(exc))
    return await meetings.book_meeting(TENANT, object(), title="Demo", start_time=FUTURE)


async def _update_meeting(monkeypatch, exc):
    _use_service(monkeypatch, _service_raising(exc))
    return await meetings.update_meeting_tool(TENANT, _BoomDB(exc), meeting_id="m1", new_title="New")


async def _cancel_meeting(monkeypatch, exc):
    _use_service(monkeypatch, _service_raising(exc))
    return await meetings.cancel_meeting_tool(TENANT, _BoomDB(exc), meeting_id="m1")


MEETING_CASES = [
    (_check_availability, "Could not check calendar availability for that date and timezone."),
    (_book_meeting, "The meeting was not booked."),
    (_update_meeting, "The meeting was not changed."),
    (_cancel_meeting, "The meeting was not cancelled."),
]


@pytest.mark.parametrize("call,sentence", MEETING_CASES, ids=lambda v: getattr(v, "__name__", None))
async def test_meetings_mask_runtime_error(call, sentence, monkeypatch, caplog):
    result = await call(monkeypatch, RuntimeError(MARKER))
    assert result["success"] is False
    assert result["error"] == sentence
    _assert_masked(result, caplog)


@pytest.mark.parametrize("call,sentence", MEETING_CASES, ids=lambda v: getattr(v, "__name__", None))
async def test_meetings_mask_asyncpg_data_error(call, sentence, monkeypatch, caplog):
    result = await call(monkeypatch, asyncpg.exceptions.DataError(MARKER))
    assert result["error"] == sentence
    _assert_masked(result, caplog)


async def test_check_availability_timezone_sentence_reaches_the_customer(monkeypatch):
    _use_service(monkeypatch, _service_raising(RuntimeError("unused")))
    result = await meetings.check_availability(TENANT, object(), date="2099-10-02")
    assert result == {"success": False, "error": "Provide the timezone for availability; do not assume UTC."}


async def test_check_availability_connector_lookup_message_is_kept(monkeypatch):
    exc = ConnectorLookupError("calendar", "driver detail must stay out of the text")
    result = await _check_availability(monkeypatch, exc)
    assert result == {"success": False, "error": exc.message}
    assert "driver detail" not in result["error"]


async def test_book_meeting_reviewed_connector_changed_is_kept(monkeypatch):
    exc = ReviewedConnectorChanged("The connected account changed after review. No action was sent; review a new proposal.")
    result = await _book_meeting(monkeypatch, exc)
    assert result == {"success": False, "error": str(exc)}


async def test_book_meeting_connector_lookup_message_is_kept(monkeypatch):
    exc = ConnectorLookupError("calendar")
    result = await _book_meeting(monkeypatch, exc)
    assert result == {"success": False, "error": exc.message}


async def test_update_meeting_needs_a_change_sentence_reaches_the_customer(monkeypatch):
    _use_service(monkeypatch, _service_raising(RuntimeError("unused")))
    result = await meetings.update_meeting_tool(TENANT, _BoomDB(RuntimeError("unused")), meeting_id="m1")
    assert result == {"success": False, "error": "Provide a new time or title."}


async def test_cancel_meeting_calendar_not_connected_default_text_is_kept(monkeypatch):
    result = await _cancel_meeting(monkeypatch, CalendarNotConnectedError())
    assert result == {"success": False, "error": CalendarNotConnectedError().message}


async def test_book_meeting_naive_time_still_names_the_offset(monkeypatch):
    _use_service(monkeypatch, _service_raising(RuntimeError("unused")))
    result = await meetings.book_meeting(
        TENANT, object(), title="Demo", start_time="2099-10-02T09:00:00", confirm=True,
    )
    assert "offset" in result["error"]


# --- schedule_reminder tool --------------------------------------------------

def _plan_step_raising(monkeypatch, exc):
    async def boom(**_kwargs):
        raise exc

    monkeypatch.setattr("app.services.assistant_plan_steps.schedule_reminder", boom)


async def _schedule_reminder_tool():
    return await workflow.schedule_reminder(
        TENANT, object(), lead_id="l1", scheduled_at=FUTURE, reminder_type="sms",
    )


async def test_schedule_reminder_masks_runtime_error(monkeypatch, caplog):
    _plan_step_raising(monkeypatch, RuntimeError(MARKER))
    result = await _schedule_reminder_tool()
    assert result == {"success": False, "error": "The reminder was not scheduled."}
    _assert_masked(result, caplog)


async def test_schedule_reminder_masks_asyncpg_data_error(monkeypatch, caplog):
    _plan_step_raising(monkeypatch, asyncpg.exceptions.DataError(MARKER))
    result = await _schedule_reminder_tool()
    assert result == {"success": False, "error": "The reminder was not scheduled."}
    _assert_masked(result, caplog)


async def test_schedule_reminder_plain_value_error_is_kept(monkeypatch):
    _plan_step_raising(monkeypatch, ValueError("Invalid isoformat string: 'soon'"))
    result = await _schedule_reminder_tool()
    assert result == {"success": False, "error": "Invalid isoformat string: 'soon'"}


# --- execute_action_plan (the ValueError branch) -----------------------------

def _authorize_raising(monkeypatch, exc):
    async def boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(dispatch_module, "_authorize_action_tool", boom)


async def _execute_action_plan():
    return await workflow.execute_action_plan(
        TENANT, object(), intent="Send", actions=[{"type": "send_sms"}],
    )


class _QuotingValueError(ValueError):
    """A ValueError subclass that quotes its arguments, as pydantic's does."""


async def test_action_plan_value_error_branch_masks_a_value_error_subclass(monkeypatch, caplog):
    _authorize_raising(monkeypatch, _QuotingValueError(MARKER))
    result = await _execute_action_plan()
    assert result == {
        "success": False,
        "error": "The action plan's steps are not valid. Check each step's details.",
    }
    _assert_masked(result, caplog)


async def test_action_plan_plain_value_error_is_kept(monkeypatch):
    _authorize_raising(monkeypatch, ValueError("Step 2 needs a phone number."))
    result = await _execute_action_plan()
    assert result == {"success": False, "error": "Step 2 needs a phone number."}


# --- execute_action_plan (everything that is not a ValueError) ----------------

PLAN_SENTENCE = "The action plan could not be completed. Check its status before running it again."


async def test_action_plan_catch_all_masks_a_plain_runtime_error(monkeypatch, caplog):
    # A plain RuntimeError may carry a driver's text: it is NOT trusted here, although the
    # one message this tool does pass on is raised as a RuntimeError subclass.
    _authorize_raising(monkeypatch, RuntimeError(MARKER))
    result = await _execute_action_plan()
    assert result == {"success": False, "error": PLAN_SENTENCE}
    _assert_masked(result, caplog)


async def test_action_plan_not_saved_message_is_kept(monkeypatch):
    from app.services.assistant_agent_service import ActionPlanNotSavedError

    kept = "The action plan could not be saved; no actions were run."
    _authorize_raising(monkeypatch, ActionPlanNotSavedError(kept))
    result = await _execute_action_plan()
    assert result == {"success": False, "error": kept}


def test_the_plan_service_raises_its_own_class_when_the_plan_is_not_saved():
    # The message reaches the customer only by this exact class, so the service must raise it,
    # and it stays a RuntimeError for any caller that catches that. (A guard on the source
    # text: the tool-level behaviour is proven by the two tests above.)
    import inspect

    from app.services.assistant_agent_service import ActionPlanNotSavedError, AssistantAgentService

    assert issubclass(ActionPlanNotSavedError, RuntimeError)
    source = inspect.getsource(AssistantAgentService)
    assert source.count("raise ActionPlanNotSavedError(") == 2
    assert "raise RuntimeError(\"The action plan could not be saved" not in source


# --- send_email --------------------------------------------------------------

SEND_EMAIL_SENTENCE = "The email request could not be completed."


async def _send_email(monkeypatch, exc, *, confirm=False):
    async def boom(*_a, **_k):
        raise exc

    service = SimpleNamespace(review_connector=boom, send_email=boom)
    monkeypatch.setattr("app.services.email_service.get_email_service", lambda _db: service)
    return await comms.send_email(
        TENANT, object(), to=["a@example.com"], subject="Hi", body="Hello",
        confirm=confirm, _reviewed_connector={"connector_id": "c1"},
    )


async def test_send_email_preview_masks_runtime_error(monkeypatch, caplog):
    result = await _send_email(monkeypatch, RuntimeError(MARKER))
    assert result == {
        "success": False, "status": "failed", "confirmation_allowed": False,
        "error": SEND_EMAIL_SENTENCE,
    }
    _assert_masked(result, caplog)


async def test_send_email_confirm_masks_runtime_error_and_keeps_unknown_status(monkeypatch, caplog):
    result = await _send_email(monkeypatch, RuntimeError(MARKER), confirm=True)
    assert result == {
        "success": False, "status": "unknown", "confirmation_allowed": False,
        "error": SEND_EMAIL_SENTENCE,
    }
    _assert_masked(result, caplog)


async def test_send_email_preview_not_connected_text_is_kept(monkeypatch):
    exc = EmailNotConnectedError()
    result = await _send_email(monkeypatch, exc)
    assert result == {
        "success": False, "status": "failed", "confirmation_allowed": False,
        "error": exc.message,
    }


async def test_send_email_connector_lookup_message_is_kept(monkeypatch):
    exc = ConnectorLookupError("email", "driver detail")
    result = await _send_email(monkeypatch, exc)
    assert result["error"] == exc.message
    assert result["status"] == "failed"


async def test_send_email_content_validation_message_is_kept(monkeypatch):
    exc = EmailContentValidationError("Email validation failed: Subject cannot be empty", issues=["x"])
    result = await _send_email(monkeypatch, exc)
    assert result["error"] == "Email validation failed: Subject cannot be empty"


# --- assistant_plan_steps.schedule_reminder ----------------------------------

async def test_plan_step_insert_failure_is_masked(caplog):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"id": "l1", "phone_number": "+15555550100", "email": "a@example.com"},
    ]
    db.table.return_value.insert.return_value.execute.side_effect = RuntimeError(MARKER)
    result = await plan_step_schedule_reminder(
        db, TENANT, {"lead_id": "l1", "scheduled_at": FUTURE, "reminder_type": "sms"}, {},
    )
    assert result == {"success": False, "error": "The reminder could not be confirmed."}
    text = json.dumps(result, default=str)
    assert "leads_phone_key" not in text and "DETAIL" not in text
    _assert_masked(result, caplog)

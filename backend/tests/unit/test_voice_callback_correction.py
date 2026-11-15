"""A corrected callback time is a new proposal; only a later confirmation schedules it."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline import action_execution as ax
from app.domain.services.voice_pipeline.contact_capture import CaptureStatus, ContactCaptureState

PHONE = "+14155552671"


def _when(days):
    moment = (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0)
    return moment.isoformat()


A, B = _when(3), _when(5)


class FakeExecutor:
    runs: list = []

    def __init__(self, pool):
        pass

    async def execute(self, *, executor, payload, **_):
        result = await executor()
        FakeExecutor.runs.append(result)
        return result


@pytest.fixture
def rig(monkeypatch):
    FakeExecutor.runs = []
    context = {"brief": {"approved_next_actions": ["schedule_callback"]}, "email_connected": False,
               "direction": "outbound", "campaign_status": "active", "phone_number": PHONE,
               "lead_id": "lead", "callback_worker_ready": True, "call_id": "call",
               "campaign_id": "camp", "call_status": "in_progress"}

    async def pool(_session):
        return object()

    async def load(_session, _pool):
        return dict(context)

    monkeypatch.setattr(ax, "_pool", pool)
    monkeypatch.setattr(ax, "_context", load)
    monkeypatch.setattr(ax, "_call_ids", lambda _s: ("tenant", "camp", "call"))
    monkeypatch.setattr(ax, "DurableActionExecutor", FakeExecutor)
    capture = ContactCaptureState(kind="phone", status=CaptureStatus.CONFIRMED, normalized_value=PHONE,
                                  confirmed_at=datetime.now(timezone.utc))
    session = SimpleNamespace(turn_id=1, captured_slots=SimpleNamespace(phone_capture=capture),
                              _voice_action_delivered_text="")

    async def say(turn, text, when):
        session.turn_id = turn
        arguments = {"requested_time": when, "timezone": "UTC"}
        result = await ax.execute_connected_voice_action(session, "schedule_callback", arguments, text)
        # The agent reads the proposal back; a bare "yes" later refers to it.
        if result["status"] == "needs_confirmation":
            session._voice_action_delivered_text = result["confirmation_summary"]
        return result

    return session, say


@pytest.mark.asyncio
async def test_corrected_callback_time_is_a_new_proposal_scheduled_only_after_a_later_confirmation(rig):
    session, say = rig
    first = await say(1, "Call me back on the third day", A)
    assert first["status"] == "needs_confirmation"
    corrected = await say(2, "Actually make it later in the week instead", B)
    assert corrected["status"] == "needs_confirmation"
    assert corrected["request_id"] != first["request_id"]
    assert corrected["confirmation_summary"] != first["confirmation_summary"]
    assert FakeExecutor.runs == []  # neither A nor B has been scheduled
    done = await say(3, "Yes", B)
    assert done["status"] == "scheduled" and done["success"]
    assert datetime.fromisoformat(done["scheduled_at"]) == datetime.fromisoformat(B)
    assert len(FakeExecutor.runs) == 1


@pytest.mark.asyncio
async def test_old_time_can_not_be_scheduled_by_a_yes_after_the_correction(rig):
    session, say = rig
    await say(1, "Call me then", A)
    await say(2, "Actually make it later in the week instead", B)
    replay = await say(3, "Yes", A)  # model replays the superseded time
    assert replay["status"] == "needs_confirmation"
    assert FakeExecutor.runs == []


@pytest.mark.asyncio
async def test_a_no_after_the_corrected_time_schedules_nothing(rig):
    session, say = rig
    await say(1, "Call me then", A)
    await say(2, "Actually make it later in the week instead", B)
    refused = await say(3, "No, not then", B)
    assert refused["status"] == "needs_confirmation" and not refused["success"]
    assert FakeExecutor.runs == []

"""Assistant tools must not hand an exception's own text to the customer.

Package A of the error-text design: the ``tool_error_text`` helper and the read
tools, ``initiate_call``, ``start_campaign``, the campaign admin and voice tools,
and ``dispatch_tool``.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import asyncpg
import pytest

from app.domain.services.campaign_service import (
    CampaignError,
    CampaignPermissionCheckUnavailable,
    CampaignReadinessError,
)
from app.infrastructure.assistant.tools import campaign_admin, campaign_ai_options
from app.infrastructure.assistant.tools import campaigns as campaigns_tool
from app.infrastructure.assistant.tools import dispatch as dispatch_module
from app.infrastructure.assistant.tools import ALL_TOOLS
from app.infrastructure.assistant.tools.calls import get_recent_calls, initiate_call
from app.infrastructure.assistant.tools.campaign_admin import (
    get_campaign_detail,
    manage_lead,
    update_campaign_config,
)
from app.infrastructure.assistant.tools.campaign_ai_options import (
    apply_campaign_voice,
    list_voices,
)
from app.infrastructure.assistant.tools.campaigns import get_campaigns, start_campaign
from app.infrastructure.assistant.tools.dashboard import (
    get_actions_today,
    get_dashboard_stats,
    get_usage_info,
)
from app.infrastructure.assistant.tools.leads import (
    get_lead_followup,
    get_leads,
    get_qualified_leads,
)
from app.infrastructure.assistant.tools.tool_errors import RETRY_HINT, tool_error_text

MARKER = (
    'duplicate key value violates unique constraint "leads_phone_key" '
    "DETAIL: Key (phone_number)=(+447700900123)"
)
MARKER_PARTS = ("duplicate key", "leads_phone_key", "DETAIL", "+447700900123")
TENANT = "10000000-0000-0000-0000-000000000001"


def _marker_records(caplog):
    return [
        r for r in caplog.records
        if "leads_phone_key" in r.getMessage()
        or (r.exc_info and "leads_phone_key" in str(r.exc_info[1]))
    ]


def _assert_masked(result, caplog):
    text = json.dumps(result, default=str)
    for part in MARKER_PARTS:
        assert part not in text, (part, result)
    assert len(_marker_records(caplog)) == 1


class _BoomDB:
    pool = None

    def __init__(self, exc):
        self._exc = exc

    def table(self, *_args, **_kwargs):
        raise self._exc


@pytest.fixture(autouse=True)
def _log_level(caplog):
    caplog.set_level(logging.INFO)


# --- helper unit tests -------------------------------------------------------

def test_helper_exact_type_passes_through():
    assert tool_error_text(ValueError("since must be earlier."), "fb", tool="t", public=(ValueError,)) \
        == "since must be earlier."


def test_helper_subclass_of_public_type_is_masked(caplog):
    result = tool_error_text(asyncpg.exceptions.DataError(MARKER), "fb", tool="t", public=(ValueError,))
    assert result == "fb"
    _assert_masked(result, caplog)


def test_helper_class_not_listed_is_masked(caplog):
    result = tool_error_text(RuntimeError(MARKER), "fb", tool="t", public=(ValueError,))
    assert result == "fb"
    _assert_masked(result, caplog)


def test_helper_retry_hint_only_for_retryable_errors(caplog):
    assert tool_error_text(ConnectionRefusedError(MARKER), "fb", tool="t") == "fb" + RETRY_HINT
    assert tool_error_text(RuntimeError("x"), "fb", tool="t") == "fb"


def test_helper_empty_text_falls_back():
    assert tool_error_text(ValueError("   "), "fb", tool="t", public=(ValueError,)) == "fb"


def test_helper_raising_str_falls_back():
    class Bad(ValueError):
        def __str__(self):
            raise RuntimeError("no text")

    # Exact type listed, but str() raises: the helper must not raise.
    assert tool_error_text(Bad(), "fb", tool="t", public=(Bad,)) == "fb"


# --- Group 1: fixed sentence only -------------------------------------------

async def _get_lead_followup(exc):
    return await get_lead_followup(TENANT, _BoomDB(exc), lead_id="l1")


async def _get_campaigns(exc):
    return await get_campaigns(TENANT, _BoomDB(exc))


async def _get_usage_info(exc):
    return await get_usage_info(TENANT, _BoomDB(exc))


async def _get_actions_today(exc):
    return await get_actions_today(TENANT, _BoomDB(exc))


async def _get_campaign_detail(exc):
    return await get_campaign_detail(TENANT, _BoomDB(exc), campaign_id="c1")


async def _update_campaign_config(exc):
    return await update_campaign_config(
        TENANT, _BoomDB(exc), campaign_id="c1", changes={"goal": "x"}, confirm=False,
    )


async def _manage_lead(exc):
    return await manage_lead(TENANT, _BoomDB(exc), campaign_id="c1", action="remove", lead_id="l1")


async def _get_leads(exc):
    return await get_leads(TENANT, _BoomDB(exc))


async def _get_qualified_leads(exc):
    return await get_qualified_leads(TENANT, _BoomDB(exc))


async def _get_recent_calls(exc):
    return await get_recent_calls(TENANT, _BoomDB(exc))


async def _get_dashboard_stats(exc):
    return await get_dashboard_stats(TENANT, _BoomDB(exc))


GROUP1 = [
    (_get_lead_followup, "Could not read that contact's follow-up details."),
    (_get_campaigns, "Could not read the campaigns."),
    (_get_usage_info, "Could not read the plan usage."),
    (_get_actions_today, "Could not read today's assistant actions."),
    (_get_campaign_detail, "Could not read the campaign."),
    (_update_campaign_config, "The campaign update could not be confirmed. Preview the change again."),
]

# Sites that also trust a plain ValueError (so a DataError must still be masked).
GROUP2 = [
    (_get_leads, "Could not read the contacts."),
    (_get_qualified_leads, "Could not read the qualified leads."),
    (_get_recent_calls, "Could not read the calls."),
    (_get_dashboard_stats, "Could not read the dashboard figures."),
    (_manage_lead, "The contact change could not be confirmed. Preview the change again."),
]


@pytest.mark.parametrize("call,sentence", GROUP1 + GROUP2, ids=lambda v: getattr(v, "__name__", None))
async def test_runtime_error_text_never_reaches_the_customer(call, sentence, caplog):
    result = await call(RuntimeError(MARKER))
    assert result["error"] == sentence
    _assert_masked(result, caplog)


@pytest.mark.parametrize("call,sentence", GROUP2, ids=lambda v: getattr(v, "__name__", None))
async def test_asyncpg_data_error_is_masked_where_value_error_is_trusted(call, sentence, caplog):
    result = await call(asyncpg.exceptions.DataError(MARKER))
    assert result["error"] == sentence
    _assert_masked(result, caplog)


async def test_connection_refused_gets_the_retry_hint(caplog):
    result = await _get_campaigns(ConnectionRefusedError(MARKER))
    assert result["error"] == "Could not read the campaigns." + RETRY_HINT
    _assert_masked(result, caplog)


# --- Group 2: a message written for people still reaches the customer --------

@pytest.mark.parametrize("call", [_get_leads, _get_qualified_leads, _get_recent_calls, _get_dashboard_stats])
async def test_range_validation_sentence_reaches_the_customer(call):
    if call is _get_leads:
        result = await get_leads(TENANT, _BoomDB(RuntimeError("unused")), since="2026-02-01", until="2026-01-01")
    elif call is _get_qualified_leads:
        result = await get_qualified_leads(TENANT, _BoomDB(RuntimeError("unused")), since="2026-02-01", until="2026-01-01")
    elif call is _get_recent_calls:
        result = await get_recent_calls(TENANT, _BoomDB(RuntimeError("unused")), since="2026-02-01", until="2026-01-01")
    else:
        result = await get_dashboard_stats(TENANT, _BoomDB(RuntimeError("unused")), since="2026-02-01", until="2026-01-01")
    assert result == {"error": "since must be earlier than until."}


class _CampaignRowDB:
    """Returns one campaign whose saved country is not a real region."""
    pool = None

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        return SimpleNamespace(
            data=[{"id": "c1", "direction": "outbound", "script_config": {"default_country_code": "ZZ"}}],
            count=1, error=None,
        )


async def test_manage_lead_default_country_sentence_reaches_the_customer(monkeypatch):
    result = await manage_lead(
        TENANT, _CampaignRowDB(), campaign_id="c1", action="remove", lead_id="l1",
    )
    assert result["error"].startswith("default_country must be")


# --- initiate_call -----------------------------------------------------------

async def test_initiate_call_preview_masks_runtime_error(monkeypatch, caplog):
    from app.domain.services import phone_number_normalizer as pn

    def boom(*_a, **_k):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(pn, "normalize_phone_for_capture", boom)
    result = await initiate_call(TENANT, SimpleNamespace(pool=object()), phone_number="+14155551234", campaign_id="c1")
    assert result["error"] == "Could not prepare this call."
    assert result["success"] is False and result["status"] == "failed"
    assert result["confirmation_allowed"] is False and result["dialer_job_id"] is None
    _assert_masked(result, caplog)


async def test_initiate_call_preview_masks_asyncpg_data_error(monkeypatch, caplog):
    from app.domain.services import phone_number_normalizer as pn

    def boom(*_a, **_k):
        raise asyncpg.exceptions.DataError(MARKER)

    monkeypatch.setattr(pn, "normalize_phone_for_capture", boom)
    result = await initiate_call(TENANT, SimpleNamespace(pool=object()), phone_number="+14155551234", campaign_id="c1")
    assert result["error"] == "Could not prepare this call."
    _assert_masked(result, caplog)


async def test_initiate_call_connection_refused_gets_the_retry_hint(monkeypatch, caplog):
    from app.domain.services import phone_number_normalizer as pn

    def boom(*_a, **_k):
        raise ConnectionRefusedError(MARKER)

    monkeypatch.setattr(pn, "normalize_phone_for_capture", boom)
    result = await initiate_call(TENANT, SimpleNamespace(pool=object()), phone_number="+14155551234", campaign_id="c1")
    assert result["error"] == "Could not prepare this call." + RETRY_HINT


async def test_initiate_call_phone_sentence_reaches_the_customer():
    result = await initiate_call(
        TENANT, SimpleNamespace(pool=object()), phone_number="020 7183 8750", campaign_id="c1",
    )
    assert result["error"].startswith("Country or region is required")
    assert result["status"] == "failed"


async def test_initiate_call_confirm_keeps_its_fixed_sentence(monkeypatch):
    from app.domain.services import phone_number_normalizer as pn

    def boom(*_a, **_k):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(pn, "normalize_phone_for_capture", boom)
    result = await initiate_call(
        TENANT, SimpleNamespace(pool=object()), phone_number="+14155551234", campaign_id="c1", confirm=True,
    )
    assert result["error"] == "Call scheduling could not be confirmed."
    assert result["status"] == "unknown"


# --- start_campaign ----------------------------------------------------------

def _service_raising(monkeypatch, exc):
    class _Service:
        async def get_campaign(self, campaign_id, tenant_id=None):
            raise exc

    monkeypatch.setattr(campaigns_tool, "_get_campaign_service", lambda _db: _Service())


START_SENTENCE = "The campaign start could not be confirmed. Check its status before trying again."


async def test_start_campaign_masks_runtime_error(monkeypatch, caplog):
    _service_raising(monkeypatch, RuntimeError(MARKER))
    result = await start_campaign(TENANT, object(), campaign_id="c1", confirm=True)
    assert result == {"success": False, "error": START_SENTENCE}
    _assert_masked(result, caplog)


async def test_start_campaign_masks_base_campaign_error(monkeypatch, caplog):
    _service_raising(monkeypatch, CampaignError("Failed to start campaign: " + MARKER))
    result = await start_campaign(TENANT, object(), campaign_id="c1", confirm=True)
    assert result == {"success": False, "error": START_SENTENCE}
    _assert_masked(result, caplog)


async def test_start_campaign_readiness_reason_reaches_the_customer(monkeypatch):
    reason = "No outbound line is ready. Choose a working line first."
    _service_raising(monkeypatch, CampaignReadinessError({"reason": reason, "reason_code": "no_line"}))
    result = await start_campaign(TENANT, object(), campaign_id="c1", confirm=True)
    assert result == {"success": False, "error": reason}


async def test_start_campaign_permission_check_message_reaches_the_customer(monkeypatch):
    _service_raising(monkeypatch, CampaignPermissionCheckUnavailable())
    result = await start_campaign(TENANT, object(), campaign_id="c1", confirm=True)
    assert result == {"success": False, "error": CampaignPermissionCheckUnavailable.MESSAGE}


def test_campaign_error_str_equals_message():
    assert str(CampaignReadinessError({"reason": "r", "reason_code": "c"})) == "r"
    assert str(CampaignPermissionCheckUnavailable()) == CampaignPermissionCheckUnavailable.MESSAGE


# --- voice tools -------------------------------------------------------------

def _catalog_raising(monkeypatch, exc):
    async def boom(_provider):
        raise exc

    monkeypatch.setattr(campaign_ai_options, "_voice_catalog_for_provider", boom)


async def test_apply_campaign_voice_masks_runtime_error(monkeypatch, caplog):
    _catalog_raising(monkeypatch, RuntimeError(MARKER))
    result = await apply_campaign_voice(
        TENANT, object(), campaign_ids=["c1"], tts_provider="p", voice_id="v",
    )
    assert result == {"error": "The voice change could not be confirmed. Preview the change again."}
    _assert_masked(result, caplog)


async def test_list_voices_masks_runtime_error(monkeypatch, caplog):
    _catalog_raising(monkeypatch, RuntimeError(MARKER))
    result = await list_voices(TENANT, object(), tts_provider="p")
    assert result == {"error": "Could not load the voices for that provider."}
    _assert_masked(result, caplog)


# --- dispatch_tool -----------------------------------------------------------

def _dispatch_ready(monkeypatch, tool):
    from app.core.security import principal

    async def allowed(*_a, **_k):
        return None

    monkeypatch.setattr(principal, "check_assistant_session", allowed)
    monkeypatch.setattr(dispatch_module, "_authorize_query_tool", allowed)
    monkeypatch.setitem(ALL_TOOLS["get_campaigns"], "function", tool)


async def test_dispatch_catch_all_masks_runtime_error(monkeypatch, caplog):
    async def tool(_tenant, _db, **_kwargs):
        raise RuntimeError(MARKER)

    _dispatch_ready(monkeypatch, tool)
    result = await dispatch_module.dispatch_tool(
        "get_campaigns", TENANT, SimpleNamespace(pool=object()), None, {}, actor_user_id="u1",
    )
    assert result == {"error": "The request could not be completed."}
    _assert_masked(result, caplog)


async def test_dispatch_bad_arguments_text_is_fixed(monkeypatch):
    async def tool(_tenant, _db):
        return {"ok": True}

    _dispatch_ready(monkeypatch, tool)
    result = await dispatch_module.dispatch_tool(
        "get_campaigns", TENANT, SimpleNamespace(pool=object()), None, {"bogus": MARKER},
        actor_user_id="u1",
    )
    # The NAME of the argument the model got wrong is given back; its value is not.
    assert result == {
        "error": "Invalid arguments for get_campaigns. Unexpected argument 'bogus'."
                 " Check the parameter names and values."
    }
    assert "leads_phone_key" not in json.dumps(result) and "+447700900123" not in json.dumps(result)


# --- review of 24 November -----------------------------------------------------
class _StartedService:
    """The campaign start itself succeeds."""

    async def get_campaign(self, campaign_id, tenant_id=None):
        return {"id": campaign_id, "name": "Spring", "status": "draft", "direction": "outbound"}

    async def start_campaign(self, campaign_id, tenant_id=None):
        return SimpleNamespace(success=True, jobs_enqueued=3, jobs_withheld=0,
                               leads_selected=3, error=None, message="started")


class _ActionRecordRefused:
    """The record of the action, written AFTER the start, hits a refused connection."""

    def table(self, _name):
        return self

    def insert(self, _row):
        return self

    def execute(self):
        raise ConnectionRefusedError(MARKER)


async def test_start_campaign_that_started_is_not_called_a_failure_when_its_record_fails(monkeypatch, caplog):
    # The campaign is running and three calls are queued. A failed write of the action's
    # record used to turn the answer into "The campaign could not be started ...".
    from app.infrastructure.assistant.tools import campaigns as campaigns_tool

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(campaigns_tool, "_get_campaign_service", lambda _db: _StartedService())
    result = await campaigns_tool.start_campaign(TENANT, _ActionRecordRefused(), campaign_id="c1", confirm=True)
    assert result["success"] is True and "error" not in result
    assert result["jobs_enqueued"] == 3
    assert "leads_phone_key" not in json.dumps(result, default=str)
    lost = [r for r in caplog.records if "action record not written" in r.getMessage()]
    assert len(lost) == 1 and lost[0].levelno == logging.ERROR


async def test_start_campaign_dispatch_failure_does_not_say_not_started(monkeypatch):
    # Raised by the service AFTER the campaign was set running, with calls already queued.
    from app.domain.services.campaign_service import CampaignDispatchError

    _service_raising(monkeypatch, CampaignDispatchError(
        "Dialer queue batch dispatch could not be confirmed", jobs_enqueued=2, jobs_pending=5))
    result = await start_campaign(TENANT, object(), campaign_id="c1", confirm=True)
    assert result == {"success": False, "error": START_SENTENCE}
    assert "could not be started" not in result["error"]


async def test_start_campaign_sentence_gets_no_second_instruction(monkeypatch):
    # "Check its status before trying again. Try again in a moment." contradicted itself.
    _service_raising(monkeypatch, ConnectionRefusedError(MARKER))
    result = await start_campaign(TENANT, object(), campaign_id="c1", confirm=True)
    assert result == {"success": False, "error": START_SENTENCE}


def test_helper_retry_hint_can_be_switched_off():
    assert tool_error_text(ConnectionRefusedError("x"), "fb", tool="t") == "fb Try again in a moment."
    assert tool_error_text(ConnectionRefusedError("x"), "fb", tool="t", retry_hint=False) == "fb"


def test_helper_always_leaves_a_record_when_the_message_cannot_be_read(caplog):
    class Unreadable(ValueError):
        def __str__(self):
            raise RuntimeError("no text")

    caplog.set_level(logging.INFO)
    assert tool_error_text(Unreadable(), "fb", tool="t", public=(Unreadable,)) == "fb"
    failed = [r for r in caplog.records if "assistant_tool_failed" in r.getMessage()]
    assert len(failed) == 1 and failed[0].levelno == logging.ERROR


async def test_initiate_call_preview_failure_writes_one_error_record(monkeypatch, caplog):
    from app.domain.services import phone_number_normalizer as pn
    from app.infrastructure.assistant.tools.calls import initiate_call

    def boom(*_a, **_k):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(pn, "normalize_phone_for_capture", boom)
    caplog.set_level(logging.INFO)
    await initiate_call(TENANT, SimpleNamespace(pool=object()), phone_number="+14155551234", campaign_id="c1")
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, [r.getMessage() for r in errors]


async def test_dispatch_names_the_arguments_the_model_left_out(monkeypatch):
    async def tool(_tenant, _db, *, campaign_id, list_id):
        return {"ok": True}

    _dispatch_ready(monkeypatch, tool)
    result = await dispatch_module.dispatch_tool(
        "get_campaigns", TENANT, SimpleNamespace(pool=object()), None, {}, actor_user_id="u1",
    )
    assert result == {
        "error": "Invalid arguments for get_campaigns. Missing: 'campaign_id', 'list_id'."
                 " Check the parameter names and values."
    }


def test_dispatch_hint_takes_names_only():
    def tool(_tenant, _db, *, campaign_id, limit=5):
        return None

    def hint(text, sent=()):
        return dispatch_module._bad_argument_hint(text, dict.fromkeys(sent), tool)

    assert hint("tool() got an unexpected keyword argument 'bogus'", sent=["bogus"]) == " Unexpected argument 'bogus'."
    assert hint("tool() missing 1 required keyword-only argument: 'campaign_id'") == " Missing: 'campaign_id'."
    # a name is given back only when it is REAL
    assert hint("inner() got an unexpected keyword argument 'y'", sent=["x"]) == ""          # not sent by the model
    assert hint("tool() missing 1 required positional argument: 'not_a_parameter'") == ""   # not the tool's
    # the whole message must be Python's own: an argument KEY made up to look like one gives nothing
    made_up = "zz missing 1 required positional argument: 'Your_account_is_locked_call_support'"
    assert hint("tool() got an unexpected keyword argument '" + made_up + "'", sent=[made_up]) == ""
    assert hint(made_up, sent=[made_up]) == ""
    # a TypeError raised INSIDE a tool has unknown text: no hint
    assert hint("unsupported operand type(s) for +: 'int' and 'str'") == ""
    assert hint(MARKER, sent=[MARKER]) == ""


def test_a_failed_start_carries_no_driver_text_in_its_campaign_error(monkeypatch):
    # The message of a CampaignError can reach a customer (the Salesforce callback returns it).
    import asyncio

    from app.domain.services.campaign_service import CampaignError, CampaignService

    svc = CampaignService(object())

    async def boom(*_a, **_k):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(svc, "get_campaign", boom)
    with pytest.raises(CampaignError) as raised:
        asyncio.run(svc.start_campaign("c1", tenant_id=TENANT))
    assert str(raised.value) == "Failed to start campaign"
    assert isinstance(raised.value.__cause__, RuntimeError)


# --- second check of 24 November -------------------------------------------------
class _LookupFails:
    """The campaign cannot even be read: no start was attempted."""

    def __init__(self, exc):
        self._exc = exc

    async def get_campaign(self, campaign_id, tenant_id=None):
        raise self._exc


@pytest.mark.parametrize("exc, expected", [
    (RuntimeError(MARKER), "Could not prepare the campaign start."),
    (ConnectionRefusedError(MARKER), "Could not prepare the campaign start. Try again in a moment."),
])
async def test_start_campaign_preview_failure_does_not_speak_of_a_start(monkeypatch, caplog, exc, expected):
    from app.infrastructure.assistant.tools import campaigns as campaigns_tool

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(campaigns_tool, "_get_campaign_service", lambda _db: _LookupFails(exc))
    result = await campaigns_tool.start_campaign(TENANT, object(), campaign_id="c1")      # the preview
    assert result == {"success": False, "error": expected}
    _assert_masked(result, caplog)


class _RecordReturnsAnError:
    def table(self, _name):
        return self

    def insert(self, _row):
        return self

    def execute(self):
        return SimpleNamespace(data=None, error=MARKER)


async def test_start_campaign_tags_a_record_that_came_back_as_an_error(monkeypatch, caplog):
    from app.infrastructure.assistant.tools import campaigns as campaigns_tool

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(campaigns_tool, "_get_campaign_service", lambda _db: _StartedService())
    result = await campaigns_tool.start_campaign(TENANT, _RecordReturnsAnError(), campaign_id="c1", confirm=True)
    assert result["success"] is True and "error" not in result
    lost = [r.getMessage() for r in caplog.records if "action record not written" in r.getMessage()]
    assert len(lost) == 1 and "leads_phone_key" not in lost[0]


@pytest.mark.parametrize("call", ["update_campaign_config", "apply_campaign_voice"])
async def test_preview_the_change_again_gets_no_second_instruction(monkeypatch, call):
    # "... Preview the change again. Try again in a moment." said two things.
    hinted = tool_error_text(ConnectionRefusedError("x"), "S. Preview the change again.", tool=call)
    assert hinted.endswith("Try again in a moment.")                       # the helper's default
    import inspect

    from app.infrastructure.assistant.tools import campaign_admin, campaign_ai_options

    module = campaign_admin if call == "update_campaign_config" else campaign_ai_options
    source = inspect.getsource(getattr(module, call))
    assert "Preview the change again." in source and "retry_hint=False" in source

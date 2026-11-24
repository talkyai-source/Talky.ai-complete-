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


START_SENTENCE = "The campaign could not be started. Check its status before trying again."


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
    assert result == {
        "error": "Invalid arguments for get_campaigns. Check the parameter names and values."
    }

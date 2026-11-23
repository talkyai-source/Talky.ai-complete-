"""The platform's switch for the account's choice of where the recording notice is said.

OFF unless OUTBOUND_NOTICE_PLACEMENT_CHOICE_ENABLED is set to 1/true/yes/on. While it is
off nothing about any call changes, whatever an account has stored; the settings screen is
not offered the choice; and the endpoint refuses without reading or writing anything.
"""
from __future__ import annotations

import itertools
import logging
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import recording_notice_placement as ep
from app.api.v1.endpoints import recordings
from app.domain.services import notice_placement as np
from app.domain.services.recording_policy_service import (
    MINIMUM_DISCLOSURE_TEXT,
    RecordingDecision,
    is_platform_notice_text,
)
from tests.unit.test_recording_notice_placement_endpoint import _Audit, _user

SWITCH = "OUTBOUND_NOTICE_PLACEMENT_CHOICE_ENABLED"
OWN = "Acme records calls for training."


def _decision(placement):
    return RecordingDecision(
        should_record=True, announcement_required=True, announcement_text=OWN,
        opt_out_dtmf_digit=None, retention_days=90, reason="test",
        outbound_notice_placement=placement,
    )


@pytest.mark.parametrize("value,on", [
    ("1", True), ("true", True), ("YES", True), (" on ", True),
    (None, False), ("", False), ("0", False), ("false", False), ("off", False), ("maybe", False),
])
def test_the_switch_is_off_unless_set_to_an_on_word(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv(SWITCH, raising=False)
    else:
        monkeypatch.setenv(SWITCH, value)
    assert np.placement_choice_enabled() is on
    assert np.ENV_SWITCH == SWITCH


def test_switched_off_a_stored_choice_is_not_used_and_the_log_says_so(monkeypatch, caplog):
    monkeypatch.delenv(SWITCH, raising=False)
    with caplog.at_level(logging.INFO, logger="app.domain.services.notice_placement"):
        result = np.decide_notice_placement(
            _decision("in_opening"), OWN, opener_available=True, pinned=False,
            call_id="call-switch-off-1", path="cascaded")
    assert result.in_opening is False
    assert result.why == "account_choice_switched_off" and result.said == "notice_then_opening"
    (line,) = [r.getMessage() for r in caplog.records if "recording_notice_placement " in r.getMessage()]
    assert "stored=in_opening" in line and "choice_switch=off" in line


def test_switched_on_the_stored_choice_is_used(monkeypatch):
    monkeypatch.setenv(SWITCH, "true")
    result = np.decide_notice_placement(
        _decision("in_opening"), OWN, opener_available=True, pinned=False,
        call_id="call-switch-on-1", path="cascaded")
    assert result.in_opening is True and result.why == "account_choice"


def test_switched_off_every_call_follows_the_rule_from_before_this_feature(monkeypatch):
    """Whatever is stored, the placement equals the old one-line rule."""
    monkeypatch.delenv(SWITCH, raising=False)
    texts = [None, "", OWN, MINIMUM_DISCLOSURE_TEXT, "This call may be recorded for quality and training purposes."]
    for placement, text, opener, pinned in itertools.product(
            ["own_sentence_first", "in_opening", "bogus", None], texts, [True, False], [True, False]):
        got = np.decide_notice_placement(
            _decision(placement), text, opener_available=opener, pinned=pinned,
            call_id="call-old-rule", path="cascaded").in_opening
        assert got == bool(opener and text and is_platform_notice_text(text)), (placement, text, opener, pinned)


def test_the_settings_answer_offers_the_choice_only_while_the_switch_is_on(monkeypatch):
    row = {"default_consent_mode": "two_party", "announcement_text": OWN, "opt_out_dtmf_digit": None,
           "two_party_country_codes": [], "retention_days": 90,
           "outbound_notice_placement": "in_opening", "updated_at": None}
    monkeypatch.delenv(SWITCH, raising=False)
    assert recordings._policy_response(dict(row)).outbound_notice_placement is None
    monkeypatch.setenv(SWITCH, "true")
    assert recordings._policy_response(dict(row)).outbound_notice_placement == "in_opening"


async def test_switched_off_the_endpoint_refuses_and_reads_and_writes_nothing(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    tenant, user = _user()
    entered = []

    @asynccontextmanager
    async def acquire(_pool, t):
        entered.append(t)
        yield None

    monkeypatch.setattr(ep, "acquire_with_tenant", acquire)
    audit = _Audit()
    with pytest.raises(HTTPException) as refused:
        await ep.put_outbound_notice_placement(
            body=ep.NoticePlacementBody(outbound_notice_placement="in_opening"),
            user=user, db_pool=object(), audit_logger=audit)
    assert refused.value.status_code == 503
    assert refused.value.detail == {"code": "notice_placement_unavailable"}
    assert entered == [] and audit.events == []


# --- Added after the independent review of 23 November -------------------------------

from app.domain.services import recording_policy_service as rps  # noqa: E402
from tests.unit import test_outbound_ai_disclosure_opener as opener_t  # noqa: E402
from tests.unit import test_realtime_opening_notice as rt_t  # noqa: E402


@pytest.fixture
def _clean_calls(monkeypatch):
    rps._DISCLOSURE_LEDGER.clear()
    monkeypatch.delenv("OPENER_PREMADE_ENABLED", raising=False)
    monkeypatch.delenv(rt_t.on.ENV_SWITCH, raising=False)
    yield
    rps._DISCLOSURE_LEDGER.clear()


def test_switched_on_an_incoming_lines_own_text_is_never_moved_into_the_opening(monkeypatch):
    """The choice is about the account's sentence, not the text pinned on a line."""
    monkeypatch.setenv(SWITCH, "true")
    result = np.decide_notice_placement(
        _decision("in_opening"), "This incoming line is recorded.", opener_available=True,
        pinned=True, call_id="call-pinned-1", path="cascaded")
    assert result.in_opening is False
    assert result.why == "incoming_line_text" and result.said == "notice_then_opening"


async def test_switched_off_a_whole_cascaded_call_is_the_same_whatever_is_stored(monkeypatch, _clean_calls):
    """End to end through the greeting: the account sentence first, then the opening."""
    monkeypatch.delenv(SWITCH, raising=False)
    outcomes = []
    for placement in ("own_sentence_first", "in_opening"):
        rps._DISCLOSURE_LEDGER.clear()
        spoken, session = await opener_t._answer(_decision(placement))
        outcomes.append((spoken, [m.content for m in session.conversation_history],
                         rps.get_disclosure_state(opener_t.CALL_UUID), session._has_introduced))
    assert outcomes[0] == outcomes[1]
    spoken, history, _state, _introduced = outcomes[1]
    assert spoken[0] == ("tts", OWN)
    assert spoken[1:] and all(kind == "presynth_audio" for kind, _ in spoken[1:])
    assert history == [OWN, opener_t.OPENER]


async def test_switched_off_a_realtime_call_still_requires_the_account_sentence_first(monkeypatch, _clean_calls):
    monkeypatch.delenv(SWITCH, raising=False)
    gates = []
    for placement in ("own_sentence_first", "in_opening"):
        rps._DISCLOSURE_LEDGER.clear()
        gate = await rt_t.start(rt_t.build(), _decision(placement))
        gates.append((gate.notice, gate.required_text, gate.notice_owed))
    assert gates[0] == gates[1]
    assert gates[1] == ("tenant_text_first", f"{OWN} {rt_t.PLAIN_OPENER}", True)


async def test_a_fault_in_the_audit_record_is_not_reported_as_the_choice_being_unavailable(monkeypatch):
    """Only the missing column of the UPDATE means "unavailable"; anything else is raised."""
    import asyncpg
    from types import SimpleNamespace

    monkeypatch.setenv(SWITCH, "true")
    _tenant, user = _user()

    async def fetchrow(_query, *_args):
        return {"default_consent_mode": "two_party", "announcement_text": OWN, "opt_out_dtmf_digit": None,
                "two_party_country_codes": [], "retention_days": 90,
                "outbound_notice_placement": "in_opening", "updated_at": None}

    @asynccontextmanager
    async def acquire(_pool, _t):
        yield SimpleNamespace(fetchrow=fetchrow)

    class _BrokenAudit(_Audit):
        async def log_in_transaction(self, conn, **kwargs):
            raise asyncpg.UndefinedColumnError("a column of the audit table is missing")

    monkeypatch.setattr(ep, "acquire_with_tenant", acquire)
    with pytest.raises(asyncpg.UndefinedColumnError):
        await ep.put_outbound_notice_placement(
            body=ep.NoticePlacementBody(outbound_notice_placement="in_opening"),
            user=user, db_pool=object(), audit_logger=_BrokenAudit())
